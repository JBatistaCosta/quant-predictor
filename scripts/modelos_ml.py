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
from lightgbm import LGBMClassifier, early_stopping
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

from dados_historicos import (
    CAT_FEATURES,
    FEATURES,
    FEATURES_V2,
    FEATURES_V3,
    FEATURES_V3B,
    FEATURES_V4,
    FEATURES_V5,
    FEATURES_V6,
    FEATURES_V7,
    FEATURES_V8,
    FEATURES_V9,
    FEATURES_V10,
    RESULTADO_AWAY,
    RESULTADO_CORNERS_OVER95,
    RESULTADO_CORNERS_UNDER95,
    RESULTADO_DRAW,
    RESULTADO_FAIXA_0_1,
    RESULTADO_FAIXA_2_3,
    RESULTADO_FAIXA_4_6,
    RESULTADO_FAIXA_7MAIS,
    RESULTADO_HOME,
    RESULTADO_BTTS_NO,
    RESULTADO_BTTS_YES,
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
    "resultado_btts": {RESULTADO_BTTS_NO: "prob_btts_no", RESULTADO_BTTS_YES: "prob_btts_yes"},
    "resultado_corners_ou95": {RESULTADO_CORNERS_UNDER95: "prob_corners_under", RESULTADO_CORNERS_OVER95: "prob_corners_over"},
    "resultado_faixa_gols": {
        RESULTADO_FAIXA_0_1: "prob_faixa_0_1",
        RESULTADO_FAIXA_2_3: "prob_faixa_2_3",
        RESULTADO_FAIXA_4_6: "prob_faixa_4_6",
        RESULTADO_FAIXA_7MAIS: "prob_faixa_7mais",
    },
}

# Profundidade de aprendizado (nº de iterações/árvores de boosting) quando
# há validação disponível (`val_df` passado por `backtest_kelly.
# tunar_treinar_e_calibrar`): treina até um teto alto e para de verdade
# (`early_stopping_rounds`) assim que o log-loss de VALIDAÇÃO parar de
# melhorar por `RODADAS_EARLY_STOPPING` iterações seguidas -- exatamente
# "menor log-loss encontrado, evitando subida por overfitting" (pedido do
# usuário). Sem validação (treino diário de produção, `rodar_predicoes.py`,
# que não separa holdout -- ver decisão do usuário de manter 100% do
# histórico em produção), não tem como saber quando parar, então usa um
# teto fixo conservador (mesmo valor de sempre, sem mudança de
# comportamento em produção).
ITERACOES_MAXIMAS_EARLY_STOPPING = 2000
RODADAS_EARLY_STOPPING = 50
ITERACOES_PRODUCAO = {"catboost": 200, "xgboost": 200, "lightgbm": 80}

# Regularização/subsampling FIXOS (não entram na grade de tuning de
# `backtest_kelly.GRADE_HIPERPARAMETROS` -- variar isso junto com
# depth/learning_rate multiplicaria o tamanho da grade sem necessidade)
# -- pedido do usuário depois de ver a curva de aprendizado (Treino/
# Validação/Teste) mostrando que o modelo "decora" os dados antes da
# parada antecipada conseguir agir: cada árvore treina só numa amostra
# aleatória de 80% das linhas e 80% das colunas (equivalente a
# `subsample`/`colsample_bytree` do XGBoost), reduzindo a chance de uma
# árvore virar regra específica de poucas partidas. Junto com o aumento
# de `min_child_samples`/`min_child_weight`/`l2_leaf_reg` abaixo (exige
# mais partidas sustentando uma folha antes de criar a regra).
FRACAO_SUBSAMPLE = 0.8
FRACAO_COLSAMPLE = 0.8

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
    # v6 (progresso da temporada, ver dados_historicos.FEATURES_V6) -- mesmos
    # defaults da v1, ainda sem tuning dedicado.
    "catboost_v6": {"depth": 6, "learning_rate": 0.05},
    "xgboost_v6": {"max_depth": 4, "learning_rate": 0.08},
    "lightgbm_v6": {"num_leaves": 15, "learning_rate": 0.1},
    # v7 (estatísticas de jogo do FBref, ver dados_historicos.FEATURES_V7) --
    # mesmos defaults da v1, ainda sem tuning dedicado.
    "catboost_v7": {"depth": 6, "learning_rate": 0.05},
    "xgboost_v7": {"max_depth": 4, "learning_rate": 0.08},
    "lightgbm_v7": {"num_leaves": 15, "learning_rate": 0.1},
    # v8 (estatísticas do FotMob, ver dados_historicos.FEATURES_V8) -- mesmos
    # defaults da v1, ainda sem tuning dedicado.
    "catboost_v8": {"depth": 6, "learning_rate": 0.05},
    "xgboost_v8": {"max_depth": 4, "learning_rate": 0.08},
    "lightgbm_v8": {"num_leaves": 15, "learning_rate": 0.1},
    # v9 — mesmas features da v8; MLP tunado após primeira rodada mostrar log-loss ~1.07
    # (próximo ao baseline aleatório 1.099). Arquitetura maior + mais paciência no early stopping.
    "catboost_v9": {"depth": 6, "learning_rate": 0.05},
    "xgboost_v9": {"max_depth": 4, "learning_rate": 0.08},
    "lightgbm_v9": {"num_leaves": 15, "learning_rate": 0.1},
    "mlp_v9": {"hidden_layer_sizes": (256, 128, 64), "max_iter": 1000, "learning_rate_init": 0.0005, "n_iter_no_change": 50},
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
    # v6 = v5 (classificação/H2H/árbitro) + progresso_temporada (0-1, posição
    # da partida no calendário da temporada) -- linhagem separada de v3B
    # (v5 + XI titular), não empilha as duas.
    "catboost_v6": FEATURES_V6,
    "xgboost_v6": FEATURES_V6,
    "lightgbm_v6": FEATURES_V6,
    # v7 = v6 + forma pré-jogo das estatísticas do FBref (posse, chutes,
    # chutes no alvo, escanteios, faltas, cartões) -- linhagem separada de
    # v3B pela mesma razão de v6.
    "catboost_v7": FEATURES_V7,
    "xgboost_v7": FEATURES_V7,
    "lightgbm_v7": FEATURES_V7,
    # v8 = v7 + forma pré-jogo das estatísticas do FotMob (~22 colunas com
    # boa cobertura -- xG detalhado, finalização, construção de jogo,
    # defesa, duelos). Linhagem separada de v3B pela mesma razão de v6/v7.
    "catboost_v8": FEATURES_V8,
    "xgboost_v8": FEATURES_V8,
    "lightgbm_v8": FEATURES_V8,
    # v9 — mesmas features da v8; MLP adicionado como 4ª família.
    "catboost_v9": FEATURES_V9,
    "xgboost_v9": FEATURES_V9,
    "lightgbm_v9": FEATURES_V9,
    "mlp_v9": FEATURES_V9,
    # v10 — v9 + idade/altura do XI titular (na data da partida) + venue_capacity_home.
    # Cobertura depende do backfill de birth_date/height (ingestao_perfil_jogador_local)
    # e de stadium_capacity (ingestao_equipes_local). Features ficam NaN onde sem dado,
    # mesma tolerância de titular_rating/valor_mercado (v3B).
    "catboost_v10": FEATURES_V10,
    "xgboost_v10": FEATURES_V10,
    "lightgbm_v10": FEATURES_V10,
    "mlp_v10": FEATURES_V10,
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
    `backtest_kelly.tunar_treinar_e_calibrar`. Quando passados, treina até
    `ITERACOES_MAXIMAS_EARLY_STOPPING` com parada real
    (`early_stopping_rounds`) no log-loss de VALIDAÇÃO -- CatBoost usa o
    PRIMEIRO pool de `eval_set` pro detector de overfitting por padrão, por
    isso `val_prep` vem antes de `test_prep` na lista (nunca o contrário,
    senão a parada vazaria informação do Test). `use_best_model=True`
    garante que a predição final usa a iteração de menor log-loss de
    validação, não a última treinada. O treino diário em produção
    (`rodar_predicoes.py`) não passa `val_df`/`test_df` -- sem holdout não
    tem como saber quando parar, então usa o teto fixo de sempre
    (`ITERACOES_PRODUCAO`), sem mudança de comportamento lá."""
    usa_early_stopping = val_df is not None and test_df is not None
    modelo = CatBoostClassifier(
        loss_function="MultiClass",
        thread_count=2,
        iterations=ITERACOES_MAXIMAS_EARLY_STOPPING if usa_early_stopping else ITERACOES_PRODUCAO["catboost"],
        cat_features=CAT_FEATURES,
        random_seed=42,
        verbose=False,
        # Regularização/subsampling fixos (ver FRACAO_SUBSAMPLE/FRACAO_
        # COLSAMPLE) -- `subsample` só funciona com bootstrap Bernoulli/MVS
        # (o default "Bayesian" do CatBoost não aceita esse parâmetro);
        # `rsm` é o equivalente do CatBoost pra colsample_bytree (Random
        # Subspace Method, fração de FEATURES por árvore); `l2_leaf_reg`
        # sobe do default (3) pra 5, mais conservador.
        bootstrap_type="Bernoulli",
        subsample=FRACAO_SUBSAMPLE,
        rsm=FRACAO_COLSAMPLE,
        l2_leaf_reg=5,
        **params,
    )
    treino = preparar_liga_para_catboost(train_df)
    eval_set = None
    if usa_early_stopping:
        val_prep, test_prep = preparar_liga_para_catboost(val_df), preparar_liga_para_catboost(test_df)
        eval_set = [(val_prep[features], val_prep[coluna_alvo]), (test_prep[features], test_prep[coluna_alvo])]
    modelo.fit(
        treino[features],
        treino[coluna_alvo],
        eval_set=eval_set,
        early_stopping_rounds=RODADAS_EARLY_STOPPING if usa_early_stopping else None,
        use_best_model=usa_early_stopping,
    )

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
    """Ver docstring de `treinar_catboost` sobre `val_df`/`test_df` -- MAS
    XGBoost decide a parada olhando o ÚLTIMO item de `eval_set` (o oposto
    do CatBoost, que olha o primeiro), então aqui a ordem é
    (treino, teste, validação) -- validação por último de propósito, senão
    a parada usaria o Test Set pra decidir quando parar (vazamento)."""
    usa_early_stopping = val_df is not None and test_df is not None
    treino_encoded = pd.get_dummies(train_df[features], columns=CAT_FEATURES)
    modelo = XGBClassifier(
        objective="multi:softprob",
        num_class=train_df[coluna_alvo].nunique(),
        n_estimators=ITERACOES_MAXIMAS_EARLY_STOPPING if usa_early_stopping else ITERACOES_PRODUCAO["xgboost"],
        eval_metric="mlogloss",
        random_state=42,
        early_stopping_rounds=RODADAS_EARLY_STOPPING if usa_early_stopping else None,
        # Regularização/subsampling fixos (ver FRACAO_SUBSAMPLE/FRACAO_
        # COLSAMPLE no topo do arquivo): cada árvore só vê 80% das linhas
        # e 80% das colunas, e `min_child_weight=3` exige mais partidas
        # sustentando uma folha antes de criar a regra (default é 1).
        subsample=FRACAO_SUBSAMPLE,
        colsample_bytree=FRACAO_COLSAMPLE,
        min_child_weight=3,
        **params,
    )
    eval_set = None
    if usa_early_stopping:
        # reencoda val/test com as MESMAS colunas do treino (reindex,
        # fill_value=0) -- mesma técnica de `prever_xgboost`, senão uma liga
        # ausente no lote de val/test (ou presente só lá) quebra o fit.
        val_encoded = pd.get_dummies(val_df[features], columns=CAT_FEATURES).reindex(columns=treino_encoded.columns, fill_value=0)
        test_encoded = pd.get_dummies(test_df[features], columns=CAT_FEATURES).reindex(columns=treino_encoded.columns, fill_value=0)
        eval_set = [(treino_encoded, train_df[coluna_alvo]), (test_encoded, test_df[coluna_alvo]), (val_encoded, val_df[coluna_alvo])]
    modelo.fit(treino_encoded, train_df[coluna_alvo], eval_set=eval_set, verbose=False)

    curva = None
    if eval_set is not None:
        # Índices batem com a ordem de eval_set acima: validation_0=treino,
        # validation_1=teste, validation_2=validação (não confundir com o
        # nome "validation_N" do XGBoost, que é só a posição na lista).
        resultados = modelo.evals_result()
        curva = _montar_curva(
            resultados["validation_0"]["mlogloss"], resultados["validation_2"]["mlogloss"], resultados["validation_1"]["mlogloss"]
        )
    return modelo, treino_encoded.columns, curva


def prever_xgboost(modelo, colunas_treino, df: pd.DataFrame, features: list[str] = FEATURES):
    # garante as mesmas colunas (mesma ordem) vistas no treino -- uma liga
    # do treino ausente na predição, ou uma liga na predição fora do
    # dataset "Feature Stacked", vira coluna de zeros em vez de quebrar
    # (quando o treino usou early stopping, o XGBoost >=1.6 já limita
    # predict_proba à melhor iteração automaticamente -- não precisa passar
    # iteration_range aqui).
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
    `val_df`/`test_df` -- MAS, ao contrário de CatBoost (que olha só o
    primeiro eval_set) e XGBoost (que olha só o último), o callback de
    early stopping do LightGBM para assim que QUALQUER valid_set passado
    parar de melhorar. Incluir o Test nesse `eval_set` faria a decisão de
    parada depender do Test (vazamento) -- por isso aqui o `eval_set` do
    `.fit()` usado pra early stopping é só (treino, validação); o Test
    continua sendo avaliado normalmente DEPOIS do treino (fora desta
    função, em `tunar_treinar_e_calibrar`), só não aparece como curva
    ponto-a-ponto por iteração pra este framework especificamente."""
    usa_early_stopping = val_df is not None and test_df is not None
    treino = train_df.copy()
    categorias_liga = sorted(treino["liga"].dropna().unique())
    treino["liga"] = pd.Categorical(treino["liga"], categories=categorias_liga)
    modelo = LGBMClassifier(
        objective="multiclass",
        num_class=treino[coluna_alvo].nunique(),
        n_estimators=ITERACOES_MAXIMAS_EARLY_STOPPING if usa_early_stopping else ITERACOES_PRODUCAO["lightgbm"],
        # `min_child_samples` subiu de 10 pra 20 (default do LightGBM,
        # mais conservador -- exige mais partidas sustentando uma folha).
        # `subsample`/`colsample_bytree` (ver FRACAO_SUBSAMPLE/FRACAO_
        # COLSAMPLE no topo do arquivo) -- `subsample_freq=1` é
        # obrigatório pro bagging (subsample) realmente ativar no
        # LightGBM, senão o parâmetro é ignorado silenciosamente.
        min_child_samples=20,
        subsample=FRACAO_SUBSAMPLE,
        subsample_freq=1,
        colsample_bytree=FRACAO_COLSAMPLE,
        random_state=42,
        verbosity=-1,
        **params,
    )
    eval_set, eval_names, callbacks = None, None, None
    if usa_early_stopping:
        val_prep = val_df.copy()
        val_prep["liga"] = alinhar_categoria_liga(val_prep["liga"], categorias_liga)
        eval_set = [(treino[features], treino[coluna_alvo]), (val_prep[features], val_df[coluna_alvo])]
        eval_names = ["treino", "validacao"]
        callbacks = [early_stopping(stopping_rounds=RODADAS_EARLY_STOPPING, verbose=False)]
    modelo.fit(
        treino[features],
        treino[coluna_alvo],
        categorical_feature=CAT_FEATURES,
        eval_set=eval_set,
        eval_names=eval_names,
        callbacks=callbacks,
    )

    curva = None
    if eval_set is not None:
        resultados = modelo.evals_result_
        curva = _montar_curva(resultados["treino"]["multi_logloss"], resultados["validacao"]["multi_logloss"], None)
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
    "catboost_v6": (treinar_catboost, prever_catboost),
    "xgboost_v6": (treinar_xgboost, prever_xgboost),
    "lightgbm_v6": (treinar_lightgbm, prever_lightgbm),
    "catboost_v7": (treinar_catboost, prever_catboost),
    "xgboost_v7": (treinar_xgboost, prever_xgboost),
    "lightgbm_v7": (treinar_lightgbm, prever_lightgbm),
    "catboost_v8": (treinar_catboost, prever_catboost),
    "xgboost_v8": (treinar_xgboost, prever_xgboost),
    "lightgbm_v8": (treinar_lightgbm, prever_lightgbm),
    # v9 — mesmas features da v8, 4ª família de algoritmo (MLP) adicionada;
    # stacking sobre as 4 famílias base é treinado separadamente em
    # `walkforward_cv_v9.py` (precisa de OOF predictions, não entra aqui).
    "catboost_v9": (treinar_catboost, prever_catboost),
    "xgboost_v9": (treinar_xgboost, prever_xgboost),
    "lightgbm_v9": (treinar_lightgbm, prever_lightgbm),
    "mlp_v9": (None, None),  # funções definidas abaixo; placeholder pra herdar grade
    # v10 — adiciona titular_avg_age, titular_avg_height e venue_capacity_home
    "catboost_v10": (treinar_catboost, prever_catboost),
    "xgboost_v10": (treinar_xgboost, prever_xgboost),
    "lightgbm_v10": (treinar_lightgbm, prever_lightgbm),
    "mlp_v10": (None, None),  # funções definidas abaixo; placeholder pra herdar grade
}


# =============================================================================
# MLP (v9) — 4ª família de algoritmo
# =============================================================================
# Usa sklearn.neural_network.MLPClassifier dentro de um Pipeline (imputer +
# scaler + MLP) pra tratar NaN nativamente e escalar features (sensível a
# escala, ao contrário dos modelos de árvore). A coluna categórica `liga`
# recebe one-hot igual ao XGBoost (MLP não aceita categórica diretamente).
# `early_stopping=True` + `validation_fraction=0.12` carva 12% do próprio
# treino como set de parada interno — sem precisar de val_df externo (isso
# simplifica a interface mas significa que a parada não usa o MESMO Val Set
# do early stopping das árvores — trade-off aceito pra manter a interface
# uniforme de `(params, train_df, coluna_alvo, features, val_df, test_df)`).

def treinar_mlp(
    params: dict,
    train_df: pd.DataFrame,
    coluna_alvo: str = "resultado",
    features: list[str] = FEATURES,
    val_df: pd.DataFrame | None = None,
    test_df: pd.DataFrame | None = None,
):
    treino_encoded = pd.get_dummies(train_df[features], columns=CAT_FEATURES)
    colunas_treino = treino_encoded.columns.tolist()

    pipeline = Pipeline([
        ("imputer", SimpleImputer(strategy="mean")),
        ("scaler", StandardScaler()),
        ("mlp", MLPClassifier(
            hidden_layer_sizes=params.get("hidden_layer_sizes", (256, 128, 64)),
            max_iter=params.get("max_iter", 1000),
            learning_rate_init=params.get("learning_rate_init", 0.0005),
            early_stopping=True,
            validation_fraction=0.12,
            n_iter_no_change=params.get("n_iter_no_change", 50),
            random_state=42,
            verbose=False,
        )),
    ])
    pipeline.fit(treino_encoded.values, train_df[coluna_alvo].values)
    return pipeline, colunas_treino, None


def prever_mlp(pipeline, colunas_treino, df: pd.DataFrame, features: list[str] = FEATURES):
    encoded = pd.get_dummies(df[features], columns=CAT_FEATURES).reindex(columns=colunas_treino, fill_value=0)
    mlp = pipeline.named_steps["mlp"]
    return pipeline.predict_proba(encoded.values), mlp.classes_


# =============================================================================
# Stacking meta-modelo (v9) — treinado em walkforward_cv_v9.py
# =============================================================================
# A LogisticRegression meta-modelo NÃO entra no dict TREINADORES porque ela
# recebe como input as probabilidades dos 4 modelos base (não as features
# brutas), então sua interface é diferente. Exposta aqui como funções
# independentes pra ser chamada diretamente por walkforward_cv_v9.py e por
# rodar_predicoes.py quando (v10) o stacking entrar em produção.

def treinar_stacking_v9(
    meta_X: np.ndarray,
    meta_y: np.ndarray,
) -> LogisticRegression:
    """Treina a LogisticRegression meta-modelo.

    `meta_X`: shape (n, n_modelos * n_classes) — OOF predictions empilhadas.
    `meta_y`: shape (n,) — rótulos reais (0/1/2 para home/draw/away).
    """
    meta_modelo = LogisticRegression(
        max_iter=500,
        random_state=42,
        C=1.0,
        multi_class="multinomial",
        solver="lbfgs",
    )
    meta_modelo.fit(meta_X, meta_y)
    return meta_modelo


def prever_stacking_v9(
    meta_modelo: LogisticRegression,
    meta_X: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """(probs, classes) — mesma interface de `prever_catboost`/etc."""
    return meta_modelo.predict_proba(meta_X), meta_modelo.classes_


# Corrige os placeholders mlp depois de definir as funções.
TREINADORES["mlp_v9"] = (treinar_mlp, prever_mlp)
TREINADORES["mlp_v10"] = (treinar_mlp, prever_mlp)
