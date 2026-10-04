#!/usr/bin/env python3
"""Normaliza a memória da posse por zona (Achado 34).

Os multiplicadores de perda/chute por posição na posse (Achado 33) foram medidos em relação à taxa esperada da zona. Se a distribuição de posições que o simulador
produz em uma zona difere da real, a média do multiplicador na zona deixa de ser 1 e a taxa de perda/chute DA ZONA muda (foi o que empurrava a bola para o ataque).
Aqui o simulador roda, mede a média efetiva do multiplicador em cada zona e a divide, em iterações, até ficar em 1. Grava `memoria_normalizacao_ligas_2015_16.json`.
Uso: python scripts/calibrar_memoria_posse.py [--jogos 3000] [--iteracoes 3]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import simulador_cadeia_bola as s  # noqa: E402

PASTA = "dados_referencia/statsbomb"
ARQ = os.path.join(PASTA, "memoria_normalizacao_ligas_2015_16.json")


def medias_efetivas(med: dict) -> list[tuple[float, float]]:
    return [(med[f"mem|{z}|p"] / med[f"mem|{z}|n"] if med.get(f"mem|{z}|n") else 1.0, med[f"mem|{z}|c"] / med[f"mem|{z}|n"] if med.get(f"mem|{z}|n") else 1.0) for z in range(18)]


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jogos", type=int, default=3000)
    ap.add_argument("--iteracoes", type=int, default=3)
    ap.add_argument("--semente", type=int, default=7)
    a = ap.parse_args()
    if os.path.exists(ARQ):
        os.remove(ARQ)
    par = s.Parametros(PASTA)
    par.norma_memoria = [(1.0, 1.0)] * 18
    for it in range(a.iteracoes):
        med = s.simular(par, a.jogos, a.semente + it)
        ef = medias_efetivas(med)
        print(f"iteração {it}: média efetiva da memória por zona (perda): " + " ".join(f"{x[0]:.3f}" for x in ef))
        par.norma_memoria = [(n[0] * e[0], n[1] * e[1]) for n, e in zip(par.norma_memoria, ef)]
    json.dump({"norma": par.norma_memoria, "jogos_por_iteracao": a.jogos, "iteracoes": a.iteracoes}, open(ARQ, "w"))
    print("gravado", ARQ)
