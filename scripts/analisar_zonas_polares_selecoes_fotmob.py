#!/usr/bin/env python3
"""Tabela das 14 zonas polares com os chutes de SELEÇÕES do FotMob (Eurocopa e Copa América, 2012-2024) contra a de clubes da calculadora (Achado 37).

Mesma regra da tela "Zona-a-zona" da Análise de Evento: 7 anéis de distância ao centro do gol (0-6, 6-9, 9-12, 12-16,5, 16,5-22, 22-30, > 30 m) x cone central de 30 graus ou aberto;
sem pênaltis (nem disputa de pênaltis, nem gol contra). IC 95% por bootstrap de PARTIDAS (chutes do mesmo jogo não são independentes). Também mede, por edição, se o xG do FotMob
mudou: xG por chute e gols/xG em três faixas de distância fixas.
Uso: python scripts/analisar_zonas_polares_selecoes_fotmob.py [--saida arq.json]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import baixar_fotmob_torneios as bf  # noqa: E402
import zonas_polares as zp  # noqa: E402

PASTA = "dados_referencia/fotmob"
GOL_X, GOL_Y = 105.0, 34.0
FAIXAS_FIXAS = [("0-12 m", 0, 12), ("12-22 m", 12, 22), (">= 22 m", 22, 999)]


def chutes_validos(reg: dict) -> list[tuple]:
    """[(zona polar 0..13, xG, gol 0/1, distância m)] dos chutes do jogo, sem pênalti, disputa de pênaltis e gol contra."""
    out = []
    for s in ((((reg.get("matchDetails") or {}).get("content") or {}).get("shotmap") or {}).get("shots")) or []:
        if s.get("isOwnGoal") or s.get("situation") == "Penalty" or s.get("period") == "PenaltyShootout" or s.get("expectedGoals") is None:
            continue
        dx, dy = GOL_X - float(s["x"]), float(s["y"]) - GOL_Y
        dist, ang = math.hypot(dx, dy), math.degrees(math.atan2(abs(dy), max(dx, 1e-9)))
        out.append((zp.indice(dist, ang), float(s["expectedGoals"]), 1 if s.get("eventType") == "Goal" else 0, dist))
    return out


def agregar(jogos: list[list[tuple]]) -> dict:
    n = [0] * 14
    sx = [0.0] * 14
    sg = [0] * 14
    for ch in jogos:
        for z, xg, g, _ in ch:
            n[z] += 1
            sx[z] += xg
            sg[z] += g
    return {"n": n, "xg": sx, "gols": sg}


def tabela(jogos: list[list[tuple]], reamostras: int = 1000, semente: int = 7) -> list[dict]:
    base = agregar(jogos)
    total = sum(base["n"])
    rng = random.Random(semente)
    boots = []
    for _ in range(reamostras):
        boots.append(agregar([jogos[rng.randrange(len(jogos))] for _ in jogos]))

    def ic(f):
        v = sorted(f(b) for b in boots)
        return (v[int(0.975 * (len(v) - 1))] - v[int(0.025 * (len(v) - 1))]) / 2

    linhas = []
    for z in range(14):
        n = base["n"][z]
        linhas.append({"zona": zp.NOMES[z], "n": n, "pct": 100 * n / total, "pct_ic": 100 * ic(lambda b: b["n"][z] / sum(b["n"])),
                       "xg_chute": base["xg"][z] / n if n else 0.0, "xg_ic": ic(lambda b: b["xg"][z] / b["n"][z] if b["n"][z] else 0.0),
                       "gol_chute": base["gols"][z] / n if n else 0.0, "gol_ic": ic(lambda b: b["gols"][z] / b["n"][z] if b["n"][z] else 0.0)})
    return linhas


def por_edicao(registros: dict) -> list[dict]:
    """Por torneio-edição: chutes, xG por chute, gols por chute, gols/xG, e xG por chute e gols/xG em faixas de distância fixas."""
    out = []
    for nome, jogos in registros.items():
        todos = [x for ch in jogos for x in ch]
        if not todos:                                           # edições sem mapa de chutes no FotMob
            out.append({"edicao": nome, "jogos": len(jogos), "chutes": 0})
            continue
        linha = {"edicao": nome, "jogos": len(jogos), "chutes": len(todos), "xg_chute": sum(x[1] for x in todos) / len(todos), "gol_chute": sum(x[2] for x in todos) / len(todos),
                 "gols_sobre_xg": sum(x[2] for x in todos) / sum(x[1] for x in todos)}
        for rotulo, lo, hi in FAIXAS_FIXAS:
            sub = [x for x in todos if lo <= x[3] < hi]
            linha[f"xg_{rotulo}"] = sum(x[1] for x in sub) / len(sub) if sub else 0.0
            linha[f"gols_xg_{rotulo}"] = sum(x[2] for x in sub) / sum(x[1] for x in sub) if sub and sum(x[1] for x in sub) else 0.0
        out.append(linha)
    return out


def carregar_tudo(pasta: str = PASTA) -> dict[str, list[list[tuple]]]:
    reg = {}
    for torneio, (_, temporadas) in bf.TORNEIOS.items():
        for t in temporadas:
            reg[f"{torneio} {t}"] = [chutes_validos(r) for r in bf.carregar(pasta, torneio, t)]
    return reg


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--saida", default="")
    a = ap.parse_args()
    reg = carregar_tudo()
    todos = [ch for jogos in reg.values() for ch in jogos]
    linhas = tabela(todos)
    n = sum(l["n"] for l in linhas)
    print(f"{len(todos)} jogos, {n} chutes (sem pênaltis)\n")
    print(f"{'zona':16s} {'n':>6s} {'% sel ± IC':>13s} {'% clubes':>9s} | {'xG/chute sel ± IC':>18s} {'clubes':>7s} | {'gol/chute sel ± IC':>19s} {'clubes':>7s}")
    for l, (_, pf, xf, gf) in zip(linhas, zp.FOTMOB):
        print(f"{l['zona']:16s} {l['n']:6d} {l['pct']:6.2f} ± {l['pct_ic']:4.2f} {pf:9.2f} | {l['xg_chute']:7.3f} ± {l['xg_ic']:5.3f} {xf:8.3f} | {l['gol_chute']:8.3f} ± {l['gol_ic']:5.3f} {gf:8.3f}")
    print(f"\nTotal: xG/chute {sum(l['n'] * l['xg_chute'] for l in linhas) / n:.4f} (clubes {sum(p / 100 * x for _, p, x, _ in zp.FOTMOB):.4f}); "
          f"gol/chute {sum(l['n'] * l['gol_chute'] for l in linhas) / n:.4f} (clubes {sum(p / 100 * g for _, p, _, g in zp.FOTMOB):.4f})")
    ed = por_edicao(reg)
    print("\nPor edição: jogos chutes | xG/chute gol/chute gols/xG | xG 0-12 m, 12-22 m, >=22 m | gols/xG 0-12 m, 12-22 m, >=22 m")
    for e in ed:
        if not e["chutes"]:
            print(f"{e['edicao']:22s} {e['jogos']:3d}     0 | sem mapa de chutes no FotMob")
            continue
        print(f"{e['edicao']:22s} {e['jogos']:3d} {e['chutes']:5d} | {e['xg_chute']:.4f} {e['gol_chute']:.4f} {e['gols_sobre_xg']:.2f} | "
              f"{e['xg_0-12 m']:.3f} {e['xg_12-22 m']:.3f} {e['xg_>= 22 m']:.3f} | {e['gols_xg_0-12 m']:.2f} {e['gols_xg_12-22 m']:.2f} {e['gols_xg_>= 22 m']:.2f}")
    if a.saida:
        json.dump({"tabela": linhas, "por_edicao": ed}, open(a.saida, "w"), ensure_ascii=False)
