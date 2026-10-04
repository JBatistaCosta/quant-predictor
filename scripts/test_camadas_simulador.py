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


def test_expoente_amplifica_mando_e_forca_sem_mexer_no_padrao():
    h = cam.Historia()
    for _ in range(40):
        h.add(jogo(15, 10, 2, 1, casa=1, fora=2))
        h.add(jogo(12, 12, 1, 1, casa=3, fora=4))
    base = {**cam.CONFIG}
    amp = {**cam.CONFIG, "exp_mando": 2.0, "exp_forca": 2.0}
    m1 = cam.camada_mando_chutes(h, 1, 2, base)[0]["ataque_chute"]
    m2 = cam.camada_mando_chutes(h, 1, 2, amp)[0]["ataque_chute"]
    assert math.isclose(m2, m1 ** 2)
    f1 = cam.camada_forca_chutes(h, 1, 3, base)[0]
    f2 = cam.camada_forca_chutes(h, 1, 3, amp)[0]
    assert math.isclose(f2["ataque_chute"], f1["ataque_chute"] ** 2) and math.isclose(f2["defesa_chute"], f1["defesa_chute"] ** 2)
    assert cam.CONFIG["exp_mando"] == 1.0 and cam.CONFIG["exp_forca"] == 1.0          # o padrão não amplifica


def test_estado_por_contexto_mando_favoritismo_e_alvo():
    assert cam.perfil_de_elo(150) == "forte" and cam.perfil_de_elo(-150) == "fraco" and cam.perfil_de_elo(30) == "parelho" and cam.perfil_de_elo(None) == "parelho"
    e = cam.montar_estado_jogo(elo_dif_mandante=200.0)                                  # mandante forte, visitante fraco
    assert e["tabelas"][0][1] == cam.ESTADO_TABELAS_CTX["casa|forte"][1] and e["tabelas"][1][1] == cam.ESTADO_TABELAS_CTX["fora|fraco"][1]
    assert e["desvio"] == (0, 0)
    n = cam.montar_estado_jogo(elo_dif_mandante=0.0, neutro=True)                       # campo neutro: tabela "neutro|parelho" para os dois
    assert n["tabelas"][0] == n["tabelas"][1] and n["tabelas"][0][1] == cam.ESTADO_TABELAS_CTX["neutro|parelho"][1]
    a = cam.montar_estado_jogo(elo_dif_mandante=-200.0, alvo_ctx={"fora|forte": 0, "casa|fraco": 0.5})   # visitante forte se contenta com o empate; mandante fraco meio caminho
    assert a["desvio"] == (0.5, 1)
    # tabela por time: o simulador usa a do time com a bola
    import simulador_cadeia_bola as s
    assert s.mult_estado({-2: (1.2, .9), -1: (1.1, .95), 0: (1.0, 1.0), 1: (.9, 1.1), 2: (.8, 1.2)}, 0.5) == (0.95, 1.05)


def test_ctx_todas_as_chaves_e_empate_neutro():
    for l in ("casa", "fora", "neutro"):
        for p in ("forte", "fraco", "parelho"):
            t = cam.ESTADO_TABELAS_CTX[f"{l}|{p}"]
            assert set(t) == {-2, -1, 0, 1, 2} and t[0] == (1.0, 1.0)
            assert t[-1][0] > 1.0 > t[1][0] and t[1][1] > 1.0                               # perdendo chuta mais; ganhando chuta menos e com mais qualidade


def test_gols_fator_multiplica_a_conversao_dos_dois_lados_e_padrao_e_neutro():
    h = cam.Historia()
    for _ in range(30):
        h.add(jogo(12, 12, 1, 1))
    c, f = cam.multiplicadores(["gols_fator"], h, 1, 2)
    assert c["conversao_ataque"] == 1.0 and f["conversao_ataque"] == 1.0
    c, f = cam.multiplicadores(["gols_fator"], h, 1, 2, {**cam.CONFIG, "gols_fator": 1.06})
    assert c["conversao_ataque"] == 1.06 and f["conversao_ataque"] == 1.06


def jogo_g(hs, as_, hg, ag, casa, fora):
    return {"home": casa, "away": fora, "hs": hs, "as": as_, "hg": hg, "ag": ag}


def test_forca_por_gols_segue_os_gols_e_e_encolhida_e_mistura_reverte_aos_chutes():
    h = cam.Historia()
    for _ in range(40):                                  # time 1 faz 3 gols e sofre 0; time 2 o contrário; chutes iguais; time 3 e 4 neutros
        h.add(jogo_g(10, 10, 3, 0, 1, 2))
        h.add(jogo_g(10, 10, 1, 1, 3, 4))
    cfg = {**cam.CONFIG, "exp_forca": 1.0}
    c, f = cam.camada_forca_gols(h, 1, 2, cfg)
    assert c["ataque_chute"] > 1.2 and c["defesa_chute"] < 0.9                      # time 1: ataca muito e quase não sofre
    assert f["ataque_chute"] < 0.9 and f["defesa_chute"] > 1.2
    g = h.gols_por_time()
    n, k = h.n[1], cfg["k_gols"]
    assert abs(c["ataque_chute"] - (h.gpro[1] + k * g) / ((n + k) * g)) < 1e-12     # encolhimento por k pseudo-jogos
    cfg_k = {**cfg, "k_gols": 400.0}                                                # mais pseudo-jogos: mais perto de 1
    assert abs(cam.camada_forca_gols(h, 1, 2, cfg_k)[0]["ataque_chute"] - 1) < abs(c["ataque_chute"] - 1)
    # mistura com peso 0 = camada de chutes (chutes iguais -> força 1); peso 1 = só gols
    m0 = cam.camada_forca_mista(h, 1, 2, {**cfg, "peso_gols": 0.0})
    ch = cam.camada_forca_chutes(h, 1, 2, cfg)
    assert all(abs(m0[i][k_] - ch[i][k_]) < 1e-12 for i in range(2) for k_ in ("ataque_chute", "defesa_chute"))
    m1 = cam.camada_forca_mista(h, 1, 2, {**cfg, "peso_gols": 1.0})
    assert all(abs(m1[i][k_] - cam.camada_forca_gols(h, 1, 2, cfg)[i][k_]) < 1e-12 for i in range(2) for k_ in ("ataque_chute", "defesa_chute"))
    assert cam.camada_forca_gols(cam.Historia(), 1, 2, cfg)[0] == {"ataque_chute": 1.0, "defesa_chute": 1.0}   # sem história: neutro
