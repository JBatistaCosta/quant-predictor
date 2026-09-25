"""Backtest walk-forward do modulador de GSAx do goleiro (`pricing_pipeline.
py::PlayerToTeamAggregator.modular_por_goleiro`) -- responde a pergunta que
nunca foi testada antes (só existia uma checagem de ESTABILIDADE da métrica,
correlação por tamanho de janela, não de ACERTO real): usar `λ_gols_
adversario = λ_xGOT_time_adversario · (1 - gsax_rate_do_goleiro_rival)`
prevê gols sofridos melhor do que assumir goleiro NEUTRO (`λ_gols_adversario
= λ_xGOT_time_adversario`, comportamento anterior à feature GSAx existir)?

Contexto (Fase 7/Frente B do plano da sessão): a pipeline bottom-up de
agregação de jogador (`pricing_pipeline_v1`, que já usa GSAx) perde pro
modelo de time atual (`hibrido_gols_v1`) numa comparação pareada. Antes de
decidir se a agregação bottom-up é um caminho morto, é preciso saber se GSAx
-- um dos componentes menos testados dessa pipeline -- tem sinal real ou
está introduzindo ruído.

Achado que motivou este script (não uma suposição): `dados_historicos.
obter_gsax_atual` (produção) calcula as últimas 100 partidas do goleiro
RELATIVAS A HOJE, não à data da partida-alvo -- o `gsax_rate` já persistido
em `player_match_estimates` tem vazamento de futuro (look-ahead bias) se
usado direto num backtest histórico. `dados_historicos.obter_gsax_
walkforward` (nova, sem tocar `obter_gsax_atual`) calcula o mesmo GSAx_rate
mas só com partidas anteriores à data de cada partida-alvo.

Desenho (walk-forward-safe nos DOIS lados, não só no GSAx):
- `λ_xGOT_time_adversario` vem de `player_match_walkforward.lambda_xg_jogo`
  (já walk-forward-safe, populado por `backtest_jogador_mercados_
  walkforward.py`, model_version=MODEL_VERSION_XG_WALKFORWARD, fonte_
  titular='previsto') -- NUNCA de `player_match_estimates` (produção, que
  teria o mesmo tipo de vazamento que motivou esta validação).
- `gsax_rate` vem de `obter_gsax_walkforward`, cortado na data da própria
  partida-alvo.
- Comparação pareada por partida: RMSE + log-verossimilhança de Poisson
  (`y·log(λ)-λ`) de `λ_com_gsax` vs. `λ_neutro`, contra gols realmente
  marcados pelo time adversário do goleiro (=gols sofridos pelo goleiro).
  IC95% via bootstrap pareado da diferença de log-verossimilhança (mesma
  disciplina do projeto -- reaproveita `_rmse`/`_ic95_bootstrap_diferenca`
  de `backtest_jogador_mercados_walkforward.py`).
- Persistido em `player_market_backtest` (mercado='gsax_gols_adversario'),
  só o agregado por liga x temporada (sem previsão bruta por partida --
  suficiente pro objetivo de decidir se vale manter GSAx no pricing
  pipeline).

Uso:
    SUPABASE_URL=... SUPABASE_KEY=... python3 backtest_gsax_walkforward.py
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from supabase import Client, create_client

import dados_historicos as dh
from backtest_jogador_mercados_walkforward import _ic95_bootstrap_diferenca, _rmse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

MODEL_VERSION = "gsax_walkforward_v1"
# model_version que backtest_jogador_mercados_walkforward.py usa pra gravar
# lambda_xg_jogo em player_match_walkforward -- é a fonte de λ_xGOT aqui,
# tem que ser exatamente a mesma string ou a query não acha nada.
MODEL_VERSION_XG_WALKFORWARD = "jogador_mercados_catboost_walkforward_v1"
# Piso de amostra por liga x temporada -- mais alto que o piso genérico de
# 30 usado no resto do projeto porque o efeito esperado aqui é mais sutil
# (GSAX_SHRINKAGE_K=5.0 já revela bastante ruído na métrica; achado de 15/09
# registrado no CONTEXTO_PROJETO.md já mediu esse modulador como "efeito
# diluído" numa comparação anterior, log-loss 1X2 quase idêntico).
AMOSTRA_MINIMA_LIGA = 100


def _log_verossimilhanca_poisson(lam: np.ndarray, y: np.ndarray) -> np.ndarray:
    """log P(y|lambda) por linha (Poisson), lambda com piso pra evitar
    log(0) -- mesmo espírito do clamp de probabilidade já usado em todo o
    projeto (`-ln(0.0001)` etc.), só que aqui em log-verossimilhança de
    contagem, não de probabilidade de seleção."""
    lam_seguro = np.clip(lam, 1e-6, None)
    return y * np.log(lam_seguro) - lam_seguro


def _metricas_poisson_com_ic(lam_variante: np.ndarray, lam_referencia: np.ndarray, y: np.ndarray) -> dict:
    """IC95% bootstrap pareado da diferença de log-verossimilhança
    (variante - referência) -- reaproveita `_ic95_bootstrap_diferenca`
    (que espera "erro", menor=melhor) passando o NEGATIVO da log-
    verossimilhança como erro. `ic_inf>0` (na convenção de aumento da
    log-verossimilhança) = variante bate a referência de forma
    estatisticamente sustentada, não só na média pontual."""
    erro_variante = -_log_verossimilhanca_poisson(lam_variante, y)
    erro_referencia = -_log_verossimilhanca_poisson(lam_referencia, y)
    diff_media, ic_inf, ic_sup = _ic95_bootstrap_diferenca(erro_referencia, erro_variante)
    return {
        "rmse_modelo": _rmse(lam_variante, y),
        "rmse_baseline": _rmse(lam_referencia, y),
        "log_verossimilhanca_media_variante": float(-erro_variante.mean()),
        "log_verossimilhanca_media_referencia": float(-erro_referencia.mean()),
        "diff_log_verossimilhanca_media": diff_media,
        "ic95_inf": ic_inf,
        "ic95_sup": ic_sup,
        "variante_melhor_sustentado": bool(ic_inf > 0) if not np.isnan(ic_inf) else False,
        "n": int(len(y)),
    }


def _carregar_dataset(supabase: Client) -> pd.DataFrame:
    """Monta 1 linha por (partida, goleiro titular confirmado, time
    adversário) nas ligas de `dh.LIGAS_JOGADOR_MERCADOS`, com `λ_xGOT_
    adversario` (soma de `lambda_xg_jogo` walk-forward-safe dos jogadores do
    time adversário) e gols reais marcados pelo adversário (=sofridos pelo
    goleiro)."""
    league_ids = list(dh.obter_ids_ligas(supabase, dh.LIGAS_JOGADOR_MERCADOS).values())
    if not league_ids:
        logger.warning("Nenhuma liga de dh.LIGAS_JOGADOR_MERCADOS encontrada em `leagues`.")
        return pd.DataFrame()

    logger.info("Carregando partidas finalizadas das %d ligas do escopo...", len(league_ids))
    df_matches = dh.carregar_partidas_finalizadas(supabase, league_ids)
    if df_matches.empty:
        return pd.DataFrame()
    match_ids = df_matches["id"].astype(int).unique().tolist()

    logger.info("Carregando titulares confirmados como goleiro em %d partidas...", len(match_ids))
    lineup_rows = dh._paginar_por_lotes_de_id(
        lambda lote, inicio, fim: (
            supabase.table("match_lineup_fotmob")
            .select("match_id, team_id, player_id")
            .eq("is_starter", True)
            .in_("match_id", lote)
            .order("match_id")
            .range(inicio, fim)
        ),
        match_ids,
    )
    if not lineup_rows:
        return pd.DataFrame()
    df_lineup = pd.DataFrame(lineup_rows)

    gk_rows = dh._paginar_por_lotes_de_id(
        lambda lote, inicio, fim: (
            supabase.table("match_player_stats_fotmob")
            .select("match_id, player_id")
            .eq("is_goalkeeper", True)
            .in_("match_id", lote)
            .order("match_id")
            .range(inicio, fim)
        ),
        match_ids,
    )
    if not gk_rows:
        return pd.DataFrame()
    df_gk = pd.DataFrame(gk_rows).drop_duplicates()

    # 1 goleiro titular confirmado por (match_id, team_id) -- dupla checagem
    # já validada em obter_gsax_atual/obter_gsax_walkforward.
    df_goleiros = df_lineup.merge(df_gk, on=["match_id", "player_id"], how="inner")
    df_goleiros = df_goleiros.drop_duplicates(subset=["match_id", "team_id"])
    if df_goleiros.empty:
        return pd.DataFrame()

    df = df_goleiros.merge(
        df_matches.rename(columns={"id": "match_id", "league_id": "league_id_match"}),
        on="match_id", how="inner",
    )
    eh_mandante = df["team_id"] == df["home_team_id"]
    df["time_adversario"] = np.where(eh_mandante, df["away_team_id"], df["home_team_id"])
    df["gols_reais_adversario"] = np.where(eh_mandante, df["away_goals"], df["home_goals"])
    df = df.rename(columns={"player_id": "goleiro_id", "league_id_match": "league_id"})

    logger.info("Carregando lambda_xg_jogo walk-forward (model_version=%s)...", MODEL_VERSION_XG_WALKFORWARD)
    lambda_rows = dh._paginar_por_lotes_de_id(
        lambda lote, inicio, fim: (
            supabase.table("player_match_walkforward")
            .select("match_id, team_id, lambda_xg_jogo")
            .eq("model_version", MODEL_VERSION_XG_WALKFORWARD)
            .eq("fonte_titular", "previsto")
            .in_("match_id", lote)
            .order("match_id")
            .range(inicio, fim)
        ),
        match_ids,
    )
    if not lambda_rows:
        logger.warning("Nenhuma linha de lambda_xg_jogo walk-forward encontrada -- rode backtest_jogador_mercados_walkforward.py primeiro.")
        return pd.DataFrame()
    df_lambda = pd.DataFrame(lambda_rows).dropna(subset=["lambda_xg_jogo"])
    df_lambda["lambda_xg_jogo"] = df_lambda["lambda_xg_jogo"].clip(lower=0.0)
    lambda_xgot_time = (
        df_lambda.groupby(["match_id", "team_id"])["lambda_xg_jogo"].sum().reset_index(name="lambda_xgot_adversario")
    )

    df = df.merge(
        lambda_xgot_time.rename(columns={"team_id": "time_adversario"}),
        on=["match_id", "time_adversario"], how="inner",
    )
    return df[["match_id", "league_id", "season", "match_date", "goleiro_id", "lambda_xgot_adversario", "gols_reais_adversario"]]


def rodar(supabase: Client) -> int:
    df = _carregar_dataset(supabase)
    if df.empty:
        logger.warning("Dataset vazio -- nada pra fazer backtest de GSAx.")
        return 0
    logger.info("%d pares (partida, goleiro titular confirmado, adversário com λ_xGOT walk-forward) carregados.", len(df))

    alvos = list(zip(df["goleiro_id"].astype(int), df["match_date"]))
    logger.info("Calculando GSAx walk-forward-safe pra %d alvos únicos...", len(set(alvos)))
    gsax_por_alvo = dh.obter_gsax_walkforward(supabase, list(set(alvos)))
    logger.info("GSAx calculável (xGOT_enfrentado>0 na janela) pra %d de %d alvos únicos.", len(gsax_por_alvo), len(set(alvos)))

    df["gsax_rate"] = [
        gsax_por_alvo.get((int(g), d), {}).get("gsax_rate") for g, d in zip(df["goleiro_id"], df["match_date"])
    ]
    n_antes_filtro = len(df)
    df = df.dropna(subset=["gsax_rate"])
    logger.info(
        "%d de %d partidas do universo elegível sobrevivem ao filtro de GSAx calculável (%.1f%%).",
        len(df), n_antes_filtro, 100 * len(df) / n_antes_filtro if n_antes_filtro else 0.0,
    )
    if df.empty:
        return 0

    df["lambda_neutro"] = df["lambda_xgot_adversario"]
    df["lambda_com_gsax"] = (df["lambda_xgot_adversario"] * (1 - df["gsax_rate"].clip(-1, 1))).clip(lower=0.0)

    lam_variante = df["lambda_com_gsax"].to_numpy()
    lam_referencia = df["lambda_neutro"].to_numpy()
    y = df["gols_reais_adversario"].to_numpy()

    metricas_geral = _metricas_poisson_com_ic(lam_variante, lam_referencia, y)
    logger.info(
        "GERAL: RMSE com_gsax=%.4f neutro=%.4f | log-verossim. com_gsax=%.4f neutro=%.4f | "
        "diff=%.4f IC95%%=[%.4f,%.4f] sustentado=%s n=%d",
        metricas_geral["rmse_modelo"], metricas_geral["rmse_baseline"],
        metricas_geral["log_verossimilhanca_media_variante"], metricas_geral["log_verossimilhanca_media_referencia"],
        metricas_geral["diff_log_verossimilhanca_media"], metricas_geral["ic95_inf"], metricas_geral["ic95_sup"],
        metricas_geral["variante_melhor_sustentado"], metricas_geral["n"],
    )

    linhas_agregado = []
    for (league_id, season), grupo in df.groupby(["league_id", "season"]):
        if len(grupo) < AMOSTRA_MINIMA_LIGA:
            continue
        m = _metricas_poisson_com_ic(
            grupo["lambda_com_gsax"].to_numpy(), grupo["lambda_neutro"].to_numpy(), grupo["gols_reais_adversario"].to_numpy()
        )
        logger.info(
            "  liga=%s temporada=%s: RMSE com_gsax=%.4f neutro=%.4f diff_logvero=%.4f IC95%%=[%.4f,%.4f] sustentado=%s n=%d",
            league_id, season, m["rmse_modelo"], m["rmse_baseline"], m["diff_log_verossimilhanca_media"],
            m["ic95_inf"], m["ic95_sup"], m["variante_melhor_sustentado"], m["n"],
        )
        linhas_agregado.append({
            "season": str(season), "league_id": int(league_id), "model_version": MODEL_VERSION,
            "mercado": "gsax_gols_adversario", "fonte_titular": "previsto",
            "n_partidas": int(grupo["match_id"].nunique()), "n_previsoes": m["n"],
            "rmse_modelo": m["rmse_modelo"], "rmse_baseline": m["rmse_baseline"],
            "log_loss": -m["log_verossimilhanca_media_variante"], "brier": None, "calibracao": None,
            "ic95_diff_inf": m["ic95_inf"] if not np.isnan(m["ic95_inf"]) else None,
            "ic95_diff_sup": m["ic95_sup"] if not np.isnan(m["ic95_sup"]) else None,
        })

    if linhas_agregado:
        supabase.table("player_market_backtest").upsert(
            linhas_agregado, on_conflict="season,league_id,model_version,mercado,fonte_titular"
        ).execute()
        logger.info("%d grupos (liga x temporada) gravados em player_market_backtest.", len(linhas_agregado))

    return len(linhas_agregado)


if __name__ == "__main__":
    import os

    url = os.environ["SUPABASE_URL"].strip()
    key = os.environ["SUPABASE_KEY"].strip()
    sb = create_client(url, key)
    rodar(sb)
