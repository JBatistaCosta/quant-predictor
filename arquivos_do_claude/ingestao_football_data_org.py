"""
Ingestão de dados da football-data.org para o Supabase (projeto quant-futebol-dados).

Cobre Brasileirão + principais ligas europeias + Champions League + seleções
(Copa do Mundo, Eurocopa) — não cobre Libertadores nem outras competições
sul-americanas (fora do free tier dessa fonte). Complementar à API-Football.

Uso:
    1. pip install requests supabase
    2. Defina as variáveis de ambiente (ou edite abaixo):
         FOOTBALL_DATA_TOKEN -> seu token em football-data.org/client/home
         SUPABASE_URL        -> https://cgurxgfdmpmsnrshqycx.supabase.co
         SUPABASE_KEY        -> service_role key (Settings > API no painel do Supabase)
    3. python ingestao_football_data_org.py [--check]

Rate limit do plano gratuito: 10 requisições/minuto. O script já respeita
isso com uma pausa de 6.5s entre chamadas — não precisa se preocupar com cota
diária como na API-Football (aqui não existe limite diário, só por minuto).
"""

import os
import sys
import time
import requests
from supabase import create_client

# ---------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------
API_TOKEN = os.environ.get("FOOTBALL_DATA_TOKEN", "eff3d4a516b74d96a357738d6e2a987f")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://cgurxgfdmpmsnrshqycx.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNndXJ4Z2ZkbXBtc25yc2hxeWN4Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4MzM0NTU3NiwiZXhwIjoyMDk4OTIxNTc2fQ.FFp-jjSWJYS-2u_0sOdJzPIcJdDfE_wSfw_Kr11H8Us")

BASE_URL = "https://api.football-data.org/v4"
HEADERS = {"X-Auth-Token": API_TOKEN}
PAUSA_ENTRE_CHAMADAS = 6.5  # segundos, para respeitar 10 req/min

# Competições disponíveis no plano gratuito relevantes ao projeto.
# (código na football-data.org, nome, tipo)
LIGAS = [
    ("BSA", "Brasileirão Série A",     "league"),
    ("PL",  "Premier League",          "league"),
    ("PD",  "La Liga",                 "league"),
    ("SA",  "Serie A (Itália)",        "league"),
    ("BL1", "Bundesliga",              "league"),
    ("FL1", "Ligue 1",                 "league"),
    ("CL",  "UEFA Champions League",   "cup"),
    ("WC",  "Copa do Mundo",           "international"),
    ("EC",  "Eurocopa",                "international"),
]

# 3 temporadas de treino + 1 de teste, igual ao pipeline da API-Football.
# WC e EC só existem a cada 4 anos: temporadas sem edição retornam vazio
# e o script simplesmente pula, sem gastar cota extra.
TEMPORADAS = [2022, 2023, 2024, 2025]
TEMPORADA_TESTE = TEMPORADAS[-1]
TEMPORADAS_TREINO = TEMPORADAS[:-1]

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def chamar_api(endpoint: str, params: dict) -> dict:
    """Faz uma requisição à football-data.org, respeitando o rate limit."""
    resp = requests.get(f"{BASE_URL}/{endpoint}", headers=HEADERS, params=params)
    restante = resp.headers.get("X-Requests-Available-Minute", "?")
    print(f"  [API] {endpoint} {params} -> requisições restantes neste minuto: {restante}")
    time.sleep(PAUSA_ENTRE_CHAMADAS)
    if resp.status_code != 200:
        print(f"  [ERRO HTTP {resp.status_code}] {resp.text[:200]}")
        return {}
    return resp.json()


def upsert_liga(codigo: str, nome: str, tipo: str, pais: str | None) -> int:
    res = (
        supabase.table("leagues")
        .upsert(
            {"external_id": codigo, "name": nome, "type": tipo, "country": pais},
            on_conflict="external_id",
        )
        .execute()
    )
    return res.data[0]["id"]


def upsert_time(time_api: dict, tipo_liga: str) -> int:
    res = (
        supabase.table("teams")
        .upsert(
            {
                "external_id": str(time_api["id"]),
                "name": time_api.get("name") or time_api.get("shortName", "Desconhecido"),
                "is_national_team": tipo_liga == "international",
            },
            on_conflict="external_id",
        )
        .execute()
    )
    return res.data[0]["id"]


def checar_temporadas_disponiveis(codigo: str, nome: str) -> None:
    """Consulta 1x a competição e mostra quais temporadas o plano cobre."""
    dados = chamar_api(f"competitions/{codigo}", {})
    if not dados:
        print(f"  {nome}: não foi possível consultar.")
        return
    temporadas = dados.get("seasons", [])
    anos = sorted({s["startDate"][:4] for s in temporadas})
    print(f"  {nome}: temporadas no seu plano -> {anos}")


def ingerir_temporada(codigo: str, nome: str, tipo: str, temporada: int) -> None:
    print(f"\n=== {nome} — temporada {temporada} ===")
    dados = chamar_api(f"competitions/{codigo}/matches", {"season": temporada})
    jogos = dados.get("matches", [])
    if not jogos:
        print("  Nenhum jogo retornado (temporada fora do plano ou competição não realizada nesse ano).")
        return

    pais = dados.get("area", {}).get("name") if isinstance(dados.get("area"), dict) else None
    liga_id = upsert_liga(codigo, nome, tipo, pais)

    cache_times: dict[int, int] = {}
    lote_partidas = []

    status_map = {
        "FINISHED": "finished",
        "SCHEDULED": "scheduled",
        "TIMED": "scheduled",
        "IN_PLAY": "live",
        "PAUSED": "live",
        "POSTPONED": "postponed",
        "CANCELLED": "cancelled",
        "SUSPENDED": "postponed",
    }

    for jogo in jogos:
        for lado in ("homeTeam", "awayTeam"):
            t = jogo[lado]
            if t["id"] not in cache_times:
                cache_times[t["id"]] = upsert_time(t, tipo)

        status = status_map.get(jogo["status"], "scheduled")
        placar = jogo.get("score", {}).get("fullTime", {})

        lote_partidas.append(
            {
                "external_id": f"fd_{jogo['id']}",  # prefixo pra não colidir com ids da API-Football
                "league_id": liga_id,
                "season": str(temporada),
                "match_date": jogo["utcDate"],
                "home_team_id": cache_times[jogo["homeTeam"]["id"]],
                "away_team_id": cache_times[jogo["awayTeam"]["id"]],
                "home_goals": placar.get("home"),
                "away_goals": placar.get("away"),
                "status": status,
            }
        )

    for i in range(0, len(lote_partidas), 500):
        supabase.table("matches").upsert(
            lote_partidas[i : i + 500], on_conflict="external_id"
        ).execute()

    print(f"  OK: {len(lote_partidas)} partidas gravadas, {len(cache_times)} times.")


def main() -> None:
    if "COLE_SEU" in API_TOKEN or "COLE_SUA" in SUPABASE_KEY:
        sys.exit("Configure FOOTBALL_DATA_TOKEN e SUPABASE_KEY antes de rodar.")

    if "--check" in sys.argv:
        print("Verificando cobertura de temporadas (1 requisição por competição)...")
        for codigo, nome, _ in LIGAS:
            checar_temporadas_disponiveis(codigo, nome)
        print("\nRode sem --check para ingerir de fato.")
        return

    for codigo, nome, tipo in LIGAS:
        for temporada in TEMPORADAS:
            try:
                ingerir_temporada(codigo, nome, tipo, temporada)
            except Exception as e:
                print(f"  Falha em {nome}/{temporada}: {e}")

    print("\nIngestão concluída.")


if __name__ == "__main__":
    main()
