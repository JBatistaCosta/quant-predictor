"""
Ingestão do HISTÓRICO DE TÉCNICOS por time (coachHistory do endpoint de time
do FotMob, /api/data/teams?id=X) — alimenta team_coach_history_fotmob, base
das features manager_match_count / is_honeymoon_phase de features_contexto.py.

Granularidade da fonte: TEMPORADA × técnico × liga (V/E/D agregados), NÃO data
exata de troca — a lista vem em ordem cronológica dentro de cada temporada
(observado no payload real: Rodgers antes de Klopp em 2015/2016), e order_idx
preserva essa ordem pra reconstrução aproximada da vigência por partida.

Só times com crosswalk source='fotmob' (mesma disciplina de sempre).
Custo: 1 chamada por time (~210 times, ~5 min com pacing 1.3s).

Uso:
    SUPABASE_URL=... SUPABASE_KEY=... python3 ingestao_fotmob_coaches.py [--limite N]
"""

import argparse
import os
import sys
import time

import requests
from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

BASE = "https://www.fotmob.com/api/data"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
}
PACING_SEGUNDOS = 1.3


def buscar_paginado(supabase, tabela, colunas, filtros=None):
    resultado, pagina = [], 0
    while True:
        q = supabase.table(tabela).select(colunas)
        for chave, valor in (filtros or {}).items():
            q = q.eq(chave, valor)
        chunk = q.range(pagina * 1000, pagina * 1000 + 999).execute().data
        resultado.extend(chunk)
        if len(chunk) < 1000:
            break
        pagina += 1
    return resultado


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limite", type=int, default=None)
    args = ap.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        sys.exit("Defina SUPABASE_URL e SUPABASE_KEY como variáveis de ambiente.")

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    crosswalk = buscar_paginado(supabase, "team_source_ids", "team_id, source_id", {"source": "fotmob"})
    if args.limite:
        crosswalk = crosswalk[: args.limite]
    print(f"{len(crosswalk)} times a processar.")

    ok, falhas = 0, 0
    for i, c in enumerate(crosswalk):
        try:
            r = requests.get(f"{BASE}/teams", params={"id": c["source_id"]}, headers=HEADERS, timeout=20)
            r.raise_for_status()
            historico = ((r.json().get("overview") or {}).get("coachHistory")) or []
        except Exception as e:
            print(f"  falha team_id={c['team_id']}: {e}")
            falhas += 1
            time.sleep(PACING_SEGUNDOS)
            continue

        linhas, vistos = [], {}
        for idx, h in enumerate(historico):
            chave = (str(h.get("id")), h.get("season"), h.get("leagueId"))
            if chave in vistos:
                continue  # dedupe intra-lote (mesma lição do upsert de transferências)
            vistos[chave] = True
            linhas.append({
                "team_id": c["team_id"],
                "coach_fotmob_id": str(h.get("id")),
                "coach_name": h.get("name"),
                "season": h.get("season"),
                "fotmob_league_id": h.get("leagueId"),
                "wins": h.get("win"), "draws": h.get("draw"), "losses": h.get("loss"),
                "order_idx": idx,
            })
        if linhas:
            supabase.table("team_coach_history_fotmob").upsert(
                linhas, on_conflict="team_id,coach_fotmob_id,season,fotmob_league_id"
            ).execute()
        ok += 1
        if (i + 1) % 20 == 0:
            print(f"  {i + 1}/{len(crosswalk)}...")
        time.sleep(PACING_SEGUNDOS)

    print(f"\nOK: {ok} times, {falhas} falhas.")


if __name__ == "__main__":
    main()
