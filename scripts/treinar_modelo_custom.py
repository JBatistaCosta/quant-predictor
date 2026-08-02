#!/usr/bin/env python3
"""Treino customizado a partir de uma configuração em custom_model_configs.

Lê o config_id do Supabase, monta o dataset com as features selecionadas
pelo usuário, treina o modelo escolhido (catboost / xgboost / lightgbm /
logistic_regression / random_forest / dixon_coles) e persiste as métricas
de avaliação de volta em custom_model_configs.

Arquitetura deliberadamente simples (sem walk-forward, sem stacking):
  - Split cronológico fixo: Train ≤ 2023 | Test ≥ 2024
    (mesmo "Teste sempre em 2025" do projeto, mas inclui 2024 no test pra
     ter sample suficiente numa configuração arbitrária do usuário)
  - Avaliação: log-loss + Brier Score multiclasse + acurácia (1X2) ou
    log-loss + Brier binário + acurácia (over_under / btts)
  - Métricas gravadas em custom_model_configs.metrics (JSONB)

Variáveis de ambiente:
  SUPABASE_URL, SUPABASE_KEY   — acesso ao banco
  CONFIG_ID                    — UUID da linha em custom_model_configs

Erros fatais atualizam custom_model_configs.status='erro' e
custom_model_configs.error_message com a mensagem antes de sair com
exit code 1, pra o painel mostrar o problema sem precisar abrir o Actions.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression as LR
from sklearn.metrics import log_loss, brier_score_loss, accuracy_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.impute import SimpleImputer
from supabase import create_client

# Importa a infraestrutura existente do projeto
sys.path.insert(0, os.path.dirname(__file__))
import dados_historicos as dh
import modelos_ml as ml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("treinar_modelo_custom")

# Split cronológico: treino até 2023, teste 2024+
TREINO_MAX_ANO = 2023
TESTE_MIN_ANO = 2024

# Mapeamento: chave do target → coluna do dataset + tipo
TARGETS = {
    "1x2": {"coluna": "resultado", "tipo": "multiclasse", "classes": 3},
    "over_under_2.5": {"coluna": "resultado_over25", "tipo": "binario", "classes": 2},
    "btts": {"coluna": "resultado_btts", "tipo": "binario", "classes": 2},
}

# Algoritmos que passam pelo módulo modelos_ml.py (reaproveitam treinar_*/prever_*)
ALGORITMOS_ML = {"catboost", "xgboost", "lightgbm"}
# Algoritmos nativos sklearn (logistic_regression, random_forest)
ALGORITMOS_SKLEARN = {"logistic_regression", "random_forest"}


# ---------------------------------------------------------------------------
# Utilitários Supabase
# ---------------------------------------------------------------------------

def criar_supabase():
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_KEY"]
    return create_client(url, key)


def carregar_config(supabase, config_id: str) -> dict:
    resp = (
        supabase.table("custom_model_configs")
        .select("*")
        .eq("id", config_id)
        .single()
        .execute()
    )
    if not resp.data:
        raise ValueError(f"Config {config_id!r} não encontrada em custom_model_configs.")
    return resp.data


def atualizar_status(supabase, config_id: str, status: str, **extras):
    payload = {"status": status, **extras}
    supabase.table("custom_model_configs").update(payload).eq("id", config_id).execute()


def marcar_erro(supabase, config_id: str, msg: str):
    try:
        atualizar_status(supabase, config_id, "erro", error_message=msg[:2000])
    except Exception as e:
        logger.error("Falha ao gravar erro no Supabase: %s", e)


# ---------------------------------------------------------------------------
# Preparação do dataset
# ---------------------------------------------------------------------------

def carregar_dataset(supabase, features: list[str], target_info: dict) -> pd.DataFrame:
    """Carrega o dataset completo com as features solicitadas.

    Reutiliza dh.montar_dataset_completo, que já faz todos os joins,
    rolling windows, elo, squad_rating etc. — devolvendo TODAS as features
    disponíveis. Aqui selecionamos só as que o usuário pediu.
    """
    logger.info("Carregando dataset histórico...")
    # montar_dataset_ml_empilhado já seleciona as ligas do Model Benchmarking internamente
    dataset = dh.montar_dataset_ml_empilhado(supabase)
    if dataset.empty:
        raise RuntimeError("Dataset histórico vazio — verifique as ligas e temporadas no banco.")

    logger.info("Dataset carregado: %d partidas, %d colunas", len(dataset), len(dataset.columns))

    # Adiciona coluna de alvo se for btts (não é gerada por padrão)
    if target_info["coluna"] == "resultado_btts" and "resultado_btts" not in dataset.columns:
        if "home_goals" in dataset.columns and "away_goals" in dataset.columns:
            dataset["resultado_btts"] = (
                (dataset["home_goals"] > 0) & (dataset["away_goals"] > 0)
            ).astype(int)
        else:
            raise RuntimeError("Não foi possível calcular resultado_btts: home_goals/away_goals ausentes.")

    # Verifica que todas as features requisitadas existem no dataset
    features_disponiveis = set(dataset.columns)
    faltando = [f for f in features if f not in features_disponiveis]
    if faltando:
        logger.warning(
            "Features solicitadas mas NÃO disponíveis no dataset (serão ignoradas): %s",
            faltando,
        )
        features = [f for f in features if f in features_disponiveis]

    if not features:
        raise RuntimeError(
            "Nenhuma feature válida disponível — verifique se os dados de base (elo, squad_rating, etc.) "
            "foram ingeridos corretamente."
        )

    # ml.CAT_FEATURES (="liga") precisa estar no dataset independente da
    # seleção do usuário — treinar_catboost/lightgbm/xgboost acessam
    # train_df["liga"] internamente mesmo quando "liga" não foi escolhida
    # como feature; sem isso, falha com KeyError: 'liga'.
    cols_necessarias = list(dict.fromkeys(
        ml.CAT_FEATURES + features + [target_info["coluna"], "match_date"]
    ))
    cols_presentes = [c for c in cols_necessarias if c in dataset.columns]
    dataset = dataset[cols_presentes].dropna(subset=[target_info["coluna"]]).copy()

    logger.info(
        "Dataset após filtro de colunas e NaN no alvo: %d partidas, %d features usadas",
        len(dataset),
        len(features),
    )
    return dataset, features


def split_dataset(dataset: pd.DataFrame, target_col: str, features: list[str]):
    """Split cronológico: treino ≤ TREINO_MAX_ANO, teste ≥ TESTE_MIN_ANO."""
    anos = pd.to_datetime(dataset["match_date"]).dt.year
    train_df = dataset[anos <= TREINO_MAX_ANO].copy()
    test_df = dataset[anos >= TESTE_MIN_ANO].copy()

    if len(train_df) < 100:
        raise RuntimeError(
            f"Treino com apenas {len(train_df)} amostras — dados insuficientes."
        )
    if len(test_df) < 20:
        raise RuntimeError(
            f"Teste com apenas {len(test_df)} amostras — dados insuficientes."
        )

    logger.info("Split: %d treino / %d teste", len(train_df), len(test_df))
    return train_df, test_df


# ---------------------------------------------------------------------------
# Avaliação
# ---------------------------------------------------------------------------

def calcular_metricas(y_true: np.ndarray, probs: np.ndarray, tipo: str) -> dict:
    """Calcula log-loss, Brier e acurácia."""
    classes = sorted(set(y_true))
    if probs.ndim == 1:
        probs = np.column_stack([1 - probs, probs])

    ll = log_loss(y_true, probs, labels=classes)
    pred_cls = probs.argmax(axis=1)
    acc = accuracy_score(y_true, pred_cls)

    metricas: dict = {
        "log_loss": round(float(ll), 5),
        "accuracy": round(float(acc), 5),
        "n_test": len(y_true),
    }

    if tipo == "binario":
        # Brier binário (classe positiva = índice 1)
        p_pos = probs[:, 1]
        y_bin = (y_true == classes[-1]).astype(int)
        brier = brier_score_loss(y_bin, p_pos)
        metricas["brier_score"] = round(float(brier), 5)
    else:
        # Brier multiclasse: média dos Brier por classe
        from sklearn.preprocessing import label_binarize
        y_bin_matrix = label_binarize(y_true, classes=classes)
        briers = [
            brier_score_loss(y_bin_matrix[:, i], probs[:, i])
            for i in range(len(classes))
        ]
        metricas["brier_score_medio"] = round(float(np.mean(briers)), 5)

    return metricas


# ---------------------------------------------------------------------------
# Treino via modelos_ml (catboost / xgboost / lightgbm)
# ---------------------------------------------------------------------------

def treinar_via_ml(
    algoritmo: str,
    features: list[str],
    hyperparameters: dict | None,
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    target_col: str,
    tipo: str,
) -> tuple[dict, object, list[str]]:
    """Treina usando as funções existentes em modelos_ml.py."""
    # Resolve nome interno (catboost_v1 é o slot genérico)
    nome_interno = f"{algoritmo}_v1"
    treinar_fn, prever_fn = ml.TREINADORES.get(nome_interno, (None, None))

    if treinar_fn is None:
        raise RuntimeError(f"Algoritmo {algoritmo!r} não encontrado em modelos_ml.TREINADORES.")

    # Hiperparâmetros: mescla defaults com os do usuário
    defaults = ml.PARAMS_DEFAULT.get(nome_interno, {})
    params = {**defaults, **(hyperparameters or {})}

    # CAT_FEATURES (="liga") deve sempre estar em features — treinar_catboost/
    # xgboost/lightgbm exigem ela internamente como coluna categórica.
    features = list(dict.fromkeys([*ml.CAT_FEATURES, *features]))

    logger.info("Treinando %s com params=%s, %d features (incl. liga)", algoritmo, params, len(features))

    modelo, extra, _ = treinar_fn(
        params=params,
        train_df=train_df,
        coluna_alvo=target_col,
        features=features,
    )

    probs, classes = prever_fn(modelo, extra, test_df[features])

    y_true = test_df[target_col].values
    metricas = calcular_metricas(y_true, probs, tipo)
    metricas["algoritmo"] = algoritmo
    metricas["n_features"] = len(features)
    metricas["params"] = params

    logger.info("Métricas: %s", metricas)
    return metricas, modelo, features


# ---------------------------------------------------------------------------
# Treino via sklearn (logistic_regression / random_forest)
# ---------------------------------------------------------------------------

def treinar_via_sklearn(
    algoritmo: str,
    features: list[str],
    hyperparameters: dict | None,
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    target_col: str,
    tipo: str,
) -> tuple[dict, object, list[str]]:
    """Treina com Pipeline sklearn (imputer + scaler + classificador)."""
    hp = hyperparameters or {}

    if algoritmo == "logistic_regression":
        clf = LR(
            max_iter=hp.get("max_iter", 1000),
            C=hp.get("C", 1.0),
            solver=hp.get("solver", "lbfgs"),
            multi_class="multinomial" if tipo == "multiclasse" else "ovr",
        )
    else:  # random_forest
        clf = RandomForestClassifier(
            n_estimators=hp.get("n_estimators", 200),
            max_depth=hp.get("max_depth", None),
            min_samples_leaf=hp.get("min_samples_leaf", 5),
            n_jobs=-1,
            random_state=42,
        )

    # Apenas features numéricas (sklearn não aceita categorias direto)
    features_num = [f for f in features if f not in ml.CAT_FEATURES]
    if not features_num:
        raise RuntimeError(
            f"Nenhuma feature numérica disponível para {algoritmo}. "
            "Logistic Regression e Random Forest não aceitam features categóricas puras."
        )

    pipe = Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler", StandardScaler()),
        ("clf", clf),
    ])

    X_train = train_df[features_num].values
    y_train = train_df[target_col].values
    X_test = test_df[features_num].values
    y_true = test_df[target_col].values

    logger.info("Treinando %s com %d features numéricas", algoritmo, len(features_num))
    pipe.fit(X_train, y_train)
    probs = pipe.predict_proba(X_test)

    metricas = calcular_metricas(y_true, probs, tipo)
    metricas["algoritmo"] = algoritmo
    metricas["n_features"] = len(features_num)
    metricas["params"] = hp

    logger.info("Métricas: %s", metricas)
    return metricas, pipe, features_num


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    config_id = os.environ.get("CONFIG_ID", "").strip()
    if not config_id:
        logger.error("CONFIG_ID não definido.")
        sys.exit(1)

    supabase = criar_supabase()

    # Marca como "treinando" imediatamente
    atualizar_status(supabase, config_id, "treinando")

    try:
        # 1. Carrega configuração
        cfg = carregar_config(supabase, config_id)
        algoritmo = cfg["algorithm"]
        features_req = cfg["features"] or []
        target_key = cfg.get("target") or "1x2"
        hyperparameters = cfg.get("hyperparameters")

        logger.info(
            "Config carregada: algoritmo=%s, target=%s, %d features",
            algoritmo, target_key, len(features_req),
        )

        if not features_req:
            raise ValueError("Nenhuma feature selecionada na configuração.")

        target_info = TARGETS.get(target_key)
        if not target_info:
            raise ValueError(f"Target {target_key!r} não suportado. Use: {list(TARGETS)}")

        # 2. Carrega e prepara dataset
        dataset, features_usadas = carregar_dataset(supabase, features_req, target_info)
        train_df, test_df = split_dataset(dataset, target_info["coluna"], features_usadas)

        # 3. Treina o modelo
        if algoritmo in ALGORITMOS_ML:
            metricas, modelo, features_finais = treinar_via_ml(
                algoritmo, features_usadas, hyperparameters,
                train_df, test_df, target_info["coluna"], target_info["tipo"],
            )
        elif algoritmo in ALGORITMOS_SKLEARN:
            metricas, modelo, features_finais = treinar_via_sklearn(
                algoritmo, features_usadas, hyperparameters,
                train_df, test_df, target_info["coluna"], target_info["tipo"],
            )
        elif algoritmo == "dixon_coles":
            raise NotImplementedError(
                "Dixon-Coles customizado ainda não implementado neste script — "
                "use catboost/xgboost/lightgbm/logistic_regression/random_forest por enquanto."
            )
        else:
            raise ValueError(f"Algoritmo desconhecido: {algoritmo!r}")

        # 4. Grava resultado no Supabase
        agora = datetime.now(timezone.utc).isoformat()
        
        feature_importance = {}
        # Tenta extrair feature importance (árvores)
        if hasattr(modelo, 'feature_importances_'):
            try:
                importances = modelo.feature_importances_
                feature_importance = {f: float(imp) for f, imp in zip(features_finais, importances)}
            except Exception:
                pass
        elif hasattr(modelo, 'get_feature_importance'):
            try:
                importances = modelo.get_feature_importance()
                feature_importance = {f: float(imp) for f, imp in zip(features_finais, importances)}
            except Exception:
                pass

        # Estrutura esperada pelo RelatorioTreinoModal.jsx do frontend
        metrics_final = {
            "models": {
                algoritmo: metricas
            },
            "feature_importance": feature_importance,
            "learning_curves": {} # Curvas não salvas no modo custom ainda, mas a chave previne erros
        }

        atualizar_status(
            supabase,
            config_id,
            "treinado",
            metrics=metrics_final,
            trained_at=agora,
            error_message=None,
        )
        logger.info("✅ Treino concluído e métricas gravadas em custom_model_configs.")

        # Imprime JSON estruturado nos logs do Actions (fácil de parsear/grep)
        print(json.dumps({"config_id": config_id, "status": "treinado", "metricas": metrics_final}, ensure_ascii=False))

    except Exception as exc:
        logger.exception("Erro fatal no treino customizado: %s", exc)
        marcar_erro(supabase, config_id, str(exc))
        sys.exit(1)


if __name__ == "__main__":
    main()
