#!/usr/bin/env python3
"""Validação fora da amostra da força dos times e da janela no simulador (Achado 32).

Treino = jogos dos quartos 0 e 2; teste = quartos 1 e 3 (jogos alternados da mesma temporada). As razões de força são estimadas só no treino (a confiabilidade, só
comparando os quartos 0 e 2 entre si). Cada jogo de teste é simulado `--sims` vezes entre os dois times reais; compara-se a média simulada de chutes, gols e
escanteios de cada time com o que aconteceu, contra o palpite 'todo time igual' (média do treino). Mede também a dispersão entre jogos.

Uso: python scripts/validar_simulador_forca.py [--sims 30] [--semente 1]
"""

from __future__ import annotations

import argparse
import json
import os
import random
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import forca_dos_times as F  # noqa: E402
import simulador_cadeia_bola as s  # noqa: E402


def corr(x, y):
    return F.pearson(x, y)


def validar(pasta: str, sims: int, semente: int) -> dict:
    dados = json.load(open(os.path.join(pasta, "forca_e_janela_ligas_2015_16.json")))
    adj = F.ajustar(dados, treino=(0, 2), metades=((0,), (2,)))
    mult = {t: s.Multiplicadores.de_dict(v) for t, v in adj["times"].items()}
    par = s.Parametros(pasta)
    teste = [m for m in dados["partidas"] if m["q"] in (1, 3)]
    treino = [m for m in dados["partidas"] if m["q"] in (0, 2)]
    media_treino = {k: st.mean(v for m in treino for v in m[k]) for k in ("chutes", "gols", "escanteios")}
    rng = random.Random(semente)
    prev = {c: {"forca": [], "neutro": [], "obs": []} for c in ("chutes", "gols", "escanteios")}
    totais = {"forca": [], "neutro": [], "obs": []}
    for m in teste:
        casa, fora = str(m["casa"]), str(m["fora"])
        for rotulo, times, jm in (("forca", (mult[casa], mult[fora]), adj["janela"]), ("neutro", (s.NEUTRO, s.NEUTRO), s.JANELA_NEUTRA)):
            acc = {c: [0.0, 0.0] for c in prev}
            tot_esc = []
            for _ in range(sims):
                r = s.simular_partida(par, rng, times, jm)
                for c, chave in (("chutes", "chutes"), ("gols", "gols"), ("escanteios", "escanteios")):
                    acc[c][0] += r.get(f"{chave}_0", 0)
                    acc[c][1] += r.get(f"{chave}_1", 0)
                tot_esc.append(r.get("escanteios", 0))
            for c in prev:
                if rotulo == "forca":
                    prev[c]["forca"] += [acc[c][0] / sims, acc[c][1] / sims]
                    prev[c]["obs"] += list(m[c])
                else:
                    prev[c]["neutro"] += [acc[c][0] / sims, acc[c][1] / sims]
            totais[rotulo].append(tot_esc[0])
        totais["obs"].append(sum(m["escanteios"]))
    out = {"jogos_teste": len(teste), "confiabilidade": adj["confiabilidade"], "peso": adj["peso"], "janela": adj["janela"], "metricas": {}}
    for c, v in prev.items():
        obs = v["obs"]
        mse_const = st.mean((o - media_treino[c]) ** 2 for o in obs)
        mse_forca = st.mean((o - p) ** 2 for o, p in zip(obs, v["forca"]))
        out["metricas"][c] = {"media_obs": st.mean(obs), "media_prevista": st.mean(v["forca"]), "corr_forca": corr(v["forca"], obs),
                              "mse_constante": mse_const, "mse_forca": mse_forca, "ganho_mse_pct": 100 * (1 - mse_forca / mse_const),
                              "dp_previsto": st.pstdev(v["forca"]), "dp_observado": st.pstdev(obs)}
    out["escanteios_por_jogo"] = {"var_sobre_media_obs": st.variance(totais["obs"]) / st.mean(totais["obs"])}
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pasta", default="dados_referencia/statsbomb")
    ap.add_argument("--sims", type=int, default=30)
    ap.add_argument("--semente", type=int, default=1)
    ap.add_argument("--saida", default="")
    a = ap.parse_args()
    r = validar(a.pasta, a.sims, a.semente)
    print(f"{r['jogos_teste']} jogos de teste; confiabilidade (r entre metades do treino):", {k: round(v, 2) for k, v in r["confiabilidade"].items()})
    for c, m in r["metricas"].items():
        print(f"{c:11s} obs {m['media_obs']:.2f} prev {m['media_prevista']:.2f} | corr {m['corr_forca']:+.3f} | MSE const {m['mse_constante']:.3f} força {m['mse_forca']:.3f} "
              f"(ganho {m['ganho_mse_pct']:+.2f}%) | dp previsto {m['dp_previsto']:.2f} obs {m['dp_observado']:.2f}")
    print("escanteios var/média observada por jogo:", round(r["escanteios_por_jogo"]["var_sobre_media_obs"], 2))
    if a.saida:
        json.dump(r, open(a.saida, "w"), ensure_ascii=False)
