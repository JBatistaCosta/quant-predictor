#!/usr/bin/env python3
"""Desfalques inferidos pela LISTA DO JOGO (titulares + reservas, ~20 por time) -- proxy retroativo de
lesão/suspensão/condição física, sem depender da lista `unavailable` do FotMob (que só vale para o presente).

Fonte: `match_lineup_fotmob` guarda os convocados de CADA jogo (titulares `is_starter=true`, banco `false`).
Quem é regular e NÃO está na lista do jogo está indisponível por lesão, suspensão, rodízio, saída do clube...
(o motivo não é conhecido). Duas análises, ambas SÓ LEITURA:

  1. RETORNO DE AUSÊNCIA LONGA (nível do jogador): jogador regular que volta à lista depois de >= 8 jogos do
     time fora dos 20 e >= 120 dias. Mede titularidade, minutos e nota nos 6 primeiros jogos contra a linha de
     base do próprio jogador (média dos 10 jogos anteriores), e a chance de nova ausência.
  2. REGULARES FORA DA LISTA (nível do time): em cada jogo, regulares = jogadores com >= 5 titularidades nas
     10 partidas anteriores do time; "fora" = regulares que não estão na lista do jogo. Compara casa - fora
     com o resíduo do placar esperado pelo Elo por xG (`elo_xg_tres_vias`) e, onde há odds, com a Pinnacle
     (pré-fechamento e fechamento), com erro-padrão agrupado por time da casa.

ATENÇÃO ao que o dado permite: a lista do jogo só é conhecida ~60-75 min antes do apito; a Pinnacle de
FECHAMENTO já incorpora esse sinal (ver CONTEXTO_PROJETO.md, 02/10/2026). Transferência/empréstimo também
aparece como 'fora' (a variante `n_fora_ret`, que exige reaparecer em <= 30 jogos, é RETROSPECTIVA e serve só
de teto limpo, não de variável de previsão).

Variáveis de ambiente: SUPABASE_URL, SUPABASE_KEY (chave pública basta). Uso:
    python scripts/analisar_desfalques_lista_do_jogo.py --cache-dir /tmp/cache_lista   # ~10 min na 1ª vez
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import math
import os
import sys
import time
import urllib.request

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import elo_xg_tres_vias as modelo
from desfalques_lista import regulares_fora_por_partida  # noqa: F401  (a lógica mora em desfalques_lista.py; reexportada p/ testes)

EP_ELO_POR_PONTO = 0.00144   # d(escore esperado)/d(ponto de Elo) perto do equilíbrio


# --------------------------------------------------------------------------- download
def _get(url: str, chave: str, caminho: str):
    for t in range(5):
        try:
            req = urllib.request.Request(f"{url}/rest/v1/{caminho}", headers={"apikey": chave, "Authorization": "Bearer " + chave})
            return json.load(urllib.request.urlopen(req, timeout=90))
        except Exception:
            if t == 4:
                raise
            time.sleep(2 * (t + 1))


def baixar(url: str, chave: str, tabela: str, colunas: str, passo: int = 60000, workers: int = 6) -> pd.DataFrame:
    """Paginação por id (keyset) em faixas paralelas: sem OFFSET profundo, que estoura o timeout do PostgREST."""
    mx = _get(url, chave, f"{tabela}?select=id&order=id.desc&limit=1")[0]["id"]

    def faixa(lo: int, hi: int):
        out, ultimo = [], lo - 1
        while True:
            d = _get(url, chave, f"{tabela}?select={colunas}&id=gt.{ultimo}&id=lt.{hi}&order=id.asc&limit=1000")
            out += d
            if len(d) < 1000:
                return out
            ultimo = d[-1]["id"]

    faixas = [(lo, min(lo + passo, mx + 2)) for lo in range(0, mx + 1, passo)]
    linhas = []
    with cf.ThreadPoolExecutor(workers) as ex:
        for r in ex.map(lambda f: faixa(*f), faixas):
            linhas += r
    return pd.DataFrame(linhas)


def carregar(url: str, chave: str, cache_dir: str | None) -> dict:
    def com_cache(nome, fn):
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)
            p = os.path.join(cache_dir, nome + ".pkl")
            if os.path.exists(p):
                return pd.read_pickle(p)
        df = fn()
        if cache_dir:
            df.to_pickle(p)
        return df

    return {
        "L": com_cache("lineup", lambda: baixar(url, chave, "match_lineup_fotmob", "id,match_id,team_id,fotmob_player_id,is_starter")),
        "S": com_cache("stats", lambda: baixar(url, chave, "match_player_stats_fotmob", "id,match_id,team_id,fotmob_player_id,minutes_played,rating")),
        "E": com_cache("elo", lambda: baixar(url, chave, "team_elo_xg_history", "id,match_id,team_id,rating_antes")),
        "M": com_cache("partidas", lambda: baixar(url, chave, "matches",
                       "id,match_date,home_team_id,away_team_id,home_goals,away_goals,is_neutral,league_id,status").query("status == 'finished'")),
    }


# --------------------------------------------------------------------------- lista do jogo
def preparar_lista(L: pd.DataFrame, S: pd.DataFrame, M: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Uma linha por (jogador, time, jogo) em que o jogador esteve na lista, com ordem do jogo do time (`tn`),
    jogos do time fora da lista desde a aparição anterior (`missed`), dias (`gap`) e linhas de base."""
    M = (M.rename(columns={"id": "match_id"}) if "match_id" not in M else M).copy()
    M["d"] = pd.to_datetime(M["match_date"].str[:10])
    L = L.drop_duplicates(["match_id", "team_id", "fotmob_player_id"]).merge(M[["match_id", "d"]], on="match_id")
    L = L[L.fotmob_player_id.notna() & L.is_starter.notna()]
    S = S.drop_duplicates(["match_id", "team_id", "fotmob_player_id"])[["match_id", "team_id", "fotmob_player_id", "minutes_played", "rating"]]
    L = L.merge(S, on=["match_id", "team_id", "fotmob_player_id"], how="left")
    L["st"] = L.is_starter.astype(int)
    L["played"] = ((L.st == 1) | (L.minutes_played.fillna(0) > 0)).astype(int)
    TM = L[["team_id", "match_id", "d"]].drop_duplicates().sort_values(["team_id", "d", "match_id"]).reset_index(drop=True)
    TM["tn"] = TM.groupby("team_id").cumcount()
    L = L.merge(TM, on=["team_id", "match_id", "d"]).sort_values(["fotmob_player_id", "team_id", "d", "match_id"]).reset_index(drop=True)
    g = L.groupby(["fotmob_player_id", "team_id"])
    L["rn"] = g.cumcount()
    L["prev_tn"], L["prev_d"] = g.tn.shift(1), g.d.shift(1)
    L["missed"] = L.tn - L.prev_tn - 1
    L["gap"] = (L.d - L.prev_d).dt.days
    for c, nome in (("st", "bst"), ("minutes_played", "bmin"), ("rating", "brat")):
        L[nome] = g[c].transform(lambda x: x.shift(1).rolling(10, min_periods=5).mean())
    # regular: >= 15 aparições na lista dentro de 45 partidas do time
    L["c40"] = g.tn.transform(lambda x: x.rolling(15, min_periods=15).apply(lambda w: 1.0 if (w.iloc[-1] - w.iloc[0]) <= 45 else 0.0, raw=False))
    L["prev_reg"], L["prev_bst"] = g.c40.shift(1), g.bst.shift(1)
    return L, TM


def eventos_retorno(L: pd.DataFrame) -> pd.DataFrame:
    """Regular que volta à lista depois de >= 8 jogos do time fora dos 20 e >= 120 dias."""
    ev = L[(L.gap >= 120) & (L.missed >= 8) & (L.prev_reg == 1) & (L.prev_bst >= 0.4)].copy()
    ev["faixa"] = pd.cut(ev.gap, [119, 179, 269, 10000], labels=["120-179 d", "180-269 d", "270+ d"])
    return ev


def pos_retorno(L: pd.DataFrame, ev: pd.DataFrame, ks: int = 6) -> pd.DataFrame:
    keys = ["fotmob_player_id", "team_id"]
    base = ev[keys + ["rn", "faixa", "bst", "bmin", "brat"]].reset_index(drop=True)
    partes = []
    for k in range(ks):
        t = base.copy()
        t["rn_k"] = t.rn + k
        j = t.merge(L[keys + ["rn", "st", "minutes_played", "rating", "missed"]].rename(
            columns={"rn": "rn_k", "st": "st_k", "minutes_played": "min_k", "rating": "rat_k", "missed": "miss_k"}), on=keys + ["rn_k"], how="left")
        j["k"] = k + 1
        partes.append(j)
    P = pd.concat(partes)
    saida = []
    for (f, k), x in P.groupby(["faixa", "k"], observed=True):
        dm, dr = (x.min_k - x.bmin).dropna(), (x.rat_k - x.brat).dropna()
        saida.append({"faixa": f, "k": k, "n": int(x.st_k.notna().sum()), "pct_titular": 100 * x.st_k.mean(), "pct_titular_base": 100 * x.bst.mean(),
                      "d_min": dm.mean(), "ep_min": dm.std() / math.sqrt(max(len(dm), 1)), "d_nota": dr.mean(), "ep_nota": dr.std() / math.sqrt(max(len(dr), 1))})
    return pd.DataFrame(saida)


def nova_ausencia(L: pd.DataFrame, ev: pd.DataFrame) -> dict:
    """Chance de >= 4 jogos do time fora da lista nas 5 aparições seguintes: retornos vs regulares sem ausência longa."""
    g = L.groupby(["fotmob_player_id", "team_id"])
    sh = pd.concat([g.missed.shift(-i) for i in range(1, 6)], axis=1)
    L = L.assign(reabs=np.where(sh.notna().all(axis=1), (sh.max(axis=1) >= 4).astype(float), np.nan))
    controle = L[(L.missed <= 1) & (L.prev_reg == 1) & (L.prev_bst >= 0.4) & L.reabs.notna()]
    e2 = L.loc[ev.index][L.loc[ev.index].reabs.notna()]
    return {"controle": 100 * controle.reabs.mean(), "n_controle": int(len(controle)), "retornos": 100 * e2.reabs.mean(), "n_retornos": int(len(e2))}


# --------------------------------------------------------------------------- resíduo e regressão
def montar_jogos(agg: pd.DataFrame, M: pd.DataFrame, E: pd.DataFrame, desde: str = "2021-01-01", min_reg: int = 8) -> pd.DataFrame:
    """Um jogo por linha: lado casa/fora dos desfalques e o resíduo (escore real - esperado pelo Elo por xG, ratings de ANTES do jogo)."""
    M = M[M.home_goals.notna()].rename(columns={"id": "match_id"}).copy()
    M["d"] = pd.to_datetime(M["match_date"].str[:10])
    e = E[["match_id", "team_id", "rating_antes"]]
    J = (M.merge(e.rename(columns={"team_id": "home_team_id", "rating_antes": "rh"}), on=["match_id", "home_team_id"])
          .merge(e.rename(columns={"team_id": "away_team_id", "rating_antes": "ra"}), on=["match_id", "away_team_id"]))
    J = J[J.d >= desde].copy()
    P = modelo.probabilidades_1x2(J.rh.values, J.ra.values, J.is_neutral.fillna(False).astype(float).values)
    J["esp"] = P[:, 0] + 0.5 * P[:, 1]
    J["real"] = np.where(J.home_goals > J.away_goals, 1.0, np.where(J.home_goals == J.away_goals, 0.5, 0.0))
    J["res"] = J.real - J.esp
    cols = ["n_fora", "n_fora_ret", "peso_fora", "n_reg"]
    h = agg.rename(columns={"team_id": "home_team_id", **{c: c + "_h" for c in cols}})[["match_id", "home_team_id"] + [c + "_h" for c in cols]]
    a = agg.rename(columns={"team_id": "away_team_id", **{c: c + "_a" for c in cols}})[["match_id", "away_team_id"] + [c + "_a" for c in cols]]
    J = J.merge(h, on=["match_id", "home_team_id"]).merge(a, on=["match_id", "away_team_id"])
    J = J[(J.n_reg_h >= min_reg) & (J.n_reg_a >= min_reg)].copy()
    J["d_fora"] = (J.n_fora_h - J.n_fora_a).astype(float)
    return J


def regressao_agrupada(y: np.ndarray, x: np.ndarray, grupos: np.ndarray) -> dict:
    """y ~ a + b*z, z = x padronizado; erro-padrão agrupado (CR0) por `grupos`. b em unidades de y por 1 dp de x."""
    ok = ~(np.isnan(y) | np.isnan(x))
    y, x, grupos = y[ok], x[ok], grupos[ok]
    z = (x - x.mean()) / x.std()
    X = np.column_stack([np.ones(len(z)), z])
    b = np.linalg.lstsq(X, y, rcond=None)[0]
    u = y - X @ b
    bread, meat = np.linalg.inv(X.T @ X), np.zeros((2, 2))
    for gg in np.unique(grupos):
        s = X[grupos == gg].T @ u[grupos == gg]
        meat += np.outer(s, s)
    se = math.sqrt((bread @ meat @ bread)[1, 1])
    return {"n": int(len(y)), "beta": float(b[1]), "ep": se, "t": float(b[1] / se), "pts_elo_por_dp": float(b[1] / EP_ELO_POR_PONTO)}


def esperado_mercado(J: pd.DataFrame, odds: dict, snapshot: str) -> np.ndarray:
    """Escore esperado (vitória 1, empate 0,5) da Pinnacle desvigada (proporcional); NaN sem odds completas."""
    sel = ("home", "draw", "away")
    O = np.array([[odds.get((int(m), snapshot, s), np.nan) for s in sel] for m in J.match_id])
    ok = ~np.isnan(O).any(1) & (O > 1).all(1)
    q = 1.0 / np.where(ok[:, None], O, 3.0)
    p = q / q.sum(1, keepdims=True)
    return np.where(ok, p[:, 0] + 0.5 * p[:, 1], np.nan)


def baixar_odds_pinnacle(url: str, chave: str, match_ids: list[int]) -> dict:
    ids, out = sorted(set(int(i) for i in match_ids)), {}
    for ini in range(0, len(ids), 40):  # lotes pequenos: filtro único por bookmaker em toda a tabela estoura o timeout
        lote = ids[ini:ini + 40]
        linhas = _get(url, chave, "odds_market?select=id,match_id,selection,odds,snapshot&bookmaker=eq.pinnacle&market=eq.1X2"
                                  f"&snapshot=in.(pre_closing,closing)&match_id=in.({','.join(map(str, lote))})&order=id.asc&limit=1000")
        for r in linhas:
            out[(r["match_id"], r["snapshot"], r["selection"])] = float(r["odds"])
    return out


# --------------------------------------------------------------------------- execução
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cache-dir")
    ap.add_argument("--sem-odds", action="store_true", help="pula a comparação com a Pinnacle")
    args = ap.parse_args()
    url, chave = os.environ.get("SUPABASE_URL", "").strip(), os.environ.get("SUPABASE_KEY", "").strip()
    if not url or not chave:
        sys.exit("Defina SUPABASE_URL e SUPABASE_KEY.")
    D = carregar(url, chave, args.cache_dir)
    L, TM = preparar_lista(D["L"], D["S"], D["M"])
    print(f"{len(L)} linhas de lista | {L.match_id.nunique()} partidas | {L.team_id.nunique()} times")

    ev = eventos_retorno(L)
    print(f"\n== 1) Retorno à lista após >= 8 jogos do time fora dos 20 (>= 120 d): {len(ev)} eventos ==")
    print(pos_retorno(L, ev).round(3).to_string(index=False))
    na = nova_ausencia(L, ev)
    print(f"nova ausência (>= 4 jogos fora nas 5 aparições seguintes): retornos {na['retornos']:.1f}% (n={na['n_retornos']}) vs controle {na['controle']:.1f}% (n={na['n_controle']})")

    agg = regulares_fora_por_partida(L, TM)
    J = montar_jogos(agg, D["M"], D["E"])
    print(f"\n== 2) Regulares fora da lista do jogo: {len(J)} jogos; média de regulares fora por time {agg.n_fora.mean():.2f} ==")
    g = J.home_team_id.values
    for nome, x in (("regulares fora (contagem)", J.d_fora), ("fora que voltam em <= 30 jogos (retrospectivo)", J.n_fora_ret_h - J.n_fora_ret_a),
                    ("peso dos regulares fora", J.peso_fora_h - J.peso_fora_a)):
        r = regressao_agrupada(J.res.values, x.values.astype(float), g)
        print(f"  vs Elo xG | {nome:<46} β={r['beta']:+.4f} ± {r['ep']:.4f}  t={r['t']:+.2f}  ({r['pts_elo_por_dp']:+.1f} pts de Elo por dp)")
    print("  resíduo médio por diferença de regulares fora (casa - fora, capada em ±3):")
    print(J.groupby(J.d_fora.clip(-3, 3)).res.agg(["size", "mean", "sem"]).round(4).to_string())
    if not args.sem_odds:
        odds = baixar_odds_pinnacle(url, chave, J[J.d >= "2022-08-01"].match_id.tolist())
        for snap, rot in (("pre_closing", "Pinnacle pré-fechamento"), ("closing", "Pinnacle fechamento")):
            J["mk"] = esperado_mercado(J, odds, snap)
            x = J[J.mk.notna()]
            r = regressao_agrupada((x.real - x.mk).values, x.d_fora.values, x.home_team_id.values)
            print(f"  vs {rot:<24} n={r['n']:>5}  β={r['beta']:+.4f} ± {r['ep']:.4f}  t={r['t']:+.2f}  ({r['pts_elo_por_dp']:+.1f} pts de Elo por dp)")


if __name__ == "__main__":
    main()
