"""
Treina um modelo personalizado a partir de uma configuração em custom_model_configs.

Carrega o dataset completo via montar_dataset_ml_empilhado, restringe às features
escolhidas pelo usuário, treina com o algoritmo especificado (split 80/20 por data),
avalia com log-loss/accuracy/Brier Score e salva os resultados no banco.

Uso:
    SUPABASE_URL=... SUPABASE_KEY=... python3 treinar_modelo_custom.py --config-id <UUID>
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss
from supabase import create_client

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from dados_historicos import CAT_FEATURES, montar_dataset_ml_empilhado
from modelos_ml import (
    treinar_catboost,
    treinar_lightgbm,
    treinar_mlp,
    treinar_xgboost,
    prever_catboost,
    prever_lightgbm,
    prever_mlp,
    prever_xgboost,
)

SUPABASE_URL = (os.environ.get("SUPABASE_URL") or "").strip()
SUPABASE_KEY = (os.environ.get("SUPABASE_KEY") or "").strip()

PARAMS_DEFAULT_CUSTOM = {
    "catboost":  {"depth": 6, "learning_rate": 0.05},
    "xgboost":   {"max_depth": 4, "learning_rate": 0.08},
    "lightgbm":  {"num_leaves": 15, "learning_rate": 0.1},
    "mlp":       {},
}

TREINADORES_CUSTOM = {
    "catboost":  (treinar_catboost,  prever_catboost),
    "xgboost":   (treinar_xgboost,   prever_xgboost),
    "lightgbm":  (treinar_lightgbm,  prever_lightgbm),
    "mlp":       (treinar_mlp,       prever_mlp),
}


def _atualizar_config(supabase, config_id: str, payload: dict):
    supabase.table("custom_model_configs").update(payload).eq("id", config_id).execute()


def _brier_multiclasse(y_true, probs, classes):
    total = 0.0
    for i, cls in enumerate(classes):
        y_bin = (y_true == cls).astype(int)
        total += brier_score_loss(y_bin, probs[:, i])
    return total / len(classes)


def treinar(config_id: str):
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    resp = supabase.table("custom_model_configs").select("*").eq("id", config_id).single().execute()
    config = resp.data
    if not config:
        raise ValueError(f"Configuração {config_id} não encontrada.")

    algoritmo = config["algorithm"]
    features_config = list(config["features"] or [])
    nome = config["name"]

    # liga é sempre incluída (CAT_FEATURES = ['liga'])
    features_uso = [f for f in features_config if f != "liga"] + ["liga"]
    print(f"[treinar_custom] '{nome}' | algoritmo={algoritmo} | {len(features_uso)} features")

    _atualizar_config(supabase, config_id, {"status": "treinando"})

    print("[treinar_custom] Carregando dataset...")
    dataset = montar_dataset_ml_empilhado(supabase)
    if dataset.empty:
        raise RuntimeError("Dataset vazio — verifique o banco de dados.")

    colunas_faltando = [f for f in features_uso if f not in dataset.columns]
    if colunas_faltando:
        print(f"[treinar_custom] AVISO: colunas ausentes no dataset (NaN): {colunas_faltando}")
        for col in colunas_faltando:
            dataset[col] = float("nan")

    # Split 80/20 por data (sem vazamento: treino sempre antes do teste)
    dataset = dataset.sort_values("match_date").reset_index(drop=True)
    corte = int(len(dataset) * 0.8)
    train_df = dataset.iloc[:corte].copy()
    test_df  = dataset.iloc[corte:].copy()
    print(f"[treinar_custom] Treino: {len(train_df)} | Teste: {len(test_df)}")

    fn_treinar, fn_prever = TREINADORES_CUSTOM[algoritmo]
    params = PARAMS_DEFAULT_CUSTOM[algoritmo]

    print(f"[treinar_custom] Treinando {algoritmo}...")
    modelo, extra, _ = fn_treinar(params, train_df, features=features_uso)

    print("[treinar_custom] Avaliando no conjunto de teste...")
    probs, classes = fn_prever(modelo, extra, test_df, features=features_uso)
    y_true = test_df["resultado"].values

    ll    = log_loss(y_true, probs, labels=classes)
    acc   = accuracy_score(y_true, classes[np.argmax(probs, axis=1)])
    brier = _brier_multiclasse(y_true, probs, classes)

    # Frequências de classe no teste (baseline naive)
    freq = {str(cls): float((y_true == cls).mean()) for cls in classes}
    probs_naive = np.array([[freq[str(c)] for c in classes]] * len(y_true))
    ll_naive = log_loss(y_true, probs_naive, labels=classes)

    metrics = {
        "log_loss":       round(ll, 5),
        "accuracy":       round(acc, 4),
        "brier_score":    round(brier, 5),
        "log_loss_naive": round(ll_naive, 5),
        "n_treino":       len(train_df),
        "n_teste":        len(test_df),
        "n_features":     len(features_uso),
        "classes":        [str(c) for c in classes],
        "freq_classes_teste": freq,
    }
    print(f"[treinar_custom] log_loss={ll:.4f} (naive={ll_naive:.4f}) | acc={acc:.3f} | brier={brier:.4f}")

    model_key = f"custom_{config_id[:8]}_{algoritmo}"
    _atualizar_config(supabase, config_id, {
        "status":     "concluido",
        "metrics":    metrics,
        "model_key":  model_key,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "error_message": None,
    })
    print(f"[treinar_custom] Concluído. model_key={model_key}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-id", required=True, help="UUID da configuração em custom_model_configs")
    args = parser.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERRO: variáveis SUPABASE_URL e SUPABASE_KEY são obrigatórias.", file=sys.stderr)
        sys.exit(1)

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    try:
        treinar(args.config_id)
    except Exception as exc:
        msg = traceback.format_exc()
        print(f"[treinar_custom] ERRO:\n{msg}", file=sys.stderr)
        try:
            supabase.table("custom_model_configs").update({
                "status": "erro",
                "error_message": str(exc)[:2000],
            }).eq("id", args.config_id).execute()
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
