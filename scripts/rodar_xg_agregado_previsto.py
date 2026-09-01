"""Predição de odds (1X2/over-under) via xG agregado por jogador, para
partidas `scheduled` -- grava em `model_predictions`/`model_match_estimates`.

Camada 1 (aqui): soma `lambda_xg_jogo` (já persistido por jogador-partida
em `player_match_estimates`, ver `rodar_jogador_mercados_previsto.py`) por
`(match_id, team_id)`, sem filtro de titular -- um reserva com pouca
chance de jogar já contribui pouco sozinho, porque `minutos_esperados` já
entra como feature do regressor que produz `lambda_xg_jogo` (ver
`arquivos_do_claude/calibrar_kappa_xg_agregado.py` pra validação real
disso). Prioriza `fonte_titular='real'` quando a partida já tem escalação
oficial confirmada, senão usa `'previsto'` -- mesmo critério que
`AnaliseAvancadaEvento.jsx` já usa pra exibição, nunca soma as duas fontes
juntas (dobraria a contagem). Aplica `kappa_liga`, fitado e persistido em
`league_model_params` por `calibrar_kappa_xg_agregado.py` (default 1,0
pra liga sem calibração ainda).

Camada 2 (reuso): `distribuicoes.py` (mesmo módulo do modelo misto)
deriva 1X2/over-under/BTTS/handicap da matriz de placar bivariada com o ρ
global do Dixon-Coles já calibrado.

Mesmo padrão de encadeamento de `prever_jogador_mercados.yml`: roda como
step adicional logo depois de `rodar_jogador_mercados_previsto.py`, do
qual depende (precisa de `player_match_estimates` já populada pra essas
partidas).

Uso:
    SUPABASE_URL=... SUPABASE_KEY=... python3 rodar_xg_agregado_previsto.py [--dias N] [--match-ids ID,ID,...]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Callable

import dados_historicos as dh
import pandas as pd
from supabase import Client, create_client

import distribuicoes as dist
import rodar_jogador_mercados_previsto as rjm

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

MODEL_NAME = "xg_jogador_agregado_v1"
LOTE_UPSERT = 500
# Mesmo ρ global do Dixon-Coles usado em `backfill_xg_agregado_walkforward.py`
# (mesmo valor calibrado de `src/utils/poisson.js`, DIXON_COLES_RHO).
RHO_GLOBAL = -0.042


def obter_env(nome: str) -> str:
    valor = os.environ.get(nome)
    if not valor:
        sys.exit(f"Configure {nome} antes de rodar.")
    return valor


def carregar_kappa_por_liga(supabase: Client) -> dict[int, float]:
    linhas = (
        supabase.table("league_model_params")
        .select("league_id, param_value")
        .eq("model_name", MODEL_NAME).eq("stat", "gols").eq("param_name", "kappa")
        .execute().data or []
    )
    return {linha["league_id"]: float(linha["param_value"]) for linha in linhas}


def carregar_lambda_agregado_por_partida(supabase: Client, match_ids: list[int]) -> dict[tuple[int, int], float]:
    """Soma `lambda_xg_jogo` por `(match_id, team_id)`, priorizando
    `fonte_titular='real'` sobre `'previsto'` quando ambas existem pra
    aquele match_id -- mesmo critério de prioridade de
    `AnaliseAvancadaEvento.jsx`."""
    linhas = dh._paginar_por_lotes_de_id(
        lambda lote, inicio, fim: (
            supabase.table("player_match_estimates")
            .select("match_id, team_id, fonte_titular, lambda_xg_jogo")
            .in_("match_id", lote)
            .not_.is_("lambda_xg_jogo", "null")
            .order("match_id")
            .range(inicio, fim)
        ),
        match_ids,
    )
    if not linhas:
        return {}

    df = pd.DataFrame(linhas)
    # A preferência 'real' > 'previsto' é POR TIME, não por partida: um
    # time pode não ter escalação oficial confirmada ainda mesmo quando o
    # adversário já tem (times não recebem a escalação oficial no mesmo
    # instante). Agrupar por match_id sozinho faria o time sem 'real'
    # desaparecer inteiro sempre que o outro lado da mesma partida tivesse
    # 'real' -- bug real pego pelo smoke test antes de ir pra produção.
    fontes_disponiveis = df.groupby(["match_id", "team_id"])["fonte_titular"].apply(set)
    resultado: dict[tuple[int, int], float] = {}
    for (match_id, team_id), fontes in fontes_disponiveis.items():
        fonte_usada = "real" if "real" in fontes else "previsto"
        subset = df[
            (df["match_id"] == match_id) & (df["team_id"] == team_id) & (df["fonte_titular"] == fonte_usada)
        ]
        resultado[(int(match_id), int(team_id))] = float(subset["lambda_xg_jogo"].sum())
    return resultado


def upsert_em_lotes(supabase: Client, tabela: str, linhas: list[dict], on_conflict: str) -> int:
    if not linhas:
        return 0
    chaves = on_conflict.split(",")
    unicas = list({tuple(linha[k] for k in chaves): linha for linha in linhas}.values())
    for i in range(0, len(unicas), LOTE_UPSERT):
        supabase.table(tabela).upsert(unicas[i: i + LOTE_UPSERT], on_conflict=on_conflict).execute()
    return len(unicas)


def montar_e_persistir(supabase: Client, fixtures: pd.DataFrame, lambda_por_time: dict[tuple[int, int], float], kappa_por_liga: dict[int, float]) -> None:
    estimativas, previsoes = [], []
    n_sem_dados = 0
    for _, jogo in fixtures.iterrows():
        match_id = int(jogo["id"])
        chave_home, chave_away = (match_id, int(jogo["home_team_id"])), (match_id, int(jogo["away_team_id"]))
        if chave_home not in lambda_por_time or chave_away not in lambda_por_time:
            n_sem_dados += 1
            continue

        kappa = kappa_por_liga.get(int(jogo["league_id"]), 1.0)
        lam_home = lambda_por_time[chave_home] * kappa
        lam_away = lambda_por_time[chave_away] * kappa

        params_partida = {"lambda_home": round(lam_home, 4), "lambda_away": round(lam_away, 4), "rho": RHO_GLOBAL}
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
    logger.info(
        "[%s] %d parâmetros e %d probabilidades gravados (%d partidas sem player_match_estimates ainda, puladas).",
        MODEL_NAME, n_est, n_prev, n_sem_dados,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=rjm.DIAS_JANELA_DEFAULT, help="janela de dias à frente (default 7)")
    ap.add_argument("--match-ids", type=str, default=None, help="lista de match_id separados por vírgula")
    args = ap.parse_args()
    match_ids_filtro = [int(m) for m in args.match_ids.split(",")] if args.match_ids else None

    supabase = create_client(obter_env("SUPABASE_URL"), obter_env("SUPABASE_KEY"))

    fixtures = rjm.buscar_fixtures(supabase, args.dias, match_ids_filtro)
    if fixtures.empty:
        logger.info("Nenhuma partida 'scheduled' na janela -- nada a fazer.")
        return
    match_ids = fixtures["id"].astype(int).tolist()
    logger.info("%d partidas na janela.", len(match_ids))

    kappa_por_liga = carregar_kappa_por_liga(supabase)
    lambda_por_time = carregar_lambda_agregado_por_partida(supabase, match_ids)
    if not lambda_por_time:
        logger.info("Nenhuma player_match_estimates com lambda_xg_jogo pras partidas da janela ainda -- nada a fazer.")
        return

    montar_e_persistir(supabase, fixtures, lambda_por_time, kappa_por_liga)


if __name__ == "__main__":
    main()
