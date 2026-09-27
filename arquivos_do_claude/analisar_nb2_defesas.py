#!/usr/bin/env python3
"""Testa sobredispersão no mercado de Defesas de goleiro e decide se a
Binomial Negativa (NB2) bate a Poisson pura, com o mesmo rigor estatístico
(MLE + teste de razão de verossimilhança na fronteira, validação OOS) já
exigido neste projeto antes de trocar a família de distribuição de um
mercado em produção (ver `calibrar_disp_r_chutes.py`/`api/corners-model.js`
pra escanteios/faltas, que usam método dos momentos -- aqui é MLE, a pedido
explícito, o que também permite o teste de hipótese formal via LRT que o
método dos momentos não oferece).

Contexto (achado 27/09, `CONTEXTO_PROJETO.md`): o modelo de defesas
(`jogador_defesas_catboost_poisson_v1`) já bate o baseline em RMSE em
100/102 grupos liga×temporada contra `Saves` real de goleiro -- a MÉDIA
está bem estimada. Este script testa a FORMA da distribuição ao redor
dessa média: será que o volume de defesas de um goleiro varia mais, jogo a
jogo, do que uma Poisson (variância = média) permite? Se sim, a probabilidade
de mercado das linhas altas (3+, 4+ defesas) sai sistematicamente errada
mesmo com a média certa -- exatamente o padrão já encontrado e corrigido
pra escanteios/faltas (Fase 6.2 do motor de Markov) e pra chutes de jogador
(achado de 27/09 registrado em `CONTEXTO_PROJETO.md`, "chutes tem um
problema pior nos extremos de volume").

Fonte de dado: `player_match_walkforward`, fonte_titular='relacionados' --
a mesma passada que expôs o bug do gate de posição (achado 27/09: o modelo
nunca viu jogador de linha no treino). Aqui filtramos ADICIONALMENTE por
`match_player_stats_fotmob.is_goalkeeper=true`, então o gate de posição
(zera `lambda_defesas_jogo` pra quem não é goleiro) não afeta nenhuma linha
usada por este script -- os números aqui são os mesmos antes/depois desse
fix. `relacionados` foi escolhida (em vez de `previsto`/`real`) só porque é
a passada com mais observações por goleiro (inclui titular+banco de cada
partida, ~30 mil goleiro-partida vs. ~30 mil também nas outras duas nesta
base específica -- na prática as três dão a mesma população de goleiro real
aqui, mas `relacionados` é a convenção já usada nas duas outras investigações
de 27/09 sobre este mesmo mercado).

Metodologia (pedido explícito do usuário, 27/09):

1. **MLE de α** (parametrização NB2: Var(Y) = μ + α·μ², r = 1/α,
   p = r/(r+μ)) via `scipy.optimize.minimize_scalar`, condicionado no μ de
   CADA observação (nunca a variância agregada da liga -- mistura variação
   entre-jogos, já capturada pelo modelo, com a variação dentro-de-um-jogo
   que α deveria medir; mesmo cuidado de `distribuicoes.
   ajustar_dispersao_nb`). Ajustado GLOBAL e POR LIGA, só nas temporadas de
   TREINO (< `SEASON_CORTE_OOS`) -- nunca vaza temporada de validação pro
   ajuste do parâmetro.
2. **LRT na fronteira** (H0: α=0, que é exatamente o limite do espaço de
   parâmetro `α >= 0`, não um ponto interior): a estatística de razão de
   verossimilhança sob H0 não segue qui-quadrado(1) puro nesse caso -- seu
   nulo assintótico é uma mistura 50/50 de qui-quadrado(0) (massa em 0) e
   qui-quadrado(1) (Self & Liang 1987, caso clássico de teste de variância
   de componente na fronteira). Usar qui-quadrado(1) puro SUPERESTIMARIA a
   evidência contra Poisson (p-valor dobraria o peso da cauda). O p-valor
   correto é `0.5 * (1 - chi2.cdf(LR, df=1))`.
3. **Avaliação por linha de mercado** (over/under 1.5/2.5/3.5/4.5) nas
   temporadas OOS (>= `SEASON_CORTE_OOS`), usando o α ajustado só em
   treino -- ΔLog-loss e ΔBrier (NB2 − Poisson) por linha.
4. **Calibração por decil** nas caudas altas (Over 3.5/4.5): divide o μ OOS
   em 10 decis e compara, por decil, a frequência real de "over" contra a
   probabilidade média prevista por Poisson e por NB2 (razão O/E).

Persistência: só grava `disp_r` (=1/α) em `league_model_params`
(`model_name='jogador_defesas_catboost_poisson_v1'`, `stat='defesas'`,
`param_name='disp_r'`) pra ligas que passam nos 3 critérios -- `n >=
N_MINIMO_LIGA`, LRT rejeita Poisson (`p < 0.05`) e `α > ALPHA_MINIMO`
(sem isso, seria só uma NB numericamente instável fingindo ser Poisson,
r->∞). Liga que não passa fica SEM linha na tabela -- o mesmo "fallback
gracioso" que `dist.mercados_de_defesas`/`dist._nb_pmf_vetor` já degradam
sozinhos pra Poisson quando não encontram `disp_r`, sem precisar de um
valor "chutado" (mesmo padrão de `calibrar_disp_r_chutes.py`, que também
não persiste nada pra liga subdispersa).

Fica em `arquivos_do_claude/` (fora do deploy/CI) porque é recalibração
manual/esporádica -- mesmo lugar que os outros `calibrar_disp_r_*.py`.

Uso:
    set SUPABASE_URL=...
    set SUPABASE_KEY=sua_service_role_key
    python analisar_nb2_defesas.py
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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import distribuicoes as dist  # noqa: E402
import treinar_modelo_jogador_mercados as tmj  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
logger = logging.getLogger("analisar_nb2_defesas")

MODEL_NAME = "jogador_defesas_catboost_poisson_v1"
TAMANHO_PAGINA = 1000
TAMANHO_LOTE_IDS = 500
N_MINIMO_LIGA = 300
# Temporadas >= este valor viram validação fora da amostra (OOS); < este
# valor calibra alpha. Split fixo (não walk-forward incremental) porque o
# alvo aqui é o parâmetro de FORMA da distribuição (estável ao longo do
# tempo, ao contrário de lambda por jogador) -- não precisa reajustar a
# cada temporada, só provar que generaliza pra temporadas futuras.
SEASON_CORTE_OOS = "2025"
LINHAS_MERCADO = (1.5, 2.5, 3.5, 4.5)
LINHAS_CAUDA_ALTA = (3.5, 4.5)
# alpha <= isso não é superdispersão real (ruído numérico da otimização
# em torno de 0) -- mesmo limiar pedido explicitamente pro fallback
# gracioso na precificação (r = 1/alpha explodiria pra >100.000).
ALPHA_MINIMO = 1e-5


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
    """(league_id, season, mu=lambda_defesas_jogo, y=Saves real), só goleiro
    de verdade (`match_player_stats_fotmob.is_goalkeeper=true`) -- o gate de
    posição (achado 27/09) zera `lambda_defesas_jogo` pra jogador de linha
    na passada 'relacionados', mas esse filtro aqui é redundante-e-seguro:
    garante que a população desta análise é goleiro real com ou sem o gate
    já aplicado no ambiente de onde os dados vêm.

    `Saves` extraído via `tmj.extrair_saves_stats_raw` -- MESMA função usada
    no treino do modelo (`treinar_modelo_jogador_mercados.carregar_dados`),
    nunca reimplementada aqui, pra nunca divergir da definição de alvo que o
    modelo realmente aprendeu."""
    ligas = supabase.table("leagues").select("id").execute().data or []
    linhas_saida: list[dict] = []

    for liga in ligas:
        league_id = liga["id"]
        previstos = _paginar(lambda ini, fim, lg=league_id: (
            supabase.table("player_match_walkforward")
            .select("match_id, player_id, season, lambda_defesas_jogo")
            .eq("league_id", lg)
            .eq("fonte_titular", "relacionados")
            .not_.is_("lambda_defesas_jogo", "null")
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
                .select("match_id, player_id, stats_raw")
                .in_("match_id", lt)
                .eq("is_goalkeeper", True)
                .order("id")
                .range(ini, fim)
            ))
            for s in stats:
                valor = tmj.extrair_saves_stats_raw(s.get("stats_raw"))
                if valor == valor:  # not NaN
                    reais[(s["match_id"], s["player_id"])] = valor

        for p in previstos:
            chave = (p["match_id"], p["player_id"])
            if chave not in reais:
                continue
            mu = float(p["lambda_defesas_jogo"])
            # ACHADO 27/09 (esta análise): o gate de posição em
            # backtest_jogador_mercados_walkforward.py/rodar_jogador_
            # mercados_previsto.py (`posicao_num != 0 -> lambda=0.0`, ver
            # CONTEXTO_PROJETO.md) trava em 0 ~0,19% dos goleiros REAIS
            # cujo `players.usual_position_id` não é 0 (dado cadastral
            # errado/desatualizado -- não é o mesmo bug do jogador de linha
            # que motivou o gate). `mu=0` com `y` real positivo (visto até
            # 7 defesas) não é "modelo previu perto de zero" -- é o gate
            # tendo confundido esse goleiro com jogador de linha. Incluir
            # esses pontos envenenaria a log-verossimilhança pra QUALQUER
            # alpha (P(Y=y>0 | média=0) = 0 em Poisson e em NB2), travando
            # a otimização na fronteira superior sem sinal nenhum -- não é
            # sobredispersão real, é lambda substituído por zero por engano.
            # Exclusão documentada, não "dado ruim descartado sem explicação".
            if mu <= 0:
                continue
            linhas_saida.append({"league_id": league_id, "season": p["season"], "mu": mu, "y": reais[chave]})
        logger.info("liga=%s: %d goleiro-partida com Saves real", league_id, sum(1 for lx in linhas_saida if lx["league_id"] == league_id))

    return pd.DataFrame(linhas_saida)


# ---------------------------------------------------------------------------
# MLE + LRT na fronteira
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
    """MLE de alpha (NB2) por otimização escalar limitada + LRT na fronteira
    contra Poisson (H0: alpha=0). Ver docstring do módulo pra por que o
    p-valor usa a mistura 50/50 qui-quadrado(0)/qui-quadrado(1), não
    qui-quadrado(1) puro."""
    mu, y = np.asarray(mu, dtype=float), np.asarray(y, dtype=float)
    resultado = minimize_scalar(_neg_log_lik_nb2, args=(mu, y), bounds=(1e-8, 50.0), method="bounded")
    alpha_hat = float(resultado.x)
    log_lik_nb2 = -float(resultado.fun)
    log_lik_poisson = _log_lik_poisson(mu, y)
    # MLE nunca piora a verossimilhança vs. o ponto de fronteira (Poisson é
    # o caso alpha->0 da mesma família) -- LR negativo só pode ser ruído
    # numérico do otimizador perto da fronteira, trava em 0.
    lr = max(2.0 * (log_lik_nb2 - log_lik_poisson), 0.0)
    p_valor = 0.5 * (1.0 - chi2.cdf(lr, df=1))
    return {
        "n": len(y), "alpha": alpha_hat, "r": (1.0 / alpha_hat) if alpha_hat > ALPHA_MINIMO else float("inf"),
        "log_lik_poisson": log_lik_poisson, "log_lik_nb2": log_lik_nb2,
        "lr_estatistica": lr, "p_valor_lrt": p_valor, "rejeita_poisson": bool(p_valor < 0.05),
    }


# ---------------------------------------------------------------------------
# Avaliação por linha de mercado (OOS)
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
    """Delta Log-loss/Brier (NB2 - Poisson) por linha de corte, OOS. alpha
    negativo em relação à Poisson (Delta < 0) significa que a NB2 melhora."""
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
    """Observado/Esperado por decil de mu (OOS), Poisson vs. NB2, pra uma
    linha específica -- pensado pras caudas altas (3.5/4.5), onde a média
    geral do achado #3 pode esconder erro concentrado só nos goleiros de
    maior volume esperado."""
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
            "league_id": league_id, "model_name": MODEL_NAME, "stat": "defesas",
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

    logger.info("Carregando defesas walk-forward (fonte='relacionados', só goleiro real)...")
    df = carregar_dados(supabase)
    if df.empty:
        sys.exit("Nenhuma linha carregada -- rode backtest_jogador_mercados_walkforward.yml primeiro.")
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
