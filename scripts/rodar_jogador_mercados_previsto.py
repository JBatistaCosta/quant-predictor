"""Predição em lote de chutes/gols por jogador, para partidas `scheduled`
dentro da janela de dias configurada -- grava em `player_match_estimates`.

Ao contrário de `rodar_xi_previsto.py` (que CONSTRÓI a previsão de
titularidade do zero, pontuando o elenco inteiro), este script CONSOME a
titularidade já resolvida em outro lugar -- não reimplementa filtro de
suspensão/transferência/formação (já aplicados por quem gerou
`xi_previsto`/`match_lineup_fotmob`). Sua única responsabilidade é: dado
"quem provavelmente/de fato joga", estimar quantos chutes/gols cada um deve
ter.

Duas passadas por partida, gravadas em `player_match_estimates` com
`fonte_titular` distinto (pedido explícito do usuário, ver plano da
sessão -- guardar as duas pra comparação, nunca uma sobrescrevendo a
outra):
  'previsto' -- quando a partida AINDA NÃO tem escalação oficial capturada
                (`match_lineup_fotmob` vazia pra esse match_id) -- usa
                `xi_previsto` (prob_titular contínuo, gerado por
                `rodar_xi_previsto.py`). Minutos esperados são uma MISTURA
                probabilística (prob_titular x minutos médios como titular
                + (1-prob_titular) x minutos médios como reserva).
  'real'     -- quando `scripts/ingerir_escalacao_pre_jogo.py` já capturou
                a escalação oficial (`match_lineup_fotmob` tem linhas pra
                esse match_id, ~60min antes do apito). Minutos esperados
                viram determinísticos por papel confirmado (titular ou
                reserva, sem mistura).
Mesma distinção de fonte que `dados_historicos.obter_titular_atual` já usa
pra força de XI agregada por time -- aqui replicada na granularidade de
JOGADOR (obter_titular_atual só devolve agregado por time, não serve pra
decidir quem é candidato individualmente).

`chutes_90_bayesiano`/`taxa_conversao_bayesiana` são a versão "hoje" (sem
corte de data) do shrinkage bayesiano de `treinar_modelo_jogador_mercados.
engenharia_features` -- mesma fórmula (`_shrinkage_bayesiano`, importada de
lá), prior por posição x liga calculado sobre TODO o histórico disponível
(não faz sentido restringir a "temporada anterior" aqui -- não há
vazamento a evitar, a predição é sempre pro futuro).

Uso:
    SUPABASE_URL=... SUPABASE_KEY=... python3 rodar_jogador_mercados_previsto.py [--dias N] [--match-ids ID,ID,...]
"""

from __future__ import annotations

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

import treinar_modelo_jogador_mercados as tmj

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BUCKET_ARTEFATOS = "custom-model-artifacts"
DIAS_JANELA_DEFAULT = 7
MODEL_VERSION = "jogador_chutes_catboost_poisson_v1"
MODEL_VERSION_GOLS_DIRETO = "jogador_gols_direto_catboost_poisson_v1"
MODEL_VERSION_XG = "jogador_xg_catboost_rmse_v1"


def _dividir_em_lotes(itens: list, tamanho: int = 500):
    for i in range(0, len(itens), tamanho):
        yield itens[i : i + tamanho]


def carregar_modelo_chutes(supabase: Client):
    path = "jogador_mercados/jogador_chutes_catboost_poisson_v1.joblib"
    try:
        conteudo = supabase.storage.from_(BUCKET_ARTEFATOS).download(path)
        return joblib.load(io.BytesIO(conteudo))
    except Exception as e:
        logger.error(f"Sem artefato de chutes ({path}): {e} -- rode treinar_modelo_jogador_mercados.py primeiro.")
        return None


def carregar_modelo_gols_direto(supabase: Client):
    """Opcional -- se ausente, o script segue só com o afinamento de Poisson
    (thinning), que não depende de artefato próprio."""
    path = "jogador_mercados/jogador_gols_direto_catboost_poisson_v1.joblib"
    try:
        conteudo = supabase.storage.from_(BUCKET_ARTEFATOS).download(path)
        return joblib.load(io.BytesIO(conteudo))
    except Exception as e:
        logger.warning(f"Sem artefato de gols direto ({path}): {e} -- seguindo só com thinning.")
        return None


def carregar_modelo_xg(supabase: Client):
    """Opcional -- se ausente, o script segue sem lambda_xg_jogo (não
    bloqueia chutes/gols, mesmo espírito de `carregar_modelo_gols_direto`)."""
    path = f"jogador_mercados/{MODEL_VERSION_XG}.joblib"
    try:
        conteudo = supabase.storage.from_(BUCKET_ARTEFATOS).download(path)
        return joblib.load(io.BytesIO(conteudo))
    except Exception as e:
        logger.warning(f"Sem artefato de xG ({path}): {e} -- seguindo sem lambda_xg_jogo.")
        return None


def buscar_fixtures(supabase: Client, dias: int, match_ids: list[int] | None) -> pd.DataFrame:
    if match_ids:
        resp = (
            supabase.table("matches")
            .select("id, match_date, home_team_id, away_team_id, league_id")
            .in_("id", match_ids)
            .eq("status", "scheduled")
            .execute()
        )
        return pd.DataFrame(resp.data or [])
    hoje = dt.datetime.now(dt.timezone.utc)
    limite = hoje + dt.timedelta(days=dias)
    # Piso de match_date usa uma janela de graça pro passado (não `hoje` em
    # ponto) -- achado real (2026-08-30): `sync-matches.js` pode demorar pra
    # virar o status de uma partida de 'scheduled' pra 'live'/'finished'
    # depois do apito, e enquanto isso não acontece um piso estrito em
    # `hoje` exclui a partida de QUALQUER rodada futura -- inclusive da
    # passada 'real', que é justamente a que mais precisa rodar perto/logo
    # depois do apito (é quando a escalação oficial acabou de sair). Sem
    # essa graça, uma partida que já começou mas ainda está 'scheduled' no
    # banco nunca mais recebe previsão nova (nem "real" nem "previsto"),
    # mesmo rodando o script de novo depois -- 4 partidas travadas assim
    # confirmadas via SQL direto antes deste fix.
    piso = hoje - dt.timedelta(hours=6)
    resp = (
        supabase.table("matches")
        .select("id, match_date, home_team_id, away_team_id, league_id")
        .eq("status", "scheduled")
        .gte("match_date", piso.isoformat())
        .lte("match_date", limite.isoformat())
        .execute()
    )
    return pd.DataFrame(resp.data or [])


def _buscar_candidatos_por_fonte(supabase: Client, match_ids: list[int]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Devolve (candidatos_real, candidatos_previsto) -- cada partida cai
    numa fonte só, nunca nas duas: escalação oficial (match_lineup_fotmob)
    quando existe pra aquele match_id, senão XI previsto (xi_previsto).
    Mesma lógica de cutover de `dados_historicos.obter_titular_atual`,
    replicada aqui na granularidade de jogador (aquela função só devolve
    agregado por time).

    Paginação de verdade (`dh._paginar_por_lotes_de_id`, `tamanho_lote`
    reduzido): `match_lineup_fotmob` tem ~42 linhas/partida e `xi_previsto`
    ~54 linhas/partida (medido via SQL) -- o lote default de 500 `match_id`
    somaria dezenas de milhares de linhas, muito acima do corte silencioso
    de 1000 do PostgREST (mesmo bug já confirmado em `_bayesiano_atual`
    nesta sessão, aqui na seleção de CANDIDATOS -- partidas no fim de um
    lote grande perderiam jogadores inteiros da escalação, não só a taxa
    bayesiana)."""
    TAMANHO_LOTE_JOGADORES_POR_PARTIDA = 100
    lineup_rows = dh._paginar_por_lotes_de_id(
        lambda lote, inicio, fim: (
            supabase.table("match_lineup_fotmob")
            .select("match_id, team_id, player_id, is_starter")
            .in_("match_id", lote)
            .order("match_id")
            .range(inicio, fim)
        ),
        match_ids,
        tamanho_lote=TAMANHO_LOTE_JOGADORES_POR_PARTIDA,
    )
    df_real = pd.DataFrame(lineup_rows)
    if not df_real.empty:
        df_real = df_real[df_real["player_id"].notna()].copy()

    match_ids_com_real = set(df_real["match_id"].unique().tolist()) if not df_real.empty else set()
    match_ids_pendentes = [m for m in match_ids if m not in match_ids_com_real]

    df_previsto = pd.DataFrame()
    if match_ids_pendentes:
        previsto_rows = dh._paginar_por_lotes_de_id(
            lambda lote, inicio, fim: (
                supabase.table("xi_previsto")
                .select("match_id, team_id, player_id, prob_titular, is_titular_previsto")
                .in_("match_id", lote)
                .order("match_id")
                .range(inicio, fim)
            ),
            match_ids_pendentes,
            tamanho_lote=TAMANHO_LOTE_JOGADORES_POR_PARTIDA,
        )
        df_previsto = pd.DataFrame(previsto_rows)
        if not df_previsto.empty:
            df_previsto = df_previsto[df_previsto["player_id"].notna()].copy()

    return df_real, df_previsto


def _posicao_detalhe_atual(supabase: Client, player_ids: list[int]) -> dict[int, str]:
    """Posição fina (código FotMob: GK/CB/RB/LB/RWB/LWB/CDM/CM/CAM/RM/LM/RW/
    LW/ST) por jogador -- mesma fonte e mesmo cuidado de dedup de
    `rodar_xi_previsto.py` (`player_availability_fotmob` pode ter mais de 1
    linha de snapshot pro mesmo jogador, achado real em produção lá).
    Ausente pra jogador ainda não capturado com essa granularidade (~15-20%
    do elenco, ver PR #395) -- fica None, frontend cai pro bucket grosso."""
    if not player_ids:
        return {}
    rows = []
    for lote in _dividir_em_lotes(player_ids, 200):
        rows.extend(
            supabase.table("player_availability_fotmob")
            .select("player_id, posicao_detalhe")
            .in_("player_id", lote)
            .execute()
            .data
            or []
        )
    if not rows:
        return {}
    df = pd.DataFrame(rows)
    df = df[df["player_id"].notna() & df["posicao_detalhe"].notna()].copy()
    if df.empty:
        return {}
    df["player_id"] = df["player_id"].astype(int)
    df = df.drop_duplicates(subset=["player_id"])
    return dict(zip(df["player_id"], df["posicao_detalhe"], strict=True))


def _minutos_medios_titular_reserva(supabase: Client, player_ids: list[int]) -> tuple[dict[int, float], dict[int, float]]:
    """Média histórica de minutos jogados, separada por papel (titular vs.
    reserva) -- fonte pra `minutos_esperados` das duas passadas (ver
    docstring do módulo). Cruza `match_player_stats_fotmob.minutes_played`
    com `match_lineup_fotmob.is_starter` da MESMA partida; jogador sem
    nenhuma aparição histórica de um dos dois papéis cai no fallback
    default (45min titular / 15min reserva -- mesmo espírito de "chute
    inicial documentado" já aceito em outras features do projeto)."""
    if not player_ids:
        return {}, {}

    # Paginação de verdade (`dh._paginar_por_lotes_de_id`) -- achado real
    # nesta sessão (ver plano): um lote de 100 `player_id` soma ~4.610
    # linhas em média (46,1 jogos/jogador, medido via SQL) contra jogadores
    # de amostra maior, muito acima do corte silencioso de 1000 linhas do
    # PostgREST. Sem isso, a maioria dos jogadores de cada lote perdia
    # silenciosamente o histórico e caía no fallback default, mesmo tendo
    # dado real na base.
    stats_rows = dh._paginar_por_lotes_de_id(
        lambda lote, inicio, fim: (
            supabase.table("match_player_stats_fotmob")
            .select("match_id, player_id, minutes_played")
            .in_("player_id", lote)
            .gt("minutes_played", 0)
            .order("player_id")
            .range(inicio, fim)
        ),
        player_ids,
        tamanho_lote=50,
    )
    lineup_rows = dh._paginar_por_lotes_de_id(
        lambda lote, inicio, fim: (
            supabase.table("match_lineup_fotmob")
            .select("match_id, player_id, is_starter")
            .in_("player_id", lote)
            .order("player_id")
            .range(inicio, fim)
        ),
        player_ids,
        tamanho_lote=50,
    )
    if not stats_rows:
        return {}, {}

    df_stats = pd.DataFrame(stats_rows)
    df_lineup = pd.DataFrame(lineup_rows) if lineup_rows else pd.DataFrame(columns=["match_id", "player_id", "is_starter"])
    df = df_stats.merge(df_lineup, on=["match_id", "player_id"], how="left")

    media_titular = df[df["is_starter"] == True].groupby("player_id")["minutes_played"].mean().to_dict()  # noqa: E712
    media_reserva = df[df["is_starter"] == False].groupby("player_id")["minutes_played"].mean().to_dict()  # noqa: E712
    return media_titular, media_reserva


def _bayesiano_atual(supabase: Client, candidatos: pd.DataFrame, nome_liga_por_team_id: dict[int, str]) -> pd.DataFrame:
    """chutes_90_bayesiano/gols_90_bayesiano/xg_90_bayesiano/chutes_no_alvo_
    90_bayesiano/taxa_conversao_bayesiana/taxa_no_alvo_bayesiana "hoje" (sem
    corte de data -- toda a história disponível) pros jogadores em
    `candidatos` (colunas match_id, team_id, player_id no mínimo). Mesma
    fórmula de `treinar_modelo_jogador_mercados.engenharia_features`
    (reaproveitada via `tmj._shrinkage_bayesiano`), mas sem o cuidado de
    "temporada anterior" no prior -- não há vazamento a evitar numa
    predição pro futuro, então o prior usa TODO o histórico disponível."""
    player_ids = candidatos["player_id"].astype(int).unique().tolist()
    # Paginação de verdade (`dh._paginar_por_lotes_de_id`) -- mesmo achado
    # real de `_minutos_medios_titular_reserva` acima: um lote de 200
    # `player_id` soma ~9.220 linhas em média (46,1 jogos/jogador), muito
    # acima do corte silencioso de 1000 linhas do PostgREST. Confirmado em
    # produção via SQL: Amine Gouiri (209 jogos reais na base) e outros
    # titulares consolidados vinham com `n_hist=0` aqui e colapsavam pro
    # prior de posição×liga, mesmo tendo histórico real extenso -- não era
    # falta de regularização bayesiana (que já existe e está correta), era
    # esse truncamento silencioso descartando o histórico deles antes de
    # chegar no shrinkage.
    shots_rows = dh._paginar_por_lotes_de_id(
        lambda lote, inicio, fim: (
            supabase.table("match_shots_fotmob")
            .select("player_id, event_type, is_own_goal, xg, is_on_target, is_blocked")
            .in_("player_id", lote)
            .order("player_id")
            .range(inicio, fim)
        ),
        player_ids,
        tamanho_lote=50,
    )
    # tamanho_lote reduzido de 200 pra 100 (mesmo valor já usado em
    # _minutos_medios_titular_reserva acima, pra match_player_stats_fotmob) --
    # achado real: a expansão de 6 pra 12 ligas do modelo de jogador (ver
    # dh.LIGAS_JOGADOR_MERCADOS) engordou o volume médio de linhas/jogador
    # o bastante pra um lote de 200 IDs estourar `statement_timeout` bem
    # dentro da paginação por OFFSET (run agendada de 2026-08-31 13:27,
    # timeout na página offset=20000 de um único lote) -- mesma classe de
    # custo quadrático já documentada em `_paginar_por_lotes_de_id`.
    # REDUZIDO DE NOVO 100->50 (04/09): recorreu -- run de 04/09 14:18
    # (id 33882916790) estourou `statement_timeout` de novo, dessa vez na
    # página offset=5000 de um lote de 100 (o lote anterior, também de
    # 100, tinha chegado até offset=9000 sem erro -- o volume por lote
    # varia bastante conforme a composição de jogadores, não é um número
    # fixo). Média global medida via SQL agora: 41,7 linhas/jogador em
    # match_player_stats_fotmob (777.631 linhas / 18.647 jogadores) --
    # segue subindo com o tempo (mais partidas ingeridas), então o mesmo
    # lote que era seguro em 30/08 deixou de ser. `tentado keyset antes,
    # revertido` (ver `_paginar_por_lotes_de_id`) -- reduzir o lote segue
    # sendo o remédio real disponível pra essa tabela especificamente.
    stats_rows = dh._paginar_por_lotes_de_id(
        lambda lote, inicio, fim: (
            supabase.table("match_player_stats_fotmob")
            .select("player_id, minutes_played")
            .in_("player_id", lote)
            .gt("minutes_played", 0)
            .order("player_id")
            .range(inicio, fim)
        ),
        player_ids,
        tamanho_lote=50,
    )
    df_stats = pd.DataFrame(stats_rows) if stats_rows else pd.DataFrame(columns=["player_id", "minutes_played"])
    minutos_totais = df_stats.groupby("player_id")["minutes_played"].sum().to_dict()
    # Jogos disputados (linhas de match_player_stats_fotmob já filtradas em
    # minutes_played > 0 na query acima) -- denominador da média "por jogo",
    # separada da média "por 90" (normalizada por minutos, não por partida:
    # um jogador que sempre entra aos 70' tem médias bem diferentes nas duas
    # visões, e as duas têm valor -- "por jogo" é mais intuitivo, "por 90"
    # controla por tempo em campo).
    n_jogos_totais = df_stats.groupby("player_id").size().to_dict()

    df_shots = pd.DataFrame(shots_rows) if shots_rows else pd.DataFrame(columns=["player_id", "event_type", "is_own_goal", "xg", "is_on_target", "is_blocked"])
    if not df_shots.empty:
        df_shots["_e_gol_proprio"] = (df_shots["event_type"] == "Goal") & (~df_shots["is_own_goal"].fillna(False))
        # Mesma definição de "chute ao gol" de treinar_modelo_jogador_
        # mercados.carregar_dados (ver comentário lá pro porquê: dado da
        # FotMob não distingue bloqueio na linha do gol de bloqueio em
        # qualquer outro lugar, então usa is_on_target sozinho).
        df_shots["_e_chute_no_alvo"] = df_shots["is_on_target"].fillna(False)
        chutes_totais = df_shots.groupby("player_id").size().to_dict()
        gols_totais = df_shots.groupby("player_id")["_e_gol_proprio"].sum().to_dict()
        chutes_no_alvo_totais = df_shots.groupby("player_id")["_e_chute_no_alvo"].sum().to_dict()
        # xg pode ser nulo por chute (~0,3% no escopo, ver docstring de
        # treinar_modelo_jogador_mercados) -- soma ignora NaN naturalmente
        # (pandas .sum() pula NaN por padrão).
        xg_totais = df_shots.groupby("player_id")["xg"].sum().to_dict()
    else:
        chutes_totais, gols_totais, xg_totais, chutes_no_alvo_totais = {}, {}, {}, {}

    players_rows = []
    for lote in _dividir_em_lotes(player_ids, 500):
        players_rows.extend(supabase.table("players").select("id, usual_position_id").in_("id", lote).execute().data or [])
    posicao_por_jogador = {p["id"]: (p.get("usual_position_id") or 0) for p in players_rows}

    linhas = []
    for player_id in player_ids:
        minutos = minutos_totais.get(player_id, 0)
        jogos = n_jogos_totais.get(player_id, 0)
        chutes = chutes_totais.get(player_id, 0)
        gols = gols_totais.get(player_id, 0)
        xg = xg_totais.get(player_id, 0.0) or 0.0
        chutes_no_alvo = chutes_no_alvo_totais.get(player_id, 0)
        chutes_90 = (chutes / (minutos / 90.0)) if minutos > 0 else 0.0
        gols_90 = (gols / (minutos / 90.0)) if minutos > 0 else 0.0
        xg_90 = (xg / (minutos / 90.0)) if minutos > 0 else 0.0
        chutes_no_alvo_90 = (chutes_no_alvo / (minutos / 90.0)) if minutos > 0 else 0.0
        n_hist = int(minutos / 90.0)
        linhas.append({
            "player_id": player_id, "_chutes_90_bruto": chutes_90, "_gols_90_bruto": gols_90, "_xg_90_bruto": xg_90,
            "_chutes_no_alvo_90_bruto": chutes_no_alvo_90,
            "n_hist": n_hist, "posicao_num": int(posicao_por_jogador.get(player_id, 0) or 0),
            # Média CRUA por jogo (sem shrinkage bayesiano, diferente das
            # colunas "_90_bruto" acima que ainda passam por prior/shrinkage
            # mais abaixo) -- é só o total histórico dividido por jogos
            # disputados, pensado pra leitura direta ("quantos chutes ele dá
            # por jogo"), não como feature de modelo.
            "chutes_por_jogo": (chutes / jogos) if jogos > 0 else 0.0,
            "gols_por_jogo": (gols / jogos) if jogos > 0 else 0.0,
            "xg_por_jogo": (xg / jogos) if jogos > 0 else 0.0,
            "chutes_no_alvo_por_jogo": (chutes_no_alvo / jogos) if jogos > 0 else 0.0,
        })
    df = pd.DataFrame(linhas)
    if df.empty:
        return df

    df = df.merge(candidatos[["player_id", "team_id"]].drop_duplicates("player_id"), on="player_id", how="left")
    df["liga"] = df["team_id"].map(nome_liga_por_team_id).fillna("desconhecida")

    prior_chutes = df.groupby(["posicao_num", "liga"])["_chutes_90_bruto"].mean()
    prior_gols = df.groupby(["posicao_num", "liga"])["_gols_90_bruto"].mean()
    prior_xg = df.groupby(["posicao_num", "liga"])["_xg_90_bruto"].mean()
    prior_no_alvo = df.groupby(["posicao_num", "liga"])["_chutes_no_alvo_90_bruto"].mean()
    df["_prior_chutes_90"] = df.apply(lambda r: prior_chutes.get((r["posicao_num"], r["liga"]), df["_chutes_90_bruto"].mean()), axis=1)
    df["_prior_gols_90"] = df.apply(lambda r: prior_gols.get((r["posicao_num"], r["liga"]), df["_gols_90_bruto"].mean()), axis=1)
    df["_prior_xg_90"] = df.apply(lambda r: prior_xg.get((r["posicao_num"], r["liga"]), df["_xg_90_bruto"].mean()), axis=1)
    df["_prior_chutes_no_alvo_90"] = df.apply(
        lambda r: prior_no_alvo.get((r["posicao_num"], r["liga"]), df["_chutes_no_alvo_90_bruto"].mean()), axis=1
    )

    df["chutes_90_bayesiano"] = tmj._shrinkage_bayesiano(df["n_hist"], df["_chutes_90_bruto"], df["_prior_chutes_90"], tmj.W_SHRINKAGE)
    df["gols_90_bayesiano"] = tmj._shrinkage_bayesiano(df["n_hist"], df["_gols_90_bruto"], df["_prior_gols_90"], tmj.W_SHRINKAGE)
    df["xg_90_bayesiano"] = tmj._shrinkage_bayesiano(df["n_hist"], df["_xg_90_bruto"], df["_prior_xg_90"], tmj.W_SHRINKAGE)
    df["chutes_no_alvo_90_bayesiano"] = tmj._shrinkage_bayesiano(
        df["n_hist"], df["_chutes_no_alvo_90_bruto"], df["_prior_chutes_no_alvo_90"], tmj.W_SHRINKAGE
    )
    df["taxa_conversao_bayesiana"] = np.where(
        df["chutes_90_bayesiano"] > 0.01, df["gols_90_bayesiano"] / df["chutes_90_bayesiano"], 0.0
    ).clip(0, 1)
    # Taxa de "chute ao gol" (por chute) -- mesmo afinamento de Poisson já
    # usado pra gols, aplicado um passo antes (chutes -> chutes_no_alvo, em
    # vez de chutes -> gols direto). Ver docstring do módulo.
    df["taxa_no_alvo_bayesiana"] = np.where(
        df["chutes_90_bayesiano"] > 0.01, df["chutes_no_alvo_90_bayesiano"] / df["chutes_90_bayesiano"], 0.0
    ).clip(0, 1)
    return df[[
        "player_id", "chutes_90_bayesiano", "gols_90_bayesiano", "xg_90_bayesiano", "chutes_no_alvo_90_bayesiano",
        "taxa_conversao_bayesiana", "taxa_no_alvo_bayesiana",
        "chutes_por_jogo", "gols_por_jogo", "xg_por_jogo", "chutes_no_alvo_por_jogo", "posicao_num",
    ]]


def rodar(supabase: Client, dias: int = DIAS_JANELA_DEFAULT, match_ids: list[int] | None = None) -> int:
    fixtures = buscar_fixtures(supabase, dias, match_ids)
    if fixtures.empty:
        logger.info("Nenhuma partida 'scheduled' na janela -- nada a prever.")
        return 0

    modelo_chutes = carregar_modelo_chutes(supabase)
    if modelo_chutes is None:
        return 0
    modelo_gols_direto = carregar_modelo_gols_direto(supabase)
    modelo_xg = carregar_modelo_xg(supabase)

    fixture_ids = [int(m) for m in fixtures["id"].tolist()]
    df_real, df_previsto = _buscar_candidatos_por_fonte(supabase, fixture_ids)
    if df_real.empty and df_previsto.empty:
        logger.warning("Nenhuma partida da janela tem escalação real nem XI previsto ainda -- nada a prever (rode rodar_xi_previsto.py primeiro).")
        return 0

    league_ids = fixtures["league_id"].dropna().astype(int).unique().tolist()
    ligas_rows = supabase.table("leagues").select("id, name").in_("id", league_ids).execute().data or []
    nome_por_league_id = {l["id"]: l["name"] for l in ligas_rows}
    team_to_league = {}
    for _, f in fixtures.iterrows():
        if pd.isna(f.get("league_id")):
            continue
        team_to_league[int(f["home_team_id"])] = int(f["league_id"])
        team_to_league[int(f["away_team_id"])] = int(f["league_id"])
    nome_liga_por_team_id = {tid: nome_por_league_id.get(lid, "desconhecida") for tid, lid in team_to_league.items()}

    todos_player_ids = list(set(
        (df_real["player_id"].astype(int).tolist() if not df_real.empty else [])
        + (df_previsto["player_id"].astype(int).tolist() if not df_previsto.empty else [])
    ))
    media_titular, media_reserva = _minutos_medios_titular_reserva(supabase, todos_player_ids)
    posicao_detalhe_por_jogador = _posicao_detalhe_atual(supabase, todos_player_ids)

    team_ids = sorted(set(fixtures["home_team_id"]).union(fixtures["away_team_id"]))
    elo_por_time = dh.obter_elo_atual(supabase, team_ids)
    squad_rating_por_time = dh.obter_squad_rating_atual(supabase, team_ids)

    linhas_saida = []
    agora = dt.datetime.now(dt.timezone.utc).isoformat()

    # TENTADO E REVERTIDO (04/09): limpeza de linha órfã aqui (delete por
    # match_id+team_id+fonte_titular, mesmo padrão de rodar_xi_previsto.py)
    # -- causa raiz real do jogador que sai do elenco (xi_previsto não o
    # lista mais) mas cuja linha antiga nunca é apagada de
    # player_match_estimates. Implementado, mas até ~180 deletes
    # sequenciais (1 por match/team do lote) numa run real derrubaram a
    # tolerância das queries paginadas seguintes: um `statement_timeout`
    # que não acontecia antes (offset=1000, bem mais raso que os timeouts
    # anteriores desta sessão) apareceu na primeira run com esse código.
    # Revertido -- o sintoma visível (>11 titulares) já está coberto
    # incondicionalmente pelo fix do frontend (`ehTitular()` exige
    # is_titular_previsto===true, nunca cai em fallback), então a limpeza
    # de linha órfã é só higiene de dado, não correção de bug visível --
    # não vale o risco de derrubar o cron diário inteiro. Se retomar,
    # trocar por 1-2 deletes em lote (todos os órfãos de uma vez, não 1
    # query por match/team) em vez do loop atual.
    for fonte, df_candidatos in (("real", df_real), ("previsto", df_previsto)):
        if df_candidatos.empty:
            continue
        bayesiano = _bayesiano_atual(supabase, df_candidatos, nome_liga_por_team_id)
        if bayesiano.empty:
            continue
        df = df_candidatos.merge(bayesiano, on="player_id", how="inner")

        if fonte == "real":
            df["minutos_esperados"] = df.apply(
                lambda r: media_titular.get(r["player_id"], 70.0) if r["is_starter"] else media_reserva.get(r["player_id"], 15.0),
                axis=1,
            )
            df["prob_titular_usada"] = df["is_starter"].astype(float)
            df["is_titular_previsto"] = df["is_starter"].astype(bool)
        else:
            df["minutos_esperados"] = df.apply(
                lambda r: r["prob_titular"] * media_titular.get(r["player_id"], 70.0)
                + (1 - r["prob_titular"]) * media_reserva.get(r["player_id"], 15.0),
                axis=1,
            )
            df["prob_titular_usada"] = df["prob_titular"]
            # BUG REAL corrigido (ver migration add_is_titular_previsto_
            # jogador_mercados): `prob_titular` é uma probabilidade
            # CONTÍNUA e INDEPENDENTE por jogador (cada um pontuado
            # isoladamente pelo modelo de XI, sem restrição de somar 11 por
            # time) -- um corte de 0.5 sobre ela, como o frontend fazia
            # antes, podia (e em produção chegava a) marcar até 24
            # jogadores "titular" no mesmo time. `xi_previsto.
            # is_titular_previsto` é a seleção final de verdade
            # (`selecionar_titulares_por_posicao`, restrita por posição/
            # formação, sempre exatamente 11) -- é essa que deve decidir o
            # bucket Titular/Banco, não a probabilidade bruta.
            df["is_titular_previsto"] = df["is_titular_previsto"].fillna(False).astype(bool)

        df = df.merge(fixtures.rename(columns={"id": "match_id"})[["match_id", "home_team_id", "away_team_id"]], on="match_id", how="left")
        df["mando"] = (df["team_id"] == df["home_team_id"]).astype(int)
        df["opponent_team_id"] = np.where(df["team_id"] == df["home_team_id"], df["away_team_id"], df["home_team_id"])
        df["elo_diff"] = df["team_id"].map(elo_por_time) - df["opponent_team_id"].map(elo_por_time)
        df["squad_rating_diff"] = df["team_id"].map(squad_rating_por_time) - df["opponent_team_id"].map(squad_rating_por_time)
        df["dias_desde_ultimo_jogo"] = 7.0  # sem histórico ponto-no-tempo aqui -- mesmo chute inicial documentado do resto do pipeline ao vivo
        df["liga"] = df["team_id"].map(nome_liga_por_team_id).fillna("desconhecida")

        df = df.dropna(subset=tmj.FEATURES_CHUTES)
        if df.empty:
            continue

        lambda_chutes = modelos_ml_predict(modelo_chutes, df)
        lambda_gols_thinning = lambda_chutes * df["taxa_conversao_bayesiana"].to_numpy()
        lambda_gols_direto = modelos_ml_predict(modelo_gols_direto, df) if modelo_gols_direto is not None else None
        lambda_xg = modelos_ml_predict_regressor(modelo_xg, df, tmj.FEATURES_XG) if modelo_xg is not None else None
        lambda_chutes_no_alvo = lambda_chutes * df["taxa_no_alvo_bayesiana"].to_numpy()

        for i, (_, row) in enumerate(df.iterrows()):
            linhas_saida.append({
                "match_id": int(row["match_id"]), "team_id": int(row["team_id"]), "player_id": int(row["player_id"]),
                "fonte_titular": fonte, "prob_titular_usada": float(row["prob_titular_usada"]),
                "is_titular_previsto": bool(row["is_titular_previsto"]),
                "minutos_esperados": float(row["minutos_esperados"]),
                "taxa_conversao_bayesiana": float(row["taxa_conversao_bayesiana"]),
                "taxa_no_alvo_bayesiana": float(row["taxa_no_alvo_bayesiana"]),
                "chutes_90_bayesiano": float(row["chutes_90_bayesiano"]),
                "gols_90_bayesiano": float(row["gols_90_bayesiano"]),
                "xg_90_bayesiano": float(row["xg_90_bayesiano"]),
                "chutes_no_alvo_90_bayesiano": float(row["chutes_no_alvo_90_bayesiano"]),
                "chutes_por_jogo": float(row["chutes_por_jogo"]),
                "gols_por_jogo": float(row["gols_por_jogo"]),
                "xg_por_jogo": float(row["xg_por_jogo"]),
                "chutes_no_alvo_por_jogo": float(row["chutes_no_alvo_por_jogo"]),
                "posicao_detalhe": posicao_detalhe_por_jogador.get(int(row["player_id"])),
                "lambda_chutes_jogo": float(lambda_chutes[i]),
                "lambda_gols_jogo_thinning": float(lambda_gols_thinning[i]),
                "lambda_gols_jogo_direto": float(lambda_gols_direto[i]) if lambda_gols_direto is not None else None,
                "lambda_xg_jogo": float(lambda_xg[i]) if lambda_xg is not None else None,
                "lambda_chutes_no_alvo_jogo": float(lambda_chutes_no_alvo[i]),
                "model_version": MODEL_VERSION, "gerado_em": agora,
            })

    if not linhas_saida:
        logger.info("Nenhuma linha gerada.")
        return 0

    for lote in _dividir_em_lotes(linhas_saida, 500):
        supabase.table("player_match_estimates").upsert(
            lote, on_conflict="match_id,team_id,player_id,model_version,fonte_titular"
        ).execute()

    logger.info(f"{len(linhas_saida)} linhas gravadas em player_match_estimates ({len(fixtures)} partidas na janela).")
    return len(linhas_saida)


def modelos_ml_predict(modelo, df: pd.DataFrame) -> np.ndarray:
    """CatBoost puro (não passou por `preparar_liga_para_catboost` -- faz
    isso aqui, mesma exigência de string sem NaN em `liga` que o treino já
    documenta)."""
    df_prep = df.copy()
    df_prep["liga"] = df_prep["liga"].fillna("desconhecida").astype(str)
    return np.maximum(modelo.predict(df_prep[tmj.FEATURES_CHUTES]), 0.01)


def modelos_ml_predict_regressor(modelo, df: pd.DataFrame, features: list[str]) -> np.ndarray:
    """Mesma preparação de `modelos_ml_predict`, mas pra regressor RMSE puro
    (xG) -- piso 0.0, não 0.01: xG=0.0 é um valor legítimo (jogador sem
    nenhum chute esperado), diferente do λ de Poisson (que nunca deveria
    ser exatamente 0 como "chute inicial"). Mesmo cuidado de
    `_prever_catboost_regressor_nao_negativo` em
    treinar_modelo_jogador_mercados.py -- RMSE não garante não-negatividade
    por construção como o Poisson garante."""
    df_prep = df.copy()
    df_prep["liga"] = df_prep["liga"].fillna("desconhecida").astype(str)
    return np.maximum(modelo.predict(df_prep[features]), 0.0)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=DIAS_JANELA_DEFAULT, help="janela de dias à frente para prever (default 7)")
    ap.add_argument("--match-ids", type=str, default=None, help="lista de match_id separados por vírgula (ex.: chamado logo após captura de escalação real) -- ignora --dias quando presente")
    args = ap.parse_args()

    url = os.environ["SUPABASE_URL"].strip()
    key = os.environ["SUPABASE_KEY"].strip()
    sb = create_client(url, key)

    ids = [int(x) for x in args.match_ids.split(",")] if args.match_ids else None
    rodar(sb, args.dias, ids)
