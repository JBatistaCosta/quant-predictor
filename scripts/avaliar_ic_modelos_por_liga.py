#!/usr/bin/env python3
"""IC95% por bootstrap do log-loss/acurácia de cada modelo, por liga -- responde
"essa diferença entre modelos é real ou é ruído de amostra pequena?" pro
ranking "melhor modelo por liga" que `api/model-stats.js`/`ModelosStats.jsx`
já mostram (log-loss/Brier/acurácia puros, sem intervalo de confiança).

Motivação (CONTEXTO_PROJETO.md, achado #27): investigando por que o modelo
misto (`hibrido_gols_v1`/`hibrido_gols_xg_v1`) aparecia como "melhor" em
Bundesliga/Champions League/Copa Libertadores no mercado Over/Under 2.5,
um bootstrap ad-hoc (2000 reamostragens, mesmo espírito de
`backtest_kelly.comparar_pareado_com_mercado` -- só que comparando MODELOS
entre si, não modelo-vs-mercado) mostrou que a "vitória" na Bundesliga não
resiste ao IC95% (sobreposição quase total com os classificadores) e que as
duas variantes do próprio híbrido trocam de posição entre ligas (sinal de
ruído, não de edge estrutural). Este script generaliza aquele bootstrap
ad-hoc pra QUALQUER (model_name, market, league_id) com amostra suficiente,
persistindo o resultado em `model_stats_ic` (migration
<TIMESTAMP>_model_stats_ic.sql) -- mesmo padrão de tabela pré-calculada já
usado por `model_stats_resumo` (migration 20260825001000), só que aqui o
bootstrap não cabe numa agregação SQL/plpgsql (precisa reamostrar linha a
linha com reposição), por isso um script Python em vez de uma função
`recalcular_*` chamada via RPC.

Descoberta dos combos a avaliar: reaproveita `model_stats_resumo` (já lista
todo model_name presente em cada mercado, tabela pequena) em vez de fazer
`SELECT DISTINCT` em `model_predictions` (5,2M+ linhas) -- mesma
combinação que o painel `/modelos` já trata como "existe dado suficiente
pra mostrar". Puxa as previsões de UM (model_name, market) por vez (só
~102 combinações no total hoje, bem menos que as ~730 linhas de
model_stats_resumo, que já é por LIGA) e agrupa por liga em memória --
evita repetir a mesma busca de `model_predictions` uma vez por liga.

Resultado real: gols reais (`matches.home_goals/away_goals`) pra 1X2/
Over-Under 2.5, total de escanteios (`match_stats_fotmob`, mesma fonte de
`avaliar_modelo_misto_vs_mercado._carregar_resultados_reais`) pro mercado
de escanteios.

Amostra mínima de 30 partidas por grupo (mesmo corte de
`avaliar_modelo_misto_vs_mercado.py`) -- abaixo disso nem tenta, o IC seria
tão largo que não diria nada.

Não grava nada em `model_predictions`/`matches` -- só leitura desses,
escrita em `model_stats_ic`.

Uso:
    set SUPABASE_URL=...
    set SUPABASE_KEY=sua_service_role_key
    python scripts/avaliar_ic_modelos_por_liga.py
    python scripts/avaliar_ic_modelos_por_liga.py --mercados "1X2,over_under_2.5"  # subconjunto, teste rápido
    python scripts/avaliar_ic_modelos_por_liga.py --sem-gravar                     # só imprime, não escreve
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

import numpy as np
import pandas as pd
from supabase import create_client

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dados_historicos as dh

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
logger = logging.getLogger("avaliar_ic_modelos_por_liga")

# Seleções esperadas por mercado -- usado só pra validar que uma previsão tem
# TODAS as seleções antes de entrar no cálculo (favorito/log-loss exigem o
# conjunto completo; uma seleção faltando indicaria dado incompleto, não um
# jogo sem previsão nenhuma).
MERCADOS = {
    "1X2": ["home", "draw", "away"],
    "over_under_2.5": ["over", "under"],
    "corners_over_under_9.5": ["over", "under"],
}

AMOSTRA_MINIMA = 30
N_REAMOSTRAGENS = 2000
SEED = 42
LOTE_UPSERT = 500


def obter_env(nome: str) -> str:
    valor = os.environ.get(nome)
    if not valor:
        sys.exit(f"Configure {nome} antes de rodar.")
    return valor


def _modelos_por_mercado(supabase, mercado: str) -> list[str]:
    linhas = dh._paginar(
        lambda inicio, fim: supabase.table("model_stats_resumo").select("model_name").eq("market", mercado).order("model_name").range(inicio, fim)
    )
    return sorted({l["model_name"] for l in linhas})


def _paginar_predicoes(supabase, model_name: str, mercado: str) -> dict[int, dict[str, float]]:
    """`model_predictions` (match_id, selection, probability) filtrado por
    model_name/market, paginado por KEYSET composto (match_id, selection) --
    mesmo padrão de `avaliar_modelo_misto_vs_mercado._carregar_predicoes`
    (índice `(model_name, market, match_id)`, achado #19, casa com
    `ORDER BY match_id`; OFFSET nessa mesma tabela já estourou
    `statement_timeout` em produção, ver mesmo achado)."""
    predicoes: dict[int, dict[str, float]] = {}
    cursor: tuple[int, str] | None = None
    while True:
        query = (
            supabase.table("model_predictions")
            .select("match_id, selection, probability")
            .eq("model_name", model_name)
            .eq("market", mercado)
            .order("match_id")
            .order("selection")
            .limit(1000)
        )
        if cursor is not None:
            cursor_match_id, cursor_selecao = cursor
            query = query.or_(f"match_id.gt.{cursor_match_id},and(match_id.eq.{cursor_match_id},selection.gt.{cursor_selecao})")
        pagina = query.execute().data or []
        for linha in pagina:
            predicoes.setdefault(linha["match_id"], {})[linha["selection"]] = float(linha["probability"])
        if len(pagina) < 1000:
            break
        ultimo = pagina[-1]
        cursor = (ultimo["match_id"], ultimo["selection"])
    return predicoes


def _resultado_real_gols(home_goals: int, away_goals: int, mercado: str) -> str:
    if mercado == "1X2":
        if home_goals > away_goals:
            return "home"
        if home_goals == away_goals:
            return "draw"
        return "away"
    return "over" if (home_goals + away_goals) > 2.5 else "under"


def _carregar_resultados_reais(supabase, match_ids: list[int], mercado: str) -> dict[int, tuple[int, str]]:
    """`match_id -> (league_id, seleção_real)`."""
    if mercado == "corners_over_under_9.5":
        total_df = dh._carregar_total_corners_por_partida(supabase, match_ids)

        def factory_liga(lote, inicio, fim):
            return supabase.table("matches").select("id, league_id").in_("id", lote).range(inicio, fim)

        league_por_match = {l["id"]: l["league_id"] for l in dh._paginar_por_lotes_de_id(factory_liga, match_ids)}

        resultados: dict[int, tuple[int, str]] = {}
        for _, linha in total_df.iterrows():
            if pd.isna(linha["total_corners"]):
                continue
            match_id = int(linha["match_id"])
            league_id = league_por_match.get(match_id)
            if league_id is None:
                continue
            resultados[match_id] = (league_id, "over" if linha["total_corners"] > 9.5 else "under")
        return resultados

    def factory(lote, inicio, fim):
        return (
            supabase.table("matches")
            .select("id, league_id, home_goals, away_goals")
            .in_("id", lote)
            .eq("status", "finished")
            .order("id")
            .range(inicio, fim)
        )

    resultados = {}
    for linha in dh._paginar_por_lotes_de_id(factory, match_ids):
        if linha["home_goals"] is None or linha["away_goals"] is None:
            continue
        resultados[linha["id"]] = (linha["league_id"], _resultado_real_gols(linha["home_goals"], linha["away_goals"], mercado))
    return resultados


def _clamp(p: float, eps: float = 1e-4) -> float:
    return min(max(p, eps), 1 - eps)


def _log_loss_e_acertos(linhas: list[tuple[dict, str]]) -> tuple[np.ndarray, np.ndarray]:
    log_losses = np.empty(len(linhas))
    acertos = np.empty(len(linhas))
    for i, (probs, real) in enumerate(linhas):
        log_losses[i] = -np.log(_clamp(probs[real]))
        favorito = max(probs, key=probs.get)
        acertos[i] = 1.0 if favorito == real else 0.0
    return log_losses, acertos


def _bootstrap_ic95(log_losses: np.ndarray, acertos: np.ndarray, rng: np.random.Generator, n_reamostragens: int = N_REAMOSTRAGENS) -> tuple[tuple[float, float], tuple[float, float]]:
    """Bootstrap não pareado (2000 reamostragens com reposição, mesma
    disciplina de `backtest_kelly.comparar_pareado_com_mercado`/achado #3 --
    aqui MARGINAL por modelo, não pareado contra um segundo modelo, porque o
    objetivo é o IC de cada modelo isoladamente, não uma diferença). O MESMO
    índice reamostrado é usado pra log-loss e acurácia em cada iteração
    (matriz `(n_reamostragens, n)` vetorizada, não um laço Python por
    reamostragem -- laço chegou a levar dezenas de minutos pro total de
    grupos deste script num teste inicial)."""
    n = len(log_losses)
    idx = rng.integers(0, n, size=(n_reamostragens, n))
    ll_medias = log_losses[idx].mean(axis=1)
    acc_medias = acertos[idx].mean(axis=1)
    ll_lo, ll_hi = np.percentile(ll_medias, [2.5, 97.5])
    acc_lo, acc_hi = np.percentile(acc_medias, [2.5, 97.5])
    return (float(ll_lo), float(ll_hi)), (float(acc_lo), float(acc_hi))


def avaliar_modelo_mercado(supabase, model_name: str, mercado: str, rng: np.random.Generator) -> list[dict]:
    predicoes = _paginar_predicoes(supabase, model_name, mercado)
    if not predicoes:
        return []

    resultados = _carregar_resultados_reais(supabase, list(predicoes.keys()), mercado)
    selecoes_esperadas = MERCADOS[mercado]

    por_liga: dict[int, list[tuple[dict, str]]] = {}
    for match_id, (league_id, real) in resultados.items():
        probs = predicoes.get(match_id)
        if not probs or not all(s in probs for s in selecoes_esperadas):
            continue
        por_liga.setdefault(league_id, []).append((probs, real))

    saidas = []
    for league_id, linhas in por_liga.items():
        if len(linhas) < AMOSTRA_MINIMA:
            continue
        log_losses, acertos = _log_loss_e_acertos(linhas)
        (ll_lo, ll_hi), (acc_lo, acc_hi) = _bootstrap_ic95(log_losses, acertos, rng)
        saidas.append({
            "model_name": model_name, "market": mercado, "league_id": league_id,
            "n_jogos": len(linhas),
            "log_loss": round(float(log_losses.mean()), 4),
            "log_loss_ic_inf": round(ll_lo, 4), "log_loss_ic_sup": round(ll_hi, 4),
            "accuracy": round(float(acertos.mean()), 4),
            "accuracy_ic_inf": round(acc_lo, 4), "accuracy_ic_sup": round(acc_hi, 4),
        })
    return saidas


def upsert_em_lotes(supabase, linhas: list[dict]) -> int:
    for i in range(0, len(linhas), LOTE_UPSERT):
        supabase.table("model_stats_ic").upsert(linhas[i : i + LOTE_UPSERT], on_conflict="model_name,market,league_id").execute()
    return len(linhas)


def main() -> None:
    parser = argparse.ArgumentParser(description="IC95% por bootstrap do log-loss/acurácia de cada modelo, por liga.")
    parser.add_argument("--mercados", default="", help="Subconjunto separado por vírgula (vazio = todos os 3 de model_stats_resumo).")
    parser.add_argument("--modelos", default="", help="Subconjunto de model_name separado por vírgula (vazio = todos os de model_stats_resumo pro mercado). Útil pra teste rápido.")
    parser.add_argument("--sem-gravar", action="store_true", help="Só imprime, não escreve em model_stats_ic.")
    args = parser.parse_args()
    filtro_modelos = {m.strip() for m in args.modelos.split(",") if m.strip()} or None

    supabase = create_client(obter_env("SUPABASE_URL"), obter_env("SUPABASE_KEY"))
    rng = np.random.default_rng(SEED)

    mercados = [m.strip() for m in args.mercados.split(",") if m.strip()] or list(MERCADOS.keys())
    for m in mercados:
        if m not in MERCADOS:
            sys.exit(f"Mercado desconhecido: {m!r} (válidos: {list(MERCADOS.keys())})")

    todas_as_linhas: list[dict] = []
    for mercado in mercados:
        modelos = _modelos_por_mercado(supabase, mercado)
        if filtro_modelos is not None:
            modelos = [m for m in modelos if m in filtro_modelos]
        logger.info("[%s] %d modelo(s) a avaliar.", mercado, len(modelos))
        for model_name in modelos:
            linhas = avaliar_modelo_mercado(supabase, model_name, mercado, rng)
            if linhas:
                logger.info("[%s/%s] IC95%% calculado em %d liga(s).", model_name, mercado, len(linhas))
            todas_as_linhas.extend(linhas)

    todas_as_linhas.sort(key=lambda r: (r["market"], r["league_id"], r["log_loss"]))
    for r in todas_as_linhas:
        logger.info(
            "%-30s %-22s liga=%-3d n=%-5d log-loss=%.4f IC95%%=[%.4f,%.4f]  acc=%.1f%% IC95%%=[%.1f%%,%.1f%%]",
            r["market"], r["model_name"], r["league_id"], r["n_jogos"],
            r["log_loss"], r["log_loss_ic_inf"], r["log_loss_ic_sup"],
            r["accuracy"] * 100, r["accuracy_ic_inf"] * 100, r["accuracy_ic_sup"] * 100,
        )

    if args.sem_gravar:
        print(f"\n(--sem-gravar: {len(todas_as_linhas)} linha(s) NÃO escritas em model_stats_ic.)")
        return

    n_gravadas = upsert_em_lotes(supabase, todas_as_linhas) if todas_as_linhas else 0
    logger.info("Gravadas %d linha(s) em model_stats_ic.", n_gravadas)


if __name__ == "__main__":
    main()
