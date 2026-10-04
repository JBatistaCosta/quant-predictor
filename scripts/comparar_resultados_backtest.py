#!/usr/bin/env python3
"""Compara pareado, jogo a jogo, variantes de resultados do backtest do simulador (resultado_backtest_simulador.json), com IC 95% por bootstrap.

Uso: python scripts/comparar_resultados_backtest.py arquivo.json:variante [arquivo2.json:variante2 ...] [--contra arquivo.json:variante]
Cada modelo é comparado com `--contra` (padrão: o primeiro), com o Dixon-Coles e com o mercado, nos jogos que têm os dois e todos os modelos listados.
Métricas: log-loss e Brier do 1X2 e do over/under 2,5 (negativo = melhor que a referência). Sem rede.
"""

from __future__ import annotations

import argparse
import json
import math
import random


def perdas(g, k):
    m = g[k]
    r = g["res"]
    p = m["p1x2"]
    po = min(max(m["pover"], 1e-12), 1 - 1e-12)
    over = g["over"]
    return (-math.log(max(p[r], 1e-12)), sum((p[i] - (1.0 if i == r else 0.0)) ** 2 for i in range(3)),
            -math.log(po if over else 1 - po), (po - (1.0 if over else 0.0)) ** 2)


def carregar(spec):
    caminho, variante = spec.rsplit(":", 1)
    return variante, {g["id"]: g for g in json.load(open(caminho))["jogos"]}


def ic(dif, B=4000, semente=7):
    rng = random.Random(semente)
    n = len(dif)
    ms = sorted(sum(dif[rng.randrange(n)] for _ in range(n)) / n for _ in range(B))
    return sum(dif) / n, ms[int(0.025 * B)], ms[int(0.975 * B)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("modelos", nargs="+")
    ap.add_argument("--contra", default="")
    a = ap.parse_args()
    cargas = [(spec, *carregar(spec)) for spec in a.modelos]
    ref_spec = a.contra or a.modelos[0]
    ref_var, ref_g = carregar(ref_spec)
    ids = set(ref_g)
    for _, _, g in cargas:
        ids &= set(g)
    ids = sorted(i for i in ids if "dc" in ref_g[i] and "mercado" in ref_g[i])
    n = len(ids)
    nomes = ["ll 1X2", "Brier 1X2", "ll O/U", "Brier O/U"]
    print(f"{n} jogos com Dixon-Coles e odds em todos os modelos; referência da diferença: {ref_spec}")
    base = [perdas(ref_g[i], ref_var) for i in ids]
    dc = [perdas(ref_g[i], "dc") for i in ids]
    mk = [perdas(ref_g[i], "mercado") for i in ids]
    for nm, ls in (("dc", dc), ("mercado", mk)):
        print(f"  {nm:8s} " + " ".join(f"{sum(x[k] for x in ls) / n:.4f}" for k in range(4)))
    for spec, var, g in cargas:
        ls = [perdas(g[i], var) for i in ids]
        gols = sum(g[i][var]["gols"] for i in ids) / n
        pov = sum(g[i][var]["pover"] for i in ids) / n
        print(f"\n{spec}: gols/jogo {gols:.3f}, P(over) médio {pov:.3f}; médias " + " ".join(f"{sum(x[k] for x in ls) / n:.4f}" for k in range(4)))
        for lab, outro in ((f"menos {ref_spec}", base), ("menos Dixon-Coles", dc), ("menos mercado", mk)):
            if lab.startswith("menos " + ref_spec) and spec == ref_spec:
                continue
            linha = []
            for k in range(4):
                m, lo, hi = ic([x[k] - y[k] for x, y in zip(ls, outro)])
                sig = "*" if (lo > 0 or hi < 0) else " "
                linha.append(f"{nomes[k]} {m:+.4f} [{lo:+.4f};{hi:+.4f}]{sig}")
            print(f"  {lab:34s} " + " | ".join(linha))


if __name__ == "__main__":
    main()
