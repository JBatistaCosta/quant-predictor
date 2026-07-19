#!/usr/bin/env python3
"""Backfill de `player_market_value_history` pros jogadores já casados em
`player_source_ids` (source='transfermarkt') -- ver `matching_transfermarkt.py`
pro casamento em si. Roda contra a mesma instância local da transfermarkt-api
(ver `.github/workflows/sync_valores_mercado.yml`).

Formato de `marketValueHistory` confirmado com uma chamada real antes de
escrever o parser (`GET /players/{id}/market_value`, ver histórico do PR):
lista de {age, date, clubId, clubName, marketValue}, NÃO vem ordenada por
data -- ordenar antes de gravar não é estritamente necessário (a constraint
única é por data, upsert é idempotente), mas evita confusão em quem for ler
a tabela depois.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from typing import Callable

import requests
from supabase import Client, create_client

logger = logging.getLogger("backfill_valor_mercado")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

TAMANHO_PAGINA = 1000


def obter_env_obrigatoria(nome: str) -> str:
    valor = (os.environ.get(nome) or "").strip()
    if not valor:
        logger.error("Variável de ambiente obrigatória ausente: %s", nome)
        sys.exit(1)
    return valor


def get_supabase_client() -> Client:
    return create_client(obter_env_obrigatoria("SUPABASE_URL"), obter_env_obrigatoria("SUPABASE_KEY"))


def _paginar(query_builder_factory: Callable[[int, int], object], tamanho_pagina: int = TAMANHO_PAGINA) -> list[dict]:
    todas_as_linhas: list[dict] = []
    pagina = 0
    while True:
        inicio = pagina * tamanho_pagina
        fim = inicio + tamanho_pagina - 1
        resposta = query_builder_factory(inicio, fim).execute()
        linhas = resposta.data or []
        todas_as_linhas.extend(linhas)
        if len(linhas) < tamanho_pagina:
            break
        pagina += 1
    return todas_as_linhas


def obter_pendentes(supabase: Client, limite: int) -> list[dict]:
    """Jogador está pendente se tem crosswalk confirmado mas ainda nenhuma
    linha em player_market_value_history -- LEFT JOIN feito em duas
    consultas (PostgREST não faz join anti-join direto) e diferença em
    Python, mais simples que embutir SQL cru."""
    casados = _paginar(
        lambda inicio, fim: supabase.table("player_source_ids")
        .select("player_id, source_id")
        .eq("source", "transfermarkt")
        .order("player_id")
        .range(inicio, fim)
    )
    ja_processados = _paginar(
        lambda inicio, fim: supabase.table("player_market_value_history").select("player_id").eq("source", "transfermarkt").range(inicio, fim)
    )
    ids_processados = {l["player_id"] for l in ja_processados}
    pendentes = [c for c in casados if c["player_id"] not in ids_processados]
    return pendentes[:limite]


def buscar_historico(api_base: str, transfermarkt_id: str, tentativas: int = 2) -> list[dict]:
    ultimo_erro = None
    for _ in range(tentativas):
        try:
            resp = requests.get(f"{api_base}/players/{transfermarkt_id}/market_value", timeout=20)
            resp.raise_for_status()
            return resp.json().get("marketValueHistory", [])
        except requests.RequestException as e:
            ultimo_erro = e
            time.sleep(1)
    logger.warning("Falha ao buscar histórico do id=%s após tentativas: %s", transfermarkt_id, ultimo_erro)
    return []


def rodar_backfill(supabase: Client, api_base: str, limite: int, pausa_segundos: float = 0.6) -> dict:
    pendentes = obter_pendentes(supabase, limite)
    linhas_gravadas, jogadores_sem_historico = 0, 0

    for jogador in pendentes:
        historico = buscar_historico(api_base, jogador["source_id"])
        time.sleep(pausa_segundos)
        if not historico:
            jogadores_sem_historico += 1
            continue

        linhas = [
            {
                "player_id": jogador["player_id"],
                "value_date": ponto["date"],
                "value": ponto["marketValue"],
                "currency": "EUR",
                "age_at_date": ponto.get("age"),
                "club_name": ponto.get("clubName"),
                "source": "transfermarkt",
            }
            for ponto in historico
            if ponto.get("date") is not None and ponto.get("marketValue") is not None
        ]
        if not linhas:
            jogadores_sem_historico += 1
            continue

        resp = supabase.table("player_market_value_history").upsert(linhas, on_conflict="player_id,source,value_date").execute()
        if getattr(resp, "error", None):
            raise RuntimeError(f"Falha ao gravar player_market_value_history (player_id={jogador['player_id']}): {resp.error}")
        linhas_gravadas += len(linhas)

    return {
        "jogadores_processados": len(pendentes),
        "linhas_gravadas": linhas_gravadas,
        "jogadores_sem_historico": jogadores_sem_historico,
    }


def main() -> None:
    limite = int(os.environ.get("LIMITE_JOGADORES", "150"))
    # .strip() antes do .rstrip("/") -- mesma proteção contra newline/espaço
    # residual em secret que já mordeu SUPABASE_URL nesta sessão.
    api_base = (os.environ.get("TRANSFERMARKT_API_BASE") or "http://127.0.0.1:8000").strip().rstrip("/")
    supabase = get_supabase_client()

    resultado = rodar_backfill(supabase, api_base, limite)
    logger.info(
        "jogadores_processados=%d linhas_gravadas=%d jogadores_sem_historico=%d",
        resultado["jogadores_processados"],
        resultado["linhas_gravadas"],
        resultado["jogadores_sem_historico"],
    )


if __name__ == "__main__":
    main()
