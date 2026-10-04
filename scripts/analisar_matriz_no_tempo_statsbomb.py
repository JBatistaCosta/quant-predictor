#!/usr/bin/env python3
"""A matriz de transição entre zonas muda ao longo do jogo? (StatsBomb, 4 ligas de 2015/16, ~2,7 milhões de ações) -- Achado 28.

Lê os dados reduzidos versionados (`dados_referencia/statsbomb/brutos`, sem rede). Cada ação (definição do Achado 15, 18 zonas de
`gerar_matriz_transicao_statsbomb.py`) recebe a janela de 15 minutos em que COMEÇA (1T: 0-15, 15-30, 30-45; 2T: 45-60, 60-75, 75-90; acréscimos e
prorrogação ficam fora). Mede (1) taxas de chute, perda e avanço por janela e por faixa do campo; (2) quanto uma matriz POR JANELA prevê melhor a
próxima ação do que a matriz única, fora da amostra (5 blocos contíguos de partidas por competição, treino nos outros 4).

Uso:  python scripts/analisar_matriz_no_tempo_statsbomb.py [pasta_brutos] [grupo]      # padrão: dados_referencia/statsbomb/brutos ligas_2015_16
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import comparar_competicoes_statsbomb as c  # noqa: E402
import gerar_matriz_transicao_statsbomb as g  # noqa: E402

JANELAS = ["0-15", "15-30", "30-45", "45-60", "60-75", "75-90"]
FAIXAS = ["defesa", "meio", "ataque fora da área", "grande área"]
BLOCOS = 5


def janela(periodo, minuto):
    if periodo == 1 and minuto < 45:
        return minuto // 15
    if periodo == 2 and 45 <= minuto < 90:
        return 3 + (minuto - 45) // 15
    return None


def faixa_da_zona(z: int) -> int:
    return 0 if z < 3 else (1 if z < 9 else (2 if z < 15 else 3))


def acoes_com_janela(eventos: list[dict]):
    """[(janela, zona de origem, símbolo de desfecho, avanço em x (m) se continua, senão None)] das ações da partida."""
    for e in eventos:
        w = janela(e.get("period"), e.get("minute", 0))
        if w is None:
            continue
        for tipo, x0, y0, x1, y1, *_ in g.acoes_da_partida([e]):
            o = g.zona_18(*g.para_metros(x0, y0))
            if tipo == "continua":
                xm0, _ = g.para_metros(x0, y0)
                xm1, ym1 = g.para_metros(x1, y1)
                yield w, o, g.zona_18(xm1, ym1), xm1 - xm0
            else:
                yield w, o, 18 if tipo == "chute" else 19, None


def contar(pasta: str, grupo: str):
    """cont[bloco][janela][zona][símbolo] e (soma do avanço, n de continuações) por janela x faixa."""
    cont = [[[[0] * 20 for _ in range(18)] for _ in range(6)] for _ in range(BLOCOS * 8)]
    avanco = [[[0.0, 0] for _ in FAIXAS] for _ in JANELAS]
    bloco_global = 0
    for rotulo, regs in c.carregar_fracionado(pasta, grupo).items():
        n = len(regs)
        for i, r in enumerate(regs):
            b = bloco_global + i * BLOCOS // n
            for w, o, s, dx in acoes_com_janela(c.eventos_reconstruidos(r)):
                cont[b][w][o][s] += 1
                if dx is not None:
                    a = avanco[w][faixa_da_zona(o)]
                    a[0] += dx
                    a[1] += 1
        bloco_global += BLOCOS
        del regs
    return cont, avanco


def relatorio(cont, avanco) -> None:
    tot = [[[sum(cont[b][w][o][s] for b in range(len(cont))) for s in range(20)] for o in range(18)] for w in range(6)]
    print("POR JANELA (todas as zonas): ações, chute %, perda %, avanço médio de quem continua (m)")
    for w, nome in enumerate(JANELAS):
        n = sum(sum(l) for l in tot[w])
        ch = sum(l[18] for l in tot[w])
        pe = sum(l[19] for l in tot[w])
        s_av = sum(a[0] for a in avanco[w])
        n_av = sum(a[1] for a in avanco[w])
        print(f"  {nome:6s} ações={n:8d}  chute={100 * ch / n:5.3f}%  perda={100 * pe / n:5.2f}%  avanço={s_av / n_av:5.2f} m")
    print("\nONDE ESTÃO AS AÇÕES (% das ações de cada janela, por faixa do campo de quem tem a bola):")
    for f, fn in enumerate(FAIXAS):
        linhas = []
        for w in range(6):
            n = sum(sum(tot[w][o]) for o in range(18))
            nf = sum(sum(tot[w][o]) for o in range(18) if faixa_da_zona(o) == f)
            linhas.append(f"{100 * nf / n:6.2f}")
        print(f"  {fn:20s} " + "  ".join(linhas))
    print("\nPOR FAIXA x JANELA: chute % | perda % | avanço (m)")
    for f, fn in enumerate(FAIXAS):
        linhas = []
        for w in range(6):
            zonas = [o for o in range(18) if faixa_da_zona(o) == f]
            n = sum(sum(tot[w][o]) for o in zonas)
            ch = sum(tot[w][o][18] for o in zonas)
            pe = sum(tot[w][o][19] for o in zonas)
            av = avanco[w][f]
            linhas.append(f"{100 * ch / n:5.2f}|{100 * pe / n:5.2f}|{av[0] / av[1]:4.1f}")
        print(f"  {fn:20s} " + "  ".join(linhas))
    print(f"  {'':20s} " + "  ".join(f"{j:>14s}" for j in JANELAS))
    print("\nDECOMPOSIÇÃO do chute % por ação: (a) ocupação da janela com as taxas por zona de TODO o jogo; (b) ocupação de TODO o jogo com as taxas por zona da janela")
    ocup_total = [sum(sum(tot[w][o]) for w in range(6)) for o in range(18)]
    taxa_total = [sum(tot[w][o][18] for w in range(6)) / ocup_total[o] for o in range(18)]
    for w, nome in enumerate(JANELAS):
        ocup_w = [sum(tot[w][o]) for o in range(18)]
        a = sum(ocup_w[o] * taxa_total[o] for o in range(18)) / sum(ocup_w)
        taxa_w = [tot[w][o][18] / ocup_w[o] if ocup_w[o] else 0.0 for o in range(18)]
        b = sum(ocup_total[o] * taxa_w[o] for o in range(18)) / sum(ocup_total)
        print(f"  {nome:6s} (a) só ocupação: {100 * a:5.3f}%   (b) só taxas por zona: {100 * b:5.3f}%")
    # fora da amostra: matriz por janela x matriz única, treino nos outros blocos
    alfa = 0.5
    ganhos = []
    for t in range(len(cont)):
        n_teste = sum(cont[t][w][o][s] for w in range(6) for o in range(18) for s in range(20))
        if n_teste == 0:
            continue
        unica = [[alfa] * 20 for _ in range(18)]
        por_w = [[[alfa] * 20 for _ in range(18)] for _ in range(6)]
        for b in range(len(cont)):
            if b == t:
                continue
            for w in range(6):
                for o in range(18):
                    for s in range(20):
                        v = cont[b][w][o][s]
                        unica[o][s] += v
                        por_w[w][o][s] += v
        ll_u = ll_w = 0.0
        for w in range(6):
            for o in range(18):
                su, sw = sum(unica[o]), sum(por_w[w][o])
                for s in range(20):
                    v = cont[t][w][o][s]
                    if v:
                        ll_u += v * math.log(unica[o][s] / su)
                        ll_w += v * math.log(por_w[w][o][s] / sw)
        ganhos.append((ll_w - ll_u) / n_teste)
    m = sum(ganhos) / len(ganhos)
    dp = (sum((x - m) ** 2 for x in ganhos) / (len(ganhos) - 1)) ** 0.5
    print(f"\nMATRIZ POR JANELA contra MATRIZ ÚNICA (fora da amostra, {len(ganhos)} blocos): ganho = {m:+.5f} ± {dp / len(ganhos) ** 0.5:.5f} nats por ação")


if __name__ == "__main__":
    pasta = sys.argv[1] if len(sys.argv) > 1 else "dados_referencia/statsbomb/brutos"
    grupo = sys.argv[2] if len(sys.argv) > 2 else "ligas_2015_16"
    relatorio(*contar(pasta, grupo))
