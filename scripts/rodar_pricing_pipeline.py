"""Runner fino do pricing pipeline de 4 camadas (`pricing_pipeline.py`) para
partidas `scheduled` dentro da janela de dias configurada -- grava os
mercados de gols da Camada 3 (Dixon-Coles reconciliado) em
`model_predictions` com `model_name='pricing_pipeline_v1'`, pra exibição em
`src/pages/AnaliseAvancadaEvento.jsx` (seção "Pricing Pipeline").

Escopo deliberadamente restrito às Camadas 1-3 (agregação bottom-up +
reconciliação + matriz conjunta) -- NÃO roda a Camada 4 (`EnsembleVetoManager`,
que decide ordem de aposta contra odds reais). O que este runner persiste é
"que probabilidade o modelo atribui a cada mercado", não "que aposta fazer" --
a segunda pergunta depende de odds reais capturadas por partida, que nem toda
partida tem, e além disso mistura decisão de risco (Kelly/vetos) com a
previsão em si. Rodar `process_match()` completo fica pra quando essa
integração de odds + decisão de aposta for pedida explicitamente (ver plano
da sessão).

Fontes de dado (todas já existentes, nenhuma tabela nova):
  - `player_match_estimates` -- estimativas por jogador (Camada 1), mesma
    tabela/mesmo `select` que `AnaliseAvancadaEvento.jsx` já usa. Prioriza
    `fonte_titular='real'` sobre `'previsto'` quando as duas existem (mesma
    prioridade da UI, ver `SecaoJogadorMercados`).
  - `model_match_estimates.params` -- lambda_home/lambda_away/rho do modelo
    macro já treinado (mesmo jsonb que `AnaliseAvancadaEvento.jsx::
    lerParametrosPartida` lê) -- usado como `macro_priors` da Camada 2.
    Partida sem nenhum `model_name` com params utilizáveis é pulada (não há
    macro pra reconciliar contra).

GSAx (goleiro) e `delta_shooting` (xGOT-xG por jogador) não existem em
lugar nenhum do projeto hoje -- `gk_stats` usa `gsax_rate=0.0` pros dois
lados (neutro, sem modular nada) e `delta_shooting` fica ausente em
`jogadores` (a Camada 1 já trata coluna ausente como 0 com aviso, não
exceção). Gaps de dado documentados, não bugs -- ver `pricing_pipeline.py`.

Uso:
    SUPABASE_URL=... SUPABASE_KEY=... python3 rodar_pricing_pipeline.py [--dias N] [--match-ids ID,ID,...]
"""

from __future__ import annotations

import argparse
import logging
import os

import pandas as pd
from supabase import Client, create_client

from pricing_pipeline import DixonColesJointEngine, HierarchicalReconciler, PlayerToTeamAggregator
from rodar_jogador_mercados_previsto import buscar_fixtures

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

MODEL_NAME_SAIDA = "pricing_pipeline_v1"
COLUNAS_JOGADOR = [
    "match_id", "team_id", "player_id", "fonte_titular", "is_titular_previsto", "prob_titular_usada",
    "posicao_detalhe", "minutos_esperados", "lambda_chutes_jogo", "lambda_chutes_no_alvo_jogo",
    "lambda_xg_jogo", "taxa_conversao_bayesiana",
]


def _buscar_macro_priors(supabase: Client, match_ids: list[int]) -> dict[int, dict]:
    """`{match_id: {lambda_home, lambda_away, rho}}` -- primeiro `model_name`
    com lambda_home/lambda_away > 0 em `model_match_estimates.params`, mesmo
    critério de `lerParametrosPartida` (frontend). Partida sem nenhum
    `model_name` utilizável simplesmente não aparece no dict -- quem chama
    pula (não há macro pra reconciliar contra, ver docstring do módulo)."""
    resp = (
        supabase.table("model_match_estimates")
        .select("match_id, params")
        .in_("match_id", match_ids)
        .not_.is_("params", "null")
        .execute()
    )
    saida: dict[int, dict] = {}
    for linha in resp.data or []:
        if linha["match_id"] in saida:
            continue  # já achou um macro utilizável pra essa partida, primeiro que aparece vale
        params = linha.get("params") or {}
        try:
            lam = float(params.get("lambda_home"))
            mu = float(params.get("lambda_away"))
        except (TypeError, ValueError):
            continue
        if lam <= 0 or mu <= 0:
            continue
        rho = params.get("rho")
        try:
            rho = float(rho)
        except (TypeError, ValueError):
            rho = 0.0
        saida[linha["match_id"]] = {"lambda_home": lam, "lambda_away": mu, "rho_liga": rho}
    return saida


def _buscar_jogadores(supabase: Client, match_ids: list[int]) -> pd.DataFrame:
    """Uma linha por jogador das partidas em `match_ids`, nas DUAS fontes
    quando existirem -- a escolha de qual fonte usar (real > previsto,
    mesma prioridade de `SecaoJogadorMercados`) acontece depois, por
    partida, em `_selecionar_fonte_por_partida`."""
    resp = (
        supabase.table("player_match_estimates")
        .select(",".join(COLUNAS_JOGADOR))
        .in_("match_id", match_ids)
        .execute()
    )
    return pd.DataFrame(resp.data or [], columns=COLUNAS_JOGADOR)


def _selecionar_fonte_por_partida(jogadores_partida: pd.DataFrame) -> pd.DataFrame:
    fontes = set(jogadores_partida["fonte_titular"])
    fonte = "real" if "real" in fontes else "previsto"
    return jogadores_partida[jogadores_partida["fonte_titular"] == fonte]


def rodar(supabase: Client, dias: int, match_ids: list[int] | None) -> int:
    fixtures = buscar_fixtures(supabase, dias, match_ids)
    if fixtures.empty:
        logger.info("Nenhuma partida 'scheduled' na janela -- nada a precificar.")
        return 0

    fixture_ids = [int(m) for m in fixtures["id"].tolist()]
    macro_por_partida = _buscar_macro_priors(supabase, fixture_ids)
    jogadores_todos = _buscar_jogadores(supabase, fixture_ids)

    agregador = PlayerToTeamAggregator()
    reconciler = HierarchicalReconciler()
    engine = DixonColesJointEngine()

    linhas_saida = []
    partidas_processadas = 0
    for _, fixture in fixtures.iterrows():
        match_id = int(fixture["id"])
        macro = macro_por_partida.get(match_id)
        if macro is None:
            logger.info("Partida %s sem modelo macro (model_match_estimates.params) utilizável -- pulando.", match_id)
            continue

        jogadores_partida = jogadores_todos[jogadores_todos["match_id"] == match_id]
        if jogadores_partida.empty:
            logger.info("Partida %s sem player_match_estimates -- pulando.", match_id)
            continue
        jogadores_partida = _selecionar_fonte_por_partida(jogadores_partida)

        home_id, away_id = int(fixture["home_team_id"]), int(fixture["away_team_id"])
        jogadores_home = jogadores_partida[jogadores_partida["team_id"] == home_id]
        jogadores_away = jogadores_partida[jogadores_partida["team_id"] == away_id]
        if jogadores_home.empty or jogadores_away.empty:
            logger.info("Partida %s sem elenco dos 2 times em player_match_estimates -- pulando.", match_id)
            continue

        try:
            agregacao_home = agregador.agregar(jogadores_home, gsax_rate_adversario=0.0)
            agregacao_away = agregador.agregar(jogadores_away, gsax_rate_adversario=0.0)
        except ValueError as exc:
            logger.warning("Partida %s: falha na Camada 1 (%s) -- pulando.", match_id, exc)
            continue

        reconciliacao_home = reconciler.reconciliar(
            agregacao_home.lambda_bottom_up, macro["lambda_home"], contexto=f"match {match_id} mandante"
        )
        reconciliacao_away = reconciler.reconciliar(
            agregacao_away.lambda_bottom_up, macro["lambda_away"], contexto=f"match {match_id} visitante"
        )
        # Quarentena não bloqueia a Camada 3 (documentado em pricing_
        # pipeline.py) -- a matriz ainda é construída com o lambda macro, só
        # que o valor exibido acaba não tendo o "de acordo" do bottom-up.
        # Como este runner só grava probabilidade (não decide aposta), não
        # há Camada 4 aqui pra propagar quarantine_flag em stake=0 -- fica
        # registrado só via log.
        if reconciliacao_home.quarantine_flag or reconciliacao_away.quarantine_flag:
            logger.warning(
                "Partida %s: reconciliação em quarentena (home kappa=%s, away kappa=%s) -- "
                "gravando mesmo assim (Camada 3 usa lambda_macro), sem decisão de aposta.",
                match_id, reconciliacao_home.kappa, reconciliacao_away.kappa,
            )

        resultado = engine.gerar(reconciliacao_home.lambda_final, reconciliacao_away.lambda_final, macro["rho_liga"])
        for (mercado, selecao), probabilidade in resultado.mercados.items():
            linhas_saida.append({
                "match_id": match_id, "model_name": MODEL_NAME_SAIDA, "market": mercado,
                "selection": selecao, "probability": round(float(probabilidade), 5),
            })
        partidas_processadas += 1

    if not linhas_saida:
        logger.info("Nenhuma linha gerada.")
        return 0

    for i in range(0, len(linhas_saida), 500):
        lote = linhas_saida[i : i + 500]
        supabase.table("model_predictions").upsert(lote, on_conflict="match_id,model_name,market,selection").execute()

    logger.info(
        "%d linhas gravadas em model_predictions (%d de %d partidas na janela).",
        len(linhas_saida), partidas_processadas, len(fixtures),
    )
    return len(linhas_saida)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=7, help="janela de dias à frente para precificar (default 7)")
    ap.add_argument("--match-ids", type=str, default=None, help="lista de match_id separados por vírgula -- ignora --dias quando presente")
    args = ap.parse_args()

    url = os.environ["SUPABASE_URL"].strip()
    key = os.environ["SUPABASE_KEY"].strip()
    sb = create_client(url, key)

    ids = [int(x) for x in args.match_ids.split(",")] if args.match_ids else None
    rodar(sb, args.dias, ids)
