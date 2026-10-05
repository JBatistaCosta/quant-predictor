#!/usr/bin/env python3
"""Camadas de ajuste do simulador da cadeia da bola (Achado 47).

IDEIA: as transições medidas (StatsBomb + perfis por competição) são a BASE fixa. Tudo o que muda de jogo para jogo entra como CAMADAS: cada camada olha só o
histórico anterior ao jogo e devolve multiplicadores perto de 1,0 sobre quatro quantidades do simulador (`Multiplicadores`):
  ataque_chute / defesa_chute      -> chance de a linha de ação virar chute (de quem ataca / contra quem defende)
  conversao_ataque / conversao_defesa -> chance de o chute virar gol (de quem ataca / contra quem defende)
Os multiplicadores das camadas se COMBINAM multiplicando, com teto (CONFIG["teto"]). Toda camada que estima algo do histórico é encolhida em direção a 1,0
("mola": pseudo-jogos em CONFIG), então com pouco histórico ela quase não age. Quais camadas valem é decidido por ablação no backtest
(scripts/backtest_simulador_preditivo.py): liga-se uma camada por vez e ela fica só se a diferença de log-loss pareada tiver IC 95% a favor.

Para criar uma camada nova: escreva `def camada_x(h, casa, fora, cfg) -> (dict_casa, dict_fora)` com só as chaves que ela mexe e registre em CAMADAS.
`h` é a `Historia` (jogos anteriores); `casa`/`fora` são ids de time.
"""

from __future__ import annotations

import datetime
import math

CHUTES_POR_TIME_SIM_NEUTRO = 12.68      # simulador padrão, 400 jogos, semente 1
GOLS_POR_CHUTE_SIM_NEUTRO = 0.1017      # simulador padrão, 2.500 jogos, semente 11 (2,536 gols / 24,94 chutes)
CHAVES = ("ataque_chute", "defesa_chute", "conversao_ataque", "conversao_defesa")

# Resposta ao placar (Achado 52): por saldo de gols de QUEM CHUTA, truncado em -2..+2, relativa ao empate: (multiplicador de volume de chutes, multiplicador de qualidade).
# Estimada em 5 ligas, temporadas 2022-2024 (fora do teste de 2025/26). Volume = chutes por 90 min; qualidade = xG por chute. Ponderada por minutos entre perfis de força.
ESTADO_TABELA = {-2: (1.231, 0.966), -1: (1.185, 0.979), 0: (1.0, 1.0), 1: (0.883, 1.148), 2: (0.941, 1.235)}


# Resposta ao placar por CONTEXTO (mando x favoritismo), mesma base e método da tabela acima, relativa ao empate do próprio grupo. Chave "local|perfil": local = casa/fora/neutro,
# perfil = forte (Elo do time >= +corte sobre o adversário), fraco (<= -corte) ou parelho. O campo neutro não está registrado no banco (matches.is_neutral é falso em todos os jogos):
# "neutro" é a média simples de casa e fora, uma hipótese, não medição. Em liga as curvas são praticamente iguais entre os contextos (Achado 52).
ESTADO_TABELAS_CTX = {
    "casa|forte": {-2: (1.223, 0.977), -1: (1.178, 1.011), 0: (1.0, 1.0), 1: (0.862, 1.144), 2: (0.878, 1.271)},
    "casa|fraco": {-2: (1.265, 1.008), -1: (1.239, 1.01), 0: (1.0, 1.0), 1: (0.89, 1.132), 2: (0.909, 1.346)},
    "casa|parelho": {-2: (1.267, 0.908), -1: (1.193, 0.963), 0: (1.0, 1.0), 1: (0.869, 1.147), 2: (0.936, 1.149)},
    "fora|forte": {-2: (1.173, 0.936), -1: (1.159, 0.934), 0: (1.0, 1.0), 1: (0.873, 1.132), 2: (0.969, 1.252)},
    "fora|fraco": {-2: (1.285, 0.947), -1: (1.186, 0.989), 0: (1.0, 1.0), 1: (0.868, 1.2), 2: (0.895, 1.51)},
    "fora|parelho": {-2: (1.268, 0.993), -1: (1.203, 0.963), 0: (1.0, 1.0), 1: (0.881, 1.159), 2: (0.914, 1.167)},
    "neutro|forte": {-2: (1.198, 0.957), -1: (1.168, 0.972), 0: (1.0, 1.0), 1: (0.867, 1.138), 2: (0.923, 1.261)},
    "neutro|fraco": {-2: (1.275, 0.978), -1: (1.212, 1.0), 0: (1.0, 1.0), 1: (0.879, 1.166), 2: (0.902, 1.428)},
    "neutro|parelho": {-2: (1.268, 0.951), -1: (1.198, 0.963), 0: (1.0, 1.0), 1: (0.875, 1.153), 2: (0.925, 1.158)},
}


def perfil_de_elo(elo_dif, corte=100.0):
    """Favoritismo de um time pelo Elo dele menos o do adversário: 'forte' (>= corte), 'fraco' (<= -corte) ou 'parelho'. Elo ausente = parelho."""
    if elo_dif is None or elo_dif != elo_dif:
        return "parelho"
    return "forte" if elo_dif >= corte else ("fraco" if elo_dif <= -corte else "parelho")


def montar_estado_jogo(elo_dif_mandante=0.0, neutro=False, volume=True, qualidade=True, exp_volume=1.0, alvo=(1, 1), alvo_ctx=None, elo_corte=100.0):
    """`estado` de UM jogo, ajustado por mando (casa/fora/neutro) e favoritismo (Elo). `alvo` é o placar desejado de cada time (padrão +1, ganhar); `alvo_ctx` {"local|perfil": alvo}
    substitui o alvo do time cujo contexto casa com a chave (ex.: {"fora|fraco": 0} = o fraco que joga fora se contenta com o empate; aceita fracionário, que interpola)."""
    locais = ("neutro", "neutro") if neutro else ("casa", "fora")
    perfis = (perfil_de_elo(elo_dif_mandante, elo_corte), perfil_de_elo(None if elo_dif_mandante is None else -elo_dif_mandante, elo_corte))
    tabelas, alvos = [], []
    for i in (0, 1):
        chave = f"{locais[i]}|{perfis[i]}"
        tabelas.append({int(s): ((v ** exp_volume) if volume else 1.0, q if qualidade else 1.0) for s, (v, q) in ESTADO_TABELAS_CTX[chave].items()})
        alvos.append(alvo_ctx[chave] if alvo_ctx and chave in alvo_ctx else alvo[i])
    return {"tabelas": tabelas, "desvio": (1 - alvos[0], 1 - alvos[1])}


def montar_estado(tabela=None, volume=True, qualidade=True, exp_volume=1.0, alvo=(1, 1)):
    """dict `estado` para simular_partida. `volume`/`qualidade` ligam cada efeito; `exp_volume` amplifica o multiplicador de volume (o simulador realiza só ~50% em log
    do multiplicador de chute, Achado 51; a qualidade age direto na chance de gol e não precisa); `alvo` = placar desejado (saldo final satisfatório) de cada time."""
    tabela = tabela or ESTADO_TABELA
    return {"tabela": {int(s): ((v ** exp_volume) if volume else 1.0, q if qualidade else 1.0) for s, (v, q) in tabela.items()},
            "desvio": (1 - alvo[0], 1 - alvo[1])}


CONFIG = {
    "k_time": 8.0,            # pseudo-jogos que puxam a força de um time para 1,0
    "k_mando_gols": 50.0,     # pseudo-jogos que puxam o mando da conversão para 1,0
    "janela_mando": 200,      # nº de jogos mais recentes que as camadas *_janela olham (mando que acompanha mudança entre temporadas)
    # Expoentes de amplificação dos multiplicadores de chute (Achado 51). O simulador entrega só ~50% (em log) da razão de chutes pedida
    # (scripts/calibrar_elasticidade_chute.py: elasticidade 0,49 +- 0,02, igual para ataque e defesa), então o multiplicador precisa ser elevado a ~1/0,5 = 2.
    # 1,0 = sem amplificação (comportamento dos Achados 46 a 49). O valor 2,0 foi fixado pela calibração do próprio simulador, antes de olhar o teste.
    "exp_mando": 1.0,
    "exp_forca": 1.0,
    "teto": (0.6, 1.6),       # limites do produto das camadas, por chave
    "gols_fator": 1.0,        # fator de gols da calibração em outra temporada (camada gols_fator); 1,0 = sem fator
    # Força dos times por GOLS (Achado 57), no estilo do Dixon-Coles: ataque = gols feitos / média da liga, defesa = gols sofridos / média, sem ajuste pelo adversário.
    "k_gols": 20.0,           # pseudo-jogos que puxam a força por gols para 1,0 (gols são mais ruidosos que chutes: 20 contra 8 dos chutes; fixado antes de olhar o teste)
    "peso_gols": 0.5,         # camada forca_mista: força = chutes^(1-peso) x gols^peso (0 = só chutes, 1 = só gols)
    # Força por gols ajustada pelo adversário e com decaimento no tempo (Achado 58). Sem nenhum dos dois (padrão) a força por gols é a razão simples do Achado 57.
    "ajuste_adv_iter": 0,     # iterações do ajuste pelo adversário (ataque e defesa estimados juntos); 0 = sem ajuste
    "meia_vida_dias": None,   # meia-vida do peso de cada jogo anterior, em dias (EWMA); None = todos os jogos com o mesmo peso
    "elo_corte": 100.0,       # diferença de Elo a partir da qual um time é "forte" ou "fraco" na reação ao placar (Achado 52)
}


class Historia:
    """Acumula, só dos jogos já disputados, chutes e gols feitos/sofridos por time e os totais de mandante e visitante."""

    def __init__(self):
        self.pro, self.contra, self.n = {}, {}, {}
        self.gpro, self.gcontra = {}, {}                # gols feitos e sofridos por time
        self.lista = []                                 # (dia, mandante, visitante, gols casa, gols fora) de cada jogo, em ordem
        self.data_atual = None                          # dia (ISO) do jogo que vai ser previsto; quem chama define antes de pedir os multiplicadores
        self._cache_forca = {}
        self.soma_casa = self.soma_fora = 0.0          # chutes
        self.gols_casa = self.gols_fora = 0.0
        self.recentes = []                              # (chutes casa, chutes fora, gols casa, gols fora) de cada jogo, em ordem
        self.jogos = 0

    def add(self, j):
        for t, gf, gc in ((j["home"], j["hg"], j["ag"]), (j["away"], j["ag"], j["hg"])):
            self.gpro[t] = self.gpro.get(t, 0.0) + gf
            self.gcontra[t] = self.gcontra.get(t, 0.0) + gc
        for t, f, c in ((j["home"], j["hs"], j["as"]), (j["away"], j["as"], j["hs"])):
            self.pro[t] = self.pro.get(t, 0.0) + f
            self.contra[t] = self.contra.get(t, 0.0) + c
            self.n[t] = self.n.get(t, 0) + 1
        self.soma_casa += j["hs"]
        self.soma_fora += j["as"]
        self.gols_casa += j["hg"]
        self.gols_fora += j["ag"]
        self.recentes.append((j["hs"], j["as"], j["hg"], j["ag"]))
        self.lista.append((datetime.date.fromisoformat(str(j["date"])[:10]).toordinal() if j.get("date") else None, j["home"], j["away"], j["hg"], j["ag"]))
        self.jogos += 1

    def janela(self, n):
        """Somas (chutes casa, chutes fora, gols casa, gols fora) dos últimos `n` jogos (ou de todos, se houver menos) e quantos jogos entraram."""
        r = self.recentes[-n:]
        return (sum(x[0] for x in r), sum(x[1] for x in r), sum(x[2] for x in r), sum(x[3] for x in r)), len(r)

    def chutes_por_time(self):
        return (self.soma_casa + self.soma_fora) / (2 * self.jogos)

    def gols_por_time(self):
        return (self.gols_casa + self.gols_fora) / (2 * self.jogos)

    def gols_por_chute(self):
        return (self.gols_casa + self.gols_fora) / (self.soma_casa + self.soma_fora)


# ------------------------------------------------------------------ camadas
def camada_nivel_chutes(h, casa, fora, cfg):
    """Reescala os chutes do simulador para a média de chutes por time da história (o simulador neutro faz ~12,7)."""
    n = h.chutes_por_time() / CHUTES_POR_TIME_SIM_NEUTRO
    return {"ataque_chute": n}, {"ataque_chute": n}


def camada_mando_chutes(h, casa, fora, cfg):
    """Mandante chuta mais: raiz da razão chutes casa / fora da história, dividida entre quem joga em casa (+) e fora (-)."""
    m = math.sqrt(h.soma_casa / h.soma_fora) ** cfg.get("exp_mando", 1.0)
    return {"ataque_chute": m}, {"ataque_chute": 1.0 / m}


def camada_mando_chutes_janela(h, casa, fora, cfg):
    """Igual a `mando_chutes`, mas só com os últimos `janela_mando` jogos: segue a mudança de mando entre temporadas (Achado 47: a razão de chutes
    casa/fora foi de 1,13 em 2024/25 para 1,24 em 2025/26, e a história inteira ficou atrasada)."""
    (sc, sf, _, _), _ = h.janela(cfg["janela_mando"])
    m = math.sqrt(sc / sf) ** cfg.get("exp_mando", 1.0)
    return {"ataque_chute": m}, {"ataque_chute": 1.0 / m}


def camada_forca_chutes(h, casa, fora, cfg):
    """Ataque e defesa de cada time pelos chutes feitos e sofridos, encolhidos para 1,0 com `k_time` pseudo-jogos."""
    def forca(t):
        if h.n.get(t, 0) == 0:
            return 1.0, 1.0
        m, n, k = h.chutes_por_time(), h.n[t], cfg["k_time"]
        return (h.pro[t] + k * m) / ((n + k) * m), (h.contra[t] + k * m) / ((n + k) * m)
    ac, dc = forca(casa)
    af, df = forca(fora)
    e = cfg.get("exp_forca", 1.0)
    return {"ataque_chute": ac ** e, "defesa_chute": dc ** e}, {"ataque_chute": af ** e, "defesa_chute": df ** e}


def forca_gols_ajustada(h, k, meia_vida_dias, iteracoes):
    """Ataque e defesa de cada time pelos gols, com peso de cada jogo anterior 0,5^(dias desde o jogo / meia-vida) (None = peso 1), só jogos com data ANTERIOR à do
    jogo previsto (`h.data_atual`), e ajuste pelo adversário: ataque_i = gols feitos / gols esperados contra as defesas enfrentadas, defesa_i = gols sofridos / esperados
    contra os ataques enfrentados, iterado `iteracoes` vezes (0 = razão simples, sem ajuste). Mandante e visitante têm médias próprias (o mando não vira força).
    k pseudo-jogos de um time médio puxam cada força para 1,0. Normalizado a média 1 entre os times. -> {time: (ataque, defesa)}."""
    chave = (h.jogos, k, meia_vida_dias, iteracoes, h.data_atual)
    if chave in h._cache_forca:
        return h._cache_forca[chave]
    hoje = datetime.date.fromisoformat(str(h.data_atual)[:10]).toordinal() if h.data_atual else None
    jogos = []
    for dia, casa, fora, gc, gf in h.lista:
        if hoje is not None and dia is not None and dia >= hoje:
            continue
        w = 0.5 ** ((hoje - dia) / meia_vida_dias) if (meia_vida_dias and hoje is not None and dia is not None) else 1.0
        jogos.append((w, casa, fora, gc, gf))
    forca = {}
    sw = sum(w for w, *_ in jogos)
    if sw > 0:
        mh = sum(w * gc for w, _, _, gc, _ in jogos) / sw           # gols do mandante por jogo
        ma = sum(w * gf for w, _, _, _, gf in jogos) / sw           # gols do visitante por jogo
        mb = (mh + ma) / 2
        times = {t for _, c, f, _, _ in jogos for t in (c, f)}
        atk = {t: 1.0 for t in times}
        dfs = {t: 1.0 for t in times}
        for _ in range(max(iteracoes, 0) + 1):                      # 1ª passada = razão simples (força 1 para todos); as seguintes ajustam pelo adversário
            num_a, den_a = {t: k * mb for t in times}, {t: k * mb for t in times}
            num_d, den_d = {t: k * mb for t in times}, {t: k * mb for t in times}
            for w, c, f, gc, gf in jogos:
                num_a[c] += w * gc; den_a[c] += w * mh * dfs[f]          # ataque do mandante contra a defesa do visitante
                num_a[f] += w * gf; den_a[f] += w * ma * dfs[c]
                num_d[c] += w * gf; den_d[c] += w * ma * atk[f]          # defesa do mandante contra o ataque do visitante
                num_d[f] += w * gc; den_d[f] += w * mh * atk[c]
            atk = {t: num_a[t] / den_a[t] for t in times}
            dfs = {t: num_d[t] / den_d[t] for t in times}
            ma_, md_ = sum(atk.values()) / len(atk), sum(dfs.values()) / len(dfs)
            atk = {t: v / ma_ for t, v in atk.items()}
            dfs = {t: v / md_ for t, v in dfs.items()}
        forca = {t: (atk[t], dfs[t]) for t in times}
    h._cache_forca[chave] = forca
    return forca


def _forca_gols(h, t, k, cfg=None):
    """(ataque, defesa) pelos gols feitos e sofridos, encolhidos para 1,0 com `k` pseudo-jogos. Com `ajuste_adv_iter` > 0 ou `meia_vida_dias` no cfg: força ajustada
    pelo adversário e/ou com decaimento no tempo (`forca_gols_ajustada`)."""
    if cfg and (cfg.get("meia_vida_dias") or cfg.get("ajuste_adv_iter", 0) > 0):
        return forca_gols_ajustada(h, k, cfg.get("meia_vida_dias"), cfg.get("ajuste_adv_iter", 0)).get(t, (1.0, 1.0))
    if h.n.get(t, 0) == 0:
        return 1.0, 1.0
    m, n = h.gols_por_time(), h.n[t]
    return (h.gpro[t] + k * m) / ((n + k) * m), (h.gcontra[t] + k * m) / ((n + k) * m)


def _forca_chutes(h, t, k):
    if h.n.get(t, 0) == 0:
        return 1.0, 1.0
    m, n = h.chutes_por_time(), h.n[t]
    return (h.pro[t] + k * m) / ((n + k) * m), (h.contra[t] + k * m) / ((n + k) * m)


def _camada_forca(h, casa, fora, cfg, peso):
    """Força de cada time = chutes^(1-peso) x gols^peso (ataque e defesa), elevada ao expoente de amplificação `exp_forca` e aplicada ao VOLUME de chutes
    (os gols seguem do volume). Com peso 0 é a camada forca_chutes; com peso 1, só gols."""
    e = cfg.get("exp_forca", 1.0)
    out = []
    for t in (casa, fora):
        ac, dc = _forca_chutes(h, t, cfg["k_time"])
        ag, dg = _forca_gols(h, t, cfg["k_gols"], cfg)
        a = math.exp((1 - peso) * math.log(ac) + peso * math.log(ag))
        d = math.exp((1 - peso) * math.log(dc) + peso * math.log(dg))
        out.append({"ataque_chute": a ** e, "defesa_chute": d ** e})
    return out[0], out[1]


def camada_forca_gols(h, casa, fora, cfg):
    """Força dos times só por gols feitos e sofridos (substitui forca_chutes)."""
    return _camada_forca(h, casa, fora, cfg, 1.0)


def camada_forca_mista(h, casa, fora, cfg):
    """Força dos times por mistura geométrica de chutes e gols, com `peso_gols` (0 a 1) no gols (substitui forca_chutes)."""
    return _camada_forca(h, casa, fora, cfg, cfg["peso_gols"])


def camada_gols_nivel(h, casa, fora, cfg):
    """Nível de gols: gols por chute da história sobre o do simulador neutro, aplicado aos dois lados."""
    g = h.gols_por_chute() / GOLS_POR_CHUTE_SIM_NEUTRO
    return {"conversao_ataque": g}, {"conversao_ataque": g}


def camada_gols_mando(h, casa, fora, cfg):
    """Mando na conversão: razão (gols por chute em casa) / (gols por chute fora), encolhida por jogos/(jogos+k) e dividida entre os dois lados."""
    if h.gols_casa <= 0 or h.gols_fora <= 0:
        return {}, {}
    r = (h.gols_casa / h.soma_casa) / (h.gols_fora / h.soma_fora)
    w = h.jogos / (h.jogos + cfg["k_mando_gols"])
    m = math.exp(0.5 * w * math.log(r))
    return {"conversao_ataque": m}, {"conversao_ataque": 1.0 / m}


def camada_gols_mando_janela(h, casa, fora, cfg):
    """Igual a `gols_mando`, mas com a razão de gols por chute dos últimos `janela_mando` jogos."""
    (sc, sf, gc, gf), n = h.janela(cfg["janela_mando"])
    if gc <= 0 or gf <= 0:
        return {}, {}
    r = (gc / sc) / (gf / sf)
    w = n / (n + cfg["k_mando_gols"])
    m = math.exp(0.5 * w * math.log(r))
    return {"conversao_ataque": m}, {"conversao_ataque": 1.0 / m}


def camada_gols_fator(h, casa, fora, cfg):
    """Fator fixo de gols (multiplica a conversão dos dois lados). Vem da calibração do total de gols numa temporada DIFERENTE da avaliada (Achado 54): gols reais por jogo
    sobre gols simulados por jogo da configuração. 1,0 = sem fator."""
    g = cfg.get("gols_fator", 1.0)
    return {"conversao_ataque": g}, {"conversao_ataque": g}


CAMADAS = {
    "nivel_chutes": camada_nivel_chutes,
    "mando_chutes": camada_mando_chutes,
    "forca_chutes": camada_forca_chutes,
    "forca_gols": camada_forca_gols,
    "forca_mista": camada_forca_mista,
    "gols_nivel": camada_gols_nivel,
    "gols_mando": camada_gols_mando,
    "gols_fator": camada_gols_fator,
    "mando_chutes_janela": camada_mando_chutes_janela,
    "gols_mando_janela": camada_gols_mando_janela,
}


def multiplicadores(camadas, h, casa, fora, cfg=None):
    """Combina as camadas pedidas -> (dict do mandante, dict do visitante) com as quatro chaves, já com teto."""
    cfg = cfg or CONFIG
    lo, hi = cfg["teto"]
    out = ({k: 1.0 for k in CHAVES}, {k: 1.0 for k in CHAVES})
    for nome in camadas:
        dc, df = CAMADAS[nome](h, casa, fora, cfg)
        for alvo, d in ((out[0], dc), (out[1], df)):
            for k, v in d.items():
                alvo[k] *= v
    for alvo in out:
        for k in CHAVES:
            alvo[k] = min(max(alvo[k], lo), hi)
    return out
