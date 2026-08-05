#!/usr/bin/env python3
"""Walk-forward CV para modelos customizados.

3 folds temporais com janela expansiva:
  Fold 1: Train ≤ 2021  |  Val 2022  |  Test 2023
  Fold 2: Train ≤ 2022  |  Val 2023  |  Test 2024
  Fold 3: Train ≤ 2023  |  Val 2024  |  Test 2025

Diferenças em relação a walkforward_cv_v9.py:
  - Features e algorithms configuráveis pelo usuário via custom_model_configs
  - Qualquer target suportado (1x2, over_under_2.5, btts, faixa_gols, etc.)
  - Sem stacking (deixado para v10)
  - Nomes nos model_predictions: "{config.name} [{algo}]" para cada algoritmo

Variáveis de ambiente: SUPABASE_URL, SUPABASE_KEY, CONFIG_ID
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
from sklearn.preprocessing import StandardScaler, label_binarize
from sklearn.impute import SimpleImputer
from supabase import create_client

sys.path.insert(0, os.path.dirname(__file__))
import dados_historicos as dh
import modelos_ml as ml
import calibracao as cal_lib

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("treinar_modelo_custom_wf")

FOLDS = [
    {"nome": "fold_1", "treino_max_ano": 2021, "val_ano": 2022, "test_ano": 2023},
    {"nome": "fold_2", "treino_max_ano": 2022, "val_ano": 2023, "test_ano": 2024},
    {"nome": "fold_3", "treino_max_ano": 2023, "val_ano": 2024, "test_ano": 2025},
]

TARGETS = {
    "1x2": {"coluna": "resultado", "tipo": "multiclasse", "classes": 3},
    "over_under_2.5": {"coluna": "resultado_over25", "tipo": "binario", "classes": 2},
    "btts": {"coluna": "resultado_btts", "tipo": "binario", "classes": 2},
    "faixa_gols": {"coluna": "resultado_faixa_gols", "tipo": "multiclasse", "classes": 4},
    "corners_over_under_9.5": {"coluna": "resultado_corners_ou95", "tipo": "binario", "classes": 2},
    "faixa_corners": {"coluna": "resultado_faixa_corners", "tipo": "multiclasse", "classes": 4},
}

_TARGET_PRED_META = {
    "1x2": {"market": "1X2", "class_to_sel": {0: "home", 1: "draw", 2: "away"}},
    "over_under_2.5": {"market": "over_under_2.5", "class_to_sel": {0: "under", 1: "over"}},
    "btts": {"market": "btts", "class_to_sel": {0: "no", 1: "yes"}},
    "faixa_gols": {"market": "faixa_gols", "class_to_sel": {0: "0-1", 1: "2-3", 2: "4-6", 3: "7+"}},
    "corners_over_under_9.5": {"market": "corners_over_under_9.5", "class_to_sel": {0: "under", 1: "over"}},
    "faixa_corners": {"market": "faixa_corners", "class_to_sel": {0: "≤8", 1: "9-10", 2: "11-12", 3: "13+"}},
}

ALGORITMOS_ML = {"catboost", "xgboost", "lightgbm"}
ALGORITMOS_SKLEARN = {"logistic_regression", "random_forest"}

_FOTMOB_SHORTS_V8 = frozenset({
    "chutes_fm", "chutes_alvo_fm", "chutes_fora_fm", "chutes_bloqueados_fm",
    "chutes_area_fm", "chutes_fora_area_fm", "chances_claras_fm",
    "chances_claras_perdidas_fm", "passes_certos_fm", "bolas_longas_certas_fm",
    "cruzamentos_certos_fm", "desarmes_fm", "interceptacoes_fm", "bloqueios_fm",
    "afastamentos_fm", "defesas_goleiro_fm", "duelos_vencidos_fm",
    "duelos_aereos_vencidos_fm", "dribles_certos_fm", "faltas_fm",
    "cartoes_amarelos_fm", "cartoes_vermelhos_fm", "escanteios_fm",
    "toques_area_adv_fm", "posse_fm",
    "pct_fast_break_fm", "pct_bola_parada_fm", "xg_chute_fm", "pct_gols_2tempo_fm",
})
_FOTMOB_POSICOES_V8 = ("sofrido_home", "sofrido_away", "home", "away")


def _migrar_chave_fotmob_v8(key: str) -> str | None:
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
                logger.warning("Feature FotMob v8 descartada (janela inexistente): %s", key)
                return None
    return key


def _migrar_features(features: list[str]) -> list[str]:
    return [nova for key in features if (nova := _migrar_chave_fotmob_v8(key)) is not None]


# ---------------------------------------------------------------------------
# Supabase
# ---------------------------------------------------------------------------

def criar_supabase():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])


def atualizar_status(supabase, config_id: str, status: str, **extras):
    supabase.table("custom_model_configs").update({"status": status, **extras}).eq("id", config_id).execute()


def marcar_erro(supabase, config_id: str, msg: str):
    try:
        atualizar_status(supabase, config_id, "erro", error_message=msg[:2000])
    except Exception as e:
        logger.error("Falha ao gravar erro no Supabase: %s", e)


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------

def carregar_dataset(
    supabase, features: list[str], target_info: dict, todas_ligas: bool = False
) -> tuple[pd.DataFrame, list[str]]:
    logger.info("Carregando dataset ML empilhado (todas_ligas=%s)...", todas_ligas)
    dataset = dh.montar_dataset_ml_empilhado(
        supabase,
        anos_por_liga=None if todas_ligas else 7,
        todas_as_ligas=todas_ligas,
    )
    if dataset.empty:
        raise RuntimeError("Dataset vazio.")

    logger.info("Dataset bruto: %d linhas, %d colunas", len(dataset), len(dataset.columns))

    features = _migrar_features(features)

    if target_info["coluna"] == "resultado_btts" and "resultado_btts" not in dataset.columns:
        if "home_goals" in dataset.columns and "away_goals" in dataset.columns:
            dataset["resultado_btts"] = (
                (dataset["home_goals"] > 0) & (dataset["away_goals"] > 0)
            ).astype(int)
        else:
            raise RuntimeError("Colunas home_goals/away_goals ausentes para calcular btts.")

    features_disponiveis = set(dataset.columns)
    faltando = [f for f in features if f not in features_disponiveis]
    if faltando:
        logger.warning("Features ignoradas (não disponíveis no dataset): %s", faltando)
        features = [f for f in features if f in features_disponiveis]

    if not features:
        raise RuntimeError("Nenhuma feature válida disponível.")

    cols_necessarias = list(dict.fromkeys(
        ml.CAT_FEATURES + features + [target_info["coluna"], "match_date", "match_id", "liga"]
    ))
    cols_presentes = [c for c in cols_necessarias if c in dataset.columns]
    dataset = dataset[cols_presentes].dropna(subset=[target_info["coluna"]]).copy()
    dataset = dataset.reset_index(drop=True)

    logger.info("Dataset pronto: %d partidas, %d features", len(dataset), len(features))
    return dataset, features


def split_por_fold(dataset: pd.DataFrame, treino_max_ano: int, val_ano: int, test_ano: int):
    anos = pd.to_datetime(dataset["match_date"]).dt.year
    train_df = dataset[anos <= treino_max_ano].copy()
    val_df   = dataset[anos == val_ano].copy()
    test_df  = dataset[anos == test_ano].copy()
    return train_df, val_df, test_df


# ---------------------------------------------------------------------------
# Métricas
# ---------------------------------------------------------------------------

def calcular_metricas_fold(
    test_df: pd.DataFrame,
    probs: np.ndarray,
    classes: np.ndarray,
    target_col: str,
    tipo: str,
) -> dict:
    y = test_df[target_col].values
    classes_sorted = np.sort(classes)

    ordered = np.zeros((len(probs), len(classes_sorted)))
    for i, c in enumerate(classes):
        idx = np.where(classes_sorted == c)[0]
        if len(idx):
            ordered[:, idx[0]] = probs[:, i]
    ordered = np.clip(ordered, 1e-7, 1 - 1e-7)

    ll = log_loss(y, ordered, labels=classes_sorted)
    acc = accuracy_score(y, classes_sorted[np.argmax(ordered, axis=1)])

    metricas: dict = {
        "logloss_test": round(float(ll), 5),
        "accuracy_test": round(float(acc), 5),
        "n_test": len(y),
    }

    if tipo == "binario":
        p_pos = ordered[:, -1]
        y_bin = (y == classes_sorted[-1]).astype(int)
        metricas["brier_score_test"] = round(float(brier_score_loss(y_bin, p_pos)), 5)
    else:
        y_bin_matrix = label_binarize(y, classes=classes_sorted)
        briers = [brier_score_loss(y_bin_matrix[:, i], ordered[:, i]) for i in range(len(classes_sorted))]
        metricas["brier_score_test"] = round(float(np.mean(briers)), 5)

    # Por liga
    logloss_por_liga = {}
    if "liga" in test_df.columns:
        test_indexed = test_df.reset_index(drop=True)
        for liga, grupo in test_indexed.groupby("liga"):
            if len(grupo) < 10:
                continue
            idx = grupo.index
            y_l = grupo[target_col].values
            p_l = ordered[idx]
            try:
                ll_l = log_loss(y_l, p_l, labels=classes_sorted)
                acc_l = accuracy_score(y_l, classes_sorted[np.argmax(p_l, axis=1)])
                logloss_por_liga[str(liga)] = {
                    "logloss": round(float(ll_l), 5),
                    "accuracy": round(float(acc_l), 4),
                    "n": len(y_l),
                }
            except Exception:
                pass

    metricas["logloss_por_liga"] = logloss_por_liga
    return metricas


# ---------------------------------------------------------------------------
# Treino por algoritmo
# ---------------------------------------------------------------------------

def _amostrar_curva(curva: list | None, max_pontos: int = 100) -> list | None:
    if not curva or len(curva) <= max_pontos:
        return curva
    passo = max(1, len(curva) // max_pontos)
    amostrada = curva[::passo]
    if curva[-1]["iteracao"] != amostrada[-1]["iteracao"]:
        amostrada = amostrada + [curva[-1]]
    return amostrada


def treinar_fold_ml(
    algoritmo: str,
    features: list[str],
    hyperparameters: dict | None,
    train_df: pd.DataFrame,
    val_df: pd.DataFrame,
    test_df: pd.DataFrame,
    target_col: str,
) -> tuple[np.ndarray, np.ndarray, object, list | None, np.ndarray, np.ndarray]:
    """Retorna (probs, classes, modelo, curva, val_probs, val_classes).
    val_probs/val_classes são as predições no val set, usadas para calibração."""
    nome_interno = f"{algoritmo}_v1"
    treinar_fn, prever_fn = ml.TREINADORES.get(nome_interno, (None, None))
    if treinar_fn is None:
        raise RuntimeError(f"Algoritmo {algoritmo!r} não encontrado em modelos_ml.TREINADORES.")

    defaults = ml.PARAMS_DEFAULT.get(nome_interno, {})
    params = {**defaults, **(hyperparameters or {})}
    feats = list(dict.fromkeys([*ml.CAT_FEATURES, *features]))

    modelo, extra, curva_bruta = treinar_fn(
        params=params,
        train_df=train_df,
        coluna_alvo=target_col,
        features=feats,
        val_df=val_df,
        test_df=val_df,
    )
    curva = _amostrar_curva(curva_bruta or [])
    val_probs, val_classes = prever_fn(modelo, extra, val_df, feats)
    probs, classes = prever_fn(modelo, extra, test_df, feats)
    return probs, classes, modelo, curva, val_probs, val_classes


def treinar_fold_sklearn(
    algoritmo: str,
    features: list[str],
    hyperparameters: dict | None,
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    target_col: str,
    tipo: str,
) -> tuple[np.ndarray, np.ndarray, object, None]:
    hp = hyperparameters or {}
    features_num = [f for f in features if f not in ml.CAT_FEATURES]
    if not features_num:
        raise RuntimeError(f"Sem features numéricas para {algoritmo}.")

    if algoritmo == "logistic_regression":
        clf = LR(
            max_iter=hp.get("max_iter", 1000),
            C=hp.get("C", 1.0),
            solver=hp.get("solver", "lbfgs"),
            multi_class="multinomial" if tipo == "multiclasse" else "ovr",
        )
    else:
        clf = RandomForestClassifier(
            n_estimators=hp.get("n_estimators", 200),
            max_depth=hp.get("max_depth", None),
            min_samples_leaf=hp.get("min_samples_leaf", 5),
            n_jobs=-1,
            random_state=42,
        )

    pipe = Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler", StandardScaler()),
        ("clf", clf),
    ])
    pipe.fit(train_df[features_num].values, train_df[target_col].values)
    probs = pipe.predict_proba(test_df[features_num].values)
    classes = pipe.named_steps["clf"].classes_
    return probs, classes, pipe, None


# ---------------------------------------------------------------------------
# Predições → model_predictions
# ---------------------------------------------------------------------------

def salvar_predicoes_fold3(
    supabase,
    test_df: pd.DataFrame,
    probs: np.ndarray,
    classes: np.ndarray,
    model_name: str,
    target_key: str,
) -> None:
    meta = _TARGET_PRED_META.get(target_key)
    if meta is None or "match_id" not in test_df.columns:
        return

    market = meta["market"]
    class_to_sel = meta["class_to_sel"]
    match_ids = test_df["match_id"].values

    # Apaga previsões anteriores deste modelo neste mercado
    supabase.table("model_predictions").delete().eq("model_name", model_name).eq("market", market).execute()

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
        return

    logger.info("Salvando %d predições (modelo=%s, mercado=%s)...", len(rows), model_name, market)
    for start in range(0, len(rows), 500):
        supabase.table("model_predictions").upsert(
            rows[start:start + 500],
            on_conflict="match_id,model_name,market,selection",
        ).execute()
    logger.info("✅ %d predições salvas.", len(rows))


# ---------------------------------------------------------------------------
# Feature importance
# ---------------------------------------------------------------------------

def extrair_importancia(modelo, features: list[str], algoritmo: str) -> dict:
    try:
        if algoritmo == "catboost" and hasattr(modelo, "get_feature_importance"):
            imps = modelo.get_feature_importance()
            return {f: round(float(v), 6) for f, v in zip(features, imps)}
        if hasattr(modelo, "feature_importances_"):
            imps = modelo.feature_importances_
            return {f: round(float(v), 6) for f, v in zip(features, imps)}
        if hasattr(modelo, "named_steps") and hasattr(modelo.named_steps.get("clf", None), "feature_importances_"):
            imps = modelo.named_steps["clf"].feature_importances_
            feat_num = [f for f in features if f not in ml.CAT_FEATURES]
            return {f: round(float(v), 6) for f, v in zip(feat_num, imps)}
    except Exception as e:
        logger.warning("Falha ao extrair feature importance: %s", e)
    return {}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    config_id = os.environ.get("CONFIG_ID", "").strip()
    if not config_id:
        logger.error("CONFIG_ID não definido.")
        sys.exit(1)

    supabase = criar_supabase()
    atualizar_status(supabase, config_id, "treinando")

    try:
        cfg_resp = supabase.table("custom_model_configs").select("*").eq("id", config_id).single().execute()
        if not cfg_resp.data:
            raise ValueError(f"Config {config_id!r} não encontrada.")
        cfg = cfg_resp.data

        config_name   = cfg["name"]
        features_req  = cfg["features"] or []
        target_key    = cfg.get("target") or "1x2"
        hyperparams   = cfg.get("hyperparameters") or {}
        algoritmos    = cfg.get("algorithms") or []
        todas_ligas   = bool(cfg.get("todas_ligas"))

        if not algoritmos:
            raise ValueError("Lista de algoritmos (algorithms) vazia na configuração.")
        if not features_req:
            raise ValueError("Nenhuma feature selecionada na configuração.")

        target_info = TARGETS.get(target_key)
        if not target_info:
            raise ValueError(f"Target {target_key!r} não suportado.")

        logger.info("Config: name=%s, target=%s, algoritmos=%s, %d features",
                    config_name, target_key, algoritmos, len(features_req))

        # Carrega dataset completo uma vez só
        dataset, features_usadas = carregar_dataset(supabase, features_req, target_info, todas_ligas)

        target_col = target_info["coluna"]
        tipo       = target_info["tipo"]

        # Acumuladores de resultado
        resultados_folds: list[dict] = []
        feature_importance: dict[str, dict] = {}
        learning_curves: dict[str, list] = {}
        # model_name_por_algo: {"catboost": "Meu Modelo [catboost]", ...}
        model_name_por_algo = {algo: f"{config_name} [{algo}]" for algo in algoritmos}
        model_names = list(model_name_por_algo.values())

        # ── Walk-forward CV ──────────────────────────────────────────────────
        for fold in FOLDS:
            fold_nome = fold["nome"]
            logger.info("── %s ─────────────────────────────────────", fold_nome)

            train_df, val_df, test_df = split_por_fold(
                dataset, fold["treino_max_ano"], fold["val_ano"], fold["test_ano"]
            )

            if train_df.empty or test_df.empty:
                logger.warning("Fold %s com split vazio — pulando.", fold_nome)
                continue

            logger.info("  n_treino=%d | n_val=%d | n_test=%d",
                        len(train_df), len(val_df), len(test_df))

            fold_resultado: dict = {
                "fold":          fold_nome,
                "treino_max_ano": fold["treino_max_ano"],
                "val_ano":       fold["val_ano"],
                "test_ano":      fold["test_ano"],
                "n_treino":      len(train_df),
                "n_val":         len(val_df),
                "n_test":        len(test_df),
                "models":        {},
            }

            for algo in algoritmos:
                logger.info("  Treinando %s...", algo)
                try:
                    val_probs_cal = None
                    val_classes_cal = None
                    val_df_para_calib = None

                    if algo in ALGORITMOS_ML:
                        # Para ML usa val_df apenas quando há partidas suficientes
                        val_para_fit = val_df if len(val_df) >= 50 else None
                        val_df_para_calib = val_para_fit if val_para_fit is not None else train_df.iloc[-max(50, len(train_df)//7):]
                        probs, classes, modelo, curva, val_probs_cal, val_classes_cal = treinar_fold_ml(
                            algo, features_usadas, hyperparams if isinstance(hyperparams, dict) else {},
                            train_df, val_df_para_calib,
                            test_df, target_col,
                        )
                        if fold_nome == "fold_3" and curva:
                            learning_curves[f"{algo}_fold_3"] = curva
                    elif algo in ALGORITMOS_SKLEARN:
                        probs, classes, modelo, _ = treinar_fold_sklearn(
                            algo, features_usadas, hyperparams if isinstance(hyperparams, dict) else {},
                            train_df, test_df, target_col, tipo,
                        )
                        curva = None
                    else:
                        logger.warning("Algoritmo %r não suportado — pulando.", algo)
                        continue

                    metricas = calcular_metricas_fold(
                        test_df.reset_index(drop=True), probs, classes, target_col, tipo
                    )
                    metricas["n_features"] = len(features_usadas)
                    fold_resultado["models"][algo] = metricas

                    logger.info("    logloss=%.4f  acc=%.3f  brier=%.4f",
                                metricas.get("logloss_test", 0),
                                metricas.get("accuracy_test", 0),
                                metricas.get("brier_score_test", 0))

                    # Predições do fold_3 → model_predictions
                    if fold_nome == "fold_3":
                        salvar_predicoes_fold3(
                            supabase, test_df.reset_index(drop=True),
                            probs, classes,
                            model_name_por_algo[algo], target_key,
                        )
                        # Feature importance do fold_3
                        feats_para_imp = list(dict.fromkeys([*ml.CAT_FEATURES, *features_usadas])) \
                            if algo in ALGORITMOS_ML else features_usadas
                        imp = extrair_importancia(modelo, feats_para_imp, algo)
                        if imp:
                            feature_importance[algo] = imp

                    # --- Calibração Platt e Isotonic (apenas ML com val set disponível) ---
                    if val_probs_cal is not None and val_df_para_calib is not None:
                        val_y = val_df_para_calib[target_col].values
                        for metodo in ("platt", "isotonic"):
                            try:
                                cals = cal_lib.ajustar_calibracao_np(val_probs_cal, val_y, val_classes_cal, metodo)
                                tp_cal = cal_lib.aplicar_calibracao_np(probs, cals)
                                m_cal = calcular_metricas_fold(
                                    test_df.reset_index(drop=True), tp_cal, val_classes_cal, target_col, tipo
                                )
                                fold_resultado["models"][algo][f"logloss_calibrado_{metodo}"] = m_cal["logloss_test"]
                                fold_resultado["models"][algo][f"brier_calibrado_{metodo}"] = m_cal.get("brier_score_test")
                                logger.info("    [%s] logloss_cal=%.4f", metodo, m_cal["logloss_test"])
                                if fold_nome == "fold_3":
                                    salvar_predicoes_fold3(
                                        supabase, test_df.reset_index(drop=True),
                                        tp_cal, val_classes_cal,
                                        f"{model_name_por_algo[algo]}_calibrado_{metodo}", target_key,
                                    )
                            except Exception as exc_cal:
                                logger.warning("Calibração %s falhou para %s/%s: %s", metodo, algo, fold_nome, exc_cal)

                except Exception as exc:
                    logger.error("  Erro em %s / %s: %s", algo, fold_nome, exc)
                    fold_resultado["models"][algo] = {"erro": str(exc)}

            resultados_folds.append(fold_resultado)

        # ── Métricas finais ──────────────────────────────────────────────────
        agora = datetime.now(timezone.utc).isoformat()

        metrics_final = {
            "mode":              "walk_forward_cv",
            "target":            target_key,
            "algorithms":        algoritmos,
            "model_names":       model_names,
            "folds":             resultados_folds,
            "feature_importance": feature_importance,
            "learning_curves":   learning_curves,
        }

        atualizar_status(
            supabase, config_id, "treinado",
            metrics=metrics_final,
            trained_at=agora,
            error_message=None,
        )
        logger.info("✅ Walk-forward CV concluído e métricas gravadas.")
        print(json.dumps({"config_id": config_id, "status": "treinado",
                          "model_names": model_names,
                          "folds": len(resultados_folds)}, ensure_ascii=False))

    except Exception as exc:
        logger.exception("Erro fatal: %s", exc)
        marcar_erro(supabase, config_id, str(exc))
        sys.exit(1)


if __name__ == "__main__":
    main()
