#!/usr/bin/env python3
"""Backfill de previsões OUT-OF-SAMPLE do `catboost_v9` pras partidas de
2026 já disputadas (jan/2026 até hoje) -- pedido do usuário depois de
produtizar o modelo pra jogos futuros (`rodar_catboost_v9_previsto.py`):
`walkforward_cv_v9.py` só cobre o histórico até dez/2025 (Fold 3: Test
2025), então 2026 é um período TOTALMENTE fora da amostra de treino/
avaliação original -- exatamente o forward-test mais honesto que existe
(bem mais confiável que reaproveitar o próprio ano de treino).

CUIDADO METODOLÓGICO (o motivo de este script não ser um "rodar de novo"
qualquer): diferente de `rodar_catboost_v9_previsto.py` (que treina em
TODO o histórico, incluindo o mais recente, porque as partidas-alvo são
FUTURAS -- ainda sem resultado, então `dropna(subset=[coluna_alvo])`
já as exclui automaticamente do treino), aqui as partidas-alvo de 2026 JÁ
TÊM resultado conhecido. Treinar em "todo o histórico" incluiria essas
mesmas partidas no treino -- vazamento (avaliar o modelo nos dados que ele
viu). Por isso o corte é temporal explícito: treina SÓ com `match_date <
CORTE_TREINO_TESTE` (31/12/2025, mesmo limite do Fold 3 do walk-forward) e
prevê nas partidas de 2026 -- réplica de um "Fold 4" (Train ≤ 2025 | Test
2026 YTD), não uma partição aleatória.

Reaproveita a mesma infraestrutura de `rodar_catboost_v9_previsto.py`
(treinador/features/serialização) -- ver docstring lá pro contexto
completo do padrão de persistência em `model_predictions` (mesmo
`on_conflict`, mesma convenção `market='1x2'` minúsculo).

Escopo: as 6 ligas do Model Benchmarking, partidas com `home_goals`/
`away_goals` preenchidos (resultado conhecido) e `match_date >=
CORTE_TREINO_TESTE`. Script de execução única (workflow_dispatch, sem
cron) -- depois que essas previsões existirem em `model_predictions`, a
próxima rodada de `matriz_confiabilidade_ev.yml` já as inclui
automaticamente (ela lê tudo que existir pra `model_name='catboost_v9'` e
cruza com o resultado real).

Variáveis de ambiente obrigatórias: SUPABASE_URL, SUPABASE_KEY (service_role).
"""

from __future__ import annotations

import logging
import os
import sys

import pandas as pd
from supabase import Client, create_client

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dados_historicos as dh
import modelos_ml as ml
import walkforward_cv_v9 as wf9

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
logger = logging.getLogger("backfill_catboost_v9_2026")

MODEL_NAME = "catboost_v9"
CORTE_TREINO_TESTE = pd.Timestamp("2026-01-01", tz="UTC")
MIN_TREINO = 500


def obter_env(nome: str) -> str:
    valor = os.environ.get(nome)
    if not valor:
        sys.exit(f"Configure {nome} antes de rodar.")
    return valor


def upsert_predicoes(supabase: Client, linhas: list[dict]) -> None:
    for inicio in range(0, len(linhas), 500):
        lote = linhas[inicio : inicio + 500]
        supabase.table("model_predictions").upsert(lote, on_conflict="match_id,model_name,market,selection").execute()


def main() -> None:
    supabase = create_client(obter_env("SUPABASE_URL"), obter_env("SUPABASE_KEY"))

    league_ids = list(dh.obter_ids_ligas(supabase, dh.LIGAS_MODEL_BENCHMARKING).values())
    logger.info("Montando dataset 'Feature Stacked' (histórico completo, inclui 2026)...")
    dataset = dh.montar_dataset_ml_empilhado(supabase, league_ids_manual=league_ids)
    if dataset.empty:
        logger.error("Dataset vazio -- não há como treinar/prever.")
        return
    dataset["match_date"] = pd.to_datetime(dataset["match_date"], utc=True)

    features = [f for f in ml.FEATURES_POR_MODELO[MODEL_NAME] if f in dataset.columns]
    faltando = set(ml.FEATURES_POR_MODELO[MODEL_NAME]) - set(features)
    if faltando:
        logger.warning("Features ausentes no dataset (ignoradas): %s", sorted(faltando))
    params = ml.PARAMS_DEFAULT[MODEL_NAME]
    treinar_fn, prever_fn = ml.TREINADORES[MODEL_NAME]

    antes_do_corte = dataset["match_date"] < CORTE_TREINO_TESTE
    total_previsoes = 0

    # --- 1X2 (multiclasse) ---
    treino_1x2 = dataset[antes_do_corte].dropna(subset=[wf9.COLUNA_ALVO, *features])
    alvo_1x2 = dataset[~antes_do_corte & dataset[wf9.COLUNA_ALVO].notna()].reset_index(drop=True)
    if len(treino_1x2) < MIN_TREINO:
        logger.warning("[1x2]: só %d partidas de treino válidas (mínimo %d) -- pulando.", len(treino_1x2), MIN_TREINO)
    elif alvo_1x2.empty:
        logger.info("[1x2]: nenhuma partida de 2026 já disputada encontrada no dataset -- nada a prever.")
    else:
        modelo, extra, _ = treinar_fn(params, treino_1x2, coluna_alvo=wf9.COLUNA_ALVO, features=features)
        probs, classes = prever_fn(modelo, extra, alvo_1x2, features=features)
        linhas = wf9.construir_predicoes(alvo_1x2, probs, classes, MODEL_NAME)
        if linhas:
            upsert_predicoes(supabase, linhas)
            total_previsoes += len(linhas)
            logger.info("[1x2]: %d previsão(ões) gravada(s) em model_predictions (%d partidas de 2026).",
                        len(linhas), len(alvo_1x2))

    # --- over_under_2.5 / btts (binários) ---
    for mercado_cfg in wf9.MERCADOS_BINARIOS:
        coluna_alvo = mercado_cfg["coluna_alvo"]
        market = mercado_cfg["market"]
        mapa_selecao = mercado_cfg["mapa_selecao"]
        if coluna_alvo not in dataset.columns:
            logger.warning("[%s]: coluna %r ausente no dataset -- pulando.", market, coluna_alvo)
            continue

        treino_valido = dataset[antes_do_corte].dropna(subset=[coluna_alvo, *features])
        alvo_mercado = dataset[~antes_do_corte & dataset[coluna_alvo].notna()].reset_index(drop=True)
        if len(treino_valido) < MIN_TREINO:
            logger.warning("[%s]: só %d partidas de treino válidas (mínimo %d) -- pulando.",
                            market, len(treino_valido), MIN_TREINO)
            continue
        if alvo_mercado.empty:
            logger.info("[%s]: nenhuma partida de 2026 já disputada encontrada -- nada a prever.", market)
            continue

        modelo, extra, _ = treinar_fn(params, treino_valido, coluna_alvo=coluna_alvo, features=features)
        probs, classes = prever_fn(modelo, extra, alvo_mercado, features=features)
        linhas = wf9.construir_predicoes_mercado(alvo_mercado, probs, classes, MODEL_NAME, market, mapa_selecao)
        if linhas:
            upsert_predicoes(supabase, linhas)
            total_previsoes += len(linhas)
            logger.info("[%s]: %d previsão(ões) gravada(s) em model_predictions (%d partidas de 2026).",
                        market, len(linhas), len(alvo_mercado))

    logger.info("Concluído: %d previsão(ões) no total.", total_previsoes)


if __name__ == "__main__":
    main()
