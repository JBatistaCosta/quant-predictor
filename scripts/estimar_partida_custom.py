#!/usr/bin/env python3
"""Estimativa sob demanda de um modelo customizado pra UMA partida específica.

Disparado pelo botão "Estimar com modelo personalizado" na página do jogo
(src/pages/AnaliseEstatisticaJogo.jsx). Diferente de treinar_modelo_custom.py/
treinar_modelo_custom_wf.py (que treinam e fazem backtest histórico), este
script NÃO faz split cronológico treino/teste pra avaliação -- ele retreina
o modelo escolhido em TODO o histórico disponível no escopo da config
(exceto a própria partida-alvo) e prevê só essa partida. Mesmo padrão já
usado pelo pipeline principal (`rodar_predicoes.py`: "o modelo final que
prevê as fixtures do dia é sempre refeito no dataset INTEIRO"), aqui restrito
a UM modelo (config_id) e UMA partida (match_id) escolhidos pelo usuário.

Suporta tanto um algoritmo único (config modo simples, ou um dos algoritmos
de uma config walk_forward_cv) quanto um grupo de stacking definido na
config (meta-modelo LogisticRegression sobre os algoritmos do grupo, mesmo
padrão fit-no-val de treinar_modelo_custom_wf.py, com um único split 85/15
em vez de walk-forward de 3 folds -- não faz sentido walk-forward pra uma
previsão pontual).

Variáveis de ambiente:
  SUPABASE_URL, SUPABASE_KEY   — acesso ao banco (service_role)
  REQUEST_ID                   — UUID da linha em custom_model_ondemand_predictions

Erros fatais atualizam custom_model_ondemand_predictions.status='erro' e
error_message com a mensagem antes de sair com exit code 1.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression as LR
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.impute import SimpleImputer
from supabase import create_client

sys.path.insert(0, os.path.dirname(__file__))
import dados_historicos as dh
import modelos_ml as ml
from treinar_modelo_custom import TARGETS, _TARGET_PRED_META, _migrar_features, ALGORITMOS_ML, ALGORITMOS_SKLEARN
from treinar_modelo_custom_wf import montar_meta_features

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("estimar_partida_custom")

# Mínimo de partidas no pool de treino (histórico do escopo da config, sem
# contar a partida-alvo) pra considerar a estimativa confiável o bastante
# pra calcular. Abaixo disso, mais vale errar cedo com uma mensagem clara do
# que devolver uma probabilidade baseada em ruído.
MIN_PARTIDAS_TREINO = 100


def criar_supabase():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])


def atualizar_status(supabase, request_id: str, status: str, **extras):
    supabase.table("custom_model_ondemand_predictions").update({"status": status, **extras}).eq("id", request_id).execute()


def marcar_erro(supabase, request_id: str, msg: str):
    try:
        atualizar_status(supabase, request_id, "erro", error_message=msg[:2000])
    except Exception as e:
        logger.error("Falha ao gravar erro no Supabase: %s", e)


# ---------------------------------------------------------------------------
# Treino + predição (algoritmo único, sem avaliação de métricas -- só
# treina uma vez e prevê nos dataframes pedidos)
# ---------------------------------------------------------------------------

def _treinar_e_prever(
    algoritmo: str,
    features: list[str],
    hyperparameters: dict | None,
    train_df: pd.DataFrame,
    val_df: pd.DataFrame | None,
    dfs_para_prever: dict[str, pd.DataFrame],
    target_col: str,
    tipo: str,
) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Treina `algoritmo` uma vez em `train_df` (usando `val_df` pra early
    stopping quando aplicável) e prevê em cada df de `dfs_para_prever`.
    Retorna {nome: (probs, classes)}."""
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
        return {nome: prever_fn(modelo, extra, df, feats) for nome, df in dfs_para_prever.items()}

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
        return {
            nome: (pipe.predict_proba(df[features_num].values), pipe.named_steps["clf"].classes_)
            for nome, df in dfs_para_prever.items()
        }

    if algoritmo == "dixon_coles":
        raise NotImplementedError(
            "Dixon-Coles customizado ainda não implementado — "
            "use catboost/xgboost/lightgbm/logistic_regression/random_forest."
        )

    raise ValueError(f"Algoritmo desconhecido: {algoritmo!r}")


def _split_treino_val(pool_treino: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    """Split cronológico único: últimos 15% do pool viram val (early
    stopping dos algoritmos de árvore + treino do meta-modelo de stacking).
    Sem val quando o pool é pequeno demais pra valer a pena reservar parte
    dele (mesmo limiar de treinar_via_ml em treinar_modelo_custom.py)."""
    if len(pool_treino) < 200:
        return pool_treino, None
    val_n = max(50, int(len(pool_treino) * 0.15))
    return pool_treino.iloc[:-val_n].reset_index(drop=True), pool_treino.iloc[-val_n:].reset_index(drop=True)


def estimar_algoritmo_unico(
    algoritmo: str, features: list[str], hyperparameters: dict | None,
    pool_treino: pd.DataFrame, linha_alvo: pd.DataFrame, target_col: str, tipo: str,
) -> tuple[np.ndarray, np.ndarray]:
    train_fit, val_fit = _split_treino_val(pool_treino)
    preds = _treinar_e_prever(algoritmo, features, hyperparameters, train_fit, val_fit, {"alvo": linha_alvo}, target_col, tipo)
    return preds["alvo"]


def estimar_stacking(
    algos_grupo: list[str], features: list[str], hyperparameters: dict | None,
    pool_treino: pd.DataFrame, linha_alvo: pd.DataFrame, target_col: str, tipo: str,
) -> tuple[np.ndarray, np.ndarray]:
    train_fit, val_fit = _split_treino_val(pool_treino)
    if val_fit is None:
        raise ValueError(
            f"Pool de treino pequeno demais pra separar um val set de stacking "
            f"({len(pool_treino)} partidas, mínimo 200)."
        )

    probs_val_por_algo: dict[str, np.ndarray] = {}
    classes_val_por_algo: dict[str, np.ndarray] = {}
    probs_alvo_por_algo: dict[str, np.ndarray] = {}
    classes_alvo_por_algo: dict[str, np.ndarray] = {}
    for algo in algos_grupo:
        preds = _treinar_e_prever(
            algo, features, hyperparameters, train_fit, val_fit,
            {"val": val_fit, "alvo": linha_alvo}, target_col, tipo,
        )
        probs_val_por_algo[algo], classes_val_por_algo[algo] = preds["val"]
        probs_alvo_por_algo[algo], classes_alvo_por_algo[algo] = preds["alvo"]

    classes_sorted = np.sort(classes_val_por_algo[algos_grupo[0]])
    meta_X_val = montar_meta_features(probs_val_por_algo, classes_val_por_algo, algos_grupo, classes_sorted)
    meta_y_val = val_fit[target_col].values
    meta_modelo = ml.treinar_stacking_v9(meta_X_val, meta_y_val)

    meta_X_alvo = montar_meta_features(probs_alvo_por_algo, classes_alvo_por_algo, algos_grupo, classes_sorted)
    return ml.prever_stacking_v9(meta_modelo, meta_X_alvo)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    request_id = os.environ.get("REQUEST_ID", "").strip()
    if not request_id:
        logger.error("REQUEST_ID não definido.")
        sys.exit(1)

    supabase = criar_supabase()
    atualizar_status(supabase, request_id, "processando")

    try:
        req_resp = supabase.table("custom_model_ondemand_predictions").select("*").eq("id", request_id).single().execute()
        if not req_resp.data:
            raise ValueError(f"Requisição {request_id!r} não encontrada.")
        req = req_resp.data
        config_id = req["config_id"]
        match_id = req["match_id"]
        algoritmo_escolhido = req["algorithm"]

        cfg_resp = supabase.table("custom_model_configs").select("*").eq("id", config_id).single().execute()
        if not cfg_resp.data:
            raise ValueError(f"Configuração {config_id!r} não encontrada.")
        cfg = cfg_resp.data

        features_req = cfg["features"] or []
        target_key = cfg.get("target") or "1x2"
        hyperparameters = cfg.get("hyperparameters") or {}
        todas_ligas = bool(cfg.get("todas_ligas"))
        league_ids = cfg.get("league_ids") or None
        seasons = cfg.get("seasons") or None
        stacking_groups = cfg.get("stacking_groups") or []
        algoritmos_validos = cfg.get("algorithms") or ([cfg["algorithm"]] if cfg.get("algorithm") else [])

        if not features_req:
            raise ValueError("Configuração sem features selecionadas.")
        target_info = TARGETS.get(target_key)
        if not target_info:
            raise ValueError(f"Target {target_key!r} não suportado.")
        target_col = target_info["coluna"]
        tipo = target_info["tipo"]

        eh_stacking = algoritmo_escolhido.startswith("stacking:")
        algos_do_grupo: list[str] = []
        if eh_stacking:
            nome_grupo = algoritmo_escolhido[len("stacking:"):]
            grupo = next((g for g in stacking_groups if g.get("name") == nome_grupo), None)
            if not grupo:
                raise ValueError(f"Grupo de stacking {nome_grupo!r} não existe nesta configuração.")
            algos_do_grupo = [a for a in (grupo.get("algorithms") or []) if a in algoritmos_validos]
            if len(algos_do_grupo) < 2:
                raise ValueError(f"Grupo de stacking {nome_grupo!r} tem menos de 2 algoritmos válidos.")
        elif algoritmo_escolhido not in algoritmos_validos:
            raise ValueError(f"Algoritmo {algoritmo_escolhido!r} não faz parte desta configuração.")

        logger.info(
            "Config: target=%s, algoritmo=%s, %d features, match_id=%s",
            target_key, algoritmo_escolhido, len(features_req), match_id,
        )

        # Monta o dataset com o histórico do escopo da config + a partida-alvo
        # (mesmo pipeline de features de montar_dataset_ml_empilhado -- ver
        # docstring do parâmetro match_id_extra em dados_historicos.py).
        dataset = dh.montar_dataset_ml_empilhado(
            supabase,
            anos_por_liga=None if (todas_ligas or seasons) else 6,
            todas_as_ligas=todas_ligas,
            league_ids_manual=league_ids,
            seasons=seasons,
            match_id_extra=match_id,
        )
        if dataset.empty:
            raise RuntimeError("Dataset vazio — verifique o escopo de ligas/temporadas da configuração.")

        features_usadas = _migrar_features(features_req)
        if target_col == "resultado_btts" and "resultado_btts" not in dataset.columns:
            if "home_goals" in dataset.columns and "away_goals" in dataset.columns:
                dataset["resultado_btts"] = ((dataset["home_goals"] > 0) & (dataset["away_goals"] > 0)).astype(int)

        features_disponiveis = set(dataset.columns)
        faltando = [f for f in features_usadas if f not in features_disponiveis]
        if faltando:
            logger.warning("Features ignoradas (não disponíveis no dataset): %s", faltando)
            features_usadas = [f for f in features_usadas if f in features_disponiveis]
        if not features_usadas:
            raise RuntimeError("Nenhuma feature válida disponível no dataset.")

        cols_necessarias = list(dict.fromkeys(
            ml.CAT_FEATURES + features_usadas + [target_col, "match_date", "match_id"]
        ))
        cols_presentes = [c for c in cols_necessarias if c in dataset.columns]
        dataset = dataset[cols_presentes].copy()

        linha_alvo = dataset[dataset["match_id"] == match_id].reset_index(drop=True)
        if linha_alvo.empty:
            raise RuntimeError(
                f"Partida match_id={match_id} não apareceu no dataset final — "
                "provavelmente fora do escopo de ligas/temporadas da configuração."
            )
        pool_treino = dataset[dataset["match_id"] != match_id].dropna(subset=[target_col]).reset_index(drop=True)

        if len(pool_treino) < MIN_PARTIDAS_TREINO:
            raise RuntimeError(
                f"Histórico de treino insuficiente pro escopo da configuração "
                f"({len(pool_treino)} partidas, mínimo {MIN_PARTIDAS_TREINO})."
            )

        logger.info("Pool de treino: %d partidas | prevendo match_id=%s", len(pool_treino), match_id)

        if eh_stacking:
            probabilidades, classes_final = estimar_stacking(
                algos_do_grupo, features_usadas, hyperparameters, pool_treino, linha_alvo, target_col, tipo,
            )
        else:
            probabilidades, classes_final = estimar_algoritmo_unico(
                algoritmo_escolhido, features_usadas, hyperparameters, pool_treino, linha_alvo, target_col, tipo,
            )

        meta = _TARGET_PRED_META[target_key]
        class_to_sel = meta["class_to_sel"]
        probs_dict: dict[str, float] = {}
        for idx, cls in enumerate(classes_final):
            sel = class_to_sel.get(int(cls))
            if sel is not None:
                probs_dict[sel] = round(float(probabilidades[0, idx]), 6)

        soma = sum(probs_dict.values())
        if soma <= 0:
            raise RuntimeError("Probabilidades calculadas somam zero — resultado inválido.")
        fair_odds = {sel: (round(1.0 / p, 3) if p > 0 else None) for sel, p in probs_dict.items()}

        atualizar_status(
            supabase, request_id, "concluido",
            probabilities=probs_dict, fair_odds=fair_odds,
            n_treino=len(pool_treino),
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        logger.info("✅ Estimativa concluída: %s", probs_dict)

    except Exception as exc:
        logger.exception("Erro na estimativa sob demanda: %s", exc)
        marcar_erro(supabase, request_id, str(exc))
        sys.exit(1)


if __name__ == "__main__":
    main()
