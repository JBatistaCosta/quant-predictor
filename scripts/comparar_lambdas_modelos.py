#!/usr/bin/env python3
"""Compara os gols esperados (lambdas) de cada lado previstos por modelos diferentes, nos mesmos jogos, contra os gols que aconteceram.

Fontes de lambda (mandante, visitante):
  * mercado e dixon_coles_v1: lambdas IMPLÍCITOS -- as duas Poisson independentes cujo 1X2 e over 2,5 mais se aproximam das probabilidades do mercado (odds de fechamento sem margem) ou
    do modelo (só há 1X2 e over/under 2,5 guardados para eles);
  * markov_multievento_v1, hibrido_gols_v1, hibrido_gols_xg_v1: lambdas pelas linhas de gols por time guardadas em `model_predictions` (over_under_team_1_* e _2_*): E[gols] = soma das
    probabilidades de "mais de k+0,5 gols" para k = 0..4 (a cauda acima de 5 gols é desprezada, subestima em ~0,01);
  * simulador: gols médios simulados de cada lado (`gols_casa`, `gols_fora` dos resultados do backtest, variante escolhida).
Medidas (por time-jogo): viés (média do lambda menos média dos gols), erro quadrático médio, log-verossimilhança de Poisson dos gols reais com esse lambda (menor NLL = melhor), correlação do
lambda com os gols e a inclinação da regressão dos gols sobre o lambda (1 = bem calibrado; <1 = lambdas espalhados demais; >1 = espalhados de menos).
Somente leitura; credenciais por variável de ambiente (SUPABASE_URL, SUPABASE_KEY), sem valor padrão.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request

import numpy as np
from scipy.optimize import least_squares

LINHAS = ["0.5", "1.5", "2.5", "3.5", "4.5"]


def _get(caminho: str) -> list:
    url, chave = os.environ["SUPABASE_URL"].rstrip("/"), os.environ["SUPABASE_KEY"]
    req = urllib.request.Request(f"{url}/rest/v1/{caminho}", headers={"apikey": chave, "Authorization": "Bearer " + chave})
    for espera in (2, 4, 8, 16, None):
        try:
            return json.load(urllib.request.urlopen(req, timeout=120))
        except urllib.error.HTTPError as e:
            if e.code < 500 or espera is None:
                raise
        except (urllib.error.URLError, TimeoutError):
            if espera is None:
                raise
        time.sleep(espera)


def pagina(caminho: str) -> list:
    out, off = [], 0
    while True:
        pag = _get(f"{caminho}&limit=1000&offset={off}&order=id.asc")
        out += pag
        off += 1000
        if len(pag) < 1000:
            return out


def devig(odds):
    inv = [1.0 / o for o in odds]
    t = sum(inv)
    return [x / t for x in inv]


def pmf(lam, kmax=12):
    k = np.arange(kmax + 1)
    return np.exp(-lam + k * np.log(lam) - np.array([math.lgamma(x + 1) for x in k]))


def alvo_de(lh, la):
    ph, pa = pmf(lh), pmf(la)
    m = np.outer(ph, pa)
    p1 = np.tril(m, -1).sum()
    px = np.trace(m)
    p2 = np.triu(m, 1).sum()
    k = np.add.outer(np.arange(m.shape[0]), np.arange(m.shape[1]))
    over = m[k >= 3].sum()
    return np.array([p1, px, p2, over])


def lambdas_implicitos(p3, p_over):
    alvo = np.array([p3[0], p3[1], p3[2], p_over])
    f = lambda x: alvo_de(math.exp(x[0]), math.exp(x[1])) - alvo
    r = least_squares(f, x0=[math.log(1.4), math.log(1.1)], bounds=([-3, -3], [2, 2]))
    return math.exp(r.x[0]), math.exp(r.x[1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--temporada", default="2025")
    ap.add_argument("--pasta", default="dados_referencia/backtest_simulador")
    ap.add_argument("--modelos", nargs="*", default=["markov_multievento_v1", "hibrido_gols_v1", "hibrido_gols_xg_v1"])
    ap.add_argument("--simulador", nargs="*", default=[], help="Rotulo=resultado.json:variante:liga.csv (gols_casa/gols_fora do backtest)")
    ap.add_argument("--cache", default="", help="arquivo JSON para guardar/ler as previsões baixadas")
    ap.add_argument("--saida", default="")
    a = ap.parse_args()
    jogos = {}
    for f in sorted(glob.glob(os.path.join(a.pasta, "*_2022_25.csv"))):
        for r in csv.DictReader(open(f)):
            if r["season"] == a.temporada and r["dc_h"] and r["o_h"] and r["o_over"]:
                jogos[int(r["id"])] = {"hg": int(r["hg"]), "ag": int(r["ag"]), "liga": os.path.basename(f).replace("_2022_25.csv", ""),
                                       "dc": ([float(r["dc_h"]), float(r["dc_d"]), float(r["dc_a"])], float(r["dc_over"])),
                                       "mercado": (devig([float(r["o_h"]), float(r["o_d"]), float(r["o_a"])]), devig([float(r["o_over"]), float(r["o_under"])])[0])}
    ids = sorted(jogos)
    print(f"{len(ids)} jogos com Dixon-Coles e odds", flush=True)
    lam = {"mercado": {}, "dixon_coles_v1": {}}
    for i in ids:
        lam["mercado"][i] = lambdas_implicitos(*jogos[i]["mercado"])
        lam["dixon_coles_v1"][i] = lambdas_implicitos(*jogos[i]["dc"])
    print("lambdas implícitos calculados", flush=True)
    cache = json.load(open(a.cache)) if a.cache and os.path.exists(a.cache) else {}
    mercados = ",".join([f"over_under_team_{t}_{l}" for t in (1, 2) for l in LINHAS])
    for nome in a.modelos:
        if nome not in cache:
            tab = {}
            for k in range(0, len(ids), 100):
                filtro = ",".join(str(x) for x in ids[k:k + 100])
                for r in pagina(f"model_predictions?select=match_id,market,probability&model_name=eq.{urllib.parse.quote(nome)}&selection=eq.over&market=in.({mercados})&match_id=in.({filtro})"):
                    tab.setdefault(str(r["match_id"]), {})[r["market"]] = float(r["probability"])
            cache[nome] = tab
            if a.cache:
                json.dump(cache, open(a.cache, "w"))
            print(f"{nome}: {len(tab)} jogos baixados", flush=True)
        lam[nome] = {}
        for i in ids:
            d = cache[nome].get(str(i))
            if d and all(f"over_under_team_{t}_{l}" in d for t in (1, 2) for l in LINHAS):
                lam[nome][i] = (sum(d[f"over_under_team_1_{l}"] for l in LINHAS), sum(d[f"over_under_team_2_{l}"] for l in LINHAS))
    for spec in a.simulador:
        rot, resto = spec.split("=", 1)
        caminho, variante = resto.split(":")[:2]
        lam[rot] = {}
        for g in json.load(open(caminho))["jogos"]:
            if g["id"] in jogos and variante in g and "gols_casa" in g[variante]:
                lam[rot][g["id"]] = (g[variante]["gols_casa"], g[variante]["gols_fora"])
    comuns = [i for i in ids if all(i in lam[k] for k in lam)]
    print(f"\n{len(comuns)} jogos com TODAS as fontes: {', '.join(lam)}\n")
    real_h = np.array([jogos[i]["hg"] for i in comuns], float)
    real_a = np.array([jogos[i]["ag"] for i in comuns], float)
    g = np.concatenate([real_h, real_a])
    print(f"gols reais: mandante {real_h.mean():.3f}, visitante {real_a.mean():.3f}, total {(real_h + real_a).mean():.3f}\n")
    cab = f"{'fonte':26s} {'lam casa':>8s} {'lam fora':>8s} {'viés tot':>8s} {'RMSE':>6s} {'NLL/time':>9s} {'corr':>6s} {'incl.':>6s} {'corr(dif)':>9s}"
    print(cab)
    nll = {}
    res = {}
    for k in lam:
        lh = np.array([lam[k][i][0] for i in comuns])
        la = np.array([lam[k][i][1] for i in comuns])
        x = np.concatenate([lh, la])
        nl = np.array([-(gg * math.log(max(l, 1e-9)) - l - math.lgamma(gg + 1)) for gg, l in zip(g, x)])
        nll[k] = nl
        incl = float(np.polyfit(x, g, 1)[0])
        corr = float(np.corrcoef(x, g)[0, 1])
        cd = float(np.corrcoef(lh - la, real_h - real_a)[0, 1])
        rmse = math.sqrt(float(((x - g) ** 2).mean()))
        res[k] = {"lambda_casa": float(lh.mean()), "lambda_fora": float(la.mean()), "vies_total": float((lh + la).mean() - (real_h + real_a).mean()), "rmse": rmse,
                  "nll_por_time": float(nl.mean()), "corr": corr, "inclinacao": incl, "corr_dif": cd}
        print(f"{k:26s} {lh.mean():8.3f} {la.mean():8.3f} {res[k]['vies_total']:+8.3f} {rmse:6.3f} {nl.mean():9.4f} {corr:6.3f} {incl:6.3f} {cd:9.3f}")
    print("\nNLL de Poisson por time-jogo, diferença contra o mercado e contra o Dixon-Coles (negativo = melhor; * = IC 95% exclui zero)")
    rng = random.Random(5)
    n = len(g)
    for k in lam:
        if k == "mercado":
            continue
        linha = []
        for ref in ("mercado", "dixon_coles_v1"):
            if k == ref:
                continue
            d = nll[k] - nll[ref]
            ms = sorted(float(d[np.array([rng.randrange(n) for _ in range(n)])].mean()) for _ in range(1500))
            m, lo, hi = float(d.mean()), ms[37], ms[1462]
            linha.append(f"vs {ref}: {m:+.4f} [{lo:+.4f};{hi:+.4f}]{'*' if lo > 0 or hi < 0 else ' '}")
            res[k][f"nll_menos_{ref}"] = [m, lo, hi]
        print(f"  {k:26s} " + " | ".join(linha))
    por_liga = {}
    for liga in sorted({jogos[i]["liga"] for i in comuns}):
        idx = [j for j, i in enumerate(comuns) if jogos[i]["liga"] == liga]
        por_liga[liga] = {k: float(np.concatenate([nll[k][:len(comuns)][idx], nll[k][len(comuns):][idx]]).mean()) for k in lam}
    print("\nNLL por time-jogo por liga:")
    print(f"  {'liga':15s} " + " ".join(f"{k[:14]:>14s}" for k in lam))
    for liga, d in por_liga.items():
        print(f"  {liga:15s} " + " ".join(f"{d[k]:14.4f}" for k in lam))
    if a.saida:
        json.dump({"jogos": len(comuns), "resultado": res, "por_liga": por_liga}, open(a.saida, "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
