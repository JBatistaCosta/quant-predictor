"""Treino do modelo de chutes por jogador numa partida (regressor de
contagem, perda de Poisson) -- primeira peça da feature "predição de
chutes/gols por jogador" (pedido do usuário, ver CONTEXTO_PROJETO.md e o
plano da sessão que criou este arquivo).

Escopo confirmado com o usuário: só as 6 ligas já usadas em todo treino de
ML do projeto (`dados_historicos.LIGAS_MODEL_BENCHMARKING`), com corte
temporal por liga em `CORTE_TEMPORADA_MINIMA` -- 2020+ nas 5 europeias e
2023+ no Brasileirão, onde `match_shots_fotmob` (chute a chute, com xG/xGOT/
coordenadas) tem cobertura ~100% confirmada por query real contra produção
antes de escrever este script (não presumida).

Rótulo (`chutes_partida`): agregado de `match_shots_fotmob` por
`(match_id, team_id, player_id)`, NÃO de `match_player_stats_fotmob.
total_shots` -- confirmado por query real que essa coluna agregada só tem
~33-36% de preenchimento mesmo em temporada 100% coberta pela tabela de
chutes (ex.: Premier League 2023, Brasileirão 2023), enquanto
`match_shots_fotmob` (chute a chute) é a fonte mais completa. A ausência de
um jogador em `match_shots_fotmob` só vira "0 chutes" quando a PRÓPRIA
PARTIDA tem pelo menos 1 chute registrado (prova de que o FotMob capturou o
shotmap daquele jogo) -- sem essa guarda, partida sem shotmap nenhum
inflaria zeros artificiais.

`match_player_stats_fotmob` (minutes_played, rating) segue sendo a fonte do
"esqueleto" de quem jogou -- é o único lugar com minutos jogados, essencial
pra normalizar chutes/90 e pra saber que o jogador realmente entrou em
campo (~75% de preenchimento de minutes_played mesmo em temporada
plenamente coberta, limitação real da fonte, documentada, não escondida).

Gols por jogador NÃO tem modelo de ML próprio -- é derivado por afinamento
de Poisson (`lambda_gols = lambda_chutes * taxa_conversao_bayesiana`) em
`rodar_jogador_mercados_previsto.py`, não aqui.

Persistência: mesmo mecanismo de `treinar_modelo_xi.py` (bucket privado
`custom-model-artifacts`, prefixo `jogador_mercados/`, registro em
`models_registry` com `market='jogador_chutes'`) -- ao contrário de
`treinar_regressor_xgot.py` (só grava previsão do próprio holdout, nunca
reaproveitada), este modelo PRECISA ser recarregável depois por
`rodar_jogador_mercados_previsto.py` pra pontuar fixtures futuras que ainda
nem existiam no momento do treino.

Uso:
    SUPABASE_URL=... SUPABASE_KEY=... python3 treinar_modelo_jogador_mercados.py
"""

from __future__ import annotations

import io
import logging

import joblib
import numpy as np
import pandas as pd
from supabase import Client, create_client

import dados_historicos as dh
import modelos_ml
from treinar_modelo_xi import _buscar_por_lotes, _paginar_keyset, _split_cronologico

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BUCKET_ARTEFATOS = "custom-model-artifacts"

# Primeira temporada (inclusive) com cobertura confiável de match_shots_fotmob
# por liga -- validado por query real contra produção antes de fixar aqui
# (ver docstring do módulo). Não alargar sem revalidar cobertura de verdade.
CORTE_TEMPORADA_MINIMA = {
    "Premier League": 2020,
    "La Liga": 2020,
    "Bundesliga": 2020,
    "Serie A (Itália)": 2020,
    "Ligue 1": 2020,
    "Brasileirão Série A": 2023,
}

# Janela de shrinkage bayesiano (mesmo espírito de `w` em
# `dados_historicos._anexar_bayesiano_por_partida`): quantas "partidas de
# prior" pesam contra o histórico real do próprio jogador. Jogador com
# menos de ~W_SHRINKAGE partidas de histórico tem a taxa dominada pelo prior
# de posição x liga; mediana real de partidas por jogador na base é 16
# (medido por query antes de escrever este script), então W_SHRINKAGE=8
# deixa o prior pesar bastante pra metade dos jogadores sem zerar o sinal
# individual de quem já tem carreira mais longa.
W_SHRINKAGE = 8

FEATURES_CHUTES = [
    "chutes_90_bayesiano",
    "minutos_esperados",
    "posicao_num",
    "elo_diff",
    "squad_rating_diff",
    "mando",
    "dias_desde_ultimo_jogo",
    "liga",
]
TARGET_CHUTES = "chutes_partida"
TARGET_GOLS = "gols_partida"
FRACAO_TESTE = 0.2

MODEL_NAMES = {
    "catboost": "jogador_chutes_catboost_poisson_v1",
    "lightgbm": "jogador_chutes_lightgbm_poisson_v1",
}
# Candidato alternativo ao afinamento de Poisson (thinning) -- ver docstring
# do módulo e `_treinar_regressor_poisson`.
MODEL_NAMES_GOLS = {
    "catboost": "jogador_gols_direto_catboost_poisson_v1",
    "lightgbm": "jogador_gols_direto_lightgbm_poisson_v1",
}


def carregar_dados(supabase: Client) -> pd.DataFrame:
    logger.info("Resolvendo IDs das 6 ligas do escopo confirmado...")
    ids_por_liga = dh.obter_ids_ligas(supabase, dh.LIGAS_MODEL_BENCHMARKING)
    if not ids_por_liga:
        return pd.DataFrame()
    league_ids = list(ids_por_liga.values())
    nome_da_liga = {v: k for k, v in ids_por_liga.items()}

    logger.info("Carregando partidas finalizadas das 6 ligas...")
    matches = dh.carregar_partidas_finalizadas(supabase, league_ids)
    if matches.empty:
        return pd.DataFrame()
    matches["liga"] = matches["league_id"].map(nome_da_liga)
    matches["_season_year"] = matches["season"].astype(str).str[:4].astype(int)
    matches["corte_minimo"] = matches["liga"].map(CORTE_TEMPORADA_MINIMA)
    matches = matches[matches["_season_year"] >= matches["corte_minimo"]].copy()
    if matches.empty:
        logger.warning("Nenhuma partida dentro do corte temporal por liga -- nada pra fazer.")
        return pd.DataFrame()
    match_ids = matches["id"].astype(int).tolist()
    logger.info(f"{len(match_ids)} partidas dentro do escopo (liga + corte temporal).")

    # Só conta ausência-em-match_shots_fotmob como "0 chutes" pra partida que
    # o FotMob de fato capturou (>=1 linha de chute) -- senão partida sem
    # shotmap nenhum inflaria zero artificial pra todo mundo que jogou nela.
    logger.info("Verificando quais partidas têm shotmap capturado (match_shots_fotmob)...")
    shots_meta_rows = _paginar_keyset(
        lambda cursor: supabase.table("match_shots_fotmob").select(
            "id, match_id, team_id, player_id, event_type, is_own_goal"
        )
    )
    df_shots = pd.DataFrame(shots_meta_rows)
    if df_shots.empty:
        logger.warning("match_shots_fotmob vazia -- nada pra fazer.")
        return pd.DataFrame()
    df_shots = df_shots[df_shots["match_id"].isin(match_ids)].copy()
    matches_com_shotmap = set(df_shots["match_id"].unique().tolist())
    match_ids = [m for m in match_ids if m in matches_com_shotmap]
    matches = matches[matches["id"].isin(match_ids)].copy()
    logger.info(f"{len(match_ids)} partidas com shotmap confirmado (match_shots_fotmob).")

    # Esqueleto de quem jogou de verdade (minutos + rating só existem aqui).
    logger.info("Carregando esqueleto de aparições (match_player_stats_fotmob)...")
    stats_rows = _buscar_por_lotes(
        supabase, "match_player_stats_fotmob", "match_id", match_ids,
        "match_id, team_id, player_id, minutes_played, rating",
    )
    df_stats = pd.DataFrame(stats_rows)
    if df_stats.empty:
        logger.warning("Nenhuma linha de match_player_stats_fotmob no escopo -- nada pra fazer.")
        return pd.DataFrame()
    df_stats = df_stats[df_stats["player_id"].notna() & df_stats["minutes_played"].notna() & (df_stats["minutes_played"] > 0)].copy()
    # player_id vem de JSON com linhas nulas misturadas (~99,9% preenchido,
    # não 100%) -- pandas promove a coluna inteira pra float64 pra caber o
    # NaN, e o filtro .notna() acima remove as linhas mas NÃO desfaz a
    # promoção de tipo. Sem este cast, `player_ids.tolist()` mais abaixo
    # produz floats (1711.0) que o postgrest serializa como "1711.0" --
    # Postgres rejeita isso pra coluna bigint (`invalid input syntax for
    # type bigint`, achado real rodando o treino em produção pela 1a vez).
    df_stats["player_id"] = df_stats["player_id"].astype(int)

    # Rótulos: chutes/gols agregados por jogador-partida a partir do chute a
    # chute (não do agregado de match_player_stats_fotmob -- ver docstring).
    # Gol contra não conta como gol do PRÓPRIO atirador (é o time adversário
    # que se beneficia, não a produção ofensiva desse jogador).
    df_shots["_e_gol_proprio"] = (df_shots["event_type"] == "Goal") & (~df_shots["is_own_goal"].fillna(False))
    rotulos = (
        df_shots[df_shots["player_id"].notna()]
        .groupby(["match_id", "team_id", "player_id"])
        .agg(chutes_partida=("id", "count"), gols_partida=("_e_gol_proprio", "sum"))
        .reset_index()
    )

    df = df_stats.merge(rotulos, on=["match_id", "team_id", "player_id"], how="left")
    df["chutes_partida"] = df["chutes_partida"].fillna(0).astype(int)
    df["gols_partida"] = df["gols_partida"].fillna(0).astype(int)

    df = df.rename(columns={"match_id": "id_match"}).merge(
        matches[["id", "match_date", "home_team_id", "away_team_id", "league_id", "season", "liga"]].rename(columns={"id": "id_match"}),
        on="id_match", how="inner",
    )
    df["match_id"] = df["id_match"]
    df["opponent_team_id"] = np.where(df["team_id"] == df["home_team_id"], df["away_team_id"], df["home_team_id"])
    df["mando"] = (df["team_id"] == df["home_team_id"]).astype(int)

    # Defesa em profundidade: o merge com `rotulos` (linha acima) pode
    # repolular `player_id` pra float64 mesmo já tendo sido casteado em
    # df_stats -- `rotulos` vem de `df_shots.groupby("player_id")`, e
    # `match_shots_fotmob.player_id` também é nullable (mesma causa raiz do
    # cast em df_stats). Casteia de novo bem antes de usar em `.in_()`.
    df["player_id"] = df["player_id"].astype(int)
    player_ids = df["player_id"].unique().tolist()
    players_rows = _buscar_por_lotes(supabase, "players", "id", player_ids, "id, usual_position_id")
    df_players = pd.DataFrame(players_rows).rename(columns={"id": "player_id"})
    df = df.merge(df_players, on="player_id", how="left")

    # Força do próprio time e do adversário na data da partida -- mesmas
    # funções ponto-no-tempo já usadas por treinar_modelo_xi.py, reaproveitadas
    # aqui em vez de reimplementar Elo/rating de elenco do zero.
    elo = dh._carregar_elo_pre_jogo(supabase, league_ids)
    if not elo.empty:
        df = df.merge(elo.rename(columns={"rating_antes": "elo_proprio"}), on=["match_id", "team_id"], how="left")
        df = df.merge(
            elo.rename(columns={"team_id": "opponent_team_id", "rating_antes": "elo_oponente"}),
            on=["match_id", "opponent_team_id"], how="left",
        )
    else:
        df["elo_proprio"] = np.nan
        df["elo_oponente"] = np.nan

    squad_rating = dh._carregar_squad_rating_pre_jogo(supabase, match_ids)
    if not squad_rating.empty:
        df = df.merge(
            squad_rating.rename(columns={"squad_rating_antes": "squad_rating_proprio"}), on=["match_id", "team_id"], how="left"
        )
        df = df.merge(
            squad_rating.rename(columns={"team_id": "opponent_team_id", "squad_rating_antes": "squad_rating_oponente"}),
            on=["match_id", "opponent_team_id"], how="left",
        )
    else:
        df["squad_rating_proprio"] = np.nan
        df["squad_rating_oponente"] = np.nan

    return df


def _shrinkage_bayesiano(n: pd.Series, ewma: pd.Series, prior: pd.Series, w: int) -> pd.Series:
    """`stat_bayesiano = (n*ewma + w*prior) / (n+w)` -- mesma fórmula de
    `dados_historicos._anexar_bayesiano_por_partida`, extraída aqui como
    núcleo compartilhável (só ainda não movida pra dados_historicos.py
    porque o único outro uso segue no nível de TIME, com shape de dataframe
    diferente -- mover quando surgir um 3º consumidor)."""
    return ((n * ewma) + (w * prior)) / (n + w)


def engenharia_features(df: pd.DataFrame) -> pd.DataFrame:
    logger.info("Gerando features (shrinkage bayesiano de chutes/90 por jogador)...")
    df = df.copy()
    df["match_date"] = pd.to_datetime(df["match_date"], utc=True)
    df = df.sort_values(["player_id", "match_date"]).reset_index(drop=True)

    df["dias_desde_ultimo_jogo"] = df.groupby("player_id")["match_date"].diff().dt.days
    df["dias_desde_ultimo_jogo"] = df["dias_desde_ultimo_jogo"].fillna(14).clip(upper=30)

    # Minutos esperados (feature de contexto histórico pra TREINO -- ao vivo,
    # rodar_jogador_mercados_previsto.py usa `obter_titular_atual`/prob_titular
    # em vez disso, ver plano da sessão). Média móvel dos minutos jogados nas
    # aparições ANTERIORES do próprio jogador (shift(1), nunca a partida
    # atual -- vazaria o quanto ele de fato jogou hoje). Sem histórico
    # nenhum, chute inicial de meio jogo (mesmo espírito de "chute inicial
    # documentado" já aceito pro player_elo/XI no resto do projeto).
    df["minutos_esperados"] = df.groupby("player_id")["minutes_played"].transform(
        lambda s: s.shift(1).expanding().mean()
    )
    df["minutos_esperados"] = df["minutos_esperados"].fillna(45.0)

    # chutes/90 minutos por aparição, denominador com piso (evita explosão
    # pra cameo de 1-2 minutos com 1 chute = "180 chutes/90").
    minutos_piso = df["minutes_played"].clip(lower=10)
    df["_chutes_90_bruto"] = df["chutes_partida"] / (minutos_piso / 90.0)
    df["_gols_90_bruto"] = df["gols_partida"] / (minutos_piso / 90.0)

    # EWMA temporal por jogador, shift(1) -- nunca inclui a própria partida
    # sendo featurizada (mesmo cuidado de vazamento de `_anexar_bayesiano_
    # por_partida`, adaptado pra chave de jogador em vez de time). O
    # shift(1) acontece DENTRO do cálculo por grupo, de propósito -- shiftar
    # depois via um groupby separado arrisca (re)referenciar a coluna
    # bruta errada por engano.
    def _ewma_shift_grupo(g: pd.DataFrame, col_bruto: str) -> pd.Series:
        if g[col_bruto].isna().all():
            return pd.Series(np.nan, index=g.index)
        ewma = g[col_bruto].ewm(halflife="120 days", times=g["match_date"]).mean()
        return ewma.shift(1)

    df["n_hist"] = df.groupby("player_id").cumcount()
    for col_bruto, destino in (("_chutes_90_bruto", "ewma_chutes_90"), ("_gols_90_bruto", "ewma_gols_90")):
        resultado = df.groupby("player_id", group_keys=False).apply(lambda g, c=col_bruto: _ewma_shift_grupo(g, c))
        df[destino] = resultado.fillna(0)

    df["posicao_num"] = df["usual_position_id"].fillna(0).astype(int)

    # Prior de posição x liga -- SEMPRE a partir da TEMPORADA ANTERIOR (nunca
    # a própria temporada da partida sendo featurizada), mesmo cuidado
    # ponto-no-tempo de `_anexar_bayesiano_por_partida`: uma média "hoje"
    # incluiria jogos futuros do mesmo jogador/posição, vazando informação.
    df["_season_year"] = df["season"].astype(str).str[:4].astype(int)
    prior_temporada = df.groupby(["posicao_num", "liga", "_season_year"])["_chutes_90_bruto"].mean()
    prior_gols_temporada = df.groupby(["posicao_num", "liga", "_season_year"])["_gols_90_bruto"].mean()
    prior_liga_geral = df.groupby(["posicao_num", "liga"])["_chutes_90_bruto"].mean()
    prior_gols_liga_geral = df.groupby(["posicao_num", "liga"])["_gols_90_bruto"].mean()

    def _prior_chutes(row):
        chave_ano_anterior = (row["posicao_num"], row["liga"], row["_season_year"] - 1)
        if chave_ano_anterior in prior_temporada.index:
            return prior_temporada.loc[chave_ano_anterior]
        chave_liga = (row["posicao_num"], row["liga"])
        if chave_liga in prior_liga_geral.index:
            return prior_liga_geral.loc[chave_liga]
        return df["_chutes_90_bruto"].mean()

    def _prior_gols(row):
        chave_ano_anterior = (row["posicao_num"], row["liga"], row["_season_year"] - 1)
        if chave_ano_anterior in prior_gols_temporada.index:
            return prior_gols_temporada.loc[chave_ano_anterior]
        chave_liga = (row["posicao_num"], row["liga"])
        if chave_liga in prior_gols_liga_geral.index:
            return prior_gols_liga_geral.loc[chave_liga]
        return df["_gols_90_bruto"].mean()

    df["_prior_chutes_90"] = df.apply(_prior_chutes, axis=1)
    df["_prior_gols_90"] = df.apply(_prior_gols, axis=1)

    df["chutes_90_bayesiano"] = _shrinkage_bayesiano(df["n_hist"], df["ewma_chutes_90"], df["_prior_chutes_90"], W_SHRINKAGE)
    df["gols_90_bayesiano"] = _shrinkage_bayesiano(df["n_hist"], df["ewma_gols_90"], df["_prior_gols_90"], W_SHRINKAGE)

    # Taxa de conversão (gol por chute), como razão de somas estabilizadas
    # (gols_90/chutes_90), não média de razões por partida -- razão por
    # partida é indefinida/ruidosa quando o jogador teve 0 chutes naquele
    # jogo específico.
    df["taxa_conversao_bayesiana"] = np.where(
        df["chutes_90_bayesiano"] > 0.01, df["gols_90_bayesiano"] / df["chutes_90_bayesiano"], 0.0
    ).clip(0, 1)

    df["elo_diff"] = df["elo_proprio"] - df["elo_oponente"]
    df["squad_rating_diff"] = df["squad_rating_proprio"] - df["squad_rating_oponente"]

    return df.dropna(subset=["match_date", "player_id", "team_id", "chutes_partida"])


def _rmse(previsto: np.ndarray, real: np.ndarray) -> float:
    return float(np.sqrt(np.mean((previsto - real) ** 2)))


def _salvar_artefato(supabase: Client, nome_arquivo: str, modelo) -> str:
    path = f"jogador_mercados/{nome_arquivo}.joblib"
    buffer = io.BytesIO()
    joblib.dump(modelo, buffer)
    supabase.storage.from_(BUCKET_ARTEFATOS).upload(
        path, buffer.getvalue(), {"upsert": "true", "content-type": "application/octet-stream"}
    )
    return path


ALGORITMOS_POISSON = (
    ("catboost", modelos_ml.treinar_catboost_poisson, modelos_ml.prever_catboost_poisson, {"depth": 6, "learning_rate": 0.05}),
    ("lightgbm", modelos_ml.treinar_lightgbm_poisson, modelos_ml.prever_lightgbm_poisson, {"num_leaves": 31, "learning_rate": 0.05}),
)


def _treinar_regressor_poisson(
    treino: pd.DataFrame, teste: pd.DataFrame, supabase: Client, *, target: str, features: list[str],
    market: str, model_names: dict[str, str], baseline_previsto: np.ndarray,
) -> dict:
    """Núcleo de treino compartilhado entre o regressor de chutes e o
    regressor de gols DIRETO (candidato alternativo ao afinamento de
    Poisson -- ver `rodar_jogador_mercados_previsto.py` e a docstring do
    módulo) -- mesmos 2 algoritmos, mesmo baseline-vs-modelo, só muda o
    alvo/feature set/nome de mercado registrado."""
    real = teste[target].to_numpy()
    baseline_rmse = _rmse(baseline_previsto, real)
    logger.info(f"[{market}] Baseline: RMSE={baseline_rmse:.4f}")

    resultado: dict[str, dict] = {"baseline": {"rmse": baseline_rmse, "n_teste": len(teste)}}
    for algoritmo, treinar_fn, prever_fn, params in ALGORITMOS_POISSON:
        logger.info(f"[{market}] Treinando {algoritmo} (Poisson, alvo={target})...")
        extra_treino = treinar_fn(params, treino, target, features=features)
        modelo = extra_treino[0]
        extra = extra_treino[1] if len(extra_treino) > 1 else None
        previsto = prever_fn(modelo, extra, teste, features=features)
        modelo_rmse = _rmse(previsto, real)
        melhor = "MELHOR que baseline" if modelo_rmse < baseline_rmse else "pior que baseline"
        logger.info(f"  {algoritmo}: RMSE={modelo_rmse:.4f} vs. baseline={baseline_rmse:.4f} -> {melhor}")

        model_name = model_names[algoritmo]
        path = _salvar_artefato(supabase, model_name, modelo)
        metricas = {"rmse_modelo": modelo_rmse, "rmse_baseline": baseline_rmse, "n_treino": len(treino), "n_teste": len(teste)}
        resultado[algoritmo] = {"metricas": metricas, "artifact_path": path}

        supabase.table("models_registry").upsert(
            {
                "name": model_name, "market": market, "algorithm": algoritmo, "status": "testing",
                "features_used": features, "metrics_test": metricas, "artifact_url": path,
            },
            on_conflict="name,market",
        ).execute()
    return resultado


def treinar(df: pd.DataFrame, supabase: Client) -> dict:
    treino, teste = _split_cronologico(df, FRACAO_TESTE)
    logger.info(f"Treino: {len(treino)} | Teste: {len(teste)} linhas")

    # Baseline ingênuo obrigatório (ver plano): média histórica CRUA do
    # próprio jogador (chutes_90_bayesiano/gols_90_bayesiano JÁ são
    # shrinkados -- o baseline usa a EWMA sem shrinkage nenhum, senão
    # competiria contra uma versão de si mesmo).
    baseline_chutes = (teste["ewma_chutes_90"] * teste["minutos_esperados"] / 90.0).clip(lower=0.01).to_numpy()
    resultado_chutes = _treinar_regressor_poisson(
        treino, teste, supabase, target=TARGET_CHUTES, features=FEATURES_CHUTES,
        market="jogador_chutes", model_names=MODEL_NAMES, baseline_previsto=baseline_chutes,
    )

    # Regressor de gols DIRETO -- candidato alternativo ao afinamento de
    # Poisson (lambda_gols = lambda_chutes x taxa_conversao_bayesiana, ver
    # rodar_jogador_mercados_previsto.py). Mesmas features (inclui
    # chutes_90_bayesiano como sinal de volume) -- o walk-forward decide
    # empiricamente qual dos dois bate o baseline com mais folga (ver plano:
    # "não declarar vencedor sem medir").
    baseline_gols = (teste["ewma_gols_90"] * teste["minutos_esperados"] / 90.0).clip(lower=0.001).to_numpy()
    resultado_gols = _treinar_regressor_poisson(
        treino, teste, supabase, target=TARGET_GOLS, features=FEATURES_CHUTES,
        market="jogador_gols_direto", model_names=MODEL_NAMES_GOLS, baseline_previsto=baseline_gols,
    )

    return {"chutes": resultado_chutes, "gols_direto": resultado_gols}


if __name__ == "__main__":
    import os

    url = os.environ["SUPABASE_URL"].strip()
    key = os.environ["SUPABASE_KEY"].strip()
    sb = create_client(url, key)

    df_bruto = carregar_dados(sb)
    if df_bruto.empty:
        logger.warning("Sem dados no escopo (6 ligas, corte temporal, shotmap confirmado) -- nada para treinar.")
    else:
        treinar(engenharia_features(df_bruto), sb)
