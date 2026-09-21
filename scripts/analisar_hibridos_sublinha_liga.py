#!/usr/bin/env python3
"""Quebra o desempenho dos modelos híbridos em produção (`hibrido_gols_xg_v1`,
`hibrido_gols_xg_v2_estado`, `hibrido_corners_v1`) por SUB-LINHA (mandante/
empate/visitante no 1X2, under/over no over_under_2.5, sim/não no BTTS,
under/over em escanteios) e, dentro de cada sub-linha, por LIGA -- pedido do
usuário depois de ver que nenhuma célula odd x edge da matriz de
confiabilidade EV (`matriz_confiabilidade_ev.py`) sobrevive pros híbridos: a
pergunta agora é se o problema é uniforme entre seleções/ligas ou concentrado
nalgumas (mesmo espírito de `backtest_kelly.resumir_por_liga`, só que também
fatiado por seleção primeiro).

Reaproveita a coleta de apostas já escrita em `matriz_confiabilidade_ev.py`
(`coletar_apostas_gols`/`coletar_apostas_escanteios`, que já anexam `liga` em
cada aposta) em vez de duplicar o walk-forward -- monta o dataset "Feature
Stacked" (~90-110min, a parte cara) UMA VEZ e reaproveita pros 3 modelos.

Diferente da matriz de confiabilidade EV: aqui não segmenta por faixa de odd/
edge nem aplica Bonferroni/FDR -- é a quebra mais grossa (só sub-linha e liga,
sem bootstrap de mediana) que `backtest_kelly.resumir_backtest`/
`resumir_por_liga` já fazem, só que agora cruzadas. Não persiste nada no
Supabase -- só leitura e relatório em stdout, mesma categoria de
`analisar_cartoes_edge_ev.py`/`testar_covariaveis_corners.py`.

Uso:
    set SUPABASE_URL=...
    set SUPABASE_KEY=sua_service_role_key (ou anon -- só leitura)
    python scripts/analisar_hibridos_sublinha_liga.py
"""

from __future__ import annotations

import logging
import os
import sys

import pandas as pd
from supabase import Client, create_client

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import matriz_confiabilidade_ev as mcev

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
logger = logging.getLogger("analisar_hibridos_sublinha_liga")

MIN_N_SUBLINHA = 30  # mais permissivo que MIN_N_CELULA da matriz (50) -- aqui não há grade odd x edge fatiando ainda mais a amostra.


def relatorio_por_sublinha_e_liga(nome_modelo_mercado: str, apostas: list[dict]) -> None:
    if not apostas:
        logger.warning("[%s]: nenhuma aposta -- nada a quebrar por sub-linha.", nome_modelo_mercado)
        return

    apostas_por_selecao: dict[str, list[dict]] = {}
    for aposta in apostas:
        apostas_por_selecao.setdefault(aposta["selecao"], []).append(aposta)

    logger.info("#" * 130)
    logger.info("### %s ###", nome_modelo_mercado)
    logger.info("#" * 130)
    for selecao, apostas_sel in sorted(apostas_por_selecao.items()):
        if len(apostas_sel) < MIN_N_SUBLINHA:
            logger.info("[%s / %s]: só %d apostas (< %d) -- pulando.", nome_modelo_mercado, selecao, len(apostas_sel), MIN_N_SUBLINHA)
            continue
        resumo = mcev.bk.resumir_backtest(f"{nome_modelo_mercado} / {selecao}", apostas_sel, None)
        flag = "SIGNIFICATIVO (IC95% > 0)" if resumo["significativo"] else "sem evidência de edge positivo"
        logger.info(
            "--- SUB-LINHA %-10s | %4d apostas | ROI médio %+7.2f%% | IC95%% [%+7.2f%%, %+7.2f%%] | %s ---",
            selecao, resumo["n_apostas"], resumo["roi_medio"] * 100,
            resumo["roi_ic95_inferior"] * 100, resumo["roi_ic95_superior"] * 100, flag,
        )
        relatorio_liga = [r for r in mcev.bk.resumir_por_liga(f"{nome_modelo_mercado} / {selecao}", apostas_sel) if r["n_apostas"] >= MIN_N_SUBLINHA]
        if relatorio_liga:
            mcev.bk.imprimir_relatorio_por_liga(relatorio_liga)
        else:
            logger.info("    (nenhuma liga com >= %d apostas nesta sub-linha)", MIN_N_SUBLINHA)
    logger.info("#" * 130)


def obter_env(nome: str) -> str:
    valor = os.environ.get(nome)
    if not valor:
        sys.exit(f"Configure {nome} antes de rodar.")
    return valor


def main() -> None:
    supabase: Client = create_client(obter_env("SUPABASE_URL"), obter_env("SUPABASE_KEY"))

    league_ids = mcev.obter_league_ids_escopo(supabase)
    logger.info("Montando dataset Feature Stacked (%d ligas em escopo)...", len(league_ids))
    dataset = mcev.dh.montar_dataset_ml_empilhado(supabase, league_ids_manual=league_ids)
    dataset["match_date"] = pd.to_datetime(dataset["match_date"], utc=True)
    dataset = dataset.dropna(subset=["match_id"]).sort_values("match_date").reset_index(drop=True)
    logger.info("Dataset montado: %d partidas (%s a %s).",
                len(dataset), dataset["match_date"].min().date(), dataset["match_date"].max().date())

    for modelo_gols in mcev.MODELOS_GOLS:
        logger.info("--- Gols (%s) ---", modelo_gols)
        apostas_gols = mcev.coletar_apostas_gols(supabase, dataset, modelo_gols)
        for mercado in mcev.MERCADOS_GOLS:
            relatorio_por_sublinha_e_liga(f"{modelo_gols} [{mercado}]", [a for a in apostas_gols if a["mercado"] == mercado])

    logger.info("--- Escanteios (%s) ---", mcev.wf_corners.MODEL_NAME)
    apostas_escanteios = mcev.coletar_apostas_escanteios(supabase, dataset)
    relatorio_por_sublinha_e_liga(f"{mcev.wf_corners.MODEL_NAME} [{mcev.MERCADO_ESCANTEIOS}]", apostas_escanteios)


if __name__ == "__main__":
    main()
