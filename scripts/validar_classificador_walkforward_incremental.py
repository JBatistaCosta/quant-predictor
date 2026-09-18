#!/usr/bin/env python3
"""Walk-forward incremental (janela expansiva, passo mensal) dos
classificadores de Cartões e Faltas (total da partida) contra a
CLIMATOLOGIA -- não contra o mercado.

MOTIVAÇÃO: cartões já tem comparação com a Pinnacle (ver CONTEXTO_
PROJETO.md, "Comparação com mercado -- Cartões geral"), mas faltas NUNCA
teve mercado real (`odds_market` não tem nenhuma linha de "foul"/"falta",
confirmado via SQL) -- por isso a única validação possível pra faltas é
contra um baseline sem informação nenhuma (climatologia: taxa histórica
de over/under daquela linha). Pergunta do usuário: dá pra medir a
capacidade de predição contra os eventos reais (não o mercado) com IC95%?
Sim -- é a mesma máquina de bootstrap pareado já usada pros walk-forwards
de gols/escanteios (`backtest_kelly.comparar_pareado_com_mercado`), só
trocando o braço "mercado" por um braço "climatologia" (taxa de over do
TREINO até aquele ponto, sem nenhuma feature -- é o baseline mais fraco
possível, e é o mesmo usado no BSS já documentado neste projeto).

DIFERENÇA DE DESENHO em relação aos walk-forwards de gols/escanteios
(`validar_hibrido_walkforward_incremental.py`/`validar_corners_
walkforward_incremental.py`): aqui NÃO há fatia de calibração separada --
os classificadores de Cartões/Faltas não têm nenhum parâmetro estrutural
(ρ/dispersão/split) pra calibrar depois do fit, a probabilidade sai direto
do `predict_proba`. Por isso o passo é só treino/teste, mais simples.

ALGORITMO: Random Forest via sklearn (mesmo `treinar_via_sklearn` de
`treinar_modelo_custom.py`, hiperparâmetros idênticos) -- historicamente
o melhor ou empatado com XGBoost nas 12 linhas já documentadas
(CONTEXTO_PROJETO.md, tabela "fold 3" de Faltas). Não usa `liga` como
feature (nenhuma das configs reais de Cartões/Faltas inclui essa coluna,
e Random Forest via sklearn não aceita categórica direto de qualquer
forma).

ESCOPO: só as linhas "total da partida" de Cartões e Faltas (12 linhas no
total, 6+6) -- as mesmas já documentadas com log-loss/Brier intrínseco.
Cartões/Faltas por time e Faltas 1º tempo ficam de fora desta rodada
(mesmo feature set, mesmo script -- é só questão de adicionar entradas em
`MERCADOS_AVALIADOS` se algum dia fizer sentido).

Não escreve nada no Supabase -- só leitura e relatório em stdout, mesma
categoria dos demais scripts `validar_*_walkforward_incremental.py`.

Uso:
    set SUPABASE_URL=...
    set SUPABASE_KEY=sua_service_role_key (ou anon -- só leitura)
    python scripts/validar_classificador_walkforward_incremental.py
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
logger = logging.getLogger("validar_classificador_walkforward_incremental")

# Mesmos hiperparâmetros de `treinar_modelo_custom.treinar_via_sklearn`
# pro ramo "random_forest" (defaults quando `hyperparameters` é None, que
# é como as 37 configs reais de Cartões/Faltas estão cadastradas hoje).
RF_PARAMS = {"n_estimators": 200, "max_depth": None, "min_samples_leaf": 5, "random_state": 42, "n_jobs": -1}

FEATURES_CARTOES = [
    "media_cartoes_amarelos_fm_5j_home", "media_cartoes_amarelos_fm_sofrido_5j_home",
    "media_cartoes_amarelos_fm_5j_away", "media_cartoes_amarelos_fm_sofrido_5j_away",
    "media_cartoes_vermelhos_fm_5j_home", "media_cartoes_vermelhos_fm_5j_away",
    "media_faltas_fm_5j_home", "media_faltas_fm_sofrido_5j_home",
    "media_faltas_fm_5j_away", "media_faltas_fm_sofrido_5j_away",
    "arbitro_cartoes_media", "arbitro_faltas_media", "arbitro_n_jogos",
]
FEATURES_FALTAS = [
    "media_faltas_fm_5j_home", "media_faltas_fm_sofrido_5j_home",
    "media_faltas_fm_5j_away", "media_faltas_fm_sofrido_5j_away",
    "media_cartoes_amarelos_fm_5j_home", "media_cartoes_amarelos_fm_5j_away",
    "arbitro_cartoes_media", "arbitro_faltas_media", "arbitro_n_jogos",
]

# Mesmos feature sets/linhas das 12 `custom_model_configs` reais "Cartões
# — FBref + FotMob (O/U X)" e "Faltas — FBref + FotMob (O/U X)" (conferido
# direto na tabela via `execute_sql`, 18/09).
MERCADOS_AVALIADOS = [
    {"nome": "cartoes_total", "linhas": dh.LINHAS_CARTOES_OU, "coluna_fn": dh.coluna_resultado_cartoes_ou, "features": FEATURES_CARTOES},
    {"nome": "faltas_total", "linhas": dh.LINHAS_FALTAS_OU, "coluna_fn": dh.coluna_resultado_faltas_ou, "features": FEATURES_FALTAS},
]

LIGAS_MODELO_MISTO_ESCOPO = "treino"
FRACAO_TREINO_INICIAL = 0.60
MIN_TREINO = 500
MIN_TESTE_PASSO = 20


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
) -> tuple[np.ndarray, np.ndarray] | None:
    """Devolve `(perdas_modelo, perdas_climatologia)` do mês de teste, ou
    `None` quando falta dado suficiente. A climatologia é a taxa de "over"
    (`coluna_alvo`) no TREINO até esse ponto -- baseline sem feature
    nenhuma, mesmo espírito do BSS já documentado neste projeto."""
    treino = dataset[dataset["match_date"] < inicio_teste]
    teste = dataset[(dataset["match_date"] >= inicio_teste) & (dataset["match_date"] <= fim_teste)]

    treino_valido = treino.dropna(subset=[coluna_alvo, *features])
    teste_valido = teste.dropna(subset=[coluna_alvo, *features])
    if len(treino_valido) < MIN_TREINO or len(teste_valido) < MIN_TESTE_PASSO:
        return None

    p_climatologia = float(treino_valido[coluna_alvo].mean())
    p_climatologia = min(max(p_climatologia, 1e-4), 1 - 1e-4)

    modelo = treinar_random_forest(treino_valido, features, coluna_alvo)
    probs = modelo.predict_proba(teste_valido[features])
    classes = list(modelo.classes_)
    if 1 not in classes:
        # Treino sem nenhum exemplo de "over" (degenerado, não deveria
        # acontecer com MIN_TREINO=500 e taxas base ~50%, mas guarda
        # explícita é mais barata que um ValueError no meio do walk-forward).
        return None
    idx_over = classes.index(1)

    real = teste_valido[coluna_alvo].to_numpy()
    p_modelo_over = np.clip(probs[:, idx_over], 1e-4, 1 - 1e-4)
    p_modelo_real = np.where(real == 1, p_modelo_over, 1 - p_modelo_over)
    p_climatologia_real = np.where(real == 1, p_climatologia, 1 - p_climatologia)

    return -np.log(p_modelo_real), -np.log(p_climatologia_real)


def avaliar_mercado(dataset: pd.DataFrame, nome: str, linha: float, coluna_fn, features: list[str]) -> dict | None:
    coluna_alvo = coluna_fn(linha)
    if coluna_alvo not in dataset.columns:
        logger.warning("[%s %.1f]: coluna %r ausente no dataset -- pulando.", nome, linha, coluna_alvo)
        return None

    n_total = len(dataset)
    corte_inicial = dataset["match_date"].iloc[int(n_total * FRACAO_TREINO_INICIAL)]
    passos = montar_passos_mensais(dataset, corte_inicial)

    perdas_modelo_total, perdas_clim_total = [], []
    for inicio_teste, fim_teste in passos:
        resultado = rodar_passo(dataset, features, coluna_alvo, inicio_teste, fim_teste)
        if resultado is None:
            continue
        pm, pc = resultado
        perdas_modelo_total.extend(pm.tolist())
        perdas_clim_total.extend(pc.tolist())

    if len(perdas_modelo_total) < 100:
        logger.warning("[%s %.1f]: só %d observações no walk-forward -- pulando.", nome, linha, len(perdas_modelo_total))
        return None

    perdas_modelo_total = np.array(perdas_modelo_total)
    perdas_clim_total = np.array(perdas_clim_total)
    comparacao = bk.comparar_pareado_com_mercado(perdas_modelo_total, perdas_clim_total)
    logger.info(
        "[%s %.1f]: n=%d | log-loss modelo=%.4f | log-loss climatologia=%.4f",
        nome, linha, len(perdas_modelo_total), perdas_modelo_total.mean(), perdas_clim_total.mean(),
    )
    return {"nome": f"{nome} O/U {linha} [vs. climatologia]", "comparacao": comparacao}


def main() -> None:
    supabase = create_client(obter_env("SUPABASE_URL"), obter_env("SUPABASE_KEY"))

    league_ids = obter_league_ids_escopo(supabase)
    logger.info("Montando dataset Feature Stacked (%d ligas em escopo)...", len(league_ids))
    dataset = dh.montar_dataset_ml_empilhado(supabase, league_ids_manual=league_ids)
    dataset["match_date"] = pd.to_datetime(dataset["match_date"], utc=True)
    dataset = dataset.dropna(subset=["match_id"]).sort_values("match_date").reset_index(drop=True)
    logger.info("Dataset montado: %d partidas (%s a %s).",
                len(dataset), dataset["match_date"].min().date(), dataset["match_date"].max().date())

    linhas_pareadas = []
    for mercado in MERCADOS_AVALIADOS:
        features = [f for f in mercado["features"] if f in dataset.columns]
        faltando = set(mercado["features"]) - set(features)
        if faltando:
            logger.warning("[%s] features ausentes no dataset (ignoradas): %s", mercado["nome"], sorted(faltando))
        for linha in mercado["linhas"]:
            resultado = avaliar_mercado(dataset, mercado["nome"], linha, mercado["coluna_fn"], features)
            if resultado is not None:
                linhas_pareadas.append(resultado)

    bk.imprimir_relatorio_pareado(linhas_pareadas)


if __name__ == "__main__":
    main()
