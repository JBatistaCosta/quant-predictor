#!/usr/bin/env python3
"""Acurácia direta, placares exatos e mercados derivados (BTTS, total de gols, handicap) do simulador, de versões calibradas e dos modelos de referência, na temporada de teste.

Modelos (todos nos mesmos jogos da temporada de TESTE):
  * simulador: probabilidades tiradas da contagem de placares simulados (`placares`, gols de cada lado limitados a 7), das variantes escolhidas;
  * simulador calibrado: duas Poisson independentes com o lambda do simulador multiplicado por um nível c_casa, c_fora ajustado por Poisson na temporada de TREINO;
  * logit de Elo por xG (3 parâmetros, ajustado em 2022-2023): só 1X2;
  * Dixon-Coles e mercado (odds de fechamento sem margem): 1X2 e over/under 2,5, nos jogos com odds;
  * climatologia (frequências do treino) como piso.
Medidas: 1X2 -- acurácia de 3 resultados, acurácia do vencedor entre os jogos sem empate, fração de palpites de empate, log-loss; placar exato -- log-verossimilhança do placar real,
acerto do placar mais provável e entre os 3 mais prováveis; mercados derivados -- acurácia e Brier (over/under 0,5 a 4,5, ambos marcam e handicap -0,5/-1,5 do mandante), contra a frequência do treino.
Só há odds de 1X2 e de over/under 2,5 no banco; os demais mercados ficam sem referência de mercado. Dados: `resultado_backtest_simulador.json` (um por liga, com `placares`) e os CSVs do backtest. Sem rede.
"""

from __future__ import annotations

import argparse
import json
import math
import urllib.parse

import numpy as np
from scipy.optimize import minimize
from scipy.special import gammaln

import calibrar_lambdas_potencia as cal
from avaliar_mistura_elo_xg import _get, logit_ord

G = 8                                            # placares de 0 a 7 (7 = 7 ou mais)


def grade_de_contagem(placares, n=None):
    m = np.zeros((G, G))
    for x, y, c in placares:
        m[min(x, 7), min(y, 7)] += c
    return m / m.sum()


def grade_poisson(lh, la):
    k = np.arange(G)
    ph = np.exp(-lh + k * math.log(lh) - gammaln(k + 1))
    pa = np.exp(-la + k * math.log(la) - gammaln(k + 1))
    ph[-1] += 1 - ph.sum()
    pa[-1] += 1 - pa.sum()
    return np.outer(ph, pa)


def mercados(m):
    """Probabilidades derivadas da grade: 1X2, total de gols, ambos marcam, handicap -0,5 e -1,5 do mandante."""
    i, j = np.indices(m.shape)
    out = {"1x2": np.array([m[i > j].sum(), m[i == j].sum(), m[i < j].sum()]), "btts": m[(i > 0) & (j > 0)].sum(),
           "hcp_-0.5": m[i > j].sum(), "hcp_-1.5": m[(i - j) >= 2].sum()}
    for linha in (0.5, 1.5, 2.5, 3.5, 4.5):
        out[f"over_{linha}"] = m[(i + j) > linha].sum()
    return out


def baixar_cadastrado(nome, ids):
    """Previsões de um modelo cadastrado em `model_predictions`: grade 8x8 de placares, ambos marcam e over/under 0,5 a 4,5. {id: {"grade", "btts", "over_x"}}"""
    mercados_ = "placar_exato,btts," + ",".join(f"over_under_{l}" for l in ("0.5", "1.5", "2.5", "3.5", "4.5"))
    out = {}
    for k in range(0, len(ids), 40):
        filtro = ",".join(str(x) for x in ids[k:k + 40])
        off = 0
        while True:
            pag = _get(f"model_predictions?select=match_id,market,selection,probability&model_name=eq.{urllib.parse.quote(nome)}&market=in.({mercados_})&match_id=in.({filtro})&order=id.asc&limit=1000&offset={off}")
            for r in pag:
                d = out.setdefault(r["match_id"], {"grade": np.zeros((G, G))})
                p_ = float(r["probability"])
                if r["market"] == "placar_exato" and "-" in r["selection"]:
                    x, z = r["selection"].split("-")
                    if x.isdigit() and z.isdigit():
                        d["grade"][min(int(x), 7), min(int(z), 7)] += p_
                elif r["market"] == "btts" and r["selection"] == "yes":
                    d["btts"] = p_
                elif r["market"].startswith("over_under_") and r["selection"] == "over":
                    d["over_" + r["market"].split("_")[-1]] = p_
            if len(pag) < 1000:
                break
            off += 1000
    return out


def ic_dif(x, B=3000, semente=3):
    rng = np.random.default_rng(semente)
    n = len(x)
    ms = np.sort([x[rng.integers(0, n, n)].mean() for _ in range(B)])
    return float(x.mean()), float(ms[int(0.025 * B)]), float(ms[int(0.975 * B)])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--treino", nargs="+", required=True)
    ap.add_argument("--teste", nargs="+", required=True)
    ap.add_argument("--variantes", nargs="+", required=True)
    ap.add_argument("--pasta", default="dados_referencia/backtest_simulador")
    ap.add_argument("--cadastrado", default="", help="modelo de model_predictions para comparar placares e mercados (ex.: markov_multievento_v1); precisa de SUPABASE_URL e SUPABASE_KEY")
    ap.add_argument("--saida", default="")
    a = ap.parse_args()
    cj = cal.carregar_csv(a.pasta)
    # Elo por xG: logit ajustado em 2022-2023
    import csv, glob, os
    elo = {}
    for f in sorted(glob.glob(os.path.join(a.pasta, "*_2022_25.csv"))):
        for r in csv.DictReader(open(f)):
            if r.get("elox") not in (None, ""):
                elo[int(r["id"])] = (r["season"], float(r["elox"]))
    tr_ids = [(i, v[1]) for i, v in elo.items() if v[0] in ("2022", "2023")]
    yt = np.array([0 if cj[i]["hg"] > cj[i]["ag"] else (1 if cj[i]["hg"] == cj[i]["ag"] else 2) for i, _ in tr_ids])
    xt = np.array([x for _, x in tr_ids])

    def nll(p):
        P = logit_ord((xt + p[1]) / 400 * math.log(10), p[0], abs(p[2]))
        return -np.log(np.clip(P[np.arange(len(yt)), yt], 1e-9, 1)).sum()
    pa, ph, pt = minimize(nll, [1.0, 60.0, 0.6], method="Nelder-Mead").x
    pt = abs(pt)
    freq_tr = np.bincount(yt, minlength=3) / len(yt)

    saida = {}
    for var in a.variantes:
        tr = cal.carregar_jogos(a.treino, var, cj)
        brutos = {}
        for f in a.teste:
            for j in json.load(open(f))["jogos"]:
                v = j.get(var)
                if v and "placares" in v and j["id"] in cj:
                    brutos[j["id"]] = v
        ids = sorted(brutos)
        arr = lambda L, k: np.array([x[k] for x in L], float)
        # nível de gols (Poisson) ajustado no treino
        p_nivel = cal.ajustar(arr(tr, 1), arr(tr, 2), arr(tr, 3), arr(tr, 4), "nivel")
        c_h, c_a = math.exp(p_nivel[0]), math.exp(p_nivel[1])
        # frequências do treino para os mercados derivados
        def real(i):
            g = cj[i]
            return g["hg"], g["ag"]
        grades = {"simulador": {}, "simulador calibrado (nível)": {}}
        for i in ids:
            v = brutos[i]
            grades["simulador"][i] = grade_de_contagem(v["placares"])
            grades["simulador calibrado (nível)"][i] = grade_poisson(max(v["gols_casa"] * c_h, 0.05), max(v["gols_fora"] * c_a, 0.05))
        # alvo real
        hg = np.array([real(i)[0] for i in ids]); ag = np.array([real(i)[1] for i in ids])
        y = np.where(hg > ag, 0, np.where(hg == ag, 1, 2))
        alvos = {"btts": (hg > 0) & (ag > 0), "hcp_-0.5": hg > ag, "hcp_-1.5": (hg - ag) >= 2}
        for linha in (0.5, 1.5, 2.5, 3.5, 4.5):
            alvos[f"over_{linha}"] = (hg + ag) > linha
        tr_ids_v = [x[0] for x in tr]
        real_tr = [(cj[i]["hg"], cj[i]["ag"]) for i in tr_ids_v]
        hg_t, ag_t = np.array([r[0] for r in real_tr]), np.array([r[1] for r in real_tr])
        base = {"btts": ((hg_t > 0) & (ag_t > 0)).mean(), "hcp_-0.5": (hg_t > ag_t).mean(), "hcp_-1.5": ((hg_t - ag_t) >= 2).mean()}
        for linha in (0.5, 1.5, 2.5, 3.5, 4.5):
            base[f"over_{linha}"] = ((hg_t + ag_t) > linha).mean()
        placar_freq = {}
        for r in real_tr:
            placar_freq[(min(r[0], 7), min(r[1], 7))] = placar_freq.get((min(r[0], 7), min(r[1], 7)), 0) + 1
        placar_clim = np.zeros((G, G))
        for (x, z), c in placar_freq.items():
            placar_clim[x, z] = c
        placar_clim = (placar_clim + 0.5) / (placar_clim + 0.5).sum()
        grades["climatologia"] = {i: placar_clim for i in ids}

        print(f"\n=== {var}: {len(ids)} jogos de teste; nível c=({c_h:.3f}, {c_a:.3f}) ajustado em {len(tr)} jogos de treino ===")
        # ---------------- 1X2
        sel = [k for k, i in enumerate(ids) if cj[i].get("dc") and cj[i].get("mk")]
        P1 = {nome: np.array([mercados(g[i])["1x2"] for i in ids]) for nome, g in grades.items() if nome != "climatologia"}
        P1["logit Elo por xG"] = logit_ord((np.array([elo[i][1] for i in ids]) + ph) / 400 * math.log(10), pa, pt)
        P1["climatologia"] = np.tile(freq_tr, (len(ids), 1))
        P1_dc = np.array([cj[ids[k]]["dc"][0] for k in sel]); P1_mk = np.array([cj[ids[k]]["mk"][0] for k in sel])
        print("\n1X2 -- acurácia direta (todos os jogos de teste; 'com odds' = só os jogos com Dixon-Coles e odds)")
        print(f"  {'modelo':34s} {'3 res.':>7s} {'vencedor':>9s} {'% empate palpitado':>19s} {'log-loss':>9s}")
        res1 = {}

        def linha1(nome, P, y_, tam):
            lado = np.where(P[:, 0] >= P[:, 2], 0, 2)
            v = y_ != 1
            acc3 = (P.argmax(1) == y_).mean()
            accv = (lado[v] == y_[v]).mean()
            emp = (P.argmax(1) == 1).mean()
            ll = -np.log(np.clip(P[np.arange(len(y_)), y_], 1e-9, 1)).mean()
            print(f"  {nome:34s} {acc3:7.3f} {accv:9.3f} {emp:19.3f} {ll:9.4f}  {tam}")
            return {"acc3": float(acc3), "acc_vencedor": float(accv), "p_palpite_empate": float(emp), "logloss": float(ll)}
        for nome, P in P1.items():
            res1[nome] = linha1(nome, P, y, f"n={len(ids)}")
        ys = y[sel]
        print("  -- jogos com odds:")
        sub = {nome: P[sel] for nome, P in P1.items()}
        sub["Dixon-Coles"], sub["mercado"] = P1_dc, P1_mk
        for nome, P in sub.items():
            res1[nome + " [com odds]"] = linha1(nome, P, ys, f"n={len(sel)}")
        for ref in ("Dixon-Coles", "mercado"):
            for nome in ("simulador", "simulador calibrado (nível)", "logit Elo por xG"):
                d = (sub[nome].argmax(1) == ys).astype(float) - (sub[ref].argmax(1) == ys).astype(float)
                m_, lo, hi = ic_dif(d)
                print(f"  acurácia de 3 resultados, {nome} menos {ref}: {m_:+.3f} [{lo:+.3f};{hi:+.3f}]{'*' if lo > 0 or hi < 0 else ''}")
        # ---------------- placar exato
        print("\nPlacar exato (placares de 0 a 7 gols; log-verossimilhança média do placar real, acerto do mais provável e dos 3 mais prováveis)")
        print(f"  {'modelo':34s} {'logL':>8s} {'top-1':>7s} {'top-3':>7s}")
        res2 = {}
        for nome, g in grades.items():
            lls, t1, t3 = [], [], []
            for k, i in enumerate(ids):
                m = g[i]
                if nome == "simulador":
                    m = (m * 1000 + 0.5) / (1000 + 0.5 * G * G)
                real_c = (min(hg[k], 7), min(ag[k], 7))
                lls.append(math.log(max(m[real_c], 1e-12)))
                ordem = np.argsort(m.ravel())[::-1][:3]
                alvo = real_c[0] * G + real_c[1]
                t1.append(ordem[0] == alvo); t3.append(alvo in ordem)
            res2[nome] = {"logL": float(np.mean(lls)), "top1": float(np.mean(t1)), "top3": float(np.mean(t3))}
            print(f"  {nome:34s} {np.mean(lls):8.4f} {np.mean(t1):7.3f} {np.mean(t3):7.3f}")
        # ---------------- mercados derivados
        print("\nMercados derivados (acurácia de escolher o lado mais provável | Brier; referência = frequência do treino)")
        print(f"  {'mercado':10s} {'freq real':>9s} | " + " | ".join(f"{n[:22]:>22s}" for n in ("simulador", "simulador calibrado (nível)")) + f" | {'climatologia':>20s}")
        res3 = {}
        for mk, alvo in alvos.items():
            linha = f"  {mk:10s} {alvo.mean():9.3f} | "
            res3[mk] = {"freq_real": float(alvo.mean())}
            for nome in ("simulador", "simulador calibrado (nível)"):
                p = np.array([mercados(grades[nome][i])[mk] for i in ids])
                acc = ((p > 0.5) == alvo).mean(); br = ((p - alvo) ** 2).mean()
                res3[mk][nome] = {"acc": float(acc), "brier": float(br)}
                linha += f"{acc:8.3f} | {br:9.4f}       | "
            acc_c = ((base[mk] > 0.5) == alvo).mean(); br_c = ((base[mk] - alvo) ** 2).mean()
            res3[mk]["climatologia"] = {"acc": float(acc_c), "brier": float(br_c)}
            print(linha + f"{acc_c:8.3f} | {br_c:8.4f}")
        # over/under 2,5 com odds
        po25 = {n: np.array([mercados(grades[n][ids[k]])["over_2.5"] for k in sel]) for n in ("simulador", "simulador calibrado (nível)")}
        po25["Dixon-Coles"] = np.array([cj[ids[k]]["dc"][1] for k in sel]); po25["mercado"] = np.array([cj[ids[k]]["mk"][1] for k in sel])
        o = alvos["over_2.5"][sel]
        print(f"\nOver/under 2,5 nos {len(sel)} jogos com odds:")
        res4 = {}
        for n, p in po25.items():
            acc = ((p > 0.5) == o).mean(); ll = -np.log(np.clip(np.where(o, p, 1 - p), 1e-9, 1)).mean()
            res4[n] = {"acc": float(acc), "logloss": float(ll)}
            print(f"  {n:34s} acurácia {acc:.3f}  log-loss {ll:.4f}")
        if a.cadastrado:
            cad = baixar_cadastrado(a.cadastrado, ids)
            ok = [k for k, i in enumerate(ids) if i in cad and cad[i]["grade"].sum() > 0.5 and "btts" in cad[i]]
            print(f"\n{a.cadastrado} (previsões gravadas em lote; ver ressalva): {len(ok)} de {len(ids)} jogos com grade de placares, comparação nos mesmos jogos")
            print(f"  {'modelo':34s} {'logL placar':>11s} {'top-1':>7s} {'top-3':>7s} | {'BTTS acc':>8s} {'Brier':>7s} | {'O2.5 acc':>8s} {'Brier':>7s} | {'O3.5 Brier':>10s}")
            for nome, get in (("simulador calibrado (nível)", lambda i: grades["simulador calibrado (nível)"][i]), (a.cadastrado, lambda i: cad[i]["grade"] / cad[i]["grade"].sum())):
                lls, t1, t3, pb, p25, p35 = [], [], [], [], [], []
                for k in ok:
                    i = ids[k]
                    m = get(i)
                    rc = (min(hg[k], 7), min(ag[k], 7))
                    lls.append(math.log(max(m[rc], 1e-12)))
                    ordem = np.argsort(m.ravel())[::-1][:3]
                    t1.append(ordem[0] == rc[0] * G + rc[1]); t3.append(rc[0] * G + rc[1] in ordem)
                    mk_ = mercados(m)
                    pb.append(cad[i]["btts"] if nome == a.cadastrado else mk_["btts"])
                    p25.append(cad[i].get("over_2.5", mk_["over_2.5"]) if nome == a.cadastrado else mk_["over_2.5"])
                    p35.append(cad[i].get("over_3.5", mk_["over_3.5"]) if nome == a.cadastrado else mk_["over_3.5"])
                pb, p25, p35 = np.array(pb), np.array(p25), np.array(p35)
                ab, a25, a35 = alvos["btts"][ok], alvos["over_2.5"][ok], alvos["over_3.5"][ok]
                print(f"  {nome:34s} {np.mean(lls):11.4f} {np.mean(t1):7.3f} {np.mean(t3):7.3f} | {((pb > 0.5) == ab).mean():8.3f} {((pb - ab) ** 2).mean():7.4f} | {((p25 > 0.5) == a25).mean():8.3f} {((p25 - a25) ** 2).mean():7.4f} | {((p35 - a35) ** 2).mean():10.4f}")
        saida[var] = {"1x2": res1, "placar": res2, "mercados": res3, "over25_com_odds": res4, "nivel": [c_h, c_a], "jogos": len(ids), "jogos_com_odds": len(sel)}
    if a.saida:
        json.dump(saida, open(a.saida, "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
