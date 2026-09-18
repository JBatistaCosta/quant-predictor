#!/usr/bin/env python3
"""Walk-forward incremental (janela expansiva, passo mensal) comparando
`hibrido_gols_xg_v1` (FEATURES_V12_MESMA_LIGA) contra `hibrido_gols_xg_v2_estado`
(FEATURES_V15_QUALIDADE_ESTADO) -- mesmo alvo (xG observado) nos dois, só
muda o feature set.

Motivação (pedido do usuário, 18/09): a avaliação de produção usa um único
holdout de 20% (n≈4.322) medido uma vez. Um walk-forward "de verdade" --
treina até o mês M-1, testa no mês M, repete pra cada mês -- transforma
toda partida do período de teste em observação out-of-sample, multiplicando
o N disponível pra comparar as duas variantes e pra medir edge vs. mercado
com mais poder estatístico.

DESENHO (decisões registradas em CONTEXTO_PROJETO.md, discussão de 17-18/09):
  - Passo MENSAL (não semanal) -- N já sobe bastante em relação ao holdout
    único, sem multiplicar a orquestração por 4-5x.
  - O dataset "Feature Stacked" é montado UMA VEZ (é a parte cara -- ver
    comentário de `.github/workflows/treinar_modelo_hibrido.yml`); cada
    passo do walk-forward só reparticiona esse mesmo dataframe e refita o
    CatBoost (rápido, segundos), nunca reconstrói features.
  - Calibração (ρ do Dixon-Coles) continua numa fatia PRÓPRIA, separada do
    treino -- mesma disciplina de `treinar_modelo_hibrido.ajustar_
    parametros_estruturais`: os JANELA_CALIBRACAO_MESES meses imediatamente
    antes do mês de teste, nunca vistos pelo regressor daquele passo.
  - Escanteios ficam FORA deste script -- a pergunta é sobre λ de GOLS
    (`hibrido_gols_xg_v2_estado` só muda a feature de gols), e nenhum dos
    3 mercados avaliados (1X2/over_under_2.5/btts) precisa de
    `corners_disp_r` -- por isso não usa o shrinkage bayesiano assimétrico
    (`dist.ajustar_dispersao_nb_bayesiana`), que existe pra quando um script
    de escanteios walk-forward for construído.
  - Primeiros 60% cronológicos do dataset ficam de fora do walk-forward
    (viram só o treino inicial do primeiro passo) -- mesmo ponto de partida
    do split de produção, garante um treino inicial substancial antes do
    primeiro mês testado.

Não escreve nada no Supabase -- só leitura e relatório em stdout, mesma
categoria de `validar_blend_hibrido_pinnacle.py`/`avaliar_modelo_misto_vs_
mercado.py`.

Uso:
    set SUPABASE_URL=...
    set SUPABASE_KEY=sua_service_role_key (ou anon -- só leitura)
    python scripts/validar_hibrido_walkforward_incremental.py
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
logger = logging.getLogger("validar_hibrido_walkforward_incremental")

MODELOS: dict[str, list[str]] = {
    "hibrido_gols_xg_v1": dh.FEATURES_V12_MESMA_LIGA,
    "hibrido_gols_xg_v2_estado": dh.FEATURES_V15_QUALIDADE_ESTADO,
}
ALVO_HOME, ALVO_AWAY = "xg_home", "xg_away"
PARAMS_CATBOOST = {"depth": 6, "learning_rate": 0.05}  # mesmos defaults de PARAMS_DEFAULT (modelos_ml.py)

MERCADOS_AVALIADOS = ["1X2", "over_under_2.5", "btts"]
LIGAS_MODELO_MISTO_ESCOPO = "treino"

FRACAO_TREINO_INICIAL = 0.60  # mesmo ponto de partida do split de produção
JANELA_CALIBRACAO_MESES = 3   # meses antes do mês de teste, só pra ajustar ρ
MIN_TREINO = 500              # mínimo de linhas com alvo pra fitar o regressor
MIN_CALIB = 100                # mínimo de partidas válidas pra MLE de ρ não degenerar

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


def montar_passos_mensais(dataset: pd.DataFrame) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """Um passo por mês-calendário, começando logo após o primeiro
    `FRACAO_TREINO_INICIAL` cronológico do dataset. Devolve
    `[(inicio_mes, fim_mes), ...]` em ordem cronológica."""
    dataset = dataset.sort_values("match_date")
    n = len(dataset)
    corte_inicial = dataset["match_date"].iloc[int(n * FRACAO_TREINO_INICIAL)]

    datas_teste = dataset.loc[dataset["match_date"] >= corte_inicial, "match_date"]
    periodos = sorted(datas_teste.dt.to_period("M").unique())
    return [(p.start_time.tz_localize(datas_teste.dt.tz), p.end_time.tz_localize(datas_teste.dt.tz)) for p in periodos]


def rodar_passo(
    dataset: pd.DataFrame, features: list[str], inicio_teste: pd.Timestamp, fim_teste: pd.Timestamp,
) -> tuple[list[int], np.ndarray, np.ndarray, float] | None:
    """Um passo do walk-forward: treina em tudo ANTES da janela de
    calibração, calibra ρ na janela de `JANELA_CALIBRACAO_MESES` meses
    logo antes do mês de teste, aplica no mês de teste. Devolve `None`
    quando não há dado suficiente pra algum dos três estágios."""
    inicio_calib = inicio_teste - pd.DateOffset(months=JANELA_CALIBRACAO_MESES)

    treino = dataset[dataset["match_date"] < inicio_calib]
    calib = dataset[(dataset["match_date"] >= inicio_calib) & (dataset["match_date"] < inicio_teste)]
    teste = dataset[(dataset["match_date"] >= inicio_teste) & (dataset["match_date"] <= fim_teste)]

    if teste.empty:
        return None

    treino_valido = treino.dropna(subset=[ALVO_HOME, ALVO_AWAY])
    if len(treino_valido) < MIN_TREINO:
        return None

    modelo_home, _, _ = ml.treinar_catboost_poisson(PARAMS_CATBOOST, treino_valido, ALVO_HOME, features=features)
    modelo_away, _, _ = ml.treinar_catboost_poisson(PARAMS_CATBOOST, treino_valido, ALVO_AWAY, features=features)

    lam_home_calib = ml.prever_catboost_poisson(modelo_home, None, calib, features=features)
    lam_away_calib = ml.prever_catboost_poisson(modelo_away, None, calib, features=features)
    valido_calib = (calib["home_goals"].notna() & calib["away_goals"].notna()).to_numpy()
    if valido_calib.sum() < MIN_CALIB:
        return None

    rho = dist.ajustar_rho_mle(
        lam_home_calib[valido_calib], lam_away_calib[valido_calib],
        calib.loc[valido_calib, "home_goals"].to_numpy(), calib.loc[valido_calib, "away_goals"].to_numpy(),
    )

    lam_home_teste = ml.prever_catboost_poisson(modelo_home, None, teste, features=features)
    lam_away_teste = ml.prever_catboost_poisson(modelo_away, None, teste, features=features)

    return teste["match_id"].tolist(), lam_home_teste, lam_away_teste, rho


def gerar_previsoes_walkforward(dataset: pd.DataFrame, features: list[str], model_name: str) -> dict[int, dict]:
    passos = montar_passos_mensais(dataset)
    previsoes: dict[int, dict] = {}
    passos_validos = 0
    for inicio_teste, fim_teste in passos:
        resultado = rodar_passo(dataset, features, inicio_teste, fim_teste)
        if resultado is None:
            logger.warning("[%s] passo %s: dado insuficiente, pulando.", model_name, inicio_teste.date())
            continue
        match_ids, lam_home, lam_away, rho = resultado
        for i, match_id in enumerate(match_ids):
            previsoes[match_id] = {"lam_home": float(lam_home[i]), "lam_away": float(lam_away[i]), "rho": rho}
        passos_validos += 1
        logger.info(
            "[%s] passo %s -> %s: n_teste=%d, ρ=%.4f",
            model_name, inicio_teste.date(), fim_teste.date(), len(match_ids), rho,
        )
    logger.info("[%s] walk-forward: %d/%d passos com previsão gerada, %d partidas no total.",
                model_name, passos_validos, len(passos), len(previsoes))
    return previsoes


def _probs_mercados(lam_home: float, lam_away: float, rho: float) -> dict[tuple[str, str], float]:
    matriz = dist.matriz_placares(lam_home, lam_away, rho)
    return dist.mercados_de_gols(matriz)


def _resultado_real_codigo(home_goals: float, away_goals: float, mercado: str) -> str:
    if mercado == "1X2":
        if home_goals > away_goals:
            return "home"
        if home_goals == away_goals:
            return "draw"
        return "away"
    if mercado == "btts":
        return "yes" if (home_goals > 0 and away_goals > 0) else "no"
    return "over" if (home_goals + away_goals) > 2.5 else "under"


def avaliar_mercado(
    supabase: Client, dataset: pd.DataFrame, previsoes: dict[int, dict], mercado: str, model_name: str,
) -> list[dict]:
    match_ids_com_previsao = list(previsoes.keys())
    resultados_reais = dict(zip(dataset["match_id"], zip(dataset["home_goals"], dataset["away_goals"])))
    linhas_pareadas = []
    for snapshot, rotulo in SNAPSHOTS_AVALIADOS:
        odds_brutas = bk._carregar_odds_pinnacle_brutas(
            supabase, match_ids_com_previsao, mercado, snapshot=snapshot, com_fallback_fechamento=False
        )
        odds_devig = bk._devigar_odds_por_partida(odds_brutas, mercado)
        match_ids_validos = sorted(set(match_ids_com_previsao) & set(odds_devig))
        if len(match_ids_validos) < 30:
            logger.warning("%s [%s, %s]: só %d partidas com odd Pinnacle -- pulando.",
                            model_name, mercado, rotulo, len(match_ids_validos))
            continue

        perdas_modelo, perdas_mercado = [], []
        for match_id in match_ids_validos:
            hg, ag = resultados_reais[match_id]
            if pd.isna(hg) or pd.isna(ag):
                continue
            real = _resultado_real_codigo(hg, ag, mercado)
            p = previsoes[match_id]
            mercados_modelo = _probs_mercados(p["lam_home"], p["lam_away"], p["rho"])
            p_modelo = max(mercados_modelo[(mercado, real)], 1e-15)
            # `_devigar_odds_por_partida` sempre grava `prob_<selecao>` --
            # mesmo formato pra todo mercado, sem prefixo especial (ver
            # backtest_kelly._devigar_odds_por_partida/MERCADOS).
            p_mercado = max(odds_devig[match_id][f"prob_{real}"], 1e-15)
            perdas_modelo.append(-np.log(p_modelo))
            perdas_mercado.append(-np.log(p_mercado))

        if len(perdas_modelo) < 30:
            continue
        perdas_modelo, perdas_mercado = np.array(perdas_modelo), np.array(perdas_mercado)
        comparacao = bk.comparar_pareado_com_mercado(perdas_modelo, perdas_mercado)
        nome = f"{model_name} [{mercado}, {rotulo}]"
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
    dataset = dataset.dropna(subset=["match_id"]).reset_index(drop=True)
    logger.info("Dataset montado: %d partidas (%s a %s).",
                len(dataset), dataset["match_date"].min().date(), dataset["match_date"].max().date())

    linhas_pareadas_total = []
    for model_name, features in MODELOS.items():
        features_existentes = [f for f in features if f in dataset.columns]
        faltando = set(features) - set(features_existentes)
        if faltando:
            logger.warning("[%s] features ausentes no dataset (ignoradas): %s", model_name, sorted(faltando))

        previsoes = gerar_previsoes_walkforward(dataset, features_existentes, model_name)
        if not previsoes:
            logger.error("[%s]: nenhuma previsão gerada, pulando avaliação.", model_name)
            continue

        for mercado in MERCADOS_AVALIADOS:
            linhas_pareadas_total.extend(avaliar_mercado(supabase, dataset, previsoes, mercado, model_name))

    bk.imprimir_relatorio_pareado(linhas_pareadas_total)


if __name__ == "__main__":
    main()
