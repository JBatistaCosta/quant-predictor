#!/usr/bin/env python3
"""Pergunta do usuário sobre `cartoes_rf` (o classificador por trás das
células confiáveis da matriz de confiabilidade EV -- cartões O/U 4.5 e
5.5, odd 1.30-2.50): "desse modelo, quais ligas mostraram sinal?", "e qual
casa de apostas tem sinal?" e, por fim, "roda a carteira cronológica só
com Série B + bet365/betano" -- o recorte descoberto pelas 2 perguntas
anteriores (ver `LIGA_RESTRITA`/`CASAS_RESTRITAS` abaixo), registrado como
próximo passo em `model_betting_strategy` (sub_faixa `rf_confiavel_*`,
20/09) antes de promover a confiança de `em_revisao` pra `alta`.

ATUALIZADO 20/09 pós-fix de dado (PRs #612/#613, bug de match_stats_fotmob.
yellow_cards/red_cards zerando cartão real -- até 30,4% das linhas no Q3/
2026): rerodando `matriz_confiabilidade_ev.py` com o dado corrigido, a
célula 5.5/edge[15%,25%) (que existia no achado original de 19/09) deixou
de ser confiável (IC95% agora cruza zero) -- só sobrevivem as 2 células de
edge>=25% (4.5 e 5.5), ambas mais fortes que antes do fix. `EDGE_MINIMO_
SINAL` abaixo foi atualizado de 0.15 pra 0.25 por isso -- reflete o
critério confiável ATUAL, não o de 19/09.

A matriz de confiabilidade EV (`matriz_confiabilidade_ev.py`) agrega TODAS
as ligas/casas do escopo numa célula só -- nunca quebrou por liga nem por
bookmaker. Este script reaproveita a MESMA reconstrução walk-forward
(`validar_cartoes_walkforward_incremental.gerar_previsoes_walkforward`,
mesmas features/hiperparâmetros) e a MESMA faixa confiável (odd
[1.30,2.50), edge>=15%) já validada, e quebra o relatório por liga E por
casa de aposta usando a infraestrutura já existente em `backtest_kelly.py`
(`montar_apostas(liga_por_match_id=..., bookmakers_por_partida=...)` +
`resumir_por_liga`/`resumir_por_casa_aposta` e seus `imprimir_relatorio_*`
-- a quebra por liga já existia pros walk-forwards de gols/escanteios via
chamadas avulsas; a quebra por casa é nova, adicionada aqui e reaproveitável
por qualquer script futuro que precise da mesma pergunta).

Não escreve nada no Supabase -- só leitura e relatório em stdout, mesma
categoria de `validar_cartoes_walkforward_incremental.py`.

AVISO ESTATÍSTICO: quebrar por liga divide a amostra já modesta (99-141
apostas no agregado) em fatias menores -- qualquer liga com poucas apostas
(a maioria, provavelmente) não deve ser lida como "confirmado" só por ter
ROI positivo. O relatório mostra o IC95% de cada liga justamente pra deixar
isso explícito; ligas com IC cruzando zero ou n muito pequeno (<20-30) são
"não conclusivo", não "sem sinal".

Uso:
    set SUPABASE_URL=...
    set SUPABASE_KEY=sua_service_role_key (ou anon -- só leitura)
    python scripts/analisar_cartoes_liga_sinal.py
"""

from __future__ import annotations

import logging
import os
import sys

import pandas as pd
from supabase import Client, create_client

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_kelly as bk
import dados_historicos as dh
import matriz_confiabilidade_ev as mcev
import validar_cartoes_walkforward_incremental as wf_cartoes

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
logger = logging.getLogger("analisar_cartoes_liga_sinal")

# Faixa confiável ATUAL (pós-fix, 20/09) -- odd baixa (bucket [1.30,2.50)
# de `backtest_kelly.FAIXAS_STAKING`) e edge >= 25% (as 2 células que
# sobrevivem com o dado corrigido; a de edge 15-25% que existia no achado
# de 19/09 não se sustentou depois do fix de PRs #612/#613).
LINHAS_ALVO = (4.5, 5.5)
ODD_MIN, ODD_MAX = 1.30, 2.50
EDGE_MINIMO_SINAL = 0.25
MIN_N_LIGA_CONCLUSIVO = 20  # abaixo disso, "não conclusivo" mesmo se IC>0

# Recorte "restrito" pedido pelo usuário depois de ver a quebra por liga/casa
# (registrado como próximo passo em model_betting_strategy, sub_faixa
# rf_confiavel_*, 20/09): a carteira cronológica original (CONTEXTO_
# PROJETO.md "ACHADO REFORÇADO 19/09") usa TODAS as ligas/casas agregadas --
# isto testa o efeito líquido de restringir às 2 únicas fatias com edge
# demonstrado (liga: Brasileirão Série B; casa: bet365/betano, excluindo
# Pinnacle, que não mostrou edge nenhum).
LIGA_RESTRITA = "Brasileirão Série B"
CASAS_RESTRITAS = {"bet365", "betano"}


def obter_env(nome: str) -> str:
    valor = os.environ.get(nome)
    if not valor:
        sys.exit(f"Configure {nome} antes de rodar.")
    return valor


def main() -> None:
    supabase: Client = create_client(obter_env("SUPABASE_URL"), obter_env("SUPABASE_KEY"))

    league_ids = wf_cartoes.obter_league_ids_escopo(supabase)
    logger.info("Montando dataset Feature Stacked (%d ligas em escopo)...", len(league_ids))
    dataset = dh.montar_dataset_ml_empilhado(supabase, league_ids_manual=league_ids)
    dataset["match_date"] = pd.to_datetime(dataset["match_date"], utc=True)
    dataset = dataset.dropna(subset=["match_id"]).sort_values("match_date").reset_index(drop=True)
    logger.info("Dataset montado: %d partidas (%s a %s).",
                len(dataset), dataset["match_date"].min().date(), dataset["match_date"].max().date())

    # "liga" já vem como NOME (dataset["liga"] = dataset["league_id"].map(nome_da_liga),
    # ver dados_historicos.py) -- exatamente o que montar_apostas/resumir_por_liga esperam.
    liga_por_match_id = dict(zip(dataset["match_id"].astype(int), dataset["liga"]))

    datas_por_match = dict(zip(dataset["match_id"].astype(int), dataset["match_date"]))

    features = [f for f in wf_cartoes.FEATURES_CARTOES if f in dataset.columns]
    faltando = set(wf_cartoes.FEATURES_CARTOES) - set(features)
    if faltando:
        logger.warning("features ausentes no dataset (ignoradas): %s", sorted(faltando))

    apostas_confiaveis_total = []
    for linha in LINHAS_ALVO:
        coluna_alvo = dh.coluna_resultado_cartoes_ou(linha)
        if coluna_alvo not in dataset.columns:
            logger.warning("[cartoes_total %.1f]: coluna %r ausente -- pulando.", linha, coluna_alvo)
            continue

        previsoes = wf_cartoes.gerar_previsoes_walkforward(dataset, features, coluna_alvo, linha)
        if not previsoes:
            logger.error("[cartoes_total %.1f]: nenhuma previsão gerada.", linha)
            continue

        mercado = f"cartoes_over_under_{linha}"
        match_ids = list(previsoes.keys())
        # _com_bookmaker (não só a odd) -- pergunta do usuário: além de liga,
        # o edge também é uniforme entre casas de aposta ou concentrado numa
        # só? Mesma info que api/backtest-betting.js já expõe como
        # casas_aposta_fechamento no Backtest completo (PR #605).
        odds_reais, bookmaker_por_partida = bk.carregar_melhores_odds_fechamento_com_bookmaker(supabase, match_ids, mercado)
        resultados_reais = dict(zip(dataset["match_id"].astype(int), dataset[coluna_alvo]))
        predicoes = {mid: {"prob_over": p, "prob_under": 1 - p} for mid, p in previsoes.items()}

        apostas = bk.montar_apostas(
            predicoes, odds_reais, resultados_reais, liga_por_match_id=liga_por_match_id, mercado=mercado,
            bookmakers_por_partida=bookmaker_por_partida,
        )
        # Restringe à faixa confiável de verdade (odd [1.30,2.50), edge>=15%)
        # -- montar_apostas só filtra por EDGE_MINIMO=2% (padrão do projeto),
        # bem mais largo que a faixa validada.
        confiaveis = [
            a for a in apostas
            if ODD_MIN <= a["odd"] < ODD_MAX and (a["prob_modelo"] - 1 / a["odd"]) >= EDGE_MINIMO_SINAL
        ]
        logger.info(
            "[cartoes_total %.1f]: %d apostas na faixa confiável (odd [%.2f,%.2f), edge>=%.0f%%) de %d totais com edge>=2%%.",
            linha, len(confiaveis), ODD_MIN, ODD_MAX, EDGE_MINIMO_SINAL * 100, len(apostas),
        )
        apostas_confiaveis_total.extend([{**a, "mercado": mercado, "match_date": datas_por_match[a["match_id"]]} for a in confiaveis])

    if not apostas_confiaveis_total:
        logger.error("Nenhuma aposta na faixa confiável em nenhuma linha -- não há como quebrar por liga.")
        return

    relatorio_geral = bk.resumir_backtest("cartoes_rf (faixa confiável, TODAS as ligas agregadas)", apostas_confiaveis_total, None)
    logger.info("=" * 100)
    logger.info(
        "AGREGADO (referência, mesmo número já registrado em CONTEXTO_PROJETO.md): n=%d | ROI médio %+.1f%% IC95%%[%+.1f%%,%+.1f%%] | %s",
        relatorio_geral["n_apostas"], relatorio_geral["roi_medio"] * 100,
        relatorio_geral["roi_ic95_inferior"] * 100, relatorio_geral["roi_ic95_superior"] * 100,
        "SIGNIFICATIVO (IC95%>0)" if relatorio_geral["significativo"] else "sem evidência",
    )
    logger.info("=" * 100)

    relatorio_por_liga = bk.resumir_por_liga("cartoes_rf", apostas_confiaveis_total)
    bk.imprimir_relatorio_por_liga(relatorio_por_liga)

    relatorio_por_casa = bk.resumir_por_casa_aposta("cartoes_rf", apostas_confiaveis_total)
    bk.imprimir_relatorio_por_casa_aposta(relatorio_por_casa)

    logger.info(
        "Ligas com n<%d são NÃO CONCLUSIVAS mesmo com IC95%%>0 -- amostra pequena demais pra distinguir "
        "sinal genuíno de ruído (ver docstring do script).", MIN_N_LIGA_CONCLUSIVO,
    )
    for r in sorted(relatorio_por_liga, key=lambda r: r["roi_ic95_inferior"], reverse=True):
        if r["n_apostas"] < MIN_N_LIGA_CONCLUSIVO:
            logger.info("  -> %s: n=%d (< %d, não conclusivo)", r["model_name"], r["n_apostas"], MIN_N_LIGA_CONCLUSIVO)

    # ==========================================================================
    # Carteira cronológica RESTRITA (liga=Brasileirão Série B, casa in
    # {bet365,betano}) -- pedido do usuário depois de ver a quebra por liga/
    # casa acima, é o "próximo passo antes de promover a alta" já registrado
    # em model_betting_strategy (sub_faixa rf_confiavel_*). Reaproveita
    # `simular_carteira_cronologica` de matriz_confiabilidade_ev.py (mesma
    # função que gerou os números 3,09x/3,86x/1,67x já citados em
    # CONTEXTO_PROJETO.md, mas ali rodada sobre TODAS as ligas/casas
    # agregadas) -- aqui roda só no recorte com edge demonstrado.
    # ==========================================================================
    restritas = [
        a for a in apostas_confiaveis_total
        if a.get("liga") == LIGA_RESTRITA and a.get("casa_aposta") in CASAS_RESTRITAS
    ]
    logger.info("=" * 100)
    logger.info(
        "CARTEIRA CRONOLÓGICA RESTRITA (liga=%r, casa em %s): %d de %d apostas confiáveis sobrevivem ao recorte.",
        LIGA_RESTRITA, sorted(CASAS_RESTRITAS), len(restritas), len(apostas_confiaveis_total),
    )
    if not restritas:
        logger.error("Nenhuma aposta sobrevive ao recorte liga+casa -- não há como simular a carteira restrita.")
    else:
        # Por linha (mercado) separada, mesmo recorte de granularidade que
        # CONTEXTO_PROJETO.md já reporta pra carteira agregada (3,09x pra 4.5,
        # 3,86x/1,67x pras 2 células de 5.5) -- e também combinada (as 2
        # linhas juntas, ordem cronológica real de quem apostaria nas 2 ao
        # mesmo tempo).
        for mercado_alvo in sorted({a["mercado"] for a in restritas}):
            subset = sorted([a for a in restritas if a["mercado"] == mercado_alvo], key=lambda a: a["match_date"])
            resultado = mcev.simular_carteira_cronologica(subset)
            relatorio = bk.resumir_backtest(f"cartoes_rf / {mercado_alvo} (restrito)", subset, None)
            logger.info(
                "  %s: n=%d | ROI médio %+.1f%% IC95%%[%+.1f%%,%+.1f%%] | carteira: banca final %.2fx, drawdown máx %.1f%% (n apostado=%d)",
                mercado_alvo, relatorio["n_apostas"], relatorio["roi_medio"] * 100,
                relatorio["roi_ic95_inferior"] * 100, relatorio["roi_ic95_superior"] * 100,
                resultado["banca_final_x"], resultado["drawdown_maximo"] * 100, resultado["n_apostado"],
            )

        restritas_ordenadas = sorted(restritas, key=lambda a: a["match_date"])
        resultado_combinado = mcev.simular_carteira_cronologica(restritas_ordenadas)
        relatorio_combinado = bk.resumir_backtest("cartoes_rf / restrito (4.5+5.5 combinadas)", restritas_ordenadas, None)
        logger.info("-" * 100)
        logger.info(
            "COMBINADO (4.5+5.5, mesma banca): n=%d | ROI médio %+.1f%% IC95%%[%+.1f%%,%+.1f%%] | %s | "
            "carteira: banca final %.2fx, drawdown máx %.1f%% (n apostado=%d)",
            relatorio_combinado["n_apostas"], relatorio_combinado["roi_medio"] * 100,
            relatorio_combinado["roi_ic95_inferior"] * 100, relatorio_combinado["roi_ic95_superior"] * 100,
            "SIGNIFICATIVO (IC95%>0)" if relatorio_combinado["significativo"] else "sem evidência",
            resultado_combinado["banca_final_x"], resultado_combinado["drawdown_maximo"] * 100, resultado_combinado["n_apostado"],
        )
    logger.info("=" * 100)


if __name__ == "__main__":
    main()
