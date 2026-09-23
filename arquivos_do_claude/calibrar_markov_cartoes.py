#!/usr/bin/env python3
"""Calibra os multiplicadores de cartão (amarelo/vermelho) do motor de
simulação por Cadeia de Markov multi-evento (Fase 1 do plano da sessão),
com dado PRÓPRIO do projeto -- diferente de escanteio/falta (Achado 13,
ACHADOS_COMPORTAMENTO.md), cartão TEM minuto real no pipeline
(`match_events.minute`), então aqui o ganho é substituir uma forma externa
StatsBomb por uma calibrada direto nos dados do projeto.

Persiste em `league_markov_params`, mesma tabela/convenção de
`calibrar_markov_gols_chutes.py`.

O QUE CALIBRA, e de onde:
  - mult_estado_placar (cartao_amarelo, cartao_vermelho):
    `match_team_cartoes_estado` (migration 20260922110000, nova nesta
    sessão -- espelha match_team_game_state mas conta match_events).
  - mult_janela_pos_evento (cartao_amarelo, cartao_vermelho):
    `match_team_event_response` (migration 20260905100000, estendida nesta
    sessão com colunas cartoes_amarelos_pro/cartoes_vermelhos_pro/_contra)
    -- mede se a taxa de cartão sobe/desce nos 5/15min após um gol ou uma
    expulsão, comparado ao "regime" (comportamento de base) do MESMO estado.
  - mult_vantagem_numerica (cartao_amarelo, cartao_vermelho): mesmo PROXY
    documentado em calibrar_markov_gols_chutes.py -- usa janela=regime com
    evento=expulsao_pro/expulsao_contra como aproximação de "jogando em
    desvantagem/vantagem numérica há um tempo", contra janela=regime
    evento=nenhum como baseline. Mesma limitação conhecida (não há coluna
    de vantagem numérica contínua no schema).
  - mult_minuto_bin (cartao_amarelo, cartao_vermelho): histograma direto de
    `match_events.minute` -- ganho real sobre a forma StatsBomb do Achado
    9/13 (aqui é o dado do próprio pipeline, não uma amostra externa).

CONTROLE POR FORÇA DE EQUIPE: mesma ressalva de calibrar_markov_gols_
chutes.py -- agregação por liga aqui não controla por Elo individualmente.
Times fracos podem cometer mais faltas/cartões por jogarem mais na defesa
(ver Achado 17, ACHADOS_COMPORTAMENTO.md, sobre times fracos perderem mais
posse em todo canto do campo) -- controlar por Elo é validação futura, não
feita aqui.

Uso:
    set SUPABASE_URL=...
    set SUPABASE_KEY=sua_service_role_key
    python calibrar_markov_cartoes.py
"""

from __future__ import annotations

import logging
import os
import sys
from collections import defaultdict
from typing import Callable

from supabase import create_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
logger = logging.getLogger("calibrar_markov_cartoes")

N_MINIMO_LIGA_ESTADO = 2000     # minutos totais por estado
N_MINIMO_LIGA_MINUTO = 100      # nº de cartões mínimo pra confiar num histograma de minuto por liga
N_MINIMO_JANELA = 500           # minutos totais na janela (0-5/5-15/regime) pra confiar no multiplicador
MINUTE_BINS = [(0, 14), (15, 29), (30, 44), (45, 59), (60, 74), (75, 200)]


def obter_env(nome: str) -> str:
    valor = os.environ.get(nome)
    if not valor:
        sys.exit(f"Configure {nome} antes de rodar.")
    return valor


def _paginar(query_builder_factory: Callable[[int, int], object], tamanho_pagina: int = 1000) -> list[dict]:
    todas: list[dict] = []
    pagina = 0
    while True:
        inicio, fim = pagina * tamanho_pagina, pagina * tamanho_pagina + tamanho_pagina - 1
        linhas = query_builder_factory(inicio, fim).execute().data or []
        todas.extend(linhas)
        if len(linhas) < tamanho_pagina:
            break
        pagina += 1
    return todas


def _chunk(lista, tamanho):
    for i in range(0, len(lista), tamanho):
        yield lista[i:i + tamanho]


def _bin_de(minuto: float) -> str:
    for lo, hi in MINUTE_BINS:
        if lo <= minuto <= hi:
            return f"{lo}-{min(hi, 89) if hi < 200 else 89}"
    return "75-89"


def _liga_por_match(supabase, match_ids: list[int]) -> dict[int, int]:
    liga_por_match: dict[int, int] = {}
    for lote in _chunk(match_ids, 500):
        ms = _paginar(lambda ini, fim, lt=lote: (
            supabase.table("matches").select("id, league_id").in_("id", lt).order("id").range(ini, fim)
        ))
        for m in ms:
            if m["league_id"] is not None:
                liga_por_match[m["id"]] = m["league_id"]
    return liga_por_match


def carregar_cartoes_estado(supabase) -> dict[int, dict]:
    """{league_id: {estado: {"minutos":.., "amarelos_pro":.., "vermelhos_pro":..}}}."""
    linhas = _paginar(lambda ini, fim: (
        supabase.table("match_team_cartoes_estado")
        .select("match_id, estado, minutos, cartoes_amarelos_pro, cartoes_vermelhos_pro, placar_confere")
        .eq("placar_confere", True)
        .order("id")
        .range(ini, fim)
    ))
    liga_por_match = _liga_por_match(supabase, sorted({l["match_id"] for l in linhas}))

    por_liga: dict[int, dict] = defaultdict(lambda: defaultdict(lambda: {"minutos": 0.0, "amarelos_pro": 0, "vermelhos_pro": 0}))
    for l in linhas:
        liga_id = liga_por_match.get(l["match_id"])
        if liga_id is None:
            continue
        acc = por_liga[liga_id][l["estado"]]
        acc["minutos"] += float(l["minutos"])
        acc["amarelos_pro"] += int(l["cartoes_amarelos_pro"])
        acc["vermelhos_pro"] += int(l["cartoes_vermelhos_pro"])
    return por_liga


def calcular_mult_estado(por_liga: dict[int, dict], campo: str) -> dict[int, dict]:
    resultado: dict[int, dict] = {}
    for liga_id, por_estado in por_liga.items():
        minutos_totais = sum(v["minutos"] for v in por_estado.values())
        eventos_totais = sum(v[campo] for v in por_estado.values())
        if minutos_totais <= 0 or eventos_totais <= 0:
            continue
        taxa_geral = eventos_totais / minutos_totais
        multiplicadores = {}
        for estado, v in por_estado.items():
            if v["minutos"] < N_MINIMO_LIGA_ESTADO:
                continue
            multiplicadores[estado] = (v[campo] / v["minutos"]) / taxa_geral
        if len(multiplicadores) == 3:
            resultado[liga_id] = {"mult": multiplicadores, "amostra_n": int(minutos_totais)}
    return resultado


def carregar_resposta_evento_cartoes(supabase) -> list[dict]:
    return _paginar(lambda ini, fim: (
        supabase.table("match_team_event_response")
        .select("match_id, evento, janela, estado, minutos, cartoes_amarelos_pro, cartoes_vermelhos_pro")
        .order("id")
        .range(ini, fim)
    ))


def calcular_mult_janela(linhas: list[dict], liga_por_match: dict[int, int], campo: str) -> dict[int, dict]:
    """mult_janela_pos_evento: taxa em (evento, janela) / taxa em (evento=nenhum, janela=regime)
    DENTRO DO MESMO ESTADO -- é a comparação válida documentada na migration
    20260905100000 (v_resposta_evento)."""
    agregado: dict[int, dict] = defaultdict(lambda: defaultdict(lambda: {"minutos": 0.0, "n": 0}))
    for l in linhas:
        liga_id = liga_por_match.get(l["match_id"])
        if liga_id is None:
            continue
        chave = (l["evento"], l["janela"], l["estado"])
        acc = agregado[liga_id][chave]
        acc["minutos"] += float(l["minutos"])
        acc["n"] += int(l[campo])

    resultado: dict[int, dict] = defaultdict(dict)
    for liga_id, por_chave in agregado.items():
        # baseline por estado: (evento=nenhum, janela=regime)
        baseline = {}
        for (evento, janela, estado), acc in por_chave.items():
            if evento == "nenhum" and janela == "regime" and acc["minutos"] >= N_MINIMO_JANELA:
                baseline[estado] = acc["n"] / acc["minutos"]
        for (evento, janela, estado), acc in por_chave.items():
            if janela == "regime" and evento == "nenhum":
                continue  # é o próprio baseline
            if estado not in baseline or baseline[estado] <= 0 or acc["minutos"] < N_MINIMO_JANELA:
                continue
            taxa = acc["n"] / acc["minutos"]
            chave_destino = f"{evento}_{janela}" if evento != "nenhum" else f"nenhum_{janela}"
            resultado[liga_id][chave_destino] = {"valor": taxa / baseline[estado], "amostra_n": int(acc["minutos"])}
    return resultado


def carregar_minutos_cartoes(supabase) -> dict[int, dict[str, list[float]]]:
    eventos = _paginar(lambda ini, fim: (
        supabase.table("match_events")
        .select("match_id, minute, event_type")
        .in_("event_type", ["yellow_card", "red_card", "second_yellow_card"])
        .not_.is_("minute", "null")
        .order("id")
        .range(ini, fim)
    ))
    liga_por_match = _liga_por_match(supabase, sorted({e["match_id"] for e in eventos}))

    por_liga: dict[int, dict[str, list[float]]] = defaultdict(lambda: {"cartao_amarelo": [], "cartao_vermelho": []})
    for e in eventos:
        liga_id = liga_por_match.get(e["match_id"])
        if liga_id is None:
            continue
        destino = "cartao_amarelo" if e["event_type"] == "yellow_card" else "cartao_vermelho"
        por_liga[liga_id][destino].append(float(e["minute"]))
    return por_liga


def calcular_mult_minuto(por_liga_minutos: dict[int, dict[str, list[float]]]) -> dict[int, dict[str, dict]]:
    resultado: dict[int, dict[str, dict]] = defaultdict(dict)
    bins_labels = [f"{lo}-{min(hi,89) if hi<200 else 89}" for lo, hi in MINUTE_BINS]
    for liga_id, por_evento in por_liga_minutos.items():
        for evento, minutos in por_evento.items():
            if len(minutos) < N_MINIMO_LIGA_MINUTO:
                continue
            contagem_bin: dict[str, int] = defaultdict(int)
            for m in minutos:
                contagem_bin[_bin_de(m)] += 1
            media_por_bin = len(minutos) / len(MINUTE_BINS)
            mult = {b: contagem_bin.get(b, 0) / media_por_bin for b in bins_labels}
            resultado[liga_id][evento] = {"mult": mult, "amostra_n": len(minutos)}
    return resultado


def persistir(supabase, linhas: list[dict]) -> None:
    if not linhas:
        logger.warning("Nada pra persistir.")
        return
    for lote in _chunk(linhas, 500):
        supabase.table("league_markov_params").upsert(
            lote, on_conflict="league_id,evento,tipo,chave"
        ).execute()
    logger.info("%d linhas gravadas em league_markov_params.", len(linhas))


def main() -> None:
    supabase = create_client(obter_env("SUPABASE_URL"), obter_env("SUPABASE_KEY"))
    linhas: list[dict] = []

    logger.info("Carregando match_team_cartoes_estado (placar_confere=true)...")
    por_liga_estado = carregar_cartoes_estado(supabase)
    mult_estado_amarelo = calcular_mult_estado(por_liga_estado, "amarelos_pro")
    mult_estado_vermelho = calcular_mult_estado(por_liga_estado, "vermelhos_pro")
    for evento, mult_por_liga in [("cartao_amarelo", mult_estado_amarelo), ("cartao_vermelho", mult_estado_vermelho)]:
        for liga_id, info in mult_por_liga.items():
            for chave, valor in info["mult"].items():
                linhas.append({
                    "league_id": liga_id, "evento": evento, "tipo": "mult_estado_placar", "chave": chave,
                    "valor": round(valor, 4), "amostra_n": info["amostra_n"],
                    "origem": "match_team_cartoes_estado (placar_confere=true, agregado por liga, não controlado por Elo)",
                })

    logger.info("Carregando match_team_event_response (janela pós-evento e vantagem numérica proxy)...")
    resp = carregar_resposta_evento_cartoes(supabase)
    liga_por_match_resp = _liga_por_match(supabase, sorted({r["match_id"] for r in resp}))
    mult_janela_amarelo = calcular_mult_janela(resp, liga_por_match_resp, "cartoes_amarelos_pro")
    mult_janela_vermelho = calcular_mult_janela(resp, liga_por_match_resp, "cartoes_vermelhos_pro")
    for evento_cartao, mult_por_liga in [("cartao_amarelo", mult_janela_amarelo), ("cartao_vermelho", mult_janela_vermelho)]:
        for liga_id, chaves in mult_por_liga.items():
            for chave_janela, info in chaves.items():
                # separa em mult_janela_pos_evento (marcou/sofreu/expulsao_*_0-5/5-15)
                # de mult_vantagem_numerica (proxy: expulsao_*_regime)
                if chave_janela.endswith("_regime") and ("expulsao_pro" in chave_janela or "expulsao_contra" in chave_janela):
                    tipo = "mult_vantagem_numerica"
                    chave_final = "numerico_-1_regime" if "expulsao_pro" in chave_janela else "numerico_+1_regime"
                    origem = "PROXY: match_team_event_response, janela=regime após expulsao_pro/expulsao_contra -- aproxima 'jogando em desvantagem/vantagem numérica há tempo', sem coluna dedicada de vantagem numérica no schema."
                elif chave_janela.endswith("_regime"):
                    continue  # marcou/sofreu em regime não é o foco de mult_vantagem_numerica nem mult_janela_pos_evento (regime=baseline por definição)
                else:
                    tipo = "mult_janela_pos_evento"
                    chave_final = chave_janela
                    origem = "match_team_event_response (comparado contra evento=nenhum/janela=regime do mesmo estado, mesma disciplina de v_resposta_evento)"
                linhas.append({
                    "league_id": liga_id, "evento": evento_cartao, "tipo": tipo, "chave": chave_final,
                    "valor": round(info["valor"], 4), "amostra_n": info["amostra_n"], "origem": origem,
                })

    logger.info("Carregando histograma de minuto (match_events)...")
    por_liga_minutos = carregar_minutos_cartoes(supabase)
    mult_minuto = calcular_mult_minuto(por_liga_minutos)
    for liga_id, por_evento in mult_minuto.items():
        for evento, info in por_evento.items():
            for chave, valor in info["mult"].items():
                linhas.append({
                    "league_id": liga_id, "evento": evento, "tipo": "mult_minuto_bin", "chave": chave,
                    "valor": round(valor, 4), "amostra_n": info["amostra_n"],
                    "origem": "match_events.minute (histograma direto por liga -- dado próprio, substitui a forma StatsBomb do Achado 9/13)",
                })

    persistir(supabase, linhas)


if __name__ == "__main__":
    main()
