"""
Ingestão de dados da API-Football para o Supabase (projeto quant-futebol-dados).

Uso:
    1. pip install requests supabase
    2. Defina as variáveis de ambiente (ou edite abaixo):
         API_FOOTBALL_KEY   -> sua chave em https://dashboard.api-football.com
         SUPABASE_URL       -> https://cgurxgfdmpmsnrshqycx.supabase.co
         SUPABASE_KEY       -> service_role key (Settings > API no painel do Supabase)
    3. python ingestao_api_football.py

Estratégia para o plano gratuito (100 requisições/dia):
    - 1 requisição por liga/temporada traz TODOS os jogos da temporada.
    - O script mostra quantas requisições restam após cada chamada.
"""

import os
import sys
import requests
from supabase import create_client

# ---------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------
API_KEY = os.environ.get("API_FOOTBALL_KEY", "eff3d4a516b74d96a357738d6e2a987f")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://cgurxgfdmpmsnrshqycx.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "sb_publishable_pOX8y7CjfFALS_dCZ61w7g_wEPu7T4g")

BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}

# Ligas a ingerir: (id na API-Football, nome, tipo)
# Confira no dashboard quais TEMPORADAS seu plano gratuito libera.
LIGAS = [
    (71,  "Brasileirão Série A",  "league"),
    (13,  "Copa Libertadores",    "cup"),
    (39,  "Premier League",       "league"),
    (140, "La Liga",              "league"),
    (135, "Serie A (Itália)",     "league"),
    (78,  "Bundesliga",           "league"),
    (61,  "Ligue 1",              "league"),
    (9,   "Copa América",         "international"),
    (1,   "Copa do Mundo",        "international"),
]

# Temporadas a buscar: as 3 primeiras servem de TREINO para o modelo
# (estimar força de ataque/defesa de cada time), a última é o alvo de
# ESTIMATIVA/VALIDAÇÃO. Ajuste conforme o que seu plano libera (ver
# checar_temporadas_disponiveis() abaixo antes de rodar em lote).
TEMPORADAS = [2022, 2023, 2024, 2025]
TEMPORADA_TESTE = TEMPORADAS[-1]  # a que o modelo vai tentar prever
TEMPORADAS_TREINO = TEMPORADAS[:-1]

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def chamar_api(endpoint: str, params: dict) -> dict:
    """Faz uma requisição à API-Football e mostra a cota restante."""
    resp = requests.get(f"{BASE_URL}/{endpoint}", headers=HEADERS, params=params)
    resp.raise_for_status()
    restante = resp.headers.get("x-ratelimit-requests-remaining", "?")
    print(f"  [API] {endpoint} {params} -> requisições restantes hoje: {restante}")
    dados = resp.json()
    if dados.get("errors"):
        print(f"  [ERRO da API] {dados['errors']}")
    return dados


def upsert_liga(api_id: int, nome: str, tipo: str, pais: str | None) -> int:
    """Insere/atualiza a liga e retorna o id interno no banco."""
    res = (
        supabase.table("leagues")
        .upsert(
            {"external_id": str(api_id), "name": nome, "type": tipo, "country": pais},
            on_conflict="external_id",
        )
        .execute()
    )
    return res.data[0]["id"]


def upsert_time(time_api: dict, tipo_liga: str) -> int:
    """Insere/atualiza um time e retorna o id interno no banco."""
    res = (
        supabase.table("teams")
        .upsert(
            {
                "external_id": str(time_api["id"]),
                "name": time_api["name"],
                "is_national_team": tipo_liga == "international",
            },
            on_conflict="external_id",
        )
        .execute()
    )
    return res.data[0]["id"]


def ingerir_temporada(api_id: int, nome: str, tipo: str, temporada: int) -> None:
    """Busca todos os jogos de uma liga/temporada e grava no banco."""
    print(f"\n=== {nome} — temporada {temporada} ===")
    dados = chamar_api("fixtures", {"league": api_id, "season": temporada})
    jogos = dados.get("response", [])
    if not jogos:
        print("  Nenhum jogo retornado (temporada fora do plano gratuito?)")
        return

    pais = jogos[0]["league"].get("country")
    liga_id = upsert_liga(api_id, nome, tipo, pais)

    # Cache local de times para evitar upserts repetidos
    cache_times: dict[int, int] = {}
    lote_partidas = []

    for jogo in jogos:
        for lado in ("home", "away"):
            t = jogo["teams"][lado]
            if t["id"] not in cache_times:
                cache_times[t["id"]] = upsert_time(t, tipo)

        status_api = jogo["fixture"]["status"]["short"]
        if status_api in ("FT", "AET", "PEN"):
            status = "finished"
        elif status_api in ("PST",):
            status = "postponed"
        elif status_api in ("CANC", "ABD"):
            status = "cancelled"
        elif status_api in ("1H", "2H", "HT", "ET", "P", "LIVE"):
            status = "live"
        else:
            status = "scheduled"

        lote_partidas.append(
            {
                "external_id": str(jogo["fixture"]["id"]),
                "league_id": liga_id,
                "season": str(temporada),
                "match_date": jogo["fixture"]["date"],
                "home_team_id": cache_times[jogo["teams"]["home"]["id"]],
                "away_team_id": cache_times[jogo["teams"]["away"]["id"]],
                "home_goals": jogo["goals"]["home"],
                "away_goals": jogo["goals"]["away"],
                "status": status,
            }
        )

    # Upsert em lotes de 500 para não estourar o payload
    for i in range(0, len(lote_partidas), 500):
        supabase.table("matches").upsert(
            lote_partidas[i : i + 500], on_conflict="external_id"
        ).execute()

    print(f"  OK: {len(lote_partidas)} partidas gravadas, {len(cache_times)} times.")


def checar_temporadas_disponiveis(api_id: int, nome: str) -> None:
    """Consulta 1x a liga e mostra quais temporadas o seu plano cobre,
    para não desperdiçar requisições pedindo fixtures de temporadas vazias."""
    dados = chamar_api("leagues", {"id": api_id})
    respostas = dados.get("response", [])
    if not respostas:
        print(f"  {nome}: liga não encontrada.")
        return
    temporadas = respostas[0].get("seasons", [])
    disponiveis = sorted(s["year"] for s in temporadas)
    print(f"  {nome}: temporadas no seu plano -> {disponiveis}")
    faltando = [t for t in TEMPORADAS if t not in disponiveis]
    if faltando:
        print(f"    ATENÇÃO: {faltando} não parecem disponíveis no seu plano.")


def main() -> None:
    if "COLE_SUA" in API_KEY or "COLE_SUA" in SUPABASE_KEY:
        sys.exit("Configure API_FOOTBALL_KEY e SUPABASE_KEY antes de rodar.")

    total_chamadas = len(LIGAS) * len(TEMPORADAS)
    print(f"Plano: {total_chamadas} requisições ({len(LIGAS)} ligas x {len(TEMPORADAS)} temporadas).")
    if total_chamadas > 90:
        print("ATENÇÃO: perto do limite diário de 100. Considere reduzir a lista.")

    if "--check" in sys.argv:
        print("\nVerificando cobertura de temporadas (1 requisição por liga)...")
        for api_id, nome, _ in LIGAS:
            checar_temporadas_disponiveis(api_id, nome)
        print("\nRode sem --check para ingerir de fato, após ajustar TEMPORADAS/LIGAS se necessário.")
        return

    for api_id, nome, tipo in LIGAS:
        for temporada in TEMPORADAS:
            try:
                ingerir_temporada(api_id, nome, tipo, temporada)
            except Exception as e:  # segue para a próxima liga em caso de erro
                print(f"  Falha em {nome}/{temporada}: {e}")

    print("\nIngestão concluída.")


if __name__ == "__main__":
    main()
