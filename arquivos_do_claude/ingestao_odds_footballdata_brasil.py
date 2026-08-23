"""
Ingestão de odds reais de mercado do Brasileirão via football-data.co.uk
("Extra Leagues": https://www.football-data.co.uk/brazil.php) -> tabela
odds_market no Supabase. Mesma fonte já usada pelas 5 ligas europeias
(ingestao_odds_footballdata.py), formato diferente: aqui é UM arquivo só
com TODAS as temporadas (não um CSV por liga+temporada), e só tem odds
de FECHAMENTO ("C" no sufixo das colunas -- PSCH/B365CH/etc, sem
PSH/B365H sem o C -- confirmado em football-data.co.uk/notes.txt: "For
the closing odds, as below but with an additional 'C' character...
e.g. B365CH = closing Bet365 home win odds") -- por isso
`snapshot='closing'` é passado explicitamente em vez de usar o default
da tabela ('pre_closing'), ao contrário do script europeu.

notes.txt não especifica timezone de `Date`/`Time` -- não é um problema
aqui porque o casamento usa tolerância de ±3 dias (mesma da fonte
europeia), que absorve qualquer diferença de fuso sem risco de casar a
partida errada (nenhum jogo do Brasileirão se repete entre o mesmo par
de times em menos de 3 dias).

Cobertura real (confirmada por validação manual antes de escrever este
script, ver CONTEXTO_PROJETO.md): 2019-2026 casam 100% por DATA (±3 dias,
mesma tolerância do script europeu) + nome de time, ZERO divergência de
placar. 2012-2018 (2.660 linhas) não têm partida correspondente --
Brasileirão só existe no nosso banco a partir de 2019, não é bug.

Sem mercado de over/under 2.5 -- essa fonte, pro Brasil, só tem colunas
de 1X2 (confirmado no header do CSV real, diferente do arquivo das 5
ligas europeias que tem >2.5/<2.5).

Uso:
    pip install requests supabase pandas
    set SUPABASE_KEY=sua_service_role_key

    python ingestao_odds_footballdata_brasil.py
"""

import io
import os
import sys
import unicodedata

import pandas as pd
import requests
from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://cgurxgfdmpmsnrshqycx.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

LEAGUE_ID = 1  # Brasileirão Série A
URL = "https://www.football-data.co.uk/new/BRA.csv"
TOLERANCIA_DIAS = 3  # mesma tolerância do script europeu

# (prefixo "C" no CSV, nome de exibição). "Max" fica de fora -- não é
# odd de casa nenhuma, é o MAIOR valor entre várias casas pra cada
# seleção (métrica sintética), gravar como "bookmaker" seria enganoso.
BOOKMAKERS = [
    ("B365C", "bet365"),
    ("PSC", "pinnacle"),
    ("BFEC", "betfair_exchange"),
    ("AvgC", "media_mercado"),
]

# BUG REAL corrigido: o delete antes do insert apagava por bookmaker
# (pinnacle/bet365/...) sem checar origem -- "pinnacle"/"bet365" são os
# MESMOS nomes usados pelo backfill da OddsPapi, então rodar este script
# de novo apagaria as odds ricas da OddsPapi (dezenas de mercados) pra
# regravar só 1X2 do football-data.co.uk por cima. Escopado por
# origem=ORIGEM -- só apaga/reprocessa o que o PRÓPRIO script já gravou.
ORIGEM = "football_data_co_uk"

_TOKENS_IGNORADOS = {
    "fc", "cf", "afc", "fbpa", "fbc", "sc", "ac", "ssc", "as", "rc", "cd",
    "ud", "rcd", "ec", "ca", "cr", "se", "club", "clube",
}

# Achados validando o mapeamento manualmente antes de escrever este script
# (?diagnostico equivalente feito localmente) -- essa fonte usa sufixo de
# estado ("Botafogo RJ") ou abreviação sem raiz comum ("Atletico-MG",
# "Athletico-PR", "Atletico GO", "CSA") que não batem por token-subset com
# o nome completo do nosso banco. Mesmo espírito de
# ingestao_odds_footballdata.py/sync-match-stats.js.
ALIASES_MANUAIS = {
    "athletico pr": "Club Athletico Paranaense",
    "atletico go": "AC Goianiense",
    "atletico mg": "Atletico Mineiro",
    "botafogo rj": "Botafogo FR",
    "csa": "CS Alagoano",
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


def match_times(norm_fd: str, norm_para_id: dict):
    if norm_fd in norm_para_id:
        return norm_fd
    alias_bruto = ALIASES_MANUAIS.get(norm_fd)
    if alias_bruto is not None:
        alias = normalizar(alias_bruto)
        if alias in norm_para_id:
            return alias
    tokens_fd = set(norm_fd.split())
    for candidato in norm_para_id:
        tokens_c = set(candidato.split())
        if tokens_fd and tokens_c and (tokens_fd <= tokens_c or tokens_c <= tokens_fd):
            return candidato
    return None


def main():
    if not SUPABASE_KEY:
        sys.exit("Configure SUPABASE_KEY antes de rodar.")
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    jogos, inicio = [], 0
    while True:
        lote = (supabase.table("matches")
                .select("id, match_date, home_team_id, away_team_id, home_goals, away_goals")
                .eq("league_id", LEAGUE_ID)
                .range(inicio, inicio + 999).execute().data)
        jogos.extend(lote)
        if len(lote) < 1000:
            break
        inicio += 1000
    if not jogos:
        sys.exit("Nenhum jogo do Brasileirão no banco.")

    nossos = pd.DataFrame(jogos)
    nossos["data"] = pd.to_datetime(nossos["match_date"], utc=True).dt.date

    ids_times = {j["home_team_id"] for j in jogos} | {j["away_team_id"] for j in jogos}
    times_rows = (supabase.table("teams").select("id, name")
                  .in_("id", list(ids_times)).execute().data)
    norm_para_id = {normalizar(t["name"]): t["id"] for t in times_rows}

    print(f"Baixando odds de {URL} ...")
    resp = requests.get(URL, timeout=30)
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text))
    df = df[df["League"] == "Serie A"].copy()

    df["_data"] = pd.to_datetime(df["Date"], dayfirst=True).dt.date

    mapa_times = {}
    for nome_fd in pd.concat([df["Home"], df["Away"]]).dropna().unique():
        norm = normalizar(str(nome_fd))
        banco_norm = match_times(norm, norm_para_id)
        if banco_norm is None:
            print(f"  AVISO: time sem correspondência: '{nome_fd}' (esperado pra 2012-2018 -- ver CONTEXTO_PROJETO.md)")
            continue
        mapa_times[nome_fd] = norm_para_id[banco_norm]

    registros, sem_match, sem_odds = [], 0, 0
    for _, linha in df.iterrows():
        home_id = mapa_times.get(linha["Home"])
        away_id = mapa_times.get(linha["Away"])
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

        teve_odds = False
        for prefixo, nome_casa in BOOKMAKERS:
            colunas = {"home": f"{prefixo}H", "draw": f"{prefixo}D", "away": f"{prefixo}A"}
            for selecao, col in colunas.items():
                if col in df.columns:
                    v = linha.get(col)
                    if v is not None and not pd.isna(v):
                        registros.append({
                            "match_id": match_id, "bookmaker": nome_casa, "market": "1X2",
                            "selection": selecao, "odds": round(float(v), 3), "snapshot": "closing",
                            "origem": ORIGEM,
                        })
                        teve_odds = True
        if not teve_odds:
            sem_odds += 1

    match_ids = sorted({r["match_id"] for r in registros})
    for i in range(0, len(match_ids), 200):
        supabase.table("odds_market").delete().eq("origem", ORIGEM).in_("match_id", match_ids[i:i + 200]).execute()

    for i in range(0, len(registros), 500):
        supabase.table("odds_market").insert(registros[i:i + 500]).execute()

    print(f"\n{len(registros)} linhas de odds gravadas em odds_market pra {len(match_ids)} partidas "
          f"({sem_match} partidas casadas por nome mas sem correspondência de data, {sem_odds} sem nenhuma odd na fonte).")


if __name__ == "__main__":
    main()
