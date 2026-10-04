#!/usr/bin/env python3
"""Backtest walk-forward do simulador da cadeia da bola COMO PREVISOR (1X2 e over/under 2,5).

Pergunta: as probabilidades que saem de simular cada jogo N vezes batem o Dixon-Coles e o mercado (odds de fechamento)?

Regras que mantêm o teste honesto:
  * Walk-forward: a força de cada time para o jogo J usa SÓ jogos com data anterior à de J (a temporada de aquecimento entra inteira, a de teste só até J).
  * A força vem de chutes feitos/sofridos (FotMob), encolhida para 1 com `k` pseudo-jogos. Os multiplicadores de perda/quebra ficam em 1: o FotMob não tem as
    coordenadas de passe que o forca_dos_times.py usa (StatsBomb), então só o canal "chute" tem força por time.
  * Mando de campo: o simulador não tem; entra como fator no ataque (mandante x sqrt(h), visitante / sqrt(h), h = chutes casa / chutes fora da história).
  * Nível: o simulador neutro faz ~12,7 chutes por time; o ataque é reescalado para a média de chutes por time da história.
  * Controle obrigatório: a mesma simulação com todos os times iguais (só nível e mando) -- é o piso que a força dos times precisa bater.
Comparação: log-loss e Brier por jogo, apenas nos jogos que têm Dixon-Coles E odds; diferença pareada com IC 95% por bootstrap sobre jogos.
Uso: python scripts/backtest_simulador_preditivo.py --csv jogos.csv --temporada 2025 --sims 1000 --saida resultado.json
CSV: id,season,date,home,away,hg,ag,hs,as,hxg,axg,hc,ac,dc_h,dc_d,dc_a,dc_over,o_h,o_d,o_a,o_over,o_under
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import multiprocessing as mp
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import simulador_cadeia_bola as s  # noqa: E402

CHUTES_POR_TIME_SIM_NEUTRO = 12.68        # medido no simulador padrão (400 jogos, semente 1)
K_ENCOLHIMENTO = 8.0                      # pseudo-jogos em direção ao time médio
_P = None


def _iniciar():
    global _P
    os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    _P = s.Parametros()


def _simular_jogo(args):
    jid, mult_casa, mult_fora, n, semente = args
    rng = random.Random(semente)
    casa = s.Multiplicadores(ataque_chute=mult_casa[0], defesa_chute=mult_casa[1])
    fora = s.Multiplicadores(ataque_chute=mult_fora[0], defesa_chute=mult_fora[1])
    h = d = a = over = 0
    gc = gf = ch = cf = 0
    for _ in range(n):
        r = s.simular_partida(_P, rng, times=(casa, fora))
        g0, g1 = r.get("gols_0", 0), r.get("gols_1", 0)
        h += g0 > g1
        d += g0 == g1
        a += g0 < g1
        over += (g0 + g1) >= 3
        gc, gf, ch, cf = gc + g0, gf + g1, ch + r.get("chutes_0", 0), cf + r.get("chutes_1", 0)
    return jid, {"h": h, "d": d, "a": a, "over": over, "n": n, "gols_casa": gc / n, "gols_fora": gf / n, "chutes_casa": ch / n, "chutes_fora": cf / n}


class Historia:
    """Acumula chutes feitos/sofridos por time só dos jogos já disputados."""

    def __init__(self):
        self.pro, self.contra, self.n = {}, {}, {}
        self.soma_casa = self.soma_fora = 0.0
        self.jogos = 0

    def add(self, j):
        for t, f, c in ((j["home"], j["hs"], j["as"]), (j["away"], j["as"], j["hs"])):
            self.pro[t] = self.pro.get(t, 0.0) + f
            self.contra[t] = self.contra.get(t, 0.0) + c
            self.n[t] = self.n.get(t, 0) + 1
        self.soma_casa += j["hs"]
        self.soma_fora += j["as"]
        self.jogos += 1

    def media_time(self):
        return (self.soma_casa + self.soma_fora) / (2 * self.jogos)

    def mando(self):
        return math.sqrt(self.soma_casa / self.soma_fora)

    def forca(self, t, usar_forca):
        if not usar_forca or self.n.get(t, 0) == 0:
            return 1.0, 1.0
        m, n = self.media_time(), self.n[t]
        ataque = (self.pro[t] + K_ENCOLHIMENTO * m) / ((n + K_ENCOLHIMENTO) * m)
        defesa = (self.contra[t] + K_ENCOLHIMENTO * m) / ((n + K_ENCOLHIMENTO) * m)
        return ataque, defesa

    def multiplicadores(self, casa, fora, usar_forca):
        nivel = self.media_time() / CHUTES_POR_TIME_SIM_NEUTRO
        mando = self.mando()
        ac, dc = self.forca(casa, usar_forca)
        af, df = self.forca(fora, usar_forca)
        return (ac * nivel * mando, dc), (af * nivel / mando, df)


def ler(caminho):
    out = []
    for r in csv.DictReader(open(caminho)):
        j = {"id": int(r["id"]), "season": r["season"], "date": r["date"], "home": int(r["home"]), "away": int(r["away"]),
             "hg": int(r["hg"]), "ag": int(r["ag"]), "hs": float(r["hs"]), "as": float(r["as"])}
        for k in ("dc_h", "dc_d", "dc_a", "dc_over", "o_h", "o_d", "o_a", "o_over", "o_under"):
            j[k] = float(r[k]) if r[k] != "" else None
        out.append(j)
    out.sort(key=lambda j: (j["date"], j["id"]))
    return out


def devig(odds):
    inv = [1.0 / o for o in odds]
    t = sum(inv)
    return [x / t for x in inv]


def perda_1x2(p, res):                      # res: 0 casa, 1 empate, 2 fora
    return -math.log(max(p[res], 1e-12)), sum((p[i] - (1.0 if i == res else 0.0)) ** 2 for i in range(3))


def perda_ou(po, over):
    po = min(max(po, 1e-12), 1 - 1e-12)
    return -math.log(po if over else 1 - po), (po - (1.0 if over else 0.0)) ** 2


def bootstrap_ic(dif, B=5000, semente=7):
    rng = random.Random(semente)
    n = len(dif)
    medias = sorted(sum(dif[rng.randrange(n)] for _ in range(n)) / n for _ in range(B))
    return sum(dif) / n, medias[int(0.025 * B)], medias[int(0.975 * B)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--temporada", default="2025")
    ap.add_argument("--sims", type=int, default=1000)
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--limite", type=int, default=0, help="só os N primeiros jogos de teste (depuração)")
    ap.add_argument("--saida", default="resultado_backtest_simulador.json")
    a = ap.parse_args()

    jogos = ler(a.csv)
    hist, tarefas, meta = Historia(), [], {}
    teste = 0
    for j in jogos:
        if j["season"] == a.temporada and hist.jogos >= 100 and (not a.limite or teste < a.limite):
            teste += 1
            for variante, usar in (("forca", True), ("neutro", False)):
                mc, mf = hist.multiplicadores(j["home"], j["away"], usar)
                tarefas.append(((j["id"], variante), mc, mf, a.sims, j["id"] * 10 + (1 if usar else 2)))
            meta[j["id"]] = j
        hist.add(j)

    print(f"{teste} jogos de teste, {len(tarefas)} simulações de {a.sims} partidas, {a.workers} processos", flush=True)
    # progresso gravado a cada simulação concluída: se o processo morrer (contêiner reiniciado), a próxima execução retoma daqui
    parcial = a.saida + ".parcial.jsonl"
    resultados = {}
    if os.path.exists(parcial):
        for linha in open(parcial):
            jid, variante, r = json.loads(linha)
            resultados[(jid, variante)] = r
        print(f"retomando: {len(resultados)} simulações já prontas", flush=True)
    args = [(t[0], t[1], t[2], t[3], t[4]) for t in tarefas if t[0] not in resultados]
    with mp.Pool(a.workers, initializer=_iniciar) as pool, open(parcial, "a") as arq:
        for i, (jid, r) in enumerate(pool.imap_unordered(_simular_jogo, args, chunksize=2), 1):
            resultados[jid] = r
            arq.write(json.dumps([jid[0], jid[1], r]) + "\n")
            arq.flush()
            if i % 50 == 0:
                print(f"  {i}/{len(args)}", flush=True)

    linhas = []
    for jid, j in meta.items():
        res = 0 if j["hg"] > j["ag"] else (1 if j["hg"] == j["ag"] else 2)
        over = (j["hg"] + j["ag"]) >= 3
        lin = {"id": jid, "data": j["date"], "res": res, "over": over}
        for v in ("forca", "neutro"):
            r = resultados[(jid, v)]
            n = r["n"]
            lin[v] = {"p1x2": [(r["h"] + 1) / (n + 3), (r["d"] + 1) / (n + 3), (r["a"] + 1) / (n + 3)], "pover": (r["over"] + 1) / (n + 2),
                      "gols": r["gols_casa"] + r["gols_fora"], "chutes": r["chutes_casa"] + r["chutes_fora"]}
        if j["dc_h"] is not None:
            lin["dc"] = {"p1x2": [j["dc_h"], j["dc_d"], j["dc_a"]], "pover": j["dc_over"]}
        if j["o_h"] is not None and j["o_over"] is not None:
            lin["mercado"] = {"p1x2": devig([j["o_h"], j["o_d"], j["o_a"]]), "pover": devig([j["o_over"], j["o_under"]])[0]}
        linhas.append(lin)

    comuns = [l for l in linhas if "dc" in l and "mercado" in l]
    modelos = ["forca", "neutro", "dc", "mercado"]
    resumo = {"jogos_teste": len(linhas), "jogos_comuns": len(comuns), "sims_por_jogo": a.sims}
    perdas = {m: {"ll": [], "br": [], "ll_ou": [], "br_ou": []} for m in modelos}
    for l in comuns:
        for m in modelos:
            ll, br = perda_1x2(l[m]["p1x2"], l["res"])
            lo, bo = perda_ou(l[m]["pover"], l["over"])
            for k, v in (("ll", ll), ("br", br), ("ll_ou", lo), ("br_ou", bo)):
                perdas[m][k].append(v)
    n = len(comuns)
    resumo["medias"] = {m: {k: sum(v) / n for k, v in perdas[m].items()} for m in modelos}
    resumo["diferencas_pareadas"] = {}
    for par in (("forca", "dc"), ("forca", "mercado"), ("forca", "neutro"), ("dc", "mercado"), ("neutro", "dc")):
        for k in ("ll", "br", "ll_ou", "br_ou"):
            dif = [x - y for x, y in zip(perdas[par[0]][k], perdas[par[1]][k])]
            m, lo, hi = bootstrap_ic(dif)
            resumo["diferencas_pareadas"][f"{par[0]}-{par[1]}:{k}"] = {"media": m, "ic95": [lo, hi]}
    reais = [(l["over"], meta[l["id"]]) for l in linhas]
    resumo["diagnostico"] = {
        "gols_reais_por_jogo": sum(j["hg"] + j["ag"] for _, j in reais) / len(reais),
        "gols_sim_forca": sum(l["forca"]["gols"] for l in linhas) / len(linhas),
        "chutes_reais_por_jogo": sum(j["hs"] + j["as"] for _, j in reais) / len(reais),
        "chutes_sim_forca": sum(l["forca"]["chutes"] for l in linhas) / len(linhas),
        "frac_over_real": sum(1 for o, _ in reais if o) / len(reais),
        "frac_over_sim_forca": sum(l["forca"]["pover"] for l in linhas) / len(linhas),
    }
    json.dump({"resumo": resumo, "jogos": linhas}, open(a.saida, "w"), ensure_ascii=False)
    print(json.dumps(resumo, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
