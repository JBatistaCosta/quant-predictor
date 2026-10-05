#!/usr/bin/env python3
"""Compara, como previsores do 1X2, o Elo normal (por resultado, `team_elo_history` escopo global), o Elo por xG (`team_elo_xg_history`; escore de atualização 25% resultado + 75% xG)
e misturas das duas notas (25% normal + 75% xG, e 75% normal + 25% xG), todas convertidas em 1X2 pelo MESMO logit ordenado de 3 parâmetros (`P(casa) = sigma(a*x - t)`,
`P(fora) = sigma(-a*x - t)`, x = (diferença de nota + mando)/400*ln10), ajustado nas temporadas 2022 e 2023 e avaliado em 2024 e 2025. Também um logit com os dois Elos como variáveis
separadas (pesos livres, ajustados no treino) e o peso ótimo de mistura estimado no treino.
Nos jogos de 2025 com Dixon-Coles e odds, compara com o mercado e o Dixon-Coles. Dados: CSVs do backtest (ids, gols, Elo normal em `elod`, Dixon-Coles e odds) e as notas do Elo por xG
baixadas de `team_elo_xg_history` (somente leitura; SUPABASE_URL e SUPABASE_KEY por variável de ambiente, sem valor padrão; cache opcional).
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import os
import time
import urllib.error
import urllib.request

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit


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


def baixar_elo_xg(ids, cache):
    """Devolve {match_id: {str(team_id): rating_antes}}; guarda/lê um cache JSON se pedido."""
    if cache and os.path.exists(cache):
        return {int(k): v for k, v in json.load(open(cache)).items()}
    out = {}
    for i in range(0, len(ids), 80):
        filtro = ",".join(str(x) for x in ids[i:i + 80])
        off = 0
        while True:
            pag = _get(f"team_elo_xg_history?select=match_id,team_id,rating_antes&match_id=in.({filtro})&limit=1000&offset={off}&order=id.asc")
            for r in pag:
                out.setdefault(r["match_id"], {})[str(r["team_id"])] = r["rating_antes"]
            if len(pag) < 1000:
                break
            off += 1000
    if cache:
        json.dump({str(k): v for k, v in out.items()}, open(cache, "w"))
    return out


def logit_ord(x, a, t):
    pc, pf = expit(a * x - t), expit(-a * x - t)
    return np.stack([pc, 1 - pc - pf, pf], 1)


def ic(x, B=3000, semente=3):
    rng = np.random.default_rng(semente)
    n = len(x)
    ms = np.sort([x[rng.integers(0, n, n)].mean() for _ in range(B)])
    return float(x.mean()), float(ms[int(0.025 * B)]), float(ms[int(0.975 * B)])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pasta", default="dados_referencia/backtest_simulador")
    ap.add_argument("--cache", default="")
    ap.add_argument("--saida", default="")
    a = ap.parse_args()
    jogos = []
    for f in sorted(glob.glob(os.path.join(a.pasta, "*_2022_25.csv"))):
        for r in csv.DictReader(open(f)):
            if r["elod"] == "":
                continue
            hg, ag = int(r["hg"]), int(r["ag"])
            jogos.append({"id": int(r["id"]), "temp": r["season"], "casa": r["home"], "fora": r["away"], "d": float(r["elod"]), "y": 0 if hg > ag else (1 if hg == ag else 2),
                          "dc": [float(r[k]) for k in ("dc_h", "dc_d", "dc_a")] if r["dc_h"] else None, "mk": [float(r[k]) for k in ("o_h", "o_d", "o_a")] if r["o_h"] else None})
    elo_xg = baixar_elo_xg([j["id"] for j in jogos], a.cache)
    usados = []
    for j in jogos:
        d = elo_xg.get(j["id"])
        if d and j["casa"] in d and j["fora"] in d:
            j["dx"] = d[j["casa"]] - d[j["fora"]]
            usados.append(j)
    print(f"{len(jogos)} jogos com Elo normal; {len(usados)} também com Elo por xG", flush=True)
    treino = [j for j in usados if j["temp"] in ("2022", "2023")]
    teste = [j for j in usados if j["temp"] in ("2024", "2025")]
    dt, dxt, yt = (np.array([j[k] for j in treino]) for k in ("d", "dx", "y"))
    d, dx, y = (np.array([j[k] for j in teste]) for k in ("d", "dx", "y"))
    print(f"treino {len(treino)} (2022-2023), teste {len(teste)} (2024-2025); desvio-padrão da diferença de nota: Elo normal {dt.std():.1f}, Elo por xG {dxt.std():.1f}; correlação entre as duas {np.corrcoef(dt, dxt)[0, 1]:.3f}\n")

    def ajustar(x_treino):
        def nll(p):
            P = logit_ord((x_treino + p[1]) / 400 * math.log(10), p[0], abs(p[2]))
            return -np.log(np.clip(P[np.arange(len(yt)), yt], 1e-9, 1)).sum()
        r = minimize(nll, [1.0, 60.0, 0.6], method="Nelder-Mead")
        return r.x[0], r.x[1], abs(r.x[2])

    modelos = {}
    for nome, xt, xe in (("Elo normal", dt, d), ("Elo por xG", dxt, dx), ("mistura 25% normal + 75% xG", 0.25 * dt + 0.75 * dxt, 0.25 * d + 0.75 * dx),
                         ("mistura 75% normal + 25% xG", 0.75 * dt + 0.25 * dxt, 0.75 * d + 0.25 * dx), ("mistura 50/50", 0.5 * dt + 0.5 * dxt, 0.5 * d + 0.5 * dx)):
        pa, ph, pt = ajustar(xt)
        modelos[nome] = (logit_ord((xe + ph) / 400 * math.log(10), pa, pt), (pa, ph, pt))
    # peso livre da mistura, estimado no treino
    melhor = None
    for w in np.linspace(0, 1, 11):
        pa, ph, pt = ajustar(w * dt + (1 - w) * dxt)
        P = logit_ord((w * dt + (1 - w) * dxt + ph) / 400 * math.log(10), pa, pt)
        v = -np.log(np.clip(P[np.arange(len(yt)), yt], 1e-9, 1)).mean()
        if melhor is None or v < melhor[0]:
            melhor = (v, w, (pa, ph, pt))
    w_otimo = melhor[1]
    pa, ph, pt = melhor[2]
    modelos[f"mistura com peso ótimo do treino (normal {w_otimo:.1f})"] = (logit_ord((w_otimo * d + (1 - w_otimo) * dx + ph) / 400 * math.log(10), pa, pt), melhor[2])
    # duas variáveis livres
    def nll2(p):
        x = (p[0] * dt + p[1] * dxt + p[2]) / 400 * math.log(10)
        P = logit_ord(x, 1.0, abs(p[3]))
        return -np.log(np.clip(P[np.arange(len(yt)), yt], 1e-9, 1)).sum()
    r2 = minimize(nll2, [0.4, 0.4, 60.0, 0.6], method="Nelder-Mead", options={"maxiter": 4000})
    P2 = logit_ord((r2.x[0] * d + r2.x[1] * dx + r2.x[2]) / 400 * math.log(10), 1.0, abs(r2.x[3]))
    modelos["logit com os dois Elos (pesos livres)"] = (P2, tuple(r2.x))
    freq = np.bincount(yt, minlength=3) / len(yt)
    modelos["climatologia"] = (np.tile(freq, (len(y), 1)), None)

    n = len(y)
    perdas = {}
    print(f"{'modelo':58s} {'acerto venc.':>12s} {'acerto 3':>8s} {'P(emp)':>7s} {'AUC emp':>7s} {'ECE':>6s} {'log-loss':>8s} {'Brier':>6s}")
    saida = {}
    venc = y != 1
    for nome, (P, par) in modelos.items():
        ll = -np.log(np.clip(P[np.arange(n), y], 1e-9, 1))
        br = ((P - np.eye(3)[y]) ** 2).sum(1)
        perdas[nome] = ll
        lado = np.where(P[:, 0] >= P[:, 2], 0, 2)
        acc_v = float((lado[venc] == y[venc]).mean())
        conf = P.max(1)
        ac = P.argmax(1) == y
        faixas = np.minimum((conf * 10).astype(int), 9)
        ece = sum(abs(ac[faixas == b].mean() - conf[faixas == b].mean()) * (faixas == b).sum() for b in range(10) if (faixas == b).any()) / n
        emp = (y == 1).astype(int)
        o = np.argsort(P[:, 1]); rk = np.empty(n); rk[o] = np.arange(1, n + 1)
        auc = (rk[emp == 1].sum() - emp.sum() * (emp.sum() + 1) / 2) / (emp.sum() * (n - emp.sum()))
        print(f"{nome:58s} {acc_v:12.3f} {ac.mean():8.3f} {P[:, 1].mean():7.3f} {auc:7.3f} {ece:6.3f} {ll.mean():8.4f} {br.mean():6.4f}")
        saida[nome] = {"acc_vencedor": acc_v, "acc_3": float(ac.mean()), "p_empate": float(P[:, 1].mean()), "auc_empate": float(auc), "ece": float(ece), "logloss": float(ll.mean()),
                       "brier": float(br.mean()), "parametros": [float(v) for v in par] if par is not None else None}
    base = "Elo normal"
    print(f"\nDiferença de log-loss pareada contra o Elo normal (teste 2024-2025, n={n}; negativo = melhor):")
    for nome in modelos:
        if nome == base:
            continue
        m, lo, hi = ic(perdas[nome] - perdas[base])
        print(f"  {nome:58s} {m:+.4f} [{lo:+.4f}; {hi:+.4f}]{'*' if lo > 0 or hi < 0 else ' '}")
        saida[nome]["contra_elo_normal"] = [m, lo, hi]
    idx = [i for i, j in enumerate(teste) if j["dc"] and j["mk"]]
    def devig(o):
        inv = [1 / x for x in o]; s = sum(inv); return [x / s for x in inv]
    ll_mk = np.array([-math.log(devig(teste[i]["mk"])[teste[i]["y"]]) for i in idx])
    ll_dc = np.array([-math.log(teste[i]["dc"][teste[i]["y"]]) for i in idx])
    print(f"\nNos {len(idx)} jogos de 2025 com Dixon-Coles e odds (log-loss do 1X2; mercado {ll_mk.mean():.4f}, Dixon-Coles {ll_dc.mean():.4f}):")
    for nome in modelos:
        sub = perdas[nome][idx]
        a1, a2 = ic(sub - ll_mk), ic(sub - ll_dc)
        print(f"  {nome:58s} {sub.mean():.4f} | mercado {a1[0]:+.4f} [{a1[1]:+.4f};{a1[2]:+.4f}] | Dixon-Coles {a2[0]:+.4f} [{a2[1]:+.4f};{a2[2]:+.4f}]")
        saida[nome]["contra_mercado_2025"] = list(a1)
        saida[nome]["contra_dc_2025"] = list(a2)
    if a.saida:
        json.dump({"jogos_teste": n, "jogos_2025_com_odds": len(idx), "modelos": saida}, open(a.saida, "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
