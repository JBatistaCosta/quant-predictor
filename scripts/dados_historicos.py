#!/usr/bin/env python3
"""Preparação de dados históricos pro Model Benchmarking.

O futebol é um esporte não-estacionário (times mudam de elenco, técnico,
forma temporada a temporada), então "quanto mais dado, melhor" não vale
igual pra todo tipo de modelo. Este módulo implementa duas estratégias de
janela diferentes, compartilhadas por `rodar_predicoes.py` e
`backtest_kelly.py`:

  - Dixon-Coles (Poisson): janela CURTA (2-3 temporadas -- as duas últimas
    completas + a atual), com peso de decaimento temporal (time-decay) pra
    dar mais peso a jogo recente e não deixar ruído de anos distantes pesar
    igual. Ver `montar_janela_dixon_coles`/`estimar_forcas_dixon_coles`.
  - CatBoost/XGBoost/LightGBM: janela mais LONGA (5-8 temporadas), mas
    "Feature Stacked" entre ligas equivalentes -- em vez de buscar 15 anos
    de uma liga só, empilha os últimos N anos de cada uma das 6 ligas do
    Model Benchmarking (5 europeias de elite + Brasileirão), ganhando
    linhas sem trazer dinâmica tática datada demais. Ver
    `montar_dataset_ml_empilhado`.
  - `split_cronologico`: 60% treino / 20% validação / 20% teste (out-of-
    -sample), sempre por ORDEM DE DATA -- nunca aleatório, senão vazaria
    informação do futuro pro treino.

Todas as consultas ao Supabase paginam de verdade (`.range()`): sem isso o
PostgREST corta silenciosamente em 1000 linhas, e é fácil passar disso aqui
(7 temporadas x 6 ligas x ~380 jogos = mais de 15 mil linhas).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable

import numpy as np
import pandas as pd
from supabase import Client

logger = logging.getLogger("dados_historicos")

# Códigos de resultado usados em `resultado` (dataset ML) e em
# `predict_proba`/`empacotar_predicoes` (`scripts/modelos_ml.py`) -- fonte
# única pra evitar import circular entre `rodar_predicoes.py` e
# `backtest_kelly.py`.
RESULTADO_HOME, RESULTADO_DRAW, RESULTADO_AWAY = 0, 1, 2

# Códigos do alvo binário `resultado_over25` (mercado Over/Under 2.5 gols).
RESULTADO_UNDER25, RESULTADO_OVER25 = 0, 1

# Códigos do alvo binário `resultado_btts` (Both Teams To Score).
RESULTADO_BTTS_NO, RESULTADO_BTTS_YES = 0, 1

# Códigos do alvo binário `resultado_corners_ou95` (mercado Over/Under 9.5
# escanteios totais -- soma casa+visitante).
RESULTADO_CORNERS_UNDER95, RESULTADO_CORNERS_OVER95 = 0, 1

# Códigos do alvo multiclasse `resultado_faixa_gols` (mercado "faixa de
# gols", 4 classes sobre o total casa+visitante -- pedido explícito do
# usuário: 0-1 / 2-3 / 4-6 / 7+).
RESULTADO_FAIXA_0_1, RESULTADO_FAIXA_2_3, RESULTADO_FAIXA_4_6, RESULTADO_FAIXA_7MAIS = 0, 1, 2, 3

# Códigos do alvo multiclasse `resultado_faixa_corners` (≤8 / 9-10 / 11-12 / 13+).
RESULTADO_FAIXA_CORNERS_8MENOS, RESULTADO_FAIXA_CORNERS_9_10, RESULTADO_FAIXA_CORNERS_11_12, RESULTADO_FAIXA_CORNERS_13MAIS = 0, 1, 2, 3


def codigo_faixa_gols(total_gols: float) -> int:
    """Bucket do total de gols (casa+visitante) nas 4 faixas -- usado tanto
    na construção do alvo do dataset "Feature Stacked" quanto no Dixon-Coles
    (`rodar_predicoes._prever_probs_dixon_coles`, que soma o MESMO grid de
    placares por faixa em vez de por vencedor/total>2.5) e no backtest
    (`backtest_kelly._resultado_codigo_mercado`), pra nunca duplicar os
    limiares das faixas em 3 lugares."""
    if total_gols <= 1:
        return RESULTADO_FAIXA_0_1
    if total_gols <= 3:
        return RESULTADO_FAIXA_2_3
    if total_gols <= 6:
        return RESULTADO_FAIXA_4_6
    return RESULTADO_FAIXA_7MAIS


def codigo_faixa_corners(total: float) -> int:
    """Bucket do total de escanteios (casa+visitante) nas 4 faixas (≤8 / 9-10 / 11-12 / 13+).
    Mesmos limites usados na Análise Estatística por jogo."""
    if total <= 8:
        return RESULTADO_FAIXA_CORNERS_8MENOS
    if total <= 10:
        return RESULTADO_FAIXA_CORNERS_9_10
    if total <= 12:
        return RESULTADO_FAIXA_CORNERS_11_12
    return RESULTADO_FAIXA_CORNERS_13MAIS

TAMANHO_PAGINA = 1000

# Tamanho seguro de lista dentro de um `.in_()` -- uma lista de milhares de
# IDs (ex.: os ~8-9 mil match_id de Train+Val no dataset "Feature Stacked")
# gera uma URL longa demais e o Cloudflare da Supabase devolve HTTP 520
# antes mesmo da consulta chegar no Postgres. Confirmado rodando o backtest
# de verdade: `.in_("id", match_ids)` com a lista inteira falhava com 520;
# em lotes de 500 funciona.
TAMANHO_LOTE_IDS = 500

# Decaimento temporal do Dixon-Coles: meia-vida de ~13 meses (mesmo valor
# usado no pipeline de produção deste projeto, `modelo_dixon_coles.py`) --
# nunca foi recalibrado por validação cruzada de verdade, é um chute
# inicial razoável (achado documentado em CONTEXTO_PROJETO.md).
XI_DECAIMENTO = 0.0018

# Nomes exatamente como estão em `leagues.name` (confirmado por consulta
# direta ao banco, não adivinhado). Brasileirão incluído por pedido
# explícito do usuário -- antes ficava de fora (só as 5 europeias) porque
# faltava profundidade de histórico (backfill de temporadas antigas do
# FotMob resolveu isso, ver .github/workflows/temporadas_fotmob_backfill.yml)
# e por não ter squad-rating/elo tão completo quanto a Europa; cobertura
# real de elo pré-jogo pro Brasileirão é ~46% (contra ~100% na Europa) e xG
# é ~2% (contra ~40% na Europa) -- ambas NaN-tolerantes pros modelos de
# árvore (mesmo espírito do xG parcial já aceito nas ligas europeias), só
# fica mais fraco especificamente nessas duas features pro Brasileirão.
LIGAS_MODEL_BENCHMARKING = ["Premier League", "La Liga", "Serie A (Itália)", "Bundesliga", "Ligue 1", "Brasileirão Série A"]

# Últimos N jogos usados na média móvel pré-jogo de gols marcados/sofridos
# dos modelos de árvore.
JANELA_ROLLING_ML = 5


# =============================================================================
# Paginação genérica
# =============================================================================
def _paginar(query_builder_factory: Callable[[int, int], object], tamanho_pagina: int = TAMANHO_PAGINA) -> list[dict]:
    """Executa uma query em blocos de `tamanho_pagina`, contornando o corte
    silencioso de 1000 linhas do PostgREST/Supabase por requisição.
    `query_builder_factory(inicio, fim)` deve devolver um query builder já
    com `.range()` aplicado, pronto pra `.execute()`."""
    todas_as_linhas: list[dict] = []
    pagina = 0
    while True:
        inicio = pagina * tamanho_pagina
        fim = inicio + tamanho_pagina - 1
        resposta = query_builder_factory(inicio, fim).execute()
        linhas = resposta.data or []
        todas_as_linhas.extend(linhas)
        if len(linhas) < tamanho_pagina:
            break
        pagina += 1
    return todas_as_linhas


def _dividir_em_lotes(itens: list, tamanho: int = TAMANHO_LOTE_IDS):
    """Quebra uma lista grande em lotes menores -- usado antes de qualquer
    `.in_()` cuja lista de IDs pode chegar a milhares de itens (ver
    `TAMANHO_LOTE_IDS`)."""
    for inicio in range(0, len(itens), tamanho):
        yield itens[inicio : inicio + tamanho]


def _paginar_por_lotes_de_id(
    query_builder_factory: Callable[[list, int, int], object], ids: list, tamanho_pagina: int = TAMANHO_PAGINA
) -> list[dict]:
    """Combina lote de IDs (evita URL longa demais / HTTP 520) com paginação
    por `.range()` dentro de cada lote (evita o corte de 1000 linhas -- um
    lote de IDs ainda pode devolver mais de 1000 linhas se a tabela tiver
    várias linhas por ID, ex. várias casas de apostas por partida).
    `query_builder_factory(lote, inicio, fim)` devolve o query builder."""
    todas_as_linhas: list[dict] = []
    for lote in _dividir_em_lotes(ids):
        todas_as_linhas.extend(_paginar(lambda inicio, fim, lote=lote: query_builder_factory(lote, inicio, fim), tamanho_pagina))
    return todas_as_linhas


# =============================================================================
# Carregamento base
# =============================================================================
def obter_ids_ligas(supabase: Client, nomes: list[str]) -> dict[str, int]:
    """Resolve nome->id consultando `leagues` de verdade, em vez de assumir
    IDs fixos -- mesma disciplina já usada no projeto pra mapeamento de
    fonte externa (nunca adivinhar, sempre confirmar contra o banco)."""
    resposta = supabase.table("leagues").select("id, name").in_("name", nomes).execute()
    encontrados = {linha["name"]: linha["id"] for linha in (resposta.data or [])}
    faltando = set(nomes) - set(encontrados)
    if faltando:
        logger.warning("Ligas não encontradas em `leagues`: %s", faltando)
    return encontrados


def carregar_partidas_finalizadas(
    supabase: Client, league_ids: list[int], temporadas: list[str] | None = None
) -> pd.DataFrame:
    """Carrega partidas com placar definido (`status='finished'`) das ligas
    informadas, paginando de verdade."""

    def factory(inicio, fim):
        query = (
            supabase.table("matches")
            .select("id, league_id, season, match_date, home_team_id, away_team_id, home_goals, away_goals, match_stage, is_neutral")
            .in_("league_id", league_ids)
            .eq("status", "finished")
            .order("match_date")
            .range(inicio, fim)
        )
        if temporadas:
            query = query.in_("season", temporadas)
        return query

    linhas = _paginar(factory)
    df = pd.DataFrame(linhas)
    if df.empty:
        return df
    df["match_date"] = pd.to_datetime(df["match_date"], utc=True)
    return df


def carregar_partidas_por_id(supabase: Client, match_ids: list[int]) -> pd.DataFrame:
    """Recarrega `home_team_id`/`away_team_id`/placar/data pra um conjunto
    específico de `match_id` -- usado pelo backtest pra reconstruir os
    times de verdade por trás de linhas do dataset "Feature Stacked" (que
    guarda só as features agregadas, não os IDs de time)."""
    if not match_ids:
        return pd.DataFrame()

    def factory(lote, inicio, fim):
        return (
            supabase.table("matches")
            .select("id, match_date, home_team_id, away_team_id, home_goals, away_goals")
            .in_("id", lote)
            .order("id")
            .range(inicio, fim)
        )

    linhas = _paginar_por_lotes_de_id(factory, match_ids)
    df = pd.DataFrame(linhas)
    if not df.empty:
        df["match_date"] = pd.to_datetime(df["match_date"], utc=True)
    return df


def obter_elo_atual(supabase: Client, team_ids: list[int], escopo: str = "liga") -> dict[int, float]:
    """Rating ATUAL de cada time (`team_elo`) -- correto pra montar as
    features de um jogo que ainda vai acontecer. Usar `team_elo_history`
    aqui seria olhar pro passado por engano (ver `_carregar_elo_pre_jogo`,
    usado só pra reconstruir dataset de TREINO)."""
    if not team_ids:
        return {}
    resposta = supabase.table("team_elo").select("team_id, rating").eq("escopo", escopo).in_("team_id", team_ids).execute()
    return {linha["team_id"]: linha["rating"] for linha in (resposta.data or [])}


# Quantos jogadores do elenco atual entram na média de força (v2, ver
# `obter_squad_rating_atual`) -- tamanho aproximado de um elenco de
# matchday (titulares + banco), pra não diluir o rating do time com
# reservas que quase não jogam.
TOP_N_ELENCO_SQUAD_RATING = 18


def obter_squad_rating_atual(supabase: Client, team_ids: list[int], top_n: int = TOP_N_ELENCO_SQUAD_RATING) -> dict[int, float]:
    """Força do ELENCO atual de cada time (v2, feature `squad_rating_home`/
    `_away`) -- média do rating Elo-like por jogador (`player_ratings`,
    calculado a partir de `match_player_stats_fotmob`), ponderada por
    `n_partidas` (proxy de "titular regular" -- jogador com poucas
    partidas pesa pouco) e limitada aos `top_n` mais usados do elenco
    (`players.last_team_id`).

    EXCLUI jogadores marcados como lesionados agora (`player_availability_
    fotmob.injured=true`) do cálculo -- é a única forma honesta de trazer
    desfalque pra essa feature: não existe histórico de lesão no banco (só
    o snapshot atual, ver CONTEXTO_PROJETO.md), então esse sinal só pode
    entrar nas predições AO VIVO (fixtures futuras), nunca no treino/
    backtest (`_carregar_squad_rating_pre_jogo` já reflete quem jogou de
    verdade em cada partida histórica -- um jogador lesionado simplesmente
    não aparece no elenco daquela partida, sem precisar de tratamento
    especial)."""
    if not team_ids:
        return {}
    ids = [int(t) for t in team_ids]

    jogadores = supabase.table("players").select("id, last_team_id").in_("last_team_id", ids).execute().data or []
    if not jogadores:
        return {}
    player_ids = [j["id"] for j in jogadores]
    time_por_jogador = {j["id"]: j["last_team_id"] for j in jogadores}

    lesionados: set = set()
    for lote in _dividir_em_lotes(player_ids):
        linhas = (
            supabase.table("player_availability_fotmob")
            .select("player_id")
            .in_("player_id", lote)
            .eq("injured", True)
            .execute()
            .data
            or []
        )
        lesionados.update(l["player_id"] for l in linhas)

    ratings: list[dict] = []
    for lote in _dividir_em_lotes(player_ids):
        linhas = supabase.table("player_ratings").select("player_id, rating, n_partidas").in_("player_id", lote).execute().data or []
        ratings.extend(linhas)
    if not ratings:
        return {}

    df = pd.DataFrame(ratings)
    df = df[~df["player_id"].isin(lesionados)]
    if df.empty:
        return {}
    df["team_id"] = df["player_id"].map(time_por_jogador)

    resultado: dict[int, float] = {}
    for team_id, grupo in df.groupby("team_id"):
        grupo_top = grupo.sort_values("n_partidas", ascending=False).head(top_n)
        pesos = grupo_top["n_partidas"]
        resultado[int(team_id)] = (
            float(np.average(grupo_top["rating"], weights=pesos)) if pesos.sum() > 0 else float(grupo_top["rating"].mean())
        )
    return resultado


def _xg_marcado_sofrido(supabase: Client, match_ids: list[int], team_id: int) -> dict[str, float]:
    """xG marcado/sofrido do `team_id` num punhado de partidas (`match_ids`,
    tipicamente os últimos 5 jogos de casa OU de fora de um time só).
    FotMob como fonte primária (~75% cobertura); FBref (match_stats) como
    fallback por partida sem FotMob — coalesce por (match_id, team_id)."""
    if not match_ids:
        return {"marcado": np.nan, "sofrido": np.nan}
    fb = supabase.table("match_stats").select("match_id, team_id, xg").in_("match_id", match_ids).execute().data or []
    fm = supabase.table("match_stats_fotmob").select("match_id, team_id, xg").in_("match_id", match_ids).execute().data or []
    xg_map: dict[tuple, float] = {}
    for l in fb:
        if l["xg"] is not None:
            xg_map[(l["match_id"], l["team_id"])] = l["xg"]
    for l in fm:
        if l["xg"] is not None:
            xg_map[(l["match_id"], l["team_id"])] = l["xg"]
    marcado = [v for (_, tid), v in xg_map.items() if tid == team_id]
    sofrido = [v for (_, tid), v in xg_map.items() if tid != team_id]
    return {
        "marcado": float(np.mean(marcado)) if marcado else np.nan,
        "sofrido": float(np.mean(sofrido)) if sofrido else np.nan,
    }


def obter_forma_recente_por_mando(
    supabase: Client, team_ids: list[int], ultimos_n: int = JANELA_ROLLING_ML
) -> dict[int, dict[str, float]]:
    """Forma recente (gols e xG, marcado/sofrido) calculada SEPARADAMENTE
    pro histórico de mando de cada time -- últimos `ultimos_n` jogos EM
    CASA pra feature "_home", últimos `ultimos_n` jogos FORA pra "_away".
    Mando importa (um time pode ser bem melhor em casa do que fora, ou o
    contrário), e é a mesma lógica usada em `montar_dataset_ml_empilhado`
    pro dataset de treino -- aqui pra montar a feature de um jogo FUTURO
    (sem risco de vazamento: o jogo previsto ainda nem aconteceu).

    xG (`match_stats.xg`, Understat/FBref) só cobre 2022+ nas 5 ligas de
    elite europeias -- fica NaN pra times/ligas sem essa fonte (ex.:
    Brasileirão), sem quebrar nada (CatBoost/XGBoost/LightGBM lidam
    nativamente com NaN numérico)."""
    forma: dict[int, dict[str, float]] = {}
    for team_id in team_ids:
        jogos_casa = (
            supabase.table("matches")
            .select("id, home_goals, away_goals")
            .eq("status", "finished")
            .eq("home_team_id", int(team_id))
            .order("match_date", desc=True)
            .limit(ultimos_n)
            .execute()
            .data
            or []
        )
        jogos_fora = (
            supabase.table("matches")
            .select("id, home_goals, away_goals")
            .eq("status", "finished")
            .eq("away_team_id", int(team_id))
            .order("match_date", desc=True)
            .limit(ultimos_n)
            .execute()
            .data
            or []
        )
        xg_casa = _stats_marcado_sofrido_lote(supabase, "match_stats_fotmob", ["xg", "xgot", "tackles", "interceptions", "duels_won", "aerial_duels_won", "touches_opp_box"], [j["id"] for j in jogos_casa], team_id)
        xg_fora = _stats_marcado_sofrido_lote(supabase, "match_stats_fotmob", ["xg", "xgot", "tackles", "interceptions", "duels_won", "aerial_duels_won", "touches_opp_box"], [j["id"] for j in jogos_fora], team_id)

        forma[team_id] = {
            "media_gols_marcados_5j_home": float(np.mean([j["home_goals"] for j in jogos_casa])) if jogos_casa else np.nan,
            "media_gols_sofridos_5j_home": float(np.mean([j["away_goals"] for j in jogos_casa])) if jogos_casa else np.nan,
            "media_gols_marcados_5j_away": float(np.mean([j["away_goals"] for j in jogos_fora])) if jogos_fora else np.nan,
            "media_gols_sofridos_5j_away": float(np.mean([j["home_goals"] for j in jogos_fora])) if jogos_fora else np.nan,
            "media_xg_5j_home": xg_casa.get("xg", {}).get("marcado"),
            "media_xg_sofrido_5j_home": xg_casa.get("xg", {}).get("sofrido"),
            "media_xg_5j_away": xg_fora.get("xg", {}).get("marcado"),
            "media_xg_sofrido_5j_away": xg_fora.get("xg", {}).get("sofrido"),
            "xgot_home_5j": xg_casa.get("xgot", {}).get("marcado"),
            "xgot_away_5j": xg_fora.get("xgot", {}).get("marcado"),
            "tackles_home_5j": xg_casa.get("tackles", {}).get("marcado"),
            "tackles_away_5j": xg_fora.get("tackles", {}).get("marcado"),
            "interceptions_home_5j": xg_casa.get("interceptions", {}).get("marcado"),
            "interceptions_away_5j": xg_fora.get("interceptions", {}).get("marcado"),
            "ground_duels_won_home_5j": xg_casa.get("duels_won", {}).get("marcado"),
            "ground_duels_won_away_5j": xg_fora.get("duels_won", {}).get("marcado"),
            "aerials_won_home_5j": xg_casa.get("aerial_duels_won", {}).get("marcado"),
            "aerials_won_away_5j": xg_fora.get("aerial_duels_won", {}).get("marcado"),
            "touches_opp_box_home_5j": xg_casa.get("touches_opp_box", {}).get("marcado"),
            "touches_opp_box_away_5j": xg_fora.get("touches_opp_box", {}).get("marcado"),
        }
    return forma


def _stats_marcado_sofrido_lote_multi_janelas(
    supabase: Client, tabela: str, colunas: list[str], match_ids: list[int], team_id: int
) -> dict[str, dict[str, float]]:
    """Versão de `_stats_marcado_sofrido_lote` que calcula médias móveis para 5j, 10j, 20j
    e seus respectivos decays exponenciais."""
    if not match_ids:
        # Se não há jogos, retorna NaN para todas combinações
        vazio = {}
        for col in colunas:
            vazio[col] = {}
            for j in [5, 10, 20]:
                vazio[col].update({
                    f"marcado_{j}j": np.nan, f"sofrido_{j}j": np.nan,
                    f"marcado_{j}j_decay": np.nan, f"sofrido_{j}j_decay": np.nan
                })
        return vazio
        
    linhas = supabase.table(tabela).select(f"match_id, team_id, {', '.join(colunas)}").in_("match_id", match_ids).execute().data or []
    
    # Ordenar linhas na mesma ordem de match_ids (do mais recente pro mais antigo)
    linhas_ordenadas = []
    for mid in match_ids:
        for l in linhas:
            if l["match_id"] == mid:
                linhas_ordenadas.append(l)
    
    resultado: dict[str, dict[str, float]] = {}
    for col in colunas:
        marcado = [l[col] for l in linhas_ordenadas if l["team_id"] == team_id and l.get(col) is not None]
        sofrido = [l[col] for l in linhas_ordenadas if l["team_id"] != team_id and l.get(col) is not None]
        
        col_res = {}
        for janela in [5, 10, 20]:
            marc_j = marcado[:janela]
            sofr_j = sofrido[:janela]
            
            # Simple mean
            col_res[f"marcado_{janela}j"] = float(np.mean(marc_j)) if marc_j else np.nan
            col_res[f"sofrido_{janela}j"] = float(np.mean(sofr_j)) if sofr_j else np.nan
            
            # Decay (ema)
            # Reverte array para a ordem cronológica (antigo -> recente) para calcular o EMA corretamente
            if marc_j:
                s = pd.Series(marc_j[::-1])
                col_res[f"marcado_{janela}j_decay"] = float(s.ewm(span=janela, min_periods=1).mean().iloc[-1])
            else:
                col_res[f"marcado_{janela}j_decay"] = np.nan
                
            if sofr_j:
                s = pd.Series(sofr_j[::-1])
                col_res[f"sofrido_{janela}j_decay"] = float(s.ewm(span=janela, min_periods=1).mean().iloc[-1])
            else:
                col_res[f"sofrido_{janela}j_decay"] = np.nan

        resultado[col] = col_res
    return resultado


def _stats_marcado_sofrido_lote(
    supabase: Client, tabela: str, colunas: list[str], match_ids: list[int], team_id: int
) -> dict[str, dict[str, float]]:
    """Generaliza `_xg_marcado_sofrido` pra QUALQUER coluna de uma tabela no
    formato 1-linha-por-time-por-partida (`match_stats`/`match_stats_fotmob`)
    -- um punhado de `match_ids` (tipicamente os últimos 5 jogos de casa OU
    de fora de um time só), devolve marcado (linha do próprio `team_id`) e
    sofrido (linha do adversário) por coluna, numa passada só (evita
    refazer a mesma query pra cada uma das 7/24 colunas de v7/v8)."""
    if not match_ids:
        return {col: {"marcado": np.nan, "sofrido": np.nan} for col in colunas}
    linhas = supabase.table(tabela).select(f"match_id, team_id, {', '.join(colunas)}").in_("match_id", match_ids).execute().data or []
    resultado: dict[str, dict[str, float]] = {}
    for col in colunas:
        marcado = [l[col] for l in linhas if l["team_id"] == team_id and l.get(col) is not None]
        sofrido = [l[col] for l in linhas if l["team_id"] != team_id and l.get(col) is not None]
        resultado[col] = {
            "marcado": float(np.mean(marcado)) if marcado else np.nan,
            "sofrido": float(np.mean(sofrido)) if sofrido else np.nan,
        }
    return resultado


def obter_forma_recente_extra_por_mando(
    supabase: Client, team_ids: list[int], ultimos_n: int = JANELA_ROLLING_ML
) -> dict[int, dict[str, float]]:
    """v7 AO VIVO: forma pré-jogo das estatísticas do FBref (`match_stats`
    -- posse/chutes/chutes no alvo/escanteios/faltas/cartões amarelos/
    vermelhos, ver `COLUNAS_FORMA_EXTRA_POR_RAW`), mesmo espírito de
    `obter_forma_recente_por_mando` (gols/xG) -- últimos `ultimos_n` jogos
    EM CASA pras colunas "_home", últimos `ultimos_n` jogos FORA pras
    "_away". Usa `_stats_marcado_sofrido_lote` (generaliza
    `_xg_marcado_sofrido` pra qualquer coluna de `match_stats`)."""
    forma: dict[int, dict[str, float]] = {}
    for team_id in team_ids:
        jogos_casa = (
            supabase.table("matches")
            .select("id")
            .eq("status", "finished")
            .eq("home_team_id", int(team_id))
            .order("match_date", desc=True)
            .limit(ultimos_n)
            .execute()
            .data
            or []
        )
        jogos_fora = (
            supabase.table("matches")
            .select("id")
            .eq("status", "finished")
            .eq("away_team_id", int(team_id))
            .order("match_date", desc=True)
            .limit(ultimos_n)
            .execute()
            .data
            or []
        )
        stats_casa = _stats_marcado_sofrido_lote(supabase, "match_stats", COLUNAS_STATS_EXTRA, [j["id"] for j in jogos_casa], team_id)
        stats_fora = _stats_marcado_sofrido_lote(supabase, "match_stats", COLUNAS_STATS_EXTRA, [j["id"] for j in jogos_fora], team_id)

        linha: dict[str, float] = {}
        for col_raw, mapa in COLUNAS_FORMA_EXTRA_POR_RAW.items():
            linha[mapa["marcado_home"]] = stats_casa[col_raw]["marcado"]
            linha[mapa["sofrido_home"]] = stats_casa[col_raw]["sofrido"]
            linha[mapa["marcado_away"]] = stats_fora[col_raw]["marcado"]
            linha[mapa["sofrido_away"]] = stats_fora[col_raw]["sofrido"]
        forma[team_id] = linha
    return forma


def obter_forma_recente_fotmob_por_mando(
    supabase: Client, team_ids: list[int], ultimos_n: int = JANELA_ROLLING_ML
) -> dict[int, dict[str, float]]:
    """v8 AO VIVO: forma pré-jogo das ~22 colunas do FotMob (`match_stats_
    fotmob`, ver `COLUNAS_STATS_FOTMOB`) -- mesmo padrão de
    `obter_forma_recente_extra_por_mando` (v7/FBref), só que lendo de
    `match_stats_fotmob` (cobertura bem mais ampla, ver docstring de
    `_anexar_stats_fotmob_por_partida`)."""
    forma: dict[int, dict[str, float]] = {}
    colunas_raw = list(COLUNAS_STATS_FOTMOB.keys())
    for team_id in team_ids:
        jogos_casa = (
            supabase.table("matches")
            .select("id")
            .eq("status", "finished")
            .eq("home_team_id", int(team_id))
            .order("match_date", desc=True)
            .limit(ultimos_n)
            .execute()
            .data
            or []
        )
        jogos_fora = (
            supabase.table("matches")
            .select("id")
            .eq("status", "finished")
            .eq("away_team_id", int(team_id))
            .order("match_date", desc=True)
            .limit(ultimos_n)
            .execute()
            .data
            or []
        )
        stats_casa = _stats_marcado_sofrido_lote(supabase, "match_stats_fotmob", colunas_raw, [j["id"] for j in jogos_casa], team_id)
        stats_fora = _stats_marcado_sofrido_lote(supabase, "match_stats_fotmob", colunas_raw, [j["id"] for j in jogos_fora], team_id)

        linha: dict[str, float] = {}
        for col_raw, nome_curto in COLUNAS_STATS_FOTMOB.items():
            mapa = colunas_forma_fotmob(nome_curto)
            linha[mapa["marcado_home"]] = stats_casa[col_raw]["marcado"]
            linha[mapa["sofrido_home"]] = stats_casa[col_raw]["sofrido"]
            linha[mapa["marcado_away"]] = stats_fora[col_raw]["marcado"]
            linha[mapa["sofrido_away"]] = stats_fora[col_raw]["sofrido"]
        forma[team_id] = linha
    return forma


def obter_progresso_temporada_atual(supabase: Client, ligas_temporadas: list[tuple[int, str]]) -> dict[int, float]:
    """v6 AO VIVO: posição da fixture no calendário da temporada (0=primeira
    rodada, 1=última) -- mesma lógica de `_progresso_temporada` (posição
    relativa por ORDEM DE DATA dentro de cada liga+temporada, não pela
    coluna `round`), só que aqui considera TODAS as partidas da temporada
    (agendadas + finalizadas): a fixture que queremos prever ainda não
    aconteceu, então não dá pra usar só `status='finished'` como no
    histórico."""
    progresso: dict[int, float] = {}
    for league_id, season in ligas_temporadas:
        partidas = (
            supabase.table("matches")
            .select("id, match_date")
            .eq("league_id", league_id)
            .eq("season", season)
            .order("match_date")
            .execute()
            .data
            or []
        )
        n = len(partidas)
        if n <= 1:
            for p in partidas:
                progresso[p["id"]] = 0.0
            continue
        for posicao, p in enumerate(partidas):
            progresso[p["id"]] = round(posicao / (n - 1), 4)
    return progresso


# =============================================================================
# Janela curta + decaimento temporal -- Dixon-Coles
# =============================================================================
def montar_janela_dixon_coles(
    supabase: Client, league_id: int, n_temporadas: int = 3, data_referencia: datetime | None = None
) -> pd.DataFrame:
    """Janela curta pro Dixon-Coles: as `n_temporadas` mais recentes (padrão
    3 -- as duas últimas completas + a atual), com peso de decaimento
    temporal (`peso_decaimento = exp(-XI_DECAIMENTO * dias_desde_jogo)`)
    já calculado por linha."""
    partidas_liga = carregar_partidas_finalizadas(supabase, [league_id])
    if partidas_liga.empty:
        return partidas_liga

    temporadas_recentes = sorted(partidas_liga["season"].unique())[-n_temporadas:]
    janela = partidas_liga[partidas_liga["season"].isin(temporadas_recentes)].copy()

    referencia = data_referencia or datetime.now(timezone.utc)
    dias_desde_jogo = (referencia - janela["match_date"]).dt.total_seconds() / 86400
    janela["peso_decaimento"] = np.exp(-XI_DECAIMENTO * dias_desde_jogo.clip(lower=0))
    return janela


def estimar_forcas_dixon_coles(janela: pd.DataFrame) -> dict[int, dict[str, float]]:
    """Força de ataque/defesa por time, via média ponderada pelo peso de
    decaimento temporal (jogo recente pesa mais que jogo antigo dentro da
    própria janela).

    Não é a otimização por máxima verossimilhança completa do Dixon-Coles
    original (essa já existe em `arquivos_do_claude/modelo_dixon_coles.py`,
    no pipeline de produção) -- é uma aproximação mais simples e barata,
    adequada pro benchmark diário: ataque/defesa relativos à média da
    própria janela, no mesmo espírito de qualquer estimador simples de
    força de Poisson.
    """
    if janela.empty:
        return {}

    gols_marcados = pd.concat(
        [
            janela[["home_team_id", "peso_decaimento", "home_goals"]].rename(
                columns={"home_team_id": "team_id", "home_goals": "gols"}
            ),
            janela[["away_team_id", "peso_decaimento", "away_goals"]].rename(
                columns={"away_team_id": "team_id", "away_goals": "gols"}
            ),
        ],
        ignore_index=True,
    )
    gols_sofridos = pd.concat(
        [
            janela[["home_team_id", "peso_decaimento", "away_goals"]].rename(
                columns={"home_team_id": "team_id", "away_goals": "gols"}
            ),
            janela[["away_team_id", "peso_decaimento", "home_goals"]].rename(
                columns={"away_team_id": "team_id", "home_goals": "gols"}
            ),
        ],
        ignore_index=True,
    )

    media_gols_liga_geral = np.average(gols_marcados["gols"], weights=gols_marcados["peso_decaimento"])

    forcas: dict[int, dict[str, float]] = {}
    for team_id, marcados_time in gols_marcados.groupby("team_id"):
        if marcados_time["peso_decaimento"].sum() == 0:
            continue
        sofridos_time = gols_sofridos[gols_sofridos["team_id"] == team_id]
        media_marcados = np.average(marcados_time["gols"], weights=marcados_time["peso_decaimento"])
        media_sofridos = np.average(sofridos_time["gols"], weights=sofridos_time["peso_decaimento"])
        forcas[int(team_id)] = {
            "ataque": float(media_marcados / media_gols_liga_geral),
            "defesa": float(media_sofridos / media_gols_liga_geral),
        }
    return forcas


# =============================================================================
# Dataset "Feature Stacked" -- CatBoost / XGBoost / LightGBM
# =============================================================================
def _carregar_elo_pre_jogo(supabase: Client, league_ids: list[int]) -> pd.DataFrame:
    """`team_elo_history.rating_antes` é o rating do time ANTES daquele jogo
    específico -- ponto-no-tempo real, sem vazar o resultado do próprio
    jogo (ou de jogos futuros) pro treino."""

    def factory(inicio, fim):
        return (
            supabase.table("team_elo_history")
            .select("match_id, team_id, rating_antes")
            .eq("escopo", "liga")
            .in_("league_id", league_ids)
            .order("match_id")
            .range(inicio, fim)
        )

    linhas = _paginar(factory)
    return pd.DataFrame(linhas)


def _carregar_squad_rating_pre_jogo(supabase: Client, match_ids: list[int]) -> pd.DataFrame:
    """Força do ELENCO que efetivamente jogou cada partida histórica (v2,
    feature `squad_rating_home`/`_away`) -- média do rating Elo-like de
    cada jogador (`player_rating_history.rating_antes`, valor ANTES
    daquela partida específica -- mesmo ponto-no-tempo real de
    `elo_home`/`elo_away`, ver `_carregar_elo_pre_jogo`) ponderada pelos
    minutos jogados NAQUELA partida (`match_player_stats_fotmob`, que
    também dá o `team_id` -- `player_rating_history` não guarda time).

    Sem vazamento e sem precisar de tratamento especial pra desfalque: um
    jogador lesionado/suspenso simplesmente não tem linha em
    `match_player_stats_fotmob` pra essa partida (não jogou), então não
    entra na média -- ao contrário da versão AO VIVO (`obter_squad_rating_
    atual`), que precisa excluir lesionados explicitamente porque usa o
    elenco atual inteiro, não uma escalação real."""
    if not match_ids:
        return pd.DataFrame(columns=["match_id", "team_id", "squad_rating_antes"])

    def factory_escalacao(lote, inicio, fim):
        return (
            supabase.table("match_player_stats_fotmob")
            .select("match_id, team_id, player_id, minutes_played")
            .in_("match_id", lote)
            .order("match_id")
            .range(inicio, fim)
        )

    escalacoes = pd.DataFrame(_paginar_por_lotes_de_id(factory_escalacao, match_ids))
    if escalacoes.empty:
        return pd.DataFrame(columns=["match_id", "team_id", "squad_rating_antes"])

    def factory_ratings(lote, inicio, fim):
        return (
            supabase.table("player_rating_history")
            .select("match_id, player_id, rating_antes")
            .in_("match_id", lote)
            .order("match_id")
            .range(inicio, fim)
        )

    ratings = pd.DataFrame(_paginar_por_lotes_de_id(factory_ratings, match_ids))
    if ratings.empty:
        return pd.DataFrame(columns=["match_id", "team_id", "squad_rating_antes"])

    escalacoes = escalacoes.merge(ratings, on=["match_id", "player_id"], how="inner")
    escalacoes = escalacoes[escalacoes["minutes_played"].fillna(0) > 0]
    if escalacoes.empty:
        return pd.DataFrame(columns=["match_id", "team_id", "squad_rating_antes"])

    def _media_ponderada(grupo: pd.DataFrame) -> float:
        pesos = grupo["minutes_played"]
        return float(np.average(grupo["rating_antes"], weights=pesos)) if pesos.sum() > 0 else float(grupo["rating_antes"].mean())

    agregado = (
        escalacoes.groupby(["match_id", "team_id"])
        .apply(_media_ponderada, include_groups=False)
        .reset_index(name="squad_rating_antes")
    )
    return agregado


# Mesmos limiares de `arquivos_do_claude/features_contexto.py` (script que
# pré-computa `match_features_contexto`) -- reaproveitados aqui pra
# `obter_fadiga_atual` (v3, predição AO VIVO) bater exatamente com a
# semântica da versão histórica já persistida.
DIAS_DESCANSO_PADRAO = 7.0  # estreia do time no histórico -- sem jogo anterior pra calcular descanso
LIMIAR_MIDWEEK_HORAS = 72  # < 3 dias de descanso = jogo "no meio de semana" (turnaround apertado)


def _carregar_fadiga_pre_jogo(supabase: Client, match_ids: list[int]) -> pd.DataFrame:
    """Descanso pré-jogo (v3, features `days_since_last_match_home`/`_away`
    + `is_midweek_fatigue_home`/`_away`) -- já PRÉ-COMPUTADO por
    `arquivos_do_claude/features_contexto.py` em `match_features_contexto`
    (100% de cobertura nas 5 ligas de elite dos últimos 6 anos, checado
    direto no banco antes de usar), então aqui é só leitura + pivot por
    (match_id, team_id) -- sem recálculo, sem risco de divergir da
    semântica original (dias desde o último jogo do time em QUALQUER
    competição presente no banco, `is_midweek_fatigue`=1 quando < 72h)."""
    if not match_ids:
        return pd.DataFrame(columns=["match_id", "team_id", "days_since_last_match", "is_midweek_fatigue"])

    def factory(lote, inicio, fim):
        return (
            supabase.table("match_features_contexto")
            .select("match_id, team_id, days_since_last_match, is_midweek_fatigue")
            .in_("match_id", lote)
            .order("match_id")
            .range(inicio, fim)
        )

    linhas = _paginar_por_lotes_de_id(factory, match_ids)
    return pd.DataFrame(linhas) if linhas else pd.DataFrame(columns=["match_id", "team_id", "days_since_last_match", "is_midweek_fatigue"])


def obter_fadiga_atual(supabase: Client, team_ids: list[int]) -> dict[int, pd.Timestamp]:
    """Data do último jogo TERMINADO de cada time (qualquer competição
    presente no banco, casa ou fora) -- base pra calcular o descanso de uma
    fixture futura (`fixture.match_date - obter_fadiga_atual()[team_id]`,
    ver `montar_features_fixtures`). Ao contrário de `obter_elo_atual`, não
    devolve um valor "atual" pronto: cada fixture tem sua própria data de
    referência, então o cálculo do delta fica pro chamador -- só a data do
    último jogo é reaproveitável entre fixtures do mesmo time."""
    ultimo_jogo: dict[int, pd.Timestamp] = {}
    for team_id in team_ids:
        jogos_casa = (
            supabase.table("matches")
            .select("match_date")
            .eq("status", "finished")
            .eq("home_team_id", int(team_id))
            .order("match_date", desc=True)
            .limit(1)
            .execute()
            .data
            or []
        )
        jogos_fora = (
            supabase.table("matches")
            .select("match_date")
            .eq("status", "finished")
            .eq("away_team_id", int(team_id))
            .order("match_date", desc=True)
            .limit(1)
            .execute()
            .data
            or []
        )
        candidatos = [pd.to_datetime(j["match_date"], utc=True) for j in (jogos_casa + jogos_fora)]
        if candidatos:
            ultimo_jogo[int(team_id)] = max(candidatos)
    return ultimo_jogo


# =============================================================================
# v4 (parâmetro de disciplina): risco de suspensão por acúmulo de cartões
# amarelos -- `match_events` (gap confirmado antes de implementar: existia
# desde o schema original mas tinha 0 linhas), populada via
# `arquivos_do_claude/ingestao_fotmob_cartoes.py` (MESMO payload matchDetails
# já usado pro resto do pipeline FotMob, `content.matchFacts.events.events`,
# confirmado por inspeção direta da resposta real da API antes de desenhar o
# schema -- não adivinhado).
# =============================================================================
# Regra de "quantos cartões amarelos viram suspensão por acúmulo" -- varia
# por competição de verdade, PESQUISADA uma a uma (não adivinhada):
#   - Premier League: FA -- 5 cautions nos primeiros 19 jogos = 1 jogo de
#     suspensão; 10 até o 32º jogo = 2 jogos; 15 na temporada = 3 jogos.
#     Como 5/10/15 é uma progressão aritmética de passo 5, um modelo de
#     "reset a cada 5" (limiar constante) produz o MESMO sinal de "faltam
#     quantos cartões pro próximo limiar" que a regra real (cumulativa, sem
#     reset) -- simplificação exata, não aproximada, pra evitar modelar o
#     corte por Nº de jogos (19/32) que não muda esse resultado.
#   - La Liga: RFEF -- 5 amarelos na mesma temporada/competição = 1 jogo;
#     zera e reinicia um novo ciclo idêntico depois de cumprida.
#   - Bundesliga: DFB -- toda 5ª amarela (cumulativa, mesmo espírito de
#     progressão aritmética de passo 5 do caso inglês) = 1 jogo de "Gelbsperre".
#   - Ligue 1: regra NOVA da LFP pra 2025/26 em diante -- 5 amarelos na
#     temporada = 1 jogo (substituiu a regra anterior de "3 amarelos numa
#     janela móvel de 10 jogos"). Aplicamos a regra atual (limiar 5) pra
#     toda a janela de treino -- simplificação aceita, mesmo espírito do
#     `XI_DECAIMENTO` (nunca recalibrado por temporada): não vale a
#     complexidade de reimplementar uma janela móvel histórica só pra
#     temporadas anteriores a 2025/26 numa feature heurística de ML.
#   - Serie A (Itália): FIGC -- 1º ciclo em 5 amarelos; reincidência com
#     limiar decrescente (5, 4, 3, 2, 1, e a partir daí sempre 1) --
#     sistema de "diffida"/squalifica progressiva, mais granular que as
#     outras 4 ligas.
#   - Brasileirão: CBF -- 3 amarelos em jogos DIFERENTES = 1 jogo; zera e
#     reinicia (mesmo espírito de La Liga/Bundesliga, só que limiar 3 em
#     vez de 5). Fora do dataset "Feature Stacked" (só ligas europeias),
#     mas usado pela predição AO VIVO (`rodar_predicoes.py` cobre as 6
#     ligas, Brasileirão incluso).
# Cada valor é um int (limiar constante, todo ciclo de reincidência usa o
# mesmo número) ou uma lista de int (limiar por ciclo, índice = nº de
# suspensões já cumpridas NA TEMPORADA, clampado no último valor da lista
# pra ciclos além do que foi pesquisado).
CARTAO_LIMIAR_POR_LIGA: dict[str, int | list[int]] = {
    "Premier League": 5,
    "La Liga": 5,
    "Serie A (Itália)": [5, 5, 4, 3, 2, 1],
    "Bundesliga": 5,
    "Ligue 1": 5,
    "Brasileirão Série A": 3,
}
LIMIAR_CARTAO_PADRAO = 5  # fallback pra liga fora do dict acima (não pesquisada)

# Tamanho do elenco considerado "titulares prováveis" pro cálculo AO VIVO
# (fixture futura, sem escalação real ainda) -- mesmo padrão de
# `TOP_N_ELENCO_SQUAD_RATING`.
TOP_N_ELENCO_CARTOES = TOP_N_ELENCO_SQUAD_RATING

# Tipos de evento que contam pro acúmulo de amarelos -- cartão vermelho
# DIRETO não entra (nenhuma das 6 competições pesquisadas conta reds
# diretos pro limiar de suspensão por cartões, só amarelos -- incluindo o
# segundo amarelo de uma expulsão por 2 cartões, que É um amarelo de
# verdade emitido pelo árbitro).
TIPOS_EVENTO_CARTAO_AMARELO = ("yellow_card", "second_yellow_card")


def _limiar_do_ciclo(regra: int | list[int], n_suspensoes_cumpridas: int) -> int:
    """Limiar de cartões do ciclo de reincidência atual -- `regra` é um int
    (mesmo limiar sempre) ou uma lista (limiar por ciclo, ver
    `CARTAO_LIMIAR_POR_LIGA`, caso da Serie A)."""
    if isinstance(regra, int):
        return regra
    indice = min(n_suspensoes_cumpridas, len(regra) - 1)
    return regra[indice]


def _estado_apos_cada_cartao(datas_ordenadas: list, regra: int | list[int]) -> list[tuple]:
    """Aplica a regra de reset da liga a uma sequência ORDENADA (por data)
    de cartões amarelos de UM jogador numa liga+temporada, devolvendo o
    estado (cartoes_no_ciclo, n_suspensoes) IMEDIATAMENTE APÓS cada cartão
    -- usado depois via `merge_asof` pra achar o estado ANTES de cada
    partida subsequente do jogador (nunca vaza o cartão do PRÓPRIO jogo que
    se está tentando prever: só cartões de partidas ANTERIORES contam)."""
    estados = []
    cartoes_no_ciclo, n_suspensoes = 0, 0
    for _ in datas_ordenadas:
        cartoes_no_ciclo += 1
        limiar_atual = _limiar_do_ciclo(regra, n_suspensoes)
        if cartoes_no_ciclo >= limiar_atual:
            cartoes_no_ciclo = 0
            n_suspensoes += 1
        estados.append((cartoes_no_ciclo, n_suspensoes))
    return estados


def _historico_cartoes_por_jogador(supabase: Client, match_ids: list[int], partidas_meta: pd.DataFrame) -> pd.DataFrame:
    """Eventos de cartão amarelo (`match_events`) dos `match_ids` informados,
    já com (league_id, season, match_date) anexados via `partidas_meta`
    (colunas `id`/`league_id`/`season`/`match_date`) -- base compartilhada
    por `_carregar_cartoes_pre_jogo` (treino) e por quem quiser reconstruir
    o histórico completo de um jogador."""
    if not match_ids:
        return pd.DataFrame(columns=["match_id", "player_id", "match_date", "league_id", "season"])

    def factory(lote, inicio, fim):
        return (
            supabase.table("match_events")
            .select("match_id, player_id, event_type")
            .in_("match_id", lote)
            .in_("event_type", list(TIPOS_EVENTO_CARTAO_AMARELO))
            .eq("source", "fotmob")
            .order("match_id")
            .range(inicio, fim)
        )

    eventos = pd.DataFrame(_paginar_por_lotes_de_id(factory, match_ids))
    if eventos.empty or "player_id" not in eventos.columns:
        return pd.DataFrame(columns=["match_id", "player_id", "match_date", "league_id", "season"])
    eventos = eventos[eventos["player_id"].notna()].copy()
    eventos["player_id"] = eventos["player_id"].astype(int)

    eventos = eventos.merge(
        partidas_meta[["id", "league_id", "season", "match_date"]].rename(columns={"id": "match_id"}),
        on="match_id",
        how="inner",
    )
    return eventos


def _carregar_total_corners_por_partida(supabase: Client, match_ids: list[int]) -> pd.DataFrame:
    """Total de escanteios (casa+visitante) por partida, alvo do mercado
    Over/Under 9.5 -- vem de `match_stats_fotmob` (1 linha por match_id+
    team_id, coluna `corners`). É o RESULTADO real da partida (não uma
    feature pré-jogo, igual `home_goals`/`away_goals`), então não tem risco
    de vazamento -- só é usado como alvo de treino/avaliação, nunca como
    entrada do modelo. `min_count=2` garante que só soma quando as DUAS
    linhas (casa e visitante) existem -- senão fica NaN (partida sem dado de
    escanteio ainda ingerido), filtrado depois no backtest."""
    if not match_ids:
        return pd.DataFrame(columns=["match_id", "total_corners"])

    def factory(lote, inicio, fim):
        return (
            supabase.table("match_stats_fotmob")
            .select("match_id, corners")
            .in_("match_id", lote)
            .range(inicio, fim)
        )

    linhas = _paginar_por_lotes_de_id(factory, match_ids)
    if not linhas:
        return pd.DataFrame(columns=["match_id", "total_corners"])
    df = pd.DataFrame(linhas)
    total = df.groupby("match_id")["corners"].apply(lambda s: s.sum(min_count=2)).reset_index(name="total_corners")
    return total


def _carregar_cartoes_pre_jogo(
    supabase: Client, partidas: pd.DataFrame, nome_da_liga: dict[int, str]
) -> pd.DataFrame:
    """Risco de suspensão por acúmulo de cartões amarelos ANTES de cada
    partida (v4, features `jogadores_pendurados_home`/`_away` +
    `cartoes_acumulados_home`/`_away`) -- ponto-no-tempo real: só conta
    cartão de partida ANTERIOR (mesma liga+temporada, ordem cronológica),
    aplicando a regra de reset/limiar específica da liga
    (`CARTAO_LIMIAR_POR_LIGA`). Calculado sobre o elenco que REALMENTE jogou
    cada partida (`match_player_stats_fotmob`, mesmo padrão de
    `_carregar_squad_rating_pre_jogo`) -- um jogador suspenso simplesmente
    não aparece na escalação daquela partida, sem precisar de tratamento
    especial (mesma lógica já validada pro rating de elenco v2)."""
    colunas_saida = ["match_id", "team_id", "cartoes_acumulados_antes", "jogadores_pendurados_antes"]
    if partidas.empty:
        return pd.DataFrame(columns=colunas_saida)

    match_ids = partidas["id"].astype(int).tolist()
    partidas_meta = partidas[["id", "league_id", "season", "match_date"]]

    def factory_escalacao(lote, inicio, fim):
        return (
            supabase.table("match_player_stats_fotmob")
            .select("match_id, team_id, player_id, minutes_played")
            .in_("match_id", lote)
            .order("match_id")
            .range(inicio, fim)
        )

    escalacoes = pd.DataFrame(_paginar_por_lotes_de_id(factory_escalacao, match_ids))
    escalacoes = escalacoes[escalacoes["player_id"].notna()] if not escalacoes.empty else escalacoes
    if escalacoes.empty:
        return pd.DataFrame(columns=colunas_saida)
    escalacoes = escalacoes[escalacoes["minutes_played"].fillna(0) > 0].copy()
    escalacoes["player_id"] = escalacoes["player_id"].astype(int)
    escalacoes = escalacoes.merge(partidas_meta.rename(columns={"id": "match_id"}), on="match_id", how="inner")

    eventos = _historico_cartoes_por_jogador(supabase, match_ids, partidas_meta)
    if eventos.empty:
        escalacoes["cartoes_no_ciclo_antes"] = 0
        escalacoes["n_suspensoes_antes"] = 0
    else:
        # Estado (cartoes_no_ciclo, n_suspensoes) IMEDIATAMENTE APÓS cada
        # cartão, por (jogador, liga, temporada) -- aplica a regra de reset
        # específica da liga em ordem cronológica.
        eventos = eventos.sort_values("match_date")
        estados_por_grupo = []
        for (_player_id, _league_id, _season), grupo in eventos.groupby(["player_id", "league_id", "season"]):
            regra = CARTAO_LIMIAR_POR_LIGA.get(nome_da_liga.get(_league_id), LIMIAR_CARTAO_PADRAO)
            estados = _estado_apos_cada_cartao(grupo["match_date"].tolist(), regra)
            grupo = grupo.copy()
            grupo["cartoes_no_ciclo"] = [e[0] for e in estados]
            grupo["n_suspensoes"] = [e[1] for e in estados]
            estados_por_grupo.append(grupo)
        eventos_com_estado = pd.concat(estados_por_grupo, ignore_index=True).sort_values("match_date")

        # `merge_asof` pra achar, pra cada (jogador, partida a prever), o
        # estado do cartão mais recente ANTES da data daquela partida --
        # `allow_exact_matches=False` garante que o cartão da PRÓPRIA
        # partida (mesma data) nunca entra no "antes" (sem vazamento).
        escalacoes_ordenadas = escalacoes.sort_values("match_date")
        combinado = pd.merge_asof(
            escalacoes_ordenadas,
            eventos_com_estado[["player_id", "league_id", "season", "match_date", "cartoes_no_ciclo", "n_suspensoes"]].sort_values(
                "match_date"
            ),
            on="match_date",
            by=["player_id", "league_id", "season"],
            direction="backward",
            allow_exact_matches=False,
        )
        combinado["cartoes_no_ciclo_antes"] = combinado["cartoes_no_ciclo"].fillna(0).astype(int)
        combinado["n_suspensoes_antes"] = combinado["n_suspensoes"].fillna(0).astype(int)
        escalacoes = combinado

    escalacoes["limiar_atual"] = escalacoes.apply(
        lambda linha: _limiar_do_ciclo(
            CARTAO_LIMIAR_POR_LIGA.get(nome_da_liga.get(linha["league_id"]), LIMIAR_CARTAO_PADRAO), linha["n_suspensoes_antes"]
        ),
        axis=1,
    )
    escalacoes["pendurado"] = escalacoes["cartoes_no_ciclo_antes"] == (escalacoes["limiar_atual"] - 1)

    agregado = (
        escalacoes.groupby(["match_id", "team_id"])
        .agg(cartoes_acumulados_antes=("cartoes_no_ciclo_antes", "sum"), jogadores_pendurados_antes=("pendurado", "sum"))
        .reset_index()
    )
    agregado["jogadores_pendurados_antes"] = agregado["jogadores_pendurados_antes"].astype(int)
    return agregado


def obter_cartoes_atuais(supabase: Client, team_ids: list[int], nome_da_liga: dict[int, str], league_id_por_time: dict[int, int]) -> dict[int, dict[str, float]]:
    """Versão AO VIVO (v4) de `_carregar_cartoes_pre_jogo` -- calcula o
    acúmulo de cartões ATUAL (temporada corrente) do elenco REGULAR de cada
    time (`players.last_team_id` + `TOP_N_ELENCO_CARTOES` mais usados,
    mesmo padrão de `obter_squad_rating_atual`), já que uma fixture futura
    não tem escalação real ainda. `league_id_por_time` resolve em que liga
    (pra escolher a regra certa em `CARTAO_LIMIAR_POR_LIGA`) cada time
    disputa a temporada atual -- normalmente a liga da própria fixture."""
    if not team_ids:
        return {}
    ids = [int(t) for t in team_ids]

    jogadores = supabase.table("players").select("id, last_team_id").in_("last_team_id", ids).execute().data or []
    if not jogadores:
        return {}
    player_ids = [j["id"] for j in jogadores]
    time_por_jogador = {j["id"]: j["last_team_id"] for j in jogadores}

    # Elenco regular = top N por minutos recentes -- reaproveita
    # `player_ratings.n_partidas` (proxy de "titular regular" já usado por
    # `obter_squad_rating_atual`) só pra RECORTAR o elenco, não pro cálculo
    # de cartão em si.
    ratings: list[dict] = []
    for lote in _dividir_em_lotes(player_ids):
        linhas = supabase.table("player_ratings").select("player_id, n_partidas").in_("player_id", lote).execute().data or []
        ratings.extend(linhas)
    df_regulares = pd.DataFrame(ratings) if ratings else pd.DataFrame(columns=["player_id", "n_partidas"])
    df_regulares["team_id"] = df_regulares["player_id"].map(time_por_jogador)

    elenco_regular: dict[int, list[int]] = {}
    for team_id, grupo in df_regulares.groupby("team_id"):
        elenco_regular[int(team_id)] = grupo.sort_values("n_partidas", ascending=False).head(TOP_N_ELENCO_CARTOES)["player_id"].tolist()
    # Times sem `player_ratings` (nunca processados pelo Elo de jogador) --
    # cai pro elenco inteiro conhecido em `players` como fallback, ainda
    # melhor que ficar sem nenhum jogador.
    for team_id in ids:
        if team_id not in elenco_regular:
            elenco_regular[team_id] = [j["id"] for j in jogadores if j["last_team_id"] == team_id][:TOP_N_ELENCO_CARTOES]

    todos_ids_regulares = sorted({pid for lst in elenco_regular.values() for pid in lst})
    if not todos_ids_regulares:
        return {}

    # Temporada corrente por liga -- maior valor de `season` já visto em
    # `matches` daquela liga (mesma ideia de "temporada atual" usada em
    # outras partes do pipeline, sem depender de um relógio de calendário
    # esportivo específico por competição).
    temporada_atual_por_liga: dict[int, str] = {}
    for league_id in set(league_id_por_time.values()):
        resp = (
            supabase.table("matches").select("season").eq("league_id", league_id).order("season", desc=True).limit(1).execute().data
        )
        if resp:
            temporada_atual_por_liga[league_id] = resp[0]["season"]

    eventos: list[dict] = []
    for lote in _dividir_em_lotes(todos_ids_regulares):
        linhas = (
            supabase.table("match_events")
            .select("match_id, player_id, event_type")
            .in_("player_id", lote)
            .in_("event_type", list(TIPOS_EVENTO_CARTAO_AMARELO))
            .eq("source", "fotmob")
            .execute()
            .data
            or []
        )
        eventos.extend(linhas)
    if not eventos:
        eventos_df = pd.DataFrame(columns=["match_id", "player_id", "event_type"])
    else:
        eventos_df = pd.DataFrame(eventos)

    match_ids_eventos = eventos_df["match_id"].unique().tolist() if not eventos_df.empty else []
    partidas_meta = pd.DataFrame(columns=["id", "league_id", "season", "match_date"])
    if match_ids_eventos:

        def factory(lote, inicio, fim):
            return supabase.table("matches").select("id, league_id, season, match_date").in_("id", lote).range(inicio, fim)

        partidas_meta = pd.DataFrame(_paginar_por_lotes_de_id(factory, match_ids_eventos))
        if not partidas_meta.empty:
            partidas_meta["match_date"] = pd.to_datetime(partidas_meta["match_date"], utc=True)

    resultado: dict[int, dict[str, float]] = {}
    for team_id in ids:
        elenco = elenco_regular.get(team_id, [])
        league_id = league_id_por_time.get(team_id)
        temporada = temporada_atual_por_liga.get(league_id)
        regra = CARTAO_LIMIAR_POR_LIGA.get(nome_da_liga.get(league_id), LIMIAR_CARTAO_PADRAO)

        cartoes_acumulados, jogadores_pendurados = 0, 0
        for player_id in elenco:
            if eventos_df.empty or partidas_meta.empty or temporada is None:
                continue
            eventos_jogador = eventos_df[eventos_df["player_id"] == player_id].merge(partidas_meta, left_on="match_id", right_on="id")
            eventos_jogador = eventos_jogador[(eventos_jogador["league_id"] == league_id) & (eventos_jogador["season"] == temporada)]
            if eventos_jogador.empty:
                continue
            estados = _estado_apos_cada_cartao(eventos_jogador.sort_values("match_date")["match_date"].tolist(), regra)
            cartoes_no_ciclo, n_suspensoes = estados[-1]
            limiar_atual = _limiar_do_ciclo(regra, n_suspensoes)
            cartoes_acumulados += cartoes_no_ciclo
            if cartoes_no_ciclo == limiar_atual - 1:
                jogadores_pendurados += 1

        resultado[team_id] = {
            "cartoes_acumulados": float(cartoes_acumulados),
            "jogadores_pendurados": float(jogadores_pendurados),
        }
    return resultado


# =============================================================================
# Estádio provável de fixtures futuras (investigação -- fecha o gap
# documentado de `travel_distance_km` só existir pra partidas já
# finalizadas). Achado: `match_context_fotmob.stadium_lat/long` já cobre
# 98%+ das partidas finalizadas, mas só 8/3139 partidas AGENDADAS (times
# ainda não têm o próprio estádio "conhecido" registrado pra elas). Times
# raramente mudam de estádio DENTRO de uma temporada -- confirmado direto
# no banco (`match_context_fotmob` join `matches`): a maioria dos times tem
# 1 estádio dominante nos jogos de casa recentes, com exceções reais
# (reforma, jogo fora de sede por decisão de mando) capturadas como
# minoria. Usar o estádio de casa mais RECENTE do próprio time como
# aproximação fecha 210/... times conhecidos, cobrindo 1.908/3.139 (60,8%)
# das partidas agendadas hoje SEM nenhuma chamada de API nova (só leitura
# do que já foi capturado em `match_context_fotmob`). Wiring completo em
# `match_features_contexto`/features de modelo (fadiga/técnico têm colunas
# NOT NULL que exigiriam replicar o resto de `features_contexto.py`) fica
# documentado como próximo passo -- fora do escopo desta rodada (cartões é
# o pedido principal), esta função é o bloco de construção pronto pra isso.
# =============================================================================
def obter_estadio_provavel_mandante(supabase: Client, team_ids: list[int]) -> dict[int, dict[str, float]]:
    """Estádio mais provável de cada time (`team_ids`) pra uma partida em
    que jogue como mandante -- o estádio de CASA mais recente já capturado
    em `match_context_fotmob` (qualquer status de partida, ordenado por
    data desc). Sem chamada de API: só leitura de dado já ingerido."""
    if not team_ids:
        return {}
    ids = [int(t) for t in team_ids]

    def factory(lote, inicio, fim):
        return (
            supabase.table("matches")
            .select("id, home_team_id, match_date, match_context_fotmob(stadium_lat, stadium_long, stadium_name)")
            .in_("home_team_id", lote)
            .order("match_date", desc=True)
            .range(inicio, fim)
        )

    linhas = _paginar_por_lotes_de_id(factory, ids)
    resultado: dict[int, dict[str, float]] = {}
    for linha in linhas:
        team_id = linha["home_team_id"]
        if team_id in resultado:
            continue
        contexto = linha.get("match_context_fotmob")
        if isinstance(contexto, list):
            contexto = contexto[0] if contexto else None
        if not contexto or contexto.get("stadium_lat") is None:
            continue
        resultado[team_id] = {
            "stadium_lat": float(contexto["stadium_lat"]),
            "stadium_long": float(contexto["stadium_long"]),
            "stadium_name": contexto.get("stadium_name"),
        }
    return resultado


def _forma_por_mando_multi_janelas(partidas: pd.DataFrame, col_home: str, col_away: str, prefixo: str) -> pd.DataFrame:
    """Calcula janelas de 5j, 10j, 20j e seus repectivos decays (ewm) para uma métrica,
    gerando um DataFrame com todas as combinações de `prefixo`."""
    casa = partidas[["id", "match_date", "home_team_id", col_home, col_away]].sort_values(["home_team_id", "match_date"]).copy()
    fora = partidas[["id", "match_date", "away_team_id", col_home, col_away]].sort_values(["away_team_id", "match_date"]).copy()
    
    colunas_finais = []
    
    for janela in [5, 10, 20]:
        # Simple rolling
        c_m_col = f"{prefixo}_home_{janela}j"
        c_s_col = f"{prefixo}_sofrido_home_{janela}j"
        casa[c_m_col] = casa.groupby("home_team_id")[col_home].transform(lambda s: s.shift(1).rolling(janela, min_periods=1).mean())
        casa[c_s_col] = casa.groupby("home_team_id")[col_away].transform(lambda s: s.shift(1).rolling(janela, min_periods=1).mean())
        
        f_m_col = f"{prefixo}_away_{janela}j"
        f_s_col = f"{prefixo}_sofrido_away_{janela}j"
        fora[f_m_col] = fora.groupby("away_team_id")[col_away].transform(lambda s: s.shift(1).rolling(janela, min_periods=1).mean())
        fora[f_s_col] = fora.groupby("away_team_id")[col_home].transform(lambda s: s.shift(1).rolling(janela, min_periods=1).mean())
        
        # Exponential decay rolling
        c_m_dec_col = f"{prefixo}_home_{janela}j_decay"
        c_s_dec_col = f"{prefixo}_sofrido_home_{janela}j_decay"
        casa[c_m_dec_col] = casa.groupby("home_team_id")[col_home].transform(lambda s: s.shift(1).ewm(span=janela, min_periods=1).mean())
        casa[c_s_dec_col] = casa.groupby("home_team_id")[col_away].transform(lambda s: s.shift(1).ewm(span=janela, min_periods=1).mean())
        
        f_m_dec_col = f"{prefixo}_away_{janela}j_decay"
        f_s_dec_col = f"{prefixo}_sofrido_away_{janela}j_decay"
        fora[f_m_dec_col] = fora.groupby("away_team_id")[col_away].transform(lambda s: s.shift(1).ewm(span=janela, min_periods=1).mean())
        fora[f_s_dec_col] = fora.groupby("away_team_id")[col_home].transform(lambda s: s.shift(1).ewm(span=janela, min_periods=1).mean())
        
        colunas_finais.extend([c_m_col, c_s_col, f_m_col, f_s_col, c_m_dec_col, c_s_dec_col, f_m_dec_col, f_s_dec_col])
        
    return casa.set_index("id")[[c for c in colunas_finais if "home" in c]].join(
        fora.set_index("id")[[c for c in colunas_finais if "away" in c]]
    )

def _forma_por_mando(partidas: pd.DataFrame, col_home: str, col_away: str, saida: dict[str, str]) -> pd.DataFrame:
    """Média móvel pré-jogo (`.shift(1)` antes do `.rolling()` -- sem isso a
    média incluiria o próprio jogo que se está tentando prever, vazamento
    clássico em backtest de esporte), calculada SEPARADAMENTE pro histórico
    de mando de cada time: a feature "_home" de uma partida só olha os
    jogos ANTERIORES em que aquele time jogou EM CASA; a "_away" só os
    jogos anteriores em que jogou FORA. Mando importa (um time pode ser bem
    melhor em casa do que fora), então misturar os dois contextos numa
    média só perde informação.

    `col_home`/`col_away` são as colunas de `partidas` com o valor do time
    da casa e do time de fora NAQUELA partida (ex.: `home_goals`/
    `away_goals`, ou `xg_home`/`xg_away`) -- serve tanto pra gols quanto
    pra xG com a mesma função, já que em ambos os casos "o que o adversário
    fez" é exatamente "o que o time sofreu"."""
    casa = partidas[["id", "match_date", "home_team_id", col_home, col_away]].sort_values(["home_team_id", "match_date"]).copy()
    casa[saida["marcado_home"]] = casa.groupby("home_team_id")[col_home].transform(
        lambda s: s.shift(1).rolling(JANELA_ROLLING_ML, min_periods=1).mean()
    )
    casa[saida["sofrido_home"]] = casa.groupby("home_team_id")[col_away].transform(
        lambda s: s.shift(1).rolling(JANELA_ROLLING_ML, min_periods=1).mean()
    )

    fora = partidas[["id", "match_date", "away_team_id", col_home, col_away]].sort_values(["away_team_id", "match_date"]).copy()
    fora[saida["marcado_away"]] = fora.groupby("away_team_id")[col_away].transform(
        lambda s: s.shift(1).rolling(JANELA_ROLLING_ML, min_periods=1).mean()
    )
    fora[saida["sofrido_away"]] = fora.groupby("away_team_id")[col_home].transform(
        lambda s: s.shift(1).rolling(JANELA_ROLLING_ML, min_periods=1).mean()
    )

    return casa.set_index("id")[[saida["marcado_home"], saida["sofrido_home"]]].join(
        fora.set_index("id")[[saida["marcado_away"], saida["sofrido_away"]]]
    )


def _anexar_xg_por_partida(supabase: Client, partidas: pd.DataFrame) -> pd.DataFrame:
    """xG observado por partida como `xg_home`/`xg_away`.
    FotMob como fonte primária (~75% cobertura vs ~36% do FBref); FBref
    (match_stats) como fallback por partida sem dado FotMob — coalesce por
    (match_id, team_id), FotMob vence quando ambos existem."""
    match_ids = partidas["id"].astype(int).tolist()

    def factory_fb(lote, inicio, fim):
        return supabase.table("match_stats").select("match_id, team_id, xg").in_("match_id", lote).order("match_id").range(inicio, fim)

    def factory_fm(lote, inicio, fim):
        return supabase.table("match_stats_fotmob").select("match_id, team_id, xg").in_("match_id", lote).order("match_id").range(inicio, fim)

    linhas_fb = _paginar_por_lotes_de_id(factory_fb, match_ids)
    linhas_fm = _paginar_por_lotes_de_id(factory_fm, match_ids)

    partidas = partidas.copy()
    xg_map: dict[tuple, float] = {}
    for l in linhas_fb:
        if l["xg"] is not None:
            xg_map[(l["match_id"], l["team_id"])] = l["xg"]
    for l in linhas_fm:
        if l["xg"] is not None:
            xg_map[(l["match_id"], l["team_id"])] = l["xg"]

    if not xg_map:
        partidas["xg_home"] = np.nan
        partidas["xg_away"] = np.nan
        return partidas

    linhas = [{"id": mid, "team_id": tid, "xg": xg} for (mid, tid), xg in xg_map.items()]
    stats = pd.DataFrame(linhas)
    stats_home = stats.rename(columns={"team_id": "home_team_id", "xg": "xg_home"})
    stats_away = stats.rename(columns={"team_id": "away_team_id", "xg": "xg_away"})
    partidas = partidas.merge(stats_home[["id", "home_team_id", "xg_home"]], on=["id", "home_team_id"], how="left")
    partidas = partidas.merge(stats_away[["id", "away_team_id", "xg_away"]], on=["id", "away_team_id"], how="left")
    return partidas





def _anexar_xgot_por_partida(supabase: Client, partidas: pd.DataFrame) -> pd.DataFrame:
    """Mesmo espírito de `_anexar_xg_por_partida`, mas xGOT (expected goals
    on target -- xG ajustado pela qualidade do chute a gol, não só a
    chance) via `match_stats_fotmob.xgot` -- fonte separada de
    `match_stats` (nunca cruzada com ela), com boa cobertura nas 5 ligas de
    elite europeias, Brasileirão e Libertadores (achado ao investigar se
    xGOT tinha dado real no banco -- tinha, só não estava sendo usado em
    nenhum treino). Só usado como ALVO de regressão (nunca como feature de
    forma pré-jogo por enquanto -- diferente de xG, que já vira
    `media_xg_5j_*`), fica NaN fora da cobertura, sem quebrar nada."""
    match_ids = partidas["id"].astype(int).tolist()

    def factory(lote, inicio, fim):
        return supabase.table("match_stats_fotmob").select("match_id, team_id, xgot").in_("match_id", lote).order("match_id").range(inicio, fim)

    linhas = _paginar_por_lotes_de_id(factory, match_ids)
    partidas = partidas.copy()
    if not linhas:
        partidas["xgot_home"] = np.nan
        partidas["xgot_away"] = np.nan
        return partidas

    stats = pd.DataFrame(linhas).rename(columns={"match_id": "id"})
    stats_home = stats.rename(columns={"team_id": "home_team_id", "xgot": "xgot_home"})
    stats_away = stats.rename(columns={"team_id": "away_team_id", "xgot": "xgot_away"})

    partidas = partidas.merge(stats_home[["id", "home_team_id", "xgot_home"]], on=["id", "home_team_id"], how="left")
    partidas = partidas.merge(stats_away[["id", "away_team_id", "xgot_away"]], on=["id", "away_team_id"], how="left")
    return partidas


_SITUACOES_BOLA_PARADA = {"SetPiece", "FreeKick", "FromCorner"}

# Features derivadas de match_shots_fotmob (nível de chute → agregado por time/partida)
COLUNAS_SITUACAO_CHUTES = [
    "pct_fast_break_fm",       # % chutes em contra-ataque
    "pct_bola_parada_fm",      # % chutes de bola parada (canto/falta/pênalti/lateral)
    "xg_chute_fm",             # xG médio por chute (qualidade de finalização)
    "pct_gols_2tempo_fm",      # % dos gols marcados no 2º tempo
]
COLUNAS_FORMA_SITUACAO_CHUTES = {
    col: {
        "marcado_home": f"media_{col}_5j_home",
        "sofrido_home": f"media_{col}_sofrido_5j_home",
        "marcado_away": f"media_{col}_5j_away",
        "sofrido_away": f"media_{col}_sofrido_5j_away",
    }
    for col in COLUNAS_SITUACAO_CHUTES
}


def _anexar_situacao_chutes_por_partida(supabase: Client, partidas: pd.DataFrame) -> pd.DataFrame:
    """Agrega `match_shots_fotmob` por (match_id, team_id) e anexa 4 features
    de situação de chute: % contra-ataque, % bola parada, xG médio por chute,
    % gols no 2º tempo. Fica NaN quando não há dado FotMob pra partida."""
    match_ids = partidas["id"].astype(int).tolist()

    def factory(lote, inicio, fim):
        return (
            supabase.table("match_shots_fotmob")
            .select("match_id, team_id, xg, situation, event_type, period")
            .in_("match_id", lote)
            .order("match_id")
            .range(inicio, fim)
        )

    linhas = _paginar_por_lotes_de_id(factory, match_ids)
    partidas = partidas.copy()

    if not linhas:
        for col in COLUNAS_SITUACAO_CHUTES:
            partidas[f"{col}_home"] = np.nan
            partidas[f"{col}_away"] = np.nan
        return partidas

    shots = pd.DataFrame(linhas)

    def _agregar(grupo):
        n = len(grupo)
        pct_fb = (grupo["situation"] == "FastBreak").sum() / n if n else np.nan
        pct_bp = grupo["situation"].isin(_SITUACOES_BOLA_PARADA).sum() / n if n else np.nan
        xg_med = grupo["xg"].dropna().mean() if grupo["xg"].notna().any() else np.nan
        gols = grupo[grupo["event_type"] == "Goal"]
        pct_g2 = (gols["period"] == "2H").sum() / len(gols) if len(gols) > 0 else np.nan
        return pd.Series({
            "pct_fast_break_fm": pct_fb,
            "pct_bola_parada_fm": pct_bp,
            "xg_chute_fm": xg_med,
            "pct_gols_2tempo_fm": pct_g2,
        })

    agg = shots.groupby(["match_id", "team_id"], group_keys=False).apply(_agregar).reset_index()
    agg = agg.rename(columns={"match_id": "id"})
    for col in COLUNAS_SITUACAO_CHUTES:
        agg_home = agg.rename(columns={"team_id": "home_team_id", col: f"{col}_home"})
        agg_away = agg.rename(columns={"team_id": "away_team_id", col: f"{col}_away"})
        partidas = partidas.merge(agg_home[["id", "home_team_id", f"{col}_home"]], on=["id", "home_team_id"], how="left")
        partidas = partidas.merge(agg_away[["id", "away_team_id", f"{col}_away"]], on=["id", "away_team_id"], how="left")
    return partidas


def obter_situacao_chutes_por_mando(
    supabase: Client, team_ids: list[int], ultimos_n: int = JANELA_ROLLING_ML
) -> dict[int, dict[str, float]]:
    """AO VIVO: forma pré-jogo das features de situação de chute (% fast break,
    % bola parada, xG médio por chute, % gols 2º tempo) -- mesmo padrão de
    `obter_forma_recente_fotmob_por_mando`, lendo de `match_shots_fotmob`."""
    forma: dict[int, dict[str, float]] = {}
    for team_id in team_ids:
        jogos_casa = (
            supabase.table("matches").select("id").eq("status", "finished")
            .eq("home_team_id", int(team_id)).order("match_date", desc=True)
            .limit(ultimos_n).execute().data or []
        )
        jogos_fora = (
            supabase.table("matches").select("id").eq("status", "finished")
            .eq("away_team_id", int(team_id)).order("match_date", desc=True)
            .limit(ultimos_n).execute().data or []
        )

        def _buscar_e_agregar(match_ids_lista):
            if not match_ids_lista:
                return {col: {"marcado": np.nan, "sofrido": np.nan} for col in COLUNAS_SITUACAO_CHUTES}
            ids = [j["id"] for j in match_ids_lista]
            linhas = (
                supabase.table("match_shots_fotmob")
                .select("match_id, team_id, xg, situation, event_type, period")
                .in_("match_id", ids).execute().data or []
            )
            resultado: dict[str, dict[str, float]] = {}
            for col in COLUNAS_SITUACAO_CHUTES:
                marcados, sofridos = [], []
                for mid in ids:
                    proprio = [l for l in linhas if l["match_id"] == mid and l["team_id"] == team_id]
                    advers = [l for l in linhas if l["match_id"] == mid and l["team_id"] != team_id]
                    for lado, lista in [("marcado", proprio), ("sofrido", advers)]:
                        if not lista:
                            continue
                        n = len(lista)
                        if col == "pct_fast_break_fm":
                            v = sum(1 for l in lista if l.get("situation") == "FastBreak") / n
                        elif col == "pct_bola_parada_fm":
                            v = sum(1 for l in lista if l.get("situation") in _SITUACOES_BOLA_PARADA) / n
                        elif col == "xg_chute_fm":
                            vals = [l["xg"] for l in lista if l.get("xg") is not None]
                            v = float(np.mean(vals)) if vals else np.nan
                        else:  # pct_gols_2tempo_fm
                            gols = [l for l in lista if l.get("event_type") == "Goal"]
                            v = sum(1 for l in gols if l.get("period") == "2H") / len(gols) if gols else np.nan
                        (marcados if lado == "marcado" else sofridos).append(v)
                resultado[col] = {
                    "marcado": float(np.mean(marcados)) if marcados else np.nan,
                    "sofrido": float(np.mean(sofridos)) if sofridos else np.nan,
                }
            return resultado

        stats_casa = _buscar_e_agregar(jogos_casa)
        stats_fora = _buscar_e_agregar(jogos_fora)
        linha: dict[str, float] = {}
        for col in COLUNAS_SITUACAO_CHUTES:
            mapa = COLUNAS_FORMA_SITUACAO_CHUTES[col]
            linha[mapa["marcado_home"]] = stats_casa[col]["marcado"]
            linha[mapa["sofrido_home"]] = stats_casa[col]["sofrido"]
            linha[mapa["marcado_away"]] = stats_fora[col]["marcado"]
            linha[mapa["sofrido_away"]] = stats_fora[col]["sofrido"]
        forma[team_id] = linha
    return forma


def _anexar_bayesiano_por_partida(partidas: pd.DataFrame, w: int = 5, halflife_days: str = '21 days') -> pd.DataFrame:
    """Aplica o algoritmo de Regressão Bayesiana à Média (Shrinkage) c/ EWMA Temporal
    para xG, xGOT e xGA (xG sofrido).
    
    A fórmula é: stat_bayesiano = ((n * ewma_atual) + (w * prior)) / (n + w).
    Isso empurra a EWMA do time no começo da temporada para o seu
    baseline da temporada passada (ou média da liga se promovido).
    Gera colunas 'xg_bayesiano', 'xgot_bayesiano', 'xga_bayesiano' e 
    flags unificadas 'is_stat_estimated_home', 'is_stat_estimated_away'."""
    if "xg_home" not in partidas.columns or "xgot_home" not in partidas.columns:
        return partidas
        
    partidas = partidas.copy()
    partidas["_season_year"] = partidas["season"].astype(str).str[:4].astype(int)
    
    # Derivar xga (xG sofrido)
    partidas["xga_home"] = partidas["xg_away"]
    partidas["xga_away"] = partidas["xg_home"]
    
    # Desempilhar pra calcular as médias do time e da liga
    casa = partidas[["id", "league_id", "_season_year", "match_date", "home_team_id", "xg_home", "xgot_home", "xga_home"]].rename(
        columns={"home_team_id": "team_id", "xg_home": "xg", "xgot_home": "xgot", "xga_home": "xga"}
    )
    fora = partidas[["id", "league_id", "_season_year", "match_date", "away_team_id", "xg_away", "xgot_away", "xga_away"]].rename(
        columns={"away_team_id": "team_id", "xg_away": "xg", "xgot_away": "xgot", "xga_away": "xga"}
    )
    # Importante: Garantir datetime para o ewma(times=...)
    df = pd.concat([casa, fora]).sort_values(["_season_year", "team_id", "match_date"]).reset_index(drop=True)
    df["match_date"] = pd.to_datetime(df["match_date"], utc=True)
    
    # Contagem de histórico válido (n). cumcount() começa em 0.
    df["n"] = df.groupby(["_season_year", "team_id"]).cumcount()
    
    def calcular_ewma_grupo(group, col):
        if group[col].isna().all():
            return group[col]
        return group.ewm(halflife=halflife_days, times=group["match_date"])[col].mean()
        
    for col in ["xg", "xgot", "xga"]:
        # EWMA temporal (aplicando sobre todo o histórico da temporada)
        ewm_inseguro = df.groupby(["_season_year", "team_id"], group_keys=False).apply(lambda g: calcular_ewma_grupo(g, col))
        
        # Shift(1) para evitar vazamento de dados do futuro (cada partida usa o EWMA de *antes* dela)
        df[f"ewma_{col}"] = df.groupby(["_season_year", "team_id"])[ewm_inseguro.name].shift(1).fillna(0)
        
        # Calcular prior da temporada anterior
        league_season = df.groupby(["league_id", "_season_year"])[col].mean().to_dict()
        team_season = df.groupby(["team_id", "_season_year"])[col].mean().reset_index()
        team_season["_prev_season"] = team_season["_season_year"] + 1
        prior_map = team_season.set_index(["team_id", "_prev_season"])[col].to_dict()
        
        def get_prior(row):
            t_prior = prior_map.get((row["team_id"], row["_season_year"]))
            if t_prior is not None:
                return t_prior, 0
            l_prior = league_season.get((row["league_id"], row["_season_year"] - 1))
            if l_prior is not None:
                return l_prior, 1
            # Se não existir nada, usa o ultimate fallback da temporada atual da liga
            return league_season.get((row["league_id"], row["_season_year"]), 1.5), 1
            
        priors = df.apply(get_prior, axis=1, result_type="expand")
        df[f"{col}_prior"] = priors[0]
        
        # O is_stat_estimated é único e definido pelo `n` < w ou prior imputado. 
        # Podemos usar o 'xg' como driver central desta flag booleana.
        if col == "xg":
            df["is_stat_estimated"] = np.where(df["n"] < w, 1, priors[1])
            
        # Aplica a fórmula Bayesiana
        df[f"{col}_bayesiano"] = ((df["n"] * df[f"ewma_{col}"]) + (w * df[f"{col}_prior"])) / (df["n"] + w)

    # Re-pivotar
    home_cols = {"team_id": "home_team_id", "xg_bayesiano": "xg_bayesiano_home", "xgot_bayesiano": "xgot_bayesiano_home", "xga_bayesiano": "xga_bayesiano_home", "is_stat_estimated": "is_stat_estimated_home"}
    away_cols = {"team_id": "away_team_id", "xg_bayesiano": "xg_bayesiano_away", "xgot_bayesiano": "xgot_bayesiano_away", "xga_bayesiano": "xga_bayesiano_away", "is_stat_estimated": "is_stat_estimated_away"}
    
    home_merge = df[["id", "team_id", "xg_bayesiano", "xgot_bayesiano", "xga_bayesiano", "is_stat_estimated"]].rename(columns=home_cols)
    away_merge = df[["id", "team_id", "xg_bayesiano", "xgot_bayesiano", "xga_bayesiano", "is_stat_estimated"]].rename(columns=away_cols)
    
    partidas = partidas.merge(home_merge, on=["id", "home_team_id"], how="left")
    partidas = partidas.merge(away_merge, on=["id", "away_team_id"], how="left")
    partidas.drop(columns=["_season_year", "xga_home", "xga_away"], inplace=True)
    
    return partidas


COLUNAS_STATS_EXTRA = ["possession", "shots", "shots_on_target", "corners", "fouls", "yellow_cards", "red_cards"]


def _anexar_stats_extra_por_partida(supabase: Client, partidas: pd.DataFrame) -> pd.DataFrame:
    """Busca as estatísticas de jogo do FBref (`match_stats.possession`/
    `shots`/`shots_on_target`/`corners`/`fouls`/`yellow_cards`/`red_cards`)
    de cada partida e anexa como colunas `{stat}_home`/`{stat}_away` --
    mesmo espírito de `_anexar_xg_por_partida` (aliás, mesma tabela: até
    agora só a coluna `xg` dela virava feature, as outras 7 eram
    coletadas e nunca usadas por nenhum modelo). Só serve de matéria-prima
    pra forma pré-jogo (`_forma_por_mando`, ver FEATURES_NUMERICAS_V7) --
    o valor bruto da PRÓPRIA partida nunca é feature (vazaria o resultado:
    não dá pra saber quantos escanteios vai ter antes do jogo acontecer).
    """
    match_ids = partidas["id"].astype(int).tolist()

    def factory(lote, inicio, fim):
        return (
            supabase.table("match_stats")
            .select(f"match_id, team_id, {', '.join(COLUNAS_STATS_EXTRA)}")
            .in_("match_id", lote)
            .order("match_id")
            .range(inicio, fim)
        )

    linhas = _paginar_por_lotes_de_id(factory, match_ids)
    partidas = partidas.copy()
    if not linhas:
        for col in COLUNAS_STATS_EXTRA:
            partidas[f"{col}_home"] = np.nan
            partidas[f"{col}_away"] = np.nan
        return partidas

    stats = pd.DataFrame(linhas).rename(columns={"match_id": "id"})
    for col in COLUNAS_STATS_EXTRA:
        stats_home = stats.rename(columns={"team_id": "home_team_id", col: f"{col}_home"})
        stats_away = stats.rename(columns={"team_id": "away_team_id", col: f"{col}_away"})
        partidas = partidas.merge(stats_home[["id", "home_team_id", f"{col}_home"]], on=["id", "home_team_id"], how="left")
        partidas = partidas.merge(stats_away[["id", "away_team_id", f"{col}_away"]], on=["id", "away_team_id"], how="left")
    return partidas


COLUNAS_FORMA_GOLS = {
    "marcado_home": "media_gols_marcados_5j_home",
    "sofrido_home": "media_gols_sofridos_5j_home",
    "marcado_away": "media_gols_marcados_5j_away",
    "sofrido_away": "media_gols_sofridos_5j_away",
}
COLUNAS_FORMA_XG = {
    "marcado_home": "media_xg_5j_home",
    "sofrido_home": "media_xg_sofrido_5j_home",
    "marcado_away": "media_xg_5j_away",
    "sofrido_away": "media_xg_sofrido_5j_away",
}
COLUNAS_FORMA_XGOT = {
    "marcado_home": "media_xgot_5j_home",
    "sofrido_home": "media_xgot_sofrido_5j_home",
    "marcado_away": "media_xgot_5j_away",
    "sofrido_away": "media_xgot_sofrido_5j_away",
}

# v7 (estatísticas de jogo do FBref, `match_stats`): mesmo espírito de
# COLUNAS_FORMA_GOLS/XG, uma forma pré-jogo (média móvel 5 jogos, separada
# por mando) por estatística -- "sofrido" aqui é sempre "o que o adversário
# fez NAQUELA partida", útil mesmo pra posse/faltas/cartões (ex.: cartão
# sofrido = quanto o adversário costuma tomar de cartão jogando contra este
# time). Colunas brutas por partida (`possession_home`/`_away` etc.) vêm de
# `_anexar_stats_extra_por_partida`.
COLUNAS_FORMA_POSSE = {
    "marcado_home": "media_posse_5j_home",
    "sofrido_home": "media_posse_sofrida_5j_home",
    "marcado_away": "media_posse_5j_away",
    "sofrido_away": "media_posse_sofrida_5j_away",
}
COLUNAS_FORMA_CHUTES = {
    "marcado_home": "media_chutes_5j_home",
    "sofrido_home": "media_chutes_sofridos_5j_home",
    "marcado_away": "media_chutes_5j_away",
    "sofrido_away": "media_chutes_sofridos_5j_away",
}
COLUNAS_FORMA_CHUTES_ALVO = {
    "marcado_home": "media_chutes_alvo_5j_home",
    "sofrido_home": "media_chutes_alvo_sofridos_5j_home",
    "marcado_away": "media_chutes_alvo_5j_away",
    "sofrido_away": "media_chutes_alvo_sofridos_5j_away",
}
COLUNAS_FORMA_ESCANTEIOS = {
    "marcado_home": "media_escanteios_5j_home",
    "sofrido_home": "media_escanteios_sofridos_5j_home",
    "marcado_away": "media_escanteios_5j_away",
    "sofrido_away": "media_escanteios_sofridos_5j_away",
}
COLUNAS_FORMA_FALTAS = {
    "marcado_home": "media_faltas_5j_home",
    "sofrido_home": "media_faltas_sofridas_5j_home",
    "marcado_away": "media_faltas_5j_away",
    "sofrido_away": "media_faltas_sofridas_5j_away",
}
COLUNAS_FORMA_CARTOES_AMARELOS = {
    "marcado_home": "media_cartoes_amarelos_5j_home",
    "sofrido_home": "media_cartoes_amarelos_sofridos_5j_home",
    "marcado_away": "media_cartoes_amarelos_5j_away",
    "sofrido_away": "media_cartoes_amarelos_sofridos_5j_away",
}
COLUNAS_FORMA_CARTOES_VERMELHOS = {
    "marcado_home": "media_cartoes_vermelhos_5j_home",
    "sofrido_home": "media_cartoes_vermelhos_sofridos_5j_home",
    "marcado_away": "media_cartoes_vermelhos_5j_away",
    "sofrido_away": "media_cartoes_vermelhos_sofridos_5j_away",
}

# Coluna crua de `match_stats` (FBref) -> dict de saída COLUNAS_FORMA_X
# correspondente -- usado pela forma AO VIVO (v7, `obter_forma_recente_
# extra_por_mando`) pra não repetir os 7 mapeamentos na mão.
COLUNAS_FORMA_EXTRA_POR_RAW = {
    "possession": COLUNAS_FORMA_POSSE,
    "shots": COLUNAS_FORMA_CHUTES,
    "shots_on_target": COLUNAS_FORMA_CHUTES_ALVO,
    "corners": COLUNAS_FORMA_ESCANTEIOS,
    "fouls": COLUNAS_FORMA_FALTAS,
    "yellow_cards": COLUNAS_FORMA_CARTOES_AMARELOS,
    "red_cards": COLUNAS_FORMA_CARTOES_VERMELHOS,
}

# Colunas de feature dos modelos de árvore (usado também por
# `scripts/modelos_ml.py` pra garantir treino e predição com o mesmo shape).
FEATURES_NUMERICAS = [
    "elo_home",
    "elo_away",
    *COLUNAS_FORMA_GOLS.values(),
    *COLUNAS_FORMA_XG.values(),
]
CAT_FEATURES = ["liga"]
FEATURES = FEATURES_NUMERICAS + CAT_FEATURES

# Mapeamento ordinal para match_stage — usada em montar_dataset_ml_empilhado
# para criar a coluna numérica `match_stage_ord` selecionável como feature.
# Ordem crescente de "pressão/importância" do jogo.
MATCH_STAGE_ORDER = {
    "regular_season": 0,
    "group_stage":    1,
    "early_round":    2,
    "round_of_16":    3,
    "quarter_final":  4,
    "semi_final":     5,
    "final":          6,
    "playoff":        7,
}

# v2 (parâmetros de jogador): tudo da v1 + força do elenco (`squad_rating_
# home`/`_away`, ver `_carregar_squad_rating_pre_jogo`/`obter_squad_rating_
# atual`) -- dixon_coles_v1 não ganha v2 (é um modelo Poisson de força de
# TIME, não aceita feature arbitrária de jogador sem virar outro modelo).
FEATURES_NUMERICAS_V2 = FEATURES_NUMERICAS + ["squad_rating_home", "squad_rating_away"]
FEATURES_V2 = FEATURES_NUMERICAS_V2 + CAT_FEATURES

# v3 (parâmetros de fadiga): tudo da v2 + descanso pré-jogo (dias desde o
# último jogo + flag de turnaround apertado, ver `_carregar_fadiga_pre_
# jogo`/`obter_fadiga_atual`) -- mesma razão de dixon_coles_v1 ficar de
# fora da v2 vale aqui.
FEATURES_NUMERICAS_V3 = FEATURES_NUMERICAS_V2 + [
    "days_since_last_match_home",
    "days_since_last_match_away",
    "is_midweek_fatigue_home",
    "is_midweek_fatigue_away",
]
FEATURES_V3 = FEATURES_NUMERICAS_V3 + CAT_FEATURES

# v4 (parâmetro de disciplina): tudo da v3 + risco de suspensão por
# acúmulo de cartões amarelos (`cartoes_acumulados_home`/`_away` -- soma de
# cartões no ciclo atual do elenco; `jogadores_pendurados_home`/`_away` --
# quantos desses jogadores estão a 1 cartão da suspensão, ver
# `_carregar_cartoes_pre_jogo`/`obter_cartoes_atuais`) -- mesma razão de
# dixon_coles_v1 ficar de fora da v2/v3 vale aqui.
FEATURES_NUMERICAS_V4 = FEATURES_NUMERICAS_V3 + [
    "cartoes_acumulados_home",
    "cartoes_acumulados_away",
    "jogadores_pendurados_home",
    "jogadores_pendurados_away",
]
FEATURES_V4 = FEATURES_NUMERICAS_V4 + CAT_FEATURES


# =============================================================================
# v5 (contexto de campeonato): classificação, confronto direto (H2H) e
# tendência de árbitro -- os três de custo-benefício mais alto levantados
# nesta rodada: classificação e H2H são 100% deriváveis do que já está
# carregado em `partidas` (zero query nova pro treino); árbitro usa dado
# já capturado (`match_context_fotmob.referee` + `match_stats_fotmob`),
# só nunca tinha virado feature.
# =============================================================================
def _calcular_classificacao_pre_jogo(partidas: pd.DataFrame) -> pd.DataFrame:
    """Classificação (pontos/saldo de gols/posição) ANTES de cada partida
    -- calculado 100% em cima de `partidas` já carregado, separado por
    (league_id, season) pra não misturar temporada/liga diferente.
    `pontos_por_jogo`/`saldo_por_jogo` (não os valores brutos) normalizam
    times com jogo adiado/atrasado -- na mesma rodada do campeonato, times
    podem ter disputado um número diferente de jogos, e pontos brutos não
    seriam comparáveis entre eles. `jogos_disputados` fica como feature
    companheira, pra distinguir "0 pontos em 0 jogos" (sem informação
    ainda, estreia na temporada) de "0 pontos em 10 jogos" (time em crise
    real) -- os dois têm pontos_por_jogo=0.0 igual, só o companheiro
    diferencia."""
    linhas = []
    for _, grupo in partidas.groupby(["league_id", "season"]):
        grupo = grupo.sort_values("match_date")
        pontos: dict[int, int] = {}
        saldo: dict[int, int] = {}
        jogos: dict[int, int] = {}
        for row in grupo.itertuples():
            tabela = sorted(set(pontos) | {row.home_team_id, row.away_team_id}, key=lambda t: (-pontos.get(t, 0), -saldo.get(t, 0)))
            posicao = {t: i + 1 for i, t in enumerate(tabela)}
            for team_id in (row.home_team_id, row.away_team_id):
                n = jogos.get(team_id, 0)
                linhas.append(
                    {
                        "match_id": row.id,
                        "team_id": team_id,
                        "pontos_por_jogo": pontos.get(team_id, 0) / n if n > 0 else 0.0,
                        "saldo_por_jogo": saldo.get(team_id, 0) / n if n > 0 else 0.0,
                        "posicao": posicao[team_id],
                        "jogos_disputados": n,
                    }
                )
            # atualiza DEPOIS de registrar o snapshot -- nunca vaza o
            # resultado do PRÓPRIO jogo que se está tentando prever.
            h, a, hg, ag = row.home_team_id, row.away_team_id, row.home_goals, row.away_goals
            if hg > ag:
                pontos[h] = pontos.get(h, 0) + 3
            elif hg == ag:
                pontos[h] = pontos.get(h, 0) + 1
                pontos[a] = pontos.get(a, 0) + 1
            else:
                pontos[a] = pontos.get(a, 0) + 3
            saldo[h] = saldo.get(h, 0) + (hg - ag)
            saldo[a] = saldo.get(a, 0) + (ag - hg)
            jogos[h] = jogos.get(h, 0) + 1
            jogos[a] = jogos.get(a, 0) + 1
    return pd.DataFrame(linhas)


def obter_bayesiano_atual(supabase: Client, ligas_temporadas: list[tuple[int, str]], w: int = 5, halflife_days: str = '21 days') -> dict[int, dict]:
    """Calcula o xG, xGOT e xGA Bayesiano (Shrinkage) com EWMA AO VIVO para os times nas ligas/temporadas informadas.
    Puxa a temporada corrente e a anterior da liga, passa pela mesma pipeline do backend, e extrai o último valor da EWMA para as previsões futuras."""
    resultado: dict[int, dict] = {}
    for league_id, season in set(ligas_temporadas):
        curr_year = int(str(season)[:4])
        prev_season1 = f"{curr_year - 1}/{curr_year}"
        prev_season2 = f"{curr_year - 1}"
        
        partidas = (
            supabase.table("matches")
            .select("id, season, match_date, home_team_id, away_team_id")
            .eq("league_id", league_id)
            .eq("status", "finished")
            .in_("season", [season, prev_season1, prev_season2])
            .execute()
            .data
            or []
        )
        if not partidas:
            continue
            
        df = pd.DataFrame(partidas)
        # Fetch xG and xGOT
        df = _anexar_xg_por_partida(supabase, df)
        df = _anexar_xgot_por_partida(supabase, df)
        
        if "xg_home" not in df.columns or "xgot_home" not in df.columns:
            continue
            
        df["_season_year"] = df["season"].astype(str).str[:4].astype(int)
        df["xga_home"] = df["xg_away"]
        df["xga_away"] = df["xg_home"]
        
        casa = df[["home_team_id", "_season_year", "match_date", "xg_home", "xgot_home", "xga_home"]].rename(
            columns={"home_team_id": "team_id", "xg_home": "xg", "xgot_home": "xgot", "xga_home": "xga"}
        )
        fora = df[["away_team_id", "_season_year", "match_date", "xg_away", "xgot_away", "xga_away"]].rename(
            columns={"away_team_id": "team_id", "xg_away": "xg", "xgot_away": "xgot", "xga_away": "xga"}
        )
        
        long_df = pd.concat([casa, fora]).sort_values(["_season_year", "team_id", "match_date"]).reset_index(drop=True)
        long_df["match_date"] = pd.to_datetime(long_df["match_date"], utc=True)
        
        # We need prior (from prev_season)
        league_season_priors = {}
        team_season_priors = {}
        for col in ["xg", "xgot", "xga"]:
            league_season_priors[col] = long_df.groupby(["_season_year"])[col].mean().to_dict()
            team_season_priors[col] = long_df.groupby(["team_id", "_season_year"])[col].mean().to_dict()
            
        # Filter to current season to get last EWMA and n
        curr_df = long_df[long_df["_season_year"] == curr_year].copy()
        if curr_df.empty:
            continue
            
        def get_last_ewma(group, col):
            if group[col].isna().all():
                return 0.0
            return group.ewm(halflife=halflife_days, times=group["match_date"])[col].mean().iloc[-1]
            
        team_ids = curr_df["team_id"].unique()
        for team_id in team_ids:
            team_rows = curr_df[curr_df["team_id"] == team_id]
            n_next = len(team_rows) # n for the NEXT match is exactly the number of matches already played
            
            res_team = {}
            for col in ["xg", "xgot", "xga"]:
                last_ewma = get_last_ewma(team_rows, col)
                
                # Get Prior
                prior = team_season_priors[col].get((team_id, curr_year - 1))
                is_estimado = 0
                if prior is None:
                    prior = league_season_priors[col].get(curr_year - 1)
                    is_estimado = 1
                if prior is None:
                    prior = league_season_priors[col].get(curr_year, 1.5)
                    is_estimado = 1
                if n_next < w:
                    is_estimado = 1
                    
                bayes = ((n_next * last_ewma) + (w * prior)) / (n_next + w)
                res_team[f"{col}_bayesiano"] = float(bayes)
                if col == "xg":
                    res_team["is_stat_estimated"] = is_estimado
                    
            resultado[int(team_id)] = res_team
            
    return resultado


def obter_classificacao_atual(supabase: Client, ligas_temporadas: list[tuple[int, str]]) -> dict[tuple[int, int], dict]:
    """Classificação ATUAL (estado final de todos os jogos já disputados)
    de cada (league_id, season) informado -- pra montar a feature de
    fixtures futuras. Mesma lógica de `_calcular_classificacao_pre_jogo`,
    só que o que importa é o estado FINAL da temporada corrente (todos os
    jogos até agora), não um snapshot por partida histórica. Chave do
    retorno é (league_id, team_id) -- um time pode aparecer em mais de
    uma liga/temporada ao longo do histórico, mas dentro de uma rodada de
    fixtures futuras só entra na (league_id, season) certa."""
    resultado: dict[tuple[int, int], dict] = {}
    for league_id, season in set(ligas_temporadas):
        partidas = (
            supabase.table("matches")
            .select("home_team_id, away_team_id, home_goals, away_goals")
            .eq("league_id", league_id)
            .eq("season", season)
            .eq("status", "finished")
            .execute()
            .data
            or []
        )
        pontos: dict[int, int] = {}
        saldo: dict[int, int] = {}
        jogos: dict[int, int] = {}
        for p in partidas:
            h, a, hg, ag = p["home_team_id"], p["away_team_id"], p["home_goals"], p["away_goals"]
            if hg > ag:
                pontos[h] = pontos.get(h, 0) + 3
            elif hg == ag:
                pontos[h] = pontos.get(h, 0) + 1
                pontos[a] = pontos.get(a, 0) + 1
            else:
                pontos[a] = pontos.get(a, 0) + 3
            saldo[h] = saldo.get(h, 0) + (hg - ag)
            saldo[a] = saldo.get(a, 0) + (ag - hg)
            jogos[h] = jogos.get(h, 0) + 1
            jogos[a] = jogos.get(a, 0) + 1
        tabela = sorted(set(pontos) | set(jogos), key=lambda t: (-pontos.get(t, 0), -saldo.get(t, 0)))
        posicao = {t: i + 1 for i, t in enumerate(tabela)}
        for team_id in tabela:
            n = jogos.get(team_id, 0)
            resultado[(league_id, team_id)] = {
                "pontos_por_jogo": pontos.get(team_id, 0) / n if n > 0 else 0.0,
                "saldo_por_jogo": saldo.get(team_id, 0) / n if n > 0 else 0.0,
                "posicao": posicao[team_id],
                "jogos_disputados": n,
            }
    return resultado


def resumir_h2h(historico: list[tuple[int, int, int, int]], home_team_id: int) -> tuple[float, float, int]:
    """Resume uma lista de confrontos ANTERIORES (`(home_id, away_id,
    home_goals, away_goals)`, qualquer lado histórico) da perspectiva de
    quem é mandante NESTA partida (`home_team_id`) -- compartilhado pelo
    caminho de treino (`_calcular_h2h_pre_jogo`) e o AO VIVO
    (`obter_h2h_atual`). Sem confronto anterior: taxa de vitória neutra
    (0.5, não 0 -- "sem informação" é diferente de "sempre perdeu") e
    média de gols NaN (não dá pra estimar sem nenhum jogo)."""
    n = len(historico)
    if n == 0:
        return 0.5, float("nan"), 0
    vitorias, soma_gols = 0, 0
    for h_home, h_away, h_hg, h_ag in historico:
        gm, ga = (h_hg, h_ag) if h_home == home_team_id else (h_ag, h_hg)
        if gm > ga:
            vitorias += 1
        soma_gols += h_hg + h_ag
    return vitorias / n, soma_gols / n, n


def _calcular_h2h_pre_jogo(partidas: pd.DataFrame) -> pd.DataFrame:
    """Confronto direto ANTERIOR entre os 2 times de cada partida (dentro
    do próprio escopo do dataset "Feature Stacked" -- não busca partidas
    de copas continentais fora desse recorte) -- ponto-no-tempo real (só
    conta encontros com `match_date` ANTERIOR ao jogo que se está
    montando a feature). Uma linha por `match_id` (não por time -- é uma
    característica do CONFRONTO, entra igual pros dois lados no dataset
    final)."""
    df = partidas[["id", "match_date", "home_team_id", "away_team_id", "home_goals", "away_goals"]].copy()
    df["par"] = [tuple(sorted((h, a))) for h, a in zip(df["home_team_id"], df["away_team_id"])]
    df = df.sort_values("match_date")

    linhas = []
    for _, grupo in df.groupby("par"):
        historico: list[tuple[int, int, int, int]] = []
        for row in grupo.itertuples():
            taxa_vitoria_mandante, media_gols, n = resumir_h2h(historico, row.home_team_id)
            linhas.append(
                {
                    "match_id": row.id,
                    "h2h_taxa_vitoria_mandante": taxa_vitoria_mandante,
                    "h2h_media_gols": media_gols,
                    "h2h_n_jogos": n,
                }
            )
            historico.append((row.home_team_id, row.away_team_id, row.home_goals, row.away_goals))
    return pd.DataFrame(linhas)


def obter_h2h_atual(supabase: Client, pares_de_times: list[tuple[int, int]]) -> dict[tuple[int, int], list[tuple[int, int, int, int]]]:
    """Histórico de confronto direto ATÉ HOJE pra cada par de times de
    fixtures futuras -- devolve a lista crua de confrontos anteriores
    (mesmo formato de `resumir_h2h`), chaveada por par ORDENADO
    (min,max) de team_id -- o caller (`montar_features_fixtures`) decide
    qual dos dois é mandante NESTA partida e chama `resumir_h2h`. Ao
    contrário da fadiga, o corte ponto-no-tempo aqui é sempre "agora"
    (única referência que importa pra fixtures futuras -- H2H não muda
    entre uma fixture de amanhã e uma da semana que vem)."""
    resultado: dict[tuple[int, int], list[tuple[int, int, int, int]]] = {}
    for par in {tuple(sorted(p)) for p in pares_de_times}:
        time_a, time_b = par
        linhas = (
            supabase.table("matches")
            .select("home_team_id, away_team_id, home_goals, away_goals")
            .eq("status", "finished")
            .or_(f"and(home_team_id.eq.{time_a},away_team_id.eq.{time_b}),and(home_team_id.eq.{time_b},away_team_id.eq.{time_a})")
            .execute()
            .data
            or []
        )
        resultado[par] = [(l["home_team_id"], l["away_team_id"], l["home_goals"], l["away_goals"]) for l in linhas]
    return resultado


def _carregar_arbitro_pre_jogo(supabase: Client, partidas: pd.DataFrame) -> pd.DataFrame:
    """Tendência do árbitro (cartões/faltas médios por jogo QUE ELE
    apitou) ANTES de cada partida -- ponto-no-tempo real (`.shift(1)`
    antes do `.expanding()`, mesma disciplina de `_forma_por_mando`: nunca
    inclui o próprio jogo). Chaveado por NOME do árbitro
    (`match_context_fotmob.referee` não tem ID estável na fonte) -- risco
    leve de homônimos raros ficarem misturados, aceito (mesmo espírito de
    outras simplificações documentadas neste módulo). Cobertura real
    ~61% no escopo de treino (checado direto no banco antes de
    implementar) -- NaN-tolerante como o resto, uma linha por `match_id`
    (característica do jogo/árbitro, não por time)."""
    vazio = pd.DataFrame(columns=["match_id", "arbitro_cartoes_media", "arbitro_faltas_media", "arbitro_n_jogos"])
    match_ids = partidas["id"].astype(int).tolist()
    if not match_ids:
        return vazio

    def factory_contexto(lote, inicio, fim):
        return (
            supabase.table("match_context_fotmob")
            .select("match_id, referee")
            .in_("match_id", lote)
            .order("match_id")
            .range(inicio, fim)
        )

    contexto = pd.DataFrame(_paginar_por_lotes_de_id(factory_contexto, match_ids))
    if contexto.empty or "referee" not in contexto.columns:
        return vazio
    contexto = contexto[contexto["referee"].notna()].copy()
    if contexto.empty:
        return vazio

    def factory_stats(lote, inicio, fim):
        return (
            supabase.table("match_stats_fotmob")
            .select("match_id, yellow_cards, red_cards, fouls_committed")
            .in_("match_id", lote)
            .order("match_id")
            .range(inicio, fim)
        )

    stats = pd.DataFrame(_paginar_por_lotes_de_id(factory_stats, contexto["match_id"].astype(int).tolist()))
    if stats.empty:
        return vazio
    por_jogo = (
        stats.groupby("match_id")
        .agg(
            total_cartoes=("yellow_cards", lambda s: float(s.fillna(0).sum())),
            total_vermelhos=("red_cards", lambda s: float(s.fillna(0).sum())),
            total_faltas=("fouls_committed", lambda s: float(s.fillna(0).sum())),
        )
        .reset_index()
    )
    por_jogo["total_cartoes"] = por_jogo["total_cartoes"] + por_jogo["total_vermelhos"]

    base = contexto.merge(por_jogo, on="match_id", how="inner")
    base = base.merge(partidas[["id", "match_date"]].rename(columns={"id": "match_id"}), on="match_id", how="inner")
    base = base.sort_values(["referee", "match_date"])

    base["arbitro_cartoes_media"] = base.groupby("referee")["total_cartoes"].transform(lambda s: s.shift(1).expanding().mean())
    base["arbitro_faltas_media"] = base.groupby("referee")["total_faltas"].transform(lambda s: s.shift(1).expanding().mean())
    base["arbitro_n_jogos"] = base.groupby("referee").cumcount()

    return base[["match_id", "arbitro_cartoes_media", "arbitro_faltas_media", "arbitro_n_jogos"]]


def obter_arbitro_atual(supabase: Client, match_ids: list[int]) -> dict[int, dict]:
    """Tendência do árbitro pra fixtures futuras -- BEST-EFFORT: escalação
    de árbitro só costuma ser divulgada pouco antes do jogo, então isso
    fica sem dado (dict vazio) pra quase toda fixture agendada (checado
    direto no banco: 8 de 3139 partidas agendadas têm árbitro capturado
    hoje) -- não é bug, é limitação real da fonte (não tem como prever
    quem vai apitar). Quando o árbitro É conhecido, calcula a mesma
    média histórica de `_carregar_arbitro_pre_jogo`."""
    if not match_ids:
        return {}
    contexto = supabase.table("match_context_fotmob").select("match_id, referee").in_("match_id", match_ids).execute().data or []
    nomes = {c["match_id"]: c["referee"] for c in contexto if c.get("referee")}
    if not nomes:
        return {}

    arbitros = list(set(nomes.values()))
    linhas_contexto = supabase.table("match_context_fotmob").select("match_id, referee").in_("referee", arbitros).execute().data or []
    referee_por_match = {l["match_id"]: l["referee"] for l in linhas_contexto}
    # Exclui TODO o lote de fixtures sendo previsto do próprio histórico --
    # `status='scheduled'` normalmente implica que `match_stats_fotmob`
    # ainda não existe pra essa partida (cartão/falta só existe depois do
    # jogo), mas já foi confirmado direto no banco que ~8 partidas têm
    # `status` desatualizado (jogo já aconteceu, `match_stats_fotmob`
    # completo, `status` nunca virou `finished`) -- sem essa exclusão
    # explícita, uma fixture com esse problema vazaria o próprio resultado
    # pra dentro da própria média (a suposição de "scheduled = sem stats
    # ainda" deixaria de proteger). Excluir o LOTE inteiro (não só a
    # própria fixture) também evita uma partida do lote contaminar a média
    # usada pra outra do mesmo lote, mesmo problema de outro ângulo.
    ids_do_lote = set(match_ids)
    match_ids_historico = [m for m in referee_por_match if m not in ids_do_lote]
    linhas_stats = (
        (
            supabase.table("match_stats_fotmob")
            .select("match_id, yellow_cards, red_cards, fouls_committed")
            .in_("match_id", match_ids_historico)
            .execute()
            .data
            or []
        )
        if match_ids_historico
        else []
    )

    # `match_stats_fotmob` tem uma linha POR TIME por partida -- soma as
    # duas linhas de cada `match_id` ANTES de acumular por árbitro, senão
    # "n" conta aparição de time (2x por partida) em vez de partida, e as
    # médias saem pela metade do valor real (mesmo agrupamento de
    # `_carregar_arbitro_pre_jogo`, que usa `groupby("match_id").agg(sum)`).
    por_jogo: dict[int, dict] = {}
    for linha in linhas_stats:
        totais = por_jogo.setdefault(linha["match_id"], {"cartoes": 0.0, "faltas": 0.0})
        totais["cartoes"] += (linha.get("yellow_cards") or 0) + (linha.get("red_cards") or 0)
        totais["faltas"] += linha.get("fouls_committed") or 0

    acumulado: dict[str, dict] = {}
    for match_id, totais in por_jogo.items():
        arb = referee_por_match.get(match_id)
        if not arb:
            continue
        est = acumulado.setdefault(arb, {"cartoes": 0.0, "faltas": 0.0, "n": 0})
        est["cartoes"] += totais["cartoes"]
        est["faltas"] += totais["faltas"]
        est["n"] += 1

    resultado: dict[int, dict] = {}
    for match_id, nome in nomes.items():
        est = acumulado.get(nome)
        if not est or est["n"] == 0:
            continue
        resultado[match_id] = {
            "arbitro_cartoes_media": est["cartoes"] / est["n"],
            "arbitro_faltas_media": est["faltas"] / est["n"],
            "arbitro_n_jogos": est["n"],
        }
    return resultado


def _carregar_titular_pre_jogo(supabase: Client, partidas: pd.DataFrame) -> pd.DataFrame:
    """Força do XI TITULAR confirmado de cada partida histórica (v3B,
    feature `titular_rating_home`/`_away` + `titular_valor_mercado_home`/
    `_away`) -- ao contrário de `_carregar_squad_rating_pre_jogo` (v2, usa
    TODO o elenco que jogou, ponderado por minutos, inclusive quem entrou
    do banco), aqui é estritamente quem começou (`match_lineup_fotmob.
    is_starter=true`) -- o sinal que estaria disponível pra quem visse a
    escalação confirmada ANTES do apito inicial, sem misturar impacto de
    substituição.

    Rating: `player_rating_history.rating_antes` (mesmo ponto-no-tempo real
    de `_carregar_squad_rating_pre_jogo`) -- média simples do XI (sem peso
    de minutos: um titular é um titular, não tem "menos titular").

    Valor de mercado NA DATA DO JOGO (não o valor atual/mais recente, que
    vazaria valorização/desvalorização POSTERIOR à partida): `merge_asof`
    pareia cada jogador com o snapshot de `player_market_value_history`
    mais recente com `value_date <= match_date`, agrupado por `player_id`
    -- mesmo princípio de ponto-no-tempo já usado em todo o resto deste
    módulo, aplicado a uma série temporal em vez de um valor "antes desta
    partida" pré-calculado. Soma (não média) do XI -- valor de mercado é
    aditivo por natureza (patrimônio do elenco em campo), diferente de
    rating (nota de habilidade por jogador)."""
    vazio = pd.DataFrame(columns=["match_id", "team_id", "titular_rating_antes", "titular_valor_mercado_antes",
                                    "titular_avg_age_antes", "titular_avg_height"])
    match_ids = partidas["id"].astype(int).tolist()
    if not match_ids:
        return vazio

    def factory_lineup(lote, inicio, fim):
        return (
            supabase.table("match_lineup_fotmob")
            .select("match_id, team_id, player_id")
            .eq("is_starter", True)
            .in_("match_id", lote)
            .order("match_id")
            .range(inicio, fim)
        )

    lineup = pd.DataFrame(_paginar_por_lotes_de_id(factory_lineup, match_ids))
    if lineup.empty or "player_id" not in lineup.columns:
        return vazio
    lineup = lineup[lineup["player_id"].notna()].copy()
    if lineup.empty:
        return vazio
    lineup["player_id"] = lineup["player_id"].astype(int)

    def factory_ratings(lote, inicio, fim):
        return (
            supabase.table("player_rating_history")
            .select("match_id, player_id, rating_antes")
            .in_("match_id", lote)
            .order("match_id")
            .range(inicio, fim)
        )

    ratings = pd.DataFrame(_paginar_por_lotes_de_id(factory_ratings, match_ids))
    if not ratings.empty:
        lineup = lineup.merge(ratings, on=["match_id", "player_id"], how="left")
    else:
        lineup["rating_antes"] = np.nan

    player_ids = lineup["player_id"].unique().tolist()

    def factory_valores(lote, inicio, fim):
        return (
            supabase.table("player_market_value_history")
            .select("player_id, value_date, value_eur")
            .in_("player_id", lote)
            .order("player_id")
            .range(inicio, fim)
        )

    valores = pd.DataFrame(_paginar_por_lotes_de_id(factory_valores, player_ids))

    def factory_birth(lote, inicio, fim):
        return (
            supabase.table("players")
            .select("id, birth_date")
            .in_("id", lote)
            .order("id")
            .range(inicio, fim)
        )

    def factory_height(lote, inicio, fim):
        return (
            supabase.table("player_details_fotmob")
            .select("player_id, height_cm")
            .in_("player_id", lote)
            .order("player_id")
            .range(inicio, fim)
        )

    nascimentos = pd.DataFrame(_paginar_por_lotes_de_id(factory_birth, player_ids))
    alturas = pd.DataFrame(_paginar_por_lotes_de_id(factory_height, player_ids))

    lineup = lineup.merge(partidas[["id", "match_date"]].rename(columns={"id": "match_id"}), on="match_id", how="left")
    lineup["match_date"] = pd.to_datetime(lineup["match_date"], utc=True)

    if not valores.empty:
        valores = valores.dropna(subset=["value_date"]).copy()
        valores["value_date"] = pd.to_datetime(valores["value_date"], utc=True)
        valores = valores.sort_values("value_date")
        lineup = lineup.sort_values("match_date")
        lineup = pd.merge_asof(
            lineup, valores, left_on="match_date", right_on="value_date", by="player_id", direction="backward"
        )
    else:
        lineup["value_eur"] = np.nan

    if not nascimentos.empty and "birth_date" in nascimentos.columns:
        nascimentos = nascimentos.rename(columns={"id": "player_id"}).dropna(subset=["birth_date"]).copy()
        nascimentos["birth_date"] = pd.to_datetime(nascimentos["birth_date"], utc=True)
        lineup = lineup.merge(nascimentos[["player_id", "birth_date"]], on="player_id", how="left")
        lineup["titular_age_anos"] = (
            (lineup["match_date"] - lineup["birth_date"]).dt.days / 365.25
        ).where(lineup["birth_date"].notna())
    else:
        lineup["titular_age_anos"] = np.nan

    if not alturas.empty and "height_cm" in alturas.columns:
        lineup = lineup.merge(alturas[["player_id", "height_cm"]], on="player_id", how="left")
    else:
        lineup["height_cm"] = np.nan

    agregado = (
        lineup.groupby(["match_id", "team_id"])
        .agg(
            titular_rating_antes=("rating_antes", "mean"),
            titular_valor_mercado_antes=("value_eur", "sum"),
            titular_avg_age_antes=("titular_age_anos", "mean"),
            titular_avg_height=("height_cm", "mean"),
        )
        .reset_index()
    )
    return agregado


def obter_titular_atual(supabase: Client, match_ids: list[int]) -> dict[int, dict[int, dict]]:
    """Força do XI titular pra fixtures futuras -- BEST-EFFORT: escalação
    confirmada só costuma sair ~1h antes do apito inicial, então isso fica
    sem dado (dict vazio) pra quase toda fixture agendada com dias de
    antecedência -- mesma limitação de fonte já aceita em
    `obter_arbitro_atual` (não é bug, é o `predict.yml` rodando de dias
    antes do jogo, mas a escalação só existir perto da hora). Quando JÁ
    está confirmada (rara), usa `player_ratings.rating` (rating ATUAL,
    mesmo dado de `obter_squad_rating_atual`) e o valor de mercado mais
    recente conhecido de cada titular (não precisa de ponto-no-tempo
    histórico aqui -- "agora" é a única referência que importa pra uma
    fixture futura). Devolve `{match_id: {team_id: {...}}}` -- o caller
    (`montar_features_fixtures`) escolhe o lado (casa/fora) por partida."""
    resultado: dict[int, dict[int, dict]] = {}
    if not match_ids:
        return resultado

    lineup = (
        supabase.table("match_lineup_fotmob")
        .select("match_id, team_id, player_id")
        .eq("is_starter", True)
        .in_("match_id", match_ids)
        .execute()
        .data
        or []
    )
    lineup = [l for l in lineup if l.get("player_id") is not None]
    if not lineup:
        return resultado

    player_ids = list({l["player_id"] for l in lineup})
    ratings = (
        supabase.table("player_ratings").select("player_id, rating").in_("player_id", player_ids).execute().data or []
    )
    rating_por_jogador = {r["player_id"]: r["rating"] for r in ratings}

    valores = (
        supabase.table("player_market_value_history")
        .select("player_id, value_date, value_eur")
        .in_("player_id", player_ids)
        .execute()
        .data
        or []
    )
    valor_mais_recente: dict[int, tuple[str, float]] = {}
    for v in valores:
        if v.get("value_date") is None or v.get("value_eur") is None:
            continue
        atual = valor_mais_recente.get(v["player_id"])
        if atual is None or v["value_date"] > atual[0]:
            valor_mais_recente[v["player_id"]] = (v["value_date"], float(v["value_eur"]))

    por_match_team: dict[tuple[int, int], list[dict]] = {}
    for l in lineup:
        por_match_team.setdefault((l["match_id"], l["team_id"]), []).append(l)

    for (match_id, team_id), jogadores in por_match_team.items():
        ratings_xi = [rating_por_jogador[j["player_id"]] for j in jogadores if j["player_id"] in rating_por_jogador]
        valores_xi = [valor_mais_recente[j["player_id"]][1] for j in jogadores if j["player_id"] in valor_mais_recente]
        if not ratings_xi and not valores_xi:
            continue
        resultado.setdefault(match_id, {})[team_id] = {
            "titular_rating": float(np.mean(ratings_xi)) if ratings_xi else float("nan"),
            "titular_valor_mercado": float(np.sum(valores_xi)) if valores_xi else float("nan"),
        }
    return resultado


# v5 (contexto de campeonato): tudo da v4 + classificação
# (`pontos_por_jogo`/`saldo_por_jogo`/`posicao`/`jogos_disputados`,
# ver `_calcular_classificacao_pre_jogo`/`obter_classificacao_atual`) +
# confronto direto (`h2h_taxa_vitoria_mandante`/`h2h_media_gols`/
# `h2h_n_jogos`, ver `_calcular_h2h_pre_jogo`/`obter_h2h_atual`) +
# tendência de árbitro (`arbitro_cartoes_media`/`arbitro_faltas_media`/
# `arbitro_n_jogos`, ver `_carregar_arbitro_pre_jogo`/`obter_arbitro_atual`
# -- só treino tem cobertura real, ao vivo fica NaN quase sempre) --
# dixon_coles_v1 não ganha v5 pela mesma razão de v2/v3/v4.
FEATURES_NUMERICAS_V5 = FEATURES_NUMERICAS_V4 + [
    "pontos_por_jogo_home",
    "pontos_por_jogo_away",
    "saldo_por_jogo_home",
    "saldo_por_jogo_away",
    "posicao_home",
    "posicao_away",
    "jogos_disputados_home",
    "jogos_disputados_away",
    "h2h_taxa_vitoria_mandante",
    "h2h_media_gols",
    "h2h_n_jogos",
    "arbitro_cartoes_media",
    "arbitro_faltas_media",
    "arbitro_n_jogos",
]
FEATURES_V5 = FEATURES_NUMERICAS_V5 + CAT_FEATURES

# v3B (XI titular): tudo da v5 + força do XI CONFIRMADO titular (não o
# elenco inteiro usado como proxy pela v2) -- `titular_rating_home`/`_away`
# (rating médio do XI, `_carregar_titular_pre_jogo`/`obter_titular_atual`)
# + `titular_valor_mercado_home`/`_away` (soma do valor de mercado do XI
# NA DATA DO JOGO, mesmas funções). O nome "v3B" vem do PR #114 (só a
# migração/captura, sem os modelos) -- mantido por já estar estabelecido
# no repo, mas o conjunto de features aqui é v5 + XI titular, não v2 + XI
# titular (não faria sentido competir no benchmarking sem já carregar
# classificação/H2H/árbitro, que são features de custo zero já provadas).
# Cobertura de treino depende do escopo do backfill de `match_lineup_
# fotmob` (parcial: 1-2 temporadas recentes, decisão explícita do usuário
# -- ver arquivos_do_claude/ingestao_fotmob_lineup_backfill.py) -- fora
# desse recorte fica NaN, mesmo espírito tolerante de elo/xG/squad_rating.
# Ao vivo fica NaN quase sempre (mesma limitação do árbitro: escalação
# confirmada só sai perto do apito) -- dixon_coles_v1 não ganha v3B pela
# mesma razão de v2/v3/v4/v5.
FEATURES_NUMERICAS_V3B = FEATURES_NUMERICAS_V5 + [
    "titular_rating_home",
    "titular_rating_away",
    "titular_valor_mercado_home",
    "titular_valor_mercado_away",
]
FEATURES_V3B = FEATURES_NUMERICAS_V3B + CAT_FEATURES

# v6 (progresso da temporada): tudo da v5 + `progresso_temporada` (0 a 1),
# pra o modelo distinguir início de temporada (padrões de forma ainda
# ruidosos, poucos pontos disputados) de reta final (motivação de
# título/rebaixamento, elencos possivelmente já poupando titulares). NÃO
# estende v3B (XI titular) de propósito -- cobertura de escalação é
# parcial (1-2 temporadas recentes), não faz sentido empilhar mais uma
# feature esparsa em cima de outra já esparsa sem necessidade. Camada
# NOVA em vez de adicionar em v5 direto -- v5/v3b já têm modelos
# registrados em `models_registry` com métricas de teste que dependem do
# shape de feature exato; mudar retroativamente invalidaria essas
# métricas sem gerar uma nova versão pra comparar.
FEATURES_NUMERICAS_V6 = FEATURES_NUMERICAS_V5 + ["progresso_temporada"]
FEATURES_V6 = FEATURES_NUMERICAS_V6 + CAT_FEATURES

# v7 (estatísticas de jogo do FBref): tudo da v6 + forma pré-jogo (média
# móvel 5 jogos, separada por mando) de posse de bola, chutes, chutes no
# alvo, escanteios, faltas, cartões amarelos e vermelhos -- 7 colunas de
# `match_stats` que já estavam no banco (coletadas por `ingestao_stats_
# fbref.py`) mas nunca tinham virado feature de nenhum modelo (só `xg`
# virava, via v1). Estende v6 (não v3B/XI titular) pela mesma razão de v6
# não estender v3B: são ramos independentes que partem de v5, evitar
# empilhar duas fontes esparsas (XI titular parcial + stats do FBref
# parcial) sem necessidade. Cobertura: 14 competições, majoritariamente
# 2022-2026 (checado direto no banco antes de implementar).
FEATURES_NUMERICAS_V7 = FEATURES_NUMERICAS_V6 + [
    *COLUNAS_FORMA_POSSE.values(),
    *COLUNAS_FORMA_CHUTES.values(),
    *COLUNAS_FORMA_CHUTES_ALVO.values(),
    *COLUNAS_FORMA_ESCANTEIOS.values(),
    *COLUNAS_FORMA_FALTAS.values(),
    *COLUNAS_FORMA_CARTOES_AMARELOS.values(),
    *COLUNAS_FORMA_CARTOES_VERMELHOS.values(),
]
FEATURES_V7 = FEATURES_NUMERICAS_V7 + CAT_FEATURES

# v8 (estatísticas do FotMob, `match_stats_fotmob`): tudo da v7 + forma
# pré-jogo de ~22 colunas do FotMob com cobertura quase completa em TODAS
# as temporadas 2019-2026 (checado coluna a coluna direto no banco antes de
# implementar -- ver `arquivos_do_claude/catalogo_estatisticas_
# disponiveis.md`). NÃO inclui `accurate_passes_total` (praticamente nunca
# populada -- 0 em quase toda temporada, coluna com bug de captura na fonte)
# nem `xg`/`xgot` (já usados: `xg` vem do FBref desde v1, `xgot` só como
# ALVO de regressão -- ambos irregulares no FotMob em 2019-2022, sem motivo
# de duplicar com pior cobertura). `touches_opp_box` foi adicionado em v9:
# cobertura 8% em 2019 → 63% em 2022 → 79% em 2023 → 100% em 2024+
# (aceitável com NaN-tolerância dos modelos de árvore; excluído do v8
# original mas incluído aqui retroativamente pra COLUNAS_STATS_FOTMOB servir
# de base tanto ao v8 quanto ao v9 -- v8 já tinha métricas salvas no
# registry, não re-treina). `possession`/`corners`/`fouls_committed`/
# `yellow_cards`/`red_cards` aqui são intencionalmente REDUNDANTES com as
# equivalentes do FBref (v7) -- fonte independente, útil como segundo sinal
# do mesmo fenômeno, os modelos de árvore lidam bem com colinearidade.
#
# `COLUNAS_STATS_FOTMOB` mapeia coluna bruta -> nome curto (sufixo "_fm"
# pra nunca colidir com as colunas de forma do FBref, ex.:
# media_faltas_5j_home é FBref, media_faltas_fm_5j_home é FotMob) --
# gerado em loop em vez de 22 dicts escritos à mão (mesmo padrão de
# COLUNAS_FORMA_GOLS/XG/V7, só que construído programaticamente pelo
# volume de colunas).
COLUNAS_STATS_FOTMOB = {
    "total_shots": "chutes_fm",
    "shots_on_target": "chutes_alvo_fm",
    "shots_off_target": "chutes_fora_fm",
    "shots_blocked": "chutes_bloqueados_fm",
    "shots_inside_box": "chutes_area_fm",
    "shots_outside_box": "chutes_fora_area_fm",
    "big_chances": "chances_claras_fm",
    "big_chances_missed": "chances_claras_perdidas_fm",
    "accurate_passes": "passes_certos_fm",
    "accurate_long_balls": "bolas_longas_certas_fm",
    "accurate_crosses": "cruzamentos_certos_fm",
    "tackles": "desarmes_fm",
    "interceptions": "interceptacoes_fm",
    "blocks": "bloqueios_fm",
    "clearances": "afastamentos_fm",
    "keeper_saves": "defesas_goleiro_fm",
    "duels_won": "duelos_vencidos_fm",
    "aerial_duels_won": "duelos_aereos_vencidos_fm",
    "successful_dribbles": "dribles_certos_fm",
    "fouls_committed": "faltas_fm",
    "yellow_cards": "cartoes_amarelos_fm",
    "red_cards": "cartoes_vermelhos_fm",
    "corners": "escanteios_fm",
    "touches_opp_box": "toques_area_adv_fm",
    "possession": "posse_fm",
}


def colunas_forma_fotmob(nome_curto: str) -> dict[str, str]:
    """Mesmo formato de COLUNAS_FORMA_GOLS/XG/V7 (`marcado_home`/
    `sofrido_home`/`marcado_away`/`sofrido_away` -> nome da coluna de
    saída), gerado a partir do nome curto pra evitar 22 dicts repetidos."""
    return {
        "marcado_home": f"media_{nome_curto}_5j_home",
        "sofrido_home": f"media_{nome_curto}_sofrido_5j_home",
        "marcado_away": f"media_{nome_curto}_5j_away",
        "sofrido_away": f"media_{nome_curto}_sofrido_5j_away",
    }


def _anexar_stats_fotmob_por_partida(supabase: Client, partidas: pd.DataFrame) -> pd.DataFrame:
    """Busca as colunas do FotMob listadas em `COLUNAS_STATS_FOTMOB` de
    cada partida e anexa como `{coluna}_home`/`{coluna}_away` -- mesmo
    espírito de `_anexar_stats_extra_por_partida` (FBref), só que lendo de
    `match_stats_fotmob`. Só matéria-prima pra forma pré-jogo -- o valor
    bruto da PRÓPRIA partida nunca é feature (vazaria o resultado)."""
    match_ids = partidas["id"].astype(int).tolist()
    colunas_raw = list(COLUNAS_STATS_FOTMOB.keys())

    def factory(lote, inicio, fim):
        return (
            supabase.table("match_stats_fotmob")
            .select(f"match_id, team_id, {', '.join(colunas_raw)}")
            .in_("match_id", lote)
            .order("match_id")
            .range(inicio, fim)
        )

    linhas = _paginar_por_lotes_de_id(factory, match_ids)
    partidas = partidas.copy()
    # Sufixo "_fm" já no nome da coluna CRUA (não só na forma/rolling
    # average) -- "corners"/"possession" existem em COLUNAS_STATS_EXTRA
    # (FBref) E em COLUNAS_STATS_FOTMOB, e sem esse sufixo aqui os dois
    # merges abaixo colidiam com as colunas já anexadas por
    # `_anexar_stats_extra_por_partida`, viravam "_x"/"_y" (renomeio
    # automático do pandas) e quebravam `_forma_por_mando` mais adiante
    # com KeyError.
    if not linhas:
        for col in colunas_raw:
            partidas[f"{col}_fm_home"] = np.nan
            partidas[f"{col}_fm_away"] = np.nan
        return partidas

    stats = pd.DataFrame(linhas).rename(columns={"match_id": "id"})
    for col in colunas_raw:
        stats_home = stats.rename(columns={"team_id": "home_team_id", col: f"{col}_fm_home"})
        stats_away = stats.rename(columns={"team_id": "away_team_id", col: f"{col}_fm_away"})
        partidas = partidas.merge(stats_home[["id", "home_team_id", f"{col}_fm_home"]], on=["id", "home_team_id"], how="left")
        partidas = partidas.merge(stats_away[["id", "away_team_id", f"{col}_fm_away"]], on=["id", "away_team_id"], how="left")
    return partidas


FEATURES_NUMERICAS_V8 = FEATURES_NUMERICAS_V7 + [
    col for nome_curto in COLUNAS_STATS_FOTMOB.values() for col in colunas_forma_fotmob(nome_curto).values()
]
FEATURES_V8 = FEATURES_NUMERICAS_V8 + CAT_FEATURES

# v9 — tudo da v8 + novidades:
#   • xGOT médio pré-jogo (4 features, `COLUNAS_FORMA_XGOT`): forma rolling 5j
#     separada por mando -- xGOT corrige xG por dificuldade de finalização,
#     cobertura ~77% (melhor que xG FBref 36% pois usa FotMob como fonte).
#   • Features de situação de chute (16 features, `COLUNAS_FORMA_SITUACAO_CHUTES`):
#     derivadas de `match_shots_fotmob` -- % chutes em contra-ataque, % bola
#     parada, xG médio por chute (qualidade de finalização), % gols no 2º tempo.
#   • `touches_opp_box` (4 features via `COLUNAS_STATS_FOTMOB`): toques na
#     área adversária -- cobertura 63%+ em 2022 e 100% em 2024+.
#   • Arquitetura: MLP como 4ª família + stacking LogísticaRegressão +
#     walk-forward CV por temporada (documentado em walkforward_cv_v9.py).
FEATURES_NUMERICAS_V9 = FEATURES_NUMERICAS_V8 + [
    *COLUNAS_FORMA_XGOT.values(),
    *[col for mapa in COLUNAS_FORMA_SITUACAO_CHUTES.values() for col in mapa.values()],
]
FEATURES_V9 = FEATURES_NUMERICAS_V9 + CAT_FEATURES

# v10 — tudo da v9 + features do XI titular expandidas:
#   • titular_avg_age_home/away: idade média dos titulares NA DATA DA PARTIDA,
#     calculada de players.birth_date (novo campo, migração 20260729120000).
#     Cobertura depende do backfill de birth_date (ingestao_perfil_jogador_local.py)
#     + cobertura de match_lineup_fotmob (mesma janela que v3B).
#   • titular_avg_height_home/away: altura média dos titulares (cm), de
#     player_details_fotmob.height_cm. Mesma cobertura que titular_valor_mercado.
#   • venue_capacity_home: capacidade do estádio do time da casa, de
#     teams.stadium_capacity (migração 20260729130000, ingestao_equipes_local.py).
#     Proxy do efeito casa (lotação amplifica vantagem do torcedor).
FEATURES_NUMERICAS_V10 = FEATURES_NUMERICAS_V9 + [
    "titular_avg_age_home",
    "titular_avg_age_away",
    "titular_avg_height_home",
    "titular_avg_height_away",
    "venue_capacity_home",
]
FEATURES_V10 = FEATURES_NUMERICAS_V10 + CAT_FEATURES


def _carregar_venue_capacity(supabase: Client, team_ids: list[int]) -> pd.Series:
    """Capacidade do estádio por teams.id — NaN quando não preenchido.
    Retorna Series indexada por team_id."""
    if not team_ids:
        return pd.Series(dtype=float)
    ids_str = ",".join(str(i) for i in set(team_ids))
    rows = (
        supabase.table("teams")
        .select("id, stadium_capacity")
        .in_("id", list(set(team_ids)))
        .execute()
        .data
    )
    if not rows:
        return pd.Series(dtype=float)
    return pd.Series(
        {r["id"]: r["stadium_capacity"] for r in rows if r.get("stadium_capacity") is not None},
        dtype=float,
    )


# Agrupamento de features pro painel de análise exploratória do v9 --
# cada feature de FEATURES_V9 pertence a exatamente um grupo (mapeado por
# prefixo/sufixo de nome, sem hardcoded de lista completa pra não sair de
# sincronia se novas colunas de forma forem adicionadas).
def grupo_da_feature(nome: str) -> str:
    if nome in ("elo_home", "elo_away"):
        return "Elo"
    if nome in ("xg_home", "xg_away", "xgot_home", "xgot_away"):
        return "xG direto"
    if nome in ("squad_rating_home", "squad_rating_away"):
        return "Elenco"
    if "since_last_match" in nome or "midweek" in nome:
        return "Fadiga"
    if "cartoes_acumulados" in nome or "jogadores_pendurados" in nome:
        return "Disciplina"
    if any(k in nome for k in ("posicao_", "ppg_", "saldo_gols_", "jogos_disputados_")):
        return "Tabela"
    if nome.startswith("h2h_"):
        return "H2H"
    if nome.startswith("arbitro_"):
        return "Árbitro"
    if nome == "progresso_temporada":
        return "Progresso"
    if nome == "liga":
        return "Liga (categ.)"
    if "_fm_" in nome:
        return "Stats FotMob"
    if any(k in nome for k in ("gols_marcados", "gols_sofridos")):
        return "Forma (Gols)"
    if any(k in nome for k in ("media_xg_", "xg_sofrido", "media_xgot_", "xgot_sofrido")):
        return "Forma (xG)"
    # v7 FBref stats (posse, chutes, escanteios, faltas, cartoes)
    if any(k in nome for k in ("posse_", "chutes_", "escanteios_", "faltas_", "cartoes_")):
        return "Stats FBref"
    return "Outros"


def _progresso_temporada(partidas: pd.DataFrame) -> pd.DataFrame:
    """Posição da partida no calendário da temporada, 0 (primeira rodada)
    a 1 (última) -- não usa a coluna `round` (só populada a partir de 2023
    pra várias ligas, ver CONTEXTO_PROJETO.md sobre o Brasileirão) nem
    contagem de jogos por TIME (`jogos_disputados`, já existe mas não é
    comparável entre ligas com números de rodada diferentes -- 380 jogos
    x 306). Em vez disso, ordena todas as partidas de cada (league_id,
    season) por data e usa a posição relativa nessa ordenação -- funciona
    igual em qualquer liga/temporada, sem depender de `round` estar
    preenchido."""
    partidas = partidas.sort_values("match_date").copy()
    grupo = partidas.groupby(["league_id", "season"])
    posicao = grupo.cumcount()
    tamanho = grupo["id"].transform("count")
    partidas["progresso_temporada"] = (posicao / (tamanho - 1).clip(lower=1)).round(4)
    return partidas


def montar_dataset_ml_empilhado(
    supabase: Client, anos_por_liga: int | None = 6, todas_as_ligas: bool = False
) -> pd.DataFrame:
    """Dataset "Feature Stacked": empilha as últimas `anos_por_liga`
    temporadas de CADA uma das 6 ligas do Model Benchmarking (5 de elite
    europeias + Brasileirão, ver `LIGAS_MODEL_BENCHMARKING`) em vez de usar
    15 anos de uma liga só, ganhando linhas sem trazer dinâmica tática
    datada demais de uma década atrás. Cada liga contribui só com as
    temporadas mais recentes DELA MESMA (acesso/rebaixamento faz o rótulo
    de temporada não se alinhar perfeitamente entre ligas, então o corte é
    sempre relativo à própria liga).

    `todas_as_ligas=True` troca as 6 ligas do benchmark por TODAS as linhas
    de `leagues` (ligas domésticas menores, copas nacionais/continentais,
    torneios internacionais) -- usado pelo Treino Customizado quando o
    usuário pede cobertura total do banco. `anos_por_liga=None` desliga o
    corte de temporadas recentes (histórico completo por liga) -- combinado
    com `todas_as_ligas=True` por padrão nesse caso, já que truncar
    temporadas contradiria "todas as partidas disponíveis".

    Features (todas calculadas SEM olhar o resultado do próprio jogo):
    `elo_home`/`elo_away` (rating pré-jogo, `team_elo_history`), forma de
    gols e de xG dos últimos `JANELA_ROLLING_ML` jogos -- SEPARADA por
    mando (`_home`/`_away`, ver `_forma_por_mando`) -- `liga` (categórica),
    e `squad_rating_home`/`_away` (v2 -- força do elenco que jogou,
    `_carregar_squad_rating_pre_jogo`). Fica NaN-tolerante igual elo/xG --
    cobertura é bem desigual entre ligas (elo pré-jogo: ~100% na Europa,
    ~46% no Brasileirão; xG: ~40% na Europa, ~2% no Brasileirão, checado
    direto no banco) -- mas não é bloqueante, os modelos de árvore lidam
    com NaN numérico nativamente, e só os modelos v2 de fato usam a coluna
    de squad rating.
    """
    if todas_as_ligas:
        resposta = supabase.table("leagues").select("id, name").execute()
        ligas = {linha["name"]: linha["id"] for linha in (resposta.data or [])}
    else:
        ligas = obter_ids_ligas(supabase, LIGAS_MODEL_BENCHMARKING)
    if not ligas:
        logger.error("Nenhuma liga encontrada em `leagues` pro escopo pedido (todas_as_ligas=%s).", todas_as_ligas)
        return pd.DataFrame()

    league_ids = list(ligas.values())
    nome_da_liga = {v: k for k, v in ligas.items()}

    partidas = carregar_partidas_finalizadas(supabase, league_ids)
    if partidas.empty:
        return partidas

    if anos_por_liga is not None:
        partidas_recortadas = []
        for league_id, grupo in partidas.groupby("league_id"):
            temporadas_recentes = sorted(grupo["season"].unique())[-anos_por_liga:]
            partidas_recortadas.append(grupo[grupo["season"].isin(temporadas_recentes)])
        partidas = pd.concat(partidas_recortadas, ignore_index=True).sort_values("match_date").reset_index(drop=True)
    else:
        partidas = partidas.sort_values("match_date").reset_index(drop=True)

    partidas = _anexar_xg_por_partida(supabase, partidas)
    partidas = _anexar_xgot_por_partida(supabase, partidas)
    partidas = _anexar_bayesiano_por_partida(partidas)
    partidas = _anexar_stats_extra_por_partida(supabase, partidas)
    partidas = _anexar_stats_fotmob_por_partida(supabase, partidas)
    partidas = _anexar_situacao_chutes_por_partida(supabase, partidas)
    partidas = _progresso_temporada(partidas)
    forma_gols = _forma_por_mando(partidas, "home_goals", "away_goals", COLUNAS_FORMA_GOLS)
    forma_xg = _forma_por_mando_multi_janelas(partidas, "xg_home", "xg_away", "xg")
    forma_xgot = _forma_por_mando_multi_janelas(partidas, "xgot_home", "xgot_away", "xgot")
    forma_posse = _forma_por_mando(partidas, "possession_home", "possession_away", COLUNAS_FORMA_POSSE)
    forma_chutes = _forma_por_mando(partidas, "shots_home", "shots_away", COLUNAS_FORMA_CHUTES)
    forma_chutes_alvo = _forma_por_mando(partidas, "shots_on_target_home", "shots_on_target_away", COLUNAS_FORMA_CHUTES_ALVO)
    forma_escanteios = _forma_por_mando(partidas, "corners_home", "corners_away", COLUNAS_FORMA_ESCANTEIOS)
    forma_faltas = _forma_por_mando(partidas, "fouls_home", "fouls_away", COLUNAS_FORMA_FALTAS)
    forma_cartoes_amarelos = _forma_por_mando(partidas, "yellow_cards_home", "yellow_cards_away", COLUNAS_FORMA_CARTOES_AMARELOS)
    forma_cartoes_vermelhos = _forma_por_mando(partidas, "red_cards_home", "red_cards_away", COLUNAS_FORMA_CARTOES_VERMELHOS)
    formas_fotmob = {
        nome_curto: _forma_por_mando(partidas, f"{col_raw}_fm_home", f"{col_raw}_fm_away", colunas_forma_fotmob(nome_curto))
        for col_raw, nome_curto in COLUNAS_STATS_FOTMOB.items()
    }
    formas_situacao_chutes = {
        col: _forma_por_mando(partidas, f"{col}_home", f"{col}_away", COLUNAS_FORMA_SITUACAO_CHUTES[col])
        for col in COLUNAS_SITUACAO_CHUTES
    }

    elo = _carregar_elo_pre_jogo(supabase, league_ids)
    if not elo.empty:
        elo_home = elo.rename(columns={"match_id": "id", "team_id": "home_team_id", "rating_antes": "elo_home"})
        elo_away = elo.rename(columns={"match_id": "id", "team_id": "away_team_id", "rating_antes": "elo_away"})
    else:
        elo_home = pd.DataFrame(columns=["id", "home_team_id", "elo_home"])
        elo_away = pd.DataFrame(columns=["id", "away_team_id", "elo_away"])

    base_cols = ["id", "match_date", "season", "league_id", "home_team_id", "away_team_id",
                 "home_goals", "away_goals", "xg_home", "xg_away", "xgot_home", "xgot_away",
                 "progresso_temporada"]
    for col in ("match_stage", "is_neutral"):
        if col in partidas.columns:
            base_cols.append(col)
    dataset = partidas[base_cols].copy()
    dataset["liga"] = dataset["league_id"].map(nome_da_liga)
    if "match_stage" in dataset.columns:
        dataset["match_stage_ord"] = dataset["match_stage"].map(MATCH_STAGE_ORDER).fillna(0).astype(int)
    if "is_neutral" in dataset.columns:
        dataset["is_neutral"] = dataset["is_neutral"].fillna(False).astype(int)
    dataset["resultado"] = np.select(
        [dataset["home_goals"] > dataset["away_goals"], dataset["home_goals"] == dataset["away_goals"]],
        [RESULTADO_HOME, RESULTADO_DRAW],
        default=RESULTADO_AWAY,
    )
    # Alvo binário pro mercado Over/Under 2.5 gols -- mesmas features de
    # `resultado` (elo/forma/xG pré-jogo), só troca o alvo. RESULTADO_OVER25
    # = 1 quando total de gols > 2.5.
    dataset["resultado_over25"] = (dataset["home_goals"] + dataset["away_goals"] > 2.5).astype(int)
    dataset["resultado_btts"] = ((dataset["home_goals"] > 0) & (dataset["away_goals"] > 0)).astype(int)
    # Alvo multiclasse pro mercado "faixa de gols" (0-1/2-3/4-6/7+) -- mesma
    # base de gols, sem dado novo (ver `codigo_faixa_gols`).
    dataset["resultado_faixa_gols"] = (dataset["home_goals"] + dataset["away_goals"]).apply(codigo_faixa_gols)
    dataset = dataset.merge(elo_home[["id", "home_team_id", "elo_home"]], on=["id", "home_team_id"], how="left")
    dataset = dataset.merge(elo_away[["id", "away_team_id", "elo_away"]], on=["id", "away_team_id"], how="left")
    dataset = dataset.join(forma_gols, on="id")
    dataset = dataset.join(forma_xg, on="id")
    dataset = dataset.join(forma_xgot, on="id")
    dataset = dataset.join(forma_posse, on="id")
    dataset = dataset.join(forma_chutes, on="id")
    dataset = dataset.join(forma_chutes_alvo, on="id")
    dataset = dataset.join(forma_escanteios, on="id")
    dataset = dataset.join(forma_faltas, on="id")
    dataset = dataset.join(forma_cartoes_amarelos, on="id")
    dataset = dataset.join(forma_cartoes_vermelhos, on="id")
    for forma in formas_fotmob.values():
        dataset = dataset.join(forma, on="id")
    for forma in formas_situacao_chutes.values():
        dataset = dataset.join(forma, on="id")

    squad_rating = _carregar_squad_rating_pre_jogo(supabase, partidas["id"].astype(int).tolist())
    if not squad_rating.empty:
        squad_home = squad_rating.rename(columns={"match_id": "id", "team_id": "home_team_id", "squad_rating_antes": "squad_rating_home"})
        squad_away = squad_rating.rename(columns={"match_id": "id", "team_id": "away_team_id", "squad_rating_antes": "squad_rating_away"})
    else:
        squad_home = pd.DataFrame(columns=["id", "home_team_id", "squad_rating_home"])
        squad_away = pd.DataFrame(columns=["id", "away_team_id", "squad_rating_away"])
    dataset = dataset.merge(squad_home[["id", "home_team_id", "squad_rating_home"]], on=["id", "home_team_id"], how="left")
    dataset = dataset.merge(squad_away[["id", "away_team_id", "squad_rating_away"]], on=["id", "away_team_id"], how="left")

    fadiga = _carregar_fadiga_pre_jogo(supabase, partidas["id"].astype(int).tolist())
    colunas_fadiga = ["days_since_last_match", "is_midweek_fatigue"]
    if not fadiga.empty:
        fadiga_home = fadiga.rename(columns={"match_id": "id", "team_id": "home_team_id"}).rename(
            columns={c: f"{c}_home" for c in colunas_fadiga}
        )
        fadiga_away = fadiga.rename(columns={"match_id": "id", "team_id": "away_team_id"}).rename(
            columns={c: f"{c}_away" for c in colunas_fadiga}
        )
    else:
        fadiga_home = pd.DataFrame(columns=["id", "home_team_id", *[f"{c}_home" for c in colunas_fadiga]])
        fadiga_away = pd.DataFrame(columns=["id", "away_team_id", *[f"{c}_away" for c in colunas_fadiga]])
    dataset = dataset.merge(
        fadiga_home[["id", "home_team_id", *[f"{c}_home" for c in colunas_fadiga]]], on=["id", "home_team_id"], how="left"
    )
    dataset = dataset.merge(
        fadiga_away[["id", "away_team_id", *[f"{c}_away" for c in colunas_fadiga]]], on=["id", "away_team_id"], how="left"
    )

    cartoes = _carregar_cartoes_pre_jogo(supabase, partidas, nome_da_liga)
    if not cartoes.empty:
        cartoes_home = cartoes.rename(columns={"match_id": "id", "team_id": "home_team_id"}).rename(
            columns={"cartoes_acumulados_antes": "cartoes_acumulados_home", "jogadores_pendurados_antes": "jogadores_pendurados_home"}
        )
        cartoes_away = cartoes.rename(columns={"match_id": "id", "team_id": "away_team_id"}).rename(
            columns={"cartoes_acumulados_antes": "cartoes_acumulados_away", "jogadores_pendurados_antes": "jogadores_pendurados_away"}
        )
    else:
        cartoes_home = pd.DataFrame(columns=["id", "home_team_id", "cartoes_acumulados_home", "jogadores_pendurados_home"])
        cartoes_away = pd.DataFrame(columns=["id", "away_team_id", "cartoes_acumulados_away", "jogadores_pendurados_away"])
    dataset = dataset.merge(
        cartoes_home[["id", "home_team_id", "cartoes_acumulados_home", "jogadores_pendurados_home"]], on=["id", "home_team_id"], how="left"
    )
    dataset = dataset.merge(
        cartoes_away[["id", "away_team_id", "cartoes_acumulados_away", "jogadores_pendurados_away"]], on=["id", "away_team_id"], how="left"
    )

    classificacao = _calcular_classificacao_pre_jogo(partidas)
    colunas_classificacao = ["pontos_por_jogo", "saldo_por_jogo", "posicao", "jogos_disputados"]
    if not classificacao.empty:
        classificacao_home = classificacao.rename(columns={"match_id": "id", "team_id": "home_team_id"}).rename(
            columns={c: f"{c}_home" for c in colunas_classificacao}
        )
        classificacao_away = classificacao.rename(columns={"match_id": "id", "team_id": "away_team_id"}).rename(
            columns={c: f"{c}_away" for c in colunas_classificacao}
        )
    else:
        classificacao_home = pd.DataFrame(columns=["id", "home_team_id", *[f"{c}_home" for c in colunas_classificacao]])
        classificacao_away = pd.DataFrame(columns=["id", "away_team_id", *[f"{c}_away" for c in colunas_classificacao]])
    dataset = dataset.merge(
        classificacao_home[["id", "home_team_id", *[f"{c}_home" for c in colunas_classificacao]]], on=["id", "home_team_id"], how="left"
    )
    dataset = dataset.merge(
        classificacao_away[["id", "away_team_id", *[f"{c}_away" for c in colunas_classificacao]]], on=["id", "away_team_id"], how="left"
    )

    h2h = _calcular_h2h_pre_jogo(partidas)
    if h2h.empty:
        h2h = pd.DataFrame(columns=["id", "h2h_taxa_vitoria_mandante", "h2h_media_gols", "h2h_n_jogos"])
    else:
        h2h = h2h.rename(columns={"match_id": "id"})
    dataset = dataset.merge(h2h, on="id", how="left")

    arbitro = _carregar_arbitro_pre_jogo(supabase, partidas)
    if arbitro.empty:
        arbitro = pd.DataFrame(columns=["id", "arbitro_cartoes_media", "arbitro_faltas_media", "arbitro_n_jogos"])
    else:
        arbitro = arbitro.rename(columns={"match_id": "id"})
    dataset = dataset.merge(arbitro, on="id", how="left")

    titular = _carregar_titular_pre_jogo(supabase, partidas)
    colunas_titular_base = ["titular_rating_antes", "titular_valor_mercado_antes"]
    colunas_titular_v10 = ["titular_avg_age_antes", "titular_avg_height"]
    colunas_titular = colunas_titular_base + [c for c in colunas_titular_v10 if c in titular.columns]
    if not titular.empty:
        titular_home = titular.rename(columns={"match_id": "id", "team_id": "home_team_id"}).rename(
            columns={c: f"{c.replace('_antes', '')}_home" for c in colunas_titular}
        )
        titular_away = titular.rename(columns={"match_id": "id", "team_id": "away_team_id"}).rename(
            columns={c: f"{c.replace('_antes', '')}_away" for c in colunas_titular}
        )
    else:
        titular_home = pd.DataFrame(columns=["id", "home_team_id", "titular_rating_home", "titular_valor_mercado_home",
                                             "titular_avg_age_home", "titular_avg_height_home"])
        titular_away = pd.DataFrame(columns=["id", "away_team_id", "titular_rating_away", "titular_valor_mercado_away",
                                             "titular_avg_age_away", "titular_avg_height_away"])
    cols_titular_home = [c for c in titular_home.columns if c not in ("match_id", "team_id")]
    cols_titular_away = [c for c in titular_away.columns if c not in ("match_id", "team_id")]
    dataset = dataset.merge(
        titular_home[["id", "home_team_id", *[c for c in cols_titular_home if c not in ("id", "home_team_id")]]],
        on=["id", "home_team_id"], how="left"
    )
    dataset = dataset.merge(
        titular_away[["id", "away_team_id", *[c for c in cols_titular_away if c not in ("id", "away_team_id")]]],
        on=["id", "away_team_id"], how="left"
    )

    venue_capacity = _carregar_venue_capacity(supabase, dataset["home_team_id"].dropna().astype(int).tolist())
    dataset["venue_capacity_home"] = dataset["home_team_id"].map(venue_capacity)

    # Alvo binário pro mercado Over/Under 9.5 escanteios totais -- resultado
    # real da partida (não uma feature pré-jogo), NaN quando o dado de
    # escanteio ainda não foi ingerido pra aquela partida (ver
    # `_carregar_total_corners_por_partida`) -- filtrado depois no backtest,
    # não no dataset inteiro (senão jogaria fora linhas válidas pros outros
    # mercados).
    corners = _carregar_total_corners_por_partida(supabase, partidas["id"].astype(int).tolist())
    if corners.empty:
        corners = pd.DataFrame(columns=["match_id", "total_corners"])
    dataset = dataset.merge(corners.rename(columns={"match_id": "id"}), on="id", how="left")
    dataset["resultado_corners_ou95"] = np.where(
        dataset["total_corners"].notna(), (dataset["total_corners"] > 9.5).astype(float), np.nan
    )
    dataset["resultado_faixa_corners"] = dataset["total_corners"].apply(
        lambda x: float(codigo_faixa_corners(x)) if pd.notna(x) else np.nan
    )

    dataset = dataset.rename(columns={"id": "match_id"})

    # ------------------------------------------------------------------
    # Features derivadas: diferenciais e momentos pré-jogo.
    # Calculadas após todos os joins — NaN-tolerante (subtração de NaN
    # produz NaN, que CatBoost/XGBoost/LightGBM lidam nativamente).
    # posicao_diff: positivo = mandante tem posição melhor (número menor).
    # xg_momentum: positivo = mandante acelerando; negativo = desacelerando.
    # ------------------------------------------------------------------
    dataset["elo_diff"] = dataset["elo_home"] - dataset["elo_away"]
    for _h, _a, _out in [
        ("xg_bayesiano_home",         "xg_bayesiano_away",          "xg_diff_bayesiano"),
        ("xgot_bayesiano_home",        "xgot_bayesiano_away",        "xgot_diff_bayesiano"),
        ("squad_rating_home",          "squad_rating_away",          "squad_rating_diff"),
        ("titular_rating_home",        "titular_rating_away",        "rating_diff_xi"),
        ("titular_valor_mercado_home", "titular_valor_mercado_away", "valor_diff_xi"),
        ("titular_avg_age_home",       "titular_avg_age_away",       "age_diff_xi"),
        ("titular_avg_height_home",    "titular_avg_height_away",    "height_diff_xi"),
        ("posicao_away",               "posicao_home",               "posicao_diff"),
        ("pontos_por_jogo_home",       "pontos_por_jogo_away",       "pontos_diff"),
    ]:
        if _h in dataset.columns and _a in dataset.columns:
            dataset[_out] = dataset[_h] - dataset[_a]
    for _side in ("home", "away"):
        _5j, _10j = f"xg_{_side}_5j", f"xg_{_side}_10j"
        if _5j in dataset.columns and _10j in dataset.columns:
            dataset[f"xg_momentum_{_side}"] = dataset[_5j] - dataset[_10j]

    # ------------------------------------------------------------------
    # Seleção final de colunas com filtro seguro (só inclui o que existe).
    # Substituiu o `dataset[*FEATURES_NUMERICAS_V3B]` original que causava
    # KeyError porque COLUNAS_FORMA_XG/XGOT foram substituídas pelas
    # colunas multi-janela de _forma_por_mando_multi_janelas mas a lista
    # de constantes não foi atualizada.
    # ------------------------------------------------------------------
    _COLUNAS_DESEJADAS = [
        "match_id", "match_date", "liga",
        # Elo
        "elo_home", "elo_away",
        # Forma de gols (5j, mando separado)
        *COLUNAS_FORMA_GOLS.values(),
        # xG e xGOT multi-janela (5j / 10j / 20j + EWMA)
        *[
            f"xg_{s}_{j}"
            for s in ("home", "sofrido_home", "away", "sofrido_away")
            for j in ("5j", "10j", "20j", "5j_decay", "10j_decay", "20j_decay")
        ],
        *[
            f"xgot_{s}_{j}"
            for s in ("home", "sofrido_home", "away", "sofrido_away")
            for j in ("5j", "10j", "20j", "5j_decay", "10j_decay", "20j_decay")
        ],
        # xG / xGOT / xGA Bayesiano e flags de estimativa
        "xg_bayesiano_home", "xg_bayesiano_away",
        "xga_bayesiano_home", "xga_bayesiano_away",
        "xgot_bayesiano_home", "xgot_bayesiano_away",
        "is_stat_estimated_home", "is_stat_estimated_away",
        # Força do elenco
        "squad_rating_home", "squad_rating_away",
        # Fadiga
        "days_since_last_match_home", "days_since_last_match_away",
        "is_midweek_fatigue_home", "is_midweek_fatigue_away",
        # Disciplina / cartões
        "cartoes_acumulados_home", "cartoes_acumulados_away",
        "jogadores_pendurados_home", "jogadores_pendurados_away",
        # Classificação
        "pontos_por_jogo_home", "pontos_por_jogo_away",
        "saldo_por_jogo_home", "saldo_por_jogo_away",
        "posicao_home", "posicao_away",
        "jogos_disputados_home", "jogos_disputados_away",
        # H2H
        "h2h_taxa_vitoria_mandante", "h2h_media_gols", "h2h_n_jogos",
        # Árbitro
        "arbitro_cartoes_media", "arbitro_faltas_media", "arbitro_n_jogos",
        # XI Titular (v3B + v10)
        "titular_rating_home", "titular_rating_away",
        "titular_valor_mercado_home", "titular_valor_mercado_away",
        "titular_avg_age_home", "titular_avg_age_away",
        "titular_avg_height_home", "titular_avg_height_away",
        # Venue / contexto de temporada
        "venue_capacity_home",
        "progresso_temporada",
        # FBref (v7)
        *COLUNAS_FORMA_POSSE.values(),
        *COLUNAS_FORMA_CHUTES.values(),
        *COLUNAS_FORMA_CHUTES_ALVO.values(),
        *COLUNAS_FORMA_ESCANTEIOS.values(),
        *COLUNAS_FORMA_FALTAS.values(),
        *COLUNAS_FORMA_CARTOES_AMARELOS.values(),
        *COLUNAS_FORMA_CARTOES_VERMELHOS.values(),
        # FotMob (v8)
        *[col for nome_curto in COLUNAS_STATS_FOTMOB.values() for col in colunas_forma_fotmob(nome_curto).values()],
        # Situação de chutes FotMob (v9)
        *[col for mapa in COLUNAS_FORMA_SITUACAO_CHUTES.values() for col in mapa.values()],
        # Features derivadas (v11)
        "elo_diff",
        "xg_diff_bayesiano", "xgot_diff_bayesiano",
        "squad_rating_diff",
        "rating_diff_xi", "valor_diff_xi", "age_diff_xi", "height_diff_xi",
        "xg_momentum_home", "xg_momentum_away",
        "posicao_diff", "pontos_diff",
        # Alvos
        "resultado", "resultado_over25", "resultado_btts", "resultado_faixa_gols", "resultado_corners_ou95", "resultado_faixa_corners",
        # xG/xGOT observados (somente como alvo de regressão, NÃO como features)
        "xg_home", "xg_away", "xgot_home", "xgot_away",
    ]
    dataset = dataset[[c for c in _COLUNAS_DESEJADAS if c in dataset.columns]]

    # NaN em elo/xG (times/temporadas sem essa fonte -- ver
    # `_anexar_xg_por_partida`) fica como está: CatBoost/XGBoost/LightGBM
    # lidam nativamente com NaN numérico. Só removemos as linhas sem forma
    # de GOLS (estreia do time NESSE dataset, sem nenhum jogo anterior pra
    # calcular média) -- essas, sim, não têm informação nenhuma pro modelo.
    return dataset.dropna(subset=["media_gols_marcados_5j_home", "media_gols_marcados_5j_away"]).reset_index(drop=True)


# =============================================================================
# Split cronológico (treino / validação / teste out-of-sample)
# =============================================================================
def split_cronologico(
    df: pd.DataFrame,
    treino: float = 0.6,
    validacao: float = 0.2,
    teste: float = 0.2,
    col_data: str = "match_date",
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Divide cronologicamente -- nunca aleatório, isso vazaria informação
    do futuro pro treino. As linhas mais ANTIGAS (60%) vão pro treino
    inicial das árvores, a fatia seguinte (20%) pra validação/tuning de
    hiperparâmetro, e as mais RECENTES (20%) ficam de fora de treino e
    tuning -- é nelas que roda a simulação de banca out-of-sample
    (`backtest_kelly.py`)."""
    if abs(treino + validacao + teste - 1.0) > 1e-6:
        raise ValueError("treino + validacao + teste precisa somar 1.0")

    ordenado = df.sort_values(col_data).reset_index(drop=True)
    n = len(ordenado)
    # cortes cumulativos (não dois `int()` truncados separados e somados)
    # -- senão sobra sistematicamente 1 linha "perdida" no teste mesmo
    # quando `teste=0.0` (ex.: n=128, treino=0.8→102, validacao=0.2→25,
    # 102+25=127 ≠ 128). Com corte cumulativo, teste=0.0 vira
    # corte_validacao=round(n*1.0)=n, ou seja test_df fica vazio de verdade.
    corte_treino = round(n * treino)
    corte_validacao = round(n * (treino + validacao))

    train_df = ordenado.iloc[:corte_treino]
    val_df = ordenado.iloc[corte_treino:corte_validacao]
    test_df = ordenado.iloc[corte_validacao:]

    def _intervalo(bloco: pd.DataFrame) -> str:
        if bloco.empty:
            return "vazio"
        return f"{bloco[col_data].min()} a {bloco[col_data].max()}"

    logger.info(
        "Split cronológico: treino=%d (%s) | validação=%d (%s) | teste=%d (%s)",
        len(train_df), _intervalo(train_df),
        len(val_df), _intervalo(val_df),
        len(test_df), _intervalo(test_df),
    )
    return train_df, val_df, test_df
