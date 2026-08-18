"""
Script de Ingestão de Odds Históricas do Brasileirão (2012-2024)
Fonte Kaggle: felipebandeiraramos/brazilian-soccer-odds-data (final_df_kaggle.csv)
================================================================================

Este script ingere odds históricas de 1X2 (Pinnacle + Média de Mercado) e Over/Under 2.5 Gols
do dataset do Kaggle para a tabela `odds_market` do Supabase para as temporadas do Brasileirão.

Uso:
  python scripts/ingestar_felipe_kaggle_odds.py [--temporadas 2018 2019 2020 2021 2022 2023 2024] [--dry-run]
"""

import argparse
import os
import sys
import unicodedata
from datetime import datetime, timezone
import pandas as pd
from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://cgurxgfdmpmsnrshqycx.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# BUG REAL corrigido: o delete antes do insert apagava TODAS as odds da
# partida (in_("match_id", lote), sem filtro de bookmaker/origem) --
# rodar este script de novo destruiria os 90+ mercados que o backfill da
# OddsPapi importa pra cada partida do Brasileirão, porque "pinnacle" é o
# MESMO nome de bookmaker usado pelas duas fontes. Escopado por
# origem=ORIGEM agora -- só apaga/reprocessa o que o PRÓPRIO script já
# gravou antes, nunca o de outra fonte.
ORIGEM = "kaggle_felipe"

env_path = os.path.join(os.path.dirname(__file__), "..", "functions", ".env")
if os.path.exists(env_path):
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("SUPABASE_SERVICE_ROLE_KEY=") or line.startswith("SUPABASE_KEY="):
                val = line.split("=", 1)[1].strip().strip("'\"")
                if val and not SUPABASE_KEY:
                    SUPABASE_KEY = val

ALIASES = {
    "atletico mg": "atleticomineiro",
    "atletico-mg": "atleticomineiro",
    "atletico pr": "athleticoparanaense",
    "atletico-pr": "athleticoparanaense",
    "athletico-pr": "athleticoparanaense",
    "bragantino": "rbbragantino",
    "red bull bragantino": "rbbragantino",
    "america mg": "americamineiro",
    "america-mg": "americamineiro",
    "botafogo rj": "botafogo",
    "botafogo-rj": "botafogo",
    "sport recife": "sport",
    "atletico go": "atleticogoianiense",
    "atletico-go": "atleticogoianiense",
}

def normalizar_texto(s: str) -> str:
    if not s or pd.isna(s): return ""
    s_clean = str(s).lower().strip()
    if s_clean in ALIASES:
        return ALIASES[s_clean]
    s = s_clean.replace("-", " ").replace(".", " ").replace("'", " ")
    s = unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode("utf-8")
    tokens = [t for t in s.split() if t not in ["fc", "cf", "afc", "fbpa", "fbc", "sc", "ac", "ec", "ca", "cr", "se", "club", "clube", "af", "rj", "mg", "pr", "go", "sp", "rs", "pa", "pe", "ce", "ba"]]
    res = "".join(tokens) if tokens else s.replace(" ", "")
    return ALIASES.get(res, res)

def nomes_batem(a: str, b: str) -> bool:
    na, nb = normalizar_texto(a), normalizar_texto(b)
    return na == nb or na in nb or nb in na

def main():
    parser = argparse.ArgumentParser(description="Ingestão de Odds Históricas do Kaggle para Supabase")
    parser.add_argument("--temporadas", nargs="+", default=["2018", "2019", "2020", "2021", "2022", "2023", "2024"], help="Lista de temporadas (ex: 2023 2024)")
    parser.add_argument("--liga-id", type=int, default=1, help="ID da liga (1 = Brasileirão Série A)")
    parser.add_argument("--dry-run", action="store_true", help="Simular sem gravar no banco")
    args = parser.parse_args()

    csv_path = os.path.join(os.path.dirname(__file__), "..", "arquivos_do_claude", "kaggle_downloads", "felipebandeiraramos_brazilian-soccer-odds-data", "final_df_kaggle.csv")

    if not os.path.exists(csv_path):
        sys.exit(f"ERRO: Arquivo '{csv_path}' não foi encontrado. Baixe o dataset primeiro.")

    if not SUPABASE_KEY:
        sys.exit("ERRO: SUPABASE_KEY não informada no ambiente ou .env.")

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    df = pd.read_csv(csv_path)

    print("=== INGESTÃO DE ODDS HISTÓRICAS DO BRASILEIRÃO (KAGGLE) ===")
    print(f"Temporadas Selecionadas: {args.temporadas}")
    print(f"Modo: {'DRY-RUN (Simulação)' if args.dry_run else 'EXECUÇÃO REAL'}")

    agora_iso = datetime.now(timezone.utc).isoformat()
    total_odds_geradas = 0
    total_jogos_casados = 0

    for season_str in args.temporadas:
        try:
            season_int = int(season_str)
        except ValueError:
            continue

        df_season = df[df["Season"] == season_int]
        if df_season.empty:
            print(f"\n[Temporada {season_str}] Nenhuma linha encontrada no CSV.")
            continue

        # Carregar partidas do banco para a temporada
        matches = supabase.table("matches").select(
            "id, match_date, home_team_id, away_team_id, "
            "home:teams!matches_home_team_id_fkey(name), "
            "away:teams!matches_away_team_id_fkey(name)"
        ).eq("league_id", args.liga_id).eq("season", str(season_str)).execute().data or []

        print(f"\n[Temporada {season_str}] CSV: {len(df_season)} jogos | Banco: {len(matches)} jogos")

        linhas_odds_temp = []
        casados_temp = 0

        for _, row in df_season.iterrows():
            h_csv = str(row["Home"])
            a_csv = str(row["Away"])
            date_csv = str(row["Date"]) if pd.notna(row["Date"]) else ""

            match_banco = None
            for m in matches:
                h_banco = m.get("home", {}).get("name") if m.get("home") else ""
                a_banco = m.get("away", {}).get("name") if m.get("away") else ""

                if nomes_batem(h_banco, h_csv) and nomes_batem(a_banco, a_csv):
                    match_banco = m
                    break

            if match_banco:
                m_id = match_banco["id"]
                casados_temp += 1

                # 1. Pinnacle 1X2 Closing (PSCH, PSCD, PSCA)
                if pd.notna(row.get("PSCH")) and pd.notna(row.get("PSCD")) and pd.notna(row.get("PSCA")):
                    try:
                        linhas_odds_temp.extend([
                            {"match_id": m_id, "bookmaker": "pinnacle", "market": "1X2", "selection": "home", "odds": float(row["PSCH"]), "snapshot": "closing", "captured_at": agora_iso, "origem": ORIGEM},
                            {"match_id": m_id, "bookmaker": "pinnacle", "market": "1X2", "selection": "draw", "odds": float(row["PSCD"]), "snapshot": "closing", "captured_at": agora_iso, "origem": ORIGEM},
                            {"match_id": m_id, "bookmaker": "pinnacle", "market": "1X2", "selection": "away", "odds": float(row["PSCA"]), "snapshot": "closing", "captured_at": agora_iso, "origem": ORIGEM},
                        ])
                    except ValueError: pass

                # 2. Média de Mercado 1X2 Closing (AvgCH, AvgCD, AvgCA)
                if pd.notna(row.get("AvgCH")) and pd.notna(row.get("AvgCD")) and pd.notna(row.get("AvgCA")):
                    try:
                        linhas_odds_temp.extend([
                            {"match_id": m_id, "bookmaker": "media_mercado", "market": "1X2", "selection": "home", "odds": float(row["AvgCH"]), "snapshot": "closing", "captured_at": agora_iso, "origem": ORIGEM},
                            {"match_id": m_id, "bookmaker": "media_mercado", "market": "1X2", "selection": "draw", "odds": float(row["AvgCD"]), "snapshot": "closing", "captured_at": agora_iso, "origem": ORIGEM},
                            {"match_id": m_id, "bookmaker": "media_mercado", "market": "1X2", "selection": "away", "odds": float(row["AvgCA"]), "snapshot": "closing", "captured_at": agora_iso, "origem": ORIGEM},
                        ])
                    except ValueError: pass

                # 3. Média de Mercado Over/Under 2.5 (AvgOver2.5, AvgUnder2.5)
                if pd.notna(row.get("AvgOver2.5")) and pd.notna(row.get("AvgUnder2.5")):
                    try:
                        linhas_odds_temp.extend([
                            {"match_id": m_id, "bookmaker": "media_mercado", "market": "over_under_2.5", "selection": "over", "odds": float(row["AvgOver2.5"]), "snapshot": "closing", "captured_at": agora_iso, "origem": ORIGEM},
                            {"match_id": m_id, "bookmaker": "media_mercado", "market": "over_under_2.5", "selection": "under", "odds": float(row["AvgUnder2.5"]), "snapshot": "closing", "captured_at": agora_iso, "origem": ORIGEM},
                        ])
                    except ValueError: pass

        total_jogos_casados += casados_temp
        total_odds_geradas += len(linhas_odds_temp)
        print(f"  -> Jogos casados: {casados_temp}/{len(matches)} | Odds geradas: {len(linhas_odds_temp)}")

        if not args.dry_run and linhas_odds_temp:
            # Apagar SÓ as odds antigas do PRÓPRIO script (origem=ORIGEM)
            # dessas partidas antes de re-inserir -- nunca odds de outra
            # origem (ver comentário em ORIGEM acima).
            m_ids_temp = list({r["match_id"] for r in linhas_odds_temp})
            for i in range(0, len(m_ids_temp), 200):
                lote = m_ids_temp[i:i+200]
                supabase.table("odds_market").delete().eq("origem", ORIGEM).in_("match_id", lote).execute()

            # Gravar lote a lote
            for i in range(0, len(linhas_odds_temp), 500):
                lote_odds = linhas_odds_temp[i:i+500]
                supabase.table("odds_market").insert(lote_odds).execute()
            print(f"  -> Gravação no Supabase concluída para a temporada {season_str}!")

    print(f"\n=== RESUMO GERAL ===")
    print(f"Total de jogos casados com a base: {total_jogos_casados}")
    print(f"Total de odds geradas: {total_odds_geradas}")

if __name__ == "__main__":
    main()
