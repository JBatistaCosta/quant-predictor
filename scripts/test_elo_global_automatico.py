#!/usr/bin/env python3
"""Testes do modo `automatico` de `elo_global.py`: cai para o completo SÓ quando o
incremental recusa por ordem cronológica; qualquer outro erro continua falhando.

Roda com `pytest scripts/test_elo_global_automatico.py -v` da raiz do repo.
"""

from __future__ import annotations

import pytest

import elo_global as eg


def _prepara(monkeypatch, partidas, ids_processados):
    """Substitui as leituras do banco e devolve a lista de chamadas ao completo."""
    chamadas = {"completo": 0, "gravou": 0}

    def fake_paginar(montar_query, order="id"):
        # 1ª chamada: team_elo (ratings); 2ª: team_elo_history (match_ids já processados)
        fake_paginar.n += 1
        if fake_paginar.n == 1:
            return [{"team_id": 1, "rating": 1500, "partidas": 10}]
        return [{"match_id": m} for m in ids_processados]

    fake_paginar.n = 0
    monkeypatch.setattr(eg, "_paginar", fake_paginar)
    monkeypatch.setattr(eg, "carregar_partidas_finalizadas", lambda sb: partidas)
    monkeypatch.setattr(eg, "carregar_seeds_externas", lambda sb: {})
    monkeypatch.setattr(eg, "processar_completo", lambda sb: chamadas.__setitem__("completo", chamadas["completo"] + 1))
    monkeypatch.setattr(eg, "processar_partidas", lambda *a, **k: [])
    monkeypatch.setattr(eg, "registrar_elo_lote", lambda *a, **k: chamadas.__setitem__("gravou", chamadas["gravou"] + 1) or 0)
    return chamadas


def _partida(id_, data):
    return {"id": id_, "match_date": data, "home_team_id": 1, "away_team_id": 2}


def test_automatico_cai_para_completo_com_partida_antiga(monkeypatch):
    partidas = [_partida(1, "2026-08-30T10:00:00+00:00"), _partida(2, "2014-08-23T17:00:00+00:00")]
    chamadas = _prepara(monkeypatch, partidas, ids_processados=[1])
    eg.processar_automatico(object())
    assert chamadas["completo"] == 1
    assert chamadas["gravou"] == 0


def test_automatico_usa_incremental_quando_tudo_em_ordem(monkeypatch):
    partidas = [_partida(1, "2026-08-30T10:00:00+00:00"), _partida(2, "2026-09-02T17:00:00+00:00")]
    chamadas = _prepara(monkeypatch, partidas, ids_processados=[1])
    eg.processar_automatico(object())
    assert chamadas["completo"] == 0
    assert chamadas["gravou"] == 1


def test_incremental_puro_continua_recusando(monkeypatch):
    partidas = [_partida(1, "2026-08-30T10:00:00+00:00"), _partida(2, "2014-08-23T17:00:00+00:00")]
    _prepara(monkeypatch, partidas, ids_processados=[1])
    with pytest.raises(eg.IncrementalForaDeOrdem):
        eg.processar_incremental(object())


def test_outro_erro_nao_dispara_completo(monkeypatch):
    partidas = [_partida(1, "2026-08-30T10:00:00+00:00"), _partida(2, "2026-09-02T17:00:00+00:00")]
    chamadas = _prepara(monkeypatch, partidas, ids_processados=[1])

    def quebra(*a, **k):
        raise RuntimeError("banco fora do ar")

    monkeypatch.setattr(eg, "registrar_elo_lote", quebra)
    with pytest.raises(RuntimeError):
        eg.processar_automatico(object())
    assert chamadas["completo"] == 0
