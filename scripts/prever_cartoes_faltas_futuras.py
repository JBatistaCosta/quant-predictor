#!/usr/bin/env python3
"""Aplica as 12 configs "geral" de Cartões/Faltas JÁ TREINADAS (cartoes_
over_under_1.5-6.5, faltas_over_under_20.5-30.5) sobre as partidas ainda
não disputadas, gravando em `model_predictions` -- mesmo mecanismo de
`prever_partidas_futuras_custom.py` (reaproveitado daqui via import, ver
`processar_config`), só que num workflow PRÓPRIO, separado do que atende
a Carteira (Paper Trading).

Por que separado (10/09, decisão do usuário): as duas frentes nasceram no
mesmo script/workflow (`prever_partidas_futuras_custom.yml`) na sessão em
que Cartões/Faltas ganharam consumidor (o painel "Cartões e Faltas" em
AnaliseEstatisticaJogo.jsx/AnaliseAvancadaEvento.jsx). Rodar as 15 configs
juntas (3 da Carteira + 12 novas) num job só se mostrou lento em produção
-- cada config monta o dataset "Feature Stacked" do zero, e uma run manual
de teste passou de 1h ainda em andamento. Separar em workflows/schedules
independentes:
  - isola falha/lentidão de um lado do outro (a Carteira não fica
    esperando Cartões/Faltas terminar, nem vice-versa);
  - cada um pode ter seu próprio timeout ajustado à sua carga real;
  - dá pra disparar manualmente só a parte que interessa (ex.: testar só
    Cartões/Faltas sem re-rodar a Carteira inteira) -- mesmo espírito do
    endpoint dedicado de escanteios (`api/corners-model.js`), que também
    não compartilha pipeline com os classificadores 1X2/BTTS.

`prever_partidas_futuras_custom.py` continua com a Carteira (MERCADOS_
CARTEIRA) -- só a parte de Cartões/Faltas (MERCADOS_CARTOES_FALTAS_GERAL)
saiu de lá pra cá. As funções de fato (`processar_config`, resolução de
escopo de liga, busca de partidas-alvo) continuam as MESMAS, importadas
do módulo original -- nenhuma lógica duplicada, só o `main()`/escopo de
targets processados é diferente.

Variáveis de ambiente obrigatórias: SUPABASE_URL, SUPABASE_KEY (service_role).
"""

from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from prever_partidas_futuras_custom import (  # noqa: E402
    MERCADOS_CARTOES_FALTAS_GERAL,
    criar_supabase,
    processar_config,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("prever_cartoes_faltas_futuras")


def main() -> None:
    logger.info("Iniciando previsão em lote de Cartões/Faltas (geral) sobre partidas futuras...")
    supabase = criar_supabase()

    resp = (
        supabase.table("custom_model_configs")
        .select("id, name, target, todas_ligas, league_ids, seasons, model_artifacts")
        .eq("status", "treinado")
        .in_("target", list(MERCADOS_CARTOES_FALTAS_GERAL))
        .execute()
    )
    configs = resp.data or []
    if not configs:
        logger.warning("Nenhuma config treinada com target em %s. Encerrando.", sorted(MERCADOS_CARTOES_FALTAS_GERAL))
        return

    logger.info("%d config(ões) treinada(s) elegível(is) (target em %s).", len(configs), sorted(MERCADOS_CARTOES_FALTAS_GERAL))

    total_geral = 0
    configs_com_previsao = 0
    for cfg in configs:
        try:
            linhas = processar_config(supabase, cfg)
        except Exception:
            logger.exception("Falha processando config %r -- pulando, as outras continuam.", cfg.get("name"))
            continue
        if linhas > 0:
            configs_com_previsao += 1
            total_geral += linhas

    logger.info("Concluído: %d config(ões) geraram previsão, %d linha(s) salvas em model_predictions no total.",
                configs_com_previsao, total_geral)


if __name__ == "__main__":
    main()
