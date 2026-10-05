#!/usr/bin/env python3
"""Calibra a calculadora (src/pages/AnaliseEvento.jsx) INTEIRA contra os resultados: forma recente (xG e xGA dos últimos jogos, com decaimento) + mando (gamma) + Elo + Poisson com Dixon-Coles.

O Achado 69 calibrou só o fator de Elo isolado (base de gols igual para todos). Na tela, porém, o λ vem de xG próprio x xGA do adversário / média da liga x gamma de mando (fórmula 'multiplicativo'),
que já carrega a força dos times; o fator de Elo multiplica POR CIMA, então a mesma força entra duas vezes e o favorito da casa fica inflado. Aqui a tela é reproduzida jogo a jogo, só com informação
anterior ao jogo: forma = últimos `--janela` jogos de cada time (qualquer mando), peso exp(-xi*i) (ξ 0,2, como a importação), xG/xGA dos CSVs do backtest; Elo = `elod`; posse neutra (50%).
Ajusta em 2022-2023 e avalia em 2024-2025 (sem tocar no teste). Variantes: sem Elo; fórmula atual (k 0,5737, peso 50%); E/0,5 antigo; k refeito; k e razão de mando refeitos; k, mando e expoente
da força da forma refeitos. Medidas: log-loss, Brier, ECE do favorito e, para os jogos em que o mandante é favorito (P(vitória casa) > 50%), probabilidade média prevista contra a frequência real.
Sem rede.
"""

from __future__ import annotations

import argparse
import csv
import glob
import math
import os
from collections import defaultdict

import numpy as np
from scipy.optimize import minimize
from scipy.special import gammaln

LIGA_H, LIGA_A = 1.3854, 1.1274
LIGA_G = (LIGA_H + LIGA_A) / 2
GAMMA_H, GAMMA_A = LIGA_H / LIGA_G, LIGA_A / LIGA_G
RHO = -0.042
K_ATUAL = 0.5737


def ler(pasta, janela, xi, minimo):
    jogos = []
    for f in sorted(glob.glob(os.path.join(pasta, "*_2022_25.csv"))):
        for r in csv.DictReader(open(f)):
            if r["elod"] == "" or r["hxg"] == "" or r["axg"] == "":
                continue
            jogos.append({"data": r["date"], "id": int(r["id"]), "temp": r["season"], "casa": r["home"], "fora": r["away"], "d": float(r["elod"]),
                          "hxg": float(r["hxg"]), "axg": float(r["axg"]), "hg": int(r["hg"]), "ag": int(r["ag"])})
    jogos.sort(key=lambda j: (j["data"], j["id"]))
    hist = defaultdict(list)                                    # time -> [(xg a favor, xg contra)] do mais antigo ao mais recente
    pesos = np.exp(-xi * np.arange(janela))
    out = []
    for j in jogos:
        hc, hf = hist[j["casa"]], hist[j["fora"]]
        if len(hc) >= minimo and len(hf) >= minimo:
            def forma(h):
                ult = h[-janela:][::-1]                         # mais recente primeiro
                w = pesos[:len(ult)]
                return float(np.dot(w, [a for a, _ in ult]) / w.sum()), float(np.dot(w, [c for _, c in ult]) / w.sum())
            xg1, xga1 = forma(hc)
            xg2, xga2 = forma(hf)
            out.append({**j, "xg1": xg1, "xga1": xga1, "xg2": xg2, "xga2": xga2})
        hist[j["casa"]].append((j["hxg"], j["axg"]))
        hist[j["fora"]].append((j["axg"], j["hxg"]))
    return out


def matriz_1x2(l1, l2, kmax=10):
    k = np.arange(kmax + 1)
    lg = gammaln(k + 1)
    p1 = np.exp(-l1[:, None] + k[None, :] * np.log(l1[:, None]) - lg[None, :])
    p2 = np.exp(-l2[:, None] + k[None, :] * np.log(l2[:, None]) - lg[None, :])
    m = p1[:, :, None] * p2[:, None, :]
    m[:, 0, 0] *= 1 - l1 * l2 * RHO
    m[:, 0, 1] *= 1 + l1 * RHO
    m[:, 1, 0] *= 1 + l2 * RHO
    m[:, 1, 1] *= 1 - RHO
    i, j = np.indices((kmax + 1, kmax + 1))
    P = np.stack([(m * (i > j)).sum((1, 2)), (m * (i == j)).sum((1, 2)), (m * (i < j)).sum((1, 2))], 1)
    return P / P.sum(1, keepdims=True)


def lambdas(d, xg1, xga1, xg2, xga2, k, peso=50.0, r=GAMMA_H / GAMMA_A, beta=1.0, antigo=False):
    """λ da calculadora: força da forma (xG x xGA / média, elevada a beta) x mando (razão r entre casa e fora, produto do gamma atual) x fator de Elo."""
    c = math.sqrt(GAMMA_H * GAMMA_A)
    gh, ga = c * math.sqrt(r), c / math.sqrt(r)
    forca1 = (xg1 * xga2 / LIGA_G) ** beta * LIGA_G ** (1 - beta)
    forca2 = (xg2 * xga1 / LIGA_G) ** beta * LIGA_G ** (1 - beta)
    if antigo:
        E = 1 / (1 + 10 ** (-(d * peso / 100) / 400))
        f1, f2 = E / 0.5, (1 - E) / 0.5
    else:
        x = d / 400 * math.log(10)
        e = np.clip(k * (peso / 50.0) * x / 2, -math.log(3), math.log(3))
        f1, f2 = np.exp(e), np.exp(-e)
    return np.maximum(0.1, forca1 * gh * f1), np.maximum(0.1, forca2 * ga * f2)


def metricas(P, y, nome):
    n = len(y)
    ll = -np.log(np.clip(P[np.arange(n), y], 1e-9, 1))
    br = ((P - np.eye(3)[y]) ** 2).sum(1)
    conf = P.max(1); ac = P.argmax(1) == y
    faixas = np.minimum((conf * 10).astype(int), 9)
    ece = sum(abs(ac[faixas == b].mean() - conf[faixas == b].mean()) * (faixas == b).sum() for b in range(10) if (faixas == b).any()) / n
    fav = P[:, 0] > 0.5
    return {"nome": nome, "ll": float(ll.mean()), "brier": float(br.mean()), "ece": float(ece), "p_casa_media": float(P[:, 0].mean()), "casa_real": float((y == 0).mean()),
            "fav_n": int(fav.sum()), "fav_prev": float(P[fav, 0].mean()) if fav.any() else float("nan"), "fav_real": float((y[fav] == 0).mean()) if fav.any() else float("nan"),
            "emp_prev": float(P[:, 1].mean()), "emp_real": float((y == 1).mean())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pasta", default="dados_referencia/backtest_simulador")
    ap.add_argument("--janela", type=int, default=10)
    ap.add_argument("--xi", type=float, default=0.2)
    ap.add_argument("--minimo", type=int, default=5, help="jogos anteriores mínimos de cada time")
    a = ap.parse_args()
    jogos = ler(a.pasta, a.janela, a.xi, a.minimo)
    tr = [j for j in jogos if j["temp"] in ("2022", "2023")]
    te = [j for j in jogos if j["temp"] in ("2024", "2025")]
    col = lambda L, k: np.array([j[k] for j in L], float)
    yf = lambda L: np.array([0 if j["hg"] > j["ag"] else (1 if j["hg"] == j["ag"] else 2) for j in L])
    ytr, yte = yf(tr), yf(te)
    args = lambda L: [col(L, k) for k in ("d", "xg1", "xga1", "xg2", "xga2")]
    Atr, Ate = args(tr), args(te)
    print(f"{len(tr)} jogos de ajuste (2022-2023) e {len(te)} de teste (2024-2025); forma = últimos {a.janela} jogos, ξ {a.xi}\n")

    def perda(p, extra):
        k, lr, beta = p[0], p[1] if extra >= 1 else math.log(GAMMA_H / GAMMA_A), p[2] if extra >= 2 else 1.0
        l1, l2 = lambdas(*Atr, k=k, r=math.exp(lr), beta=beta)
        P = matriz_1x2(l1, l2)
        return float(-np.log(np.clip(P[np.arange(len(ytr)), ytr], 1e-9, 1)).mean())
    ajustes = {}
    r0 = minimize(lambda p: perda([p[0], 0, 1], 0), [0.3], method="Nelder-Mead"); ajustes["k"] = (r0.x[0], math.log(GAMMA_H / GAMMA_A), 1.0)
    r1 = minimize(lambda p: perda([p[0], p[1], 1], 1), [0.3, 0.2], method="Nelder-Mead"); ajustes["k+mando"] = (r1.x[0], r1.x[1], 1.0)
    r2 = minimize(lambda p: perda(p, 2), [0.3, 0.2, 1.0], method="Nelder-Mead"); ajustes["k+mando+beta"] = tuple(r2.x)
    r3 = minimize(lambda p: perda([p[0], math.log(GAMMA_H / GAMMA_A), p[1]], 2), [0.3, 1.0], method="Nelder-Mead")       # mando fixo no gamma atual (o gamma é compartilhado com outras partes do app)
    ajustes["k+beta (mando atual)"] = (r3.x[0], math.log(GAMMA_H / GAMMA_A), r3.x[1])
    for nome, (k, lr, b) in ajustes.items():
        print(f"ajuste {nome:14s}: k {k:.4f}, razão casa/fora {math.exp(lr):.3f} (atual {GAMMA_H / GAMMA_A:.3f}), expoente da forma {b:.3f}")
    res = []
    for nome, kw in (("sem Elo (peso 0%)", dict(k=0.0, peso=0.0)), ("atual: k 0,5737, peso 50%", dict(k=K_ATUAL)), ("antigo E/0,5, peso 50%", dict(k=0, antigo=True)),
                     ("antigo E/0,5, peso 100%", dict(k=0, antigo=True, peso=100.0))):
        P = matriz_1x2(*lambdas(*Ate, **kw)); res.append(metricas(P, yte, nome))
    for nome, (k, lr, b) in ajustes.items():
        P = matriz_1x2(*lambdas(*Ate, k=k, r=math.exp(lr), beta=b)); res.append(metricas(P, yte, f"refeito: {nome}"))
    print(f"\n{'variante (teste 2024-2025)':34s} {'log-loss':>8s} {'Brier':>7s} {'ECE':>6s} {'P(casa)':>8s}/{'real':>5s} {'fav. casa: prev':>16s}/{'real':>5s} {'(n)':>6s} {'P(emp)':>7s}/{'real':>5s}")
    for m in res:
        print(f"{m['nome']:34s} {m['ll']:8.4f} {m['brier']:7.4f} {m['ece']:6.3f} {m['p_casa_media']:8.3f}/{m['casa_real']:.3f} {m['fav_prev']:16.3f}/{m['fav_real']:.3f} ({m['fav_n']:4d}) {m['emp_prev']:7.3f}/{m['emp_real']:.3f}")
    # confiabilidade da probabilidade de vitória da casa, atual e refeita
    print("\nConfiabilidade de P(vitória da casa): faixa prevista | n | prevista média | real — atual e refeito (k+beta, mando atual = o que está na calculadora)")
    atual = matriz_1x2(*lambdas(*Ate, k=K_ATUAL))[:, 0]
    k, lr, b = ajustes["k+beta (mando atual)"]
    novo = matriz_1x2(*lambdas(*Ate, k=k, r=math.exp(lr), beta=b))[:, 0]
    caiu = (yte == 0)
    for lo, hi in ((0, .2), (.2, .3), (.3, .4), (.4, .5), (.5, .6), (.6, .7), (.7, .8), (.8, .9), (.9, 1.01)):
        ma, mn = (atual >= lo) & (atual < hi), (novo >= lo) & (novo < hi)
        if ma.sum() >= 15 or mn.sum() >= 15:
            print(f"  {lo:.1f}-{min(hi, 1):.1f} | atual n={ma.sum():4d} prev {atual[ma].mean() if ma.any() else float('nan'):.3f} real {caiu[ma].mean() if ma.any() else float('nan'):.3f} | refeito n={mn.sum():4d} prev {novo[mn].mean() if mn.any() else float('nan'):.3f} real {caiu[mn].mean() if mn.any() else float('nan'):.3f}")
    # robustez à janela da forma: parâmetros ajustados com a janela pedida, avaliados em outras janelas (teste 2024-2025)
    k, lr, b = ajustes["k+beta (mando atual)"]
    print(f"\nRobustez do ajuste 'k+beta (mando atual)' (k {k:.3f}, expoente {b:.3f}, ajustados com janela {a.janela}) a outras janelas da forma, teste 2024-2025:")
    print(f"  {'janela':>6s} {'n':>5s} | {'sem Elo':>8s} {'atual':>8s} {'refeito':>8s} (log-loss) | ECE atual / refeito | favorito da casa: prev. atual / refeita / real")
    for jan in (5, 10, 20):
        t2 = [j for j in ler(a.pasta, jan, a.xi, min(a.minimo, jan)) if j["temp"] in ("2024", "2025")]
        A2 = [col(t2, kk) for kk in ("d", "xg1", "xga1", "xg2", "xga2")]; y2 = yf(t2)
        m0 = metricas(matriz_1x2(*lambdas(*A2, k=0.0, peso=0.0)), y2, "")
        m1 = metricas(matriz_1x2(*lambdas(*A2, k=K_ATUAL)), y2, "")
        m2 = metricas(matriz_1x2(*lambdas(*A2, k=k, r=math.exp(lr), beta=b)), y2, "")
        print(f"  {jan:6d} {len(t2):5d} | {m0['ll']:8.4f} {m1['ll']:8.4f} {m2['ll']:8.4f}            | {m1['ece']:.3f} / {m2['ece']:.3f}      | {m1['fav_prev']:.3f} / {m2['fav_prev']:.3f} / {m1['fav_real']:.3f}")
    return ajustes, res


if __name__ == "__main__":
    main()
