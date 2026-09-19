#!/usr/bin/env python3
"""Matriz de confiabilidade ODD x EV -- pergunta do usuário: existe uma
combinação (faixa de odd, faixa de edge) onde um modelo bate o mercado de
forma repetível, mesmo que o modelo perca no agregado? (ex.: "entre 13% e
25% de EV pra odds de 3.1 a 5.7 é confiável")

Roda essa análise pros 3 pares modelo/mercado que já têm walk-forward
incremental out-of-sample + mercado real com odds suficientes neste
projeto: `hibrido_gols_xg_v1` (1X2/over_under_2.5/btts), `hibrido_
corners_v1` (corners_over_under_9.5) e o classificador de Cartões (6
linhas). Reaproveita o loop de treino/previsão de cada um por import
direto (`validar_hibrido_walkforward_incremental.py`/`validar_corners_
walkforward_incremental.py`/`validar_cartoes_walkforward_incremental.py`)
-- monta o dataset "Feature Stacked" (a parte cara, ~90-110min) UMA VEZ só
e reaproveita pros 3, em vez de rodar 3 workflows separados.

LIÇÃO DA RODADA ANTERIOR (`analisar_cartoes_edge_ev.py`, ver CONTEXTO_
PROJETO.md "PENDENTE DE INVESTIGAÇÃO 19/09"): segmentar só por EDGE
CUMULATIVO (edge >= X) sem controlar a FAIXA DE ODD produziu ROI médio
implausível (+100% a +880%), quase certamente artefato de poucas apostas
de odd extrema dominando a média (cauda direita gorda) sobrevivendo ao
bootstrap percentil. Correções aplicadas aqui:
  1. Segmenta por uma GRADE 2D (faixa de odd x faixa de edge FECHADA, não
     cumulativa) -- isola o efeito de odd extrema em vez de deixá-lo
     vazar pra qualquer limiar de edge que o inclua.
  2. Critério de "confiável" agora exige o IC95% da MEDIANA do ROI
     TAMBÉM positivo, além da média (a mediana é robusta a outliers de
     odd alta; se só a média passa, é o mesmo sintoma do artefato
     anterior) -- decisão do usuário, opção "rigorosa".
  3. Amostra mínima por célula subiu de 20/30 pra 50 apostas.
Mesmo assim, qualquer célula "confiável" que sair daqui deve ser tratada
como HIPÓTESE a testar com mais dado (o cron acumulando mais partidas),
não como conclusão definitiva -- é uma grade de ~9-20 células por
modelo/mercado, e mesmo com bootstrap, testar múltiplas células ao mesmo
tempo aumenta a chance de achar uma "significativa" por acaso puro
(problema de comparações múltiplas, não corrigido aqui de propósito --
resultado é exploratório, não confirmatório).

FAIXAS DE ODD: as mesmas de `backtest_kelly.FAIXAS_STAKING` (1.30-2.50/
2.50-4.00/4.00-8.00/8.00+) -- já é a categorização de risco usada em
produção pra dimensionar stake, reaproveitada aqui em vez de inventar
uma nova.
FAIXAS DE EDGE: bandas fechadas 2-5%/5-10%/10-15%/15-25%/25%+ (não
cumulativas, ao contrário do script anterior).

Não escreve nada no Supabase -- só leitura e relatório em stdout, mesma
categoria dos demais scripts `validar_*_walkforward_incremental.py`.

Uso:
    set SUPABASE_URL=...
    set SUPABASE_KEY=sua_service_role_key (ou anon -- só leitura)
    python scripts/matriz_confiabilidade_ev.py
"""

from __future__ import annotations

import logging
import os
import sys

import numpy as np
import pandas as pd
from supabase import Client, create_client

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_kelly as bk
import dados_historicos as dh
import distribuicoes as dist
import validar_cartoes_walkforward_incremental as wf_cartoes
import validar_corners_walkforward_incremental as wf_corners
import validar_hibrido_walkforward_incremental as wf_gols

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
logger = logging.getLogger("matriz_confiabilidade_ev")

ODD_BUCKETS = [(f["odd_min"], f["odd_max"]) for f in bk.FAIXAS_STAKING]
EDGE_BUCKETS = [(0.02, 0.05), (0.05, 0.10), (0.10, 0.15), (0.15, 0.25), (0.25, float("inf"))]
MIN_N_CELULA = 50
N_REAMOSTRAGENS = bk.N_REAMOSTRAGENS_BOOTSTRAP
SEED = bk.SEED

MERCADOS_GOLS = ["1X2", "over_under_2.5", "btts"]
MODELO_GOLS = "hibrido_gols_xg_v1"
MERCADO_ESCANTEIOS = "corners_over_under_9.5"


def obter_env(nome: str) -> str:
    valor = os.environ.get(nome)
    if not valor:
        sys.exit(f"Configure {nome} antes de rodar.")
    return valor


def obter_league_ids_escopo(supabase: Client) -> list[int]:
    linhas = (
        supabase.table("leagues").select("id")
        .eq("modelo_misto_escopo", "treino")
        .execute().data or []
    )
    return [l["id"] for l in linhas]


def bootstrap_ic95_estatistica(valores: np.ndarray, fn_estat, n_reamostragens: int = N_REAMOSTRAGENS, seed: int = SEED) -> tuple[float, float, float]:
    """Generaliza `backtest_kelly.bootstrap_ic95_roi` pra qualquer
    estatística (média OU mediana) -- precisamos das duas."""
    if len(valores) == 0:
        return 0.0, 0.0, 0.0
    estat = float(fn_estat(valores))
    rng = np.random.default_rng(seed)
    reamostras = [float(fn_estat(rng.choice(valores, size=len(valores), replace=True))) for _ in range(n_reamostragens)]
    lo, hi = np.percentile(reamostras, [2.5, 97.5])
    return estat, float(lo), float(hi)


def avaliar_celula(apostas_bucket: list[dict], modelo: str, mercado: str, faixa_odd: tuple[float, float], faixa_edge: tuple[float, float]) -> dict | None:
    rois = np.array(bk.simular_banca(apostas_bucket))
    if len(rois) < MIN_N_CELULA:
        return None
    roi_medio, media_lo, media_hi = bootstrap_ic95_estatistica(rois, np.mean)
    roi_mediano, mediana_lo, mediana_hi = bootstrap_ic95_estatistica(rois, np.median)
    confiavel = media_lo > 0 and mediana_lo > 0
    return {
        "modelo": modelo, "mercado": mercado, "odd_min": faixa_odd[0], "odd_max": faixa_odd[1],
        "edge_min": faixa_edge[0], "edge_max": faixa_edge[1], "n": len(rois),
        "roi_medio": roi_medio, "media_ic95": (media_lo, media_hi),
        "roi_mediano": roi_mediano, "mediana_ic95": (mediana_lo, mediana_hi),
        "confiavel": confiavel,
    }


def avaliar_matriz(apostas: list[dict], modelo: str, mercado: str) -> list[dict]:
    for aposta in apostas:
        aposta["edge"] = aposta["prob_modelo"] - (1 / aposta["odd"])
    resultados = []
    for odd_min, odd_max in ODD_BUCKETS:
        for edge_min, edge_max in EDGE_BUCKETS:
            subset = [a for a in apostas if odd_min <= a["odd"] < odd_max and edge_min <= a["edge"] < edge_max]
            celula = avaliar_celula(subset, modelo, mercado, (odd_min, odd_max), (edge_min, edge_max))
            if celula is not None:
                resultados.append(celula)
    logger.info("[%s, %s]: %d células com n>=%d de %d possíveis.",
                modelo, mercado, len(resultados), MIN_N_CELULA, len(ODD_BUCKETS) * len(EDGE_BUCKETS))
    return resultados


# =============================================================================
# Coleta de apostas por modelo/mercado (reaproveita cada walk-forward)
# =============================================================================
def coletar_apostas_gols(supabase: Client, dataset: pd.DataFrame) -> list[dict]:
    features = [f for f in wf_gols.MODELOS[MODELO_GOLS] if f in dataset.columns]
    previsoes = wf_gols.gerar_previsoes_walkforward(dataset, features, MODELO_GOLS)
    if not previsoes:
        logger.error("[%s]: nenhuma previsão gerada.", MODELO_GOLS)
        return []

    resultados_gols = dict(zip(dataset["match_id"], zip(dataset["home_goals"], dataset["away_goals"])))
    match_ids = list(previsoes.keys())
    apostas_total = []
    for mercado in MERCADOS_GOLS:
        odds_reais = bk.carregar_melhores_odds_fechamento(supabase, match_ids, mercado)
        predicoes, resultados_reais = {}, {}
        for match_id, p in previsoes.items():
            hg, ag = resultados_gols.get(match_id, (None, None))
            if pd.isna(hg) or pd.isna(ag):
                continue
            mercados_modelo = wf_gols._probs_mercados(p["lam_home"], p["lam_away"], p["rho"])
            predicoes[match_id] = {f"prob_{selecao}": prob for (m, selecao), prob in mercados_modelo.items() if m == mercado}
            resultados_reais[match_id] = bk._resultado_codigo_mercado(hg, ag, mercado)
        apostas = bk.montar_apostas(predicoes, odds_reais, resultados_reais, mercado=mercado)
        logger.info("[%s, %s]: %d apostas com edge >= %.0f%% e odd real disponível.", MODELO_GOLS, mercado, len(apostas), bk.EDGE_MINIMO * 100)
        apostas_total.extend([{**a, "mercado": mercado} for a in apostas])
    return apostas_total


def coletar_apostas_escanteios(supabase: Client, dataset: pd.DataFrame) -> list[dict]:
    features = [f for f in wf_corners.FEATURES if f in dataset.columns]
    n = len(dataset)
    corte_inicial = dataset["match_date"].iloc[int(n * wf_corners.FRACAO_TREINO_INICIAL)]
    warmup = dataset[dataset["match_date"] < corte_inicial]
    alpha_prior = wf_corners.calcular_prior_disp_r_global(warmup, features)
    passos = wf_corners.montar_passos_mensais(dataset, corte_inicial)
    previsoes = wf_corners.gerar_previsoes_walkforward(dataset, features, alpha_prior, passos)
    if not previsoes:
        logger.error("[%s]: nenhuma previsão gerada.", wf_corners.MODEL_NAME)
        return []

    resultados_reais_total = dict(zip(dataset["match_id"], dataset[wf_corners.ALVO_TOTAL]))
    match_ids = list(previsoes.keys())
    predicoes, resultados_reais = {}, {}
    for match_id, p in previsoes.items():
        total_real = resultados_reais_total.get(match_id)
        if pd.isna(total_real):
            continue
        mercados_modelo = dist.mercados_de_escanteios(p["lam_corners"], p["disp_r"], p["alpha"], p["beta"])
        predicoes[match_id] = {
            "prob_over": mercados_modelo[(MERCADO_ESCANTEIOS, "over")],
            "prob_under": mercados_modelo[(MERCADO_ESCANTEIOS, "under")],
        }
        resultados_reais[match_id] = dh.RESULTADO_CORNERS_OVER95 if total_real > wf_corners.LINHA_MERCADO else dh.RESULTADO_CORNERS_UNDER95

    odds_reais = bk.carregar_melhores_odds_fechamento(supabase, match_ids, MERCADO_ESCANTEIOS)
    apostas = bk.montar_apostas(predicoes, odds_reais, resultados_reais, mercado=MERCADO_ESCANTEIOS)
    logger.info("[%s, %s]: %d apostas com edge >= %.0f%% e odd real disponível.",
                wf_corners.MODEL_NAME, MERCADO_ESCANTEIOS, len(apostas), bk.EDGE_MINIMO * 100)
    return [{**a, "mercado": MERCADO_ESCANTEIOS} for a in apostas]


def coletar_apostas_cartoes(supabase: Client, dataset: pd.DataFrame) -> list[dict]:
    features = [f for f in wf_cartoes.FEATURES_CARTOES if f in dataset.columns]
    apostas_total = []
    for linha in dh.LINHAS_CARTOES_OU:
        coluna_alvo = dh.coluna_resultado_cartoes_ou(linha)
        if coluna_alvo not in dataset.columns:
            continue
        previsoes = wf_cartoes.gerar_previsoes_walkforward(dataset, features, coluna_alvo, linha)
        if not previsoes:
            continue
        mercado = f"cartoes_over_under_{linha}"
        match_ids = list(previsoes.keys())
        odds_reais = bk.carregar_melhores_odds_fechamento(supabase, match_ids, mercado)
        resultados_reais = dict(zip(dataset["match_id"].astype(int), dataset[coluna_alvo]))
        predicoes = {mid: {"prob_over": p, "prob_under": 1 - p} for mid, p in previsoes.items()}
        apostas = bk.montar_apostas(predicoes, odds_reais, resultados_reais, mercado=mercado)
        logger.info("[cartoes_total %.1f]: %d apostas com edge >= %.0f%% e odd real disponível.", linha, len(apostas), bk.EDGE_MINIMO * 100)
        apostas_total.extend([{**a, "mercado": mercado} for a in apostas])
    return apostas_total


def imprimir_matriz(resultados: list[dict]) -> None:
    resultados_ordenados = sorted(resultados, key=lambda r: r["media_ic95"][0], reverse=True)
    n_confiaveis = sum(1 for r in resultados if r["confiavel"])
    logger.info("=" * 110)
    logger.info("MATRIZ DE CONFIABILIDADE ODD x EDGE (critério rigoroso: IC95%% da média E da mediana > 0, n>=%d)", MIN_N_CELULA)
    logger.info("%d de %d células avaliadas passam no critério rigoroso.", n_confiaveis, len(resultados))
    logger.info("=" * 110)
    for r in resultados_ordenados:
        edge_max_str = f"{r['edge_max']*100:.0f}%" if r["edge_max"] != float("inf") else "inf"
        odd_max_str = f"{r['odd_max']:.2f}" if r["odd_max"] != float("inf") else "inf"
        veredito = "CONFIÁVEL (média E mediana IC95%>0)" if r["confiavel"] else "não confiável"
        logger.info(
            "%s [%s] | odd [%.2f,%s) | edge [%.0f%%,%s) | n=%4d | ROI médio %+7.1f%% IC95%%[%+7.1f%%,%+7.1f%%] | ROI mediano %+7.1f%% IC95%%[%+7.1f%%,%+7.1f%%] | %s",
            r["modelo"], r["mercado"], r["odd_min"], odd_max_str, r["edge_min"] * 100, edge_max_str, r["n"],
            r["roi_medio"] * 100, r["media_ic95"][0] * 100, r["media_ic95"][1] * 100,
            r["roi_mediano"] * 100, r["mediana_ic95"][0] * 100, r["mediana_ic95"][1] * 100,
            veredito,
        )
    logger.info("=" * 110)


def main() -> None:
    supabase = create_client(obter_env("SUPABASE_URL"), obter_env("SUPABASE_KEY"))

    league_ids = obter_league_ids_escopo(supabase)
    logger.info("Montando dataset Feature Stacked (%d ligas em escopo)...", len(league_ids))
    dataset = dh.montar_dataset_ml_empilhado(supabase, league_ids_manual=league_ids)
    dataset["match_date"] = pd.to_datetime(dataset["match_date"], utc=True)
    dataset = dataset.dropna(subset=["match_id"]).sort_values("match_date").reset_index(drop=True)
    logger.info("Dataset montado: %d partidas (%s a %s).",
                len(dataset), dataset["match_date"].min().date(), dataset["match_date"].max().date())

    resultados_total = []

    logger.info("--- Gols (%s) ---", MODELO_GOLS)
    apostas_gols = coletar_apostas_gols(supabase, dataset)
    for mercado in MERCADOS_GOLS:
        resultados_total.extend(avaliar_matriz([a for a in apostas_gols if a["mercado"] == mercado], MODELO_GOLS, mercado))

    logger.info("--- Escanteios (%s) ---", wf_corners.MODEL_NAME)
    apostas_escanteios = coletar_apostas_escanteios(supabase, dataset)
    resultados_total.extend(avaliar_matriz(apostas_escanteios, wf_corners.MODEL_NAME, MERCADO_ESCANTEIOS))

    logger.info("--- Cartões (classificador Random Forest) ---")
    apostas_cartoes = coletar_apostas_cartoes(supabase, dataset)
    for linha in dh.LINHAS_CARTOES_OU:
        mercado = f"cartoes_over_under_{linha}"
        resultados_total.extend(avaliar_matriz([a for a in apostas_cartoes if a["mercado"] == mercado], "cartoes_rf", mercado))

    imprimir_matriz(resultados_total)


if __name__ == "__main__":
    main()
