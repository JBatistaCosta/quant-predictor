#!/usr/bin/env python3
"""Testes do Elo por xG (escore misto resultado + xG). Roda com
`pytest scripts/test_elo_global_xg.py -v` da raiz do repo.
"""

from __future__ import annotations

import math

import elo_global_xg as ex


def _p(id_, data, casa, fora, gc, gf, neutro=False):
    return {"id": id_, "match_date": data, "home_team_id": casa, "away_team_id": fora,
            "home_goals": gc, "away_goals": gf, "round": None, "is_neutral": neutro}


def test_escore_sem_xg_e_so_resultado_com_k_de_resultado():
    assert ex.escore_atualizacao(2, 1, None, None) == (1.0, ex.K_RES, False)
    assert ex.escore_atualizacao(1, 1, 1.0, None) == (0.5, ex.K_RES, False)  # xG de um lado só = fallback


def test_escore_com_xg_mistura_e_usa_k_de_xg():
    s, k, usou = ex.escore_atualizacao(0, 0, 2.0, 0.5)  # empate no placar, casa dominou no xG
    s_xg = 1 / (1 + math.exp(-ex.A_XG * 1.5))
    assert usou and k == ex.K_XG
    assert abs(s - (ex.W_RES * 0.5 + (1 - ex.W_RES) * s_xg)) < 1e-12
    assert s > 0.5  # o xG empurra o escore para a casa mesmo no 0 a 0


def test_xg_puxa_o_rating_mesmo_com_empate():
    partidas = [_p(1, "2024-01-01", 1, 2, 0, 0)]
    r_xg, *_ = ex.processar_partidas(partidas, {(1, 1): 2.5, (1, 2): 0.3})
    r_sem, *_ = ex.processar_partidas(partidas, {})
    assert r_xg[1] > r_sem[1] and r_xg[2] < r_sem[2]


def test_soma_zero_e_ordem_cronologica_no_historico():
    partidas = [_p(1, "2024-01-01", 1, 2, 2, 0), _p(2, "2024-01-08", 2, 3, 1, 1), _p(3, "2024-01-15", 3, 1, 0, 3)]
    rating, contagem, hist = ex.processar_partidas(partidas, {(3, 3): 0.2, (3, 1): 1.9})
    assert abs(sum(rating.values()) - 3 * ex.RATING_INICIAL) < 1e-9  # K simétrico: soma conservada
    assert contagem == {1: 2, 2: 2, 3: 2}
    assert [h["match_id"] for h in hist] == [1, 1, 2, 2, 3, 3]
    # o rating de "antes" da partida 3 do time 1 é o "depois" da partida 1
    h1_depois = next(h["rating_depois"] for h in hist if h["match_id"] == 1 and h["team_id"] == 1)
    h3_antes = next(h["rating_antes"] for h in hist if h["match_id"] == 3 and h["team_id"] == 1)
    assert h3_antes == h1_depois
    assert [h["usou_xg"] for h in hist if h["match_id"] == 3] == [True, True]
    assert [h["usou_xg"] for h in hist if h["match_id"] == 1] == [False, False]


def test_campo_neutro_sem_vantagem_de_casa():
    assert abs(ex.esperado_casa(1500, 1500, True) - 0.5) < 1e-12
    assert ex.esperado_casa(1500, 1500, False) > 0.5


def test_partidas_fora_de_ordem_na_lista_nao_sao_reordenadas_aqui():
    # a ordenação é responsabilidade de carregar_partidas (data, id); processar_partidas segue a lista dada
    partidas = [_p(2, "2024-01-08", 1, 2, 1, 0), _p(1, "2024-01-01", 1, 2, 0, 1)]
    _, _, hist = ex.processar_partidas(partidas, {})
    assert [h["match_id"] for h in hist][:2] == [2, 2]
