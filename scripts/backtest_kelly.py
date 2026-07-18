#!/usr/bin/env python3
"""Backtest out-of-sample dos 4 modelos do Model Benchmarking (Requisito 5).

Não roda no cron diário (`predict.yml`) -- é uma rotina de VALIDAÇÃO,
disparada manualmente (`python scripts/backtest_kelly.py`), no mesmo
espírito de `api/backtest-betting.js` (painel de modelos em produção), só
que em cima do dataset "Feature Stacked" das 5 ligas de elite.

Passo a passo:
  1. Monta o dataset "Feature Stacked" (`dados_historicos.montar_dataset_ml_empilhado`)
     e divide cronologicamente em Train (60%) / Validation (20%) / Test (20%).
  2. Pros 3 modelos de árvore: grid search pequeno (profundidade x
     learning_rate) treinando só no Train e avaliando log-loss só no Val;
     refita a melhor configuração em Train+Val (nunca olha o Test até aqui).
     O dixon_coles_v1 entra como baseline sem tuning (não tem hiperparâmetro
     de árvore) -- força de ataque/defesa estimada só com Train+Val.
  3. No Test Set (out-of-sample de verdade): busca a melhor odd real
     fechada (`odds_market`, snapshot pré-fechamento, exclui a média
     sintética) e simula banca jogo a jogo com Kelly fracionário 25%
     (capado em 25% da banca por aposta, não-composta). Só entra em campo
     quando o edge (prob. modelo - prob. implícita da odd) passa de 2pp.
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
from catboost import CatBoostClassifier
from lightgbm import LGBMClassifier
from scipy.stats import poisson
from xgboost import XGBClassifier

import dados_historicos
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


def _treinar_catboost(params: dict, train_df: pd.DataFrame):
    modelo = CatBoostClassifier(
        loss_function="MultiClass",
        thread_count=2,
        iterations=200,
        cat_features=rp.CAT_FEATURES,
        random_seed=SEED,
        verbose=False,
        **params,
    )
    treino = rp._preparar_liga_para_catboost(train_df)
    modelo.fit(treino[rp.FEATURES], treino["resultado"])
    return modelo, None


def _prever_catboost(modelo, _extra, df: pd.DataFrame):
    df = rp._preparar_liga_para_catboost(df)
    return modelo.predict_proba(df[rp.FEATURES]), modelo.classes_


def _treinar_xgboost(params: dict, train_df: pd.DataFrame):
    treino_encoded = pd.get_dummies(train_df[rp.FEATURES], columns=rp.CAT_FEATURES)
    modelo = XGBClassifier(
        objective="multi:softprob",
        num_class=3,
        n_estimators=200,
        eval_metric="mlogloss",
        random_state=SEED,
        **params,
    )
    modelo.fit(treino_encoded, train_df["resultado"])
    return modelo, treino_encoded.columns


def _prever_xgboost(modelo, colunas_treino, df: pd.DataFrame):
    encoded = pd.get_dummies(df[rp.FEATURES], columns=rp.CAT_FEATURES).reindex(columns=colunas_treino, fill_value=0)
    return modelo.predict_proba(encoded), modelo.classes_


def _treinar_lightgbm(params: dict, train_df: pd.DataFrame):
    treino = train_df.copy()
    categorias_liga = sorted(treino["liga"].dropna().unique())
    treino["liga"] = pd.Categorical(treino["liga"], categories=categorias_liga)
    modelo = LGBMClassifier(
        objective="multiclass",
        num_class=3,
        n_estimators=150,
        min_child_samples=10,
        random_state=SEED,
        verbosity=-1,
        **params,
    )
    modelo.fit(treino[rp.FEATURES], treino["resultado"], categorical_feature=rp.CAT_FEATURES)
    return modelo, categorias_liga


def _prever_lightgbm(modelo, categorias_liga, df: pd.DataFrame):
    df = df.copy()
    df["liga"] = rp._alinhar_categoria_liga(df["liga"], categorias_liga)
    return modelo.predict_proba(df[rp.FEATURES]), modelo.classes_


# treinar(params, train_df) -> (modelo, extra) | prever(modelo, extra, df) -> (probs, classes)
TREINADORES = {
    "catboost_v1": (_treinar_catboost, _prever_catboost),
    "xgboost_v1": (_treinar_xgboost, _prever_xgboost),
    "lightgbm_v1": (_treinar_lightgbm, _prever_lightgbm),
}


def tunar_e_treinar(nome_modelo: str, train_df: pd.DataFrame, val_df: pd.DataFrame, train_mais_val_df: pd.DataFrame):
    """Treina cada combinação da grade só no Train, mede log-loss só no Val
    -- escolhe a melhor e refita em Train+Val (usa toda informação anterior
    ao Test no modelo final, mas nunca olha o Test)."""
    treinar, prever = TREINADORES[nome_modelo]
    melhor_params, melhor_log_loss = None, np.inf

    for params in GRADE_HIPERPARAMETROS[nome_modelo]:
        modelo, extra = treinar(params, train_df)
        probs_val, classes = prever(modelo, extra, val_df)
        log_loss = _log_loss_multiclasse(val_df["resultado"].to_numpy(), probs_val, classes)
        logger.info("  %s params=%s -> log-loss(val)=%.4f", nome_modelo, params, log_loss)
        if log_loss < melhor_log_loss:
            melhor_log_loss, melhor_params = log_loss, params

    logger.info("%s: melhor config %s (log-loss val=%.4f) -- refitando em Train+Val.", nome_modelo, melhor_params, melhor_log_loss)
    modelo_final, extra_final = treinar(melhor_params, train_mais_val_df)
    return modelo_final, extra_final, melhor_params, melhor_log_loss


# =============================================================================
# Baseline dixon_coles_v1 (sem tuning -- não tem profundidade/learning_rate)
# =============================================================================
def prever_dixon_coles_backtest(
    partidas_treino_val: pd.DataFrame, test_df: pd.DataFrame, times_por_match_id: dict[int, dict]
) -> dict[int, dict[str, float]]:
    """Força de ataque/defesa estimada só com Train+Val (nunca olha o
    Test), decaimento temporal calculado como se "hoje" fosse a data do
    primeiro jogo do Test -- situação real de quem só teria o dado de
    Train+Val disponível até aquele momento."""
    referencia = test_df["match_date"].min()
    janela = partidas_treino_val.copy()
    dias = (referencia - janela["match_date"]).dt.total_seconds() / 86400
    janela["peso_decaimento"] = np.exp(-dados_historicos.XI_DECAIMENTO * dias.clip(lower=0))
    forcas = dados_historicos.estimar_forcas_dixon_coles(janela)

    predicoes = {}
    for partida in test_df.itertuples():
        times = times_por_match_id.get(partida.match_id)
        if not times:
            continue
        forca_casa = forcas.get(times["home_team_id"], rp.FORCA_PADRAO)
        forca_fora = forcas.get(times["away_team_id"], rp.FORCA_PADRAO)
        lambda_casa = forca_casa["ataque"] * forca_fora["defesa"] * rp.MANDO_CASA
        lambda_fora = forca_fora["ataque"] * forca_casa["defesa"]

        prob_home = prob_draw = prob_away = 0.0
        for gc in range(rp.MAX_GOLS_SIMULADOS):
            for gf in range(rp.MAX_GOLS_SIMULADOS):
                p = (
                    poisson.pmf(gc, lambda_casa)
                    * poisson.pmf(gf, lambda_fora)
                    * rp.tau_dixon_coles(gc, gf, lambda_casa, lambda_fora, rp.RHO_DIXON_COLES)
                )
                if gc > gf:
                    prob_home += p
                elif gc == gf:
                    prob_draw += p
                else:
                    prob_away += p

        total = prob_home + prob_draw + prob_away
        predicoes[partida.match_id] = {
            "prob_home": prob_home / total,
            "prob_draw": prob_draw / total,
            "prob_away": prob_away / total,
        }
    return predicoes


# =============================================================================
# Odds reais de fechamento pro Test Set
# =============================================================================
def carregar_melhores_odds_fechamento(supabase, match_ids: list[int]) -> dict[int, dict[str, float]]:
    """Melhor odd real (exclui a média sintética `media_mercado`) por
    partida/seleção -- `snapshot='pre_closing'` porque é a cobertura mais
    ampla de odds históricas reais neste banco (achado documentado em
    CONTEXTO_PROJETO.md: `closing` só cobre 1 temporada)."""

    def factory(inicio, fim):
        return (
            supabase.table("odds_market")
            .select("match_id, bookmaker, selection, odds")
            .in_("match_id", match_ids)
            .eq("market", "1X2")
            .eq("snapshot", "pre_closing")
            .neq("bookmaker", "media_mercado")
            .order("match_id")
            .range(inicio, fim)
        )

    linhas = dados_historicos._paginar(factory)
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
            ("home", "prob_home", "odd_home", rp.RESULTADO_HOME),
            ("draw", "prob_draw", "odd_draw", rp.RESULTADO_DRAW),
            ("away", "prob_away", "odd_away", rp.RESULTADO_AWAY),
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
    logger.info("=" * 78)
    logger.info("BACKTEST OUT-OF-SAMPLE (Test Set) -- Kelly fracionário 25%%, edge mínimo %.0fpp", EDGE_MINIMO * 100)
    logger.info("=" * 78)
    for r in relatorio_ordenado:
        flag = "SIGNIFICATIVO (IC95% > 0)" if r["significativo"] else "ruído / sem evidência"
        logger.info(
            "%-16s | %4d apostas | ROI médio %+7.2f%% | IC95%% [%+7.2f%%, %+7.2f%%] | %s",
            r["model_name"],
            r["n_apostas"],
            r["roi_medio"] * 100,
            r["roi_ic95_inferior"] * 100,
            r["roi_ic95_superior"] * 100,
            flag,
        )
        if r["hiperparametros"]:
            logger.info("  hiperparâmetros escolhidos (log-loss no Val): %s", r["hiperparametros"])
    logger.info("=" * 78)


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

    try:
        logger.info("Rodando baseline dixon_coles_v1 no Test Set (sem tuning, só Train+Val)...")
        match_ids_treino_val = train_mais_val_df["match_id"].astype(int).tolist()
        partidas_treino_val = dados_historicos.carregar_partidas_por_id(supabase, match_ids_treino_val)
        partidas_teste = dados_historicos.carregar_partidas_por_id(supabase, match_ids_teste)
        times_por_match_id = partidas_teste.set_index("id")[["home_team_id", "away_team_id"]].to_dict("index")

        preds_dixon = prever_dixon_coles_backtest(partidas_treino_val, test_df, times_por_match_id)
        apostas_dixon = montar_apostas(preds_dixon, odds_teste, resultados_reais)
        relatorio.append(resumir_backtest("dixon_coles_v1", apostas_dixon, None))
    except Exception:
        logger.exception("Falha no baseline dixon_coles_v1 -- pulando, os outros modelos continuam.")

    for nome_modelo in TREINADORES:
        try:
            logger.info("Tuning + treino final: %s", nome_modelo)
            modelo, extra, melhor_params, _ = tunar_e_treinar(nome_modelo, train_df, val_df, train_mais_val_df)
            _, prever = TREINADORES[nome_modelo]
            probs_teste, classes = prever(modelo, extra, test_df)
            preds = rp._empacotar_predicoes(test_df["match_id"].tolist(), probs_teste, classes)
            apostas = montar_apostas(preds, odds_teste, resultados_reais)
            relatorio.append(resumir_backtest(nome_modelo, apostas, melhor_params))
        except Exception:
            logger.exception("Falha ao rodar %s -- pulando, os outros modelos continuam.", nome_modelo)

    imprimir_relatorio(relatorio)


if __name__ == "__main__":
    main()
