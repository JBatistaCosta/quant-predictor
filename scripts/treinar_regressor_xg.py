#!/usr/bin/env python3
"""Regressor de gols esperados (xG) por equipe -- modelo que prevê um valor
CONTÍNUO (não uma classe/probabilidade como os outros 57 modelos do Model
Benchmarking). Reaproveita o MESMO dataset "Feature Stacked"
(`dados_historicos.montar_dataset_ml_empilhado`, 5 ligas de elite
europeias) -- só troca o alvo pra `xg_home`/`xg_away`, que o dataset já
traz embutido (via `match_stats.xg`, Understat) pra alimentar a feature de
forma de xG.

Treina DUAS versões por execução (mesmo dataset, features diferentes,
gravadas como linhas separadas em `model_match_estimates` -- PK
`match_id,model_name` já suporta as duas coexistindo sem conflito):
  - v1: features base (elo/forma/xG/liga).
  - v2: v1 + força do XI titular na data da partida (`titular_rating_home`/
    `_away`, `titular_valor_mercado_home`/`_away` + diferenciais -- ver
    `FEATURES_XG_XI_V2` em dados_historicos.py). Pedido do usuário: usar o
    XI titular (real quando a escalação já saiu, previsto -- `xi_previsto`
    -- quando ainda não saiu, ver `obter_titular_atual`) como sinal de
    força ofensiva/defensiva esperada pra prever xG.

Cobertura: só as 5 ligas de elite europeias, mesma limitação de
`montar_dataset_ml_empilhado` -- NÃO cobre Brasileirão/Libertadores ainda,
mesmo esses tendo xG real via FotMob (`match_stats_fotmob.xg`), porque
isso exigiria alargar o escopo de liga da função compartilhada (usada
pelos 57 modelos de classificação também) -- mudança maior, fora do
escopo deste script.

Split cronológico (não por temporada como os outros scripts): últimos 20%
das partidas por data viram teste (holdout de verdade), resto vira treino
-- só os jogos de TESTE entram em `model_match_estimates` (mesma
disciplina do `modelo_dixon_coles.py`: nunca salvar previsão em cima de
dado que o próprio modelo já viu no treino).

Uso:
    set SUPABASE_URL=...
    set SUPABASE_KEY=sua_service_role_key
    python treinar_regressor_xg.py
"""

from __future__ import annotations

import os
import sys

import numpy as np
from supabase import create_client

import dados_historicos
import modelos_ml

MODEL_NAMES = ["catboost_xg_regressor_v1", "catboost_xg_regressor_v2"]
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

    print("Montando dataset (5 ligas de elite europeias, xG via match_stats/Understat)...")
    dataset = dados_historicos.montar_dataset_ml_empilhado(supabase)
    if dataset.empty:
        sys.exit("Dataset vazio -- nada pra treinar.")

    dataset = dataset.sort_values("match_date").reset_index(drop=True)
    corte = int(len(dataset) * (1 - FRACAO_TESTE))
    treino_base, teste_base = dataset.iloc[:corte], dataset.iloc[corte:]

    for model_name in MODEL_NAMES:
        features = modelos_ml.FEATURES_POR_MODELO[model_name]
        params = modelos_ml.PARAMS_DEFAULT[model_name]

        previsoes_xg = []
        resultados = {}
        for lado, coluna_alvo in (("home", "xg_home"), ("away", "xg_away")):
            treino = treino_base.dropna(subset=[coluna_alvo])
            teste = teste_base.dropna(subset=[coluna_alvo])
            if treino.empty or teste.empty:
                print(f"[{model_name}/{lado}] sem dados suficientes de xG real pra treinar/testar -- pulando.")
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
                previsoes_xg.append((int(match_id), lado, round(float(valor), 3)))

        if not previsoes_xg:
            print(f"[{model_name}] nenhum lado (home/away) teve dado suficiente -- nada gravado.")
            continue

        por_match: dict[int, dict] = {}
        for match_id, lado, valor in previsoes_xg:
            if match_id not in por_match:
                por_match[match_id] = {"match_id": match_id, "model_name": model_name}
            por_match[match_id][f"xg_{lado}_previsto"] = valor

        linhas = list(por_match.values())
        for i in range(0, len(linhas), 500):
            supabase.table("model_match_estimates").upsert(
                linhas[i : i + 500], on_conflict="match_id,model_name",
            ).execute()

        print(f"\n{len(linhas)} partidas de teste gravadas em model_match_estimates ({model_name}).")
        for lado, r in resultados.items():
            melhor = "MELHOR que baseline" if r["rmse_modelo"] < r["rmse_baseline"] else "pior que baseline"
            print(f"  xG {lado}: RMSE modelo {r['rmse_modelo']:.3f} vs. baseline (média) {r['rmse_baseline']:.3f} -> {melhor} "
                  f"(treino={r['n_treino']}, teste={r['n_teste']})")


if __name__ == "__main__":
    main()
