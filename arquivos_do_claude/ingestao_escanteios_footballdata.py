"""
Backfill de ESCANTEIOS (e de quebra: chutes, chutes no gol, faltas,
cartões) via football-data.co.uk -> match_stats.

Por que isso resolve o buraco dos escanteios: o FBref (usado antes) não
disponibiliza escanteios por partida nesta versão do soccerdata (só em
agregados por temporada — foi por isso que a coluna `corners` ficou
sempre nula até agora). O football-data.co.uk tem (colunas HC/AC), e de
quebra também tem chutes/cartões/faltas — o que preenche de graça a
temporada 2022, que hoje não tem NENHUM dado de match_stats (só rodamos
o FBref pra 2023-2025).

Não mexe em xG nem posse (essa fonte não tem) — só soma escanteios (e
preenche as demais colunas SE ainda estiverem vazias, sem sobrescrever
o que já veio do FBref/Understat).

Uso:
    pip install requests supabase pandas
    set SUPABASE_KEY=sua_service_role_key   (cmd, sem aspas)

    python ingestao_escanteios_footballdata.py PL 2022
    python ingestao_escanteios_footballdata.py PL 2023
    ... (uma liga+temporada por vez; cobre 2019-2025 já ingeridas)
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
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "COLE_SUA_SERVICE_ROLE_KEY_AQUI")

LIGAS = {"PL": "E0", "PD": "SP1", "SA": "I1", "BL1": "D1", "FL1": "F1"}

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


def num_ou_none(v):
    try:
        return None if pd.isna(v) else int(v)
    except (TypeError, ValueError):
        return None


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

    # Estatísticas já existentes (pra não sobrescrever xG/posse do
    # FBref/Understat com nulo por engano — só preenchemos o que falta)
    existentes = {}
    ids_partidas = nossos["id"].tolist()
    for k in range(0, len(ids_partidas), 200):
        lote = (supabase.table("match_stats")
                .select("match_id, team_id, xg, xg_source, possession")
                .in_("match_id", ids_partidas[k : k + 200]).execute().data)
        for r in lote:
            existentes[(r["match_id"], r["team_id"])] = r

    url = f"https://www.football-data.co.uk/mmz4281/{temp_fd}/{div}.csv"
    print(f"Baixando estatísticas de {url} ...")
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text))

    faltando = [c for c in ("Date", "HomeTeam", "AwayTeam", "HC", "AC") if c not in df.columns]
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

        for lado, team_id, col_shots, col_sot, col_fouls, col_corners, col_yellow, col_red in [
            ("home", home_id, "HS", "HST", "HF", "HC", "HY", "HR"),
            ("away", away_id, "AS", "AST", "AF", "AC", "AY", "AR"),
        ]:
            existente = existentes.get((match_id, team_id), {})
            registro = {"match_id": match_id, "team_id": int(team_id)}
            # Escanteios: sempre grava (é o motivo do script)
            registro["corners"] = num_ou_none(linha.get(col_corners))
            # Demais campos: só preenche se ainda estiver vazio (não
            # sobrescreve dado do FBref que já esteja lá)
            for campo, col in [("shots", col_shots), ("shots_on_target", col_sot),
                               ("fouls", col_fouls), ("yellow_cards", col_yellow),
                               ("red_cards", col_red)]:
                if not existente or existente.get(campo) is None:
                    registro[campo] = num_ou_none(linha.get(col))
            registros.append(registro)

    for i in range(0, len(registros), 500):
        supabase.table("match_stats").upsert(
            registros[i : i + 500], on_conflict="match_id,team_id"
        ).execute()

    print(f"\n{liga_cod}/{temporada}: {len(registros)} linhas de estatísticas atualizadas "
          f"({sem_match} partidas sem correspondência).")


if __name__ == "__main__":
    main()
