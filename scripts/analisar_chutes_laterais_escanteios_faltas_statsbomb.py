#!/usr/bin/env python3
"""Resultado do chute por zona, destino do lateral e do escanteio cobrado, falta e bola fora por zona (StatsBomb, 4 ligas de 2015/16) -- Achado 30.

Completa as peças da cadeia semi-Markov depois do Achado 29 (tempo morto e recuperação): o que acontece DEPOIS de um chute, para onde vão os reinícios e onde
as faltas e as bolas fora acontecem. Lê os eventos COMPLETOS versionados (`dados_referencia/statsbomb/completo`, sem rede) e usa as mesmas 18 zonas.

Uso:  python scripts/analisar_chutes_laterais_escanteios_faltas_statsbomb.py [pasta_completo] [grupo] [--resumo saida.json]
"""

from __future__ import annotations

import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import comparar_competicoes_statsbomb as c  # noqa: E402
import gerar_matriz_transicao_statsbomb as g  # noqa: E402

FAIXAS_NOME = ["defesa", "meio baixo", "meio alto", "ataque fora baixo", "ataque fora alto", "grande área"]
RESULTADOS = ["Goal", "Saved", "Blocked", "Off T", "Wayward", "Post"]
PROXIMA_POSSE = ["Regular Play", "From Goal Kick", "From Corner", "From Keeper", "From Kick Off", "From Throw In", "From Free Kick", "From Counter"]


def zona_de(loc) -> int | None:
    return g.zona_18(*g.para_metros(*loc[:2])) if loc and len(loc) >= 2 else None


def zona_espelhada(loc) -> int | None:
    """Zona (referencial de quem tem a bola) de um evento do ADVERSÁRIO: espelha o ponto (120 - x, 80 - y)."""
    return g.zona_18(*g.para_metros(120 - loc[0], 80 - loc[1])) if loc and len(loc) >= 2 else None


def seg(e: dict) -> float:
    h, m, s = e["timestamp"].split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def analisar_partida(ev: list[dict]) -> dict:
    """Contagens da partida: chutes (zona, resultado, xG, cabeça, próxima posse), laterais (origem, destino, completo), escanteios, faltas e passes fora."""
    r = {"chutes": [], "laterais": [], "escanteios": [], "faltas": [], "fora": []}
    n = len(ev)
    for i, e in enumerate(ev):
        if e.get("period") not in (1, 2):
            continue
        t = e["type"]["name"]
        loc = e.get("location")
        if t == "Shot" and loc:
            sh = e["shot"]
            resultado = (sh.get("outcome") or {}).get("name")
            corpo = (sh.get("body_part") or {}).get("name", "")
            prox, mesmo = None, None
            for j in range(i + 1, min(i + 60, n)):
                f = ev[j]
                if f.get("possession") != e.get("possession") and f.get("period") == e["period"]:
                    prox = (f.get("play_pattern") or {}).get("name")
                    mesmo = (f.get("possession_team") or f["team"])["id"] == e["team"]["id"]
                    break
            r["chutes"].append((zona_de(loc), resultado, float(sh.get("statsbomb_xg") or 0.0), "Head" in corpo, (sh.get("type") or {}).get("name"), prox, mesmo))
        elif t == "Pass" and loc:
            p = e["pass"]
            tipo = (p.get("type") or {}).get("name")
            resultado = (p.get("outcome") or {}).get("name")
            fim = p.get("end_location")
            if tipo == "Throw-in":
                r["laterais"].append((zona_de(loc), zona_de(fim), resultado is None, float(p.get("length") or 0.0)))
            elif tipo == "Corner":
                chute_depois = None
                for j in range(i + 1, min(i + 60, n)):
                    f = ev[j]
                    if f.get("possession") != e.get("possession"):
                        break
                    if f["type"]["name"] == "Shot" and f["team"]["id"] == e["team"]["id"]:
                        chute_depois = (float(f["shot"].get("statsbomb_xg") or 0.0), (f["shot"].get("outcome") or {}).get("name") == "Goal")
                        break
                r["escanteios"].append((zona_de(fim), resultado is None, (p.get("technique") or {}).get("name"), 0 if loc[1] < 40 else 1, chute_depois))
            elif tipo is None and resultado == "Out":
                r["fora"].append(zona_de(loc))
        elif t == "Foul Committed" and loc:
            card = (e.get("foul_committed") or {}).get("card", {}).get("name")
            # falta de DEFESA (quem faz a falta não tem a bola): espelha para o referencial de quem tinha a bola; falta de ATAQUE (a equipe da posse
            # é quem faz a falta): o ponto já está no referencial de quem tinha a bola
            ataque = (e.get("possession_team") or e["team"])["id"] == e["team"]["id"]
            r["faltas"].append((zona_de(loc) if ataque else zona_espelhada(loc), card, ataque))
    return r


def processar(pasta: str, grupo: str) -> dict:
    tot = {"jogos": 0, "chutes": [], "laterais": [], "escanteios": [], "faltas": [], "fora": []}
    for d in sorted(os.listdir(os.path.join(pasta, grupo))):
        rotulo = json.load(open(os.path.join(pasta, grupo, d, "INDICE.json")))["rotulo"]
        for reg in c.carregar_completo(pasta, grupo, rotulo):
            tot["jogos"] += 1
            r = analisar_partida(reg["eventos"])
            for k in ("chutes", "laterais", "escanteios", "faltas", "fora"):
                tot[k].extend(r[k])
    return tot


def pct(a, b):
    return 100.0 * a / b if b else 0.0


def relatorio(t: dict, acoes_por_zona: list[int] | None = None) -> dict:
    jogos = t["jogos"]
    out: dict = {"jogos": jogos}
    print(f"{jogos} jogos\n\nCHUTES: {len(t['chutes'])} ({len(t['chutes']) / jogos:.1f} por jogo)")
    por_faixa = collections.defaultdict(list)
    for z, res, xg, cabeca, tipo, prox, mesmo in t["chutes"]:
        if z is not None:
            por_faixa[z // 3].append((res, xg, cabeca, tipo, prox, mesmo))
    print(f"  {'faixa':18s} {'n':>7s} " + " ".join(f"{x:>8s}" for x in RESULTADOS) + f" {'xG médio':>9s} {'cabeça %':>9s}")
    out["chutes_por_faixa"] = {}
    for f in range(6):
        v = por_faixa[f]
        if not v:
            continue
        cnt = collections.Counter(x[0] for x in v)
        linha = {k: pct(cnt[k], len(v)) for k in RESULTADOS}
        print(f"  {FAIXAS_NOME[f]:18s} {len(v):7d} " + " ".join(f"{linha[k]:8.1f}" for k in RESULTADOS) + f" {sum(x[1] for x in v) / len(v):9.3f} {pct(sum(1 for x in v if x[2]), len(v)):9.1f}")
        out["chutes_por_faixa"][FAIXAS_NOME[f]] = {"n": len(v), "resultados_pct": linha, "xg_medio": sum(x[1] for x in v) / len(v), "cabeca_pct": pct(sum(1 for x in v if x[2]), len(v))}
    print("\nO QUE VEM DEPOIS DO CHUTE (primeira posse diferente: padrão e se a posse continua com quem chutou), % por resultado:")
    por_res = collections.defaultdict(list)
    for z, res, xg, cabeca, tipo, prox, mesmo in t["chutes"]:
        por_res[res].append((prox, mesmo))
    print(f"  {'resultado':10s} {'n':>7s} " + " ".join(f"{p[:11]:>11s}" for p in PROXIMA_POSSE) + f" {'mesma equipe':>13s}")
    out["depois_do_chute"] = {}
    for res in RESULTADOS:
        v = por_res.get(res, [])
        if len(v) < 20:
            continue
        cnt = collections.Counter(x[0] for x in v)
        linha = {p: pct(cnt[p], len(v)) for p in PROXIMA_POSSE}
        mesma = pct(sum(1 for x in v if x[1]), len(v))
        print(f"  {res:10s} {len(v):7d} " + " ".join(f"{linha[p]:11.1f}" for p in PROXIMA_POSSE) + f" {mesma:13.1f}")
        out["depois_do_chute"][res] = {"n": len(v), "proxima_posse_pct": linha, "mesma_equipe_pct": mesma}
    lat = [x for x in t["laterais"] if x[0] is not None and x[1] is not None]
    print(f"\nLATERAIS: {len(t['laterais'])} ({len(t['laterais']) / jogos:.1f} por jogo); completos {pct(sum(1 for x in t['laterais'] if x[2]), len(t['laterais'])):.1f}%; comprimento médio {sum(x[3] for x in t['laterais']) / len(t['laterais']):.1f} jardas")
    print("  origem (faixa) -> destino da faixa (% por linha), só laterais completos ou não (zona onde a bola chega):")
    print(f"  {'origem':18s} {'n':>6s} {'completo %':>10s} " + " ".join(f"{x[:9]:>9s}" for x in FAIXAS_NOME))
    out["laterais"] = {}
    for f in range(6):
        v = [x for x in lat if x[0] // 3 == f]
        if len(v) < 50:
            continue
        cnt = collections.Counter(x[1] // 3 for x in v)
        linha = [pct(cnt[k], len(v)) for k in range(6)]
        print(f"  {FAIXAS_NOME[f]:18s} {len(v):6d} {pct(sum(1 for x in v if x[2]), len(v)):10.1f} " + " ".join(f"{x:9.1f}" for x in linha))
        out["laterais"][FAIXAS_NOME[f]] = {"n": len(v), "completo_pct": pct(sum(1 for x in v if x[2]), len(v)), "destino_faixa_pct": linha}
    esc = t["escanteios"]
    print(f"\nESCANTEIOS COBRADOS: {len(esc)} ({len(esc) / jogos:.1f} por jogo); completos {pct(sum(1 for x in esc if x[1]), len(esc)):.1f}%")
    dest = collections.Counter(x[0] for x in esc if x[0] is not None)
    print("  zona onde a bola chega (18 zonas, % dos escanteios):")
    nomes = [f"{FAIXAS_NOME[z // 3]}/{['esq', 'cen', 'dir'][z % 3]}" for z in range(18)]
    out["escanteios"] = {"n": len(esc), "completo_pct": pct(sum(1 for x in esc if x[1]), len(esc)), "destino_pct": {}}
    for z, n in dest.most_common(6):
        print(f"    {nomes[z]:28s} {pct(n, len(esc)):5.1f}%")
    for z, n in dest.items():
        out["escanteios"]["destino_pct"][nomes[z]] = pct(n, len(esc))
    tec = collections.Counter(x[2] for x in esc)
    print("  técnica:", ", ".join(f"{k} {pct(v, len(esc)):.0f}%" for k, v in tec.most_common()))
    com_chute = [x[4] for x in esc if x[4] is not None]
    xg_sum = sum(x[0] for x in com_chute)
    gols = sum(1 for x in com_chute if x[1])
    print(f"  DEPOIS DO ESCANTEIO: {pct(len(com_chute), len(esc)):.1f}% geram um chute da mesma equipe na mesma posse; xG por escanteio {xg_sum / len(esc):.3f}; gols {pct(gols, len(esc)):.2f}% dos escanteios")
    out["escanteios"].update({"gera_chute_pct": pct(len(com_chute), len(esc)), "xg_por_escanteio": xg_sum / len(esc), "gol_pct": pct(gols, len(esc))})
    faltas = [x for x in t["faltas"] if x[0] is not None]
    print(f"\nFALTAS COMETIDAS: {len(faltas)} ({len(faltas) / jogos:.1f} por jogo); com cartão {pct(sum(1 for x in faltas if x[1]), len(faltas)):.1f}%; "
          f"falta de ataque (de quem tem a bola) {pct(sum(1 for x in faltas if x[2]), len(faltas)):.1f}%")
    print("  por zona do time que TINHA a bola (zona espelhada), % das faltas" + (" e por 100 ações naquela zona:" if acoes_por_zona else ":"))
    cf = collections.Counter(x[0] for x in faltas)
    cfora = collections.Counter(x for x in t["fora"] if x is not None)
    out["faltas_por_faixa"] = {}
    print(f"  {'faixa':18s} {'% faltas':>9s} " + (f"{'faltas/100 ações':>17s} {'fora/100 ações':>15s}" if acoes_por_zona else ""))
    for f in range(6):
        nf = sum(cf[z] for z in range(3 * f, 3 * f + 3))
        no = sum(cfora[z] for z in range(3 * f, 3 * f + 3))
        linha = f"  {FAIXAS_NOME[f]:18s} {pct(nf, len(faltas)):9.1f} "
        d = {"pct_faltas": pct(nf, len(faltas))}
        if acoes_por_zona:
            na = sum(acoes_por_zona[3 * f:3 * f + 3])
            linha += f"{100 * nf / na:17.3f} {100 * no / na:15.3f}"
            d.update({"faltas_por_100_acoes": 100 * nf / na, "passes_fora_por_100_acoes": 100 * no / na})
        out["faltas_por_faixa"][FAIXAS_NOME[f]] = d
        print(linha)
    return out


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    pasta = args[0] if args else "dados_referencia/statsbomb/completo"
    grupo = args[1] if len(args) > 1 else "ligas_2015_16"
    dados = processar(pasta, grupo)
    acoes = None
    resumo_path = os.path.join(os.path.dirname(pasta.rstrip("/")), f"{grupo}.json")
    if os.path.exists(resumo_path):
        res = json.load(open(resumo_path))
        acoes = [sum(sum(r["contagens_18x20"][z]) for r in res.values()) for z in range(18)]
    saida = relatorio(dados, acoes)
    if "--resumo" in sys.argv:
        alvo = sys.argv[sys.argv.index("--resumo") + 1]
        os.makedirs(os.path.dirname(alvo) or ".", exist_ok=True)
        json.dump(saida, open(alvo, "w"), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        print("resumo gravado em", alvo)
