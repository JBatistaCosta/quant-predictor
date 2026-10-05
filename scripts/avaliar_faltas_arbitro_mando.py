#!/usr/bin/env python3
"""Faltas (e, com a mesma máquina, cartões e escanteios) com mando, taxas de cada time (quanto faz e quanto provoca) e árbitro, contra a frequência histórica e o modelo de taxas do total (Achado 67).

Estatística por lado de cada jogo: faltas cometidas e cartões (amarelos + vermelhos equivalentes) de `match_disciplina` (por time, com `is_home`); escanteios a favor de `hc`/`ac` dos CSVs. Árbitro:
`match_context_fotmob.referee` (só o nome; cobertura parcial). Tudo walk-forward, só com jogos anteriores:
  * M0  frequência da liga em 2024 (treino);
  * M1  taxas do total do jogo (Achado 67): média do total dos jogos de cada time encolhida (k jogos);
  * M2  mando + times: média esperada do lado = média da liga naquele lado (mandante e visitante têm médias diferentes) + quanto o time FAZ da estatística (desvio dela contra a média do lado, encolhido com k jogos)
        + quanto o ADVERSÁRIO provoca (o que seus adversários fazem contra ele, encolhido); o total é a soma dos dois lados;
  * M3  M2 + árbitro: desvio médio do total do jogo contra a previsão de M2 nos jogos anteriores do árbitro, encolhido com `k_arbitro` jogos (efeito 0 sem árbitro conhecido).
Binomial negativa com dispersão estimada em 2024. Avaliado em 2025: log-verossimilhança do total, correlação da média com o total e Brier das linhas, com diferença pareada (IC 95% por bootstrap) contra M1.
Credenciais por variável de ambiente (SUPABASE_URL, SUPABASE_KEY), sem valor padrão. Cache opcional (`--cache`): disciplina e árbitro.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import os

import numpy as np

import avaliar_escanteios_cartoes_faltas as ecf
from avaliar_mistura_elo_xg import _get

LINHAS = {"faltas": (22.5, 24.5, 26.5, 28.5), "cartoes": (3.5, 4.5, 5.5), "escanteios": (8.5, 9.5, 10.5, 11.5)}


def baixar_arbitro(ids, cache):
    if cache and os.path.exists(cache):
        return {int(k): v for k, v in json.load(open(cache)).items()}
    out = {}
    for i in range(0, len(ids), 80):
        filtro = ",".join(str(x) for x in ids[i:i + 80])
        for r in _get(f"match_context_fotmob?select=match_id,referee&match_id=in.({filtro})&limit=1000"):
            if r["referee"]:
                out[r["match_id"]] = r["referee"]
    if cache:
        json.dump({str(k): v for k, v in out.items()}, open(cache, "w"))
    return out


def carregar_csv(pasta):
    jogos = []
    for f in sorted(glob.glob(os.path.join(pasta, "*_2022_25.csv"))):
        liga = os.path.basename(f).replace("_2022_25.csv", "")
        for r in csv.DictReader(open(f)):
            jogos.append({"id": int(r["id"]), "liga": liga, "temp": r["season"], "data": r["date"], "casa": r["home"], "fora": r["away"],
                          "esc": (float(r["hc"]), float(r["ac"])) if r["hc"] != "" and r["ac"] != "" else None})
    jogos.sort(key=lambda j: (j["data"], j["id"]))
    return jogos


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pasta", default="dados_referencia/backtest_simulador")
    ap.add_argument("--cache", default="")
    ap.add_argument("--k", type=float, default=10.0, help="jogos de encolhimento das taxas dos times")
    ap.add_argument("--k-arbitro", type=float, default=10.0, help="jogos de encolhimento do efeito do árbitro")
    ap.add_argument("--saida", default="")
    a = ap.parse_args()
    jogos = carregar_csv(a.pasta)
    ids = [j["id"] for j in jogos]
    lado = {}
    cache_lado = a.cache + "_lado.json" if a.cache else ""
    if cache_lado and os.path.exists(cache_lado):
        lado = {int(k): v for k, v in json.load(open(cache_lado)).items()}
    else:
        for i in range(0, len(ids), 80):
            filtro = ",".join(str(x) for x in ids[i:i + 80])
            for r in _get(f"match_disciplina?select=match_id,is_home,cartoes_amarelos,cartoes_vermelhos_equiv,faltas_cometidas,fonte_cartoes&match_id=in.({filtro})&limit=1000"):
                lado.setdefault(r["match_id"], []).append(r)
        if cache_lado:
            json.dump({str(k): v for k, v in lado.items()}, open(cache_lado, "w"))
    arb = baixar_arbitro(ids, a.cache + "_arb.json" if a.cache else "")
    for j in jogos:
        rows = lado.get(j["id"], [])
        j["arbitro"] = arb.get(j["id"])
        j["lados"] = {"escanteios": j["esc"], "faltas": None, "cartoes": None}
        if len(rows) == 2 and {bool(r["is_home"]) for r in rows} == {True, False}:
            h = next(r for r in rows if r["is_home"]); v = next(r for r in rows if not r["is_home"])
            if h["faltas_cometidas"] is not None and v["faltas_cometidas"] is not None:
                j["lados"]["faltas"] = (float(h["faltas_cometidas"]), float(v["faltas_cometidas"]))
            if all(r["fonte_cartoes"] in ecf.FONTES_OK and r["cartoes_amarelos"] is not None for r in rows):
                j["lados"]["cartoes"] = tuple(float(r["cartoes_amarelos"] + (r["cartoes_vermelhos_equiv"] or 0)) for r in (h, v))
    cob = np.mean([j["arbitro"] is not None for j in jogos if j["temp"] == "2025"])
    print(f"cobertura do árbitro em 2025: {cob:.1%}; jogos com árbitro em 2022-2025: {sum(j['arbitro'] is not None for j in jogos)}")

    resultado = {}
    for mercado in ("faltas", "cartoes", "escanteios"):
        # --- passagem walk-forward
        liga_lado = {}                                            # (liga, 0/1) -> (soma, n)
        faz, sofre, nt = {}, {}, {}                               # por time: desvio do que faz e do que o adversário faz contra ele
        tot_t, ntot_t, liga_tot = {}, {}, {}
        arb_res, arb_n = {}, {}
        pred = {}                                                 # id -> dict de médias por modelo
        for j in jogos:
            y = j["lados"][mercado]
            ligaid = j["liga"]
            L = [liga_lado.get((ligaid, s), (0.0, 0)) for s in (0, 1)]
            if y is not None and all(l[1] >= 60 for l in L):
                m = [L[s][0] / L[s][1] for s in (0, 1)]
                h, v = j["casa"], j["fora"]
                def sh(d, n_, t):
                    return d.get(t, 0.0) / (nt.get(t, 0) + a.k)
                # M2: lado casa = média casa + faz(h) + sofre(v); lado fora = média fora + faz(v) + sofre(h)
                mu_casa = m[0] + sh(faz, nt, h) + sh(sofre, nt, v)
                mu_fora = m[1] + sh(faz, nt, v) + sh(sofre, nt, h)
                m2 = mu_casa + mu_fora
                # M1: total
                Lt = liga_tot[ligaid][0] / liga_tot[ligaid][1]
                m1 = Lt + sum((tot_t.get(t, 0.0) + a.k * Lt) / (ntot_t.get(t, 0) + a.k) - Lt for t in (h, v))
                ar = j["arbitro"]
                ef = arb_res.get(ar, 0.0) / (arb_n.get(ar, 0) + a.k_arbitro) if ar else 0.0
                pred[j["id"]] = {"M1": m1, "M2": m2, "M3": m2 + ef}
            if y is not None:
                h, v = j["casa"], j["fora"]
                tot = y[0] + y[1]
                # atualiza
                for s, val in ((0, y[0]), (1, y[1])):
                    a_, n_ = liga_lado.get((ligaid, s), (0.0, 0)); liga_lado[(ligaid, s)] = (a_ + val, n_ + 1)
                a_, n_ = liga_tot.get(ligaid, (0.0, 0)); liga_tot[ligaid] = (a_ + tot, n_ + 1)
                if all(liga_lado.get((ligaid, s), (0, 0))[1] >= 1 for s in (0, 1)):
                    mh = liga_lado[(ligaid, 0)][0] / liga_lado[(ligaid, 0)][1]; ma = liga_lado[(ligaid, 1)][0] / liga_lado[(ligaid, 1)][1]
                    faz[h] = faz.get(h, 0.0) + (y[0] - mh); sofre[h] = sofre.get(h, 0.0) + (y[1] - ma)
                    faz[v] = faz.get(v, 0.0) + (y[1] - ma); sofre[v] = sofre.get(v, 0.0) + (y[0] - mh)
                    nt[h] = nt.get(h, 0) + 1; nt[v] = nt.get(v, 0) + 1
                for t in (h, v):
                    tot_t[t] = tot_t.get(t, 0.0) + tot; ntot_t[t] = ntot_t.get(t, 0) + 1
                if j["arbitro"] and j["id"] in pred:
                    arb_res[j["arbitro"]] = arb_res.get(j["arbitro"], 0.0) + (tot - pred[j["id"]]["M2"]); arb_n[j["arbitro"]] = arb_n.get(j["arbitro"], 0) + 1
        treino = [j for j in jogos if j["temp"] == "2024" and j["id"] in pred and j["lados"][mercado] is not None]
        teste = [j for j in jogos if j["temp"] == "2025" and j["id"] in pred and j["lados"][mercado] is not None]
        y_tr = np.array([sum(j["lados"][mercado]) for j in treino]); y = np.array([sum(j["lados"][mercado]) for j in teste])
        print(f"\n=== {mercado.upper()}: treino {len(treino)} (2024), teste {len(teste)} (2025); total médio {y.mean():.2f}; com árbitro conhecido no teste: {np.mean([j['arbitro'] is not None for j in teste]):.1%} ===")
        mus, rs = {}, {}
        for nome in ("M1", "M2", "M3"):
            mus[nome] = np.array([pred[j["id"]][nome] for j in teste])
            rs[nome] = ecf.dispersao(y_tr, np.array([pred[j["id"]][nome] for j in treino]))
        emp = {}
        for lig in {j["liga"] for j in treino}:
            emp[lig] = np.array([sum(j["lados"][mercado]) for j in treino if j["liga"] == lig])
        ll_clim = np.array([math.log(((emp[j["liga"]] == sum(j["lados"][mercado])).sum() + 0.5) / (len(emp[j["liga"]]) + 0.5 * 60)) for j in teste])
        print(f"  {'modelo':34s} {'corr':>6s} {'logL do total':>13s}  contra M1 (logL)")
        nomes = {"M1": "M1 taxas do total (Achado 67)", "M2": "M2 mando + faz/provoca", "M3": "M3 M2 + árbitro"}
        res = {}
        for nome in ("M1", "M2", "M3"):
            mu, r = mus[nome], rs[nome]
            ll = ecf.nb_logpmf(y, mu, r) if r else (-mu + y * np.log(np.maximum(mu, 1e-9)) - ecf.gammaln(y + 1))
            ll1 = ecf.nb_logpmf(y, mus["M1"], rs["M1"]) if rs["M1"] else (-mus["M1"] + y * np.log(np.maximum(mus["M1"], 1e-9)) - ecf.gammaln(y + 1))
            c = float(np.corrcoef(mu, y)[0, 1])
            mm, lo, hi = ecf.ic(ll - ll1)
            res[nome] = {"corr": c, "logL": float(ll.mean()), "vs_M1": [mm, lo, hi], "r": r}
            print(f"  {nomes[nome]:34s} {c:6.3f} {ll.mean():13.4f}  {mm:+.4f} [{lo:+.4f};{hi:+.4f}]{'*' if lo > 0 or hi < 0 else ''}")
        print(f"  {'frequência da liga':34s} {'—':>6s} {ll_clim.mean():13.4f}")
        print(f"\n  Brier das linhas (menor é melhor); diferença contra M1 com IC 95%")
        print(f"  {'linha':>6s} {'freq':>6s} | {'liga':>7s} {'M1':>7s} {'M2':>7s} {'M3':>7s} | M2-M1 | M3-M2 | M3-M1")
        linhas = {}
        for ln in LINHAS[mercado]:
            over = y > ln
            pc = np.array([float((emp[j["liga"]] > ln).mean()) for j in teste])
            br = {"liga": (pc - over) ** 2}
            for nome in ("M1", "M2", "M3"):
                br[nome] = (ecf.nb_cdf_over(ln, mus[nome], rs[nome]) - over) ** 2
            d21, d32, d31 = ecf.ic(br["M2"] - br["M1"]), ecf.ic(br["M3"] - br["M2"]), ecf.ic(br["M3"] - br["M1"])
            f = lambda t: f"{t[0]:+.4f}{'*' if t[1] > 0 or t[2] < 0 else ' '}"
            print(f"  {ln:6.1f} {over.mean():6.3f} | {br['liga'].mean():7.4f} {br['M1'].mean():7.4f} {br['M2'].mean():7.4f} {br['M3'].mean():7.4f} | {f(d21)} | {f(d32)} | {f(d31)}")
            linhas[str(ln)] = {k_: float(v.mean()) for k_, v in br.items()}
            linhas[str(ln)].update({"M2-M1": d21, "M3-M2": d32, "M3-M1": d31})
        # subconjunto com árbitro conhecido e já com histórico
        sub = np.array([j["arbitro"] is not None and arb_n.get(j["arbitro"], 0) >= 0 for j in teste])
        if sub.sum() > 100:
            d = []
            for ln in LINHAS[mercado]:
                over = y > ln
                b2 = (ecf.nb_cdf_over(ln, mus["M2"], rs["M2"]) - over) ** 2; b3 = (ecf.nb_cdf_over(ln, mus["M3"], rs["M3"]) - over) ** 2
                d.append((b3 - b2)[sub])
            mm, lo, hi = ecf.ic(np.mean(d, 0))
            print(f"  só jogos com árbitro conhecido (n={sub.sum()}): Brier M3-M2 médio nas linhas {mm:+.4f} [{lo:+.4f};{hi:+.4f}]{'*' if lo > 0 or hi < 0 else ''}")
        resultado[mercado] = {"modelos": res, "linhas": linhas, "n_teste": len(teste)}
    if a.saida:
        json.dump(resultado, open(a.saida, "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
