"""Backtest walk-forward por temporada dos modelos de chutes/gols por
jogador -- mesmo espírito de `backtest_xi_walkforward.py`: pra cada
temporada com histórico suficiente ANTES dela, treina do zero só com dado
estritamente anterior e avalia nela, sem vazamento.

Responde 2 perguntas que o plano da sessão deixou em aberto pra decisão
empírica, não assumida:

1. **Chutes**: o modelo (CatBoost Poisson) bate o baseline ingênuo (EWMA
   crua do próprio jogador x minutos esperados) de forma sustentada,
   temporada a temporada -- não só no split único de
   `treinar_modelo_jogador_mercados.py`?
2. **Gols**: entre afinamento de Poisson (`lambda_gols = lambda_chutes_
   previsto x taxa_conversao_bayesiana`, sem treinar nada novo) e um
   regressor Poisson DIRETO de gols (mesmas features, alvo=gols_partida),
   qual bate o baseline com mais folga? Reportado lado a lado, sem
   declarar vencedor fixo no código -- cabe a quem for usar em produção
   olhar `player_market_backtest` e decidir.

Escopo do que este backtest NÃO cobre (decisão consciente, não omissão):
compara `fonte_titular='previsto'` contra ele mesmo em todas as linhas
(minutos_esperados aqui é sempre a média histórica do jogador, o único
sinal disponível uniformemente pro passado inteiro) -- a comparação real
`'previsto'` vs. `'real'` (escalação oficial confirmada) só existe pra
partidas FUTURAS que passam pelas duas passadas de
`rodar_jogador_mercados_previsto.py` (ver plano da sessão); esse dado
acumula em produção, não é replicável retroativamente sem re-simular
qual escalação "teria saído" pra cada jogo antigo.

Uso:
    SUPABASE_URL=... SUPABASE_KEY=... python3 backtest_jogador_mercados_walkforward.py
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, log_loss
from supabase import Client, create_client

import dados_historicos as dh
import modelos_ml
import treinar_modelo_jogador_mercados as tmj

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

MODEL_VERSION = "jogador_mercados_catboost_walkforward_v1"
# Mesmo racional de MIN_LINHAS_TREINO em backtest_xi_walkforward.py -- só
# tenta treinar uma temporada se já existe histórico minimamente informativo
# antes dela. Maior que o de XI (300) porque o alvo aqui é mais esparso
# (evento raro por jogador-partida, precisa de mais linhas pra estabilizar).
MIN_LINHAS_TREINO = 2000
N_REAMOSTRAGENS_BOOTSTRAP = 1000


def _rmse(previsto: np.ndarray, real: np.ndarray) -> float:
    return float(np.sqrt(np.mean((previsto - real) ** 2)))


def _ic95_bootstrap_diferenca(erro_baseline: np.ndarray, erro_modelo: np.ndarray) -> tuple[float, float, float]:
    """IC95% via bootstrap da diferença (baseline - modelo) de erro
    ABSOLUTO por linha -- positivo = modelo erra menos que o baseline em
    média. Mesmo espírito (reamostragem com reposição, 2000-ish
    repetições) já usado no projeto pra decidir "edge real vs. ruído de
    amostra pequena" em `api/backtest-betting.js`/`avaliar_ic_modelos_por_
    liga.py` -- aqui aplicado a erro de regressão em vez de log-loss de
    aposta, mesmo princípio estatístico."""
    diffs = erro_baseline - erro_modelo
    n = len(diffs)
    if n < 30:
        return float(diffs.mean()), float("nan"), float("nan")
    rng = np.random.default_rng(42)
    medias_boot = np.array([
        diffs[rng.integers(0, n, size=n)].mean() for _ in range(N_REAMOSTRAGENS_BOOTSTRAP)
    ])
    return float(diffs.mean()), float(np.percentile(medias_boot, 2.5)), float(np.percentile(medias_boot, 97.5))


def _metricas_regressao_com_ic(previsto: np.ndarray, baseline: np.ndarray, real: np.ndarray) -> dict:
    erro_modelo = np.abs(previsto - real)
    erro_baseline = np.abs(baseline - real)
    diff_media, ic_inf, ic_sup = _ic95_bootstrap_diferenca(erro_baseline, erro_modelo)
    return {
        "rmse_modelo": _rmse(previsto, real),
        "rmse_baseline": _rmse(baseline, real),
        "diff_erro_absoluto_medio": diff_media,
        "ic95_inf": ic_inf,
        "ic95_sup": ic_sup,
        # Só "sustentado" quando o IC95% inteiro fica ACIMA de zero -- mesma
        # leitura conservadora já documentada no projeto (IC que cruza zero
        # não é edge comprovado, mesmo com média pontual positiva).
        "modelo_melhor_sustentado": bool(ic_inf > 0) if not np.isnan(ic_inf) else False,
        "n": int(len(real)),
    }


def _metricas_probabilidade_marcar(lambda_gols: np.ndarray, real_marcou: np.ndarray, baseline_lambda: np.ndarray) -> dict:
    """Log-loss/Brier/calibração em quintis de P(marcou>=1) = 1 - exp(-lambda),
    contra o baseline "chute inicial" (mesma fórmula, com o lambda do
    baseline). Evento raro (~9% marca em qualquer partida, medido antes de
    escrever este script) -- log-loss é a métrica que importa de verdade,
    acurácia seria dominada pelo desbalanceamento."""
    p_modelo = 1 - np.exp(-np.clip(lambda_gols, 1e-6, None))
    p_baseline = 1 - np.exp(-np.clip(baseline_lambda, 1e-6, None))
    ordenado = np.argsort(p_modelo)
    calibracao = []
    tamanho = len(ordenado) // 5
    if tamanho > 0:
        for i in range(5):
            idx = ordenado[i * tamanho: len(ordenado) if i == 4 else (i + 1) * tamanho]
            if len(idx) == 0:
                continue
            calibracao.append({
                "previsto_medio": float(p_modelo[idx].mean()),
                "real": float(real_marcou[idx].mean()),
                "n": int(len(idx)),
            })
    return {
        "log_loss_modelo": float(log_loss(real_marcou, p_modelo, labels=[0, 1])),
        "log_loss_baseline": float(log_loss(real_marcou, p_baseline, labels=[0, 1])),
        "brier_modelo": float(brier_score_loss(real_marcou, p_modelo)),
        "brier_baseline": float(brier_score_loss(real_marcou, p_baseline)),
        "calibracao": calibracao,
        "n": int(len(real_marcou)),
    }


def _persistir_previsao_bruta(
    supabase: Client, teste: pd.DataFrame, previsto_chutes: np.ndarray, lambda_gols_thinning: np.ndarray,
    lambda_gols_direto: np.ndarray, temporada: str,
) -> int:
    """Grava a previsão bruta (1 linha por jogador x partida x fonte_titular)
    em `player_match_walkforward` -- `fonte_titular` sempre 'previsto' aqui
    (ver docstring do módulo sobre o que este backtest cobre)."""
    linhas = []
    for match_id, team_id, player_id, league_id, minutos_esp, taxa_conv, prev_chutes, lam_gols_thin, lam_gols_dir in zip(
        teste["match_id"], teste["team_id"], teste["player_id"], teste["league_id"], teste["minutos_esperados"],
        teste["taxa_conversao_bayesiana"], previsto_chutes, lambda_gols_thinning, lambda_gols_direto, strict=True,
    ):
        linhas.append({
            "match_id": int(match_id), "team_id": int(team_id), "player_id": int(player_id),
            "fonte_titular": "previsto", "prob_titular_usada": None, "minutos_esperados": float(minutos_esp),
            "taxa_conversao_bayesiana": float(taxa_conv), "lambda_chutes_jogo": float(prev_chutes),
            "lambda_gols_jogo_thinning": float(lam_gols_thin), "lambda_gols_jogo_direto": float(lam_gols_dir),
            "season": str(temporada), "league_id": int(league_id), "model_version": MODEL_VERSION,
        })
    total = 0
    for lote in dh._dividir_em_lotes(linhas, 500):
        supabase.table("player_match_walkforward").upsert(
            lote, on_conflict="match_id,team_id,player_id,model_version,fonte_titular"
        ).execute()
        total += len(lote)
    return total


def rodar(supabase: Client) -> int:
    logger.info("Carregando dataset (6 ligas, corte temporal por liga, shotmap confirmado)...")
    df_bruto = tmj.carregar_dados(supabase)
    if df_bruto.empty:
        logger.warning("Dataset vazio -- nada pra fazer backtest.")
        return 0
    df = tmj.engenharia_features(df_bruto)
    if df.empty:
        logger.warning("Nenhuma linha após engenharia de features -- nada pra fazer backtest.")
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

        params = {"depth": 6, "learning_rate": 0.05}
        modelo_chutes, _, _ = modelos_ml.treinar_catboost_poisson(params, treino, tmj.TARGET_CHUTES, features=tmj.FEATURES_CHUTES)
        previsto_chutes = modelos_ml.prever_catboost_poisson(modelo_chutes, None, teste_temporada, features=tmj.FEATURES_CHUTES)
        baseline_chutes = (teste_temporada["ewma_chutes_90"] * teste_temporada["minutos_esperados"] / 90.0).clip(lower=0.01).to_numpy()
        real_chutes = teste_temporada[tmj.TARGET_CHUTES].to_numpy()
        metricas_chutes = _metricas_regressao_com_ic(previsto_chutes, baseline_chutes, real_chutes)
        logger.info(
            f"  chutes: RMSE modelo={metricas_chutes['rmse_modelo']:.4f} baseline={metricas_chutes['rmse_baseline']:.4f} "
            f"IC95%(dif)=[{metricas_chutes['ic95_inf']:.4f},{metricas_chutes['ic95_sup']:.4f}] "
            f"sustentado={metricas_chutes['modelo_melhor_sustentado']}"
        )

        modelo_gols_direto, _, _ = modelos_ml.treinar_catboost_poisson(params, treino, tmj.TARGET_GOLS, features=tmj.FEATURES_CHUTES)
        previsto_gols_direto = modelos_ml.prever_catboost_poisson(modelo_gols_direto, None, teste_temporada, features=tmj.FEATURES_CHUTES)

        taxa_conversao = teste_temporada["taxa_conversao_bayesiana"].to_numpy()
        lambda_gols_thinning = previsto_chutes * taxa_conversao
        real_marcou = (teste_temporada[tmj.TARGET_GOLS].to_numpy() > 0).astype(int)
        baseline_lambda_gols = (teste_temporada["ewma_gols_90"] * teste_temporada["minutos_esperados"] / 90.0).clip(lower=0.001).to_numpy()

        metricas_gols_thinning = _metricas_probabilidade_marcar(lambda_gols_thinning, real_marcou, baseline_lambda_gols)
        metricas_gols_direto = _metricas_probabilidade_marcar(previsto_gols_direto, real_marcou, baseline_lambda_gols)
        logger.info(
            f"  gols (marcar>=1): thinning log-loss={metricas_gols_thinning['log_loss_modelo']:.4f} vs. "
            f"direto log-loss={metricas_gols_direto['log_loss_modelo']:.4f} vs. baseline={metricas_gols_thinning['log_loss_baseline']:.4f} "
            f"-- {'thinning melhor' if metricas_gols_thinning['log_loss_modelo'] < metricas_gols_direto['log_loss_modelo'] else 'direto melhor'}"
        )

        n_gravado = _persistir_previsao_bruta(
            supabase, teste_temporada, previsto_chutes, lambda_gols_thinning, previsto_gols_direto, temporada
        )
        logger.info(f"  {n_gravado} previsões por jogador gravadas em player_match_walkforward.")

        linhas_agregado = []
        for league_id, g in teste_temporada.groupby("league_id"):
            mask = (teste_temporada["league_id"] == league_id).to_numpy()
            if mask.sum() < 30:
                continue
            m_chutes = _metricas_regressao_com_ic(previsto_chutes[mask], baseline_chutes[mask], real_chutes[mask])
            m_gols_thin = _metricas_probabilidade_marcar(lambda_gols_thinning[mask], real_marcou[mask], baseline_lambda_gols[mask])
            m_gols_dir = _metricas_probabilidade_marcar(previsto_gols_direto[mask], real_marcou[mask], baseline_lambda_gols[mask])
            for mercado, metricas in (
                ("chutes", m_chutes),
                ("gols_thinning", m_gols_thin),
                ("gols_direto", m_gols_dir),
            ):
                linhas_agregado.append({
                    "season": str(temporada), "league_id": int(league_id), "model_version": MODEL_VERSION,
                    "mercado": mercado, "n_partidas": int(g["match_id"].nunique()), "n_previsoes": metricas["n"],
                    "rmse_modelo": metricas.get("rmse_modelo"), "rmse_baseline": metricas.get("rmse_baseline"),
                    "log_loss": metricas.get("log_loss_modelo"), "brier": metricas.get("brier_modelo"),
                    "calibracao": metricas.get("calibracao"),
                })

        if linhas_agregado:
            supabase.table("player_market_backtest").upsert(
                linhas_agregado, on_conflict="season,league_id,model_version,mercado"
            ).execute()
            total_gravado += len(linhas_agregado)
            logger.info(f"  {len(linhas_agregado)} grupos (liga x mercado) gravados em player_market_backtest.")

    logger.info(f"Backtest concluído: {total_gravado} grupos gravados em player_market_backtest.")
    return total_gravado


if __name__ == "__main__":
    import os

    url = os.environ["SUPABASE_URL"].strip()
    key = os.environ["SUPABASE_KEY"].strip()
    sb = create_client(url, key)
    rodar(sb)
