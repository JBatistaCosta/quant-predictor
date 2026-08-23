"""
Ingestão do CSV normalizado do OddsPortal (ver capturar_odds_oddsportal.py)
-> tabela odds_market no Supabase.

Mesmo padrão de casamento dos outros scripts de odds do projeto (ver
ingestao_odds_footiqo.py): normaliza nome de time, casa por
(home_team, away_team, data ±3 dias) dentro da liga informada, pula
partidas que já têm QUALQUER odd gravada (idempotente), grava em lotes.

Diferença das outras fontes: o CSV já vem no formato "longo" (uma linha
por casa/mercado/seleção, colunas bookmaker/market/selection/odds já
prontas) -- não tem parsing de mercado aqui, só casamento de partida/time.

Uso:
    pip install pandas supabase
    set SUPABASE_KEY=sua_service_role_key

    python ingestao_odds_oddsportal.py arquivos_do_claude/odds_oddsportal/libertadores_2024.csv --liga-id 23
    python ingestao_odds_oddsportal.py <csv> --liga-id 23 --dry-run
"""

import argparse
import os
import sys
import unicodedata
from difflib import get_close_matches

import pandas as pd
from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://cgurxgfdmpmsnrshqycx.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

TOLERANCIA_DIAS = 3

_TOKENS_IGNORADOS = {
    "fc", "cf", "afc", "sc", "ec", "ca", "cr", "cd", "cdc", "cdp", "csd", "se",
    "club", "clube", "as", "ac", "rc", "rb", "aa", "car", "csc", "fbpa",
}

# Chave = nome normalizado como o OddsPortal escreve; valor = nome (bruto) do
# NOSSO banco. Fica vazio até a primeira raspagem real reportar os times sem
# correspondência -- mesma disciplina dos outros scripts (nunca adivinhar
# alias sem ver o dado real primeiro).
ALIASES_MANUAIS: dict[str, str] = {}


def normalizar(nome: str) -> str:
    for ch in ("-", "–", "—", ".", "'", "&"):
        nome = nome.replace(ch, " ")
    nome = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode()
    nome = nome.lower()
    tokens_originais = nome.split()
    tokens = [t for t in tokens_originais if t not in _TOKENS_IGNORADOS]
    if not tokens:
        tokens = tokens_originais
    return " ".join(tokens)


def match_times(norm_op: str, norm_para_id: dict):
    if norm_op in norm_para_id:
        return norm_op, "exato"
    alias_bruto = ALIASES_MANUAIS.get(norm_op)
    if alias_bruto is not None:
        alias = normalizar(alias_bruto)
        if alias in norm_para_id:
            return alias, "alias"
    tokens_op = set(norm_op.split())
    for candidato in norm_para_id:
        tokens_c = set(candidato.split())
        if tokens_op and tokens_c and (tokens_op <= tokens_c or tokens_c <= tokens_op):
            return candidato, "subconjunto"
    prox = get_close_matches(norm_op, list(norm_para_id), n=1, cutoff=0.8)
    if prox:
        return prox[0], "fuzzy (confira!)"
    return None, "falhou"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path")
    parser.add_argument("--liga-id", type=int, required=True, help="id de public.leagues")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not SUPABASE_KEY:
        sys.exit("Configure SUPABASE_KEY antes de rodar.")
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    df = pd.read_csv(args.csv_path)
    faltando = [c for c in ("match_date", "home_team", "away_team", "bookmaker", "market", "selection", "odds") if c not in df.columns]
    if faltando:
        print("  [DIAGNÓSTICO] colunas disponíveis:", sorted(df.columns.tolist()))
        sys.exit(f"Faltam colunas esperadas: {faltando}")

    # match_date vem como "YYYY-MM-DD HH:MM:SS UTC" (formato do OddsHarvester).
    df["_data"] = pd.to_datetime(df["match_date"], format="mixed", utc=True).dt.date
    print(f"Arquivo: {len(df)} linhas de odds, {df[['home_team', 'away_team', '_data']].drop_duplicates().shape[0]} partida(s) únicas.")

    jogos, inicio = [], 0
    while True:
        lote = (supabase.table("matches")
                .select("id, match_date, home_team_id, away_team_id")
                .eq("league_id", args.liga_id)
                .range(inicio, inicio + 999).execute().data)
        jogos.extend(lote)
        if len(lote) < 1000:
            break
        inicio += 1000
    if not jogos:
        sys.exit(f"Nenhum jogo da liga_id={args.liga_id} no banco.")

    nossos = pd.DataFrame(jogos)
    nossos["data"] = pd.to_datetime(nossos["match_date"], utc=True).dt.date

    ids_times = {j["home_team_id"] for j in jogos} | {j["away_team_id"] for j in jogos}
    times_rows = (supabase.table("teams").select("id, name")
                  .in_("id", list(ids_times)).execute().data)
    norm_para_id = {normalizar(t["name"]): t["id"] for t in times_rows}
    nome_por_id = {t["id"]: t["name"] for t in times_rows}

    mapa_times = {}
    for nome_op in pd.concat([df["home_team"], df["away_team"]]).dropna().unique():
        norm = normalizar(str(nome_op))
        banco_norm, metodo = match_times(norm, norm_para_id)
        if banco_norm is None:
            print(f"  AVISO: time sem correspondência: '{nome_op}'")
            continue
        mapa_times[nome_op] = norm_para_id[banco_norm]
        if "confira" in metodo:
            print(f"  ATENÇÃO ({metodo}): '{nome_op}' -> '{nome_por_id[mapa_times[nome_op]]}'")

    ids_jogos = [j["id"] for j in jogos]
    ja_tem_odds = set()
    inicio = 0
    while True:
        lote = (supabase.table("odds_market").select("match_id")
                .in_("match_id", ids_jogos)
                .range(inicio, inicio + 999).execute().data)
        ja_tem_odds.update(r["match_id"] for r in lote)
        if len(lote) < 1000:
            break
        inicio += 1000
    if ja_tem_odds:
        print(f"  {len(ja_tem_odds)} partida(s) já com odds gravadas -- serão puladas.")

    # Uma resolução de match_id por (home_team, away_team, _data) só, reaproveitada
    # pra todas as linhas de odds daquela partida (evita repetir a busca por linha).
    cache_match_id: dict[tuple, int | None] = {}

    def resolver_match_id(home_raw, away_raw, data):
        chave = (home_raw, away_raw, data)
        if chave in cache_match_id:
            return cache_match_id[chave]
        home_id = mapa_times.get(home_raw)
        away_id = mapa_times.get(away_raw)
        if home_id is None or away_id is None:
            cache_match_id[chave] = None
            return None
        cand = nossos[
            (nossos["home_team_id"] == home_id) & (nossos["away_team_id"] == away_id)
            & (abs(pd.to_datetime(nossos["data"]) - pd.to_datetime(data)).dt.days <= TOLERANCIA_DIAS)
        ]
        resultado = int(cand.iloc[0]["id"]) if not cand.empty else None
        cache_match_id[chave] = resultado
        return resultado

    registros, sem_match, ja_gravadas = [], 0, 0
    for _, linha in df.iterrows():
        match_id = resolver_match_id(linha["home_team"], linha["away_team"], linha["_data"])
        if match_id is None:
            sem_match += 1
            continue
        if match_id in ja_tem_odds:
            ja_gravadas += 1
            continue
        odd = linha.get("odds")
        if pd.isna(odd):
            continue
        registros.append({
            "match_id": match_id,
            "bookmaker": str(linha["bookmaker"]),
            "market": str(linha["market"]),
            "selection": str(linha["selection"]),
            "odds": round(float(odd), 3),
            "snapshot": "closing",
        })

    registros = list({(r["match_id"], r["bookmaker"], r["market"], r["selection"]): r for r in registros}.values())

    print(f"\n{len(registros)} linhas de odds prontas pra gravar "
          f"({sem_match} linhas sem correspondência de partida, {ja_gravadas} linhas de partidas já com odds gravadas antes).")

    if args.dry_run:
        print("--dry-run: nada foi gravado.")
        return

    for i in range(0, len(registros), 500):
        supabase.table("odds_market").insert(registros[i:i + 500]).execute()
    print("Gravado em odds_market.")


if __name__ == "__main__":
    main()
