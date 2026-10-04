"""Diagnóstico do Achado 49: a razão de chutes casa/fora que o simulador REALIZA quando se pede uma razão r (time médio contra time médio).
Uso: python scripts/diagnostico_elasticidade_mando.py"""
import sys, random, math, multiprocessing as mp, os
AQUI = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, AQUI); os.chdir(os.path.join(AQUI, '..'))
import simulador_cadeia_bola as s
P = None
def ini():
    global P; P = s.Parametros()
def run(args):
    razao, semente, n = args
    m = math.sqrt(razao); rng = random.Random(semente)
    casa, fora = s.Multiplicadores(ataque_chute=m), s.Multiplicadores(ataque_chute=1/m)
    c = e = f = ch = cf = gc = gf = 0
    for _ in range(n):
        r = s.simular_partida(P, rng, times=(casa, fora)); a, b = r.get('gols_0', 0), r.get('gols_1', 0)
        c += a > b; e += a == b; f += a < b; ch += r.get('chutes_0', 0); cf += r.get('chutes_1', 0); gc += a; gf += b
    return razao, (c, e, f, ch, cf, gc, gf, n)
if __name__ == '__main__':
    tarefas = [(r, 100 + i, 700) for r in (1.00, 1.13, 1.24) for i in range(4)]
    tot = {}
    with mp.Pool(4, initializer=ini) as p:
        for razao, v in p.imap_unordered(run, tarefas):
            t = tot.setdefault(razao, [0]*8); tot[razao] = [x+y for x, y in zip(t, v)]
    print('razão de chutes casa/fora imposta -> simulador (time médio contra time médio, N=%d jogos)' % tot[1.0][7])
    for r in sorted(tot):
        c, e, f, ch, cf, gc, gf, n = tot[r]
        print(f"  {r:.2f}: casa/empate/fora {c/n:.3f}/{e/n:.3f}/{f/n:.3f} | chutes casa/fora {ch/n:.2f}/{cf/n:.2f} (razão {ch/cf:.3f}) | gols casa/fora {gc/n:.2f}/{gf/n:.2f}")
