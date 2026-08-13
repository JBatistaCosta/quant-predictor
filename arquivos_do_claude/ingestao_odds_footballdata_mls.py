"""
Ingestão de odds reais de mercado da MLS via football-data.co.uk
("New League Data": https://www.football-data.co.uk/usa.php) -> tabela
odds_market no Supabase. Mesmo formato do Brasileirão
(ingestao_odds_footballdata_brasil.py): UM arquivo só com TODAS as
temporadas (não um CSV por liga+temporada), só odds de FECHAMENTO
("C" no sufixo das colunas -- PSCH/B365CH/etc) e SEM mercado de
over/under 2.5 (só 1X2) -- confirmado no header real do CSV antes de
escrever este script (mesma regra de sempre: nunca adivinhar formato).

Cobertura real: temporadas 2012-2026 no arquivo fonte; nosso banco só tem
MLS a partir de 2022, então tudo antes disso é ignorado automaticamente
pelo casamento por partida (não existe partida nossa pra casar).

Uso:
    pip install requests supabase pandas
    set SUPABASE_KEY=sua_service_role_key

    python ingestao_odds_footballdata_mls.py
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

LEAGUE_ID = 29  # MLS
URL = "https://www.football-data.co.uk/new/USA.csv"
TOLERANCIA_DIAS = 3  # mesma tolerância dos scripts europeu/Brasileirão

BOOKMAKERS = [
    ("B365C", "bet365"),
    ("PSC", "pinnacle"),
    ("BFEC", "betfair_exchange"),
    ("AvgC", "media_mercado"),
]

_TOKENS_IGNORADOS = {
    "fc", "cf", "afc", "sc",
}

# Nosso banco guarda vários times da MLS só pelo nome da cidade (ex.:
# "Colorado", "Columbus", "Houston") -- casa por subconjunto de token com
# o nome completo da fonte ("Colorado Rapids", "Columbus Crew", "Houston
# Dynamo") sem precisar de alias manual pra a maioria. Só as siglas/nomes
# sem raiz comum com o nosso banco (achados rodando pela 1ª vez) precisam
# de alias -- chave é o nome normalizado da FONTE, valor é o nome (bruto)
# do NOSSO banco. "Chivas USA" fica de fora de propósito -- time extinto
# em 2014, não existe no nosso banco (só temos MLS a partir de 2022).
ALIASES_MANUAIS = {
    "los angeles galaxy": "LA Galaxy",
    "new york red bulls": "NY Red Bulls",
    "new york city": "NYCFC",
    "atlanta utd": "Atlanta United",
    "sporting kansas city": "Sporting KC",
    "los angeles": "LAFC",  # "fc" já removido pela normalização
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


def para_float(v):
    """Betfair Exchange (BFEC*) às vezes vem com 'x' em vez de número
    (mercado suspenso/sem liquidez na fonte, confirmado rodando pela 1ª
    vez) -- devolve None nesses casos em vez de estourar ValueError."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


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
                .select("id, match_date, home_team_id, away_team_id")
                .eq("league_id", LEAGUE_ID)
                .range(inicio, inicio + 999).execute().data)
        jogos.extend(lote)
        if len(lote) < 1000:
            break
        inicio += 1000
    if not jogos:
        sys.exit("Nenhum jogo da MLS no banco.")

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
    df = df[df["League"] == "MLS"].copy()

    df["_data"] = pd.to_datetime(df["Date"], dayfirst=True).dt.date

    mapa_times = {}
    for nome_fd in pd.concat([df["Home"], df["Away"]]).dropna().unique():
        norm = normalizar(str(nome_fd))
        banco_norm = match_times(norm, norm_para_id)
        if banco_norm is None:
            print(f"  AVISO: time sem correspondência: '{nome_fd}'")
            continue
        mapa_times[nome_fd] = norm_para_id[banco_norm]

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

    registros, sem_match, sem_odds, ja_gravadas = [], 0, 0, 0
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
        if match_id in ja_tem_odds:
            ja_gravadas += 1
            continue

        teve_odds = False
        for prefixo, nome_casa in BOOKMAKERS:
            colunas = {"home": f"{prefixo}H", "draw": f"{prefixo}D", "away": f"{prefixo}A"}
            for selecao, col in colunas.items():
                if col in df.columns:
                    v = linha.get(col)
                    if v is not None and not pd.isna(v):
                        odd = para_float(v)
                        if odd is not None:
                            registros.append({
                                "match_id": match_id, "bookmaker": nome_casa, "market": "1X2",
                                "selection": selecao, "odds": round(odd, 3), "snapshot": "closing",
                            })
                            teve_odds = True
        if not teve_odds:
            sem_odds += 1

    for i in range(0, len(registros), 500):
        supabase.table("odds_market").insert(registros[i:i + 500]).execute()

    print(f"\n{len(registros)} linhas de odds gravadas em odds_market "
          f"({sem_match} partidas sem correspondência, {sem_odds} partidas casadas sem odds na fonte, "
          f"{ja_gravadas} já tinham odds gravadas antes).")


if __name__ == "__main__":
    main()
