#!/usr/bin/env python3
"""Sinal do top-N de xG (previsto e real) contra resultado e gols.

Pergunta do usuário (25/09): somar só os N jogadores com maior xG do time
(top-1..5) prevê melhor o resultado que o total do time? Mesmo espírito do
achado de xA (top-2/3 bate o time inteiro — ver CONTEXTO_PROJETO.md).

Duas fontes, na MESMA amostra de partidas:
  * previsto — `player_match_walkforward.lambda_xg_jogo` (`previsto`, clip ≥0),
    walk-forward (pré-jogo). ⚠️ A tabela só tem jogadores que entraram em campo
    (ver validar_informacao_nova_lambda.py) — o recorte já usa um pouco de
    informação pós-jogo.
  * real — `match_player_stats_fotmob.xg` (pós-jogo; nulo = 0 chutes; só
    partidas com cobertura de xG). Só DESCRITIVO: não serve pra prever.

Saída: correlação da diferença mandante − visitante com o saldo de gols e com
o resultado (V/E/D, Spearman); correlação do time com os próprios gols;
correlação PARCIAL com os gols dado o total do time (o top-N acrescenta algo
além do volume?); e, só pro previsto, teste fora da amostra (GLM de Poisson
dos gols do time, treino antes de --corte): top-N no lugar do total e total +
concentração ln(top-N/total), com IC95% bootstrap.

Uso:
    python arquivos_do_claude/validar_topn_xg.py --cache-dir /tmp/topn

Variáveis de ambiente: SUPABASE_URL, SUPABASE_KEY (chave pública basta). Só leitura.
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd
from scipy import stats
from scipy.optimize import minimize

sys.path.insert(0, os.path.dirname(__file__))
from validar_assistencia_jogador_walkforward import Rest, obter_env  # noqa: E402
from validar_totais_vs_mercado import _cache  # noqa: E402

CORTE_PADRAO = "2025-06-01"
NS = (1, 2, 3, 4, 5)
N_BOOT = 2000
SEED = 42


def carregar(cache_dir):
    rest = None

    def cliente():
        nonlocal rest
        rest = rest or Rest(obter_env("SUPABASE_URL"), obter_env("SUPABASE_KEY"))
        return rest

    jogos = _cache(cache_dir, "matches_completo", lambda: cliente().baixar(
        "matches", "id,match_date,league_id,home_team_id,away_team_id,status,home_goals,away_goals", {}, 4))
    wf = _cache(cache_dir, "wf_xg_jogador", lambda: cliente().baixar(
        "player_match_walkforward", "id,match_id,team_id,player_id,fonte_titular,lambda_xg_jogo",
        {"lambda_xg_jogo": "not.is.null"}, 24))
    st = _cache(cache_dir, "stats_xg_jogador", lambda: cliente().baixar(
        "match_player_stats_fotmob", "id,match_id,team_id,player_id,xg,minutes_played,is_goalkeeper",
        {"minutes_played": "gt.0"}, 24))
    return jogos, wf, st


def top_n(df: pd.DataFrame, col: str) -> pd.DataFrame:
    """Por time-partida: total e soma dos N maiores valores de `col`."""
    d = df.sort_values(["match_id", "team_id", col], ascending=[True, True, False]).copy()
    d["rk"] = d.groupby(["match_id", "team_id"]).cumcount() + 1
    out = d.groupby(["match_id", "team_id"])[col].sum().rename("total").to_frame()
    for n in NS:
        out[f"top{n}"] = d[d["rk"] <= n].groupby(["match_id", "team_id"])[col].sum()
    return out


def painel(jogos: pd.DataFrame, T: pd.DataFrame) -> pd.DataFrame:
    base = pd.concat([
        jogos.assign(team_id=jogos["home_team_id"], adv=jogos["away_team_id"], g=jogos["home_goals"], ga=jogos["away_goals"], casa=1),
        jogos.assign(team_id=jogos["away_team_id"], adv=jogos["home_team_id"], g=jogos["away_goals"], ga=jogos["home_goals"], casa=0),
    ])[["id", "team_id", "adv", "g", "ga", "casa", "dt"]].rename(columns={"id": "match_id"})
    adv = T.reset_index().rename(columns={"team_id": "adv", **{c: c + "_adv" for c in T.columns}})
    return base.merge(T.reset_index(), on=["match_id", "team_id"]).merge(adv, on=["match_id", "adv"])


def correlacoes(nome: str, D: pd.DataFrame) -> None:
    cols = ["total"] + [f"top{n}" for n in NS]
    h = D[D["casa"] == 1]
    saldo = (h["g"] - h["ga"]).values
    print(f"\n===== {nome}: {h['match_id'].nunique()} partidas / {len(D)} time-partida")
    print("  métrica  | corr(diff, saldo) | corr(diff, V/E/D) | corr(time, gols) | corr parcial c/ gols dado o total")
    X = np.column_stack([np.ones(len(D)), D["total"]])
    res_g = D["g"] - X @ np.linalg.lstsq(X, D["g"], rcond=None)[0]
    for c in cols:
        dif = (h[c] - h[c + "_adv"]).values
        parcial = "   —"
        if c != "total":
            res_c = D[c] - X @ np.linalg.lstsq(X, D[c], rcond=None)[0]
            parcial = f"{stats.pearsonr(res_g, res_c)[0]:+.4f}"
        print(f"  {c:8s} | {stats.pearsonr(dif, saldo)[0]:+.4f}           | {stats.spearmanr(dif, np.sign(saldo))[0]:+.4f}           "
              f"| {stats.pearsonr(D[c], D['g'])[0]:+.4f}          | {parcial}")


def ajustar(df, cols):
    X = np.column_stack([np.ones(len(df))] + [df[c] for c in cols])
    y = df["g"].values
    f = lambda b: np.sum(np.exp(X @ b) - y * (X @ b))  # noqa: E731
    g = lambda b: X.T @ (np.exp(X @ b) - y)  # noqa: E731
    H = lambda b: (X * np.exp(X @ b)[:, None]).T @ X  # noqa: E731
    return minimize(f, np.zeros(X.shape[1]), jac=g, hess=H, method="Newton-CG").x


def nll(df, cols, b):
    X = np.column_stack([np.ones(len(df))] + [df[c] for c in cols])
    return -stats.poisson.logpmf(df["g"].values, np.exp(X @ b))


def fora_da_amostra(D: pd.DataFrame, corte: pd.Timestamp, rng) -> None:
    D = D.copy()
    for c in ["total"] + [f"top{n}" for n in NS]:
        D[f"l_{c}"] = np.log(D[c].clip(lower=0.02))
        D[f"l_{c}_adv"] = np.log(D[c + "_adv"].clip(lower=0.02))
    for n in NS:
        D[f"conc{n}"] = np.log((D[f"top{n}"] / D["total"]).clip(lower=0.01))
    tr, te = D[D["dt"] < corte], D[D["dt"] >= corte]
    print(f"\n===== FORA DA AMOSTRA (previsto): treino {tr['match_id'].nunique()} / teste {te['match_id'].nunique()} partidas — NLL Poisson dos gols do time")
    base = ["l_total", "l_total_adv", "casa"]
    nb = nll(te, base, ajustar(tr, base))
    print(f"  total do time (base): NLL={nb.mean():.5f}")
    for n in NS:
        for rot, cols in ((f"top{n} no lugar do total", [f"l_top{n}", f"l_top{n}_adv", "casa"]),
                          (f"total + concentração top{n}", base + [f"conc{n}"])):
            b = ajustar(tr, cols)
            d = nll(te, cols, b) - nb
            bs = [d[rng.integers(0, len(d), len(d))].mean() for _ in range(N_BOOT)]
            extra = f" β_conc={b[-1]:+.3f}" if "conc" in rot else ""
            print(f"  {rot:30s} Δ={d.mean():+.5f} [{np.percentile(bs, 2.5):+.5f},{np.percentile(bs, 97.5):+.5f}]{extra}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corte", default=CORTE_PADRAO)
    parser.add_argument("--cache-dir", help="pasta pra guardar/reaproveitar os downloads (.pkl)")
    args = parser.parse_args()

    jogos, wf, st = carregar(args.cache_dir)
    jogos = jogos[jogos["status"] == "finished"].dropna(subset=["home_goals", "away_goals"]).copy()
    jogos["dt"] = pd.to_datetime(jogos["match_date"], format="ISO8601", utc=True).dt.tz_localize(None)

    prev = wf[wf["fonte_titular"] == "previsto"].assign(v=lambda d: d["lambda_xg_jogo"].clip(lower=0))
    st = st.dropna(subset=["team_id"])
    com_xg = st.groupby("match_id")["xg"].apply(lambda s: s.notna().any())
    real = st[st["match_id"].isin(com_xg[com_xg].index)].assign(v=lambda d: d["xg"].fillna(0.0))

    P, R = painel(jogos, top_n(prev, "v")), painel(jogos, top_n(real, "v"))
    comum = set(P["match_id"]) & set(R["match_id"])
    P, R = P[P["match_id"].isin(comum)], R[R["match_id"].isin(comum)]
    correlacoes("PREVISTO (walk-forward, pré-jogo — ver ⚠️ no cabeçalho)", P)
    correlacoes("REAL (xG pós-jogo, só descritivo)", R)
    fora_da_amostra(P, pd.Timestamp(args.corte), np.random.default_rng(SEED))


if __name__ == "__main__":
    main()
