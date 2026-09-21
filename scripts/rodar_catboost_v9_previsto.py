#!/usr/bin/env python3
"""Treina `catboost_v9` (hoje só existe como backtest walk-forward em
`walkforward_cv_v9.py`, previsões persistidas em `model_predictions` cobrindo
só jan/2023-dez/2025) com TODO o histórico disponível e gera previsão pras
partidas AINDA NÃO disputadas -- mesmo gap já fechado pra `cartoes_rf` em
`rodar_cartoes_rf_previsto.py` (ver docstring lá pro contexto completo do
padrão). Sem isso, catboost_v9 nunca teria previsão pra partida `scheduled`,
e a Carteira (Paper Trading) nunca teria como apostar nele -- inclusive na
faixa mandante/1X2/odd 1.30-2.50/edge 5-10% que a matriz de confiabilidade EV
marcou como `confiavel=true` (n=586, IC95% da média E da mediana positivos).

DESENHO (reaproveita a infraestrutura já existente, não reinventa):
  - Mesmo treinador/features de produção usados pelo cron diário
    (`rodar_predicoes.py`): `modelos_ml.TREINADORES["catboost_v9"]`
    (`treinar_catboost`/`prever_catboost`) + `modelos_ml.FEATURES_POR_MODELO
    ["catboost_v9"]`. Chamado SEM `val_df`/`test_df` -- sem holdout, treina
    até o teto fixo `ITERACOES_PRODUCAO["catboost"]` (mesmo comportamento já
    documentado no docstring de `treinar_catboost` pro treino diário em
    produção), nunca os 3 folds anuais de `walkforward_cv_v9.py` (esses são
    só pra avaliação histórica).
  - `dados_historicos.montar_dataset_ml_empilhado(..., match_ids_extra=
    match_ids_alvo)` -- mesmo mecanismo de `rodar_cartoes_rf_previsto.py`/
    `prever_partidas_futuras_custom.py` pra injetar partida futura no mesmo
    pipeline de features do histórico.
  - Serialização das previsões em `model_predictions`: reaproveita
    `walkforward_cv_v9.construir_predicoes` (1x2) e
    `walkforward_cv_v9.construir_predicoes_mercado` (over_under_2.5/btts,
    via `walkforward_cv_v9.MERCADOS_BINARIOS`) -- NÃO usa `treinar_modelo_
    custom.salvar_predicoes_no_banco` porque o `target_key="1x2"` de
    `_TARGET_PRED_META` grava `market="1X2"` (maiúsculo), incompatível com
    as 6.474 linhas históricas de catboost_v9 já gravadas com `market="1x2"`
    (minúsculo, ver `MERCADOS_CATBOOST_V9` em `matriz_confiabilidade_ev.py`
    e `walkforward_cv_v9.construir_predicoes`) -- usar a função errada
    duplicaria o mercado sob duas grafias e quebraria todo consumidor
    (matriz de confiabilidade, paper trading) que filtra por `market='1x2'`.
  - Upsert em `model_predictions` com o mesmo `on_conflict="match_id,
    model_name,market,selection"` de `salvar_predicoes_no_banco` (a
    constraint única já existe no banco) -- reruns diários substituem a
    previsão anterior da mesma partida em vez de duplicar.

Escopo de partidas-alvo: as 6 ligas do Model Benchmarking
(`dados_historicos.LIGAS_MODEL_BENCHMARKING`), `status='scheduled'`, dentro
de `JANELA_DIAS`.

Variáveis de ambiente obrigatórias: SUPABASE_URL, SUPABASE_KEY (service_role).
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd
from supabase import Client, create_client

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dados_historicos as dh
import modelos_ml as ml
import walkforward_cv_v9 as wf9

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
logger = logging.getLogger("rodar_catboost_v9_previsto")

MODEL_NAME = "catboost_v9"
JANELA_DIAS = 14
MIN_TREINO = 500  # sanity check -- dataset histórico real tem milhares de linhas


def obter_env(nome: str) -> str:
    valor = os.environ.get(nome)
    if not valor:
        sys.exit(f"Configure {nome} antes de rodar.")
    return valor


def buscar_partidas_alvo(supabase: Client, league_ids: list[int]) -> list[int]:
    if not league_ids:
        return []
    ate_iso = (datetime.now(timezone.utc) + timedelta(days=JANELA_DIAS)).isoformat()
    resp = (
        supabase.table("matches")
        .select("id")
        .in_("league_id", league_ids)
        .eq("status", "scheduled")
        .lte("match_date", ate_iso)
        .execute()
    )
    return [linha["id"] for linha in (resp.data or [])]


def upsert_predicoes(supabase: Client, linhas: list[dict]) -> None:
    for inicio in range(0, len(linhas), 500):
        lote = linhas[inicio : inicio + 500]
        supabase.table("model_predictions").upsert(lote, on_conflict="match_id,model_name,market,selection").execute()


def main() -> None:
    supabase = create_client(obter_env("SUPABASE_URL"), obter_env("SUPABASE_KEY"))

    league_ids = list(dh.obter_ids_ligas(supabase, dh.LIGAS_MODEL_BENCHMARKING).values())
    match_ids_alvo = buscar_partidas_alvo(supabase, league_ids)
    if not match_ids_alvo:
        logger.info("Nenhuma partida agendada dentro do escopo (%d ligas) nos próximos %d dias -- encerrando.",
                    len(league_ids), JANELA_DIAS)
        return
    logger.info("%d partida(s) agendada(s) candidata(s) (próximos %d dias).", len(match_ids_alvo), JANELA_DIAS)

    logger.info("Montando dataset 'Feature Stacked' (histórico + partidas-alvo)...")
    dataset = dh.montar_dataset_ml_empilhado(supabase, league_ids_manual=league_ids, match_ids_extra=match_ids_alvo)
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

    alvo_df = dataset[dataset["match_id"].isin(match_ids_alvo)].reset_index(drop=True)
    if alvo_df.empty:
        logger.warning("Nenhuma das %d partida(s) candidata(s) sobreviveu ao dataset final "
                        "(provável falta de histórico recente dos times pra montar a média móvel) -- encerrando.",
                        len(match_ids_alvo))
        return

    total_previsoes = 0

    # --- 1X2 (multiclasse) ---
    treino_1x2 = dataset.dropna(subset=[wf9.COLUNA_ALVO, *features])
    if len(treino_1x2) < MIN_TREINO:
        logger.warning("[1x2]: só %d partidas de treino válidas (mínimo %d) -- pulando.", len(treino_1x2), MIN_TREINO)
    else:
        modelo, extra, _ = treinar_fn(params, treino_1x2, coluna_alvo=wf9.COLUNA_ALVO, features=features)
        probs, classes = prever_fn(modelo, extra, alvo_df, features=features)
        linhas = wf9.construir_predicoes(alvo_df, probs, classes, MODEL_NAME)
        if linhas:
            upsert_predicoes(supabase, linhas)
            total_previsoes += len(linhas)
            logger.info("[1x2]: %d previsão(ões) gravada(s) em model_predictions.", len(linhas))

    # --- over_under_2.5 / btts (binários) ---
    for mercado_cfg in wf9.MERCADOS_BINARIOS:
        coluna_alvo = mercado_cfg["coluna_alvo"]
        market = mercado_cfg["market"]
        mapa_selecao = mercado_cfg["mapa_selecao"]
        if coluna_alvo not in dataset.columns:
            logger.warning("[%s]: coluna %r ausente no dataset -- pulando.", market, coluna_alvo)
            continue

        treino_valido = dataset.dropna(subset=[coluna_alvo, *features])
        if len(treino_valido) < MIN_TREINO:
            logger.warning("[%s]: só %d partidas de treino válidas (mínimo %d) -- pulando.",
                            market, len(treino_valido), MIN_TREINO)
            continue

        modelo, extra, _ = treinar_fn(params, treino_valido, coluna_alvo=coluna_alvo, features=features)
        probs, classes = prever_fn(modelo, extra, alvo_df, features=features)
        linhas = wf9.construir_predicoes_mercado(alvo_df, probs, classes, MODEL_NAME, market, mapa_selecao)
        if linhas:
            upsert_predicoes(supabase, linhas)
            total_previsoes += len(linhas)
            logger.info("[%s]: %d previsão(ões) gravada(s) em model_predictions.", market, len(linhas))

    logger.info("Concluído: %d previsão(ões) no total.", total_previsoes)


if __name__ == "__main__":
    main()
