#!/usr/bin/env python3
"""Compara, nos mesmos jogos, os modelos cadastrados em `model_predictions` (1X2 e over/under 2,5) contra o mercado (odds de fechamento) e o Dixon-Coles.

Amostra: jogos dos CSVs do backtest (`dados_referencia/backtest_simulador/*_2022_25.csv`, temporada de teste) que têm `dixon_coles_v1` e odds de fechamento. Cada modelo é avaliado
nos jogos em que tem previsão; as diferenças pareadas contra o mercado e o Dixon-Coles usam só esses jogos (IC 95% por bootstrap). Somente leitura; credenciais por variável de
ambiente (SUPABASE_URL e SUPABASE_KEY), sem valor padrão.
Uso: python scripts/comparar_modelos_cadastrados.py --temporada 2025 --modelos markov_multievento_v1 hibrido_gols_v1 ... [--saida resultado.json]
Atenção: o modelo pode ter sido treinado em jogos da própria amostra (não é garantido que todas as previsões sejam walk-forward); olhe a data de criação e o método de cada modelo antes de tratar a comparação como fora da amostra.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request


def _get(caminho: str) -> list:
    url, chave = os.environ["SUPABASE_URL"].rstrip("/"), os.environ["SUPABASE_KEY"]
    req = urllib.request.Request(f"{url}/rest/v1/{caminho}", headers={"apikey": chave, "Authorization": "Bearer " + chave})
    for espera in (2, 4, 8, 16, None):
        try:
            return json.load(urllib.request.urlopen(req, timeout=120))
        except urllib.error.HTTPError as e:
            if e.code < 500 or espera is None:
                raise
        except (urllib.error.URLError, TimeoutError):
            if espera is None:
                raise
        time.sleep(espera)


def pagina(caminho: str) -> list:
    out, off = [], 0
    while True:
        pag = _get(f"{caminho}&limit=1000&offset={off}&order=id.asc")
        out += pag
        off += 1000
        if len(pag) < 1000:
            return out


def devig(odds):
    inv = [1.0 / o for o in odds]
    t = sum(inv)
    return [x / t for x in inv]


def ic(dif, B=3000, semente=7):
    rng = random.Random(semente)
    n = len(dif)
    ms = sorted(sum(dif[rng.randrange(n)] for _ in range(n)) / n for _ in range(B))
    return sum(dif) / n, ms[int(0.025 * B)], ms[int(0.975 * B)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--temporada", default="2025")
    ap.add_argument("--modelos", nargs="+", required=True)
    ap.add_argument("--pasta", default="dados_referencia/backtest_simulador")
    ap.add_argument("--saida", default="")
    a = ap.parse_args()
    jogos = {}
    for f in sorted(glob.glob(os.path.join(a.pasta, "*_2022_25.csv"))):
        for r in csv.DictReader(open(f)):
            if r["season"] == a.temporada and r["dc_h"] and r["o_h"] and r["o_over"]:
                hg, ag = int(r["hg"]), int(r["ag"])
                jogos[int(r["id"])] = {"res": 0 if hg > ag else (1 if hg == ag else 2), "over": hg + ag >= 3,
                                       "dc": ([float(r["dc_h"]), float(r["dc_d"]), float(r["dc_a"])], float(r["dc_over"])),
                                       "mercado": (devig([float(r["o_h"]), float(r["o_d"]), float(r["o_a"])]), devig([float(r["o_over"]), float(r["o_under"])])[0])}
    ids = sorted(jogos)
    print(f"{len(ids)} jogos de teste com Dixon-Coles e odds")
    previsoes = {}
    for nome in a.modelos:
        p1, po = {}, {}
        for i in range(0, len(ids), 100):
            filtro = ",".join(str(x) for x in ids[i:i + 100])
            for r in pagina(f"model_predictions?select=match_id,market,selection,probability&model_name=eq.{urllib.parse.quote(nome)}&match_id=in.({filtro})"):
                mk, sel, p = r["market"].lower(), r["selection"].lower(), float(r["probability"])
                if mk == "1x2":
                    p1.setdefault(r["match_id"], {})[sel] = p
                elif mk == "over_under_2.5" and sel == "over":
                    po[r["match_id"]] = p
        previsoes[nome] = (p1, po)

    def perdas(p3, po, res, over):
        out = []
        if p3 is not None:
            p3 = [min(max(x, 1e-9), 1) for x in p3]
            out += [-math.log(p3[res]), sum((p3[k] - (1.0 if k == res else 0.0)) ** 2 for k in range(3))]
        else:
            out += [None, None]
        if po is not None:
            po = min(max(po, 1e-9), 1 - 1e-9)
            out += [-math.log(po if over else 1 - po), (po - (1.0 if over else 0.0)) ** 2]
        else:
            out += [None, None]
        return out

    ref = {"mercado": {i: perdas(*jogos[i]["mercado"][:1], jogos[i]["mercado"][1], jogos[i]["res"], jogos[i]["over"]) for i in ids},
           "dixon_coles_v1": {i: perdas(*jogos[i]["dc"][:1], jogos[i]["dc"][1], jogos[i]["res"], jogos[i]["over"]) for i in ids}}
    rotulos = ["ll 1X2", "Brier 1X2", "ll O/U", "Brier O/U"]
    resultado = {}
    linhas = []
    for nome, (p1, po) in previsoes.items():
        pm = {}
        for i in ids:
            d = p1.get(i)
            p3 = [d["home"], d["draw"], d["away"]] if d and all(k in d for k in ("home", "draw", "away")) else None
            pm[i] = perdas(p3, po.get(i), jogos[i]["res"], jogos[i]["over"])
        r = {"jogos_1x2": sum(1 for i in ids if pm[i][0] is not None), "jogos_ou": sum(1 for i in ids if pm[i][2] is not None)}
        for k, j in (("1x2", 0), ("ou", 2)):
            sub = [i for i in ids if pm[i][j] is not None]
            if len(sub) < 30:
                continue
            r[f"media_{k}"] = [sum(pm[i][j] for i in sub) / len(sub), sum(pm[i][j + 1] for i in sub) / len(sub)]
            for refnome in ref:
                m, lo, hi = ic([pm[i][j] - ref[refnome][i][j] for i in sub])
                r[f"{k}_menos_{refnome}"] = [m, lo, hi]
        resultado[nome] = r
        linhas.append((nome, r))
    print(f"{'modelo':58s} {'n1X2':>5s} {'ll 1X2':>7s} {'-mercado (IC)':>26s} {'-Dixon-Coles (IC)':>26s} | {'nOU':>5s} {'ll OU':>7s} {'-mercado (IC)':>26s}")
    for nome, r in linhas:
        def f(k):
            if k not in r:
                return " " * 26
            m, lo, hi = r[k]
            return f"{m:+.4f} [{lo:+.4f};{hi:+.4f}]{'*' if lo > 0 or hi < 0 else ' '}"
        print(f"{nome[:58]:58s} {r['jogos_1x2']:5d} {r.get('media_1x2', [float('nan')])[0]:7.4f} {f('1x2_menos_mercado')} {f('1x2_menos_dixon_coles_v1')} | {r['jogos_ou']:5d} {r.get('media_ou', [float('nan')])[0]:7.4f} {f('ou_menos_mercado')}")
    mk1 = sum(ref["mercado"][i][0] for i in ids) / len(ids)
    mk2 = sum(ref["mercado"][i][2] for i in ids) / len(ids)
    dc1 = sum(ref["dixon_coles_v1"][i][0] for i in ids) / len(ids)
    dc2 = sum(ref["dixon_coles_v1"][i][2] for i in ids) / len(ids)
    print(f"\nreferências nos {len(ids)} jogos: mercado ll 1X2 {mk1:.4f}, ll O/U {mk2:.4f}; Dixon-Coles v1 ll 1X2 {dc1:.4f}, ll O/U {dc2:.4f}")
    if a.saida:
        json.dump({"jogos": len(ids), "referencias": {"mercado": [mk1, mk2], "dixon_coles_v1": [dc1, dc2]}, "modelos": resultado}, open(a.saida, "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
