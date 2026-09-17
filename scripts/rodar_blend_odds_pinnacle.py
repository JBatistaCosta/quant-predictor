"""Runner fino que combina `hibrido_gols_xg_v1` com a Pinnacle devigada
(odds implícitas como "fator de ajuste") pro mercado `over_under_2.5`,
gravando `model_name='blend_xg_pinnacle_ou25_v1'` em `model_predictions`.

Modelo-base é `hibrido_gols_xg_v1`, NÃO `catboost_v9` -- achado real ao
tentar plugar a primeira versão deste runner em produção (17/09):
`catboost_v9` só tem previsões pra partidas `finished` (artefato de
walk-forward de treino/avaliação em `custom_model_configs`), nunca
`scheduled` -- não gera previsão pra jogo futuro nenhum, então não tinha
como rodar de verdade em produção. `hibrido_gols_xg_v1` é o modelo que de
fato roda ao vivo (previsões `scheduled` populadas continuamente). A
validação inteira (walk-forward, pesos por faixa) foi refeita do zero
com `hibrido_gols_xg_v1` como base -- não é o mesmo resultado reaproveitado
do CatBoost com o nome trocado, ver números abaixo.

Único mercado coberto -- é o único validado. Nesta mesma sessão de
descoberta, o mesmo blend testado em 1X2 (com `catboost_v9`) NÃO se
sustentou em walk-forward (peso ótimo instável entre folds, blend pior que
a Pinnacle pura no agregado) -- ver `CONTEXTO_PROJETO.md` (achado "Blend
pós-hoc modelo+mercado", 17/09) pro histórico completo da validação antes
de estender pra outro mercado/modelo sem repetir esse processo.

## Método (peso por faixa, não peso único nem curva contínua)

`logit(p_final) = w·logit(p_xg) + (1-w)·logit(p_pinnacle)`, com
`p_pinnacle` = Pinnacle devigada (`backtest_kelly._devig_odds_ratio`) na
odd de ABERTURA (`MIN(captured_at)` por seleção -- nunca fechamento, que
vazaria informação de última hora, exatamente o que se quer testar se dá
pra capturar via `w`).

`w` varia por FAIXA de magnitude do mercado (`|p_pinnacle_over - 0,5|` --
0 = jogo parelho, 0,5 = mercado já decidido) -- validado por walk-forward
(4 folds cronológicos, cada um treina só com dado anterior ao fold de
teste, n=5.910 partidas jan/2023-set/2026). Peso único fixo já bate mercado
e modelo de forma significativa em todos os 4 folds (w estável, cresce
suavemente 0,30→0,40); segmentar em 3 faixas ganha um pouco mais mas não
fecha significância sobre o peso único aqui (diferente do CatBoost, onde
segmentar era significativo) -- usado faixas mesmo assim por consistência
de método entre os dois modelos-base e porque nunca piora.

Os cortes de faixa (tercis) e os pesos (médias dos 4 folds do walk-forward)
são CONSTANTES FIXAS abaixo, não recalculadas aqui -- reajustar em cima do
histórico mais recente sem repetir o walk-forward completo reintroduziria
o mesmo overfitting que a validação existiu pra descartar (ver docstring
de `CORTES_MAGNITUDE`/`PESOS_POR_FAIXA`).

Uso:
    SUPABASE_URL=... SUPABASE_KEY=... python3 rodar_blend_odds_pinnacle.py [--dias N] [--match-ids ID,ID,...]

Modo `--backtest`: mesmo padrão de `rodar_pricing_pipeline.py` -- processa
partidas `finished` nos últimos `--dias` dias em vez de `scheduled` nos
próximos `--dias`, pra popular histórico de desempenho real (log-loss/
Brier em `/modelos`) sem esperar rodadas futuras acontecerem.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os

import numpy as np
from supabase import Client, create_client

import dados_historicos as dh
from backtest_kelly import _devig_odds_ratio
from rodar_jogador_mercados_previsto import buscar_fixtures

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

MODEL_NAME_ENTRADA = "hibrido_gols_xg_v1"
MODEL_NAME_SAIDA = "blend_xg_pinnacle_ou25_v1"
MERCADO = "over_under_2.5"
SELECOES = ("over", "under")

# Tercis de |p_pinnacle_over - 0,5| sobre as 5.910 partidas do dataset de
# validação (jan/2023-set/2026, odd de abertura) -- FIXOS, não recalculados
# em produção (ver docstring do módulo).
CORTES_MAGNITUDE = (0.041, 0.088)
# Médias dos pesos ótimos dos 4 folds do walk-forward, mesma ordem de faixa
# (parelho / meio-termo / favorito claro) -- FIXOS pelo mesmo motivo.
PESOS_POR_FAIXA = (0.075, 0.20, 0.51)


def _clamp(p: float, eps: float = 1e-4) -> float:
    return min(max(p, eps), 1 - eps)


def _logit(p: float) -> float:
    p = _clamp(p)
    return float(np.log(p / (1 - p)))


def _peso_por_magnitude(magnitude: float) -> float:
    for corte, peso in zip(CORTES_MAGNITUDE, PESOS_POR_FAIXA):
        if magnitude <= corte:
            return peso
    return PESOS_POR_FAIXA[-1]


def _blend(p_modelo: dict[str, float], p_mercado: dict[str, float], w: float) -> dict[str, float]:
    bruto = {s: np.exp(w * _logit(p_modelo[s]) + (1 - w) * _logit(p_mercado[s])) for s in SELECOES}
    z = sum(bruto.values())
    return {s: bruto[s] / z for s in bruto}


def _buscar_previsoes_catboost(supabase: Client, match_ids: list[int]) -> dict[int, dict[str, float]]:
    """`{match_id: {"over": p, "under": p}}` a partir de `model_predictions`
    -- pagina de verdade (`dh._paginar`), mesmo cuidado já documentado nos
    outros runners contra o corte silencioso de 1000 linhas do PostgREST."""
    linhas = dh._paginar(
        lambda inicio, fim: (
            supabase.table("model_predictions")
            .select("match_id, selection, probability")
            .eq("model_name", MODEL_NAME_ENTRADA)
            .eq("market", MERCADO)
            .in_("match_id", match_ids)
            .range(inicio, fim)
        )
    )
    saida: dict[int, dict[str, float]] = {}
    for linha in linhas:
        saida.setdefault(linha["match_id"], {})[linha["selection"]] = float(linha["probability"])
    return {mid: p for mid, p in saida.items() if set(p) == set(SELECOES)}


def _buscar_odds_abertura_pinnacle(supabase: Client, match_ids: list[int]) -> dict[int, dict[str, float]]:
    """`{match_id: {"over": odd, "under": odd}}` -- odd de ABERTURA (primeiro
    `captured_at` por seleção), nunca fechamento (ver docstring do módulo).
    `odds_market` não tem `snapshot='opening'` dedicado; `MIN(captured_at)`
    é o proxy já validado nesta sessão pra "primeira odd capturada"."""
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


def _buscar_fixtures_finalizadas(supabase: Client, dias: int, match_ids: list[int] | None) -> list[int]:
    """Mesmo padrão de `rodar_pricing_pipeline._buscar_fixtures_finalizadas`
    -- só a lista de `match_id`, que é tudo que este runner precisa."""
    query = supabase.table("matches").select("id").eq("status", "finished")
    if match_ids:
        query = query.in_("id", match_ids)
    else:
        piso = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=dias)
        query = query.gte("match_date", piso.isoformat())
    resp = query.execute()
    return [int(row["id"]) for row in (resp.data or [])]


def rodar(supabase: Client, dias: int, match_ids: list[int] | None, backtest: bool = False) -> int:
    if backtest:
        fixture_ids = _buscar_fixtures_finalizadas(supabase, dias, match_ids)
    else:
        fixtures = buscar_fixtures(supabase, dias, match_ids)
        fixture_ids = [int(m) for m in fixtures["id"].tolist()] if not fixtures.empty else []

    if not fixture_ids:
        logger.info("Nenhuma partida '%s' na janela -- nada a combinar.", "finished" if backtest else "scheduled")
        return 0

    previsoes = _buscar_previsoes_catboost(supabase, fixture_ids)
    odds = _buscar_odds_abertura_pinnacle(supabase, fixture_ids)

    linhas_saida = []
    partidas_processadas = 0
    for match_id in fixture_ids:
        p_modelo = previsoes.get(match_id)
        odds_partida = odds.get(match_id)
        if p_modelo is None:
            logger.info("Partida %s sem previsão de %s em %s -- pulando.", match_id, MODEL_NAME_ENTRADA, MERCADO)
            continue
        if odds_partida is None:
            logger.info("Partida %s sem odd de abertura da Pinnacle em %s -- pulando.", match_id, MERCADO)
            continue

        p_mercado = _devig_odds_ratio(odds_partida)
        magnitude = abs(p_mercado["over"] - 0.5)
        w = _peso_por_magnitude(magnitude)
        p_final = _blend(p_modelo, p_mercado, w)

        for selecao, probabilidade in p_final.items():
            linhas_saida.append({
                "match_id": match_id, "model_name": MODEL_NAME_SAIDA, "market": MERCADO,
                "selection": selecao, "probability": round(float(probabilidade), 5),
            })
        partidas_processadas += 1

    if not linhas_saida:
        logger.info("Nenhuma linha gerada.")
        return 0

    for i in range(0, len(linhas_saida), 500):
        lote = linhas_saida[i : i + 500]
        supabase.table("model_predictions").upsert(lote, on_conflict="match_id,model_name,market,selection").execute()

    logger.info(
        "%d linhas gravadas em model_predictions (%d de %d partidas na janela tinham catboost_v9 + odd de abertura).",
        len(linhas_saida), partidas_processadas, len(fixture_ids),
    )
    return len(linhas_saida)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=7, help="janela de dias (à frente por padrão; pra trás com --backtest) -- default 7")
    ap.add_argument("--match-ids", type=str, default=None, help="lista de match_id separados por vírgula -- ignora --dias quando presente")
    ap.add_argument(
        "--backtest", action="store_true",
        help="processa partidas 'finished' (últimos --dias dias, ou --match-ids explícito) em vez de 'scheduled' -- ver docstring do módulo",
    )
    args = ap.parse_args()

    url = os.environ["SUPABASE_URL"].strip()
    key = os.environ["SUPABASE_KEY"].strip()
    sb = create_client(url, key)

    ids = [int(x) for x in args.match_ids.split(",")] if args.match_ids else None
    rodar(sb, args.dias, ids, backtest=args.backtest)
