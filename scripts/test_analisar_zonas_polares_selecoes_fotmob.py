"""Testes de scripts/analisar_zonas_polares_selecoes_fotmob.py (sem rede)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analisar_zonas_polares_selecoes_fotmob as a  # noqa: E402
import zonas_polares as zp  # noqa: E402


def chute(x, y, xg, evento="AttemptSaved", situacao="RegularPlay", periodo="FirstHalf", contra=False):
    return {"x": x, "y": y, "expectedGoals": xg, "eventType": evento, "situation": situacao, "period": periodo, "isOwnGoal": contra}


def test_chutes_validos_exclui_penalti_disputa_gol_contra_e_sem_xg():
    reg = {"matchDetails": {"content": {"shotmap": {"shots": [chute(94, 34, 0.2), chute(94, 34, 0.76, situacao="Penalty"), chute(94, 34, 0.5, periodo="PenaltyShootout"),
                                                              chute(94, 34, 0.1, contra=True), chute(94, 34, None), chute(94, 34, 0.3, evento="Goal")]}}}}
    out = a.chutes_validos(reg)
    assert len(out) == 2 and [x[2] for x in out] == [0, 1]


def test_zona_polar_do_chute_de_frente_a_11_m_e_na_ponta_da_area():
    z_frente = a.chutes_validos({"matchDetails": {"content": {"shotmap": {"shots": [chute(105 - 11, 34, 0.1)]}}}})[0][0]
    z_aberto = a.chutes_validos({"matchDetails": {"content": {"shotmap": {"shots": [chute(105 - 5, 34 + 14, 0.1)]}}}})[0][0]
    assert zp.NOMES[z_frente] == "9–12 m · central" and zp.NOMES[z_aberto].endswith("aberto")


def test_tabela_soma_100_por_cento_e_por_edicao_trata_edicao_sem_chutes():
    jogos = [[(0, 0.4, 1, 3.0), (2, 0.2, 0, 7.0)], [(2, 0.2, 0, 7.0), (13, 0.02, 0, 35.0)]]
    t = a.tabela(jogos, reamostras=50)
    assert abs(sum(l["pct"] for l in t) - 100) < 1e-9 and t[2]["n"] == 2 and abs(t[2]["xg_chute"] - 0.2) < 1e-9
    ed = a.por_edicao({"x 2024": jogos, "y 2016": [[], []]})
    assert ed[0]["chutes"] == 4 and ed[1]["chutes"] == 0 and abs(ed[0]["gols_sobre_xg"] - 1 / 0.82) < 1e-9
