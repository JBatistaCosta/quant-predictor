#!/usr/bin/env python3
"""Achado 59: a distribuição de placares do simulador é a responsável pela diferença para o Dixon-Coles?

Entrada: resultados do backtest com a contagem de placares simulados (`jogos[i][variante]["placares"]`, gols de cada lado limitados a 7, e `gols_casa`/`gols_fora`)
e o CSV da liga (placares reais). Sem rede.

Perguntas, nos jogos com Dixon-Coles e odds:
  1. Se a força fosse a mesma mas o placar saísse de duas Poisson independentes com as médias de gols do próprio simulador, o 1X2 e o over/under ficam melhores ou piores
     que as probabilidades da simulação? (Isola a forma da distribuição: mesma média, outra forma.)
  2. Verossimilhança do placar real (log P(placar real)): simulador contra Poisson independente com as mesmas médias.
  3. Frequências somadas: empate, 0-0, 1-1, 1-0/0-1, total de gols, contra as reais e contra a Poisson.
  4. Dispersão (variância / média do total de gols em torno da média do modelo) e correlação entre os gols dos dois lados, simulador contra o real.
Uso: python scripts/analisar_placares_simulados.py --variante exp_misto Liga=resultado.json:liga.csv [Liga2=...]
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random

TETO = 7        # gols de cada lado acima disso entram na última célula (como no backtest)


def poisson(lam, k_max=TETO):
    p = [math.exp(-lam) * lam ** k / math.factorial(k) for k in range(k_max + 1)]
    s = sum(p)
    return [x / s for x in p]


def matriz_poisson(lh, la):
    ph, pa = poisson(lh), poisson(la)
    return [[ph[i] * pa[j] for j in range(TETO + 1)] for i in range(TETO + 1)]


def matriz_sim(placares, n, alfa=0.0):
    m = [[alfa] * (TETO + 1) for _ in range(TETO + 1)]
    for x, y, c in placares:
        m[x][y] += c
    s = n + alfa * (TETO + 1) ** 2
    return [[v / s for v in linha] for linha in m]


def p1x2_e_over(m):
    h = sum(m[i][j] for i in range(TETO + 1) for j in range(TETO + 1) if i > j)
    d = sum(m[i][i] for i in range(TETO + 1))
    a = 1.0 - h - d
    over = sum(m[i][j] for i in range(TETO + 1) for j in range(TETO + 1) if i + j >= 3)
    return [h, d, a], over


def perdas(p, over_p, res, over):
    p = [min(max(x, 1e-9), 1) for x in p]
    po = min(max(over_p, 1e-9), 1 - 1e-9)
    br = sum((p[i] - (1.0 if i == res else 0.0)) ** 2 for i in range(3))
    return (-math.log(p[res]), br, -math.log(po if over else 1 - po), (po - (1.0 if over else 0.0)) ** 2)


def ic(dif, B=4000, semente=7):
    rng = random.Random(semente)
    n = len(dif)
    ms = sorted(sum(dif[rng.randrange(n)] for _ in range(n)) / n for _ in range(B))
    return sum(dif) / n, ms[int(0.025 * B)], ms[int(0.975 * B)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ligas", nargs="+", help="Nome=resultado.json:liga.csv")
    ap.add_argument("--variante", default="exp_misto")
    ap.add_argument("--saida", default="")
    a = ap.parse_args()
    v = a.variante
    linhas = []                                                    # um por jogo
    for spec in a.ligas:
        nome, resto = spec.split("=", 1)
        caminho, csv_ = resto.rsplit(":", 1)
        reais = {int(r["id"]): (int(r["hg"]), int(r["ag"])) for r in csv.DictReader(open(csv_))}
        jogos = json.load(open(caminho))
        n_sims = jogos["resumo"]["sims_por_jogo"]
        for g in jogos["jogos"]:
            if "dc" not in g or "mercado" not in g or v not in g or not g[v].get("placares"):
                continue
            hg, ag = reais[g["id"]]
            linhas.append({"liga": nome, "id": g["id"], "hg": hg, "ag": ag, "sim": g[v], "dc": g["dc"], "n": n_sims})
    n = len(linhas)
    print(f"{n} jogos com Dixon-Coles e odds, variante {v}")

    # -------- 1 e 2: Poisson independente com as médias do próprio simulador
    perd_sim, perd_poi, perd_dc, ll_sim, ll_poi = [], [], [], [], []
    for l in linhas:
        s = l["sim"]
        msim = matriz_sim(s["placares"], l["n"])
        mpoi = matriz_poisson(s["gols_casa"], s["gols_fora"])
        res = 0 if l["hg"] > l["ag"] else (1 if l["hg"] == l["ag"] else 2)
        over = l["hg"] + l["ag"] >= 3
        l["msim"], l["mpoi"] = msim, mpoi
        p, o = p1x2_e_over(msim)
        perd_sim.append(perdas(p, o, res, over))
        p, o = p1x2_e_over(mpoi)
        perd_poi.append(perdas(p, o, res, over))
        perd_dc.append(perdas(l["dc"]["p1x2"], l["dc"]["pover"], res, over))
        ms_ = matriz_sim(s["placares"], l["n"], alfa=0.5)
        i, j = min(l["hg"], TETO), min(l["ag"], TETO)
        ll_sim.append(-math.log(ms_[i][j]))
        ll_poi.append(-math.log(max(mpoi[i][j], 1e-12)))
    nomes = ["log-loss 1X2", "Brier 1X2", "log-loss O/U", "Brier O/U"]
    res_json = {"jogos": n, "variante": v}
    print("\n== 1. Mesma média de gols, outra forma da distribuição (log-loss; menor = melhor) ==")
    medias = {k: [sum(x[j] for x in lst) / n for j in range(4)] for k, lst in (("simulação", perd_sim), ("Poisson(médias da simulação)", perd_poi), ("Dixon-Coles", perd_dc))}
    for k, m in medias.items():
        print(f"  {k:30s} " + " ".join(f"{nomes[j]} {m[j]:.4f}" for j in range(4)))
    res_json["medias"] = medias
    res_json["poisson_menos_simulacao"] = {}
    for lab, A, B in (("Poisson - simulação", perd_poi, perd_sim), ("simulação - Dixon-Coles", perd_sim, perd_dc), ("Poisson - Dixon-Coles", perd_poi, perd_dc)):
        out = []
        for j in range(4):
            m, lo, hi = ic([x[j] - y[j] for x, y in zip(A, B)])
            out.append(f"{nomes[j]} {m:+.4f} [{lo:+.4f};{hi:+.4f}]{'*' if lo > 0 or hi < 0 else ''}")
            res_json["poisson_menos_simulacao"][f"{lab}:{nomes[j]}"] = [m, lo, hi]
        print(f"  {lab:30s} " + " | ".join(out))
    m, lo, hi = ic([a_ - b_ for a_, b_ in zip(ll_poi, ll_sim)])
    print(f"\n== 2. -log P(placar real): Poisson - simulação = {m:+.4f} [{lo:+.4f}; {hi:+.4f}]{'*' if lo > 0 or hi < 0 else ''}  (positivo = a simulação explica melhor o placar)")
    print(f"   médias: simulação {sum(ll_sim) / n:.4f}, Poisson {sum(ll_poi) / n:.4f}")
    res_json["verossimilhanca_placar"] = {"simulacao": sum(ll_sim) / n, "poisson": sum(ll_poi) / n, "poisson_menos_simulacao": [m, lo, hi]}

    # -------- 3: frequências somadas
    def freq(f):
        return f
    cel = {"0-0": [(0, 0)], "1-1": [(1, 1)], "2-2": [(2, 2)], "1-0 ou 0-1": [(1, 0), (0, 1)], "2-0 ou 0-2": [(2, 0), (0, 2)], "2-1 ou 1-2": [(2, 1), (1, 2)], "empate (qualquer)": [(i, i) for i in range(TETO + 1)]}
    print("\n== 3. Frequência de placares (média por jogo): real | simulação | Poisson | diferença simulação - real ==")
    res_json["frequencias"] = {}
    for nome, cs in cel.items():
        real = sum(1 for l in linhas if (min(l["hg"], TETO), min(l["ag"], TETO)) in cs) / n
        sim = sum(sum(l["msim"][i][j] for i, j in cs) for l in linhas) / n
        poi = sum(sum(l["mpoi"][i][j] for i, j in cs) for l in linhas) / n
        ep = math.sqrt(real * (1 - real) / n)
        print(f"  {nome:18s} {real:.4f} | {sim:.4f} | {poi:.4f} | {sim - real:+.4f} (ep real ~{ep:.4f})")
        res_json["frequencias"][nome] = {"real": real, "simulacao": sim, "poisson": poi}
    dc_emp = sum(l["dc"]["p1x2"][1] for l in linhas) / n
    print(f"  empate segundo o Dixon-Coles (média): {dc_emp:.4f}")
    res_json["empate_dc"] = dc_emp
    print("  total de gols (0,1,2,3,4,5,6+): real | simulação | Poisson")
    tot_real = [0.0] * 7
    tot_sim = [0.0] * 7
    tot_poi = [0.0] * 7
    for l in linhas:
        tot_real[min(l["hg"] + l["ag"], 6)] += 1 / n
        for nm, mm, arr in (("s", l["msim"], tot_sim), ("p", l["mpoi"], tot_poi)):
            for i in range(TETO + 1):
                for j in range(TETO + 1):
                    arr[min(i + j, 6)] += mm[i][j] / n
    for k in range(7):
        print(f"    {k}{'+' if k == 6 else ' '} {tot_real[k]:.4f} | {tot_sim[k]:.4f} | {tot_poi[k]:.4f}")
    res_json["total_gols"] = {"real": tot_real, "simulacao": tot_sim, "poisson": tot_poi}

    # -------- 4: dispersão e correlação
    fano_sim = []
    cov_sim = []
    for l in linhas:
        m = l["msim"]
        eh = sum(i * m[i][j] for i in range(TETO + 1) for j in range(TETO + 1))
        ea = sum(j * m[i][j] for i in range(TETO + 1) for j in range(TETO + 1))
        vt = sum((i + j - eh - ea) ** 2 * m[i][j] for i in range(TETO + 1) for j in range(TETO + 1))
        fano_sim.append(vt / (eh + ea))
        cov = sum((i - eh) * (j - ea) * m[i][j] for i in range(TETO + 1) for j in range(TETO + 1))
        vh = sum((i - eh) ** 2 * m[i][j] for i in range(TETO + 1) for j in range(TETO + 1))
        va = sum((j - ea) ** 2 * m[i][j] for i in range(TETO + 1) for j in range(TETO + 1))
        cov_sim.append(cov / math.sqrt(vh * va))
    lam_t = [l["sim"]["gols_casa"] + l["sim"]["gols_fora"] for l in linhas]
    fano_real = sum((l["hg"] + l["ag"] - lt) ** 2 for l, lt in zip(linhas, lam_t)) / sum(lam_t)
    rh = [l["hg"] - l["sim"]["gols_casa"] for l in linhas]
    ra = [l["ag"] - l["sim"]["gols_fora"] for l in linhas]
    mh, ma = sum(rh) / n, sum(ra) / n
    corr_real = sum((x - mh) * (y - ma) for x, y in zip(rh, ra)) / math.sqrt(sum((x - mh) ** 2 for x in rh) * sum((y - ma) ** 2 for y in ra))
    corr_boot = []
    rng = random.Random(3)
    for _ in range(2000):
        idx = [rng.randrange(n) for _ in range(n)]
        xs, ys = [rh[i] for i in idx], [ra[i] for i in idx]
        mx, my = sum(xs) / n, sum(ys) / n
        corr_boot.append(sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)))
    corr_boot.sort()
    print("\n== 4. Dispersão e dependência entre os lados ==")
    print(f"  variância / média do total de gols: simulação (dentro do jogo) {sum(fano_sim) / n:.3f}; real em torno da média da simulação {fano_real:.3f}; Poisson = 1,000")
    print(f"  correlação entre os gols dos dois lados: simulação (dentro do jogo) {sum(cov_sim) / n:+.3f}; real (resíduos) {corr_real:+.3f} [{corr_boot[50]:+.3f}; {corr_boot[1949]:+.3f}]; Poisson = 0")
    res_json["dispersao"] = {"fano_sim": sum(fano_sim) / n, "fano_real": fano_real, "corr_sim": sum(cov_sim) / n, "corr_real": corr_real, "corr_real_ic": [corr_boot[50], corr_boot[1949]]}
    # por liga: Poisson - simulação no log-loss do 1X2
    print("\n== Por liga: Poisson - simulação, log-loss do 1X2 e do O/U ==")
    for liga in sorted({l["liga"] for l in linhas}):
        idx = [i for i, l in enumerate(linhas) if l["liga"] == liga]
        d1 = [perd_poi[i][0] - perd_sim[i][0] for i in idx]
        d2 = [perd_poi[i][2] - perd_sim[i][2] for i in idx]
        m1, lo1, hi1 = ic(d1)
        m2, lo2, hi2 = ic(d2)
        print(f"  {liga:15s} n={len(idx):4d}  1X2 {m1:+.4f} [{lo1:+.4f};{hi1:+.4f}]{'*' if lo1 > 0 or hi1 < 0 else ' '} | O/U {m2:+.4f} [{lo2:+.4f};{hi2:+.4f}]{'*' if lo2 > 0 or hi2 < 0 else ' '}")
    if a.saida:
        json.dump(res_json, open(a.saida, "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
