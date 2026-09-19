#!/usr/bin/env python3
"""Segmenta as apostas de Cartões (walk-forward incremental, mesmo modelo
de `validar_cartoes_walkforward_incremental.py`) por FAIXA DE EDGE --
pergunta do usuário: mesmo que o modelo perca do mercado NO AGREGADO
(PR #592, toda linha conclusiva com MERCADO SUPERIOR), pode existir uma
faixa de confiança (edge alto) onde o modelo consistentemente vence?

DIFERENÇA METODOLÓGICA em relação ao PR #592 (log-loss pareado): aqui a
pergunta é "dá pra ganhar dinheiro apostando só nos jogos de edge alto?",
não "a probabilidade do modelo é mais bem calibrada que a do mercado?" --
por isso usa a MESMA máquina de EV+ já estabelecida no projeto pra ROI/
Kelly (`backtest_kelly.montar_apostas`/`simular_banca`/`bootstrap_ic95_
roi`, mesmo critério de `api/backtest-betting.js`: só conta como edge real
quando o LIMITE INFERIOR do IC95% do ROI fica acima de zero, nunca o ROI
médio isolado). Usa a MELHOR ODD REAL disponível (`carregar_melhores_
odds_fechamento`, pre_closing com fallback pra closing) -- não a Pinnacle
devigada do PR #592 (aquela mede qualidade de probabilidade; esta mede
lucro de apostar de verdade, então precisa da odd que se paga de fato,
não da probabilidade "verdadeira" implícita).

LIMIARES_EDGE = [0.02, 0.04, 0.07, 0.10] -- os mesmos 4 valores de
`ev_minimo` já usados em `backtest_kelly.FAIXAS_STAKING` (não são a mesma
grandeza -- EV vs. edge de probabilidade -- mas servem de referência
natural já validada no projeto pra "quão exigente" cada corte é). Cada
faixa é CUMULATIVA (edge >= limiar), não um bucket fechado -- é a mesma
prática já usada informalmente neste projeto (ver CONTEXTO_PROJETO.md,
"o filtro de EV corta a amostra... às vezes muda o sinal de negativo pra
positivo") de subir a régua de exigência e ver se o ROI destrava.

Não escreve nada no Supabase -- só leitura e relatório em stdout, mesma
categoria dos demais scripts `validar_*_walkforward_incremental.py`.
Reaproveita o dataset "Feature Stacked" e o loop de treino/previsão de
`validar_cartoes_walkforward_incremental.py` por import direto (mesmo
walk-forward, mesmas previsões out-of-sample) -- só troca a AVALIAÇÃO.

Uso:
    set SUPABASE_URL=...
    set SUPABASE_KEY=sua_service_role_key (ou anon -- só leitura)
    python scripts/analisar_cartoes_edge_ev.py
"""

from __future__ import annotations

import logging
import os
import sys

import pandas as pd
from supabase import Client, create_client

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_kelly as bk
import dados_historicos as dh
import validar_cartoes_walkforward_incremental as wf

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
logger = logging.getLogger("analisar_cartoes_edge_ev")

LIMIARES_EDGE = [0.02, 0.04, 0.07, 0.10]
MIN_APOSTAS_LIMIAR = 20


def avaliar_linha_por_edge(supabase: Client, dataset: pd.DataFrame, previsoes: dict[int, float], linha: float, coluna_alvo: str) -> list[dict]:
    mercado = f"cartoes_over_under_{linha}"
    match_ids = list(previsoes.keys())
    odds_reais = bk.carregar_melhores_odds_fechamento(supabase, match_ids, mercado)
    resultados_reais = dict(zip(dataset["match_id"].astype(int), dataset[coluna_alvo]))
    predicoes = {mid: {"prob_over": p, "prob_under": 1 - p} for mid, p in previsoes.items()}

    apostas = bk.montar_apostas(predicoes, odds_reais, resultados_reais, mercado=mercado)
    for aposta in apostas:
        aposta["edge"] = aposta["prob_modelo"] - (1 / aposta["odd"])
    logger.info("[cartoes_total %.1f]: %d apostas com edge >= %.0f%% (EDGE_MINIMO padrão) e odd real disponível.",
                linha, len(apostas), bk.EDGE_MINIMO * 100)

    linhas_relatorio = []
    for limiar in LIMIARES_EDGE:
        subset = [a for a in apostas if a["edge"] >= limiar]
        rois = bk.simular_banca(subset)
        if len(rois) < MIN_APOSTAS_LIMIAR:
            logger.warning("[cartoes_total %.1f, edge>=%.0f%%]: só %d apostas com stake>0 -- pulando.",
                            linha, limiar * 100, len(rois))
            continue
        roi_medio, ic_inf, ic_sup = bk.bootstrap_ic95_roi(rois)
        linhas_relatorio.append({
            "linha": linha, "limiar_edge": limiar, "n_apostas": len(rois),
            "roi_medio": roi_medio, "roi_ic95_inferior": ic_inf, "roi_ic95_superior": ic_sup,
            "significativo": ic_inf > 0,
        })
        logger.info("[cartoes_total %.1f, edge>=%.0f%%]: n=%d | ROI médio=%+.1f%% | IC95%%=[%+.1f%%, %+.1f%%] | %s",
                    linha, limiar * 100, len(rois), roi_medio * 100, ic_inf * 100, ic_sup * 100,
                    "EV+ SIGNIFICATIVO" if ic_inf > 0 else "sem edge confirmado")
    return linhas_relatorio


def imprimir_relatorio_edge(linhas: list[dict]) -> None:
    linhas_ordenadas = sorted(linhas, key=lambda r: r["roi_ic95_inferior"], reverse=True)
    logger.info("=" * 90)
    logger.info("EV+ POR FAIXA DE EDGE (ROI via Kelly fracionário, bootstrap 95%s) -- ordenado por IC95%s inferior", "%", "%")
    logger.info("=" * 90)
    if not linhas_ordenadas:
        logger.info("Nenhuma combinação linha/limiar teve amostra suficiente (>= %d apostas com stake>0).", MIN_APOSTAS_LIMIAR)
    for r in linhas_ordenadas:
        veredito = "EV+ SIGNIFICATIVO (IC95% > 0)" if r["significativo"] else (
            "PERDE com significância (IC95% < 0)" if r["roi_ic95_superior"] < 0 else "inconclusivo"
        )
        logger.info(
            "cartoes_total O/U %.1f, edge>=%.0f%% | %4d apostas | ROI médio %+6.1f%% | IC95%% [%+6.1f%%, %+6.1f%%] | %s",
            r["linha"], r["limiar_edge"] * 100, r["n_apostas"], r["roi_medio"] * 100,
            r["roi_ic95_inferior"] * 100, r["roi_ic95_superior"] * 100, veredito,
        )
    logger.info("=" * 90)


def main() -> None:
    supabase = create_client(wf.obter_env("SUPABASE_URL"), wf.obter_env("SUPABASE_KEY"))

    league_ids = wf.obter_league_ids_escopo(supabase)
    logger.info("Montando dataset Feature Stacked (%d ligas em escopo)...", len(league_ids))
    dataset = dh.montar_dataset_ml_empilhado(supabase, league_ids_manual=league_ids)
    dataset["match_date"] = pd.to_datetime(dataset["match_date"], utc=True)
    dataset = dataset.dropna(subset=["match_id"]).sort_values("match_date").reset_index(drop=True)
    logger.info("Dataset montado: %d partidas (%s a %s).",
                len(dataset), dataset["match_date"].min().date(), dataset["match_date"].max().date())

    features = [f for f in wf.FEATURES_CARTOES if f in dataset.columns]
    faltando = set(wf.FEATURES_CARTOES) - set(features)
    if faltando:
        logger.warning("features ausentes no dataset (ignoradas): %s", sorted(faltando))

    linhas_relatorio_total = []
    for linha in dh.LINHAS_CARTOES_OU:
        coluna_alvo = dh.coluna_resultado_cartoes_ou(linha)
        if coluna_alvo not in dataset.columns:
            logger.warning("[cartoes_total %.1f]: coluna %r ausente no dataset -- pulando.", linha, coluna_alvo)
            continue
        previsoes = wf.gerar_previsoes_walkforward(dataset, features, coluna_alvo, linha)
        if not previsoes:
            logger.error("[cartoes_total %.1f]: nenhuma previsão gerada, pulando avaliação.", linha)
            continue
        linhas_relatorio_total.extend(avaliar_linha_por_edge(supabase, dataset, previsoes, linha, coluna_alvo))

    imprimir_relatorio_edge(linhas_relatorio_total)


if __name__ == "__main__":
    main()
