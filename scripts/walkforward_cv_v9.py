#!/usr/bin/env python3
"""Walk-forward cross-validation v9.

3 folds temporais (por ano de match_date), cada fold expandindo a janela de
treino:
  Fold 1: Train ≤ 2021  |  Val 2022  |  Test 2023
  Fold 2: Train ≤ 2022  |  Val 2023  |  Test 2024
  Fold 3: Train ≤ 2023  |  Val 2024  |  Test 2025

Por fold e por modelo (catboost_v9 / xgboost_v9 / lightgbm_v9 / mlp_v9):
  - Treina no Train set com FEATURES_V9 = FEATURES_V8
  - Avalia no Test set: logloss, Brier Score, acurácia por liga
  - Gera OOF predictions no Val set → alimentam o stacking meta-modelo

Stacking v9:
  - Meta-features: probabilidades dos 4 base models sobre Val de todos os
    folds (OOF) → shape (n_val_total, 4 × 3 = 12)
  - Meta-modelo: LogisticRegression multinomial calibrada
  - Avaliado no Test set do fold_3

Feature coverage:
  - % não-nulos por feature no dataset completo
  - Agrupado por grupo de feature (grupo_da_feature de dados_historicos)

Feature importance:
  - CatBoost built-in importance (get_feature_importance) no modelo treinado
    no fold_3 -- agnóstico ao One-hot encoding, interpretável diretamente
    em termos de features originais
  - Agrupado por grupo de feature

Saída: upsert em 3 tabelas Supabase + JSON estruturado nos logs do Actions.

Variáveis de ambiente obrigatórias: SUPABASE_URL, SUPABASE_KEY.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sklearn.metrics import log_loss
from supabase import create_client

import dados_historicos as dh
import modelos_ml as ml

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("walkforward_cv_v9")

# ---------------------------------------------------------------------------
# Configuração dos folds
# ---------------------------------------------------------------------------
FOLDS = [
    {"nome": "fold_1", "treino_max_ano": 2021, "val_ano": 2022, "test_ano": 2023},
    {"nome": "fold_2", "treino_max_ano": 2022, "val_ano": 2023, "test_ano": 2024},
    {"nome": "fold_3", "treino_max_ano": 2023, "val_ano": 2024, "test_ano": 2025},
]

MODELOS_BASE = ["catboost_v9", "xgboost_v9", "lightgbm_v9", "mlp_v9"]
COLUNA_ALVO = "resultado"
CLASSES_1X2 = np.array([dh.RESULTADO_HOME, dh.RESULTADO_DRAW, dh.RESULTADO_AWAY])

TAMANHO_LOTE_UPSERT = 500


# ---------------------------------------------------------------------------
# Utilitários de split e métricas
# ---------------------------------------------------------------------------

def split_por_ano(dataset: pd.DataFrame, treino_max_ano: int, val_ano: int, test_ano: int):
    """Divide o dataset por ano do match_date — preserva ordenação cronológica."""
    anos = pd.to_datetime(dataset["match_date"]).dt.year
    train_df = dataset[anos <= treino_max_ano].copy()
    val_df = dataset[anos == val_ano].copy()
    test_df = dataset[anos == test_ano].copy()
    return train_df, val_df, test_df


def _log_loss_safe(y_true: np.ndarray, probs: np.ndarray, classes: np.ndarray) -> float:
    """Log-loss multiclasse com reorder de colunas pra bater com y_true."""
    ordered = np.zeros((len(probs), len(CLASSES_1X2)))
    for i, c in enumerate(classes):
        idx = np.where(CLASSES_1X2 == c)[0]
        if len(idx):
            ordered[:, idx[0]] = probs[:, i]
    ordered = np.clip(ordered, 1e-7, 1 - 1e-7)
    return log_loss(y_true, ordered, labels=CLASSES_1X2)


def _brier_score_multiclasse(y_true: np.ndarray, probs: np.ndarray, classes: np.ndarray) -> float:
    """Brier Score multiclasse (média das 3 seleções binárias)."""
    total = 0.0
    for i, c in enumerate(classes):
        y_bin = (y_true == c).astype(float)
        col_idx = np.where(CLASSES_1X2 == c)[0]
        if not len(col_idx):
            continue
        p = probs[:, i]
        total += np.mean((p - y_bin) ** 2)
    return total / len(CLASSES_1X2)


def _acuracia(y_true: np.ndarray, probs: np.ndarray, classes: np.ndarray) -> float:
    classe_pred = classes[np.argmax(probs, axis=1)]
    return float(np.mean(classe_pred == y_true))


def construir_predicoes(test_df: pd.DataFrame, probs: np.ndarray, classes: np.ndarray, model_name: str) -> list[dict]:
    """Converte probabilidades do test set em linhas para `model_predictions` (1 linha por match × seleção)."""
    mapa = {dh.RESULTADO_HOME: "home", dh.RESULTADO_DRAW: "draw", dh.RESULTADO_AWAY: "away"}
    col_por_sel: dict[str, int] = {}
    for col_i, c in enumerate(classes):
        sel = mapa.get(int(c))
        if sel:
            col_por_sel[sel] = col_i

    ids = test_df.reset_index(drop=True)["id"].tolist()
    linhas: list[dict] = []
    for row_i, match_id in enumerate(ids):
        for sel in ("home", "draw", "away"):
            if sel not in col_por_sel:
                continue
            p = float(np.clip(probs[row_i, col_por_sel[sel]], 1e-7, 1 - 1e-7))
            linhas.append({
                "match_id": int(match_id),
                "model_name": model_name,
                "market": "1x2",
                "selection": sel,
                "probability": round(p, 5),
                "fair_odds": round(1.0 / p, 4),
            })
    return linhas


def calcular_metricas(df: pd.DataFrame, probs: np.ndarray, classes: np.ndarray) -> dict:
    y = df[COLUNA_ALVO].values
    logloss = _log_loss_safe(y, probs, classes)
    brier = _brier_score_multiclasse(y, probs, classes)
    acc = _acuracia(y, probs, classes)

    por_liga: dict[str, dict] = {}
    for liga, grupo in df.groupby("liga"):
        mask = grupo.index
        y_l = grupo[COLUNA_ALVO].values
        p_l = probs[df.index.get_indexer(mask)]
        if len(y_l) < 10:
            continue
        por_liga[str(liga)] = {
            "logloss": round(_log_loss_safe(y_l, p_l, classes), 5),
            "brier_score": round(_brier_score_multiclasse(y_l, p_l, classes), 5),
            "accuracy": round(_acuracia(y_l, p_l, classes), 4),
            "n": len(y_l),
        }

    return {
        "logloss_test": round(logloss, 5),
        "brier_score_test": round(brier, 5),
        "accuracy_test": round(acc, 4),
        "logloss_por_liga": por_liga,
    }


# ---------------------------------------------------------------------------
# Treinamento e predição de um modelo base num fold
# ---------------------------------------------------------------------------

def treinar_modelo_fold(
    nome: str,
    train_df: pd.DataFrame,
    val_df: pd.DataFrame,
    test_df: pd.DataFrame,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, object, object]:
    """Treina `nome` em train_df; retorna (val_probs, val_classes, test_probs,
    test_classes, modelo, extra) — val_probs são as OOF predictions pro stacking."""
    treinar_fn, prever_fn = ml.TREINADORES[nome]
    features = ml.FEATURES_POR_MODELO[nome]
    params = ml.PARAMS_DEFAULT[nome]

    modelo, extra, _ = treinar_fn(
        params, train_df,
        coluna_alvo=COLUNA_ALVO,
        features=features,
        val_df=val_df,
        test_df=test_df,
    )

    val_probs, val_classes = prever_fn(modelo, extra, val_df, features=features)
    test_probs, test_classes = prever_fn(modelo, extra, test_df, features=features)

    return val_probs, val_classes, test_probs, test_classes, modelo, extra


# ---------------------------------------------------------------------------
# Feature coverage
# ---------------------------------------------------------------------------

def calcular_cobertura(dataset: pd.DataFrame, features: list[str]) -> list[dict]:
    linhas = []
    for feat in features:
        if feat not in dataset.columns:
            continue
        cobertura = (1 - dataset[feat].isna().mean()) * 100
        grupo = dh.grupo_da_feature(feat)
        row = {
            "feature_name": feat,
            "feature_group": grupo,
            "coverage_pct": round(float(cobertura), 2),
        }
        if feat != "liga":
            col = pd.to_numeric(dataset[feat], errors="coerce")
            row["mean_value"] = round(float(col.mean()), 6) if col.notna().any() else None
            row["std_value"] = round(float(col.std()), 6) if col.notna().any() else None
        else:
            row["mean_value"] = None
            row["std_value"] = None
        linhas.append(row)
    return linhas


# ---------------------------------------------------------------------------
# Feature importance via CatBoost built-in
# ---------------------------------------------------------------------------

def calcular_importancia_catboost(
    modelo_catboost,
    features: list[str],
    fold_nome: str,
) -> list[dict]:
    """CatBoost.get_feature_importance() retorna importância (Shapley-like
    por padrão) por feature na mesma ordem de `features`."""
    try:
        importancias = modelo_catboost.get_feature_importance()
    except Exception as e:
        logger.warning("Falha em get_feature_importance: %s", e)
        return []

    linhas = []
    for feat, imp in zip(features, importancias):
        linhas.append({
            "feature_name": feat,
            "feature_group": dh.grupo_da_feature(feat),
            "importance_mean": round(float(imp), 6),
            "importance_std": None,
            "fold": fold_nome,
        })
    return sorted(linhas, key=lambda x: -x["importance_mean"])


# ---------------------------------------------------------------------------
# Stacking meta-modelo
# ---------------------------------------------------------------------------

def montar_meta_X(probs_por_modelo: dict[str, np.ndarray], classes_por_modelo: dict[str, np.ndarray]) -> np.ndarray:
    """Concatena as probabilidades de todos os modelos base num array de meta-
    features — colunas ordenadas pela ordem de MODELOS_BASE x CLASSES_1X2."""
    blocos = []
    for nome in MODELOS_BASE:
        probs = probs_por_modelo[nome]
        classes = classes_por_modelo[nome]
        # Reordena colunas pra garantir [home, draw, away] consistente
        bloco = np.zeros((len(probs), len(CLASSES_1X2)))
        for i, c in enumerate(classes):
            idx = np.where(CLASSES_1X2 == c)[0]
            if len(idx):
                bloco[:, idx[0]] = probs[:, i]
        blocos.append(bloco)
    return np.hstack(blocos)


# ---------------------------------------------------------------------------
# Upload Supabase
# ---------------------------------------------------------------------------

def upsert_em_lotes(supabase_client, tabela: str, linhas: list[dict]) -> None:
    for inicio in range(0, len(linhas), TAMANHO_LOTE_UPSERT):
        lote = linhas[inicio: inicio + TAMANHO_LOTE_UPSERT]
        supabase_client.table(tabela).upsert(lote, on_conflict="id").execute()


def limpar_tabela(supabase_client, tabela: str) -> None:
    """Remove todas as linhas (pra recomputar do zero em cada run)."""
    supabase_client.table(tabela).delete().neq("id", 0).execute()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_KEY", "")
    if not url or not key:
        logger.error("SUPABASE_URL e SUPABASE_KEY precisam estar definidos.")
        sys.exit(1)

    supabase_client = create_client(url, key)

    logger.info("=== Walk-forward CV v9 iniciado ===")
    logger.info("Carregando dataset ML empilhado...")
    dataset = dh.montar_dataset_ml_empilhado(supabase_client, anos_por_liga=7)
    if dataset.empty:
        logger.error("Dataset vazio — abortando.")
        sys.exit(1)

    dataset = dataset.reset_index(drop=True)
    logger.info("Dataset: %d partidas", len(dataset))

    features_v9 = dh.FEATURES_V9

    # ------------------------------------------------------------------
    # Feature coverage — calculado no dataset completo (uma única vez)
    # ------------------------------------------------------------------
    logger.info("Calculando cobertura de features...")
    cobertura = calcular_cobertura(dataset, features_v9)
    logger.info("JSON:cobertura:%s", json.dumps({"n_features": len(cobertura)}, ensure_ascii=False))

    # ------------------------------------------------------------------
    # Walk-forward CV — 3 folds
    # ------------------------------------------------------------------
    resultados_wf: list[dict] = []
    predicoes_v9: list[dict] = []

    # Acumuladores OOF pra stacking
    oof_y_list: list[np.ndarray] = []
    oof_probs_por_modelo: dict[str, list[np.ndarray]] = {m: [] for m in MODELOS_BASE}
    oof_classes_por_modelo: dict[str, np.ndarray | None] = {m: None for m in MODELOS_BASE}

    # Modelos treinados no fold_3 pra feature importance
    modelo_catboost_fold3: object | None = None

    for fold in FOLDS:
        fold_nome = fold["nome"]
        logger.info("--- %s | treino ≤ %d | val %d | test %d ---",
                    fold_nome, fold["treino_max_ano"], fold["val_ano"], fold["test_ano"])

        train_df, val_df, test_df = split_por_ano(
            dataset, fold["treino_max_ano"], fold["val_ano"], fold["test_ano"]
        )
        logger.info("  n_treino=%d | n_val=%d | n_test=%d", len(train_df), len(val_df), len(test_df))

        if train_df.empty or val_df.empty or test_df.empty:
            logger.warning("  Fold %s com split vazio — pulando.", fold_nome)
            continue

        for nome in MODELOS_BASE:
            logger.info("  Treinando %s...", nome)
            try:
                val_probs, val_classes, test_probs, test_classes, modelo, _ = treinar_modelo_fold(
                    nome, train_df, val_df, test_df
                )
            except Exception as exc:
                logger.error("  Erro em %s fold %s: %s", nome, fold_nome, exc)
                continue

            # Métricas no test set
            metricas = calcular_metricas(test_df.reset_index(drop=True), test_probs, test_classes)
            logger.info("  JSON:resultado:%s", json.dumps({
                "modelo": nome, "fold": fold_nome,
                "logloss": metricas["logloss_test"],
                "brier_score": metricas["brier_score_test"],
                "accuracy": metricas["accuracy_test"],
            }))

            resultados_wf.append({
                "modelo": nome,
                "fold": fold_nome,
                "train_max_ano": fold["treino_max_ano"],
                "val_ano": fold["val_ano"],
                "test_ano": fold["test_ano"],
                "n_treino": len(train_df),
                "n_val": len(val_df),
                "n_test": len(test_df),
                **metricas,
            })

            # Coleta predições OOF do test set pra persistir em model_predictions
            predicoes_v9.extend(construir_predicoes(test_df, test_probs, test_classes, nome))

            # Acumula OOF predictions no val set pra stacking
            oof_probs_por_modelo[nome].append(val_probs)
            oof_classes_por_modelo[nome] = val_classes

            # Guarda modelo do fold_3 pra feature importance
            if fold_nome == "fold_3" and nome == "catboost_v9":
                modelo_catboost_fold3 = modelo

        oof_y_list.append(val_df[COLUNA_ALVO].values)

    # ------------------------------------------------------------------
    # Stacking meta-modelo
    # ------------------------------------------------------------------
    if all(len(oof_probs_por_modelo[m]) == len(FOLDS) for m in MODELOS_BASE):
        logger.info("Treinando stacking meta-modelo...")

        oof_probs_concat = {
            m: np.vstack(oof_probs_por_modelo[m]) for m in MODELOS_BASE
        }
        oof_classes_concat = oof_classes_por_modelo
        oof_y_concat = np.concatenate(oof_y_list)

        meta_X_train = montar_meta_X(oof_probs_concat, oof_classes_concat)
        meta_modelo = ml.treinar_stacking_v9(meta_X_train, oof_y_concat)

        # Avalia stacking no test set do fold_3
        _, _, test_df_fold3 = split_por_ano(
            dataset,
            FOLDS[-1]["treino_max_ano"],
            FOLDS[-1]["val_ano"],
            FOLDS[-1]["test_ano"],
        )
        test_probs_por_modelo: dict[str, np.ndarray] = {}
        test_classes_por_modelo: dict[str, np.ndarray] = {}
        for nome in MODELOS_BASE:
            features = ml.FEATURES_POR_MODELO[nome]
            params = ml.PARAMS_DEFAULT[nome]
            treinar_fn, prever_fn = ml.TREINADORES[nome]
            train_fold3, val_fold3, _ = split_por_ano(
                dataset,
                FOLDS[-1]["treino_max_ano"],
                FOLDS[-1]["val_ano"],
                FOLDS[-1]["test_ano"],
            )
            try:
                modelo_f3, extra_f3, _ = treinar_fn(
                    params, train_fold3, coluna_alvo=COLUNA_ALVO,
                    features=features, val_df=val_fold3, test_df=test_df_fold3,
                )
                tp, tc = prever_fn(modelo_f3, extra_f3, test_df_fold3, features=features)
                test_probs_por_modelo[nome] = tp
                test_classes_por_modelo[nome] = tc
            except Exception as exc:
                logger.error("Refit fold_3 falhou para %s: %s", nome, exc)

        if len(test_probs_por_modelo) == len(MODELOS_BASE):
            meta_X_test = montar_meta_X(test_probs_por_modelo, test_classes_por_modelo)
            stacking_probs, stacking_classes = ml.prever_stacking_v9(meta_modelo, meta_X_test)
            metricas_stacking = calcular_metricas(
                test_df_fold3.reset_index(drop=True), stacking_probs, stacking_classes
            )
            logger.info("JSON:resultado:%s", json.dumps({
                "modelo": "stacking_v9",
                "fold": "fold_3",
                "logloss": metricas_stacking["logloss_test"],
                "brier_score": metricas_stacking["brier_score_test"],
                "accuracy": metricas_stacking["accuracy_test"],
            }))
            resultados_wf.append({
                "modelo": "stacking_v9",
                "fold": FOLDS[-1]["nome"],
                "train_max_ano": FOLDS[-1]["treino_max_ano"],
                "val_ano": FOLDS[-1]["val_ano"],
                "test_ano": FOLDS[-1]["test_ano"],
                "n_treino": len(train_fold3),
                "n_val": len(val_fold3),
                "n_test": len(test_df_fold3),
                **metricas_stacking,
            })
            predicoes_v9.extend(construir_predicoes(test_df_fold3, stacking_probs, stacking_classes, "stacking_v9"))
    else:
        logger.warning("Stacking pulado — nem todos os folds foram completados.")

    # ------------------------------------------------------------------
    # Feature importance (CatBoost fold_3)
    # ------------------------------------------------------------------
    importancia_linhas: list[dict] = []
    if modelo_catboost_fold3 is not None:
        logger.info("Calculando feature importance (CatBoost fold_3)...")
        features_catboost = ml.FEATURES_POR_MODELO["catboost_v9"]
        importancia_linhas = calcular_importancia_catboost(modelo_catboost_fold3, features_catboost, "fold_3")
        top10 = importancia_linhas[:10]
        logger.info("JSON:importancia_top10:%s", json.dumps(
            [{k: v for k, v in r.items() if k in ("feature_name", "feature_group", "importance_mean")} for r in top10],
            ensure_ascii=False,
        ))

    # ------------------------------------------------------------------
    # Persistência no Supabase
    # ------------------------------------------------------------------
    logger.info("Gravando resultados no Supabase...")

    limpar_tabela(supabase_client, "model_v9_walkforward_results")
    if resultados_wf:
        supabase_client.table("model_v9_walkforward_results").insert(resultados_wf).execute()
        logger.info("  model_v9_walkforward_results: %d linhas", len(resultados_wf))

    limpar_tabela(supabase_client, "model_v9_feature_importance")
    if importancia_linhas:
        for inicio in range(0, len(importancia_linhas), TAMANHO_LOTE_UPSERT):
            supabase_client.table("model_v9_feature_importance").insert(
                importancia_linhas[inicio: inicio + TAMANHO_LOTE_UPSERT]
            ).execute()
        logger.info("  model_v9_feature_importance: %d linhas", len(importancia_linhas))

    limpar_tabela(supabase_client, "model_v9_feature_coverage")
    if cobertura:
        for inicio in range(0, len(cobertura), TAMANHO_LOTE_UPSERT):
            supabase_client.table("model_v9_feature_coverage").insert(
                cobertura[inicio: inicio + TAMANHO_LOTE_UPSERT]
            ).execute()
        logger.info("  model_v9_feature_coverage: %d linhas", len(cobertura))

    # model_predictions — previsões OOF por partida (SimulacaoCarteira / backtest)
    MODELOS_V9 = ["catboost_v9", "xgboost_v9", "lightgbm_v9", "mlp_v9", "stacking_v9"]
    logger.info("Limpando previsões v9 anteriores de model_predictions...")
    for _mv9 in MODELOS_V9:
        supabase_client.table("model_predictions").delete().eq("model_name", _mv9).eq("market", "1x2").execute()
    if predicoes_v9:
        for inicio in range(0, len(predicoes_v9), TAMANHO_LOTE_UPSERT):
            supabase_client.table("model_predictions").insert(
                predicoes_v9[inicio: inicio + TAMANHO_LOTE_UPSERT]
            ).execute()
        logger.info("  model_predictions (v9 OOF): %d linhas", len(predicoes_v9))

    logger.info("=== Walk-forward CV v9 concluído ===")


if __name__ == "__main__":
    main()
