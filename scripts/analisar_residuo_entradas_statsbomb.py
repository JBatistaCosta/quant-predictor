#!/usr/bin/env python3
"""De que lance vem cada linha de ação? (Achado 31, v3).

O Achado 31 mostrou que a cadeia só com 'ação que continua' + 'recuperação em jogo' sub-ocupa o terço final: sobravam ~61 linhas de ação por jogo que não vêm
de nenhuma das duas. Aqui classificamos TODA linha da matriz (a mesma lista de `acoes_da_partida`) pela forma como a bola chegou àquela zona:
  continua      -- a linha anterior é da mesma equipe e terminou em 'continua' (a bola seguiu com ela);
  recuperação   -- a linha anterior é do adversário (perda) ou a bola mudou de dono em jogo, e o próprio lance é de jogo corrido;
  lateral / tiro livre / tiro de meta / escanteio / saída de bola / pênalti -- o próprio lance é essa bola parada (o tipo vem de `pass.type` / `shot.type`).
Conta por zona (18, referencial da equipe que age) e por jogo. Uso: python scripts/analisar_residuo_entradas_statsbomb.py [--grupo ligas_2015_16] [--saida arq.json]
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import comparar_competicoes_statsbomb as c  # noqa: E402
import gerar_matriz_transicao_statsbomb as g  # noqa: E402

CLASSES = ["continua", "recuperação", "lateral", "tiro livre", "tiro de meta", "escanteio", "saída de bola", "pênalti"]
TIPO_PASSE = {"Throw-in": "lateral", "Free Kick": "tiro livre", "Goal Kick": "tiro de meta", "Corner": "escanteio", "Kick Off": "saída de bola"}
TIPO_CHUTE = {"Free Kick": "tiro livre", "Penalty": "pênalti", "Corner": "escanteio", "Kick Off": "saída de bola"}


def classe_do_lance(e: dict) -> str | None:
    """Classe de bola parada do próprio evento, ou None se for jogo corrido."""
    t = (e.get("type") or {}).get("name")
    if t == "Pass":
        return TIPO_PASSE.get(((e.get("pass") or {}).get("type") or {}).get("name"))
    if t == "Shot":
        return TIPO_CHUTE.get(((e.get("shot") or {}).get("type") or {}).get("name"))
    return None


def linhas_da_partida(ev: list[dict]) -> list[tuple]:
    """[(classe de entrada, zona 0..17, equipe, tipo da linha)] para cada linha que `acoes_da_partida` geraria, na mesma ordem."""
    saida = []
    anterior = None                              # (equipe, tipo da linha)
    for e in ev:
        linha = g.acoes_da_partida([e])
        if not linha:
            continue
        tipo, x, y = linha[0][0], linha[0][1], linha[0][2]
        equipe = (e.get("team") or {}).get("id")
        zona = g.zona_18(*g.para_metros(x, y))
        cl = classe_do_lance(e)
        if cl is None:
            cl = "continua" if anterior is not None and anterior[0] == equipe and anterior[1] == "continua" else "recuperação"
        saida.append((cl, zona, equipe, tipo))
        anterior = (equipe, tipo)
    return saida


def nucleo_pos_perda_e_chute(linhas: list[tuple]) -> dict:
    """Para cada linha (continua, perda ou chute): quem fica com a próxima linha ('mesma' ou 'adv'), de que classe é e em que zona (referencial de quem age) começa.
    Chave 'continua|<zona>', 'perda|<zona>' ou 'chute|<zona>' (zona de ONDE A LINHA COMEÇA) -> {'<mesma|adv>|<classe>|<zona destino>': n}. É o núcleo empírico que substitui recuperação, reinício,
    escanteio e lateral mantido calibrados à mão no simulador (v3)."""
    saida: dict = collections.defaultdict(collections.Counter)
    for (cl, z, eq, tipo), (cl2, z2, eq2, _) in zip(linhas, linhas[1:]):
        saida[f"{tipo}|{z}"][f"{'mesma' if eq2 == eq else 'adv'}|{cl2}|{z2}"] += 1
    return saida


def processar(pasta: str, grupo: str) -> dict:
    cont = {k: [0] * 18 for k in CLASSES}
    nucleo: dict = collections.defaultdict(collections.Counter)
    jogos = 0
    for rotulo in sorted(json.load(open(os.path.join(pasta, grupo, d, "INDICE.json")))["rotulo"] for d in os.listdir(os.path.join(pasta, grupo))):
        for reg in c.carregar_completo(pasta, grupo, rotulo):
            jogos += 1
            linhas = linhas_da_partida(reg["eventos"])
            for cl, z, _, _ in linhas:
                cont[cl][z] += 1
            for k, cnt in nucleo_pos_perda_e_chute(linhas).items():
                nucleo[k].update(cnt)
    return {"jogos": jogos, "linhas_por_classe_e_zona": cont, "nucleo_pos_perda_e_chute": {k: dict(v) for k, v in nucleo.items()}}


def relatorio(res: dict) -> None:
    j = res["jogos"]
    cont = res["linhas_por_classe_e_zona"]
    print(f"{j} jogos\nLINHAS DE AÇÃO POR CLASSE DE ENTRADA (por jogo) e % por faixa onde a linha começa:")
    print(f"{'classe':14s} {'por jogo':>8s}   % por faixa (defesa, meio baixo, meio alto, ataque baixo, ataque alto, grande área)")
    for k in CLASSES:
        v = cont[k]
        tot = sum(v)
        faixas = [sum(v[b * 3:b * 3 + 3]) for b in range(6)]
        print(f"{k:14s} {tot / j:8.1f}   " + " ".join(f"{100 * x / tot:5.1f}" if tot else "  -  " for x in faixas))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pasta", default="dados_referencia/statsbomb/completo")
    ap.add_argument("--grupo", default="ligas_2015_16")
    ap.add_argument("--saida")
    a = ap.parse_args()
    r = processar(a.pasta, a.grupo)
    relatorio(r)
    if a.saida:
        json.dump(r, open(a.saida, "w"), ensure_ascii=False)
