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

import math

CHUTES_POR_TIME_SIM_NEUTRO = 12.68      # simulador padrão, 400 jogos, semente 1
GOLS_POR_CHUTE_SIM_NEUTRO = 0.1017      # simulador padrão, 2.500 jogos, semente 11 (2,536 gols / 24,94 chutes)
CHAVES = ("ataque_chute", "defesa_chute", "conversao_ataque", "conversao_defesa")

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
}


class Historia:
    """Acumula, só dos jogos já disputados, chutes e gols feitos/sofridos por time e os totais de mandante e visitante."""

    def __init__(self):
        self.pro, self.contra, self.n = {}, {}, {}
        self.soma_casa = self.soma_fora = 0.0          # chutes
        self.gols_casa = self.gols_fora = 0.0
        self.recentes = []                              # (chutes casa, chutes fora, gols casa, gols fora) de cada jogo, em ordem
        self.jogos = 0

    def add(self, j):
        for t, f, c in ((j["home"], j["hs"], j["as"]), (j["away"], j["as"], j["hs"])):
            self.pro[t] = self.pro.get(t, 0.0) + f
            self.contra[t] = self.contra.get(t, 0.0) + c
            self.n[t] = self.n.get(t, 0) + 1
        self.soma_casa += j["hs"]
        self.soma_fora += j["as"]
        self.gols_casa += j["hg"]
        self.gols_fora += j["ag"]
        self.recentes.append((j["hs"], j["as"], j["hg"], j["ag"]))
        self.jogos += 1

    def janela(self, n):
        """Somas (chutes casa, chutes fora, gols casa, gols fora) dos últimos `n` jogos (ou de todos, se houver menos) e quantos jogos entraram."""
        r = self.recentes[-n:]
        return (sum(x[0] for x in r), sum(x[1] for x in r), sum(x[2] for x in r), sum(x[3] for x in r)), len(r)

    def chutes_por_time(self):
        return (self.soma_casa + self.soma_fora) / (2 * self.jogos)

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


CAMADAS = {
    "nivel_chutes": camada_nivel_chutes,
    "mando_chutes": camada_mando_chutes,
    "forca_chutes": camada_forca_chutes,
    "gols_nivel": camada_gols_nivel,
    "gols_mando": camada_gols_mando,
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
