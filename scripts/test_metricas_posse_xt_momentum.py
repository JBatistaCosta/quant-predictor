"""Testes de scripts/metricas_posse_xt_momentum.py (sem rede)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import metricas_posse_xt_momentum as M  # noqa: E402


def L(eq, z, tipo, t, metade=0, xg=0.0, gol=0):
    return (eq, z, tipo, metade, xg, gol, t)


def test_corridas_duracao_e_posse_por_tempo():
    # A fica 0-30 s (3 linhas), B 30-40 s (1 linha), A 40-60 s (1 linha, até a última linha)
    jogo = [L("A", 4, "continua", 0), L("A", 7, "continua", 10), L("A", 10, "perda", 20), L("B", 3, "perda", 30), L("A", 6, "continua", 40), L("A", 9, "continua", 60)]
    r = M.calcular([jogo])
    assert r["corridas_por_jogo"] == 3
    assert r["linhas_por_corrida_media"] == 2.0 and r["duracao_corrida_media_s"] == (30 + 10 + 20) / 3


def test_xt_considera_so_o_que_vem_depois_na_mesma_corrida():
    jogo = [L("A", 4, "continua", 0), L("A", 13, "chute", 5, xg=0.2, gol=1), L("B", 4, "continua", 10)]
    r = M.calcular([jogo])
    z4, z13 = r["xt_por_zona"][4], r["xt_por_zona"][13]
    assert z4["n"] == 2 and abs(z4["p_chute_depois"] - 0.5) < 1e-9 and abs(z4["xg_depois"] - 0.1) < 1e-9 and abs(z4["p_gol_depois"] - 0.5) < 1e-9
    assert z13["p_chute_depois"] == 1.0 and z13["xg_depois"] == 0.2


def test_momentum_conta_chutes_proximos_e_ignora_outra_equipe_e_outra_metade():
    jogo = [L("A", 16, "chute", 10), L("A", 16, "chute", 50), L("B", 16, "chute", 55), L("A", 16, "chute", 500), L("A", 16, "chute", 20, metade=1)]
    r = M.calcular([jogo])
    # 5 chutes; A tem um anterior a 40 s e outro a 450 s na 1ª metade; B e a 2ª metade começam sem anterior
    assert r["chutes_ate_60s_do_anterior_pct"] == 100 * 1 / 5 and r["chutes_ate_300s_do_anterior_pct"] == 100 * 1 / 5


def test_risco_por_posicao_vale_um_quando_a_perda_nao_depende_da_posicao():
    jogo = [L("A", 4, "continua", 0), L("A", 4, "perda", 5), L("B", 4, "perda", 10), L("B", 4, "continua", 15)]
    r = M.calcular([jogo])
    pos = {p["posicao"]: p for p in r["risco_por_posicao_na_corrida"]}
    assert abs(pos["1"]["perda_obs_sobre_esp"] - 1) < 1e-9 and abs(pos["2"]["perda_obs_sobre_esp"] - 1) < 1e-9


def test_risco_por_posicao_mostra_memoria_quando_a_perda_so_vem_no_fim_da_corrida():
    jogo = [L("A", 4, "continua", 0), L("A", 4, "perda", 5), L("B", 4, "continua", 10), L("B", 4, "perda", 15)]
    pos = {p["posicao"]: p for p in M.calcular([jogo])["risco_por_posicao_na_corrida"]}
    assert pos["1"]["perda_obs_sobre_esp"] == 0.0 and pos["2"]["perda_obs_sobre_esp"] == 2.0
