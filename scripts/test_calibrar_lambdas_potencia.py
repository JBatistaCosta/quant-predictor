#!/usr/bin/env python3
"""Testes de scripts/calibrar_lambdas_potencia.py (sintéticos, sem rede)."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import calibrar_lambdas_potencia as c


def test_recupera_potencia_e_nivel():
    rng = np.random.default_rng(0)
    n = 20000
    lh, la = rng.uniform(0.6, 2.4, n), rng.uniform(0.6, 2.4, n)
    hg = rng.poisson(1.3 * lh ** 1.5)
    ag = rng.poisson(0.9 * la ** 1.5)
    p = c.ajustar(lh, la, hg, ag, "potencia")
    assert abs(p[2] - 1.5) < 0.05 and abs(np.exp(p[0]) - 1.3) < 0.08 and abs(np.exp(p[1]) - 0.9) < 0.08
    q = c.ajustar(lh, la, hg, ag, "nivel")
    h2, a2 = c.aplicar(p, lh, la, "potencia")
    h3, a3 = c.aplicar(q, lh, la, "nivel")
    assert c.nll_poisson(h2, hg) + c.nll_poisson(a2, ag) < c.nll_poisson(h3, hg) + c.nll_poisson(a3, ag)


def test_probabilidades_somam_um_e_simetria():
    p, tot = c.probs(np.array([1.4, 1.0]), np.array([1.4, 2.0]))
    assert np.allclose(p.sum(1), 1) and abs(p[0, 0] - p[0, 2]) < 1e-9
    assert p[1, 2] > p[1, 0] and 0 < tot[0] < 1


if __name__ == "__main__":
    test_recupera_potencia_e_nivel()
    test_probabilidades_somam_um_e_simetria()
    print("ok")
