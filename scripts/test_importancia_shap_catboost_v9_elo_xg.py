#!/usr/bin/env python3
"""Testes de `importancia_shap_catboost_v9_elo_xg.py` com um CatBoost minúsculo em dados sintéticos.
Roda com `pytest scripts/test_importancia_shap_catboost_v9_elo_xg.py -v`."""

from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier, Pool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import importancia_shap_catboost_v9_elo_xg as m


def _modelo_sintetico(n=600, seed=0):
    rng = np.random.default_rng(seed)
    X = pd.DataFrame({"forca": rng.normal(size=n), "ruido": rng.normal(size=n), "apoio": rng.normal(size=n)})
    escore = 1.5 * X.forca + 0.5 * X.forca * X.apoio
    y = np.where(escore > 0.5, 0, np.where(escore < -0.5, 2, 1))
    modelo = CatBoostClassifier(loss_function="MultiClass", iterations=60, depth=4, random_seed=1, verbose=False, thread_count=1)
    modelo.fit(X, y)
    return modelo, X, y


def test_shap_mais_valor_base_reconstroi_a_previsao_bruta():
    modelo, X, y = _modelo_sintetico()
    sv = np.asarray(modelo.get_feature_importance(Pool(X, y), type="ShapValues"))
    bruto = modelo.predict(X, prediction_type="RawFormulaVal")
    assert np.allclose(sv.sum(axis=2), bruto, atol=1e-4)  # aditividade do SHAP, última coluna = valor-base


def test_tabela_importancia_acha_a_variavel_informativa_e_soma_um():
    modelo, X, y = _modelo_sintetico()
    tabela, sv = m.tabela_importancia(modelo, Pool(X, y), list(X.columns))
    assert sv.shape == (len(X), 3, 3)
    assert tabela.variavel.iloc[0] == "forca" and tabela.variavel.iloc[-1] == "ruido"
    assert abs(tabela.shap_pct.sum() - 1.0) < 1e-9
    assert set(["pvc", "lfc", "shap_casa", "shap_empate", "shap_fora", "shap_total"]) <= set(tabela.columns)


def test_direcao_tem_sinal_certo_para_casa_e_fora():
    modelo, X, y = _modelo_sintetico()
    feats = list(X.columns)
    _, sv = m.tabela_importancia(modelo, Pool(X, y), feats)
    d = m.direcao(sv, X, feats, ["forca"])[0]
    assert d["corr_casa"] > 0.7 and d["corr_fora"] < -0.7


def test_interacao_matriz_simetrica_sem_diagonal_e_parceiro_certo():
    modelo, X, y = _modelo_sintetico()
    feats = list(X.columns)
    principal, inter = m.matriz_interacao(modelo, Pool(X.iloc[:200], y[:200]), len(feats))
    assert inter.shape == (3, 3) and np.allclose(np.diag(inter), 0) and np.allclose(inter, inter.T, atol=1e-6)
    assert principal[feats.index("forca")] > principal[feats.index("ruido")]
    assert m.melhores_parceiros(inter, feats, "forca", 1)[0][0] == "apoio"  # a interação foi construída entre forca e apoio
    assert m.pares_mais_fortes(inter, feats, 1)[0][0] in ("forca x apoio", "forca x ruido", "ruido x apoio")


def test_estabilidade_rankings_identicos_dao_spearman_um_e_invertidos_menos_um():
    nomes = [f"v{i}" for i in range(30)]
    a = pd.DataFrame({"variavel": nomes, "shap_total": np.arange(30, 0, -1, dtype=float)})
    b = a.copy()
    c = pd.DataFrame({"variavel": nomes, "shap_total": np.arange(1, 31, dtype=float)})
    e = m.estabilidade_entre_folds({"f1": a, "f2": b, "f3": c}, top_n=5)
    assert abs(e["spearman"]["f1~f2"] - 1.0) < 1e-9 and abs(e["spearman"]["f1~f3"] + 1.0) < 1e-9
    assert e["sobreposicao_top_n"]["f1~f2"] == 5 and e["sobreposicao_top_n"]["f1~f3"] == 0
