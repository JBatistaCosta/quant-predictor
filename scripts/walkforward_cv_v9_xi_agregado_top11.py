#!/usr/bin/env python3
"""Variante 3 de walkforward_cv_v9_xi_agregado.py -- em vez de somar o
elenco relacionado inteiro (`COLUNAS_XI_AGREGADO_JOGADOR`, ~15,4
jogadores/time em média, PR #633), soma só os 11 titulares reconstruídos
por top-11 de `prob_titular` em `xi_titular_walkforward`
(`COLUNAS_XI_AGREGADO_TOP11_JOGADOR`/
`_anexar_forca_xi_agregada_top11_por_partida`, ver comentário completo em
dados_historicos.py).

Motivação (pedido do usuário 22/09, depois de `matriz_confiabilidade_ev.py`
não achar NENHUMA célula confiável pra catboost_v9_xi_agregado): a hipótese
é que somar o banco inteiro (jogadores com prob_titular baixíssima) dilui o
sinal do XI de verdade -- achado ao investigar `player_match_estimates`
(versão de produção desta mesma tabela) que tem uma coluna
`is_titular_previsto` marcando exatamente os 11 titulares, e que
`player_match_walkforward`/`xi_titular_walkforward` (usadas pro treino) não
têm persistida.

Mesmos 3 folds anuais de walkforward_cv_v9.py, compara `catboost_v9`
(FEATURES_V9_XG_CORRIGIDO) contra `catboost_v9_xi_agregado_top11`
(FEATURES_V19_XI_AGREGADO_TOP11) em log-loss/Brier/acurácia -- se não
piorar muito, o próximo passo é rodar matriz_confiabilidade_ev.py (já
inclui esse modelo no escopo) pra checar edge real.

Uso:
    set SUPABASE_URL=...
    set SUPABASE_KEY=sua_service_role_key
    python scripts/walkforward_cv_v9_xi_agregado_top11.py
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
logger = logging.getLogger("walkforward_cv_v9_xi_agregado_top11")

MODELOS = ["catboost_v9", "catboost_v9_xi_agregado_top11"]
MODELO_NOVO = "catboost_v9_xi_agregado_top11"
MERCADOS_PERSISTIR = {"1x2", "over_under_2.5"}  # BTTS fica de fora -- mesmo motivo de catboost_v9 (sem odds).


def obter_env(nome: str) -> str:
    valor = os.environ.get(nome)
    if not valor:
        sys.exit(f"Configure {nome} antes de rodar.")
    return valor


def treinar_e_avaliar(nome: str, coluna_alvo: str, train_df, val_df, test_df) -> tuple[dict, list]:
    """Treina `nome` e devolve (test_probs, test_classes) -- não reaproveita
    `wf9.treinar_modelo_fold` porque esse já assume a métrica 1X2
    (`calcular_metricas`); aqui a métrica muda por mercado."""
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
    logger.info("COMPARAÇÃO catboost_v9 (sem XI agregado) vs. catboost_v9_xi_agregado_top11 (+ xG/gols/chutes só dos 11 titulares reconstruídos)")
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
            "[%s %s] logloss base=%.5f xi_agregado_top11=%.5f (Δ%+.5f, %s) | brier base=%.5f xi_agregado_top11=%.5f (Δ%+.5f) | "
            "acc base=%.4f xi_agregado_top11=%.4f | n_test=%d",
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

    cobertura_xi = dataset[[f"{col}_home" for col in dh.COLUNAS_XI_AGREGADO_TOP11_JOGADOR]].notna().any(axis=1).mean()
    logger.info("Cobertura da feature XI agregado top11 no dataset: %.1f%% das partidas com pelo menos 1 valor não-nulo.", cobertura_xi * 100)

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

    logger.info("=== walkforward_cv_v9_xi_agregado_top11 concluído ===")


if __name__ == "__main__":
    main()
