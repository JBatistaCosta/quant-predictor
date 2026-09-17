#!/usr/bin/env python3
"""Revalidação do blend `hibrido_gols_xg_v1` + Pinnacle (over_under_2.5),
com o bug de baseline da validação original corrigido (ver
CONTEXTO_PROJETO.md, achado "bug exato encontrado", 17/09).

O script original (nunca versionado, recuperado do transcript da sessão
anterior) media "log-loss só do mercado"/"log-loss só do modelo" chamando
a MESMA função de blend com w=0.0/w=1.0. Isso não é a identidade: a
renormalização do pooling logarítmico aplicada com a mesma fonte nos dois
termos AFIA a probabilidade (`p -> p²/(p²+(1-p)²)`), inflando o log-loss
de uma fonte já calibrada -- o blend real "vencia" um espantalho, não o
mercado de verdade.

Correção: baseline de mercado/modelo puro é sempre `-log(p)` direto na
probabilidade bruta, nunca através da fórmula de blend. A fórmula de
pooling só é usada pra combinar DUAS fontes DIFERENTES (o blend de
verdade, `w` estritamente entre 0 e 1) -- aí é matematicamente válida
(log-linear pooling), não degenera.

Mesma metodologia da validação anterior, só o baseline corrigido:
- Odd de ABERTURA = MIN(captured_at) por seleção (nunca fechamento).
- Devig via `backtest_kelly._devig_odds_ratio` (Cheung, padrão do projeto).
- Walk-forward: 5 blocos cronológicos, folds 1-4 treinam em blocks[0:i],
  testam em blocks[i] -- peso `w` (único e por faixa de magnitude) ajustado
  só no treino de cada fold.
- Bootstrap pareado (2000 resamples, seed=42) pra IC95% das diferenças.

Uso:
    SUPABASE_URL=... SUPABASE_KEY=... python3 validar_blend_hibrido_pinnacle.py
"""

from __future__ import annotations

import logging
import os

import numpy as np
from supabase import Client, create_client

import dados_historicos as dh
from backtest_kelly import _devig_odds_ratio

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

MODEL_NAME = "hibrido_gols_xg_v1"
MERCADO = "over_under_2.5"
SELECOES = ("over", "under")
N_BLOCOS = 5
N_FAIXAS = 3
GRID_W = np.arange(0.0, 1.01, 0.05)
N_BOOT = 2000
SEED = 42


def _clamp(p: float, eps: float = 1e-4) -> float:
    return min(max(p, eps), 1 - eps)


def _logit(p: float) -> float:
    p = _clamp(p)
    return float(np.log(p / (1 - p)))


def _buscar_previsoes(supabase: Client) -> dict[int, dict[str, float]]:
    """`{match_id: {"over": p, "under": p}}` -- pagina de verdade
    (corte silencioso de 1000 linhas do PostgREST, mesmo cuidado de sempre)."""
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
    """Odd de ABERTURA = MIN(captured_at) por seleção -- mesmo proxy usado
    em produção (`rodar_blend_odds_pinnacle.py`), sem filtrar por
    `snapshot` (a coluna só tem 'pre_closing'/'closing', nunca um rótulo
    dedicado de abertura -- MIN(captured_at) já pega a primeira captura
    disponível, qualquer que seja o rótulo)."""
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
    """`{match_id: (resultado_ou, match_date)}`."""
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


# ---------------------------------------------------------------------------
# Baseline CORRETO -- log-loss direto na probabilidade bruta, nunca via blend
# ---------------------------------------------------------------------------
def ll_puro(dados: list[dict], fonte: str) -> np.ndarray:
    return np.array([-np.log(_clamp(d[fonte][d["real"]])) for d in dados])


# ---------------------------------------------------------------------------
# Blend de verdade -- pooling logarítmico entre DUAS fontes diferentes,
# válido pra w estritamente entre 0 e 1 (nunca usado como baseline puro)
# ---------------------------------------------------------------------------
def ll_blend(dados: list[dict], w: float) -> np.ndarray:
    lls = []
    for d in dados:
        bruto = {s: np.exp(w * _logit(d["modelo"][s]) + (1 - w) * _logit(d["mercado"][s])) for s in SELECOES}
        z = sum(bruto.values())
        blended = {s: bruto[s] / z for s in bruto}
        lls.append(-np.log(_clamp(blended[d["real"]])))
    return np.array(lls)


def melhor_w(train: list[dict]) -> float:
    return min(GRID_W, key=lambda w: ll_blend(train, w).mean())


def bootstrap_diff_ic95(a: np.ndarray, b: np.ndarray, seed: int = SEED, n_boot: int = N_BOOT):
    rng = np.random.default_rng(seed)
    n = len(a)
    idx = rng.integers(0, n, size=(n_boot, n))
    diffs = a[idx].mean(axis=1) - b[idx].mean(axis=1)
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    return float((a - b).mean()), float(lo), float(hi)


def relatar(nome: str, a: np.ndarray, b: np.ndarray) -> None:
    d, lo, hi = bootstrap_diff_ic95(a, b)
    sig = "SIGNIFICATIVO" if (lo > 0) == (hi > 0) else "não significativo"
    print(f"  {nome}: diff={d:.4f}  IC95=[{lo:.4f},{hi:.4f}]  -- {sig}")


def rodar_walkforward(dados: list[dict]) -> None:
    n = len(dados)
    print(f"n_total = {n}")
    tam = n // N_BLOCOS
    blocos = [dados[i * tam:(i + 1) * tam] if i < N_BLOCOS - 1 else dados[i * tam:] for i in range(N_BLOCOS)]

    pooled_fixo, pooled_seg, pooled_mkt, pooled_model = [], [], [], []
    for i in range(1, N_BLOCOS):
        train = [x for b in blocos[:i] for x in b]
        test = blocos[i]

        w_unico = melhor_w(train)
        ll_fixo_test = ll_blend(test, w_unico)

        mags_train = sorted(d["mag"] for d in train)
        cortes = [mags_train[int(len(mags_train) * k / N_FAIXAS)] for k in range(1, N_FAIXAS)]

        def faixa(d, cortes=cortes):
            for idx, c in enumerate(cortes):
                if d["mag"] <= c:
                    return idx
            return N_FAIXAS - 1

        w_por_faixa = {}
        for f in range(N_FAIXAS):
            train_f = [d for d in train if faixa(d) == f]
            w_por_faixa[f] = melhor_w(train_f) if len(train_f) >= 30 else w_unico

        ll_seg_test = np.array([
            ll_blend([d], w_por_faixa[faixa(d)])[0] for d in test
        ])

        ll_mkt_test = ll_puro(test, "mercado")
        ll_model_test = ll_puro(test, "modelo")

        pooled_fixo.extend(ll_fixo_test.tolist())
        pooled_seg.extend(ll_seg_test.tolist())
        pooled_mkt.extend(ll_mkt_test.tolist())
        pooled_model.extend(ll_model_test.tolist())

        print(f"fold {i}: n_train={len(train)} n_test={len(test)}")
        print(f"  w_unico={w_unico:.2f} ll_blend={ll_fixo_test.mean():.4f}  ll_mkt(puro)={ll_mkt_test.mean():.4f}  ll_model(puro)={ll_model_test.mean():.4f}")
        print(f"  w_por_faixa={ {k: round(v, 2) for k, v in w_por_faixa.items()} } cortes={[round(c, 3) for c in cortes]} ll_seg={ll_seg_test.mean():.4f}")

    pooled_fixo = np.array(pooled_fixo)
    pooled_seg = np.array(pooled_seg)
    pooled_mkt = np.array(pooled_mkt)
    pooled_model = np.array(pooled_model)

    print()
    print(f"POOLED (n={len(pooled_fixo)}): ll_model(puro)={pooled_model.mean():.4f}  ll_mkt(puro)={pooled_mkt.mean():.4f}  ll_blend={pooled_fixo.mean():.4f}  ll_seg={pooled_seg.mean():.4f}")
    relatar("blend vs mercado(puro)", pooled_fixo, pooled_mkt)
    relatar("blend vs modelo(puro)", pooled_fixo, pooled_model)
    relatar("seg vs blend", pooled_seg, pooled_fixo)
    relatar("seg vs mercado(puro)", pooled_seg, pooled_mkt)
    relatar("mercado(puro) vs modelo(puro)", pooled_mkt, pooled_model)


if __name__ == "__main__":
    url = os.environ["SUPABASE_URL"].strip()
    key = os.environ["SUPABASE_KEY"].strip()
    sb = create_client(url, key)

    dataset = montar_dataset(sb)
    rodar_walkforward(dataset)
