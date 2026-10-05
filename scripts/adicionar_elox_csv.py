#!/usr/bin/env python3
"""Acrescenta a coluna `elox` (Elo por xG do mandante menos o do visitante, ANTES do jogo; `team_elo_xg_history.rating_antes`) aos CSVs do backtest do simulador que ainda não a têm.

Lê as notas de um cache JSON ({match_id: {team_id: rating}}, o mesmo que `scripts/avaliar_mistura_elo_xg.py --cache` grava) ou, sem cache, baixa de `team_elo_xg_history`
(somente leitura; SUPABASE_URL e SUPABASE_KEY por variável de ambiente, sem valor padrão). Idempotente: CSV que já tem `elox` é deixado como está.
Uso: python scripts/adicionar_elox_csv.py [--pasta dados_referencia/backtest_simulador] [--cache elo_xg.json]
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from avaliar_mistura_elo_xg import baixar_elo_xg  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pasta", default="dados_referencia/backtest_simulador")
    ap.add_argument("--cache", default="")
    a = ap.parse_args()
    arquivos = sorted(glob.glob(os.path.join(a.pasta, "*_2022_25.csv")))
    linhas = {}
    for f in arquivos:
        with open(f, newline="") as fh:
            linhas[f] = list(csv.DictReader(fh))
    pendentes = [f for f in arquivos if "elox" not in linhas[f][0]]
    if not pendentes:
        print("todos os CSVs já têm elox")
        return
    ids = sorted({int(r["id"]) for f in pendentes for r in linhas[f]})
    elo = baixar_elo_xg(ids, a.cache)
    for f in pendentes:
        cols = list(linhas[f][0]) + ["elox"]
        com = 0
        for r in linhas[f]:
            d = elo.get(int(r["id"]), {})
            if r["home"] in d and r["away"] in d:
                r["elox"] = round(d[r["home"]] - d[r["away"]], 1)
                com += 1
            else:
                r["elox"] = ""
        with open(f, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerows(linhas[f])
        print(f"{os.path.basename(f)}: {com} de {len(linhas[f])} jogos com elox")


if __name__ == "__main__":
    main()
