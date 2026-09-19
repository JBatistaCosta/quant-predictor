#!/usr/bin/env python3
"""Treina o classificador `cartoes_rf` (o mesmo Random Forest usado em
`validar_cartoes_walkforward_incremental.py`, achado das 3 células
confiáveis na matriz de confiabilidade EV -- ver CONTEXTO_PROJETO.md) com
TODO o histórico disponível e gera previsão pras partidas AINDA NÃO
disputadas, persistindo em `model_predictions` -- pedido do usuário pra
poder acompanhar essas 2 linhas (as únicas com célula confiável: O/U 4.5
e 5.5, odd 1.30-2.50, edge 15-25%+) numa carteira de papel (forward test)
de verdade, não só no backtest histórico.

GAP QUE ISSO FECHA: `cartoes_rf` nunca tinha gerado uma previsão
persistida -- ele só existia dentro do walk-forward de avaliação
(`validar_cartoes_walkforward_incremental.py`), que treina e descarta o
modelo a cada passo mensal, sempre sobre partidas JÁ resolvidas. Sem
nenhuma linha em `model_predictions` pra partida `scheduled`, a Carteira
(Paper Trading) nunca teria como apostar nesse modelo, por mais validado
que estivesse no backtest -- mesmo problema já documentado pra outros
modelos "não vivos" (catboost_v9, CONTEXTO_PROJETO.md 19/09) e resolvido
antes pra modelos customizados via `prever_partidas_futuras_custom.py`.

DESENHO (reaproveita a infraestrutura já existente, não reinventa):
  - Mesmo `FEATURES_CARTOES`/`treinar_random_forest` de `validar_cartoes_
    walkforward_incremental.py` (import direto) -- é o MESMO modelo,
    só que treinado numa vez só com TODO o histórico (não em janela
    expansiva mensal, que existe apenas pra gerar previsão out-of-sample
    pra cada mês passado; aqui queremos o modelo mais atual possível pra
    prever o futuro).
  - `dados_historicos.montar_dataset_ml_empilhado(..., match_ids_extra=
    match_ids_alvo)` -- mesmo mecanismo que `prever_partidas_futuras_
    custom.py`/`treinar_modelo_hibrido.py` usam pra injetar partida
    AINDA NÃO disputada no mesmo pipeline de features (todas calculadas
    com dado anterior à partida, funcionam igual pra jogo futuro).
  - `treinar_modelo_custom.salvar_predicoes_no_banco` -- mesma função
    genérica de persistência em `model_predictions` que os modelos
    customizados usam; `_TARGET_PRED_META["cartoes_over_under_4.5"/"5.5"]`
    já existe lá (`class_to_sel={0:"under",1:"over"}`, bate exatamente com
    `dados_historicos.RESULTADO_CARTOES_UNDER/OVER`), não precisou de
    entrada nova.
  - Treina só as 2 linhas confiáveis (4.5/5.5) -- as outras 4 nunca
    passaram no critério da matriz, gerar previsão pra elas seria
    desperdiçado (a Carteira e a análise atual só olham pra essas 2).

Escopo de partidas-alvo: mesmo escopo de liga do próprio `cartoes_rf`
(`obter_league_ids_escopo`, `modelo_misto_escopo='treino'`), `status=
'scheduled'`, dentro de `JANELA_DIAS` -- não filtra por liga com odds ao
vivo sincronizada (ao contrário de `prever_partidas_futuras_custom.py`)
porque o mercado de cartões (`bookings_over_under_full_time_X`) usa uma
fonte de odds diferente (Pinnacle/bet365/Betano via `odds_market`, não
`liga_oddspapi_tournament`) -- confirmado via SQL que já há partidas
agendadas com odd real pras 2 linhas antes de escrever este script.

Variáveis de ambiente obrigatórias: SUPABASE_URL, SUPABASE_KEY (service_role).
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
from supabase import Client, create_client

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dados_historicos as dh
import validar_cartoes_walkforward_incremental as wf_cartoes
from treinar_modelo_custom import salvar_predicoes_no_banco

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
logger = logging.getLogger("rodar_cartoes_rf_previsto")

MODEL_NAME = "cartoes_rf"
# Só as 2 linhas que sobrevivem ao critério rigoroso (IC95% média E
# mediana do ROI positivos, n>=50) E a Bonferroni/FDR na matriz de
# confiabilidade EV -- ver CONTEXTO_PROJETO.md "ACHADO REFORÇADO (19/09)".
LINHAS_ALVO = (4.5, 5.5)
JANELA_DIAS = 14
MIN_TREINO = wf_cartoes.MIN_TREINO


def obter_env(nome: str) -> str:
    valor = os.environ.get(nome)
    if not valor:
        sys.exit(f"Configure {nome} antes de rodar.")
    return valor


def buscar_partidas_alvo(supabase: Client, league_ids: list[int]) -> list[int]:
    if not league_ids:
        return []
    ate_iso = (datetime.now(timezone.utc) + timedelta(days=JANELA_DIAS)).isoformat()
    resp = (
        supabase.table("matches")
        .select("id")
        .in_("league_id", league_ids)
        .eq("status", "scheduled")
        .lte("match_date", ate_iso)
        .execute()
    )
    return [linha["id"] for linha in (resp.data or [])]


def main() -> None:
    supabase = create_client(obter_env("SUPABASE_URL"), obter_env("SUPABASE_KEY"))

    league_ids = wf_cartoes.obter_league_ids_escopo(supabase)
    match_ids_alvo = buscar_partidas_alvo(supabase, league_ids)
    if not match_ids_alvo:
        logger.info("Nenhuma partida agendada dentro do escopo (%d ligas) nos próximos %d dias -- encerrando.",
                    len(league_ids), JANELA_DIAS)
        return
    logger.info("%d partida(s) agendada(s) candidata(s) (próximos %d dias).", len(match_ids_alvo), JANELA_DIAS)

    logger.info("Montando dataset 'Feature Stacked' (histórico + partidas-alvo)...")
    dataset = dh.montar_dataset_ml_empilhado(supabase, league_ids_manual=league_ids, match_ids_extra=match_ids_alvo)
    if dataset.empty:
        logger.error("Dataset vazio -- não há como treinar/prever.")
        return
    dataset["match_date"] = pd.to_datetime(dataset["match_date"], utc=True)

    features = [f for f in wf_cartoes.FEATURES_CARTOES if f in dataset.columns]
    faltando = set(wf_cartoes.FEATURES_CARTOES) - set(features)
    if faltando:
        logger.warning("Features ausentes no dataset (ignoradas): %s", sorted(faltando))

    linhas_alvo_df = dataset[dataset["match_id"].isin(match_ids_alvo)].reset_index(drop=True)
    if linhas_alvo_df.empty:
        logger.warning("Nenhuma das %d partida(s) candidata(s) sobreviveu ao dataset final "
                        "(provável falta de histórico recente dos times pra montar a média móvel) -- encerrando.",
                        len(match_ids_alvo))
        return

    total_previsoes = 0
    for linha in LINHAS_ALVO:
        coluna_alvo = dh.coluna_resultado_cartoes_ou(linha)
        if coluna_alvo not in dataset.columns:
            logger.warning("[cartoes_total %.1f]: coluna %r ausente no dataset -- pulando.", linha, coluna_alvo)
            continue

        treino_valido = dataset.dropna(subset=[coluna_alvo, *features])
        if len(treino_valido) < MIN_TREINO:
            logger.warning("[cartoes_total %.1f]: só %d partidas de treino válidas (mínimo %d) -- pulando.",
                            linha, len(treino_valido), MIN_TREINO)
            continue

        alvo_validos = linhas_alvo_df.dropna(subset=features).reset_index(drop=True)
        if alvo_validos.empty:
            logger.warning("[cartoes_total %.1f]: nenhuma partida-alvo com todas as features -- pulando.", linha)
            continue

        modelo = wf_cartoes.treinar_random_forest(treino_valido, features, coluna_alvo)
        classes = np.array(modelo.classes_)
        if dh.RESULTADO_CARTOES_OVER not in classes:
            logger.warning("[cartoes_total %.1f]: treino sem nenhum exemplo de 'over' -- modelo degenerado, pulando.", linha)
            continue

        probs = modelo.predict_proba(alvo_validos[features])
        target_key = f"cartoes_over_under_{linha}"
        salvar_predicoes_no_banco(supabase, alvo_validos, probs, classes, MODEL_NAME, target_key)
        total_previsoes += len(alvo_validos)
        logger.info("[cartoes_total %.1f]: %d previsão(ões) gravada(s) em model_predictions (model_name=%r).",
                    linha, len(alvo_validos), MODEL_NAME)

    logger.info("Concluído: %d previsão(ões) no total.", total_previsoes)


if __name__ == "__main__":
    main()
