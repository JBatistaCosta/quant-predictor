#!/usr/bin/env python3
"""Escanteios, cartões e faltas: previsão do total por jogo contra a frequência histórica, nas 5 ligas, temporada de teste.

Resultados reais: escanteios = hc + ac dos CSVs do backtest (FotMob); faltas = soma de `match_disciplina.faltas_cometidas`; cartões = soma de `cartoes_amarelos + cartoes_vermelhos_equiv`
da mesma tabela, só em jogos cuja `fonte_cartoes` é `match_events` ou `fallback_fotmob` (regra de `api/_lib/resultadosReais.js`; o FotMob zera amarelos em parte dos jogos).
Modelos, todos com o total do jogo e as linhas over/under:
  * climatologia: distribuição empírica do total na liga em 2024 (treino);
  * taxas dos times (walk-forward): média do total dos jogos anteriores de cada time, encolhida para a média da liga com `k` jogos; média do jogo = liga + desvio do mandante + desvio do visitante;
    binomial negativa com dispersão estimada no treino; os jogos de 2022 a 2024 aquecem a história;
  * simulador (escanteios e faltas, que ele gera; ele NÃO modela cartões; faltas = tiros livres simulados, proxy): média simulada do total ajustada por nível/potência no treino (2024), binomial
    negativa com a dispersão do treino; também a distribuição simulada crua;
  * `markov_multievento_v1` (modelo cadastrado, previsões gravadas em lote depois dos jogos): probabilidades por linha em `model_predictions`.
Medidas por linha: Brier, log-loss, acurácia; e a log-verossimilhança do total (binomial negativa) e a correlação da média prevista com o total real. Não há odds de escanteios, cartões ou faltas
nas 5 ligas no banco, então não há comparação com mercado.
Credenciais por variável de ambiente (SUPABASE_URL, SUPABASE_KEY), sem valor padrão. Dados do simulador: `resultado_backtest_simulador.json` das rodadas com `dist_escanteios`/`dist_faltas`.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import os
import urllib.parse

import numpy as np
from scipy.optimize import minimize
from scipy.special import gammaln

from avaliar_mistura_elo_xg import _get

MERCADOS = {"escanteios": (8.5, 9.5, 10.5, 11.5), "faltas": (22.5, 24.5, 26.5, 28.5), "cartoes": (2.5, 3.5, 4.5, 5.5)}
PREFIXO = {"escanteios": "corners_over_under_", "faltas": "faltas_over_under_", "cartoes": "cartoes_over_under_"}
FONTES_OK = ("match_events", "fallback_fotmob")


def nb_logpmf(k, mu, r):
    mu = np.maximum(mu, 1e-6)
    return gammaln(k + r) - gammaln(r) - gammaln(k + 1) + r * np.log(r / (r + mu)) + k * np.log(mu / (r + mu))


def nb_cdf_over(linha, mu, r, kmax=80):
    """P(total > linha) para vetores mu (linha é x.5)."""
    k = np.arange(int(linha) + 1)
    p = np.exp(nb_logpmf(k[None, :], mu[:, None], r)) if r else np.exp(-mu[:, None] + k[None, :] * np.log(np.maximum(mu[:, None], 1e-9)) - gammaln(k + 1)[None, :])
    return 1 - p.sum(1)


def dispersao(y, mu):
    """r da binomial negativa por momentos (var = mu + mu^2/r); None = Poisson."""
    v = float(np.mean((y - mu) ** 2)); m = float(np.mean(mu)); m2 = float(np.mean(mu ** 2))
    return m2 / (v - m) if v > m * 1.001 else None


def ic(x, B=2000, semente=3):
    rng = np.random.default_rng(semente)
    n = len(x)
    ms = np.sort([x[rng.integers(0, n, n)].mean() for _ in range(B)])
    return float(x.mean()), float(ms[int(0.025 * B)]), float(ms[int(0.975 * B)])


def baixar_disciplina(ids, cache):
    if cache and os.path.exists(cache):
        return {int(k): v for k, v in json.load(open(cache)).items()}
    out = {}
    for i in range(0, len(ids), 80):
        filtro = ",".join(str(x) for x in ids[i:i + 80])
        pag = _get(f"match_disciplina?select=match_id,cartoes_amarelos,cartoes_vermelhos_equiv,faltas_cometidas,fonte_cartoes&match_id=in.({filtro})&limit=1000")
        for r in pag:
            out.setdefault(r["match_id"], []).append(r)
    if cache:
        json.dump({str(k): v for k, v in out.items()}, open(cache, "w"))
    return out


def baixar_cadastrado(nome, ids, cache):
    if cache and os.path.exists(cache):
        return {int(k): v for k, v in json.load(open(cache)).items()}
    mk = ",".join(f"{PREFIXO[m]}{l}" for m in MERCADOS for l in MERCADOS[m])
    out = {}
    for i in range(0, len(ids), 60):
        filtro = ",".join(str(x) for x in ids[i:i + 60])
        off = 0
        while True:
            pag = _get(f"model_predictions?select=match_id,market,probability&model_name=eq.{urllib.parse.quote(nome)}&selection=eq.over&market=in.({mk})&match_id=in.({filtro})&order=id.asc&limit=1000&offset={off}")
            for r in pag:
                out.setdefault(r["match_id"], {})[r["market"]] = float(r["probability"])
            if len(pag) < 1000:
                break
            off += 1000
    if cache:
        json.dump({str(k): v for k, v in out.items()}, open(cache, "w"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pasta", default="dados_referencia/backtest_simulador")
    ap.add_argument("--treino", nargs="*", default=[], help="resultados do simulador na temporada de treino (2024)")
    ap.add_argument("--teste", nargs="*", default=[], help="resultados do simulador na temporada de teste (2025)")
    ap.add_argument("--variante", default="elo_xg_misto")
    ap.add_argument("--cadastrado", default="markov_multievento_v1")
    ap.add_argument("--cache", default="", help="prefixo de caminho para cache JSON (ex.: /tmp/esc)")
    ap.add_argument("--k", type=float, default=10.0, help="jogos de encolhimento das taxas dos times")
    ap.add_argument("--saida", default="")
    a = ap.parse_args()

    jogos = []
    for f in sorted(glob.glob(os.path.join(a.pasta, "*_2022_25.csv"))):
        liga = os.path.basename(f).replace("_2022_25.csv", "")
        for r in csv.DictReader(open(f)):
            jogos.append({"id": int(r["id"]), "liga": liga, "temp": r["season"], "data": r["date"], "casa": r["home"], "fora": r["away"],
                          "esc": (float(r["hc"]) + float(r["ac"])) if r["hc"] != "" and r["ac"] != "" else None})
    jogos.sort(key=lambda j: (j["data"], j["id"]))
    disc = baixar_disciplina([j["id"] for j in jogos], a.cache + "_disc.json" if a.cache else "")
    for j in jogos:
        rows = disc.get(j["id"], [])
        j["fal"] = sum(r["faltas_cometidas"] for r in rows) if len(rows) == 2 and all(r["faltas_cometidas"] is not None for r in rows) else None
        ok = len(rows) == 2 and all(r["fonte_cartoes"] in FONTES_OK and r["cartoes_amarelos"] is not None for r in rows)
        j["car"] = sum(r["cartoes_amarelos"] + (r["cartoes_vermelhos_equiv"] or 0) for r in rows) if ok else None
    chaves = {"escanteios": "esc", "faltas": "fal", "cartoes": "car"}
    for m, k in chaves.items():
        print(f"{m}: total médio {np.mean([j[k] for j in jogos if j[k] is not None]):.2f} em {sum(j[k] is not None for j in jogos)} de {len(jogos)} jogos")

    # taxas dos times, walk-forward (média do total dos jogos anteriores do time, encolhida)
    mu_time = {m: {} for m in MERCADOS}
    for m, k in chaves.items():
        soma, n, liga_soma, liga_n = {}, {}, {}, {}
        for j in jogos:
            L = liga_soma.get(j["liga"], 0.0) / liga_n[j["liga"]] if liga_n.get(j["liga"], 0) >= 60 else None
            if L is not None and j[k] is not None:
                dev = []
                for t in (j["casa"], j["fora"]):
                    tt = (soma.get(t, 0.0) + a.k * L) / (n.get(t, 0) + a.k)
                    dev.append(tt - L)
                mu_time[m][j["id"]] = L + dev[0] + dev[1]
            if j[k] is not None:
                for t in (j["casa"], j["fora"]):
                    soma[t] = soma.get(t, 0.0) + j[k]; n[t] = n.get(t, 0) + 1
                liga_soma[j["liga"]] = liga_soma.get(j["liga"], 0.0) + j[k]; liga_n[j["liga"]] = liga_n.get(j["liga"], 0) + 1

    treino = [j for j in jogos if j["temp"] == "2024"]
    teste = [j for j in jogos if j["temp"] == "2025"]
    ids_teste = [j["id"] for j in teste]
    cad = baixar_cadastrado(a.cadastrado, ids_teste, a.cache + "_cad.json" if a.cache else "") if a.cadastrado else {}

    # simulador
    sim = {"treino": {}, "teste": {}}
    for chave, arqs in (("treino", a.treino), ("teste", a.teste)):
        for f in arqs:
            for g in json.load(open(f))["jogos"]:
                v = g.get(a.variante)
                if v and v.get("dist_escanteios"):
                    sim[chave][g["id"]] = v

    def dist_media(d):
        tot = sum(c for _, c in d)
        return sum(k * c for k, c in d) / tot

    saida = {}
    for m, k in chaves.items():
        print(f"\n=== {m.upper()} ===")
        tr = [j for j in treino if j[k] is not None]
        te = [j for j in teste if j[k] is not None]
        y_tr = np.array([j[k] for j in tr], float); y = np.array([j[k] for j in te], float)
        print(f"treino {len(tr)} jogos (2024), teste {len(te)} jogos (2025); total médio treino {y_tr.mean():.2f}, teste {y.mean():.2f}")
        mus, rs = {}, {}
        # climatologia (por liga): empírica
        emp = {}
        for lig in {j['liga'] for j in tr}:
            v = np.array([j[k] for j in tr if j["liga"] == lig], float)
            emp[lig] = v
        # taxas dos times
        ok_t = [j for j in tr if j["id"] in mu_time[m]]
        mu_tr = np.array([mu_time[m][j["id"]] for j in ok_t]); rs["taxas dos times"] = dispersao(np.array([j[k] for j in ok_t], float), mu_tr)
        mus["taxas dos times"] = np.array([mu_time[m].get(j["id"], np.nan) for j in te])
        # simulador
        if m != "cartoes" and sim["treino"] and sim["teste"]:
            ch = "dist_escanteios" if m == "escanteios" else "dist_faltas"
            tr_s = [j for j in tr if j["id"] in sim["treino"]]
            x_tr = np.array([dist_media(sim["treino"][j["id"]][ch]) for j in tr_s]); y_trs = np.array([j[k] for j in tr_s], float)
            f = lambda p: float(np.mean(np.exp(p[0] + p[1] * np.log(x_tr)) - y_trs * (p[0] + p[1] * np.log(x_tr))))
            p_pot = minimize(f, [0.0, 1.0], method="Nelder-Mead").x
            g1 = lambda p: float(np.mean(np.exp(p[0]) * x_tr - y_trs * (p[0] + np.log(x_tr))))
            p_niv = minimize(g1, [0.0], method="Nelder-Mead").x
            x_te = np.array([dist_media(sim["teste"][j["id"]][ch]) if j["id"] in sim["teste"] else np.nan for j in te])
            mus["simulador (cru)"] = x_te
            mus["simulador + nível"] = np.exp(p_niv[0]) * x_te
            mus["simulador + potência"] = np.exp(p_pot[0] + p_pot[1] * np.log(x_te))
            rs["simulador (cru)"] = dispersao(y_trs, x_tr)
            rs["simulador + nível"] = dispersao(y_trs, np.exp(p_niv[0]) * x_tr)
            rs["simulador + potência"] = dispersao(y_trs, np.exp(p_pot[0] + p_pot[1] * np.log(x_tr)))
            print(f"simulador: total simulado médio {np.nanmean(x_te):.2f} contra real {y.mean():.2f}; nível c={math.exp(p_niv[0]):.3f}; potência a={p_pot[0]:.3f}, beta={p_pot[1]:.3f}")
        elif m != "cartoes":
            print("(sem resultados do simulador para esta rodada)")
        else:
            print("o simulador não modela cartões")
        # métricas do total
        print(f"\n  {'modelo':26s} {'n':>5s} {'corr(média,total)':>17s} {'logL do total':>13s}")
        resumo = {}
        base_ll = None
        comuns = np.ones(len(te), bool)
        for nome, mu in mus.items():
            comuns &= ~np.isnan(mu)
        # climatologia: logL da empírica suavizada (contagem+0,5)
        ll_clim = []
        for j in te:
            v = emp[j["liga"]]
            ll_clim.append(math.log(((v == j[k]).sum() + 0.5) / (len(v) + 0.5 * 60)))
        ll_clim = np.array(ll_clim)
        print(f"  {'climatologia (liga)':26s} {len(te):5d} {'—':>17s} {ll_clim.mean():13.4f}")
        for nome, mu in mus.items():
            r = rs.get(nome)
            ll = nb_logpmf(y, mu, r) if r else (-mu + y * np.log(np.maximum(mu, 1e-9)) - gammaln(y + 1))
            c = comuns
            cr = float(np.corrcoef(mu[c], y[c])[0, 1])
            d = ll[c] - ll_clim[c]
            mm, lo, hi = ic(d)
            print(f"  {nome:26s} {c.sum():5d} {cr:17.3f} {ll[c].mean():13.4f}   (contra climatologia {mm:+.4f} [{lo:+.4f};{hi:+.4f}]{'*' if lo > 0 or hi < 0 else ''})")
            resumo[nome] = {"corr": cr, "logL": float(ll[c].mean()), "contra_clim": [mm, lo, hi], "r": r}
        # linhas
        print(f"\n  linhas over/under (Brier | acurácia; n={comuns.sum()} jogos comuns)")
        cab = "  linha | freq over | clim.        | " + " | ".join(f"{n[:18]:18s}" for n in list(mus) + ([a.cadastrado] if cad else []))
        print(cab)
        lin_res = {}
        for ln in MERCADOS[m]:
            over = (y > ln)
            pc = np.array([float((emp[j['liga']] > ln).mean()) for j in te])
            linha = f"  {ln:5.1f} | {over[comuns].mean():9.3f} | {((pc - over) ** 2)[comuns].mean():.4f} {((pc > 0.5) == over)[comuns].mean():.3f} | "
            lin_res[ln] = {"freq": float(over[comuns].mean()), "clim": float(((pc - over) ** 2)[comuns].mean())}
            for nome, mu in mus.items():
                p = nb_cdf_over(ln, np.where(np.isnan(mu), 1.0, mu), rs.get(nome))
                b = ((p - over) ** 2)[comuns].mean(); acc = ((p > 0.5) == over)[comuns].mean()
                lin_res[ln][nome] = float(b)
                linha += f"{b:.4f} {acc:.3f}      | "
            if cad:
                sub = [i for i, j in enumerate(te) if comuns[i] and j["id"] in cad and f"{PREFIXO[m]}{ln}" in cad[j["id"]]]
                if len(sub) > 50:
                    p = np.array([cad[te[i]["id"]][f"{PREFIXO[m]}{ln}"] for i in sub]); o = over[sub]
                    pc_s = pc[sub]
                    linha += f"{((p - o) ** 2).mean():.4f} {((p > 0.5) == o).mean():.3f} (n={len(sub)}; clim {((pc_s - o) ** 2).mean():.4f})"
                    lin_res[ln][a.cadastrado] = float(((p - o) ** 2).mean())
            print(linha)
        saida[m] = {"total": resumo, "linhas": {str(k_): v for k_, v in lin_res.items()}, "n_teste": len(te)}
    if a.saida:
        json.dump(saida, open(a.saida, "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
