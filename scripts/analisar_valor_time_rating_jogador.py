#!/usr/bin/env python3
"""Valor, PARA O TIME, do rating de jogador: a habilidade dos 11 titulares melhora a previsão do saldo de xG e de
gols além do Elo do time, da forma recente, do mando e dos desfalques?

Resultado de 03/10/2026 em CONTEXTO_PROJETO.md (34.608 time x jogo): sim, mas pouco -- o melhor acréscimo (Elo
médio dos titulares) reduz o erro quadrático em ~1,4% (xG) e ~1,1% (gols); criação + defesa valem cerca de metade
disso e quase nada por cima do Elo do jogador.

DESENHO (só leitura, nada é gravado):
  - Habilidade de cada jogador ANTES de cada jogo (só passado): criação (chances criadas/90), defesa (desarmes +
    interceptações/90) e nota geral, ajustadas por papel/mando/força do time (`analisar_rating_aspecto_jogador.py`),
    mais o Elo do jogador (`elo_cfg - 1500`, `analisar_rating_jogador.py`).
  - Por (jogo, time): média sobre os TITULARES de linha (goleiro fora; >= 8 titulares com dado), habilidade ausente
    (1ª aparição) conta 0. Padronizadas pelo desvio-padrão do próprio time (coeficiente = efeito de +1 dp).
  - Tabela time x jogo de `analisar_desfalques_xg_gols.tabela_time_jogo` (xG e gols a favor/contra, Elo, forma de xG,
    mando, regulares fora da lista). Modelos OLS ajustados antes de `--corte` (2025-01-01) e avaliados depois:
    R² fora da amostra e ganho pareado no erro quadrático contra a base (EP agrupado por time).
  - CUIDADOS: o XI só é conhecido ~1 h antes do jogo; mede saldo de xG/gols, NÃO edge contra o mercado.

Variáveis de ambiente: SUPABASE_URL, SUPABASE_KEY (chave pública basta). Uso:
    python scripts/analisar_valor_time_rating_jogador.py --cache-dir /tmp/cache_rating   # reaproveita o cache do outro script
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analisar_desfalques_xg_gols as ax
import analisar_rating_aspecto_jogador as aa
import analisar_rating_jogador as base
import desfalques_lista as dl
from analisar_desfalques_lista_do_jogo import baixar

MIN_TITULARES = 8
CORTE = aa.CORTE
FEATURES = ["cria", "defe", "nota", "eloj"]
CTL = ax.CONTROLES + ["f", "f_op"]
MODELOS = {
    "M1 base (Elo, forma, mando, desfalques)": CTL,
    "M2 + criação e defesa do XI": CTL + ["cria_own_z", "defe_own_z", "cria_op_z", "defe_op_z"],
    "M3 + nota geral do XI": CTL + ["nota_own_z", "nota_op_z"],
    "M4 + Elo médio dos jogadores do XI": CTL + ["eloj_own_z", "eloj_op_z"],
    "M5 + criação, defesa e nota": CTL + ["cria_own_z", "defe_own_z", "cria_op_z", "defe_op_z", "nota_own_z", "nota_op_z"],
    "M6 + Elo do XI + criação + defesa": CTL + ["eloj_own_z", "eloj_op_z", "cria_own_z", "defe_own_z", "cria_op_z", "defe_op_z"],
    "M7 + Elo do XI + nota geral": CTL + ["eloj_own_z", "eloj_op_z", "nota_own_z", "nota_op_z"],
}


# --------------------------------------------------------------------------- dados
def carregar_extra(url: str, chave: str, cache_dir: str | None) -> dict:
    """Tabelas que o outro script não baixa: partidas COM placar e xG por time e jogo."""
    def com_cache(nome, fn):
        caminho = os.path.join(cache_dir, nome + ".pkl") if cache_dir else None
        if caminho and os.path.exists(caminho):
            return pd.read_pickle(caminho)
        df = fn()
        if caminho:
            os.makedirs(cache_dir, exist_ok=True)
            df.to_pickle(caminho)
        return df

    return {
        "M": com_cache("partidas_gols", lambda: baixar(url, chave, "matches",
                       "id,match_date,home_team_id,away_team_id,home_goals,away_goals,league_id,status").query("status == 'finished'")),
        "X": com_cache("xg_time", lambda: baixar(url, chave, "match_stats_fotmob", "id,match_id,team_id,xg").dropna(subset=["xg"])),
    }


# --------------------------------------------------------------------------- features do XI
def habilidades_pre_jogo(S: pd.DataFrame) -> pd.DataFrame:
    """Acrescenta a `S` (saída de `aa.preparar_aspectos`) as colunas `sk_chances90`, `sk_def90`, `sk_nota90`
    (habilidade ANTES do jogo, NaN na 1ª aparição) e `elo_j` (Elo do jogador antes do jogo - 1500)."""
    S = S.copy()
    for ycol in ("chances90", "def90", "nota90"):
        _, res, ok = aa.contexto_esperado(S, ycol)
        y = S[ycol].values.astype(float)
        prior = float(np.nanmean(y[ok & (S.d < aa.CORTE).values]))
        S["sk_" + ycol] = aa.habilidade_passado(S.player_id.values, y, res, S.min90.values, ok, aa.PRIOR_K, prior)["habilidade"]
    S["elo_j"] = S.elo_cfg - 1500.0
    return S


def features_xi(S: pd.DataFrame, min_titulares: int = MIN_TITULARES) -> pd.DataFrame:
    """Média, por (jogo, time), da habilidade dos TITULARES (ausente = 0): `cria`, `defe`, `nota`, `eloj`, e `n_xi`.
    Times com menos de `min_titulares` titulares com dado ficam de fora."""
    xi = S[S.is_starter == True]  # noqa: E712
    agg = xi.groupby(["match_id", "team_id"]).agg(
        n_xi=("player_id", "size"), cria=("sk_chances90", lambda s: s.fillna(0).mean()), defe=("sk_def90", lambda s: s.fillna(0).mean()),
        nota=("sk_nota90", lambda s: s.fillna(0).mean()), eloj=("elo_j", "mean")).reset_index()
    return agg[agg.n_xi >= min_titulares].reset_index(drop=True)


def juntar_xi(T: pd.DataFrame, agg: pd.DataFrame) -> pd.DataFrame:
    """Anexa o XI do próprio time (`_own`) e do adversário (`_op`) à tabela time x jogo e padroniza (`_z`: +1 dp)."""
    own = agg.rename(columns={c: c + "_own" for c in FEATURES + ["n_xi"]})
    opp = agg.rename(columns={"team_id": "opp_id", **{c: c + "_op" for c in FEATURES + ["n_xi"]}})
    T = T.merge(own, on=["match_id", "team_id"]).merge(opp, on=["match_id", "opp_id"])
    for c in FEATURES:
        sd = T[c + "_own"].std()
        for s in ("_own", "_op"):
            T[c + s + "_z"] = T[c + s] / sd if sd and sd > 0 else 0.0
    return T


# --------------------------------------------------------------------------- avaliação
def ganho_pareado(e0: np.ndarray, e1: np.ndarray, grupos: np.ndarray) -> tuple[float, float]:
    """Média de (e0 - e1) (positivo = o modelo 1 erra menos) e erro-padrão agrupado por `grupos` (time)."""
    dd = np.asarray(e0, float) - np.asarray(e1, float)
    m = dd.mean()
    s = pd.Series(dd).groupby(grupos).sum()
    n = pd.Series(dd).groupby(grupos).count()
    return float(m), float(np.sqrt(((s - m * n) ** 2).sum()) / len(dd))


def avaliar_time(T: pd.DataFrame, y: str, modelos: dict = MODELOS, corte: str = CORTE) -> dict:
    """Por modelo: R² e RMSE fora da amostra (ajuste antes de `corte`) e ganho pareado no erro quadrático contra o 1º modelo."""
    lig = pd.get_dummies(T.league_id, prefix="l", drop_first=True).astype(float).values
    tr = (pd.to_datetime(T.d) < corte).values
    yy = T[y].values.astype(float)
    g = T.team_id.values[~tr]
    saida: dict = {}
    e_base = None
    for nome, cols in modelos.items():
        X = np.column_stack([np.ones(len(T)), T[cols].astype(float).values, lig])
        b = np.linalg.lstsq(X[tr], yy[tr], rcond=None)[0]
        e = (yy[~tr] - X[~tr] @ b) ** 2
        r2 = 1 - e.sum() / np.sum((yy[~tr] - yy[~tr].mean()) ** 2)
        if e_base is None:
            e_base = e
        ganho, ep = ganho_pareado(e_base, e, g)
        saida[nome] = {"r2": float(r2), "rmse": float(np.sqrt(e.mean())), "ganho": ganho, "ep": ep}
    saida["_n"] = {"treino": int(tr.sum()), "teste": int((~tr).sum())}
    return saida


def relatorio(T: pd.DataFrame, corte: str = CORTE) -> None:
    n = avaliar_time(T, "dif_xg", {k: v for k, v in list(MODELOS.items())[:1]}, corte)["_n"]
    print(f"treino {n['treino']} | teste {n['teste']} time x jogo (corte {corte})")
    for y, nome in (("dif_xg", "SALDO DE xG"), ("dif_gols", "SALDO DE GOLS")):
        r = avaliar_time(T, y, corte=corte)
        r0 = next(iter(r.values()))["r2"]
        print(f"\n=== {nome} (fora da amostra)")
        for k, v in r.items():
            if k == "_n":
                continue
            print(f"  {k:<42} R²={v['r2']:.4f} ({100 * (v['r2'] - r0):+.2f} pp) | RMSE {v['rmse']:.4f} | ganho pareado {v['ganho']:+.4f} ± {v['ep']:.4f}")
        o = ax.ols_agrupado(T, y, MODELOS["M5 + criação, defesa e nota"])
        print("  efeito de +1 dp do XI médio (amostra toda, EP agrupado por time):")
        for c in ["cria_own_z", "defe_own_z", "nota_own_z"]:
            b, se = o[c]
            print(f"    {c:<11} {b:+.4f} ± {se:.4f} (t={b / se:+.1f})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache-dir", default=None)
    ap.add_argument("--corte", default=CORTE)
    args = ap.parse_args()
    url, chave = os.environ.get("SUPABASE_URL", ""), os.environ.get("SUPABASE_KEY", "")
    if not url or not chave:
        sys.exit("SUPABASE_URL e SUPABASE_KEY precisam estar definidos.")
    d = base.carregar(url, chave, args.cache_dir)
    extra = carregar_extra(url, chave, args.cache_dir)
    S0, _ = base.preparar(d)
    agg = features_xi(habilidades_pre_jogo(aa.preparar_aspectos(S0, d["L"])))
    L = d["L"][["match_id", "team_id", "fotmob_player_id", "is_starter"]]
    desf = dl.desfalques_por_partida(L, extra["M"].rename(columns={"id": "match_id"})[["match_id", "match_date"]])
    T = ax.tabela_time_jogo(extra["M"], extra["X"], d["E"], desf)
    T = juntar_xi(T, agg)
    print(f"times-jogo com XI: {len(agg)} | linhas time x jogo: {len(T)} | partidas: {T.match_id.nunique()}")
    relatorio(T, args.corte)


if __name__ == "__main__":
    main()
