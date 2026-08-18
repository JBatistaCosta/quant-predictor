"""
Ingestão de resultados HISTÓRICOS (2019-2022) das 5 ligas europeias, via
football-data.co.uk — separado do treino de produção (que continua em
2023-2024, no máximo esticando pra incluir 2022 se decidirem depois).

Objetivo: base mais ampla pra ESTUDAR fatores como mando por time, sem
misturar com os dados usados pra gerar as odds de verdade.

Cobre só as 5 ligas europeias — football-data.co.uk não tem Brasileirão,
e a football-data.org (fonte do Brasileirão) trava no plano gratuito nas
3 temporadas mais recentes, então não dá pra estender o histórico
brasileiro com as fontes já validadas no projeto.

Uso:
    pip install requests supabase pandas
    set SUPABASE_KEY=sua_service_role_key   (cmd, sem aspas)

    python ingestao_historico_ligas.py PL 2019
    python ingestao_historico_ligas.py PL 2020
    ... (uma liga+temporada por vez, igual aos outros scripts)
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

# Mesmos aliases já validados no ingestao_odds_footballdata.py (mesma
# fonte, mesma convenção de nomes) — reaproveitados aqui.
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
    "fc koln": "1. FC Köln", "hamburg": "Hamburger SV",
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

    # Times JÁ conhecidos (de 2023-2025) — usados pra casar nome primeiro;
    # times históricos que não existem mais são criados na hora.
    times_rows = supabase.table("teams").select("id, name").execute().data
    norm_para_id = {normalizar(t["name"]): t["id"] for t in times_rows}
    nome_por_id = {t["id"]: t["name"] for t in times_rows}

    url = f"https://www.football-data.co.uk/mmz4281/{temp_fd}/{div}.csv"
    print(f"Baixando histórico de {url} ...")
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text))

    faltando = [c for c in ("Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG") if c not in df.columns]
    if faltando:
        print("  [DIAGNÓSTICO] colunas disponíveis:", sorted(df.columns.tolist()))
        sys.exit(f"Faltam colunas esperadas: {faltando}")

    df["_data"] = pd.to_datetime(df["Date"], dayfirst=True)
    df = df.dropna(subset=["FTHG", "FTAG"])  # só jogos já disputados

    # Casa (ou CRIA) cada time
    mapa_times = {}
    for nome_fd in pd.concat([df["HomeTeam"], df["AwayTeam"]]).dropna().unique():
        norm = normalizar(str(nome_fd))
        banco_norm, metodo = match_times(norm, norm_para_id)
        if banco_norm is None:
            # Time histórico que não existe no banco (ex: rebaixado antes
            # de 2023 e nunca mais voltou) — cria agora.
            res = (supabase.table("teams").insert({"name": str(nome_fd)}).execute())
            novo_id = res.data[0]["id"]
            norm_para_id[norm] = novo_id
            nome_por_id[novo_id] = str(nome_fd)
            mapa_times[nome_fd] = novo_id
            print(f"  NOVO TIME criado (não existia no banco): '{nome_fd}'")
            continue
        mapa_times[nome_fd] = norm_para_id[banco_norm]
        if "confira" in metodo:
            print(f"  ATENÇÃO ({metodo}): '{nome_fd}' -> '{nome_por_id[mapa_times[nome_fd]]}'")

    registros = []
    for _, linha in df.iterrows():
        home_id = mapa_times.get(linha["HomeTeam"])
        away_id = mapa_times.get(linha["AwayTeam"])
        if home_id is None or away_id is None:
            continue
        # external_id sintético e estável (não vem de nenhuma API) —
        # permite rerun idempotente (upsert) sem duplicar.
        ext_id = f"fdcouk_{div}_{linha['_data'].date().isoformat()}_{home_id}_{away_id}"
        registros.append({
            "external_id": ext_id,
            "league_id": liga_id,
            "season": temporada,
            "match_date": linha["_data"].isoformat(),
            "home_team_id": int(home_id),
            "away_team_id": int(away_id),
            "home_goals": int(linha["FTHG"]),
            "away_goals": int(linha["FTAG"]),
            "status": "finished",
        })

    for i in range(0, len(registros), 500):
        supabase.table("matches").upsert(
            registros[i : i + 500], on_conflict="external_id"
        ).execute()

    print(f"\n{liga_cod}/{temporada}: {len(registros)} partidas históricas gravadas.")


if __name__ == "__main__":
    main()
