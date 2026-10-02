#!/usr/bin/env python3
"""Testes do modelo 1X2 logit ordenado sobre o Elo por xG (`elo_xg_tres_vias.py`).
Roda com `pytest scripts/test_elo_xg_tres_vias.py -v` da raiz do repo."""

from __future__ import annotations

import math

import numpy as np

import elo_xg_tres_vias as m


def test_probabilidades_somam_um_e_ficam_entre_zero_e_um():
    rc = np.array([1400.0, 1500.0, 1700.0, 1900.0])
    rf = np.array([1900.0, 1500.0, 1500.0, 1300.0])
    P = m.probabilidades_1x2(rc, rf, 0)
    assert P.shape == (4, 3)
    assert np.allclose(P.sum(axis=1), 1.0)
    assert ((P > 0) & (P < 1)).all()


def test_mais_forca_do_mandante_aumenta_a_vitoria_da_casa_e_reduz_a_do_visitante():
    rf = 1500.0
    P = np.vstack([m.probabilidades_1x2(rc, rf, 0)[0] for rc in (1300.0, 1500.0, 1700.0)])
    assert P[0, m.CASA] < P[1, m.CASA] < P[2, m.CASA]
    assert P[0, m.FORA] > P[1, m.FORA] > P[2, m.FORA]


def test_campo_neutro_e_simetrico_ao_trocar_os_times():
    a = m.probabilidades_1x2(1650.0, 1500.0, 1)[0]
    b = m.probabilidades_1x2(1500.0, 1650.0, 1)[0]
    assert math.isclose(a[m.CASA], b[m.FORA], rel_tol=1e-12)
    assert math.isclose(a[m.FORA], b[m.CASA], rel_tol=1e-12)
    assert math.isclose(a[m.EMPATE], b[m.EMPATE], rel_tol=1e-12)


def test_times_iguais_em_campo_neutro_dao_vitoria_igual_e_empate_pela_largura_t():
    p = m.PARAMS_PADRAO
    P = m.probabilidades_1x2(1500.0, 1500.0, 1)[0]
    assert math.isclose(P[m.CASA], P[m.FORA], rel_tol=1e-12)
    esperado_empate = 1 - 2 / (1 + math.exp(p["t"]))
    assert math.isclose(P[m.EMPATE], esperado_empate, rel_tol=1e-9)
    assert 0.28 < P[m.EMPATE] < 0.31


def test_vantagem_de_casa_so_vale_fora_de_campo_neutro():
    em_casa = m.probabilidades_1x2(1500.0, 1500.0, 0)[0]
    neutro = m.probabilidades_1x2(1500.0, 1500.0, 1)[0]
    assert em_casa[m.CASA] > neutro[m.CASA] and em_casa[m.FORA] < neutro[m.FORA]


def test_empate_e_maximo_com_times_equilibrados_e_cai_com_a_diferenca_de_forca():
    empates = [m.probabilidades_1x2(1500.0 + d, 1500.0, 1)[0][m.EMPATE] for d in (0, 100, 200, 400)]
    assert empates == sorted(empates, reverse=True)


def test_o_empate_nunca_e_o_palpite_com_os_parametros_padrao():
    rc = np.linspace(1200, 1900, 50)
    P = m.probabilidades_1x2(rc, np.full(50, 1500.0), 1)
    assert (P.argmax(axis=1) != m.EMPATE).all()


def test_ajuste_recupera_os_parametros_de_dados_simulados():
    rng = np.random.default_rng(7)
    n = 60000
    verdadeiro = {"a": 0.9, "h": 70.0, "t": 0.5}
    rc, rf = rng.normal(1500, 120, n), rng.normal(1500, 120, n)
    neu = (rng.random(n) < 0.1).astype(float)
    P = m.probabilidades_1x2(rc, rf, neu, verdadeiro)
    u = rng.random(n)
    y = np.where(u < P[:, 0], m.CASA, np.where(u < P[:, 0] + P[:, 1], m.EMPATE, m.FORA))
    est, perda = m.ajustar(rc, rf, neu, y)
    assert abs(est["a"] - verdadeiro["a"]) < 0.06
    assert abs(est["h"] - verdadeiro["h"]) < 8.0
    assert abs(est["t"] - verdadeiro["t"]) < 0.04
    assert est["t"] > 0 and perda < math.log(3)


def test_metricas_de_previsao_perfeita_e_de_climatologia():
    y = np.array([0, 1, 2, 0, 0, 2])
    perfeita = np.eye(3)[y] * 0.98 + 0.01
    assert m.rps(perfeita, y) < 0.001
    assert m.perda_logaritmica(perfeita, y) < 0.03
    assert m.avaliar(perfeita, y, [0.5, 1 / 6, 1 / 3])["acuracia"] == 1.0
    freq = np.array([0.5, 1 / 6, 1 / 3])
    clim = np.tile(freq, (len(y), 1))
    assert abs(m.informacao_ganha_bits(clim, y, freq)) < 1e-9  # sem informação além da frequência-base


def test_rps_confere_com_um_exemplo_feito_a_mao():
    P = np.array([[0.5, 0.3, 0.2]])
    # real = empate: acumulado previsto [0.5, 0.8], real [0, 1] -> (0.25 + 0.04) / 2
    assert math.isclose(m.rps(P, [1]), 0.145, rel_tol=1e-12)


def test_auc_de_scores_que_separam_perfeitamente_e_de_scores_constantes():
    assert m.auc_um_contra_todos([0.1, 0.2, 0.8, 0.9], [0, 0, 1, 1]) == 1.0
    assert m.auc_um_contra_todos([0.5, 0.5, 0.5, 0.5], [0, 0, 1, 1]) == 0.5
    assert m.auc_um_contra_todos([0.9, 0.8, 0.2, 0.1], [0, 0, 1, 1]) == 0.0


def test_erro_de_calibracao_zero_para_probabilidades_iguais_a_frequencia():
    p = np.array([0.25] * 4 + [0.75] * 4)
    t = np.array([1, 0, 0, 0, 1, 1, 1, 0], dtype=float)
    assert m.erro_de_calibracao(p, t) < 1e-12
