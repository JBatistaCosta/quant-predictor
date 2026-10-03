#!/usr/bin/env python3
"""Testes de `analisar_valor_time_rating_jogador.py`. Roda com
`pytest scripts/test_analisar_valor_time_rating_jogador.py -v` da raiz do repo."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

import analisar_desfalques_xg_gols as ax
import analisar_valor_time_rating_jogador as av


def test_ganho_pareado_positivo_quando_o_modelo_1_erra_menos():
    g = np.array([1, 1, 2, 2, 3, 3])
    m, ep = av.ganho_pareado(np.array([4.0] * 6), np.array([1.0] * 6), g)
    assert math.isclose(m, 3.0) and ep < 1e-9           # diferença constante: erro-padrão zero
    m2, _ = av.ganho_pareado(np.array([1.0] * 6), np.array([4.0] * 6), g)
    assert m2 < 0


def _xi(n_titulares=9, extra_reserva=True):
    linhas = []
    for p in range(n_titulares):
        linhas.append(dict(match_id=1, team_id=7, player_id=p, is_starter=True, sk_chances90=1.0 if p < 3 else np.nan,
                           sk_def90=0.5, sk_nota90=0.2, elo_j=100.0))
    if extra_reserva:
        linhas.append(dict(match_id=1, team_id=7, player_id=99, is_starter=False, sk_chances90=9.0, sk_def90=9.0, sk_nota90=9.0, elo_j=999.0))
    return pd.DataFrame(linhas)


def test_features_xi_so_titulares_e_ausente_conta_zero():
    f = av.features_xi(_xi())
    assert len(f) == 1 and f.n_xi[0] == 9                       # reserva não entra
    assert math.isclose(f.cria[0], 3 * 1.0 / 9)                 # 6 titulares sem habilidade contam 0
    assert math.isclose(f.defe[0], 0.5) and math.isclose(f.eloj[0], 100.0)


def test_features_xi_descarta_times_com_poucos_titulares():
    assert av.features_xi(_xi(n_titulares=6)).empty


def _tabela_sintetica(n=6000, efeito=0.3, seed=0):
    rng = np.random.default_rng(seed)
    T = pd.DataFrame({
        "match_id": np.arange(n), "team_id": rng.integers(0, 60, n), "league_id": rng.integers(0, 3, n),
        "d": pd.to_datetime("2024-01-01") + pd.to_timedelta(rng.integers(0, 900, n), unit="D"),
    })
    for c in ax.CONTROLES + ["f", "f_op"]:
        T[c] = rng.normal(size=n)
    for c in ("cria", "defe", "nota", "eloj"):
        for s in ("_own", "_op"):
            T[c + s + "_z"] = rng.normal(size=n)
    T["dif_xg"] = 0.5 * T.elo_diff + efeito * (T.eloj_own_z - T.eloj_op_z) + rng.normal(size=n)
    T["dif_gols"] = T["dif_xg"] + rng.normal(size=n)
    return T


def test_avaliar_time_detecta_efeito_real_do_xi():
    r = av.avaliar_time(_tabela_sintetica(), "dif_xg")
    m4 = r["M4 + Elo médio dos jogadores do XI"]
    assert m4["ganho"] > 5 * m4["ep"] and m4["r2"] > r["M1 base (Elo, forma, mando, desfalques)"]["r2"]
    assert abs(r["M2 + criação e defesa do XI"]["ganho"]) < 4 * r["M2 + criação e defesa do XI"]["ep"]   # criação/defesa sem efeito nesses dados


def test_avaliar_time_sem_efeito_nao_mostra_ganho_relevante():
    r = av.avaliar_time(_tabela_sintetica(efeito=0.0, seed=1), "dif_xg")
    m4 = r["M4 + Elo médio dos jogadores do XI"]
    assert m4["ganho"] < 4 * m4["ep"]
    assert r["_n"]["treino"] > 0 and r["_n"]["teste"] > 0


def test_juntar_xi_monta_dono_e_adversario_e_padroniza():
    agg = pd.DataFrame({"match_id": [1, 1], "team_id": [10, 20], "n_xi": [11, 11], "cria": [0.2, -0.2], "defe": [0.1, 0.0],
                        "nota": [0.1, -0.1], "eloj": [50.0, -50.0]})
    T = pd.DataFrame({"match_id": [1, 1], "team_id": [10, 20], "opp_id": [20, 10]})
    out = av.juntar_xi(T, agg)
    a = out[out.team_id == 10].iloc[0]
    assert a.cria_own == 0.2 and a.cria_op == -0.2 and a.eloj_own == 50.0 and a.eloj_op == -50.0
    assert math.isclose(a.cria_own_z, -a.cria_op_z)
