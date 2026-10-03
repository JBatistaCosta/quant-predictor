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


@pytest.mark.skipif(not os.environ.get("SB_CACHE_DIR"), reason="defina SB_CACHE_DIR (pasta com acoes_v2.json gerado pelo script) para a validação contra o Achado 15")
def test_grade_3x3_reproduz_a_matriz_publicada_no_achado_15():
    """Checagem do MÉTODO: refeita na grade do Achado 15, a matriz tem de bater com a publicada (zoneTransitionMatrix.js)."""
    import json
    import re
    import pathlib
    acoes = [tuple(a) for a in json.load(open(os.path.join(os.environ["SB_CACHE_DIR"], "acoes_v2.json")))]
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


def test_zona_18_refina_a_de_12_em_qualquer_ponto():
    """Cada zona de 18 cabe dentro de uma de 12 (PAI_12_DE_18), em todo o campo."""
    for x in range(0, 211):
        for y in range(0, 137):
            xm, ym = x / 2, y / 2
            z18 = g.zona_18(xm, ym)
            assert 0 <= z18 < 18 and g.PAI_12_DE_18[z18] == g.zona_12(xm, ym)


@pytest.mark.parametrize("x,y,zona", [(10, 5, 0), (40, 34, 4), (60, 34, 7), (75, 34, 10), (85, 34, 13), (95, 34, 16),
                                      (52.5, 34, 7), (52.49, 34, 4), (79.25, 34, 13), (79.24, 34, 10), (95, 5, 12)])
def test_zona_18_casos_de_fronteira(x, y, zona):
    assert g.zona_18(x, y) == zona


def test_pai_12_de_18_tem_3_filhos_nas_faixas_divididas():
    from collections import Counter
    filhos = Counter(g.PAI_12_DE_18)
    assert sorted(filhos) == list(range(12))
    assert [filhos[z] for z in range(12)] == [1, 1, 1, 2, 2, 2, 2, 2, 2, 1, 1, 1]      # meio e ataque_fora_da_area têm 2 filhos


def test_comparar_12_vs_18_acha_ganho_quando_a_metade_alta_se_comporta_diferente():
    import random
    rnd = random.Random(0)
    acoes = []
    for _ in range(4000):                      # faixa do meio: a metade alta (x>=52,5 m ~ 60 jardas) só continua para frente; a baixa só perde
        acoes.append(("continua", 62, 40, 100, 40))
        acoes.append(("perda", 50, 40, None, None))
        acoes.append(("continua", 50, 40, 52, 40) if rnd.random() < 0.5 else ("perda", 50, 40, None, None))
    c = g.comparar_12_vs_18(acoes, blocos=4)
    assert c["ganho_medio_nats_por_acao"] > 0.05 and c["ganho_alvo_grosso_nats_por_acao"] > 0


def test_comparar_12_vs_18_nao_inventa_ganho_quando_nao_ha_diferenca():
    import random
    rnd = random.Random(1)
    acoes = [("perda", 62 if rnd.random() < 0.5 else 50, 40, None, None) if rnd.random() < 0.3 else ("continua", 62 if rnd.random() < 0.5 else 50, 40, 100, 40)
             for _ in range(40000)]            # as duas metades do meio têm o mesmo comportamento
    c = g.comparar_12_vs_18(acoes, blocos=4)
    assert abs(c["ganho_alvo_grosso_nats_por_acao"]) < 0.01


def test_acoes_marcam_a_origem_e_cruzamento_nao_altera_a_matriz():
    eventos = [
        ev("Pass", [100, 10], **{"pass": {"end_location": [110, 40], "cross": True}}),                               # cruzamento certo
        ev("Pass", [100, 10], **{"pass": {"end_location": [110, 40], "cross": True, "outcome": {"name": "Incomplete"}}}),
        ev("Pass", [100, 10], **{"pass": {"end_location": [90, 10]}}),
        ev("Carry", [20, 40], carry={"end_location": [40, 40]}),
        ev("Shot", [110, 40]),
        ev("Dispossessed", [60, 40]),
    ]
    acoes = g.acoes_da_partida(eventos)
    assert [a[5] for a in acoes] == ["cruzamento", "cruzamento", "passe", "conducao", "chute", "falha"]
    sem_origem = [a[:5] for a in acoes]
    assert g.calcular(acoes, "18")["transicao"] == g.calcular(sem_origem, "18")["transicao"]
    assert g.calcular(acoes, "18")["contagem"] == g.calcular(sem_origem, "18")["contagem"]


def test_analisar_cruzamento_separa_perda_e_destino():
    acoes = [("continua", 100, 10, 110, 40, "cruzamento")] + [("perda", 100, 10, None, None, "cruzamento")] * 3 \
        + [("continua", 100, 10, 90, 10, "passe")] * 3 + [("perda", 100, 10, None, None, "passe")] \
        + [("continua", 20, 40, 40, 40, "conducao"), ("chute", 110, 40, None, None, "chute")]
    r = g.analisar_cruzamento(acoes, "18")
    o = g.zona_18(*g.para_metros(100, 10))
    c, p = r["por_zona"]["cruzamento"][o], r["por_zona"]["passe"][o]
    assert (c["continua"], c["perda"]) == (1, 3) and (p["continua"], p["perda"]) == (3, 1)
    assert c["destino"][g.zona_18(*g.para_metros(110, 40))] == 1
    assert sum(sum(d["destino"]) + d["perda"] for d in r["por_zona"]["passe"]) == 4   # condução e chute ficam fora
