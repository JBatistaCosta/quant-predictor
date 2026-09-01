#!/usr/bin/env python3
"""Backfill histórico do modelo de odds "xG agregado por jogador".

Agrega `lambda_xg_jogo` (já persistido por jogador-partida em
`player_match_walkforward`, ver `backtest_jogador_mercados_walkforward.py`)
por `(match_id, team_id)` -- soma direta, sem filtro de titular (ver
`arquivos_do_claude/calibrar_kappa_xg_agregado.py` pro porquê: um reserva
com pouca chance de jogar já contribui pouco sozinho, porque
`minutos_esperados` já entra como feature do regressor que produz
`lambda_xg_jogo`). Aplica `kappa_liga` (fitado e persistido em
`league_model_params` por `calibrar_kappa_xg_agregado.py`), monta
`(lambda_home, lambda_away)` por partida, e reusa `distribuicoes.py`
(mesmo módulo do modelo misto -- `matriz_placares`/`mercados_de_gols`, com
o ρ global já calibrado do Dixon-Coles) pra derivar 1X2/over-under/BTTS/
handicap. Grava em `model_predictions`
(`model_name='xg_jogador_agregado_walkforward_v1'`) e em
`model_match_estimates` (`params.lambda_home/away/rho`, mesmo formato que
`treinar_modelo_hibrido.py` já grava -- deixa o modelo pronto pra
exposição futura na mesma UI que já lê esse formato, sem trabalho de
frontend extra, embora essa exposição não seja parte do v1).

É isso que habilita validação real via `api/backtest-betting.js` (já
existente, não precisa de mudança nenhuma nele) contra
`dixon_coles_walkforward_v1` -- não precisa esperar novas partidas
`scheduled` pra medir se o modelo agregado bottom-up bate, empata ou perde
pro Dixon-Coles top-down.

Só workflow_dispatch -- caro o bastante (paginação de ~473 mil linhas de
`player_match_walkforward`) pra não valer cron; rodar de novo depois de um
novo backtest de jogador-mercados, ou depois de recalibrar `kappa_liga`.

Uso:
    set SUPABASE_URL=...
    set SUPABASE_KEY=sua_service_role_key
    python backfill_xg_agregado_walkforward.py
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Callable

from supabase import create_client

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import distribuicoes as dist

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
logger = logging.getLogger("backfill_xg_agregado_walkforward")

MODEL_NAME = "xg_jogador_agregado_walkforward_v1"
KAPPA_MODEL_NAME = "xg_jogador_agregado_v1"  # mesmo model_name usado pra gravar/ler kappa_liga
TAMANHO_PAGINA = 1000
TAMANHO_LOTE_IDS = 500
LOTE_UPSERT = 500
TEMPORADAS = [str(ano) for ano in range(2018, 2028)]

# ρ global do Dixon-Coles, mesmo valor calibrado já usado em
# `src/utils/poisson.js` (DIXON_COLES_RHO) -- reusado aqui em vez de
# refitar um ρ específico pro modelo agregado (ver riscos no plano da
# sessão: ajuste fica pra v2 se o resultado do backtest justificar).
RHO_GLOBAL = -0.042


def obter_env(nome: str) -> str:
    valor = os.environ.get(nome)
    if not valor:
        sys.exit(f"Configure {nome} antes de rodar.")
    return valor


def _paginar(query_builder_factory: Callable[[int, int], object], tamanho_pagina: int = TAMANHO_PAGINA) -> list[dict]:
    todas: list[dict] = []
    pagina = 0
    while True:
        inicio, fim = pagina * tamanho_pagina, pagina * tamanho_pagina + tamanho_pagina - 1
        linhas = query_builder_factory(inicio, fim).execute().data or []
        todas.extend(linhas)
        if len(linhas) < tamanho_pagina:
            break
        pagina += 1
    return todas


def carregar_kappa_por_liga(supabase) -> dict[int, float]:
    linhas = supabase.table("league_model_params").select("league_id, param_value") \
        .eq("model_name", KAPPA_MODEL_NAME).eq("stat", "gols").eq("param_name", "kappa").execute().data or []
    kappa = {linha["league_id"]: float(linha["param_value"]) for linha in linhas}
    logger.info("kappa_liga carregado pra %d ligas (default 1.0 pras demais).", len(kappa))
    return kappa


def carregar_lambda_agregado(supabase) -> dict[tuple[int, int], dict]:
    """Soma `lambda_xg_jogo` por `(match_id, team_id)`, paginado por
    (league_id, temporada) -- mesmo motivo de
    `calibrar_kappa_xg_agregado.carregar_lambda_agregado` (OFFSET puro
    sobre as ~473 mil linhas da tabela arrisca o custo quadrático já
    documentado em `dados_historicos._paginar_por_lotes_de_id`).

    Filtra `fonte_titular='previsto'` explicitamente: hoje é o ÚNICO valor
    presente em `player_match_walkforward` (confirmado via SQL -- a
    passada "real" do walk-forward, mencionada no plano original de
    chutes/gols por jogador, nunca chegou a ser implementada nessa
    tabela), mas o filtro evita somar duas vezes o mesmo jogador se isso
    mudar no futuro.
    """
    ligas = supabase.table("leagues").select("id").execute().data or []
    league_ids = [linha["id"] for linha in ligas]

    agregados: dict[tuple[int, int], dict] = {}
    for league_id in league_ids:
        for temporada in TEMPORADAS:
            linhas = _paginar(lambda inicio, fim, lg=league_id, sz=temporada: (
                supabase.table("player_match_walkforward")
                .select("match_id, team_id, league_id, lambda_xg_jogo")
                .eq("league_id", lg).eq("season", sz).eq("fonte_titular", "previsto")
                .not_.is_("lambda_xg_jogo", "null")
                .order("id")
                .range(inicio, fim)
            ))
            for linha in linhas:
                chave = (linha["match_id"], linha["team_id"])
                bucket = agregados.setdefault(chave, {"league_id": linha["league_id"], "soma_lambda": 0.0})
                bucket["soma_lambda"] += float(linha["lambda_xg_jogo"])
    logger.info("%d combinações (match_id, team_id) com lambda_xg_jogo agregado", len(agregados))
    return agregados


def carregar_partidas(supabase, match_ids: list[int]) -> dict[int, dict]:
    partidas: dict[int, dict] = {}
    for inicio in range(0, len(match_ids), TAMANHO_LOTE_IDS):
        lote = match_ids[inicio: inicio + TAMANHO_LOTE_IDS]
        linhas = _paginar(lambda ini, fim, lt=lote: (
            supabase.table("matches")
            .select("id, home_team_id, away_team_id")
            .in_("id", lt)
            .order("id")
            .range(ini, fim)
        ))
        for linha in linhas:
            partidas[linha["id"]] = linha
    return partidas


def montar_lambdas_por_partida(
    agregados: dict[tuple[int, int], dict], partidas: dict[int, dict], kappa_por_liga: dict[int, float],
) -> dict[int, dict]:
    """Combina os dois lados (casa/fora) de cada partida num só registro
    `{match_id: {lambda_home, lambda_away, league_id}}`. Só entra partida
    com AMBOS os lados presentes -- os mercados de gols (1X2, over/under)
    não fazem sentido com só um lado."""
    por_partida: dict[int, dict] = {}
    for (match_id, team_id), bucket in agregados.items():
        partida = partidas.get(match_id)
        if not partida:
            continue
        kappa = kappa_por_liga.get(bucket["league_id"], 1.0)
        lam = bucket["soma_lambda"] * kappa
        registro = por_partida.setdefault(match_id, {"league_id": bucket["league_id"]})
        if team_id == partida["home_team_id"]:
            registro["lambda_home"] = lam
        elif team_id == partida["away_team_id"]:
            registro["lambda_away"] = lam

    completas = {mid: r for mid, r in por_partida.items() if "lambda_home" in r and "lambda_away" in r}
    logger.info("%d de %d partidas têm os dois lados (casa e fora) presentes.", len(completas), len(por_partida))
    return completas


def upsert_em_lotes(supabase, tabela: str, linhas: list[dict], on_conflict: str) -> int:
    """Upsert com dedup por chave de conflito -- mesmo tratamento de
    `treinar_modelo_hibrido.upsert_em_lotes`/`modelo_dixon_coles.py`."""
    if not linhas:
        return 0
    chaves = on_conflict.split(",")
    unicas = list({tuple(linha[k] for k in chaves): linha for linha in linhas}.values())
    for i in range(0, len(unicas), LOTE_UPSERT):
        supabase.table(tabela).upsert(unicas[i: i + LOTE_UPSERT], on_conflict=on_conflict).execute()
    return len(unicas)


def persistir(supabase, lambdas_por_partida: dict[int, dict]) -> None:
    estimativas, previsoes = [], []
    for match_id, registro in lambdas_por_partida.items():
        lam_home, lam_away = registro["lambda_home"], registro["lambda_away"]
        params_partida = {
            "lambda_home": round(lam_home, 4), "lambda_away": round(lam_away, 4), "rho": RHO_GLOBAL,
        }
        estimativas.append({
            "match_id": match_id, "model_name": MODEL_NAME,
            "xg_home_previsto": params_partida["lambda_home"],
            "xg_away_previsto": params_partida["lambda_away"],
            "params": params_partida,
        })

        matriz = dist.matriz_placares(lam_home, lam_away, RHO_GLOBAL)
        for (mercado, selecao), prob in dist.mercados_de_gols(matriz).items():
            previsoes.append({
                "match_id": match_id, "model_name": MODEL_NAME,
                "market": mercado, "selection": selecao,
                "probability": round(float(min(max(prob, 1e-9), 1.0)), 6),
            })

    n_est = upsert_em_lotes(supabase, "model_match_estimates", estimativas, "match_id,model_name")
    n_prev = upsert_em_lotes(supabase, "model_predictions", previsoes, "match_id,model_name,market,selection")
    logger.info("[%s] %d parâmetros e %d probabilidades gravados.", MODEL_NAME, n_est, n_prev)


def main() -> None:
    supabase = create_client(obter_env("SUPABASE_URL"), obter_env("SUPABASE_KEY"))

    kappa_por_liga = carregar_kappa_por_liga(supabase)
    agregados = carregar_lambda_agregado(supabase)
    if not agregados:
        sys.exit("Nenhuma linha com lambda_xg_jogo em player_match_walkforward -- rode o backtest de jogador primeiro.")

    match_ids = sorted({match_id for match_id, _ in agregados})
    logger.info("Carregando dados de %d partidas...", len(match_ids))
    partidas = carregar_partidas(supabase, match_ids)

    lambdas_por_partida = montar_lambdas_por_partida(agregados, partidas, kappa_por_liga)
    if not lambdas_por_partida:
        sys.exit("Nenhuma partida com os dois lados agregados -- nada a persistir.")

    persistir(supabase, lambdas_por_partida)


if __name__ == "__main__":
    main()
