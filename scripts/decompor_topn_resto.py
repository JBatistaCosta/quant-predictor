#!/usr/bin/env python3
"""Complemento do Achado 48: quanto a diferença entre os times no TOP-N e a diferença no RESTO do elenco acrescentam ao Elo para explicar o saldo de xG.

Entrada: os mesmos CSV de scripts/analisar_topn_jogadores.py (colunas id,season,gd,xgd,elod,x2..x5,xall,a2..a5,aall). Regressão linear do saldo de xG:
  Elo | Elo + diferença top-N | Elo + diferença top-N + diferença do resto (= elenco inteiro - top-N). Reporta R² e o ganho sobre o Elo sozinho.
Uso: python scripts/decompor_topn_resto.py arq1.csv [arq2.csv ...]
"""
import csv
import sys

import numpy as np


def ler(arquivos):
    rows = []
    for f in arquivos:
        for r in csv.DictReader(open(f)):
            if r["elod"] == "" or r["xgd"] == "":
                continue
            rows.append({k: float(v) for k, v in r.items() if k not in ("id", "season")})
    return rows


def main():
    rows = ler(sys.argv[1:])
    col = lambda k: np.array([r[k] for r in rows])
    y, elo = col("xgd"), col("elod")

    def r2(X):
        X = np.column_stack([np.ones(len(y))] + X)
        b, *_ = np.linalg.lstsq(X, y, rcond=None)
        res = y - X @ b
        return 1 - res @ res / ((y - y.mean()) @ (y - y.mean())), b

    base, _ = r2([elo])
    print(f"{len(rows)} partidas; R² do saldo de xG só com o Elo: {base:.4f}; desvio-padrão da diferença de Elo entre os times: {elo.std():.1f}")
    for nome, p in (("xG previsto", "x"), ("xA previsto", "a")):
        print(f"\n== {nome}")
        print("  desvio-padrão da diferença entre os times: " + ", ".join(f"top-{n} {col(p + n).std():.3f}" for n in ("2", "3", "5")) + f", inteiro {col(p + 'all').std():.3f}")
        for n in ("2", "3", "4", "5", "all"):
            r, _ = r2([elo, col(p + n)])
            print(f"  Elo + diferença top-{n:>3s}: R² {r:.4f} (ganho {r - base:+.4f})")
        for n in ("2", "3", "5"):
            top = col(p + n)
            resto = col(p + "all") - top
            r, b = r2([elo, top, resto])
            r_t, _ = r2([elo, top])
            r_r, _ = r2([elo, resto])
            print(f"  Elo + top-{n} + resto: R² {r:.4f} (ganho {r - base:+.4f}); só top-{n} {r_t - base:+.4f}; só resto {r_r - base:+.4f}; coef. top {b[2]:+.3f}, resto {b[3]:+.3f}")


if __name__ == "__main__":
    main()
