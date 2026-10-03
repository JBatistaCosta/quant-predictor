"""Testes unitários (sem rede, sem banco) do parsing do mapa de calor do FotMob."""

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ingerir_heatmap_jogador as h

SVG = '<circle cx="52.5" cy="34" r="7.5"/><circle cx="64.1" cy="12.8" r="7.5"/><circle cx="105.4" cy="-0.3" r="7.5"/>'


def test_parse_pontos_em_decimos_e_limitado_ao_campo():
    # 105,4 passa de 105 e -0,3 fica abaixo de 0 (a bolinha é desenhada com raio, o centro pode vazar um pouco)
    assert h.parse_pontos(SVG) == [525, 340, 641, 128, 1050, 0]


def test_parse_pontos_vazio_ou_sem_circulo():
    assert h.parse_pontos("") == []
    assert h.parse_pontos(None) == []
    assert h.parse_pontos("<g></g>") == []


def test_mapa_opta_para_fotmob():
    det = {"content": {"playerStats": {"127374": {"id": 127374, "optaId": 109656}, "5": {"id": 5, "optaId": None}, "9": {"id": 9}}}}
    assert h.mapa_opta_para_fotmob(det) == {"109656": "127374"}
    assert h.mapa_opta_para_fotmob({}) == {}


def test_montar_linhas_usa_ponte_lineup_e_plano_b():
    heat = {"players": {"p1": SVG, "p2": SVG, "p3": SVG, "p4": ""}, "lastModified": "Sat, 03 Oct 2026 01:26:31 GMT"}
    ponte = {"1": "100", "2": "200", "4": "400"}                   # p3 sem ponte; p4 sem pontos
    lineup = {"100": {"team_id": 7, "player_id": 11}}
    linhas, sem_ponte = h.montar_linhas(50, heat, ponte, lineup, {"200": 22})
    assert sem_ponte == 1
    assert [(l["fotmob_player_id"], l["team_id"], l["player_id"], l["n_pontos"]) for l in linhas] == [("100", 7, 11, 3), ("200", None, 22, 3)]
    assert len(linhas[0]["pontos"]) == 2 * linhas[0]["n_pontos"]
    assert linhas[0]["fonte_atualizada_em"].startswith("2026-10-03T01:26:31")


def test_montar_linhas_data_invalida_nao_quebra():
    linhas, _ = h.montar_linhas(1, {"players": {"p1": SVG}, "lastModified": "lixo"}, {"1": "9"}, {}, {})
    assert linhas[0]["fonte_atualizada_em"] is None


def test_deve_retentar():
    agora = datetime(2026, 10, 3, tzinfo=timezone.utc)
    recente, antigo = agora - timedelta(days=1), agora - timedelta(days=30)
    assert h.deve_retentar(None, antigo, agora)                                                # nunca tentada
    assert not h.deve_retentar({"status": "ok"}, recente, agora)                               # pronta
    assert h.deve_retentar({"status": "indisponivel", "tentativas": 1}, recente, agora)        # jogo recente: pode ainda não ter mapa
    assert not h.deve_retentar({"status": "indisponivel", "tentativas": 1}, antigo, agora)     # jogo antigo: 404 definitivo
    assert not h.deve_retentar({"status": "erro", "tentativas": 3}, recente, agora)            # esgotou tentativas


def test_url_heatmap():
    assert h.url_heatmap("5103566").endswith("/heatmap/match/5103566/heatmaps?heatmapUrl=https%3A%2F%2Fpub.fotmob.com%2Fprod%2Fdb%2Fapi%2Fheatmap%2Fmatch%2F5103566")
