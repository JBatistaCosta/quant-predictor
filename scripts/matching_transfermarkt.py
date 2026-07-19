#!/usr/bin/env python3
"""Crosswalk jogador interno -> Transfermarkt (Fase 1 do enriquecimento de
valor de mercado discutido pro Model Benchmarking).

Roda contra uma instância LOCAL da `transfermarkt-api` (felipeall/
transfermarkt-api, FastAPI + scraping) -- ver `.github/workflows/
sync_valores_mercado.yml`, que sobe o serviço antes de chamar este script.
Não existe imagem publicada no Docker Hub; o workflow clona e roda via
`python app/main.py` (mesma técnica usada pra validar isso manualmente
antes de escrever este parser -- o endpoint de busca devolve `id`, `name`,
`club.name`, `age`, `nationalities`, confirmado com uma chamada real antes
de generalizar).

DISCIPLINA DE MATCHING (mesma do crosswalk de time, `team_source_ids`):
nunca grava um casamento ambíguo. Só confirma automaticamente quando nome
E clube batem com folga -- caso contrário, reporta como pendente pra
revisão manual e NÃO escreve nada. Custo de um crosswalk errado é alto
(contaminaria todo o histórico de valor daquele jogador).

Escopo: só jogadores do elenco ATUAL (temporada mais recente com jogos
`finished`, por liga -- não o rótulo de temporada mais alto, que pra ligas
europeias já pode ser a próxima janela ainda não iniciada) das 6 ligas
(5 europeias de elite + Brasileirão), não os ~11 mil jogadores da tabela
inteira.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import time
import unicodedata
from typing import Callable

import requests
from supabase import Client, create_client

logger = logging.getLogger("matching_transfermarkt")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

LIGAS_ESCOPO = [1, 4, 7, 10, 13, 16]  # Brasileirão + Premier League + La Liga + Serie A + Bundesliga + Ligue 1
TAMANHO_PAGINA = 1000

# Confiança mínima pra auto-confirmar um casamento sem revisão humana.
CONFIANCA_MINIMA_AUTO = 0.62
# Margem mínima sobre o segundo melhor candidato -- evita confirmar quando
# dois jogadores homônimos têm score parecido.
MARGEM_MINIMA_SOBRE_SEGUNDO = 0.12


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
# Normalização de texto pra comparação (mesmo espírito de
# `normalizarTexto`/`nomesBatem` em api/model-maintenance.js, portado pra
# Python)
# =============================================================================
def normalizar_texto(s: str | None) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()
    return s


def nomes_batem(a: str | None, b: str | None) -> bool:
    """Match por substring nos dois sentidos -- tolera apelido/nome
    reduzido (ex.: "Man City" contido em "Manchester City")."""
    x, y = normalizar_texto(a), normalizar_texto(b)
    return bool(x) and bool(y) and (x in y or y in x)


def score_nome(nome_interno: str, nome_candidato: str) -> float:
    """Similaridade de Jaccard sobre tokens -- mais rigoroso que substring
    pra nome de pessoa (evita "João Silva" casar com qualquer "Silva")."""
    tokens_a = set(normalizar_texto(nome_interno).split())
    tokens_b = set(normalizar_texto(nome_candidato).split())
    if not tokens_a or not tokens_b:
        return 0.0
    intersecao = len(tokens_a & tokens_b)
    uniao = len(tokens_a | tokens_b)
    return intersecao / uniao if uniao else 0.0


def score_idade(idade_interna: float | None, idade_candidato: int | None) -> float:
    if idade_interna is None or idade_candidato is None:
        return 0.0
    diferenca = abs(idade_interna - idade_candidato)
    return max(0.0, 1 - diferenca / 3)  # tolera até ~3 anos (idade interna pode estar desatualizada)


def pontuar_candidato(nome_interno: str, clube_interno: str | None, idade_interna: float | None, candidato: dict) -> float:
    """Combina nome (peso maior, é o sinal mais confiável), clube (forte
    quando bate, mas jogador pode ter trocado de time desde a última sync)
    e idade (sinal fraco sozinho, só desempata)."""
    s_nome = score_nome(nome_interno, candidato.get("name") or "")
    s_clube = 1.0 if nomes_batem(clube_interno, (candidato.get("club") or {}).get("name")) else 0.0
    s_idade = score_idade(idade_interna, candidato.get("age"))
    return 0.55 * s_nome + 0.30 * s_clube + 0.15 * s_idade


# =============================================================================
# Escopo: elenco atual das 6 ligas
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
            .select("id, name, age, last_team_id")
            .in_("id", lote)
            .range(inicio, fim)
        )
        jogadores.extend(linhas)

    ids_times = sorted({j["last_team_id"] for j in jogadores if j.get("last_team_id") is not None})
    nome_por_time: dict[int, str] = {}
    for i in range(0, len(ids_times), 500):
        lote = ids_times[i : i + 500]
        linhas = _paginar(lambda inicio, fim, lote=lote: supabase.table("teams").select("id, name").in_("id", lote).range(inicio, fim))
        nome_por_time.update({l["id"]: l["name"] for l in linhas})

    for j in jogadores:
        j["nome_time"] = nome_por_time.get(j.get("last_team_id"))

    jogadores.sort(key=lambda j: j["id"])  # ordem determinística -- lotes reprodutíveis entre chamadas
    return jogadores


def obter_ja_casados(supabase: Client) -> set[int]:
    linhas = _paginar(lambda inicio, fim: supabase.table("player_source_ids").select("player_id").eq("source", "transfermarkt").range(inicio, fim))
    return {l["player_id"] for l in linhas}


# =============================================================================
# Chamada à transfermarkt-api local
# =============================================================================
def buscar_candidatos(api_base: str, nome: str, tentativas: int = 2) -> list[dict]:
    ultimo_erro = None
    for tentativa in range(tentativas):
        try:
            resp = requests.get(f"{api_base}/players/search/{requests.utils.quote(nome)}", timeout=20)
            resp.raise_for_status()
            return resp.json().get("results", [])
        except requests.RequestException as e:
            ultimo_erro = e
            time.sleep(1)
    logger.warning("Busca falhou pra %r após %d tentativas: %s", nome, tentativas, ultimo_erro)
    return []


def casar_jogadores(supabase: Client, api_base: str, limite: int, pausa_segundos: float = 0.6) -> dict:
    jogadores = obter_jogadores_em_escopo(supabase)
    ja_casados = obter_ja_casados(supabase)
    pendentes = [j for j in jogadores if j["id"] not in ja_casados]
    lote = pendentes[:limite]

    confirmados, ambiguos, sem_candidato = [], [], []

    for jogador in lote:
        candidatos = buscar_candidatos(api_base, jogador["name"])
        time.sleep(pausa_segundos)
        if not candidatos:
            sem_candidato.append({"player_id": jogador["id"], "nome": jogador["name"]})
            continue

        pontuados = sorted(
            ((pontuar_candidato(jogador["name"], jogador.get("nome_time"), jogador.get("age"), c), c) for c in candidatos),
            key=lambda par: par[0],
            reverse=True,
        )
        melhor_score, melhor = pontuados[0]
        segundo_score = pontuados[1][0] if len(pontuados) > 1 else 0.0

        if melhor_score >= CONFIANCA_MINIMA_AUTO and (melhor_score - segundo_score) >= MARGEM_MINIMA_SOBRE_SEGUNDO:
            confirmados.append(
                {
                    "player_id": jogador["id"],
                    "source": "transfermarkt",
                    "source_id": str(melhor["id"]),
                    "source_name": melhor.get("name"),
                    "confianca": round(melhor_score, 3),
                }
            )
        else:
            ambiguos.append(
                {
                    "player_id": jogador["id"],
                    "nome_interno": jogador["name"],
                    "time_interno": jogador.get("nome_time"),
                    "melhor_candidato": melhor.get("name"),
                    "score": round(melhor_score, 3),
                    "score_segundo": round(segundo_score, 3),
                }
            )

    if confirmados:
        for i in range(0, len(confirmados), 500):
            resp = supabase.table("player_source_ids").upsert(confirmados[i : i + 500], on_conflict="player_id,source").execute()
            if getattr(resp, "error", None):
                raise RuntimeError(f"Falha ao gravar player_source_ids: {resp.error}")

    return {
        "processados": len(lote),
        "confirmados": len(confirmados),
        "ambiguos": ambiguos,
        "sem_candidato": len(sem_candidato),
        "pendentes_restantes": len(pendentes) - len(lote),
    }


def main() -> None:
    limite = int(os.environ.get("LIMITE_JOGADORES", "150"))
    api_base = os.environ.get("TRANSFERMARKT_API_BASE", "http://127.0.0.1:8000").rstrip("/")
    supabase = get_supabase_client()

    resultado = casar_jogadores(supabase, api_base, limite)
    logger.info(
        "Processados=%d confirmados=%d sem_candidato=%d pendentes_restantes=%d",
        resultado["processados"],
        resultado["confirmados"],
        resultado["sem_candidato"],
        resultado["pendentes_restantes"],
    )
    if resultado["ambiguos"]:
        logger.info("Casamentos ambíguos (NÃO gravados, revisar manualmente):")
        for a in resultado["ambiguos"]:
            logger.info("  player_id=%s %r (time=%r) -> melhor candidato %r (score=%.2f, segundo=%.2f)",
                        a["player_id"], a["nome_interno"], a["time_interno"], a["melhor_candidato"], a["score"], a["score_segundo"])


if __name__ == "__main__":
    main()
