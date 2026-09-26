#!/usr/bin/env python3
"""Testes de `backtest_jogador_mercados_walkforward.montar_candidatos_relacionados`
com dataset sintético -- a passada 'relacionados' existe pra fechar o
vazamento de somar por time só quem ENTROU em campo (CONTEXTO_PROJETO.md,
26/09), então o que importa garantir é que nenhuma informação da própria
partida (quem entrou, quanto jogou, o que fez) chega aos candidatos que não
entraram.

Roda com `pytest scripts/test_backtest_jogador_relacionados.py -v` da raiz do repo.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import backtest_jogador_mercados_walkforward as bj  # noqa: E402

H = bj.COLUNAS_HISTORICO_JOGADOR


def _linha(match_id, player_id, data, valor_hist, *, team_id=1, liga="L", chutes=1, xa=0.1, n_hist=1):
    base = {
        "match_id": match_id, "team_id": team_id, "player_id": player_id, "match_date": pd.Timestamp(data, tz="UTC"),
        "league_id": 9, "season": "2025", "liga": liga, "elo_diff": 10.0, "squad_rating_diff": 0.5, "mando": 1,
        "dias_desde_ultimo_jogo": 7.0, "xg_partida": 0.3, "xa_partida": xa, "n_hist": n_hist,
        bj.tmj.TARGET_CHUTES: chutes, bj.tmj.TARGET_GOLS: 0, bj.tmj.TARGET_CHUTES_NO_ALVO: 0,
    }
    base.update({c: valor_hist for c in H})
    base["posicao_num"] = 3
    return base


@pytest.fixture
def cenario():
    # Jogador 10 joga as 3 partidas. Jogador 20 fica no banco sem entrar na
    # partida 2 (entre as partidas 1 e 3, onde joga). Jogador 30 só joga ANTES
    # (partida 1). Jogador 40 nunca joga.
    df = pd.DataFrame([
        _linha(1, 10, "2025-01-01", 1.0), _linha(2, 10, "2025-01-08", 2.0), _linha(3, 10, "2025-01-15", 3.0),
        _linha(1, 20, "2025-01-01", 5.0), _linha(3, 20, "2025-01-15", 6.0),
        _linha(1, 30, "2025-01-01", 7.0),
        _linha(1, 50, "2025-01-01", 0.2, n_hist=0),
    ])
    xi = pd.DataFrame({
        "match_id": [2, 2, 2, 2],
        "team_id": [1, 1, 1, 1],
        "player_id": [10, 20, 30, 40],
        "prob_titular": [0.9, 0.3, 0.1, 0.0],
    })
    return df, xi


def test_quem_entrou_usa_a_propria_linha(cenario):
    df, xi = cenario
    cand = bj.montar_candidatos_relacionados(df, xi, {2}).set_index("player_id")
    assert cand.loc[10, "entrou_em_campo"]
    assert cand.loc[10, "chutes_90_bayesiano"] == 2.0
    assert cand.loc[10, bj.tmj.TARGET_CHUTES] == 1


def test_quem_nao_entrou_usa_a_proxima_aparicao(cenario):
    df, xi = cenario
    cand = bj.montar_candidatos_relacionados(df, xi, {2}).set_index("player_id")
    # Próxima aparição do 20 é a partida 3: as features dela só usam o que
    # veio antes dela, que é exatamente o que veio antes da partida 2.
    assert not cand.loc[20, "entrou_em_campo"]
    assert cand.loc[20, "chutes_90_bayesiano"] == 6.0
    assert cand.loc[20, bj.tmj.TARGET_CHUTES] == 0
    assert cand.loc[20, "xg_partida"] == 0.0
    assert cand.loc[20, "xa_partida"] == 0.0
    # Dias desde o último jogo = partida 2 − partida 1.
    assert cand.loc[20, "dias_desde_ultimo_jogo"] == 7


def test_sem_proxima_aparicao_usa_a_anterior(cenario):
    df, xi = cenario
    cand = bj.montar_candidatos_relacionados(df, xi, {2}).set_index("player_id")
    assert cand.loc[30, "chutes_90_bayesiano"] == 7.0


def test_nunca_apareceu_usa_prior_da_liga(cenario):
    df, xi = cenario
    cand = bj.montar_candidatos_relacionados(df, xi, {2}).set_index("player_id")
    assert cand.loc[40, "chutes_90_bayesiano"] == pytest.approx(0.2)
    assert cand.loc[40, "posicao_num"] == 0
    assert cand.loc[40, "dias_desde_ultimo_jogo"] == 14


def test_contexto_e_minutos_misturados(cenario):
    df, xi = cenario
    cand = bj.montar_candidatos_relacionados(df, xi, {2}).set_index("player_id")
    assert (cand["elo_diff"] == 10.0).all() and (cand["mando"] == 1).all()
    # minutos_esperados_titular/reserva = valor_hist no cenário (6.0 pro 20).
    assert cand.loc[20, "minutos_esperados"] == pytest.approx(0.3 * 6.0 + 0.7 * 6.0)
    assert cand.loc[10, "minutos_esperados"] == pytest.approx(2.0)


def test_nao_vaza_o_que_aconteceu_na_partida(cenario):
    """Mudar o que QUEM NÃO ENTROU fez em outras partidas futuras além da
    próxima, ou o desempenho de quem entrou, não pode mexer nas features de
    quem não entrou."""
    df, xi = cenario
    base = bj.montar_candidatos_relacionados(df, xi, {2}).set_index("player_id")
    df2 = df.copy()
    df2.loc[(df2["match_id"] == 2) & (df2["player_id"] == 10), bj.tmj.TARGET_CHUTES] = 9
    alterado = bj.montar_candidatos_relacionados(df2, xi, {2}).set_index("player_id")
    for pid in (20, 30, 40):
        np.testing.assert_allclose(base.loc[pid, H].to_numpy(dtype=float), alterado.loc[pid, H].to_numpy(dtype=float))
