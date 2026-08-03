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

# Mapeamento: target_key → nome do mercado e tradução int→string usados em model_predictions
_TARGET_PRED_META = {
    "1x2": {"market": "1X2", "class_to_sel": {0: "home", 1: "draw", 2: "away"}},
    "over_under_2.5": {"market": "over_under_2.5", "class_to_sel": {0: "under", 1: "over"}},
    "btts": {"market": "btts", "class_to_sel": {0: "no", 1: "yes"}},
}

# ---------------------------------------------------------------------------
# Migração de chaves de features — FotMob v8
# ---------------------------------------------------------------------------
# O frontend gerava as chaves FotMob no formato {metric}_{position}_{window}
# (ex: chutes_area_fm_home_5j), mas o dataset armazena essas colunas como
# media_{metric}_{window}_{position} (ex: media_chutes_area_fm_5j_home), e
# somente a janela de 5j existe. Configs salvas antes da correção do frontend
# precisam ter as chaves traduzidas automaticamente no momento do treino.
_FOTMOB_SHORTS_V8 = frozenset({
    # v8 — stats básicos do FotMob
    "chutes_fm", "chutes_alvo_fm", "chutes_fora_fm", "chutes_bloqueados_fm",
    "chutes_area_fm", "chutes_fora_area_fm", "chances_claras_fm",
    "chances_claras_perdidas_fm", "passes_certos_fm", "bolas_longas_certas_fm",
    "cruzamentos_certos_fm", "desarmes_fm", "interceptacoes_fm", "bloqueios_fm",
    "afastamentos_fm", "defesas_goleiro_fm", "duelos_vencidos_fm",
    "duelos_aereos_vencidos_fm", "dribles_certos_fm", "faltas_fm",
    "cartoes_amarelos_fm", "cartoes_vermelhos_fm", "escanteios_fm",
    "toques_area_adv_fm", "posse_fm",
    # v9 — situação de chutes (mesmo padrão de nomes no dataset: media_{metric}_5j_{pos})
    "pct_fast_break_fm", "pct_bola_parada_fm", "xg_chute_fm", "pct_gols_2tempo_fm",
})
_FOTMOB_POSICOES_V8 = ("sofrido_home", "sofrido_away", "home", "away")


def _migrar_chave_fotmob_v8(key: str) -> str | None:
    """Tenta converter chave FotMob v8 do formato antigo frontend para dataset.

    Retorna:
      - str  com o nome correto se 5j e reconhecida como FotMob v8
      - None se a janela não existe no dataset (10j / 20j / decay)
      - key inalterada se não for reconhecida como FotMob v8
    """
    for metric in sorted(_FOTMOB_SHORTS_V8, key=len, reverse=True):
        prefix = f"{metric}_"
        if not key.startswith(prefix):
            continue
        rest = key[len(prefix):]
        for pos in _FOTMOB_POSICOES_V8:
            if rest == f"{pos}_5j":
                nova = f"media_{metric}_5j_{pos}"
                if nova != key:
                    logger.info("Feature migrada (FotMob v8): %s → %s", key, nova)
                return nova
            if rest.startswith(f"{pos}_"):
                logger.warning(
                    "Feature FotMob v8 descartada (janela não existe no dataset): %s", key
                )
                return None
    return key


def _migrar_features(features: list[str]) -> list[str]:
    """Aplica migração de chaves FotMob v8 sobre a lista de features.

    Chaves com janelas inexistentes (10j/20j/decay) são removidas aqui
    para que o log de 'features faltando' fique mais limpo.
    """
    return [nova for key in features if (nova := _migrar_chave_fotmob_v8(key)) is not None]


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

    # Migra chaves FotMob v8 do formato antigo do frontend (metric_pos_window)
    # para o formato do dataset (media_metric_window_pos), e remove janelas
    # inexistentes (10j/20j/decay) antes da validação de features_disponiveis.
    features = _migrar_features(features)

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
        ml.CAT_FEATURES + features + [target_info["coluna"], "match_date", "match_id"]
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
) -> tuple[dict, object, list[str], list | None, np.ndarray, np.ndarray]:
    """Treina usando as funções existentes em modelos_ml.py."""
    nome_interno = f"{algoritmo}_v1"
    treinar_fn, prever_fn = ml.TREINADORES.get(nome_interno, (None, None))

    if treinar_fn is None:
        raise RuntimeError(f"Algoritmo {algoritmo!r} não encontrado em modelos_ml.TREINADORES.")

    defaults = ml.PARAMS_DEFAULT.get(nome_interno, {})
    params = {**defaults, **(hyperparameters or {})}

    # CAT_FEATURES (="liga") deve sempre estar em features
    features = list(dict.fromkeys([*ml.CAT_FEATURES, *features]))

    logger.info("Treinando %s com params=%s, %d features (incl. liga)", algoritmo, params, len(features))

    # Separa os últimos 15% do treino como validação para early stopping e
    # curvas de aprendizado. Treino cronológico: os mais recentes ficam no val.
    val_df_fit = None
    train_fit = train_df
    if len(train_df) >= 200:
        val_n = max(50, int(len(train_df) * 0.15))
        train_fit = train_df.iloc[:-val_n].copy()
        val_df_fit = train_df.iloc[-val_n:].copy()

    modelo, extra, curva = treinar_fn(
        params=params,
        train_df=train_fit,
        coluna_alvo=target_col,
        features=features,
        val_df=val_df_fit,
        test_df=val_df_fit,
    )

    # Passa test_df completo + features explícito para evitar que prever_fn
    # caia no default global FEATURES (que contém colunas antigas como
    # media_xg_5j_home) ao receber um df já fatiado sem todas as colunas.
    probs, classes = prever_fn(modelo, extra, test_df, features)

    y_true = test_df[target_col].values
    metricas = calcular_metricas(y_true, probs, tipo)
    metricas["algoritmo"] = algoritmo
    metricas["n_features"] = len(features)
    metricas["params"] = params

    logger.info("Métricas: %s", metricas)
    return metricas, modelo, features, curva, probs, classes


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
) -> tuple[dict, object, list[str], None, np.ndarray, np.ndarray]:
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
    return metricas, pipe, features_num, None, probs, pipe.named_steps['clf'].classes_


# ---------------------------------------------------------------------------
# Salvar predições por partida
# ---------------------------------------------------------------------------

def salvar_predicoes_no_banco(
    supabase,
    test_df: pd.DataFrame,
    probs: np.ndarray,
    classes: np.ndarray,
    model_name: str,
    target_key: str,
) -> None:
    """Grava probabilidades por partida em model_predictions (upsert)."""
    meta = _TARGET_PRED_META.get(target_key)
    if meta is None:
        logger.warning("target_key %r sem meta de predição — predições não salvas.", target_key)
        return

    if "match_id" not in test_df.columns:
        logger.warning("match_id ausente no test_df — predições não salvas.")
        return

    market = meta["market"]
    class_to_sel = meta["class_to_sel"]
    match_ids = test_df["match_id"].values

    rows = []
    for i, match_id in enumerate(match_ids):
        for col_idx, cls_int in enumerate(classes):
            sel = class_to_sel.get(int(cls_int))
            if sel is None:
                continue
            rows.append({
                "match_id": int(match_id),
                "model_name": model_name,
                "market": market,
                "selection": sel,
                "probability": round(float(probs[i, col_idx]), 6),
            })

    if not rows:
        logger.warning("Nenhuma linha a inserir em model_predictions.")
        return

    logger.info(
        "Salvando %d predições em model_predictions (modelo=%s, mercado=%s)...",
        len(rows), model_name, market,
    )
    batch_size = 500
    for start in range(0, len(rows), batch_size):
        supabase.table("model_predictions").upsert(
            rows[start : start + batch_size],
            on_conflict="match_id,model_name,market,selection",
        ).execute()

    logger.info("✅ %d predições salvas em model_predictions.", len(rows))


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
        curva_aprendizado = None
        if algoritmo in ALGORITMOS_ML:
            metricas, modelo, features_finais, curva_aprendizado, probs_test, classes_test = treinar_via_ml(
                algoritmo, features_usadas, hyperparameters,
                train_df, test_df, target_info["coluna"], target_info["tipo"],
            )
        elif algoritmo in ALGORITMOS_SKLEARN:
            metricas, modelo, features_finais, _, probs_test, classes_test = treinar_via_sklearn(
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

        learning_curves = {}
        if curva_aprendizado is not None:
            learning_curves[algoritmo] = curva_aprendizado

        metrics_final = {
            "models": {algoritmo: metricas},
            "feature_importance": feature_importance,
            "learning_curves": learning_curves,
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

        # 5. Salva predições por partida para que o modelo apareça em Estatísticas,
        #    Backtest e Simulação de Carteira automaticamente.
        salvar_predicoes_no_banco(supabase, test_df, probs_test, classes_test, cfg["name"], target_key)

        # Imprime JSON estruturado nos logs do Actions (fácil de parsear/grep)
        print(json.dumps({"config_id": config_id, "status": "treinado", "metricas": metrics_final}, ensure_ascii=False))

    except Exception as exc:
        logger.exception("Erro fatal no treino customizado: %s", exc)
        marcar_erro(supabase, config_id, str(exc))
        sys.exit(1)


if __name__ == "__main__":
    main()
