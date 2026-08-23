#!/usr/bin/env python3
"""Ingestão de odds do Brasileirão a partir do dataset Kaggle
`brunobchinaglia/bets-on-soccer-2015-2025` (arquivos_do_claude/kaggle/,
ver README lá). Mapeamento validado manualmente antes de escrever este
script: 2.274/2.274 (100%) das partidas de 2019-2024 casaram por
(temporada, time_casa, time_fora) — chave única dentro de uma temporada
de returno duplo, sem precisar de rodada nem data (o CSV não tem data) —
com ZERO divergência de placar entre CSV e banco nas casadas (confirma
que o matching está correto, não é coincidência). 2015-2018 não têm
partida correspondente no banco (Brasileirão só começa em 2019 aqui) —
ficam de fora, não é bug.

3 times duplicados reais encontrados e corrigidos no banco durante a
validação (não fazem parte deste script, já fundidos direto via SQL):
América-MG (id 807 -> 3), Sport Recife (id 727 -> 42), CSA (id 710 -> 854)
-- cada um tinha um segundo registro sem `external_id`/poucas partidas,
mesmo clube com fonte diferente (api_football vs fbref/fotmob).

ALIASES_MANUAIS (mesmo espírito de ingestao_stats_fbref.py e
sync-match-stats.js): apelidos/abreviações do CSV sem raiz comum com o
nome completo do banco.

Snapshot: o CSV não identifica casa de apostas nem timing (abertura vs
fechamento) -- tratado como uma odd só, gravada com
bookmaker='kaggle_oddspedia' e snapshot='closing' (melhor suposição --
Oddspedia tipicamente mostra a odd mais recente antes do apito; não dá
pra confirmar com certeza a partir do dataset). Reprocessável: apaga
odds desse bookmaker pros matches em questão antes de inserir de novo.

Uso:
    set SUPABASE_URL=...
    set SUPABASE_KEY=sua_service_role_key
    python ingestao_odds_kaggle_brasileirao.py
"""

from __future__ import annotations

import csv
import os
import re
import sys
import unicodedata
from pathlib import Path

from supabase import create_client

LEAGUE_ID = 1  # Brasileirão Série A
BOOKMAKER = "kaggle_oddspedia"
CSV_PATH = Path(__file__).parent / "kaggle" / "odds_brasileirao_2015_2024.csv"

ALIASES_MANUAIS = {
    "athletico pr": "club athletico paranaense",
    "atletico go": "ac goianiense",
    "america mg": "america fc",
    "red bull bragantino": "rb bragantino",
    "sport recife": "sc recife",
    "csa": "cs alagoano",
}


def obter_env_obrigatoria(nome: str) -> str:
    valor = os.environ.get(nome)
    if not valor:
        sys.exit(f"Configure {nome} antes de rodar.")
    return valor


def normalizar(s: str) -> str:
    s = s.lower()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return ALIASES_MANUAIS.get(s, s)


def nomes_batem(a: str, b: str) -> bool:
    a, b = normalizar(a), normalizar(b)
    if not a or not b:
        return False
    if a == b:
        return True
    ta, tb = set(a.split()), set(b.split())
    return ta <= tb or tb <= ta


def carregar_partidas(supabase) -> dict[str, list[dict]]:
    resposta = (
        supabase.table("matches")
        .select("id, season, home:teams!matches_home_team_id_fkey(name), away:teams!matches_away_team_id_fkey(name)")
        .eq("league_id", LEAGUE_ID)
        .execute()
    )
    por_temporada: dict[str, list[dict]] = {}
    for linha in resposta.data:
        por_temporada.setdefault(linha["season"], []).append({
            "id": linha["id"], "home_name": linha["home"]["name"], "away_name": linha["away"]["name"],
        })
    return por_temporada


def main() -> None:
    url = obter_env_obrigatoria("SUPABASE_URL")
    key = obter_env_obrigatoria("SUPABASE_KEY")
    supabase = create_client(url, key)

    if not CSV_PATH.exists():
        sys.exit(f"CSV não encontrado: {CSV_PATH}")

    por_temporada = carregar_partidas(supabase)
    linhas_csv = list(csv.DictReader(open(CSV_PATH, encoding="utf-8")))

    registros = []
    casados, sem_match, incompletas = 0, 0, 0
    for row in linhas_csv:
        candidatos = por_temporada.get(row["temporada"], [])
        achado = next(
            (m for m in candidatos if nomes_batem(row["time_casa"], m["home_name"]) and nomes_batem(row["time_fora"], m["away_name"])),
            None,
        )
        if not achado:
            sem_match += 1
            continue
        casados += 1

        odds = {"home": row.get("odd_casa"), "draw": row.get("odd_empate"), "away": row.get("odd_fora")}
        if not all(odds.values()):
            incompletas += 1
            continue

        for selecao, valor in odds.items():
            registros.append({
                "match_id": achado["id"], "bookmaker": BOOKMAKER, "market": "1X2",
                "selection": selecao, "odds": float(valor), "snapshot": "closing",
                "origem": BOOKMAKER,
            })

    print(f"{len(linhas_csv)} linhas no CSV -- {casados} casadas, {sem_match} sem partida correspondente, "
          f"{incompletas} casadas mas com odd faltando (não gravadas)")

    if not registros:
        sys.exit("Nada pra gravar.")

    match_ids = sorted({r["match_id"] for r in registros})
    for i in range(0, len(match_ids), 200):
        supabase.table("odds_market").delete().eq("bookmaker", BOOKMAKER).in_("match_id", match_ids[i:i + 200]).execute()

    for i in range(0, len(registros), 500):
        supabase.table("odds_market").insert(registros[i:i + 500]).execute()

    print(f"{len(registros)} linhas de odds gravadas em odds_market (bookmaker='{BOOKMAKER}') "
          f"pra {len(match_ids)} partidas.")


if __name__ == "__main__":
    main()
