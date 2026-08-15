#!/usr/bin/env python3
"""Aplica cada modelo customizado JÁ TREINADO (custom_model_configs,
model_artifacts) sobre as partidas AINDA NÃO disputadas dentro do escopo
de liga da configuração, persistindo em `model_predictions` -- mesma
tabela/convenção de nome (`"{config.name} [{algoritmo}]"` /
`"{config.name} [Stacking: {grupo}]"`) que `treinar_modelo_custom_wf.py`
já usa pro conjunto de TESTE histórico.

Por que isso existe: até esta mudança, `model_predictions` só tinha
previsão de modelo customizado pra partidas `status='finished'` (o
conjunto de teste do treino) -- CONFIRMADO por query real (achado numa
sessão de suporte: "até agora nenhum modelo personalizado treinado
conseguiu encontrar aposta no paper trading"). A Carteira (Paper Trading,
`api/model-maintenance.js` -> `apostarCarteira`) procura previsão em
`model_predictions` pra partidas `status='scheduled'`; sem nenhuma linha
lá, nenhum modelo customizado nunca tinha candidato a apostar, por mais
bem treinado que estivesse.

Já existia uma ferramenta de estimativa sob demanda, 1 partida por vez
(`estimar_partida_custom.py`, botão "Estimar com modelo personalizado"),
mas ela grava só em `custom_model_ondemand_predictions` (tabela efêmera,
só pra exibir na tela daquela partida) -- nunca em `model_predictions`,
então a Carteira não enxerga. Reaproveita a MESMA infraestrutura (
`dados_historicos.montar_dataset_ml_empilhado`, `model_artifacts.
carregar_artefato`/`prever_com_estado`, `treinar_modelo_custom.
_TARGET_PRED_META`), mas em LOTE: monta o dataset "Feature Stacked"
pesado (minutos) UMA VEZ por config (não 1x por partida -- inviável,
ver `JANELA_DIAS` abaixo e o comentário de `match_ids_extra` em
dados_historicos.py) e aplica todos os artefatos treinados dela sobre
todas as partidas-alvo de uma vez.

Escopo das configs processadas:
  - status='treinado' (nunca dispara treino, só aplica o que já existe)
  - target em MERCADOS_CARTEIRA (os 3 que a Carteira sabe apostar: 1x2/
    over_under_2.5/btts) -- outros targets (faixa_gols, corners_*) ainda
    não têm mercado de odds capturado nesse projeto, previsão pra eles
    seria sempre "0 apostas possíveis" na Carteira, sem uso real ainda.
  - model_artifacts não vazio (config sem NENHUM artefato persistido é
    pulada -- nada pra aplicar).

Escopo das partidas-alvo, por config:
  - status='scheduled'
  - league_id na INTERSEÇÃO entre o escopo de liga da config (league_ids
    explícito, ou todas as ligas se todas_ligas=true, ou o benchmark de 6
    ligas por padrão) e as ligas que TÊM odds ao vivo sincronizadas
    (`liga_oddspapi_tournament` -- sem odds capturada pra essa liga, a
    Carteira nunca vai conseguir casar previsão com odds mesmo assim, a
    previsão seria desperdiçada).
  - match_date dentro de JANELA_DIAS a partir de agora (mesmo espírito da
    janela de odds ao vivo, `JANELA_CANDIDATOS_DIAS` em
    api/model-maintenance.js -- um pouco mais generosa aqui de propósito,
    já que incluir uma partida extra no dataset é barato).

Cada (config, artefato) é isolado em try/except -- uma falha não derruba
o resto do lote, igual ao padrão já usado em `rodar_predicoes.py`.

Variáveis de ambiente obrigatórias: SUPABASE_URL, SUPABASE_KEY (service_role).
"""

from __future__ import annotations

import logging
import os
import sys

from supabase import Client, create_client

sys.path.insert(0, os.path.dirname(__file__))
import dados_historicos as dh
import model_artifacts as ma
from treinar_modelo_custom import TARGETS, salvar_predicoes_no_banco

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("prever_partidas_futuras_custom")

# Só os mercados que a Carteira (Paper Trading) sabe apostar hoje --
# MERCADOS_CARTEIRA_SUPORTADOS em api/model-maintenance.js.
MERCADOS_CARTEIRA = {"1x2", "over_under_2.5", "btts"}

# Um pouco mais larga que a janela de odds ao vivo (21 dias, ver
# JANELA_CANDIDATOS_DIAS em api/model-maintenance.js) -- incluir uma
# partida a mais no dataset não tem custo real (a Carteira só aposta
# quando também existe odd capturada; se essa janela pegar partida sem
# odd ainda, a previsão só fica esperando o sync de odds alcançar).
JANELA_DIAS = 30


def criar_supabase() -> Client:
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])


def obter_ligas_com_odds_ao_vivo(supabase: Client) -> set[int]:
    """Ligas com torneio da OddsPapi resolvido -- fonte de verdade
    dinâmica, mesma tabela usada por tarefaOddsSyncLote (api/model-
    maintenance.js). Sem isso a partida nunca vai ter odd real pra casar
    na Carteira, então prever pra ela seria desperdiçado."""
    resp = supabase.table("liga_oddspapi_tournament").select("league_id").execute()
    return {linha["league_id"] for linha in (resp.data or [])}


def resolver_escopo_ligas(supabase: Client, cfg: dict) -> set[int] | None:
    """None = sem restrição de liga (todas_ligas=true)."""
    league_ids = cfg.get("league_ids") or None
    if league_ids:
        return set(league_ids)
    if cfg.get("todas_ligas"):
        return None
    return set(dh.obter_ids_ligas(supabase, dh.LIGAS_MODEL_BENCHMARKING).values())


def buscar_partidas_alvo(supabase: Client, ligas_alvo: set[int]) -> list[int]:
    if not ligas_alvo:
        return []
    from datetime import datetime, timedelta, timezone

    ate_iso = (datetime.now(timezone.utc) + timedelta(days=JANELA_DIAS)).isoformat()
    resp = (
        supabase.table("matches")
        .select("id")
        .in_("league_id", list(ligas_alvo))
        .eq("status", "scheduled")
        .lte("match_date", ate_iso)
        .execute()
    )
    return [linha["id"] for linha in (resp.data or [])]


def nome_do_modelo(config_name: str, chave_artefato: str) -> str:
    """Mesma convenção de treinar_modelo_custom_wf.py: algoritmo puro vira
    "{config} [{algo}]", grupo de stacking (chave prefixada "stacking:")
    vira "{config} [Stacking: {grupo}]"."""
    if chave_artefato.startswith("stacking:"):
        grupo = chave_artefato[len("stacking:"):]
        return f"{config_name} [Stacking: {grupo}]"
    return f"{config_name} [{chave_artefato}]"


def processar_config(supabase: Client, cfg: dict) -> int:
    """Retorna o número de linhas de model_predictions gravadas pra essa config."""
    config_id = cfg["id"]
    config_name = cfg["name"]
    target_key = cfg.get("target") or "1x2"
    artefatos = cfg.get("model_artifacts") or {}
    todas_ligas = bool(cfg.get("todas_ligas"))
    league_ids = cfg.get("league_ids") or None
    seasons = cfg.get("seasons") or None

    if not artefatos:
        logger.info("Config %r (%s): sem artefato treinado/persistido -- pulando.", config_name, config_id)
        return 0

    target_info = TARGETS.get(target_key)
    if not target_info:
        logger.warning("Config %r: target %r não suportado -- pulando.", config_name, target_key)
        return 0
    target_col = target_info["coluna"]

    ligas_com_odds = obter_ligas_com_odds_ao_vivo(supabase)
    escopo = resolver_escopo_ligas(supabase, cfg)
    ligas_alvo = ligas_com_odds if escopo is None else (escopo & ligas_com_odds)
    match_ids_alvo = buscar_partidas_alvo(supabase, ligas_alvo)
    if not match_ids_alvo:
        logger.info("Config %r: nenhuma partida agendada dentro do escopo (ligas=%s) nos próximos %d dias -- pulando.",
                    config_name, sorted(ligas_alvo), JANELA_DIAS)
        return 0

    logger.info("Config %r: %d partida(s) candidata(s), %d artefato(s) treinado(s) (%s).",
                config_name, len(match_ids_alvo), len(artefatos), list(artefatos.keys()))

    dataset = dh.montar_dataset_ml_empilhado(
        supabase,
        anos_por_liga=None if (todas_ligas or seasons) else 6,
        todas_as_ligas=todas_ligas,
        league_ids_manual=league_ids,
        seasons=seasons,
        match_ids_extra=match_ids_alvo,
    )
    if dataset.empty:
        logger.warning("Config %r: dataset vazio -- pulando.", config_name)
        return 0

    if target_col == "resultado_btts" and "resultado_btts" not in dataset.columns:
        if "home_goals" in dataset.columns and "away_goals" in dataset.columns:
            dataset["resultado_btts"] = ((dataset["home_goals"] > 0) & (dataset["away_goals"] > 0)).astype(int)

    linhas_alvo = dataset[dataset["match_id"].isin(match_ids_alvo)].reset_index(drop=True)
    if linhas_alvo.empty:
        logger.warning(
            "Config %r: nenhuma das %d partida(s) candidata(s) sobreviveu ao dataset final "
            "(provável falta de histórico recente dos times pra montar a média móvel) -- pulando.",
            config_name, len(match_ids_alvo),
        )
        return 0

    total_linhas = 0
    for chave_artefato, artefato in artefatos.items():
        model_name = nome_do_modelo(config_name, chave_artefato)
        try:
            estado = ma.carregar_artefato(supabase, artefato["path"])
            probabilidades, classes_final = ma.prever_com_estado(estado, linhas_alvo)
            salvar_predicoes_no_banco(supabase, linhas_alvo, probabilidades, classes_final, model_name, target_key)
            total_linhas += len(linhas_alvo)
        except Exception:
            logger.exception("Falha aplicando %r (config %r) -- pulando esse artefato, os outros continuam.",
                              model_name, config_name)

    return total_linhas


def main() -> None:
    logger.info("Iniciando previsão em lote de modelos customizados sobre partidas futuras...")
    supabase = criar_supabase()

    resp = (
        supabase.table("custom_model_configs")
        .select("id, name, target, todas_ligas, league_ids, seasons, model_artifacts")
        .eq("status", "treinado")
        .in_("target", list(MERCADOS_CARTEIRA))
        .execute()
    )
    configs = resp.data or []
    if not configs:
        logger.warning("Nenhuma config treinada com target em %s. Encerrando.", sorted(MERCADOS_CARTEIRA))
        return

    logger.info("%d config(ões) treinada(s) elegível(is) (target em %s).", len(configs), sorted(MERCADOS_CARTEIRA))

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
