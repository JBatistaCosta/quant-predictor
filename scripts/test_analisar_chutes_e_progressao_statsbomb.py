"""Testes de scripts/analisar_chutes_e_progressao_statsbomb.py (sem rede)."""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analisar_chutes_e_progressao_statsbomb as a  # noqa: E402
import simulador_cadeia_bola as s  # noqa: E402


def test_polar_de_frente_e_na_linha_de_fundo():
    d, ang = a.polar_do_gol(105 - 11, 34)                  # 11 m de frente (ponto de pênalti)
    assert abs(d - 11) < 1e-9 and ang == 0
    d, ang = a.polar_do_gol(105, 34 + 3.66)                # junto à trave, na linha de fundo
    assert abs(d - 3.66) < 1e-6 and abs(ang - 90) < 1e-6


def shot(x, y, xg, gol=False, tipo="Open Play", cabeca=False):
    return {"type": {"name": "Shot"}, "location": [x, y], "period": 1, "team": {"id": 1},
            "shot": {"statsbomb_xg": xg, "outcome": {"name": "Goal" if gol else "Saved"}, "type": {"name": tipo}, "body_part": {"name": "Head" if cabeca else "Right Foot"}}}


def test_chutes_da_partida_codifica_tipo_cabeca_e_gol():
    # StatsBomb: x 0-120, y 0-80, gol em (120, 40); (108, 40) = 12 jardas = 10,5 m do gol
    out = a.chutes_da_partida([shot(108, 40, 0.15, gol=True), shot(108, 40, 0.76, tipo="Penalty"), shot(100, 30, 0.05, cabeca=True)])
    assert [x[2] for x in out] == [1, 0, 0] and [x[3] for x in out] == [0, 2, 0] and [x[4] for x in out] == [0, 0, 1]
    assert abs(out[0][5] - 10.5) < 0.1 and out[0][6] == 0


def passe(time, loc, fim):
    return {"type": {"name": "Pass"}, "team": {"id": time}, "location": loc, "pass": {"end_location": fim}, "minute": 1, "period": 1}


def test_progressao_conta_so_continuacoes_da_mesma_equipe_por_posicao():
    acc = [[[0] * 18 for _ in range(18)] for _ in s.POSICOES_CORRIDA]
    a.progressao_da_partida([passe(1, [30, 40], [60, 40]), passe(1, [60, 40], [90, 40]), passe(2, [30, 40], [40, 40])], acc)
    # só a 1ª linha do time 1 tem uma linha seguinte do mesmo time; a 2ª é seguida pelo adversário e a do time 2 é a última
    assert sum(sum(sum(l) for l in b) for b in acc) == 1 and sum(acc[0][z18][z18b] for z18 in range(18) for z18b in range(18)) == 1
