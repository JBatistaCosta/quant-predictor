"""Testes unitários (sem rede) da geração da matriz de transição do StatsBomb."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gerar_matriz_transicao_statsbomb as g


@pytest.mark.parametrize("x,y,zona_banco", [      # mesmos casos de scripts/test_detalhe_jogador_sql.py (zona do banco = índice + 1)
    (10, 5, 1), (10, 34, 2), (10, 60, 3), (50, 5, 4), (50, 34, 5), (50, 60, 6), (80, 5, 7), (80, 34, 8), (80, 60, 9),
    (95, 34, 11), (95, 20, 10), (95, 50, 12), (95, 5, 7), (88.5, 13.85, 10), (88.49, 34, 8), (105, 68, 9),
])
def test_zona_12_igual_a_do_banco(x, y, zona_banco):
    assert g.zona_12(x, y) + 1 == zona_banco


def test_para_metros_converte_o_campo_inteiro():
    assert g.para_metros(120, 80) == (105.0, 68.0)
    assert g.para_metros(0, 0) == (0.0, 0.0)
    assert g.para_metros(60, 40) == (52.5, 34.0)


def test_zona_3x3_cantos():
    assert g.zona_3x3(0, 0) == 0 and g.zona_3x3(105, 68) == 8 and g.zona_3x3(52.5, 34) == 4


def ev(tipo, loc, **extra):
    return {"type": {"name": tipo}, "location": loc, **extra}


def test_acoes_da_partida_classifica_os_desfechos():
    eventos = [
        ev("Pass", [10, 40], **{"pass": {"end_location": [30, 40]}}),                                    # completo
        ev("Pass", [10, 40], **{"pass": {"end_location": [30, 40], "outcome": {"name": "Incomplete"}}}),  # perda
        ev("Pass", [10, 40], **{"pass": {"end_location": [30, 40], "outcome": {"name": "Pass Offside"}}}),
        ev("Carry", [20, 40], carry={"end_location": [40, 40]}),
        ev("Shot", [110, 40]),
        ev("Dispossessed", [60, 40]),
        ev("Miscontrol", [60, 40]),
        ev("Dribble", [60, 40], dribble={"outcome": {"name": "Incomplete"}}),   # drible incompleto NÃO conta como perda (Achado 15)
        ev("Pass", [10, 40], **{"pass": {"end_location": [30, 40], "outcome": {"name": "Unknown"}}}),             # passe "Unknown" também não
        ev("Pass", [10, 40], **{"pass": {"end_location": [30, 40], "outcome": {"name": "Injury Clearance"}}}),   # nem "Injury Clearance"
        ev("Duel", [60, 40]),                                                  # ignorado
        {"type": {"name": "Pass"}},                                            # sem location: ignorado
    ]
    tipos = [a[0] for a in g.acoes_da_partida(eventos)]
    assert tipos == ["continua", "perda", "perda", "continua", "chute", "perda", "perda"]


def test_calcular_taxas_e_matriz_somam_um():
    acoes = [("continua", 5, 40, 60, 40), ("continua", 5, 40, 5, 40), ("perda", 5, 40, None, None), ("chute", 110, 40, None, None)]
    r = g.calcular(acoes, "12")
    assert r["n_acoes"] == 4
    z0 = g.zona_12(*g.para_metros(5, 40))
    d = r["taxa_desfecho"][z0]
    assert d["continua"] == pytest.approx(2 / 3) and d["perda"] == pytest.approx(1 / 3) and d["chute"] == 0
    assert sum(r["transicao"][z0]) == pytest.approx(1.0)
    assert r["transicao"][z0][z0] == pytest.approx(0.5)
    for z, linha in enumerate(r["transicao"]):             # zona sem continuação fica zerada, nunca divide por zero
        assert sum(linha) == pytest.approx(1.0) or sum(linha) == 0


@pytest.mark.skipif(not os.environ.get("SB_CACHE_DIR"), reason="defina SB_CACHE_DIR (pasta com acoes.json gerado pelo script) para a validação contra o Achado 15")
def test_grade_3x3_reproduz_a_matriz_publicada_no_achado_15():
    """Checagem do MÉTODO: refeita na grade do Achado 15, a matriz tem de bater com a publicada (zoneTransitionMatrix.js)."""
    import json
    import re
    import pathlib
    acoes = [tuple(a) for a in json.load(open(os.path.join(os.environ["SB_CACHE_DIR"], "acoes.json")))]
    r = g.calcular(acoes, "3x3")
    assert sum(r["contagem"]["continua"]) == 552934                      # o N do Achado 15
    js = (pathlib.Path(__file__).resolve().parent.parent / "src" / "utils" / "zoneTransitionMatrix.js").read_text(encoding="utf-8")
    taxa = [tuple(float(v) for v in m) for m in re.findall(r"\{ chute: ([\d.]+), perda: ([\d.]+), continua: ([\d.]+) \}", js)]
    corpo = re.search(r"export const MATRIZ_TRANSICAO = \[(.*?)\n\];", js, re.S).group(1)
    matriz = [[float(v) for v in linha.split(",")] for linha in re.findall(r"\[([\d.,\s]+)\]", corpo)]
    for z in range(9):
        for i, k in enumerate(("chute", "perda", "continua")):
            assert abs(r["taxa_desfecho"][z][k] - taxa[z][i]) < 0.0006
        for w in range(9):
            assert abs(r["transicao"][z][w] - matriz[z][w]) < 0.0006
