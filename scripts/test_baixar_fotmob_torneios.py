"""Testes de scripts/baixar_fotmob_torneios.py (sem rede)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import baixar_fotmob_torneios as b  # noqa: E402


def test_enxugar_remove_so_as_chaves_descartadas():
    d = {"general": 1, "content": 2, "nav": 3, "seo": 4, "ongoing": False, "hasPendingVAR": False}
    assert b.enxugar(d) == {"general": 1, "content": 2} and "nav" in d


def test_salvar_e_carregar_fracionado_em_varias_partes(tmp_path):
    regs = [{"fotmob_match_id": 100 + i, "fixture": {"home": {"id": "1"}}, "matchDetails": {"general": {"matchId": i}}} for i in range(60)]
    b.salvar(str(tmp_path), "euro", "2024", regs)
    destino = tmp_path / "euro" / "2024"
    assert sorted(p.name for p in destino.iterdir()) == ["INDICE.json", "parte-001.json.xz", "parte-002.json.xz", "parte-003.json.xz"]
    lidos = b.carregar(str(tmp_path), "euro", "2024")
    assert [r["fotmob_match_id"] for r in lidos] == [100 + i for i in range(60)] and lidos[5]["matchDetails"]["general"]["matchId"] == 5


def test_torneios_cobrem_euro_e_copa_america():
    assert b.TORNEIOS["euro"][0] == 50 and b.TORNEIOS["copa_america"][0] == 44 and "2024" in b.TORNEIOS["euro"][1]


def test_cruzamento_apelidos_e_total_de_passes():
    import cruzar_fotmob_statsbomb_torneios as x
    assert x.norm("Türkiye") == x.norm("Turkey") and x.norm("Czechia") == "czech republic"
    assert abs(x.total_de_passes("643 (94%)") - 684.04) < 0.1 and x.total_de_passes("sem dado") is None
    fm = [{"casa": "spain", "fora": "italy", "placar": (1, 0), "data": "d", "id": 1}, {"casa": "spain", "fora": "italy", "placar": (0, 2), "data": "d2", "id": 2}]
    sb = [{"casa": "italy", "fora": "spain", "gols": (2, 0), "chutes": [3, 4], "xg": [1.0, 2.0], "escanteios": [1, 2], "passes": [10, 20]},
          {"casa": "spain", "fora": "italy", "gols": (1, 0), "chutes": [5, 6], "xg": [1.5, 0.5], "escanteios": [3, 4], "passes": [30, 40]}]
    pares, sem = x.cruzar(fm, sb)
    assert len(pares) == 2 and not sem
    assert pares[0]["statsbomb"]["chutes"] == [5, 6] and pares[1]["statsbomb"]["chutes"] == [4, 3]       # o par repetido é desempatado pelo placar; ordem casa/fora acertada
