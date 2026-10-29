"""La figura del informe: cuántas de las 10 cifras acierta Claude en cada modo y cuánto tarda.

    uv run grafico.py                          # lee resultados_web.csv y escribe grafico.png
    uv run grafico.py resultados.csv           # o cualquier otro archivo de evaluar.py
"""
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

AQUI = Path(__file__).parent
ARCHIVO = AQUI / (sys.argv[1] if len(sys.argv) > 1 else "resultados_web.csv")
MODOS = {"memoria": "Solo memoria", "web": "Búsqueda web", "mcp": "MCP del BCRP"}
# acierta y se equivoca con los colores de estado (bien / mal); «no sabe» en gris neutro, porque no es un error
RESULTADOS = {"acierta": ("Acierta", "#0ca30c", "#ffffff"),
              "no sabe": ("No sabe", "#c3c2b7", "#0b0b0b"),
              "se equivoca": ("Se equivoca", "#d03b3b", "#ffffff")}
FONDO, TINTA, TINTA_2, GRILLA, BASE = "#fcfcfb", "#0b0b0b", "#52514e", "#e1e0d9", "#c3c2b7"

res = pd.read_csv(ARCHIVO)
modos = [m for m in MODOS if m in set(res.modo)][::-1]   # de arriba abajo: memoria, web, mcp
conteo = res.groupby("modo").resultado.value_counts().unstack(fill_value=0).reindex(modos, fill_value=0)
segundos = res.groupby("modo").segundos.mean().reindex(modos)
n = res.groupby("modo").size().max()

plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "text.color": TINTA,
                     "axes.labelcolor": TINTA_2, "xtick.color": TINTA_2, "ytick.color": TINTA})
fig, (izq, der) = plt.subplots(1, 2, figsize=(10, 3.6), gridspec_kw={"width_ratios": [2.2, 1], "wspace": 0.08},
                               sharey=True, facecolor=FONDO)
y = range(len(modos))

# Izquierda: las 10 preguntas de cada modo, partidas por resultado (2 px de fondo entre segmentos)
inicio = pd.Series(0, index=modos)
for clave, (etiqueta, color, tinta) in RESULTADOS.items():
    valores = conteo.get(clave, pd.Series(0, index=modos))
    izq.barh(y, valores, left=inicio, height=0.5, color=color, edgecolor=FONDO, linewidth=2, label=etiqueta)
    for i, (v, x0) in enumerate(zip(valores, inicio)):
        if v:
            izq.text(x0 + v / 2, i, f"{v}", ha="center", va="center", color=tinta, fontsize=10, fontweight="bold")
    inicio += valores
izq.set_xlim(0, n)
izq.set_xticks(range(0, n + 1, 2))
izq.set_xlabel(f"preguntas (de {n})")
izq.set_yticks(list(y), [MODOS[m] for m in modos], fontsize=11)
izq.set_title("Resultado por pregunta", loc="left", fontsize=11, color=TINTA_2)
izq.legend(ncols=3, loc="upper left", bbox_to_anchor=(0, -0.2), frameon=False, fontsize=9.5)

# Derecha: segundos promedio por pregunta (otra escala, así que va en su propio panel)
der.barh(y, segundos, height=0.5, color="#2a78d6", edgecolor=FONDO, linewidth=2)
for i, s in enumerate(segundos):
    der.text(s + 0.6, i, f"{s:.1f} s", va="center", color=TINTA, fontsize=10)
der.set_xlim(0, segundos.max() * 1.35)
der.set_xlabel("segundos promedio por pregunta")
der.set_title("Tiempo", loc="left", fontsize=11, color=TINTA_2)

for ax in (izq, der):
    ax.set_facecolor(FONDO)
    ax.grid(axis="x", color=GRILLA, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.tick_params(length=0)
    for lado in ("top", "right", "left"):
        ax.spines[lado].set_visible(False)
    ax.spines["bottom"].set_color(BASE)

fig.suptitle("¿Cuántas cifras oficiales del BCRP acierta Claude Haiku?", x=0.02, ha="left", fontsize=13,
             fontweight="bold", y=0.965)
fig.text(0.02, 0.87, f"10 preguntas, mismo modelo, tres formas de responder · {ARCHIVO.name} (evaluar.py)",
         ha="left", fontsize=9.5, color=TINTA_2)
fig.subplots_adjust(left=0.13, right=0.98, top=0.74, bottom=0.27)
fig.savefig(AQUI / "grafico.png", dpi=200, facecolor=FONDO)
print(f"grafico.png escrito desde {ARCHIVO.name}")
