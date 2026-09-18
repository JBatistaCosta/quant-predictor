#!/usr/bin/env python3
"""Walk-forward incremental (janela expansiva, passo mensal) de
`hibrido_corners_v1` contra a Pinnacle -- mesma metodologia de
`validar_hibrido_walkforward_incremental.py`, mas pro modelo de
escanteios em vez do de gols.

MOTIVAÇÃO ESPECÍFICA DE ESCANTEIOS: ao contrário de ρ (Dixon-Coles) e do
split Beta-Binomial, a dispersão da Binomial Negativa (`corners_disp_r`) é
de longe o parâmetro mais faminto por dado dos 4 estimados na calibração
(medido por bootstrap em dado real, ver `dist.ajustar_dispersao_nb` e
CONTEXTO_PROJETO.md: CV(r) ainda em 445% com n=200, só cai a ~20% a partir
de n≈2.000-3.000). Um passo mensal de walk-forward, sozinho, não chega
nem perto disso -- por isso este script usa `dist.ajustar_dispersao_nb_
bayesiana` (shrinkage bayesiano, PR #585) em vez do estimador direto.

DE ONDE VEM O PRIOR: precisa ser calculado numa fatia que a REGRESSÃO de
λ nunca viu no fit -- nunca do próprio treino (resíduo de treino é
otimisticamente pequeno, inflaria o prior pro lado errado, o de MENOS
dispersão -- mesmo cuidado de `ajustar_parametros_estruturais`). Um
acumulado "de passos anteriores do walk-forward" pareceria natural, mas
não é seguro aqui: a fatia de calibração do passo N vira parte do TREINO
do passo N+1 (janela expansiva), então usá-la como prior do passo N+1
violaria exatamente essa regra. Em vez disso, o prior é um ÚNICO valor
GLOBAL, calculado UMA VEZ antes do walk-forward começar, por validação
cruzada k-fold cronológica dentro do warm-up inicial (`FRACAO_TREINO_
INICIAL`) -- cada fold é previsto por um regressor ajustado nos outros
folds do MESMO warm-up, nunca no fold sendo previsto. Fica fixo por toda
a execução (é um parâmetro de nuisance agregado, não uma previsão por
partida -- um pouco de "olhar pra frente" DENTRO do warm-up, já reservado
antes do início do walk-forward de teste, não vaza pra nenhuma partida
efetivamente avaliada).

ρ não entra aqui (o mercado misto de gols não é avaliado por este
script) -- só `corners_over_under_9.5`, o único mercado de escanteios com
odds reais suficientes (ver CONTEXTO_PROJETO.md: 152-2.795 partidas
dependendo do snapshot).

Não escreve nada no Supabase -- só leitura e relatório em stdout, mesma
categoria de `validar_hibrido_walkforward_incremental.py`.

Uso:
    set SUPABASE_URL=...
    set SUPABASE_KEY=sua_service_role_key (ou anon -- só leitura)
    python scripts/validar_corners_walkforward_incremental.py
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
import modelos_ml as ml

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
logger = logging.getLogger("validar_corners_walkforward_incremental")

MODEL_NAME = "hibrido_corners_v1"
FEATURES = dh.FEATURES_V14_CORNERS_DECAY
ALVO_TOTAL = "total_corners"
PARAMS_CATBOOST = {"depth": 6, "learning_rate": 0.05}  # mesmos defaults de PARAMS_DEFAULT (modelos_ml.py)

MERCADO_AVALIADO = "corners_over_under_9.5"
LINHA_MERCADO = 9.5
LIGAS_MODELO_MISTO_ESCOPO = "treino"

FRACAO_TREINO_INICIAL = 0.60  # mesmo ponto de partida do split de produção
JANELA_CALIBRACAO_MESES = 3   # meses antes do mês de teste, só pra ajustar r/α/β
MIN_TREINO = 500
MIN_CALIB = 200                # piso já usado em produção pro split; ver docstring pra r
N_FOLDS_PRIOR = 5              # k-fold cronológico dentro do warm-up, só pro prior global de r

SNAPSHOTS_AVALIADOS = [("pre_closing", "abertura"), ("closing", "fechamento")]


def obter_env(nome: str) -> str:
    valor = os.environ.get(nome)
    if not valor:
        sys.exit(f"Configure {nome} antes de rodar.")
    return valor


def obter_league_ids_escopo(supabase: Client) -> list[int]:
    linhas = (
        supabase.table("leagues").select("id")
        .eq("modelo_misto_escopo", LIGAS_MODELO_MISTO_ESCOPO)
        .execute().data or []
    )
    return [l["id"] for l in linhas]


def montar_passos_mensais(dataset: pd.DataFrame, corte_inicial: pd.Timestamp) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    datas_teste = dataset.loc[dataset["match_date"] >= corte_inicial, "match_date"]
    tz = datas_teste.dt.tz
    periodos = sorted(datas_teste.dt.to_period("M").unique())
    return [(p.start_time.tz_localize(tz), p.end_time.tz_localize(tz)) for p in periodos]


def calcular_prior_disp_r_global(warmup: pd.DataFrame, features: list[str]) -> float:
    """Prior GLOBAL de α (=1/r) da NB, único e fixo, calculado uma vez por
    k-fold cronológico dentro do warm-up -- ver docstring do módulo pra
    por que não pode vir do acumulado de passos do walk-forward. Devolve
    α (não r) porque é essa a escala aditiva certa pra shrinkage
    (`ajustar_dispersao_nb_bayesiana` já faz a conversão)."""
    warmup = warmup.dropna(subset=[ALVO_TOTAL]).sort_values("match_date").reset_index(drop=True)
    n = len(warmup)
    tamanho_fold = n // N_FOLDS_PRIOR
    numerador_total, denominador_total = 0.0, 0.0

    for k in range(N_FOLDS_PRIOR):
        inicio = k * tamanho_fold
        fim = n if k == N_FOLDS_PRIOR - 1 else (k + 1) * tamanho_fold
        fold_idx = warmup.index[inicio:fim]
        resto_idx = warmup.index.difference(fold_idx)
        if len(fold_idx) < 50 or len(resto_idx) < MIN_TREINO:
            continue

        modelo, _, _ = ml.treinar_catboost_poisson(PARAMS_CATBOOST, warmup.loc[resto_idx], ALVO_TOTAL, features=features)
        lam_fold = ml.prever_catboost_poisson(modelo, None, warmup.loc[fold_idx], features=features)
        numerador, denominador = dist._alpha_dispersao_nb(lam_fold, warmup.loc[fold_idx, ALVO_TOTAL].to_numpy())
        numerador_total += numerador
        denominador_total += denominador

    alpha_prior = numerador_total / denominador_total if denominador_total > 0 else 0.0
    r_prior = float("inf") if alpha_prior <= 0 else 1.0 / alpha_prior
    logger.info("Prior global de dispersão (k-fold no warm-up, n=%d): r=%.2f (α=%.6f)", n, min(r_prior, 1e6), alpha_prior)
    return alpha_prior


def rodar_passo(
    dataset: pd.DataFrame, features: list[str], alpha_prior: float, inicio_teste: pd.Timestamp, fim_teste: pd.Timestamp,
) -> tuple[list[int], np.ndarray, float, float, float] | None:
    """Devolve `(match_ids, lam_teste, disp_r, alpha_split, beta_split)`
    ou `None` quando falta dado suficiente em algum estágio."""
    inicio_calib = inicio_teste - pd.DateOffset(months=JANELA_CALIBRACAO_MESES)

    treino = dataset[dataset["match_date"] < inicio_calib]
    calib = dataset[(dataset["match_date"] >= inicio_calib) & (dataset["match_date"] < inicio_teste)]
    teste = dataset[(dataset["match_date"] >= inicio_teste) & (dataset["match_date"] <= fim_teste)]

    if teste.empty:
        return None

    treino_valido = treino.dropna(subset=[ALVO_TOTAL])
    if len(treino_valido) < MIN_TREINO:
        return None

    modelo, _, _ = ml.treinar_catboost_poisson(PARAMS_CATBOOST, treino_valido, ALVO_TOTAL, features=features)

    lam_calib = ml.prever_catboost_poisson(modelo, None, calib, features=features)
    tem_total = calib[ALVO_TOTAL].notna().to_numpy()
    if tem_total.sum() < MIN_CALIB:
        return None

    # r por shrinkage bayesiano -- local (fatia de calibração deste passo,
    # só alguns meses) puxado pro prior global calculado uma vez no
    # warm-up (ver calcular_prior_disp_r_global).
    disp_r = min(
        dist.ajustar_dispersao_nb_bayesiana_prior_fixo(
            lam_calib[tem_total], calib.loc[tem_total, ALVO_TOTAL].to_numpy(), alpha_prior,
        ),
        1e6,
    )

    # Split Beta-Binomial -- estimador direto (fica útil já com n~200-300,
    # ver CONTEXTO_PROJETO.md), sem shrinkage.
    colunas_split = {"total_corners_home"}
    if not colunas_split <= set(calib.columns):
        return None
    split_ok = tem_total & calib["total_corners_home"].notna().to_numpy()
    if split_ok.sum() < MIN_CALIB:
        return None
    alpha_split, beta_split = dist.ajustar_beta_binomial(
        calib.loc[split_ok, ALVO_TOTAL].to_numpy(), calib.loc[split_ok, "total_corners_home"].to_numpy(),
    )

    lam_teste = ml.prever_catboost_poisson(modelo, None, teste, features=features)
    return teste["match_id"].tolist(), lam_teste, disp_r, alpha_split, beta_split


def gerar_previsoes_walkforward(dataset: pd.DataFrame, features: list[str], alpha_prior: float, passos: list) -> dict[int, dict]:
    previsoes: dict[int, dict] = {}
    passos_validos = 0
    for inicio_teste, fim_teste in passos:
        resultado = rodar_passo(dataset, features, alpha_prior, inicio_teste, fim_teste)
        if resultado is None:
            logger.warning("[%s] passo %s: dado insuficiente, pulando.", MODEL_NAME, inicio_teste.date())
            continue
        match_ids, lam_teste, disp_r, alpha_split, beta_split = resultado
        for i, match_id in enumerate(match_ids):
            previsoes[match_id] = {
                "lam_corners": float(lam_teste[i]), "disp_r": disp_r, "alpha": alpha_split, "beta": beta_split,
            }
        passos_validos += 1
        logger.info("[%s] passo %s -> %s: n_teste=%d, r=%.1f, α=%.2f, β=%.2f",
                     MODEL_NAME, inicio_teste.date(), fim_teste.date(), len(match_ids), disp_r, alpha_split, beta_split)
    logger.info("[%s] walk-forward: %d/%d passos com previsão gerada, %d partidas no total.",
                MODEL_NAME, passos_validos, len(passos), len(previsoes))
    return previsoes


def avaliar_mercado(supabase: Client, dataset: pd.DataFrame, previsoes: dict[int, dict]) -> list[dict]:
    match_ids_com_previsao = list(previsoes.keys())
    resultados_reais = dict(zip(dataset["match_id"], dataset[ALVO_TOTAL]))
    linhas_pareadas = []

    for snapshot, rotulo in SNAPSHOTS_AVALIADOS:
        odds_brutas = bk._carregar_odds_pinnacle_brutas(
            supabase, match_ids_com_previsao, MERCADO_AVALIADO, snapshot=snapshot, com_fallback_fechamento=False
        )
        odds_devig = bk._devigar_odds_por_partida(odds_brutas, MERCADO_AVALIADO)
        match_ids_validos = sorted(set(match_ids_com_previsao) & set(odds_devig))
        if len(match_ids_validos) < 30:
            logger.warning("%s [%s]: só %d partidas com odd Pinnacle -- pulando.",
                            MODEL_NAME, rotulo, len(match_ids_validos))
            continue

        perdas_modelo, perdas_mercado = [], []
        for match_id in match_ids_validos:
            total_real = resultados_reais.get(match_id)
            if pd.isna(total_real):
                continue
            real = "over" if total_real > LINHA_MERCADO else "under"
            p = previsoes[match_id]
            mercados_modelo = dist.mercados_de_escanteios(p["lam_corners"], p["disp_r"], p["alpha"], p["beta"])
            p_modelo = max(mercados_modelo[(MERCADO_AVALIADO, real)], 1e-15)
            p_mercado = max(odds_devig[match_id][f"prob_{real}"], 1e-15)
            perdas_modelo.append(-np.log(p_modelo))
            perdas_mercado.append(-np.log(p_mercado))

        if len(perdas_modelo) < 30:
            continue
        perdas_modelo, perdas_mercado = np.array(perdas_modelo), np.array(perdas_mercado)
        comparacao = bk.comparar_pareado_com_mercado(perdas_modelo, perdas_mercado)
        nome = f"{MODEL_NAME} [{MERCADO_AVALIADO}, {rotulo}]"
        if len(perdas_modelo) < 100:
            nome += " (n pequeno)"
        linhas_pareadas.append({"nome": nome, "comparacao": comparacao})
        logger.info("%s: n=%d | log-loss modelo=%.4f | log-loss mercado=%.4f",
                    nome, len(perdas_modelo), perdas_modelo.mean(), perdas_mercado.mean())
    return linhas_pareadas


def main() -> None:
    supabase = create_client(obter_env("SUPABASE_URL"), obter_env("SUPABASE_KEY"))

    league_ids = obter_league_ids_escopo(supabase)
    logger.info("Montando dataset Feature Stacked (%d ligas em escopo)...", len(league_ids))
    dataset = dh.montar_dataset_ml_empilhado(supabase, league_ids_manual=league_ids)
    dataset["match_date"] = pd.to_datetime(dataset["match_date"], utc=True)
    dataset = dataset.dropna(subset=["match_id"]).sort_values("match_date").reset_index(drop=True)
    logger.info("Dataset montado: %d partidas (%s a %s).",
                len(dataset), dataset["match_date"].min().date(), dataset["match_date"].max().date())

    features = [f for f in FEATURES if f in dataset.columns]
    faltando = set(FEATURES) - set(features)
    if faltando:
        logger.warning("Features ausentes no dataset (ignoradas): %s", sorted(faltando))

    n = len(dataset)
    corte_inicial = dataset["match_date"].iloc[int(n * FRACAO_TREINO_INICIAL)]
    warmup = dataset[dataset["match_date"] < corte_inicial]
    logger.info("Warm-up (fora do walk-forward): %d partidas (até %s).", len(warmup), corte_inicial.date())

    alpha_prior = calcular_prior_disp_r_global(warmup, features)

    passos = montar_passos_mensais(dataset, corte_inicial)
    previsoes = gerar_previsoes_walkforward(dataset, features, alpha_prior, passos)
    if not previsoes:
        logger.error("Nenhuma previsão gerada -- abortando avaliação.")
        return

    linhas_pareadas = avaliar_mercado(supabase, dataset, previsoes)
    bk.imprimir_relatorio_pareado(linhas_pareadas)


if __name__ == "__main__":
    main()
