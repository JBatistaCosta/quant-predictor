#!/usr/bin/env python3
"""Backtest out-of-sample dos 4 modelos do Model Benchmarking (Requisito 5)
+ comparação calibrado-vs-cru (Item 3 da rodada de otimização).

Não roda no cron diário (`predict.yml`) -- é uma rotina de VALIDAÇÃO,
disparada manualmente (`python scripts/backtest_kelly.py`), no mesmo
espírito de `api/backtest-betting.js` (painel de modelos em produção), só
que em cima do dataset "Feature Stacked" das 5 ligas de elite.

Passo a passo:
  1. Monta o dataset "Feature Stacked" (`dados_historicos.montar_dataset_ml_empilhado`)
     e divide cronologicamente em Train (60%) / Validation (20%) / Test (20%).
  2. Pros 3 modelos de árvore: grid search pequeno (profundidade x
     learning_rate) treinando só no Train e avaliando log-loss só no Val;
     a MESMA configuração vencedora do Train é reaproveitada pra ajustar a
     calibração (Platt, ver `calibracao.py`) comparando a predição do
     modelo-só-Train contra o resultado real do Val -- depois refita a
     configuração vencedora em Train+Val (nunca olha o Test até aqui). O
     dixon_coles_v1 entra como baseline sem tuning (não tem hiperparâmetro
     de árvore), mas com o mesmo esquema de calibração (força estimada só
     no Train, calibração ajustada no Val, versão final estimada em
     Train+Val).
  3. No Test Set (out-of-sample de verdade): busca a melhor odd real
     fechada (`odds_market`, snapshot pré-fechamento, exclui a média
     sintética) e simula banca jogo a jogo com Kelly fracionário 25%
     (capado em 25% da banca por aposta, não-composta), tanto pra predição
     CRUA quanto pra CALIBRADA de cada modelo -- 8 linhas no relatório
     final (4 modelos x {cru, calibrado}). Só entra em campo quando o edge
     (prob. modelo - prob. implícita da odd) passa de 2pp.
  4. Bootstrap (2000 reamostragens) do ROI por aposta pra IC 95% -- só
     conta como "significativo" quando o limite INFERIOR do IC fica acima
     de zero (edge médio isolado não prova vantagem real, é o mesmo
     critério já usado em `api/backtest-betting.js`).

Variáveis de ambiente obrigatórias: SUPABASE_URL, SUPABASE_KEY.
"""

from __future__ import annotations

import logging
from itertools import product

import numpy as np
import pandas as pd

import calibracao
import dados_historicos
import modelos_ml
import rodar_predicoes as rp

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backtest_kelly")

FRACAO_KELLY = 0.25  # quarter-Kelly
TETO_FRACAO_BANCA = 0.25  # nunca aposta mais que 25% da banca numa única partida
EDGE_MINIMO = 0.02  # 2pp -- mesmo padrão de api/backtest-betting.js
N_REAMOSTRAGENS_BOOTSTRAP = 2000
SEED = 42


# =============================================================================
# Grid search pequeno (Val) + refit final (Train+Val) por modelo de árvore
# =============================================================================
GRADE_HIPERPARAMETROS = {
    "catboost_v1": [{"depth": d, "learning_rate": lr} for d, lr in product([4, 6, 8], [0.03, 0.05, 0.1])],
    "xgboost_v1": [{"max_depth": d, "learning_rate": lr} for d, lr in product([3, 4, 6], [0.03, 0.08, 0.15])],
    "lightgbm_v1": [{"num_leaves": nl, "learning_rate": lr} for nl, lr in product([15, 31, 63], [0.05, 0.1, 0.2])],
}


def _log_loss_multiclasse(y_true: np.ndarray, probs: np.ndarray, classes: np.ndarray) -> float:
    indice_da_classe = {int(rotulo): i for i, rotulo in enumerate(np.ravel(classes))}
    eps = 1e-15
    linhas = np.arange(len(y_true))
    colunas = np.array([indice_da_classe[int(y)] for y in y_true])
    p = np.clip(probs[linhas, colunas], eps, 1 - eps)
    return float(-np.mean(np.log(p)))


def tunar_treinar_e_calibrar(nome_modelo: str, train_df: pd.DataFrame, val_df: pd.DataFrame, train_mais_val_df: pd.DataFrame):
    """Treina cada combinação da grade só no Train, mede log-loss só no Val
    -- escolhe a melhor. Ajusta a calibração (Platt) comparando a predição
    do modelo-só-Train contra o resultado real do Val (nunca no Train,
    senão só mediria overfitting; nunca no Test, senão deixaria de ser
    out-of-sample). Por fim refita a config vencedora em Train+Val (usa
    toda informação anterior ao Test no modelo final, mas nunca olha o
    Test)."""
    treinar, prever = modelos_ml.TREINADORES[nome_modelo]
    melhor_params, melhor_log_loss = None, np.inf
    melhor_modelo_val, melhor_extra_val = None, None

    for params in GRADE_HIPERPARAMETROS[nome_modelo]:
        modelo, extra = treinar(params, train_df)
        probs_val, classes = prever(modelo, extra, val_df)
        log_loss = _log_loss_multiclasse(val_df["resultado"].to_numpy(), probs_val, classes)
        logger.info("  %s params=%s -> log-loss(val)=%.4f", nome_modelo, params, log_loss)
        if log_loss < melhor_log_loss:
            melhor_log_loss, melhor_params = log_loss, params
            melhor_modelo_val, melhor_extra_val = modelo, extra

    probs_val_melhor, classes_val_melhor = prever(melhor_modelo_val, melhor_extra_val, val_df)
    preds_val = modelos_ml.empacotar_predicoes(val_df["match_id"].tolist(), probs_val_melhor, classes_val_melhor)
    resultados_val = dict(zip(val_df["match_id"], val_df["resultado"]))
    coeficientes = calibracao.ajustar_calibracao(preds_val, resultados_val)

    logger.info("%s: melhor config %s (log-loss val=%.4f) -- refitando em Train+Val.", nome_modelo, melhor_params, melhor_log_loss)
    modelo_final, extra_final = treinar(melhor_params, train_mais_val_df)
    return modelo_final, extra_final, melhor_params, coeficientes


# =============================================================================
# Baseline dixon_coles_v1 (sem tuning -- não tem profundidade/learning_rate)
# =============================================================================
def _estimar_forcas_com_decaimento(partidas: pd.DataFrame, data_referencia) -> dict[int, dict[str, float]]:
    janela = partidas.copy()
    dias = (data_referencia - janela["match_date"]).dt.total_seconds() / 86400
    janela["peso_decaimento"] = np.exp(-dados_historicos.XI_DECAIMENTO * dias.clip(lower=0))
    return dados_historicos.estimar_forcas_dixon_coles(janela)


def prever_dixon_coles_backtest(
    forcas: dict[int, dict[str, float]], partidas_alvo: pd.DataFrame
) -> dict[int, dict[str, float]]:
    """Aplica `forcas` já estimadas às partidas-alvo (Val ou Test) -- núcleo
    puro reaproveitado de `rodar_predicoes._prever_probs_dixon_coles`."""
    predicoes = {}
    for partida in partidas_alvo.itertuples():
        forca_casa = forcas.get(partida.home_team_id, rp.FORCA_PADRAO)
        forca_fora = forcas.get(partida.away_team_id, rp.FORCA_PADRAO)
        predicoes[partida.id] = rp._prever_probs_dixon_coles(forca_casa, forca_fora)
    return predicoes


def tunar_e_calibrar_dixon_coles(
    partidas_treino: pd.DataFrame, partidas_val: pd.DataFrame, partidas_treino_val: pd.DataFrame, data_referencia_teste
):
    """Mesmo esquema Train/Val/Train+Val dos modelos de árvore, adaptado pro
    Dixon-Coles: força estimada só no Train, calibração ajustada comparando
    a previsão (com essa força) contra o resultado real do Val, versão
    final com força estimada em Train+Val (pra prever o Test)."""
    forcas_treino = _estimar_forcas_com_decaimento(partidas_treino, partidas_val["match_date"].min())
    preds_val = prever_dixon_coles_backtest(forcas_treino, partidas_val)
    resultados_val = {
        int(p.id): rp._resultado_codigo(p.home_goals, p.away_goals) for p in partidas_val.itertuples()
    }
    coeficientes = calibracao.ajustar_calibracao(preds_val, resultados_val)

    forcas_finais = _estimar_forcas_com_decaimento(partidas_treino_val, data_referencia_teste)
    return forcas_finais, coeficientes


# =============================================================================
# Odds reais de fechamento pro Test Set
# =============================================================================
def carregar_melhores_odds_fechamento(supabase, match_ids: list[int]) -> dict[int, dict[str, float]]:
    """Melhor odd real (exclui a média sintética `media_mercado`) por
    partida/seleção -- `snapshot='pre_closing'` porque é a cobertura mais
    ampla de odds históricas reais neste banco (achado documentado em
    CONTEXTO_PROJETO.md: `closing` só cobre 1 temporada)."""

    def factory(lote, inicio, fim):
        return (
            supabase.table("odds_market")
            .select("match_id, bookmaker, selection, odds")
            .in_("match_id", lote)
            .eq("market", "1X2")
            .eq("snapshot", "pre_closing")
            .neq("bookmaker", "media_mercado")
            .order("match_id")
            .range(inicio, fim)
        )

    linhas = dados_historicos._paginar_por_lotes_de_id(factory, match_ids)
    campo_por_selecao = {"home": "odd_home", "draw": "odd_draw", "away": "odd_away"}

    melhor: dict[int, dict[str, float]] = {}
    for linha in linhas:
        campo = campo_por_selecao.get(linha["selection"])
        if not campo:
            continue
        atual = melhor.setdefault(linha["match_id"], {"odd_home": 0.0, "odd_draw": 0.0, "odd_away": 0.0})
        if linha["odds"] and linha["odds"] > atual[campo]:
            atual[campo] = linha["odds"]
    return melhor


# =============================================================================
# Simulação de banca -- Kelly fracionário 25%, não-composta
# =============================================================================
def kelly_fracionario(prob: float, odd: float, fracao: float = FRACAO_KELLY, teto: float = TETO_FRACAO_BANCA) -> float:
    """Fração da banca a apostar: fração de Kelly completo (`f* = (bp - q) / b`,
    b = odd líquida), capada em `teto` -- mesmo padrão de
    `api/backtest-betting.js`."""
    b = odd - 1
    if b <= 0:
        return 0.0
    kelly_completo = (prob * (b + 1) - 1) / b
    if kelly_completo <= 0:
        return 0.0
    return min(kelly_completo * fracao, teto)


def montar_apostas(
    predicoes: dict[int, dict[str, float]],
    odds_por_partida: dict[int, dict[str, float]],
    resultados_reais: dict[int, int],
) -> list[dict]:
    """Só entra em campo quando o edge (prob. modelo - prob. implícita da
    odd) passa de `EDGE_MINIMO` -- mesmo filtro de `api/backtest-betting.js`."""
    apostas = []
    for match_id, probs in predicoes.items():
        odds = odds_por_partida.get(match_id)
        resultado_real = resultados_reais.get(match_id)
        if not odds or resultado_real is None:
            continue
        for selecao, campo_prob, campo_odd, codigo_resultado in (
            ("home", "prob_home", "odd_home", dados_historicos.RESULTADO_HOME),
            ("draw", "prob_draw", "odd_draw", dados_historicos.RESULTADO_DRAW),
            ("away", "prob_away", "odd_away", dados_historicos.RESULTADO_AWAY),
        ):
            odd = odds.get(campo_odd)
            if not odd:
                continue
            prob_modelo = probs[campo_prob]
            edge = prob_modelo - (1 / odd)
            if edge < EDGE_MINIMO:
                continue
            apostas.append(
                {
                    "match_id": match_id,
                    "selecao": selecao,
                    "prob_modelo": prob_modelo,
                    "odd": odd,
                    "acertou": resultado_real == codigo_resultado,
                }
            )
    return apostas


def simular_banca(apostas: list[dict]) -> list[float]:
    """Devolve o ROI de CADA aposta individual (lucro / stake, não o lucro
    bruto) -- não-composto: cada aposta usa fração de uma banca fixa de 1
    unidade, nunca reinveste lucro (mantém apostas comparáveis entre
    modelos/grupos mesmo com stakes de tamanho diferente)."""
    rois = []
    for aposta in apostas:
        stake = kelly_fracionario(aposta["prob_modelo"], aposta["odd"])
        if stake <= 0:
            continue
        lucro = stake * (aposta["odd"] - 1) if aposta["acertou"] else -stake
        rois.append(lucro / stake)
    return rois


def bootstrap_ic95_roi(
    rois: list[float], n_reamostragens: int = N_REAMOSTRAGENS_BOOTSTRAP, seed: int = SEED
) -> tuple[float, float, float]:
    """ROI médio + IC 95% via bootstrap (reamostragem com reposição das
    apostas individuais) -- edge médio isolado não prova vantagem real, o
    que importa é o limite INFERIOR do IC ficar acima de zero (mesmo
    critério de `api/backtest-betting.js`)."""
    if not rois:
        return 0.0, 0.0, 0.0
    rois_arr = np.array(rois)
    roi_medio = float(rois_arr.mean())
    rng = np.random.default_rng(seed)
    rois_bootstrap = [rng.choice(rois_arr, size=len(rois_arr), replace=True).mean() for _ in range(n_reamostragens)]
    limite_inferior, limite_superior = np.percentile(rois_bootstrap, [2.5, 97.5])
    return roi_medio, float(limite_inferior), float(limite_superior)


def resumir_backtest(nome_modelo: str, apostas: list[dict], melhor_params: dict | None) -> dict:
    rois = simular_banca(apostas)
    roi_medio, ic_inferior, ic_superior = bootstrap_ic95_roi(rois)
    return {
        "model_name": nome_modelo,
        "hiperparametros": melhor_params,
        "n_apostas": len(rois),
        "roi_medio": roi_medio,
        "roi_ic95_inferior": ic_inferior,
        "roi_ic95_superior": ic_superior,
        "significativo": ic_inferior > 0,
    }


def imprimir_relatorio(relatorio: list[dict]) -> None:
    relatorio_ordenado = sorted(relatorio, key=lambda r: r["roi_ic95_inferior"], reverse=True)
    logger.info("=" * 86)
    logger.info("BACKTEST OUT-OF-SAMPLE (Test Set) -- Kelly fracionário 25%%, edge mínimo %.0fpp", EDGE_MINIMO * 100)
    logger.info("=" * 86)
    for r in relatorio_ordenado:
        flag = "SIGNIFICATIVO (IC95% > 0)" if r["significativo"] else "sem evidência de edge positivo"
        logger.info(
            "%-24s | %4d apostas | ROI médio %+7.2f%% | IC95%% [%+7.2f%%, %+7.2f%%] | %s",
            r["model_name"],
            r["n_apostas"],
            r["roi_medio"] * 100,
            r["roi_ic95_inferior"] * 100,
            r["roi_ic95_superior"] * 100,
            flag,
        )
        if r["hiperparametros"]:
            logger.info("  hiperparâmetros escolhidos (log-loss no Val): %s", r["hiperparametros"])
    logger.info("=" * 86)


# =============================================================================
# Orquestração
# =============================================================================
def main() -> None:
    supabase = rp.get_supabase_client()

    logger.info("Montando dataset 'Feature Stacked' (5-8 temporadas, ligas de elite)...")
    dataset = dados_historicos.montar_dataset_ml_empilhado(supabase, anos_por_liga=rp.JANELA_ML_ANOS)
    if dataset.empty:
        logger.error("Dataset vazio -- não há como rodar o backtest.")
        return

    train_df, val_df, test_df = dados_historicos.split_cronologico(dataset)
    if val_df.empty or test_df.empty:
        logger.error("Validação ou teste ficaram vazios -- dataset pequeno demais pro split 60/20/20.")
        return
    train_mais_val_df = pd.concat([train_df, val_df], ignore_index=True)

    match_ids_teste = test_df["match_id"].astype(int).tolist()
    logger.info("Buscando odds reais de fechamento pro Test Set (%d partidas out-of-sample)...", len(match_ids_teste))
    odds_teste = carregar_melhores_odds_fechamento(supabase, match_ids_teste)
    resultados_reais = dict(zip(test_df["match_id"], test_df["resultado"]))

    relatorio: list[dict] = []

    # --- dixon_coles_v1: baseline sem tuning, mas com o mesmo esquema de calibração ---
    try:
        logger.info("Rodando dixon_coles_v1 (força real + calibração ajustada no Val)...")
        match_ids_treino = train_df["match_id"].astype(int).tolist()
        match_ids_val = val_df["match_id"].astype(int).tolist()
        match_ids_treino_val = train_mais_val_df["match_id"].astype(int).tolist()

        partidas_treino = dados_historicos.carregar_partidas_por_id(supabase, match_ids_treino)
        partidas_val = dados_historicos.carregar_partidas_por_id(supabase, match_ids_val)
        partidas_treino_val = dados_historicos.carregar_partidas_por_id(supabase, match_ids_treino_val)
        partidas_teste = dados_historicos.carregar_partidas_por_id(supabase, match_ids_teste)

        forcas_finais, coeficientes_dixon = tunar_e_calibrar_dixon_coles(
            partidas_treino, partidas_val, partidas_treino_val, partidas_teste["match_date"].min()
        )

        preds_raw = prever_dixon_coles_backtest(forcas_finais, partidas_teste)
        preds_calibradas = {mid: calibracao.aplicar_calibracao(p, coeficientes_dixon) for mid, p in preds_raw.items()}

        relatorio.append(resumir_backtest("dixon_coles_v1", montar_apostas(preds_raw, odds_teste, resultados_reais), None))
        relatorio.append(
            resumir_backtest("dixon_coles_v1_calibrado", montar_apostas(preds_calibradas, odds_teste, resultados_reais), None)
        )
    except Exception:
        logger.exception("Falha no baseline dixon_coles_v1 -- pulando, os outros modelos continuam.")

    # --- catboost_v1 / xgboost_v1 / lightgbm_v1: tuning + calibração ---
    for nome_modelo in modelos_ml.TREINADORES:
        try:
            logger.info("Tuning + calibração + treino final: %s", nome_modelo)
            modelo, extra, melhor_params, coeficientes = tunar_treinar_e_calibrar(nome_modelo, train_df, val_df, train_mais_val_df)
            _, prever = modelos_ml.TREINADORES[nome_modelo]

            probs_teste, classes = prever(modelo, extra, test_df)
            preds_raw = modelos_ml.empacotar_predicoes(test_df["match_id"].tolist(), probs_teste, classes)
            preds_calibradas = {mid: calibracao.aplicar_calibracao(p, coeficientes) for mid, p in preds_raw.items()}

            relatorio.append(resumir_backtest(nome_modelo, montar_apostas(preds_raw, odds_teste, resultados_reais), melhor_params))
            relatorio.append(
                resumir_backtest(
                    f"{nome_modelo}_calibrado", montar_apostas(preds_calibradas, odds_teste, resultados_reais), melhor_params
                )
            )
        except Exception:
            logger.exception("Falha ao rodar %s -- pulando, os outros modelos continuam.", nome_modelo)

    imprimir_relatorio(relatorio)


if __name__ == "__main__":
    main()
