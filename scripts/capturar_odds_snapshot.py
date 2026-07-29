#!/usr/bin/env python3
"""Captura odds da The-Odds-API e persiste em `odds_snapshots`.

Tipos de snapshot:
  abertura      — D-7 a D-14, captura inicial (nunca sobrescrita se já existir)
  pre_fechamento — D-1, atualiza com odds mais recentes
  fechamento    — D-0 ~2h antes, atualiza com odds mais recentes

Mercados capturados por chamada: h2h, spreads, totals.

Consumo estimado da cota gratuita (500 req/mês, The-Odds-API):
  7 ligas × 1 chamada/liga = 7 req por rodada de snapshot
  Rodadas/mês: abertura ~4 (semanal) + pre_fechamento ~30 + fechamento ~60
  → ~(4+30+60) × 7 ≈ 658 req/mês — ATENÇÃO: pode exceder 500 req/mês se todas
  as rodadas forem executadas. Monitorar `X-Requests-Remaining` nos headers.
  Reduzir cobrindo só 3-4 ligas ativas OU diminuir frequência do fechamento.

Variáveis de ambiente obrigatórias:
  SUPABASE_URL, SUPABASE_KEY, ODDS_API_KEY

Uso:
  python capturar_odds_snapshot.py --snapshot-type abertura
  python capturar_odds_snapshot.py --snapshot-type pre_fechamento
  python capturar_odds_snapshot.py --snapshot-type fechamento
"""

from __future__ import annotations

import argparse
import difflib
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

import requests
from supabase import create_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("capturar_odds_snapshot")

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

ODDS_API_BASE = "https://api.the-odds-api.com/v4/sports"
ODDS_API_MARKETS = "h2h,spreads,totals"
ODDS_API_REGIONS = "eu"
ODDS_API_FORMAT = "decimal"

# Mapeamento: nome da liga em `leagues.name` → sport_key da The-Odds-API
# Atualizar se novas ligas forem adicionadas ao banco.
LIGAS_ODDS_API: dict[str, str] = {
    "Premier League":      "soccer_epl",
    "La Liga":             "soccer_spain_la_liga",
    "Serie A (Itália)":    "soccer_italy_serie_a",
    "Bundesliga":          "soccer_germany_bundesliga",
    "Ligue 1":             "soccer_france_ligue_one",
    "Brasileirão Série A": "soccer_brazil_campeonato",
    "Championship":        "soccer_efl_champ",
}

# Janela de tempo (dias a partir de agora) por tipo de snapshot
JANELAS: dict[str, tuple[int, int]] = {
    "abertura":       (7, 21),   # D+7 a D+21 — odds de abertura, janela ampla
    "pre_fechamento": (1, 3),    # D+1 a D+3  — dia seguinte e pós-amanhã
    "fechamento":     (0, 1),    # D+0 a D+1  — partidas de hoje
}

# Limiar de similaridade de nome de equipe (0-1)
LIMIAR_SIMILARIDADE = 0.72

# Tamanho do lote de upsert pro Supabase
LOTE_UPSERT = 200


# ---------------------------------------------------------------------------
# Busca partidas no Supabase
# ---------------------------------------------------------------------------

def carregar_partidas_janela(supabase_client, inicio: datetime, fim: datetime) -> list[dict]:
    """Carrega partidas de `matches` no intervalo [inicio, fim] com nomes dos times."""
    resp = (
        supabase_client
        .table("matches")
        .select("id, match_date, home_team_id, away_team_id, league_id, teams!matches_home_team_id_fkey(name), away_team:teams!matches_away_team_id_fkey(name)")
        .gte("match_date", inicio.isoformat())
        .lte("match_date", fim.isoformat())
        .order("match_date")
        .execute()
    )
    partidas = []
    for row in (resp.data or []):
        home_name = (row.get("teams") or {}).get("name", "")
        away_name = (row.get("away_team") or {}).get("name", "")
        partidas.append({
            "id":           row["id"],
            "match_date":   row["match_date"],
            "league_id":    row["league_id"],
            "home_team":    home_name,
            "away_team":    away_name,
        })
    return partidas


def carregar_ligas_ids(supabase_client, nomes: list[str]) -> dict[int, str]:
    """Retorna {league_id: sport_key} para as ligas monitoradas."""
    resp = supabase_client.table("leagues").select("id, name").in_("name", nomes).execute()
    resultado = {}
    for row in (resp.data or []):
        sport_key = LIGAS_ODDS_API.get(row["name"])
        if sport_key:
            resultado[row["id"]] = sport_key
    return resultado


# ---------------------------------------------------------------------------
# Chamada à The-Odds-API
# ---------------------------------------------------------------------------

def buscar_odds_sport(sport_key: str, api_key: str) -> list[dict]:
    """Retorna lista de eventos com bookmakers/markets da The-Odds-API."""
    url = f"{ODDS_API_BASE}/{sport_key}/odds"
    params = {
        "apiKey":      api_key,
        "regions":     ODDS_API_REGIONS,
        "markets":     ODDS_API_MARKETS,
        "oddsFormat":  ODDS_API_FORMAT,
        "dateFormat":  "iso",
    }
    resp = requests.get(url, params=params, timeout=30)
    remaining = resp.headers.get("X-Requests-Remaining", "?")
    used = resp.headers.get("X-Requests-Used", "?")
    logger.info("  [%s] HTTP %d | quota usada=%s restante=%s", sport_key, resp.status_code, used, remaining)
    if resp.status_code == 401:
        logger.error("  Chave de API inválida ou expirada.")
        return []
    if resp.status_code == 422:
        logger.warning("  Sport key desconhecida ou sem eventos: %s", sport_key)
        return []
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Matching The-Odds-API ↔ Supabase
# ---------------------------------------------------------------------------

def _similaridade(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def casar_evento(evento: dict, partidas: list[dict]) -> int | None:
    """Retorna o match_id do Supabase mais compatível com o evento da API, ou None."""
    try:
        commence = datetime.fromisoformat(evento["commence_time"].replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return None

    home_api = evento.get("home_team", "")
    away_api = evento.get("away_team", "")

    # Filtra partidas dentro de ±24h do commence_time
    candidatos = [
        p for p in partidas
        if abs((datetime.fromisoformat(p["match_date"].replace("Z", "+00:00")) - commence).total_seconds()) < 86400
    ]
    if not candidatos:
        return None

    melhor_id = None
    melhor_score = 0.0
    for p in candidatos:
        score = (
            _similaridade(home_api, p["home_team"]) * 0.55
            + _similaridade(away_api, p["away_team"]) * 0.45
        )
        if score > melhor_score:
            melhor_score = score
            melhor_id = p["id"]

    if melhor_score >= LIMIAR_SIMILARIDADE:
        return melhor_id

    logger.debug(
        "  Sem match para '%s' vs '%s' (melhor score=%.2f)", home_api, away_api, melhor_score
    )
    return None


# ---------------------------------------------------------------------------
# Construir linhas pra upsert
# ---------------------------------------------------------------------------

def extrair_linhas(evento: dict, match_id: int, snapshot_type: str, snapshot_at: datetime) -> list[dict]:
    """Para um evento da API, gera 1 linha por (bookmaker, market)."""
    linhas = []
    for bk in evento.get("bookmakers", []):
        bookmaker = bk.get("key", "")
        if not bookmaker:
            continue
        for mkt in bk.get("markets", []):
            market_key = mkt.get("key", "")
            if market_key not in ("h2h", "spreads", "totals"):
                continue
            outcomes = mkt.get("outcomes", [])
            if not outcomes:
                continue
            linhas.append({
                "match_id":      match_id,
                "bookmaker":     bookmaker,
                "market":        market_key,
                "snapshot_type": snapshot_type,
                "snapshot_at":   snapshot_at.isoformat(),
                "outcomes":      outcomes,
            })
    return linhas


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Captura snapshot de odds da The-Odds-API.")
    parser.add_argument(
        "--snapshot-type",
        choices=["abertura", "pre_fechamento", "fechamento"],
        required=True,
        help="Tipo do snapshot a capturar.",
    )
    args = parser.parse_args()
    snapshot_type: str = args.snapshot_type

    supabase_url = os.environ.get("SUPABASE_URL", "")
    supabase_key = os.environ.get("SUPABASE_KEY", "")
    odds_api_key = os.environ.get("ODDS_API_KEY", "")
    if not supabase_url or not supabase_key:
        logger.error("SUPABASE_URL e SUPABASE_KEY precisam estar definidos.")
        sys.exit(1)
    if not odds_api_key:
        logger.error("ODDS_API_KEY precisa estar definido.")
        sys.exit(1)

    supabase = create_client(supabase_url, supabase_key)
    agora = datetime.now(timezone.utc)
    dias_min, dias_max = JANELAS[snapshot_type]
    inicio = agora + timedelta(days=dias_min)
    fim    = agora + timedelta(days=dias_max)

    logger.info("=== Snapshot: %s | janela: %s a %s ===", snapshot_type, inicio.date(), fim.date())

    # Carrega partidas da janela e o mapeamento liga→sport_key
    partidas = carregar_partidas_janela(supabase, inicio, fim)
    if not partidas:
        logger.info("Nenhuma partida na janela — nada a capturar.")
        return

    liga_ids_ativos = {p["league_id"] for p in partidas}
    liga_para_sport_key = carregar_ligas_ids(supabase, list(LIGAS_ODDS_API.keys()))

    # Só consulta ligas que têm partidas na janela E estão mapeadas
    sport_keys_a_consultar = {
        sk for lid, sk in liga_para_sport_key.items() if lid in liga_ids_ativos
    }
    if not sport_keys_a_consultar:
        logger.info("Nenhuma liga monitorada com partidas na janela.")
        return

    logger.info("Ligas a consultar: %s", sorted(sport_keys_a_consultar))

    # Captura e processa cada liga
    todas_linhas: list[dict] = []
    nao_casados: list[str] = []

    for sport_key in sorted(sport_keys_a_consultar):
        logger.info("Consultando %s...", sport_key)
        try:
            eventos = buscar_odds_sport(sport_key, odds_api_key)
        except requests.HTTPError as exc:
            logger.error("  Erro HTTP em %s: %s", sport_key, exc)
            continue
        except requests.RequestException as exc:
            logger.error("  Erro de rede em %s: %s", sport_key, exc)
            continue

        casados = 0
        for evento in eventos:
            match_id = casar_evento(evento, partidas)
            if match_id is None:
                nao_casados.append(f"{evento.get('home_team')} vs {evento.get('away_team')} ({sport_key})")
                continue
            casados += 1
            linhas = extrair_linhas(evento, match_id, snapshot_type, agora)
            todas_linhas.extend(linhas)

        logger.info("  %s: %d eventos, %d casados, %d linhas geradas", sport_key, len(eventos), casados, len([l for l in todas_linhas if True]))

    if nao_casados:
        logger.warning("Eventos sem match no Supabase (%d):", len(nao_casados))
        for s in nao_casados[:20]:
            logger.warning("  - %s", s)

    if not todas_linhas:
        logger.info("Nenhuma linha para persistir.")
        return

    # Upsert no Supabase
    # abertura: ignore_duplicates=True (primeira captura vence — não sobrescreve)
    # pre_fechamento/fechamento: update normal (odds mais recente vence)
    ignore = snapshot_type == "abertura"
    total_upsert = 0
    for inicio_lote in range(0, len(todas_linhas), LOTE_UPSERT):
        lote = todas_linhas[inicio_lote: inicio_lote + LOTE_UPSERT]
        supabase.table("odds_snapshots").upsert(
            lote,
            on_conflict="match_id,bookmaker,market,snapshot_type",
            ignore_duplicates=ignore,
        ).execute()
        total_upsert += len(lote)

    logger.info("odds_snapshots: %d linhas persistidas (ignore_duplicates=%s)", total_upsert, ignore)
    logger.info("=== Snapshot %s concluído ===", snapshot_type)


if __name__ == "__main__":
    main()
