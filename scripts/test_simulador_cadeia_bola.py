"""Testes de scripts/simulador_cadeia_bola.py (usam os resumos versionados, sem rede)."""
import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import simulador_cadeia_bola as s  # noqa: E402

PASTA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dados_referencia", "statsbomb")
P = s.Parametros(PASTA)


def test_lognormal_reproduz_mediana_e_media():
    mu, sg = s.lognormal_de(1.36, 1.66)
    assert abs(math.exp(mu) - 1.36) < 1e-9 and abs(math.exp(mu + sg * sg / 2) - 1.66) < 1e-9
    assert s.lognormal_de(0.0, 5.0)[1] == 0.0                      # mediana zero: degenera (usa a média)


def test_amostrador_respeita_pesos_e_recusa_zero():
    a = s.Amostrador([0, 1, 0])
    assert {a.sortear(random.Random(i)) for i in range(50)} == {1}
    try:
        s.Amostrador([0, 0])
        assert False
    except ValueError:
        pass


def test_mesma_semente_mesmo_jogo_e_chutes_por_janela_somam_o_total():
    a = s.simular_partida(P, random.Random(7))
    b = s.simular_partida(P, random.Random(7))
    assert a == b
    assert a["acoes"] > 1000 and sum(a["janela_chutes"]) == a["chutes"]


def test_proxima_linha_sempre_devolve_zona_valida_e_classe_conhecida():
    rng = random.Random(3)
    classes = {"recuperação", "lateral", "tiro livre", "tiro de meta", "escanteio", "pênalti", "continua"}
    for z in range(18):
        for amostra in (P.apos_perda[z], P.apos_chute[z]):
            for _ in range(200):
                quem, classe, zona = s.proxima_linha(P, rng, amostra)
                assert quem in ("mesma", "adv") and classe in classes and 0 <= zona < 18
        assert 0.0 <= P.p_quebra[z] <= 1.0


def test_totais_perto_do_observado_e_ocupacao_por_faixa():
    med = s.simular(P, 400, 1, memoria=False)             # sem a memória da posse a ocupação do campo fecha a < 1 ponto percentual
    for k, tol in (("chutes", 0.08), ("gols", 0.15), ("escanteios", 0.10), ("laterais", 0.10)):
        assert abs(med[k] / P.alvos[k] - 1) < tol, k
    ocup = [sum(v for k, v in med.items() if k.startswith("ent|") and k.endswith(f"|{b}")) for b in range(6)]
    obs = [sum(P.acoes_por_zona[b * 3:b * 3 + 3]) for b in range(6)]
    for o, x in zip(ocup, obs):
        assert abs(o / sum(ocup) - x / sum(obs)) < 0.01             # a ocupação do campo bate a menos de 1 ponto percentual


def test_multiplicadores_neutros_nao_mudam_o_jogo_e_time_forte_chuta_mais():
    neutro = s.simular(P, 200, 5)
    forte = s.Multiplicadores(ataque_chute=1.5)
    rng = random.Random(5)
    chutes_forte = sum(s.simular_partida(P, rng, (forte, s.NEUTRO))["chutes_0"] for _ in range(200)) / 200
    chutes_neutro = neutro["chutes"] / 2
    assert chutes_forte > chutes_neutro * 1.15


def test_multiplicador_de_chute_na_janela_aumenta_so_aquela_janela():
    jm = [dict(w) for w in s.JANELA_NEUTRA]
    jm[0]["chute"] = 2.0
    rng = random.Random(9)
    base = [0] * 6
    alt = [0] * 6
    for _ in range(300):
        for i, v in enumerate(s.simular_partida(P, rng)["janela_chutes"]):
            base[i] += v
        for i, v in enumerate(s.simular_partida(P, rng, janela_mult=jm)["janela_chutes"]):
            alt[i] += v
    # dobrar a chance de chute por linha NÃO dobra os chutes: cada chute encerra a posse e devolve a bola à defesa (saturação), então sobe ~+28%
    assert alt[0] > base[0] * 1.15 and abs(alt[5] / base[5] - 1) < 0.2


def test_forca_dos_times_pesos_e_pearson():
    import forca_dos_times as F
    assert abs(F.pearson([1, 2, 3, 4], [2, 4, 6, 8]) - 1) < 1e-9 and F.pearson([1, 1, 1], [1, 2, 3]) == 0.0
    assert F.peso_de_confiabilidade(0.0) == 0.0 and abs(F.peso_de_confiabilidade(1.0) - 1.0) < 1e-9 and F.peso_de_confiabilidade(-0.5) == 0.0


def test_memoria_da_posse_reproduz_o_risco_de_perda_por_posicao_na_corrida():
    import metricas_posse_xt_momentum as M
    rng = random.Random(4)
    partidas = []
    for _ in range(150):
        reg: list = []
        s.simular_partida(P, rng, registro=reg)
        partidas.append(reg)
    risco = {r["posicao"]: r for r in M.calcular(partidas)["risco_por_posicao_na_corrida"]}
    assert risco["1"]["perda_obs_sobre_esp"] > 1.1 and risco["15+"]["perda_obs_sobre_esp"] < 0.9        # posse recém-ganhada é frágil; posse longa é segura
    sem = []
    for _ in range(150):
        reg = []
        s.simular_partida(P, rng, registro=reg, memoria=False)
        sem.append(reg)
    r0 = {r["posicao"]: r for r in M.calcular(sem)["risco_por_posicao_na_corrida"]}
    assert abs(r0["15+"]["perda_obs_sobre_esp"] - 1) < 0.1 or r0["15+"]["perda_obs_sobre_esp"] > risco["15+"]["perda_obs_sobre_esp"]
