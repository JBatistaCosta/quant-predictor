#!/usr/bin/env python3
"""Origem dos escanteios no StatsBomb Open Data (La Liga 2015/16, 380 jogos) -- Achado 25 de ACHADOS_COMPORTAMENTO.md.

No StatsBomb o evento IMEDIATAMENTE anterior a um escanteio é do time que defende (bloqueio, corte, defesa do goleiro); a causa é o
ÚLTIMO LANCE do time que atacava (até 25 eventos antes). Este script atribui cada escanteio a esse lance (tipo e zona de 18) e calcula
escanteios por 100 ações do mesmo tipo e por zona. Precisa do cache `acoes_v2.json` para os denominadores:
    python scripts/gerar_matriz_transicao_statsbomb.py --cache-dir /tmp/sb
    python scripts/analisar_origem_escanteios_statsbomb.py /tmp/sb
"""
import sys, collections, json
sys.path.insert(0,'scripts')
import gerar_matriz_transicao_statsbomb as g
from concurrent.futures import ThreadPoolExecutor
S=(sys.argv[1] if len(sys.argv)>1 else '/tmp/sb')+'/'  # pasta com acoes_v2.json (gerada por gerar_matriz_transicao_statsbomb.py --cache-dir)
ms=sorted(p['match_id'] for p in g.baixar_json(f"{g.BASE}/matches/11/27.json"))
ACOES={'Shot','Pass','Carry','Dribble','Dispossessed','Miscontrol'}
def uma(mid):
    ev=g.baixar_json(f"{g.BASE}/events/{mid}.json"); out=[]
    for i,e in enumerate(ev):
        if e['type']['name']=='Pass' and ((e['pass'].get('type') or {}).get('name'))=='Corner':
            tid=e['team']['id']; k=i-1; n=0
            while k>=0 and n<25:
                p=ev[k]
                if p['team']['id']==tid and p['type']['name'] in ACOES and p.get('location'): break
                k-=1; n+=1
            else: p=None
            if k<0 or p is None or n>=25: out.append(('?',None)); continue
            t=p['type']['name']
            if t=='Shot': d='Chute'
            elif t=='Pass':
                pp=p['pass']; o=(pp.get('outcome') or {}).get('name','completo')
                d=('Cruzamento' if pp.get('cross') else 'Passe')+'/'+('completo' if o=='completo' else o)
            elif t=='Carry': d='Conducao'
            else: d=t
            out.append((d, g.zona_18(*g.para_metros(*p['location'][:2]))))
    return out
corn=[]
with ThreadPoolExecutor(6) as ex:
    for o in ex.map(uma, ms): corn+=o
tot=len(corn); print('escanteios',tot)
c=collections.Counter(d for d,z in corn)
acoes=[tuple(a) for a in json.load(open(S+'acoes_v2.json'))]
den=collections.Counter(); den_z=collections.Counter()
for tipo,x0,y0,x1,y1,orig in acoes:
    key={'chute':'Chute'}.get(orig)
    if orig=='chute': key='Chute'
    elif orig=='cruzamento': key='Cruzamento/'+('completo' if tipo=='continua' else 'perda')
    elif orig=='passe': key='Passe/'+('completo' if tipo=='continua' else 'perda')
    elif orig=='conducao': key='Conducao'
    else: key='falha'
    den[key]+=1
print('origem do escanteio (ultimo lance do time que atacou) e escanteios por 100 acoes desse tipo:')
for d,n in c.most_common(10):
    base=None
    if d=='Chute': base=den['Chute']
    elif d=='Conducao': base=den['Conducao']
    elif d.startswith('Cruzamento'): base=sum(v for k,v in den.items() if k.startswith('Cruzamento'))
    elif d.startswith('Passe'): base=sum(v for k,v in den.items() if k.startswith('Passe'))
    print(f'  {n/tot*100:5.1f}%  {d:28s} n={n:5d}', f'por 100 acoes (todas desse tipo): {100*n/base:.2f}' if base else '')
# por zona de origem: escanteios por 100 acoes que começam na zona
zn=collections.Counter(z for d,z in corn if z is not None)
dz=collections.Counter()
for tipo,x0,y0,x1,y1,orig in acoes: dz[g.zona_18(*g.para_metros(x0,y0))]+=1
print('escanteios gerados por 100 acoes, por zona onde o ULTIMO LANCE do atacante comecou (18 zonas):')
for z in range(18): print(f'  {g.NOMES_18[z]:42s} {100*zn[z]/dz[z]:5.2f}  (acoes {dz[z]}, escanteios {zn[z]})')
print('por jogo: acoes totais', len(acoes)/len(ms), 'escanteios', tot/len(ms))
