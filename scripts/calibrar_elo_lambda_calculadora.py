#!/usr/bin/env python3
"""[SUPERADO pelo Achado 71 -- ver scripts/calibrar_calculadora_completa.py: este ajuste isolado do fator de Elo (k 0,57) deixa o favorito da casa inflado quando a forma recente em xG/xGA entra junto.]
Calibra a conversão diferença de Elo -> gols esperados da calculadora (src/pages/AnaliseEvento.jsx).

Hoje a calculadora multiplica o λ de cada lado por E/0,5 e (1-E)/0,5, com E = 1/(1+10^(-peso*dif/400)): satura (fator 2 e 0 nos extremos, o λ vai ao piso 0,1) e, com peso 100%, é
confiante demais (Achado 62). Aqui a alternativa log-linear: λ_casa = base_casa * exp(+k*x/2) e λ_fora = base_fora * exp(-k*x/2), com x = peso*dif/400*ln(10) e k = K_ELO. O k vem de uma
regressão de Poisson dos gols de cada time-jogo sobre x (efeito de mando nas bases), ajustada nas temporadas 2022 e 2023 e avaliada em 2024 e 2025 (sem tocar no teste). Compara com o E/0,5
atual (peso 50% e 100%) e com o logit ordenado do Achado 62. Entrada: CSVs do backtest (`elod`, gols). Sem rede.
"""

from __future__ import annotations

import argparse
import csv
import glob
import math
import os

import numpy as np
from scipy.optimize import minimize
from scipy.special import gammaln

from avaliar_pseudo_modelo_elo import pmf_matriz


def ler(pasta):
    out = []
    for f in sorted(glob.glob(os.path.join(pasta, "*_2022_25.csv"))):
        liga = os.path.basename(f).replace("_2022_25.csv", "")
        for r in csv.DictReader(open(f)):
            if r["elod"] == "":
                continue
            out.append({"liga": liga, "temp": r["season"], "d": float(r["elod"]), "hg": int(r["hg"]), "ag": int(r["ag"])})
    return out


def ajustar_k(d, hg, ag):
    """Poisson: log λ_casa = a + (k/2) x ; log λ_fora = b - (k/2) x, x = d/400*ln10. Devolve (a, b, k)."""
    x = d / 400.0 * math.log(10.0)

    def nll(p):
        lh, la = p[0] + p[2] * x / 2, p[1] - p[2] * x / 2
        return float(np.mean(np.exp(lh) - hg * lh + gammaln(hg + 1) + np.exp(la) - ag * la + gammaln(ag + 1)))
    r = minimize(nll, [0.3, 0.1, 0.4], method="Nelder-Mead", options={"xatol": 1e-8, "fatol": 1e-12, "maxiter": 5000})
    return r.x


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pasta", default="dados_referencia/backtest_simulador")
    a = ap.parse_args()
    jogos = ler(a.pasta)
    tr = [j for j in jogos if j["temp"] in ("2022", "2023")]
    te = [j for j in jogos if j["temp"] in ("2024", "2025")]
    arr = lambda L, k: np.array([j[k] for j in L], float)
    pa, pb, pk = ajustar_k(arr(tr, "d"), arr(tr, "hg"), arr(tr, "ag"))
    print(f"treino {len(tr)} jogos (2022-2023), teste {len(te)} (2024-2025)")
    print(f"k calibrado = {pk:.4f}  (base casa {math.exp(pa):.3f}, base fora {math.exp(pb):.3f} gols com dif. de Elo 0)")
    d, hg, ag = arr(te, "d"), arr(te, "hg"), arr(te, "ag")
    y = np.where(hg > ag, 0, np.where(hg == ag, 1, 2))

    def ll(P):
        return -np.log(np.clip(P[np.arange(len(y)), y], 1e-9, 1))
    # base de gols (média da liga por lado) como na calculadora atual: média de gols casa/fora do treino por liga
    base = {}
    for liga in {j["liga"] for j in tr}:
        t = [j for j in tr if j["liga"] == liga]
        base[liga] = (sum(j["hg"] for j in t) / len(t), sum(j["ag"] for j in t) / len(t))
    bh = np.array([base[j["liga"]][0] for j in te]); ba = np.array([base[j["liga"]][1] for j in te])
    media = (bh + ba) / 2
    res = {}
    for w in (0.5, 1.0):
        E = 1 / (1 + 10 ** (-(d * w) / 400))
        res[f"E/0,5 atual, peso {int(w * 100)}% (base única)"] = ll(pmf_matriz(np.maximum(0.1, media * E / 0.5), np.maximum(0.1, media * (1 - E) / 0.5)))
    x = d / 400 * math.log(10)
    for nome, k_, mando in (("log-linear k calibrado, base única", pk, False), ("log-linear k calibrado, bases casa/fora", pk, True)):
        lh = (bh if mando else media) * np.exp(k_ * x / 2); la = (ba if mando else media) * np.exp(-k_ * x / 2)
        res[nome] = ll(pmf_matriz(np.maximum(0.1, lh), np.maximum(0.1, la)))
    print(f"\n{'conversão Elo -> λ':46s} {'log-loss 1X2 (2024-2025)':>26s}")
    for nome, v in res.items():
        print(f"  {nome:44s} {v.mean():26.4f}")
    # equivalência com o peso atual: o primeiro termo de E/0,5 é exp(peso*x) -> k_efetivo = peso
    print(f"\nk calibrado equivale a peso de Elo de {pk * 100:.0f}% na fórmula atual (k efetivo de E/0,5 ~ peso).")
    return pk


if __name__ == "__main__":
    main()
