#!/usr/bin/env python3
"""Simula a carteira cronológica de `catboost_v9` restrita à única célula
`confiavel=true` que a matriz de confiabilidade EV encontrou pra esse
modelo (achado 21/09): mandante, 1X2, odd [1.30,2.50), edge [5%,10%).
Diferente de `analisar_cartoes_liga_sinal.py`, aqui não há restrição de
liga nem de casa de aposta -- a quebra por seleção (ver conversa que
motivou este script) já mostrou que o efeito é positivo nas 6 ligas do
benchmarking; a quebra por seleção é o filtro relevante (mandante sim,
visitante/empate não), não geografia.

DESENHO: diferente do script de cartões, não precisa reconstruir nenhum
walk-forward -- as previsões de catboost_v9 já estão persistidas em
`model_predictions` (produção diária via `rodar_catboost_v9_previsto.py` +
backfill histórico via `backfill_catboost_v9_2026.py`). Este script só lê
o que já existe, cruza com a odd real de fechamento e o resultado real,
filtra pra faixa confiável e simula a carteira cronológica (mesma
`matriz_confiabilidade_ev.simular_carteira_cronologica_detalhada` usada
pra cartões).

Escreve em `carteira_catboost_v9_historico` (precisa de service_role key)
-- alimenta a aba "Carteira catboost_v9" em Sugestões de Valor
(src/pages/ResumoValorApostas.jsx).

Uso:
    set SUPABASE_URL=...
    set SUPABASE_KEY=sua_service_role_key
    python scripts/analisar_catboost_v9_liga_sinal.py
"""

from __future__ import annotations

import logging
import os
import sys

from supabase import Client, create_client

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_kelly as bk
import dados_historicos as dh
import matriz_confiabilidade_ev as mcev

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
logger = logging.getLogger("analisar_catboost_v9_liga_sinal")

MODEL_NAME = "catboost_v9"
MERCADO = "1X2"
SELECAO_ALVO = "home"
ODD_MIN, ODD_MAX = 1.30, 2.50
EDGE_MIN, EDGE_MAX = 0.05, 0.10

# Ver migration 20260921180000_create_carteira_catboost_v9_historico.sql e
# model_betting_strategy (mercado='1x2_mandante').
SUB_FAIXA_CARTEIRA = "catboost_v9_odd1.30-2.50_edge5-10"


def obter_env(nome: str) -> str:
    valor = os.environ.get(nome)
    if not valor:
        sys.exit(f"Configure {nome} antes de rodar.")
    return valor


def main() -> None:
    supabase: Client = create_client(obter_env("SUPABASE_URL"), obter_env("SUPABASE_KEY"))

    logger.info("Lendo previsões de %s (market=1x2) já persistidas em model_predictions...", MODEL_NAME)
    linhas_pred = dh._paginar(
        lambda inicio, fim: supabase.table("model_predictions")
        .select("match_id, selection, probability")
        .eq("model_name", MODEL_NAME)
        .eq("market", "1x2")
        .range(inicio, fim)
    )
    if not linhas_pred:
        logger.error("Nenhuma previsão de %s encontrada em model_predictions -- encerrando.", MODEL_NAME)
        return

    previsoes: dict[int, dict[str, float]] = {}
    for linha in linhas_pred:
        previsoes.setdefault(linha["match_id"], {})[f"prob_{linha['selection']}"] = linha["probability"]
    match_ids = list(previsoes.keys())
    logger.info("%d partida(s) com previsão de %s.", len(match_ids), MODEL_NAME)

    linhas_matches = dh._paginar_por_lotes_de_id(
        lambda lote, inicio, fim: supabase.table("matches")
        .select("id, league_id, home_goals, away_goals, match_date")
        .in_("id", lote)
        .range(inicio, fim),
        match_ids,
    )
    resultados_reais: dict[int, int] = {}
    datas_por_match: dict[int, str] = {}
    league_id_por_match: dict[int, int] = {}
    for linha in linhas_matches:
        if linha["home_goals"] is None or linha["away_goals"] is None:
            continue
        resultados_reais[linha["id"]] = bk._resultado_codigo_mercado(linha["home_goals"], linha["away_goals"], MERCADO)
        datas_por_match[linha["id"]] = linha["match_date"]
        league_id_por_match[linha["id"]] = linha["league_id"]

    league_ids = sorted({v for v in league_id_por_match.values() if v is not None})
    linhas_leagues = (
        supabase.table("leagues").select("id, name").in_("id", league_ids).execute().data or []
        if league_ids
        else []
    )
    nome_por_league_id = {l["id"]: l["name"] for l in linhas_leagues}
    liga_por_match_id = {
        mid: nome_por_league_id.get(lid) for mid, lid in league_id_por_match.items() if lid in nome_por_league_id
    }

    logger.info("Buscando melhores odds reais de fechamento (1X2)...")
    odds_reais, bookmaker_por_partida = bk.carregar_melhores_odds_fechamento_com_bookmaker(supabase, match_ids, MERCADO)

    apostas = bk.montar_apostas(
        previsoes, odds_reais, resultados_reais,
        liga_por_match_id=liga_por_match_id, mercado=MERCADO, bookmakers_por_partida=bookmaker_por_partida,
    )
    logger.info("%d apostas com edge >= %.0f%% e odd real disponível (antes do filtro da faixa confiável).",
                len(apostas), bk.EDGE_MINIMO * 100)

    confiaveis = [
        a for a in apostas
        if a["selecao"] == SELECAO_ALVO
        and ODD_MIN <= a["odd"] < ODD_MAX
        and EDGE_MIN <= (a["prob_modelo"] - 1 / a["odd"]) < EDGE_MAX
        and a["match_id"] in datas_por_match
    ]
    logger.info(
        "%d apostas na faixa confiável (mandante, odd [%.2f,%.2f), edge [%.0f%%,%.0f%%)) de %d totais.",
        len(confiaveis), ODD_MIN, ODD_MAX, EDGE_MIN * 100, EDGE_MAX * 100, len(apostas),
    )
    if not confiaveis:
        logger.error("Nenhuma aposta na faixa confiável -- não há como simular a carteira.")
        return

    for a in confiaveis:
        a["mercado"] = MERCADO
        a["linha"] = ODD_MIN
        a["match_date"] = datas_por_match[a["match_id"]]
        # prob_devig precisa das 3 pontas do 1X2 (não só 2 como em O/U) --
        # carregar_melhores_odds_fechamento_com_bookmaker já devolve as 3
        # (odd_home/odd_draw/odd_away) no mesmo dict por partida.
        odds_partida = odds_reais.get(a["match_id"], {})
        odd_home, odd_draw, odd_away = odds_partida.get("odd_home"), odds_partida.get("odd_draw"), odds_partida.get("odd_away")
        if odd_home and odd_draw and odd_away:
            a["prob_devig"] = bk._devig_odds_ratio({"home": odd_home, "draw": odd_draw, "away": odd_away})[a["selecao"]]
        else:
            a["prob_devig"] = None

    relatorio = bk.resumir_backtest(f"{MODEL_NAME} (faixa confiável mandante/1X2)", confiaveis, None)
    logger.info("=" * 100)
    logger.info(
        "AGREGADO: n=%d | ROI médio %+.1f%% IC95%%[%+.1f%%,%+.1f%%] | %s",
        relatorio["n_apostas"], relatorio["roi_medio"] * 100,
        relatorio["roi_ic95_inferior"] * 100, relatorio["roi_ic95_superior"] * 100,
        "SIGNIFICATIVO (IC95%>0)" if relatorio["significativo"] else "sem evidência",
    )
    logger.info("=" * 100)

    relatorio_por_liga = bk.resumir_por_liga(MODEL_NAME, confiaveis)
    bk.imprimir_relatorio_por_liga(relatorio_por_liga)

    apostas_ordenadas = sorted(confiaveis, key=lambda a: a["match_date"])
    resultado_carteira, ledger = mcev.simular_carteira_cronologica_detalhada(apostas_ordenadas)
    logger.info(
        "CARTEIRA: banca final %.2fx, drawdown máx %.1f%% (n apostado=%d)",
        resultado_carteira["banca_final_x"], resultado_carteira["drawdown_maximo"] * 100, resultado_carteira["n_apostado"],
    )
    persistir_carteira(supabase, SUB_FAIXA_CARTEIRA, ledger)
    logger.info("=" * 100)


def persistir_carteira(supabase: Client, sub_faixa: str, ledger: list[dict]) -> None:
    """Apaga e recria (não upsert incremental) -- mesmo padrão de
    `analisar_cartoes_liga_sinal.persistir_carteira_restrita`: cada execução
    recalcula do zero (mais jogos podem ter sido resolvidos desde a última
    vez), então o ledger persistido precisa refletir exatamente a última
    execução."""
    linhas = []
    for i, aposta in enumerate(ledger):
        linhas.append({
            "sub_faixa": sub_faixa,
            "match_id": int(aposta["match_id"]),
            "match_date": aposta["match_date"],
            "mercado": aposta["mercado"],
            "linha": aposta["linha"],
            "selecao": aposta["selecao"],
            "bookmaker": aposta.get("casa_aposta"),
            "prob_modelo": aposta["prob_modelo"],
            "odd_justa": 1 / aposta["prob_modelo"],
            "odd_real": aposta["odd"],
            "prob_devig": aposta.get("prob_devig"),
            "edge": aposta["prob_modelo"] - 1 / aposta["odd"],
            "ev": aposta["prob_modelo"] * aposta["odd"] - 1,
            "stake_pct": aposta["stake_pct"],
            "acertou": bool(aposta["acertou"]),
            "banca_antes": aposta["banca_antes"],
            "banca_depois": aposta["banca_depois"],
            "ordem": i,
        })
    supabase.table("carteira_catboost_v9_historico").delete().eq("sub_faixa", sub_faixa).execute()
    if not linhas:
        logger.warning("Ledger vazio -- nada persistido em carteira_catboost_v9_historico (sub_faixa=%s).", sub_faixa)
        return
    for inicio in range(0, len(linhas), 500):
        lote = linhas[inicio: inicio + 500]
        supabase.table("carteira_catboost_v9_historico").upsert(
            lote, on_conflict="sub_faixa,match_id,mercado,selecao"
        ).execute()
    logger.info("Persistidas %d linhas em carteira_catboost_v9_historico (sub_faixa=%s).", len(linhas), sub_faixa)


if __name__ == "__main__":
    main()
