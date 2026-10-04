#!/usr/bin/env python3
"""Taxas dos eventos AO LONGO DO JOGO e duração das ações (StatsBomb Open Data, La Liga 2015/16, 380 jogos) -- Achado 27.

Para simular a bola no TEMPO (cadeia de Markov com tempo de permanência), faltam duas peças que a matriz de transição não tem:
  1. o ritmo: quantas ações por minuto e como chutes, perdas, faltas, escanteios e gols variam por janela de 15 minutos e no acréscimo;
  2. a duração de cada ação (passe, condução, chute, drible) e a duração das posses.
Usa os campos `period`, `minute`, `second`, `duration` e `possession` dos eventos. Reaproveita a definição de ação de
`gerar_matriz_transicao_statsbomb.py` (passe completo/perdido, condução, chute, `Dispossessed`, `Miscontrol`).

Uso:  python scripts/analisar_tempo_eventos_statsbomb.py [pasta_cache] [--resumo dados_referencia/statsbomb/tempo_la_liga_2015_16.json]
O cache por partida (<pasta>/tempo_eventos.json, ~7,5 MB) fica fora do Git; `--resumo` grava o agregado (poucos KB) que fica versionado.
"""

from __future__ import annotations

import collections
import json
import os
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gerar_matriz_transicao_statsbomb as g  # noqa: E402

JANELAS = ["1-15", "16-30", "31-45", "45+ (acresc. 1T)", "46-60", "61-75", "76-90", "90+ (acresc. 2T)"]
ACOES = ("Pass", "Carry", "Shot", "Dribble", "Dispossessed", "Miscontrol")


def janela(periodo: int, minuto: int) -> int | None:
    """Janela de 15 minutos; 1T: minuto <45 -> 0..2, >= 45 -> acréscimo (3); 2T: 45..89 -> 4..6, >= 90 -> acréscimo (7). Prorrogação fica fora."""
    if periodo == 1:
        return 3 if minuto >= 45 else minuto // 15
    if periodo == 2:
        return 7 if minuto >= 90 else 4 + (minuto - 45) // 15
    return None


def corner(e: dict) -> bool:
    return e["type"]["name"] == "Pass" and ((e["pass"].get("type") or {}).get("name")) == "Corner"


def analisar_partida(ev: list[dict]) -> dict:
    cont = collections.Counter()           # (janela, rotulo) -> n
    dur = collections.defaultdict(list)    # tipo de ação -> durações (s)
    posse = {}                             # possession id -> [primeiro ts, último ts, n ações]
    fim_periodo = {}                       # periodo -> maior minuto*60+segundo
    for e in ev:
        t = e["type"]["name"]
        p = e.get("period")
        if p not in (1, 2):
            continue
        ts = e["minute"] * 60 + e["second"]
        fim_periodo[p] = max(fim_periodo.get(p, 0), ts)
        w = janela(p, e["minute"])
        if w is None:
            continue
        if t == "Pass":
            pp = e["pass"]
            r = (pp.get("outcome") or {}).get("name")
            cont[(w, "acao")] += 1
            cont[(w, "passe")] += 1
            if r in g.PERDAS_PASSE:
                cont[(w, "perda")] += 1
            if corner(e):
                cont[(w, "escanteio")] += 1
            if pp.get("cross"):
                cont[(w, "cruzamento")] += 1
        elif t == "Carry":
            cont[(w, "acao")] += 1
        elif t == "Shot":
            cont[(w, "acao")] += 1
            cont[(w, "chute")] += 1
            if (e["shot"].get("outcome") or {}).get("name") == "Goal":
                cont[(w, "gol")] += 1
        elif t in ("Dispossessed", "Miscontrol"):
            cont[(w, "acao")] += 1
            cont[(w, "perda")] += 1
        elif t == "Foul Committed":
            cont[(w, "falta")] += 1
        elif t == "Dribble":
            cont[(w, "drible")] += 1
        if t in ACOES and e.get("duration") is not None:
            chave = "Cruzamento" if t == "Pass" and e["pass"].get("cross") else t
            dur[chave].append(float(e["duration"]))
        if t in ACOES and e.get("possession") is not None:
            d = posse.setdefault(e["possession"], [ts, ts, 0, p])
            d[0], d[1] = min(d[0], ts), max(d[1], ts)
            d[2] += 1
    return {"cont": {f"{w}|{r}": n for (w, r), n in cont.items()}, "dur": dict(dur),
            "posses": [[v[1] - v[0], v[2]] for v in posse.values()], "fim": fim_periodo}


def baixar(cache: str | None) -> dict:
    caminho = os.path.join(cache, "tempo_eventos.json") if cache else None
    if caminho and os.path.exists(caminho):
        return json.load(open(caminho))
    ids = sorted(p["match_id"] for p in g.baixar_json(f"{g.BASE}/matches/{g.COMPETICAO}/{g.TEMPORADA}.json"))
    with ThreadPoolExecutor(max_workers=6) as ex:
        partidas = list(ex.map(lambda m: analisar_partida(g.baixar_json(f"{g.BASE}/events/{m}.json")), ids))
    res = {"jogos": len(ids), "partidas": partidas}
    if caminho:
        os.makedirs(cache, exist_ok=True)
        json.dump(res, open(caminho, "w"))
    return res


def relatorio(res: dict) -> None:
    n = res["jogos"]
    tot = collections.Counter()
    for p in res["partidas"]:
        for k, v in p["cont"].items():
            tot[k] += v
    fim1 = statistics.mean(p["fim"].get("1", p["fim"].get(1, 0)) for p in res["partidas"]) / 60
    fim2 = statistics.mean(p["fim"].get("2", p["fim"].get(2, 0)) for p in res["partidas"]) / 60
    print(f"{n} jogos. Duração média do 1T (último evento): {fim1:.1f} min; do 2T: {fim2:.1f} min (de 45,0 e 90,0 de início) -> acréscimos médios ~{fim1 - 45:.1f} e ~{fim2 - 90:.1f} min")
    acr1, acr2 = max(fim1 - 45, 1e-9), max(fim2 - 90, 1e-9)
    larg = [15, 15, 15, acr1, 15, 15, 15, acr2]
    print("\nPOR JANELA (por jogo, dois times; 'por min' divide pela duração da janela; acréscimo = média do último evento):")
    print(f"{'janela':18s} {'acoes/min':>9s} {'chutes/min':>10s} {'gols/min':>9s} {'perda %':>8s} {'faltas/min':>10s} {'escant./min':>11s} {'cruz./min':>9s}")
    for w, nome in enumerate(JANELAS):
        a = tot[f"{w}|acao"] / n
        print(f"{nome:18s} {a / larg[w]:9.2f} {tot[f'{w}|chute'] / n / larg[w]:10.3f} {tot[f'{w}|gol'] / n / larg[w]:9.4f} "
              f"{100 * tot[f'{w}|perda'] / max(tot[f'{w}|passe'] + tot[f'{w}|acao'] - tot[f'{w}|passe'], 1):8.1f} "
              f"{tot[f'{w}|falta'] / n / larg[w]:10.3f} {tot[f'{w}|escanteio'] / n / larg[w]:11.4f} {tot[f'{w}|cruzamento'] / n / larg[w]:9.3f}")
    print("\nDURAÇÃO DAS AÇÕES (segundos, campo `duration` do StatsBomb):")
    dur = collections.defaultdict(list)
    for p in res["partidas"]:
        for k, v in p["dur"].items():
            dur[k].extend(v)
    for k, v in sorted(dur.items(), key=lambda kv: -len(kv[1])):
        v = sorted(v)
        if len(v) < 20:
            continue
        print(f"  {k:12s} n={len(v):7d} média {statistics.mean(v):5.2f}  mediana {statistics.median(v):5.2f}  p90 {v[int(0.9 * len(v))]:5.2f}")
    posses = [x for p in res["partidas"] for x in p["posses"]]
    dp = sorted(x[0] for x in posses)
    na = sorted(x[1] for x in posses)
    print(f"\nPOSSES: {len(posses) / n:.0f} por jogo; duração (1º ao último evento de ação) média {statistics.mean(dp):.1f}s mediana {statistics.median(dp):.1f}s p90 {dp[int(0.9 * len(dp))]:.0f}s; "
          f"ações por posse média {statistics.mean(na):.1f} mediana {statistics.median(na):.0f} p90 {na[int(0.9 * len(na))]}")
    soma = collections.Counter()
    for x in posses:
        soma["tempo"] += x[0]
    print(f"Tempo total em posse (soma das durações, aprox.): {soma['tempo'] / n / 60:.1f} min por jogo dos ~{fim1 + fim2 - 45:.0f} min de bola rolando+paradas")


def quantis(v: list[float]) -> dict:
    v = sorted(v)
    return {"n": len(v), "media": statistics.mean(v), "mediana": statistics.median(v), "p90": v[int(0.9 * len(v))]}


def resumo(res: dict) -> dict:
    """Agregado versionável: contagens totais por janela e evento, quantis das durações, posses por jogo e duração/ações por posse."""
    tot = collections.Counter()
    dur = collections.defaultdict(list)
    for p in res["partidas"]:
        for k, v in p["cont"].items():
            tot[k] += v
        for k, v in p["dur"].items():
            dur[k].extend(v)
    posses = [x for p in res["partidas"] for x in p["posses"]]
    fim1 = statistics.mean(p["fim"].get("1", p["fim"].get(1, 0)) for p in res["partidas"]) / 60
    fim2 = statistics.mean(p["fim"].get("2", p["fim"].get(2, 0)) for p in res["partidas"]) / 60
    return {"jogos": res["jogos"], "janelas": JANELAS, "fim_medio_1t_min": fim1, "fim_medio_2t_min": fim2, "totais_por_janela_e_evento": dict(tot),
            "duracao_acoes_s": {k: quantis(v) for k, v in dur.items() if len(v) >= 20},
            "posses_por_jogo": len(posses) / res["jogos"], "duracao_posse_s": quantis([x[0] for x in posses]), "acoes_por_posse": quantis([x[1] for x in posses])}


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dados = baixar(args[0] if args else None)
    relatorio(dados)
    if "--resumo" in sys.argv:
        alvo = sys.argv[sys.argv.index("--resumo") + 1]
        os.makedirs(os.path.dirname(alvo) or ".", exist_ok=True)
        json.dump(resumo(dados), open(alvo, "w"), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        print("resumo gravado em", alvo)
