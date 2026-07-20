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
    "Feature Stacked" entre ligas de elite equivalentes -- em vez de buscar
    15 anos de uma liga só, empilha os últimos N anos de cada uma das 5
    ligas europeias de elite, ganhando linhas sem trazer dinâmica tática
    datada demais. Ver `montar_dataset_ml_empilhado`.
  - `split_cronologico`: 60% treino / 20% validação / 20% teste (out-of-
    -sample), sempre por ORDEM DE DATA -- nunca aleatório, senão vazaria
    informação do futuro pro treino.

Todas as consultas ao Supabase paginam de verdade (`.range()`): sem isso o
PostgREST corta silenciosamente em 1000 linhas, e é fácil passar disso aqui
(7 temporadas x 5 ligas x ~380 jogos = mais de 10 mil linhas).
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
# direta ao banco, não adivinhado).
LIGAS_ELITE_EUROPEIAS = ["Premier League", "La Liga", "Serie A (Itália)", "Bundesliga", "Ligue 1"]

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
            .select("id, league_id, season, match_date, home_team_id, away_team_id, home_goals, away_goals")
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
    tipicamente os últimos 5 jogos de casa OU de fora de um time só) --
    escala pequena o bastante pra não precisar de lote/paginação."""
    if not match_ids:
        return {"marcado": np.nan, "sofrido": np.nan}
    linhas = supabase.table("match_stats").select("match_id, team_id, xg").in_("match_id", match_ids).execute().data or []
    marcado = [l["xg"] for l in linhas if l["team_id"] == team_id and l["xg"] is not None]
    sofrido = [l["xg"] for l in linhas if l["team_id"] != team_id and l["xg"] is not None]
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
        xg_casa = _xg_marcado_sofrido(supabase, [j["id"] for j in jogos_casa], team_id)
        xg_fora = _xg_marcado_sofrido(supabase, [j["id"] for j in jogos_fora], team_id)

        forma[team_id] = {
            "media_gols_marcados_5j_home": float(np.mean([j["home_goals"] for j in jogos_casa])) if jogos_casa else np.nan,
            "media_gols_sofridos_5j_home": float(np.mean([j["away_goals"] for j in jogos_casa])) if jogos_casa else np.nan,
            "media_gols_marcados_5j_away": float(np.mean([j["away_goals"] for j in jogos_fora])) if jogos_fora else np.nan,
            "media_gols_sofridos_5j_away": float(np.mean([j["home_goals"] for j in jogos_fora])) if jogos_fora else np.nan,
            "media_xg_5j_home": xg_casa["marcado"],
            "media_xg_sofrido_5j_home": xg_casa["sofrido"],
            "media_xg_5j_away": xg_fora["marcado"],
            "media_xg_sofrido_5j_away": xg_fora["sofrido"],
        }
    return forma


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
    """Busca o xG observado (`match_stats.xg`, Understat/FBref) de cada
    partida e anexa como colunas `xg_home`/`xg_away`. Só cobre 2022+ nas 5
    ligas de elite europeias (confirmado por consulta direta antes de usar
    -- 2019-2021 não têm NENHUMA linha em `match_stats` pra essas ligas, e
    2022 tem chutes mas não xG) -- fica NaN fora dessa cobertura, sem
    quebrar nada."""
    match_ids = partidas["id"].astype(int).tolist()

    def factory(lote, inicio, fim):
        return supabase.table("match_stats").select("match_id, team_id, xg").in_("match_id", lote).order("match_id").range(inicio, fim)

    linhas = _paginar_por_lotes_de_id(factory, match_ids)
    partidas = partidas.copy()
    if not linhas:
        partidas["xg_home"] = np.nan
        partidas["xg_away"] = np.nan
        return partidas

    stats = pd.DataFrame(linhas).rename(columns={"match_id": "id"})
    stats_home = stats.rename(columns={"team_id": "home_team_id", "xg": "xg_home"})
    stats_away = stats.rename(columns={"team_id": "away_team_id", "xg": "xg_away"})

    partidas = partidas.merge(stats_home[["id", "home_team_id", "xg_home"]], on=["id", "home_team_id"], how="left")
    partidas = partidas.merge(stats_away[["id", "away_team_id", "xg_away"]], on=["id", "away_team_id"], how="left")
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

# v2 (parâmetros de jogador): tudo da v1 + força do elenco (`squad_rating_
# home`/`_away`, ver `_carregar_squad_rating_pre_jogo`/`obter_squad_rating_
# atual`) -- dixon_coles_v1 não ganha v2 (é um modelo Poisson de força de
# TIME, não aceita feature arbitrária de jogador sem virar outro modelo).
FEATURES_NUMERICAS_V2 = FEATURES_NUMERICAS + ["squad_rating_home", "squad_rating_away"]
FEATURES_V2 = FEATURES_NUMERICAS_V2 + CAT_FEATURES


def montar_dataset_ml_empilhado(supabase: Client, anos_por_liga: int = 6) -> pd.DataFrame:
    """Dataset "Feature Stacked": empilha as últimas `anos_por_liga`
    temporadas de CADA uma das 5 ligas de elite europeias (em vez de usar
    15 anos de uma liga só), ganhando linhas sem trazer dinâmica tática
    datada demais de uma década atrás. Cada liga contribui só com as
    temporadas mais recentes DELA MESMA (acesso/rebaixamento faz o rótulo
    de temporada não se alinhar perfeitamente entre ligas, então o corte é
    sempre relativo à própria liga).

    Features (todas calculadas SEM olhar o resultado do próprio jogo):
    `elo_home`/`elo_away` (rating pré-jogo, `team_elo_history`), forma de
    gols e de xG dos últimos `JANELA_ROLLING_ML` jogos -- SEPARADA por
    mando (`_home`/`_away`, ver `_forma_por_mando`) -- `liga` (categórica),
    e `squad_rating_home`/`_away` (v2 -- força do elenco que jogou,
    `_carregar_squad_rating_pre_jogo`). Fica NaN-tolerante igual elo/xG:
    cobre >99,9% das partidas das 5 ligas de elite (checado direto no
    banco), mas não é bloqueante -- os modelos de árvore lidam com NaN
    numérico nativamente, e só os modelos v2 de fato usam essa coluna.
    """
    ligas = obter_ids_ligas(supabase, LIGAS_ELITE_EUROPEIAS)
    if not ligas:
        logger.error("Nenhuma das ligas de elite foi encontrada em `leagues`.")
        return pd.DataFrame()

    league_ids = list(ligas.values())
    nome_da_liga = {v: k for k, v in ligas.items()}

    partidas = carregar_partidas_finalizadas(supabase, league_ids)
    if partidas.empty:
        return partidas

    partidas_recortadas = []
    for league_id, grupo in partidas.groupby("league_id"):
        temporadas_recentes = sorted(grupo["season"].unique())[-anos_por_liga:]
        partidas_recortadas.append(grupo[grupo["season"].isin(temporadas_recentes)])
    partidas = pd.concat(partidas_recortadas, ignore_index=True).sort_values("match_date").reset_index(drop=True)

    partidas = _anexar_xg_por_partida(supabase, partidas)
    forma_gols = _forma_por_mando(partidas, "home_goals", "away_goals", COLUNAS_FORMA_GOLS)
    forma_xg = _forma_por_mando(partidas, "xg_home", "xg_away", COLUNAS_FORMA_XG)

    elo = _carregar_elo_pre_jogo(supabase, league_ids)
    if not elo.empty:
        elo_home = elo.rename(columns={"match_id": "id", "team_id": "home_team_id", "rating_antes": "elo_home"})
        elo_away = elo.rename(columns={"match_id": "id", "team_id": "away_team_id", "rating_antes": "elo_away"})
    else:
        elo_home = pd.DataFrame(columns=["id", "home_team_id", "elo_home"])
        elo_away = pd.DataFrame(columns=["id", "away_team_id", "elo_away"])

    dataset = partidas[["id", "match_date", "league_id", "home_team_id", "away_team_id", "home_goals", "away_goals"]].copy()
    dataset["liga"] = dataset["league_id"].map(nome_da_liga)
    dataset["resultado"] = np.select(
        [dataset["home_goals"] > dataset["away_goals"], dataset["home_goals"] == dataset["away_goals"]],
        [RESULTADO_HOME, RESULTADO_DRAW],
        default=RESULTADO_AWAY,
    )
    # Alvo binário pro mercado Over/Under 2.5 gols -- mesmas features de
    # `resultado` (elo/forma/xG pré-jogo), só troca o alvo. RESULTADO_OVER25
    # = 1 quando total de gols > 2.5.
    dataset["resultado_over25"] = (dataset["home_goals"] + dataset["away_goals"] > 2.5).astype(int)
    dataset = dataset.merge(elo_home[["id", "home_team_id", "elo_home"]], on=["id", "home_team_id"], how="left")
    dataset = dataset.merge(elo_away[["id", "away_team_id", "elo_away"]], on=["id", "away_team_id"], how="left")
    dataset = dataset.join(forma_gols, on="id")
    dataset = dataset.join(forma_xg, on="id")

    squad_rating = _carregar_squad_rating_pre_jogo(supabase, partidas["id"].astype(int).tolist())
    if not squad_rating.empty:
        squad_home = squad_rating.rename(columns={"match_id": "id", "team_id": "home_team_id", "squad_rating_antes": "squad_rating_home"})
        squad_away = squad_rating.rename(columns={"match_id": "id", "team_id": "away_team_id", "squad_rating_antes": "squad_rating_away"})
    else:
        squad_home = pd.DataFrame(columns=["id", "home_team_id", "squad_rating_home"])
        squad_away = pd.DataFrame(columns=["id", "away_team_id", "squad_rating_away"])
    dataset = dataset.merge(squad_home[["id", "home_team_id", "squad_rating_home"]], on=["id", "home_team_id"], how="left")
    dataset = dataset.merge(squad_away[["id", "away_team_id", "squad_rating_away"]], on=["id", "away_team_id"], how="left")

    dataset = dataset.rename(columns={"id": "match_id"})

    dataset = dataset[["match_id", "match_date", "liga", *FEATURES_NUMERICAS_V2, "resultado", "resultado_over25"]]

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
