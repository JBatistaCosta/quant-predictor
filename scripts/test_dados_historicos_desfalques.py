#!/usr/bin/env python3
"""Testes dos desfalques pela lista do jogo (`desfalques_lista.py` e
`dados_historicos.acrescentar_desfalques_lista`). Roda com
`pytest scripts/test_dados_historicos_desfalques.py -v` da raiz do repo."""

from __future__ import annotations

import math

import pandas as pd

import dados_historicos as dh
import desfalques_lista as dl


def _historia(n_jogos=12, ausente_no_ultimo=True):
    """Time 1 com 11 titulares fixos (jogadores 1..11) em `n_jogos` jogos; no ÚLTIMO jogo o
    jogador 1 fica fora da lista (se `ausente_no_ultimo`). Time 2 só aparece para ter adversário."""
    linhas, partidas = [], []
    for j in range(n_jogos):
        mid = 100 + j
        partidas.append({"id": mid, "match_date": f"2024-01-{j + 1:02d}", "home_team_id": 1, "away_team_id": 2})
        for p in range(1, 12):
            if ausente_no_ultimo and j == n_jogos - 1 and p == 1:
                continue
            linhas.append({"match_id": mid, "team_id": 1, "fotmob_player_id": p, "is_starter": True})
        for p in range(21, 31):
            linhas.append({"match_id": mid, "team_id": 2, "fotmob_player_id": p, "is_starter": True})
    return pd.DataFrame(linhas), pd.DataFrame(partidas)


def test_regular_ausente_da_lista_conta_como_desfalque():
    lineup, partidas = _historia()
    d = dl.desfalques_por_partida(lineup, partidas)
    ultimo = d[(d.match_id == 111) & (d.team_id == 1)].iloc[0]
    assert ultimo["n_fora"] == 1
    assert math.isclose(ultimo["peso_fora"], 10 / 10)  # 10 titularidades nas 10 anteriores


def test_sem_ausente_da_zero_desfalques():
    lineup, partidas = _historia(ausente_no_ultimo=False)
    d = dl.desfalques_por_partida(lineup, partidas)
    ultimo = d[(d.match_id == 111) & (d.team_id == 1)].iloc[0]
    assert ultimo["n_fora"] == 0


def test_inicio_de_historico_fica_nan_e_nao_zero():
    lineup, partidas = _historia()
    d = dl.desfalques_por_partida(lineup, partidas)
    primeiro = d[(d.match_id == 100) & (d.team_id == 1)].iloc[0]
    assert math.isnan(primeiro["n_fora"]) and math.isnan(primeiro["peso_fora"])


def test_nao_usa_o_futuro_para_definir_regular():
    """Jogador que só vira titular DEPOIS do jogo não pode contar como regular nele."""
    lineup, partidas = _historia(ausente_no_ultimo=False)
    extra = pd.DataFrame([{"match_id": 111, "team_id": 1, "fotmob_player_id": 99, "is_starter": True}])
    d = dl.desfalques_por_partida(pd.concat([lineup, extra]), partidas)
    assert d[(d.match_id == 111) & (d.team_id == 1)].iloc[0]["n_fora"] == 0


def test_acrescentar_monta_colunas_de_casa_fora_e_diferenca():
    lineup, partidas = _historia()
    resultado = dh.acrescentar_desfalques_lista(partidas.copy(), lineup)
    ultimo = resultado[resultado["id"] == 111].iloc[0]
    assert ultimo["desf_fora_home"] == 1
    for coluna in dh.FEATURES_NUMERICAS_DESFALQUES:
        assert coluna in resultado.columns
    # time 2 (fora) tem só 10 jogadores distintos fixos => 10 regulares >= MIN_REGULARES, 0 fora
    assert ultimo["desf_fora_away"] == 0
    assert ultimo["desf_fora_diff"] == 1


def test_sem_lista_vira_nan_e_nao_altera_o_resto():
    _, partidas = _historia()
    resultado = dh.acrescentar_desfalques_lista(partidas.copy(), pd.DataFrame())
    assert resultado["desf_fora_home"].isna().all()
    assert list(resultado["id"]) == list(partidas["id"])


def test_lista_de_features_extende_a_base_sem_tocar_producao():
    assert set(dh.FEATURES_NUMERICAS_DESFALQUES) <= set(dh.FEATURES_V9_ELO_XG_DESFALQUES)
    assert not set(dh.FEATURES_NUMERICAS_DESFALQUES) & set(dh.FEATURES_V9_ELO_XG)
