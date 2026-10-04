"""Testes de scripts/simulador_cadeia_bola.py (usam os resumos versionados, sem rede)."""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import simulador_cadeia_bola as s  # noqa: E402

PASTA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dados_referencia", "statsbomb")
P = s.Parametros(PASTA)


def test_lognormal_reproduz_mediana_e_media():
    mu, sg = s.lognormal_de(1.36, 1.66)
    import math
    assert abs(math.exp(mu) - 1.36) < 1e-9 and abs(math.exp(mu + sg * sg / 2) - 1.66) < 1e-9
    assert s.lognormal_de(0.0, 5.0)[1] == 0.0 or s.lognormal_de(0.0, 5.0)[1] >= 0


def test_amostrador_respeita_pesos_e_recusa_zero():
    a = s.Amostrador([0, 1, 0])
    assert {a.sortear(random.Random(i)) for i in range(50)} == {1}
    try:
        s.Amostrador([0, 0])
        assert False
    except ValueError:
        pass


def test_mesma_semente_mesmo_jogo_e_relogio_dentro_do_limite():
    a = s.simular_partida(P, random.Random(7))
    b = s.simular_partida(P, random.Random(7))
    assert a == b
    assert a["acoes"] > 1000 and a["chutes"] >= 1 and sum(a["janela_chutes"]) == a["chutes"]


def test_estados_devolvidos_sao_validos():
    rng = random.Random(3)
    m = s.collections.Counter()
    for _ in range(2000):
        quem, zona, tm, _ = s.resolver_chute(P, rng, m, rng.randrange(18))
        assert quem in ("mesma", "adv") and 0 <= zona < 18 and tm >= 0
        quem, zona, tm, _ = s.resolver_perda(P, rng, m, rng.randrange(18))
        assert quem in ("mesma", "adv") and 0 <= zona < 18 and tm >= 0


def test_totais_basicos_perto_do_observado():
    med = s.simular(P, 300, 1)
    assert 0.9 < med["chutes"] / P.alvos["chutes"] < 1.1
    assert 0.8 < med["gols"] / P.alvos["gols"] < 1.2
