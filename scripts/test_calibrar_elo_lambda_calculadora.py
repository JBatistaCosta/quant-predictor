"""Testes de scripts/calibrar_elo_lambda_calculadora.py (sintéticos, sem rede)."""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import calibrar_elo_lambda_calculadora as c  # noqa: E402


def test_ajustar_k_recupera_o_k_verdadeiro():
    rng = np.random.default_rng(1)
    n = 30000
    d = rng.normal(0, 170, n)
    x = d / 400 * math.log(10)
    k = 0.6
    hg = rng.poisson(np.exp(0.4 + k * x / 2))
    ag = rng.poisson(np.exp(0.1 - k * x / 2))
    a, b, kk = c.ajustar_k(d, hg, ag)
    assert abs(kk - k) < 0.04 and abs(a - 0.4) < 0.03 and abs(b - 0.1) < 0.03
