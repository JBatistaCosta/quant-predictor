"""
Ingestão de odds reais de mercado via Footiqo (footiqo.com/database/leagues/...)
-> tabela odds_market no Supabase.

Fonte gratuita (exportação de CSV/Excel direto na página de cada liga,
sem login, confirmado testando na prática -- ver ingestao_odds_footballdata.py
pro mesmo tipo de checagem antes de escrever o script pras outras fontes
football-data.co.uk). Cobre competições que football-data.co.uk NÃO cobre:
Champions League (desde 2015/16) e Copa Libertadores (desde 2019) --
Sudamericana/Copa do Brasil/Brasileirão B/Copa America/Euro NÃO estão no
catálogo da Footiqo (confirmado na listagem completa de ligas do site).

Formato real do CSV (confirmado, não adivinhado -- cabeçalho + amostra real
inspecionados antes de escrever o parser):
    "id","matchDate","Country","League","Season","homeTeam","awayTeam",
    "H","D","A",                              -- 1X2
    "O05","U05","O15","U15","O25","U25","O35","U35","O45","U45",  -- over/under 0.5 a 4.5 gols
    "BTTSY","BTTSN"                            -- ambas marcam (sim/não)
`matchDate` vem como "DD-MM-AA HH:MM" (ano com 2 dígitos). Odds são da
1xBet (casa única, sem Pinnacle/Bet365/média de mercado como as outras
fontes já usadas no projeto) e são de FECHAMENTO (a própria página descreve
como "closing odds").

Cada exportação da Footiqo é UMA temporada por vez (o arquivo baixado tem
uma "Season" só) -- rode este script uma vez por arquivo/temporada baixada
manualmente pela página da liga (League > filtro de temporada > Export).

Duas ambiguidades de nome resolvidas por inferência (times mais frequentes
na competição, ver ALIASES_MANUAIS) -- se a Footiqo estiver se referindo ao
OUTRO time do mesmo nome curto numa linha específica, essa linha
simplesmente não casa com nenhuma partida nossa (sem_match++), não grava
odd no time errado:
  - "U. Catolica": Chile (CD Universidad Católica, participação frequente
    na Libertadores) escolhido sobre Equador (Universidad Catolica, rara).
  - "Nacional": Uruguai (Club Nacional de Football) escolhido sobre
    Colômbia (Atlético Nacional, que a Footiqo geralmente não abrevia só
    "Nacional") e Bolívia (Nacional Potosí, raríssimo na Libertadores).

Uso:
    pip install pandas supabase
    set SUPABASE_KEY=sua_service_role_key

    python ingestao_odds_footiqo.py <CAMINHO_DO_CSV> --liga-id 23
    python ingestao_odds_footiqo.py <CAMINHO_DO_CSV> --liga-id 19   (Champions League)
"""

import argparse
import os
import sys
import unicodedata
from datetime import datetime, timedelta

import pandas as pd
from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://cgurxgfdmpmsnrshqycx.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

TOLERANCIA_DIAS = 3  # mesma tolerância dos outros scripts de odds do projeto

# (coluna_over, coluna_under, linha) -- cada par vira um mercado "over_under_X.5"
LINHAS_OU = [("O05", "U05", "0.5"), ("O15", "U15", "1.5"), ("O25", "U25", "2.5"),
             ("O35", "U35", "3.5"), ("O45", "U45", "4.5")]

_TOKENS_IGNORADOS = {
    "fc", "cf", "afc", "sc", "ec", "ca", "cr", "cd", "cdc", "cdp", "csd", "se",
    "club", "clube", "as", "ac", "rc", "rb", "aa", "car", "csc", "fbpa",
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


# Chave = nome normalizado como a Footiqo escreve; valor = nome (bruto) do
# NOSSO banco. Ver docstring do módulo pras 2 ambiguidades resolvidas por
# inferência (U. Catolica / Nacional).
ALIASES_MANUAIS = {
    "estudiantes l p": "Estudiantes de La Plata",
    "ind rivadavia": "CS Independiente Rivadavia",
    "u catolica": "CD Universidad Católica",  # Chile -- ver docstring
    "nacional": "Club Nacional de Football",  # Uruguai -- ver docstring
    "sporting cristal": "CS Cristal",
    "ind del valle": "CAR Independiente del Valle",
    "u de deportes": "Club Universitario de Deportes",
    "deportes tolima": "CD Tolima",
    "flamengo rj": "CR Flamengo",
    "ind medellin": "CD Independiente Medellín",
    "ldu quito": "LDU de Quito",
    "la guaira": "Deportivo La Guaira FC",
    "libertad asuncion": "Club Libertad Asuncion",
    "cerro porteno": "Club Cerro Porteño",
    "universidad central": "Universidad Central de Venezuela FC",
    "penarol": "CA Peñarol",
    "santa fe": "Independiente Santa Fe",
    "junior": "CDP Junior FC",
}


def match_times(norm_fq: str, norm_para_id: dict):
    if norm_fq in norm_para_id:
        return norm_fq
    alias_bruto = ALIASES_MANUAIS.get(norm_fq)
    if alias_bruto is not None:
        alias = normalizar(alias_bruto)
        if alias in norm_para_id:
            return alias
    tokens_fq = set(norm_fq.split())
    for candidato in norm_para_id:
        tokens_c = set(candidato.split())
        if tokens_fq and tokens_c and (tokens_fq <= tokens_c or tokens_c <= tokens_fq):
            return candidato
    return None


def parar_data_footiqo(serie: pd.Series) -> pd.Series:
    # "DD-MM-AA HH:MM", ano de 2 dígitos -- Footiqo só tem dado de 2015 em
    # diante, então "20" nunca vai significar 1920.
    return pd.to_datetime(serie, format="%d-%m-%y %H:%M", utc=True).dt.date


def para_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path")
    parser.add_argument("--liga-id", type=int, required=True, help="id de public.leagues (ex: 23=Libertadores, 19=Champions League)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not SUPABASE_KEY:
        sys.exit("Configure SUPABASE_KEY antes de rodar.")
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    df = pd.read_csv(args.csv_path)
    faltando = [c for c in ("matchDate", "homeTeam", "awayTeam", "H", "D", "A") if c not in df.columns]
    if faltando:
        print("  [DIAGNÓSTICO] colunas disponíveis:", sorted(df.columns.tolist()))
        sys.exit(f"Faltam colunas esperadas: {faltando}")

    df["_data"] = parar_data_footiqo(df["matchDate"])
    temporadas = sorted(df["Season"].astype(str).unique())
    print(f"Arquivo: {len(df)} linhas, temporada(s) {temporadas}, liga Footiqo '{df['League'].iloc[0]}'.")

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

    mapa_times = {}
    for nome_fq in pd.concat([df["homeTeam"], df["awayTeam"]]).dropna().unique():
        norm = normalizar(str(nome_fq))
        banco_norm = match_times(norm, norm_para_id)
        if banco_norm is None:
            print(f"  AVISO: time sem correspondência: '{nome_fq}'")
            continue
        mapa_times[nome_fq] = norm_para_id[banco_norm]

    ids_jogos = [j["id"] for j in jogos]
    ja_tem_odds = set()
    inicio = 0
    while True:
        lote = (supabase.table("odds_market").select("match_id")
                .eq("origem", "footiqo")
                .in_("match_id", ids_jogos)
                .range(inicio, inicio + 999).execute().data)
        ja_tem_odds.update(r["match_id"] for r in lote)
        if len(lote) < 1000:
            break
        inicio += 1000

    registros, sem_match, sem_odds, ja_gravadas = [], 0, 0, 0
    for _, linha in df.iterrows():
        home_id = mapa_times.get(linha["homeTeam"])
        away_id = mapa_times.get(linha["awayTeam"])
        if home_id is None or away_id is None:
            continue

        cand = nossos[
            (nossos["home_team_id"] == home_id) & (nossos["away_team_id"] == away_id)
            & (abs(pd.to_datetime(nossos["data"]) - pd.to_datetime(linha["_data"])).dt.days <= TOLERANCIA_DIAS)
        ]
        if cand.empty:
            sem_match += 1
            continue
        match_id = int(cand.iloc[0]["id"])
        if match_id in ja_tem_odds:
            ja_gravadas += 1
            continue

        teve_odds = False
        for selecao, col in (("home", "H"), ("draw", "D"), ("away", "A")):
            odd = para_float(linha.get(col))
            if odd is not None:
                registros.append({"match_id": match_id, "bookmaker": "1xbet", "market": "1X2",
                                  "selection": selecao, "odds": odd, "snapshot": "closing", "origem": "footiqo"})
                teve_odds = True

        for col_over, col_under, linha_gols in LINHAS_OU:
            mercado = f"over_under_{linha_gols}"
            for selecao, col in (("over", col_over), ("under", col_under)):
                odd = para_float(linha.get(col))
                if odd is not None:
                    registros.append({"match_id": match_id, "bookmaker": "1xbet", "market": mercado,
                                      "selection": selecao, "odds": odd, "snapshot": "closing", "origem": "footiqo"})
                    teve_odds = True

        for selecao, col in (("yes", "BTTSY"), ("no", "BTTSN")):
            odd = para_float(linha.get(col))
            if odd is not None:
                registros.append({"match_id": match_id, "bookmaker": "1xbet", "market": "btts",
                                  "selection": selecao, "odds": odd, "snapshot": "closing", "origem": "footiqo"})
                teve_odds = True

        if not teve_odds:
            sem_odds += 1

    # Dedup por chave de conflito antes de gravar -- mesma cautela dos
    # scripts de regeneração (ver modelo_dixon_coles.py), mesmo sem termos
    # visto duplicata real aqui ainda.
    registros = list({(r["match_id"], r["bookmaker"], r["market"], r["selection"]): r for r in registros}.values())

    print(f"\n{len(registros)} linhas de odds prontas pra gravar "
          f"({sem_match} partidas sem correspondência, {sem_odds} partidas casadas sem odds na fonte, "
          f"{ja_gravadas} já tinham odds gravadas antes).")

    if args.dry_run:
        print("--dry-run: nada foi gravado.")
        return

    for i in range(0, len(registros), 500):
        supabase.table("odds_market").insert(registros[i:i + 500]).execute()
    print("Gravado em odds_market.")


if __name__ == "__main__":
    main()
