"""Elasticidade dos multiplicadores de chute do simulador (Achado 51).

Pergunta: se o multiplicador de chute de um time é m, quanto da razão de chutes o simulador REALIZA? Mede, em log, e = ln(razão realizada) / ln(razão pedida)
com times médios, para (a) multiplicador de ATAQUE (o que o mando e o ataque da camada de força usam) e (b) multiplicador de DEFESA (a defesa da camada de força).
Faz parte da calibração do próprio simulador: não usa nenhum dado de jogo real. O expoente que compensa é 1/e.
Uso: python scripts/calibrar_elasticidade_chute.py [--jogos 2800]
"""
import argparse
import math
import multiprocessing as mp
import os
import random
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
os.chdir(os.path.join(AQUI, ".."))
import simulador_cadeia_bola as s  # noqa: E402

P = None


def ini():
    global P
    P = s.Parametros()


def rodar(args):
    tipo, m, semente, n = args
    rng = random.Random(semente)
    if tipo == "ataque":          # time 0 ataca com m, time 1 com 1/m (como o mando): razão pedida = m^2
        t0, t1 = s.Multiplicadores(ataque_chute=m), s.Multiplicadores(ataque_chute=1 / m)
    else:                         # defesa: o time 1 tem defesa m (> 1 deixa o time 0 chutar mais) e o 0 tem 1/m: razão pedida = m^2 a favor do time 0
        t0, t1 = s.Multiplicadores(defesa_chute=1 / m), s.Multiplicadores(defesa_chute=m)
    ch0 = ch1 = 0
    for _ in range(n):
        r = s.simular_partida(P, rng, times=(t0, t1))
        ch0 += r.get("chutes_0", 0)
        ch1 += r.get("chutes_1", 0)
    return (tipo, m), (ch0, ch1, n)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--jogos", type=int, default=2800)
    a = ap.parse_args()
    ms = (1.06, 1.12, 1.25)
    por = a.jogos // 4
    tarefas = [(t, m, 1000 + i, por) for t in ("ataque", "defesa") for m in ms for i in range(4)]
    tot = {}
    with mp.Pool(4, initializer=ini) as pool:
        for k, v in pool.imap_unordered(rodar, tarefas):
            t = tot.setdefault(k, [0, 0, 0])
            tot[k] = [x + y for x, y in zip(t, v)]
    print(f"{'tipo':8s} {'m':>5s} {'razão pedida':>13s} {'realizada':>10s} {'elasticidade e':>15s} {'expoente 1/e':>13s}")
    for k in sorted(tot):
        ch0, ch1, n = tot[k]
        pedida, real = k[1] ** 2, ch0 / ch1
        e = math.log(real) / math.log(pedida)
        print(f"{k[0]:8s} {k[1]:5.2f} {pedida:13.3f} {real:10.3f} {e:15.3f} {1 / e:13.2f}")
