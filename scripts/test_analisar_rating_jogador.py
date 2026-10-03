#!/usr/bin/env python3
"""Testes das funções puras de `analisar_rating_jogador.py`. Roda com
`pytest scripts/test_analisar_rating_jogador.py -v` da raiz do repo."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

import analisar_rating_jogador as ar


def test_simular_segue_a_formula_do_rating():
    antes, final = ar.simular(np.array([1, 1]), np.array([7.8, 7.8]))
    assert antes[0] == 1500.0
    r1 = 1500 + 20 * ((7.8 - 6.8) / 3)
    assert math.isclose(antes[1], r1)
    r2_ = r1 + 20 * ((7.8 - (6.8 + (r1 - 1500) / 500)) / 3)
    assert math.isclose(final[1], r2_)


def test_simular_converge_para_o_equilibrio_do_indice():
    # índice constante 7,8 -> equilíbrio 1500 + 500 * (7,8 - 6,8) = 2000
    _, final = ar.simular(np.array([1] * 2000), np.full(2000, 7.8))
    assert abs(final[1] - 2000) < 1


def test_simular_mantem_jogadores_separados():
    antes, final = ar.simular(np.array([1, 2, 1]), np.array([8.0, 6.0, 8.0]))
    assert antes[1] == 1500.0          # jogador 2 não herda o rating do jogador 1
    assert antes[2] > 1500 and final[1] > 1500 > final[2]


def _stats(**k):
    base = dict(rating=[7.0], goals=[0], assists=[0], xg=[np.nan], xa=[0.0], chances_created=[0])
    base.update(k)
    return pd.DataFrame(base)


def test_indice_partida_bonus_e_limites():
    nota, bonus = ar.indice_partida(_stats(rating=[7.0], goals=[5], assists=[1], xg=[1.0], xa=[0.3], chances_created=[2]))
    assert nota[0] == 7.0
    esperado = 3 * 0.15 + 1 * 0.10 + 1.0 * 0.20 + min(0.3 + 2 * 0.05, 1) * 0.25   # gols-xG = 4, limitado a +1
    assert math.isclose(bonus[0], esperado)


def test_indice_partida_sem_nota_usa_neutra_e_sem_xg_nao_da_bonus_de_finalizacao():
    nota, bonus = ar.indice_partida(_stats(rating=[np.nan], goals=[2]))
    assert nota[0] == 6.8
    assert math.isclose(bonus[0], 2 * 0.15)


def test_baselines_nao_olham_o_futuro():
    b = ar.baselines_passado(np.array([1, 1, 1]), np.array([8.0, 6.0, 7.0]))
    assert b.n_prev.tolist() == [0, 1, 2]
    assert math.isnan(b.media_encolhida[0]) and math.isnan(b.ewma[0])
    assert math.isclose(b.media_encolhida[1], (8.0 + 10 * 6.8) / 11)
    assert math.isclose(b.ewma[1], 8.0)
    assert math.isclose(b.ewma[2], 0.9 * 8.0 + 0.1 * 6.0)


def test_grupo_posicao():
    assert ar.grupo_posicao(11) == 0 and ar.grupo_posicao(34) == 1 and ar.grupo_posicao(64) == 2 and ar.grupo_posicao(115) == 3
    assert math.isnan(ar.grupo_posicao(0)) and math.isnan(ar.grupo_posicao(None))


def test_r2_recupera_relacao_linear_e_ignora_nan():
    rng = np.random.default_rng(0)
    x = rng.normal(size=5000)
    y = 2 * x + rng.normal(scale=0.1, size=5000)
    x2 = x.copy()
    x2[:10] = np.nan
    r, b = ar.r2(y, [x2])
    assert r > 0.99 and abs(b[1] - 2) < 0.02


def test_persistencia_alta_quando_a_metrica_e_uma_caracteristica_do_jogador():
    rng = np.random.default_rng(1)
    n_j, n_p = 400, 12
    nivel = rng.normal(1.0, 0.5, n_j)
    S = pd.DataFrame({
        "player_id": np.repeat(np.arange(n_j), n_p), "pos": 3.0, "min90": 1.0,
        "xg": (np.repeat(nivel, n_p) + rng.normal(scale=0.05, size=n_j * n_p)),
    })
    t = ar.persistencia_aspecto(S, "xg", True)
    assert t.posicao.tolist() == ["atacante"] and t.R2[0] > 0.6


def test_persistencia_baixa_quando_e_ruido():
    rng = np.random.default_rng(2)
    n_j, n_p = 400, 12
    S = pd.DataFrame({"player_id": np.repeat(np.arange(n_j), n_p), "pos": 2.0, "min90": 1.0, "xg": rng.normal(size=n_j * n_p)})
    assert ar.persistencia_aspecto(S, "xg", True).R2[0] < 0.02


def test_preparar_ordena_no_tempo_e_calcula_rating_antes():
    S = pd.DataFrame({
        "id": [1, 2, 3, 4], "match_id": [10, 10, 20, 20], "team_id": [1, 2, 1, 2], "fotmob_player_id": ["a", "b", "a", "b"],
        "player_id": [100, 200, 100, 200], "minutes_played": [90, 90, 90, 10], "rating": [8.0, 6.0, 8.0, 6.0],
        "goals": [0, 0, 0, 0], "assists": [0, 0, 0, 0], "xg": [np.nan] * 4, "xa": [0.0] * 4, "total_shots": [1] * 4,
        "tackles": [1] * 4, "interceptions": [1] * 4, "chances_created": [0] * 4,
    })
    P = pd.DataFrame({"id": [10, 20], "match_date": ["2024-01-01", "2024-01-08"], "home_team_id": [1, 1], "away_team_id": [2, 2],
                      "league_id": [7, 7], "status": ["finished"] * 2})
    E = pd.DataFrame({"match_id": [10, 10, 20, 20], "team_id": [1, 2, 1, 2], "rating_antes": [1500.0, 1500.0, 1510.0, 1490.0]})
    L = pd.DataFrame({"match_id": [10, 10], "team_id": [1, 2], "fotmob_player_id": ["a", "b"], "is_starter": [True, True], "position_id": [64, 34]})
    T, final = ar.preparar(dict(S=S, P=P, E=E, L=L))
    assert len(T) == 3                                  # o jogador 200 só tem 10 min no jogo 20: fica de fora
    assert T.match_id.tolist() == [10, 10, 20]
    a2 = T[(T.player_id == 100) & (T.match_id == 20)].iloc[0]
    assert math.isclose(a2.elo_cfg, 1500 + 20 * ((8.0 - 6.8) / 3))
    assert a2.pos == 2 and a2.casa == 1 and a2.elo_diff == 20.0
    assert set(final) == {100, 200}
