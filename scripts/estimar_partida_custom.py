#!/usr/bin/env python3
"""Estimativa sob demanda de um modelo customizado pra UMA partida específica.

Disparado pelo botão "Estimar com modelo personalizado" na página do jogo
(src/pages/AnaliseEstatisticaJogo.jsx). Diferente de treinar_modelo_custom.py/
treinar_modelo_custom_wf.py (que treinam e fazem backtest histórico), este
script NÃO faz split cronológico treino/teste pra avaliação -- ele prevê só
a partida-alvo.

Dois caminhos, dependendo se já existe um artefato persistido pro
algoritmo/grupo pedido em custom_model_configs.model_artifacts (ver
model_artifacts.py):
  - RÁPIDO (com artefato): carrega o modelo já treinado do bucket
    custom-model-artifacts e só PREVÊ na partida-alvo -- segundos, sem
    retreinar.
  - LENTO (sem artefato -- config antiga, ou esse passo falhou no treino):
    retreina o algoritmo escolhido em TODO o histórico disponível no escopo
    da config (exceto a própria partida-alvo), igual ao pipeline principal
    faz (`rodar_predicoes.py`: "o modelo final é sempre refeito no dataset
    INTEIRO"). Ao final, persiste o artefato resultante, pra QUALQUER
    estimativa futura com essa mesma config+algoritmo cair no caminho
    rápido.

Em ambos os casos, a montagem de features da partida-alvo (elo/forma/xG
pré-jogo etc.) é feita do zero -- ela depende da data da partida e do
histórico recente dos dois times, não do modelo em si, então não tem como
"pular" mesmo com o modelo já treinado. É bem mais rápida que o retreino
(sem nenhum fit de gradient boosting), mas ainda envolve várias queries no
Supabase.

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
import modelos_ml as ml
import model_artifacts as ma
from treinar_modelo_custom import TARGETS, _TARGET_PRED_META, _migrar_features

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("estimar_partida_custom")

# Mínimo de partidas no pool de treino (histórico do escopo da config, sem
# contar a partida-alvo) pra considerar a estimativa confiável o bastante
# pra calcular. Abaixo disso, mais vale errar cedo com uma mensagem clara do
# que devolver uma probabilidade baseada em ruído. Só se aplica ao caminho
# lento (com artefato já pronto, não há retreino a validar).
MIN_PARTIDAS_TREINO = 100


def criar_supabase():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])


def atualizar_status(supabase, request_id: str, status: str, **extras):
    supabase.table("custom_model_ondemand_predictions").update({"status": status, **extras}).eq("id", request_id).execute()


def marcar_erro(supabase, request_id: str, msg: str):
    try:
        atualizar_status(supabase, request_id, "erro", error_message=msg[:2000])
    except Exception as e:
        logger.error("Falha ao gravar erro no Supabase: %s", e)


def persistir_artefato_novo(supabase, config_id: str, chave: str, estado: dict) -> None:
    """Sobe o artefato recém-treinado e atualiza custom_model_configs.
    model_artifacts (merge, não sobrescreve outras chaves) -- pra qualquer
    estimativa futura dessa config+algoritmo cair no caminho rápido."""
    try:
        path = ma.salvar_artefato(supabase, config_id, chave, estado)
        cfg_atual = supabase.table("custom_model_configs").select("model_artifacts").eq("id", config_id).single().execute()
        artefatos = dict(cfg_atual.data.get("model_artifacts") or {})
        artefatos[chave] = {"path": path, "trained_at": datetime.now(timezone.utc).isoformat()}
        supabase.table("custom_model_configs").update({"model_artifacts": artefatos}).eq("id", config_id).execute()
        logger.info("Artefato persistido em %s -- próximas estimativas dessa config+algoritmo serão instantâneas.", path)
    except Exception as exc:
        # Não derruba a estimativa em si por causa disso -- só loga.
        logger.warning("Falha ao persistir artefato (a estimativa em si já foi calculada): %s", exc)


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

        features_req = cfg["features"] or []
        target_key = cfg.get("target") or "1x2"
        hyperparameters = cfg.get("hyperparameters") or {}
        todas_ligas = bool(cfg.get("todas_ligas"))
        league_ids = cfg.get("league_ids") or None
        seasons = cfg.get("seasons") or None
        stacking_groups = cfg.get("stacking_groups") or []
        algoritmos_validos = cfg.get("algorithms") or ([cfg["algorithm"]] if cfg.get("algorithm") else [])
        artefato_existente = (cfg.get("model_artifacts") or {}).get(algoritmo_escolhido)

        if not features_req:
            raise ValueError("Configuração sem features selecionadas.")
        target_info = TARGETS.get(target_key)
        if not target_info:
            raise ValueError(f"Target {target_key!r} não suportado.")
        target_col = target_info["coluna"]
        tipo = target_info["tipo"]

        eh_stacking = algoritmo_escolhido.startswith("stacking:")
        algos_do_grupo: list[str] = []
        if eh_stacking:
            nome_grupo = algoritmo_escolhido[len("stacking:"):]
            grupo = next((g for g in stacking_groups if g.get("name") == nome_grupo), None)
            if not grupo:
                raise ValueError(f"Grupo de stacking {nome_grupo!r} não existe nesta configuração.")
            algos_do_grupo = [a for a in (grupo.get("algorithms") or []) if a in algoritmos_validos]
            if len(algos_do_grupo) < 2:
                raise ValueError(f"Grupo de stacking {nome_grupo!r} tem menos de 2 algoritmos válidos.")
        elif algoritmo_escolhido not in algoritmos_validos:
            raise ValueError(f"Algoritmo {algoritmo_escolhido!r} não faz parte desta configuração.")

        logger.info(
            "Config: target=%s, algoritmo=%s, %d features, match_id=%s, artefato_existente=%s",
            target_key, algoritmo_escolhido, len(features_req), match_id, bool(artefato_existente),
        )

        # Monta o dataset com o histórico do escopo da config + a partida-alvo
        # (mesmo pipeline de features de montar_dataset_ml_empilhado -- ver
        # docstring do parâmetro match_id_extra em dados_historicos.py).
        # Necessário nos dois caminhos (rápido e lento): a feature da
        # partida-alvo depende da data dela e do histórico recente dos times,
        # não do modelo já treinado.
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

        features_usadas = _migrar_features(features_req)
        if target_col == "resultado_btts" and "resultado_btts" not in dataset.columns:
            if "home_goals" in dataset.columns and "away_goals" in dataset.columns:
                dataset["resultado_btts"] = ((dataset["home_goals"] > 0) & (dataset["away_goals"] > 0)).astype(int)

        features_disponiveis = set(dataset.columns)
        faltando = [f for f in features_usadas if f not in features_disponiveis]
        if faltando:
            logger.warning("Features ignoradas (não disponíveis no dataset): %s", faltando)
            features_usadas = [f for f in features_usadas if f in features_disponiveis]
        if not features_usadas:
            raise RuntimeError("Nenhuma feature válida disponível no dataset.")

        cols_necessarias = list(dict.fromkeys(
            ml.CAT_FEATURES + features_usadas + [target_col, "match_date", "match_id"]
        ))
        cols_presentes = [c for c in cols_necessarias if c in dataset.columns]
        dataset = dataset[cols_presentes].copy()

        linha_alvo = dataset[dataset["match_id"] == match_id].reset_index(drop=True)
        if linha_alvo.empty:
            raise RuntimeError(
                f"Partida match_id={match_id} não apareceu no dataset final — "
                "provavelmente fora do escopo de ligas/temporadas da configuração."
            )

        n_treino_usado: int | None = None

        if artefato_existente:
            # ── Caminho rápido: carrega o modelo já treinado e só prevê ──
            logger.info("Artefato encontrado (%s) -- pulando retreino.", artefato_existente["path"])
            estado = ma.carregar_artefato(supabase, artefato_existente["path"])
            probabilidades, classes_final = ma.prever_com_estado(estado, linha_alvo)
        else:
            # ── Caminho lento: retreina do zero (e persiste pro futuro) ──
            pool_treino = dataset[dataset["match_id"] != match_id].dropna(subset=[target_col]).reset_index(drop=True)
            if len(pool_treino) < MIN_PARTIDAS_TREINO:
                raise RuntimeError(
                    f"Histórico de treino insuficiente pro escopo da configuração "
                    f"({len(pool_treino)} partidas, mínimo {MIN_PARTIDAS_TREINO})."
                )
            n_treino_usado = len(pool_treino)
            logger.info("Sem artefato -- retreinando em %d partidas | prevendo match_id=%s", len(pool_treino), match_id)

            if eh_stacking:
                estado = ma.treinar_estado_stacking(algos_do_grupo, features_usadas, hyperparameters, pool_treino, target_col, tipo)
            else:
                train_fit, val_fit = ma.split_treino_val(pool_treino)
                estado, _ = ma.treinar_e_prever(algoritmo_escolhido, features_usadas, hyperparameters, train_fit, val_fit, {}, target_col, tipo)

            probabilidades, classes_final = ma.prever_com_estado(estado, linha_alvo)
            persistir_artefato_novo(supabase, config_id, algoritmo_escolhido, estado)

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
            n_treino=n_treino_usado,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        logger.info("✅ Estimativa concluída: %s", probs_dict)

    except Exception as exc:
        logger.exception("Erro na estimativa sob demanda: %s", exc)
        marcar_erro(supabase, request_id, str(exc))
        sys.exit(1)


if __name__ == "__main__":
    main()
