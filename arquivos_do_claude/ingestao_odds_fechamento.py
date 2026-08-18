"""
Backfill de odds de FECHAMENTO REAL (não confundir com o que já temos —
aquilo é "pre_closing", capturado dias antes do jogo). As colunas de
fechamento no football-data.co.uk têm sufixo "C" (B365CH, PSCH, AvgCH...)
e representam a última odd antes do apito inicial — o padrão-ouro pra
testar se um modelo tem vantagem preditiva real (Closing Line Value/CLV).

Já está no mesmo CSV que usamos pra tudo — não precisa de fonte nova.

Por decisão do projeto, só interessa 2025 em diante (treino do modelo
já está fixado em temporadas anteriores; comparar com fechamento só faz
sentido daqui pra frente, não retroativo ao treino).

Uso:
    pip install requests supabase pandas
    set SUPABASE_KEY=sua_service_role_key   (cmd, sem aspas)

    python ingestao_odds_fechamento.py PL 2025
    python ingestao_odds_fechamento.py PD 2025
    ... (uma liga por vez; adicione outras temporadas/ligas se decidir
    depois que vale a pena)
"""

import io
import os
import sys
import unicodedata
from difflib import get_close_matches

import pandas as pd
import requests
from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]


LIGAS = {"PL": "E0", "PD": "SP1", "SA": "I1", "BL1": "D1", "FL1": "F1"}

BOOKMAKERS = [("B365", "bet365"), ("PS", "pinnacle"), ("Avg", "media_mercado")]

# BUG REAL corrigido: este script não tinha NENHUMA checagem de
# idempotência -- rodar de novo pra pegar temporada em andamento
# duplicava as linhas já gravadas. Também precisa distinguir de origem
# de outra fonte (pinnacle/bet365 colidem com a OddsPapi). Escopado por
# origem=ORIGEM + snapshot='closing' (pra não confundir com o
# pre_closing do script irmão que usa a mesma origem).
ORIGEM = "football_data_co_uk"

ALIASES_MANUAIS = {
    "man united": "Manchester United FC", "man city": "Manchester City FC",
    "nott m forest": "Nottingham Forest FC", "wolves": "Wolverhampton Wanderers FC",
    "spurs": "Tottenham Hotspur FC", "ath madrid": "Club Atlético de Madrid",
    "ath bilbao": "Athletic Club", "sociedad": "Real Sociedad de Fútbol",
    "betis": "Real Betis Balompié", "espanol": "RCD Espanyol de Barcelona",
    "vallecano": "Rayo Vallecano de Madrid", "celta": "RC Celta de Vigo",
    "alaves": "Deportivo Alavés", "gladbach": "Borussia Mönchengladbach",
    "m gladbach": "Borussia Mönchengladbach", "leverkusen": "Bayer 04 Leverkusen",
    "ein frankfurt": "Eintracht Frankfurt", "hertha": "Hertha BSC",
    "fc koln": "1. FC Köln", "cologne": "1. FC Köln", "hamburg": "Hamburger SV",
    "paris sg": "Paris Saint-Germain FC", "inter": "FC Internazionale Milano",
    "brest": "Stade Brestois 29", "lyon": "Olympique Lyonnais",
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

    nossos = pd.DataFrame(jogos)
    nossos["data"] = pd.to_datetime(nossos["match_date"], utc=True).dt.date

    ids_times = {j["home_team_id"] for j in jogos} | {j["away_team_id"] for j in jogos}
    times_rows = (supabase.table("teams").select("id, name")
                  .in_("id", list(ids_times)).execute().data)
    norm_para_id = {normalizar(t["name"]): t["id"] for t in times_rows}
    nome_por_id = {t["id"]: t["name"] for t in times_rows}

    url = f"https://www.football-data.co.uk/mmz4281/{temp_fd}/{div}.csv"
    print(f"Baixando odds de fechamento de {url} ...")
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text))

    faltando = [c for c in ("Date", "HomeTeam", "AwayTeam", "B365CH") if c not in df.columns]
    if faltando:
        print("  [DIAGNÓSTICO] colunas de fechamento não encontradas nesta temporada/liga.")
        print("  colunas disponíveis:", sorted(df.columns.tolist()))
        sys.exit("Essa temporada pode não ter odds de fechamento registradas.")

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

    ids_jogos = [j["id"] for j in jogos]
    ja_tem_odds = set()
    inicio = 0
    while True:
        lote = (supabase.table("odds_market").select("match_id")
                .eq("origem", ORIGEM).eq("snapshot", "closing")
                .in_("match_id", ids_jogos)
                .range(inicio, inicio + 999).execute().data)
        ja_tem_odds.update(r["match_id"] for r in lote)
        if len(lote) < 1000:
            break
        inicio += 1000
    if ja_tem_odds:
        print(f"  {len(ja_tem_odds)} partida(s) já com odds de fechamento desta fonte -- serão puladas.")

    registros, sem_match = [], 0
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
            continue

        for prefixo, nome_casa in BOOKMAKERS:
            for selecao, col in [("home", f"{prefixo}CH"), ("draw", f"{prefixo}CD"), ("away", f"{prefixo}CA")]:
                if col in df.columns:
                    v = linha.get(col)
                    if v is not None and not pd.isna(v):
                        registros.append({"match_id": match_id, "bookmaker": nome_casa,
                                          "market": "1X2", "selection": selecao,
                                          "odds": round(float(v), 3), "snapshot": "closing",
                                          "origem": ORIGEM})
            prefixo_ou = "P" if prefixo == "PS" else prefixo
            for selecao, col in [("over", f"{prefixo_ou}C>2.5"), ("under", f"{prefixo_ou}C<2.5")]:
                if col in df.columns:
                    v = linha.get(col)
                    if v is not None and not pd.isna(v):
                        registros.append({"match_id": match_id, "bookmaker": nome_casa,
                                          "market": "over_under_2.5", "selection": selecao,
                                          "odds": round(float(v), 3), "snapshot": "closing",
                                          "origem": ORIGEM})

    for i in range(0, len(registros), 500):
        supabase.table("odds_market").insert(registros[i : i + 500]).execute()

    print(f"\n{liga_cod}/{temporada}: {len(registros)} linhas de odds de FECHAMENTO gravadas "
          f"({sem_match} partidas sem correspondência).")


if __name__ == "__main__":
    main()
