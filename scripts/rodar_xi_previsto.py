"""Predição em lote do XI titular provável, para partidas `scheduled` ainda
sem escalação oficial confirmada -- grava em `xi_previsto`.

Reescrito a partir da versão original, que nunca funcionou de verdade:
carregava um arquivo (`modelos/xi_predictor.pkl`) que `treinar_modelo_xi.py`
nunca gera (salva por algoritmo), tinha um trecho de código morto
(`model.predict_proba(...) if False else ...`) que nunca chamava o modelo de
verdade, e só rodava se `match_lineup_fotmob` já tivesse linhas pra aquela
partida -- ou seja, só depois que a escalação oficial real já tivesse saído,
o que anula o propósito de prever com antecedência.

Elenco atual e engenharia de features seguem o MESMO padrão já usado por
`dados_historicos.obter_squad_rating_atual` (elenco = `players.last_team_id`,
exclui `player_availability_fotmob.injured=true` ANTES de pontuar -- os
modelos treinados nunca viram `is_lesionado=1` variar no treino, então
excluir na entrada é mais correto do que confiar na feature pra "aprender"
uma penalidade que ela nunca teve como aprender).

Uso:
    SUPABASE_URL=... SUPABASE_KEY=... python3 rodar_xi_previsto.py [--dias N]
"""

import argparse
import datetime as dt
import io
import logging
import os

import joblib
import numpy as np
import pandas as pd
from supabase import Client, create_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BUCKET_ARTEFATOS = "custom-model-artifacts"
ALGORITMOS = ["lightgbm", "xgboost", "catboost"]
FEATURES = ["dias_desde_ultimo_jogo", "hierarquia_elenco", "media_rating_5j", "posicao_num", "valor_mercado_eur", "is_lesionado"]
DIAS_JANELA_DEFAULT = 7
TOP_N_TITULARES = 11


def _dividir_em_lotes(itens: list, tamanho: int = 1000):
    for i in range(0, len(itens), tamanho):
        yield itens[i : i + tamanho]


def _buscar_paginado_por_lote(query_factory, lote: list, tamanho_pagina: int = 1000) -> list[dict]:
    """PostgREST corta em 1000 linhas sem `.range()` -- um jogador pode ter
    dezenas/centenas de linhas de histórico (market_value_history,
    match_player_stats_fotmob, match_lineup_fotmob), então um lote de até
    1000 player_id facilmente devolve mais de 1000 linhas.

    Achado rodando em produção: sem `.order()` explícito, OFFSET numa
    tabela grande (match_player_stats_fotmob, 250k+ linhas) deu `statement
    timeout` já na página 17 -- mesma classe de bug já corrigida em
    treinar_modelo_xi.py. Ordenar pela PRÓPRIA coluna filtrada (`player_id`,
    presente nas 3 tabelas que usam esta função) deixa o planner usar o
    índice certo, mas ainda não bastou sozinho: com lote de 1000 player_id
    o OFFSET voltou a dar timeout mais adiante (~14k linhas). Os 3 chamadores
    desta função em `montar_features_elenco_atual` reduziram o lote pra 100
    player_id -- resultado por lote fica bem menor, tira a necessidade de
    OFFSET profundo."""
    linhas: list[dict] = []
    pagina = 0
    while True:
        inicio = pagina * tamanho_pagina
        resp = query_factory(lote).order("player_id").range(inicio, inicio + tamanho_pagina - 1).execute()
        pagina_dados = resp.data or []
        linhas.extend(pagina_dados)
        if len(pagina_dados) < tamanho_pagina:
            break
        pagina += 1
    return linhas


def buscar_fixtures(supabase: Client, dias: int) -> pd.DataFrame:
    hoje = dt.datetime.utcnow()
    limite = hoje + dt.timedelta(days=dias)
    resp = (
        supabase.table("matches")
        .select("id, match_date, home_team_id, away_team_id")
        .eq("status", "scheduled")
        .gte("match_date", hoje.isoformat())
        .lte("match_date", limite.isoformat())
        .execute()
    )
    return pd.DataFrame(resp.data or [])


def carregar_modelos(supabase: Client) -> dict:
    modelos = {}
    for nome in ALGORITMOS:
        path = f"xi_titular/{nome}.joblib"
        try:
            conteudo = supabase.storage.from_(BUCKET_ARTEFATOS).download(path)
            modelos[nome] = joblib.load(io.BytesIO(conteudo))
        except Exception as e:
            logger.warning(f"Sem artefato para {nome} ({path}): {e}")
    return modelos


def montar_features_elenco_atual(supabase: Client, team_ids: list[int]) -> pd.DataFrame:
    """Elenco atual (players.last_team_id) de cada time em `team_ids`, com as
    mesmas 6 features de `treinar_modelo_xi.engenharia_features`, calculadas
    com dado de HOJE em vez de ponto-no-tempo histórico -- não existe
    'escalação futura confirmada', só o melhor proxy disponível agora."""
    if not team_ids:
        return pd.DataFrame()

    jogadores = supabase.table("players").select("id, last_team_id, usual_position_id, market_value").in_("last_team_id", team_ids).execute().data or []
    if not jogadores:
        return pd.DataFrame()
    df = pd.DataFrame(jogadores).rename(columns={"id": "player_id", "last_team_id": "team_id"})
    player_ids = df["player_id"].tolist()

    # Exclui lesionado ANTES de pontuar (mesmo padrão de obter_squad_rating_atual) --
    # o modelo nunca viu is_lesionado variar no treino, não dá pra confiar
    # nele pra aprender penalidade de lesão sozinho.
    lesionados: set = set()
    for lote in _dividir_em_lotes(player_ids):
        linhas = supabase.table("player_availability_fotmob").select("player_id").in_("player_id", lote).eq("injured", True).execute().data or []
        lesionados.update(l["player_id"] for l in linhas)
    df = df[~df["player_id"].isin(lesionados)].copy()
    if df.empty:
        return df

    player_ids = df["player_id"].tolist()

    ratings_rows = []
    for lote in _dividir_em_lotes(player_ids):
        ratings_rows.extend(supabase.table("player_ratings").select("player_id, rating").in_("player_id", lote).execute().data or [])
    df_ratings = pd.DataFrame(ratings_rows) if ratings_rows else pd.DataFrame(columns=["player_id", "rating"])

    valores_rows = []
    for lote in _dividir_em_lotes(player_ids, 100):
        valores_rows.extend(
            _buscar_paginado_por_lote(
                lambda l: supabase.table("player_market_value_history").select("player_id, value_date, value_eur").in_("player_id", l), lote
            )
        )
    valor_mais_recente: dict[int, float] = {}
    data_mais_recente: dict[int, str] = {}
    for v in valores_rows:
        if v.get("value_date") is None or v.get("value_eur") is None:
            continue
        atual = data_mais_recente.get(v["player_id"])
        if atual is None or v["value_date"] > atual:
            data_mais_recente[v["player_id"]] = v["value_date"]
            valor_mais_recente[v["player_id"]] = float(v["value_eur"])

    stats_rows = []
    for lote in _dividir_em_lotes(player_ids, 100):
        stats_rows.extend(
            _buscar_paginado_por_lote(
                lambda l: supabase.table("match_player_stats_fotmob").select("player_id, match_id, minutes_played").in_("player_id", l).gt("minutes_played", 0),
                lote,
            )
        )
    ultimo_jogo_data: dict[int, str] = {}
    if stats_rows:
        match_ids_jogados = list({s["match_id"] for s in stats_rows})
        datas_partidas: dict[int, str] = {}
        for lote in _dividir_em_lotes(match_ids_jogados):
            for m in supabase.table("matches").select("id, match_date").in_("id", lote).execute().data or []:
                datas_partidas[m["id"]] = m["match_date"]
        for s in stats_rows:
            data_jogo = datas_partidas.get(s["match_id"])
            if data_jogo is None:
                continue
            atual = ultimo_jogo_data.get(s["player_id"])
            if atual is None or data_jogo > atual:
                ultimo_jogo_data[s["player_id"]] = data_jogo

    lineup_rows = []
    for lote in _dividir_em_lotes(player_ids, 100):
        lineup_rows.extend(
            _buscar_paginado_por_lote(lambda l: supabase.table("match_lineup_fotmob").select("player_id, is_starter").in_("player_id", l), lote)
        )
    df_lineup = pd.DataFrame(lineup_rows) if lineup_rows else pd.DataFrame(columns=["player_id", "is_starter"])
    hierarquia_por_jogador = df_lineup.groupby("player_id")["is_starter"].mean().to_dict() if not df_lineup.empty else {}

    agora = dt.datetime.now(dt.timezone.utc)
    df = df.merge(df_ratings, on="player_id", how="left")
    df["media_rating_5j"] = df["rating"].fillna(6.0)
    df["valor_mercado_eur"] = df["player_id"].map(valor_mais_recente).fillna(df["market_value"]).fillna(100000)
    df["hierarquia_elenco"] = df["player_id"].map(hierarquia_por_jogador).fillna(0.5)
    df["posicao_num"] = df["usual_position_id"].fillna(0).astype(int)
    df["is_lesionado"] = 0

    def _dias_desde(player_id):
        data_str = ultimo_jogo_data.get(player_id)
        if data_str is None:
            return 14.0
        data_jogo = pd.to_datetime(data_str, utc=True)
        return min(float((agora - data_jogo).days), 30.0)

    df["dias_desde_ultimo_jogo"] = df["player_id"].apply(_dias_desde)
    return df[["player_id", "team_id", *FEATURES]]


def prever_probabilidades(modelos: dict, df_features: pd.DataFrame) -> np.ndarray:
    """Média das probabilidades dos algoritmos com artefato disponível
    (ensemble simples -- mais robusto que depender de um único algoritmo)."""
    probas = [modelo.predict_proba(df_features[FEATURES])[:, 1] for modelo in modelos.values()]
    return np.mean(probas, axis=0)


def rodar(supabase: Client, dias: int = DIAS_JANELA_DEFAULT) -> int:
    fixtures = buscar_fixtures(supabase, dias)
    if fixtures.empty:
        logger.info("Nenhuma partida 'scheduled' na janela -- nada a prever.")
        return 0

    modelos = carregar_modelos(supabase)
    if not modelos:
        logger.error("Nenhum artefato de modelo encontrado em xi_titular/*.joblib -- rode treinar_modelo_xi.py primeiro.")
        return 0

    team_ids = sorted(set(fixtures["home_team_id"]).union(fixtures["away_team_id"]))
    df_features = montar_features_elenco_atual(supabase, team_ids)
    if df_features.empty:
        logger.warning("Sem elenco atual (players.last_team_id) para os times das fixtures -- nada a prever.")
        return 0

    df_features = df_features.dropna(subset=FEATURES)
    df_features["prob_titular"] = prever_probabilidades(modelos, df_features)

    linhas = []
    for _, fixture in fixtures.iterrows():
        for team_id in (fixture["home_team_id"], fixture["away_team_id"]):
            elenco_time = df_features[df_features["team_id"] == team_id].sort_values("prob_titular", ascending=False)
            if elenco_time.empty:
                continue
            titulares_previstos = set(elenco_time.head(TOP_N_TITULARES)["player_id"])
            for _, jogador in elenco_time.iterrows():
                linhas.append(
                    {
                        "match_id": int(fixture["id"]),
                        "team_id": int(team_id),
                        "player_id": int(jogador["player_id"]),
                        "prob_titular": float(jogador["prob_titular"]),
                        "is_titular_previsto": jogador["player_id"] in titulares_previstos,
                        "model_version": "xi_titular_ensemble",
                    }
                )

    if not linhas:
        logger.info("Nenhuma linha gerada.")
        return 0

    for lote in _dividir_em_lotes(linhas, 500):
        supabase.table("xi_previsto").upsert(lote, on_conflict="match_id,team_id,player_id").execute()

    logger.info(f"{len(linhas)} linhas gravadas em xi_previsto ({len(fixtures)} partidas).")
    return len(linhas)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=DIAS_JANELA_DEFAULT, help="janela de dias à frente para prever (default 7)")
    args = ap.parse_args()

    url = os.environ["SUPABASE_URL"].strip()
    key = os.environ["SUPABASE_KEY"].strip()
    sb = create_client(url, key)
    rodar(sb, args.dias)
