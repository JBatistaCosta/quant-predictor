"""Testes das funções puras de scripts/analisar_placares_simulados.py (sem rede)."""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analisar_placares_simulados as ap  # noqa: E402


def test_poisson_soma_um_e_matriz_independente():
    assert abs(sum(ap.poisson(1.4)) - 1.0) < 1e-12
    m = ap.matriz_poisson(1.5, 1.2)
    assert abs(sum(sum(l) for l in m) - 1.0) < 1e-12
    assert abs(m[2][1] - ap.poisson(1.5)[2] * ap.poisson(1.2)[1]) < 1e-15           # independência: produto das marginais


def test_matriz_da_simulacao_e_1x2_e_over():
    placares = [[0, 0, 10], [1, 0, 30], [1, 1, 20], [0, 2, 15], [3, 1, 25]]          # 100 simulações
    m = ap.matriz_sim(placares, 100)
    assert abs(sum(sum(l) for l in m) - 1.0) < 1e-12
    (h, d, a), over = ap.p1x2_e_over(m)
    assert abs(h - 0.55) < 1e-12 and abs(d - 0.30) < 1e-12 and abs(a - 0.15) < 1e-12
    assert abs(over - 0.25) < 1e-12                                                  # só 3-1 (4 gols) tem 3 gols ou mais; 0-2 e 1-1 têm 2
    ms = ap.matriz_sim(placares, 100, alfa=0.5)                                      # com suavização, nenhuma célula fica em zero
    assert abs(sum(sum(l) for l in ms) - 1.0) < 1e-12 and min(min(l) for l in ms) > 0


def test_perdas_e_ic():
    p, br, po, bo = ap.perdas([0.5, 0.3, 0.2], 0.6, 0, True)
    assert abs(p - (-math.log(0.5))) < 1e-12 and abs(po - (-math.log(0.6))) < 1e-12
    assert abs(br - (0.25 + 0.09 + 0.04)) < 1e-12 and abs(bo - 0.16) < 1e-12
    m, lo, hi = ap.ic([1.0] * 50)
    assert m == lo == hi == 1.0
