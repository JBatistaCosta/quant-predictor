"""Infra compartilhada de treino-único/predição-múltipla e persistência de
modelos customizados treinados, usada por treinar_modelo_custom.py,
treinar_modelo_custom_wf.py e estimar_partida_custom.py.

Por que este módulo existe: sem persistência, toda estimativa sob demanda
de uma partida precisava retreinar o modelo do zero (minutos). Agora, ao
fim de um treino bem-sucedido (simples ou walk_forward_cv), os scripts de
treino fazem um refit final no dataset INTEIRO da config (não o modelo do
split de backtest/fold -- esse continua só pra avaliação) e persistem esse
artefato aqui via `salvar_artefato`, no bucket privado
`custom-model-artifacts` (joblib). `estimar_partida_custom.py` então
verifica `custom_model_configs.model_artifacts` antes de treinar: se já
existe um artefato pro algoritmo/grupo pedido, só CARREGA e PREVÊ (segundos,
em vez de minutos) -- sem artefato (config antiga, ou esse passo falhou no
treino), cai no retreino completo como fallback.

`treinar_e_prever`/`prever_com_estado`/`split_treino_val` moram aqui (não em
cada script individualmente) porque tanto o refit final de treino quanto a
estimativa sob demanda (nos dois caminhos, com e sem artefato) precisam da
MESMA lógica de treino único + predição em vários dataframes.
"""

from __future__ import annotations

import io
import logging

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression as LR
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.impute import SimpleImputer

import modelos_ml as ml

logger = logging.getLogger(__name__)

ALGORITMOS_ML = {"catboost", "xgboost", "lightgbm"}
ALGORITMOS_SKLEARN = {"logistic_regression", "random_forest"}

BUCKET_ARTEFATOS = "custom-model-artifacts"


# ---------------------------------------------------------------------------
# Treino único + predição em vários dataframes (sem avaliação de métricas)
# ---------------------------------------------------------------------------

def treinar_e_prever(
    algoritmo: str,
    features: list[str],
    hyperparameters: dict | None,
    train_df: pd.DataFrame,
    val_df: pd.DataFrame | None,
    dfs_para_prever: dict[str, pd.DataFrame],
    target_col: str,
    tipo: str,
) -> tuple[dict, dict[str, tuple[np.ndarray, np.ndarray]]]:
    """Treina `algoritmo` uma vez em `train_df` (usando `val_df` pra early
    stopping quando aplicável) e prevê em cada df de `dfs_para_prever`.

    Retorna (estado, {nome: (probs, classes)}) -- `estado` é um dict
    serializável (joblib) com tudo que `prever_com_estado` precisa depois
    pra prever de novo SEM retreinar."""
    if algoritmo in ALGORITMOS_ML:
        nome_interno = f"{algoritmo}_v1"
        treinar_fn, prever_fn = ml.TREINADORES.get(nome_interno, (None, None))
        if treinar_fn is None:
            raise ValueError(f"Algoritmo {algoritmo!r} não encontrado em modelos_ml.TREINADORES.")
        defaults = ml.PARAMS_DEFAULT.get(nome_interno, {})
        params = {**defaults, **(hyperparameters or {})}
        feats = list(dict.fromkeys([*ml.CAT_FEATURES, *features]))

        modelo, extra, _ = treinar_fn(
            params=params, train_df=train_df, coluna_alvo=target_col,
            features=feats, val_df=val_df, test_df=val_df,
        )
        previsoes = {nome: prever_fn(modelo, extra, df, feats) for nome, df in dfs_para_prever.items()}
        estado = {"algo_tipo": "ml", "algoritmo": algoritmo, "modelo": modelo, "extra": extra, "features": feats}
        return estado, previsoes

    if algoritmo in ALGORITMOS_SKLEARN:
        hp = hyperparameters or {}
        if algoritmo == "logistic_regression":
            clf = LR(
                max_iter=hp.get("max_iter", 1000), C=hp.get("C", 1.0), solver=hp.get("solver", "lbfgs"),
                multi_class="multinomial" if tipo == "multiclasse" else "ovr",
            )
        else:
            clf = RandomForestClassifier(
                n_estimators=hp.get("n_estimators", 200), max_depth=hp.get("max_depth"),
                min_samples_leaf=hp.get("min_samples_leaf", 5), n_jobs=-1, random_state=42,
            )
        features_num = [f for f in features if f not in ml.CAT_FEATURES]
        if not features_num:
            raise ValueError(f"Sem features numéricas para {algoritmo}.")
        pipe = Pipeline([("imputer", SimpleImputer(strategy="median")), ("scaler", StandardScaler()), ("clf", clf)])
        pipe.fit(train_df[features_num].values, train_df[target_col].values)
        previsoes = {
            nome: (pipe.predict_proba(df[features_num].values), pipe.named_steps["clf"].classes_)
            for nome, df in dfs_para_prever.items()
        }
        estado = {"algo_tipo": "sklearn", "algoritmo": algoritmo, "modelo": pipe, "features_num": features_num}
        return estado, previsoes

    if algoritmo == "dixon_coles":
        raise NotImplementedError(
            "Dixon-Coles customizado ainda não implementado — "
            "use catboost/xgboost/lightgbm/logistic_regression/random_forest."
        )

    raise ValueError(f"Algoritmo desconhecido: {algoritmo!r}")


def prever_com_estado(estado: dict, df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Prevê em `df` usando um `estado` salvo por `treinar_e_prever` (ou
    carregado de um artefato persistido via `carregar_artefato`) -- sem
    retreinar. Suporta algoritmo único (ml/sklearn) e grupo de stacking
    (recursivo nos membros + meta-modelo)."""
    if estado["algo_tipo"] == "ml":
        nome_interno = f"{estado['algoritmo']}_v1"
        _, prever_fn = ml.TREINADORES[nome_interno]
        return prever_fn(estado["modelo"], estado["extra"], df, estado["features"])

    if estado["algo_tipo"] == "sklearn":
        pipe = estado["modelo"]
        probs = pipe.predict_proba(df[estado["features_num"]].values)
        return probs, pipe.named_steps["clf"].classes_

    if estado["algo_tipo"] == "stacking":
        probs_por_algo: dict[str, np.ndarray] = {}
        classes_por_algo: dict[str, np.ndarray] = {}
        for algo, sub_estado in estado["membros"].items():
            probs_por_algo[algo], classes_por_algo[algo] = prever_com_estado(sub_estado, df)
        meta_X = ml.montar_meta_features(probs_por_algo, classes_por_algo, estado["algos_grupo"], estado["classes_sorted"])
        return ml.prever_stacking_v9(estado["meta_modelo"], meta_X)

    raise ValueError(f"algo_tipo desconhecido em estado: {estado.get('algo_tipo')!r}")


def split_treino_val(pool_treino: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    """Split cronológico único: últimos 15% do pool viram val (early
    stopping dos algoritmos de árvore + treino do meta-modelo de stacking).
    Sem val quando o pool é pequeno demais pra valer a pena reservar parte
    dele (mesmo limiar de treinar_via_ml em treinar_modelo_custom.py)."""
    if len(pool_treino) < 200:
        return pool_treino, None
    val_n = max(50, int(len(pool_treino) * 0.15))
    return pool_treino.iloc[:-val_n].reset_index(drop=True), pool_treino.iloc[-val_n:].reset_index(drop=True)


def treinar_estado_stacking(
    algos_grupo: list[str],
    features: list[str],
    hyperparameters: dict | None,
    pool_treino: pd.DataFrame,
    target_col: str,
    tipo: str,
) -> dict:
    """Treina um grupo de stacking completo (algoritmos base + meta-modelo)
    e devolve um `estado` serializável pra `prever_com_estado`/
    `salvar_artefato`. Split 85/15 único (não walk-forward de 3 folds --
    não faz sentido pra um artefato de produção único): treina os
    algoritmos base no 85%, ajusta o meta-modelo nas predições do 15%."""
    train_fit, val_fit = split_treino_val(pool_treino)
    if val_fit is None:
        raise ValueError(
            f"Pool de treino pequeno demais pra separar um val set de stacking "
            f"({len(pool_treino)} partidas, mínimo 200)."
        )

    membros: dict[str, dict] = {}
    probs_val_por_algo: dict[str, np.ndarray] = {}
    classes_val_por_algo: dict[str, np.ndarray] = {}
    for algo in algos_grupo:
        estado_algo, previsoes = treinar_e_prever(algo, features, hyperparameters, train_fit, val_fit, {"val": val_fit}, target_col, tipo)
        membros[algo] = estado_algo
        probs_val_por_algo[algo], classes_val_por_algo[algo] = previsoes["val"]

    classes_sorted = np.sort(classes_val_por_algo[algos_grupo[0]])
    meta_X_val = ml.montar_meta_features(probs_val_por_algo, classes_val_por_algo, algos_grupo, classes_sorted)
    meta_y_val = val_fit[target_col].values
    meta_modelo = ml.treinar_stacking_v9(meta_X_val, meta_y_val)

    return {
        "algo_tipo": "stacking", "algos_grupo": algos_grupo, "membros": membros,
        "meta_modelo": meta_modelo, "classes_sorted": classes_sorted,
    }


# ---------------------------------------------------------------------------
# Persistência (bucket privado custom-model-artifacts, joblib)
# ---------------------------------------------------------------------------

def salvar_artefato(supabase, config_id: str, chave: str, estado: dict) -> str:
    """Serializa `estado` (de treinar_e_prever/treinar_estado_stacking) e
    sobe pro bucket. Retorna o path salvo."""
    path = f"{config_id}/{chave.replace(':', '_').replace(' ', '_')}.joblib"
    buffer = io.BytesIO()
    joblib.dump(estado, buffer)
    supabase.storage.from_(BUCKET_ARTEFATOS).upload(
        path, buffer.getvalue(), {"upsert": "true", "content-type": "application/octet-stream"},
    )
    return path


def carregar_artefato(supabase, path: str) -> dict:
    """Baixa e desserializa um artefato salvo por `salvar_artefato`."""
    conteudo = supabase.storage.from_(BUCKET_ARTEFATOS).download(path)
    return joblib.load(io.BytesIO(conteudo))
