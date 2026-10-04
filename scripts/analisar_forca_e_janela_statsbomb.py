#!/usr/bin/env python3
"""Força dos times e janela do jogo, medidas nos eventos do StatsBomb (Achado 32).

Por que não o Elo: casar nomes de clube entre fontes é o que já corrompeu syncs neste projeto; aqui a força sai dos próprios eventos.
Para cada time, em cada papel (ATAQUE = linhas de ação em que ele tem a bola; DEFESA = linhas do adversário quando joga contra ele), conta por zona
(18) quantas linhas houve e quantas terminaram em continua / perda / chute (e quantas continua foram interrompidas por bola parada). Dividindo pelo que um
time médio faria nas mesmas zonas (taxas do conjunto de todas as ligas), sai a razão observado/esperado. Idem por janela de 15 min do jogo (6 janelas).
Os jogos são distribuídos em 4 quartos (índice do jogo % 4) para medir quanto da diferença entre times se repete entre metades (confiabilidade) e para
validar fora da amostra. A saída guarda também chutes, gols e escanteios por time-jogo.

Uso: python scripts/analisar_forca_e_janela_statsbomb.py [--grupo ligas_2015_16] [--saida arq.json]
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analisar_residuo_entradas_statsbomb as r  # noqa: E402
import comparar_competicoes_statsbomb as c  # noqa: E402

TIPOS = ("continua", "perda", "chute")


def vazio() -> list:
    return [[0, 0, 0, 0] for _ in range(18)]          # por zona: [n continua, n perda, n chute, n continua interrompida]


def times_da_partida(ev: list[dict]) -> list[tuple]:
    """[(id, nome)] na ordem em que aparecem em 'Starting XI' (o primeiro é o mandante no StatsBomb)."""
    return [((e.get("team") or {}).get("id"), (e.get("team") or {}).get("name")) for e in ev if (e.get("type") or {}).get("name") == "Starting XI"][:2]


def contar_partida(ev: list[dict]) -> dict:
    """Contagens de uma partida: linhas por (equipe, zona) e totais por janela, mais chutes/gols/escanteios por time."""
    linhas = r.linhas_com_janela(ev)
    por_time: dict = collections.defaultdict(vazio)
    por_janela: dict = collections.defaultdict(vazio)
    for i, (cl, z, eq, tipo, w) in enumerate(linhas):
        k = TIPOS.index(tipo)
        por_time[eq][z][k] += 1
        por_janela[w][z][k] += 1
        if tipo == "continua":
            proxima = linhas[i + 1][0] if i + 1 < len(linhas) else None
            if proxima != "continua":
                por_time[eq][z][3] += 1
                por_janela[w][z][3] += 1
    gols: collections.Counter = collections.Counter()
    escanteios: collections.Counter = collections.Counter()
    for e in ev:
        t = (e.get("type") or {}).get("name")
        eq = (e.get("team") or {}).get("id")
        if t == "Shot" and (((e.get("shot") or {}).get("outcome") or {}).get("name")) == "Goal":
            gols[eq] += 1
        elif t == "Pass" and ((((e.get("pass") or {}).get("type") or {}).get("name")) == "Corner"):
            escanteios[eq] += 1
    return {"por_time": por_time, "por_janela": por_janela, "gols": gols, "escanteios": escanteios}


def processar(pasta: str, grupo: str) -> dict:
    times: dict = {}
    papel = {q: {"ataque": collections.defaultdict(vazio), "defesa": collections.defaultdict(vazio)} for q in range(4)}
    janela = [vazio() for _ in range(6)]
    partidas = []
    idx = 0
    for rotulo in sorted(json.load(open(os.path.join(pasta, grupo, d, "INDICE.json")))["rotulo"] for d in os.listdir(os.path.join(pasta, grupo))):
        for reg in c.carregar_completo(pasta, grupo, rotulo):
            ev = reg["eventos"]
            tt = times_da_partida(ev)
            if len(tt) < 2 or tt[0][0] == tt[1][0]:
                continue
            res = contar_partida(ev)
            q = idx % 4
            idx += 1
            (a, na), (b, nb) = tt
            for atk, dfs in ((a, b), (b, a)):
                for z in range(18):
                    for k in range(4):
                        papel[q]["ataque"][atk][z][k] += res["por_time"][atk][z][k]
                        papel[q]["defesa"][dfs][z][k] += res["por_time"][atk][z][k]
            for w in range(6):
                for z in range(18):
                    for k in range(4):
                        janela[w][z][k] += res["por_janela"][w][z][k]
            times[a], times[b] = na, nb
            chutes = lambda t: sum(res["por_time"][t][z][2] for z in range(18))  # noqa: E731
            partidas.append({"id": reg.get("match_id"), "rotulo": rotulo, "q": q, "casa": a, "fora": b,
                             "chutes": [chutes(a), chutes(b)], "gols": [res["gols"][a], res["gols"][b]], "escanteios": [res["escanteios"][a], res["escanteios"][b]]})
    return {"jogos": len(partidas), "times": {str(k): v for k, v in times.items()},
            "papel": {str(q): {p: {str(t): v for t, v in d.items()} for p, d in dd.items()} for q, dd in papel.items()},
            "janela": janela, "partidas": partidas}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pasta", default="dados_referencia/statsbomb/completo")
    ap.add_argument("--grupo", default="ligas_2015_16")
    ap.add_argument("--saida", default="dados_referencia/statsbomb/forca_e_janela_ligas_2015_16.json")
    a = ap.parse_args()
    res = processar(a.pasta, a.grupo)
    json.dump(res, open(a.saida, "w"), ensure_ascii=False)
    print(res["jogos"], "jogos,", len(res["times"]), "times ->", a.saida)
