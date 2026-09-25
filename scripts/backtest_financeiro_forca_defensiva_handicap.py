#!/usr/bin/env python3
"""Backtest financeiro do mercado `handicap_-1.0` na especificação de
produção da PR #657 (offset `log(λ_xGOT)` + Defesa opponent-adjusted via
EWM, SEM `Ataque_Residuo`/mando -- decisão explícita do usuário de manter
a produção estabilizada, ver CONTEXTO_PROJETO.md 25/09) contra odds reais
de fechamento.

Não roda em produção nem em cron -- é uma rotina de VALIDAÇÃO financeira,
disparada manualmente (`python scripts/backtest_financeiro_forca_defensiva_
handicap.py`), no espírito de `backtest_kelly.py`, mas focada só neste
mercado/especificação (o único onde o teste de interação Ataque×Defesa de
25/09 não deu ganho OOS significativo, por isso ficou fora da
especificação estabilizada).

ACHADO JÁ REGISTRADO (25/09, ver CONTEXTO_PROJETO.md): a especificação
atual PERDE dinheiro de forma estatisticamente significativa (Yield IC95%
inteiramente negativo) -- o modelo é overconfident exatamente nas apostas
de maior edge aparente (teste de calibração Poisson-Binomial: apostas com
Kelly>0 têm z-score de -5 a -7, viés que PIORA quanto maior o edge). Não
implementar nenhuma estratégia de aposta real baseada nesta especificação
sem recalibrar antes. Este script existe pra reproduzir esse achado e
para ser reexecutado quando o modelo for recalibrado.

Metodologia:
  1. Reconstrói λ_xGOT por time-partida -- soma de `player_match_
     walkforward.lambda_xg_jogo` (`fonte_titular='previsto'`, clipado ≥0
     por jogador, mesma fonte walk-forward-safe usada na validação de
     Ataque_Residuo em 25/09), aproximação de `PlayerToTeamAggregator.
     agregar_xgot` (que soma `lambda_xg_jogo + delta_shooting` -- esta
     coluna não existe em `player_match_walkforward`, então fica de fora;
     simplificação deliberada, não um erro de leitura de schema).
  2. Reconstrói Defesa (`dados_historicos._calcular_forca_defensiva`,
     `shift=True` -- reaproveita a lógica de produção, não reimplementa)
     pro conjunto de teste (`matches.match_date >= --corte`,
     `status='finished'`).
  3. Aplica `pricing_pipeline.DEF_BETA_XGA`/`DEF_BETA_XA` (calibração real
     da PR #657) pra obter λ_home/λ_away finais.
  4. Roda a matriz de placares REAL (`distribuicoes.matriz_placares`, com
     ρ real de `model_match_estimates.params.rho`, `hibrido_gols_v1`) e
     extrai `handicap_-1.0` via `distribuicoes.mercados_de_gols`.
  5. Carrega odds reais de fechamento via `backtest_kelly.
     carregar_melhores_odds_fechamento_com_bookmaker` (reaproveitada, não
     duplicada -- já resolve `handicap_-1.0` -> `european_handicap_-1`, 3
     vias com push/empate tendo odd PRÓPRIA cotada -- diferente do
     handicap asiático, aqui nenhuma seleção tem mecânica de devolução de
     stake -- e já aplica o fix de snapshot da PR #619).
  6. Kelly fracionário PADRÃO por seleção (`f = (p*b-(1-p))/b`, aposta
     quando `f>0`) -- simulação de banca cronológica pra cada fração de
     Kelly em `--kelly-fracoes`, com Yield, Max Drawdown, Brier score (3
     classes) e PnL por faixa de edge, mais IC95% bootstrap (2000
     reamostragens) pro Yield geral e por seleção.

Variáveis de ambiente obrigatórias: SUPABASE_URL, SUPABASE_KEY (só
leitura -- este script nunca escreve no banco).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

import numpy as np
import pandas as pd
from supabase import Client, create_client

import backtest_kelly as bk
import dados_historicos as dh
import distribuicoes as dist
import pricing_pipeline as pp

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backtest_financeiro_forca_defensiva_handicap")

MERCADO = "handicap_-1.0"
LINHA_HANDICAP = -1.0
LAMBDA_MODEL_NAME = "hibrido_gols_v1"  # fonte do rho real (mesma da validacao de 25/09)
N_REAMOSTRAGENS_BOOTSTRAP = 2000
SEED = 42
EDGE_BUCKETS = [0.0, 0.05, 0.10, 0.15, 0.25, 5.0]
EDGE_BUCKET_LABELS = ["<5%", "5-10%", "10-15%", "15-25%", "25%+"]


# =============================================================================
# Setup
# =============================================================================
def obter_env_obrigatoria(nome: str) -> str:
    valor = (os.environ.get(nome) or "").strip()
    if not valor:
        logger.error("Variavel de ambiente obrigatoria ausente: %s", nome)
        sys.exit(1)
    return valor


def get_supabase_client() -> Client:
    return create_client(obter_env_obrigatoria("SUPABASE_URL"), obter_env_obrigatoria("SUPABASE_KEY"))


# =============================================================================
# Carregamento de dados
# =============================================================================
def carregar_matches_teste(supabase: Client, corte: str) -> pd.DataFrame:
    """Partidas finalizadas com data >= corte -- universo de teste OOS."""
    colunas = "id, home_team_id, away_team_id, home_goals, away_goals, league_id, match_date"

    def factory(lote, inicio, fim):
        return (
            supabase.table("matches")
            .select(colunas)
            .in_("league_id", lote)
            .eq("status", "finished")
            .gte("match_date", corte)
            .not_.is_("home_goals", "null")
            .not_.is_("away_goals", "null")
            .range(inicio, fim)
        )

    # league_id nao e um filtro natural de paginacao por lote de id, mas
    # matches nao tem tamanho suficiente pra estourar 1000 linhas isolado
    # por status+data -- pega direto sem o helper de lote de id.
    linhas: list[dict] = []
    inicio = 0
    tamanho_pagina = 1000
    while True:
        resp = (
            supabase.table("matches")
            .select(colunas)
            .eq("status", "finished")
            .gte("match_date", corte)
            .not_.is_("home_goals", "null")
            .not_.is_("away_goals", "null")
            .order("id")
            .range(inicio, inicio + tamanho_pagina - 1)
            .execute()
        )
        pagina = resp.data or []
        linhas.extend(pagina)
        if len(pagina) < tamanho_pagina:
            break
        inicio += tamanho_pagina
    df = pd.DataFrame(linhas)
    if df.empty:
        return df
    df["match_date"] = pd.to_datetime(df["match_date"], utc=True)
    return df.rename(columns={"id": "match_id"})


def carregar_lambda_xgot_previsto(supabase: Client, match_ids: list[int]) -> pd.DataFrame:
    """Soma de `lambda_xg_jogo` (clipado >=0) por (match_id, team_id),
    `fonte_titular='previsto'` -- aproximacao walk-forward-safe de
    `PlayerToTeamAggregator.agregar_xgot` (sem `delta_shooting`, coluna
    ausente em `player_match_walkforward`)."""
    linhas = dh._paginar_por_lotes_de_id(
        lambda lote, inicio, fim: (
            supabase.table("player_match_walkforward")
            .select("match_id, team_id, lambda_xg_jogo")
            .in_("match_id", lote)
            .eq("fonte_titular", "previsto")
            .not_.is_("lambda_xg_jogo", "null")
            .range(inicio, fim)
        ),
        match_ids,
        tamanho_lote=200,
    )
    if not linhas:
        return pd.DataFrame(columns=["match_id", "team_id", "lambda_xgot"])
    df = pd.DataFrame(linhas)
    df["lambda_xg_jogo"] = df["lambda_xg_jogo"].clip(lower=0.0)
    agregado = df.groupby(["match_id", "team_id"])["lambda_xg_jogo"].sum().reset_index()
    return agregado.rename(columns={"lambda_xg_jogo": "lambda_xgot"})


def carregar_rho_real(supabase: Client, match_ids: list[int]) -> pd.DataFrame:
    """rho real por partida (`model_match_estimates.params.rho`,
    `hibrido_gols_v1`) -- mesma fonte da validacao de 25/09."""
    linhas = dh._paginar_por_lotes_de_id(
        lambda lote, inicio, fim: (
            supabase.table("model_match_estimates")
            .select("match_id, params")
            .in_("match_id", lote)
            .eq("model_name", LAMBDA_MODEL_NAME)
            .range(inicio, fim)
        ),
        match_ids,
        tamanho_lote=300,
    )
    registros = [
        {"match_id": linha["match_id"], "rho": (linha.get("params") or {}).get("rho")}
        for linha in linhas
        if (linha.get("params") or {}).get("rho") is not None
    ]
    return pd.DataFrame(registros)


# =============================================================================
# Montagem do dataset final (lambda_home/away + rho + odds reais)
# =============================================================================
def montar_dataset(supabase: Client, corte: str) -> pd.DataFrame:
    matches = carregar_matches_teste(supabase, corte)
    if matches.empty:
        logger.warning("Nenhuma partida finalizada encontrada a partir de %s", corte)
        return pd.DataFrame()
    match_ids = matches["match_id"].astype(int).tolist()
    team_ids = sorted(set(matches["home_team_id"]) | set(matches["away_team_id"]))
    logger.info("Partidas de teste: %d | times: %d", len(matches), len(team_ids))

    logger.info("Reconstruindo defesa opponent-adjusted (EWM, walk-forward)...")
    painel = dh._carregar_serie_ofensiva_defensiva(supabase, team_ids)
    defesa_calc = dh._calcular_forca_defensiva(painel, shift=True)
    defesa_calc = defesa_calc[["match_id", "team_id", "def_residuo_xga", "def_residuo_xa"]]

    logger.info("Reconstruindo lambda_xGOT (walk-forward, previsto)...")
    lambda_xgot = carregar_lambda_xgot_previsto(supabase, match_ids)

    logger.info("Carregando rho real (hibrido_gols_v1)...")
    rho = carregar_rho_real(supabase, match_ids)

    long_ = pd.concat(
        [
            matches.assign(team_id=matches["home_team_id"], mando=1.0),
            matches.assign(team_id=matches["away_team_id"], mando=0.0),
        ]
    )[["match_id", "team_id", "mando"]]
    long_ = long_.merge(lambda_xgot, on=["match_id", "team_id"], how="inner")
    long_ = long_.merge(defesa_calc, on=["match_id", "team_id"], how="left")
    long_["def_residuo_xga"] = long_["def_residuo_xga"].fillna(0.0)
    long_["def_residuo_xa"] = long_["def_residuo_xa"].fillna(0.0)
    long_["lambda_final"] = long_["lambda_xgot"] * np.exp(
        pp.DEF_BETA_XGA * long_["def_residuo_xga"] + pp.DEF_BETA_XA * long_["def_residuo_xa"]
    )

    home = long_[long_["mando"] == 1.0][["match_id", "lambda_final"]].rename(columns={"lambda_final": "lam_home"})
    away = long_[long_["mando"] == 0.0][["match_id", "lambda_final"]].rename(columns={"lambda_final": "lam_away"})
    partidas = home.merge(away, on="match_id", how="inner")
    partidas = partidas.merge(matches, on="match_id", how="inner")
    partidas = partidas.merge(rho, on="match_id", how="inner")
    logger.info("Partidas com lambda_home + lambda_away + rho: %d", len(partidas))

    logger.info("Carregando odds reais de fechamento (%s)...", MERCADO)
    odds, _bookmaker = bk.carregar_melhores_odds_fechamento_com_bookmaker(supabase, match_ids, MERCADO)
    registros = []
    for _, row in partidas.iterrows():
        odd_partida = odds.get(int(row["match_id"]))
        if not odd_partida:
            continue
        odd_home, odd_away, odd_push = odd_partida.get("odd_home", 0.0), odd_partida.get("odd_away", 0.0), odd_partida.get("odd_push", 0.0)
        if odd_home <= 1.0 or odd_away <= 1.0 or odd_push <= 1.0:
            continue
        registros.append(
            dict(
                match_id=row["match_id"],
                match_date=row["match_date"],
                league_id=row["league_id"],
                lam_home=row["lam_home"],
                lam_away=row["lam_away"],
                rho=row["rho"],
                home_goals=row["home_goals"],
                away_goals=row["away_goals"],
                odds_home=odd_home,
                odds_away=odd_away,
                odds_push=odd_push,
            )
        )
    df = pd.DataFrame(registros)
    if df.empty:
        return df
    return df.sort_values("match_date").reset_index(drop=True)


# =============================================================================
# Probabilidades do modelo + EV/Kelly
# =============================================================================
def calcular_probabilidades_e_edge(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    p_home_l, p_away_l, p_push_l, resultado_l = [], [], [], []
    for _, row in df.iterrows():
        matriz = dist.matriz_placares(row["lam_home"], row["lam_away"], row["rho"])
        mercados = dist.mercados_de_gols(matriz, linhas_handicap=(LINHA_HANDICAP,))
        rotulo = dist.rotulo_linha(LINHA_HANDICAP)
        p_home_l.append(mercados[(f"handicap_{rotulo}", "home")])
        p_away_l.append(mercados[(f"handicap_{rotulo}", "away")])
        p_push_l.append(mercados.get((f"handicap_{rotulo}", "push"), 0.0))
        margem_real = (row["home_goals"] - row["away_goals"]) + LINHA_HANDICAP
        resultado_l.append("home" if margem_real > 0 else ("push" if margem_real == 0 else "away"))
    df["p_home"], df["p_away"], df["p_push"] = p_home_l, p_away_l, p_push_l
    df["resultado"] = resultado_l

    for lado, odds_col in [("home", "odds_home"), ("push", "odds_push"), ("away", "odds_away")]:
        p = df[f"p_{lado}"]
        b = df[odds_col] - 1.0
        df[f"ev_{lado}"] = p * df[odds_col] - 1.0
        df[f"f_{lado}"] = (p * b - (1.0 - p)) / b
    return df


# =============================================================================
# Simulacao de banca (Kelly fracionario)
# =============================================================================
def simular_carteira(df: pd.DataFrame, kelly_mult: float, bankroll0: float = 100.0) -> tuple[float, list[float], pd.DataFrame]:
    bankroll = bankroll0
    historico = [bankroll]
    apostas = []
    for _, row in df.iterrows():
        pre_bankroll = bankroll
        pnl_total = 0.0
        for lado, odds_col in [("home", "odds_home"), ("push", "odds_push"), ("away", "odds_away")]:
            f = row[f"f_{lado}"]
            if f <= 0:
                continue
            stake = pre_bankroll * kelly_mult * f
            pnl = stake * (row[odds_col] - 1.0) if row["resultado"] == lado else -stake
            pnl_total += pnl
            apostas.append(
                dict(
                    match_id=row["match_id"], match_date=row["match_date"], lado=lado,
                    stake=stake, pnl=pnl, odds=row[odds_col], ev=row[f"ev_{lado}"],
                    resultado=row["resultado"], vencedor=(row["resultado"] == lado),
                )
            )
        bankroll = pre_bankroll + pnl_total
        historico.append(bankroll)
    return bankroll, historico, pd.DataFrame(apostas)


def max_drawdown(historico: list[float]) -> float:
    hist = np.array(historico)
    pico = np.maximum.accumulate(hist)
    return float(((hist - pico) / pico).min())


def bootstrap_ic95_yield(stake: pd.Series, pnl: pd.Series, n_reamostragens: int = N_REAMOSTRAGENS_BOOTSTRAP, seed: int = SEED) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    stake_arr, pnl_arr = np.asarray(stake), np.asarray(pnl)
    n = len(stake_arr)
    if n == 0:
        return float("nan"), float("nan")
    idx_base = np.arange(n)
    yields = []
    for _ in range(n_reamostragens):
        idx = rng.choice(idx_base, size=n, replace=True)
        yields.append(pnl_arr[idx].sum() / stake_arr[idx].sum() * 100)
    return float(np.percentile(yields, 2.5)), float(np.percentile(yields, 97.5))


# =============================================================================
# Metricas de qualidade (Brier) e calibracao
# =============================================================================
def brier_score_3_classes(df: pd.DataFrame) -> tuple[float, float]:
    y_home = (df["resultado"] == "home").astype(float)
    y_away = (df["resultado"] == "away").astype(float)
    y_push = (df["resultado"] == "push").astype(float)
    brier = float(((df["p_home"] - y_home) ** 2 + (df["p_away"] - y_away) ** 2 + (df["p_push"] - y_push) ** 2).mean())
    p_naive = [y_home.mean(), y_away.mean(), y_push.mean()]
    brier_naive = float(
        ((p_naive[0] - y_home) ** 2 + (p_naive[1] - y_away) ** 2 + (p_naive[2] - y_push) ** 2).mean()
    )
    return brier, brier_naive


def teste_calibracao_poisson_binomial(df: pd.DataFrame) -> None:
    """Compara a soma das probabilidades PREVISTAS contra a contagem REAL
    de vitorias, dentro do subconjunto selecionado por edge positivo --
    aproximacao poisson-binomial (variancia = soma p*(1-p)). |z| grande
    indica que o modelo esta mal calibrado justamente onde ele "acha" que
    tem vantagem (achado central de 25/09)."""
    y = {"home": df["resultado"] == "home", "push": df["resultado"] == "push", "away": df["resultado"] == "away"}
    for lado in ["home", "push", "away"]:
        selecionadas = df[df[f"f_{lado}"] > 0]
        if selecionadas.empty:
            continue
        p = selecionadas[f"p_{lado}"]
        soma_p, var_p = p.sum(), (p * (1 - p)).sum()
        real = y[lado][selecionadas.index].sum()
        z = (real - soma_p) / np.sqrt(var_p) if var_p > 0 else float("nan")
        logger.info("  Calibracao %-5s (n=%4d, edge>0): esperado=%.1f real=%d z=%+.2f", lado, len(selecionadas), soma_p, real, z)


# =============================================================================
# Relatorio
# =============================================================================
def gerar_relatorio(df: pd.DataFrame, kelly_fracoes: list[float]) -> None:
    logger.info("=" * 78)
    logger.info("BACKTEST FINANCEIRO -- %s (n=%d partidas OOS)", MERCADO, len(df))
    logger.info("=" * 78)

    brier, brier_naive = brier_score_3_classes(df)
    logger.info("Brier score (3 classes): %.4f  (naive: %.4f)", brier, brier_naive)

    logger.info("\nCalibracao Poisson-Binomial (apostas com Kelly>0):")
    teste_calibracao_poisson_binomial(df)

    for kelly_mult in kelly_fracoes:
        bankroll_final, historico, apostas = simular_carteira(df, kelly_mult)
        if apostas.empty:
            logger.info("\nKelly %.3f: nenhuma aposta com edge positivo.", kelly_mult)
            continue
        turnover, pnl_total = apostas["stake"].sum(), apostas["pnl"].sum()
        yield_pct = pnl_total / turnover * 100
        mdd = max_drawdown(historico) * 100
        ic_lo, ic_hi = bootstrap_ic95_yield(apostas["stake"], apostas["pnl"])

        logger.info("\n--- Kelly fracionario %.3f ---", kelly_mult)
        logger.info("  Apostas: %d | Turnover: %.2f | PnL: %.2f", len(apostas), turnover, pnl_total)
        logger.info("  Yield: %.2f%%  IC95%%=[%.2f%%, %.2f%%]", yield_pct, ic_lo, ic_hi)
        logger.info("  Banca: 100.00 -> %.2f | Max Drawdown: %.2f%%", bankroll_final, mdd)

        for lado in ["home", "push", "away"]:
            sub = apostas[apostas["lado"] == lado]
            if sub.empty:
                continue
            yp = sub["pnl"].sum() / sub["stake"].sum() * 100
            lo, hi = bootstrap_ic95_yield(sub["stake"], sub["pnl"])
            logger.info("    lado %-5s (n=%4d): Yield=%.2f%%  IC95%%=[%.2f%%, %.2f%%]", lado, len(sub), yp, lo, hi)

        apostas["edge_bucket"] = pd.cut(apostas["ev"], bins=EDGE_BUCKETS, labels=EDGE_BUCKET_LABELS, include_lowest=True)
        resumo_edge = apostas.groupby("edge_bucket", observed=True).agg(
            n=("stake", "size"), turnover=("stake", "sum"), pnl=("pnl", "sum"), taxa_acerto=("vencedor", "mean")
        )
        resumo_edge["yield_pct"] = resumo_edge["pnl"] / resumo_edge["turnover"] * 100
        logger.info("  PnL por faixa de edge:\n%s", resumo_edge.to_string())


# =============================================================================
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corte", default="2025-06-01", help="Data de corte do conjunto de teste OOS (YYYY-MM-DD)")
    parser.add_argument(
        "--kelly-fracoes", default="0.125,0.25", help="Fracoes de Kelly a simular, separadas por virgula"
    )
    args = parser.parse_args()
    kelly_fracoes = [float(f) for f in args.kelly_fracoes.split(",")]

    supabase = get_supabase_client()
    df = montar_dataset(supabase, args.corte)
    if df.empty:
        logger.error("Dataset final vazio -- nada para avaliar (conferir cobertura de odds/rho/lambda no periodo).")
        sys.exit(1)
    df = calcular_probabilidades_e_edge(df)
    gerar_relatorio(df, kelly_fracoes)


if __name__ == "__main__":
    main()
