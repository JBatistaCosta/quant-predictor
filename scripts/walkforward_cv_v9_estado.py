#!/usr/bin/env python3
"""Testa se a feature "qualidade de finalização por estado do jogo"
(ganhando/perdendo, achado da fase 2 de comportamento — ver CLAUDE.md e
`ACHADOS_COMPORTAMENTO.md`) ajuda o CatBoost, mesmo tendo falhado no GLM
híbrido (`hibrido_gols_xg_v2_estado`, mesma feature, sem célula confiável
na matriz de confiabilidade EV — achado 21/09, documentado em
`model_betting_strategy`). Decisão do usuário: testar isolado no CatBoost
em vez de assumir que o resultado do híbrido já responde pela pergunta —
`catboost_v9` é o único algoritmo/modelo deste projeto que já provou achar
edge real contra o mercado, e um classificador direto de resultado (CatBoost)
é estruturalmente diferente de um GLM de Poisson sobre xG (a mesma feature
pode ajudar um e não o outro).

Compara, nos MESMOS 3 folds anuais de `walkforward_cv_v9.py` (Fold 1:
Train≤2021|Val2022|Test2023, Fold 2: Train≤2022|Val2023|Test2024, Fold 3:
Train≤2023|Val2024|Test2025 — mantém comparabilidade direta com catboost_v9,
por isso não usa os folds mensais de
`validar_hibrido_walkforward_incremental.py`), `catboost_v9`
(FEATURES_V9_XG_CORRIGIDO) contra `catboost_v9_estado`
(FEATURES_V16_CATBOOST_ESTADO = FEATURES_V9_XG_CORRIGIDO + as 8 features de
qualidade-por-chute ganhando/perdendo, ver dados_historicos.py) em log-loss/
Brier Score/acurácia — responde primeiro "a feature ajuda a CALIBRAR melhor
a probabilidade" (esta rodada), antes de saber se ajuda a bater o mercado
(pergunta separada, respondida depois rodando `matriz_confiabilidade_ev.py`
com `catboost_v9_estado` no escopo -- ver MODELOS_CATBOOST_V9 nesse script).

Reaproveita as funções de split/métrica/serialização de `walkforward_cv_v9.py`
em vez de duplicá-las -- só o loop de modelos é outro (2 modelos, não 4, sem
stacking).

Persistência: só as previsões de `catboost_v9_estado` (não catboost_v9, que
já está persistido por `walkforward_cv_v9.py`) em `model_predictions`, mesma
convenção `market='1x2'` minúsculo já usada por catboost_v9 -- mercados
1x2 e over_under_2.5 (BTTS fica de fora pelo mesmo motivo documentado pra
catboost_v9: sem sobreposição de odds capturadas, daria zero apostas sempre
-- mas o log-loss de BTTS ainda é calculado e reportado aqui, só não é
persistido).

Uso:
    set SUPABASE_URL=...
    set SUPABASE_KEY=sua_service_role_key
    python scripts/walkforward_cv_v9_estado.py
"""

from __future__ import annotations

import json
import logging
import os
import sys

from supabase import create_client

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dados_historicos as dh
import modelos_ml as ml
import walkforward_cv_v9 as wf9

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("walkforward_cv_v9_estado")

MODELOS = ["catboost_v9", "catboost_v9_estado"]
MODELO_NOVO = "catboost_v9_estado"
MERCADOS_PERSISTIR = {"1x2", "over_under_2.5"}  # BTTS fica de fora -- mesmo motivo de catboost_v9 (sem odds).


def obter_env(nome: str) -> str:
    valor = os.environ.get(nome)
    if not valor:
        sys.exit(f"Configure {nome} antes de rodar.")
    return valor


def treinar_e_avaliar(nome: str, coluna_alvo: str, train_df, val_df, test_df) -> tuple[dict, list]:
    """Treina `nome` e devolve (metricas, [test_probs, test_classes]) -- não
    reaproveita `wf9.treinar_modelo_fold` porque esse já assume a métrica
    1X2 (`calcular_metricas`); aqui a métrica muda por mercado."""
    treinar_fn, prever_fn = ml.TREINADORES[nome]
    features = [f for f in ml.FEATURES_POR_MODELO[nome] if f in train_df.columns]
    params = ml.PARAMS_DEFAULT[nome]
    modelo, extra, _ = treinar_fn(
        params, train_df, coluna_alvo=coluna_alvo, features=features, val_df=val_df, test_df=test_df,
    )
    test_probs, test_classes = prever_fn(modelo, extra, test_df, features=features)
    return test_probs, test_classes


def imprimir_comparacao(resultados: list[dict]) -> None:
    logger.info("=" * 130)
    logger.info("COMPARAÇÃO catboost_v9 (sem estado) vs. catboost_v9_estado (+ qualidade-por-chute ganhando/perdendo)")
    logger.info("=" * 130)
    por_mercado_fold: dict[tuple[str, str], dict[str, dict]] = {}
    for r in resultados:
        por_mercado_fold.setdefault((r["mercado"], r["fold"]), {})[r["modelo"]] = r
    for (mercado, fold), por_modelo in sorted(por_mercado_fold.items()):
        base = por_modelo.get("catboost_v9")
        novo = por_modelo.get(MODELO_NOVO)
        if not base or not novo:
            continue
        delta_logloss = novo["logloss_test"] - base["logloss_test"]
        delta_brier = novo["brier_score_test"] - base["brier_score_test"]
        logger.info(
            "[%s %s] logloss base=%.5f estado=%.5f (Δ%+.5f, %s) | brier base=%.5f estado=%.5f (Δ%+.5f) | "
            "acc base=%.4f estado=%.4f | n_test=%d",
            mercado, fold, base["logloss_test"], novo["logloss_test"], delta_logloss,
            "MELHOROU" if delta_logloss < 0 else "piorou",
            base["brier_score_test"], novo["brier_score_test"], delta_brier,
            base["accuracy_test"], novo["accuracy_test"], base["n_test"],
        )
    logger.info("=" * 130)


def main() -> None:
    supabase = create_client(obter_env("SUPABASE_URL"), obter_env("SUPABASE_KEY"))

    logger.info("Carregando dataset ML empilhado (mesmo escopo de walkforward_cv_v9.py)...")
    dataset = dh.montar_dataset_ml_empilhado(supabase, anos_por_liga=7)
    if dataset.empty:
        logger.error("Dataset vazio -- abortando.")
        sys.exit(1)
    dataset = dataset.reset_index(drop=True)
    logger.info("Dataset: %d partidas.", len(dataset))

    resultados: list[dict] = []
    predicoes_a_persistir: list[dict] = []

    for fold in wf9.FOLDS:
        fold_nome = fold["nome"]
        logger.info("--- %s | treino ≤ %d | val %d | test %d ---",
                    fold_nome, fold["treino_max_ano"], fold["val_ano"], fold["test_ano"])
        train_df, val_df, test_df = wf9.split_por_ano(
            dataset, fold["treino_max_ano"], fold["val_ano"], fold["test_ano"]
        )
        if train_df.empty or val_df.empty or test_df.empty:
            logger.warning("  Fold %s com split vazio -- pulando.", fold_nome)
            continue

        # --- 1X2 ---
        for nome in MODELOS:
            try:
                test_probs, test_classes = treinar_e_avaliar(nome, wf9.COLUNA_ALVO, train_df, val_df, test_df)
            except Exception as exc:
                logger.error("  Erro em %s [1x2] fold %s: %s", nome, fold_nome, exc)
                continue
            metricas = wf9.calcular_metricas(test_df.reset_index(drop=True), test_probs, test_classes, train_df)
            logger.info("  JSON:resultado:%s", json.dumps({
                "mercado": "1x2", "modelo": nome, "fold": fold_nome,
                "logloss": metricas["logloss_test"], "brier_score": metricas["brier_score_test"],
                "accuracy": metricas["accuracy_test"],
            }))
            resultados.append({"mercado": "1x2", "modelo": nome, "fold": fold_nome, "n_test": len(test_df), **metricas})
            if nome == MODELO_NOVO and "1x2" in MERCADOS_PERSISTIR:
                predicoes_a_persistir.extend(wf9.construir_predicoes(test_df, test_probs, test_classes, nome))

        # --- Mercados binários (over_under_2.5, btts) ---
        for cfg in wf9.MERCADOS_BINARIOS:
            for nome in MODELOS:
                try:
                    test_probs, test_classes = treinar_e_avaliar(nome, cfg["coluna_alvo"], train_df, val_df, test_df)
                except Exception as exc:
                    logger.error("  Erro em %s [%s] fold %s: %s", nome, cfg["market"], fold_nome, exc)
                    continue
                metricas = wf9.calcular_metricas_binario(
                    test_df.reset_index(drop=True), test_probs, test_classes, cfg["coluna_alvo"], train_df
                )
                logger.info("  JSON:resultado_binario:%s", json.dumps({
                    "mercado": cfg["market"], "modelo": nome, "fold": fold_nome,
                    "logloss": metricas["logloss_test"], "brier_score": metricas["brier_score_test"],
                    "accuracy": metricas["accuracy_test"],
                }))
                resultados.append({"mercado": cfg["market"], "modelo": nome, "fold": fold_nome, "n_test": len(test_df), **metricas})
                if nome == MODELO_NOVO and cfg["market"] in MERCADOS_PERSISTIR:
                    predicoes_a_persistir.extend(wf9.construir_predicoes_mercado(
                        test_df, test_probs, test_classes, nome, cfg["market"], cfg["mapa_selecao"]
                    ))

    imprimir_comparacao(resultados)

    logger.info("Limpando previsões anteriores de %s em model_predictions...", MODELO_NOVO)
    for mercado in MERCADOS_PERSISTIR:
        wf9._executar_com_retry(
            lambda m=mercado: supabase.table("model_predictions")
                .delete().eq("model_name", MODELO_NOVO).eq("market", m).execute()
        )
    if predicoes_a_persistir:
        wf9.inserir_em_lotes(supabase, "model_predictions", predicoes_a_persistir)
        logger.info("model_predictions (%s): %d linhas.", MODELO_NOVO, len(predicoes_a_persistir))
    else:
        logger.warning("Nenhuma previsão de %s a persistir.", MODELO_NOVO)

    logger.info("=== walkforward_cv_v9_estado concluído ===")


if __name__ == "__main__":
    main()
