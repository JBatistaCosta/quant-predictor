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

Mercados treinados:
  - 1x2            (multiclasse: home/draw/away)
  - over_under_2.5 (binário: over/under)
  - btts           (binário: yes/no — Both Teams To Score)

Stacking v9 (duas variantes para 1X2; apenas stacking_v9 para mercados binários):
  - stacking_v9:         4 base models → meta-features (n_val_total, 4×3=12 / 4×2=8)
  - stacking_v9_sem_mlp: 3 base models (sem MLP) → (n_val_total, 3×3=9) — apenas 1X2
  - Meta-modelo: LogisticRegression calibrada
  - Avaliados no Test set do fold_3

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
MODELOS_SEM_MLP = ["catboost_v9", "xgboost_v9", "lightgbm_v9"]
COLUNA_ALVO = "resultado"
CLASSES_1X2 = np.array([dh.RESULTADO_HOME, dh.RESULTADO_DRAW, dh.RESULTADO_AWAY])
CLASSES_BINARIAS = np.array([0, 1])

# Configuração de cada mercado binário adicional
MERCADOS_BINARIOS = [
    {
        "coluna_alvo": "resultado_over25",
        "market": "over_under_2.5",
        "mapa_selecao": {dh.RESULTADO_UNDER25: "under", dh.RESULTADO_OVER25: "over"},
    },
    {
        "coluna_alvo": "resultado_btts",
        "market": "btts",
        "mapa_selecao": {dh.RESULTADO_BTTS_NO: "no", dh.RESULTADO_BTTS_YES: "yes"},
    },
]

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

    ids = test_df.reset_index(drop=True)["match_id"].tolist()
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
            })
    return linhas


def construir_predicoes_mercado(
    test_df: pd.DataFrame,
    probs: np.ndarray,
    classes: np.ndarray,
    model_name: str,
    market: str,
    mapa_selecao: dict,
) -> list[dict]:
    """Versão genérica de construir_predicoes para qualquer mercado (binário ou multiclasse)."""
    col_por_codigo: dict[int, int] = {int(c): i for i, c in enumerate(classes)}
    ids = test_df.reset_index(drop=True)["match_id"].tolist()
    linhas: list[dict] = []
    for row_i, match_id in enumerate(ids):
        for codigo, sel in mapa_selecao.items():
            col_i = col_por_codigo.get(int(codigo))
            if col_i is None:
                continue
            p = float(np.clip(probs[row_i, col_i], 1e-7, 1 - 1e-7))
            linhas.append({
                "match_id": int(match_id),
                "model_name": model_name,
                "market": market,
                "selection": sel,
                "probability": round(p, 5),
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

def _amostrar_curva(curva: list[dict], max_pontos: int = 100) -> list[dict]:
    """Reduz curva de aprendizado pra no máximo max_pontos (preserva início e fim)."""
    if not curva or len(curva) <= max_pontos:
        return curva
    passo = max(1, len(curva) // max_pontos)
    amostrada = curva[::passo]
    if curva[-1]["iteracao"] != amostrada[-1]["iteracao"]:
        amostrada = amostrada + [curva[-1]]
    return amostrada


def treinar_modelo_fold(
    nome: str,
    train_df: pd.DataFrame,
    val_df: pd.DataFrame,
    test_df: pd.DataFrame,
) -> tuple:
    """Treina `nome` em train_df; retorna (val_probs, val_classes, test_probs,
    test_classes, modelo, extra, curva) — val_probs são as OOF predictions pro stacking."""
    treinar_fn, prever_fn = ml.TREINADORES[nome]
    features = ml.FEATURES_POR_MODELO[nome]
    params = ml.PARAMS_DEFAULT[nome]

    modelo, extra, curva_bruta = treinar_fn(
        params, train_df,
        coluna_alvo=COLUNA_ALVO,
        features=features,
        val_df=val_df,
        test_df=test_df,
    )
    curva = _amostrar_curva(curva_bruta or [], 100) if curva_bruta else None

    val_probs, val_classes = prever_fn(modelo, extra, val_df, features=features)
    test_probs, test_classes = prever_fn(modelo, extra, test_df, features=features)

    return val_probs, val_classes, test_probs, test_classes, modelo, extra, curva


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

def montar_meta_X(
    probs_por_modelo: dict[str, np.ndarray],
    classes_por_modelo: dict[str, np.ndarray],
    modelos: list[str] | None = None,
) -> np.ndarray:
    """Concatena as probabilidades dos modelos base num array de meta-features.
    `modelos` controla quais modelos entram — padrão = MODELOS_BASE (todos os 4)."""
    if modelos is None:
        modelos = MODELOS_BASE
    blocos = []
    for nome in modelos:
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


def montar_meta_X_binario(probs_por_modelo: dict[str, np.ndarray], classes_por_modelo: dict[str, np.ndarray]) -> np.ndarray:
    """Versão binária de montar_meta_X — colunas [0, 1] por modelo (4 × 2 = 8 cols)."""
    blocos = []
    for nome in MODELOS_BASE:
        probs = probs_por_modelo[nome]
        classes = classes_por_modelo[nome]
        bloco = np.zeros((len(probs), 2))
        for i, c in enumerate(classes):
            if int(c) in (0, 1):
                bloco[:, int(c)] = probs[:, i]
        blocos.append(bloco)
    return np.hstack(blocos)


def calcular_metricas_binario(df: pd.DataFrame, probs: np.ndarray, classes: np.ndarray, coluna_alvo: str) -> dict:
    """Métricas para mercado binário (logloss, Brier score, acurácia)."""
    y = df[coluna_alvo].values
    ordered = np.zeros((len(probs), 2))
    for i, c in enumerate(classes):
        if int(c) in (0, 1):
            ordered[:, int(c)] = probs[:, i]
    ordered = np.clip(ordered, 1e-7, 1 - 1e-7)
    logloss = log_loss(y, ordered, labels=[0, 1])
    p1 = ordered[:, 1]
    brier = float(np.mean((p1 - y) ** 2))
    acc = float(np.mean((p1 >= 0.5).astype(int) == y))

    por_liga: dict[str, dict] = {}
    for liga, grupo in df.groupby("liga"):
        mask = grupo.index
        y_l = grupo[coluna_alvo].values
        p_l = ordered[df.index.get_indexer(mask)]
        if len(y_l) < 10:
            continue
        p1_l = p_l[:, 1]
        por_liga[str(liga)] = {
            "logloss": round(float(log_loss(y_l, p_l, labels=[0, 1])), 5),
            "brier_score": round(float(np.mean((p1_l - y_l) ** 2)), 5),
            "accuracy": round(float(np.mean((p1_l >= 0.5).astype(int) == y_l)), 4),
            "n": len(y_l),
        }

    return {
        "logloss_test": round(float(logloss), 5),
        "brier_score_test": round(brier, 5),
        "accuracy_test": round(acc, 4),
        "logloss_por_liga": por_liga,
    }


# ---------------------------------------------------------------------------
# Treinamento walk-forward para mercados binários (OU25, BTTS)
# ---------------------------------------------------------------------------

def treinar_mercado_binario(
    dataset: pd.DataFrame,
    coluna_alvo: str,
    market_str: str,
    mapa_selecao: dict,
) -> tuple[list[dict], list[dict]]:
    """Executa walk-forward CV + stacking para um mercado binário.

    Retorna (resultados_wf, predicoes) — mesma estrutura do loop 1X2 principal,
    mas com `market` preenchido no resultado e nas previsões OOF.
    """
    resultados_wf: list[dict] = []
    predicoes: list[dict] = []

    oof_y_list: list[np.ndarray] = []
    oof_probs_por_modelo: dict[str, list[np.ndarray]] = {m: [] for m in MODELOS_BASE}
    oof_classes_por_modelo: dict[str, np.ndarray | None] = {m: None for m in MODELOS_BASE}

    for fold in FOLDS:
        fold_nome = fold["nome"]
        logger.info("  [%s %s] treino ≤ %d | val %d | test %d",
                    market_str, fold_nome, fold["treino_max_ano"], fold["val_ano"], fold["test_ano"])

        train_df, val_df, test_df = split_por_ano(
            dataset, fold["treino_max_ano"], fold["val_ano"], fold["test_ano"]
        )
        if train_df.empty or val_df.empty or test_df.empty:
            logger.warning("  [%s] Fold %s com split vazio — pulando.", market_str, fold_nome)
            continue

        for nome in MODELOS_BASE:
            try:
                treinar_fn, prever_fn = ml.TREINADORES[nome]
                features = ml.FEATURES_POR_MODELO[nome]
                params = ml.PARAMS_DEFAULT[nome]
                modelo, extra, _ = treinar_fn(
                    params, train_df,
                    coluna_alvo=coluna_alvo,
                    features=features,
                    val_df=val_df,
                    test_df=test_df,
                )
                val_probs, val_classes = prever_fn(modelo, extra, val_df, features=features)
                test_probs, test_classes = prever_fn(modelo, extra, test_df, features=features)
            except Exception as exc:
                logger.error("  [%s] Erro em %s fold %s: %s", market_str, nome, fold_nome, exc)
                continue

            model_name_mercado = f"{nome}__{market_str.replace('.', '_').replace('/', '_')}"
            metricas = calcular_metricas_binario(
                test_df.reset_index(drop=True), test_probs, test_classes, coluna_alvo
            )
            logger.info("  JSON:resultado_binario:%s", json.dumps({
                "market": market_str, "modelo": nome, "fold": fold_nome,
                "logloss": metricas["logloss_test"],
                "brier_score": metricas["brier_score_test"],
                "accuracy": metricas["accuracy_test"],
            }))
            resultados_wf.append({
                "modelo": nome,
                "fold": fold_nome,
                "market": market_str,
                "train_max_ano": fold["treino_max_ano"],
                "val_ano": fold["val_ano"],
                "test_ano": fold["test_ano"],
                "n_treino": len(train_df),
                "n_val": len(val_df),
                "n_test": len(test_df),
                **metricas,
            })
            predicoes.extend(construir_predicoes_mercado(
                test_df, test_probs, test_classes, nome, market_str, mapa_selecao
            ))

            oof_probs_por_modelo[nome].append(val_probs)
            oof_classes_por_modelo[nome] = val_classes

        oof_y_list.append(val_df[coluna_alvo].values)

    # Stacking binário
    if all(len(oof_probs_por_modelo[m]) == len(FOLDS) for m in MODELOS_BASE):
        logger.info("  [%s] Treinando stacking meta-modelo binário...", market_str)
        oof_probs_concat = {m: np.vstack(oof_probs_por_modelo[m]) for m in MODELOS_BASE}
        oof_y_concat = np.concatenate(oof_y_list)
        meta_X_train = montar_meta_X_binario(oof_probs_concat, oof_classes_por_modelo)
        meta_modelo = ml.treinar_stacking_v9(meta_X_train, oof_y_concat)

        # Refit no fold_3 para avaliação de stacking
        _, _, test_df_f3 = split_por_ano(
            dataset, FOLDS[-1]["treino_max_ano"], FOLDS[-1]["val_ano"], FOLDS[-1]["test_ano"]
        )
        test_probs_por_modelo: dict[str, np.ndarray] = {}
        test_classes_por_modelo: dict[str, np.ndarray] = {}
        for nome in MODELOS_BASE:
            features = ml.FEATURES_POR_MODELO[nome]
            params = ml.PARAMS_DEFAULT[nome]
            treinar_fn, prever_fn = ml.TREINADORES[nome]
            train_f3, val_f3, _ = split_por_ano(
                dataset, FOLDS[-1]["treino_max_ano"], FOLDS[-1]["val_ano"], FOLDS[-1]["test_ano"]
            )
            try:
                modelo_f3, extra_f3, _ = treinar_fn(
                    params, train_f3, coluna_alvo=coluna_alvo,
                    features=features, val_df=val_f3, test_df=test_df_f3,
                )
                tp, tc = prever_fn(modelo_f3, extra_f3, test_df_f3, features=features)
                test_probs_por_modelo[nome] = tp
                test_classes_por_modelo[nome] = tc
            except Exception as exc:
                logger.error("  [%s] Refit fold_3 falhou para %s: %s", market_str, nome, exc)

        if len(test_probs_por_modelo) == len(MODELOS_BASE):
            meta_X_test = montar_meta_X_binario(test_probs_por_modelo, test_classes_por_modelo)
            stacking_probs, stacking_classes = ml.prever_stacking_v9(meta_modelo, meta_X_test)
            metricas_st = calcular_metricas_binario(
                test_df_f3.reset_index(drop=True), stacking_probs, stacking_classes, coluna_alvo
            )
            logger.info("  JSON:resultado_binario:%s", json.dumps({
                "market": market_str, "modelo": "stacking_v9", "fold": "fold_3",
                "logloss": metricas_st["logloss_test"],
                "brier_score": metricas_st["brier_score_test"],
                "accuracy": metricas_st["accuracy_test"],
            }))
            resultados_wf.append({
                "modelo": "stacking_v9",
                "fold": FOLDS[-1]["nome"],
                "market": market_str,
                "train_max_ano": FOLDS[-1]["treino_max_ano"],
                "val_ano": FOLDS[-1]["val_ano"],
                "test_ano": FOLDS[-1]["test_ano"],
                "n_treino": len(train_f3),
                "n_val": len(val_f3),
                "n_test": len(test_df_f3),
                **metricas_st,
            })
            predicoes.extend(construir_predicoes_mercado(
                test_df_f3, stacking_probs, stacking_classes, "stacking_v9", market_str, mapa_selecao
            ))
    else:
        logger.warning("  [%s] Stacking pulado — nem todos os folds foram completados.", market_str)

    return resultados_wf, predicoes


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
                val_probs, val_classes, test_probs, test_classes, modelo, _, curva = treinar_modelo_fold(
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
                "market": "1X2",
                "train_max_ano": fold["treino_max_ano"],
                "val_ano": fold["val_ano"],
                "test_ano": fold["test_ano"],
                "n_treino": len(train_df),
                "n_val": len(val_df),
                "n_test": len(test_df),
                **metricas,
                "learning_curve": curva,
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
                "market": "1X2",
                "train_max_ano": FOLDS[-1]["treino_max_ano"],
                "val_ano": FOLDS[-1]["val_ano"],
                "test_ano": FOLDS[-1]["test_ano"],
                "n_treino": len(train_fold3),
                "n_val": len(val_fold3),
                "n_test": len(test_df_fold3),
                **metricas_stacking,
            })
            predicoes_v9.extend(construir_predicoes(test_df_fold3, stacking_probs, stacking_classes, "stacking_v9"))

        # --- Variante sem MLP (CatBoost + XGBoost + LightGBM) ---
        if all(m in test_probs_por_modelo for m in MODELOS_SEM_MLP):
            logger.info("Treinando stacking_v9_sem_mlp (sem MLP)...")
            oof_sem_mlp = {m: oof_probs_concat[m] for m in MODELOS_SEM_MLP}
            cls_sem_mlp = {m: oof_classes_concat[m] for m in MODELOS_SEM_MLP}
            meta_X_train_sem_mlp = montar_meta_X(oof_sem_mlp, cls_sem_mlp, MODELOS_SEM_MLP)
            meta_modelo_sem_mlp = ml.treinar_stacking_v9(meta_X_train_sem_mlp, oof_y_concat)

            meta_X_test_sem_mlp = montar_meta_X(
                {m: test_probs_por_modelo[m] for m in MODELOS_SEM_MLP},
                {m: test_classes_por_modelo[m] for m in MODELOS_SEM_MLP},
                MODELOS_SEM_MLP,
            )
            probs_sem_mlp, classes_sem_mlp = ml.prever_stacking_v9(meta_modelo_sem_mlp, meta_X_test_sem_mlp)
            metricas_sem_mlp = calcular_metricas(
                test_df_fold3.reset_index(drop=True), probs_sem_mlp, classes_sem_mlp
            )
            logger.info("JSON:resultado:%s", json.dumps({
                "modelo": "stacking_v9_sem_mlp",
                "fold": "fold_3",
                "logloss": metricas_sem_mlp["logloss_test"],
                "brier_score": metricas_sem_mlp["brier_score_test"],
                "accuracy": metricas_sem_mlp["accuracy_test"],
            }))
            resultados_wf.append({
                "modelo": "stacking_v9_sem_mlp",
                "fold": FOLDS[-1]["nome"],
                "market": "1X2",
                "train_max_ano": FOLDS[-1]["treino_max_ano"],
                "val_ano": FOLDS[-1]["val_ano"],
                "test_ano": FOLDS[-1]["test_ano"],
                "n_treino": len(train_fold3),
                "n_val": len(val_fold3),
                "n_test": len(test_df_fold3),
                **metricas_sem_mlp,
            })
            predicoes_v9.extend(construir_predicoes(test_df_fold3, probs_sem_mlp, classes_sem_mlp, "stacking_v9_sem_mlp"))
    else:
        logger.warning("Stacking pulado — nem todos os folds foram completados.")

    # ------------------------------------------------------------------
    # Mercados binários adicionais (Over/Under 2.5 e BTTS)
    # ------------------------------------------------------------------
    predicoes_binarios: list[dict] = []
    for cfg in MERCADOS_BINARIOS:
        logger.info("=== Treinando mercado binário: %s ===", cfg["market"])
        res_bin, pred_bin = treinar_mercado_binario(
            dataset, cfg["coluna_alvo"], cfg["market"], cfg["mapa_selecao"]
        )
        resultados_wf.extend(res_bin)
        predicoes_binarios.extend(pred_bin)
        logger.info("  %s: %d resultados, %d previsões", cfg["market"], len(res_bin), len(pred_bin))

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
    MODELOS_V9 = ["catboost_v9", "xgboost_v9", "lightgbm_v9", "mlp_v9", "stacking_v9", "stacking_v9_sem_mlp"]
    logger.info("Limpando previsões v9 anteriores de model_predictions...")
    for _mv9 in MODELOS_V9:
        supabase_client.table("model_predictions").delete().eq("model_name", _mv9).eq("market", "1x2").execute()
    if predicoes_v9:
        for inicio in range(0, len(predicoes_v9), TAMANHO_LOTE_UPSERT):
            supabase_client.table("model_predictions").insert(
                predicoes_v9[inicio: inicio + TAMANHO_LOTE_UPSERT]
            ).execute()
        logger.info("  model_predictions (v9 OOF 1X2): %d linhas", len(predicoes_v9))

    # Previsões dos mercados binários (OU25 e BTTS)
    for cfg in MERCADOS_BINARIOS:
        mkt = cfg["market"]
        logger.info("Limpando previsões v9 %s de model_predictions...", mkt)
        for _mv9 in MODELOS_V9:
            supabase_client.table("model_predictions").delete().eq("model_name", _mv9).eq("market", mkt).execute()
    pred_bin_total = predicoes_binarios
    if pred_bin_total:
        for inicio in range(0, len(pred_bin_total), TAMANHO_LOTE_UPSERT):
            supabase_client.table("model_predictions").insert(
                pred_bin_total[inicio: inicio + TAMANHO_LOTE_UPSERT]
            ).execute()
        logger.info("  model_predictions (v9 OOF binários): %d linhas", len(pred_bin_total))

    logger.info("=== Walk-forward CV v9 concluído ===")


if __name__ == "__main__":
    main()
