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
    med = s.simular(P, 400, 1)
    for k, tol in (("chutes", 0.08), ("gols", 0.15), ("escanteios", 0.10), ("laterais", 0.10)):
        assert abs(med[k] / P.alvos[k] - 1) < tol, k
    ocup = [sum(v for k, v in med.items() if k.startswith("ent|") and k.endswith(f"|{b}")) for b in range(6)]
    obs = [sum(P.acoes_por_zona[b * 3:b * 3 + 3]) for b in range(6)]
    for o, x in zip(ocup, obs):
        assert abs(o / sum(ocup) - x / sum(obs)) < 0.01             # a ocupação do campo bate a menos de 1 ponto percentual
