#!/usr/bin/env python3
"""Calibra os gols esperados (lambdas) do simulador com uma lei de potência e mede o ganho fora da amostra.

Transformação: log lambda' = a_lado + beta * log lambda  (ou seja, lambda' = c_lado * lambda^beta), ajustada por máxima verossimilhança de Poisson dos gols reais.
  * "nivel":   beta = 1 e só o nível (c_casa, c_fora) muda -- corrige viés, não a amplitude;
  * "potencia": beta comum aos dois lados, c_casa e c_fora livres;
  * "potencia2": beta próprio de cada lado.
Ajuste na temporada de TREINO (resultados do backtest com os gols esperados `gols_casa`/`gols_fora` por jogo), avaliação na de TESTE, sem tocar nela para ajustar.
Com o lambda transformado, 1X2 e over/under 2,5 saem de duas Poisson independentes (sem Dixon-Coles) e são comparados, nos jogos de teste com Dixon-Coles e odds, com o mercado
e o Dixon-Coles (diferença pareada de log-loss, IC 95% por bootstrap). Referência extra: a probabilidade que o próprio simulador guardou (`p1x2`, `pover`).
Dados: arquivos `resultado_backtest_simulador.json` (um por liga) e os CSVs do backtest (gols reais, Dixon-Coles e odds). Sem rede.
Uso: python scripts/calibrar_lambdas_potencia.py --treino r58/res.json r59/res.json ... --teste r53/res.json ... --variantes forca_expo misto_expo [--saida out.json]
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import os

import numpy as np
from scipy.optimize import minimize
from scipy.special import gammaln

KMAX = 12


def carregar_csv(pasta):
    out = {}
    for f in sorted(glob.glob(os.path.join(pasta, "*_2022_25.csv"))):
        for r in csv.DictReader(open(f)):
            d = {"hg": int(r["hg"]), "ag": int(r["ag"])}
            if r["dc_h"] and r["o_h"] and r["o_over"]:
                d["dc"] = ([float(r["dc_h"]), float(r["dc_d"]), float(r["dc_a"])], float(r["dc_over"]))
                inv = [1 / float(r[k]) for k in ("o_h", "o_d", "o_a")]
                s = sum(inv)
                io = [1 / float(r["o_over"]), 1 / float(r["o_under"])]
                d["mk"] = ([x / s for x in inv], io[0] / sum(io))
            out[int(r["id"])] = d
    return out


def carregar_jogos(arquivos, variante, csv_jogos):
    """Lista de (id, lam_casa, lam_fora, hg, ag, p1x2_sim, pover_sim)."""
    out = []
    for f in arquivos:
        for j in json.load(open(f))["jogos"]:
            v = j.get(variante)
            if v and "gols_casa" in v and j["id"] in csv_jogos:
                c = csv_jogos[j["id"]]
                out.append((j["id"], v["gols_casa"], v["gols_fora"], c["hg"], c["ag"], v["p1x2"], v["pover"]))
    return out


def nll_poisson(lam, g):
    return float((lam - g * np.log(lam) + gammaln(g + 1)).mean())


def aplicar(par, lh, la, modo):
    if modo == "nivel":
        return np.exp(par[0]) * lh, np.exp(par[1]) * la
    if modo == "potencia":
        return np.exp(par[0] + par[2] * np.log(lh)), np.exp(par[1] + par[2] * np.log(la))
    return np.exp(par[0] + par[2] * np.log(lh)), np.exp(par[1] + par[3] * np.log(la))


def ajustar(lh, la, hg, ag, modo):
    ini = {"nivel": [0.0, 0.0], "potencia": [0.0, 0.0, 1.0], "potencia2": [0.0, 0.0, 1.0, 1.0]}[modo]

    def f(p):
        a, b = aplicar(p, lh, la, modo)
        return nll_poisson(a, hg) + nll_poisson(b, ag)

    return minimize(f, ini, method="Nelder-Mead", options={"xatol": 1e-6, "fatol": 1e-9, "maxiter": 5000}).x


def pmf(lam):
    k = np.arange(KMAX + 1)
    return np.exp(-lam[:, None] + k[None, :] * np.log(lam[:, None]) - gammaln(k + 1)[None, :])


def probs(lh, la):
    ph, pa = pmf(lh), pmf(la)
    m = ph[:, :, None] * pa[:, None, :]
    i = np.arange(KMAX + 1)
    casa = (i[:, None] > i[None, :])
    emp = (i[:, None] == i[None, :])
    fora = (i[:, None] < i[None, :])
    p = np.stack([(m * casa).sum((1, 2)), (m * emp).sum((1, 2)), (m * fora).sum((1, 2))], 1)
    p /= p.sum(1, keepdims=True)
    tot = (m * ((i[:, None] + i[None, :]) >= 3)).sum((1, 2)) / m.sum((1, 2))
    return p, tot


def ic(x, B=3000, semente=3):
    rng = np.random.default_rng(semente)
    n = len(x)
    ms = np.sort([x[rng.integers(0, n, n)].mean() for _ in range(B)])
    return float(x.mean()), float(ms[int(0.025 * B)]), float(ms[int(0.975 * B)])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--treino", nargs="+", required=True)
    ap.add_argument("--teste", nargs="+", required=True)
    ap.add_argument("--variantes", nargs="+", required=True)
    ap.add_argument("--pasta", default="dados_referencia/backtest_simulador")
    ap.add_argument("--saida", default="")
    a = ap.parse_args()
    cj = carregar_csv(a.pasta)
    saida = {}
    for var in a.variantes:
        tr = carregar_jogos(a.treino, var, cj)
        te = carregar_jogos(a.teste, var, cj)
        arr = lambda L, i: np.array([x[i] for x in L], float)
        lh_t, la_t, hg_t, ag_t = arr(tr, 1), arr(tr, 2), arr(tr, 3), arr(tr, 4)
        lh, la, hg, ag = arr(te, 1), arr(te, 2), arr(te, 3), arr(te, 4)
        y = np.where(hg > ag, 0, np.where(hg == ag, 1, 2))
        over = (hg + ag) >= 3
        print(f"\n=== {var}: treino {len(tr)} jogos, teste {len(te)} jogos ===")
        print(f"  gols reais teste: casa {hg.mean():.3f}, fora {ag.mean():.3f}; lambda do simulador: casa {lh.mean():.3f}, fora {la.mean():.3f}")
        sel = [i for i, x in enumerate(te) if cj[x[0]].get("dc") and cj[x[0]].get("mk")]
        ll_dc = np.array([-math.log(cj[te[i][0]]["dc"][0][y[i]]) for i in sel])
        ll_mk = np.array([-math.log(cj[te[i][0]]["mk"][0][y[i]]) for i in sel])
        ou = lambda p, o: -np.log(np.clip(np.where(o, p, 1 - p), 1e-9, 1))
        ou_dc = np.array([ou(cj[te[i][0]]["dc"][1], over[i]) for i in sel])
        ou_mk = np.array([ou(cj[te[i][0]]["mk"][1], over[i]) for i in sel])
        print(f"  {len(sel)} jogos com DC e odds: 1X2 log-loss mercado {ll_mk.mean():.4f}, DC {ll_dc.mean():.4f}; O/U mercado {ou_mk.mean():.4f}, DC {ou_dc.mean():.4f}")
        res = {}
        modelos = {"simulador (sem transformação)": None}
        for modo in ("nivel", "potencia", "potencia2"):
            modelos[modo] = ajustar(lh_t, la_t, hg_t, ag_t, modo)
        print(f"  {'modelo':30s} {'parâmetros':34s} {'NLL/time':>8s} {'incl.':>6s} {'1X2':>7s} {'-mercado':>22s} {'-DC':>22s} {'O/U':>7s} {'-mercado':>22s}")
        for nome, par in modelos.items():
            if par is None:
                h2, a2 = lh, la
                p1 = np.array([x[5] for x in te])
                pov = np.array([x[6] for x in te])
                ptxt = "(probabilidade do próprio simulador)"
                # NLL/inclinação com os lambdas crus
            else:
                h2, a2 = aplicar(par, lh, la, nome)
                p1, pov = probs(h2, a2)
                ptxt = "c=(" + ", ".join(f"{math.exp(par[k]):.3f}" for k in (0, 1)) + ") b=" + ", ".join(f"{v:.2f}" for v in par[2:]) if len(par) > 2 else "c=(" + ", ".join(f"{math.exp(v):.3f}" for v in par) + ")"
            g = np.concatenate([hg, ag]); x = np.concatenate([h2, a2])
            nll = nll_poisson(x, g)
            incl = float(np.polyfit(x, g, 1)[0])
            l1 = -np.log(np.clip(p1[np.arange(len(y)), y], 1e-9, 1))
            lo = ou(pov, over)
            d_mk, d_dc, o_mk = ic(l1[sel] - ll_mk), ic(l1[sel] - ll_dc), ic(lo[sel] - ou_mk)
            res[nome] = {"parametros": None if par is None else [float(v) for v in par], "nll_por_time": nll, "inclinacao": incl, "ll_1x2_todos": float(l1.mean()), "ll_1x2_com_odds": float(l1[sel].mean()),
                         "contra_mercado_1x2": d_mk, "contra_dc_1x2": d_dc, "ll_ou_com_odds": float(lo[sel].mean()), "contra_mercado_ou": o_mk}
            fm = lambda t: f"{t[0]:+.4f} [{t[1]:+.4f};{t[2]:+.4f}]{'*' if t[1] > 0 or t[2] < 0 else ' '}"
            print(f"  {nome:30s} {ptxt:34s} {nll:8.4f} {incl:6.3f} {l1[sel].mean():7.4f} {fm(d_mk)} {fm(d_dc)} {lo[sel].mean():7.4f} {fm(o_mk)}")
        saida[var] = res
    if a.saida:
        json.dump(saida, open(a.saida, "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
