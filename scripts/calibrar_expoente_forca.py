#!/usr/bin/env python3
"""Calibra o amortecimento (expoente) da força dos times NO TREINO (quartos 0 e 2) -- Achado 32.

Cada time joga `--sims` partidas simuladas contra um adversário médio; compara-se, no nível do time (80 pontos, pouco ruído), o previsto com o observado no
treino: linhas de ação por jogo (posse), chutes a favor e chutes contra. Reporta correlação e inclinação (inclinação 1 = dispersão correta; < 1 = o simulador
exagera as diferenças entre times). Uso: python scripts/calibrar_expoente_forca.py [--sims 150] [--gamas-perda 0,0.5,1] [--gamas-chute 1]
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import forca_dos_times as F  # noqa: E402
import simulador_cadeia_bola as s  # noqa: E402

PASTA = "dados_referencia/statsbomb"


def alvo_observado(dados: dict, quartos: tuple) -> dict:
    """Por time, no treino: linhas de ação a favor por jogo, chutes a favor e contra por jogo."""
    jogos, linhas, chutes_pro, chutes_contra = {}, {}, {}, {}
    for m in dados["partidas"]:
        if m["q"] not in quartos:
            continue
        for t, ch, ch_adv in ((str(m["casa"]), m["chutes"][0], m["chutes"][1]), (str(m["fora"]), m["chutes"][1], m["chutes"][0])):
            jogos[t] = jogos.get(t, 0) + 1
            chutes_pro[t] = chutes_pro.get(t, 0) + ch
            chutes_contra[t] = chutes_contra.get(t, 0) + ch_adv
    for q in quartos:
        for t, bloco in dados["papel"][str(q)]["ataque"].items():
            linhas[t] = linhas.get(t, 0) + sum(sum(z[:3]) for z in bloco)
    return {t: {"linhas": linhas[t] / jogos[t], "pro": chutes_pro[t] / jogos[t], "contra": chutes_contra[t] / jogos[t]} for t in jogos}


def simular_time(args):
    t, multiplicadores, janela, sims, semente = args
    par = s.Parametros(PASTA)
    rng = random.Random(semente)
    acc = {"linhas": 0, "pro": 0, "contra": 0}
    for i in range(sims):
        r = s.simular_partida(par, rng, (multiplicadores, s.NEUTRO) if i % 2 == 0 else (s.NEUTRO, multiplicadores), janela)
        e = 0 if i % 2 == 0 else 1
        acc["linhas"] += r.get(f"acoes_{e}", 0)
        acc["pro"] += r.get(f"chutes_{e}", 0)
        acc["contra"] += r.get(f"chutes_{1 - e}", 0)
    return t, {k: v / sims for k, v in acc.items()}


def inclinacao(prev: list[float], obs: list[float]) -> float:
    n = len(prev)
    mp_, mo = sum(prev) / n, sum(obs) / n
    sxx = sum((x - mp_) ** 2 for x in prev)
    return sum((x - mp_) * (y - mo) for x, y in zip(prev, obs)) / sxx if sxx else 0.0


def avaliar(dados, adj, alvo, gama_chute, gama_perda, sims, semente):
    tarefas = [(t, s.Multiplicadores.de_dict(v, gama_chute, gama_perda), adj["janela"], sims, semente + i) for i, (t, v) in enumerate(adj["times"].items()) if t in alvo]
    with mp.Pool(4) as pool:
        res = dict(pool.map(simular_time, tarefas))
    linha = {"gama_chute": gama_chute, "gama_perda": gama_perda}
    for k in ("linhas", "pro", "contra"):
        prev, obs = [res[t][k] for t in res], [alvo[t][k] for t in res]
        linha[k] = {"corr": F.pearson(prev, obs), "inclinacao_obs_sobre_prev": inclinacao(prev, obs), "dp_prev": (sum((x - sum(prev) / len(prev)) ** 2 for x in prev) / len(prev)) ** 0.5,
                    "dp_obs": (sum((x - sum(obs) / len(obs)) ** 2 for x in obs) / len(obs)) ** 0.5}
    return linha


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sims", type=int, default=150)
    ap.add_argument("--gamas-perda", default="0,0.5,1")
    ap.add_argument("--gamas-chute", default="1")
    ap.add_argument("--semente", type=int, default=1)
    ap.add_argument("--saida", default="")
    a = ap.parse_args()
    dados = json.load(open(os.path.join(PASTA, "forca_e_janela_ligas_2015_16.json")))
    adj = F.ajustar(dados, treino=(0, 2), metades=((0,), (2,)))
    alvo = alvo_observado(dados, (0, 2))
    saida = []
    for gc in [float(x) for x in a.gamas_chute.split(",")]:
        for gp in [float(x) for x in a.gamas_perda.split(",")]:
            r = avaliar(dados, adj, alvo, gc, gp, a.sims, a.semente)
            saida.append(r)
            print(f"gama chute {gc:.2f} perda {gp:.2f} | " + " | ".join(f"{k}: corr {r[k]['corr']:+.2f} incl {r[k]['inclinacao_obs_sobre_prev']:.2f} dp {r[k]['dp_prev']:.2f}/{r[k]['dp_obs']:.2f}" for k in ("linhas", "pro", "contra")), flush=True)
    if a.saida:
        json.dump(saida, open(a.saida, "w"), ensure_ascii=False)
