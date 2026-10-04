#!/usr/bin/env python3
"""Protótipo v0 do simulador semi-Markov da bola (zonas x eventos x tempo) -- Achado 31.

Junta as peças medidas nos Achados 18 a 30 (StatsBomb, 4 ligas de 2015/16, resumos versionados em `dados_referencia/statsbomb/`) numa cadeia de Markov
com tempo de permanência e a CONFERE contra os totais por jogo que já medimos. É um protótipo ESTÁTICO (sem janela do jogo, sem nível dos times, sem
substituições nem cartões) para achar quais peças falham; não é um modelo de previsão.

ESTADO: (equipe com a bola, zona de 18 no referencial dela, relógio em segundos). Um SORTEIO por ação:
  1. desfecho da ação a partir da zona: continua para a zona w (18 destinos), chute ou perda (matriz de contagens 18 x 20 das 4 ligas, Achados 18/19);
  2. duração da ação: lognormal (mediana 1,36 s, média 1,66 s; Achado 27); chute 0,67/0,91 s;
  3. depois da ação, falta com probabilidade por faixa do campo (0,9 a 3,3 por 100 ações; 23% são de ataque; Achado 30);
  4. chute: resultado por faixa (gol/defendido/bloqueado/fora/sem direção/trave) e o que vem depois (saída de bola, tiro de meta, escanteio, goleiro,
     jogo corrido; Achado 30);
  5. perda: escanteio da MESMA equipe com a taxa por zona do Achado 25; senão recuperação do adversário na zona sorteada da matriz do Achado 29 (89,5% em
     jogo, instantânea; 10,5% reinício: lateral, tiro de meta, falta) com tempo morto lognormal por tipo (Achado 29);
  6. escanteio: gera chute da mesma equipe em 34,6% (gol em 7,6% dos chutes, Achado 30); senão o adversário recupera na defesa.
Cada tempo tem 45,8 min (1T) e 48,3 min (2T) de relógio (até o último evento, Achado 27).

Uso:  python scripts/simulador_cadeia_bola.py [--jogos 2000] [--semente 1] [--pasta dados_referencia/statsbomb]
"""

from __future__ import annotations

import argparse
import bisect
import collections
import json
import math
import os
import random
import sys

FAIXAS = ["defesa", "meio baixo", "meio alto", "ataque fora baixo", "ataque fora alto", "grande área"]
RESULTADOS = ["Goal", "Saved", "Blocked", "Off T", "Wayward", "Post"]
PROXIMAS = ["Regular Play", "From Goal Kick", "From Corner", "From Keeper", "From Kick Off", "From Throw In", "From Free Kick", "From Counter"]
DURACAO_TEMPO_S = (45.828 * 60, (93.283 - 45.0) * 60)        # 1T e 2T em segundos de relógio
ZONA_SAIDA = 4                                                # meio baixo, centro
ZONA_DEFESA_CENTRO = 1
FALTA_DE_ATAQUE = 0.23                                       # fração das faltas cometidas por quem tem a bola (Achado 30)
# CALIBRADOS no v1 (não medidos): o v0 gerava 21,8 laterais contra 46,3 e 9,4 escanteios contra 10,2 por jogo.
# Lateral que mantém a posse: bola desviada pelo adversário que sai e volta para quem a tinha (seguia uma "perda" em jogo).
P_LATERAL_MESMA = 0.085
ESCALA_ESCANTEIO = 1.0
# Folga entre o fim de uma ação e o início da seguinte (a duração do StatsBomb não cobre o intervalo todo): testado com 0,21 s e DESCARTADO (v1): derrubou chutes, gols e escanteios em ~10% porque as ações caíam em zonas menos ofensivas; ver Achado 31.
FOLGA_ENTRE_ACOES_S = 0.0
PROB_CHUTE_NO_ESCANTEIO = 0.346
PROB_GOL_CHUTE_ESCANTEIO = 0.0263 / 0.346


def lognormal_de(mediana: float, media: float) -> tuple[float, float]:
    """(mu, sigma) da lognormal com a mediana e a média dadas; sigma = sqrt(2 ln(media/mediana))."""
    if mediana <= 0 or media <= mediana:
        return (math.log(max(media, 1e-6)), 0.0)
    return (math.log(mediana), math.sqrt(2 * math.log(media / mediana)))


class Amostrador:
    """Sorteio de um índice a partir de pesos (bisect na soma acumulada)."""

    def __init__(self, pesos):
        self.cum, s = [], 0.0
        for p in pesos:
            s += float(p)
            self.cum.append(s)
        if s <= 0:
            raise ValueError("pesos somam zero")
        self.total = s

    def sortear(self, rng) -> int:
        return min(bisect.bisect_left(self.cum, rng.random() * self.total), len(self.cum) - 1)


class Parametros:
    def __init__(self, pasta: str = "dados_referencia/statsbomb"):
        lig = json.load(open(os.path.join(pasta, "ligas_2015_16.json")))
        rein = json.load(open(os.path.join(pasta, "reinicios_e_recuperacoes_ligas_2015_16.json")))
        chu = json.load(open(os.path.join(pasta, "chutes_laterais_escanteios_faltas_ligas_2015_16.json")))
        tmp = json.load(open(os.path.join(pasta, "tempo_la_liga_2015_16.json")))
        self.jogos_ref = sum(r["jogos"] for r in lig.values())
        # 1. matriz de desfecho por zona (alfa = 0,5 como nos testes fora da amostra)
        cont = [[0] * 20 for _ in range(18)]
        for r in lig.values():
            for o in range(18):
                for s in range(20):
                    cont[o][s] += r["contagens_18x20"][o][s]
        self.cont = cont
        self.acoes_por_zona = [sum(l) for l in cont]
        self.matriz = [Amostrador([v + 0.5 for v in l]) for l in cont]
        self.acoes_por_jogo_ref = sum(r["n_acoes"] for r in lig.values()) / self.jogos_ref
        # 2. durações
        d = tmp["duracao_acoes_s"]
        self.dur_acao = lognormal_de(1.36, (d["Pass"]["media"] * d["Pass"]["n"] + d["Carry"]["media"] * d["Carry"]["n"]) / (d["Pass"]["n"] + d["Carry"]["n"]))
        self.dur_chute = lognormal_de(d["Shot"]["mediana"], d["Shot"]["media"])
        # 3. falta por faixa (por ação)
        self.p_falta = [chu["faltas_por_faixa"][f]["faltas_por_100_acoes"] / 100.0 for f in FAIXAS]
        # 4. chute por faixa e o que vem depois
        cf = chu["chutes_por_faixa"]
        base = cf["meio alto"]
        self.res_chute, self.xg_faixa = [], []
        for f in FAIXAS:
            c = cf[f] if cf[f]["n"] >= 200 else base         # defesa e meio baixo têm 2 e 42 chutes: usa a faixa "meio alto"
            self.res_chute.append(Amostrador([c["resultados_pct"][r] for r in RESULTADOS]))
            self.xg_faixa.append(c["xg_medio"])
        self.res_nao_gol = [Amostrador([0.0] + [cf[f if cf[f]["n"] >= 200 else "meio alto"]["resultados_pct"][r] for r in RESULTADOS[1:]]) for f in FAIXAS]
        self.depois_chute = {}
        for res, dd in chu["depois_do_chute"].items():
            pp = dd["proxima_posse_pct"]
            pool = pp["Regular Play"] + pp["From Throw In"] + pp["From Free Kick"] + pp["From Counter"]
            mesma_pool = max(0.0, min(1.0, (dd["mesma_equipe_pct"] - pp["From Corner"]) / pool)) if pool > 0 else 0.0
            self.depois_chute[res] = (Amostrador([pp[k] for k in PROXIMAS]), mesma_pool)
        # 5. perda: recuperação, reinícios e escanteio da mesma equipe
        m = rein["matriz_recuperacao_18x18"]
        self.recup = [Amostrador([v + 0.5 for v in m[z]]) for z in range(18)]
        pe = rein["primeiro_evento_do_adversario_apos_perda"]
        n_perdas = sum(pe.values())
        self.p_reinicio = sum(v for k, v in pe.items() if k.startswith("reinício") and "Free Kick" not in k and "Corner" not in k) / n_perdas
        # tiro livre fica de fora: as faltas já são sorteadas por faixa depois de cada ação (senão contaria duas vezes; v0 dava 35,8 contra 30,6 por jogo)
        tipos = ["From Throw In", "From Goal Kick"]
        nomes = {"From Throw In": "reinício: Throw-in", "From Goal Kick": "reinício: Goal Kick"}
        self.tipo_reinicio = (tipos, Amostrador([pe.get(nomes[t] + "|", 0) for t in tipos]))
        morto = rein["tempo_morto_por_reinicio_s"]
        self.morto = {k: (lognormal_de(v["mediana"], v["media"]) if v["mediana"] > 0.5 else (None, v["media"])) for k, v in morto.items()}
        esc = {}
        for r in lig.values():
            for tipo, v in r["escanteios_origem_por_zona"].items():
                if tipo.startswith(("Passe/", "Cruzamento/")) or tipo in ("Dispossessed", "Miscontrol"):
                    acc = esc.setdefault("perda", [0] * 19)
                    acc[:] = [a + b for a, b in zip(acc, v)]
        self.q_escanteio_perda = [min(1.0, ESCALA_ESCANTEIO * esc["perda"][z] / max(cont[z][19], 1)) for z in range(18)]
        self.p_lateral_mesma = P_LATERAL_MESMA
        # 6. laterais: origem por faixa
        self.lateral_faixa = Amostrador([chu["laterais"].get(f, {"n": 0})["n"] for f in FAIXAS])
        # alvos observados por jogo
        j = chu["jogos"]
        n_chutes = sum(c["n"] for c in cf.values())
        self.alvos = {
            "ações": self.acoes_por_jogo_ref, "chutes": n_chutes / j,
            "gols": sum(c["n"] * c["resultados_pct"]["Goal"] / 100 for c in cf.values()) / j,
            "xG": sum(c["n"] * c["xg_medio"] for c in cf.values()) / j,
            "escanteios": chu["escanteios"]["n"] / j, "tiros livres": rein["reinicios_por_jogo"]["From Free Kick"],
            "laterais": rein["reinicios_por_jogo"]["From Throw In"], "tiros de meta": rein["reinicios_por_jogo"]["From Goal Kick"],
            "saídas de bola": rein["reinicios_por_jogo"]["From Kick Off"],
            "tempo morto (min)": sum(rein["tempo_morto_por_jogo_min"][k] for k in ("From Throw In", "From Free Kick", "From Goal Kick", "From Corner", "From Keeper", "From Kick Off")),
        }


def band(z: int) -> int:
    return z // 3


def lognorm(rng, par) -> float:
    mu, sigma = par
    return rng.lognormvariate(mu, sigma) if sigma > 0 else math.exp(mu)


def dead(rng, p: Parametros, padrao: str) -> float:
    par = p.morto[padrao]
    if par[0] is None:                                # mediana ~0: exponencial com a média
        return rng.expovariate(1.0 / max(par[1], 1e-6))
    return lognorm(rng, par)


def simular_partida(p: Parametros, rng: random.Random) -> dict:
    """Uma partida. Devolve contagens (ações, chutes, gols, escanteios, faltas, reinícios, tempo morto, posses, chutes por janela de 15 min)."""
    m: collections.Counter = collections.Counter()
    janela = [0] * 6                                     # chutes por janela de 15 min de relógio (0-15, 15-30, 30-45+, 45-60, 60-75, 75-90+)

    for tempo, duracao in enumerate(DURACAO_TEMPO_S):
        t = 0.0
        equipe = tempo % 2
        zona = ZONA_SAIDA
        m["posses"] += 1
        while t < duracao:
            s = p.matriz[zona].sortear(rng)
            m["acoes"] += 1
            antes = m["chutes"]
            if s == 18:                                  # chute
                t += lognorm(rng, p.dur_chute) + FOLGA_ENTRE_ACOES_S
                troca = resolver_chute(p, rng, m, zona)
            else:
                d = lognorm(rng, p.dur_acao)
                t += d + FOLGA_ENTRE_ACOES_S
                m["tempo_em_acao"] += d
                if s == 19:                              # perda
                    troca = resolver_perda(p, rng, m, zona)
                else:                                    # continua na zona s
                    zona = s
                    if rng.random() < p.p_falta[band(zona)]:
                        m["faltas"] += 1
                        m["tiros livres"] += 1
                        troca = ("adv" if rng.random() < FALTA_DE_ATAQUE else "mesma", None, dead(rng, p, "From Free Kick"), "falta")
                        if troca[0] == "adv":            # falta de ataque: o adversário cobra, na zona espelhada de recuperação
                            troca = ("adv", p.recup[zona].sortear(rng), troca[2], "falta")
                        else:
                            troca = ("mesma", zona, troca[2], "falta")
                    else:
                        continue
            janela[tempo * 3 + min(int(t // 900), 2)] += m["chutes"] - antes
            quem, nova_zona, tm, _ = troca
            if quem == "adv":
                equipe = 1 - equipe
                m["posses"] += 1
            zona = nova_zona
            t += tm
            m["tempo morto"] += tm
    return dict(m, janela_chutes=janela)


def resolver_chute(p: Parametros, rng: random.Random, m: collections.Counter, zona: int, forcar_gol: float | None = None):
    """Resolve um chute da zona `zona`. Devolve o próximo estado (quem, zona, tempo morto, motivo): quem = "mesma" (a equipe que chutou mantém a posse)
    ou "adv" (o adversário assume); a zona está no referencial de quem passa a ter a bola."""
    b = band(zona)
    m["chutes"] += 1
    m["xG"] += p.xg_faixa[b]
    if forcar_gol is not None:
        resultado = "Goal" if rng.random() < forcar_gol else RESULTADOS[p.res_nao_gol[b].sortear(rng)]
    else:
        resultado = RESULTADOS[p.res_chute[b].sortear(rng)]
    if resultado == "Goal":
        m["gols"] += 1
        m["saídas de bola"] += 1
        return ("adv", ZONA_SAIDA, dead(rng, p, "From Kick Off"), "gol")
    amostra, mesma_pool = p.depois_chute[resultado]
    proxima = PROXIMAS[amostra.sortear(rng)]
    if proxima == "From Corner":
        return resolver_escanteio(p, rng, m)
    if proxima == "From Goal Kick":
        m["tiros de meta"] += 1
        return ("adv", ZONA_DEFESA_CENTRO, dead(rng, p, "From Goal Kick"), "tiro de meta")
    if proxima == "From Keeper":
        return ("adv", ZONA_DEFESA_CENTRO, dead(rng, p, "From Keeper"), "goleiro")
    if proxima == "From Kick Off":
        return ("adv", ZONA_SAIDA, dead(rng, p, "From Kick Off"), "saída de bola")
    # jogo corrido, lateral, falta, contra-ataque: a posse pode ficar com quem chutou
    chave = proxima if proxima in ("From Throw In", "From Free Kick") else None
    tm = dead(rng, p, chave) if chave else 0.0
    if chave == "From Throw In":
        m["laterais"] += 1
    elif chave == "From Free Kick":
        m["tiros livres"] += 1
    if rng.random() < mesma_pool:
        return ("mesma", zona, tm, "mesma equipe")
    return ("adv", p.recup[zona].sortear(rng), tm, "adversário")


def resolver_escanteio(p: Parametros, rng: random.Random, m: collections.Counter):
    """Escanteio batido pela equipe que tinha a bola. 34,6% geram um chute dela (cuja continuação é a de um chute comum); senão o adversário rebate na defesa."""
    m["escanteios"] += 1
    tm = dead(rng, p, "From Corner")
    if rng.random() < PROB_CHUTE_NO_ESCANTEIO:
        quem, zona, tm2, motivo = resolver_chute(p, rng, m, 16, forcar_gol=PROB_GOL_CHUTE_ESCANTEIO)
        return (quem, zona, tm + tm2, motivo)
    return ("adv", ZONA_DEFESA_CENTRO, tm, "escanteio cortado")


def resolver_perda(p: Parametros, rng: random.Random, m: collections.Counter, zona: int):
    if rng.random() < p.q_escanteio_perda[zona]:         # a bola sai pela linha de fundo depois de a defesa tocá-la: escanteio da MESMA equipe
        return resolver_escanteio(p, rng, m)
    if rng.random() < p.p_lateral_mesma:                 # a bola desviada sai e o lateral é de quem a perdeu na ação: posse mantida, tempo morto de lateral
        m["laterais"] += 1
        return ("mesma", zona, dead(rng, p, "From Throw In"), "lateral mantido")
    z_adv = p.recup[zona].sortear(rng)
    if rng.random() < p.p_reinicio:
        tipos, amostra = p.tipo_reinicio
        tipo = tipos[amostra.sortear(rng)]
        m["laterais" if tipo == "From Throw In" else ("tiros de meta" if tipo == "From Goal Kick" else "tiros livres")] += 1
        return ("adv", z_adv, dead(rng, p, tipo), "reinício")
    return ("adv", z_adv, 0.0, "recuperação")


def simular(p: Parametros, n_jogos: int, semente: int) -> dict:
    rng = random.Random(semente)
    tot: collections.Counter = collections.Counter()
    for _ in range(n_jogos):
        r = simular_partida(p, rng)
        for k, v in r.items():
            if k != "janela_chutes":
                tot[k] += v
        for w, v in enumerate(r["janela_chutes"]):
            tot[f"janela_{w}"] += v
    return {k: v / n_jogos for k, v in tot.items()}


def relatorio(med: dict, p: Parametros) -> None:
    sim = {"ações": med.get("acoes", 0), "chutes": med.get("chutes", 0), "gols": med.get("gols", 0), "xG": med.get("xG", 0), "escanteios": med.get("escanteios", 0),
           "tiros livres": med.get("tiros livres", 0), "laterais": med.get("laterais", 0), "tiros de meta": med.get("tiros de meta", 0),
           "saídas de bola": med.get("saídas de bola", 0), "tempo morto (min)": med.get("tempo morto", 0) / 60.0}
    print(f"{'por jogo':22s} {'simulado':>10s} {'observado':>10s} {'sim/obs':>8s}")
    for k, obs in p.alvos.items():
        s = sim.get(k, 0.0)
        print(f"{k:22s} {s:10.2f} {obs:10.2f} {s / obs if obs else float('nan'):8.2f}")
    print(f"{'tempo em ação (min)':22s} {med.get('tempo_em_acao', 0) / 60:10.1f} {'46,1':>10s}")
    print(f"{'faltas':22s} {med.get('faltas', 0):10.2f} {30.0:10.2f} {med.get('faltas', 0) / 30.0:8.2f}")
    print(f"{'posses':22s} {med.get('posses', 0):10.1f} {195.2:10.1f}")
    print("chutes por janela de 15 min:", " ".join(f"{med.get(f'janela_{w}', 0):.2f}" for w in range(6)))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jogos", type=int, default=2000)
    ap.add_argument("--semente", type=int, default=1)
    ap.add_argument("--pasta", default="dados_referencia/statsbomb")
    a = ap.parse_args()
    par = Parametros(a.pasta)
    relatorio(simular(par, a.jogos, a.semente), par)
