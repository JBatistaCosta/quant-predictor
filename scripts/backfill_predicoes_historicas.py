#!/usr/bin/env python3
"""Backfill de previsões 1X2 HISTÓRICAS em `predicoes`, cobrindo o Test Set
out-of-sample (temporada 2025+) inteiro -- corrige "modelos v3+ não
aparecem na Simulação de Carteira".

Causa raiz (não é bug de dedupe/upsert nem falha silenciosa do cron): o
cron diário (`rodar_predicoes.py`) só prevê fixtures FUTURAS (~10 partidas
mais próximas por rodada, ver `LIMITE_FIXTURES`) -- nunca escreve previsão
pra uma partida que já ficou no passado. Modelos recentes (v3, v4, v5, v3B)
simplesmente ainda não tiveram tempo de calendário suficiente pra acumular
profundidade histórica sozinhos, e a Simulação de Carteira
(`api/model-maintenance.js`, `tarefaModelosDisponiveis`) só lista um modelo
quando ele tem pelo menos 1 partida FINALIZADA com previsão persistida.

Este script fecha esse buraco de uma vez: roda os MESMOS modelos do cron
diário (dixon_coles_v1 + os 18 modelos de árvore, todas as versões
v1-v5/v3B) sobre TODO o Test Set out-of-sample, com a MESMA metodologia
ponto-no-tempo de `backtest_kelly.py` (treino/validação = só dados
ANTERIORES ao início do Test Set -- nunca olha o resultado real de uma
partida do Test Set pra prever ela mesma) e persiste cada previsão (crua +
calibrada Platt/Isotonic) em `predicoes`, reaproveitando as MESMAS funções
de produção (`rodar_predicoes.prever_ml_com_calibracao` pros modelos de
árvore, `backtest_kelly.tunar_e_calibrar_dixon_coles`/
`prever_dixon_coles_backtest` pro Dixon-Coles) -- sem duplicar lógica de
treino/calibração/persistência.

Só mercado 1X2 (`predicoes` não tem colunas pra outros mercados) -- os
mercados novos (Over/Under 9.5 escanteios, faixa de gols) vivem só no
relatório de qualidade do `backtest_kelly.py`, não são servidos ao vivo.

Idempotente (upsert por match_id+model_name, mesma tabela/chave do cron
diário) -- seguro rodar de novo quantas vezes for preciso. Roda
MANUALMENTE (`python scripts/backfill_predicoes_historicas.py`), não faz
parte do cron diário (`predict.yml`).

Variáveis de ambiente obrigatórias: SUPABASE_URL, SUPABASE_KEY.
"""

from __future__ import annotations

import logging

import pandas as pd

import backtest_kelly as bk
import calibracao
import dados_historicos
import modelos_ml
import rodar_predicoes as rp

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backfill_predicoes_historicas")


def main() -> None:
    supabase = rp.get_supabase_client()

    logger.info("Montando dataset 'Feature Stacked' (mesma janela do backtest/produção)...")
    dataset = dados_historicos.montar_dataset_ml_empilhado(supabase, anos_por_liga=rp.JANELA_ML_ANOS)
    if dataset.empty:
        logger.error("Dataset vazio -- não há como backfillar.")
        return

    train_df, val_df, test_df = dados_historicos.split_cronologico(dataset)
    if test_df.empty:
        logger.error("Test Set vazio -- nada pra backfillar.")
        return
    train_mais_val_df = pd.concat([train_df, val_df], ignore_index=True)

    match_ids_teste = test_df["match_id"].astype(int).tolist()
    logger.info(
        "Test Set out-of-sample: %d partidas (%s a %s).",
        len(match_ids_teste),
        test_df["match_date"].min().date(),
        test_df["match_date"].max().date(),
    )

    todas_as_linhas: list[dict] = []

    # --- dixon_coles_v1: mesma força/calibração ponto-no-tempo do backtest
    # (força estimada só com dados ANTERIORES ao início do Test Set) ---
    try:
        match_ids_treino = train_df["match_id"].astype(int).tolist()
        match_ids_val = val_df["match_id"].astype(int).tolist()
        match_ids_treino_val = train_mais_val_df["match_id"].astype(int).tolist()
        partidas_treino = dados_historicos.carregar_partidas_por_id(supabase, match_ids_treino)
        partidas_val = dados_historicos.carregar_partidas_por_id(supabase, match_ids_val)
        partidas_treino_val = dados_historicos.carregar_partidas_por_id(supabase, match_ids_treino_val)
        partidas_teste = dados_historicos.carregar_partidas_por_id(supabase, match_ids_teste)

        logger.info("Backfillando dixon_coles_v1 (%d partidas)...", len(partidas_teste))
        forcas_finais, coeficientes_por_metodo = bk.tunar_e_calibrar_dixon_coles(
            partidas_treino, partidas_val, partidas_treino_val, partidas_teste["match_date"].min(), "1X2"
        )
        preds_raw = bk.prever_dixon_coles_backtest(forcas_finais, partidas_teste)
        preds_calibradas_por_metodo = {
            metodo: {mid: calibracao.aplicar_calibracao(p, coef) for mid, p in preds_raw.items()}
            for metodo, coef in coeficientes_por_metodo.items()
        }
        rp._persistir_cru_e_calibrados("dixon_coles_v1", preds_raw, preds_calibradas_por_metodo, {}, todas_as_linhas)
    except Exception:
        logger.exception("Falha no backfill do dixon_coles_v1 -- pulando, os outros modelos continuam.")

    # --- catboost/xgboost/lightgbm (v1-v5, v3B): mesma função de produção,
    # treino/predição 1X2, sem grid search (usa modelos_ml.PARAMS_DEFAULT,
    # igual o cron diário -- tuning de verdade é papel de backtest_kelly.py,
    # não deste backfill) ---
    for nome_modelo in modelos_ml.TREINADORES:
        try:
            logger.info("Backfillando %s (%d partidas)...", nome_modelo, len(match_ids_teste))
            preds_raw, preds_calibradas_por_metodo = rp.prever_ml_com_calibracao(nome_modelo, test_df, train_mais_val_df)
            rp._persistir_cru_e_calibrados(nome_modelo, preds_raw, preds_calibradas_por_metodo, {}, todas_as_linhas)
        except Exception:
            logger.exception("Falha ao backfillar %s -- pulando, os outros modelos continuam.", nome_modelo)

    logger.info("Persistindo %d linha(s) em `predicoes` (upsert por match_id+model_name)...", len(todas_as_linhas))
    rp.salvar_predicoes(supabase, todas_as_linhas)
    logger.info("Backfill concluído.")


if __name__ == "__main__":
    main()
