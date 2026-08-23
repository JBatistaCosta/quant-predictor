#!/usr/bin/env python3
"""Gera o "golden fixture" de paridade entre `distribuicoes.py` e o JS.

A camada que transforma parâmetros em mercados existe duas vezes: em Python
(`scripts/distribuicoes.py`, usada pelo treino em lote) e em JavaScript
(`src/utils/distribuicoesMercados.js`, usada pela calculadora do app). As
duas TÊM que concordar -- senão a mesma partida mostra probabilidade
diferente dependendo de onde o usuário olha, e o pior é que ninguém
perceberia por meses.

O projeto já convive com matemática duplicada entre front e serverless
(`src/utils/distributions.js` <-> `api/_lib/negbin.js`), protegida só por um
comentário de "se alterar uma, altere a outra". Entre linguagens diferentes
nem o copiar-colar protege, então aqui a proteção é executável: este script
grava entradas e saídas esperadas, e `verificar_paridade_js.mjs` confere o
lado JS contra elas.

Uso:
    python gerar_golden_fixture.py     # regrava src/utils/__fixtures__/mercados.golden.json
"""

from __future__ import annotations

import json
import os

import distribuicoes as dist

DESTINO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "utils", "__fixtures__", "mercados.golden.json")

# Casos escolhidos pra cobrir a faixa realista e as bordas:
#   - λ típicos de futebol (mandante ~1,5 / visitante ~1,2);
#   - ρ = 0 (caso-limite: Poisson dupla independente);
#   - ρ positivo e negativo;
#   - λ muito baixo e muito alto (cauda);
#   - dispersão de escanteios alta (quase Poisson) e baixa (cauda gorda);
#   - split assimétrico e simétrico.
CASOS_GOLS = [
    {"nome": "tipico", "lambda_home": 1.5459, "lambda_away": 1.3113, "rho": -0.0113},
    {"nome": "rho_zero", "lambda_home": 1.5, "lambda_away": 1.2, "rho": 0.0},
    {"nome": "rho_negativo_forte", "lambda_home": 1.6, "lambda_away": 1.1, "rho": -0.15},
    {"nome": "rho_positivo", "lambda_home": 1.4, "lambda_away": 1.4, "rho": 0.08},
    {"nome": "lambda_baixo", "lambda_home": 0.45, "lambda_away": 0.30, "rho": -0.05},
    {"nome": "lambda_alto", "lambda_home": 3.4, "lambda_away": 2.7, "rho": -0.02},
    {"nome": "desequilibrado", "lambda_home": 2.9, "lambda_away": 0.55, "rho": -0.04},
]

CASOS_ESCANTEIOS = [
    {"nome": "tipico_pl", "media_total": 10.3365, "disp_r": 64.3, "alpha": 4.51, "beta": 3.83},
    {"nome": "quase_poisson", "media_total": 9.5, "disp_r": 5000.0, "alpha": 5.0, "beta": 5.0},
    {"nome": "cauda_gorda", "media_total": 11.0, "disp_r": 12.0, "alpha": 6.0, "beta": 4.0},
    {"nome": "split_simetrico", "media_total": 10.0, "disp_r": 65.0, "alpha": 5.0, "beta": 5.0},
    {"nome": "split_assimetrico", "media_total": 12.5, "disp_r": 40.0, "alpha": 9.0, "beta": 3.0},
]


def serializar(mercados: dict[tuple[str, str], float]) -> dict[str, dict[str, float]]:
    """`{(mercado, selecao): p}` -> `{mercado: {selecao: p}}`, que é o formato
    que o lado JS produz naturalmente."""
    saida: dict[str, dict[str, float]] = {}
    for (mercado, selecao), prob in mercados.items():
        saida.setdefault(mercado, {})[selecao] = prob
    return saida


def main() -> None:
    fixture = {
        "_comentario": (
            "Gerado por scripts/gerar_golden_fixture.py. NÃO editar à mão. "
            "Confere a paridade entre scripts/distribuicoes.py e "
            "src/utils/distribuicoesMercados.js -- rode "
            "`node scripts/verificar_paridade_js.mjs` depois de mexer em qualquer um dos dois."
        ),
        "gols": [],
        "escanteios": [],
    }

    for caso in CASOS_GOLS:
        matriz = dist.matriz_placares(caso["lambda_home"], caso["lambda_away"], caso["rho"])
        fixture["gols"].append({
            "entrada": caso,
            "esperado": serializar(dist.mercados_de_gols(matriz)),
        })

    for caso in CASOS_ESCANTEIOS:
        fixture["escanteios"].append({
            "entrada": caso,
            "esperado": serializar(dist.mercados_de_escanteios(
                caso["media_total"], caso["disp_r"], caso["alpha"], caso["beta"],
            )),
        })

    os.makedirs(os.path.dirname(DESTINO), exist_ok=True)
    with open(DESTINO, "w", encoding="utf-8") as arquivo:
        json.dump(fixture, arquivo, indent=2, ensure_ascii=False)
        arquivo.write("\n")

    n_gols = sum(len(sel) for caso in fixture["gols"] for sel in caso["esperado"].values())
    n_corn = sum(len(sel) for caso in fixture["escanteios"] for sel in caso["esperado"].values())
    print(f"Fixture gravado em {os.path.relpath(DESTINO)}")
    print(f"  {len(CASOS_GOLS)} casos de gols ({n_gols} probabilidades)")
    print(f"  {len(CASOS_ESCANTEIOS)} casos de escanteios ({n_corn} probabilidades)")


if __name__ == "__main__":
    main()
