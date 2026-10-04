#!/usr/bin/env python3
"""Top-N dos jogadores por xG e xA PREVISTO (walk-forward) como previsor do saldo do jogo.

Para cada partida: D_N = soma do λ dos N jogadores de maior λ do mandante MENOS a do visitante (N = 2..5 e elenco inteiro 'all'). O ranking usa só o λ previsto
(informação anterior ao jogo). Alvos: saldo de gols e saldo de xG reais. Como a força do time confunde tudo (Achado do projeto, 3x), reporta também a correlação
PARCIAL controlando pela diferença de Elo, e o IC 95% por bootstrap sobre partidas da diferença entre o top-N e o elenco inteiro.
Uso: python scripts/analisar_topn_jogadores.py arq1.csv [arq2.csv ...]   (colunas: id,season,gd,xgd,elod,x2..x5,xall,a2..a5,aall)
"""
import csv
import math
import random
import sys


def corr(x, y):
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    return sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else 0.0


def parcial(x, y, z):
    rxy, rxz, ryz = corr(x, y), corr(x, z), corr(y, z)
    return (rxy - rxz * ryz) / math.sqrt((1 - rxz ** 2) * (1 - ryz ** 2))


def ler(arquivos):
    out = []
    for a in arquivos:
        for r in csv.DictReader(open(a)):
            if r["elod"] == "":
                continue
            out.append({k: (float(v) if v != "" else None) for k, v in r.items() if k not in ("id", "season")} | {"id": r["id"]})
    return out


def boot(linhas, f, B=2000, seed=11):
    rng = random.Random(seed)
    n = len(linhas)
    v = sorted(f([linhas[rng.randrange(n)] for _ in range(n)]) for _ in range(B))
    return v[int(0.025 * B)], v[int(0.975 * B)]


def main():
    L = ler(sys.argv[1:])
    print(f"{len(L)} partidas com Elo")
    for alvo in ("gd", "xgd"):
        base = [l for l in L if l[alvo] is not None]
        print(f"\n=== alvo: {'saldo de gols' if alvo == 'gd' else 'saldo de xG'} (n={len(base)}); correlação do Elo com o alvo: {corr([l['elod'] for l in base], [l[alvo] for l in base]):.3f}")
        for pref, nome in (("x", "xG previsto"), ("a", "xA previsto")):
            print(f"-- {nome}:   N      r      r parcial (controla Elo)     r_N - r_inteiro [IC95%]")
            y = [l[alvo] for l in base]
            z = [l["elod"] for l in base]
            r_all = corr([l[pref + "all"] for l in base], y)
            for n in ("2", "3", "4", "5", "all"):
                x = [l[pref + n] for l in base]
                r, rp = corr(x, y), parcial(x, y, z)
                if n == "all":
                    print(f"             {'todos':>5s} {r:6.3f}   {rp:6.3f}")
                    continue
                lo, hi = boot(base, lambda s: corr([q[pref + n] for q in s], [q[alvo] for q in s]) - corr([q[pref + "all"] for q in s], [q[alvo] for q in s]))
                print(f"             {n:>5s} {r:6.3f}   {rp:6.3f}                  {r - r_all:+.3f} [{lo:+.3f}; {hi:+.3f}]")


if __name__ == "__main__":
    main()
