#!/usr/bin/env python3
"""Simulador semi-Markov da bola (zonas x eventos x tempo) -- Achado 31, v3.

Junta as peças medidas nos Achados 18 a 31 (StatsBomb, 4 ligas de 2015/16, resumos versionados em `dados_referencia/statsbomb/`) numa cadeia de Markov
com tempo de permanência e a CONFERE contra os totais por jogo que já medimos. É um protótipo ESTÁTICO (sem janela do jogo, sem nível dos times, sem
substituições nem cartões) para achar quais peças falham; não é um modelo de previsão.

ESTADO: uma LINHA de ação = (equipe com a bola, zona de 18 no referencial dela, relógio em segundos). Um SORTEIO por linha:
  1. desfecho da ação a partir da zona: continua para a zona w (18 destinos), chute ou perda (matriz de contagens 18 x 20 das 4 ligas, Achados 18/19);
  2. duração da ação: lognormal (mediana 1,36 s, média 1,66 s; Achado 27); chute 0,67/0,91 s;
  3. chute: gol ou não pela faixa (Achado 30); depois do gol, saída de bola;
  4. PRÓXIMA LINHA pelo núcleo empírico (Achado 31, `residuo_entradas_ligas_2015_16.json`): dado de onde a linha começou e se foi continua/perda/chute,
     quem fica com a próxima linha (mesma equipe ou adversário), de que classe é (jogo corrido, lateral, tiro livre, tiro de meta, escanteio, saída de
     bola, pênalti) e em que zona começa. Substitui recuperação, reinício, escanteio, falta e lateral mantido que o v2 calibrava à mão;
  5. tempo morto lognormal por classe de bola parada (Achado 29).
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

FAIXAS = ["defesa", "meio baixo", "meio alto", "ataque fora baixo", "ataque fora alto", "grande área"]
RESULTADOS = ["Goal", "Saved", "Blocked", "Off T", "Wayward", "Post"]
DURACAO_TEMPO_S = (45.828 * 60, (93.283 - 45.0) * 60)        # 1T e 2T em segundos de relógio
ZONA_SAIDA = 4                                                # meio baixo, centro
CLASSE_PARA_REINICIO = {"lateral": "From Throw In", "tiro livre": "From Free Kick", "tiro de meta": "From Goal Kick", "escanteio": "From Corner",
                        "saída de bola": "From Kick Off", "pênalti": "From Free Kick"}
# Folga entre o fim de uma ação e o início da seguinte: a duração registrada pelo StatsBomb não cobre o intervalo todo (46,1 min em ação + 40,7 de tempo
# morto somam 86,8 dos ~94 min de relógio). CALIBRADA, não medida: 0,2 s por linha fecha os ~7 min que faltam.
FOLGA_ENTRE_ACOES_S = 0.2
CONTAGEM_DA_CLASSE = {"lateral": "laterais", "tiro livre": "tiros livres", "tiro de meta": "tiros de meta", "escanteio": "escanteios",
                      "saída de bola": "saídas de bola"}


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


def amostrador_de_chaves(contagens: dict) -> tuple[list, Amostrador]:
    chaves = list(contagens)
    return chaves, Amostrador([contagens[k] for k in chaves])


class Parametros:
    def __init__(self, pasta: str = "dados_referencia/statsbomb"):
        lig = json.load(open(os.path.join(pasta, "ligas_2015_16.json")))
        rein = json.load(open(os.path.join(pasta, "reinicios_e_recuperacoes_ligas_2015_16.json")))
        chu = json.load(open(os.path.join(pasta, "chutes_laterais_escanteios_faltas_ligas_2015_16.json")))
        tmp = json.load(open(os.path.join(pasta, "tempo_la_liga_2015_16.json")))
        nuc = json.load(open(os.path.join(pasta, "residuo_entradas_ligas_2015_16.json")))
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
        # 3. chute: gol ou não por faixa (defesa e meio baixo têm 2 e 42 chutes: usa a faixa "meio alto")
        cf = chu["chutes_por_faixa"]
        self.p_gol, self.xg_faixa = [], []
        for f in FAIXAS:
            c = cf[f] if cf[f]["n"] >= 200 else cf["meio alto"]
            self.p_gol.append(c["resultados_pct"]["Goal"] / 100.0)
            self.xg_faixa.append(c["xg_medio"])
        # 4. núcleo empírico: próxima linha depois de uma linha que começou na zona z
        nucleo = nuc["nucleo_pos_perda_e_chute"]

        def da_zona(tipo: str, z: int) -> dict:
            """Contagens da zona; zonas com menos de 30 linhas (chute na defesa, por exemplo) usam o agregado de todo o campo."""
            # a saída de bola só vem de gol (tratado à parte) ou do começo de um tempo; nos dados ela aparece também depois da última linha do 1T, que é artefato
            c = {k: v for k, v in nucleo.get(f"{tipo}|{z}", {}).items() if "|saída de bola|" not in k}
            if sum(c.values()) >= 30:
                return c
            agg: collections.Counter = collections.Counter()
            for k, v in nucleo.items():
                if k.startswith(tipo + "|"):
                    agg.update({kk: vv for kk, vv in v.items() if "|saída de bola|" not in kk})
            return dict(agg)

        self.apos_perda, self.apos_chute, self.p_quebra, self.apos_quebra = [], [], [], []
        for z in range(18):
            self.apos_perda.append(amostrador_de_chaves(da_zona("perda", z)))
            self.apos_chute.append(amostrador_de_chaves(da_zona("chute", z)))
            cz = da_zona("continua", z)
            total = sum(cz.values())
            quebra = {k: v for k, v in cz.items() if not k.startswith("mesma|recuperação|")}
            self.p_quebra.append(sum(quebra.values()) / total if total else 0.0)
            self.apos_quebra.append(amostrador_de_chaves(quebra) if quebra else None)
        # 5. tempo morto lognormal por tipo de reinício (Achado 29); mediana ~0 -> exponencial
        morto = rein["tempo_morto_por_reinicio_s"]
        self.morto = {k: (lognormal_de(v["mediana"], v["media"]) if v["mediana"] > 0.5 else (None, v["media"])) for k, v in morto.items()}
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


def proxima_linha(p: Parametros, rng: random.Random, amostra: tuple[list, Amostrador]) -> tuple[str, str, int]:
    chaves, am = amostra
    quem, classe, zona = chaves[am.sortear(rng)].split("|")
    return quem, classe, int(zona)


def simular_partida(p: Parametros, rng: random.Random) -> dict:
    """Uma partida. Devolve contagens (ações, chutes, gols, bolas paradas, tempo morto, posses, chutes por janela de 15 min) e a ocupação por classe/faixa."""
    m: collections.Counter = collections.Counter()
    janela = [0] * 6                                     # chutes por janela de 15 min de relógio (0-15, 15-30, 30-45+, 45-60, 60-75, 75-90+)

    for tempo, duracao in enumerate(DURACAO_TEMPO_S):
        t = 0.0
        zona = ZONA_SAIDA
        classe = "jogo"
        m["posses"] += 1
        while t < duracao:
            m[f"ent|{classe}|{band(zona)}"] += 1
            t += FOLGA_ENTRE_ACOES_S
            s = p.matriz[zona].sortear(rng)
            m["acoes"] += 1
            chutes_antes = m["chutes"]
            if s == 18:                                  # chute
                t += lognorm(rng, p.dur_chute)
                b = band(zona)
                m["chutes"] += 1
                m["xG"] += p.xg_faixa[b]
                if rng.random() < p.p_gol[b]:
                    m["gols"] += 1
                    quem, classe, nova = "adv", "saída de bola", ZONA_SAIDA
                else:
                    quem, classe, nova = proxima_linha(p, rng, p.apos_chute[zona])
            else:
                d = lognorm(rng, p.dur_acao)
                t += d
                m["tempo_em_acao"] += d
                if s == 19:                              # perda
                    quem, classe, nova = proxima_linha(p, rng, p.apos_perda[zona])
                elif p.apos_quebra[zona] is not None and rng.random() < p.p_quebra[zona]:   # a bola parou (falta, lateral, ...)
                    quem, classe, nova = proxima_linha(p, rng, p.apos_quebra[zona])
                else:                                    # a bola segue com a mesma equipe para a zona s
                    quem, classe, nova = "mesma", "continua", s
            janela[tempo * 3 + min(int(t // 900), 2)] += m["chutes"] - chutes_antes
            if quem == "adv":
                m["posses"] += 1
            if classe in CLASSE_PARA_REINICIO:
                m[CONTAGEM_DA_CLASSE.get(classe, "tiros livres")] += 1
                if classe == "tiro livre":
                    m["faltas"] += 1
                tm = dead(rng, p, CLASSE_PARA_REINICIO[classe])
                t += tm
                m["tempo morto"] += tm
            zona = nova
    return dict(m, janela_chutes=janela)


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
    ocup = [sum(v for k, v in med.items() if k.startswith("ent|") and k.endswith(f"|{b}")) for b in range(6)]
    obs = [sum(p.acoes_por_zona[b * 3:b * 3 + 3]) for b in range(6)]
    print("ocupação por faixa (% das ações), simulado/observado:", " ".join(f"{100 * o / sum(ocup):.1f}/{100 * x / sum(obs):.1f}" for o, x in zip(ocup, obs)))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jogos", type=int, default=2000)
    ap.add_argument("--semente", type=int, default=1)
    ap.add_argument("--pasta", default="dados_referencia/statsbomb")
    a = ap.parse_args()
    par = Parametros(a.pasta)
    relatorio(simular(par, a.jogos, a.semente), par)
