#!/usr/bin/env python3
"""Validação do simulador em competições e temporadas que ele NÃO viu (Achado 35).

Os parâmetros (matriz de transição, núcleo da próxima linha, tempos mortos, memória da posse, chutes) vêm só de La Liga, Premier League, Serie A e Ligue 1 de 2015/16.
Aqui mede-se, nos eventos completos de cada conjunto, os totais por jogo (linhas de ação, chutes, gols, escanteios, laterais, tiros livres, tiros de meta), a ocupação
do campo por faixa e a posse (corridas por jogo, linhas por corrida), e compara-se com o simulador (times neutros, mesmo número de partidas). Os dados observados trazem
erro-padrão da média; diferença > 2 erros-padrão fica marcada.
Uso: python scripts/validar_simulador_outras_competicoes.py [--sims 3000] [--saida arq.json]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analisar_residuo_entradas_statsbomb as r  # noqa: E402
import comparar_competicoes_statsbomb as c  # noqa: E402
import comparar_posse_xt_momentum as cp  # noqa: E402
import metricas_posse_xt_momentum as M  # noqa: E402
import simulador_cadeia_bola as s  # noqa: E402

PASTA = "dados_referencia/statsbomb"
CONJUNTOS = {            # nome -> (grupo, filtro de rótulo)
    "Ligas recentes (clube único: Barcelona, PSG, Leverkusen)": ("ligas_recentes", lambda t: True),
    "Euro 2020 e 2024": ("euro", lambda t: True),
    "Copa América 2024": ("copa_america", lambda t: True),
    "Copas do Mundo 2018 e 2022": ("copa_mundo", lambda t: t.endswith("2018") or t.endswith("2022")),
}
CHAVES = ["acoes", "chutes", "gols", "escanteios", "laterais", "tiros_livres", "tiros_de_meta"]
CLASSE = {"lateral": "laterais", "tiro livre": "tiros_livres", "tiro de meta": "tiros_de_meta", "escanteio": "escanteios"}


def partida_observada(ev: list[dict]) -> dict:
    linhas = r.linhas_com_janela(ev)
    reais = cp.linhas_reais(ev)
    m = {k: 0 for k in CHAVES}
    m["acoes"] = len(linhas)
    for cl, *_ in linhas:
        if cl in CLASSE:
            m[CLASSE[cl]] += 1
    m["chutes"] = sum(1 for x in reais if x[2] == "chute")
    m["gols"] = sum(x[5] for x in reais)
    m["faixa"] = [sum(1 for x in linhas if x[1] // 3 == b) for b in range(6)]
    return {"m": m, "linhas": reais}


def media_e_ep(v: list[float]) -> tuple[float, float]:
    n = len(v)
    mu = sum(v) / n
    return mu, (math.sqrt(sum((x - mu) ** 2 for x in v) / (n - 1) / n) if n > 1 else 0.0)


def simular_conjunto(par, n: int, semente: int) -> tuple[dict, dict]:
    rng = random.Random(semente)
    acc = {k: [] for k in CHAVES}
    faixas = [0] * 6
    partidas = []
    for i in range(n):
        reg: list = [] if i < 1000 else None
        res = s.simular_partida(par, rng, registro=reg)
        for k, ch in (("acoes", "acoes"), ("chutes", "chutes"), ("gols", "gols"), ("escanteios", "escanteios"), ("laterais", "laterais"), ("tiros_livres", "tiros livres"), ("tiros_de_meta", "tiros de meta")):
            acc[k].append(res.get(ch, 0))
        for b in range(6):
            faixas[b] += sum(v for kk, v in res.items() if kk.startswith("ent|") and kk.endswith(f"|{b}"))
        if reg is not None:
            partidas.append(reg)
    out = {k: sum(v) / n for k, v in acc.items()}
    out["faixa_pct"] = [100 * f / sum(faixas) for f in faixas]
    return out, M.calcular(partidas)


def main(sims: int) -> dict:
    par = s.Parametros(PASTA)
    resultado = {}
    for nome, (grupo, filtro) in CONJUNTOS.items():
        base = os.path.join(PASTA, "completo")
        rotulos = sorted(json.load(open(os.path.join(base, grupo, d, "INDICE.json")))["rotulo"] for d in os.listdir(os.path.join(base, grupo)))
        partidas = []
        for rot in rotulos:
            if filtro(rot):
                partidas += [partida_observada(reg["eventos"]) for reg in c.carregar_completo(base, grupo, rot)]
        obs = {k: media_e_ep([p["m"][k] for p in partidas]) for k in CHAVES}
        faixas = [sum(p["m"]["faixa"][b] for p in partidas) for b in range(6)]
        obs_faixa = [100 * f / sum(faixas) for f in faixas]
        obs_posse = M.calcular([p["linhas"] for p in partidas])
        sim, sim_posse = simular_conjunto(par, sims, 11)
        resultado[nome] = {"jogos": len(partidas), "obs": obs, "obs_faixa_pct": obs_faixa, "sim": {k: sim[k] for k in CHAVES}, "sim_faixa_pct": sim["faixa_pct"],
                           "posse": {k: (obs_posse[k], sim_posse[k]) for k in ("corridas_por_jogo", "linhas_por_corrida_media", "duracao_corrida_media_s", "autocorrelacao_chutes_5min_lag1", "chutes_ate_120s_do_anterior_pct")}}
    return resultado


def imprimir(res: dict) -> None:
    for nome, v in res.items():
        print(f"\n{nome} -- {v['jogos']} jogos")
        print(f"  {'por jogo':14s} {'real ± EP':>16s} {'simulado':>9s} {'sim/real':>9s}")
        for k in CHAVES:
            mu, ep = v["obs"][k]
            sm = v["sim"][k]
            marca = " *" if ep and abs(sm - mu) > 2 * ep else ""
            print(f"  {k:14s} {mu:9.2f} ± {ep:5.2f} {sm:9.2f} {sm / mu:9.2f}{marca}")
        print("  ocupação por faixa (%), real | simulado: " + "  ".join(f"{o:.1f}|{x:.1f}" for o, x in zip(v["obs_faixa_pct"], v["sim_faixa_pct"])))
        print("  posse (real | simulado): " + "; ".join(f"{k.replace('_', ' ')} {a:.3f}|{b:.3f}" for k, (a, b) in v["posse"].items()))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sims", type=int, default=3000)
    ap.add_argument("--saida", default="")
    a = ap.parse_args()
    res = main(a.sims)
    imprimir(res)
    if a.saida:
        json.dump(res, open(a.saida, "w"), ensure_ascii=False)
