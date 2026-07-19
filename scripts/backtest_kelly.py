#!/usr/bin/env python3
"""Backtest out-of-sample dos 4 modelos do Model Benchmarking (Requisito 5)
+ comparação calibrado-vs-cru, Platt-vs-Isotonic, por liga e contra o
mercado (rodada de otimização).

Não roda no cron diário (`predict.yml`) -- é uma rotina de VALIDAÇÃO,
disparada manualmente (`python scripts/backtest_kelly.py`), no mesmo
espírito de `api/backtest-betting.js` (painel de modelos em produção), só
que em cima do dataset "Feature Stacked" das 5 ligas de elite.

Passo a passo:
  1. Monta o dataset "Feature Stacked" (`dados_historicos.montar_dataset_ml_empilhado`)
     e divide cronologicamente em Train (60%) / Validation (20%) / Test (20%).
  2. Pros 3 modelos de árvore: grid search pequeno (profundidade x
     learning_rate) treinando só no Train e avaliando log-loss só no Val;
     a MESMA configuração vencedora do Train é reaproveitada pra ajustar
     as calibrações Platt E Isotonic (ver `calibracao.py`, as duas ficam
     lado a lado -- nenhuma substitui a outra) comparando a predição do
     modelo-só-Train contra o resultado real do Val -- depois refita a
     configuração vencedora em Train+Val (nunca olha o Test até aqui). O
     dixon_coles_v1 entra como baseline sem tuning (não tem hiperparâmetro
     de árvore), mas com o mesmo esquema de calibração.
  3. No Test Set (out-of-sample de verdade): busca a melhor odd real
     fechada (`odds_market`, snapshot pré-fechamento, exclui a média
     sintética) e simula banca jogo a jogo com Kelly fracionário 25%
     (capado em 25% da banca por aposta, não-composta), pra CRUA e pras 2
     CALIBRADAS de cada modelo -- 12 linhas no relatório principal (4
     modelos x {cru, calibrado_platt, calibrado_isotonic}). Só entra em
     campo quando o edge (prob. modelo - prob. implícita da odd) passa de
     2pp. Uma tabela secundária quebra o resultado CRU por liga (verifica
     se o desempenho é uniforme ou concentrado numa liga só).
  4. Bootstrap (2000 reamostragens) do ROI por aposta pra IC 95% -- só
     conta como "significativo" quando o limite INFERIOR do IC fica acima
     de zero (edge médio isolado não prova vantagem real, é o mesmo
     critério já usado em `api/backtest-betting.js`).
  5. Comparação de QUALIDADE de probabilidade (log-loss + Brier Score, não
     ROI) contra o mercado: odds da Pinnacle (referência padrão de "linha
     eficiente" em análise quantitativa de apostas) devigadas pelo método
     proporcional, nas MESMAS partidas de cada variante de modelo.

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
    -- escolhe a melhor. Ajusta a calibração (Platt E Isotonic, lado a
    lado -- ver `calibracao.py`) comparando a predição do modelo-só-Train
    contra o resultado real do Val (nunca no Train, senão só mediria
    overfitting; nunca no Test, senão deixaria de ser out-of-sample). Por
    fim refita a config vencedora em Train+Val (usa toda informação
    anterior ao Test no modelo final, mas nunca olha o Test)."""
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
    coeficientes_por_metodo = {
        metodo: calibracao.ajustar_calibracao(preds_val, resultados_val, metodo=metodo) for metodo in calibracao.METODOS
    }

    logger.info("%s: melhor config %s (log-loss val=%.4f) -- refitando em Train+Val.", nome_modelo, melhor_params, melhor_log_loss)
    modelo_final, extra_final = treinar(melhor_params, train_mais_val_df)
    return modelo_final, extra_final, melhor_params, coeficientes_por_metodo


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
    Dixon-Coles: força estimada só no Train, calibração (Platt e Isotonic,
    lado a lado) ajustada comparando a previsão (com essa força) contra o
    resultado real do Val, versão final com força estimada em Train+Val
    (pra prever o Test)."""
    forcas_treino = _estimar_forcas_com_decaimento(partidas_treino, partidas_val["match_date"].min())
    preds_val = prever_dixon_coles_backtest(forcas_treino, partidas_val)
    resultados_val = {
        int(p.id): rp._resultado_codigo(p.home_goals, p.away_goals) for p in partidas_val.itertuples()
    }
    coeficientes_por_metodo = {
        metodo: calibracao.ajustar_calibracao(preds_val, resultados_val, metodo=metodo) for metodo in calibracao.METODOS
    }

    forcas_finais = _estimar_forcas_com_decaimento(partidas_treino_val, data_referencia_teste)
    return forcas_finais, coeficientes_por_metodo


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
    liga_por_match_id: dict[int, str] | None = None,
) -> list[dict]:
    """Só entra em campo quando o edge (prob. modelo - prob. implícita da
    odd) passa de `EDGE_MINIMO` -- mesmo filtro de `api/backtest-betting.js`.
    Cada aposta carrega a `liga` do jogo (quando informada) pra permitir
    quebrar o relatório por liga depois (`resumir_por_liga`)."""
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
                    "liga": (liga_por_match_id or {}).get(match_id),
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
            "%-32s | %4d apostas | ROI médio %+7.2f%% | IC95%% [%+7.2f%%, %+7.2f%%] | %s",
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
# Quebra por liga -- verifica se o desempenho é uniforme ou concentrado
# numa liga só (mesma disciplina de não confiar em edge isolado, aplicada
# por liga em vez de por seleção)
# =============================================================================
def resumir_por_liga(nome_modelo: str, apostas: list[dict]) -> list[dict]:
    apostas_por_liga: dict[str, list[dict]] = {}
    for aposta in apostas:
        apostas_por_liga.setdefault(aposta.get("liga") or "desconhecida", []).append(aposta)
    return [resumir_backtest(f"{nome_modelo} / {liga}", apostas_liga, None) for liga, apostas_liga in apostas_por_liga.items()]


def imprimir_relatorio_por_liga(relatorio_por_liga: list[dict]) -> None:
    if not relatorio_por_liga:
        return
    relatorio_ordenado = sorted(relatorio_por_liga, key=lambda r: r["roi_ic95_inferior"], reverse=True)
    logger.info("=" * 86)
    logger.info("BACKTEST POR LIGA (Test Set, predição CRUA) -- desempenho é uniforme ou concentrado numa liga só?")
    logger.info("=" * 86)
    for r in relatorio_ordenado:
        flag = "SIGNIFICATIVO (IC95% > 0)" if r["significativo"] else "sem evidência de edge positivo"
        logger.info(
            "%-40s | %4d apostas | ROI médio %+7.2f%% | IC95%% [%+7.2f%%, %+7.2f%%] | %s",
            r["model_name"],
            r["n_apostas"],
            r["roi_medio"] * 100,
            r["roi_ic95_inferior"] * 100,
            r["roi_ic95_superior"] * 100,
            flag,
        )
    logger.info("=" * 86)


def salvar_relatorio(supabase, relatorio: list[dict]) -> None:
    """Persiste o relatório principal (ROI/Kelly/IC95%/EV+) em
    `model_benchmarking_backtest` -- painel equivalente ao "Backtest de
    apostas simuladas (EV+)" já existente em Estatísticas dos Modelos, mas
    pros modelos do Model Benchmarking. Cada rodada SUBSTITUI o resultado
    anterior por completo (delete-e-regrava, não upsert incremental) -- é
    sempre um resultado fim-a-fim contra o Test Set inteiro."""
    if not relatorio:
        return
    linhas = [
        {
            "model_name": r["model_name"],
            "n_apostas": r["n_apostas"],
            "roi_medio": round(r["roi_medio"], 5),
            "roi_ic95_inferior": round(r["roi_ic95_inferior"], 5),
            "roi_ic95_superior": round(r["roi_ic95_superior"], 5),
            "significativo": r["significativo"],
            "hiperparametros": r["hiperparametros"],
        }
        for r in relatorio
    ]
    supabase.table("model_benchmarking_backtest").delete().neq("model_name", "").execute()
    supabase.table("model_benchmarking_backtest").upsert(linhas, on_conflict="model_name").execute()
    logger.info("Relatório principal salvo em model_benchmarking_backtest (%d linha(s)).", len(linhas))


def salvar_relatorio_por_liga(supabase, relatorio_por_liga: list[dict]) -> None:
    """Persiste a quebra por liga em `model_benchmarking_backtest_liga` --
    `model_name` vem como `"{modelo} / {liga}"` (ver `resumir_por_liga`),
    precisa separar de volta antes de gravar."""
    if not relatorio_por_liga:
        return
    linhas = []
    for r in relatorio_por_liga:
        modelo, _, liga = r["model_name"].partition(" / ")
        linhas.append(
            {
                "model_name": modelo,
                "liga": liga,
                "n_apostas": r["n_apostas"],
                "roi_medio": round(r["roi_medio"], 5),
                "roi_ic95_inferior": round(r["roi_ic95_inferior"], 5),
                "roi_ic95_superior": round(r["roi_ic95_superior"], 5),
                "significativo": r["significativo"],
            }
        )
    supabase.table("model_benchmarking_backtest_liga").delete().neq("model_name", "").execute()
    supabase.table("model_benchmarking_backtest_liga").upsert(linhas, on_conflict="model_name,liga").execute()
    logger.info("Relatório por liga salvo em model_benchmarking_backtest_liga (%d linha(s)).", len(linhas))


# =============================================================================
# Comparação com o mercado -- Pinnacle sem vig (log-loss / Brier Score)
# =============================================================================
def carregar_odds_pinnacle_devigadas(supabase, match_ids: list[int]) -> dict[int, dict[str, float]]:
    """Probabilidade implícita da Pinnacle -- referência padrão de "linha
    eficiente" em análise quantitativa de apostas (menor margem/vig do
    mercado) -- devigada pelo método proporcional:
    `prob_i = (1/odd_i) / soma(1/odd_j)`, que reparte o overround
    igualmente entre as 3 seleções."""

    def factory(lote, inicio, fim):
        return (
            supabase.table("odds_market")
            .select("match_id, selection, odds")
            .in_("match_id", lote)
            .eq("market", "1X2")
            .eq("snapshot", "pre_closing")
            .eq("bookmaker", "pinnacle")
            .order("match_id")
            .range(inicio, fim)
        )

    linhas = dados_historicos._paginar_por_lotes_de_id(factory, match_ids)

    odds_por_partida: dict[int, dict[str, float]] = {}
    for linha in linhas:
        odds_por_partida.setdefault(linha["match_id"], {})[linha["selection"]] = linha["odds"]

    devigadas: dict[int, dict[str, float]] = {}
    for match_id, odds in odds_por_partida.items():
        if not all(odds.get(s) for s in ("home", "draw", "away")):
            continue
        probs_brutas = {s: 1 / odds[s] for s in ("home", "draw", "away")}
        overround = sum(probs_brutas.values())
        devigadas[match_id] = {f"prob_{s}": probs_brutas[s] / overround for s in ("home", "draw", "away")}
    return devigadas


def _metricas_probabilisticas(
    predicoes: dict[int, dict[str, float]], resultados_reais: dict[int, int], match_ids_validos: set[int]
) -> tuple[float, float, int]:
    """Log-loss e Brier Score (multiclasse), restritos aos `match_ids_validos`
    -- pra comparar modelo e mercado exatamente nas MESMAS partidas (só as
    que têm odd da Pinnacle disponível)."""
    codigo_para_campo = {
        dados_historicos.RESULTADO_HOME: "prob_home",
        dados_historicos.RESULTADO_DRAW: "prob_draw",
        dados_historicos.RESULTADO_AWAY: "prob_away",
    }
    perdas_log, briers = [], []
    for match_id in match_ids_validos:
        probs = predicoes.get(match_id)
        resultado_real = resultados_reais.get(match_id)
        if probs is None or resultado_real is None:
            continue
        p_real = max(probs[codigo_para_campo[resultado_real]], 1e-15)
        perdas_log.append(-np.log(p_real))
        briers.append(
            sum((probs[campo] - (1.0 if codigo == resultado_real else 0.0)) ** 2 for codigo, campo in codigo_para_campo.items())
        )
    if not perdas_log:
        return float("nan"), float("nan"), 0
    return float(np.mean(perdas_log)), float(np.mean(briers)), len(perdas_log)


def imprimir_relatorio_qualidade(linhas_qualidade: list[dict]) -> None:
    linhas_ordenadas = sorted(linhas_qualidade, key=lambda r: r["log_loss"])
    logger.info("=" * 86)
    logger.info("QUALIDADE DE PROBABILIDADE NO TEST SET -- modelo vs. mercado (Pinnacle sem vig)")
    logger.info("=" * 86)
    for r in linhas_ordenadas:
        marcador = " <-- mercado" if r["nome"].startswith("mercado") else ""
        logger.info("%-34s | %4d partidas | log-loss %.4f | Brier %.4f%s", r["nome"], r["n"], r["log_loss"], r["brier"], marcador)
    logger.info("=" * 86)


# =============================================================================
# Comparação PAREADA com o mercado -- superioridade / inferioridade /
# não-inferioridade (bootstrap da DIFERENÇA partida a partida, não de cada
# lado isolado -- cancela ruído comum, mesmo espírito do teste de
# Diebold-Mariano pra comparar previsões)
# =============================================================================
MARGEM_NAO_INFERIORIDADE_LOG_LOSS = 0.01  # ajustável -- não há um valor "certo" objetivo sem uma referência de EV
AMOSTRA_MINIMA_CLV = 30  # pares (partida, seleção) mínimos pra confiar na correlação de Closing Line Value


def _perdas_por_partida(
    predicoes: dict[int, dict[str, float]], resultados_reais: dict[int, int], match_ids_ordenados: list[int]
) -> tuple[np.ndarray, np.ndarray]:
    """Log-loss e Brier POR PARTIDA (não a média) -- necessário pro
    bootstrap pareado, que precisa subtrair perda-a-perda na MESMA ordem
    de partida entre modelo e mercado."""
    codigo_para_campo = {
        dados_historicos.RESULTADO_HOME: "prob_home",
        dados_historicos.RESULTADO_DRAW: "prob_draw",
        dados_historicos.RESULTADO_AWAY: "prob_away",
    }
    perdas_log, briers = [], []
    for match_id in match_ids_ordenados:
        probs = predicoes[match_id]
        resultado_real = resultados_reais[match_id]
        p_real = max(probs[codigo_para_campo[resultado_real]], 1e-15)
        perdas_log.append(-np.log(p_real))
        briers.append(
            sum((probs[campo] - (1.0 if codigo == resultado_real else 0.0)) ** 2 for codigo, campo in codigo_para_campo.items())
        )
    return np.array(perdas_log), np.array(briers)


def comparar_pareado_com_mercado(
    perdas_modelo: np.ndarray,
    perdas_mercado: np.ndarray,
    margem: float = MARGEM_NAO_INFERIORIDADE_LOG_LOSS,
    n_reamostragens: int = N_REAMOSTRAGENS_BOOTSTRAP,
    seed: int = SEED,
) -> dict:
    """Bootstrap PAREADO da diferença (perda do modelo - perda do mercado)
    partida a partida -- a MESMA reamostra de partidas é usada nos dois
    lados a cada iteração, então ruído compartilhado (jogos "difíceis" pros
    dois) se cancela, deixando o teste mais sensível que dois IC
    independentes comparados visualmente.

    - `modelo_supera_mercado`: limite SUPERIOR do IC < 0 (modelo
      confiavelmente MELHOR, perda menor).
    - `mercado_supera_modelo`: limite INFERIOR do IC > 0 (mercado
      confiavelmente melhor).
    - `nao_inferior`: limite SUPERIOR do IC < `margem` -- não-inferioridade
      de verdade (ensaio de não-inferioridade: define uma margem de
      tolerância a priori e testa se o pior cenário dentro do IC não
      ultrapassa ela), mais forte que só "não deu diferença significativa".
    """
    diffs = perdas_modelo - perdas_mercado
    rng = np.random.default_rng(seed)
    n = len(diffs)
    diffs_bootstrap = [rng.choice(diffs, size=n, replace=True).mean() for _ in range(n_reamostragens)]
    ic_inferior, ic_superior = np.percentile(diffs_bootstrap, [2.5, 97.5])
    return {
        "n": n,
        "diferenca_media": float(diffs.mean()),
        "ic95_inferior": float(ic_inferior),
        "ic95_superior": float(ic_superior),
        "modelo_supera_mercado": bool(ic_superior < 0),
        "mercado_supera_modelo": bool(ic_inferior > 0),
        "nao_inferior": bool(ic_superior < margem),
    }


def imprimir_relatorio_pareado(linhas_pareadas: list[dict], margem: float = MARGEM_NAO_INFERIORIDADE_LOG_LOSS) -> None:
    if not linhas_pareadas:
        return
    linhas_ordenadas = sorted(linhas_pareadas, key=lambda r: r["comparacao"]["diferenca_media"])
    logger.info("=" * 86)
    logger.info("COMPARAÇÃO PAREADA COM O MERCADO (log-loss, bootstrap) -- margem de não-inferioridade: %.4f", margem)
    logger.info("=" * 86)
    for r in linhas_ordenadas:
        c = r["comparacao"]
        if c["modelo_supera_mercado"]:
            veredito = "MODELO SUPERIOR (IC95% < 0)"
        elif c["mercado_supera_modelo"]:
            veredito = "MERCADO SUPERIOR (IC95% > 0)"
        elif c["nao_inferior"]:
            veredito = "NÃO-INFERIOR (limite sup. do IC < margem)"
        else:
            veredito = "INCONCLUSIVO (IC cruza a margem)"
        logger.info(
            "%-34s | %4d partidas | diferença %+7.4f | IC95%% [%+7.4f, %+7.4f] | %s",
            r["nome"], c["n"], c["diferenca_media"], c["ic95_inferior"], c["ic95_superior"], veredito,
        )
    logger.info("=" * 86)


# =============================================================================
# Closing Line Value -- o edge do modelo (contra a odd de ABERTURA) prevê
# pra que lado a linha se move até o FECHAMENTO? Já testado uma vez neste
# projeto pro Dixon-Coles de produção e deu negativo (ver
# CONTEXTO_PROJETO.md) -- aqui mede de novo pro pipeline novo.
# =============================================================================
def _carregar_pinnacle_devigada_por_snapshot(supabase, match_ids: list[int], snapshot: str) -> dict[int, dict[str, float]]:
    def factory(lote, inicio, fim):
        return (
            supabase.table("odds_market")
            .select("match_id, selection, odds")
            .in_("match_id", lote)
            .eq("market", "1X2")
            .eq("bookmaker", "pinnacle")
            .eq("snapshot", snapshot)
            .order("match_id")
            .range(inicio, fim)
        )

    linhas = dados_historicos._paginar_por_lotes_de_id(factory, match_ids)
    odds_por_partida: dict[int, dict[str, float]] = {}
    for linha in linhas:
        odds_por_partida.setdefault(linha["match_id"], {})[linha["selection"]] = linha["odds"]

    devigadas: dict[int, dict[str, float]] = {}
    for match_id, odds in odds_por_partida.items():
        if not all(odds.get(s) for s in ("home", "draw", "away")):
            continue
        probs_brutas = {s: 1 / odds[s] for s in ("home", "draw", "away")}
        overround = sum(probs_brutas.values())
        devigadas[match_id] = {f"prob_{s}": probs_brutas[s] / overround for s in ("home", "draw", "away")}
    return devigadas


def testar_closing_line_value(
    supabase,
    match_ids: list[int],
    predicoes_por_variante: dict[str, dict[int, dict[str, float]]],
    n_reamostragens: int = N_REAMOSTRAGENS_BOOTSTRAP,
    seed: int = SEED,
) -> dict[str, dict]:
    """Pra cada (partida, seleção): `edge = prob_modelo - prob_abertura` e
    `movimento = prob_fechamento - prob_abertura` (as duas devigadas).
    Correlação positiva e significativa entre os dois sugeriria que o
    modelo "acerta" a direção que a linha vai se mover -- ou seja, apostar
    na odd de abertura, antes do mercado incorporar a mesma informação,
    teria valor esperado positivo. Bootstrap (reamostragem dos pares) pra
    IC 95% da correlação."""
    abertura = _carregar_pinnacle_devigada_por_snapshot(supabase, match_ids, "pre_closing")
    fechamento = _carregar_pinnacle_devigada_por_snapshot(supabase, match_ids, "closing")
    match_ids_com_movimento = sorted(set(abertura) & set(fechamento))
    if not match_ids_com_movimento:
        return {}

    resultados: dict[str, dict] = {}
    for nome_variante, predicoes in predicoes_por_variante.items():
        edges, movimentos = [], []
        for match_id in match_ids_com_movimento:
            probs_modelo = predicoes.get(match_id)
            if probs_modelo is None:
                continue
            for selecao in ("home", "draw", "away"):
                campo = f"prob_{selecao}"
                edges.append(probs_modelo[campo] - abertura[match_id][campo])
                movimentos.append(fechamento[match_id][campo] - abertura[match_id][campo])

        if len(edges) < AMOSTRA_MINIMA_CLV:
            continue
        edges_arr, movimentos_arr = np.array(edges), np.array(movimentos)
        if edges_arr.std() == 0 or movimentos_arr.std() == 0:
            continue
        correlacao = float(np.corrcoef(edges_arr, movimentos_arr)[0, 1])

        rng = np.random.default_rng(seed)
        n = len(edges_arr)
        correlacoes_bootstrap = []
        for _ in range(n_reamostragens):
            idx = rng.integers(0, n, n)
            amostra_e, amostra_m = edges_arr[idx], movimentos_arr[idx]
            if amostra_e.std() == 0 or amostra_m.std() == 0:
                continue
            correlacoes_bootstrap.append(np.corrcoef(amostra_e, amostra_m)[0, 1])
        if not correlacoes_bootstrap:
            continue
        ic_inferior, ic_superior = np.percentile(correlacoes_bootstrap, [2.5, 97.5])
        resultados[nome_variante] = {
            "n_pares": n,
            "correlacao": correlacao,
            "ic95_inferior": float(ic_inferior),
            "ic95_superior": float(ic_superior),
            "significativo": bool(ic_inferior > 0),
        }
    return resultados


def imprimir_relatorio_clv(resultados_clv: dict[str, dict]) -> None:
    if not resultados_clv:
        logger.info("Sem dado suficiente pra testar Closing Line Value (poucos pares partida+seleção com abertura E fechamento da Pinnacle).")
        return
    logger.info("=" * 86)
    logger.info("CLOSING LINE VALUE -- o edge do modelo (vs. odd de ABERTURA) antecipa o movimento da linha até o FECHAMENTO?")
    logger.info("=" * 86)
    for nome, r in sorted(resultados_clv.items(), key=lambda kv: kv[1]["ic95_inferior"], reverse=True):
        flag = "SIGNIFICATIVO (antecipa movimento)" if r["significativo"] else "sem evidência de antecipação"
        logger.info(
            "%-34s | %4d pares | correlação %+.3f | IC95%% [%+.3f, %+.3f] | %s",
            nome, r["n_pares"], r["correlacao"], r["ic95_inferior"], r["ic95_superior"], flag,
        )
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
    liga_por_match_id = dict(zip(test_df["match_id"], test_df["liga"]))

    relatorio: list[dict] = []
    relatorio_por_liga: list[dict] = []
    todas_as_predicoes_teste: dict[str, dict[int, dict[str, float]]] = {}

    def _registrar(nome_variante: str, preds: dict[int, dict[str, float]], melhor_params: dict | None, por_liga: bool) -> None:
        todas_as_predicoes_teste[nome_variante] = preds
        apostas = montar_apostas(preds, odds_teste, resultados_reais, liga_por_match_id)
        relatorio.append(resumir_backtest(nome_variante, apostas, melhor_params))
        if por_liga:
            relatorio_por_liga.extend(resumir_por_liga(nome_variante, apostas))

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

        forcas_finais, coeficientes_por_metodo = tunar_e_calibrar_dixon_coles(
            partidas_treino, partidas_val, partidas_treino_val, partidas_teste["match_date"].min()
        )

        preds_raw = prever_dixon_coles_backtest(forcas_finais, partidas_teste)
        _registrar("dixon_coles_v1", preds_raw, None, por_liga=True)
        for metodo, coef in coeficientes_por_metodo.items():
            preds_calibradas = {mid: calibracao.aplicar_calibracao(p, coef) for mid, p in preds_raw.items()}
            _registrar(f"dixon_coles_v1_calibrado_{metodo}", preds_calibradas, None, por_liga=False)
    except Exception:
        logger.exception("Falha no baseline dixon_coles_v1 -- pulando, os outros modelos continuam.")

    # --- catboost_v1 / xgboost_v1 / lightgbm_v1: tuning + calibração ---
    for nome_modelo in modelos_ml.TREINADORES:
        try:
            logger.info("Tuning + calibração + treino final: %s", nome_modelo)
            modelo, extra, melhor_params, coeficientes_por_metodo = tunar_treinar_e_calibrar(
                nome_modelo, train_df, val_df, train_mais_val_df
            )
            _, prever = modelos_ml.TREINADORES[nome_modelo]

            probs_teste, classes = prever(modelo, extra, test_df)
            preds_raw = modelos_ml.empacotar_predicoes(test_df["match_id"].tolist(), probs_teste, classes)

            _registrar(nome_modelo, preds_raw, melhor_params, por_liga=True)
            for metodo, coef in coeficientes_por_metodo.items():
                preds_calibradas = {mid: calibracao.aplicar_calibracao(p, coef) for mid, p in preds_raw.items()}
                _registrar(f"{nome_modelo}_calibrado_{metodo}", preds_calibradas, melhor_params, por_liga=False)
        except Exception:
            logger.exception("Falha ao rodar %s -- pulando, os outros modelos continuam.", nome_modelo)

    imprimir_relatorio(relatorio)
    imprimir_relatorio_por_liga(relatorio_por_liga)
    salvar_relatorio(supabase, relatorio)
    salvar_relatorio_por_liga(supabase, relatorio_por_liga)

    # --- qualidade de probabilidade vs. mercado (Pinnacle sem vig) ---
    try:
        logger.info("Comparando qualidade de probabilidade com o mercado (Pinnacle sem vig)...")
        pinnacle_devigada = carregar_odds_pinnacle_devigadas(supabase, match_ids_teste)
        match_ids_validos = set(pinnacle_devigada.keys()) & set(resultados_reais.keys())
        if not match_ids_validos:
            logger.warning("Nenhuma odd da Pinnacle encontrada pro Test Set -- pulando comparação de qualidade.")
        else:
            linhas_qualidade = []
            log_loss_mkt, brier_mkt, n_mkt = _metricas_probabilisticas(pinnacle_devigada, resultados_reais, match_ids_validos)
            linhas_qualidade.append({"nome": "mercado (Pinnacle sem vig)", "log_loss": log_loss_mkt, "brier": brier_mkt, "n": n_mkt})
            for nome_variante, preds in todas_as_predicoes_teste.items():
                log_loss_m, brier_m, n_m = _metricas_probabilisticas(preds, resultados_reais, match_ids_validos)
                linhas_qualidade.append({"nome": nome_variante, "log_loss": log_loss_m, "brier": brier_m, "n": n_m})
            imprimir_relatorio_qualidade(linhas_qualidade)

            # --- comparação PAREADA (superioridade / inferioridade / não-inferioridade) ---
            logger.info("Testando superioridade/não-inferioridade PAREADA contra o mercado...")
            match_ids_ordenados = sorted(match_ids_validos)
            perdas_mercado_log, _ = _perdas_por_partida(pinnacle_devigada, resultados_reais, match_ids_ordenados)
            linhas_pareadas = []
            for nome_variante, preds in todas_as_predicoes_teste.items():
                match_ids_com_pred = [mid for mid in match_ids_ordenados if mid in preds]
                if len(match_ids_com_pred) < AMOSTRA_MINIMA_CLV:
                    continue
                perdas_modelo_log, _ = _perdas_por_partida(preds, resultados_reais, match_ids_com_pred)
                perdas_mercado_alinhada, _ = _perdas_por_partida(pinnacle_devigada, resultados_reais, match_ids_com_pred)
                comparacao = comparar_pareado_com_mercado(perdas_modelo_log, perdas_mercado_alinhada)
                linhas_pareadas.append({"nome": nome_variante, "comparacao": comparacao})
            imprimir_relatorio_pareado(linhas_pareadas)
    except Exception:
        logger.exception("Falha ao comparar qualidade de probabilidade com o mercado -- pulando essa seção do relatório.")

    # --- Closing Line Value: o edge (vs. abertura) antecipa o movimento até o fechamento? ---
    try:
        logger.info("Testando Closing Line Value (abertura vs. fechamento da Pinnacle)...")
        resultados_clv = testar_closing_line_value(supabase, match_ids_teste, todas_as_predicoes_teste)
        imprimir_relatorio_clv(resultados_clv)
    except Exception:
        logger.exception("Falha ao testar Closing Line Value -- pulando essa seção do relatório.")


if __name__ == "__main__":
    main()
