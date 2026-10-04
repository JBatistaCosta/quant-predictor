#!/usr/bin/env python3
"""Tempo morto de cada reinício e recuperação da bola depois de uma perda (StatsBomb, 4 ligas de 2015/16) -- Achado 29.

Duas peças que faltam para fechar o relógio e a troca de posse de uma simulação semi-Markov da bola:
  1. TEMPO MORTO: de o time perder a bola (último evento da posse anterior + duração) até o reinício (primeiro evento da posse seguinte), por tipo de
     reinício (`play_pattern` da posse: lateral, falta, tiro de meta, escanteio, goleiro, saída de bola) e por causa (último evento antes do reinício).
  2. RECUPERAÇÃO DEPOIS DA PERDA: o primeiro evento do ADVERSÁRIO depois de cada perda (definição do Achado 15: passe incompleto/fora/impedimento,
     `Dispossessed`, `Miscontrol`): de que tipo é (recuperação em jogo ou reinício), quanto demora e em que zona de 18 acontece (no referencial do adversário).

Lê os eventos COMPLETOS versionados (`dados_referencia/statsbomb/completo`, sem rede).

Uso:  python scripts/analisar_reinicios_e_recuperacoes_statsbomb.py [pasta_completo] [grupo] [--resumo saida.json]
"""

from __future__ import annotations

import collections
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import comparar_competicoes_statsbomb as c  # noqa: E402
import gerar_matriz_transicao_statsbomb as g  # noqa: E402

RUIDO = {"Pressure", "Substitution", "Injury Stoppage", "Tactical Shift", "Player On", "Player Off", "Camera On", "Camera off", "Referee Ball-Drop",
         "Bad Behaviour", "Starting XI", "Half Start", "Half End", "50/50"}
SEM_BOLA_ADV = RUIDO | {"Ball Receipt*", "Foul Won", "Dribbled Past", "Offside"}   # eventos do adversário que não são "ficar com a bola"
REINICIOS = ("From Throw In", "From Free Kick", "From Goal Kick", "From Corner", "From Keeper", "From Kick Off")
FAIXAS_ZONA = ["defesa", "meio baixo", "meio alto", "ataque fora da área baixo", "ataque fora da área alto", "grande área"]


def segundos(e: dict) -> float:
    h, m, s = e["timestamp"].split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def fim_do_evento(e: dict) -> float:
    return segundos(e) + float(e.get("duration") or 0.0)


def analisar_partida(ev: list[dict]) -> dict:
    """Devolve {'mortos': [(padrão, causa, segundos)], 'perdas': [(zona da perda, tipo do 1º evento do adversário, categoria, lag s, zona do adversário ou None)]}."""
    mortos, perdas = [], []
    pos_atual = None
    n = len(ev)
    for i, e in enumerate(ev):
        if e.get("period") not in (1, 2):
            continue
        # ---- tempo morto: primeiro evento de cada posse
        pos = e.get("possession")
        if pos != pos_atual:
            pos_atual = pos
            padrao = (e.get("play_pattern") or {}).get("name")
            if padrao in REINICIOS:
                k = i - 1
                while k >= 0 and ev[k]["type"]["name"] in RUIDO:
                    k -= 1
                if k >= 0 and ev[k].get("period") == e["period"] and ev[k].get("possession") != pos:
                    prev = ev[k]
                    gap = segundos(e) - fim_do_evento(prev)
                    if gap < 0:
                        gap = 0.0
                    causa = prev["type"]["name"]
                    if causa == "Pass":
                        causa = "Pass/" + ((prev["pass"].get("outcome") or {}).get("name") or "completo")
                    elif causa == "Shot":
                        causa = "Shot/" + ((prev["shot"].get("outcome") or {}).get("name") or "?")
                    mortos.append((padrao, causa, gap))
        # ---- perda -> recuperação do adversário
        t = e["type"]["name"]
        perdeu = (t in ("Dispossessed", "Miscontrol")) or (t == "Pass" and ((e["pass"].get("outcome") or {}).get("name")) in g.PERDAS_PASSE)
        if not perdeu or not e.get("location"):
            continue
        meu = e["team"]["id"]
        zona_perda = g.zona_18(*g.para_metros(*e["location"][:2]))
        fim = fim_do_evento(e)
        for j in range(i + 1, min(i + 30, n)):
            f = ev[j]
            if f.get("period") != e["period"]:
                break
            tf = f["type"]["name"]
            if f["team"]["id"] == meu or tf in SEM_BOLA_ADV:
                continue
            categoria = "recuperação em jogo"
            if tf == "Pass" and ((f["pass"].get("type") or {}).get("name")) in ("Throw-in", "Free Kick", "Goal Kick", "Corner", "Kick Off"):
                categoria = "reinício: " + (f["pass"]["type"]["name"])
            lag = max(segundos(f) - fim, 0.0)
            z = g.zona_18(*g.para_metros(*f["location"][:2])) if f.get("location") else None
            perdas.append((zona_perda, tf, categoria, lag, z))
            break
    return {"mortos": mortos, "perdas": perdas}


def quantis(v: list[float]) -> dict:
    v = sorted(v)
    n = len(v)
    return {"n": n, "media": statistics.mean(v), "mediana": statistics.median(v), "p10": v[int(0.1 * n)], "p90": v[int(0.9 * n)], "soma": sum(v)}


def faixa_6(z: int) -> int:
    return z // 3


def processar(pasta: str, grupo: str) -> dict:
    mortos_padrao = collections.defaultdict(list)
    mortos_causa = collections.defaultdict(list)
    perdas_tipo = collections.Counter()
    lag_cat = collections.defaultdict(list)
    matriz = [[0] * 18 for _ in range(18)]      # zona da perda (referencial de quem perdeu) -> zona do adversário (referencial do adversário), só recuperação em jogo
    n_perdas = n_com_zona = 0
    jogos = 0
    for rotulo in sorted(json.load(open(os.path.join(pasta, grupo, d, "INDICE.json")))["rotulo"] for d in os.listdir(os.path.join(pasta, grupo))):
        for reg in c.carregar_completo(pasta, grupo, rotulo):
            jogos += 1
            r = analisar_partida(reg["eventos"])
            for padrao, causa, gap in r["mortos"]:
                mortos_padrao[padrao].append(gap)
                mortos_causa[(padrao, causa)].append(gap)
            for zp, tf, cat, lag, z in r["perdas"]:
                n_perdas += 1
                perdas_tipo[(cat, tf if cat == "recuperação em jogo" else "")] += 1
                lag_cat[cat].append(lag)
                if cat == "recuperação em jogo" and z is not None:
                    matriz[zp][z] += 1
                    n_com_zona += 1
    return {"jogos": jogos, "mortos_padrao": dict(mortos_padrao), "mortos_causa": {f"{a}|{b}": v for (a, b), v in mortos_causa.items()},
            "perdas_tipo": {f"{a}|{b}": n for (a, b), n in perdas_tipo.items()}, "lag_cat": dict(lag_cat), "matriz_recuperacao": matriz,
            "n_perdas": n_perdas, "n_recuperacoes_com_zona": n_com_zona}


def resumo(res: dict) -> dict:
    jogos = res["jogos"]
    return {"jogos": jogos,
            "tempo_morto_por_reinicio_s": {k: quantis(v) for k, v in res["mortos_padrao"].items() if len(v) >= 20},
            "tempo_morto_por_jogo_min": {k: sum(v) / jogos / 60 for k, v in res["mortos_padrao"].items()},
            "reinicios_por_jogo": {k: len(v) / jogos for k, v in res["mortos_padrao"].items()},
            "tempo_morto_por_causa_s": {k: quantis(v) for k, v in res["mortos_causa"].items() if len(v) >= 100},
            "primeiro_evento_do_adversario_apos_perda": res["perdas_tipo"],
            "lag_ate_o_adversario_s": {k: quantis(v) for k, v in res["lag_cat"].items() if len(v) >= 20},
            "n_perdas": res["n_perdas"], "n_recuperacoes_com_zona": res["n_recuperacoes_com_zona"], "matriz_recuperacao_18x18": res["matriz_recuperacao"]}


def relatorio(res: dict) -> None:
    jogos = res["jogos"]
    print(f"{jogos} jogos\n\nTEMPO MORTO POR TIPO DE REINÍCIO (segundos entre o fim do último evento da posse anterior e o 1º evento do reinício):")
    print(f"  {'reinício':16s} {'por jogo':>8s} {'mediana':>8s} {'média':>7s} {'p10':>6s} {'p90':>7s} {'min/jogo':>9s}")
    total = 0.0
    for k in REINICIOS:
        v = res["mortos_padrao"].get(k, [])
        if len(v) < 2:
            continue
        q = quantis(v)
        total += q["soma"] / jogos / 60
        print(f"  {k:16s} {len(v) / jogos:8.1f} {q['mediana']:8.1f} {q['media']:7.1f} {q['p10']:6.1f} {q['p90']:7.1f} {q['soma'] / jogos / 60:9.2f}")
    print(f"  soma do tempo morto dos reinícios: {total:.1f} min por jogo")
    print("\nPRINCIPAIS CAUSAS (padrão | último evento antes do reinício): n, mediana, média (s)")
    for k, v in sorted(res["mortos_causa"].items(), key=lambda kv: -len(kv[1]))[:14]:
        if len(v) >= 100:
            q = quantis(v)
            print(f"  {k:46s} n={q['n']:6d} mediana {q['mediana']:6.1f} média {q['media']:6.1f}")
    print(f"\nPRIMEIRO EVENTO DO ADVERSÁRIO DEPOIS DE UMA PERDA ({res['n_perdas']} perdas):")
    for k, n in sorted(res["perdas_tipo"].items(), key=lambda kv: -kv[1])[:14]:
        print(f"  {100 * n / res['n_perdas']:5.1f}%  {k.replace('|', ' / ')}")
    print("\nATRASO ATÉ O ADVERSÁRIO (s, do fim da perda ao 1º evento dele):")
    for k, v in sorted(res["lag_cat"].items(), key=lambda kv: -len(kv[1])):
        if len(v) >= 20:
            q = quantis(v)
            print(f"  {k:28s} n={q['n']:6d} mediana {q['mediana']:5.1f} média {q['media']:5.1f} p10 {q['p10']:4.1f} p90 {q['p90']:5.1f}")
    print("\nRECUPERAÇÃO EM JOGO: onde o adversário fica com a bola (faixa no referencial dele) dada a faixa onde ocorreu a perda (referencial de quem perdeu), % por linha:")
    m = res["matriz_recuperacao"]
    print(f"  {'perda em':28s} " + " ".join(f"{f[:11]:>11s}" for f in FAIXAS_ZONA) + "   n")
    for fp in range(6):
        linha = [sum(m[zp][z] for zp in range(3 * fp, 3 * fp + 3) for z in range(3 * fa, 3 * fa + 3)) for fa in range(6)]
        n = sum(linha)
        if n:
            print(f"  {FAIXAS_ZONA[fp]:28s} " + " ".join(f"{100 * x / n:11.1f}" for x in linha) + f"  {n:7d}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    pasta = args[0] if args else "dados_referencia/statsbomb/completo"
    grupo = args[1] if len(args) > 1 else "ligas_2015_16"
    resultado = processar(pasta, grupo)
    relatorio(resultado)
    if "--resumo" in sys.argv:
        alvo = sys.argv[sys.argv.index("--resumo") + 1]
        os.makedirs(os.path.dirname(alvo) or ".", exist_ok=True)
        json.dump(resumo(resultado), open(alvo, "w"), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        print("resumo gravado em", alvo)
