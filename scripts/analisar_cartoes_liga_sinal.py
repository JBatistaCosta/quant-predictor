#!/usr/bin/env python3
"""Pergunta do usuário sobre `cartoes_rf` (o classificador por trás das 2
únicas células confiáveis da matriz de confiabilidade EV -- cartões O/U
4.5 e 5.5, odd 1.30-2.50, edge 15%+, ver CONTEXTO_PROJETO.md "ACHADO
REFORÇADO 19/09"): "desse modelo, quais ligas mostraram sinal?"

A matriz de confiabilidade EV (`matriz_confiabilidade_ev.py`) agrega TODAS
as ligas do escopo numa célula só -- nunca quebrou por liga. Este script
reaproveita a MESMA reconstrução walk-forward (`validar_cartoes_
walkforward_incremental.gerar_previsoes_walkforward`, mesmas features/
hiperparâmetros) e a MESMA faixa confiável (odd [1.30,2.50), edge>=15%)
já validada, e quebra o relatório por liga usando a infraestrutura já
existente em `backtest_kelly.py` (`montar_apostas(liga_por_match_id=...)`
+ `resumir_por_liga`/`imprimir_relatorio_por_liga`, usada antes só pros
walk-forwards de gols/escanteios via chamadas avulsas -- nunca tinha sido
aplicada em Cartões).

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
import validar_cartoes_walkforward_incremental as wf_cartoes

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
logger = logging.getLogger("analisar_cartoes_liga_sinal")

# Mesma faixa confiável das 2 linhas registradas em CONTEXTO_PROJETO.md
# ("ACHADO REFORÇADO 19/09") -- odd baixa (bucket [1.30,2.50) de
# `backtest_kelly.FAIXAS_STAKING`) e edge >= 15% (as 2 células confiáveis
# são 15-25% e 25%+, ambas >= 15%).
LINHAS_ALVO = (4.5, 5.5)
ODD_MIN, ODD_MAX = 1.30, 2.50
EDGE_MINIMO_SINAL = 0.15
MIN_N_LIGA_CONCLUSIVO = 20  # abaixo disso, "não conclusivo" mesmo se IC>0


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
        odds_reais = bk.carregar_melhores_odds_fechamento(supabase, match_ids, mercado)
        resultados_reais = dict(zip(dataset["match_id"].astype(int), dataset[coluna_alvo]))
        predicoes = {mid: {"prob_over": p, "prob_under": 1 - p} for mid, p in previsoes.items()}

        apostas = bk.montar_apostas(
            predicoes, odds_reais, resultados_reais, liga_por_match_id=liga_por_match_id, mercado=mercado,
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
        apostas_confiaveis_total.extend([{**a, "mercado": mercado} for a in confiaveis])

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

    logger.info(
        "Ligas com n<%d são NÃO CONCLUSIVAS mesmo com IC95%%>0 -- amostra pequena demais pra distinguir "
        "sinal genuíno de ruído (ver docstring do script).", MIN_N_LIGA_CONCLUSIVO,
    )
    for r in sorted(relatorio_por_liga, key=lambda r: r["roi_ic95_inferior"], reverse=True):
        if r["n_apostas"] < MIN_N_LIGA_CONCLUSIVO:
            logger.info("  -> %s: n=%d (< %d, não conclusivo)", r["model_name"], r["n_apostas"], MIN_N_LIGA_CONCLUSIVO)


if __name__ == "__main__":
    main()
