#!/usr/bin/env python3
"""Matriz de confiabilidade ODD x EV -- pergunta do usuário: existe uma
combinação (faixa de odd, faixa de edge) onde um modelo bate o mercado de
forma repetível, mesmo que o modelo perca no agregado? (ex.: "entre 13% e
25% de EV pra odds de 3.1 a 5.7 é confiável")

Roda essa análise pros 4 pares modelo/mercado que já têm previsão
out-of-sample + mercado real com odds suficientes neste projeto:
`hibrido_gols_xg_v1` (1X2/over_under_2.5/btts), `hibrido_corners_v1`
(corners_over_under_9.5), o classificador de Cartões (6 linhas) e
`catboost_v9` (1X2/over_under_2.5 -- pedido do usuário depois da auditoria
dos modelos "v9", CONTEXTO_PROJETO.md 16-17/09: melhor algoritmo individual
entre os classificadores de árvore, mas nunca testado com edge real contra
o mercado). Reaproveita o loop de treino/previsão de cada um dos 3
primeiros por import direto (`validar_hibrido_walkforward_incremental.py`/
`validar_corners_walkforward_incremental.py`/`validar_cartoes_
walkforward_incremental.py`) -- monta o dataset "Feature Stacked" (a parte
cara, ~90-110min) UMA VEZ só e reaproveita pros 3, em vez de rodar 3
workflows separados. `catboost_v9` é diferente: já tem previsão
out-of-sample persistida em `model_predictions` (walk-forward CV rodado
uma vez em `custom_model_configs`), não precisa desse dataset nem de
retreino aqui -- só lê o que já existe (`coletar_apostas_catboost_v9`).

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

REFORÇOS ESTATÍSTICOS ADICIONADOS (pedido do usuário, segunda rodada):
  4. Teste de Diebold-Mariano (`backtest_kelly.diebold_mariano_test`) como
     segunda evidência por célula, além do bootstrap -- usa variância de
     longo prazo (Newey-West/Bartlett) em vez de tratar cada reamostra
     como i.i.d., o que importa aqui porque apostas vizinhas no tempo (ex.
     mesma rodada de liga) podem compartilhar erro sistemático. Aplicado
     testando a série de ROI contra zero (matematicamente idêntico a
     comparar duas séries de "perda", com perda_b=0).
  5. Correção de Bonferroni E de Benjamini-Hochberg (FDR) sobre o
     p-valor de cada célula avaliada (gate = maior entre o p-valor da
     média e da mediana) -- corrige exatamente o problema de comparações
     múltiplas descrito abaixo. Uma célula só é "sobrevive à correção"
     quando passa em AMBAS (Bonferroni é mais conservador que FDR).
  6. Carteira simulada CRONOLÓGICA (`simular_carteira_cronologica`,
     mesmo espírito de "validado_carteira" já usado neste projeto pra
     escanteios/cartões) pra cada célula -- diferente do ROI por aposta
     de banca fixa (`backtest_kelly.simular_banca`, não-composto, pensado
     só pra comparar grupos), aqui a banca COMPÕE de verdade em ordem
     cronológica (stake = fração de Kelly da banca ATUAL) -- é o teste
     "se eu tivesse apostado isso em ordem, o que teria acontecido com
     meu dinheiro", incluindo drawdown máximo. Rodado em TODAS as
     células (não só as "confiáveis") pra dar uma base de comparação
     direta contra os grupos não confiáveis.

Mesmo com os reforços acima, qualquer célula "confiável" que sobreviva
às correções deve ser tratada como HIPÓTESE a testar com mais dado (o
cron acumulando mais partidas), não como conclusão definitiva -- a
carteira cronológica em particular tem variância alta com os tamanhos de
amostra típicos aqui (99-500 apostas).

FAIXAS DE ODD: as mesmas de `backtest_kelly.FAIXAS_STAKING` (1.30-2.50/
2.50-4.00/4.00-8.00/8.00+) -- já é a categorização de risco usada em
produção pra dimensionar stake, reaproveitada aqui em vez de inventar
uma nova.
FAIXAS DE EDGE: bandas fechadas 2-5%/5-10%/10-15%/15-25%/25%+ (não
cumulativas, ao contrário do script anterior).

PERSISTÊNCIA (pedido do usuário -- acompanhar no frontend, dia a dia, se as
células confiáveis continuam sobrevivendo conforme mais partidas entram no
banco): grava a matriz inteira em `matriz_confiabilidade_ev_historico`
(migration `20260919120000_create_matriz_confiabilidade_ev_historico.sql`)
via upsert em (data_execucao, modelo, mercado, odd_min, odd_max, edge_min,
edge_max) -- reexecutar no mesmo dia sobrescreve, não duplica. Precisa de
`SUPABASE_KEY` com privilégio de escrita (no workflow do GitHub Actions
esse secret já É a service_role key, mesmo padrão dos demais scripts que
escrevem, ex. `ingerir_escalacao_pre_jogo.py`).

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

# catboost_v9 (auditoria "v9", CONTEXTO_PROJETO.md 16-17/09): melhor
# algoritmo individual entre os classificadores de árvore treinados com
# walk-forward CV em `custom_model_configs`, mas nunca validado com edge
# real contra o mercado -- pedido do usuário pra incluir na matriz de
# confiabilidade e conferir de vez. BTTS fica de fora: a cobertura de odds
# de BTTS da Pinnacle só começa em jan/2026, sem sobreposição com o
# período coberto pelas previsões (jan/2023-dez/2025) -- incluir daria
# zero apostas sempre, achado já documentado.
MODELO_CATBOOST_V9 = "catboost_v9"
MERCADOS_CATBOOST_V9 = {"1x2": "1X2", "over_under_2.5": "over_under_2.5"}

# Sentinelas pra persistir as faixas "sem teto" (última de cada grade) sem
# usar NULL -- ver comentário da coluna na migration.
ODD_MAX_SENTINELA = 999.0
EDGE_MAX_SENTINELA = 9.99


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


def bootstrap_ic95_estatistica(valores: np.ndarray, fn_estat, n_reamostragens: int = N_REAMOSTRAGENS, seed: int = SEED) -> tuple[float, float, float, float]:
    """Generaliza `backtest_kelly.bootstrap_ic95_roi` pra qualquer
    estatística (média OU mediana) -- precisamos das duas. Devolve
    também um p-valor bootstrap UNICAUDAL pra H1: estatística > 0
    (proporção de reamostras que saem <= 0) -- usado depois pra
    Bonferroni/FDR."""
    if len(valores) == 0:
        return 0.0, 0.0, 0.0, 1.0
    estat = float(fn_estat(valores))
    rng = np.random.default_rng(seed)
    reamostras = np.array([float(fn_estat(rng.choice(valores, size=len(valores), replace=True))) for _ in range(n_reamostragens)])
    lo, hi = np.percentile(reamostras, [2.5, 97.5])
    p_valor = float(np.mean(reamostras <= 0))
    return estat, float(lo), float(hi), p_valor


def simular_carteira_cronologica(apostas_ordenadas: list[dict], banca_inicial: float = 1.0) -> dict:
    """Carteira simulada CRONOLÓGICA (mesmo espírito de 'validado_carteira'
    já usado neste projeto pra escanteios/cartões, ver CONTEXTO_
    PROJETO.md) -- diferente de `backtest_kelly.simular_banca` (ROI por
    aposta, banca FIXA de 1 unidade, não-composto, pensado pra comparar
    grupos), aqui a banca COMPÕE de verdade ao longo do tempo (stake =
    fração de Kelly da banca ATUAL) -- é o teste real de "se eu tivesse
    apostado isso em ordem cronológica, o que teria acontecido com meu
    dinheiro". `apostas_ordenadas` precisa já vir ordenada por
    `match_date` -- esta função não ordena de novo."""
    banca = banca_inicial
    pico = banca_inicial
    drawdown_maximo = 0.0
    n_apostado = 0
    for aposta in apostas_ordenadas:
        stake_fracao = bk.kelly_fracionario(aposta["prob_modelo"], aposta["odd"])
        if stake_fracao <= 0:
            continue
        stake = stake_fracao * banca
        banca += stake * (aposta["odd"] - 1) if aposta["acertou"] else -stake
        banca = max(banca, 0.0)
        n_apostado += 1
        pico = max(pico, banca)
        if pico > 0:
            drawdown_maximo = max(drawdown_maximo, (pico - banca) / pico)
    return {"banca_final_x": banca / banca_inicial, "drawdown_maximo": drawdown_maximo, "n_apostado": n_apostado}


def avaliar_celula(apostas_bucket: list[dict], modelo: str, mercado: str, faixa_odd: tuple[float, float], faixa_edge: tuple[float, float]) -> dict | None:
    rois = np.array(bk.simular_banca(apostas_bucket))
    if len(rois) < MIN_N_CELULA:
        return None
    roi_medio, media_lo, media_hi, p_media = bootstrap_ic95_estatistica(rois, np.mean)
    roi_mediano, mediana_lo, mediana_hi, p_mediana = bootstrap_ic95_estatistica(rois, np.median)
    confiavel = media_lo > 0 and mediana_lo > 0
    dm_stat, dm_p_valor = bk.diebold_mariano_test(rois, np.zeros_like(rois))

    apostas_ordenadas = sorted(apostas_bucket, key=lambda a: a["match_date"])
    carteira = simular_carteira_cronologica(apostas_ordenadas)

    return {
        "modelo": modelo, "mercado": mercado, "odd_min": faixa_odd[0], "odd_max": faixa_odd[1],
        "edge_min": faixa_edge[0], "edge_max": faixa_edge[1], "n": len(rois),
        "roi_medio": roi_medio, "media_ic95": (media_lo, media_hi), "p_media": p_media,
        "roi_mediano": roi_mediano, "mediana_ic95": (mediana_lo, mediana_hi), "p_mediana": p_mediana,
        "confiavel": confiavel,
        "dm_stat": dm_stat, "dm_p_valor": dm_p_valor,
        "p_celula": max(p_media, p_mediana),
        "carteira": carteira,
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


def corrigir_multiplas_comparacoes(resultados: list[dict], alpha: float = 0.05) -> None:
    """Aplica Bonferroni E Benjamini-Hochberg (FDR) sobre `p_celula` de
    cada resultado, em memória (adiciona `bonferroni_significativo`/
    `fdr_significativo` a cada dict). Bonferroni: rejeita só se
    `p <= alpha/m` (controla a taxa de erro familiar, conservador).
    Benjamini-Hochberg: ordena os p-valores, acha o maior k tal que
    `p_(k) <= (k/m)*alpha`, rejeita todos até k (controla a taxa de falsas
    descobertas, menos conservador, mais poder estatístico)."""
    m = len(resultados)
    if m == 0:
        return
    limiar_bonferroni = alpha / m
    ordenados = sorted(resultados, key=lambda r: r["p_celula"])
    maior_k_fdr = 0
    for k, r in enumerate(ordenados, start=1):
        if r["p_celula"] <= (k / m) * alpha:
            maior_k_fdr = k
    for k, r in enumerate(ordenados, start=1):
        r["bonferroni_significativo"] = r["p_celula"] <= limiar_bonferroni
        r["fdr_significativo"] = k <= maior_k_fdr


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
    datas_por_match = dict(zip(dataset["match_id"], dataset["match_date"]))
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
        apostas_total.extend([{**a, "mercado": mercado, "match_date": datas_por_match[a["match_id"]]} for a in apostas])
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
    datas_por_match = dict(zip(dataset["match_id"], dataset["match_date"]))
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
    return [{**a, "mercado": MERCADO_ESCANTEIOS, "match_date": datas_por_match[a["match_id"]]} for a in apostas]


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
        datas_por_match = dict(zip(dataset["match_id"].astype(int), dataset["match_date"]))
        predicoes = {mid: {"prob_over": p, "prob_under": 1 - p} for mid, p in previsoes.items()}
        apostas = bk.montar_apostas(predicoes, odds_reais, resultados_reais, mercado=mercado)
        logger.info("[cartoes_total %.1f]: %d apostas com edge >= %.0f%% e odd real disponível.", linha, len(apostas), bk.EDGE_MINIMO * 100)
        apostas_total.extend([{**a, "mercado": mercado, "match_date": datas_por_match[a["match_id"]]} for a in apostas])
    return apostas_total


def coletar_apostas_catboost_v9(supabase: Client) -> list[dict]:
    """Diferente de gols/escanteios/cartões, `catboost_v9` NÃO precisa de
    walk-forward reconstruído aqui -- as previsões já são out-of-sample de
    verdade (walk-forward CV rodado uma vez em `custom_model_configs`,
    persistido em `model_predictions`). Só lê o que já existe, cruza com o
    resultado real (`matches.home_goals/away_goals`) e a odd real de
    fechamento -- mesmo funil de `montar_apostas` dos outros modelos.

    `market` grava minúsculo ('1x2') nesta família, diferente do resto do
    projeto ('1X2') -- MERCADOS_CATBOOST_V9 normaliza na hora de montar
    resultado/pedir odds."""
    apostas_total = []
    for mercado_bruto, mercado in MERCADOS_CATBOOST_V9.items():
        linhas = dh._paginar(
            lambda inicio, fim, mb=mercado_bruto: supabase.table("model_predictions")
            .select("match_id, selection, probability")
            .eq("model_name", MODELO_CATBOOST_V9)
            .eq("market", mb)
            .range(inicio, fim)
        )
        if not linhas:
            continue
        previsoes: dict[int, dict[str, float]] = {}
        for linha in linhas:
            previsoes.setdefault(linha["match_id"], {})[f"prob_{linha['selection']}"] = linha["probability"]
        match_ids = list(previsoes.keys())

        linhas_matches = dh._paginar_por_lotes_de_id(
            lambda lote, inicio, fim: supabase.table("matches")
            .select("id, home_goals, away_goals, match_date")
            .in_("id", lote)
            .range(inicio, fim),
            match_ids,
        )
        resultados_reais, datas_por_match = {}, {}
        for linha in linhas_matches:
            hg, ag = linha["home_goals"], linha["away_goals"]
            if hg is None or ag is None:
                continue
            resultados_reais[linha["id"]] = bk._resultado_codigo_mercado(hg, ag, mercado)
            datas_por_match[linha["id"]] = linha["match_date"]

        odds_reais = bk.carregar_melhores_odds_fechamento(supabase, match_ids, mercado)
        apostas = bk.montar_apostas(previsoes, odds_reais, resultados_reais, mercado=mercado)
        logger.info("[%s, %s]: %d apostas com edge >= %.0f%% e odd real disponível.",
                     MODELO_CATBOOST_V9, mercado, len(apostas), bk.EDGE_MINIMO * 100)
        apostas_total.extend([{**a, "mercado": mercado, "match_date": datas_por_match[a["match_id"]]} for a in apostas if a["match_id"] in datas_por_match])
    return apostas_total


def persistir_resultados(supabase: Client, resultados: list[dict], data_execucao: str) -> None:
    """Upsert da matriz inteira (não só as células confiáveis, mesma lógica
    de `imprimir_matriz`) em `matriz_confiabilidade_ev_historico` -- é isso
    que permite o frontend acompanhar a evolução dia a dia. Reexecutar o
    workflow no mesmo `data_execucao` sobrescreve a linha (upsert na chave
    única), não duplica."""
    linhas = []
    for r in resultados:
        odd_max = r["odd_max"] if r["odd_max"] != float("inf") else ODD_MAX_SENTINELA
        edge_max = r["edge_max"] if r["edge_max"] != float("inf") else EDGE_MAX_SENTINELA
        c = r["carteira"]
        linhas.append({
            "data_execucao": data_execucao,
            "modelo": r["modelo"], "mercado": r["mercado"],
            "odd_min": r["odd_min"], "odd_max": odd_max,
            "edge_min": r["edge_min"], "edge_max": edge_max,
            "n": r["n"],
            "roi_medio": r["roi_medio"], "roi_medio_ic_inf": r["media_ic95"][0], "roi_medio_ic_sup": r["media_ic95"][1],
            "p_media": r["p_media"],
            "roi_mediano": r["roi_mediano"], "roi_mediano_ic_inf": r["mediana_ic95"][0], "roi_mediano_ic_sup": r["mediana_ic95"][1],
            "p_mediana": r["p_mediana"],
            "confiavel": r["confiavel"],
            "dm_stat": r["dm_stat"], "dm_p_valor": r["dm_p_valor"], "p_celula": r["p_celula"],
            "bonferroni_significativo": r["bonferroni_significativo"], "fdr_significativo": r["fdr_significativo"],
            "carteira_banca_final_x": c["banca_final_x"], "carteira_drawdown_maximo": c["drawdown_maximo"],
            "carteira_n_apostado": c["n_apostado"],
        })
    if not linhas:
        logger.warning("Nenhuma célula com n>=%d -- nada persistido em matriz_confiabilidade_ev_historico.", MIN_N_CELULA)
        return
    supabase.table("matriz_confiabilidade_ev_historico").upsert(
        linhas, on_conflict="data_execucao,modelo,mercado,odd_min,odd_max,edge_min,edge_max"
    ).execute()
    logger.info("Persistidas %d células em matriz_confiabilidade_ev_historico (data_execucao=%s).", len(linhas), data_execucao)


def imprimir_matriz(resultados: list[dict]) -> None:
    resultados_ordenados = sorted(resultados, key=lambda r: r["media_ic95"][0], reverse=True)
    n_confiaveis = sum(1 for r in resultados if r["confiavel"])
    n_bonferroni = sum(1 for r in resultados if r["confiavel"] and r["bonferroni_significativo"])
    n_fdr = sum(1 for r in resultados if r["confiavel"] and r["fdr_significativo"])
    logger.info("=" * 150)
    logger.info("MATRIZ DE CONFIABILIDADE ODD x EDGE (critério rigoroso: IC95%% da média E da mediana > 0, n>=%d)", MIN_N_CELULA)
    logger.info("%d de %d células passam no critério rigoroso -- dessas, %d sobrevivem a Bonferroni e %d a Benjamini-Hochberg (FDR).",
                n_confiaveis, len(resultados), n_bonferroni, n_fdr)
    logger.info("=" * 150)
    for r in resultados_ordenados:
        edge_max_str = f"{r['edge_max']*100:.0f}%" if r["edge_max"] != float("inf") else "inf"
        odd_max_str = f"{r['odd_max']:.2f}" if r["odd_max"] != float("inf") else "inf"
        if r["confiavel"]:
            if r["bonferroni_significativo"]:
                veredito = "CONFIÁVEL + sobrevive Bonferroni"
            elif r["fdr_significativo"]:
                veredito = "CONFIÁVEL + sobrevive FDR (não Bonferroni)"
            else:
                veredito = "CONFIÁVEL mas NÃO sobrevive à correção múltipla"
        else:
            veredito = "não confiável"
        c = r["carteira"]
        logger.info(
            "%s [%s] | odd [%.2f,%s) | edge [%.0f%%,%s) | n=%4d | ROI médio %+7.1f%% IC95%%[%+7.1f%%,%+7.1f%%] p=%.4f | "
            "ROI mediano %+7.1f%% IC95%%[%+7.1f%%,%+7.1f%%] p=%.4f | DM=%+.2f | carteira: banca final %.2fx, drawdown máx %.1f%% (n=%d) | %s",
            r["modelo"], r["mercado"], r["odd_min"], odd_max_str, r["edge_min"] * 100, edge_max_str, r["n"],
            r["roi_medio"] * 100, r["media_ic95"][0] * 100, r["media_ic95"][1] * 100, r["p_media"],
            r["roi_mediano"] * 100, r["mediana_ic95"][0] * 100, r["mediana_ic95"][1] * 100, r["p_mediana"],
            r["dm_stat"], c["banca_final_x"], c["drawdown_maximo"] * 100, c["n_apostado"],
            veredito,
        )
    logger.info("=" * 150)


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

    logger.info("--- %s (auditoria v9) ---", MODELO_CATBOOST_V9)
    apostas_catboost_v9 = coletar_apostas_catboost_v9(supabase)
    for mercado in MERCADOS_CATBOOST_V9.values():
        resultados_total.extend(avaliar_matriz([a for a in apostas_catboost_v9 if a["mercado"] == mercado], MODELO_CATBOOST_V9, mercado))

    corrigir_multiplas_comparacoes(resultados_total)
    data_execucao = pd.Timestamp.now(tz="UTC").date().isoformat()
    persistir_resultados(supabase, resultados_total, data_execucao)
    imprimir_matriz(resultados_total)


if __name__ == "__main__":
    main()
