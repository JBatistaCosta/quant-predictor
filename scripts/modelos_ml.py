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
from catboost import CatBoostClassifier, CatBoostRegressor
from lightgbm import LGBMClassifier
from xgboost import XGBClassifier

from dados_historicos import (
    CAT_FEATURES,
    FEATURES,
    FEATURES_V2,
    FEATURES_V3,
    FEATURES_V3B,
    FEATURES_V4,
    FEATURES_V5,
    RESULTADO_AWAY,
    RESULTADO_CORNERS_OVER95,
    RESULTADO_CORNERS_UNDER95,
    RESULTADO_DRAW,
    RESULTADO_FAIXA_0_1,
    RESULTADO_FAIXA_2_3,
    RESULTADO_FAIXA_4_6,
    RESULTADO_FAIXA_7MAIS,
    RESULTADO_HOME,
    RESULTADO_OVER25,
    RESULTADO_UNDER25,
)

# Rótulo de saída por código de classe -- usado por `empacotar_predicoes`
# pra virar {match_id: {prob_*}} sem hardcoded 1X2. `resultado` (3 classes),
# `resultado_over25` (2 classes, Over/Under 2.5 gols), `resultado_corners_
# ou95` (2 classes, Over/Under 9.5 escanteios) e `resultado_faixa_gols` (4
# classes, faixa de gols totais) usam o MESMO treino/predição por baixo
# (`TREINADORES`), só muda a coluna-alvo e esse mapeamento de rótulo.
ROTULOS_SAIDA = {
    "resultado": {RESULTADO_HOME: "prob_home", RESULTADO_DRAW: "prob_draw", RESULTADO_AWAY: "prob_away"},
    "resultado_over25": {RESULTADO_UNDER25: "prob_under", RESULTADO_OVER25: "prob_over"},
    "resultado_corners_ou95": {RESULTADO_CORNERS_UNDER95: "prob_corners_under", RESULTADO_CORNERS_OVER95: "prob_corners_over"},
    "resultado_faixa_gols": {
        RESULTADO_FAIXA_0_1: "prob_faixa_0_1",
        RESULTADO_FAIXA_2_3: "prob_faixa_2_3",
        RESULTADO_FAIXA_4_6: "prob_faixa_4_6",
        RESULTADO_FAIXA_7MAIS: "prob_faixa_7mais",
    },
}

# Hiperparâmetros default -- usados pelo treino diário em produção, sem
# tuning (`backtest_kelly.py` faz grid search em cima dessas mesmas funções
# de treino pra escolher profundidade/learning_rate melhores no Validation
# Set). `n_estimators` do LightGBM fica fixo em 80 (não entra na grade de
# tuning) -- é a config "leve e rápida" pedida originalmente pro modelo.
#
# v2 (parâmetros de jogador, ver dados_historicos.FEATURES_V2), v3
# (+ fadiga, ver dados_historicos.FEATURES_V3), v4 (+ disciplina/risco de
# suspensão por cartão, ver dados_historicos.FEATURES_V4), v5 (+
# classificação/H2H/árbitro, ver dados_historicos.FEATURES_V5) e v3B (+
# força do XI titular/valor de mercado, ver dados_historicos.FEATURES_V3B)
# reaproveitam os mesmos defaults da v1 como ponto de partida -- ainda não
# passaram por tuning dedicado, `backtest_kelly.py` faz grid search igual
# pras seis.
PARAMS_DEFAULT = {
    "catboost_v1": {"depth": 6, "learning_rate": 0.05},
    "xgboost_v1": {"max_depth": 4, "learning_rate": 0.08},
    "lightgbm_v1": {"num_leaves": 15, "learning_rate": 0.1},
    "catboost_v2": {"depth": 6, "learning_rate": 0.05},
    "xgboost_v2": {"max_depth": 4, "learning_rate": 0.08},
    "lightgbm_v2": {"num_leaves": 15, "learning_rate": 0.1},
    "catboost_v3": {"depth": 6, "learning_rate": 0.05},
    "xgboost_v3": {"max_depth": 4, "learning_rate": 0.08},
    "lightgbm_v3": {"num_leaves": 15, "learning_rate": 0.1},
    "catboost_v4": {"depth": 6, "learning_rate": 0.05},
    "xgboost_v4": {"max_depth": 4, "learning_rate": 0.08},
    "lightgbm_v4": {"num_leaves": 15, "learning_rate": 0.1},
    "catboost_v5": {"depth": 6, "learning_rate": 0.05},
    "xgboost_v5": {"max_depth": 4, "learning_rate": 0.08},
    "lightgbm_v5": {"num_leaves": 15, "learning_rate": 0.1},
    "catboost_v3b": {"depth": 6, "learning_rate": 0.05},
    "xgboost_v3b": {"max_depth": 4, "learning_rate": 0.08},
    "lightgbm_v3b": {"num_leaves": 15, "learning_rate": 0.1},
    # Regressor de xG (não é classificação -- ver treinar_catboost_regressor).
    # Mesmos defaults da v1, ainda sem tuning dedicado.
    "catboost_xg_regressor_v1": {"depth": 6, "learning_rate": 0.05},
    "catboost_xgot_regressor_v1": {"depth": 6, "learning_rate": 0.05},
}

# Lista de features por modelo -- v1 usa `FEATURES` (elo/forma/xG de time),
# v2 usa `FEATURES_V2` (+ força do elenco), v3 usa `FEATURES_V3` (+
# descanso pré-jogo/fadiga), v4 usa `FEATURES_V4` (+ risco de suspensão por
# cartão), v5 usa `FEATURES_V5` (+ classificação/H2H/árbitro), v3B usa
# `FEATURES_V3B` (v5 + força do XI titular confirmado/valor de mercado na
# data do jogo -- nome "v3B" mantido do PR #114, mas o conjunto de
# features é v5 + XI titular, não v2 + XI titular).
# dixon_coles_v1 não entra aqui (não é um modelo baseado em
# `TREINADORES`/lista de features -- é Poisson puro).
FEATURES_POR_MODELO = {
    "catboost_v1": FEATURES,
    "xgboost_v1": FEATURES,
    "lightgbm_v1": FEATURES,
    "catboost_v2": FEATURES_V2,
    "xgboost_v2": FEATURES_V2,
    "lightgbm_v2": FEATURES_V2,
    "catboost_v3": FEATURES_V3,
    "xgboost_v3": FEATURES_V3,
    "lightgbm_v3": FEATURES_V3,
    "catboost_v4": FEATURES_V4,
    "xgboost_v4": FEATURES_V4,
    "lightgbm_v4": FEATURES_V4,
    "catboost_v5": FEATURES_V5,
    "xgboost_v5": FEATURES_V5,
    "lightgbm_v5": FEATURES_V5,
    "catboost_v3b": FEATURES_V3B,
    "xgboost_v3b": FEATURES_V3B,
    "lightgbm_v3b": FEATURES_V3B,
    # Regressor de xG/xGOT -- mesmas features base da v1 (elo/forma/xG/liga),
    # prevendo um valor contínuo (gols esperados / xGOT) em vez de classe.
    "catboost_xg_regressor_v1": FEATURES,
    "catboost_xgot_regressor_v1": FEATURES,
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


def empacotar_predicoes(match_ids, probs: np.ndarray, classes, coluna_alvo: str = "resultado") -> dict[int, dict[str, float]]:
    """Mapeia a matriz (N, n_classes) de predict_proba pra
    {match_id: {prob_*}}, respeitando a ordem real de `classes_` do modelo
    (nem sempre é [0,1,...]) -- `coluna_alvo` escolhe o mapeamento código->
    nome de saída certo em `ROTULOS_SAIDA` (1X2 ou Over/Under 2.5)."""
    rotulos = ROTULOS_SAIDA[coluna_alvo]
    indice_da_classe = {int(rotulo): i for i, rotulo in enumerate(np.ravel(classes))}
    resultado = {}
    for match_id, linha_probs in zip(match_ids, probs):
        resultado[match_id] = {nome: float(linha_probs[indice_da_classe[codigo]]) for codigo, nome in rotulos.items()}
    return resultado


def _montar_curva(treino: list[float], validacao: list[float] | None, teste: list[float] | None) -> list[dict]:
    """Formato comum da curva de aprendizado (loss por iteração do boosting)
    entre os 3 frameworks -- cada um expõe isso com nomes/estrutura
    diferente (ver `treinar_catboost`/`treinar_xgboost`/`treinar_lightgbm`),
    normalizado aqui pra {iteracao, treino, validacao, teste} plano, fácil
    de servir direto pro front-end sem lógica por framework lá."""
    n = len(treino)
    return [
        {
            "iteracao": i,
            "treino": treino[i],
            "validacao": validacao[i] if validacao else None,
            "teste": teste[i] if teste else None,
        }
        for i in range(n)
    ]


def treinar_catboost(
    params: dict,
    train_df: pd.DataFrame,
    coluna_alvo: str = "resultado",
    features: list[str] = FEATURES,
    val_df: pd.DataFrame | None = None,
    test_df: pd.DataFrame | None = None,
):
    """`val_df`/`test_df` são OPCIONAIS -- só passados por
    `backtest_kelly.tunar_treinar_e_calibrar` (pra capturar a curva de
    aprendizado, comparando loss por iteração nos 3 splits). O treino
    diário em produção (`rodar_predicoes.py`) não passa esses dois
    parâmetros, então `curva` sempre volta `None` nesse caminho -- sem
    custo extra de CPU no cron diário."""
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
    eval_set = None
    if val_df is not None and test_df is not None:
        val_prep, test_prep = preparar_liga_para_catboost(val_df), preparar_liga_para_catboost(test_df)
        eval_set = [(val_prep[features], val_prep[coluna_alvo]), (test_prep[features], test_prep[coluna_alvo])]
    modelo.fit(treino[features], treino[coluna_alvo], eval_set=eval_set)

    curva = None
    if eval_set is not None:
        resultados = modelo.get_evals_result()
        curva = _montar_curva(
            resultados["learn"]["MultiClass"], resultados["validation_0"]["MultiClass"], resultados["validation_1"]["MultiClass"]
        )
    return modelo, None, curva


def prever_catboost(modelo, _extra, df: pd.DataFrame, features: list[str] = FEATURES):
    df = preparar_liga_para_catboost(df)
    return modelo.predict_proba(df[features]), modelo.classes_


def treinar_catboost_regressor(
    params: dict,
    train_df: pd.DataFrame,
    coluna_alvo: str,
    features: list[str] = FEATURES,
):
    """Mesmo espírito de `treinar_catboost`, mas regressão (RMSE) em vez de
    classificação -- usado pra gols esperados (xG) por equipe, onde o alvo é
    contínuo (`match_stats.xg`/`match_stats_fotmob.xg`), não uma classe.
    Sem `val_df`/`test_df`/curva de aprendizado -- primeira versão, mesmo
    nível de simplicidade do `treinar_catboost` original (sem tuning)."""
    modelo = CatBoostRegressor(
        loss_function="RMSE",
        thread_count=2,
        iterations=200,
        cat_features=CAT_FEATURES,
        random_seed=42,
        verbose=False,
        **params,
    )
    treino = preparar_liga_para_catboost(train_df)
    modelo.fit(treino[features], treino[coluna_alvo])
    return modelo, None, None


def prever_catboost_regressor(modelo, _extra, df: pd.DataFrame, features: list[str] = FEATURES):
    df = preparar_liga_para_catboost(df)
    return modelo.predict(df[features])


def treinar_xgboost(
    params: dict,
    train_df: pd.DataFrame,
    coluna_alvo: str = "resultado",
    features: list[str] = FEATURES,
    val_df: pd.DataFrame | None = None,
    test_df: pd.DataFrame | None = None,
):
    """Ver docstring de `treinar_catboost` sobre `val_df`/`test_df`."""
    treino_encoded = pd.get_dummies(train_df[features], columns=CAT_FEATURES)
    modelo = XGBClassifier(
        objective="multi:softprob",
        num_class=train_df[coluna_alvo].nunique(),
        n_estimators=200,
        eval_metric="mlogloss",
        random_state=42,
        **params,
    )
    eval_set = None
    if val_df is not None and test_df is not None:
        # reencoda val/test com as MESMAS colunas do treino (reindex,
        # fill_value=0) -- mesma técnica de `prever_xgboost`, senão uma liga
        # ausente no lote de val/test (ou presente só lá) quebra o fit.
        val_encoded = pd.get_dummies(val_df[features], columns=CAT_FEATURES).reindex(columns=treino_encoded.columns, fill_value=0)
        test_encoded = pd.get_dummies(test_df[features], columns=CAT_FEATURES).reindex(columns=treino_encoded.columns, fill_value=0)
        eval_set = [(treino_encoded, train_df[coluna_alvo]), (val_encoded, val_df[coluna_alvo]), (test_encoded, test_df[coluna_alvo])]
    modelo.fit(treino_encoded, train_df[coluna_alvo], eval_set=eval_set, verbose=False)

    curva = None
    if eval_set is not None:
        resultados = modelo.evals_result()
        curva = _montar_curva(
            resultados["validation_0"]["mlogloss"], resultados["validation_1"]["mlogloss"], resultados["validation_2"]["mlogloss"]
        )
    return modelo, treino_encoded.columns, curva


def prever_xgboost(modelo, colunas_treino, df: pd.DataFrame, features: list[str] = FEATURES):
    # garante as mesmas colunas (mesma ordem) vistas no treino -- uma liga
    # do treino ausente na predição, ou uma liga na predição fora do
    # dataset "Feature Stacked", vira coluna de zeros em vez de quebrar
    encoded = pd.get_dummies(df[features], columns=CAT_FEATURES).reindex(columns=colunas_treino, fill_value=0)
    return modelo.predict_proba(encoded), modelo.classes_


def treinar_lightgbm(
    params: dict,
    train_df: pd.DataFrame,
    coluna_alvo: str = "resultado",
    features: list[str] = FEATURES,
    val_df: pd.DataFrame | None = None,
    test_df: pd.DataFrame | None = None,
):
    """Configuração deliberadamente leve/rápida (poucas árvores, folhas
    rasas por padrão) -- é o modelo mais barato dos 3 em custo de CPU no
    runner do GitHub Actions. Ver docstring de `treinar_catboost` sobre
    `val_df`/`test_df`."""
    treino = train_df.copy()
    categorias_liga = sorted(treino["liga"].dropna().unique())
    treino["liga"] = pd.Categorical(treino["liga"], categories=categorias_liga)
    modelo = LGBMClassifier(
        objective="multiclass",
        num_class=treino[coluna_alvo].nunique(),
        n_estimators=80,
        min_child_samples=10,
        random_state=42,
        verbosity=-1,
        **params,
    )
    eval_set, eval_names = None, None
    if val_df is not None and test_df is not None:
        val_prep, test_prep = val_df.copy(), test_df.copy()
        val_prep["liga"] = alinhar_categoria_liga(val_prep["liga"], categorias_liga)
        test_prep["liga"] = alinhar_categoria_liga(test_prep["liga"], categorias_liga)
        eval_set = [
            (treino[features], treino[coluna_alvo]),
            (val_prep[features], val_df[coluna_alvo]),
            (test_prep[features], test_df[coluna_alvo]),
        ]
        eval_names = ["treino", "validacao", "teste"]
    modelo.fit(
        treino[features], treino[coluna_alvo], categorical_feature=CAT_FEATURES, eval_set=eval_set, eval_names=eval_names
    )

    curva = None
    if eval_set is not None:
        resultados = modelo.evals_result_
        curva = _montar_curva(
            resultados["treino"]["multi_logloss"], resultados["validacao"]["multi_logloss"], resultados["teste"]["multi_logloss"]
        )
    return modelo, categorias_liga, curva


def prever_lightgbm(modelo, categorias_liga, df: pd.DataFrame, features: list[str] = FEATURES):
    df = df.copy()
    df["liga"] = alinhar_categoria_liga(df["liga"], categorias_liga)
    return modelo.predict_proba(df[features]), modelo.classes_


# treinar(params, train_df, coluna_alvo=..., features=...) -> (modelo, extra)
# | prever(modelo, extra, df, features=...) -> (probs, classes)
# v2/v3/v4/v5/v3B reaproveitam as MESMAS funções de treino/predição da v1 (só a
# lista de features muda, ver `FEATURES_POR_MODELO` -- passada
# explicitamente pelo chamador em cada call, não fica implícita no dict).
TREINADORES = {
    "catboost_v1": (treinar_catboost, prever_catboost),
    "xgboost_v1": (treinar_xgboost, prever_xgboost),
    "lightgbm_v1": (treinar_lightgbm, prever_lightgbm),
    "catboost_v2": (treinar_catboost, prever_catboost),
    "xgboost_v2": (treinar_xgboost, prever_xgboost),
    "lightgbm_v2": (treinar_lightgbm, prever_lightgbm),
    "catboost_v3": (treinar_catboost, prever_catboost),
    "xgboost_v3": (treinar_xgboost, prever_xgboost),
    "lightgbm_v3": (treinar_lightgbm, prever_lightgbm),
    "catboost_v4": (treinar_catboost, prever_catboost),
    "xgboost_v4": (treinar_xgboost, prever_xgboost),
    "lightgbm_v4": (treinar_lightgbm, prever_lightgbm),
    "catboost_v5": (treinar_catboost, prever_catboost),
    "xgboost_v5": (treinar_xgboost, prever_xgboost),
    "lightgbm_v5": (treinar_lightgbm, prever_lightgbm),
    "catboost_v3b": (treinar_catboost, prever_catboost),
    "xgboost_v3b": (treinar_xgboost, prever_xgboost),
    "lightgbm_v3b": (treinar_lightgbm, prever_lightgbm),
}
