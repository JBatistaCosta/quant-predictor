#!/usr/bin/env python3
"""O xG de um chute é mais bem descrito pela zona (18) ou por coordenadas polares (distância e ângulo ao gol)? (Achado 34)

Treino = chutes de índice par, teste = índice ímpar. Compara o R² do xG do StatsBomb previsto por: média da zona; média por caixas de distância x ângulo;
árvores de gradiente em (distância, ângulo); e o mesmo mais cabeça e tipo de bola parada. Também mede a calibração gols / xG por zona.
Uso: python scripts/analisar_xg_polar_vs_zona.py [arquivo.json]
"""

from __future__ import annotations

import json
import sys

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor


def r2(y, p):
    return 1 - float(np.sum((y - p) ** 2) / np.sum((y - y.mean()) ** 2))


def main(arq: str) -> dict:
    d = json.load(open(arq))
    a = np.array(d["chutes"], dtype=float)                  # zona, xg, gol, tipo, cabeca, dist, angulo
    zona, xg, gol, tipo, cab, dist, ang = (a[:, i] for i in range(7))
    treino, teste = np.arange(len(a)) % 2 == 0, np.arange(len(a)) % 2 == 1
    res = {"n_chutes": len(a), "xg_medio": float(xg.mean()), "gol_medio": float(gol.mean())}
    # 1. média da zona
    media_zona = {z: xg[treino & (zona == z)].mean() if (treino & (zona == z)).any() else xg[treino].mean() for z in range(18)}
    res["r2_zona"] = r2(xg[teste], np.array([media_zona[int(z)] for z in zona[teste]]))
    # 2. caixas polares
    ib = np.minimum((dist / 2).astype(int), 20) * 100 + np.minimum((ang / 10).astype(int), 8)
    media_caixa = {}
    for k in np.unique(ib[treino]):
        media_caixa[k] = xg[treino & (ib == k)].mean()
    res["r2_caixas_polares"] = r2(xg[teste], np.array([media_caixa.get(k, xg[treino].mean()) for k in ib[teste]]))
    # 3. árvores
    for nome, X in (("r2_polar_arvores", np.c_[dist, ang]), ("r2_polar_cabeca_tipo", np.c_[dist, ang, cab, tipo]), ("r2_zona_cabeca_tipo", np.c_[zona, cab, tipo])):
        m = HistGradientBoostingRegressor(max_iter=200, learning_rate=0.08, random_state=0).fit(X[treino], xg[treino])
        res[nome] = r2(xg[teste], m.predict(X[teste]))
    # 4. calibração gols / xG por zona e variância do xG dentro da zona
    res["por_zona"] = [{"zona": z, "n": int((zona == z).sum()), "xg_medio": float(xg[zona == z].mean()) if (zona == z).any() else 0.0,
                        "gols_sobre_xg": float(gol[zona == z].sum() / xg[zona == z].sum()) if (zona == z).any() and xg[zona == z].sum() > 0 else 0.0,
                        "xg_dp": float(xg[zona == z].std()) if (zona == z).sum() > 1 else 0.0} for z in range(18)]
    return res


if __name__ == "__main__":
    r = main(sys.argv[1] if len(sys.argv) > 1 else "dados_referencia/statsbomb/chutes_e_progressao_ligas_2015_16.json")
    print(f"{r['n_chutes']} chutes; xG médio {r['xg_medio']:.4f}; gols por chute {r['gol_medio']:.4f}")
    for k in ("r2_zona", "r2_caixas_polares", "r2_polar_arvores", "r2_zona_cabeca_tipo", "r2_polar_cabeca_tipo"):
        print(f"{k:24s} R² fora da amostra {r[k]:.3f}")
    print("zona  n      xG médio  gols/xG  dp do xG")
    for z in r["por_zona"]:
        print(f"{z['zona']:4d} {z['n']:6d}  {z['xg_medio']:.4f}   {z['gols_sobre_xg']:.2f}    {z['xg_dp']:.4f}")
