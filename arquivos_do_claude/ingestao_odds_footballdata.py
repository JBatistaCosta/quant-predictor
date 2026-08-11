"""
Ingestão de odds reais de mercado via football-data.co.uk -> tabela
odds_market no Supabase.

Fonte 100% gratuita, sem chave de API, sem limite de requisições — é só
um CSV estático por liga/temporada. Cobre as 5 ligas europeias (sem
Brasileirão, que não está no escopo dessa fonte).

Grava 4 "casas" por partida: Bet365, Pinnacle, William Hill e a MÉDIA do
mercado (Avg — consenso entre várias casas, ótimo pra comparar com a odd
justa do seu modelo sem depender de uma única casa específica). Mercados:
1X2 e over/under 2.5 gols.

Uso:
    pip install requests supabase pandas
    set SUPABASE_KEY=sua_service_role_key   (cmd, sem aspas)

    python ingestao_odds_footballdata.py PL 2023
    python ingestao_odds_footballdata.py PD 2023
    ... (uma liga+temporada por vez)
"""

import io
import os
import sys
import unicodedata
from difflib import get_close_matches

import pandas as pd
import requests
from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://cgurxgfdmpmsnrshqycx.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNndXJ4Z2ZkbXBtc25yc2hxeWN4Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4MzM0NTU3NiwiZXhwIjoyMDk4OTIxNTc2fQ.FFp-jjSWJYS-2u_0sOdJzPIcJdDfE_wSfw_Kr11H8Us")

# Nosso código de liga -> código de divisão do football-data.co.uk
LIGAS = {
    "PL":  "E0",
    "PD":  "SP1",
    "SA":  "I1",
    "BL1": "D1",
    "FL1": "F1",
}

# (prefixo no CSV, nome de exibição) das casas que vamos gravar
BOOKMAKERS = [
    ("B365", "bet365"),
    ("PS", "pinnacle"),
    ("WH", "william_hill"),
    ("Avg", "media_mercado"),
]

# football-data.co.uk usa nomes de time em inglês/estilo próprio — mais
# uma convenção diferente de todas as anteriores. Aliases conhecidos de
# antemão (baseado em padrões típicos dessa fonte); avisos novos vão
# aparecer na primeira raspagem de cada liga, igual sempre.
ALIASES_MANUAIS = {
    "man united": "Manchester United FC",
    "man city": "Manchester City FC",
    "nott m forest": "Nottingham Forest FC",
    "wolves": "Wolverhampton Wanderers FC",
    "spurs": "Tottenham Hotspur FC",
    "ath madrid": "Club Atlético de Madrid",
    "ath bilbao": "Athletic Club",
    "sociedad": "Real Sociedad de Fútbol",
    "betis": "Real Betis Balompié",
    "espanol": "RCD Espanyol de Barcelona",
    "vallecano": "Rayo Vallecano de Madrid",
    "celta": "RC Celta de Vigo",
    "alaves": "Deportivo Alavés",
    "gladbach": "Borussia Mönchengladbach",
    "m gladbach": "Borussia Mönchengladbach",   # essa fonte usa "M'gladbach"
    "leverkusen": "Bayer 04 Leverkusen",
    "ein frankfurt": "Eintracht Frankfurt",
    "hertha": "Hertha BSC",
    "fc koln": "1. FC Köln",
    "hamburg": "Hamburger SV",
    "paris sg": "Paris Saint-Germain FC",
    "inter": "FC Internazionale Milano",
    "brest": "Stade Brestois 29",
    "lyon": "Olympique Lyonnais",
    "rennes": "Stade Rennais FC 1901",
}
_TOKENS_IGNORADOS = {
    "fc", "cf", "afc", "fbpa", "fbc", "sc", "ac", "ssc", "as", "rc", "cd",
    "ud", "rcd", "ec", "ca", "cr", "se", "club", "clube",
    "1846", "1904", "04", "05", "96",
}


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


def match_times(norm_fb: str, norm_para_id: dict):
    if norm_fb in norm_para_id:
        return norm_fb, "exato"
    alias_bruto = ALIASES_MANUAIS.get(norm_fb)
    if alias_bruto is not None:
        alias = normalizar(alias_bruto)
        if alias in norm_para_id:
            return alias, "alias"
    tokens_fb = set(norm_fb.split())
    for candidato in norm_para_id:
        tokens_c = set(candidato.split())
        if tokens_fb and tokens_c and (tokens_fb <= tokens_c or tokens_c <= tokens_fb):
            return candidato, "subconjunto"
    prox = get_close_matches(norm_fb, list(norm_para_id), n=1, cutoff=0.8)
    if prox:
        return prox[0], "fuzzy (confira!)"
    return None, "falhou"


def temporada_footballdata(nossa: str) -> str:
    """'2023' -> '2324' (sem hífen — confirmado no formato real da URL)."""
    ano = int(nossa)
    return f"{str(ano)[-2:]}{str(ano + 1)[-2:]}"


def main():
    if "COLE_SUA" in SUPABASE_KEY:
        sys.exit("Configure SUPABASE_KEY antes de rodar.")
    if len(sys.argv) < 3 or sys.argv[1] not in LIGAS:
        sys.exit(f"Uso: python {sys.argv[0]} <LIGA> <TEMPORADA>\nLigas: {', '.join(LIGAS)}")

    liga_cod, temporada = sys.argv[1], sys.argv[2]
    div = LIGAS[liga_cod]
    temp_fd = temporada_footballdata(temporada)

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    liga_row = supabase.table("leagues").select("id").eq("external_id", liga_cod).execute().data
    if not liga_row:
        sys.exit(f"Liga {liga_cod} não encontrada no banco.")
    liga_id = liga_row[0]["id"]

    jogos, inicio = [], 0
    while True:
        lote = (supabase.table("matches")
                .select("id, match_date, home_team_id, away_team_id")
                .eq("league_id", liga_id).eq("season", temporada)
                .range(inicio, inicio + 999).execute().data)
        jogos.extend(lote)
        if len(lote) < 1000:
            break
        inicio += 1000
    if not jogos:
        sys.exit(f"Nenhum jogo de {liga_cod}/{temporada} no banco.")

    # Idempotência: pula partidas que já têm odds gravadas (sem isso, rodar
    # de novo pra pegar só o fim de uma temporada em andamento -- ex.: o CSV
    # da fonte foi baixado no meio do campeonato e a segunda metade só ficou
    # disponível depois -- duplicava as linhas já gravadas, já que o INSERT
    # no fim do script não é upsert).
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

    nossos = pd.DataFrame(jogos)
    nossos["data"] = pd.to_datetime(nossos["match_date"], utc=True).dt.date

    ids_times = {j["home_team_id"] for j in jogos} | {j["away_team_id"] for j in jogos}
    times_rows = (supabase.table("teams").select("id, name")
                  .in_("id", list(ids_times)).execute().data)
    norm_para_id = {normalizar(t["name"]): t["id"] for t in times_rows}
    nome_por_id = {t["id"]: t["name"] for t in times_rows}

    url = f"https://www.football-data.co.uk/mmz4281/{temp_fd}/{div}.csv"
    print(f"Baixando odds de {url} ...")
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text))

    faltando = [c for c in ("Date", "HomeTeam", "AwayTeam") if c not in df.columns]
    if faltando:
        print("  [DIAGNÓSTICO] colunas disponíveis:", sorted(df.columns.tolist()))
        sys.exit(f"Faltam colunas esperadas: {faltando}")

    df["_data"] = pd.to_datetime(df["Date"], dayfirst=True).dt.date

    mapa_times = {}
    for nome_fd in pd.concat([df["HomeTeam"], df["AwayTeam"]]).dropna().unique():
        norm = normalizar(str(nome_fd))
        banco_norm, metodo = match_times(norm, norm_para_id)
        if banco_norm is None:
            print(f"  AVISO: time sem correspondência: '{nome_fd}'")
            continue
        mapa_times[nome_fd] = norm_para_id[banco_norm]
        if "confira" in metodo:
            print(f"  ATENÇÃO ({metodo}): '{nome_fd}' -> '{nome_por_id[mapa_times[nome_fd]]}'")

    registros, sem_match, sem_odds, ja_gravadas = [], 0, 0, 0
    for _, linha in df.iterrows():
        home_id = mapa_times.get(linha["HomeTeam"])
        away_id = mapa_times.get(linha["AwayTeam"])
        if home_id is None or away_id is None:
            continue

        cand = nossos[
            (nossos["home_team_id"] == home_id) & (nossos["away_team_id"] == away_id)
            & (abs(pd.to_datetime(nossos["data"]) - pd.to_datetime(linha["_data"])).dt.days <= 3)
        ]
        if cand.empty:
            sem_match += 1
            continue
        match_id = int(cand.iloc[0]["id"])
        if match_id in ja_tem_odds:
            ja_gravadas += 1
            continue

        teve_odds = False
        for prefixo, nome_casa in BOOKMAKERS:
            colunas_1x2 = {"home": f"{prefixo}H", "draw": f"{prefixo}D", "away": f"{prefixo}A"}
            for selecao, col in colunas_1x2.items():
                if col in df.columns:
                    v = linha.get(col)
                    if v is not None and not pd.isna(v):
                        registros.append({"match_id": match_id, "bookmaker": nome_casa,
                                          "market": "1X2", "selection": selecao,
                                          "odds": round(float(v), 3)})
                        teve_odds = True

            # Pinnacle usa prefixo "PS" pro 1X2 mas só "P" (sem o S) pro
            # over/under — inconsistência da própria fonte, não nossa.
            prefixo_ou = "P" if prefixo == "PS" else prefixo
            colunas_ou = {"over": f"{prefixo_ou}>2.5", "under": f"{prefixo_ou}<2.5"}
            for selecao, col in colunas_ou.items():
                if col in df.columns:
                    v = linha.get(col)
                    if v is not None and not pd.isna(v):
                        registros.append({"match_id": match_id, "bookmaker": nome_casa,
                                          "market": "over_under_2.5", "selection": selecao,
                                          "odds": round(float(v), 3)})
                        teve_odds = True
        if not teve_odds:
            sem_odds += 1

    for i in range(0, len(registros), 500):
        supabase.table("odds_market").insert(registros[i : i + 500]).execute()

    print(f"\n{liga_cod}/{temporada}: {len(registros)} linhas de odds gravadas "
          f"({sem_match} partidas sem correspondência, {sem_odds} partidas casadas sem odds na fonte, "
          f"{ja_gravadas} já tinham odds gravadas antes).")


if __name__ == "__main__":
    main()
