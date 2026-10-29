"""Servidor MCP del BCRP. Empiecen aquí: buscar_series ya funciona; completen los TODO hasta que pasen las pruebas.

    uv run pytest                                           # el contrato: las 8 pruebas deben pasar
    uv run server.py                                        # el servidor, por stdio
    claude mcp add --scope project bcrp -- uv run server.py # conectarlo a Claude Code

Las cuentas las hace Python, no el modelo: así cada cifra sale de una llamada que se puede repetir.
"""
import calendar
import json
import re
import time
import unicodedata
from datetime import date
from functools import cache
from pathlib import Path

import httpx
import pandas as pd
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

AQUI = Path(__file__).parent
CATALOGO = AQUI / "datos" / "catalogo_bcrp.csv"
URL_CATALOGO = "https://estadisticas.bcrp.gob.pe/estadisticas/series/metadata"
API = "https://estadisticas.bcrp.gob.pe/estadisticas/series/api"
SOLO_LECTURA = {"readOnlyHint": True, "openWorldHint": True}
CACHE = AQUI / "datos" / "cache"
HORAS_CACHE = 12
# El TODO 1 dice que setiembre llega como «Set», pero la API hoy responde «Sep.2026»: aceptamos los dos.
MESES = {m: i for i, m in enumerate(["ene", "feb", "mar", "abr", "may", "jun",
                                     "jul", "ago", "set", "oct", "nov", "dic"], 1)} | {"sep": 9}
FRECUENCIAS = ["Diaria", "Mensual", "Trimestral", "Anual"]   # de la más alta a la más baja

mcp = FastMCP("bcrp", instructions=(
    "Series oficiales del Banco Central de Reserva del Perú (BCRP). Primero busca el código con buscar_series; "
    "luego usa resumen para variaciones, promedios y extremos, serie para los datos y correlacion para ver si dos "
    "series se mueven juntas. No calcules a mano: cita siempre el código de la serie y el periodo."))


def _sin_tildes(s):
    return unicodedata.normalize("NFD", str(s).lower()).encode("ascii", "ignore").decode()


@cache
def catalogo():
    """El catálogo oficial: 17 mil series en unos 7 MB. Se baja una vez y queda en datos/."""
    if not CATALOGO.exists():
        CATALOGO.parent.mkdir(exist_ok=True)
        r = httpx.get(URL_CATALOGO, timeout=120)
        r.raise_for_status()
        CATALOGO.write_text(r.content.decode("latin-1"), encoding="utf-8")
    df = pd.read_csv(CATALOGO, sep=";", dtype=str).fillna("")
    df = df.rename(columns={"Código de serie": "codigo", "Categoría de serie": "categoria", "Grupo de serie": "grupo",
                            "Nombre de serie": "nombre", "Frecuencia": "frecuencia",
                            "Fecha de actualización": "actualizada", "Fecha de inicio": "inicio"})
    df["texto"] = (df.categoria + " " + df.grupo + " " + df.nombre).map(_sin_tildes)
    return df


def _ficha(codigo):
    fila = catalogo().query("codigo == @codigo")
    if fila.empty:
        raise ToolError(f"No existe el código de serie {codigo!r}. Búsquenlo con buscar_series.")
    f = fila.iloc[0]
    return {"codigo": f.codigo, "nombre": f"{f.grupo} - {f.nombre}", "categoria": f.categoria,
            "frecuencia": f.frecuencia, "inicio": f.inicio, "actualizada": f.actualizada}


@mcp.tool(annotations=SOLO_LECTURA)
def buscar_series(texto: str, frecuencia: str | None = None, limite: int = 10) -> list[dict]:
    """Busca series del BCRP por palabras: todas deben aparecer en la categoría, el grupo o el nombre.

    Ejemplos: "ipc 12 meses", "tipo de cambio interbancario venta", "reservas internacionales netas",
    "tasa de referencia", "cobre precio". frecuencia: "Diaria", "Mensual", "Trimestral" o "Anual".
    Devuelve el código, el nombre completo, la frecuencia y desde cuándo hay datos. Si no hay resultados,
    prueben con menos palabras.
    """
    df = catalogo()
    m = pd.Series(True, index=df.index)
    for palabra in _sin_tildes(texto).split():
        m &= df.texto.str.contains(re.escape(palabra))
    if frecuencia:
        m &= df.frecuencia.str.lower() == frecuencia.lower()
    # primero las series vigentes y, entre ellas, las de nombre más corto (las más generales)
    sel = df[m]
    orden = sel.assign(vieja=sel.actualizada < "2025", largo=(sel.grupo + sel.nombre).str.len())
    return [_ficha(c) for c in orden.sort_values(["vieja", "largo"]).head(limite).codigo]


# TODO 1 · serie: pedir a la API con el formato de fechas de cada frecuencia y devolver periodos legibles.

def _a_fecha_api(fecha, frecuencia, final):
    """"2025-01", "2025-01-15", "2025-T1" o "2025" al formato que pide la API según la frecuencia de la serie.

    Si la fecha es menos precisa que la serie (un mes para una serie diaria), final dice si se toma el último día
    o el primero, para que el rango quede completo.
    """
    m = re.fullmatch(r"(\d{4})(?:-T([1-4])|-(\d{1,2})(?:-(\d{1,2}))?)?", str(fecha).strip().upper())
    if not m:
        raise ToolError(f"Fecha no válida: {fecha!r}. Usen 2025-01 (mes), 2025-01-15 (día), 2025-T1 (trimestre) "
                        "o 2025 (año).")
    anio, trimestre, mes, dia = int(m[1]), m[2], m[3], m[4]
    if trimestre:
        mes = 3 * int(trimestre) if final else 3 * int(trimestre) - 2
    elif mes:
        mes = int(mes)
    else:
        mes = 12 if final else 1
    try:
        dia = int(dia) if dia else (calendar.monthrange(anio, mes)[1] if final else 1)
        date(anio, mes, dia)
    except (ValueError, calendar.IllegalMonthError):
        raise ToolError(f"Fecha no válida: {fecha!r}.") from None
    if frecuencia == "Anual":
        return f"{anio}"
    if frecuencia == "Trimestral":
        return f"{anio}-{(mes - 1) // 3 + 1}"
    if frecuencia == "Mensual":
        return f"{anio}-{mes}"
    return f"{anio}-{mes:02d}-{dia:02d}"


def _periodo(nombre, anio_max):
    """"Ene.2025" → "2025-01", "01.Oct.26" → "2026-10-01", "T1.25" → "2025-T1" y "2025" queda igual."""
    def siglo(aa):  # año de dos cifras: el más reciente que no pase del rango pedido
        anio = 2000 + int(aa)
        return anio - 100 if anio > anio_max else anio
    partes = nombre.split(".")
    if len(partes) == 3:                                            # diaria
        dia, mes, aa = partes
        return f"{siglo(aa)}-{MESES[mes.lower()]:02d}-{int(dia):02d}"
    if len(partes) == 2 and partes[0].upper().startswith("T"):     # trimestral
        return f"{siglo(partes[1])}-{partes[0].upper()}"
    if len(partes) == 2:                                            # mensual
        return f"{partes[1]}-{MESES[partes[0].lower()]:02d}"
    return nombre                                                   # anual


def _descargar(url):
    """El JSON de la API, con caché en datos/cache/ y reintentos: si hay muchos pedidos seguidos responde HTML."""
    archivo = CACHE / (re.sub(r"\W", "_", url.removeprefix(API + "/")) + ".json")
    if archivo.exists() and time.time() - archivo.stat().st_mtime < HORAS_CACHE * 3600:
        try:
            return json.loads(archivo.read_text(encoding="utf-8"))
        except ValueError:   # a medio escribir por otro proceso: se vuelve a bajar
            pass
    for intento in range(4):
        try:
            datos = httpx.get(url, timeout=30).json()
        except (httpx.HTTPError, ValueError):   # ValueError: llegó la página HTML en vez del JSON
            datos = None
        if isinstance(datos, dict) and "periods" in datos:
            try:
                CACHE.mkdir(parents=True, exist_ok=True)
                archivo.write_text(json.dumps(datos), encoding="utf-8")
            except OSError:   # sin caché también funciona
                pass
            return datos
        if intento < 3:
            time.sleep(2 ** intento)
    raise ToolError(f"El BCRP no respondió con datos ({url}). La API limita los pedidos seguidos: esperen unos "
                    "segundos y reintenten.")


def _serie(codigo, desde, hasta):
    ficha = _ficha(str(codigo).strip().upper())
    d = _a_fecha_api(desde, ficha["frecuencia"], final=False)
    h = _a_fecha_api(hasta, ficha["frecuencia"], final=True)
    url = f"{API}/{ficha['codigo']}/json/{d}/{h}"
    datos = []
    for p in _descargar(url)["periods"]:
        try:
            valor = float(p["values"][0])
        except ValueError:   # "n.d.": ese periodo no tiene dato, por ejemplo un feriado
            continue
        datos.append({"periodo": _periodo(p["name"], int(h[:4])), "valor": round(valor, 4)})
    return {"codigo": ficha["codigo"], "nombre": ficha["nombre"], "frecuencia": ficha["frecuencia"],
            "datos": datos, "fuente": url}


@mcp.tool(annotations=SOLO_LECTURA)
def serie(codigo: str, desde: str, hasta: str) -> dict:
    """Datos de una serie del BCRP entre dos fechas, ambas incluidas.

    Fechas según la frecuencia de la serie: "2025-01" (mensual), "2025-01-15" (diaria), "2025-T1" (trimestral)
    o "2025" (anual); si dan un mes para una serie diaria, se toma el mes completo.
    Ejemplo: serie("PN01273PM", "2026-01", "2026-09").
    Devuelve el nombre, la frecuencia, los datos [{"periodo", "valor"}] y la URL de la fuente. Los días sin dato
    (feriados) no aparecen. Para variaciones, promedios o extremos usen resumen.
    """
    return _serie(codigo, desde, hasta)


# TODO 2 · resumen: las cuentas en Python para que el modelo nunca las haga a mano.

@mcp.tool(annotations=SOLO_LECTURA)
def resumen(codigo: str, desde: str, hasta: str) -> dict:
    """Resumen de una serie del BCRP entre dos fechas (mismos formatos que serie): primer y último dato, cambio,
    variación %, promedio, máximo y mínimo, cada extremo con su periodo. Úsenla para cualquier cuenta.

    Si la serie ya es una tasa o una variación (inflación, tasa de interés), «cambio» está en puntos porcentuales
    y «variacion_pct» es cuánto cambió esa tasa en términos relativos.
    Ejemplo: resumen("PN01273PM", "2025-12", "2026-09").
    """
    s = _serie(codigo, desde, hasta)
    datos = s.pop("datos")
    if not datos:
        raise ToolError(f"La serie {s['codigo']} no tiene datos entre {desde} y {hasta}.")
    primero, ultimo = datos[0], datos[-1]
    valores = [x["valor"] for x in datos]
    return {**s, "observaciones": len(datos), "primero": primero, "ultimo": ultimo,
            "cambio": round(ultimo["valor"] - primero["valor"], 4),
            "variacion_pct": round(100 * (ultimo["valor"] / primero["valor"] - 1), 2) if primero["valor"] else None,
            "promedio": round(sum(valores) / len(valores), 4),
            "maximo": max(datos, key=lambda x: x["valor"]), "minimo": min(datos, key=lambda x: x["valor"])}


# TODO 3 · Herramienta propia: correlacion entre dos series.

def _a_frecuencia(s, de, a):
    """Promedia una serie a una frecuencia más baja: días a meses, meses a trimestres o a años."""
    if de == a:
        return s
    clave = {"Mensual": lambda p: p[:7],
             "Trimestral": lambda p: f"{p[:4]}-T{(int(p[5:7]) - 1) // 3 + 1}",
             "Anual": lambda p: p[:4]}[a]
    return s.groupby(s.index.map(clave)).mean()


def _pearson(df):
    r = df.iloc[:, 0].corr(df.iloc[:, 1])
    return None if pd.isna(r) else round(float(r), 3)


@mcp.tool(annotations=SOLO_LECTURA)
def correlacion(codigo_a: str, codigo_b: str, desde: str, hasta: str) -> dict:
    """¿Se mueven juntas dos series del BCRP? Correlación de Pearson entre dos fechas (mismos formatos que serie).

    Úsenla cuando pregunten si dos indicadores suben y bajan a la vez o en sentido contrario: por ejemplo el
    precio del cobre (PN38784BM) y el tipo de cambio (PN01207PM), o la tasa de referencia y la inflación. Primero
    busquen los dos códigos con buscar_series. Si las frecuencias difieren, la más alta se promedia a la más baja
    (días a meses, meses a años).
    Devuelve la correlación de los niveles y la de los cambios de un periodo al siguiente, de -1 a 1; si las dos
    series tienen tendencia, la de los cambios es la que vale. Correlación no es causalidad.
    Ejemplo: correlacion("PN38784BM", "PN01207PM", "2021-01", "2026-09").
    """
    a, b = _serie(codigo_a, desde, hasta), _serie(codigo_b, desde, hasta)
    frecuencia = max(a["frecuencia"], b["frecuencia"], key=FRECUENCIAS.index)
    sa, sb = (_a_frecuencia(pd.Series({x["periodo"]: x["valor"] for x in s["datos"]}, dtype=float),
                            s["frecuencia"], frecuencia) for s in (a, b))
    juntas = pd.concat([sa, sb], axis=1, join="inner").dropna()
    if len(juntas) < 3:
        raise ToolError(f"Solo hay {len(juntas)} periodos en común entre {a['codigo']} y {b['codigo']} de {desde} "
                        f"a {hasta}: amplíen el rango.")
    return {"serie_a": {k: a[k] for k in ("codigo", "nombre", "frecuencia")},
            "serie_b": {k: b[k] for k in ("codigo", "nombre", "frecuencia")},
            "frecuencia": frecuencia, "desde": juntas.index[0], "hasta": juntas.index[-1], "periodos": len(juntas),
            "correlacion_niveles": _pearson(juntas), "correlacion_cambios": _pearson(juntas.diff().dropna()),
            "fuentes": [a["fuente"], b["fuente"]]}


# TODO 4 · Recurso: la ficha de cualquier serie, que en Claude Code se adjunta con @bcrp:bcrp://serie/PN01273PM

@mcp.resource("bcrp://serie/{codigo}", mime_type="application/json")
def ficha(codigo: str) -> dict:
    """Ficha de una serie del BCRP: nombre, categoría, frecuencia, desde cuándo hay datos y su última actualización."""
    return {**_ficha(codigo.strip().upper()), "fuente": URL_CATALOGO}


# TODO 5 · Prompt: en Claude Code se lanza con /mcp__bcrp__informe inflación

@mcp.prompt
def informe(tema: str) -> str:
    """Informe breve sobre un tema económico del Perú, con cifras oficiales del BCRP que se pueden verificar."""
    return (f"Escribe un informe de 5 viñetas sobre «{tema}» con datos oficiales del BCRP. Busca las series con "
            "buscar_series y saca cada cifra de resumen o de serie, con el periodo más reciente disponible; no "
            "calcules a mano. En cada viñeta cita el código de la serie y el periodo, por ejemplo (PN01273PM, "
            "2026-09). Termina con la lista de URLs de las fuentes. Si no encuentras un dato, dilo en vez de estimarlo.")


if __name__ == "__main__":
    mcp.run()
