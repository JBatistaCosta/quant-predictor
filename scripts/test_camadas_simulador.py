"""Testes de scripts/camadas_simulador.py (sem rede)."""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import camadas_simulador as cam  # noqa: E402


def jogo(hs, as_, hg, ag, casa=1, fora=2):
    return {"home": casa, "away": fora, "hs": hs, "as": as_, "hg": hg, "ag": ag}


def test_janela_soma_so_os_ultimos_jogos():
    h = cam.Historia()
    for _ in range(10):
        h.add(jogo(10, 10, 1, 1))
    for _ in range(5):
        h.add(jogo(20, 10, 2, 1))
    (sc, sf, gc, gf), n = h.janela(5)
    assert (sc, sf, gc, gf, n) == (100, 50, 10, 5, 5)
    (sc, sf, _, _), n = h.janela(1000)                  # menos jogos que a janela: usa todos
    assert n == 15 and sc == 100 + 100


def test_mando_por_janela_segue_a_mudanca_e_o_da_historia_inteira_fica_atrasado():
    h = cam.Historia()
    for _ in range(300):
        h.add(jogo(10, 10, 1, 1))                       # mando neutro por muito tempo
    for _ in range(100):
        h.add(jogo(16, 10, 2, 1))                       # depois o mandante passa a chutar 60% a mais
    cfg = {**cam.CONFIG, "janela_mando": 100}
    recente = cam.camada_mando_chutes_janela(h, 1, 2, cfg)[0]["ataque_chute"]
    inteira = cam.camada_mando_chutes(h, 1, 2, cfg)[0]["ataque_chute"]
    assert math.isclose(recente, math.sqrt(1.6))
    assert 1.0 < inteira < recente                      # a história inteira só captou parte da mudança


def test_camadas_neutras_devolvem_um_e_o_teto_limita_o_produto():
    h = cam.Historia()
    for _ in range(50):
        h.add(jogo(12, 12, 1, 1))
    c, f = cam.multiplicadores(["forca_chutes"], h, 1, 2)
    assert math.isclose(c["ataque_chute"], 1.0) and math.isclose(f["conversao_ataque"], 1.0)
    cfg = {**cam.CONFIG, "teto": (0.9, 1.1)}
    c, _ = cam.multiplicadores(["nivel_chutes"], h, 1, 2, cfg)    # nível 12/12,68 está dentro do teto
    assert 0.9 <= c["ataque_chute"] <= 1.1
    h2 = cam.Historia()
    h2.add(jogo(40, 40, 3, 3))
    c, _ = cam.multiplicadores(["nivel_chutes"], h2, 1, 2, cfg)   # 40/12,68 = 3,15 -> limitado a 1,1
    assert c["ataque_chute"] == 1.1
