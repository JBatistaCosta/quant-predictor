"""
Backfill do xG em match_stats — corrige a falha anterior (a ingestão
original buscava xG no lugar errado da biblioteca soccerdata; o xG por
partida na verdade vem do método read_schedule(), que traz uma linha por
PARTIDA (não por time), já com home_xg/away_xg prontos).

Não mexe em nenhuma outra coluna: usa upsert só com (match_id, team_id, xg),
o que atualiza apenas o xG e preserva shots/possession/fouls/cards/corners
que já estão corretos.

Uso:
    pip install soccerdata supabase pandas
    set SUPABASE_KEY=sua_service_role_key   (cmd, sem aspas)

    python backfill_xg.py BSA 2023
    python backfill_xg.py PL 2023
    ... (uma liga+temporada por vez, igual ao script de ingestão original)
"""

import os
import sys
import unicodedata
from difflib import get_close_matches

import pandas as pd
import soccerdata as sd
from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

LIGAS = {
    "BSA": ("BRA-Serie A", True),
    "PL":  ("ENG-Premier League", False),
    "PD":  ("ESP-La Liga", False),
    "SA":  ("ITA-Serie A", False),
    "BL1": ("GER-Bundesliga", False),
    "FL1": ("FRA-Ligue 1", False),
}

# Mesma lógica de casamento de nomes já validada no ingestao_stats_fbref.py
ALIASES_MANUAIS = {
    "athletico pr": "ca paranaense",
    "wolves": "Wolverhampton Wanderers FC",
    "gladbach": "Borussia Mönchengladbach",
    "brest": "Stade Brestois 29",
    "lyon": "Olympique Lyonnais",
    "rennes": "Stade Rennais FC 1901",
    "psg": "Paris Saint-Germain FC",
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


def temporada_soccerdata(nossa: str, ano_calendario: bool) -> str:
    ano = int(nossa)
    if ano_calendario:
        return str(ano)
    # Formato com hífen ('23-24'), como documentado oficialmente pelo
    # soccerdata. O formato sem hífen ('2324') que usávamos antes não
    # consta na documentação — pode estar sendo mal interpretado e
    # relacionado a um bug conhecido do read_schedule() (issue #704 no
    # GitHub do projeto) que busca a temporada errada.
    return f"{str(ano)[-2:]}-{str(ano + 1)[-2:]}"


def col(df: pd.DataFrame, *candidatos):
    achatadas = {str(c).lower().strip(): c for c in df.columns}
    for cand in candidatos:
        if cand.lower() in achatadas:
            return achatadas[cand.lower()]
    return None


def main():
    if "COLE_SUA" in SUPABASE_KEY:
        sys.exit("Configure SUPABASE_KEY antes de rodar.")
    if len(sys.argv) < 3 or sys.argv[1] not in LIGAS:
        sys.exit(f"Uso: python {sys.argv[0]} <LIGA> <TEMPORADA>\nLigas: {', '.join(LIGAS)}")

    liga_cod, temporada = sys.argv[1], sys.argv[2]
    liga_sd, ano_cal = LIGAS[liga_cod]
    temp_sd = temporada_soccerdata(temporada, ano_cal)

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

    print(f"Baixando calendário com xG do FBref: {liga_sd} / {temp_sd} (pode demorar)...")
    fbref = sd.FBref(leagues=liga_sd, seasons=temp_sd)
    df = fbref.read_schedule().reset_index()

    c_home = col(df, "home_team", "home")
    c_away = col(df, "away_team", "away")
    c_hxg = col(df, "home_xg")
    c_axg = col(df, "away_xg")
    c_date = col(df, "date")
    if not all([c_home, c_away, c_hxg, c_axg, c_date]):
        print("  [DIAGNÓSTICO] colunas disponíveis:", sorted(df.columns.tolist()))
        sys.exit("Não achei todas as colunas esperadas (home_team/away_team/home_xg/away_xg/date) — "
                 "confira a lista de diagnóstico acima.")

    df["_data"] = pd.to_datetime(df[c_date]).dt.date

    # Casa nomes de time -> team_id (mesma lógica robusta do script principal)
    mapa_times = {}
    for nome_fb in pd.concat([df[c_home], df[c_away]]).unique():
        norm = normalizar(str(nome_fb))
        banco_norm, metodo = match_times(norm, norm_para_id)
        if banco_norm is None:
            print(f"  AVISO: time sem correspondência: '{nome_fb}'")
            continue
        mapa_times[nome_fb] = norm_para_id[banco_norm]
        if "confira" in metodo:
            print(f"  ATENÇÃO ({metodo}): '{nome_fb}' -> '{nome_por_id[mapa_times[nome_fb]]}'")

    atualizacoes, sem_xg, sem_match = [], 0, 0
    for _, linha in df.iterrows():
        home_id = mapa_times.get(linha[c_home])
        away_id = mapa_times.get(linha[c_away])
        if home_id is None or away_id is None:
            continue

        cand = nossos[
            (nossos["home_team_id"] == home_id) & (nossos["away_team_id"] == away_id)
            & (abs(pd.to_datetime(nossos["data"]) - pd.to_datetime(linha["_data"])).dt.days <= 1)
        ]
        if cand.empty:
            sem_match += 1
            continue
        match_id = int(cand.iloc[0]["id"])

        hxg, axg = linha[c_hxg], linha[c_axg]
        if pd.isna(hxg) and pd.isna(axg):
            sem_xg += 1
            continue

        if not pd.isna(hxg):
            atualizacoes.append({"match_id": match_id, "team_id": int(home_id),
                                  "xg": round(float(hxg), 3), "xg_source": "fbref"})
        if not pd.isna(axg):
            atualizacoes.append({"match_id": match_id, "team_id": int(away_id),
                                  "xg": round(float(axg), 3), "xg_source": "fbref"})

    for i in range(0, len(atualizacoes), 500):
        supabase.table("match_stats").upsert(
            atualizacoes[i : i + 500], on_conflict="match_id,team_id"
        ).execute()

    print(f"\n{liga_cod}/{temporada}: {len(atualizacoes)} valores de xG atualizados "
          f"({sem_xg} partidas sem xG na fonte, {sem_match} sem correspondência de partida).")


if __name__ == "__main__":
    main()
