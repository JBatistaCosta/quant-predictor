#!/usr/bin/env python3
"""Impacto dos desfalques (regulares fora da lista do jogo) no xG e nos GOLS da partida, e se a
QUALIDADE dos ausentes (Elo do jogador, xG+xA, ações defensivas, goleiro) muda o efeito além da contagem.

Resultados de 02/10/2026 em CONTEXTO_PROJETO.md (34.628 time x jogo): cada regular fora custa ~0,048 de saldo
de xG e ~0,056 de saldo de gols; com a qualidade no modelo a contagem vira ~0 e o R2 sobe só ~0,002.

DESENHO (só leitura, nada é gravado):
  - Uma linha por (jogo, time) com xG a favor/contra e gols a favor/contra; `f` = regulares do time fora da lista,
    `f_op` = do adversário (`desfalques_lista.py`: "regular" usa só jogos ANTERIORES).
  - Controles: mando, diferença de Elo (`team_elo_history`), xG a favor e contra nos 10 jogos anteriores do time e
    do adversário (só passado) e dummies de liga. Mínimo OLS com erro-padrão agrupado por time.
  - Qualidade dos ausentes: para cada regular ausente, MÉDIA POR JOGO dos 10 jogos anteriores do time (zero quando
    não jogou) de xG+xA e desarmes+interceptações; Elo do jogador (`player_rating_history`, último valor antes do
    jogo, relativo à média dos regulares do time); goleiro (>= 50% das aparições como goleiro). Somados por time e
    jogo e padronizados (coeficiente = efeito de +1 desvio-padrão).

Variáveis de ambiente: SUPABASE_URL, SUPABASE_KEY (chave pública basta). Uso:
    python scripts/analisar_desfalques_xg_gols.py --cache-dir /tmp/cache_xg_gols   # ~10 min na 1ª vez
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import desfalques_lista as dl
from analisar_desfalques_lista_do_jogo import baixar

JANELA = dl.JANELA_REGULAR
MIN_TIT = dl.MIN_TITULARIDADES
QUALIDADES = ["ab_ataque", "ab_def", "ab_gk", "ab_elo"]
CONTROLES = ["casa", "elo_diff", "pro10", "con10", "pro10_op", "con10_op"]


# --------------------------------------------------------------------------- dados
def carregar(url: str, chave: str, cache_dir: str | None) -> dict:
    def com_cache(nome, fn):
        caminho = None
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)
            caminho = os.path.join(cache_dir, nome + ".pkl")
            if os.path.exists(caminho):
                return pd.read_pickle(caminho)
        df = fn()
        if caminho:
            df.to_pickle(caminho)
        return df

    return {
        "L": com_cache("lineup", lambda: baixar(url, chave, "match_lineup_fotmob", "id,match_id,team_id,fotmob_player_id,is_starter")),
        "S": com_cache("jog_stats", lambda: baixar(url, chave, "match_player_stats_fotmob",
                       "id,match_id,team_id,fotmob_player_id,player_id,is_goalkeeper,minutes_played,rating,xg,xa,xgot,total_shots,tackles,interceptions")),
        "R": com_cache("jog_elo", lambda: baixar(url, chave, "player_rating_history", "id,player_id,match_id,rating_antes")),
        "X": com_cache("xg_time", lambda: baixar(url, chave, "match_stats_fotmob", "id,match_id,team_id,xg").dropna(subset=["xg"])),
        "E": com_cache("elo_hist", lambda: baixar(url, chave, "team_elo_history", "id,match_id,team_id,rating_antes")),
        "M": com_cache("partidas", lambda: baixar(url, chave, "matches",
                       "id,match_date,home_team_id,away_team_id,home_goals,away_goals,league_id,status").query("status == 'finished'")),
    }


# --------------------------------------------------------------------------- tabela time x jogo
def tabela_time_jogo(M: pd.DataFrame, X: pd.DataFrame, E: pd.DataFrame, desf: pd.DataFrame) -> pd.DataFrame:
    """Uma linha por (jogo, time): `xg`/`xg_c`, `gols`/`gols_c`, `f`/`f_op` (regulares fora do time e do adversário),
    `elo_diff`, `casa` e a forma passada (`pro10`/`con10` do time, `_op` do adversário). Linhas sem algum desses
    campos (início de histórico, sem xG, sem Elo) são descartadas. `M`: id, match_date, home_team_id, away_team_id,
    home_goals, away_goals, league_id; `X`: match_id, team_id, xg; `E`: match_id, team_id, rating_antes;
    `desf`: saída de `desfalques_lista.desfalques_por_partida`."""
    H = M[["id", "match_date", "home_team_id", "away_team_id", "league_id", "home_goals", "away_goals"]].rename(columns={"id": "match_id"})
    casa = H.rename(columns={"home_team_id": "team_id", "away_team_id": "opp_id", "home_goals": "gols", "away_goals": "gols_c"}).assign(casa=1)
    fora = H.rename(columns={"away_team_id": "team_id", "home_team_id": "opp_id", "away_goals": "gols", "home_goals": "gols_c"}).assign(casa=0)
    T = pd.concat([casa, fora], ignore_index=True)
    T["d"] = pd.to_datetime(T.match_date.astype(str).str[:10])
    X = X.drop_duplicates(["match_id", "team_id"])[["match_id", "team_id", "xg"]]
    T = T.merge(X, on=["match_id", "team_id"]).merge(X.rename(columns={"team_id": "opp_id", "xg": "xg_c"}), on=["match_id", "opp_id"])
    d = desf[["match_id", "team_id", "n_fora"]]
    T = T.merge(d.rename(columns={"n_fora": "f"}), on=["match_id", "team_id"], how="left")
    T = T.merge(d.rename(columns={"team_id": "opp_id", "n_fora": "f_op"}), on=["match_id", "opp_id"], how="left")
    e = E.drop_duplicates(["match_id", "team_id"])[["match_id", "team_id", "rating_antes"]]
    T = T.merge(e, on=["match_id", "team_id"], how="left").merge(e.rename(columns={"team_id": "opp_id", "rating_antes": "elo_op"}), on=["match_id", "opp_id"], how="left")
    T["elo_diff"] = T.rating_antes - T.elo_op
    T = T.sort_values(["team_id", "d", "match_id"])
    g = T.groupby("team_id")
    T["pro10"] = g.xg.transform(lambda x: x.shift(1).rolling(10, min_periods=5).mean())
    T["con10"] = g.xg_c.transform(lambda x: x.shift(1).rolling(10, min_periods=5).mean())
    op = T[["match_id", "team_id", "pro10", "con10"]].rename(columns={"team_id": "opp_id", "pro10": "pro10_op", "con10": "con10_op"})
    T = T.merge(op, on=["match_id", "opp_id"])
    T = T.dropna(subset=["f", "f_op", "pro10", "con10", "pro10_op", "con10_op", "elo_diff", "gols", "gols_c"]).reset_index(drop=True)
    T["dif_xg"] = T.xg - T.xg_c
    T["dif_gols"] = T.gols - T.gols_c
    return T


# --------------------------------------------------------------------------- qualidade dos ausentes
def qualidade_ausentes(L: pd.DataFrame, S: pd.DataFrame, R: pd.DataFrame, M: pd.DataFrame) -> pd.DataFrame:
    """Por (jogo, time) com >= `MIN_REGULARES` regulares: `n_reg`, `f` e a soma, sobre os regulares AUSENTES, de
    `ab_ataque` (xG+xA médio por jogo), `ab_def` (desarmes+interceptações médios), `ab_gk` (algum goleiro regular
    ausente) e `ab_elo` (Elo do jogador menos a média dos regulares do time). Médias dos `JANELA` jogos anteriores
    do time (zero quando não jogou). `L`: match_id, team_id, fotmob_player_id, is_starter; `S`: estatísticas por
    jogador; `R`: player_id, match_id, rating_antes; `M`: id, match_date."""
    p = M.rename(columns={"id": "match_id"})[["match_id", "match_date"]].assign(d=lambda x: pd.to_datetime(x.match_date.astype(str).str[:10]))
    S = S.drop_duplicates(["match_id", "team_id", "fotmob_player_id"]).copy()
    R = R.drop_duplicates(["player_id", "match_id"])[["player_id", "match_id", "rating_antes"]].rename(columns={"rating_antes": "pelo"})
    S = S.merge(R, on=["player_id", "match_id"], how="left")
    for c in ["xg", "xa", "tackles", "interceptions"]:
        S[c] = pd.to_numeric(S[c], errors="coerce")
    S["ataque"] = S.xg.fillna(0) + S.xa.fillna(0)
    S["defa"] = S.tackles.fillna(0) + S.interceptions.fillna(0)
    L = (L.dropna(subset=["fotmob_player_id", "is_starter"]).drop_duplicates(["match_id", "team_id", "fotmob_player_id"])
         .merge(p[["match_id", "d"]], on="match_id"))
    L["st"] = L.is_starter.astype(int)
    TM = L[["team_id", "match_id", "d"]].drop_duplicates().sort_values(["team_id", "d", "match_id"]).reset_index(drop=True)
    TM["tn"] = TM.groupby("team_id").cumcount()
    L = L.merge(TM, on=["team_id", "match_id", "d"])
    L = L.merge(S[["match_id", "team_id", "fotmob_player_id", "is_goalkeeper", "ataque", "defa", "pelo"]],
                on=["match_id", "team_id", "fotmob_player_id"], how="left")
    chaves = ["fotmob_player_id", "team_id"]
    tit = L[L.st == 1][chaves + ["tn"]]
    X = pd.concat([tit.assign(tn_alvo=tit.tn + o)[chaves + ["tn_alvo"]] for o in range(1, JANELA + 1)])
    reg = X.groupby(chaves + ["tn_alvo"]).size().rename("n_tit").reset_index()
    reg = reg[reg.n_tit >= MIN_TIT]
    reg = reg.merge(L[chaves + ["tn"]].assign(na=1).rename(columns={"tn": "tn_alvo"}), on=chaves + ["tn_alvo"], how="left")
    reg["fora"] = reg.na.isna().astype(int)
    reg = reg.merge(TM[["team_id", "tn", "match_id"]].rename(columns={"tn": "tn_alvo"}), on=["team_id", "tn_alvo"])
    vals = ["ataque", "defa"]
    V = L[chaves + ["tn"] + vals].copy()
    V[vals] = V[vals].fillna(0)
    media = pd.concat([V.assign(tn_alvo=V.tn + o)[chaves + ["tn_alvo"] + vals] for o in range(1, JANELA + 1)]).groupby(chaves + ["tn_alvo"])[vals].sum() / JANELA
    reg = reg.merge(media.reset_index(), on=chaves + ["tn_alvo"], how="left")
    gk = L.groupby(chaves).is_goalkeeper.apply(lambda s: (s == True).mean()).rename("gkp").reset_index()  # noqa: E712
    reg = reg.merge(gk, on=chaves, how="left")
    reg["gk"] = (reg.gkp >= 0.5).astype(int)
    E = L.dropna(subset=["pelo"])[chaves + ["tn", "pelo"]].rename(columns={"tn": "tn_e"}).sort_values("tn_e")
    reg = reg.sort_values("tn_alvo").assign(tn_alvo_m1=lambda d: d.tn_alvo - 1)
    reg = pd.merge_asof(reg, E, left_on="tn_alvo_m1", right_on="tn_e", by=chaves, direction="backward")
    reg["pelo_rel"] = reg.pelo - reg.groupby(["match_id", "team_id"]).pelo.transform("mean")
    aus = reg[reg.fora == 1].groupby(["match_id", "team_id"]).agg(
        f=("fora", "size"), ab_ataque=("ataque", "sum"), ab_def=("defa", "sum"), ab_gk=("gk", "max"), ab_elo=("pelo_rel", "sum")).reset_index()
    A = reg.groupby(["match_id", "team_id"]).agg(n_reg=("fora", "size")).reset_index().merge(aus, on=["match_id", "team_id"], how="left")
    for c in ["f"] + QUALIDADES:
        A[c] = A[c].fillna(0)
    return A[A.n_reg >= dl.MIN_REGULARES].reset_index(drop=True)


# --------------------------------------------------------------------------- regressão
def ols_agrupado(df: pd.DataFrame, y: str, cols: list[str], grupos: str = "team_id", dummies: str | None = "league_id") -> dict:
    """OLS de `y` sobre `cols` (+ constante e dummies de `dummies`) com erro-padrão agrupado por `grupos`.
    Devolve {coluna: (coeficiente, erro_padrao)} e `"_r2"`."""
    partes = [pd.Series(1.0, index=df.index, name="const"), df[cols].astype(float)]
    if dummies:
        partes.append(pd.get_dummies(df[dummies], prefix="d", drop_first=True).astype(float))
    Z = pd.concat(partes, axis=1)
    Zv, yv = Z.values, df[y].astype(float).values
    inv = np.linalg.pinv(Zv.T @ Zv)
    beta = inv @ Zv.T @ yv
    e = yv - Zv @ beta
    S = pd.DataFrame(Zv * e[:, None]).groupby(df[grupos].values).sum().values
    se = np.sqrt(np.diag(inv @ (S.T @ S) @ inv))
    nomes = list(Z.columns)
    saida = {c: (float(beta[nomes.index(c)]), float(se[nomes.index(c)])) for c in cols}
    saida["_r2"] = float(1 - e.var() / yv.var())
    return saida


def padronizar(T: pd.DataFrame) -> pd.DataFrame:
    """Divide cada medida de qualidade (própria e do adversário) pelo seu desvio-padrão: sufixo `_z`."""
    for q in QUALIDADES:
        for s in ("_own", "_opp"):
            sd = T[q + s].std()
            T[q + s + "_z"] = T[q + s] / sd if sd and sd > 0 else 0.0
    return T


def juntar_qualidade(T: pd.DataFrame, A: pd.DataFrame) -> pd.DataFrame:
    """Anexa a qualidade dos ausentes do próprio time (`_own`) e do adversário (`_opp`) a `T`."""
    A = A.copy()
    own = A[["match_id", "team_id"] + QUALIDADES].rename(columns={q: q + "_own" for q in QUALIDADES})
    opp = A[["match_id", "team_id"] + QUALIDADES].rename(columns={"team_id": "opp_id", **{q: q + "_opp" for q in QUALIDADES}})
    T = T.merge(own, on=["match_id", "team_id"], how="inner").merge(opp, on=["match_id", "opp_id"], how="inner")
    return padronizar(T)


# --------------------------------------------------------------------------- relatório
DESFECHOS = [("xg", "xG criado"), ("xg_c", "xG sofrido"), ("dif_xg", "Saldo de xG"),
             ("gols", "Gols marcados"), ("gols_c", "Gols sofridos"), ("dif_gols", "Saldo de gols")]


def relatorio(T: pd.DataFrame, Tq: pd.DataFrame) -> None:
    print(f"\n=== Contagem de regulares fora ({len(T)} time x jogo, {T.match_id.nunique()} partidas) ===")
    for y, nome in DESFECHOS:
        o = ols_agrupado(T, y, ["f", "f_op"] + CONTROLES)
        print(f"{nome:15s} f {o['f'][0]:+.4f} ± {o['f'][1]:.4f} | adversário {o['f_op'][0]:+.4f} ± {o['f_op'][1]:.4f}")
    Zq = [q + s + "_z" for s in ("_own", "_opp") for q in QUALIDADES]
    print(f"\n=== Qualidade dos ausentes ({len(Tq)} time x jogo; efeito por +1 desvio-padrão) ===")
    for y, nome in DESFECHOS:
        o1 = ols_agrupado(Tq, y, ["f", "f_op"] + CONTROLES)
        o2 = ols_agrupado(Tq, y, ["f", "f_op"] + Zq + CONTROLES)
        print(f"\n{nome}: R2 contagem {o1['_r2']:.4f} -> contagem+qualidade {o2['_r2']:.4f} | f {o2['f'][0]:+.4f} ± {o2['f'][1]:.4f}")
        for q in QUALIDADES:
            a, b = o2[q + "_own_z"], o2[q + "_opp_z"]
            print(f"  {q:10s} próprio {a[0]:+.4f} ± {a[1]:.4f} (t={a[0] / a[1]:+.1f}) | adversário {b[0]:+.4f} ± {b[1]:.4f} (t={b[0] / b[1]:+.1f})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache-dir", default=None)
    args = ap.parse_args()
    url, chave = os.environ.get("SUPABASE_URL", ""), os.environ.get("SUPABASE_KEY", "")
    if not url or not chave:
        sys.exit("SUPABASE_URL e SUPABASE_KEY precisam estar definidos.")
    d = carregar(url, chave, args.cache_dir)
    desf = dl.desfalques_por_partida(d["L"][["match_id", "team_id", "fotmob_player_id", "is_starter"]], d["M"])
    T = tabela_time_jogo(d["M"], d["X"], d["E"], desf)
    A = qualidade_ausentes(d["L"], d["S"], d["R"], d["M"])
    relatorio(T, juntar_qualidade(T, A))


if __name__ == "__main__":
    main()
