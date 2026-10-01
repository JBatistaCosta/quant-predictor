#!/usr/bin/env python3
"""Compara o `catboost_v9` de hoje com o MESMO catboost_v9 + Elo global por xG
(`elo_xg_home`/`elo_xg_away`/`elo_xg_diff`, ver `dados_historicos.FEATURES_V9_ELO_XG`)
no mesmo walk-forward do `walkforward_cv_v9.py`.

Por que existe: teste local de 01/10/2026 (ver CONTEXTO_PROJETO.md) -- com os 3
folds anuais (teste 2023/2024/2025, 6.473 jogos), acrescentar o Elo por xG ao v9
melhorou o Brier do 1X2 em +0,00171 +- 0,00045 (0,15496 -> 0,15325) e a perda
logarítmica em +0,0056 +- 0,0016, empatando com o Elo por xG sozinho
(-0,00013 +- 0,00053). Este script repete o experimento com as credenciais do
projeto (leitura completa do banco) pra confirmar antes de mexer em produção.

NÃO grava nada no banco, NÃO registra modelo novo em `modelos_ml.TREINADORES` e
NÃO mexe no cron diário -- só lê, treina em memória e imprime o resultado nos
logs (linhas `JSON:`).

DESENHO (igual ao walk-forward do v9, com uma diferença deliberada):
  - Folds: treino <= 2021 / val 2022 / teste 2023, depois 2022/2023/2024 e
    2023/2024/2025.
  - Treinador de produção `modelos_ml.treinar_catboost` e os mesmos
    hiperparâmetros `PARAMS_DEFAULT["catboost_v9"]`.
  - A parada antecipada olha SÓ a validação (`test_df=val_df`): o walk-forward
    original passa também o teste como segundo `eval_set`, o que deixa o teste
    participar da escolha da iteração. Aqui o teste fica totalmente de fora.
  - Variante base = `FEATURES_POR_MODELO["catboost_v9"]`; variante nova = base +
    `FEATURES_NUMERICAS_ELO_XG`.
  - Métricas por fold e agregadas: Brier do escore esperado (vitória 1, empate
    0,5), perda logarítmica, acurácia; ganho pareado da variante nova sobre a
    base com erro-padrão agrupado por time da casa.

Variáveis de ambiente obrigatórias: SUPABASE_URL, SUPABASE_KEY.
"""

from __future__ import annotations

import json
import logging
import math
import os
import sys

import numpy as np
import pandas as pd
from supabase import create_client

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dados_historicos as dh
import modelos_ml as ml

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("comparar_catboost_v9_elo_xg")

FOLDS = [
    {"nome": "fold_1", "treino_max_ano": 2021, "val_ano": 2022, "test_ano": 2023},
    {"nome": "fold_2", "treino_max_ano": 2022, "val_ano": 2023, "test_ano": 2024},
    {"nome": "fold_3", "treino_max_ano": 2023, "val_ano": 2024, "test_ano": 2025},
]
MODELO = "catboost_v9"
COLUNA_ALVO = "resultado"


def escore_esperado(probs: np.ndarray, classes: np.ndarray) -> np.ndarray:
    """P(vitória da casa) + 0,5 * P(empate), a partir das probabilidades 1X2."""
    ordem = {int(c): i for i, c in enumerate(classes)}
    return probs[:, ordem[dh.RESULTADO_HOME]] + 0.5 * probs[:, ordem[dh.RESULTADO_DRAW]]


def escore_real(y: np.ndarray) -> np.ndarray:
    return np.where(y == dh.RESULTADO_HOME, 1.0, np.where(y == dh.RESULTADO_DRAW, 0.5, 0.0))


def log_loss(probs: np.ndarray, classes: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Perda logarítmica por jogo (vetor), alinhando a coluna de cada classe."""
    ordem = {int(c): i for i, c in enumerate(classes)}
    idx = np.array([ordem[int(v)] for v in y])
    return -np.log(np.clip(probs[np.arange(len(y)), idx], 1e-9, 1.0))


def ganho_pareado(diferenca: np.ndarray, grupos: np.ndarray) -> tuple[float, float]:
    """Média de `diferenca` e erro-padrão agrupado por `grupos` (time da casa):
    jogos do mesmo time não são independentes."""
    df = pd.DataFrame({"d": diferenca, "g": grupos})
    media = df["d"].mean()
    soma = df.groupby("g")["d"].sum()
    n_g = df.groupby("g")["d"].count()
    erro = math.sqrt(float(((soma - media * n_g) ** 2).sum())) / len(df)
    return float(media), float(erro)


def treinar_e_prever(train: pd.DataFrame, val: pd.DataFrame, test: pd.DataFrame, features: list[str]):
    # test_df=val_df: a parada antecipada NÃO enxerga o teste.
    modelo, _, _ = ml.treinar_catboost(
        ml.PARAMS_DEFAULT[MODELO], train, coluna_alvo=COLUNA_ALVO, features=features, val_df=val, test_df=val
    )
    probs, classes = ml.prever_catboost(modelo, None, test, features=features)
    return probs, classes, int(modelo.get_best_iteration())


def main() -> None:
    url, key = os.environ.get("SUPABASE_URL", ""), os.environ.get("SUPABASE_KEY", "")
    if not url or not key:
        logger.error("SUPABASE_URL e SUPABASE_KEY precisam estar definidos.")
        sys.exit(1)
    supabase = create_client(url, key)

    logger.info("Montando o dataset empilhado (mesmo do walk-forward v9)...")
    dataset = dh.montar_dataset_ml_empilhado(supabase, anos_por_liga=7).reset_index(drop=True)
    if dataset.empty:
        logger.error("Dataset vazio -- abortando.")
        sys.exit(1)
    for coluna in dh.FEATURES_NUMERICAS_ELO_XG:
        if coluna not in dataset.columns:
            logger.error("Coluna %s ausente do dataset -- abortando.", coluna)
            sys.exit(1)
    logger.info("Dataset: %d partidas | cobertura Elo xG: %.1f%%", len(dataset),
                100 * dataset["elo_xg_home"].notna().mean())

    # time da casa de cada partida (pro erro-padrão agrupado) -- não vem no dataset final
    ids = [int(i) for i in dataset["match_id"].tolist()]
    casas: dict[int, int] = {}
    for inicio in range(0, len(ids), 500):
        lote = ids[inicio:inicio + 500]
        resposta = supabase.table("matches").select("id, home_team_id").in_("id", lote).execute()
        casas.update({int(r["id"]): int(r["home_team_id"]) for r in (resposta.data or [])})
    dataset["home_team_id"] = dataset["match_id"].map(casas)

    base = [f for f in ml.FEATURES_POR_MODELO[MODELO] if f in dataset.columns]
    nova = base + [f for f in dh.FEATURES_NUMERICAS_ELO_XG if f not in base]
    anos = pd.to_datetime(dataset["match_date"]).dt.year

    resultados, linhas = [], []
    for fold in FOLDS:
        train = dataset[anos <= fold["treino_max_ano"]]
        val = dataset[anos == fold["val_ano"]]
        test = dataset[anos == fold["test_ano"]]
        if train.empty or val.empty or test.empty:
            logger.warning("%s com split vazio -- pulando.", fold["nome"])
            continue
        logger.info("%s | treino=%d val=%d teste=%d", fold["nome"], len(train), len(val), len(test))
        p0, c0, it0 = treinar_e_prever(train, val, test, base)
        p1, c1, it1 = treinar_e_prever(train, val, test, nova)
        y = test[COLUNA_ALVO].to_numpy().astype(int)
        s = escore_real(y)
        e0, e1 = escore_esperado(p0, c0), escore_esperado(p1, c1)
        linha = pd.DataFrame({
            "fold": fold["nome"], "casa": test["home_team_id"].to_numpy(),
            "b0": (s - e0) ** 2, "b1": (s - e1) ** 2,
            "l0": log_loss(p0, c0, y), "l1": log_loss(p1, c1, y),
            "a0": (np.array([c0[i] for i in p0.argmax(1)]) == y).astype(float),
            "a1": (np.array([c1[i] for i in p1.argmax(1)]) == y).astype(float),
        })
        linhas.append(linha)
        g_b, ep_b = ganho_pareado((linha["b0"] - linha["b1"]).to_numpy(), linha["casa"].to_numpy())
        g_l, ep_l = ganho_pareado((linha["l0"] - linha["l1"]).to_numpy(), linha["casa"].to_numpy())
        resultado = {
            "fold": fold["nome"], "n_teste": len(test), "iteracao_otima_base": it0, "iteracao_otima_com_elo_xg": it1,
            "brier_base": float(linha["b0"].mean()), "brier_com_elo_xg": float(linha["b1"].mean()),
            "ganho_brier": g_b, "erro_padrao_brier": ep_b, "ganho_logloss": g_l, "erro_padrao_logloss": ep_l,
            "acuracia_base": float(linha["a0"].mean()), "acuracia_com_elo_xg": float(linha["a1"].mean()),
        }
        resultados.append(resultado)
        logger.info("JSON:fold:%s", json.dumps(resultado, ensure_ascii=False))

    if linhas:
        todas = pd.concat(linhas, ignore_index=True)
        g_b, ep_b = ganho_pareado((todas["b0"] - todas["b1"]).to_numpy(), todas["casa"].to_numpy())
        g_l, ep_l = ganho_pareado((todas["l0"] - todas["l1"]).to_numpy(), todas["casa"].to_numpy())
        agregado = {
            "n_teste": len(todas),
            "brier_base": float(todas["b0"].mean()), "brier_com_elo_xg": float(todas["b1"].mean()),
            "ganho_brier": g_b, "erro_padrao_brier": ep_b, "ganho_logloss": g_l, "erro_padrao_logloss": ep_l,
            "acuracia_base": float(todas["a0"].mean()), "acuracia_com_elo_xg": float(todas["a1"].mean()),
        }
        logger.info("JSON:agregado:%s", json.dumps(agregado, ensure_ascii=False))
        logger.info(
            "RESUMO: Brier %.5f -> %.5f | ganho %+.5f +- %.5f | logloss ganho %+.5f +- %.5f | acuracia %.4f -> %.4f",
            agregado["brier_base"], agregado["brier_com_elo_xg"], g_b, ep_b, g_l, ep_l,
            agregado["acuracia_base"], agregado["acuracia_com_elo_xg"],
        )


if __name__ == "__main__":
    main()
