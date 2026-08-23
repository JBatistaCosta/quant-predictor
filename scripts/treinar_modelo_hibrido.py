#!/usr/bin/env python3
"""Modelo misto: ML estima os parâmetros, a distribuição gera os mercados.

Fecha o circuito que hoje está aberto no projeto. Existem duas famílias de
modelo que não conversam:

  - Dixon-Coles clássico (`modelo_dixon_coles.py`, `rodar_predicoes.py`)
    estima λ só com força de ataque/defesa por time. Ignora elo, forma,
    xG, XI titular, fadiga, árbitro -- as ~190 features que
    `dados_historicos.py` já constrói.
  - Os classificadores (CatBoost/XGBoost/LightGBM) usam todas essas
    features, mas cospem probabilidade de classe direto, um modelo
    independente por mercado. Resultado: o P(over 2.5) de um modelo não é
    coerente com o 1X2 do MESMO modelo, e todos sofrem de overconfidence
    sistemático (coeficiente Platt entre 0,34 e 0,45, achado #6 do
    CONTEXTO_PROJETO.md).

Aqui, ML estima os PARÂMETROS (λ de gols por equipe, λ de escanteios) e
`distribuicoes.py` deriva TODOS os mercados da mesma distribuição
conjunta. Coerência entre mercados vira propriedade estrutural.

Split cronológico 60/20/20 -- treino / calibração / teste:
  - treino: ajusta os regressores de λ;
  - calibração: ajusta os parâmetros que o ML não estima (ρ, dispersão r,
    α/β do split) CONDICIONADOS nos λ do modelo, e a calibração residual.
    Fatia separada de propósito: estimar dispersão em cima dos resíduos do
    próprio treino subestimaria a dispersão real (o modelo já viu esses
    jogos);
  - teste: holdout intocado, única fatia que entra na avaliação e em
    `model_predictions`. Mesma disciplina de `modelo_dixon_coles.py` --
    nunca gravar previsão em cima de dado que o modelo já viu.

Variantes de λ treinadas e comparadas out-of-sample (não decidido por
argumento -- pedido explícito do usuário):
  - `hibrido_gols_v1`      -- perda de Poisson sobre GOLS REAIS;
  - `hibrido_gols_xg_v1`   -- perda de Poisson sobre xG observado (FotMob).

Ligas "extra" (`--ligas-extra`): competições sem cobertura de dado
suficiente pra entrar no fit dos regressores nem nas métricas (ex.: sem
nenhum xG real, como Copa Sudamericana/Copa do Brasil/FIFA Intercontinental
Cup, medido em ago/2026) -- o modelo ajustado em `--ligas` é aplicado
nelas (só `.predict`, nunca `.fit`) reaproveitando os MESMOS ρ/dispersão/
α,β da calibração, e as estimativas/mercados delas são persistidos junto,
sem entrar em `avaliar()`/`baselines()`/`models_registry`.

Escopo por padrão vem do banco, não de lista hardcoded: `--ligas` e
`--ligas-extra` são overrides manuais (útil pra teste rápido numa liga
só) -- omitidos, o escopo é resolvido de `leagues.modelo_misto_escopo`
('treino'/'extra'/NULL). Reclassificar uma liga é um `UPDATE`, não uma
mudança de código nem de input do `workflow_dispatch`.

Jogos futuros: toda partida `status='scheduled'` de qualquer liga em
escopo (treino OU extra) também recebe estimativa -- mesmo espírito das
ligas extra (só `.predict`, nunca `.fit`, nunca entra em `avaliar()`/
`baselines()`), usando o mecanismo `match_ids_extra` de
`montar_dataset_ml_empilhado` (o mesmo que `estimar_partida_custom.py`/
`prever_partidas_futuras_custom.py` já usam) pra injetar partida de
QUALQUER status no dataset. Sem persistência do modelo ajustado entre
execuções, então manter a previsão de jogo futuro atualizada exige
rodar o script de novo (o cron do workflow roda diário + semanal --
ver `.github/workflows/treinar_modelo_hibrido.yml`).

Uso:
    set SUPABASE_URL=...
    set SUPABASE_KEY=sua_service_role_key
    python treinar_modelo_hibrido.py                              # escopo via leagues.modelo_misto_escopo
    python treinar_modelo_hibrido.py --ligas "Premier League"     # override manual, teste rápido
    python treinar_modelo_hibrido.py --ligas "Premier League" --ligas-extra "Copa do Brasil,Copa Sudamericana"
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from supabase import create_client

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dados_historicos as dh
import distribuicoes as dist
import modelos_ml as ml

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
logger = logging.getLogger("treinar_modelo_hibrido")

FRACAO_TREINO = 0.60
FRACAO_CALIBRACAO = 0.20  # o resto (20%) é teste

# Variantes de λ de gols. `coluna_home`/`coluna_away` é o ALVO do regressor;
# o modelo é sempre avaliado contra os GOLS reais, mesmo quando treinado em
# xG -- senão a comparação entre variantes não faria sentido.
VARIANTES_GOLS = {
    "hibrido_gols_v1": {"home": "home_goals", "away": "away_goals"},
    "hibrido_gols_xg_v1": {"home": "xg_home", "away": "xg_away"},
}
MODELO_CORNERS = "hibrido_corners_v1"

# Lote de upsert. 500 é o mesmo tamanho já usado por modelo_dixon_coles.py
# e treinar_regressor_xg.py.
LOTE_UPSERT = 500


def obter_env(nome: str) -> str:
    valor = os.environ.get(nome)
    if not valor:
        sys.exit(f"Configure {nome} antes de rodar.")
    return valor


# ---------------------------------------------------------------------------
# Split
# ---------------------------------------------------------------------------
def dividir_cronologicamente(dataset: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """60/20/20 por DATA, nunca aleatório.

    Split aleatório vazaria o futuro no passado: a forma pré-jogo de uma
    partida de maio é construída com resultados de março, então uma
    partida de março no teste e uma de maio no treino deixariam o modelo
    ver indiretamente o que devia prever.
    """
    dataset = dataset.sort_values("match_date").reset_index(drop=True)
    n = len(dataset)
    corte_treino = int(n * FRACAO_TREINO)
    corte_calib = int(n * (FRACAO_TREINO + FRACAO_CALIBRACAO))
    return dataset.iloc[:corte_treino], dataset.iloc[corte_treino:corte_calib], dataset.iloc[corte_calib:]


# ---------------------------------------------------------------------------
# Camada 1: ML estima os parâmetros
# ---------------------------------------------------------------------------
def treinar_lambdas_gols(
    model_name: str, alvos: dict[str, str], treino: pd.DataFrame, aplicar_em: list[pd.DataFrame]
) -> list[tuple[np.ndarray, np.ndarray]] | None:
    """Treina os regressores de λ (mandante e visitante) e aplica nas fatias.

    Devolve `[(lambda_home, lambda_away), ...]` na mesma ordem de
    `aplicar_em`, ou None se não houver alvo suficiente (o caso do xG, que
    não cobre todas as partidas).
    """
    features = ml.FEATURES_POR_MODELO[model_name]
    params = ml.PARAMS_DEFAULT[model_name]

    modelos = {}
    for lado, coluna in alvos.items():
        if coluna not in treino.columns:
            logger.warning("[%s] coluna-alvo %r não existe no dataset -- variante pulada.", model_name, coluna)
            return None
        treino_lado = treino.dropna(subset=[coluna])
        if len(treino_lado) < 500:
            logger.warning(
                "[%s] só %d linhas com %r preenchido -- variante pulada (mínimo 500).",
                model_name, len(treino_lado), coluna,
            )
            return None
        modelos[lado], _, _ = ml.treinar_catboost_poisson(params, treino_lado, coluna, features=features)
        logger.info("[%s] λ_%s treinado em %d partidas (alvo=%s).", model_name, lado, len(treino_lado), coluna)

    return [
        (
            ml.prever_catboost_poisson(modelos["home"], None, fatia, features=features),
            ml.prever_catboost_poisson(modelos["away"], None, fatia, features=features),
        )
        for fatia in aplicar_em
    ]


def treinar_lambda_corners(treino: pd.DataFrame, aplicar_em: list[pd.DataFrame]) -> list[np.ndarray] | None:
    """Regressor do TOTAL de escanteios.

    Modela o total, não cada equipe: a correlação medida entre escanteios
    de casa e fora é NEGATIVA (-0,24 a -0,31), então duas marginais
    independentes somariam variância demais. A repartição entre as equipes
    fica por conta do split Beta-Binomial (ver `distribuicoes.py`).
    """
    features = ml.FEATURES_POR_MODELO[MODELO_CORNERS]
    params = ml.PARAMS_DEFAULT[MODELO_CORNERS]

    if "total_corners" not in treino.columns:
        logger.warning("[%s] `total_corners` ausente no dataset -- escanteios pulados.", MODELO_CORNERS)
        return None
    treino_c = treino.dropna(subset=["total_corners"])
    if len(treino_c) < 500:
        logger.warning("[%s] só %d partidas com escanteios -- pulado.", MODELO_CORNERS, len(treino_c))
        return None

    modelo, _, _ = ml.treinar_catboost_poisson(params, treino_c, "total_corners", features=features)
    logger.info("[%s] λ de escanteios treinado em %d partidas.", MODELO_CORNERS, len(treino_c))
    return [ml.prever_catboost_poisson(modelo, None, fatia, features=features) for fatia in aplicar_em]


def ajustar_parametros_estruturais(
    calib: pd.DataFrame,
    lam_home: np.ndarray,
    lam_away: np.ndarray,
    lam_corners: np.ndarray | None,
) -> dict[str, float]:
    """Estima, na fatia de CALIBRAÇÃO, o que o ML não estima.

    São poucos parâmetros (ρ, r, α, β) ajustados sobre milhares de
    partidas, condicionados nos λ que o ML já produziu -- é o que mantém o
    ajuste estável em vez de tentar aprender distribuição e parâmetros de
    uma vez só.

    Fatia de calibração e não a de treino de propósito: o modelo já viu as
    partidas de treino, então o resíduo lá é otimisticamente pequeno e a
    dispersão sairia subestimada.
    """
    parametros: dict[str, float] = {}

    valido = calib["home_goals"].notna() & calib["away_goals"].notna()
    parametros["rho"] = dist.ajustar_rho_mle(
        lam_home[valido.to_numpy()], lam_away[valido.to_numpy()],
        calib.loc[valido, "home_goals"].to_numpy(), calib.loc[valido, "away_goals"].to_numpy(),
    )
    logger.info("ρ (Dixon-Coles) ajustado por MLE: %.4f", parametros["rho"])

    if lam_corners is not None and "total_corners" in calib.columns:
        tem_corner = calib["total_corners"].notna().to_numpy()
        if tem_corner.sum() >= 200:
            total = calib.loc[tem_corner, "total_corners"].to_numpy()
            disp = dist.ajustar_dispersao_nb(lam_corners[tem_corner], total)
            # `inf` é matematicamente correto (NB degenera em Poisson), mas
            # não serializa em JSON nem cabe numa coluna numeric. O teto
            # replica a Poisson pra qualquer efeito prático.
            parametros["corners_disp_r"] = float(min(disp, 1e6))
            logger.info("dispersão r dos escanteios: %.1f (var/média implícita ≈ %.3f)",
                        parametros["corners_disp_r"], 1 + total.mean() / parametros["corners_disp_r"])

            colunas_split = {"total_corners_home", "total_corners_away"}
            if colunas_split <= set(calib.columns):
                split_ok = tem_corner & calib["total_corners_home"].notna().to_numpy()
                if split_ok.sum() >= 200:
                    alpha, beta = dist.ajustar_beta_binomial(
                        calib.loc[split_ok, "total_corners"].to_numpy(),
                        calib.loc[split_ok, "total_corners_home"].to_numpy(),
                    )
                    parametros["corners_alpha"], parametros["corners_beta"] = alpha, beta
                    logger.info("split Beta-Binomial: α=%.2f β=%.2f (fatia média do mandante = %.3f)",
                                alpha, beta, alpha / (alpha + beta))

    return parametros


# ---------------------------------------------------------------------------
# Camada 2: distribuição -> mercados
# ---------------------------------------------------------------------------
def derivar_mercados(
    lam_home: float, lam_away: float, rho: float,
    lam_corners: float | None, parametros: dict[str, float],
) -> dict[tuple[str, str], float]:
    """Todos os mercados de uma partida, saindo da mesma distribuição."""
    matriz = dist.matriz_placares(lam_home, lam_away, rho)
    mercados = dist.mercados_de_gols(matriz)

    if lam_corners is not None and "corners_alpha" in parametros:
        mercados.update(dist.mercados_de_escanteios(
            lam_corners,
            parametros.get("corners_disp_r", 65.0),
            parametros["corners_alpha"],
            parametros["corners_beta"],
        ))
    return mercados


# ---------------------------------------------------------------------------
# Persistência
# ---------------------------------------------------------------------------
def upsert_em_lotes(supabase, tabela: str, linhas: list[dict], on_conflict: str) -> int:
    """Upsert com dedup por chave de conflito.

    O dedup não é zelo excessivo: `matches` já teve fixture duplicada pro
    mesmo match_id em produção, e o Postgres rejeita o INSERT INTEIRO com
    "cannot affect row a second time" quando duas linhas do mesmo lote
    carregam a mesma chave. Mesmo tratamento de `modelo_dixon_coles.py`.
    """
    chaves = on_conflict.split(",")
    unicas = list({tuple(linha[k] for k in chaves): linha for linha in linhas}.values())
    for i in range(0, len(unicas), LOTE_UPSERT):
        supabase.table(tabela).upsert(unicas[i : i + LOTE_UPSERT], on_conflict=on_conflict).execute()
    return len(unicas)


def persistir(
    supabase, model_name: str, teste: pd.DataFrame,
    lam_home: np.ndarray, lam_away: np.ndarray, lam_corners: np.ndarray | None,
    parametros: dict[str, float],
) -> None:
    """Grava parâmetros por partida e probabilidades derivadas.

    Só a fatia de TESTE -- nunca previsão em cima de dado que o modelo viu.
    """
    estimativas, previsoes = [], []
    for pos, (_, jogo) in enumerate(teste.iterrows()):
        match_id = int(jogo["match_id"])
        lam_c = float(lam_corners[pos]) if lam_corners is not None else None

        params_partida = {
            "lambda_home": round(float(lam_home[pos]), 4),
            "lambda_away": round(float(lam_away[pos]), 4),
            "rho": round(parametros["rho"], 4),
        }
        if lam_c is not None:
            params_partida["corners_lambda_total"] = round(lam_c, 3)
            for chave in ("corners_disp_r", "corners_alpha", "corners_beta"):
                if chave in parametros:
                    params_partida[chave] = round(parametros[chave], 4)

        estimativas.append({
            "match_id": match_id, "model_name": model_name,
            # Mantém as colunas tipadas antigas preenchidas: o relatório
            # partida a partida e `treinar_regressor_xg.py` já leem dali.
            "xg_home_previsto": params_partida["lambda_home"],
            "xg_away_previsto": params_partida["lambda_away"],
            "params": params_partida,
        })

        for (mercado, selecao), prob in derivar_mercados(
            float(lam_home[pos]), float(lam_away[pos]), parametros["rho"], lam_c, parametros
        ).items():
            prob = float(np.clip(prob, 1e-9, 1.0))
            previsoes.append({
                "match_id": match_id, "model_name": model_name,
                "market": mercado, "selection": selecao,
                "probability": round(prob, 6),
            })

    n_est = upsert_em_lotes(supabase, "model_match_estimates", estimativas, "match_id,model_name")
    n_prev = upsert_em_lotes(supabase, "model_predictions", previsoes, "match_id,model_name,market,selection")
    logger.info("[%s] %d parâmetros e %d probabilidades gravados.", model_name, n_est, n_prev)


def registrar_no_models_registry(supabase, model_name: str, metricas: dict) -> None:
    supabase.table("models_registry").upsert({
        "name": model_name,
        "market": "parametrico_multimercado",
        "algorithm": "catboost_poisson + dixon_coles + negbin_betabinomial",
        "status": "testing",
        "features_used": ml.FEATURES_POR_MODELO[model_name],
        "hyperparameters": ml.PARAMS_DEFAULT[model_name],
        "metrics_test": metricas,
    }, on_conflict="name,market").execute()


# ---------------------------------------------------------------------------
# Avaliação
# ---------------------------------------------------------------------------
def avaliar(
    teste: pd.DataFrame, lam_home: np.ndarray, lam_away: np.ndarray,
    lam_corners: np.ndarray | None, parametros: dict[str, float],
) -> dict:
    """Métrica primária: log-verossimilhança do PLACAR observado.

    Escolhida porque mede o modelo inteiro de uma vez. Log-loss de 1X2 só
    enxerga uma projeção grosseira da distribuição -- dois modelos podem
    empatar no 1X2 e distribuir os placares de forma muito diferente
    dentro de cada resultado, e é essa diferença que decide placar exato,
    handicap e over/under.

    Baselines na mesma escala:
      - climatologia: distribuição empírica de placares do próprio TREINO
        (o "chutar sempre a frequência histórica" do achado #6);
      - Poisson sem covariáveis: λ = média de gols do treino, igual pra
        toda partida. Isola quanto as features de fato agregam.
    """
    valido = teste["home_goals"].notna() & teste["away_goals"].notna()
    idx = np.where(valido.to_numpy())[0]
    hg = teste.loc[valido, "home_goals"].astype(int).to_numpy()
    ag = teste.loc[valido, "away_goals"].astype(int).to_numpy()

    # Métricas por mercado binário (over/under 2.5, BTTS) reaproveitam a
    # MESMA matriz de placares e a MESMA função `mercados_de_gols` que grava
    # as previsões de verdade (`persistir`/`derivar_mercados`) -- fonte
    # única, sem duplicar a lógica de derivação em dois lugares que podem
    # divergir. Só esses dois além do 1X2 porque são os únicos com odds
    # Pinnacle reais em `odds_market` pra comparar/simular carteira (ver
    # MERCADOS_CARTEIRA_SUPORTADOS em api/model-maintenance.js) -- métrica
    # de mercado sem odds pra comparar não tem como validar contra o
    # mercado, só serve de curiosidade.
    log_vero, log_loss_1x2, brier_1x2, acertos = [], [], [], 0
    log_loss_ou25, brier_ou25, acertos_ou25 = [], [], 0
    log_loss_btts, brier_btts, acertos_btts = [], [], 0
    for k, pos in enumerate(idx):
        matriz = dist.matriz_placares(float(lam_home[pos]), float(lam_away[pos]), parametros["rho"])
        log_vero.append(dist.log_verossimilhanca_placar(matriz, hg[k], ag[k]))
        mercados = dist.mercados_de_gols(matriz)

        p_home = float(np.tril(matriz, -1).sum())
        p_draw = float(np.trace(matriz))
        p_away = float(np.triu(matriz, 1).sum())
        probs = np.array([p_home, p_draw, p_away])
        real = 0 if hg[k] > ag[k] else (1 if hg[k] == ag[k] else 2)
        log_loss_1x2.append(-np.log(max(probs[real], 1e-15)))
        alvo = np.zeros(3); alvo[real] = 1.0
        brier_1x2.append(float(np.sum((probs - alvo) ** 2)))
        acertos += int(np.argmax(probs) == real)

        p_over25 = mercados[("over_under_2.5", "over")]
        real_over25 = 1 if (hg[k] + ag[k]) > 2.5 else 0
        log_loss_ou25.append(-np.log(max(p_over25 if real_over25 else 1.0 - p_over25, 1e-15)))
        brier_ou25.append(float((p_over25 - real_over25) ** 2 + ((1.0 - p_over25) - (1 - real_over25)) ** 2))
        acertos_ou25 += int((p_over25 >= 0.5) == bool(real_over25))

        p_btts = mercados[("btts", "yes")]
        real_btts = 1 if (hg[k] > 0 and ag[k] > 0) else 0
        log_loss_btts.append(-np.log(max(p_btts if real_btts else 1.0 - p_btts, 1e-15)))
        brier_btts.append(float((p_btts - real_btts) ** 2 + ((1.0 - p_btts) - (1 - real_btts)) ** 2))
        acertos_btts += int((p_btts >= 0.5) == bool(real_btts))

    metricas = {
        "n_teste": int(len(idx)),
        "log_verossimilhanca_placar": float(np.mean(log_vero)),
        "log_loss_1x2": float(np.mean(log_loss_1x2)),
        "brier_1x2": float(np.mean(brier_1x2)),
        "acuracia_1x2": acertos / max(len(idx), 1),
        "log_loss_over_under_2.5": float(np.mean(log_loss_ou25)),
        "brier_over_under_2.5": float(np.mean(brier_ou25)),
        "acuracia_over_under_2.5": acertos_ou25 / max(len(idx), 1),
        "log_loss_btts": float(np.mean(log_loss_btts)),
        "brier_btts": float(np.mean(brier_btts)),
        "acuracia_btts": acertos_btts / max(len(idx), 1),
        "rho": parametros["rho"],
    }

    if lam_corners is not None and "total_corners" in teste.columns:
        tem = teste["total_corners"].notna().to_numpy()
        if tem.sum() > 0:
            reais = teste.loc[tem, "total_corners"].to_numpy()
            pos_corners = np.where(tem)[0]
            log_vero_c, log_loss_ou = [], []
            for k, pos in enumerate(pos_corners):
                pmf = dist._nb_pmf_vetor(float(lam_corners[pos]), parametros.get("corners_disp_r", 65.0))
                total = int(min(reais[k], len(pmf) - 1))
                log_vero_c.append(np.log(max(pmf[total], 1e-15)))
                p_over = float(pmf[10:].sum())  # over 9.5
                log_loss_ou.append(-np.log(max(p_over if reais[k] > 9.5 else 1 - p_over, 1e-15)))
            metricas["n_teste_corners"] = int(tem.sum())
            metricas["log_verossimilhanca_corners"] = float(np.mean(log_vero_c))
            metricas["log_loss_corners_ou95"] = float(np.mean(log_loss_ou))

    return metricas


def baselines(treino: pd.DataFrame, teste: pd.DataFrame) -> dict:
    """Climatologia e Poisson-sem-covariáveis, na mesma métrica."""
    val_tr = treino["home_goals"].notna() & treino["away_goals"].notna()
    val_te = teste["home_goals"].notna() & teste["away_goals"].notna()
    hg_tr = treino.loc[val_tr, "home_goals"].astype(int).to_numpy()
    ag_tr = treino.loc[val_tr, "away_goals"].astype(int).to_numpy()
    hg_te = teste.loc[val_te, "home_goals"].astype(int).to_numpy()
    ag_te = teste.loc[val_te, "away_goals"].astype(int).to_numpy()

    # Climatologia: frequência empírica de cada placar no treino, com
    # suavização de Laplace pra não atribuir probabilidade 0 a um placar
    # que simplesmente não apareceu no treino (log(0) = -inf).
    n = dist.MAX_GOLS + 1
    freq = np.ones((n, n))
    for h, a in zip(hg_tr, ag_tr):
        freq[min(h, n - 1), min(a, n - 1)] += 1
    freq /= freq.sum()

    # Poisson sem covariáveis: um λ só, a média do treino.
    matriz_poisson = dist.matriz_placares(float(hg_tr.mean()), float(ag_tr.mean()), 0.0)

    return {
        "climatologia_log_verossimilhanca": float(np.mean(
            [np.log(max(freq[min(h, n - 1), min(a, n - 1)], 1e-15)) for h, a in zip(hg_te, ag_te)])),
        "poisson_sem_covariaveis_log_verossimilhanca": float(np.mean(
            [dist.log_verossimilhanca_placar(matriz_poisson, h, a) for h, a in zip(hg_te, ag_te)])),
    }


# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Treina o modelo misto (parâmetros por ML + distribuição).")
    parser.add_argument(
        "--ligas", default="",
        help="Nomes de liga separados por vírgula -- override manual, útil pra teste rápido numa "
             "liga só. Omitido (padrão): resolve o escopo de `leagues.modelo_misto_escopo='treino'`.",
    )
    parser.add_argument(
        "--ligas-extra", default="",
        help="Ligas SEM treino nem avaliação (baixa cobertura de dado, ex.: sem xG real) -- "
             "aplica o modelo já ajustado nas ligas de --ligas e grava as estimativas/mercados "
             "delas também, sem entrar no fit dos regressores nem nas métricas reportadas. "
             "Override manual; omitido (padrão): resolve de `leagues.modelo_misto_escopo='extra'`.",
    )
    parser.add_argument("--sem-gravar", action="store_true", help="Avalia sem escrever no Supabase.")
    args = parser.parse_args()

    supabase = create_client(obter_env("SUPABASE_URL"), obter_env("SUPABASE_KEY"))

    # `--ligas`/`--ligas-extra` continuam existindo como override manual
    # (teste rápido numa liga só), mas o caminho normal é o escopo gravado
    # em `leagues.modelo_misto_escopo` -- reclassificar uma liga vira "mudar
    # uma coluna", não "editar código nem lembrar a string exata no próximo
    # workflow_dispatch". Sem `--ligas` e sem nenhuma liga marcada "treino"
    # ainda, `league_ids` fica `None` e `montar_dataset_ml_empilhado` cai no
    # fallback antigo (`LIGAS_MODEL_BENCHMARKING`), sem quebrar quem já usa
    # o script sem saber da coluna.
    league_ids = None
    if args.ligas:
        nomes = [n.strip() for n in args.ligas.split(",") if n.strip()]
        resposta = supabase.table("leagues").select("id, name").in_("name", nomes).execute()
        league_ids = [linha["id"] for linha in (resposta.data or [])]
        if not league_ids:
            sys.exit(f"Nenhuma liga encontrada com os nomes {nomes}.")
        logger.info("Escopo restrito a %d liga(s) (via --ligas): %s", len(league_ids), nomes)
    else:
        resposta = supabase.table("leagues").select("id, name").eq("modelo_misto_escopo", "treino").execute()
        linhas = resposta.data or []
        league_ids = [linha["id"] for linha in linhas] or None
        if league_ids:
            logger.info("Escopo de treino via `leagues.modelo_misto_escopo='treino'`: %d liga(s): %s",
                        len(league_ids), sorted(l["name"] for l in linhas))

    league_ids_extra = None
    if args.ligas_extra:
        nomes_extra = [n.strip() for n in args.ligas_extra.split(",") if n.strip()]
        resposta_extra = supabase.table("leagues").select("id, name").in_("name", nomes_extra).execute()
        league_ids_extra = [linha["id"] for linha in (resposta_extra.data or [])]
        if not league_ids_extra:
            sys.exit(f"Nenhuma liga 'extra' encontrada com os nomes {nomes_extra}.")
        logger.info("Ligas 'extra' (via --ligas-extra): %d liga(s): %s", len(league_ids_extra), nomes_extra)
    else:
        resposta_extra = supabase.table("leagues").select("id, name").eq("modelo_misto_escopo", "extra").execute()
        linhas_extra = resposta_extra.data or []
        league_ids_extra = [linha["id"] for linha in linhas_extra] or None
        if league_ids_extra:
            logger.info("Ligas 'extra' via `leagues.modelo_misto_escopo='extra'`: %d liga(s): %s",
                        len(league_ids_extra), sorted(l["name"] for l in linhas_extra))

    logger.info("Montando dataset (pode levar alguns minutos)...")
    dataset = dh.montar_dataset_ml_empilhado(supabase, league_ids_manual=league_ids)
    if dataset.empty:
        sys.exit("Dataset vazio -- nada pra treinar.")

    # Ligas "extra": aplicam o modelo (só .predict, nunca .fit) mas nunca
    # entram em treino/calibração/avaliação -- exatamente a mesma função
    # `montar_dataset_ml_empilhado`, sem split nenhum, porque toda a linha
    # dela é inferência. Vazio quando --ligas-extra não é passado.
    #
    # `exigir_forma_minima=False`: o corte padrão descarta partida onde o
    # time não tem forma de gols DENTRO do escopo de liga da chamada -- faz
    # sentido pro dataset de TREINO, mas aqui o escopo é estreito de
    # propósito (só copas/torneios continentais), então um time com
    # histórico de sobra em OUTRA competição (fora deste --ligas-extra)
    # aparecia como "sem histórico" e a partida inteira era descartada, sem
    # gerar estimativa nenhuma -- bug real encontrado via relato de usuário
    # (Copa Sudamericana/Copa do Brasil/FIFA Intercontinental Cup perdendo
    # 25-65% das partidas finalizadas por esse mecanismo).
    dataset_extra = pd.DataFrame()
    if league_ids_extra:
        logger.info("Montando dataset das ligas 'extra'...")
        dataset_extra = dh.montar_dataset_ml_empilhado(
            supabase, league_ids_manual=league_ids_extra, exigir_forma_minima=False
        )
        if dataset_extra.empty:
            logger.warning("Dataset das ligas 'extra' veio vazio -- seguindo só com as ligas de treino.")

    # Jogos FUTUROS (status='scheduled') de qualquer liga em escopo (treino +
    # extra): mesmo espírito das ligas extra -- só .predict com os parâmetros
    # já ajustados, nunca .fit, nunca entram em avaliar()/baselines(). Sem
    # isso, o painel nunca mostra estimativa nenhuma pra uma partida que
    # ainda não aconteceu, mesmo estando numa liga de TREINO (achado real:
    # usuário viu "sem estimativa" numa partida do Brasileirão -- a liga
    # estava certinha no escopo, só que a partida era futura, e
    # `carregar_partidas_finalizadas` só busca `status='finished'`).
    #
    # Forma pré-jogo reaproveita o histórico das ligas de TREINO
    # (`league_ids`) via `match_ids_extra` -- mesmo mecanismo que
    # `estimar_partida_custom.py`/`prever_partidas_futuras_custom.py` já
    # usam pra injetar partida específica de qualquer status no dataset
    # "Feature Stacked" sem esperar `status='finished'`. `exigir_forma_
    # minima=False` pelo mesmo motivo das ligas extra: um time de fora do
    # escopo de treino (ex. clube estreando numa copa continental) pode não
    # ter forma calculável aqui e ainda assim merece estimativa, só com
    # `NaN` nessas colunas -- CatBoost lida nativamente.
    dataset_futuro = pd.DataFrame()
    ligas_com_jogo_futuro = (league_ids or []) + (league_ids_extra or [])
    if ligas_com_jogo_futuro:
        def factory_futuros(inicio, fim):
            return (
                supabase.table("matches")
                .select("id")
                .in_("league_id", ligas_com_jogo_futuro)
                .eq("status", "scheduled")
                .order("id")
                .range(inicio, fim)
            )

        match_ids_futuros = [linha["id"] for linha in dh._paginar(factory_futuros)]
        if match_ids_futuros:
            logger.info("Montando dataset de %d partida(s) futura(s) (status='scheduled')...", len(match_ids_futuros))
            dataset_futuro = dh.montar_dataset_ml_empilhado(
                supabase, league_ids_manual=league_ids, match_ids_extra=match_ids_futuros, exigir_forma_minima=False
            )
            # A chamada acima devolve TODO o histórico das ligas de treino de
            # novo (é o mesmo `league_ids_manual` já usado em `dataset`) --
            # filtra só os jogos futuros pedidos via `match_ids_extra`.
            dataset_futuro = dataset_futuro[dataset_futuro["match_id"].isin(match_ids_futuros)].reset_index(drop=True)
            if dataset_futuro.empty:
                logger.warning("Dataset de jogos futuros veio vazio -- seguindo sem eles.")

    # FEATURES_V10 (default de VARIANTES_GOLS/MODELO_CORNERS, ver modelos_ml.py)
    # referencia nomes de coluna de xG/xGOT do esquema ANTIGO de forma
    # pré-jogo (`media_xg_5j_home` etc., de COLUNAS_FORMA_XG) -- mas
    # montar_dataset_ml_empilhado hoje monta essas colunas via
    # `_forma_por_mando_multi_janelas`, que usa um esquema de nome DIFERENTE
    # (`xg_home_5j` etc.), sem nunca juntar a versão antiga no dataset. Achado
    # real: TODO modelo que usa FEATURES_V2..V10 (inclusive os classificadores
    # v10 estabelecidos) está, sem saber, treinando sem nenhuma feature de xG/
    # xGOT -- mascarado até agora porque `carregar_dataset`
    # (treinar_modelo_custom.py) já descarta silenciosamente feature pedida
    # que não existe no dataset, com só um log de aviso. Esse script standalone
    # não tinha essa proteção e quebrava com KeyError. Aplica o mesmo filtro
    # aqui (mínimo pra destravar o treino agora); a correção de fundo -- portar
    # FEATURES_NUMERICAS pro esquema novo, restaurando xG/xGOT em todos os
    # modelos -- é maior (muda o que TODO modelo já treinado usa) e fica
    # registrada em CONTEXTO_PROJETO.md pra decisão separada.
    colunas_dataset = set(dataset.columns)
    for chave in ("hibrido_gols_v1", "hibrido_gols_xg_v1", "hibrido_corners_v1", "hibrido_gols_lgbm_v1"):
        originais = ml.FEATURES_POR_MODELO.get(chave, [])
        faltando = [f for f in originais if f not in colunas_dataset]
        if faltando:
            logger.warning("[%s] features ausentes no dataset (ignoradas): %s", chave, faltando)
            ml.FEATURES_POR_MODELO[chave] = [f for f in originais if f in colunas_dataset]

    treino, calib, teste = dividir_cronologicamente(dataset)
    logger.info("Split cronológico: treino=%d, calibração=%d, teste=%d (%s → %s)",
                len(treino), len(calib), len(teste),
                teste["match_date"].min(), teste["match_date"].max())

    # Ligas "extra" e jogos futuros entram só como mais itens pra APLICAR o
    # regressor já ajustado (`treinar_lambda_corners`/`treinar_lambdas_gols`
    # fazem .fit em `treino` e .predict em cada item de `aplicar_em` --
    # nenhuma das duas funções precisa mudar de assinatura pra isso).
    # Lista rotulada (não posicional) porque cada item é opcional
    # independente -- um DataFrame vazio quebraria `preparar_liga_para_
    # catboost` (não tem nem a coluna "liga"), então cada um só entra se
    # não estiver vazio.
    extras: list[tuple[str, pd.DataFrame]] = []
    if not dataset_extra.empty:
        extras.append(("extra", dataset_extra))
    if not dataset_futuro.empty:
        extras.append(("futuro", dataset_futuro))

    aplicar_em_corners = [calib, teste] + [d for _, d in extras]
    lam_corners_todos = treinar_lambda_corners(treino, aplicar_em_corners)
    if lam_corners_todos:
        lam_corners_calib, lam_corners_teste, *resto_corners = lam_corners_todos
        lam_corners_por_rotulo = dict(zip((rotulo for rotulo, _ in extras), resto_corners))
    else:
        lam_corners_calib = lam_corners_teste = None
        lam_corners_por_rotulo = {}
    lam_corners_extra = lam_corners_por_rotulo.get("extra")
    lam_corners_futuro = lam_corners_por_rotulo.get("futuro")

    referencias = baselines(treino, teste)
    logger.info("Baselines (log-verossimilhança do placar, maior=melhor): climatologia=%.4f, Poisson sem covariáveis=%.4f",
                referencias["climatologia_log_verossimilhanca"],
                referencias["poisson_sem_covariaveis_log_verossimilhanca"])

    resumo = {}
    for model_name, alvos in VARIANTES_GOLS.items():
        logger.info("\n=== %s ===", model_name)
        aplicar_em_gols = [calib, teste] + [d for _, d in extras]
        lambdas = treinar_lambdas_gols(model_name, alvos, treino, aplicar_em_gols)
        if lambdas is None:
            continue
        (lam_h_calib, lam_a_calib), (lam_h_teste, lam_a_teste), *resto_gols = lambdas
        lam_gols_por_rotulo = dict(zip((rotulo for rotulo, _ in extras), resto_gols))
        lam_h_extra, lam_a_extra = lam_gols_por_rotulo.get("extra", (None, None))
        lam_h_futuro, lam_a_futuro = lam_gols_por_rotulo.get("futuro", (None, None))

        # ρ, dispersão e α/β SÓ vêm da calibração das ligas de treino --
        # nunca recalculados pras ligas extra nem pros jogos futuros. É
        # exatamente essa reutilização que faz a inferência neles ser
        # "sem treinar".
        parametros = ajustar_parametros_estruturais(calib, lam_h_calib, lam_a_calib, lam_corners_calib)
        metricas = avaliar(teste, lam_h_teste, lam_a_teste, lam_corners_teste, parametros)
        metricas.update(referencias)
        resumo[model_name] = metricas

        logger.info("[%s] log-verossimilhança do placar: %.4f | log-loss 1X2: %.4f | Brier 1X2: %.4f | acurácia: %.1f%%",
                    model_name, metricas["log_verossimilhanca_placar"], metricas["log_loss_1x2"],
                    metricas["brier_1x2"], 100 * metricas["acuracia_1x2"])

        if not args.sem_gravar:
            persistir(supabase, model_name, teste, lam_h_teste, lam_a_teste, lam_corners_teste, parametros)
            registrar_no_models_registry(supabase, model_name, metricas)

            if lam_h_extra is not None:
                persistir(supabase, model_name, dataset_extra, lam_h_extra, lam_a_extra, lam_corners_extra, parametros)
                logger.info("[%s] +%d partidas 'extra' persistidas com os parâmetros ajustados nas ligas de treino.",
                            model_name, len(dataset_extra))
            if lam_h_futuro is not None:
                persistir(supabase, model_name, dataset_futuro, lam_h_futuro, lam_a_futuro, lam_corners_futuro, parametros)
                logger.info("[%s] +%d partida(s) futura(s) (status='scheduled') persistidas.",
                            model_name, len(dataset_futuro))
        else:
            if lam_h_extra is not None:
                logger.info("[%s] (--sem-gravar) +%d partidas 'extra' seriam persistidas.", model_name, len(dataset_extra))
            if lam_h_futuro is not None:
                logger.info("[%s] (--sem-gravar) +%d partida(s) futura(s) seriam persistidas.", model_name, len(dataset_futuro))

    print("\n" + "=" * 78)
    print("RESUMO -- log-verossimilhança média do placar observado (maior = melhor)")
    print("=" * 78)
    print(f"  {'climatologia (frequência empírica do treino)':<52} {referencias['climatologia_log_verossimilhanca']:>8.4f}")
    print(f"  {'Poisson sem covariáveis (λ = média do treino)':<52} {referencias['poisson_sem_covariaveis_log_verossimilhanca']:>8.4f}")
    for model_name, m in resumo.items():
        print(f"  {model_name:<52} {m['log_verossimilhanca_placar']:>8.4f}")
    print("\nAtenção ao ler: diferença pequena entre variantes NÃO é evidência de")
    print("vantagem. Comparar contra o mercado devigado e checar IC95% por bootstrap")
    print("antes de promover qualquer modelo (achado #3 do CONTEXTO_PROJETO.md).")
    print(f"\nGerado em {datetime.now(timezone.utc).isoformat()}")
    if args.sem_gravar:
        print("\n(--sem-gravar: nada foi escrito no Supabase.)")
    print(json.dumps(resumo, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
