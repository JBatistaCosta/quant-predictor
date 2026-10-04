"""Testes de scripts/comparar_competicoes_statsbomb.py (sem rede)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import comparar_competicoes_statsbomb as c  # noqa: E402
import gerar_matriz_transicao_statsbomb as g  # noqa: E402


def ev(tipo, time_id, loc=None, **extra):
    e = {"type": {"name": tipo}, "team": {"id": time_id}}
    if loc is not None:
        e["location"] = loc
    e.update(extra)
    return e


def corner(time_id):
    return ev("Pass", time_id, [120, 0], **{"pass": {"type": {"name": "Corner"}, "end_location": [110, 40]}})


def test_cac_esta_excluida_dos_grupos():
    assert 1267 in c.EXCLUIDAS
    todos = {cid for lista in c.GRUPOS.values() for cid, _ in lista}
    assert 1267 not in todos


def test_escanteio_e_atribuido_ao_ultimo_lance_de_quem_atacou_e_nao_ao_do_defensor():
    eventos = [
        ev("Shot", 1, [110, 40], shot={"outcome": {"name": "Blocked"}}),
        ev("Block", 2, [112, 40]),                      # evento imediatamente anterior ao escanteio é do DEFENSOR
        ev("Pressure", 2, [113, 40]),
        corner(1),
    ]
    saida = c.origem_escanteios(eventos)
    assert len(saida) == 1
    assert saida[0][0] == "Chute"
    assert saida[0][1] == g.zona_18(*g.para_metros(110, 40))


def test_cruzamento_errado_e_passe_errado_sao_distinguidos():
    cruz = ev("Pass", 1, [100, 10], **{"pass": {"cross": True, "outcome": {"name": "Incomplete"}, "end_location": [112, 40]}})
    passe = ev("Pass", 1, [90, 30], **{"pass": {"outcome": {"name": "Incomplete"}, "end_location": [105, 40]}})
    assert c.origem_escanteios([cruz, ev("Clearance", 2, [115, 40]), corner(1)])[0][0] == "Cruzamento/Incomplete"
    assert c.origem_escanteios([passe, ev("Clearance", 2, [115, 40]), corner(1)])[0][0] == "Passe/Incomplete"


def test_escanteio_sem_lance_do_atacante_na_janela_vira_interrogacao():
    eventos = [ev("Clearance", 2, [10, 10])] * 30 + [corner(1)]
    assert c.origem_escanteios(eventos) == [("?", None)]


def test_comparar_com_baseline_nao_e_positivo_quando_os_dados_sao_da_propria_baseline():
    # a baseline vê os mesmos dados (em amostra) e o modelo próprio só 80% deles (validação cruzada): a diferença é <= 0 e pequena
    base = [(o, (o + 1) % 18 if (i % 4) else 19) for i, o in enumerate(list(range(18)) * 60)]
    r = c.comparar_com_baseline(base, base)
    assert -0.1 < r["ganho_proprio_menos_baseline"] <= 0.0


def test_comparar_com_baseline_positivo_quando_o_comportamento_e_outro():
    base = [(o, 19) for o in list(range(18)) * 60]         # baseline: toda ação termina em perda
    outro = [(o, 18) for o in list(range(18)) * 60]        # outra competição: toda ação termina em chute
    assert c.comparar_com_baseline(outro, base)["ganho_proprio_menos_baseline"] > 1.0


def test_taxas_ignora_acoes_fora_da_grande_area_no_recorte_de_area():
    dados = [(16, 18), (16, 19), (16, 3), (2, 18)]
    t = c.taxas(dados)
    assert t["acoes"] == 4 and t["n_centro_area"] == 3
    assert abs(t["centro_area_chute"] - 1 / 3) < 1e-9
