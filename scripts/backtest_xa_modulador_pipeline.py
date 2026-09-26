"""Backtest do modulador de xA PRÓPRIO (`pricing_pipeline.py::
PlayerToTeamAggregator.modular_por_xa_propria`) em log-loss REAL de 1X2 --
passando pelo pipeline completo (Camada 1 + 2 + 3), não só RMSE de gols
isolado por time (já validado, ver `CONTEXTO_PROJETO.md`, 26/09).

Contexto: em 25/09 xA foi testado como modulador de λ_gols com xA
ENVIESADO (superestimava ~30%) e descartado -- RMSE fora da amostra
piorava. Em 26/09, com xA corrigido, a mesma metodologia (correlação
parcial via Frisch-Waugh-Lovell + split temporal 70/30) reverteu: RMSE de
gols por time MELHORA fora da amostra de forma estatisticamente
significativa. Isso é necessário mas não suficiente pra ligar o modulador
em produção -- falta saber se o ganho sobrevive depois de passar pela
reconciliação hierárquica (que já mistura o bottom-up com um λ macro bem
mais validado, `PESO_MACRO_DEFAULT` alto) e pela matriz conjunta de
Dixon-Coles (que converte λ em P(vitória/empate/derrota) de verdade, não
uma faixa arbitrária de gol como o teste de RMSE usa).

Desenho (walk-forward-safe, mesmo padrão de `backtest_gsax_walkforward.py`):
- `player_match_walkforward` (model_version=MODEL_VERSION_XG_WALKFORWARD,
  fonte_titular='previsto') -- NUNCA `player_match_estimates` (produção,
  teria o mesmo tipo de vazamento que motivou os outros backtests desta
  família). Essa tabela já vem sem metadado de posição/seleção de titular
  (`posicao_detalhe`/`is_titular_previsto` não existem nela) -- por isso
  este script chama as agregações da Camada 1 DIRETO (`agregar_volume`
  equivalente via soma simples, `agregar_xa_top3`, thinning), sem passar
  por `selecionar_elenco_provavel` (que exigiria esse metadado). Mesma
  soma simples já usada na validação de RMSE isolado (SQL desta sessão) --
  não reintroduz nenhuma lógica nova de seleção de elenco.
- λ macro vem de `model_match_estimates.params` (mesma prioridade de
  `rodar_pricing_pipeline._buscar_macro_priors`, reaproveitada aqui via
  import -- nunca reimplementar essa lógica duas vezes).
- TESTE DE ISOLAMENTO: força defensiva do adversário e GSAx do goleiro
  ficam NEUTROS nas duas variantes (mesmo espírito de `backtest_gsax_
  walkforward.py`, que também testa 1 feature de cada vez contra neutro,
  não a config completa de produção) -- a única diferença entre as
  variantes "sem_xa"/"com_xa" é `usar_modulador_xa` em `agregar()`.
- Comparação pareada por partida: log-loss de 1X2 (3 seleções) via
  `DixonColesJointEngine.gerar()` de verdade, IC95% via bootstrap pareado
  (`backtest_jogador_mercados_walkforward._ic95_bootstrap_diferenca`,
  mesma disciplina do projeto).
- Persistido em `player_market_backtest` (mercado='xa_modulador_1x2', só
  agregado por liga x temporada -- suficiente pro objetivo de decidir se
  `usar_modulador_xa=True` deve virar o default de `rodar_pricing_
  pipeline.py`).

Uso:
    SUPABASE_URL=... SUPABASE_KEY=... python3 backtest_xa_modulador_pipeline.py
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from supabase import Client, create_client

import dados_historicos as dh
from backtest_jogador_mercados_walkforward import _ic95_bootstrap_diferenca
from pricing_pipeline import DixonColesJointEngine, HierarchicalReconciler, PlayerToTeamAggregator
from rodar_pricing_pipeline import _buscar_macro_priors

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

MODEL_VERSION = "xa_modulador_pipeline_v1"
# Mesma string que `backtest_jogador_mercados_walkforward.py` usa pra
# gravar `lambda_xg_jogo`/`lambda_xa_jogo` em `player_match_walkforward` --
# tem que casar exatamente ou a query não acha nada (mesmo cuidado já
# documentado em `backtest_gsax_walkforward.py`).
MODEL_VERSION_XG_WALKFORWARD = "jogador_mercados_catboost_walkforward_v1"
AMOSTRA_MINIMA_LIGA = 100
SELECOES_1X2 = ("home", "draw", "away")


def _log_loss_1x2(mercados: dict[tuple[str, str], float], resultado_real: int) -> float:
    """`resultado_real`: 0=casa, 1=empate, 2=fora. Probabilidade clipada em
    [1e-4, 1] antes do log (mesmo piso usado em todo o projeto pra
    log-loss, ver `api/model-stats.js`/`recalcular_model_stats_resumo`)."""
    p = mercados.get(("1X2", SELECOES_1X2[resultado_real]), 0.0)
    return float(-np.log(np.clip(p, 1e-4, 1.0)))


def _agregados_camada1_sem_selecao(elenco: pd.DataFrame) -> dict:
    """Reproduz `PlayerToTeamAggregator.agregar_volume`/`agregar_xgot`/
    `agregar_xa_top3`/`afinar_poisson` SEM passar por `selecionar_elenco_
    provavel` -- `player_match_walkforward` não tem `posicao_detalhe`/
    `is_titular_previsto` (não precisa: já é o elenco provável calculado
    pelo próprio walk-forward). Usa os MESMOS métodos estáticos/de
    instância de `PlayerToTeamAggregator` sempre que não dependem da
    seleção -- só a orquestração muda, não a fórmula."""
    avisos: list[str] = []
    agregador = PlayerToTeamAggregator()
    lambda_xgot_total = agregador.agregar_xgot(elenco, avisos)
    lambda_thinning = agregador.afinar_poisson(elenco, avisos)
    lambda_xa_top3 = agregador.agregar_xa_top3(elenco, avisos)
    return {"lambda_xgot_total": lambda_xgot_total, "lambda_thinning": lambda_thinning, "lambda_xa_top3": lambda_xa_top3}


def _carregar_dataset(supabase: Client) -> pd.DataFrame:
    league_ids = list(dh.obter_ids_ligas(supabase, dh.LIGAS_JOGADOR_MERCADOS).values())
    if not league_ids:
        logger.warning("Nenhuma liga de dh.LIGAS_JOGADOR_MERCADOS encontrada em `leagues`.")
        return pd.DataFrame()

    logger.info("Carregando partidas finalizadas das %d ligas do escopo...", len(league_ids))
    df_matches = dh.carregar_partidas_finalizadas(supabase, league_ids)
    if df_matches.empty:
        return pd.DataFrame()
    match_ids = df_matches["id"].astype(int).unique().tolist()

    logger.info("Carregando lambda_macro (model_match_estimates.params) pra %d partidas...", len(match_ids))
    macro_por_partida = _buscar_macro_priors(supabase, match_ids)
    logger.info("%d de %d partidas têm modelo macro utilizável.", len(macro_por_partida), len(match_ids))

    logger.info("Carregando player_match_walkforward (model_version=%s, fonte=previsto)...", MODEL_VERSION_XG_WALKFORWARD)
    linhas = dh._paginar_por_lotes_de_id(
        lambda lote, inicio, fim: (
            supabase.table("player_match_walkforward")
            .select("match_id, team_id, lambda_chutes_jogo, lambda_xg_jogo, taxa_conversao_bayesiana, lambda_xa_jogo")
            .eq("model_version", MODEL_VERSION_XG_WALKFORWARD)
            .eq("fonte_titular", "previsto")
            .in_("match_id", lote)
            .order("match_id")
            .range(inicio, fim)
        ),
        match_ids,
    )
    if not linhas:
        logger.warning("Nenhuma linha de player_match_walkforward encontrada -- rode backtest_jogador_mercados_walkforward.py primeiro.")
        return pd.DataFrame()
    df_jogadores = pd.DataFrame(linhas)

    linhas_saida = []
    for _, partida in df_matches.iterrows():
        match_id = int(partida["id"])
        macro = macro_por_partida.get(match_id)
        if macro is None:
            continue
        home_id, away_id = int(partida["home_team_id"]), int(partida["away_team_id"])
        elenco_home = df_jogadores[(df_jogadores["match_id"] == match_id) & (df_jogadores["team_id"] == home_id)]
        elenco_away = df_jogadores[(df_jogadores["match_id"] == match_id) & (df_jogadores["team_id"] == away_id)]
        if elenco_home.empty or elenco_away.empty:
            continue
        hg, ag = partida["home_goals"], partida["away_goals"]
        if pd.isna(hg) or pd.isna(ag):
            continue
        resultado = 0 if hg > ag else (1 if hg == ag else 2)
        linhas_saida.append({
            "match_id": match_id, "league_id": int(partida["league_id"]), "season": str(partida["season"]),
            "match_date": partida["match_date"], "lambda_macro_home": macro["lambda_home"],
            "lambda_macro_away": macro["lambda_away"], "rho_liga": macro["rho_liga"],
            "agregados_home": _agregados_camada1_sem_selecao(elenco_home),
            "agregados_away": _agregados_camada1_sem_selecao(elenco_away),
            "resultado": resultado,
        })

    return pd.DataFrame(linhas_saida)


def _rodar_variante(df: pd.DataFrame, usar_modulador_xa: bool) -> np.ndarray:
    """Log-loss de 1X2 por partida, pipeline completo (Camada 1 já
    agregada em `_carregar_dataset` + Camada 2 + Camada 3 de verdade),
    reconstruindo `lambda_gols_xgot`/`lambda_bottom_up` a partir dos
    agregados já calculados (evita reagregar o elenco 2x por variante)."""
    reconciler = HierarchicalReconciler()
    engine = DixonColesJointEngine()
    erros = np.empty(len(df))
    for i, linha in enumerate(df.itertuples(index=False)):
        lambdas_finais = []
        for lado, macro in (("agregados_home", linha.lambda_macro_home), ("agregados_away", linha.lambda_macro_away)):
            ag = getattr(linha, lado)
            lambda_gols_xgot = ag["lambda_xgot_total"]
            if usar_modulador_xa:
                lambda_gols_xgot = PlayerToTeamAggregator.modular_por_xa_propria(
                    lambda_gols_xgot, ag["lambda_xgot_total"], ag["lambda_xa_top3"]
                )
            lambda_bottom_up = 0.5 * ag["lambda_thinning"] + 0.5 * lambda_gols_xgot
            reconciliacao = reconciler.reconciliar(lambda_bottom_up, macro, contexto=f"match {linha.match_id} {lado}")
            lambdas_finais.append(reconciliacao.lambda_final)
        resultado = engine.gerar(lambdas_finais[0], lambdas_finais[1], linha.rho_liga)
        erros[i] = _log_loss_1x2(resultado.mercados, linha.resultado)
    return erros


def rodar(supabase: Client) -> int:
    df = _carregar_dataset(supabase)
    if df.empty:
        logger.warning("Dataset vazio -- nada pra fazer backtest do modulador de xA.")
        return 0
    logger.info("%d partidas com macro + elenco walk-forward dos 2 times carregadas.", len(df))

    erro_sem_xa = _rodar_variante(df, usar_modulador_xa=False)
    erro_com_xa = _rodar_variante(df, usar_modulador_xa=True)

    diff_media, ic_inf, ic_sup = _ic95_bootstrap_diferenca(erro_sem_xa, erro_com_xa)
    sustentado = bool(ic_inf > 0) if not np.isnan(ic_inf) else False
    logger.info(
        "GERAL: log-loss sem_xa=%.5f com_xa=%.5f diff=%.5f IC95%%=[%.5f,%.5f] sustentado=%s n=%d",
        erro_sem_xa.mean(), erro_com_xa.mean(), diff_media, ic_inf, ic_sup, sustentado, len(df),
    )

    linhas_agregado = []
    for (league_id, season), idx in df.groupby(["league_id", "season"]).groups.items():
        if len(idx) < AMOSTRA_MINIMA_LIGA:
            continue
        pos = df.index.get_indexer(idx)
        dm, li, ls = _ic95_bootstrap_diferenca(erro_sem_xa[pos], erro_com_xa[pos])
        sust = bool(li > 0) if not np.isnan(li) else False
        logger.info(
            "  liga=%s temporada=%s: log-loss sem_xa=%.5f com_xa=%.5f diff=%.5f IC95%%=[%.5f,%.5f] sustentado=%s n=%d",
            league_id, season, erro_sem_xa[pos].mean(), erro_com_xa[pos].mean(), dm, li, ls, sust, len(pos),
        )
        linhas_agregado.append({
            "season": str(season), "league_id": int(league_id), "model_version": MODEL_VERSION,
            "mercado": "xa_modulador_1x2", "fonte_titular": "previsto",
            "n_partidas": int(len(pos)), "n_previsoes": int(len(pos)),
            "rmse_modelo": float(erro_com_xa[pos].mean()), "rmse_baseline": float(erro_sem_xa[pos].mean()),
            "log_loss": float(erro_com_xa[pos].mean()), "brier": None, "calibracao": None,
            "ic95_diff_inf": li if not np.isnan(li) else None, "ic95_diff_sup": ls if not np.isnan(ls) else None,
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
