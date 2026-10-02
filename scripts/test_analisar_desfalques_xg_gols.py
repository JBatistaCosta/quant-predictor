#!/usr/bin/env python3
"""Testes das funções puras de `analisar_desfalques_xg_gols.py`. Roda com
`pytest scripts/test_analisar_desfalques_xg_gols.py -v` da raiz do repo."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

import analisar_desfalques_xg_gols as ax


def test_ols_agrupado_recupera_coeficiente_conhecido():
    rng = np.random.default_rng(0)
    n = 4000
    df = pd.DataFrame({"x": rng.normal(size=n), "team_id": rng.integers(0, 40, n), "league_id": rng.integers(0, 3, n)})
    df["y"] = 2.0 + 0.5 * df.x + rng.normal(scale=0.3, size=n)
    o = ax.ols_agrupado(df, "y", ["x"])
    assert abs(o["x"][0] - 0.5) < 0.02 and o["x"][1] > 0
    assert 0.5 < o["_r2"] < 1.0


def _jogos_dois_times(n=12):
    linhas, xg, elo = [], [], []
    for j in range(n):
        mid = 100 + j
        linhas.append({"id": mid, "match_date": f"2024-01-{j + 1:02d}", "home_team_id": 1, "away_team_id": 2,
                       "home_goals": 2, "away_goals": 1, "league_id": 7})
        xg += [{"match_id": mid, "team_id": 1, "xg": 1.5}, {"match_id": mid, "team_id": 2, "xg": 0.5}]
        elo += [{"match_id": mid, "team_id": 1, "rating_antes": 1520.0}, {"match_id": mid, "team_id": 2, "rating_antes": 1500.0}]
    return pd.DataFrame(linhas), pd.DataFrame(xg), pd.DataFrame(elo)


def test_tabela_time_jogo_espelha_os_dois_lados():
    M, X, E = _jogos_dois_times()
    desf = pd.DataFrame({"match_id": [111, 111], "team_id": [1, 2], "n_fora": [2.0, 0.0]})
    T = ax.tabela_time_jogo(M, X, E, desf)
    assert len(T) == 2 and set(T.match_id) == {111}  # só o jogo com desfalque conhecido e forma passada
    casa = T[T.team_id == 1].iloc[0]
    fora = T[T.team_id == 2].iloc[0]
    assert (casa.gols, casa.gols_c, casa.xg, casa.xg_c) == (2, 1, 1.5, 0.5)
    assert (fora.gols, fora.gols_c, fora.xg, fora.xg_c) == (1, 2, 0.5, 1.5)
    assert casa.f == 2 and casa.f_op == 0 and fora.f == 0 and fora.f_op == 2
    assert casa.elo_diff == 20 and fora.elo_diff == -20
    assert math.isclose(casa.dif_gols, 1) and math.isclose(fora.dif_xg, -1.0)
    assert casa.pro10 == 1.5 and casa.con10 == 0.5 and casa.pro10_op == 0.5  # forma só do passado


def _historia_com_ausente(n=12):
    """Time 1 com 11 titulares fixos (jogadores 1..11; jogador 1 é goleiro, com xG+xA 0,5 e Elo 1600 por jogo, os
    demais Elo 1500); no último jogo o jogador 1 fica fora da lista."""
    M, linhas, stats = [], [], []
    for j in range(n):
        mid = 100 + j
        M.append({"id": mid, "match_date": f"2024-01-{j + 1:02d}"})
        for p in range(1, 12):
            if j == n - 1 and p == 1:
                continue
            linhas.append({"match_id": mid, "team_id": 1, "fotmob_player_id": p, "is_starter": True})
            stats.append({"match_id": mid, "team_id": 1, "fotmob_player_id": p, "player_id": p, "is_goalkeeper": p == 1,
                          "xg": 0.3 if p == 1 else 0.0, "xa": 0.2 if p == 1 else 0.0,
                          "tackles": 2 if p == 1 else 1, "interceptions": 1})
    R = pd.DataFrame([{"player_id": s["player_id"], "match_id": s["match_id"], "rating_antes": 1600.0 if s["player_id"] == 1 else 1500.0} for s in stats])
    return pd.DataFrame(linhas), pd.DataFrame(stats), R, pd.DataFrame(M)


def test_qualidade_do_ausente_vem_so_do_passado():
    L, S, R, M = _historia_com_ausente()
    A = ax.qualidade_ausentes(L, S, R, M)
    ult = A[(A.match_id == 111) & (A.team_id == 1)].iloc[0]
    assert ult.f == 1
    assert math.isclose(ult.ab_ataque, 0.5)       # xG+xA médio por jogo nos 10 anteriores
    assert math.isclose(ult.ab_def, 3.0)          # 2 desarmes + 1 interceptação
    assert ult.ab_gk == 1
    assert ult.ab_elo > 0                         # Elo acima da média dos regulares do time
    anterior = A[(A.match_id == 110) & (A.team_id == 1)].iloc[0]
    assert anterior.f == 0 and anterior.ab_gk == 0 and anterior.ab_ataque == 0


def test_inicio_de_historico_fica_de_fora():
    L, S, R, M = _historia_com_ausente()
    A = ax.qualidade_ausentes(L, S, R, M)
    assert 100 not in set(A.match_id)  # sem jogos anteriores não há regulares


def test_padronizar_deixa_desvio_padrao_unitario():
    rng = np.random.default_rng(1)
    T = pd.DataFrame({q + s: rng.normal(5, 3, 200) for q in ax.QUALIDADES for s in ("_own", "_opp")})
    T = ax.padronizar(T)
    for q in ax.QUALIDADES:
        assert math.isclose(T[q + "_own_z"].std(), 1.0, rel_tol=1e-6)
