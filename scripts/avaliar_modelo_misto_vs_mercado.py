#!/usr/bin/env python3
"""Avaliação pareada do modelo misto contra o mercado (Pinnacle devigado),
SEPARADA por abertura e fechamento.

Ferramenta de análise, não parte do pipeline de produção -- roda uma vez,
sob demanda (mesma categoria de `verificar_distribuicoes.py`), depois de
`treinar_modelo_hibrido.py` ter persistido `model_predictions`/
`model_match_estimates`.

Compara, para cada `model_name` em `["hibrido_gols_v1", "hibrido_gols_xg_v1"]`,
cada mercado em `["1X2", "over_under_2.5", "btts"]` e cada snapshot em
`["pre_closing" (abertura), "closing" (fechamento)]`, o log-loss do modelo
contra o log-loss da probabilidade implícita da Pinnacle devigada NESSE
snapshot especificamente (sem fallback entre eles -- diferente de
`backtest_kelly.carregar_odds_pinnacle_devigadas`, que mistura abertura
com fallback de fechamento pra maximizar cobertura; aqui o objetivo é
justamente NÃO misturar, pra responder "o modelo bate o mercado na
abertura? e no fechamento?" como duas perguntas separadas). Usa o bootstrap
PAREADO já existente em `backtest_kelly.comparar_pareado_com_mercado` --
mesma disciplina do achado #3 do CONTEXTO_PROJETO.md, agora aplicada ao
modelo misto.

Cobertura de odds Pinnacle por snapshot medida em ago/2026 (`execute_sql`):
1X2 abertura=14.920 partidas / fechamento=7.337; over_under_2.5
abertura=14.509 / fechamento=2.639; **btts abertura=90 (amostra pequena
demais pra bootstrap confiável) / fechamento=1.659**. O relatório final
avisa quando a amostra de abertura for pequena, em vez de omitir o
resultado ou fingir que é confiável.

Não grava nada no Supabase -- só leitura e relatório em stdout.

Uso:
    set SUPABASE_URL=...
    set SUPABASE_KEY=sua_service_role_key (ou anon -- só leitura)
    python scripts/avaliar_modelo_misto_vs_mercado.py
"""

from __future__ import annotations

import logging
import os
import sys

import numpy as np
from supabase import create_client

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_kelly as bk
import dados_historicos as dh

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
logger = logging.getLogger("avaliar_modelo_misto_vs_mercado")

MODEL_NAMES = ["hibrido_gols_v1", "hibrido_gols_xg_v1"]
MERCADOS_AVALIADOS = ["1X2", "over_under_2.5", "btts"]


def obter_env(nome: str) -> str:
    valor = os.environ.get(nome)
    if not valor:
        sys.exit(f"Configure {nome} antes de rodar.")
    return valor


def _carregar_predicoes(supabase, model_name: str, mercado: str) -> dict[int, dict[str, float]]:
    """`model_predictions` (match_id, selection, probability) filtrado por
    model_name/market, no formato largo (`prob_<selecao>`) que
    `backtest_kelly` espera -- paginado, `.range()` em laço obrigatório."""
    def factory(inicio, fim):
        return (
            supabase.table("model_predictions")
            .select("match_id, selection, probability")
            .eq("model_name", model_name)
            .eq("market", mercado)
            .order("match_id")
            .range(inicio, fim)
        )

    predicoes: dict[int, dict[str, float]] = {}
    for linha in dh._paginar(factory):
        predicoes.setdefault(linha["match_id"], {})[f"prob_{linha['selection']}"] = linha["probability"]
    return predicoes


def _resultado_real_codigo(home_goals: int, away_goals: int, mercado: str) -> int:
    """Deriva o código de resultado real por mercado, mesmos cortes de
    `dados_historicos.py` (linha 3143 pro BTTS) -- `1X2`/`over_under_2.5`
    reaproveitam a mesma regra de `backtest_kelly._resultado_codigo_mercado`,
    mas ela não cobre `btts` (cai no ramo over/under por padrão), então essa
    função fica separada em vez de estender a de produção."""
    if mercado == "1X2":
        if home_goals > away_goals:
            return dh.RESULTADO_HOME
        if home_goals == away_goals:
            return dh.RESULTADO_DRAW
        return dh.RESULTADO_AWAY
    if mercado == "btts":
        return dh.RESULTADO_BTTS_YES if (home_goals > 0 and away_goals > 0) else dh.RESULTADO_BTTS_NO
    return dh.RESULTADO_OVER25 if (home_goals + away_goals) > 2.5 else dh.RESULTADO_UNDER25


def _carregar_resultados_reais(supabase, match_ids: list[int], mercado: str) -> dict[int, int]:
    def factory(lote, inicio, fim):
        return (
            supabase.table("matches")
            .select("id, home_goals, away_goals")
            .in_("id", lote)
            .eq("status", "finished")
            .order("id")
            .range(inicio, fim)
        )

    resultados: dict[int, int] = {}
    for linha in dh._paginar_por_lotes_de_id(factory, match_ids):
        if linha["home_goals"] is None or linha["away_goals"] is None:
            continue
        resultados[linha["id"]] = _resultado_real_codigo(linha["home_goals"], linha["away_goals"], mercado)
    return resultados


def _perdas_por_partida(probs_por_partida: dict[int, dict[str, float]], resultados_reais: dict[int, int], match_ids_ordenados: list[int], mercado: str) -> np.ndarray:
    codigo_para_campo = {codigo: f"prob_{selecao}" for selecao, codigo in bk.MERCADOS[mercado]["codigo_por_selecao"].items()}
    perdas = []
    for match_id in match_ids_ordenados:
        probs = probs_por_partida[match_id]
        real = resultados_reais[match_id]
        p_real = max(probs[codigo_para_campo[real]], 1e-15)
        perdas.append(-np.log(p_real))
    return np.array(perdas)


# (snapshot, rótulo pro relatório) -- "pre_closing" é a odd de ABERTURA
# neste banco (nome herdado da fonte de dado, não literal), "closing" é o
# FECHAMENTO de verdade. Ver docstring do módulo.
SNAPSHOTS_AVALIADOS = [("pre_closing", "abertura"), ("closing", "fechamento")]

# Abaixo desse n, o bootstrap pareado ainda roda (a função aceita qualquer
# n>0), mas o resultado é pouco confiável -- avisar em vez de calar.
AMOSTRA_MINIMA_CONFIAVEL = 100


def avaliar_combinacao(supabase, model_name: str, mercado: str, snapshot: str, rotulo: str) -> dict | None:
    predicoes = _carregar_predicoes(supabase, model_name, mercado)
    if not predicoes:
        logger.warning("%s [%s]: sem previsões em model_predictions -- pulando.", model_name, mercado)
        return None

    match_ids = list(predicoes.keys())
    resultados_reais = _carregar_resultados_reais(supabase, match_ids, mercado)
    # snapshot PURO, sem fallback pro outro -- diferente de
    # carregar_odds_pinnacle_devigadas (que mistura abertura+fechamento pra
    # maximizar cobertura). Aqui o ponto é justamente não misturar.
    odds_brutas = bk._carregar_odds_pinnacle_brutas(supabase, match_ids, mercado, snapshot=snapshot, com_fallback_fechamento=False)
    odds_devig = bk._devigar_odds_por_partida(odds_brutas, mercado)

    match_ids_validos = sorted(set(predicoes) & set(resultados_reais) & set(odds_devig))
    if len(match_ids_validos) < 30:
        logger.warning(
            "%s [%s, %s]: só %d partidas com previsão + resultado + odd Pinnacle -- amostra pequena demais pra bootstrap confiável.",
            model_name, mercado, rotulo, len(match_ids_validos),
        )
        if not match_ids_validos:
            return None

    perdas_modelo = _perdas_por_partida(predicoes, resultados_reais, match_ids_validos, mercado)
    perdas_mercado = _perdas_por_partida(odds_devig, resultados_reais, match_ids_validos, mercado)

    aviso_amostra = " -- AMOSTRA PEQUENA, resultado pouco confiável" if len(match_ids_validos) < AMOSTRA_MINIMA_CONFIAVEL else ""
    logger.info(
        "%s [%s, %s]: n=%d | log-loss modelo=%.4f | log-loss mercado (Pinnacle devigado)=%.4f%s",
        model_name, mercado, rotulo, len(match_ids_validos), perdas_modelo.mean(), perdas_mercado.mean(), aviso_amostra,
    )

    comparacao = bk.comparar_pareado_com_mercado(perdas_modelo, perdas_mercado)
    nome = f"{model_name} [{mercado}, {rotulo}]"
    if aviso_amostra:
        nome += " (n pequeno)"
    return {"nome": nome, "comparacao": comparacao}


def main() -> None:
    supabase = create_client(obter_env("SUPABASE_URL"), obter_env("SUPABASE_KEY"))

    linhas_pareadas = []
    for model_name in MODEL_NAMES:
        for mercado in MERCADOS_AVALIADOS:
            for snapshot, rotulo in SNAPSHOTS_AVALIADOS:
                resultado = avaliar_combinacao(supabase, model_name, mercado, snapshot, rotulo)
                if resultado is not None:
                    linhas_pareadas.append(resultado)

    bk.imprimir_relatorio_pareado(linhas_pareadas)


if __name__ == "__main__":
    main()
