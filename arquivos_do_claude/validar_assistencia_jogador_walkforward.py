#!/usr/bin/env python3
"""Validação walk-forward de P(jogador dá ≥1 assistência) a partir do xA previsto.

Frente A do mercado de assistências (PR #656): antes de gastar cota comprando
odds de "Player Assists 1+" (OddsPapi, marketId 10738), confirmar que a
probabilidade derivada do `player_match_walkforward.lambda_xa_jogo` bate
baselines ingênuos contra o resultado real (`match_player_stats_fotmob.assists`).

Probabilidade: P(≥1) = 1 − exp(−λ*), com λ* = exp(a)·λ^b calibrado por
Bernoulli com ligação cloglog (é exatamente a forma 1 − e^−λ) SÓ no treino
(partidas antes de --corte). Cada baseline recebe a MESMA calibração de 2
parâmetros, pra comparação justa:
  * assistências/90 da carreira (encolhido pra média, prior de 10 jogos)
  * assistências/90 dos últimos 10 jogos (prior de 3 jogos)
  * xA real/90 da carreira e dos últimos 10 (mesmos priors)
todos × minutos_esperados/90 e calculados só com partidas ANTERIORES.

Amostra: jogador que entrou em campo (minutes_played>0) — a aposta de
assistência é anulada se o jogador não joga —, sem goleiros. Roda as duas
fontes de escalação (`real` = confirmada, `previsto` = XI previsto).
IC95% por bootstrap de partidas inteiras (jogadores da mesma partida não são
independentes).

Resultado registrado em CONTEXTO_PROJETO.md (25/09): o modelo bate os 4
baselines com IC95% inteiro abaixo de zero nas duas fontes; calibrado,
fica bem ajustado por decil, mas o λ cru com escalação confirmada superestima
~10% (O/E 0,903) e o top 5% sai levemente otimista.

Uso:
    python arquivos_do_claude/validar_assistencia_jogador_walkforward.py
    python arquivos_do_claude/validar_assistencia_jogador_walkforward.py --cache-dir /tmp/assist

`--cache-dir` guarda (e reaproveita) os 3 downloads em pickle.

Variáveis de ambiente: SUPABASE_URL, SUPABASE_KEY (chave pública basta — as
tabelas do pipeline têm RLS de leitura pública). Só leitura.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import sys
import urllib.parse
import urllib.request

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import mannwhitneyu

CORTE_PADRAO = "2025-06-01"
N_BOOT = 2000
SEED = 42
PRIOR_CARREIRA = 10.0  # jogos-equivalentes de prior (encolhimento pra média)
PRIOR_ULTIMOS_10 = 3.0
PAGINA = 1000
MODELO = "modelo xA (λ_xa_jogo)"


# =============================================================================
# Download (PostgREST, paginação por id — nunca .select() sem paginar)
# =============================================================================
def obter_env(nome: str) -> str:
    valor = (os.environ.get(nome) or "").strip()
    if not valor:
        sys.exit(f"Configure {nome} antes de rodar.")
    return valor


class Rest:
    def __init__(self, url: str, chave: str):
        self.base = url.rstrip("/") + "/rest/v1"
        self.headers = {"apikey": chave, "Authorization": f"Bearer {chave}"}

    def get(self, tabela: str, params: dict) -> list[dict]:
        url = f"{self.base}/{tabela}?" + urllib.parse.urlencode(params, safe=",.()")
        erro = None
        for _ in range(5):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=self.headers), timeout=60) as r:
                    return json.load(r)
            except Exception as e:  # noqa: BLE001 — rede instável: tenta de novo
                erro = e
        raise erro

    def _faixa(self, tabela: str, cols: str, filtros: dict, lo: int, hi: int) -> list[dict]:
        linhas, cursor = [], lo - 1
        while True:
            pagina = self.get(tabela, {"select": cols, **filtros, "id": f"gt.{cursor}",
                                       "and": f"(id.lte.{hi})", "order": "id", "limit": PAGINA})
            linhas += pagina
            if len(pagina) < PAGINA:
                return linhas
            cursor = pagina[-1]["id"]

    def baixar(self, tabela: str, cols: str, filtros: dict, n_workers: int = 16) -> pd.DataFrame:
        """Divide o intervalo de id em fatias e pagina cada uma em paralelo."""
        lo = self.get(tabela, {"select": "id", **filtros, "order": "id.asc", "limit": 1})[0]["id"]
        hi = self.get(tabela, {"select": "id", **filtros, "order": "id.desc", "limit": 1})[0]["id"]
        passo = (hi - lo) // n_workers + 1
        fatias = [(lo + i * passo, min(lo + (i + 1) * passo - 1, hi)) for i in range(n_workers)]
        with cf.ThreadPoolExecutor(n_workers) as ex:
            partes = list(ex.map(lambda f: self._faixa(tabela, cols, filtros, *f), fatias))
        df = pd.DataFrame([r for p in partes for r in p])
        print(f"{tabela}: {len(df)} linhas", flush=True)
        return df


def carregar(cache_dir: str | None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    especs = {
        "matches": ("id,match_date", {}, 4),
        "player_match_walkforward": ("id,match_id,player_id,fonte_titular,lambda_xa_jogo,minutos_esperados,league_id",
                                     {"lambda_xa_jogo": "not.is.null"}, 24),
        "match_player_stats_fotmob": ("id,match_id,player_id,assists,minutes_played,xa,is_goalkeeper",
                                      {"minutes_played": "gt.0", "player_id": "not.is.null"}, 24),
    }
    rest, saida = None, []
    for tabela, (cols, filtros, workers) in especs.items():
        caminho = os.path.join(cache_dir, f"{tabela}.pkl") if cache_dir else None
        if caminho and os.path.exists(caminho):
            df = pd.read_pickle(caminho)
        else:
            rest = rest or Rest(obter_env("SUPABASE_URL"), obter_env("SUPABASE_KEY"))
            df = rest.baixar(tabela, cols, filtros, workers)
            if caminho:
                os.makedirs(cache_dir, exist_ok=True)
                df.to_pickle(caminho)
        saida.append(df)
    return tuple(saida)


# =============================================================================
# Núcleo puro
# =============================================================================
def historico_anterior(stats: pd.DataFrame) -> pd.DataFrame:
    """Somas de assistências/minutos/xA do jogador em partidas ANTERIORES
    (carreira toda e últimos 10), sem incluir a própria partida."""
    st = stats.sort_values(["player_id", "data", "match_id"]).drop_duplicates(["match_id", "player_id"]).copy()
    g = st.groupby("player_id")
    for curto, col in [("ast", "assists"), ("min", "minutes_played"), ("xa", "xa")]:
        st[f"{curto}_prev"] = g[col].cumsum() - st[col]
        st[f"{curto}_10"] = g[col].transform(lambda v: v.shift(1).rolling(10, min_periods=1).sum()).fillna(0)
    return st


def ajustar_cloglog(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """P = 1 − exp(−exp(a + b·x)), x = ln λ. Devolve (a, b)."""
    X = np.column_stack([np.ones_like(x), x])

    def nll(b):
        p = np.clip(1 - np.exp(-np.exp(np.clip(X @ b, -20, 5))), 1e-9, 1 - 1e-9)
        return -np.sum(y * np.log(p) + (1 - y) * np.log(1 - p))

    return minimize(nll, np.array([0.0, 1.0]), method="BFGS").x


def prob_cloglog(ab: np.ndarray, x: np.ndarray) -> np.ndarray:
    return np.clip(1 - np.exp(-np.exp(ab[0] + ab[1] * x)), 1e-6, 1 - 1e-6)


def log_loss(y: np.ndarray, p: np.ndarray) -> np.ndarray:
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def bootstrap_por_partida(diff: np.ndarray, partidas: np.ndarray) -> tuple[float, float]:
    """IC95% da média de `diff` reamostrando partidas inteiras."""
    s = pd.DataFrame({"d": diff, "g": partidas}).groupby("g")["d"].agg(["sum", "size"])
    soma, n = s["sum"].values, s["size"].values
    rng = np.random.default_rng(SEED)
    k = len(soma)
    amostras = [soma[i].sum() / n[i].sum() for i in (rng.integers(0, k, k) for _ in range(N_BOOT))]
    lo, hi = np.percentile(amostras, [2.5, 97.5])
    return float(lo), float(hi)


def candidatos(df: pd.DataFrame, mu_ast: float, mu_xa: float) -> dict[str, np.ndarray]:
    """λ esperado de assistências na partida, por previsão."""
    me = df["minutos_esperados"].clip(lower=1) / 90
    kc, k10 = PRIOR_CARREIRA, PRIOR_ULTIMOS_10
    return {
        MODELO: df["lambda_xa_jogo"].values,
        "base assist/90 carreira": ((df["ast_prev"] + kc * mu_ast) / (df["min_prev"] / 90 + kc) * me).values,
        "base assist/90 últimos 10": ((df["ast_10"] + k10 * mu_ast) / (df["min_10"] / 90 + k10) * me).values,
        "base xA/90 carreira": ((df["xa_prev"] + kc * mu_xa) / (df["min_prev"] / 90 + kc) * me).values,
        "base xA/90 últimos 10": ((df["xa_10"] + k10 * mu_xa) / (df["min_10"] / 90 + k10) * me).values,
    }


def avaliar_fonte(df: pd.DataFrame, st: pd.DataFrame, corte: pd.Timestamp, fonte: str) -> None:
    tr = (df["data"] < corte).values
    te = ~tr
    y = df["y"].values
    hist_tr = st[st["data"] < corte]
    mu_ast = df.loc[tr, "assists"].sum() / (df.loc[tr, "minutes_played"].sum() / 90)
    mu_xa = hist_tr["xa"].sum() / (hist_tr["minutes_played"].sum() / 90)
    print(f"\n===== fonte_titular={fonte}: treino {tr.sum()} / teste {te.sum()} jogador-partida; "
          f"taxa real de ≥1 assistência no teste = {y[te].mean():.4f}")

    probs, perdas = {}, {}
    for nome, lam in candidatos(df, mu_ast, mu_xa).items():
        lam = np.clip(lam, 1e-4, None)
        x = np.log(lam)
        p_cru = np.clip(1 - np.exp(-lam), 1e-6, 1 - 1e-6)
        ab = ajustar_cloglog(x[tr], y[tr])
        p = prob_cloglog(ab, x)
        probs[nome], perdas[nome] = p, log_loss(y[te], p[te])
        print(f"  {nome:28s} cru: LL={log_loss(y[te], p_cru[te]).mean():.5f} O/E={y[te].sum() / p_cru[te].sum():.3f} | "
              f"calibrado (a={ab[0]:+.3f}, b={ab[1]:.3f}): LL={perdas[nome].mean():.5f} "
              f"Brier={((p[te] - y[te]) ** 2).mean():.5f} O/E={y[te].sum() / p[te].sum():.3f}")

    c = y[tr].mean()
    print(f"  probabilidade constante (taxa do treino {c:.4f}): LL={log_loss(y[te], np.full(te.sum(), c)).mean():.5f}")

    partidas = df.loc[te, "match_id"].values
    ref = perdas[MODELO]
    print("  Δ log-loss modelo − baseline (IC95% bootstrap por partida; negativo = modelo melhor):")
    for nome in probs:
        if nome == MODELO:
            continue
        lo, hi = bootstrap_por_partida(ref - perdas[nome], partidas)
        print(f"    vs {nome:28s} Δ={(ref - perdas[nome]).mean():+.5f}  IC95%=[{lo:+.5f}, {hi:+.5f}]")

    p = probs[MODELO][te]
    yt = y[te]
    auc = mannwhitneyu(p[yt == 1], p[yt == 0]).statistic / ((yt == 1).sum() * (yt == 0).sum())
    dec = pd.qcut(p, 10, labels=False, duplicates="drop")
    t = pd.DataFrame({"p": p, "y": yt, "d": dec}).groupby("d").agg(p=("p", "mean"), y=("y", "mean"))
    print(f"  AUC={auc:.3f} | decis (modelo calibrado): "
          + " | ".join(f"d{i + 1} p={r.p:.3f} obs={r.y:.3f}" for i, r in t.iterrows()))
    for q in (0.95, 0.99):
        topo = p >= np.quantile(p, q)
        print(f"  top {100 * (1 - q):.0f}%: p médio={p[topo].mean():.3f}  observado={yt[topo].mean():.3f}  n={topo.sum()}")

    melhor = min((n for n in probs if n != MODELO), key=lambda n: perdas[n].mean())
    print(f"  por liga (vs melhor baseline = {melhor}; * = IC95% inteiro abaixo de zero):")
    por_liga = pd.DataFrame({"liga": df.loc[te, "league_id"].values, "g": partidas, "d": ref - perdas[melhor]})
    for liga, s in por_liga.groupby("liga"):
        lo, hi = bootstrap_por_partida(s["d"].values, s["g"].values)
        print(f"    liga {liga:>4}: n={len(s):6d}  Δ={s['d'].mean():+.5f}  IC95%=[{lo:+.5f}, {hi:+.5f}]{'  *' if hi < 0 else ''}")


# =============================================================================
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corte", default=CORTE_PADRAO, help="treino = antes desta data; teste = a partir dela")
    parser.add_argument("--cache-dir", help="pasta pra guardar/reaproveitar os downloads (.pkl)")
    args = parser.parse_args()

    partidas, wf, stats = carregar(args.cache_dir)
    partidas = partidas.rename(columns={"id": "match_id"})
    partidas["data"] = pd.to_datetime(partidas["match_date"], format="ISO8601", utc=True).dt.tz_localize(None)
    stats = stats.merge(partidas[["match_id", "data"]], on="match_id")
    stats["assists"] = stats["assists"].fillna(0)
    stats["xa"] = stats["xa"].fillna(0.0)
    st = historico_anterior(stats)

    df = wf.merge(st[["match_id", "player_id", "data", "assists", "minutes_played", "is_goalkeeper",
                      "ast_prev", "min_prev", "xa_prev", "ast_10", "min_10", "xa_10"]], on=["match_id", "player_id"])
    df = df[~df["is_goalkeeper"].fillna(False).astype(bool)].copy()
    df["y"] = (df["assists"] >= 1).astype(float)

    corte = pd.Timestamp(args.corte)
    for fonte in ("real", "previsto"):
        avaliar_fonte(df[df["fonte_titular"] == fonte].reset_index(drop=True), st, corte, fonte)


if __name__ == "__main__":
    main()
