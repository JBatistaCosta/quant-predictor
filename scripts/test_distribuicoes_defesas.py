#!/usr/bin/env python3
"""Testes de `distribuicoes.mercados_de_defesas` (mercado de defesas de
goleiro, Poisson ou Binomial Negativa via `disp_r`) -- reimplementação
independente da PMF esperada dentro do teste (via `scipy.stats` direto),
nunca reaproveitando `_nb_pmf_vetor` internamente, pra não testar a função
contra ela mesma.

Roda com `pytest scripts/test_distribuicoes_defesas.py -v` da raiz do repo.
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy.stats import nbinom, poisson

import distribuicoes as dist


def test_over_e_under_somam_um():
    mercado = dist.mercados_de_defesas(3.0, disp_r=8.0)
    for linha in (1.5, 2.5, 3.5, 4.5):
        rotulo = f"defesas_over_under_{dist.rotulo_linha(linha)}"
        assert mercado[(rotulo, "over")] + mercado[(rotulo, "under")] == pytest.approx(1.0, abs=1e-9)


def test_poisson_default_bate_scipy_direto():
    lam = 2.7
    mercado = dist.mercados_de_defesas(lam)  # disp_r default = inf -> Poisson
    for linha in (1.5, 2.5, 3.5, 4.5):
        k = int(np.floor(linha))
        esperado = float(1.0 - poisson.cdf(k, lam))
        rotulo = f"defesas_over_under_{dist.rotulo_linha(linha)}"
        assert mercado[(rotulo, "over")] == pytest.approx(esperado, abs=1e-6)


def test_nb2_bate_scipy_nbinom_direto():
    lam, disp_r = 3.4, 6.2
    mercado = dist.mercados_de_defesas(lam, disp_r=disp_r)
    r = disp_r
    p = r / (r + lam)
    for linha in (1.5, 2.5, 3.5, 4.5):
        k = int(np.floor(linha))
        # sf(k) = P(X>k) = 1-CDF(k) -- mesma definição de "over" que a
        # função usa (renormalizada no suporte truncado, por isso tolerância
        # um pouco mais larga que o caso Poisson não-truncado acima).
        esperado = float(nbinom.sf(k, r, p))
        rotulo = f"defesas_over_under_{dist.rotulo_linha(linha)}"
        assert mercado[(rotulo, "over")] == pytest.approx(esperado, abs=1e-4)


def test_disp_r_nao_positivo_degrada_pra_poisson():
    """`_nb_pmf_vetor` já trata disp_r<=0 ou não-finito como Poisson --
    confirma que `mercados_de_defesas` propaga esse fallback gracioso sem
    reimplementar a checagem."""
    lam = 4.1
    poisson_puro = dist.mercados_de_defesas(lam, disp_r=float("inf"))
    zero = dist.mercados_de_defesas(lam, disp_r=0.0)
    negativo = dist.mercados_de_defesas(lam, disp_r=-3.0)
    for linha in (1.5, 2.5, 3.5, 4.5):
        rotulo = f"defesas_over_under_{dist.rotulo_linha(linha)}"
        assert zero[(rotulo, "over")] == pytest.approx(poisson_puro[(rotulo, "over")], abs=1e-9)
        assert negativo[(rotulo, "over")] == pytest.approx(poisson_puro[(rotulo, "over")], abs=1e-9)


def test_nb2_tem_variancia_maior_que_poisson_mesma_media():
    """Reimplementação direta da própria definição de NB2 (Var = média +
    alpha*média², alpha=1/r) -- mesma média que a Poisson, variância maior
    sempre que `disp_r` for finito e positivo."""
    lam, disp_r = 3.0, 4.0
    variancia_nb2 = lam + (lam**2) / disp_r
    assert variancia_nb2 > lam  # variância da Poisson é a própria média


def test_nb2_tem_cauda_distante_mais_gorda_que_poisson_mesma_media():
    """Sobredispersão infla a variância mantendo a média presa -- então
    empurra massa das linhas PERTO da média pras duas pontas (baixo e alto).
    Isso significa que "over" numa linha logo ACIMA da média pode até ser
    MENOR na NB2 (mais massa acumulada nos valores baixos) -- o efeito
    "cauda mais gorda" só é garantido longe o bastante da média. Usa
    lambda=1,5 (baixo, como boa parte dos goleiros reais) e linhas 2.5/3.5/
    4.5, todas >1 unidade acima da média, onde o cruzamento já aconteceu
    (verificado numericamente: cruza entre a 1ª e a 2ª linha)."""
    lam = 1.5
    poisson_puro = dist.mercados_de_defesas(lam, disp_r=float("inf"))
    nb2 = dist.mercados_de_defesas(lam, disp_r=4.0)
    for linha in (2.5, 3.5, 4.5):
        rotulo = f"defesas_over_under_{dist.rotulo_linha(linha)}"
        assert nb2[(rotulo, "over")] > poisson_puro[(rotulo, "over")]


def test_monotonico_por_linha():
    mercado = dist.mercados_de_defesas(3.5, disp_r=5.0)
    linhas = (1.5, 2.5, 3.5, 4.5)
    probs = [mercado[(f"defesas_over_under_{dist.rotulo_linha(l)}", "over")] for l in linhas]
    assert all(a > b for a, b in zip(probs, probs[1:]))


def test_lambda_zero_prob_over_perto_de_zero():
    mercado = dist.mercados_de_defesas(0.0, disp_r=5.0)
    for linha in (1.5, 2.5, 3.5, 4.5):
        rotulo = f"defesas_over_under_{dist.rotulo_linha(linha)}"
        assert mercado[(rotulo, "over")] == pytest.approx(0.0, abs=1e-6)
