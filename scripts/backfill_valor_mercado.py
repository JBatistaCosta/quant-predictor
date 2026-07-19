#!/usr/bin/env python3
"""Backfill de `player_market_value_history` a partir do FotMob.

Pivô de abordagem: a 1ª versão desta Fase 1 tentava casar jogador interno
com o Transfermarkt (crosswalk por nome+clube+idade) -- funcionava, mas
rodando de verdade em produção o IP do GitHub Actions apanhava bloqueio da
defesa anti-scraping do Transfermarkt (15/15 buscas com HTTP 500). O FotMob
já tem série temporal de valor de mercado em euros embutida no próprio
payload do jogador (`GET /api/data/playerData?id={fotmob_player_id}` ->
campo `marketValues.values`, mesmo endpoint já usado em produção neste
projeto pra elenco/transferências, sem indício de bloqueio de IP em uso
prévio) -- e crucialmente `players.fotmob_player_id` já existe e já está
100% populado pro escopo (confirmado por consulta direta: 4072/4072
jogadores do elenco atual das 6 ligas), então elimina o problema de
matching por completo -- sem ambiguidade, sem crosswalk novo.

Formato confirmado com uma chamada real antes de escrever o parser: cada
ponto tem `date`, `value`, `currency` (sempre 'EUR' nos testados),
`lowerBound`/`upperBound` (intervalo de confiança do modelo -- fonte
`scisports`, provedor de dados esportivos, não é crowd-sourced como o
Transfermarkt) e `teamName`. Cadência mensal, cobrindo vários anos por
jogador (64 pontos, 2018-2026, no jogador usado pra validar). Não vem
`age` por ponto -- calculado aqui a partir de `birthDate` (também no
payload) + a data de cada ponto.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import date, datetime
from typing import Callable

import requests
from supabase import Client, create_client

logger = logging.getLogger("backfill_valor_mercado")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

TAMANHO_PAGINA = 1000
LIGAS_ESCOPO = [1, 4, 7, 10, 13, 16]  # Brasileirão + 5 ligas europeias de elite

FOTMOB_BASE = "https://www.fotmob.com/api/data"
# Mesmo User-Agent já usado em produção neste projeto pra chamadas ao FotMob
# (arquivos_do_claude/ingestao_fotmob_elenco.py) -- sem histórico de bloqueio.
FOTMOB_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
}


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


# =============================================================================
# Escopo: elenco atual das 6 ligas (mesma lógica já usada na 1ª versão --
# temporada mais recente com jogos `finished` por liga, não o rótulo de
# temporada mais alto, que pra ligas europeias pode ser a próxima janela
# ainda não iniciada)
# =============================================================================
def obter_jogadores_em_escopo(supabase: Client) -> list[dict]:
    temporada_por_liga: dict[int, str] = {}
    for liga_id in LIGAS_ESCOPO:
        resp = (
            supabase.table("matches")
            .select("season")
            .eq("league_id", liga_id)
            .eq("status", "finished")
            .order("season", desc=True)
            .limit(1)
            .execute()
        )
        if resp.data:
            temporada_por_liga[liga_id] = resp.data[0]["season"]

    ids_partidas: list[int] = []
    for liga_id, temporada in temporada_por_liga.items():
        partidas = _paginar(
            lambda inicio, fim, lid=liga_id, temp=temporada: supabase.table("matches")
            .select("id")
            .eq("league_id", lid)
            .eq("season", temp)
            .eq("status", "finished")
            .range(inicio, fim)
        )
        ids_partidas.extend(p["id"] for p in partidas)

    ids_jogadores: set[int] = set()
    for i in range(0, len(ids_partidas), 500):
        lote = ids_partidas[i : i + 500]
        linhas = _paginar(
            lambda inicio, fim, lote=lote: supabase.table("match_player_stats_fotmob")
            .select("player_id")
            .in_("match_id", lote)
            .not_.is_("player_id", "null")
            .range(inicio, fim)
        )
        ids_jogadores.update(l["player_id"] for l in linhas)

    if not ids_jogadores:
        return []

    jogadores: list[dict] = []
    ids_lista = sorted(ids_jogadores)
    for i in range(0, len(ids_lista), 500):
        lote = ids_lista[i : i + 500]
        linhas = _paginar(
            lambda inicio, fim, lote=lote: supabase.table("players")
            .select("id, fotmob_player_id")
            .in_("id", lote)
            .not_.is_("fotmob_player_id", "null")
            .range(inicio, fim)
        )
        jogadores.extend(linhas)

    jogadores.sort(key=lambda j: j["id"])  # ordem determinística -- lotes reprodutíveis entre chamadas
    return jogadores


def obter_ja_processados(supabase: Client) -> set[int]:
    linhas = _paginar(lambda inicio, fim: supabase.table("player_market_value_history").select("player_id").eq("source", "fotmob").range(inicio, fim))
    return {l["player_id"] for l in linhas}


# =============================================================================
# Chamada à API do FotMob
# =============================================================================
def buscar_dados_jogador(fotmob_player_id: str, tentativas: int = 2) -> dict | None:
    ultimo_erro = None
    for tentativa in range(tentativas):
        try:
            resp = requests.get(f"{FOTMOB_BASE}/playerData", params={"id": fotmob_player_id}, headers=FOTMOB_HEADERS, timeout=20)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            ultimo_erro = e
            time.sleep(1)
    logger.warning("Falha ao buscar playerData id=%s após %d tentativas: %s", fotmob_player_id, tentativas, ultimo_erro)
    return None


def _calcular_idade(nascimento: date, na_data: date) -> int:
    idade = na_data.year - nascimento.year
    if (na_data.month, na_data.day) < (nascimento.month, nascimento.day):
        idade -= 1
    return idade


def _extrair_linhas(player_id: int, payload: dict) -> list[dict]:
    valores = ((payload.get("marketValues") or {}).get("values")) or []
    if not valores:
        return []

    nascimento = None
    bd = (payload.get("birthDate") or {}).get("utcTime")
    if bd:
        try:
            nascimento = datetime.fromisoformat(bd.replace("Z", "+00:00")).date()
        except ValueError:
            nascimento = None

    linhas = []
    for ponto in valores:
        if ponto.get("date") is None or ponto.get("value") is None:
            continue
        try:
            data_ponto = datetime.fromisoformat(ponto["date"].replace("Z", "+00:00")).date()
        except ValueError:
            continue
        linhas.append(
            {
                "player_id": player_id,
                "value_date": data_ponto.isoformat(),
                "value": ponto["value"],
                "currency": ponto.get("currency") or "EUR",
                "age_at_date": _calcular_idade(nascimento, data_ponto) if nascimento else None,
                "club_name": ponto.get("teamName"),
                "source": "fotmob",
            }
        )
    return linhas


def rodar_backfill(supabase: Client, limite: int, pausa_segundos: float = 1.3) -> dict:
    jogadores = obter_jogadores_em_escopo(supabase)
    ja_processados = obter_ja_processados(supabase)
    pendentes = [j for j in jogadores if j["id"] not in ja_processados]
    lote = pendentes[:limite]

    linhas_gravadas, jogadores_sem_historico = 0, 0

    for jogador in lote:
        payload = buscar_dados_jogador(jogador["fotmob_player_id"])
        time.sleep(pausa_segundos)
        if payload is None:
            jogadores_sem_historico += 1
            continue

        linhas = _extrair_linhas(jogador["id"], payload)
        if not linhas:
            jogadores_sem_historico += 1
            continue

        resp = supabase.table("player_market_value_history").upsert(linhas, on_conflict="player_id,source,value_date").execute()
        if getattr(resp, "error", None):
            raise RuntimeError(f"Falha ao gravar player_market_value_history (player_id={jogador['id']}): {resp.error}")
        linhas_gravadas += len(linhas)

    return {
        "processados": len(lote),
        "linhas_gravadas": linhas_gravadas,
        "jogadores_sem_historico": jogadores_sem_historico,
        "pendentes_restantes": len(pendentes) - len(lote),
    }


def main() -> None:
    limite = int(os.environ.get("LIMITE_JOGADORES", "150"))
    supabase = get_supabase_client()

    resultado = rodar_backfill(supabase, limite)
    logger.info(
        "processados=%d linhas_gravadas=%d jogadores_sem_historico=%d pendentes_restantes=%d",
        resultado["processados"],
        resultado["linhas_gravadas"],
        resultado["jogadores_sem_historico"],
        resultado["pendentes_restantes"],
    )


if __name__ == "__main__":
    main()
