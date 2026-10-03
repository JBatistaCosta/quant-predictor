#!/usr/bin/env python3
"""Matriz de transição de bola entre zonas do campo a partir do StatsBomb Open Data (La Liga 2015/16, 380 partidas).

Refaz o Achado 15 (ACHADOS_COMPORTAMENTO.md) numa grade parametrizável. Grades disponíveis:
  - "3x3"  : 3 terços de comprimento x 3 corredores (a grade do Achado 15; serve de CHECAGEM do método);
  - "12"   : as 12 zonas de `zona_campo12` (migration 20261003140000): defesa | meio | ataque fora da área | grande área
             adversária, x 3 corredores. As coordenadas do StatsBomb (120 x 80 jardas) são convertidas para 105 x 68 m
             antes de aplicar a MESMA regra de zona do banco.

Definições (por ação do time com a bola, a partir da zona onde a ação COMEÇA):
  - continua : Pass completo (sem `outcome`) ou Carry -> a bola vai para a zona de `end_location`;
  - chute    : Shot;
  - perda    : Pass incompleto/fora/impedimento, Dispossessed, Miscontrol.
    Esta é EXATAMENTE a definição do Achado 15: testadas todas as combinações de subtipos de perda, só esta reproduz as taxas
    publicadas (erro máximo 0,0004 = arredondamento). Drible incompleto (6.524), passe "Unknown" (2.465) e "Injury Clearance"
    (548) ficam FORA -- contá-los aumentaria a perda em 1-2 pontos percentuais em toda zona.
`taxa_desfecho[z]` = fração de chute/perda/continua entre as ações que começam em z;
`transicao[z][w]` = P(próxima zona = w | a ação continua).
Orientação: o StatsBomb já entrega cada time atacando rumo a x=120; y=0 é o lado ESQUERDO de quem ataca. No banco o corredor 1
é `lado_y_baixo`, e a correspondência com esquerda/direita do FotMob NÃO foi verificada.

Uso:
    python scripts/gerar_matriz_transicao_statsbomb.py --cache-dir /tmp/sb --saida /tmp/matriz.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

BASE = "https://raw.githubusercontent.com/statsbomb/open-data/master/data"
COMPETICAO, TEMPORADA = 11, 27           # La Liga 2015/16
SB_X, SB_Y = 120.0, 80.0                 # jardas
CAMPO_X, CAMPO_Y = 105.0, 68.0           # metros (mesmo sistema do banco)

PERDAS_PASSE = {"Incomplete", "Out", "Pass Offside"}     # "Unknown" e "Injury Clearance" NÃO contam (ver docstring)
EVENTOS_PERDA = {"Dispossessed", "Miscontrol"}

NOMES_12 = [f"{f}/{c}" for f in ("defesa", "meio", "ataque_fora_da_area", "grande_area_adversaria")
            for c in ("lado_y_baixo", "centro", "lado_y_alto")]
NOMES_3X3 = [f"{f}/{c}" for f in ("def", "meio", "atq") for c in ("esq", "cen", "dir")]


def para_metros(x: float, y: float) -> tuple[float, float]:
    return x * CAMPO_X / SB_X, y * CAMPO_Y / SB_Y


def zona_12(x_m: float, y_m: float) -> int:
    """Mesma regra de `public.zona_campo12`, devolvendo 0..11 (no banco é 1..12)."""
    if x_m < 35:
        faixa = 0
    elif x_m < 70:
        faixa = 1
    elif x_m >= 88.5 and 13.85 <= y_m <= 54.15:
        faixa = 3
    else:
        faixa = 2
    corredor = 0 if y_m < 68.0 / 3 else (1 if y_m <= 2 * 68.0 / 3 else 2)
    return faixa * 3 + corredor


def zona_3x3(x_m: float, y_m: float) -> int:
    faixa = min(int(x_m / (CAMPO_X / 3)), 2)
    corredor = min(int(y_m / (CAMPO_Y / 3)), 2)
    return faixa * 3 + corredor


GRADES = {"12": (zona_12, NOMES_12), "3x3": (zona_3x3, NOMES_3X3)}


def acoes_da_partida(eventos: list[dict]) -> list[tuple]:
    """Eventos de uma partida -> [(tipo, x_ini, y_ini, x_fim, y_fim)] com tipo em {'continua','chute','perda'}; coordenadas em jardas."""
    saida = []
    for e in eventos:
        tipo = (e.get("type") or {}).get("name")
        loc = e.get("location")
        if not loc or len(loc) < 2:
            continue
        if tipo == "Pass":
            p = e.get("pass") or {}
            fim = p.get("end_location")
            resultado = (p.get("outcome") or {}).get("name")
            if resultado is None and fim:
                saida.append(("continua", loc[0], loc[1], fim[0], fim[1]))
            elif resultado in PERDAS_PASSE:
                saida.append(("perda", loc[0], loc[1], None, None))
        elif tipo == "Carry":
            fim = (e.get("carry") or {}).get("end_location")
            if fim:
                saida.append(("continua", loc[0], loc[1], fim[0], fim[1]))
        elif tipo == "Shot":
            saida.append(("chute", loc[0], loc[1], None, None))
        elif tipo in EVENTOS_PERDA:
            saida.append(("perda", loc[0], loc[1], None, None))
    return saida


def calcular(acoes: list[tuple], grade: str) -> dict:
    """Contagens e taxas por zona a partir das ações (coordenadas em jardas)."""
    zona, nomes = GRADES[grade]
    n = len(nomes)
    contagem = {k: [0] * n for k in ("chute", "perda", "continua")}
    trans = [[0] * n for _ in range(n)]
    for tipo, x0, y0, x1, y1 in acoes:
        z0 = zona(*para_metros(x0, y0))
        contagem[tipo][z0] += 1
        if tipo == "continua":
            trans[z0][zona(*para_metros(x1, y1))] += 1
    total = [contagem["chute"][z] + contagem["perda"][z] + contagem["continua"][z] for z in range(n)]
    desfecho = [{k: (contagem[k][z] / total[z] if total[z] else 0.0) for k in contagem} for z in range(n)]
    matriz = [[(trans[z][w] / contagem["continua"][z] if contagem["continua"][z] else 0.0) for w in range(n)] for z in range(n)]
    return {"grade": grade, "zonas": nomes, "n_acoes": sum(total), "acoes_por_zona": total, "contagem": contagem,
            "taxa_desfecho": desfecho, "transicao": matriz, "transicao_contagem": trans}


def _linha_js(valores: list[float], casas: int = 4) -> str:
    return "[" + ", ".join(f"{v:.{casas}f}" for v in valores) + "]"


def escrever_modulo_js(r12: dict, caminho: str) -> None:
    """Grava `src/utils/zoneTransitionMatrix12.js` a partir do resultado da grade "12". Arquivo GERADO: não editar à mão."""
    nomes = r12["zonas"]
    linhas = ["// src/utils/zoneTransitionMatrix12.js",
              "// ARQUIVO GERADO por scripts/gerar_matriz_transicao_statsbomb.py -- NÃO editar à mão (rode o script de novo).",
              "//",
              "// Matriz de transição de bola entre as 12 zonas de `public.zona_campo12` (4 faixas de profundidade x 3 corredores),",
              "// refeita do Achado 15 (StatsBomb Open Data, La Liga 2015/16, 380 partidas). CONSTANTE UNIVERSAL EXTERNA, não calibrada",
              "// por liga/confronto, sem IC 95% -- mesmas ressalvas de zoneTransitionMatrix.js (3x3). Módulo à parte: não alimenta",
              "// nenhuma simulação em produção. Índice do array = zona do banco - 1 (zona 1 = defesa/lado_y_baixo ... 12 = grande área/lado_y_alto).",
              "// O corredor 1 do banco é `lado_y_baixo` (no StatsBomb = lado esquerdo de quem ataca); a correspondência com o FotMob NÃO foi verificada.",
              f"// Base: {r12['n_acoes']} ações (continua={sum(r12['contagem']['continua'])}, chute={sum(r12['contagem']['chute'])}, perda={sum(r12['contagem']['perda'])}).",
              "",
              "export const ZONAS_12 = ["]
    for i, nome in enumerate(nomes):
        faixa, corredor = nome.split("/")
        linhas.append(f"  {{ zona: {i + 1}, faixa: '{faixa}', corredor: '{corredor}' }},")
    linhas += ["];", "", "// Fração de chute/perda/continua entre as ações que COMEÇAM em cada zona.", "export const TAXA_DESFECHO_12 = ["]
    for i, d in enumerate(r12["taxa_desfecho"]):
        linhas.append(f"  {{ chute: {d['chute']:.4f}, perda: {d['perda']:.4f}, continua: {d['continua']:.4f} }}, // {nomes[i]}")
    linhas += ["];", "", "// P(próxima zona | a ação continua). Cada linha soma 1 (±0,0005 de arredondamento).", "export const MATRIZ_TRANSICAO_12 = ["]
    for i, linha in enumerate(r12["transicao"]):
        linhas.append(f"  {_linha_js(linha)}, // de {nomes[i]}")
    linhas += ["];", "", "// Ações observadas que começam em cada zona: quanto menor, menos confiável a linha correspondente.",
               f"export const ACOES_POR_ZONA_12 = {json.dumps(r12['acoes_por_zona'])};", ""]
    with open(caminho, "w", encoding="utf-8") as f:
        f.write("\n".join(linhas))


def baixar_json(url: str, tentativas: int = 5):
    import requests
    for t in range(tentativas):
        try:
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            return r.json()
        except Exception:
            if t == tentativas - 1:
                raise
            time.sleep(2 * (t + 1))


def acoes_da_temporada(cache_dir: str | None, workers: int = 6) -> list[tuple]:
    caminho = os.path.join(cache_dir, "acoes.json") if cache_dir else None
    if caminho and os.path.exists(caminho):
        return [tuple(a) for a in json.load(open(caminho))]
    partidas = baixar_json(f"{BASE}/matches/{COMPETICAO}/{TEMPORADA}.json")
    ids = sorted(p["match_id"] for p in partidas)
    print(f"{len(ids)} partidas")

    def uma(mid):
        return acoes_da_partida(baixar_json(f"{BASE}/events/{mid}.json"))

    acoes: list[tuple] = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, parte in enumerate(ex.map(uma, ids), 1):
            acoes.extend(parte)
            if i % 40 == 0:
                print(f"  {i}/{len(ids)} partidas, {len(acoes)} ações")
    if caminho:
        os.makedirs(cache_dir, exist_ok=True)
        json.dump(acoes, open(caminho, "w"))
    return acoes


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache-dir", default=None)
    ap.add_argument("--saida", default=None, help="grava o JSON com as duas grades")
    ap.add_argument("--js", default=None, help="grava o módulo JavaScript da grade de 12 zonas (src/utils/zoneTransitionMatrix12.js)")
    args = ap.parse_args()
    acoes = acoes_da_temporada(args.cache_dir)
    res = {g: calcular(acoes, g) for g in GRADES}
    for g, r in res.items():
        print(f"grade {g}: {r['n_acoes']} ações (continua={sum(r['contagem']['continua'])}, chute={sum(r['contagem']['chute'])}, perda={sum(r['contagem']['perda'])})")
    if args.saida:
        json.dump(res, open(args.saida, "w"), ensure_ascii=False)
        print("gravado em", args.saida)
    if args.js:
        escrever_modulo_js(res["12"], args.js)
        print("módulo gravado em", args.js)


if __name__ == "__main__":
    sys.exit(main())
