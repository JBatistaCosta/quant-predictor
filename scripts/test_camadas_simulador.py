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


def jogo_d(data, casa, fora, hg, ag):
    return {"home": casa, "away": fora, "hs": 12, "as": 12, "hg": hg, "ag": ag, "date": data}


def test_forca_por_gols_ajuste_pelo_adversario_decaimento_e_sem_futuro():
    h = cam.Historia()
    # times 1 e 2 jogam sempre em casa e fazem 3 gols por jogo, mas 1 enfrenta o time 9 (que também sofre contra 8) e 2 enfrenta o 8; 8 e 9 empatam entre si
    for d in range(1, 21):
        h.add(jogo_d(f"2025-01-{d:02d}", 1, 9, 3, 0))
        h.add(jogo_d(f"2025-01-{d:02d}", 2, 8, 3, 0))
        h.add(jogo_d(f"2025-01-{d:02d}", 9, 8, 1, 1))
        h.add(jogo_d(f"2025-01-{d:02d}", 3, 4, 1, 1))
    h.data_atual = "2025-02-01"
    simples = cam.forca_gols_ajustada(h, 5.0, None, 0)
    ajust = cam.forca_gols_ajustada(h, 5.0, None, 30)
    assert abs(simples[1][0] - simples[2][0]) < 1e-9                                 # sem ajuste, 1 e 2 têm o mesmo ataque (mesmos gols)
    assert ajust[1][0] != ajust[2][0]                                                # com ajuste, o ataque muda conforme o adversário enfrentado
    assert abs(sum(a for a, _ in ajust.values()) / len(ajust) - 1.0) < 1e-9          # normalizado a média 1
    # decaimento: jogos antigos pesam menos; se o time 3 passou a marcar muito só no fim, o ataque com decaimento curto fica maior que o sem decaimento
    h2 = cam.Historia()
    for d in range(1, 21):
        h2.add(jogo_d(f"2025-01-{d:02d}", 3, 4, 0 if d <= 12 else 4, 1))
        for a_, b_ in ((5, 6), (7, 8), (9, 10), (11, 12), (13, 14), (15, 16)):       # outros times estáveis mantêm a média da liga
            h2.add(jogo_d(f"2025-01-{d:02d}", a_, b_, 1, 1))
    h2.data_atual = "2025-02-01"
    assert cam.forca_gols_ajustada(h2, 0.5, 5.0, 0)[3][0] > cam.forca_gols_ajustada(h2, 0.5, None, 0)[3][0]      # (k pequeno: o decaimento reduz o peso dos dados, e o prior de k jogos não decai)
    # sem futuro: jogos com a data do jogo previsto (ou depois) não entram
    h2.data_atual = "2025-01-10"
    n_antes = sum(1 for d in h2.lista if d[0] < __import__("datetime").date(2025, 1, 10).toordinal())
    assert n_antes == 9 * 7
    antes = cam.forca_gols_ajustada(h2, 5.0, None, 0)
    h2.add(jogo_d("2025-01-10", 3, 4, 9, 9))
    h2.add(jogo_d("2025-02-20", 3, 4, 9, 9))
    assert cam.forca_gols_ajustada(h2, 5.0, None, 0) == antes                        # o jogo de 10/01 (mesmo dia) e o de fevereiro (futuro) são ignorados


def test_forca_elo_segue_a_diferenca_de_elo_e_e_neutra_sem_dados_ou_com_k_zero():
    h = cam.Historia()
    cfg = {**cam.CONFIG, "elo_k": 0.5, "elo_fonte": "elox"}
    assert cam.camada_forca_elo(h, 1, 2, cfg) == ({}, {})                         # sem Elo do jogo: não muda nada
    h.elo = {"elox": 0.0}
    mc, mf = cam.camada_forca_elo(h, 1, 2, cfg)
    assert mc["ataque_chute"] == 1.0 and mf["defesa_chute"] == 1.0                # times iguais
    h.elo = {"elox": 200.0, "elod": -100.0}
    mc, mf = cam.camada_forca_elo(h, 1, 2, cfg)
    x = 200.0 / 400.0 * math.log(10.0)
    m = math.exp(0.5 * x / 2)
    assert abs(mc["ataque_chute"] - m) < 1e-12 and abs(mc["defesa_chute"] - 1 / m) < 1e-12
    assert abs(mf["ataque_chute"] - 1 / m) < 1e-12 and abs(mf["defesa_chute"] - m) < 1e-12
    # razão de chutes pedida da casa sobre a de fora = exp(2*k*x)
    assert abs((mc["ataque_chute"] * mf["defesa_chute"]) / (mf["ataque_chute"] * mc["defesa_chute"]) - math.exp(2 * 0.5 * x)) < 1e-9
    assert cam.camada_forca_elo(h, 1, 2, {**cfg, "elo_fonte": "elod"})[0]["ataque_chute"] < 1.0       # a fonte escolhe a nota
    assert cam.camada_forca_elo(h, 1, 2, {**cfg, "elo_k": 0.0}) == ({}, {})
    assert abs(cam.camada_forca_elo(h, 1, 2, {**cfg, "peso_elo": 0.5})[0]["ataque_chute"] - math.exp(0.25 * x / 2)) < 1e-12
    mult = cam.multiplicadores(["forca_elo"], h, 1, 2, cfg)                       # entra na combinação de camadas
    assert mult[0]["ataque_chute"] > 1.0 > mult[1]["ataque_chute"]


def test_constantes_do_simulador_neutro_seguem_o_regime_de_quebra():
    h = cam.Historia()
    for _ in range(10):
        h.add(jogo(10, 10, 1, 1))
    antigo = cam.camada_nivel_chutes(h, 1, 2, {**cam.CONFIG, "quebra_corrigida": False})[0]["ataque_chute"]
    corrigido = cam.camada_nivel_chutes(h, 1, 2, {**cam.CONFIG, "quebra_corrigida": True})[0]["ataque_chute"]
    assert abs(antigo - 10 / cam.CHUTES_POR_TIME_SIM_NEUTRO) < 1e-12
    assert abs(corrigido - 10 / cam.CHUTES_POR_TIME_SIM_NEUTRO_CORRIGIDO) < 1e-12 and corrigido > antigo
    g_antigo = cam.camada_gols_nivel(h, 1, 2, {**cam.CONFIG, "quebra_corrigida": False})[0]["conversao_ataque"]
    g_corr = cam.camada_gols_nivel(h, 1, 2, {**cam.CONFIG, "quebra_corrigida": True})[0]["conversao_ataque"]
    assert g_corr > g_antigo
    assert cam.CONFIG["quebra_corrigida"] is True                                  # padrão da CONFIG: regime corrigido
