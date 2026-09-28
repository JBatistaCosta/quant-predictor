#!/usr/bin/env python3
"""Testa sobredispersão no mercado de Assistências e decide se a Binomial
Negativa (NB2) bate a Poisson pura, com o mesmo rigor estatístico (MLE +
teste de razão de verossimilhança na fronteira, validação OOS) já aplicado
ao mercado de Defesas de goleiro (`analisar_nb2_defesas.py`) nesta sessão.

Contexto (achado 26-27/09, `CONTEXTO_PROJETO.md`): a linha "+1" de
assistência (P(assists>=1)) já tem calibração validada e correta (Bernoulli,
ligação cloglog, `lambdaAssistenciaCalibrado` em `AnaliseAvancadaEvento.jsx`)
-- mas a linha "+2" reusa o mesmo λ* corrigido dentro de uma Poisson pura, e
uma checagem ad-hoc encontrou razão observado/Poisson de 1,1x-1,5x pra "2+"
e 1,2x-2,7x pra "3+" em quase toda faixa de λ, na direção de a Poisson
SUBESTIMAR a cauda -- mesmo padrão de subdispersão já corrigido pra
escanteios/faltas no motor de Markov (Fase 6.2) e pra defesas de goleiro
nesta sessão. Reconfirmado nesta análise via SQL direto (Pearson global
~1,00 -- não capturado pelo teste agregado -- mas O/E > 1 em TODOS os 10
decis de μ pra "2+", n=300.995 só na temporada 2025, então não é ruído de
decil isolado).

Diferença chave vs. `analisar_nb2_defesas.py`: aqui μ NÃO é a saída direta
de um regressor Poisson -- é `lambda_xa_jogo` (regressor RMSE contra xA
contínuo) já passado pela calibração cloglog de 2 parâmetros
(`lambdaAssistenciaCalibrado`, ver `CALIBRACAO_XA_ASSISTENCIA` abaixo, que
precisa ficar SINCRONIZADA com a mesma constante em `AnaliseAvancadaEvento.
jsx` -- nunca diverge sem atualizar as duas). O α medido aqui é a
sobredispersão que sobra depois que a MÉDIA já foi corrigida -- não confundir
com o "λ cru superestima 10%" já resolvido por essa calibração.

Fonte de dado: `player_match_walkforward`, `fonte_titular IN ('real',
'previsto')` -- as duas fontes de titularidade que a calibração cloglog já
cobre (cada uma com seus próprios coeficientes `a,b`). POOLING as duas no
mesmo ajuste de α: como cada linha já usa o λ* calibrado pra sua própria
fonte, a suposição é que o α de FORMA que sobra é o mesmo nas duas (não há
coluna `fonte_titular` em `league_model_params`/`disp_r` hoje, e os demais
mercados NB -- chutes/chutes_no_alvo -- já são fonte-agnósticos do mesmo
jeito). Sem goleiro (mesma convenção de
`validar_assistencia_jogador_walkforward.py`) e só jogador que entrou em
campo (`match_player_stats_fotmob.minutes_played>0`). `assists` tratado como
0 quando NULL com minutos>0 -- mesmo bug de "campo em branco = zero" já
corrigido pra xA/chutes totais (FotMob omite o stat do JSON quando é zero),
confirmado nesta sessão via SQL (64.040 linhas com minutos>0 e assists NULL).

Metodologia: idêntica a `analisar_nb2_defesas.py` (MLE de α por
`scipy.optimize.minimize_scalar`, LRT na fronteira com a mistura 50/50
qui-quadrado(0)/qui-quadrado(1), avaliação OOS por linha, calibração por
decil) -- ver a docstring daquele script pra justificativa completa de cada
etapa, não duplicada aqui.

Persistência: `disp_r` (=1/α) em `league_model_params`
(`model_name='jogador_assistencias_negbin_v1'`, `stat='assistencias'`,
`param_name='disp_r'`) só pra liga que passa nos 3 critérios (`n >=
N_MINIMO_LIGA`, LRT rejeita Poisson, `α > ALPHA_MINIMO`) -- nome de modelo
segue a convenção `jogador_<mercado>_negbin_v1` já usada por chutes/chutes_
no_alvo (`jogador_chutes_negbin_v1`/`jogador_chutes_no_alvo_negbin_v1`), não
a de defesas (`jogador_defesas_catboost_poisson_v1`), porque aqui μ não é a
saída direta de um único modelo de ML -- é a saída de uma calibração de
mercado (cloglog) que pode evoluir independente do regressor de xA.

Uso:
    set SUPABASE_URL=...
    set SUPABASE_KEY=sua_service_role_key
    python analisar_nb2_assistencias.py
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Callable

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar
from scipy.stats import chi2, nbinom, poisson
from supabase import create_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
logger = logging.getLogger("analisar_nb2_assistencias")

MODEL_NAME = "jogador_assistencias_negbin_v1"
STAT = "assistencias"
TAMANHO_PAGINA = 1000
# ACHADO 28/09 (rodando este script via workflow_dispatch em produção): com
# TAMANHO_LOTE_IDS=500 (mesmo valor de analisar_nb2_defesas.py), a query de
# match_player_stats_fotmob por `match_id IN (...) AND minutes_played > 0`
# estourou o statement_timeout do PostgREST. Causa raiz real: estatísticas
# desatualizadas da tabela (ANALYZE trouxe o custo do plano de >1.000.000
# pra ~30.000, corrigido em produção) -- mas mesmo com o plano certo, 500
# partidas de uma vez ainda levam ~7s com cache frio (EXPLAIN ANALYZE),
# perto demais do limite real (~8s). Diferente de defesas, que filtra
# `is_goalkeeper=True` (poucas linhas por partida) -- aqui o filtro é só
# `minutes_played>0` (quase todo mundo que jogou), então cada partida traz
# MUITO mais linhas. Lote menor reduz a margem de risco proporcionalmente.
TAMANHO_LOTE_IDS = 150
N_MINIMO_LIGA = 300
# Mesmo racional de analisar_nb2_defesas.py: split fixo por temporada (não
# walk-forward incremental) porque o alvo é o parâmetro de FORMA (estável),
# não o λ por jogador (esse já é walk-forward desde a Frente A).
SEASON_CORTE_OOS = "2025"
LINHAS_MERCADO = (1.5, 2.5, 3.5)
LINHAS_CAUDA_ALTA = (2.5, 3.5)
# alpha <= isso não é superdispersão real (ruído numérico da otimização em
# torno de 0) -- mesmo limiar usado no fallback gracioso da precificação.
ALPHA_MINIMO = 1e-5

# ESPELHA `CALIBRACAO_XA_ASSISTENCIA` em src/pages/AnaliseAvancadaEvento.jsx
# -- nunca mudar aqui sem mudar lá (e vice-versa). Ver a entrada de
# 26/09 em CONTEXTO_PROJETO.md pra origem desses coeficientes.
CALIBRACAO_XA_ASSISTENCIA = {
    "real": (-0.223, 0.922),
    "previsto": (-0.189, 0.925),
}


def lambda_assistencia_calibrado(lambda_xa: float, fonte_titular: str) -> float:
    if lambda_xa is None or lambda_xa <= 0:
        return lambda_xa
    a, b = CALIBRACAO_XA_ASSISTENCIA.get(fonte_titular, CALIBRACAO_XA_ASSISTENCIA["previsto"])
    return float(np.exp(a) * (lambda_xa ** b))


def obter_env(nome: str) -> str:
    valor = os.environ.get(nome)
    if not valor:
        sys.exit(f"Configure {nome} antes de rodar.")
    return valor


def _paginar(query_builder_factory: Callable[[int, int], object], tamanho_pagina: int = TAMANHO_PAGINA) -> list[dict]:
    """Mesma lógica de `dados_historicos._paginar` -- contorna o corte
    silencioso de 1000 linhas do PostgREST/Supabase por requisição."""
    todas: list[dict] = []
    pagina = 0
    while True:
        inicio, fim = pagina * tamanho_pagina, pagina * tamanho_pagina + tamanho_pagina - 1
        linhas = query_builder_factory(inicio, fim).execute().data or []
        todas.extend(linhas)
        if len(linhas) < tamanho_pagina:
            break
        pagina += 1
    return todas


def carregar_dados(supabase) -> pd.DataFrame:
    """(league_id, season, mu=λ* calibrado, y=assists real) pooling
    fonte_titular in ('real', 'previsto'). `assists` vira 0 quando NULL com
    minutos jogados > 0 (mesmo bug de "campo em branco = zero" já corrigido
    pra xA/chutes totais -- FotMob omite o stat do JSON quando é zero, não é
    dado faltante de verdade)."""
    ligas = supabase.table("leagues").select("id").execute().data or []
    linhas_saida: list[dict] = []

    for liga in ligas:
        league_id = liga["id"]
        previstos = _paginar(lambda ini, fim, lg=league_id: (
            supabase.table("player_match_walkforward")
            .select("match_id, player_id, season, fonte_titular, lambda_xa_jogo")
            .eq("league_id", lg)
            .in_("fonte_titular", ["real", "previsto"])
            .not_.is_("lambda_xa_jogo", "null")
            .order("id")
            .range(ini, fim)
        ))
        if not previstos:
            continue

        match_ids = sorted({p["match_id"] for p in previstos})
        reais: dict[tuple[int, int], float] = {}
        for inicio in range(0, len(match_ids), TAMANHO_LOTE_IDS):
            lote = match_ids[inicio: inicio + TAMANHO_LOTE_IDS]
            stats = _paginar(lambda ini, fim, lt=lote: (
                supabase.table("match_player_stats_fotmob")
                .select("match_id, player_id, assists, minutes_played, is_goalkeeper")
                .in_("match_id", lt)
                .gt("minutes_played", 0)
                .order("id")
                .range(ini, fim)
            ))
            for s in stats:
                if bool(s.get("is_goalkeeper")):
                    continue
                reais[(s["match_id"], s["player_id"])] = float(s.get("assists") or 0.0)

        n_antes = len(linhas_saida)
        for p in previstos:
            chave = (p["match_id"], p["player_id"])
            if chave not in reais:
                continue
            mu = lambda_assistencia_calibrado(float(p["lambda_xa_jogo"]), p["fonte_titular"])
            # Mesma poda de analisar_nb2_defesas.py: mu<=0 com y real
            # positivo envenenaria a log-verossimilhança pra qualquer alpha
            # (P(Y=y>0 | média=0) = 0 tanto em Poisson quanto em NB2).
            if mu is None or mu <= 0:
                continue
            linhas_saida.append({"league_id": league_id, "season": p["season"], "mu": mu, "y": reais[chave]})
        logger.info("liga=%s: %d jogador-partida com assistência real", league_id, len(linhas_saida) - n_antes)

    return pd.DataFrame(linhas_saida)


# ---------------------------------------------------------------------------
# MLE + LRT na fronteira (idêntico a analisar_nb2_defesas.py)
# ---------------------------------------------------------------------------
def _log_lik_poisson(mu: np.ndarray, y: np.ndarray) -> float:
    return float(np.sum(poisson.logpmf(y, mu)))


def _neg_log_lik_nb2(alpha: float, mu: np.ndarray, y: np.ndarray) -> float:
    if alpha <= 0:
        return -_log_lik_poisson(mu, y)
    r = 1.0 / alpha
    p = r / (r + mu)
    return -float(np.sum(nbinom.logpmf(y, r, p)))


def estimar_alpha_mle(mu: np.ndarray, y: np.ndarray) -> dict:
    """MLE de alpha (NB2) + LRT na fronteira contra Poisson (H0: alpha=0).
    Ver docstring de analisar_nb2_defesas.py pra por que o p-valor usa a
    mistura 50/50 qui-quadrado(0)/qui-quadrado(1), não qui-quadrado(1) puro."""
    mu, y = np.asarray(mu, dtype=float), np.asarray(y, dtype=float)
    resultado = minimize_scalar(_neg_log_lik_nb2, args=(mu, y), bounds=(1e-8, 50.0), method="bounded")
    alpha_hat = float(resultado.x)
    log_lik_nb2 = -float(resultado.fun)
    log_lik_poisson = _log_lik_poisson(mu, y)
    lr = max(2.0 * (log_lik_nb2 - log_lik_poisson), 0.0)
    p_valor = 0.5 * (1.0 - chi2.cdf(lr, df=1))
    return {
        "n": len(y), "alpha": alpha_hat, "r": (1.0 / alpha_hat) if alpha_hat > ALPHA_MINIMO else float("inf"),
        "log_lik_poisson": log_lik_poisson, "log_lik_nb2": log_lik_nb2,
        "lr_estatistica": lr, "p_valor_lrt": p_valor, "rejeita_poisson": bool(p_valor < 0.05),
    }


# ---------------------------------------------------------------------------
# Avaliação por linha de mercado (OOS) -- idêntico a analisar_nb2_defesas.py
# ---------------------------------------------------------------------------
def _prob_over_poisson(mu: np.ndarray, k_floor: int) -> np.ndarray:
    return 1.0 - poisson.cdf(k_floor, mu)


def _prob_over_nb2(mu: np.ndarray, k_floor: int, alpha: float) -> np.ndarray:
    if alpha <= ALPHA_MINIMO:
        return _prob_over_poisson(mu, k_floor)
    r = 1.0 / alpha
    p = r / (r + mu)
    return nbinom.sf(k_floor, r, p)


def _log_loss(p: np.ndarray, y: np.ndarray) -> float:
    eps = 1e-12
    p = np.clip(p, eps, 1 - eps)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def _brier(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def avaliar_linhas(mu: np.ndarray, y: np.ndarray, alpha: float, linhas: tuple[float, ...] = LINHAS_MERCADO) -> list[dict]:
    mu, y = np.asarray(mu, dtype=float), np.asarray(y, dtype=float)
    resultados = []
    for linha in linhas:
        k = int(np.floor(linha))
        y_over = (y > k).astype(float)
        p_poisson = _prob_over_poisson(mu, k)
        p_nb2 = _prob_over_nb2(mu, k, alpha)
        ll_poisson, ll_nb2 = _log_loss(p_poisson, y_over), _log_loss(p_nb2, y_over)
        br_poisson, br_nb2 = _brier(p_poisson, y_over), _brier(p_nb2, y_over)
        resultados.append({
            "linha": linha, "n": len(y), "taxa_over_real": float(y_over.mean()),
            "log_loss_poisson": ll_poisson, "log_loss_nb2": ll_nb2, "delta_log_loss": ll_nb2 - ll_poisson,
            "brier_poisson": br_poisson, "brier_nb2": br_nb2, "delta_brier": br_nb2 - br_poisson,
        })
    return resultados


def calibracao_por_decil(mu: np.ndarray, y: np.ndarray, linha: float, alpha: float, n_decis: int = 10) -> pd.DataFrame:
    k = int(np.floor(linha))
    mu, y = np.asarray(mu, dtype=float), np.asarray(y, dtype=float)
    y_over = (y > k).astype(float)
    p_poisson = _prob_over_poisson(mu, k)
    p_nb2 = _prob_over_nb2(mu, k, alpha)

    df = pd.DataFrame({"mu": mu, "y_over": y_over, "p_poisson": p_poisson, "p_nb2": p_nb2})
    df["decil"] = pd.qcut(df["mu"], n_decis, labels=False, duplicates="drop")

    agregado = df.groupby("decil").agg(
        n=("mu", "size"), mu_medio=("mu", "mean"), observado=("y_over", "mean"),
        esperado_poisson=("p_poisson", "mean"), esperado_nb2=("p_nb2", "mean"),
    ).reset_index()
    agregado["oe_poisson"] = agregado["observado"] / agregado["esperado_poisson"].replace(0, np.nan)
    agregado["oe_nb2"] = agregado["observado"] / agregado["esperado_nb2"].replace(0, np.nan)
    return agregado


# ---------------------------------------------------------------------------
# Persistência
# ---------------------------------------------------------------------------
def persistir(supabase, por_liga: dict[int, dict]) -> None:
    linhas = [
        {
            "league_id": league_id, "model_name": MODEL_NAME, "stat": STAT,
            "param_name": "disp_r", "param_value": round(info["r"], 5),
        }
        for league_id, info in por_liga.items()
        if info["n"] >= N_MINIMO_LIGA and info["rejeita_poisson"] and info["alpha"] > ALPHA_MINIMO
    ]
    if not linhas:
        logger.warning("Nenhuma liga passou nos 3 critérios (n, LRT, alpha) -- nada gravado, tudo fica em Poisson puro.")
        return
    supabase.table("league_model_params").upsert(
        linhas, on_conflict="league_id,model_name,stat,param_name"
    ).execute()
    logger.info("%d disp_r gravados em league_model_params.", len(linhas))


# ---------------------------------------------------------------------------
# Relatório
# ---------------------------------------------------------------------------
def imprimir_resumo(global_mle: dict, por_liga: dict[int, dict], resultados_linhas: list[dict], calibs: dict[float, pd.DataFrame]) -> None:
    print("\n=== Sobredispersão (MLE, treino < temporada %s) ===" % SEASON_CORTE_OOS)
    print(f"GLOBAL: n={global_mle['n']} alpha={global_mle['alpha']:.5f} r={global_mle['r']:.2f} "
          f"LR={global_mle['lr_estatistica']:.2f} p={global_mle['p_valor_lrt']:.2e} "
          f"rejeita_Poisson={global_mle['rejeita_poisson']}")
    print(f"\n{'liga':>6}  {'n':>7}  {'alpha':>9}  {'r':>9}  {'LR':>9}  {'p_valor':>10}  {'rejeita_Poisson':>16}")
    for league_id, info in sorted(por_liga.items()):
        r_fmt = f"{info['r']:.2f}" if np.isfinite(info["r"]) else "inf"
        print(f"{league_id:>6}  {info['n']:>7}  {info['alpha']:>9.5f}  {r_fmt:>9}  "
              f"{info['lr_estatistica']:>9.2f}  {info['p_valor_lrt']:>10.2e}  {str(info['rejeita_poisson']):>16}")

    print(f"\n=== Avaliação por linha (OOS, temporada >= {SEASON_CORTE_OOS}, alpha GLOBAL) ===")
    print(f"{'linha':>6}  {'n':>7}  {'taxa_over':>10}  {'LL_poisson':>11}  {'LL_nb2':>9}  {'d_LL':>9}  "
          f"{'Brier_poisson':>14}  {'Brier_nb2':>10}  {'d_Brier':>9}")
    for r in resultados_linhas:
        print(f"{r['linha']:>6.1f}  {r['n']:>7}  {r['taxa_over_real']:>10.4f}  {r['log_loss_poisson']:>11.4f}  "
              f"{r['log_loss_nb2']:>9.4f}  {r['delta_log_loss']:>9.4f}  {r['brier_poisson']:>14.4f}  "
              f"{r['brier_nb2']:>10.4f}  {r['delta_brier']:>9.4f}")

    for linha, calib in calibs.items():
        print(f"\n=== Calibração por decil de mu -- Over {linha} (OOS) ===")
        print(f"{'decil':>5}  {'n':>6}  {'mu_medio':>9}  {'observado':>10}  {'O/E_poisson':>12}  {'O/E_nb2':>9}")
        for _, row in calib.iterrows():
            print(f"{int(row['decil']):>5}  {int(row['n']):>6}  {row['mu_medio']:>9.3f}  {row['observado']:>10.4f}  "
                  f"{row['oe_poisson']:>12.3f}  {row['oe_nb2']:>9.3f}")


def main() -> None:
    supabase = create_client(obter_env("SUPABASE_URL"), obter_env("SUPABASE_KEY"))

    logger.info("Carregando assistências walk-forward (fonte in real/previsto, sem goleiro)...")
    df = carregar_dados(supabase)
    if df.empty:
        sys.exit("Nenhuma linha carregada -- confirme que player_match_walkforward.lambda_xa_jogo está populado.")
    logger.info("%d observações, %d ligas, temporadas %s-%s", len(df), df["league_id"].nunique(), df["season"].min(), df["season"].max())

    treino = df[df["season"] < SEASON_CORTE_OOS]
    oos = df[df["season"] >= SEASON_CORTE_OOS]
    logger.info("Treino (calibra alpha): %d linhas. OOS (valida): %d linhas.", len(treino), len(oos))
    if treino.empty or oos.empty:
        sys.exit(f"Split treino/OOS vazio em SEASON_CORTE_OOS={SEASON_CORTE_OOS} -- ajuste a constante.")

    global_mle = estimar_alpha_mle(treino["mu"].to_numpy(), treino["y"].to_numpy())

    por_liga: dict[int, dict] = {}
    for league_id, g in treino.groupby("league_id"):
        if len(g) < N_MINIMO_LIGA:
            continue
        por_liga[int(league_id)] = estimar_alpha_mle(g["mu"].to_numpy(), g["y"].to_numpy())

    resultados_linhas = avaliar_linhas(oos["mu"].to_numpy(), oos["y"].to_numpy(), global_mle["alpha"])
    calibs = {linha: calibracao_por_decil(oos["mu"].to_numpy(), oos["y"].to_numpy(), linha, global_mle["alpha"]) for linha in LINHAS_CAUDA_ALTA}

    imprimir_resumo(global_mle, por_liga, resultados_linhas, calibs)

    ganho_agregado = any(r["delta_log_loss"] < 0 for r in resultados_linhas)
    if global_mle["rejeita_poisson"] and ganho_agregado:
        logger.info("NB2 rejeita Poisson (LRT) E melhora Log-loss OOS em ao menos uma linha -- persistindo disp_r por liga.")
        persistir(supabase, por_liga)
    else:
        logger.warning(
            "NB2 NÃO demonstrou ganho estatístico suficiente (rejeita_Poisson=%s, alguma linha melhora=%s) -- "
            "nada gravado, mercado continua em Poisson pura.", global_mle["rejeita_poisson"], ganho_agregado,
        )


if __name__ == "__main__":
    main()
