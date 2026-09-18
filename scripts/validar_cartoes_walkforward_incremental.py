#!/usr/bin/env python3
"""Walk-forward incremental (janela expansiva, passo mensal) do
classificador de Cartões (total da partida, 6 linhas) contra o MERCADO
REAL (Pinnacle, abertura e fechamento) -- não contra a climatologia.

MOTIVAÇÃO: `validar_classificador_walkforward_incremental.py` (18/09) já
mostrou que Cartões e Faltas têm capacidade preditiva real contra a
climatologia -- mas isso só responde "o modelo aprende algo real?", não
"dá pra ganhar dinheiro apostando?". Faltas nunca teve mercado real
(`odds_market` não tem nenhuma linha "foul"/"falta", confirmado via SQL) e
continua sem -- mas Cartões TEM: o mercado real chama de "bookings", não
"cards"/"cartões" (`odds_market.market = 'bookings_over_under_full_time_
{linha}'`, com Pinnacle/bet365/Betano cobrindo, confirmado via SQL em
18/09 com 20.178/15.019/2.732 linhas respectivamente). Esse mercado real
nunca tinha sido comparado por um walk-forward de verdade -- as tabelas de
log-loss/Brier de Cartões já documentadas em CONTEXTO_PROJETO.md
("Comparação com mercado -- Cartões geral") vieram de scripts avulsos
(`sim_reteste_pendentes.py` e afins) num holdout único, não deste
walk-forward mensal com bootstrap pareado IC95%.

PRÉ-REQUISITO CORRIGIDO NESTA MESMA FRENTE: `backtest_kelly.MERCADOS`
nunca tinha entradas para `"cartoes_over_under_{linha}"` -- `MODELOS_
CUSTOM_CARTOES`/`avaliar_modelo_persistido_vs_mercado` referenciam essas
chaves desde os PRs #462/#463, mas qualquer chamada a
`_carregar_odds_pinnacle_brutas`/`_devigar_odds_por_partida` com esse
mercado batia `KeyError` -- nunca executado em produção (ver CONTEXTO_
PROJETO.md, "Implementação no sistema -- tecnicamente pronta, mas não
acionada de propósito"). Adicionadas agora (mesmo padrão do loop já
existente pra escanteios).

DESENHO: mesma máquina dos outros 3 walk-forwards incrementais (janela
expansiva, passo mensal, dataset "Feature Stacked" montado uma vez) --
combina o treino/teste de Random Forest de `validar_classificador_
walkforward_incremental.py` (sem fatia de calibração, classificador não
tem parâmetro estrutural) com a comparação por snapshot de mercado
(abertura/fechamento, devig Odds Ratio) de `validar_hibrido_walkforward_
incremental.py`. Faltas fica FORA deste script (sem mercado, já coberto
pelo script vs. climatologia).

Não escreve nada no Supabase -- só leitura e relatório em stdout, mesma
categoria dos demais scripts `validar_*_walkforward_incremental.py`.

Uso:
    set SUPABASE_URL=...
    set SUPABASE_KEY=sua_service_role_key (ou anon -- só leitura)
    python scripts/validar_cartoes_walkforward_incremental.py
"""

from __future__ import annotations

import logging
import os
import sys

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from supabase import Client, create_client

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_kelly as bk
import dados_historicos as dh

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
logger = logging.getLogger("validar_cartoes_walkforward_incremental")

# Mesmos hiperparâmetros de `treinar_modelo_custom.treinar_via_sklearn`
# pro ramo "random_forest" (defaults quando `hyperparameters` é None, que
# é como as 6 configs reais de Cartões total estão cadastradas hoje).
RF_PARAMS = {"n_estimators": 200, "max_depth": None, "min_samples_leaf": 5, "random_state": 42, "n_jobs": -1}

FEATURES_CARTOES = [
    "media_cartoes_amarelos_fm_5j_home", "media_cartoes_amarelos_fm_sofrido_5j_home",
    "media_cartoes_amarelos_fm_5j_away", "media_cartoes_amarelos_fm_sofrido_5j_away",
    "media_cartoes_vermelhos_fm_5j_home", "media_cartoes_vermelhos_fm_5j_away",
    "media_faltas_fm_5j_home", "media_faltas_fm_sofrido_5j_home",
    "media_faltas_fm_5j_away", "media_faltas_fm_sofrido_5j_away",
    "arbitro_cartoes_media", "arbitro_faltas_media", "arbitro_n_jogos",
]

LIGAS_MODELO_MISTO_ESCOPO = "treino"
FRACAO_TREINO_INICIAL = 0.60
MIN_TREINO = 500
MIN_TESTE_PASSO = 20
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


def treinar_random_forest(train_df: pd.DataFrame, features: list[str], coluna_alvo: str):
    pipe = Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler", StandardScaler()),
        ("clf", RandomForestClassifier(**RF_PARAMS)),
    ])
    pipe.fit(train_df[features], train_df[coluna_alvo])
    return pipe


def rodar_passo(
    dataset: pd.DataFrame, features: list[str], coluna_alvo: str, inicio_teste: pd.Timestamp, fim_teste: pd.Timestamp,
) -> dict[int, float] | None:
    """Devolve `{match_id: p_over}` do mês de teste, ou `None` quando falta
    dado suficiente. Sem fatia de calibração -- mesmo motivo de
    `validar_classificador_walkforward_incremental.py` (classificador não
    tem parâmetro estrutural pra calibrar depois do fit)."""
    treino = dataset[dataset["match_date"] < inicio_teste]
    teste = dataset[(dataset["match_date"] >= inicio_teste) & (dataset["match_date"] <= fim_teste)]

    treino_valido = treino.dropna(subset=[coluna_alvo, *features])
    teste_valido = teste.dropna(subset=[coluna_alvo, *features, "match_id"])
    if len(treino_valido) < MIN_TREINO or len(teste_valido) < MIN_TESTE_PASSO:
        return None

    modelo = treinar_random_forest(treino_valido, features, coluna_alvo)
    probs = modelo.predict_proba(teste_valido[features])
    classes = list(modelo.classes_)
    if dh.RESULTADO_CARTOES_OVER not in classes:
        # Treino sem nenhum exemplo de "over" -- degenerado, guarda mais
        # barata que um ValueError no meio do walk-forward (mesmo padrão
        # do script vs. climatologia).
        return None
    idx_over = classes.index(dh.RESULTADO_CARTOES_OVER)

    p_over = np.clip(probs[:, idx_over], 1e-4, 1 - 1e-4)
    return dict(zip(teste_valido["match_id"].astype(int), p_over.tolist()))


def gerar_previsoes_walkforward(dataset: pd.DataFrame, features: list[str], coluna_alvo: str, linha: float) -> dict[int, float]:
    n_total = len(dataset)
    corte_inicial = dataset["match_date"].iloc[int(n_total * FRACAO_TREINO_INICIAL)]
    passos = montar_passos_mensais(dataset, corte_inicial)

    previsoes: dict[int, float] = {}
    passos_validos = 0
    for inicio_teste, fim_teste in passos:
        resultado = rodar_passo(dataset, features, coluna_alvo, inicio_teste, fim_teste)
        if resultado is None:
            continue
        previsoes.update(resultado)
        passos_validos += 1
    logger.info(
        "[cartoes_total %.1f] walk-forward: %d/%d passos com previsão gerada, %d partidas no total.",
        linha, passos_validos, len(passos), len(previsoes),
    )
    return previsoes


def avaliar_mercado(
    supabase: Client, dataset: pd.DataFrame, previsoes: dict[int, float], linha: float, coluna_alvo: str,
) -> list[dict]:
    mercado = f"cartoes_over_under_{linha}"
    resultados_reais = dict(zip(dataset["match_id"].astype(int), dataset[coluna_alvo]))
    match_ids_com_previsao = list(previsoes.keys())

    linhas_pareadas = []
    for snapshot, rotulo in SNAPSHOTS_AVALIADOS:
        odds_brutas = bk._carregar_odds_pinnacle_brutas(
            supabase, match_ids_com_previsao, mercado, snapshot=snapshot, com_fallback_fechamento=False
        )
        odds_devig = bk._devigar_odds_por_partida(odds_brutas, mercado)
        match_ids_validos = sorted(set(match_ids_com_previsao) & set(odds_devig))
        if len(match_ids_validos) < 30:
            logger.warning("cartoes_total %.1f [%s]: só %d partidas com odd Pinnacle -- pulando.",
                            linha, rotulo, len(match_ids_validos))
            continue

        perdas_modelo, perdas_mercado = [], []
        for match_id in match_ids_validos:
            real = resultados_reais.get(match_id)
            if pd.isna(real):
                continue
            selecao_real = "over" if real == dh.RESULTADO_CARTOES_OVER else "under"
            p_modelo_over = previsoes[match_id]
            p_modelo = p_modelo_over if selecao_real == "over" else (1 - p_modelo_over)
            p_modelo = max(min(p_modelo, 1 - 1e-15), 1e-15)
            # `_devigar_odds_por_partida` sempre grava `prob_<selecao>` --
            # mesmo formato pra todo mercado (ver validar_hibrido_
            # walkforward_incremental.py, mesma observação).
            p_mercado = max(odds_devig[match_id][f"prob_{selecao_real}"], 1e-15)
            perdas_modelo.append(-np.log(p_modelo))
            perdas_mercado.append(-np.log(p_mercado))

        if len(perdas_modelo) < 30:
            continue
        perdas_modelo, perdas_mercado = np.array(perdas_modelo), np.array(perdas_mercado)
        comparacao = bk.comparar_pareado_com_mercado(perdas_modelo, perdas_mercado)
        nome = f"cartoes_total O/U {linha} [{rotulo}]"
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

    features = [f for f in FEATURES_CARTOES if f in dataset.columns]
    faltando = set(FEATURES_CARTOES) - set(features)
    if faltando:
        logger.warning("features ausentes no dataset (ignoradas): %s", sorted(faltando))

    linhas_pareadas_total = []
    for linha in dh.LINHAS_CARTOES_OU:
        coluna_alvo = dh.coluna_resultado_cartoes_ou(linha)
        if coluna_alvo not in dataset.columns:
            logger.warning("[cartoes_total %.1f]: coluna %r ausente no dataset -- pulando.", linha, coluna_alvo)
            continue
        previsoes = gerar_previsoes_walkforward(dataset, features, coluna_alvo, linha)
        if not previsoes:
            logger.error("[cartoes_total %.1f]: nenhuma previsão gerada, pulando avaliação.", linha)
            continue
        linhas_pareadas_total.extend(avaliar_mercado(supabase, dataset, previsoes, linha, coluna_alvo))

    bk.imprimir_relatorio_pareado(linhas_pareadas_total)


if __name__ == "__main__":
    main()
