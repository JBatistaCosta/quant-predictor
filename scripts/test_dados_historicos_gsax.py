#!/usr/bin/env python3
"""Testes de `dados_historicos.obter_gsax_atual` com um Supabase FAKE (não
mock de biblioteca -- um stand-in mínimo de `.table().select().eq().in_().
order().range().execute()` que filtra dicionários em memória). Não existe
convenção de teste de função com I/O real de Supabase neste projeto (só
`pricing_pipeline.py`, puro, tem suíte pytest) -- este arquivo cobre
especificamente a lógica de agregação/clip/amostra mínima de GSAx, que é
fácil de quebrar silenciosamente (divisão por zero, amostra pequena
inflando o rate) e caro de validar só via SQL de produção a cada mudança.

Roda com `pytest scripts/test_dados_historicos_gsax.py -v` da raiz do repo.
"""

from __future__ import annotations

import sys
import types

# `dados_historicos.py` importa `from supabase import Client` só pra type
# hint (nunca instanciado nem chamado de verdade aqui -- este arquivo só
# exercita a lógica pura de agregação de `obter_gsax_atual` contra o
# `_FakeSupabase` abaixo). Stub em vez de depender do pacote `supabase`
# real instalado -- evita puxar a dependência de rede/SDK só pra rodar
# este teste.
if "supabase" not in sys.modules:
    _stub = types.ModuleType("supabase")
    _stub.Client = object
    sys.modules["supabase"] = _stub

import dados_historicos as dh


class _FakeResposta:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, linhas: list[dict]):
        self._linhas = linhas
        self._filtros: list[tuple] = []

    def select(self, *_a, **_k):
        return self

    def eq(self, coluna, valor):
        self._filtros.append((coluna, "eq", valor))
        return self

    def in_(self, coluna, valores):
        self._filtros.append((coluna, "in", set(valores)))
        return self

    def order(self, *_a, **_k):
        return self

    def range(self, *_a, **_k):
        return self

    def execute(self):
        linhas = self._linhas
        for coluna, op, valor in self._filtros:
            if op == "eq":
                linhas = [r for r in linhas if r.get(coluna) == valor]
            else:
                linhas = [r for r in linhas if r.get(coluna) in valor]
        return _FakeResposta(linhas)


class _FakeSupabase:
    def __init__(self, tabelas: dict[str, list[dict]]):
        self._tabelas = tabelas

    def table(self, nome):
        return _FakeQuery(self._tabelas.get(nome, []))


def _construir_banco(
    *,
    n_partidas: int,
    time_goleiro: int = 10,
    time_adversario: int = 20,
    xgot_por_partida: float = 0.5,
    gols_por_partida: int = 1,
    player_id: int = 1,
) -> dict[str, list[dict]]:
    lineup, stats, matches, shots = [], [], [], []
    for i in range(n_partidas):
        match_id = 100 + i
        lineup.append({"match_id": match_id, "team_id": time_goleiro, "player_id": player_id, "is_starter": True})
        stats.append({"match_id": match_id, "player_id": player_id, "is_goalkeeper": True})
        matches.append({
            "id": match_id, "match_date": f"2026-01-{i+1:02d}T00:00:00Z", "status": "finished",
            "home_team_id": time_goleiro, "away_team_id": time_adversario,
            "home_goals": 0, "away_goals": gols_por_partida,
        })
        if xgot_por_partida > 0:
            shots.append({"match_id": match_id, "team_id": time_adversario, "xgot": xgot_por_partida, "is_on_target": True})
    return {
        "match_lineup_fotmob": lineup,
        "match_player_stats_fotmob": stats,
        "matches": matches,
        "match_shots_fotmob": shots,
    }


def test_gsax_rate_calculado_corretamente_com_amostra_suficiente():
    # 5 partidas, adversário chuta 2.0 xGOT/partida no alvo (total 10.0) e
    # marca 1 gol/partida (total 5) -- GSAx_rate = 1 - 5/10 = 0.5.
    banco = _construir_banco(n_partidas=5, xgot_por_partida=2.0, gols_por_partida=1)
    supabase = _FakeSupabase(banco)

    resultado = dh.obter_gsax_atual(supabase, [1])

    assert 1 in resultado
    assert resultado[1]["n_jogos"] == 5
    assert resultado[1]["xgot_enfrentado"] == 10.0
    assert resultado[1]["gols_sofridos"] == 5.0
    assert resultado[1]["gsax_rate"] == 0.5


def test_amostra_insuficiente_nao_populaGSAx():
    # Só 4 partidas (< GSAX_MIN_AMOSTRA=5) -- goleiro não deve aparecer no
    # resultado (fica NULL do lado de quem grava, não 0.0).
    banco = _construir_banco(n_partidas=4, xgot_por_partida=2.0, gols_por_partida=1)
    supabase = _FakeSupabase(banco)

    resultado = dh.obter_gsax_atual(supabase, [1])

    assert 1 not in resultado


def test_xgot_enfrentado_zero_nao_populaGSAx_sem_dividir_por_zero():
    # 5 partidas, mas nenhum chute com xgot capturado (achado real de
    # produção: cobertura de xgot por chute é mais esparsa que a de shotmap
    # geral) -- não deve dar ZeroDivisionError nem inventar um rate.
    banco = _construir_banco(n_partidas=5, xgot_por_partida=0.0, gols_por_partida=1)
    supabase = _FakeSupabase(banco)

    resultado = dh.obter_gsax_atual(supabase, [1])

    assert 1 not in resultado


def test_gsax_rate_extremo_e_clipado():
    # 25 partidas, xGOT enfrentado minúsculo (0.1/partida = 2.5 total) mas
    # muitos gols sofridos (3/partida = 75 total) -- rate bruto = 1 - 75/2.5
    # = -29.0, bem fora de [-1,1]. Precisa vir clipado em -1.0.
    banco = _construir_banco(n_partidas=25, xgot_por_partida=0.1, gols_por_partida=3)
    supabase = _FakeSupabase(banco)

    resultado = dh.obter_gsax_atual(supabase, [1])

    assert resultado[1]["gsax_rate"] == -1.0


def test_titular_sem_confirmacao_de_goleiro_e_ignorado():
    # is_starter=true em match_lineup_fotmob, mas SEM confirmação de
    # is_goalkeeper em match_player_stats_fotmob (ex.: jogador de linha
    # titular) -- não deve entrar na agregação de GSAx.
    banco = _construir_banco(n_partidas=5, xgot_por_partida=2.0, gols_por_partida=1)
    banco["match_player_stats_fotmob"] = []  # nenhuma confirmação de GK

    resultado = dh.obter_gsax_atual(_FakeSupabase(banco), [1])

    assert resultado == {}


def test_lista_vazia_devolve_dict_vazio():
    assert dh.obter_gsax_atual(_FakeSupabase({}), []) == {}
