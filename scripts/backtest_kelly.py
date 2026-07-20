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
# v2 (parâmetros de jogador, ver dados_historicos.FEATURES_V2) reaproveita
# a MESMA grade da v1 -- só a lista de features muda (modelos_ml.
# FEATURES_POR_MODELO), não faz sentido duplicar a grade de tuning.
GRADE_HIPERPARAMETROS = {
    "catboost_v1": [{"depth": d, "learning_rate": lr} for d, lr in product([4, 6, 8], [0.03, 0.05, 0.1])],
    "xgboost_v1": [{"max_depth": d, "learning_rate": lr} for d, lr in product([3, 4, 6], [0.03, 0.08, 0.15])],
    "lightgbm_v1": [{"num_leaves": nl, "learning_rate": lr} for nl, lr in product([15, 31, 63], [0.05, 0.1, 0.2])],
}
GRADE_HIPERPARAMETROS["catboost_v2"] = GRADE_HIPERPARAMETROS["catboost_v1"]
GRADE_HIPERPARAMETROS["xgboost_v2"] = GRADE_HIPERPARAMETROS["xgboost_v1"]
GRADE_HIPERPARAMETROS["lightgbm_v2"] = GRADE_HIPERPARAMETROS["lightgbm_v1"]

# =============================================================================
# Mercados cobertos por esta análise -- 1X2 (3 seleções) e Over/Under 2.5
# gols (binário, 2 seleções) usam a MESMA infraestrutura de treino/predição
# por baixo (`modelos_ml.TREINADORES`, parametrizada por `coluna_alvo` --
# ver `dados_historicos.montar_dataset_ml_empilhado`, que já traz
# `resultado` E `resultado_over25` no mesmo dataset) e a mesma simulação de
# banca Kelly -- só muda o conjunto de seleções e onde ler o resultado
# real. Escanteios ficou de fora: a OddsPapi não captura esse mercado neste
# projeto (só 1X2 e over_under_2.5 existem em `odds_market`).
# =============================================================================
MERCADOS = {
    "1X2": {
        "coluna_alvo": "resultado",
        "codigo_por_selecao": {"home": dados_historicos.RESULTADO_HOME, "draw": dados_historicos.RESULTADO_DRAW, "away": dados_historicos.RESULTADO_AWAY},
    },
    "over_under_2.5": {
        "coluna_alvo": "resultado_over25",
        "codigo_por_selecao": {"under": dados_historicos.RESULTADO_UNDER25, "over": dados_historicos.RESULTADO_OVER25},
    },
}


def _resultado_codigo_mercado(home_goals: int, away_goals: int, mercado: str) -> int:
    """Mesma ideia de `rp._resultado_codigo`, mas escolhe o espaço de
    códigos certo pro mercado -- usado no caminho do Dixon-Coles, que
    calcula resultado real a partir de `home_goals`/`away_goals` crus (ao
    contrário dos modelos de árvore, que já leem a coluna pronta do
    dataset "Feature Stacked")."""
    if mercado == "1X2":
        return rp._resultado_codigo(home_goals, away_goals)
    return dados_historicos.RESULTADO_OVER25 if (home_goals + away_goals) > 2.5 else dados_historicos.RESULTADO_UNDER25


def _periodo_teste(df: pd.DataFrame) -> tuple[str | None, str | None]:
    """Data mínima/máxima de `match_date` num recorte do Test Set --
    "temporada ou período de testagem" pro relatório (pode ser o Test Set
    inteiro ou só as partidas de uma liga)."""
    if df.empty:
        return None, None
    return df["match_date"].min().date().isoformat(), df["match_date"].max().date().isoformat()


def _log_loss_multiclasse(y_true: np.ndarray, probs: np.ndarray, classes: np.ndarray) -> float:
    indice_da_classe = {int(rotulo): i for i, rotulo in enumerate(np.ravel(classes))}
    eps = 1e-15
    linhas = np.arange(len(y_true))
    colunas = np.array([indice_da_classe[int(y)] for y in y_true])
    p = np.clip(probs[linhas, colunas], eps, 1 - eps)
    return float(-np.mean(np.log(p)))


def tunar_treinar_e_calibrar(
    nome_modelo: str, train_df: pd.DataFrame, val_df: pd.DataFrame, train_mais_val_df: pd.DataFrame, mercado: str = "1X2"
):
    """Treina cada combinação da grade só no Train, mede log-loss só no Val
    -- escolhe a melhor. Ajusta a calibração (Platt E Isotonic, lado a
    lado -- ver `calibracao.py`) comparando a predição do modelo-só-Train
    contra o resultado real do Val (nunca no Train, senão só mediria
    overfitting; nunca no Test, senão deixaria de ser out-of-sample). Por
    fim refita a config vencedora em Train+Val (usa toda informação
    anterior ao Test no modelo final, mas nunca olha o Test). `mercado`
    escolhe a coluna-alvo e o conjunto de seleções (`MERCADOS`) -- 1X2 e
    Over/Under 2.5 usam a mesma infraestrutura de treino, só muda o alvo."""
    coluna_alvo = MERCADOS[mercado]["coluna_alvo"]
    codigo_por_selecao = MERCADOS[mercado]["codigo_por_selecao"]
    treinar, prever = modelos_ml.TREINADORES[nome_modelo]
    features = modelos_ml.FEATURES_POR_MODELO[nome_modelo]
    melhor_params, melhor_log_loss = None, np.inf
    melhor_modelo_val, melhor_extra_val = None, None

    for params in GRADE_HIPERPARAMETROS[nome_modelo]:
        modelo, extra = treinar(params, train_df, coluna_alvo=coluna_alvo, features=features)
        probs_val, classes = prever(modelo, extra, val_df, features=features)
        log_loss = _log_loss_multiclasse(val_df[coluna_alvo].to_numpy(), probs_val, classes)
        logger.info("  %s [%s] params=%s -> log-loss(val)=%.4f", nome_modelo, mercado, params, log_loss)
        if log_loss < melhor_log_loss:
            melhor_log_loss, melhor_params = log_loss, params
            melhor_modelo_val, melhor_extra_val = modelo, extra

    probs_val_melhor, classes_val_melhor = prever(melhor_modelo_val, melhor_extra_val, val_df, features=features)
    preds_val = modelos_ml.empacotar_predicoes(
        val_df["match_id"].tolist(), probs_val_melhor, classes_val_melhor, coluna_alvo=coluna_alvo
    )
    resultados_val = dict(zip(val_df["match_id"], val_df[coluna_alvo]))
    coeficientes_por_metodo = {
        metodo: calibracao.ajustar_calibracao(preds_val, resultados_val, metodo=metodo, codigo_por_selecao=codigo_por_selecao)
        for metodo in calibracao.METODOS
    }

    logger.info(
        "%s [%s]: melhor config %s (log-loss val=%.4f) -- refitando em Train+Val.",
        nome_modelo,
        mercado,
        melhor_params,
        melhor_log_loss,
    )
    modelo_final, extra_final = treinar(melhor_params, train_mais_val_df, coluna_alvo=coluna_alvo, features=features)
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
    partidas_treino: pd.DataFrame,
    partidas_val: pd.DataFrame,
    partidas_treino_val: pd.DataFrame,
    data_referencia_teste,
    mercado: str = "1X2",
):
    """Mesmo esquema Train/Val/Train+Val dos modelos de árvore, adaptado pro
    Dixon-Coles: força estimada só no Train, calibração (Platt e Isotonic,
    lado a lado) ajustada comparando a previsão (com essa força) contra o
    resultado real do Val, versão final com força estimada em Train+Val
    (pra prever o Test). `mercado` escolhe o espaço de resultado/seleções
    (ver `MERCADOS`) -- a força Dixon-Coles em si não muda (é a mesma
    simulação de placares pros dois mercados, ver `rp._prever_probs_dixon_coles`)."""
    codigo_por_selecao = MERCADOS[mercado]["codigo_por_selecao"]
    forcas_treino = _estimar_forcas_com_decaimento(partidas_treino, partidas_val["match_date"].min())
    preds_val = prever_dixon_coles_backtest(forcas_treino, partidas_val)
    resultados_val = {
        int(p.id): _resultado_codigo_mercado(p.home_goals, p.away_goals, mercado) for p in partidas_val.itertuples()
    }
    coeficientes_por_metodo = {
        metodo: calibracao.ajustar_calibracao(preds_val, resultados_val, metodo=metodo, codigo_por_selecao=codigo_por_selecao)
        for metodo in calibracao.METODOS
    }

    forcas_finais = _estimar_forcas_com_decaimento(partidas_treino_val, data_referencia_teste)
    return forcas_finais, coeficientes_por_metodo


# =============================================================================
# Odds reais de fechamento pro Test Set
# =============================================================================
def _nome_mercado_odds(mercado: str) -> str:
    """`market` em `odds_market` usa `"1X2"` ou `"over_under_2.5"` -- mesmos
    literais das chaves de `MERCADOS`, então é uma identidade, mas fica
    isolado aqui pra não espalhar a suposição pelo arquivo todo."""
    return mercado


def carregar_melhores_odds_fechamento(supabase, match_ids: list[int], mercado: str = "1X2") -> dict[int, dict[str, float]]:
    """Melhor odd real (exclui a média sintética `media_mercado`) por
    partida/seleção -- `snapshot='pre_closing'` porque é a cobertura mais
    ampla de odds históricas reais neste banco (achado documentado em
    CONTEXTO_PROJETO.md: `closing` só cobre 1 temporada)."""
    selecoes = list(MERCADOS[mercado]["codigo_por_selecao"].keys())
    campo_por_selecao = {selecao: f"odd_{selecao}" for selecao in selecoes}

    def factory(lote, inicio, fim):
        return (
            supabase.table("odds_market")
            .select("match_id, bookmaker, selection, odds")
            .in_("match_id", lote)
            .eq("market", _nome_mercado_odds(mercado))
            .eq("snapshot", "pre_closing")
            .neq("bookmaker", "media_mercado")
            .order("match_id")
            .range(inicio, fim)
        )

    linhas = dados_historicos._paginar_por_lotes_de_id(factory, match_ids)

    melhor: dict[int, dict[str, float]] = {}
    for linha in linhas:
        campo = campo_por_selecao.get(linha["selection"])
        if not campo:
            continue
        atual = melhor.setdefault(linha["match_id"], {c: 0.0 for c in campo_por_selecao.values()})
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
    mercado: str = "1X2",
) -> list[dict]:
    """Só entra em campo quando o edge (prob. modelo - prob. implícita da
    odd) passa de `EDGE_MINIMO` -- mesmo filtro de `api/backtest-betting.js`.
    Cada aposta carrega a `liga` do jogo (quando informada) pra permitir
    quebrar o relatório por liga depois (`resumir_por_liga`). `mercado`
    escolhe o conjunto de seleções (ver `MERCADOS`) -- mesma mecânica pra
    1X2 (3 seleções) e Over/Under 2.5 (2 seleções)."""
    apostas = []
    for match_id, probs in predicoes.items():
        odds = odds_por_partida.get(match_id)
        resultado_real = resultados_reais.get(match_id)
        if not odds or resultado_real is None:
            continue
        for selecao, codigo_resultado in MERCADOS[mercado]["codigo_por_selecao"].items():
            campo_prob, campo_odd = f"prob_{selecao}", f"odd_{selecao}"
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


def _arredondar_ou_none(valor, casas: int):
    return None if valor is None or (isinstance(valor, float) and np.isnan(valor)) else round(valor, casas)


def salvar_relatorio(supabase, relatorio: list[dict]) -> None:
    """Persiste o relatório principal em `model_benchmarking_backtest` --
    painel equivalente ao "Backtest de apostas simuladas (EV+)" já
    existente em Estatísticas dos Modelos, mas pros modelos do Model
    Benchmarking, agora com qualidade (log-loss/Brier/Acurácia vs.
    Pinnacle sem vig) E os 2 blocos de ROI (fechamento -- melhor odd real
    de qualquer bookmaker -- e abertura -- especificamente Pinnacle),
    por mercado (1X2, over_under_2.5). Cada rodada SUBSTITUI o resultado
    anterior por completo (delete-e-regrava, não upsert incremental) -- é
    sempre um resultado fim-a-fim contra o Test Set inteiro."""
    if not relatorio:
        return
    linhas = [
        {
            "model_name": r["model_name"],
            "mercado": r["mercado"],
            "periodo_inicio": r.get("periodo_inicio"),
            "periodo_fim": r.get("periodo_fim"),
            "n_apostas": r.get("n_apostas"),
            "roi_medio": _arredondar_ou_none(r.get("roi_medio"), 5),
            "roi_ic95_inferior": _arredondar_ou_none(r.get("roi_ic95_inferior"), 5),
            "roi_ic95_superior": _arredondar_ou_none(r.get("roi_ic95_superior"), 5),
            "significativo": r.get("significativo"),
            "n_apostas_abertura": r.get("n_apostas_abertura"),
            "roi_abertura_medio": _arredondar_ou_none(r.get("roi_abertura_medio"), 5),
            "roi_abertura_ic95_inferior": _arredondar_ou_none(r.get("roi_abertura_ic95_inferior"), 5),
            "roi_abertura_ic95_superior": _arredondar_ou_none(r.get("roi_abertura_ic95_superior"), 5),
            "significativo_abertura": r.get("significativo_abertura"),
            "log_loss": _arredondar_ou_none(r.get("log_loss"), 6),
            "brier": _arredondar_ou_none(r.get("brier"), 6),
            "accuracy": _arredondar_ou_none(r.get("accuracy"), 5),
            "n_amostras_qualidade": r.get("n_amostras_qualidade"),
            "hiperparametros": r.get("hiperparametros"),
        }
        for r in relatorio
    ]
    supabase.table("model_benchmarking_backtest").delete().neq("model_name", "").execute()
    supabase.table("model_benchmarking_backtest").upsert(linhas, on_conflict="model_name,mercado").execute()
    logger.info("Relatório principal salvo em model_benchmarking_backtest (%d linha(s)).", len(linhas))


def salvar_relatorio_por_liga(supabase, relatorio_por_liga: list[dict]) -> None:
    """Persiste a quebra por liga em `model_benchmarking_backtest_liga` --
    mesmas colunas do relatório principal (`salvar_relatorio`), mais
    `liga`."""
    if not relatorio_por_liga:
        return
    linhas = [
        {
            "model_name": r["model_name"],
            "liga": r["liga"],
            "mercado": r["mercado"],
            "periodo_inicio": r.get("periodo_inicio"),
            "periodo_fim": r.get("periodo_fim"),
            "n_apostas": r.get("n_apostas"),
            "roi_medio": _arredondar_ou_none(r.get("roi_medio"), 5),
            "roi_ic95_inferior": _arredondar_ou_none(r.get("roi_ic95_inferior"), 5),
            "roi_ic95_superior": _arredondar_ou_none(r.get("roi_ic95_superior"), 5),
            "significativo": r.get("significativo"),
            "n_apostas_abertura": r.get("n_apostas_abertura"),
            "roi_abertura_medio": _arredondar_ou_none(r.get("roi_abertura_medio"), 5),
            "roi_abertura_ic95_inferior": _arredondar_ou_none(r.get("roi_abertura_ic95_inferior"), 5),
            "roi_abertura_ic95_superior": _arredondar_ou_none(r.get("roi_abertura_ic95_superior"), 5),
            "significativo_abertura": r.get("significativo_abertura"),
            "log_loss": _arredondar_ou_none(r.get("log_loss"), 6),
            "brier": _arredondar_ou_none(r.get("brier"), 6),
            "accuracy": _arredondar_ou_none(r.get("accuracy"), 5),
            "n_amostras_qualidade": r.get("n_amostras_qualidade"),
        }
        for r in relatorio_por_liga
    ]
    supabase.table("model_benchmarking_backtest_liga").delete().neq("model_name", "").execute()
    supabase.table("model_benchmarking_backtest_liga").upsert(linhas, on_conflict="model_name,liga,mercado").execute()
    logger.info("Relatório por liga salvo em model_benchmarking_backtest_liga (%d linha(s)).", len(linhas))


# =============================================================================
# Comparação com o mercado -- Pinnacle sem vig (log-loss / Brier Score)
# =============================================================================
def _carregar_odds_pinnacle_brutas(
    supabase, match_ids: list[int], mercado: str = "1X2", snapshot: str = "pre_closing"
) -> dict[int, dict[str, float]]:
    """Odds cruas (não devigadas) da Pinnacle por partida/seleção --
    `snapshot='pre_closing'` é a odd de ABERTURA (cobertura mais ampla
    neste banco, ver CONTEXTO_PROJETO.md); `snapshot='closing'` é a odd de
    FECHAMENTO de verdade (cobertura mais estreita, só 1 temporada) --
    usada pelo teste de Closing Line Value. Base compartilhada por
    `carregar_odds_pinnacle_devigadas` (qualidade) e
    `carregar_odds_pinnacle_abertura_bruta` (ROI vs. abertura)."""
    selecoes = list(MERCADOS[mercado]["codigo_por_selecao"].keys())

    def factory(lote, inicio, fim):
        return (
            supabase.table("odds_market")
            .select("match_id, selection, odds")
            .in_("match_id", lote)
            .eq("market", _nome_mercado_odds(mercado))
            .eq("snapshot", snapshot)
            .eq("bookmaker", "pinnacle")
            .order("match_id")
            .range(inicio, fim)
        )

    linhas = dados_historicos._paginar_por_lotes_de_id(factory, match_ids)
    odds_por_partida: dict[int, dict[str, float]] = {}
    for linha in linhas:
        if linha["selection"] not in selecoes:
            continue
        odds_por_partida.setdefault(linha["match_id"], {})[linha["selection"]] = linha["odds"]
    return odds_por_partida


def _devigar_odds_por_partida(odds_por_partida: dict[int, dict[str, float]], mercado: str = "1X2") -> dict[int, dict[str, float]]:
    """Devigagem proporcional: `prob_i = (1/odd_i) / soma(1/odd_j)`, que
    reparte o overround igualmente entre as seleções do mercado (2 ou 3)."""
    selecoes = list(MERCADOS[mercado]["codigo_por_selecao"].keys())
    devigadas: dict[int, dict[str, float]] = {}
    for match_id, odds in odds_por_partida.items():
        if not all(odds.get(s) for s in selecoes):
            continue
        probs_brutas = {s: 1 / odds[s] for s in selecoes}
        overround = sum(probs_brutas.values())
        devigadas[match_id] = {f"prob_{s}": probs_brutas[s] / overround for s in selecoes}
    return devigadas


def carregar_odds_pinnacle_devigadas(supabase, match_ids: list[int], mercado: str = "1X2") -> dict[int, dict[str, float]]:
    """Probabilidade implícita da Pinnacle -- referência padrão de "linha
    eficiente" em análise quantitativa de apostas (menor margem/vig do
    mercado) -- devigada pelo método proporcional. Usa a odd de ABERTURA
    (`pre_closing`, maior cobertura) já que é a mesma base de comparação do
    teste de ROI vs. abertura (`carregar_odds_pinnacle_abertura_bruta`)."""
    odds_por_partida = _carregar_odds_pinnacle_brutas(supabase, match_ids, mercado, snapshot="pre_closing")
    return _devigar_odds_por_partida(odds_por_partida, mercado)


def carregar_odds_pinnacle_abertura_bruta(supabase, match_ids: list[int], mercado: str = "1X2") -> dict[int, dict[str, float]]:
    """Odds cruas (com vig, NÃO devigadas) de ABERTURA da Pinnacle, no
    formato `odd_{selecao}` esperado por `montar_apostas` -- alimenta o
    teste de EV+/ROI especificamente contra a odd de abertura da Pinnacle
    (distinto do teste de ROI existente, que usa a MELHOR odd real entre
    todos os bookmakers -- ver `carregar_melhores_odds_fechamento`)."""
    odds_por_partida = _carregar_odds_pinnacle_brutas(supabase, match_ids, mercado, snapshot="pre_closing")
    return {match_id: {f"odd_{s}": odd for s, odd in odds.items()} for match_id, odds in odds_por_partida.items()}


def _metricas_probabilisticas(
    predicoes: dict[int, dict[str, float]], resultados_reais: dict[int, int], match_ids_validos: set[int], mercado: str = "1X2"
) -> tuple[float, float, float, int]:
    """Log-loss, Brier Score e Acurácia (multiclasse ou binário conforme
    `mercado`), restritos aos `match_ids_validos` -- pra comparar modelo e
    mercado exatamente nas MESMAS partidas (só as que têm odd da Pinnacle
    disponível). Acurácia = fração em que a seleção de maior probabilidade
    do modelo é a que realmente aconteceu."""
    codigo_para_campo = {codigo: f"prob_{selecao}" for selecao, codigo in MERCADOS[mercado]["codigo_por_selecao"].items()}
    perdas_log, briers, acertos = [], [], []
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
        codigo_previsto = max(codigo_para_campo, key=lambda codigo: probs[codigo_para_campo[codigo]])
        acertos.append(1.0 if codigo_previsto == resultado_real else 0.0)
    if not perdas_log:
        return float("nan"), float("nan"), float("nan"), 0
    return float(np.mean(perdas_log)), float(np.mean(briers)), float(np.mean(acertos)), len(perdas_log)


def imprimir_relatorio_qualidade(linhas_qualidade: list[dict]) -> None:
    linhas_ordenadas = sorted(linhas_qualidade, key=lambda r: r["log_loss"])
    logger.info("=" * 86)
    logger.info("QUALIDADE DE PROBABILIDADE NO TEST SET -- modelo vs. mercado (Pinnacle sem vig)")
    logger.info("=" * 86)
    for r in linhas_ordenadas:
        marcador = " <-- mercado" if r["nome"].startswith("mercado") else ""
        logger.info(
            "%-34s | %4d partidas | log-loss %.4f | Brier %.4f | Acurácia %.1f%%%s",
            r["nome"],
            r["n"],
            r["log_loss"],
            r["brier"],
            r["accuracy"] * 100,
            marcador,
        )
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
    liga_por_match_id = dict(zip(test_df["match_id"], test_df["liga"]))
    periodo_inicio_geral, periodo_fim_geral = _periodo_teste(test_df)
    periodo_por_liga = {liga: _periodo_teste(sub) for liga, sub in test_df.groupby("liga")}

    match_ids_treino = train_df["match_id"].astype(int).tolist()
    match_ids_val = val_df["match_id"].astype(int).tolist()
    match_ids_treino_val = train_mais_val_df["match_id"].astype(int).tolist()
    partidas_treino = dados_historicos.carregar_partidas_por_id(supabase, match_ids_treino)
    partidas_val = dados_historicos.carregar_partidas_por_id(supabase, match_ids_val)
    partidas_treino_val = dados_historicos.carregar_partidas_por_id(supabase, match_ids_treino_val)
    partidas_teste = dados_historicos.carregar_partidas_por_id(supabase, match_ids_teste)

    relatorio: list[dict] = []
    relatorio_por_liga: list[dict] = []
    # diagnósticos extra (comparação pareada + Closing Line Value) ainda só
    # cobrem 1X2 -- guardamos as predições dessa rodada pra rodar essas duas
    # seções depois do loop, sem refazer o tuning.
    predicoes_1x2_diagnostico: dict[str, dict[int, dict[str, float]]] = {}
    resultados_reais_1x2: dict[int, int] = {}
    match_ids_validos_1x2: set[int] = set()

    for mercado in MERCADOS:
        logger.info("=" * 86)
        logger.info("MERCADO: %s", mercado)
        logger.info("=" * 86)

        coluna_alvo = MERCADOS[mercado]["coluna_alvo"]
        selecoes_mercado = tuple(MERCADOS[mercado]["codigo_por_selecao"].keys())

        logger.info("[%s] Buscando odds reais pro Test Set (%d partidas out-of-sample)...", mercado, len(match_ids_teste))
        odds_fechamento = carregar_melhores_odds_fechamento(supabase, match_ids_teste, mercado)
        odds_abertura = carregar_odds_pinnacle_abertura_bruta(supabase, match_ids_teste, mercado)
        pinnacle_devigada = carregar_odds_pinnacle_devigadas(supabase, match_ids_teste, mercado)
        resultados_reais = dict(zip(test_df["match_id"], test_df[coluna_alvo]))
        match_ids_validos_qualidade = set(pinnacle_devigada.keys()) & set(resultados_reais.keys())
        if not match_ids_validos_qualidade:
            logger.warning("[%s] Nenhuma odd da Pinnacle encontrada pro Test Set -- qualidade vs. mercado ficará vazia.", mercado)

        todas_as_predicoes_teste: dict[str, dict[int, dict[str, float]]] = {}

        def _registrar(nome_variante: str, preds: dict[int, dict[str, float]], melhor_params: dict | None, por_liga: bool) -> None:
            todas_as_predicoes_teste[nome_variante] = preds
            apostas_fechamento = montar_apostas(preds, odds_fechamento, resultados_reais, liga_por_match_id, mercado)
            apostas_abertura = montar_apostas(preds, odds_abertura, resultados_reais, liga_por_match_id, mercado)
            resumo_f = resumir_backtest(nome_variante, apostas_fechamento, melhor_params)
            resumo_a = resumir_backtest(nome_variante, apostas_abertura, melhor_params)
            log_loss, brier, accuracy, n_qualidade = _metricas_probabilisticas(
                preds, resultados_reais, match_ids_validos_qualidade, mercado
            )
            relatorio.append(
                {
                    "model_name": nome_variante,
                    "mercado": mercado,
                    "periodo_inicio": periodo_inicio_geral,
                    "periodo_fim": periodo_fim_geral,
                    "hiperparametros": melhor_params,
                    "n_apostas": resumo_f["n_apostas"],
                    "roi_medio": resumo_f["roi_medio"],
                    "roi_ic95_inferior": resumo_f["roi_ic95_inferior"],
                    "roi_ic95_superior": resumo_f["roi_ic95_superior"],
                    "significativo": resumo_f["significativo"],
                    "n_apostas_abertura": resumo_a["n_apostas"],
                    "roi_abertura_medio": resumo_a["roi_medio"],
                    "roi_abertura_ic95_inferior": resumo_a["roi_ic95_inferior"],
                    "roi_abertura_ic95_superior": resumo_a["roi_ic95_superior"],
                    "significativo_abertura": resumo_a["significativo"],
                    "log_loss": log_loss,
                    "brier": brier,
                    "accuracy": accuracy,
                    "n_amostras_qualidade": n_qualidade,
                }
            )
            if not por_liga:
                return
            apostas_f_por_liga: dict[str, list[dict]] = {}
            for a in apostas_fechamento:
                apostas_f_por_liga.setdefault(a.get("liga") or "desconhecida", []).append(a)
            apostas_a_por_liga: dict[str, list[dict]] = {}
            for a in apostas_abertura:
                apostas_a_por_liga.setdefault(a.get("liga") or "desconhecida", []).append(a)
            match_ids_qual_por_liga: dict[str, set[int]] = {}
            for mid in match_ids_validos_qualidade:
                match_ids_qual_por_liga.setdefault(liga_por_match_id.get(mid) or "desconhecida", set()).add(mid)

            for liga in set(apostas_f_por_liga) | set(apostas_a_por_liga) | set(match_ids_qual_por_liga):
                resumo_f_l = resumir_backtest(nome_variante, apostas_f_por_liga.get(liga, []), None)
                resumo_a_l = resumir_backtest(nome_variante, apostas_a_por_liga.get(liga, []), None)
                log_loss_l, brier_l, accuracy_l, n_qualidade_l = _metricas_probabilisticas(
                    preds, resultados_reais, match_ids_qual_por_liga.get(liga, set()), mercado
                )
                periodo_ini_l, periodo_fim_l = periodo_por_liga.get(liga, (None, None))
                relatorio_por_liga.append(
                    {
                        "model_name": nome_variante,
                        "liga": liga,
                        "mercado": mercado,
                        "periodo_inicio": periodo_ini_l,
                        "periodo_fim": periodo_fim_l,
                        "n_apostas": resumo_f_l["n_apostas"],
                        "roi_medio": resumo_f_l["roi_medio"],
                        "roi_ic95_inferior": resumo_f_l["roi_ic95_inferior"],
                        "roi_ic95_superior": resumo_f_l["roi_ic95_superior"],
                        "significativo": resumo_f_l["significativo"],
                        "n_apostas_abertura": resumo_a_l["n_apostas"],
                        "roi_abertura_medio": resumo_a_l["roi_medio"],
                        "roi_abertura_ic95_inferior": resumo_a_l["roi_ic95_inferior"],
                        "roi_abertura_ic95_superior": resumo_a_l["roi_ic95_superior"],
                        "significativo_abertura": resumo_a_l["significativo"],
                        "log_loss": log_loss_l,
                        "brier": brier_l,
                        "accuracy": accuracy_l,
                        "n_amostras_qualidade": n_qualidade_l,
                    }
                )

        # --- dixon_coles_v1: baseline sem tuning, mas com o mesmo esquema de calibração ---
        try:
            logger.info("[%s] Rodando dixon_coles_v1 (força real + calibração ajustada no Val)...", mercado)
            forcas_finais, coeficientes_por_metodo = tunar_e_calibrar_dixon_coles(
                partidas_treino, partidas_val, partidas_treino_val, partidas_teste["match_date"].min(), mercado
            )
            preds_raw = prever_dixon_coles_backtest(forcas_finais, partidas_teste)
            _registrar("dixon_coles_v1", preds_raw, None, por_liga=True)
            for metodo, coef in coeficientes_por_metodo.items():
                preds_calibradas = {
                    mid: calibracao.aplicar_calibracao(p, coef, selecoes=selecoes_mercado) for mid, p in preds_raw.items()
                }
                _registrar(f"dixon_coles_v1_calibrado_{metodo}", preds_calibradas, None, por_liga=False)
        except Exception:
            logger.exception("[%s] Falha no baseline dixon_coles_v1 -- pulando, os outros modelos continuam.", mercado)

        # --- catboost_v1 / xgboost_v1 / lightgbm_v1: tuning + calibração ---
        for nome_modelo in modelos_ml.TREINADORES:
            try:
                logger.info("[%s] Tuning + calibração + treino final: %s", mercado, nome_modelo)
                modelo, extra, melhor_params, coeficientes_por_metodo = tunar_treinar_e_calibrar(
                    nome_modelo, train_df, val_df, train_mais_val_df, mercado
                )
                _, prever = modelos_ml.TREINADORES[nome_modelo]

                probs_teste, classes = prever(modelo, extra, test_df, features=modelos_ml.FEATURES_POR_MODELO[nome_modelo])
                preds_raw = modelos_ml.empacotar_predicoes(
                    test_df["match_id"].tolist(), probs_teste, classes, coluna_alvo=coluna_alvo
                )

                _registrar(nome_modelo, preds_raw, melhor_params, por_liga=True)
                for metodo, coef in coeficientes_por_metodo.items():
                    preds_calibradas = {
                        mid: calibracao.aplicar_calibracao(p, coef, selecoes=selecoes_mercado) for mid, p in preds_raw.items()
                    }
                    _registrar(f"{nome_modelo}_calibrado_{metodo}", preds_calibradas, melhor_params, por_liga=False)
            except Exception:
                logger.exception("[%s] Falha ao rodar %s -- pulando, os outros modelos continuam.", mercado, nome_modelo)

        # --- linha sintética de referência: qualidade da própria Pinnacle (sem ROI) ---
        if match_ids_validos_qualidade:
            log_loss_mkt, brier_mkt, accuracy_mkt, n_mkt = _metricas_probabilisticas(
                pinnacle_devigada, resultados_reais, match_ids_validos_qualidade, mercado
            )
            relatorio.append(
                {
                    "model_name": "mercado_pinnacle_sem_vig",
                    "mercado": mercado,
                    "periodo_inicio": periodo_inicio_geral,
                    "periodo_fim": periodo_fim_geral,
                    "log_loss": log_loss_mkt,
                    "brier": brier_mkt,
                    "accuracy": accuracy_mkt,
                    "n_amostras_qualidade": n_mkt,
                }
            )
            match_ids_qual_por_liga_mkt: dict[str, set[int]] = {}
            for mid in match_ids_validos_qualidade:
                match_ids_qual_por_liga_mkt.setdefault(liga_por_match_id.get(mid) or "desconhecida", set()).add(mid)
            for liga, mids in match_ids_qual_por_liga_mkt.items():
                log_loss_l, brier_l, accuracy_l, n_l = _metricas_probabilisticas(pinnacle_devigada, resultados_reais, mids, mercado)
                periodo_ini_l, periodo_fim_l = periodo_por_liga.get(liga, (None, None))
                relatorio_por_liga.append(
                    {
                        "model_name": "mercado_pinnacle_sem_vig",
                        "liga": liga,
                        "mercado": mercado,
                        "periodo_inicio": periodo_ini_l,
                        "periodo_fim": periodo_fim_l,
                        "log_loss": log_loss_l,
                        "brier": brier_l,
                        "accuracy": accuracy_l,
                        "n_amostras_qualidade": n_l,
                    }
                )

        imprimir_relatorio([r for r in relatorio if r["mercado"] == mercado and "roi_medio" in r])
        imprimir_relatorio_por_liga([r for r in relatorio_por_liga if r["mercado"] == mercado and "roi_medio" in r])
        imprimir_relatorio_qualidade(
            [
                {"nome": f"{r['model_name']} [{mercado}]", "log_loss": r["log_loss"], "brier": r["brier"], "accuracy": r["accuracy"], "n": r["n_amostras_qualidade"]}
                for r in relatorio
                if r["mercado"] == mercado and r.get("log_loss") is not None
            ]
        )

        if mercado == "1X2":
            predicoes_1x2_diagnostico = todas_as_predicoes_teste
            resultados_reais_1x2 = resultados_reais
            match_ids_validos_1x2 = match_ids_validos_qualidade

    salvar_relatorio(supabase, relatorio)
    salvar_relatorio_por_liga(supabase, relatorio_por_liga)

    # --- diagnósticos extra (comparação pareada + Closing Line Value) -- só 1X2 por ora ---
    if match_ids_validos_1x2:
        try:
            logger.info("Testando superioridade/não-inferioridade PAREADA contra o mercado (1X2)...")
            pinnacle_devigada_1x2 = carregar_odds_pinnacle_devigadas(supabase, match_ids_teste, "1X2")
            match_ids_ordenados = sorted(match_ids_validos_1x2)
            linhas_pareadas = []
            for nome_variante, preds in predicoes_1x2_diagnostico.items():
                match_ids_com_pred = [mid for mid in match_ids_ordenados if mid in preds]
                if len(match_ids_com_pred) < AMOSTRA_MINIMA_CLV:
                    continue
                perdas_modelo_log, _ = _perdas_por_partida(preds, resultados_reais_1x2, match_ids_com_pred)
                perdas_mercado_alinhada, _ = _perdas_por_partida(pinnacle_devigada_1x2, resultados_reais_1x2, match_ids_com_pred)
                comparacao = comparar_pareado_com_mercado(perdas_modelo_log, perdas_mercado_alinhada)
                linhas_pareadas.append({"nome": nome_variante, "comparacao": comparacao})
            imprimir_relatorio_pareado(linhas_pareadas)
        except Exception:
            logger.exception("Falha ao comparar qualidade de probabilidade com o mercado -- pulando essa seção do relatório.")

        try:
            logger.info("Testando Closing Line Value (abertura vs. fechamento da Pinnacle, 1X2)...")
            resultados_clv = testar_closing_line_value(supabase, match_ids_teste, predicoes_1x2_diagnostico)
            imprimir_relatorio_clv(resultados_clv)
        except Exception:
            logger.exception("Falha ao testar Closing Line Value -- pulando essa seção do relatório.")


if __name__ == "__main__":
    main()
