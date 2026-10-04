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


def test_escanteios_por_time_inclui_time_com_zero_e_conta_cada_lado():
    eventos = [ev("Starting XI", 1), ev("Starting XI", 2), corner(1), corner(1), corner(1)]
    assert sorted(c.escanteios_por_time(eventos)) == [0, 3]
    assert c.escanteios_por_time([ev("Starting XI", 1), ev("Starting XI", 2), corner(1), corner(2)]) == [1, 1]


def test_distribuicao_mediana_variancia_e_razao():
    d = c.distribuicao([2, 4, 4, 4, 5, 5, 7, 9])
    assert d["n"] == 8 and abs(d["media"] - 5.0) < 1e-9
    assert d["mediana"] == 4.5
    assert abs(d["variancia"] - 32 / 7) < 1e-9          # soma dos quadrados dos desvios = 32, n-1 = 7
    assert abs(d["var_sobre_media"] - (32 / 7) / 5) < 1e-9
    assert c.distribuicao([3]) == {"n": 1}


def test_ligas_recentes_esta_nos_grupos_e_cac_continua_fora():
    assert "ligas_recentes" in c.GRUPOS
    assert 1267 not in {cid for lista in c.GRUPOS.values() for cid, _ in lista}


def test_resumo_e_conferencia_sem_rede(tmp_path):
    acoes = [("continua", 100, 10, 110, 40, "cruzamento"), ("perda", 100, 10, None, None, "cruzamento"), ("chute", 110, 40, None, None, "chute"),
             ("continua", 20, 40, 40, 40, "conducao")] * 5
    bruto = {"Teste 2020": {"jogos": 2, "acoes": acoes, "escanteios": [("Chute", 16), ("?", None)], "corners_times": [[3, 5], [4, 4]]}}
    r = c.resumir_competicao(bruto["Teste 2020"], [20, 10])
    assert r["match_ids"] == [10, 20] and r["n_acoes"] == 20
    assert sum(sum(l) for l in r["contagens_18x20"]) == 20
    assert r["acoes_por_origem"]["cruzamento"] == {"continua": 5, "chute": 0, "perda": 5}
    assert r["escanteios_origem_por_zona"]["Chute"][16] == 1 and r["escanteios_origem_por_zona"]["?"][18] == 1
    import json
    pasta = tmp_path / "res"
    pasta.mkdir()
    (pasta / "g.json").write_text(json.dumps({"La Liga 2015/2016": r, "Teste 2020": r}))
    c.conferir(str(pasta))          # não deve levantar exceção; ganho de um modelo contra ele mesmo ~ 0


def test_contagens_conta_cada_acao_uma_vez():
    m = c.contagens([("chute", 110, 40, None, None, "chute"), ("perda", 50, 40, None, None, "falha")])
    assert sum(sum(l) for l in m) == 2 and sum(l[18] for l in m) == 1 and sum(l[19] for l in m) == 1
