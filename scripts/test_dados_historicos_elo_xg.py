#!/usr/bin/env python3
"""Testes das funções puras do Elo global por xG no dataset do ML
(`dados_historicos.acrescentar_elo_xg`, `_carregar_elo_xg_pre_jogo`,
`obter_elo_xg_atual`). Roda com `pytest scripts/test_dados_historicos_elo_xg.py -v`
da raiz do repo."""

from __future__ import annotations

import math

import pandas as pd

import dados_historicos as dh


def _dataset():
    return pd.DataFrame({
        "id": [1, 2, 3],
        "home_team_id": [10, 20, 30],
        "away_team_id": [20, 30, 10],
        "resultado": [0, 1, 2],
    })


def _elo():
    return pd.DataFrame({
        "match_id": [1, 1, 2, 2],
        "team_id": [10, 20, 20, 30],
        "rating_antes": [1500.0, 1480.0, 1490.0, 1600.0],
    })


def test_acrescenta_ratings_de_casa_e_fora_e_a_diferenca():
    resultado = dh.acrescentar_elo_xg(_dataset(), _elo())
    primeira = resultado.loc[resultado["id"] == 1].iloc[0]
    assert primeira["elo_xg_home"] == 1500.0 and primeira["elo_xg_away"] == 1480.0
    assert primeira["elo_xg_diff"] == 20.0
    segunda = resultado.loc[resultado["id"] == 2].iloc[0]
    assert segunda["elo_xg_diff"] == 1490.0 - 1600.0


def test_partida_sem_rating_fica_nan_e_nao_perde_linhas():
    resultado = dh.acrescentar_elo_xg(_dataset(), _elo())
    assert len(resultado) == 3
    terceira = resultado.loc[resultado["id"] == 3].iloc[0]
    assert math.isnan(terceira["elo_xg_home"]) and math.isnan(terceira["elo_xg_diff"])


def test_mantem_a_ordem_e_as_colunas_originais():
    original = _dataset()
    resultado = dh.acrescentar_elo_xg(original.copy(), _elo())
    assert resultado["id"].tolist() == [1, 2, 3]
    for coluna in original.columns:
        assert resultado[coluna].tolist() == original[coluna].tolist()


def test_linhas_duplicadas_no_historico_nao_duplicam_partidas():
    duplicado = pd.concat([_elo(), _elo()], ignore_index=True)
    assert len(dh.acrescentar_elo_xg(_dataset(), duplicado)) == 3


def test_historico_vazio_gera_colunas_nan():
    resultado = dh.acrescentar_elo_xg(_dataset(), pd.DataFrame(columns=["match_id", "team_id", "rating_antes"]))
    assert list(dh.FEATURES_NUMERICAS_ELO_XG) == ["elo_xg_home", "elo_xg_away", "elo_xg_diff"]
    for coluna in dh.FEATURES_NUMERICAS_ELO_XG:
        assert resultado[coluna].isna().all()


def test_lista_de_features_so_acrescenta_as_tres_colunas_ao_v9():
    extras = [f for f in dh.FEATURES_V9_ELO_XG if f not in dh.FEATURES_V9_XG_CORRIGIDO]
    assert extras == dh.FEATURES_NUMERICAS_ELO_XG
    assert set(dh.FEATURES_V9_XG_CORRIGIDO) <= set(dh.FEATURES_V9_ELO_XG)
    # nenhuma lista de producao ganhou as variaveis novas sem querer
    for nome in ("FEATURES_V9", "FEATURES_V9_XG_CORRIGIDO", "FEATURES_V8"):
        assert not set(dh.FEATURES_NUMERICAS_ELO_XG) & set(getattr(dh, nome))


def test_obter_elo_xg_atual_sem_times_devolve_vazio_sem_consultar_o_banco():
    class Falso:
        def table(self, *_):
            raise AssertionError("não deve consultar o banco")
    assert dh.obter_elo_xg_atual(Falso(), []) == {}
    assert dh._carregar_elo_xg_pre_jogo(Falso(), []).empty
