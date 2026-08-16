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
uma penalidade que ela nunca teve como aprender). Suspensos por acúmulo de
cartão amarelo (`jogadores_suspensos`, reaproveitando `dados_historicos.
CARTAO_LIMIAR_POR_LIGA`) são excluídos pelo mesmo motivo.

A seleção do XI (`selecionar_titulares_por_posicao`) respeita uma formação
tática plausível (1 GK, 3-5 DEF, 3-5 MEI, 1-3 ATA) em vez de só pegar os 11
de maior probabilidade sem noção de posição -- o modelo prevê cada jogador
isoladamente, então nada impediria escalar 6 zagueiros e 1 meio-campista se
esses fossem os de maior probabilidade individual.

Uso:
    SUPABASE_URL=... SUPABASE_KEY=... python3 rodar_xi_previsto.py [--dias N]
"""

import argparse
import datetime as dt
import io
import logging
import os

import dados_historicos as dh
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

# Decodificação de players.usual_position_id validada nesta sessão (o repo
# documentava só "enum interno do FotMob, sem legenda confiável" -- validado
# aqui por correlação com match_player_stats_fotmob.is_goalkeeper (98,7% de
# confirmação pro valor 0) e por gradiente monotônico de gols/assistências/
# interceptações/toques na área rival entre 1/2/3, batendo com o padrão
# esperado de DEF<MEI<ATA em ataque e o inverso em interceptação):
# 0=goleiro, 1=defensor, 2=meio-campo, 3=atacante.
FORMACAO_MIN_MAX = {0: (1, 1), 1: (3, 5), 2: (3, 5), 3: (1, 3)}


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
    OFFSET profundo.

    `.order("player_id")` sozinho NÃO é determinístico (várias linhas
    empatam no mesmo player_id) -- OFFSET com ORDER BY empatado pode
    devolver a MESMA linha em páginas diferentes (achado no mesmo bug que
    quebrou a query de elenco em `montar_features_elenco_atual`, "ON
    CONFLICT DO UPDATE command cannot affect row a second time" no upsert
    de xi_previsto). Desempate por `id` (chave primária, única nas 3
    tabelas)."""
    linhas: list[dict] = []
    pagina = 0
    while True:
        inicio = pagina * tamanho_pagina
        resp = query_factory(lote).order("player_id").order("id").range(inicio, inicio + tamanho_pagina - 1).execute()
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
        .select("id, match_date, home_team_id, away_team_id, league_id")
        .eq("status", "scheduled")
        .gte("match_date", hoje.isoformat())
        .lte("match_date", limite.isoformat())
        .execute()
    )
    return pd.DataFrame(resp.data or [])


def mapear_ligas(supabase: Client, fixtures: pd.DataFrame) -> tuple[dict[int, int], dict[int, str]]:
    """`league_id` de cada time e nome da liga (`leagues.name`, mesma
    string usada como chave em `dados_historicos.CARTAO_LIMIAR_POR_LIGA`)
    -- mesmo padrão de `rodar_predicoes.montar_features_fixtures`."""
    league_id_por_time: dict[int, int] = {}
    for _, fixture in fixtures.iterrows():
        if pd.isna(fixture.get("league_id")):
            continue
        league_id = int(fixture["league_id"])
        league_id_por_time[int(fixture["home_team_id"])] = league_id
        league_id_por_time[int(fixture["away_team_id"])] = league_id

    league_ids = sorted(set(league_id_por_time.values()))
    if not league_ids:
        return league_id_por_time, {}
    ligas = supabase.table("leagues").select("id, name").in_("id", league_ids).execute().data or []
    nome_liga_por_league_id = {liga["id"]: liga["name"] for liga in ligas}
    return league_id_por_time, nome_liga_por_league_id


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


def jogadores_suspensos(
    supabase: Client,
    player_ids: list[int],
    team_por_jogador: dict[int, int],
    league_id_por_time: dict[int, int],
    nome_liga_por_league_id: dict[int, str],
    ultimo_jogo_data: dict[int, str],
) -> set[int]:
    """Jogadores cumprindo suspensão por acúmulo de cartão amarelo pra
    fixture futura -- reaproveita a regra por liga já pesquisada em
    `dados_historicos.CARTAO_LIMIAR_POR_LIGA` (mesma família de
    `obter_cartoes_atuais`, mas aqui precisa da IDENTIDADE de cada jogador
    suspenso, não só a contagem agregada por time).

    Acúmulo de cartão sozinho não diz se a suspensão JÁ FOI cumprida --
    `dados_historicos._estado_apos_cada_cartao` reseta o ciclo no MESMO
    evento que cruza o limiar (não existe dado de "partida realmente
    perdida por suspensão", só o histórico de cartões). Pra saber se ainda
    está suspenso HOJE, cruza com `ultimo_jogo_data` (já calculado a partir
    de `match_player_stats_fotmob` pelo chamador): se o jogador já jogou
    uma partida DEPOIS do cartão que cruzou o limiar, a suspensão de 1
    partida já foi cumprida."""
    if not player_ids or not league_id_por_time:
        return set()

    temporada_atual_por_liga: dict[int, str] = {}
    for league_id in set(league_id_por_time.values()):
        resp = (
            supabase.table("matches").select("season").eq("league_id", league_id).order("season", desc=True).limit(1).execute().data
        )
        if resp:
            temporada_atual_por_liga[league_id] = resp[0]["season"]

    eventos: list[dict] = []
    for lote in _dividir_em_lotes(player_ids, 100):
        eventos.extend(
            _buscar_paginado_por_lote(
                lambda l: supabase.table("match_events")
                .select("match_id, player_id, event_type")
                .in_("player_id", l)
                .in_("event_type", list(dh.TIPOS_EVENTO_CARTAO_AMARELO))
                .eq("source", "fotmob"),
                lote,
            )
        )
    if not eventos:
        return set()
    df_eventos = pd.DataFrame(eventos)

    match_ids = df_eventos["match_id"].unique().tolist()
    partidas_meta: dict[int, dict] = {}
    for lote in _dividir_em_lotes(match_ids):
        for m in supabase.table("matches").select("id, league_id, season, match_date").in_("id", lote).execute().data or []:
            partidas_meta[m["id"]] = m

    df_eventos["league_id"] = df_eventos["match_id"].map(lambda mid: partidas_meta.get(mid, {}).get("league_id"))
    df_eventos["season"] = df_eventos["match_id"].map(lambda mid: partidas_meta.get(mid, {}).get("season"))
    df_eventos["match_date"] = df_eventos["match_id"].map(lambda mid: partidas_meta.get(mid, {}).get("match_date"))

    suspensos: set[int] = set()
    for player_id, eventos_jogador in df_eventos.groupby("player_id"):
        team_id = team_por_jogador.get(player_id)
        league_id = league_id_por_time.get(team_id)
        if league_id is None:
            continue
        temporada = temporada_atual_por_liga.get(league_id)
        if temporada is None:
            continue
        eventos_liga = eventos_jogador[(eventos_jogador["league_id"] == league_id) & (eventos_jogador["season"] == temporada)]
        eventos_liga = eventos_liga.dropna(subset=["match_date"]).sort_values("match_date")
        if eventos_liga.empty:
            continue
        regra = dh.CARTAO_LIMIAR_POR_LIGA.get(nome_liga_por_league_id.get(league_id), dh.LIMIAR_CARTAO_PADRAO)
        datas = eventos_liga["match_date"].tolist()
        estados_antes = dh._estado_apos_cada_cartao(datas[:-1], regra)
        cartoes_antes, n_susp_antes = estados_antes[-1] if estados_antes else (0, 0)
        limiar_atual = dh._limiar_do_ciclo(regra, n_susp_antes)
        if (cartoes_antes + 1) < limiar_atual:
            continue  # último cartão não cruzou o limiar -- não suspenso
        data_cartao_suspensivo = datas[-1]
        ultimo_jogo = ultimo_jogo_data.get(player_id)
        if ultimo_jogo is not None and ultimo_jogo > data_cartao_suspensivo:
            continue  # já jogou depois do cartão que suspendeu -- cumprida
        suspensos.add(int(player_id))
    return suspensos


def selecionar_titulares_por_posicao(elenco_time: pd.DataFrame) -> set:
    """Seleciona os `TOP_N_TITULARES` respeitando uma formação plausível
    (`FORMACAO_MIN_MAX`) em vez de só top-11 por probabilidade -- sem isso,
    nada impede o modelo (prevê cada jogador isoladamente, sem noção de "1
    time = 1 formação") de escalar, por exemplo, 6 zagueiros e 1 meio-
    campista. Usa `usual_position_id` CRU (não a feature `posicao_num`, que
    tem fillna(0) e colidiria posição desconhecida com goleiro)."""
    elenco_time = elenco_time.drop_duplicates(subset="player_id").sort_values("prob_titular", ascending=False)
    prob_por_jogador = dict(zip(elenco_time["player_id"], elenco_time["prob_titular"]))

    def _extend_sem_duplicar(destino: list, novos) -> None:
        vistos = set(destino)
        for pid in novos:
            if pid not in vistos:
                destino.append(pid)
                vistos.add(pid)

    selecionados: list = []
    sobras: list = []
    for posicao, (minimo, maximo) in FORMACAO_MIN_MAX.items():
        candidatos = elenco_time[elenco_time["usual_position_id"] == posicao]["player_id"].tolist()
        _extend_sem_duplicar(selecionados, candidatos[:minimo])
        sobras.extend(pid for pid in candidatos[minimo:maximo] if pid not in selecionados)

    faltam = TOP_N_TITULARES - len(selecionados)
    if faltam > 0 and sobras:
        sobras_ordenadas = sorted(dict.fromkeys(sobras), key=lambda pid: prob_por_jogador.get(pid, 0), reverse=True)
        _extend_sem_duplicar(selecionados, sobras_ordenadas[:faltam])
        faltam = TOP_N_TITULARES - len(selecionados)

    if faltam > 0:
        # Elenco elegível (posição conhecida, dentro dos baldes) tem menos
        # de 11 -- só nesse caso de dado incompleto, preenche com quem
        # sobrou (posição desconhecida ou além do máximo do balde), por
        # probabilidade -- mesmo espírito de fallback best-effort já usado
        # em `rodar()` pra elenco pequeno demais.
        restantes = elenco_time[~elenco_time["player_id"].isin(selecionados)]
        _extend_sem_duplicar(selecionados, restantes.head(faltam)["player_id"].tolist())

    # Rede de segurança: independente da causa, nunca devolver mais que
    # TOP_N_TITULARES (achado em produção -- 2 de 228 grupos saíram com 12
    # titulares previstos; a causa exata não foi isolada com certeza, mas
    # truncar pelos de maior probabilidade garante a saída correta de
    # qualquer forma).
    if len(selecionados) > TOP_N_TITULARES:
        logger.warning(f"selecionar_titulares_por_posicao devolveu {len(selecionados)} > {TOP_N_TITULARES}, truncando por probabilidade.")
        selecionados = sorted(selecionados, key=lambda pid: prob_por_jogador.get(pid, 0), reverse=True)[:TOP_N_TITULARES]

    return set(selecionados)


def montar_features_elenco_atual(
    supabase: Client,
    team_ids: list[int],
    league_id_por_time: dict[int, int] | None = None,
    nome_liga_por_league_id: dict[int, str] | None = None,
) -> pd.DataFrame:
    """Elenco atual (players.last_team_id) de cada time em `team_ids`, com as
    mesmas 6 features de `treinar_modelo_xi.engenharia_features`, calculadas
    com dado de HOJE em vez de ponto-no-tempo histórico -- não existe
    'escalação futura confirmada', só o melhor proxy disponível agora."""
    if not team_ids:
        return pd.DataFrame()

    # Achado em produção: sem paginação, essa consulta cortava em 1000
    # linhas silenciosamente (PostgREST) -- com ~228 times numa janela de 7
    # dias x ~40 jogadores/elenco, o total passa de 9000 linhas fácil, e a
    # maioria dos times sobrava com só 0-1 jogador no resultado truncado
    # (ex.: time_id=220 tem 43 jogadores reais, só 1 aparecia). Pagina por
    # OFFSET ordenado por `last_team_id` -- mas SÓ por `last_team_id` não é
    # determinístico (dezenas de jogadores empatam no mesmo time), o que
    # fez OFFSET devolver o MESMO jogador em páginas diferentes (achado no
    # próximo bug: upsert em xi_previsto quebrou com "ON CONFLICT DO UPDATE
    # command cannot affect row a second time", chave duplicada dentro do
    # mesmo lote). Desempate por `id` (chave primária, única) resolve.
    jogadores = []
    pagina = 0
    while True:
        inicio = pagina * 1000
        lote = (
            supabase.table("players")
            .select("id, last_team_id, usual_position_id, market_value")
            .in_("last_team_id", team_ids)
            .order("last_team_id")
            .order("id")
            .range(inicio, inicio + 999)
            .execute()
            .data
            or []
        )
        jogadores.extend(lote)
        if len(lote) < 1000:
            break
        pagina += 1
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

    if league_id_por_time and nome_liga_por_league_id:
        team_por_jogador = dict(zip(df["player_id"], df["team_id"]))
        suspensos = jogadores_suspensos(
            supabase, player_ids, team_por_jogador, league_id_por_time, nome_liga_por_league_id, ultimo_jogo_data
        )
        if suspensos:
            df = df[~df["player_id"].isin(suspensos)].copy()
            player_ids = df["player_id"].tolist()
            if df.empty:
                return df

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
    # usual_position_id (cru, não a feature posicao_num) segue no retorno
    # pra selecionar_titulares_por_posicao bucketar sem ambiguidade com
    # posição desconhecida (fillna(0) colidiria com goleiro real).
    return df[["player_id", "team_id", "usual_position_id", *FEATURES]]


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
    league_id_por_time, nome_liga_por_league_id = mapear_ligas(supabase, fixtures)
    df_features = montar_features_elenco_atual(supabase, team_ids, league_id_por_time, nome_liga_por_league_id)
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
            titulares_previstos = selecionar_titulares_por_posicao(elenco_time)
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
