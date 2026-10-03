#!/usr/bin/env python3
"""Testes de `analisar_rating_aspecto_jogador.py`. Roda com
`pytest scripts/test_analisar_rating_aspecto_jogador.py -v` da raiz do repo."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

import analisar_rating_aspecto_jogador as aa


def _sintetico(n_j=200, n_p=60, habilidade=True, seed=0):
    """Jogadores com habilidade própria em `chances90`; dois papéis (34 = zagueiro, 85 = meia-atacante) com médias
    bem diferentes; datas cruzam o corte de 2025-01-01."""
    rng = np.random.default_rng(seed)
    hab = rng.normal(0, 1.0, n_j) if habilidade else np.zeros(n_j)
    papel_j = rng.choice([34, 85], n_j)
    linhas = []
    datas = pd.date_range("2024-01-01", periods=n_p, freq="15D")
    for j in range(n_j):
        for k in range(n_p):
            media = (0.5 if papel_j[j] == 34 else 2.0) + hab[j]
            linhas.append((j, k, datas[k], papel_j[j], media + rng.normal(0, 1.0)))
    S = pd.DataFrame(linhas, columns=["player_id", "match_id", "d", "papel", "chances90"])
    S["min90"], S["casa"], S["elo_diff"], S["id"] = 1.0, rng.integers(0, 2, len(S)), rng.normal(0, 50, len(S)), np.arange(len(S))
    return S.sort_values(["d", "match_id", "id"]).reset_index(drop=True)


def test_contexto_remove_a_diferenca_entre_papeis():
    S = _sintetico(habilidade=False)
    esperado, res, ok = aa.contexto_esperado(S, "chances90")
    assert abs(res[(S.papel == 34).values].mean()) < 0.05 and abs(res[(S.papel == 85).values].mean()) < 0.05
    assert esperado[(S.papel == 85).values].mean() - esperado[(S.papel == 34).values].mean() > 1.3


def test_habilidade_so_usa_o_passado_e_e_encolhida():
    ids = np.array([1, 1, 1])
    res = np.array([2.0, 4.0, 0.0])
    h = aa.habilidade_passado(ids, np.zeros(3), res, np.array([1.0, 1.0, 1.0]), np.array([True, True, True]), k=10.0)
    assert h["n_prev"].tolist() == [0, 1, 2]
    assert math.isnan(h["habilidade"][0])
    assert math.isclose(h["habilidade"][1], 2.0 / (1.0 + 10.0))              # só o 1º jogo, encolhido
    assert math.isclose(h["habilidade"][2], (2.0 + 4.0) / (2.0 + 10.0))
    assert math.isclose(h["final"][1], 6.0 / (3.0 + 10.0))


def test_split_half_alto_com_habilidade_e_baixo_com_ruido():
    S = _sintetico(habilidade=True)
    _, res, ok = aa.contexto_esperado(S, "chances90")
    com, _ = aa.split_half(S.player_id.values, res, S.min90.values, ok)
    S2 = _sintetico(habilidade=False, seed=1)
    _, res2, ok2 = aa.contexto_esperado(S2, "chances90")
    sem, _ = aa.split_half(S2.player_id.values, res2, S2.min90.values, ok2)
    assert com > 0.7 and abs(sem) < 0.2


def test_avaliar_habilidade_melhora_a_previsao_fora_da_amostra():
    r = aa.avaliar(_sintetico(), "chances90")
    assert r["r2_contexto_habilidade"] > r["r2_contexto"] + 0.05
    assert 0.6 < r["escala_otima"] < 1.5 and r["n_teste"] > 0


def test_avaliar_sem_habilidade_nao_ganha_nada():
    r = aa.avaliar(_sintetico(habilidade=False, seed=3), "chances90")
    ganho = r["r2_contexto_habilidade"] - r["r2_contexto"]
    assert -0.03 < ganho < 0.005          # sem habilidade real, somar a "habilidade" só acrescenta ruído (nunca ganho)


def test_preparar_aspectos_papel_e_exclusoes():
    base = dict(match_id=[1, 1, 2], team_id=[1, 1, 1], fotmob_player_id=["a", "g", "a"], player_id=[10, 20, 10], id=[1, 2, 3],
                d=pd.to_datetime(["2024-01-01"] * 2 + ["2024-01-08"]), min90=[1.0, 1.0, 1.0], elo_diff=[10.0, 10.0, 10.0],
                casa=[1, 1, 1], chances_created=[2, 0, 1], tackles=[1, 0, 2], interceptions=[1, 0, 0], rating=[7.0, 6.0, 7.5])
    S = pd.DataFrame(base)
    L = pd.DataFrame({"match_id": [1, 1], "team_id": [1, 1], "fotmob_player_id": ["a", "g"], "is_starter": [True, True], "position_id": [34, 11]})
    out = aa.preparar_aspectos(S, L)
    assert set(out.player_id) == {10}                      # goleiro (position_id 11) fica de fora
    assert out.papel.nunique() == 1                        # no jogo 2 (sem linha de titular) usa a posição mais frequente
    assert out.chances90.tolist() == [2.0, 1.0] and out.def90.tolist() == [2.0, 2.0]
