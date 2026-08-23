"""
Script Utilitário para Ingestão de Odds Históricas via CSV Customizado (Kaggle / OddsPortal / Flashscore)
========================================================================================================

Este script permite importar odds históricas de arquivos CSV locais (obtidos em fontes como Kaggle,
OddsPortal ou Flashscore) diretamente para a tabela `odds_market` do Supabase.

Estrutura esperada do CSV (colunas mínimas ou personalizáveis):
  - data (YYYY-MM-DD ou DD/MM/YYYY)
  - home_team (nome do time mandante)
  - away_team (nome do time visitante)
  - odds_home (odd vitória mandante 1X2)
  - odds_draw (odd empate 1X2)
  - odds_away (odd vitória visitante 1X2)
  - bookmaker (opcional: pinnacle, bet365, etc. Padrão: 'bet365')

Uso:
  python scripts/ingestar_odds_csv_customizado.py --csv caminho/para/seu_arquivo.csv --liga-id 1 --temporada 2025 [--dry-run]
"""

import argparse
import csv
import os
import sys
import unicodedata
from datetime import datetime, timezone
from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://cgurxgfdmpmsnrshqycx.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

env_path = os.path.join(os.path.dirname(__file__), "..", "functions", ".env")
if os.path.exists(env_path):
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("SUPABASE_SERVICE_ROLE_KEY=") or line.startswith("SUPABASE_KEY="):
                val = line.split("=", 1)[1].strip().strip("'\"")
                if val and not SUPABASE_KEY:
                    SUPABASE_KEY = val

def normalizar_texto(s: str) -> str:
    if not s: return ""
    s = s.lower().replace("-", " ").replace(".", " ").replace("'", " ")
    s = unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode("utf-8")
    tokens = [t for t in s.split() if t not in ["fc", "cf", "afc", "fbpa", "fbc", "sc", "ac", "ec", "ca", "cr", "se", "club", "clube"]]
    return "".join(tokens) if tokens else s.replace(" ", "")

def nomes_batem(a: str, b: str) -> bool:
    na, nb = normalizar_texto(a), normalizar_texto(b)
    return na in nb or nb in na

def main():
    parser = argparse.ArgumentParser(description="Ingestão de Odds Históricas via CSV local")
    parser.add_argument("--csv", required=True, help="Caminho para o arquivo CSV de odds")
    parser.add_argument("--liga-id", type=int, default=1, help="ID da liga no banco (1 = Brasileirão Série A)")
    parser.add_argument("--temporada", default="2025", help="Temporada alvo (ex: 2025)")
    parser.add_argument("--bookmaker", default="bet365", help="Casa de apostas padrão caso não esteja no CSV")
    parser.add_argument("--dry-run", action="store_true", help="Simular sem gravar no banco de dados")
    args = parser.parse_args()

    if not os.path.exists(args.csv):
        sys.exit(f"ERRO: Arquivo CSV '{args.csv}' não encontrado.")

    if not SUPABASE_KEY:
        sys.exit("ERRO: SUPABASE_KEY não informada no ambiente ou .env.")

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    print("=== INGESTÃO DE ODDS HISTÓRICAS DE ARQUIVO CSV LOCAL ===")
    print(f"Arquivo CSV: {args.csv}")
    print(f"Liga ID: {args.liga_id} | Temporada: {args.temporada} | Dry Run: {args.dry_run}")

    # Carregar partidas do banco para a temporada
    matches = supabase.table("matches").select(
        "id, match_date, home_team_id, away_team_id, "
        "home:teams!matches_home_team_id_fkey(name), "
        "away:teams!matches_away_team_id_fkey(name)"
    ).eq("league_id", args.liga_id).eq("season", args.temporada).execute().data or []

    if not matches:
        sys.exit(f"Nenhuma partida encontrada no banco para a Liga {args.liga_id}, Temporada {args.temporada}.")

    print(f"Partidas cadastradas na base para a temporada {args.temporada}: {len(matches)}")

    # Ler o CSV local
    linhas_novas = []
    agora_iso = datetime.now(timezone.utc).isoformat()
    jogos_casados = 0

    with open(args.csv, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=";" if ";" in f.readline() else ",")
        f.seek(0)
        reader = csv.DictReader(f, delimiter=";" if ";" in f.readline() else ",")
        
        for row in reader:
            # Buscar colunas flexíveis de time e data
            home_csv = row.get("home_team") or row.get("Home") or row.get("HomeTeam") or row.get("Mandante")
            away_csv = row.get("away_team") or row.get("Away") or row.get("AwayTeam") or row.get("Visitante")
            data_csv = row.get("match_date") or row.get("data") or row.get("Date") or row.get("Data")

            odd_h = row.get("odds_home") or row.get("B365H") or row.get("PSH") or row.get("OddH") or row.get("1")
            odd_d = row.get("odds_draw") or row.get("B365D") or row.get("PSD") or row.get("OddD") or row.get("X")
            odd_a = row.get("odds_away") or row.get("B365A") or row.get("PSA") or row.get("OddA") or row.get("2")

            if not home_csv or not away_csv or not odd_h:
                continue

            # Casar com a partida do banco
            match_encontrado = None
            for m in matches:
                h_banco = m.get("home", {}).get("name") if m.get("home") else ""
                a_banco = m.get("away", {}).get("name") if m.get("away") else ""
                
                if nomes_batem(h_banco, home_csv) and nomes_batem(a_banco, away_csv):
                    match_encontrado = m
                    break

            if match_encontrado:
                bkm = row.get("bookmaker") or args.bookmaker
                m_id = match_encontrado["id"]
                jogos_casados += 1

                for sel, val in [("home", odd_h), ("draw", odd_d), ("away", odd_a)]:
                    if val:
                        try:
                            val_float = float(str(val).replace(",", "."))
                            linhas_novas.append({
                                "match_id": m_id,
                                "bookmaker": bkm.lower(),
                                "market": "1X2",
                                "selection": sel,
                                "odds": val_float,
                                "snapshot": "closing",
                                "captured_at": agora_iso
                            })
                        except ValueError:
                            pass

    print(f"\nPartidas casadas no CSV: {jogos_casados}")
    print(f"Total de registros de odds 1X2 gerados: {len(linhas_novas)}")

    if not args.dry_run and linhas_novas:
        print("Gravando odds na tabela odds_market...")
        for i in range(0, len(linhas_novas), 500):
            supabase.table("odds_market").insert(linhas_novas[i:i+500]).execute()
        print("-> Gravação concluída com sucesso!")
    elif args.dry_run:
        print("Modo DRY-RUN: Nenhuma alteração foi persistida no banco de dados.")

if __name__ == "__main__":
    main()
