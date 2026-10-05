#!/usr/bin/env python3
"""Avalia o "pseudo-modelo de Elo" da calculadora (src/pages/AnaliseEvento.jsx, `runAlgorithm`): a diferença de Elo vira uma expectativa E = 1 / (1 + 10^(-d/400)) que
multiplica os gols esperados (lambda1 = base x E / 0,5; lambda2 = base x (1 - E) / 0,5), e o 1X2 sai de duas Poisson independentes. Pergunta do usuário: ele acerta quem vence,
mas erra os empates e é muito confiante?

Só diferença de Elo (sem xG, posse nem histórico de gols): `elod` dos CSVs do backtest (Elo global antes do jogo, sem mando). Variantes:
  * calculadora, peso do Elo 100% e 50% (padrão da tela é 50%), base de gols igual para os dois lados (média da liga);
  * mesma com a base de gols do mandante e do visitante da liga (inclui o mando nos gols);
  * logit ordenado de 3 parâmetros (a, h, t: `scripts/elo_xg_tres_vias.py`) ajustado nas temporadas 2022 e 2023 e avaliado em 2024 e 2025 -- referência bem calibrada.
Medidas (jogos de 2024 e 2025): acurácia do vencedor entre os jogos sem empate; acurácia de 3 resultados; probabilidade média de empate contra a frequência real; AUC do empate;
confiança (maior probabilidade) contra acerto; erro de calibração esperado (ECE) do favorito; log-loss e Brier, e (nos jogos com odds) contra o mercado e o Dixon-Coles.
Sem rede.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import os
import random

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit


def pmf_matriz(lh, la, kmax=12):
    k = np.arange(kmax + 1)
    lg = np.array([math.lgamma(x + 1) for x in k])
    ph = np.exp(-lh[:, None] + k[None, :] * np.log(lh[:, None]) - lg[None, :])
    pa = np.exp(-la[:, None] + k[None, :] * np.log(la[:, None]) - lg[None, :])
    m = ph[:, :, None] * pa[:, None, :]
    i = np.arange(kmax + 1)
    casa = np.triu(np.ones((kmax + 1, kmax + 1)), 1).T            # i > j
    fora = np.triu(np.ones((kmax + 1, kmax + 1)), 1)              # j > i
    emp = np.eye(kmax + 1)
    p1 = (m * casa).sum((1, 2))
    p3 = (m * fora).sum((1, 2))
    px = (m * emp).sum((1, 2))
    s = p1 + px + p3
    return np.stack([p1 / s, px / s, p3 / s], 1)


def logit_ordenado(d, a, h, t):
    x = (d + h) / 400 * math.log(10)
    pc, pf = expit(a * x - t), expit(-a * x - t)
    return np.stack([pc, 1 - pc - pf, pf], 1)


def auc(score, y):
    ordem = np.argsort(score)
    r = np.empty(len(score))
    r[ordem] = np.arange(1, len(score) + 1)
    n1 = y.sum()
    n0 = len(y) - n1
    return (r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)


def ic_media(x, B=3000, semente=3):
    rng = np.random.default_rng(semente)
    n = len(x)
    ms = np.sort([x[rng.integers(0, n, n)].mean() for _ in range(B)])
    return float(x.mean()), float(ms[int(0.025 * B)]), float(ms[int(0.975 * B)])


def medidas(nome, P, y, vencedor_mask):
    n = len(y)
    pred = P.argmax(1)
    conf = P.max(1)
    acerto = (pred == y)
    # acurácia do vencedor: entre os jogos sem empate, escolhe o lado com maior probabilidade (casa x fora)
    lado = np.where(P[:, 0] >= P[:, 2], 0, 2)
    acc_venc = float((lado[vencedor_mask] == y[vencedor_mask]).mean())
    ll = -np.log(np.clip(P[np.arange(n), y], 1e-9, 1))
    br = ((P - np.eye(3)[y]) ** 2).sum(1)
    emp = (y == 1).astype(int)
    # ECE do favorito (maior probabilidade), 10 faixas
    faixas = np.minimum((conf * 10).astype(int), 9)
    ece = sum(abs(acerto[faixas == b].mean() - conf[faixas == b].mean()) * (faixas == b).sum() for b in range(10) if (faixas == b).any()) / n
    return {"nome": nome, "n": n, "acc_vencedor_sem_empate": acc_venc, "acc_3": float(acerto.mean()), "p_empate_medio": float(P[:, 1].mean()), "freq_empate": float(emp.mean()),
            "palpite_empate": float((pred == 1).mean()), "auc_empate": float(auc(P[:, 1], emp)), "confianca_media": float(conf.mean()), "ece_favorito": float(ece),
            "p_max_acima_70": float((conf > 0.70).mean()), "acerto_se_p_max_acima_70": float(acerto[conf > 0.70].mean()) if (conf > 0.70).any() else None,
            "logloss": float(ll.mean()), "brier": float(br.mean())}, ll, br


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pasta", default="dados_referencia/backtest_simulador")
    ap.add_argument("--saida", default="")
    a = ap.parse_args()
    jogos = []
    for f in sorted(glob.glob(os.path.join(a.pasta, "*_2022_25.csv"))):
        liga = os.path.basename(f).replace("_2022_25.csv", "")
        for r in csv.DictReader(open(f)):
            if r["elod"] == "":
                continue
            hg, ag = int(r["hg"]), int(r["ag"])
            jogos.append({"liga": liga, "temp": r["season"], "id": int(r["id"]), "d": float(r["elod"]), "hg": hg, "ag": ag, "y": 0 if hg > ag else (1 if hg == ag else 2),
                          "dc": [float(r[k]) for k in ("dc_h", "dc_d", "dc_a")] if r["dc_h"] else None,
                          "mk": [float(r[k]) for k in ("o_h", "o_d", "o_a")] if r["o_h"] else None})
    treino = [j for j in jogos if j["temp"] in ("2022", "2023")]
    teste = [j for j in jogos if j["temp"] in ("2024", "2025")]
    print(f"{len(jogos)} jogos com Elo ({len(treino)} de treino em 2022-2023, {len(teste)} de teste em 2024-2025)\n")
    # base de gols por liga, só do treino
    base = {}
    for liga in {j["liga"] for j in jogos}:
        t = [j for j in treino if j["liga"] == liga]
        base[liga] = (sum(j["hg"] for j in t) / len(t), sum(j["ag"] for j in t) / len(t))
    d = np.array([j["d"] for j in teste])
    y = np.array([j["y"] for j in teste])
    venc = y != 1
    res, perdas = [], {}

    def calc(peso, usar_mando):
        E = 1 / (1 + 10 ** (-(d * peso) / 400))
        if usar_mando:
            bh = np.array([base[j["liga"]][0] for j in teste])
            ba = np.array([base[j["liga"]][1] for j in teste])
        else:
            bh = ba = np.array([(base[j["liga"]][0] + base[j["liga"]][1]) / 2 for j in teste])
        return pmf_matriz(np.maximum(0.1, bh * E / 0.5), np.maximum(0.1, ba * (1 - E) / 0.5))

    for nome, P in (("calculadora, peso Elo 100%", calc(1.0, False)), ("calculadora, peso Elo 50% (padrão da tela)", calc(0.5, False)),
                    ("calculadora 100% + base de mando", calc(1.0, True))):
        m, ll, br = medidas(nome, P, y, venc)
        res.append(m)
        perdas[nome] = (ll, br)
    # logit ordenado ajustado no treino
    dt = np.array([j["d"] for j in treino])
    yt = np.array([j["y"] for j in treino])
    def nll(p):
        P = logit_ordenado(dt, p[0], p[1], abs(p[2]))
        return -np.log(np.clip(P[np.arange(len(yt)), yt], 1e-9, 1)).sum()
    r = minimize(nll, [1.0, 60.0, 0.6], method="Nelder-Mead")
    pa, ph, pt = r.x[0], r.x[1], abs(r.x[2])
    print(f"logit ordenado ajustado em 2022-2023: a={pa:.3f}, h={ph:.1f} pontos, t={pt:.3f}\n")
    Pl = logit_ordenado(d, pa, ph, pt)
    m, ll, br = medidas("logit ordenado (calibrado no treino)", Pl, y, venc)
    res.append(m)
    perdas[m["nome"]] = (ll, br)
    # climatologia
    freq = np.bincount(yt, minlength=3) / len(yt)
    Pc = np.tile(freq, (len(y), 1))
    m, ll, br = medidas("climatologia (frequências do treino)", Pc, y, venc)
    res.append(m)
    perdas[m["nome"]] = (ll, br)

    print(f"{'modelo':45s} {'venc.':>6s} {'3 res.':>6s} {'P(emp)':>6s} {'emp.real':>8s} {'palp.emp':>8s} {'AUC emp':>7s} {'conf.':>6s} {'ECE':>6s} {'ll':>7s} {'Brier':>6s} {'>70%':>5s} {'acerto>70%':>10s}")
    for m in res:
        print(f"{m['nome']:45s} {m['acc_vencedor_sem_empate']:6.3f} {m['acc_3']:6.3f} {m['p_empate_medio']:6.3f} {m['freq_empate']:8.3f} {m['palpite_empate']:8.3f} {m['auc_empate']:7.3f} {m['confianca_media']:6.3f} {m['ece_favorito']:6.3f} {m['logloss']:7.4f} {m['brier']:6.4f} {m['p_max_acima_70']:5.2f} {(m['acerto_se_p_max_acima_70'] if m['acerto_se_p_max_acima_70'] is not None else float('nan')):10.3f}")
    # contra mercado e Dixon-Coles nos jogos de teste com ambos
    idx = [i for i, j in enumerate(teste) if j["dc"] and j["mk"]]
    print(f"\nNos {len(idx)} jogos com Dixon-Coles e odds (2025), diferença de log-loss do 1X2 (positivo = pior):")
    def devig(o):
        inv = [1 / x for x in o]
        s = sum(inv)
        return [x / s for x in inv]
    ll_mk = np.array([-math.log(devig(teste[i]["mk"])[teste[i]["y"]]) for i in idx])
    ll_dc = np.array([-math.log(teste[i]["dc"][teste[i]["y"]]) for i in idx])
    saida_ref = {}
    for nome, (ll, br) in perdas.items():
        sub = ll[idx]
        a1 = ic_media(sub - ll_mk)
        a2 = ic_media(sub - ll_dc)
        print(f"  {nome:45s} log-loss {sub.mean():.4f} | contra o mercado {a1[0]:+.4f} [{a1[1]:+.4f};{a1[2]:+.4f}] | contra o Dixon-Coles {a2[0]:+.4f} [{a2[1]:+.4f};{a2[2]:+.4f}]")
        saida_ref[nome] = {"logloss": float(sub.mean()), "contra_mercado": a1, "contra_dc": a2}
    print(f"  {'mercado':45s} log-loss {ll_mk.mean():.4f}\n  {'Dixon-Coles':45s} log-loss {ll_dc.mean():.4f}")
    # confiança por faixa do favorito para a calculadora 100% e o logit
    print("\nConfiabilidade do favorito (maior probabilidade): faixa | n | confiança média | acerto — calculadora 100% e logit ordenado")
    for nome, P in (("calculadora 100%", calc(1.0, False)), ("logit ordenado", Pl)):
        conf = P.max(1)
        acerto = (P.argmax(1) == y)
        print(f"  {nome}")
        for lo, hi in ((0, .4), (.4, .5), (.5, .6), (.6, .7), (.7, .8), (.8, 1.01)):
            mk = (conf >= lo) & (conf < hi)
            if mk.sum() >= 20:
                print(f"    {lo:.1f}-{min(hi, 1):.1f} | n={mk.sum():5d} | confiança {conf[mk].mean():.3f} | acerto {acerto[mk].mean():.3f}")
    if a.saida:
        json.dump({"parametros_logit": [pa, ph, pt], "modelos": res, "contra_referencias": saida_ref}, open(a.saida, "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
