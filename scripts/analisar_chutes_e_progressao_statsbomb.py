#!/usr/bin/env python3
"""xG por chute (com coordenadas polares) e progressão da bola por posição na posse (Achado 34).

Para cada chute: zona (18) de onde saiu, xG do StatsBomb, gol, tipo (jogo corrido / bola parada / pênalti / cabeça) e a posição em coordenadas POLARES até o centro do
gol (distância em metros, ângulo em graus em relação ao eixo do campo; 0 = de frente). Para cada linha que continua com a mesma equipe: de qual zona para qual zona
(destino = zona da próxima linha da equipe), separada por posição da linha dentro da posse (mesmos 7 cortes de `simulador_cadeia_bola.POSICOES_CORRIDA`).
Uso: python scripts/analisar_chutes_e_progressao_statsbomb.py [--grupo ligas_2015_16] [--saida arq.json]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analisar_residuo_entradas_statsbomb as r  # noqa: E402
import comparar_competicoes_statsbomb as c  # noqa: E402
import gerar_matriz_transicao_statsbomb as g  # noqa: E402
import simulador_cadeia_bola as s  # noqa: E402

GOL_X_M, GOL_Y_M = g.CAMPO_X, g.CAMPO_Y / 2          # centro do gol adversário (105, 34)
TIPO_CHUTE = {"Open Play": 0, "Free Kick": 1, "Corner": 1, "Penalty": 2, "Kick Off": 0}


def polar_do_gol(x_m: float, y_m: float) -> tuple[float, float]:
    """(distância em metros, ângulo em graus entre a direção ao centro do gol e o eixo do campo; 0 = de frente, 90 = na linha de fundo)."""
    dx, dy = GOL_X_M - x_m, y_m - GOL_Y_M
    return math.hypot(dx, dy), math.degrees(math.atan2(abs(dy), max(dx, 1e-9)))


def chutes_da_partida(ev: list[dict]) -> list[list]:
    """[zona, xg, gol, tipo (0 jogo, 1 bola parada, 2 pênalti), cabeça (0/1), distância m, ângulo graus]."""
    out = []
    for e in ev:
        if (e.get("type") or {}).get("name") != "Shot" or not e.get("location") or e.get("period") not in (1, 2, 3, 4):
            continue
        sh = e.get("shot") or {}
        x, y = g.para_metros(*e["location"][:2])
        d, ang = polar_do_gol(x, y)
        out.append([g.zona_18(x, y), round(float(sh.get("statsbomb_xg") or 0.0), 4), 1 if ((sh.get("outcome") or {}).get("name")) == "Goal" else 0,
                    TIPO_CHUTE.get(((sh.get("type") or {}).get("name")), 0), 1 if ((sh.get("body_part") or {}).get("name")) == "Head" else 0, round(d, 2), round(ang, 1)])
    return out


def progressao_da_partida(ev: list[dict], acc: list) -> None:
    linhas = r.linhas_com_janela(ev)
    posicao = 0
    for i, (cl, z, eq, tipo, _) in enumerate(linhas):
        posicao = 1 if i == 0 or linhas[i - 1][2] != eq else posicao + 1
        if tipo == "continua" and i + 1 < len(linhas) and linhas[i + 1][2] == eq:
            acc[s.balde_da_posicao(posicao)][z][linhas[i + 1][1]] += 1


def processar(pasta: str, grupo: str) -> dict:
    chutes: list[list] = []
    prog = [[[0] * 18 for _ in range(18)] for _ in s.POSICOES_CORRIDA]
    jogos = 0
    for rotulo in sorted(json.load(open(os.path.join(pasta, grupo, d, "INDICE.json")))["rotulo"] for d in os.listdir(os.path.join(pasta, grupo))):
        for reg in c.carregar_completo(pasta, grupo, rotulo):
            jogos += 1
            chutes += chutes_da_partida(reg["eventos"])
            progressao_da_partida(reg["eventos"], prog)
    return {"jogos": jogos, "colunas_chutes": ["zona", "xg", "gol", "tipo", "cabeca", "dist_m", "angulo_graus"], "chutes": chutes, "progressao_por_posicao": prog}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pasta", default="dados_referencia/statsbomb/completo")
    ap.add_argument("--grupo", default="ligas_2015_16")
    ap.add_argument("--saida", default="dados_referencia/statsbomb/chutes_e_progressao_ligas_2015_16.json")
    a = ap.parse_args()
    res = processar(a.pasta, a.grupo)
    json.dump(res, open(a.saida, "w"), separators=(",", ":"))
    print(res["jogos"], "jogos,", len(res["chutes"]), "chutes ->", a.saida)
