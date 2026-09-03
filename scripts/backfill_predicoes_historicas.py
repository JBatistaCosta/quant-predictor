#!/usr/bin/env python3
"""Backfill de previsões HISTÓRICAS (1X2 e Over/Under 2.5) em `predicoes`,
cobrindo o Test Set out-of-sample (temporada 2025+) inteiro -- corrige
"modelos v3+ não aparecem na Simulação de Carteira" e "não houve
treinamento pra Over/Under 2.5 nos outros modelos" (na verdade houve --
`backtest_kelly.py` já treina e avalia os 18 modelos de árvore nesse
mercado -- só nunca tinha sido persistido em lugar nenhum servível pra
Simulação de Carteira ler).

Causa raiz do primeiro problema (não é bug de dedupe/upsert nem falha
silenciosa do cron): o cron diário (`rodar_predicoes.py`) só prevê
fixtures FUTURAS (~10 partidas mais próximas por rodada, ver
`LIMITE_FIXTURES`) -- nunca escreve previsão pra uma partida que já ficou
no passado. Modelos recentes (v3, v4, v5, v3B) simplesmente ainda não
tiveram tempo de calendário suficiente pra acumular profundidade histórica
sozinhos, e a Simulação de Carteira (`api/model-maintenance.js`,
`tarefaModelosDisponiveis`) só lista um modelo quando ele tem pelo menos 1
partida FINALIZADA com previsão persistida.

Causa raiz do segundo (Over/Under 2.5): `predicoes` só tinha colunas de
1X2 (prob_home/draw/away) até a migração `add_mercado_predicoes` -- os
modelos de árvore JÁ tinham treino/avaliação real nesse mercado
(`model_benchmarking_backtest`, mercado='over_under_2.5'), só faltava
persistir a previsão por partida em algum lugar que a Simulação de
Carteira pudesse ler.

Este script fecha os dois buracos de uma vez: roda os MESMOS modelos do
cron diário (dixon_coles_v1 + os 18 modelos de árvore, todas as versões
v1-v5/v3B) sobre TODO o Test Set out-of-sample, pros dois mercados, com a
MESMA metodologia ponto-no-tempo de `backtest_kelly.py` (treino/validação
= só dados ANTERIORES ao início do Test Set -- nunca olha o resultado real
de uma partida do Test Set pra prever ela mesma) e persiste cada previsão
(crua + calibrada Platt/Isotonic) em `predicoes`, reaproveitando as MESMAS
funções de produção (`rodar_predicoes.prever_ml_com_calibracao` pros
modelos de árvore, `backtest_kelly.tunar_e_calibrar_dixon_coles`/
`prever_dixon_coles_backtest` pro Dixon-Coles) -- sem duplicar lógica de
treino/calibração/persistência.

Só 1X2 e Over/Under 2.5 (os 2 únicos mercados com odds reais em
`odds_market`, ver `MERCADOS_CARTEIRA_SUPORTADOS` em api/model-
maintenance.js) -- os mercados novos do Model Benchmarking sem odd
nenhuma (Over/Under 9.5 escanteios, faixa de gols) continuam vivendo só no
relatório de qualidade do `backtest_kelly.py`, nunca servidos ao vivo (uma
simulação de carteira sem odd é sempre 0 apostas).

Idempotente (upsert por match_id+model_name+mercado) -- seguro rodar de
novo quantas vezes for preciso. Roda MANUALMENTE (`python scripts/
backfill_predicoes_historicas.py`), não faz parte do cron diário
(`predict.yml`).

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

    # Partidas cruas (times/gols/data) por trás de cada fatia do split --
    # carregadas UMA vez fora dos try/except dos dois mercados, pra uma
    # falha no dixon_coles de 1X2 não deixar essas variáveis indefinidas e
    # quebrar o dixon_coles de over_under_2.5 depois.
    match_ids_treino = train_df["match_id"].astype(int).tolist()
    match_ids_val = val_df["match_id"].astype(int).tolist()
    match_ids_treino_val = train_mais_val_df["match_id"].astype(int).tolist()
    partidas_treino = dados_historicos.carregar_partidas_por_id(supabase, match_ids_treino)
    partidas_val = dados_historicos.carregar_partidas_por_id(supabase, match_ids_val)
    partidas_treino_val = dados_historicos.carregar_partidas_por_id(supabase, match_ids_treino_val)
    partidas_teste = dados_historicos.carregar_partidas_por_id(supabase, match_ids_teste)

    # --- dixon_coles_v1: mesma força/calibração ponto-no-tempo do backtest
    # (força estimada só com dados ANTERIORES ao início do Test Set) ---
    try:
        logger.info("Backfillando dixon_coles_v1 [1X2] (%d partidas)...", len(partidas_teste))
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
        logger.exception("Falha no backfill do dixon_coles_v1 [1X2] -- pulando, os outros modelos continuam.")

    # --- catboost/xgboost/lightgbm (v1-v5, v3B): mesma função de produção,
    # treino/predição 1X2, sem grid search (usa modelos_ml.PARAMS_DEFAULT,
    # igual o cron diário -- tuning de verdade é papel de backtest_kelly.py,
    # não deste backfill) ---
    for nome_modelo in modelos_ml.TREINADORES:
        try:
            logger.info("Backfillando %s [1X2] (%d partidas)...", nome_modelo, len(match_ids_teste))
            preds_raw, preds_calibradas_por_metodo = rp.prever_ml_com_calibracao(nome_modelo, test_df, train_mais_val_df)
            rp._persistir_cru_e_calibrados(nome_modelo, preds_raw, preds_calibradas_por_metodo, {}, todas_as_linhas)
        except Exception:
            logger.exception("Falha ao backfillar %s [1X2] -- pulando, os outros modelos continuam.", nome_modelo)

    # =========================================================================
    # Over/Under 2.5 gols -- mesmo Test Set, mesma partição treino/val/teste
    # (resultado_over25 já vem calculado no dataset, sem NaN, não precisa de
    # filtro por mercado igual escanteios/faixa de gols precisaram).
    # =========================================================================
    codigo_por_selecao_ou25 = {"under": dados_historicos.RESULTADO_UNDER25, "over": dados_historicos.RESULTADO_OVER25}
    selecoes_ou25 = tuple(codigo_por_selecao_ou25.keys())

    try:
        logger.info("Backfillando dixon_coles_v1 [over_under_2.5] (%d partidas)...", len(partidas_teste))
        forcas_finais_ou25, coeficientes_por_metodo_ou25 = bk.tunar_e_calibrar_dixon_coles(
            partidas_treino, partidas_val, partidas_treino_val, partidas_teste["match_date"].min(), "over_under_2.5"
        )
        preds_raw_ou25 = bk.prever_dixon_coles_backtest(forcas_finais_ou25, partidas_teste)
        preds_calibradas_por_metodo_ou25 = {
            metodo: {mid: calibracao.aplicar_calibracao(p, coef, selecoes=selecoes_ou25) for mid, p in preds_raw_ou25.items()}
            for metodo, coef in coeficientes_por_metodo_ou25.items()
        }
        rp._persistir_cru_e_calibrados_over_under25("dixon_coles_v1", preds_raw_ou25, preds_calibradas_por_metodo_ou25, todas_as_linhas)

        # Gols por time (over_under_team_1/2_X.X) -- `preds_raw_ou25` já tem
        # essas chaves (mesma chamada de `_prever_probs_dixon_coles` que
        # calcula 1X2/over_under_2.5/faixa_gols/gols por time de uma vez só,
        # sem custo extra), então não precisa reprever nada. Só a variante
        # CRUA (`{}` no lugar do dict de calibração): `preds_calibradas_
        # por_metodo_ou25` foi calibrado com `selecoes=("under","over")`
        # (ver `calibracao.aplicar_calibracao`), que reconstrói o dict só
        # com essas 2 chaves -- reusar aqui daria KeyError em
        # `prob_team_1_over_X.X`. Calibrar de verdade esses mercados fica
        # pra quando/se justificar (mesmo raciocínio do cron diário, ver
        # `rodar_predicoes.main()`).
        logger.info("Backfillando dixon_coles_v1 [gols por time] (%d partidas)...", len(partidas_teste))
        rp._persistir_cru_e_calibrados_gols_time("dixon_coles_v1", preds_raw_ou25, {}, todas_as_linhas)
    except Exception:
        logger.exception("Falha no backfill do dixon_coles_v1 [over_under_2.5 / gols por time] -- pulando, os outros modelos continuam.")

    for nome_modelo in modelos_ml.TREINADORES:
        try:
            logger.info("Backfillando %s [over_under_2.5] (%d partidas)...", nome_modelo, len(match_ids_teste))
            preds_raw_ou25, preds_calibradas_por_metodo_ou25 = rp.prever_ml_com_calibracao(
                nome_modelo, test_df, train_mais_val_df,
                coluna_alvo="resultado_over25", codigo_por_selecao=codigo_por_selecao_ou25, selecoes=selecoes_ou25,
            )
            rp._persistir_cru_e_calibrados_over_under25(nome_modelo, preds_raw_ou25, preds_calibradas_por_metodo_ou25, todas_as_linhas)
        except Exception:
            logger.exception("Falha ao backfillar %s [over_under_2.5] -- pulando, os outros modelos continuam.", nome_modelo)

    logger.info("Persistindo %d linha(s) em `predicoes` (upsert por match_id+model_name+mercado)...", len(todas_as_linhas))
    rp.salvar_predicoes(supabase, todas_as_linhas)
    logger.info("Backfill concluído.")


if __name__ == "__main__":
    main()
