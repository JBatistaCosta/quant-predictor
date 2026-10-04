"""Testes de scripts/calibrar_simulador_por_competicao.py (sem os eventos completos)."""
import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import calibrar_simulador_por_competicao as cs  # noqa: E402
import simulador_cadeia_bola as s  # noqa: E402

PASTA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dados_referencia", "statsbomb")


def test_mesclar_matriz_k_zero_e_k_infinito():
    conj = [[3, 1, 0]] + [[0, 0, 0]] * 2
    base = [[1, 1, 2]] + [[2, 2, 0]] * 2
    zero = cs.mesclar_matriz(conj, base, 0.0)
    assert zero[0] == [3.0, 1.0, 0.0]
    grande = cs.mesclar_matriz(conj, base, 1e9)
    assert abs(grande[0][2] / sum(grande[0]) - 0.5) < 1e-6 and abs(grande[1][0] / sum(grande[1]) - 0.5) < 1e-6      # vira a probabilidade da base


def test_mesclar_nucleo_une_chaves_e_pondera_pela_base():
    conj = {"perda|0": {"adv|recuperação|1": 10}}
    base = {"perda|0": {"adv|recuperação|1": 1, "mesma|lateral|2": 3}, "chute|1": {"adv|tiro de meta|1": 4}}
    out = cs.mesclar_nucleo(conj, base, 8.0)
    assert out["perda|0"]["adv|recuperação|1"] == 10 + 8 * 1 / 4 and out["perda|0"]["mesma|lateral|2"] == 8 * 3 / 4
    assert out["chute|1"]["adv|tiro de meta|1"] == 8.0


def test_logveross_prefere_o_modelo_certo_e_penaliza_o_errado():
    teste = [[0] * 20 for _ in range(18)]
    teste[0][3] = 100
    certo = [[0.0] * 20 for _ in range(18)]
    certo[0][3] = 100
    errado = [[0.0] * 20 for _ in range(18)]
    errado[0][5] = 100
    assert cs.logveross_matriz(certo, teste) > cs.logveross_matriz(errado, teste)


def test_aplicar_perfil_troca_folga_duracao_dos_tempos_e_pool():
    par = s.Parametros(PASTA)
    assert abs(par.folga - 0.2) < 1e-9
    par.aplicar_perfil({"folga": 0.9, "duracao_tempo": [100.0, 200.0], "dur_acao": {"mediana": 2.0, "media": 2.4}})
    assert par.folga == 0.9 and par.duracao_tempo == (100.0, 200.0)
    rng = random.Random(1)
    curto = s.simular_partida(par, rng)
    assert curto["acoes"] < 120                                  # 300 s de jogo no total: poucas linhas
    # pool próprio de chutes: todos com xG 0,5 e gol
    par2 = s.Parametros(PASTA, "statsbomb").aplicar_perfil({"chutes": [[z, 0.5, 1, 0, 0, 10.0, 0.0] for z in range(18) for _ in range(40)] + [[16, 0.76, 1, 2, 0, 11.0, 0.0]] * 5})
    r = s.simular_partida(par2, random.Random(2))
    assert r["gols"] == r["chutes"] > 0


def test_epocas_somam_o_tamanho_dos_conjuntos():
    assert sum(n for _, n in cs.EPOCAS["torneios_selecoes"]) == 262 and sum(n for _, n in cs.EPOCAS["ligas_recentes"]) == 194


def test_quantis_dur_e_erro_relativo():
    assert cs.quantis_dur([1.0, 2.0, 3.0]) == {"mediana": 2.0, "media": 2.0}
    sim = {k: 110.0 for k in cs.vo.CHAVES} | {"posse": {"corridas_por_jogo": 90.0, "linhas_por_corrida_media": 10.0, "duracao_corrida_media_s": 10.0}, "faixa_pct": [20, 20, 20, 20, 10, 10]}
    obs = {k: 100.0 for k in cs.vo.CHAVES} | {"posse": {"corridas_por_jogo": 100.0, "linhas_por_corrida_media": 10.0, "duracao_corrida_media_s": 10.0}, "faixa_pct": [20, 20, 20, 20, 10, 10]}
    e = cs.erro_relativo(sim, obs)
    assert abs(e["acoes"] - 10) < 1e-9 and abs(e["posse_corridas_por_jogo"] - 10) < 1e-9 and e["faixas_pp"] == 0 and math.isclose(e["media_pct"], (7 * 10 + 10) / 10)
