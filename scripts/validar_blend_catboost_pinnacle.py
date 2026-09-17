#!/usr/bin/env python3
"""Revalidação do blend `catboost_v9` + Pinnacle (over_under_2.5), com o
mesmo baseline corrigido de `validar_blend_hibrido_pinnacle.py` (ver
CONTEXTO_PROJETO.md, achado "bug exato encontrado", 17/09) -- reaproveita
as funções de lá (`ll_puro`, `ll_blend`, `melhor_w`, `bootstrap_diff_ic95`,
`rodar_walkforward`), só troca a fonte da previsão de modelo.

`catboost_v9` só tem previsões pra partidas `finished` (artefato de
walk-forward de treino/avaliação em `custom_model_configs`, nunca
`scheduled` -- não é modelo "vivo", ver CONTEXTO_PROJETO.md) -- este
script é só pra checar se a linha de investigação do blend teria sinal
com essa base, não para alimentar produção nenhuma.

Uso:
    SUPABASE_URL=... SUPABASE_KEY=... python3 validar_blend_catboost_pinnacle.py
"""

from __future__ import annotations

import os

from supabase import Client, create_client

import dados_historicos as dh
from backtest_kelly import _devig_odds_ratio
from validar_blend_hibrido_pinnacle import MERCADO, SELECOES, rodar_walkforward

MODEL_NAME = "catboost_v9"


def _buscar_previsoes(supabase: Client) -> dict[int, dict[str, float]]:
    linhas = dh._paginar(
        lambda inicio, fim: (
            supabase.table("model_predictions")
            .select("match_id, selection, probability")
            .eq("model_name", MODEL_NAME)
            .eq("market", MERCADO)
            .range(inicio, fim)
        )
    )
    saida: dict[int, dict[str, float]] = {}
    for linha in linhas:
        saida.setdefault(linha["match_id"], {})[linha["selection"]] = float(linha["probability"])
    return {mid: p for mid, p in saida.items() if set(p) == set(SELECOES)}


def _buscar_odds_abertura(supabase: Client, match_ids: list[int]) -> dict[int, dict[str, float]]:
    linhas = dh._paginar(
        lambda inicio, fim: (
            supabase.table("odds_market")
            .select("match_id, selection, odds, captured_at")
            .eq("bookmaker", "pinnacle")
            .eq("market", MERCADO)
            .in_("match_id", match_ids)
            .order("captured_at", desc=False)
            .range(inicio, fim)
        )
    )
    primeira: dict[int, dict[str, tuple[float, str]]] = {}
    for linha in linhas:
        mid, sel = linha["match_id"], linha["selection"]
        capturado_em = linha["captured_at"]
        existente = primeira.setdefault(mid, {}).get(sel)
        if existente is None or capturado_em < existente[1]:
            primeira[mid][sel] = (float(linha["odds"]), capturado_em)
    saida: dict[int, dict[str, float]] = {}
    for mid, por_selecao in primeira.items():
        if set(por_selecao) != set(SELECOES):
            continue
        odds = {s: v[0] for s, v in por_selecao.items()}
        if any(o <= 1.0 for o in odds.values()):
            continue
        saida[mid] = odds
    return saida


def _buscar_resultados(supabase: Client, match_ids: list[int]) -> dict[int, tuple[str, str]]:
    linhas = dh._paginar(
        lambda inicio, fim: (
            supabase.table("matches")
            .select("id, match_date, home_goals, away_goals")
            .eq("status", "finished")
            .in_("id", match_ids)
            .range(inicio, fim)
        )
    )
    saida: dict[int, tuple[str, str]] = {}
    for linha in linhas:
        hg, ag = linha["home_goals"], linha["away_goals"]
        if hg is None or ag is None:
            continue
        real = "over" if (hg + ag) > 2.5 else "under"
        saida[linha["id"]] = (real, linha["match_date"])
    return saida


def montar_dataset(supabase: Client) -> list[dict]:
    previsoes = _buscar_previsoes(supabase)
    match_ids = list(previsoes.keys())
    odds = _buscar_odds_abertura(supabase, match_ids)
    resultados = _buscar_resultados(supabase, list(odds.keys()))

    dados = []
    for match_id, p_modelo in previsoes.items():
        if match_id not in odds or match_id not in resultados:
            continue
        real, match_date = resultados[match_id]
        p_mercado = _devig_odds_ratio(odds[match_id])
        mag = abs(p_mercado["over"] - 0.5)
        dados.append({
            "match_id": match_id, "match_date": match_date, "real": real, "mag": mag,
            "modelo": p_modelo, "mercado": p_mercado,
        })
    dados.sort(key=lambda d: d["match_date"])
    return dados


if __name__ == "__main__":
    url = os.environ["SUPABASE_URL"].strip()
    key = os.environ["SUPABASE_KEY"].strip()
    sb = create_client(url, key)

    dataset = montar_dataset(sb)
    rodar_walkforward(dataset)
