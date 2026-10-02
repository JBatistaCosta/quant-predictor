#!/usr/bin/env python3
"""Testes da captura prospectiva de indisponíveis em `ingerir_escalacao_pre_jogo.py`.
Formato do payload conferido em chamadas reais ao FotMob (02/10/2026).
Roda com `pytest scripts/test_ingerir_escalacao_indisponiveis.py -v` da raiz do repo."""

from __future__ import annotations

import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ingerir_escalacao_pre_jogo as ing

CAPTURA = "2026-10-10T10:30:00+00:00"
FOTMOB_PARA_INTERNO = {"9825": 1, "8456": 2}


def _payload(casa_unavailable, fora_unavailable):
    def time(fid, un):
        t = {"id": fid, "name": "T", "starters": [], "subs": []}
        if un is not None:
            t["unavailable"] = un
        return t
    return {"content": {"lineup": {"homeTeam": time(9825, casa_unavailable), "awayTeam": time(8456, fora_unavailable)}}}


EXEMPLO = [
    {"id": 295069, "name": "Conor Townsend", "unavailability": {"injuryId": 76, "type": "injury", "expectedReturn": "Mid April 2026"}},
    {"id": 1229526, "name": "Forson Amankwah", "unavailability": {"injuryId": 42, "type": "injury", "expectedReturn": "Doubtful"}},
]


class _Consulta:
    def __init__(self, banco, tabela):
        self.banco, self.tabela, self.op, self.args, self.filtros = banco, tabela, None, None, []

    def select(self, *a, **k): self.op = "select"; return self
    def upsert(self, linhas, **k): self.op, self.args = "upsert", (linhas, k); return self
    def delete(self): self.op = "delete"; return self
    def in_(self, c, v): self.filtros.append(("in", c, v)); return self
    def eq(self, c, v): self.filtros.append(("eq", c, v)); return self
    def lt(self, c, v): self.filtros.append(("lt", c, v)); return self

    def execute(self):
        self.banco.chamadas.append((self.tabela, self.op, self.args, self.filtros))
        class R: pass
        r = R()
        r.data = self.banco.players if self.tabela == "players" and self.op == "select" else []
        return r


class FakeSupabase:
    def __init__(self, players=None):
        self.chamadas, self.players = [], players or []

    def table(self, nome): return _Consulta(self, nome)


def test_extrai_linhas_e_coletas_com_codigo_tipo_e_retorno():
    linhas, coletas = ing._extrair_indisponiveis(_payload(EXEMPLO, []), 77, FOTMOB_PARA_INTERNO, CAPTURA)
    assert {l["fotmob_player_id"] for l in linhas} == {"295069", "1229526"}
    lca = next(l for l in linhas if l["fotmob_player_id"] == "295069")
    assert lca["injury_id"] == 76 and lca["type"] == "injury" and lca["expected_return"] == "Mid April 2026"
    assert lca["team_id"] == 1 and lca["match_id"] == 77 and lca["captured_at"] == CAPTURA
    # lista vazia do visitante VIRA coleta (n=0): "ninguém indisponível" != "nunca coletado"
    assert sorted((c["team_id"], c["n_indisponiveis"]) for c in coletas) == [(1, 2), (2, 0)]


def test_bloco_ausente_nao_vira_coleta_nem_linha():
    linhas, coletas = ing._extrair_indisponiveis(_payload(None, None), 77, FOTMOB_PARA_INTERNO, CAPTURA)
    assert linhas == [] and coletas == []


def test_time_sem_mapeamento_e_jogador_invalido_sao_ignorados():
    p = _payload([{"id": 0, "name": "x"}, {"id": 5, "name": "ok", "unavailability": {}}], EXEMPLO)
    linhas, coletas = ing._extrair_indisponiveis(p, 77, {"9825": 1}, CAPTURA)  # visitante sem crosswalk
    assert [l["fotmob_player_id"] for l in linhas] == ["5"] and linhas[0]["injury_id"] is None
    assert [c["team_id"] for c in coletas] == [1]


def test_jogador_duplicado_na_lista_e_deduplicado():
    linhas, _ = ing._extrair_indisponiveis(_payload(EXEMPLO + EXEMPLO[:1], []), 77, FOTMOB_PARA_INTERNO, CAPTURA)
    assert len(linhas) == 2


def test_salvar_grava_antes_de_apagar_e_apaga_so_os_times_coletados():
    banco = FakeSupabase(players=[{"id": 900, "fotmob_player_id": "295069"}])
    linhas, coletas = ing._extrair_indisponiveis(_payload(EXEMPLO, None), 77, FOTMOB_PARA_INTERNO, CAPTURA)
    n = ing._salvar_indisponiveis(banco, 77, linhas, coletas, CAPTURA)
    assert n == 2
    ops = [(t, o) for t, o, *_ in banco.chamadas]
    assert ops.index(("team_unavailable_fotmob", "upsert")) < ops.index(("team_unavailable_fotmob", "delete"))
    upsert = next(c for c in banco.chamadas if c[0] == "team_unavailable_fotmob" and c[1] == "upsert")
    assert upsert[2][1]["on_conflict"] == "match_id,fotmob_player_id"
    assert {l["fotmob_player_id"]: l["player_id"] for l in upsert[2][0]} == {"295069": 900, "1229526": None}
    apagados = [c for c in banco.chamadas if c[1] == "delete"]
    assert len(apagados) == 1  # só o time da casa foi coletado (visitante sem bloco)
    assert ("eq", "team_id", 1) in apagados[0][3] and ("lt", "captured_at", CAPTURA) in apagados[0][3]


def test_lista_vazia_coletada_apaga_retrato_antigo_sem_inserir():
    banco = FakeSupabase()
    linhas, coletas = ing._extrair_indisponiveis(_payload([], []), 77, FOTMOB_PARA_INTERNO, CAPTURA)
    assert ing._salvar_indisponiveis(banco, 77, linhas, coletas, CAPTURA) == 0
    tabelas_upsert = [c[0] for c in banco.chamadas if c[1] == "upsert"]
    assert tabelas_upsert == ["match_unavailable_coleta_fotmob"]
    assert len([c for c in banco.chamadas if c[1] == "delete"]) == 2


def test_sem_coleta_nao_toca_no_banco():
    banco = FakeSupabase()
    assert ing._salvar_indisponiveis(banco, 77, [], [], CAPTURA) == 0 and banco.chamadas == []


def test_so_partida_que_nao_comecou_recebe_o_retrato():
    agora = dt.datetime(2026, 10, 10, 10, 0, tzinfo=dt.timezone.utc)
    assert ing._partida_ainda_nao_comecou("2026-10-10T11:30:00+00:00", agora)
    assert ing._partida_ainda_nao_comecou("2026-10-10T11:30:00Z", agora)
    assert not ing._partida_ainda_nao_comecou("2026-10-10T09:00:00+00:00", agora)  # já começou/terminou: lista seria da coleta
    assert not ing._partida_ainda_nao_comecou(None, agora) and not ing._partida_ainda_nao_comecou("lixo", agora)
