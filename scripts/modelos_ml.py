#!/usr/bin/env python3
"""Treino/predição compartilhados dos 3 modelos de árvore (CatBoost/
XGBoost/LightGBM) do Model Benchmarking.

Usado tanto por `rodar_predicoes.py` (treino diário, produção) quanto por
`backtest_kelly.py` (grid search + validação out-of-sample) -- extraído
pra um módulo só depois de notar que os dois scripts duplicavam a mesma
lógica de treino/predição, com risco real de divergir com o tempo.

Cada `treinar_*(params, train_df)` devolve `(modelo, extra)` -- `extra` é
um estado auxiliar que a predição precisa (colunas do one-hot do XGBoost,
categorias vistas pelo LightGBM) e é `None` quando não é necessário
(CatBoost). Cada `prever_*(modelo, extra, df)` devolve `(probs, classes)`,
no mesmo formato de `sklearn.predict_proba`.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier
from lightgbm import LGBMClassifier
from xgboost import XGBClassifier

from dados_historicos import CAT_FEATURES, FEATURES, RESULTADO_AWAY, RESULTADO_DRAW, RESULTADO_HOME

# Hiperparâmetros default -- usados pelo treino diário em produção, sem
# tuning (`backtest_kelly.py` faz grid search em cima dessas mesmas funções
# de treino pra escolher profundidade/learning_rate melhores no Validation
# Set). `n_estimators` do LightGBM fica fixo em 80 (não entra na grade de
# tuning) -- é a config "leve e rápida" pedida originalmente pro modelo.
PARAMS_DEFAULT = {
    "catboost_v1": {"depth": 6, "learning_rate": 0.05},
    "xgboost_v1": {"max_depth": 4, "learning_rate": 0.08},
    "lightgbm_v1": {"num_leaves": 15, "learning_rate": 0.1},
}


def preparar_liga_para_catboost(df: pd.DataFrame) -> pd.DataFrame:
    """CatBoost aceita string ou int em feature categórica, mas não NaN cru
    (erro `bad object for id: nan`) -- fixtures de liga fora do dataset
    "Feature Stacked" (`liga=None`) precisam virar string sentinela antes
    de entrar no Pool."""
    df = df.copy()
    df["liga"] = df["liga"].fillna("desconhecida").astype(str)
    return df


def alinhar_categoria_liga(serie: pd.Series, categorias_treino) -> pd.Categorical:
    """Garante que a coluna `liga` da predição use exatamente as categorias
    vistas no treino -- um valor não visto (liga fora do dataset "Feature
    Stacked", ou None) vira categoria ausente em vez de quebrar o modelo ou
    gerar um código de categoria fora do intervalo treinado (LightGBM)."""
    return pd.Categorical(serie, categories=categorias_treino)


def empacotar_predicoes(match_ids, probs: np.ndarray, classes) -> dict[int, dict[str, float]]:
    """Mapeia a matriz (N, 3) de predict_proba pra {match_id: {prob_*}},
    respeitando a ordem real de `classes_` do modelo (nem sempre é [0,1,2])."""
    indice_da_classe = {int(rotulo): i for i, rotulo in enumerate(np.ravel(classes))}
    resultado = {}
    for match_id, linha_probs in zip(match_ids, probs):
        resultado[match_id] = {
            "prob_home": float(linha_probs[indice_da_classe[RESULTADO_HOME]]),
            "prob_draw": float(linha_probs[indice_da_classe[RESULTADO_DRAW]]),
            "prob_away": float(linha_probs[indice_da_classe[RESULTADO_AWAY]]),
        }
    return resultado


def treinar_catboost(params: dict, train_df: pd.DataFrame):
    modelo = CatBoostClassifier(
        loss_function="MultiClass",
        thread_count=2,
        iterations=200,
        cat_features=CAT_FEATURES,
        random_seed=42,
        verbose=False,
        **params,
    )
    treino = preparar_liga_para_catboost(train_df)
    modelo.fit(treino[FEATURES], treino["resultado"])
    return modelo, None


def prever_catboost(modelo, _extra, df: pd.DataFrame):
    df = preparar_liga_para_catboost(df)
    return modelo.predict_proba(df[FEATURES]), modelo.classes_


def treinar_xgboost(params: dict, train_df: pd.DataFrame):
    treino_encoded = pd.get_dummies(train_df[FEATURES], columns=CAT_FEATURES)
    modelo = XGBClassifier(
        objective="multi:softprob",
        num_class=3,
        n_estimators=200,
        eval_metric="mlogloss",
        random_state=42,
        **params,
    )
    modelo.fit(treino_encoded, train_df["resultado"])
    return modelo, treino_encoded.columns


def prever_xgboost(modelo, colunas_treino, df: pd.DataFrame):
    # garante as mesmas colunas (mesma ordem) vistas no treino -- uma liga
    # do treino ausente na predição, ou uma liga na predição fora do
    # dataset "Feature Stacked", vira coluna de zeros em vez de quebrar
    encoded = pd.get_dummies(df[FEATURES], columns=CAT_FEATURES).reindex(columns=colunas_treino, fill_value=0)
    return modelo.predict_proba(encoded), modelo.classes_


def treinar_lightgbm(params: dict, train_df: pd.DataFrame):
    """Configuração deliberadamente leve/rápida (poucas árvores, folhas
    rasas por padrão) -- é o modelo mais barato dos 3 em custo de CPU no
    runner do GitHub Actions."""
    treino = train_df.copy()
    categorias_liga = sorted(treino["liga"].dropna().unique())
    treino["liga"] = pd.Categorical(treino["liga"], categories=categorias_liga)
    modelo = LGBMClassifier(
        objective="multiclass",
        num_class=3,
        n_estimators=80,
        min_child_samples=10,
        random_state=42,
        verbosity=-1,
        **params,
    )
    modelo.fit(treino[FEATURES], treino["resultado"], categorical_feature=CAT_FEATURES)
    return modelo, categorias_liga


def prever_lightgbm(modelo, categorias_liga, df: pd.DataFrame):
    df = df.copy()
    df["liga"] = alinhar_categoria_liga(df["liga"], categorias_liga)
    return modelo.predict_proba(df[FEATURES]), modelo.classes_


# treinar(params, train_df) -> (modelo, extra) | prever(modelo, extra, df) -> (probs, classes)
TREINADORES = {
    "catboost_v1": (treinar_catboost, prever_catboost),
    "xgboost_v1": (treinar_xgboost, prever_xgboost),
    "lightgbm_v1": (treinar_lightgbm, prever_lightgbm),
}
