#!/usr/bin/env python3
"""Compara posse, xT empírico por zona e momentum entre os eventos reais e o simulador (Achado 33).

  observar : mede nos eventos completos (2-3 min) e grava `posse_xt_momentum_observado_ligas_2015_16.json`;
  comparar : simula uma partida por jogo real (com a força de cada time, treinada em todos os jogos) e uma neutra, e imprime a comparação.
Uso: python scripts/comparar_posse_xt_momentum.py observar|comparar [--sims 2]
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import comparar_competicoes_statsbomb as c  # noqa: E402
import forca_dos_times as F  # noqa: E402
import gerar_matriz_transicao_statsbomb as g  # noqa: E402
import metricas_posse_xt_momentum as M  # noqa: E402
import simulador_cadeia_bola as s  # noqa: E402

PASTA = "dados_referencia/statsbomb"
ARQ_OBS = os.path.join(PASTA, "posse_xt_momentum_observado_ligas_2015_16.json")


def linhas_reais(ev: list[dict]) -> list[tuple]:
    """(equipe, zona, tipo, metade 0/1, xg, gol, segundos desde o início da metade) para cada linha da matriz de uma partida real."""
    out = []
    for e in ev:
        if e.get("period") not in (1, 2):
            continue
        linha = g.acoes_da_partida([e])
        if not linha:
            continue
        tipo, x, y = linha[0][0], linha[0][1], linha[0][2]
        shot = e.get("shot") or {}
        xg = float(shot.get("statsbomb_xg") or 0.0) if tipo == "chute" else 0.0
        gol = 1 if tipo == "chute" and ((shot.get("outcome") or {}).get("name")) == "Goal" else 0
        h, m, sec = e["timestamp"].split(":")
        out.append(((e.get("team") or {}).get("id"), g.zona_18(*g.para_metros(x, y)), tipo, e["period"] - 1, xg, gol, int(h) * 3600 + int(m) * 60 + float(sec)))
    return out


def observar() -> dict:
    partidas = []
    for rotulo in sorted(json.load(open(os.path.join(PASTA, "completo", "ligas_2015_16", d, "INDICE.json")))["rotulo"] for d in os.listdir(os.path.join(PASTA, "completo", "ligas_2015_16"))):
        for reg in c.carregar_completo(os.path.join(PASTA, "completo"), "ligas_2015_16", rotulo):
            partidas.append(linhas_reais(reg["eventos"]))
    res = M.calcular(partidas)
    json.dump(res, open(ARQ_OBS, "w"), ensure_ascii=False)
    return res


def simular_metricas(par, multiplicadores_por_partida, janela, sims: int, semente: int) -> dict:
    rng = random.Random(semente)
    partidas = []
    for times in multiplicadores_por_partida:
        for _ in range(sims):
            reg: list = []
            s.simular_partida(par, rng, times, janela, registro=reg)
            partidas.append(reg)
    return M.calcular(partidas)


def imprimir(obs: dict, forca: dict, neutro: dict) -> None:
    linhas = [("corridas (posses) por jogo", "corridas_por_jogo"), ("duração média da corrida (s)", "duracao_corrida_media_s"), ("duração mediana (s)", "duracao_corrida_mediana_s"),
              ("duração p90 (s)", "duracao_corrida_p90_s"), ("linhas por corrida, média", "linhas_por_corrida_media"), ("linhas por corrida, p90", "linhas_por_corrida_p90"),
              ("dp da posse por tempo (mandante)", "share_tempo_dp"), ("dp da posse por linhas (mandante)", "share_linhas_dp"),
              ("chutes por equipe-jogo", "chutes_por_equipe_jogo"), ("autocorrelação de chutes (5 min, lag 1)", "autocorrelacao_chutes_5min_lag1"),
              ("% chutes a <=60 s de outro da equipe", "chutes_ate_60s_do_anterior_pct"), ("% chutes a <=120 s", "chutes_ate_120s_do_anterior_pct"), ("% chutes a <=300 s", "chutes_ate_300s_do_anterior_pct")]
    print(f"{'':44s} {'real':>9s} {'sim força':>10s} {'sim neutro':>11s}")
    for nome, k in linhas:
        print(f"{nome:44s} {obs[k]:9.3f} {forca[k]:10.3f} {neutro[k]:11.3f}")
    print("\nRisco por posição da linha na corrida (observado/esperado dada a zona; 1 = sem memória): perda e chute -- real | sim força | sim neutro")
    for o, f, n in zip(obs["risco_por_posicao_na_corrida"], forca["risco_por_posicao_na_corrida"], neutro["risco_por_posicao_na_corrida"]):
        print(f"posição {o['posicao']:>5s} n={o['n']:7d}  perda {o['perda_obs_sobre_esp']:.3f} | {f['perda_obs_sobre_esp']:.3f} | {n['perda_obs_sobre_esp']:.3f}   chute {o['chute_obs_sobre_esp']:.3f} | {f['chute_obs_sobre_esp']:.3f} | {n['chute_obs_sobre_esp']:.3f}")
    print("\nxT empírico por zona: P(chute depois, na mesma corrida) e xG depois -- real | sim força | sim neutro")
    for z in range(18):
        o, f, n = obs["xt_por_zona"][z], forca["xt_por_zona"][z], neutro["xt_por_zona"][z]
        print(f"zona {z:2d} (faixa {z // 3}) n={o['n']:6d}  P(chute) {o['p_chute_depois']:.3f} | {f['p_chute_depois']:.3f} | {n['p_chute_depois']:.3f}   xG {o['xg_depois']:.4f} | {f['xg_depois']:.4f} | {n['xg_depois']:.4f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("acao", choices=["observar", "comparar"])
    ap.add_argument("--sims", type=int, default=2)
    ap.add_argument("--semente", type=int, default=1)
    a = ap.parse_args()
    if a.acao == "observar":
        r = observar()
        print(r["jogos"], "jogos ->", ARQ_OBS)
    else:
        obs = json.load(open(ARQ_OBS))
        dados = json.load(open(os.path.join(PASTA, "forca_e_janela_ligas_2015_16.json")))
        adj = F.ajustar(dados)
        mult = {t: s.Multiplicadores.de_dict(v) for t, v in adj["times"].items()}
        par = s.Parametros(PASTA)
        pares = [(mult[str(m["casa"])], mult[str(m["fora"])]) for m in dados["partidas"]]
        forca = simular_metricas(par, pares, adj["janela"], a.sims, a.semente)
        neutro = simular_metricas(par, [(s.NEUTRO, s.NEUTRO)] * len(pares), s.JANELA_NEUTRA, a.sims, a.semente + 1)
        imprimir(obs, forca, neutro)
