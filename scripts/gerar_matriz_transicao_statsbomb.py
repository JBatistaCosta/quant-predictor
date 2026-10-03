#!/usr/bin/env python3
"""Matriz de transição de bola entre zonas do campo a partir do StatsBomb Open Data (La Liga 2015/16, 380 partidas).

Refaz o Achado 15 (ACHADOS_COMPORTAMENTO.md) numa grade parametrizável. Grades disponíveis:
  - "3x3"  : 3 terços de comprimento x 3 corredores (a grade do Achado 15; serve de CHECAGEM do método);
  - "12"   : as 12 zonas de `zona_campo12` (migration 20261003140000): defesa | meio | ataque fora da área | grande área
             adversária, x 3 corredores. As coordenadas do StatsBomb (120 x 80 jardas) são convertidas para 105 x 68 m
             antes de aplicar a MESMA regra de zona do banco.

  - "18"   : refinamento EXATO da grade de 12 (cada zona de 18 cabe dentro de uma de 12): as faixas `meio` e `ataque_fora_da_area`
             são divididas ao meio (cortes em x=52,5 m e x=79,25 m), as outras duas ficam inteiras -> 6 faixas x 3 corredores. Foram
             essas duas porque, testando dividir cada faixa ao meio, são as que mais separam o comportamento da bola (ganho de
             log-verossimilhança de 19.409 e 8.476 nats, contra 4.678 da defesa e 644 da grande área). `comparar_12_vs_18` mede,
             por validação cruzada, se a divisão ajuda a prever a próxima ação fora da amostra.

Definições (por ação do time com a bola, a partir da zona onde a ação COMEÇA):
  - continua : Pass completo (sem `outcome`) ou Carry -> a bola vai para a zona de `end_location`;
  - chute    : Shot;
  - perda    : Pass incompleto/fora/impedimento, Dispossessed, Miscontrol.
    Esta é EXATAMENTE a definição do Achado 15: testadas todas as combinações de subtipos de perda, só esta reproduz as taxas
    publicadas (erro máximo 0,0004 = arredondamento). Drible incompleto (6.524), passe "Unknown" (2.465) e "Injury Clearance"
    (548) ficam FORA -- contá-los aumentaria a perda em 1-2 pontos percentuais em toda zona.
`taxa_desfecho[z]` = fração de chute/perda/continua entre as ações que começam em z;
`transicao[z][w]` = P(próxima zona = w | a ação continua).
Orientação (VERIFICADA com dados em 03/10/2026): o StatsBomb entrega cada time atacando rumo a x=120 e y=0 é o lado ESQUERDO de quem
ataca (lateral esquerdo: y médio 11,9; direito: 68,7; campo 0..80). O FotMob (mapa de calor, campo 105 x 68, também atacando rumo a
x=105) segue a MESMA convenção (lateral esquerdo: y médio 13,2; direito: 53,1; ponta esquerdo 23,9; direito 44,6; ~1.900 jogadores).
Logo, no banco `lado_y_baixo` (corredor 1) = ESQUERDA de quem ataca, `lado_y_alto` (corredor 3) = DIREITA, nas duas fontes.

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
NOMES_18 = [f"{f}/{c}" for f in ("defesa", "meio_baixo", "meio_alto", "ataque_fora_da_area_baixo", "ataque_fora_da_area_alto",
                                 "grande_area_adversaria") for c in ("lado_y_baixo", "centro", "lado_y_alto")]
CORTE_MEIO_M, CORTE_ATAQUE_M = 52.5, 79.25
# zona de 18 (0..17) -> zona de 12 (0..11) que a contém
PAI_12_DE_18 = [{0: 0, 1: 1, 2: 1, 3: 2, 4: 2, 5: 3}[z18 // 3] * 3 + z18 % 3 for z18 in range(18)]
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


def zona_18(x_m: float, y_m: float) -> int:
    """Mesma regra de `public.zona_campo18` (0..17; no banco é 1..18): a de 12 com `meio` e `ataque_fora_da_area` divididas ao meio."""
    z12 = zona_12(x_m, y_m)
    faixa, corredor = z12 // 3, z12 % 3
    if faixa == 1:
        faixa18 = 1 if x_m < CORTE_MEIO_M else 2
    elif faixa == 2:
        faixa18 = 3 if x_m < CORTE_ATAQUE_M else 4
    else:
        faixa18 = {0: 0, 3: 5}[faixa]
    return faixa18 * 3 + corredor


def zona_3x3(x_m: float, y_m: float) -> int:
    faixa = min(int(x_m / (CAMPO_X / 3)), 2)
    corredor = min(int(y_m / (CAMPO_Y / 3)), 2)
    return faixa * 3 + corredor


GRADES = {"12": (zona_12, NOMES_12), "18": (zona_18, NOMES_18), "3x3": (zona_3x3, NOMES_3X3)}


def acoes_da_partida(eventos: list[dict]) -> list[tuple]:
    """Eventos de uma partida -> [(tipo, x_ini, y_ini, x_fim, y_fim, origem)] com tipo em {'continua','chute','perda'}; coordenadas em jardas.
    `origem` diz de que evento a ação veio: 'cruzamento' (Pass com `pass.cross`), 'passe', 'conducao' (Carry), 'chute' ou 'falha'
    (Dispossessed/Miscontrol). Não altera a matriz (que só usa `tipo`); serve para separar o cruzamento do passe comum."""
    saida = []
    for e in eventos:
        tipo = (e.get("type") or {}).get("name")
        loc = e.get("location")
        if not loc or len(loc) < 2:
            continue
        if tipo == "Pass":
            p = e.get("pass") or {}
            origem = "cruzamento" if p.get("cross") else "passe"
            fim = p.get("end_location")
            resultado = (p.get("outcome") or {}).get("name")
            if resultado is None and fim:
                saida.append(("continua", loc[0], loc[1], fim[0], fim[1], origem))
            elif resultado in PERDAS_PASSE:
                saida.append(("perda", loc[0], loc[1], None, None, origem))
        elif tipo == "Carry":
            fim = (e.get("carry") or {}).get("end_location")
            if fim:
                saida.append(("continua", loc[0], loc[1], fim[0], fim[1], "conducao"))
        elif tipo == "Shot":
            saida.append(("chute", loc[0], loc[1], None, None, "chute"))
        elif tipo in EVENTOS_PERDA:
            saida.append(("perda", loc[0], loc[1], None, None, "falha"))
    return saida


def calcular(acoes: list[tuple], grade: str) -> dict:
    """Contagens e taxas por zona a partir das ações (coordenadas em jardas)."""
    zona, nomes = GRADES[grade]
    n = len(nomes)
    contagem = {k: [0] * n for k in ("chute", "perda", "continua")}
    trans = [[0] * n for _ in range(n)]
    for tipo, x0, y0, x1, y1, *_ in acoes:
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


def escrever_modulo_js(r: dict, caminho: str) -> None:
    """Grava o módulo JavaScript de uma grade ("12" -> zoneTransitionMatrix12.js, "18" -> zoneTransitionMatrix18.js).
    Arquivo GERADO: não editar à mão."""
    n = len(r["zonas"])
    nomes = r["zonas"]
    arquivo = os.path.basename(caminho)
    banco = "zona_campo12" if n == 12 else f"zona_campo{n}"
    linhas = [f"// src/utils/{arquivo}",
              "// ARQUIVO GERADO por scripts/gerar_matriz_transicao_statsbomb.py -- NÃO editar à mão (rode o script de novo).",
              "//",
              f"// Matriz de transição de bola entre as {n} zonas de `public.{banco}`, refeita do Achado 15 (StatsBomb Open Data,",
              "// La Liga 2015/16, 380 partidas). CONSTANTE UNIVERSAL EXTERNA, não calibrada por liga/confronto, sem IC 95% -- mesmas",
              "// ressalvas de zoneTransitionMatrix.js (3x3). Módulo à parte: não alimenta nenhuma simulação em produção.",
              f"// Índice do array = zona do banco - 1 (zona 1 = {nomes[0]} ... {n} = {nomes[-1]}).",
              "// Lateralidade (verificada com dados em 03/10/2026, StatsBomb e FotMob): corredor 1 = `lado_y_baixo` = ESQUERDA de quem ataca; corredor 3 = `lado_y_alto` = DIREITA.",
              f"// Base: {r['n_acoes']} ações (continua={sum(r['contagem']['continua'])}, chute={sum(r['contagem']['chute'])}, perda={sum(r['contagem']['perda'])}).",
              "",
              f"export const ZONAS_{n} = ["]
    for i, nome in enumerate(nomes):
        faixa, corredor = nome.split("/")
        linhas.append(f"  {{ zona: {i + 1}, faixa: '{faixa}', corredor: '{corredor}' }},")
    linhas += ["];", "", "// Fração de chute/perda/continua entre as ações que COMEÇAM em cada zona.", f"export const TAXA_DESFECHO_{n} = ["]
    for i, d in enumerate(r["taxa_desfecho"]):
        linhas.append(f"  {{ chute: {d['chute']:.4f}, perda: {d['perda']:.4f}, continua: {d['continua']:.4f} }}, // {nomes[i]}")
    linhas += ["];", "", "// P(próxima zona | a ação continua). Cada linha soma 1 (±0,0005 de arredondamento).", f"export const MATRIZ_TRANSICAO_{n} = ["]
    for i, linha in enumerate(r["transicao"]):
        linhas.append(f"  {_linha_js(linha)}, // de {nomes[i]}")
    linhas += ["];", "", "// Ações observadas que começam em cada zona: quanto menor, menos confiável a linha correspondente.",
               f"export const ACOES_POR_ZONA_{n} = {json.dumps(r['acoes_por_zona'])};", ""]
    with open(caminho, "w", encoding="utf-8") as f:
        f.write("\n".join(linhas))


def comparar_12_vs_18(acoes: list[tuple], blocos: int = 5, alfa: float = 0.5) -> dict:
    """Valida por BLOCOS (as ações vêm em ordem de partida, então blocos contíguos ~ grupos de partidas) se a grade de 18 prevê
    melhor a próxima ação do que a de 12, fora da amostra. Ambos os modelos são avaliados no MESMO alvo, o espaço fino (origem de
    18 zonas; desfecho = chute | perda | zona de destino de 18): o de 18 usa P(desfecho18 | origem18); o de 12 usa
    P(desfecho12 | origem12) x P(destino18 | destino12), com a divisão dentro do destino aprendida no treino. Suavização de Laplace `alfa`.
    Devolve a log-verossimilhança média por ação (nats) de cada modelo em cada bloco e a diferença 18 - 12."""
    import math

    def codifica(a):
        tipo, x0, y0, x1, y1, *_ = a
        o18 = zona_18(*para_metros(x0, y0))
        if tipo == "continua":
            d18 = zona_18(*para_metros(x1, y1))
            return o18, d18
        return o18, 18 if tipo == "chute" else 19

    dados = [codifica(a) for a in acoes]
    n = len(dados)
    pai = PAI_12_DE_18
    pai_sim = lambda s: pai[s] if s < 18 else 12 + (s - 18)           # símbolos de 12: 0..11 destinos, 12 chute, 13 perda
    por_bloco = []
    for b in range(blocos):
        ini, fim = b * n // blocos, (b + 1) * n // blocos
        treino = dados[:ini] + dados[fim:]
        teste = dados[ini:fim]
        c18 = [[alfa] * 20 for _ in range(18)]
        c12 = [[alfa] * 14 for _ in range(12)]
        chegadas = [alfa] * 18                                         # destinos de 18 observados (para dividir o destino de 12)
        for o, s in treino:
            c18[o][s] += 1
            c12[pai[o]][pai_sim(s)] += 1
            if s < 18:
                chegadas[s] += 1
        tot12_destino = [sum(chegadas[z] for z in range(18) if pai[z] == p) for p in range(12)]
        ll18 = ll12 = 0.0
        g18 = g12 = 0.0                                                # alvo GROSSO: desfecho/destino em 12 zonas
        filhos = [[z for z in range(18) if pai[z] == p] for p in range(12)]
        for o, s in teste:
            s12 = pai_sim(s)
            tot18 = sum(c18[o])
            massa = (sum(c18[o][z] for z in filhos[s12]) if s12 < 12 else c18[o][18 + (s12 - 12)]) / tot18
            g18 += math.log(massa)
            g12 += math.log(c12[pai[o]][s12] / sum(c12[pai[o]]))
            ll18 += math.log(c18[o][s] / sum(c18[o]))
            p12 = c12[pai[o]][pai_sim(s)] / sum(c12[pai[o]])
            if s < 18:
                p12 *= chegadas[s] / tot12_destino[pai[s]]
            ll12 += math.log(p12)
        por_bloco.append({"n_teste": len(teste), "ll18": ll18 / len(teste), "ll12": ll12 / len(teste),
                          "g18": g18 / len(teste), "g12": g12 / len(teste)})
    dif = [b["ll18"] - b["ll12"] for b in por_bloco]
    media = sum(dif) / blocos
    dp = (sum((d - media) ** 2 for d in dif) / (blocos - 1)) ** 0.5
    difg = [b["g18"] - b["g12"] for b in por_bloco]
    mg = sum(difg) / blocos
    dpg = (sum((d - mg) ** 2 for d in difg) / (blocos - 1)) ** 0.5
    return {"blocos": por_bloco, "ganho_medio_nats_por_acao": media, "erro_padrao": dp / blocos ** 0.5,
            "ganho_alvo_grosso_nats_por_acao": mg, "erro_padrao_alvo_grosso": dpg / blocos ** 0.5,
            "g18_medio": sum(b["g18"] for b in por_bloco) / blocos, "g12_medio": sum(b["g12"] for b in por_bloco) / blocos,
            "ll18_medio": sum(b["ll18"] for b in por_bloco) / blocos, "ll12_medio": sum(b["ll12"] for b in por_bloco) / blocos}


def analisar_cruzamento(acoes: list[tuple], grade: str = "18") -> dict:
    """Separa o cruzamento do passe comum (sexto elemento da ação, cache `acoes_v2.json`) e mede, por zona de origem: nº de ações,
    taxa de perda e distribuição do destino quando completa. Passe e cruzamento são comparados entre si (condução e falhas ficam fora)."""
    zona, nomes = GRADES[grade]
    n = len(nomes)
    z = {k: [{"continua": 0, "perda": 0, "destino": [0] * n} for _ in range(n)] for k in ("cruzamento", "passe")}
    for tipo, x0, y0, x1, y1, origem in acoes:
        if origem not in z:
            continue
        o = zona(*para_metros(x0, y0))
        if tipo == "continua":
            z[origem][o]["continua"] += 1
            z[origem][o]["destino"][zona(*para_metros(x1, y1))] += 1
        else:
            z[origem][o]["perda"] += 1
    return {"zonas": nomes, "por_zona": z}


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
    caminho = os.path.join(cache_dir, "acoes_v2.json") if cache_dir else None
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


def imprimir_cruzamento(acoes: list[tuple]) -> None:
    r = analisar_cruzamento(acoes)
    print("cruzamento x passe comum, por zona de origem (18 zonas): n, % perda, destino mais frequente (% das completas)")
    for i, nome in enumerate(r["zonas"]):
        linha = []
        for k in ("cruzamento", "passe"):
            d = r["por_zona"][k][i]
            tot = d["continua"] + d["perda"]
            if tot == 0:
                linha.append(f"{k}: -")
                continue
            top = max(range(len(d["destino"])), key=lambda w: d["destino"][w])
            pct = 100 * d["destino"][top] / d["continua"] if d["continua"] else 0
            linha.append(f"{k}: n={tot} perda={100 * d['perda'] / tot:.1f}% -> {r['zonas'][top]} {pct:.0f}%")
        print(f"  {nome:36s} " + " | ".join(linha))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache-dir", default=None)
    ap.add_argument("--saida", default=None, help="grava o JSON com as duas grades")
    ap.add_argument("--js", default=None, help="grava o módulo JavaScript da grade de 12 zonas (src/utils/zoneTransitionMatrix12.js)")
    ap.add_argument("--js18", default=None, help="grava o módulo JavaScript da grade de 18 zonas (src/utils/zoneTransitionMatrix18.js)")
    ap.add_argument("--cruzamento", action="store_true", help="separa o cruzamento do passe e mede a diferença (precisa do cache acoes_v2.json)")
    ap.add_argument("--comparar", action="store_true", help="compara 12 x 18 zonas por validação cruzada em blocos")
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
    if args.js18:
        escrever_modulo_js(res["18"], args.js18)
        print("módulo gravado em", args.js18)
    if args.cruzamento:
        imprimir_cruzamento(acoes)
    if args.comparar:
        c = comparar_12_vs_18(acoes)
        print(f"12 x 18 (log-verossimilhança média por ação, fora da amostra): 12={c['ll12_medio']:.4f}  18={c['ll18_medio']:.4f}  "
              f"ganho do 18 = {c['ganho_medio_nats_por_acao']:+.4f} ± {c['erro_padrao']:.4f} nats/ação")
        print(f"alvo GROSSO (desfecho/destino em 12 zonas; o teste conservador): 12={c['g12_medio']:.4f}  18={c['g18_medio']:.4f}  "
              f"ganho do 18 = {c['ganho_alvo_grosso_nats_por_acao']:+.4f} ± {c['erro_padrao_alvo_grosso']:.4f} nats/ação")
        for i, b in enumerate(c["blocos"], 1):
            print(f"  bloco {i}: n={b['n_teste']}  12={b['ll12']:.4f}  18={b['ll18']:.4f}  dif={b['ll18'] - b['ll12']:+.4f}")


if __name__ == "__main__":
    sys.exit(main())
