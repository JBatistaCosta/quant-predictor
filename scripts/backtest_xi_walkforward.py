"""Backtest walk-forward por temporada do modelo de XI titular -- pra cada
temporada com histórico suficiente ANTES dela, treina do zero só com dado
ESTRITAMENTE anterior (nunca viu aquela temporada) e avalia nela.

Diferente de treinar_modelo_xi.py (que treina UMA VEZ com split cronológico
fixo, e o modelo resultante fica valendo em produção até o próximo retreino
manual): rodar esse modelo de produção sobre o histórico inteiro daria
métrica artificialmente perfeita nas partidas que já estavam no TREINO
dele -- não seria "fingir não saber" de verdade. Aqui cada temporada tem seu
próprio modelo, treinado só com o passado, pra responder essa pergunta sem
vazamento -- mesmo espírito do walk-forward já usado pro modelo de resultado
de partida (scripts/walkforward_cv_v9.py, folds por ano), aplicado ao
classificador de XI.

Caro (retreina o ensemble uma vez por temporada, não uma vez só), mas
tratável: o treino único de produção (18k+ partidas, todo o histórico) já
leva ~12min no GitHub Actions: cada fold aqui treina numa FATIA menor
(só o que veio antes daquela temporada), então o total fica na mesma ordem
de grandeza, não um múltiplo grande disso.

Resultado grava em xi_backtest_walkforward (upsert por temporada+liga),
consumido por src/pages/XiModeloStats.jsx.

Uso:
    SUPABASE_URL=... SUPABASE_KEY=... python3 backtest_xi_walkforward.py
"""

import logging

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier
from lightgbm import LGBMClassifier
from sklearn.metrics import brier_score_loss, log_loss
from supabase import Client, create_client
from xgboost import XGBClassifier

from treinar_modelo_xi import FEATURES, TARGET, carregar_dados, engenharia_features

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

MODEL_VERSION = "xi_titular_ensemble_walkforward"
# Menos que isso de linhas de treino disponíveis ANTES da temporada não dá
# pra treinar um ensemble minimamente informativo -- não é um corte
# arbitrário de "quais temporadas valem", é só a garantia de que existe
# dado suficiente pra tentar. Temporadas puladas aparecem no log.
MIN_LINHAS_TREINO = 300


def _treinar_ensemble_e_prever(treino: pd.DataFrame, teste: pd.DataFrame) -> np.ndarray:
    """Mesmos 3 algoritmos/hiperparâmetros de treinar_modelo_xi.treinar,
    sem validation set (aqui não precisa de early stopping -- é um único
    treino descartável por fold, não o modelo de produção) -- média das
    probabilidades, mesmo ensemble usado ao vivo em rodar_xi_previsto.py."""
    X_train, y_train = treino[FEATURES], treino[TARGET]
    X_test = teste[FEATURES]
    peso_pos = (len(y_train) - sum(y_train)) / max(sum(y_train), 1)
    modelos = [
        LGBMClassifier(n_estimators=300, max_depth=5, learning_rate=0.05, class_weight="balanced", random_state=42, verbosity=-1),
        XGBClassifier(n_estimators=300, max_depth=5, learning_rate=0.05, scale_pos_weight=peso_pos, random_state=42, eval_metric="logloss"),
        CatBoostClassifier(iterations=300, depth=5, learning_rate=0.05, auto_class_weights="Balanced", random_seed=42, verbose=0),
    ]
    probas = []
    for modelo in modelos:
        modelo.fit(X_train, y_train)
        probas.append(modelo.predict_proba(X_test)[:, 1])
    return np.mean(probas, axis=0)


def _metricas_grupo(df_teste: pd.DataFrame, y_proba: np.ndarray) -> dict | None:
    """Mesma definição de precisao_media_top11/taxa_xi_exato de
    treinar_modelo_xi._metricas_top11, mais Brier/log-loss/calibração em
    quintis -- mesmo cálculo usado em api/model-stats.js (?formato=xi) pra
    métrica ao vivo e backtest lerem pela mesma régua."""
    grupo = df_teste[["match_id", "team_id", "is_starter"]].copy()
    grupo["proba"] = y_proba

    precisoes, exatos = [], []
    for _, g in grupo.groupby(["match_id", "team_id"]):
        if len(g) < 11 or g["is_starter"].sum() == 0:
            continue
        top11 = g.sort_values("proba", ascending=False).head(11)
        reais = set(g[g["is_starter"] == 1].index)
        previstos = set(top11.index)
        acertos = len(reais & previstos)
        precisoes.append(acertos / 11)
        exatos.append(1.0 if reais == previstos else 0.0)
    if not precisoes:
        return None

    y_real = grupo["is_starter"].to_numpy()
    brier = float(brier_score_loss(y_real, y_proba))
    ll = float(log_loss(y_real, y_proba, labels=[0, 1]))

    ordenado = grupo.sort_values("proba")
    calibracao = []
    tamanho = len(ordenado) // 5
    if tamanho > 0:
        for i in range(5):
            fatia = ordenado.iloc[i * tamanho : len(ordenado) if i == 4 else (i + 1) * tamanho]
            if fatia.empty:
                continue
            calibracao.append(
                {
                    "previsto_medio": float(fatia["proba"].mean()),
                    "real": float(fatia["is_starter"].mean()),
                    "n": int(len(fatia)),
                }
            )

    return {
        "n_partidas": len(precisoes),
        "n_previsoes": int(len(grupo)),
        "precisao_media_top11": float(np.mean(precisoes)),
        "taxa_xi_exato": float(np.mean(exatos)),
        "brier": brier,
        "log_loss": ll,
        "calibracao": calibracao,
    }


def rodar(supabase: Client) -> int:
    logger.info("Carregando dados históricos...")
    df_bruto = carregar_dados(supabase)
    if df_bruto.empty:
        logger.warning("Sem dados de lineup em match_lineup_fotmob -- nada pra fazer backtest.")
        return 0

    df = engenharia_features(df_bruto)
    df = df.dropna(subset=["season", "league_id"])
    if df.empty:
        logger.warning("Nenhuma linha com season/league_id preenchidos -- nada pra fazer backtest.")
        return 0

    temporadas = sorted(df["season"].unique())
    logger.info(f"Temporadas encontradas: {temporadas}")

    total_gravado = 0
    for temporada in temporadas:
        teste_temporada = df[df["season"] == temporada]
        data_inicio = teste_temporada["match_date"].min()
        treino = df[df["match_date"] < data_inicio]

        if len(treino) < MIN_LINHAS_TREINO:
            logger.info(f"Temporada {temporada}: só {len(treino)} linhas de treino antes dela (< {MIN_LINHAS_TREINO}) -- pulando.")
            continue

        logger.info(f"Temporada {temporada}: treinando com {len(treino)} linhas, avaliando {len(teste_temporada)} linhas...")
        y_proba = _treinar_ensemble_e_prever(treino, teste_temporada)

        linhas_upsert = []
        for league_id, g in teste_temporada.groupby("league_id"):
            proba_liga = y_proba[(teste_temporada["league_id"] == league_id).to_numpy()]
            metricas = _metricas_grupo(g, proba_liga)
            if metricas is None:
                continue
            linhas_upsert.append(
                {
                    "season": str(temporada),
                    "league_id": int(league_id),
                    "model_version": MODEL_VERSION,
                    **metricas,
                }
            )

        if linhas_upsert:
            supabase.table("xi_backtest_walkforward").upsert(linhas_upsert, on_conflict="season,league_id,model_version").execute()
            total_gravado += len(linhas_upsert)
            logger.info(f"Temporada {temporada}: {len(linhas_upsert)} grupos (liga) gravados.")

    logger.info(f"Backtest concluído: {total_gravado} grupos gravados em xi_backtest_walkforward.")
    return total_gravado


if __name__ == "__main__":
    import os

    url = os.environ["SUPABASE_URL"].strip()
    key = os.environ["SUPABASE_KEY"].strip()
    sb = create_client(url, key)
    rodar(sb)
