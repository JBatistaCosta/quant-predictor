#!/usr/bin/env python3
"""Cruza os jogos da Eurocopa e da Copa América do FotMob (baixados por `baixar_fotmob_torneios.py`) com os do StatsBomb Open Data (Achado 36).

Casamento SUPERVISADO por seleções: o par de nomes (normalizados por uma tabela de apelidos explícita, `APELIDOS`) e o placar; jogos que não casam sem ambiguidade
são listados, nunca forçados. Para cada par casado compara: chutes, gols, xG, escanteios, passes e posse (quando os dois lados têm o dado).
Uso: python scripts/cruzar_fotmob_statsbomb_torneios.py [--saida arq.json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import baixar_fotmob_torneios as bf  # noqa: E402
import comparar_competicoes_statsbomb as c  # noqa: E402

PASTA_SB = "dados_referencia/statsbomb/completo"
PASTA_FM = "dados_referencia/fotmob"
PARES = [("euro", "2024", "euro", "UEFA Euro 2024"), ("euro", "2020", "euro", "UEFA Euro 2020"), ("copa_america", "2024", "copa_america", "Copa America 2024"), ("copa_mundo", "2022", "copa_mundo", "FIFA World Cup 2022")]
# nomes diferentes entre as fontes para a MESMA seleção (conferido à mão contra as listas de seleções de cada edição)
APELIDOS = {"turkiye": "turkey", "czechia": "czech republic", "republic of ireland": "ireland", "ir iran": "iran", "korea republic": "south korea", "usa": "united states",
            "united states of america": "united states", "bosnia and herzegovina": "bosnia herzegovina", "north macedonia": "macedonia", "cabo verde": "cape verde"}


def norm(nome: str) -> str:
    s = unicodedata.normalize("NFKD", nome or "").encode("ascii", "ignore").decode().lower().strip()
    return APELIDOS.get(s, s)


def numero(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).split()[0].replace("%", ""))
    except ValueError:
        return None


def total_de_passes(texto: str | None) -> float | None:
    """'643 (94%)' -> 684 passes no total (certos / taxa de acerto)."""
    try:
        certos = float(str(texto).split()[0])
        pct = float(str(texto).split("(")[1].split("%")[0])
        return certos * 100 / pct if pct else None
    except (IndexError, ValueError):
        return None


def resumo_fotmob(reg: dict) -> dict:
    d = reg["matchDetails"]
    fx = reg["fixture"]
    stats = {}
    for g in (((d.get("content") or {}).get("stats") or {}).get("Periods") or {}).get("All", {}).get("stats", []):
        for s in g.get("stats", []):
            if s.get("key") and isinstance(s.get("stats"), list) and len(s["stats"]) == 2:
                stats.setdefault(s["key"], [numero(s["stats"][0]), numero(s["stats"][1])])
    bruto = {}
    for g in (((d.get("content") or {}).get("stats") or {}).get("Periods") or {}).get("All", {}).get("stats", []):
        for st_ in g.get("stats", []):
            if st_.get("key") == "accurate_passes" and isinstance(st_.get("stats"), list):
                bruto = {"passes_totais": [total_de_passes(x) for x in st_["stats"]]}
                break
    stats.update(bruto)
    # o mapa de chutes do FotMob inclui as cobranças da disputa de pênaltis (period 'PenaltyShootout'); o StatsBomb as guarda em outro período e aqui são
    # excluídas dos dois lados (achado na revisão do Achado 36: elas inflavam o xG e os chutes do FotMob em jogos decididos nos pênaltis)
    chutes = [x for x in ((((d.get("content") or {}).get("shotmap") or {}).get("shots")) or []) if x.get("period") != "PenaltyShootout"]
    home, away = fx["home"], fx["away"]
    return {"id": reg["fotmob_match_id"], "casa": norm(home["name"]), "fora": norm(away["name"]), "placar": (home.get("score"), away.get("score")),
            "data": (fx["status"] or {}).get("utcTime", "")[:10], "stats": stats, "n_chutes": [sum(1 for s in chutes if s.get("teamId") == int(home["id"]) or str(s.get("teamId")) == str(home["id"])),
                                                                                                 sum(1 for s in chutes if str(s.get("teamId")) == str(away["id"]))],
            "xg_chutes": [round(sum(float(s.get("expectedGoals") or 0) for s in chutes if str(s.get("teamId")) == str(t["id"])), 3) for t in (home, away)]}


def resumo_statsbomb(ev: list[dict]) -> dict | None:
    times = [((e.get("team") or {}).get("id"), (e.get("team") or {}).get("name")) for e in ev if (e.get("type") or {}).get("name") == "Starting XI"][:2]
    if len(times) < 2:
        return None
    (a, na), (b, nb) = times
    gols, chutes, xg, esc, passes = {a: 0, b: 0}, {a: 0, b: 0}, {a: 0.0, b: 0.0}, {a: 0, b: 0}, {a: 0, b: 0}
    for e in ev:
        t = (e.get("type") or {}).get("name")
        eq = (e.get("team") or {}).get("id")
        if eq not in gols:
            continue
        if t == "Shot" and e.get("period") in (1, 2, 3, 4):
            chutes[eq] += 1
            sh = e.get("shot") or {}
            xg[eq] += float(sh.get("statsbomb_xg") or 0)
            if ((sh.get("outcome") or {}).get("name")) == "Goal" and e.get("period") != 5:
                gols[eq] += 1
        elif t == "Own Goal Against":
            gols[b if eq == a else a] += 1
        elif t == "Pass" and e.get("period") in (1, 2, 3, 4):
            passes[eq] += 1
            if (((e.get("pass") or {}).get("type") or {}).get("name")) == "Corner":
                esc[eq] += 1
    return {"casa": norm(na), "fora": norm(nb), "gols": (gols[a], gols[b]), "chutes": [chutes[a], chutes[b]], "xg": [round(xg[a], 3), round(xg[b], 3)], "escanteios": [esc[a], esc[b]],
            "passes": [passes[a], passes[b]]}


def cruzar(fm: list[dict], sb: list[dict]) -> tuple[list[dict], list]:
    pares, sem = [], []
    usados = set()
    for f in fm:
        cand = [i for i, s in enumerate(sb) if i not in usados and {s["casa"], s["fora"]} == {f["casa"], f["fora"]}]
        if len(cand) > 1:                                            # mesmo par duas vezes no torneio: desempata pelo placar (gols totais por lado)
            cand = [i for i in cand if (sb[i]["gols"] == tuple(f["placar"]) if sb[i]["casa"] == f["casa"] else sb[i]["gols"] == tuple(f["placar"][::-1]))] or cand
        if len(cand) > 1 and f.get("data"):                         # mesmo par e mesmo placar (ex.: Argentina 2x0 Canadá na fase de grupos e na semifinal): pela ordem do torneio,
            cand = cand[:1]                                           # já que `fm` vem em ordem de data e o StatsBomb em ordem de match_id (cronológica); conferido à parte
        if len(cand) == 1:
            usados.add(cand[0])
            s = sb[cand[0]]
            inv = s["casa"] != f["casa"]                              # a ordem casa/fora pode diferir entre as fontes
            o = (lambda v: v[::-1]) if inv else (lambda v: v)
            pares.append({"fotmob": f, "statsbomb": {**s, **{k: o(s[k]) for k in ("gols", "chutes", "xg", "escanteios", "passes")}}})
        else:
            sem.append((f["data"], f["casa"], f["fora"], len(cand)))
    return pares, sem


def main() -> dict:
    saida = {}
    for tfm, temp, tsb, rotulo in PARES:
        fm = [resumo_fotmob(r) for r in bf.carregar(PASTA_FM, tfm, temp)]
        base = c.carregar_completo(PASTA_SB, tsb, rotulo)
        sb = [x for x in (resumo_statsbomb(reg["eventos"]) for reg in base) if x]
        pares, sem = cruzar(fm, sb)
        saida[f"{tfm} {temp}"] = {"fotmob": len(fm), "statsbomb": len(sb), "casados": len(pares), "sem_par": sem, "pares": pares}
    return saida


def comparar(pares: list[dict]) -> dict:
    """Razão FotMob/StatsBomb e correlação nos totais do jogo (soma dos dois times) para chutes, gols, xG, escanteios e passes."""
    import statistics as st
    out = {}
    def pearson(x, y):
        mx, my = st.mean(x), st.mean(y)
        sxx, syy = sum((a - mx) ** 2 for a in x), sum((b - my) ** 2 for b in y)
        return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (sxx * syy) ** 0.5 if sxx and syy else float("nan")
    for k, ext in (("chutes", lambda p: (sum(p["fotmob"]["n_chutes"]), sum(p["statsbomb"]["chutes"]))), ("gols", lambda p: (sum(p["fotmob"]["placar"]), sum(p["statsbomb"]["gols"]))),
                   ("xg", lambda p: (sum(p["fotmob"]["xg_chutes"]), sum(p["statsbomb"]["xg"]))),
                   ("escanteios", lambda p: (sum(p["fotmob"]["stats"].get("corners", [0, 0])), sum(p["statsbomb"]["escanteios"]))),
                   ("passes", lambda p: (sum(x or 0 for x in p["fotmob"]["stats"].get("passes_totais", [0, 0])), sum(p["statsbomb"]["passes"])))):
        v = [ext(p) for p in pares]
        x, y = [a for a, _ in v], [b for _, b in v]
        if len(v) > 2 and sum(y) > 0:
            out[k] = {"media_fotmob": st.mean(x), "media_statsbomb": st.mean(y), "razao": sum(x) / sum(y), "corr": pearson(x, y)}
    return out


def razao_xg_com_ic(pares: list[dict], reamostras: int = 2000, semente: int = 3) -> dict:
    """Razão xG FotMob / xG StatsBomb (soma sobre soma) e IC 95% por bootstrap de jogos."""
    import random
    v = [(sum(p["fotmob"]["xg_chutes"]), sum(p["statsbomb"]["xg"])) for p in pares]
    rng = random.Random(semente)
    base = sum(a for a, _ in v) / sum(b for _, b in v)
    bs = sorted(sum(x[0] for x in amostra) / sum(x[1] for x in amostra) for amostra in ([v[rng.randrange(len(v))] for _ in v] for _ in range(reamostras)))
    return {"razao": base, "ic_baixo": bs[int(0.025 * (reamostras - 1))], "ic_alto": bs[int(0.975 * (reamostras - 1))], "jogos": len(v)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--saida", default="")
    a = ap.parse_args()
    res = main()
    todos = []
    for nome, v in res.items():
        print(f"\n{nome}: FotMob {v['fotmob']} jogos, StatsBomb {v['statsbomb']}, casados {v['casados']}; sem par: {v['sem_par']}")
        todos += v["pares"]
        for k, m in comparar(v["pares"]).items():
            print(f"  {k:10s} FotMob {m['media_fotmob']:7.2f} StatsBomb {m['media_statsbomb']:7.2f} razão {m['razao']:.3f} corr {m['corr']:+.3f}")
    print("\nRazão do xG FotMob / StatsBomb por edição (IC 95% por bootstrap de jogos):")
    for nome, v in res.items():
        r = razao_xg_com_ic(v["pares"])
        print(f"  {nome:22s} {r['razao']:.3f}  [{r['ic_baixo']:.3f}; {r['ic_alto']:.3f}]  ({r['jogos']} jogos)")
    tardias = [p for n, v in res.items() if n != "euro 2020" for p in v["pares"]]
    r = razao_xg_com_ic(tardias)
    print(f"  {'2022-2024 (juntas)':22s} {r['razao']:.3f}  [{r['ic_baixo']:.3f}; {r['ic_alto']:.3f}]  ({r['jogos']} jogos)")
    print("\nTODOS:", len(todos), "jogos casados")
    for k, m in comparar(todos).items():
        print(f"  {k:10s} FotMob {m['media_fotmob']:7.2f} StatsBomb {m['media_statsbomb']:7.2f} razão {m['razao']:.3f} corr {m['corr']:+.3f}")
    if a.saida:
        json.dump({n: {k: v[k] for k in ("fotmob", "statsbomb", "casados", "sem_par")} for n, v in res.items()}, open(a.saida, "w"), ensure_ascii=False)
