#!/usr/bin/env python3
"""Regressor de xGOT (expected goals on target -- xG ajustado pela
qualidade do próprio chute a gol, não só a chance) por equipe. Mesmo
espírito de `treinar_regressor_xg.py` (mesmo dataset, mesmas features v1,
mesmo split cronológico 80/20, só grava previsão do holdout) -- só troca
o alvo pra `xgot_home`/`xgot_away`.

Fonte do rótulo: `match_stats_fotmob.xgot` (via
`dados_historicos._anexar_xgot_por_partida`) -- achado ao investigar se
xGOT tinha ground truth real no banco: tinha, só nunca tinha sido cruzado
com `match_stats` nem usado em treino. Cobertura nas 5 ligas de elite
europeias (mesmo escopo de `montar_dataset_ml_empilhado` -- ver
CONTEXTO_PROJETO.md sobre a decisão de não alargar a função compartilhada
nesta primeira versão).

Uso:
    set SUPABASE_URL=...
    set SUPABASE_KEY=sua_service_role_key
    python treinar_regressor_xgot.py
"""

from __future__ import annotations

import os
import sys

import numpy as np
from supabase import create_client

import dados_historicos
import modelos_ml

MODEL_NAME = "catboost_xgot_regressor_v1"
FRACAO_TESTE = 0.2


def obter_env_obrigatoria(nome: str) -> str:
    valor = os.environ.get(nome)
    if not valor:
        sys.exit(f"Configure {nome} antes de rodar.")
    return valor


def rmse(previsto: np.ndarray, real: np.ndarray) -> float:
    return float(np.sqrt(np.mean((previsto - real) ** 2)))


def main() -> None:
    url = obter_env_obrigatoria("SUPABASE_URL")
    key = obter_env_obrigatoria("SUPABASE_KEY")
    supabase = create_client(url, key)

    print("Montando dataset (5 ligas de elite europeias, xGOT via match_stats_fotmob)...")
    dataset = dados_historicos.montar_dataset_ml_empilhado(supabase)
    if dataset.empty:
        sys.exit("Dataset vazio -- nada pra treinar.")

    dataset = dataset.sort_values("match_date").reset_index(drop=True)
    features = modelos_ml.FEATURES_POR_MODELO[MODEL_NAME]
    params = modelos_ml.PARAMS_DEFAULT[MODEL_NAME]

    corte = int(len(dataset) * (1 - FRACAO_TESTE))
    treino_base, teste_base = dataset.iloc[:corte], dataset.iloc[corte:]

    previsoes_xgot = []
    resultados = {}
    for lado, coluna_alvo in (("home", "xgot_home"), ("away", "xgot_away")):
        treino = treino_base.dropna(subset=[coluna_alvo])
        teste = teste_base.dropna(subset=[coluna_alvo])
        if treino.empty or teste.empty:
            print(f"[{lado}] sem dados suficientes de xGOT real pra treinar/testar -- pulando.")
            continue

        modelo, _, _ = modelos_ml.treinar_catboost_regressor(params, treino, coluna_alvo=coluna_alvo, features=features)
        previsto = modelos_ml.prever_catboost_regressor(modelo, None, teste, features=features)

        real = teste[coluna_alvo].to_numpy()
        baseline = np.full_like(real, treino[coluna_alvo].mean())
        resultados[lado] = {
            "rmse_modelo": rmse(previsto, real),
            "rmse_baseline": rmse(baseline, real),
            "n_treino": len(treino), "n_teste": len(teste),
        }

        for match_id, valor in zip(teste["match_id"], previsto):
            previsoes_xgot.append((int(match_id), lado, round(float(valor), 3)))

    if not previsoes_xgot:
        sys.exit("Nenhum lado (home/away) teve dado suficiente -- nada gravado.")

    por_match: dict[int, dict] = {}
    for match_id, lado, valor in previsoes_xgot:
        if match_id not in por_match:
            por_match[match_id] = {"match_id": match_id, "model_name": MODEL_NAME}
        por_match[match_id][f"xgot_{lado}_previsto"] = valor

    linhas = list(por_match.values())
    for i in range(0, len(linhas), 500):
        supabase.table("model_match_estimates").upsert(
            linhas[i : i + 500], on_conflict="match_id,model_name",
        ).execute()

    print(f"\n{len(linhas)} partidas de teste gravadas em model_match_estimates ({MODEL_NAME}).")
    for lado, r in resultados.items():
        melhor = "MELHOR que baseline" if r["rmse_modelo"] < r["rmse_baseline"] else "pior que baseline"
        print(f"  xGOT {lado}: RMSE modelo {r['rmse_modelo']:.3f} vs. baseline (média) {r['rmse_baseline']:.3f} -> {melhor} "
              f"(treino={r['n_treino']}, teste={r['n_teste']})")


if __name__ == "__main__":
    main()
