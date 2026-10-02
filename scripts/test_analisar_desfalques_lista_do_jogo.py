#!/usr/bin/env python3
"""Testes de `analisar_desfalques_lista_do_jogo.py` com dados sintéticos pequenos.
Roda com `pytest scripts/test_analisar_desfalques_lista_do_jogo.py -v` da raiz do repo."""

from __future__ import annotations

import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analisar_desfalques_lista_do_jogo as a


def _lista(n_jogos=14, ausente_em=None, jogador_ausente="P1"):
    """Um time (id 1) com 3 jogadores titulares fixos; `jogador_ausente` some da lista nos jogos de `ausente_em`."""
    linhas = []
    for tn in range(n_jogos):
        for p in ("P1", "P2", "P3"):
            if p == jogador_ausente and ausente_em is not None and tn in ausente_em:
                continue
            linhas.append({"fotmob_player_id": p, "team_id": 1, "tn": tn, "match_id": 100 + tn, "st": 1})
    L = pd.DataFrame(linhas)
    TM = pd.DataFrame({"team_id": 1, "tn": range(n_jogos), "match_id": [100 + t for t in range(n_jogos)]})
    return L, TM


def test_regular_fora_da_lista_e_contado_so_no_jogo_em_que_some():
    L, TM = _lista(ausente_em={12})
    agg = a.regulares_fora_por_partida(L, TM).set_index("match_id")
    assert agg.loc[112, "n_fora"] == 1 and agg.loc[112, "n_reg"] == 3
    assert agg.loc[111, "n_fora"] == 0
    assert math.isclose(agg.loc[112, "peso_fora"], 1.0)            # 10 titularidades nas 10 partidas anteriores / 10


def test_quem_nao_e_regular_nao_conta_como_desfalque():
    # P1 só começou 3 das 10 partidas anteriores (< 5): ausência dele não é desfalque de regular
    L, TM = _lista(n_jogos=14)
    L = L[~((L.fotmob_player_id == "P1") & (L.tn.between(2, 8)))]
    L = L[~((L.fotmob_player_id == "P1") & (L.tn == 12))]
    agg = a.regulares_fora_por_partida(L, TM).set_index("match_id")
    assert agg.loc[112, "n_fora"] == 0


def test_variante_retrospectiva_so_conta_quem_volta_em_ate_30_jogos():
    L, TM = _lista(n_jogos=14, ausente_em={12})           # P1 volta no jogo 13
    assert a.regulares_fora_por_partida(L, TM).set_index("match_id").loc[112, "n_fora_ret"] == 1
    L2, TM2 = _lista(n_jogos=14, ausente_em={12, 13})     # P1 nunca mais aparece (saiu do clube)
    agg2 = a.regulares_fora_por_partida(L2, TM2).set_index("match_id")
    assert agg2.loc[112, "n_fora"] == 1 and agg2.loc[112, "n_fora_ret"] == 0


def test_regressao_agrupada_recupera_inclinacao_e_sinal():
    rng = np.random.default_rng(0)
    n = 4000
    x = rng.normal(size=n)
    y = -0.02 * x + rng.normal(scale=0.4, size=n)
    r = a.regressao_agrupada(y, x, rng.integers(0, 40, n))
    assert abs(r["beta"] - (-0.02 * x.std())) < 0.02 and r["n"] == n
    assert math.isclose(r["pts_elo_por_dp"], r["beta"] / a.EP_ELO_POR_PONTO)


def test_regressao_agrupada_ignora_nan_e_efeito_nulo_da_t_pequeno():
    rng = np.random.default_rng(1)
    x, y = rng.normal(size=3000), rng.normal(size=3000)
    x[:50] = np.nan
    r = a.regressao_agrupada(y, x, rng.integers(0, 30, 3000))
    assert r["n"] == 2950 and abs(r["t"]) < 3.5


def test_esperado_mercado_desvigado_e_nan_sem_odds():
    J = pd.DataFrame({"match_id": [1, 2]})
    odds = {(1, "closing", "home"): 2.0, (1, "closing", "draw"): 4.0, (1, "closing", "away"): 4.0}
    e = a.esperado_mercado(J, odds, "closing")
    # prob. implícitas 0,5/0,25/0,25 já somam 1 -> escore esperado = 0,5 + 0,5*0,25
    assert math.isclose(e[0], 0.625) and np.isnan(e[1])


def test_eventos_de_retorno_exigem_regular_ausencia_longa_e_titular_antes():
    L = pd.DataFrame({"gap": [200, 200, 100, 200], "missed": [10, 10, 10, 3], "prev_reg": [1, 0, 1, 1], "prev_bst": [0.8, 0.8, 0.8, 0.8]})
    ev = a.eventos_retorno(L)
    assert list(ev.index) == [0] and str(ev.faixa.iloc[0]) == "180-269 d"
