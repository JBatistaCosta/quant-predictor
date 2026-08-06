#!/usr/bin/env python3
"""Estimativa sob demanda de um modelo customizado pra UMA partida específica.

Disparado pelo botão "Estimar com modelo personalizado" na página do jogo
(src/pages/AnaliseEstatisticaJogo.jsx). Este script NUNCA treina -- ele só
APLICA um modelo já treinado e persistido (custom_model_configs.
model_artifacts, ver model_artifacts.py) na partida-alvo. Treino acontece
exclusivamente via o botão "Treinar" do painel Treino Customizado
(treinar_modelo_custom.py / treinar_modelo_custom_wf.py, que já persistem
o artefato final ao fim de um treino bem-sucedido).

Se o algoritmo/grupo pedido não tem artefato persistido, o erro é
levantado aqui (e também validado antes, em api/model-maintenance.js,
que evita disparar o workflow nesse caso) -- a mensagem já direciona o
usuário a treinar a configuração primeiro.

A montagem de features da partida-alvo (elo/forma/xG pré-jogo etc.) é
feita do zero mesmo assim -- ela depende da data da partida e do
histórico recente dos dois times, não do modelo em si, então não tem como
"pular" mesmo com o modelo já treinado. Envolve algumas queries no
Supabase, mas nenhum fit de gradient boosting -- questão de segundos, não
minutos.

Variáveis de ambiente:
  SUPABASE_URL, SUPABASE_KEY   — acesso ao banco (service_role)
  REQUEST_ID                   — UUID da linha em custom_model_ondemand_predictions

Erros fatais atualizam custom_model_ondemand_predictions.status='erro' e
error_message com a mensagem antes de sair com exit code 1.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone

from supabase import create_client

sys.path.insert(0, os.path.dirname(__file__))
import dados_historicos as dh
import model_artifacts as ma
from treinar_modelo_custom import TARGETS, _TARGET_PRED_META

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("estimar_partida_custom")


def criar_supabase():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])


def atualizar_status(supabase, request_id: str, status: str, **extras):
    supabase.table("custom_model_ondemand_predictions").update({"status": status, **extras}).eq("id", request_id).execute()


def marcar_erro(supabase, request_id: str, msg: str):
    try:
        atualizar_status(supabase, request_id, "erro", error_message=msg[:2000])
    except Exception as e:
        logger.error("Falha ao gravar erro no Supabase: %s", e)


def main():
    request_id = os.environ.get("REQUEST_ID", "").strip()
    if not request_id:
        logger.error("REQUEST_ID não definido.")
        sys.exit(1)

    supabase = criar_supabase()
    atualizar_status(supabase, request_id, "processando")

    try:
        req_resp = supabase.table("custom_model_ondemand_predictions").select("*").eq("id", request_id).single().execute()
        if not req_resp.data:
            raise ValueError(f"Requisição {request_id!r} não encontrada.")
        req = req_resp.data
        config_id = req["config_id"]
        match_id = req["match_id"]
        algoritmo_escolhido = req["algorithm"]

        cfg_resp = supabase.table("custom_model_configs").select("*").eq("id", config_id).single().execute()
        if not cfg_resp.data:
            raise ValueError(f"Configuração {config_id!r} não encontrada.")
        cfg = cfg_resp.data

        target_key = cfg.get("target") or "1x2"
        todas_ligas = bool(cfg.get("todas_ligas"))
        league_ids = cfg.get("league_ids") or None
        seasons = cfg.get("seasons") or None
        artefato = (cfg.get("model_artifacts") or {}).get(algoritmo_escolhido)

        if not artefato:
            raise ValueError(
                f"O algoritmo/grupo {algoritmo_escolhido!r} dessa configuração ainda não tem um modelo "
                "treinado e persistido — treine (ou retreine) a configuração no painel Treino Customizado primeiro."
            )

        target_info = TARGETS.get(target_key)
        if not target_info:
            raise ValueError(f"Target {target_key!r} não suportado.")
        target_col = target_info["coluna"]

        logger.info("Config: target=%s, algoritmo=%s, match_id=%s, artefato=%s", target_key, algoritmo_escolhido, match_id, artefato["path"])

        # Monta o dataset só pra extrair a feature da partida-alvo (elo/
        # forma/xG pré-jogo etc., calculadas a partir do histórico recente
        # dos dois times) -- mesmo pipeline de montar_dataset_ml_empilhado
        # usado no treino, ver docstring do parâmetro match_id_extra em
        # dados_historicos.py. Não precisamos do pool de treino aqui: o
        # modelo já foi treinado e persistido por treinar_modelo_custom.py/
        # treinar_modelo_custom_wf.py.
        dataset = dh.montar_dataset_ml_empilhado(
            supabase,
            anos_por_liga=None if (todas_ligas or seasons) else 6,
            todas_as_ligas=todas_ligas,
            league_ids_manual=league_ids,
            seasons=seasons,
            match_id_extra=match_id,
        )
        if dataset.empty:
            raise RuntimeError("Dataset vazio — verifique o escopo de ligas/temporadas da configuração.")

        if target_col == "resultado_btts" and "resultado_btts" not in dataset.columns:
            if "home_goals" in dataset.columns and "away_goals" in dataset.columns:
                dataset["resultado_btts"] = ((dataset["home_goals"] > 0) & (dataset["away_goals"] > 0)).astype(int)

        linha_alvo = dataset[dataset["match_id"] == match_id].reset_index(drop=True)
        if linha_alvo.empty:
            raise RuntimeError(
                f"Partida match_id={match_id} não apareceu no dataset final — "
                "provavelmente fora do escopo de ligas/temporadas da configuração."
            )

        estado = ma.carregar_artefato(supabase, artefato["path"])
        probabilidades, classes_final = ma.prever_com_estado(estado, linha_alvo)

        meta = _TARGET_PRED_META[target_key]
        class_to_sel = meta["class_to_sel"]
        probs_dict: dict[str, float] = {}
        for idx, cls in enumerate(classes_final):
            sel = class_to_sel.get(int(cls))
            if sel is not None:
                probs_dict[sel] = round(float(probabilidades[0, idx]), 6)

        soma = sum(probs_dict.values())
        if soma <= 0:
            raise RuntimeError("Probabilidades calculadas somam zero — resultado inválido.")
        fair_odds = {sel: (round(1.0 / p, 3) if p > 0 else None) for sel, p in probs_dict.items()}

        atualizar_status(
            supabase, request_id, "concluido",
            probabilities=probs_dict, fair_odds=fair_odds,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        logger.info("✅ Estimativa concluída: %s", probs_dict)

    except Exception as exc:
        logger.exception("Erro na estimativa sob demanda: %s", exc)
        marcar_erro(supabase, request_id, str(exc))
        sys.exit(1)


if __name__ == "__main__":
    main()
