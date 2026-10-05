#!/usr/bin/env python3
"""Exporta o CSV de entrada do backtest do simulador (scripts/backtest_simulador_preditivo.py) para uma liga do Supabase.

Mesmo formato de dados_referencia/backtest_simulador/premier_league_2024_25.csv (ver LEIA-ME.md ao lado): uma linha por jogo finalizado das duas
temporadas (aquecimento + teste), com chutes e xG do FotMob, probabilidades do `dixon_coles_v1`, média entre casas das ODDS de fechamento e Elo global.

Só leitura (chave pública basta). Credenciais por variável de ambiente, SEM valor padrão: SUPABASE_URL e SUPABASE_KEY.
Uso: python scripts/exportar_csv_backtest_simulador.py --liga "La Liga" --temporadas 2024 2025 --saida dados_referencia/backtest_simulador/la_liga_2024_25.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict

COLUNAS = ["id", "season", "date", "home", "away", "hg", "ag", "hs", "as", "hxg", "axg", "hc", "ac", "dc_h", "dc_d", "dc_a", "dc_over",
           "o_h", "o_d", "o_a", "o_over", "o_under", "elod", "neutro"]


def _get(caminho: str) -> list:
    """GET no PostgREST, repetindo (2, 4, 8 e 16 s) em erro de servidor 5xx ou de rede: o servidor às vezes devolve 500 passageiro numa consulta grande."""
    url, chave = os.environ["SUPABASE_URL"].rstrip("/"), os.environ["SUPABASE_KEY"]
    req = urllib.request.Request(f"{url}/rest/v1/{caminho}", headers={"apikey": chave, "Authorization": "Bearer " + chave})
    for espera in (2, 4, 8, 16, None):
        try:
            return json.load(urllib.request.urlopen(req, timeout=120))
        except urllib.error.HTTPError as e:
            if e.code < 500 or espera is None:
                raise
        except (urllib.error.URLError, TimeoutError):
            if espera is None:
                raise
        time.sleep(espera)


def paginar(caminho: str, ordem: str = "id") -> list:
    """PostgREST corta em 1000 linhas sem aviso: pagina até vir página incompleta."""
    out, off = [], 0
    while True:
        pag = _get(f"{caminho}&order={ordem}.asc&limit=1000&offset={off}")
        out += pag
        off += 1000
        if len(pag) < 1000:
            return out


def em_lotes(caminho: str, campo: str, ids: list, lote: int = 120) -> list:
    out = []
    for i in range(0, len(ids), lote):
        filtro = ",".join(str(x) for x in ids[i:i + lote])
        out += paginar(f"{caminho}&{campo}=in.({filtro})")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--liga", required=True)
    ap.add_argument("--temporadas", nargs="+", required=True)
    ap.add_argument("--saida", required=True)
    a = ap.parse_args()
    liga = _get("leagues?select=id,name&name=eq." + urllib.parse.quote(a.liga))
    if len(liga) != 1:
        sys.exit(f"liga '{a.liga}' não encontrada de forma única: {liga}")
    lid = liga[0]["id"]
    temporadas = ",".join(f'"{t}"' for t in a.temporadas)
    jogos = paginar(f"matches?select=id,season,match_date,home_team_id,away_team_id,home_goals,away_goals,is_neutral&league_id=eq.{lid}"
                    f"&status=eq.finished&home_goals=not.is.null&season=in.({temporadas})")
    ids = [j["id"] for j in jogos]
    print(f"{a.liga}: {len(jogos)} jogos", file=sys.stderr)

    stats = defaultdict(dict)
    for r in em_lotes("match_stats_fotmob?select=match_id,team_id,total_shots,xg,corners", "match_id", ids):
        stats[r["match_id"]][r["team_id"]] = r
    dc = defaultdict(dict)
    for r in em_lotes("model_predictions?select=match_id,market,selection,probability&model_name=eq.dixon_coles_v1", "match_id", ids):
        dc[r["match_id"]][(r["market"], r["selection"])] = r["probability"]
    odds = defaultdict(lambda: defaultdict(list))
    for r in em_lotes("odds_market?select=match_id,market,selection,odds&snapshot=eq.closing&market=in.(1X2,over_under_2.5)", "match_id", ids):
        if r["odds"]:
            odds[r["match_id"]][(r["market"], r["selection"])].append(float(r["odds"]))
    elo = defaultdict(dict)
    for r in em_lotes("team_elo_history?select=match_id,team_id,rating_antes&escopo=eq.global", "match_id", ids):
        elo[r["match_id"]][r["team_id"]] = r["rating_antes"]

    def media(m: int, mercado: str, sel: str):
        v = odds[m].get((mercado, sel))
        return round(sum(v) / len(v), 3) if v else ""

    def num(x, casas):
        return "" if x is None else round(float(x), casas)

    with open(a.saida, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLUNAS)
        for j in sorted(jogos, key=lambda j: (j["match_date"], j["id"])):
            m, h, v = j["id"], j["home_team_id"], j["away_team_id"]
            sh, sa = stats[m].get(h, {}), stats[m].get(v, {})
            eh, ea = elo[m].get(h), elo[m].get(v)
            w.writerow([m, j["season"], str(j["match_date"])[:10], h, v, j["home_goals"], j["away_goals"],
                        sh.get("total_shots", ""), sa.get("total_shots", ""), num(sh.get("xg"), 3), num(sa.get("xg"), 3),
                        sh.get("corners", ""), sa.get("corners", ""),
                        num(dc[m].get(("1X2", "home")), 4), num(dc[m].get(("1X2", "draw")), 4), num(dc[m].get(("1X2", "away")), 4),
                        num(dc[m].get(("over_under_2.5", "over")), 4),
                        media(m, "1X2", "home"), media(m, "1X2", "draw"), media(m, "1X2", "away"),
                        media(m, "over_under_2.5", "over"), media(m, "over_under_2.5", "under"),
                        "" if eh is None or ea is None else round(eh - ea, 1), 1 if j["is_neutral"] else 0])


if __name__ == "__main__":
    main()
