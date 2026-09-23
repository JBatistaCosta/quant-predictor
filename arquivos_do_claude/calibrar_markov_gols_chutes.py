#!/usr/bin/env python3
"""Calibra, com dado PRÓPRIO do projeto, os multiplicadores de gol/chute do
motor de simulação por Cadeia de Markov multi-evento (Fase 1 do plano da
sessão) -- substituindo os multiplicadores hoje hardcoded em
`src/pages/AnaliseEvento.jsx` (MARKOV_STATE_MULTIPLIERS/MARKOV_MINUTE_BINS,
calibrados uma única vez sobre a Copa do Mundo 2022, StatsBomb, 64 jogos,
não recalibrados desde então).

Persiste em `league_markov_params` (migration
20260922100000_create_league_markov_params.sql), granularidade por liga
(fallback global com league_id=NULL quando a amostra da liga é pequena
demais -- mesmo espírito de N_MINIMO_LIGA já usado em
calibrar_disp_r_chutes.py).

O QUE CALIBRA, e de onde:
  - mult_estado_placar (gol, chute): `match_team_game_state` (migration
    20260904100000) -- já filtra por placar_confere=true e é o dado
    PRÓPRIO do pipeline (não StatsBomb externo).
  - mult_minuto_bin (gol, chute): histograma direto de
    `match_shots_fotmob.minute` (+ minute_added) -- minuto EXIBÍVEL, não o
    `clock` monótono. Isso é seguro aqui porque é só um histograma agregado
    sem ordenação entre chutes (diferente do problema que motivou o `clock`
    em match_goal_timeline) -- um chute aos 45+3 cai no bin 45-59
    corretamente, só não seria seguro se precisássemos ORDENAR chutes entre
    si.
  - mult_vantagem_numerica (gol, chute): PROXY, não medida direta. O
    schema atual não tem uma coluna de "jogadores em campo" contínua ao
    longo da partida -- match_team_event_response (fase 3) só marca
    expulsao_pro/expulsao_contra nas janelas 0-5/5-15min após o cartão,
    depois cai em "regime" (que mistura "voltou a ficar muito tempo sem
    evento com 11 em campo" com "muito tempo sem evento MAS ainda jogando
    com um a menos"). Usado aqui como proxy: razão entre a produção em
    janela=regime COM evento mais recente expulsao_pro/expulsao_contra
    (aproximação de "ainda em desvantagem/vantagem numérica, faz tempo")
    contra janela=regime evento=nenhum (nunca teve expulsão), dentro do
    MESMO estado de placar. LIMITAÇÃO CONHECIDA, documentada em `origem` --
    refinar exigiria uma coluna própria de vantagem numérica persistida
    (fora do escopo desta calibração).

CHUTE NO ALVO -- diferente de gol/chute, `match_team_game_state` NÃO separa
chutes no alvo dos demais (só `chutes_pro`/`xg_pro` agregados). Sem uma
coluna dedicada, este script REAPROVEITA os multiplicadores de `chute` pra
`chute_no_alvo` (mesma forma temporal/de estado que o chute total, já que
não há dado próprio que distinga os dois por contexto) -- registrado
explicitamente em `origem` como proxy, não como medição direta. Refinar no
futuro exigiria uma coluna `chutes_no_alvo_pro` em match_team_game_state
(nova migration), fora do escopo desta calibração.

CONTROLE POR FORÇA DE EQUIPE -- ARMADILHA JÁ DOCUMENTADA (ACHADOS_
COMPORTAMENTO.md): efeito por estado de placar que não é controlado por
força de equipe pode somar ou inverter. Este script agrega em nível de
LIGA (todos os times juntos, ponderado por minutos) -- não controla por
Elo individualmente. Antes de usar em produção, comparar o resultado aqui
contra `v_game_state_por_forca` (controlada por Elo) pra confirmar que o
sinal sobrevive; ver seção de validação no plano da sessão.

Uso:
    set SUPABASE_URL=...
    set SUPABASE_KEY=sua_service_role_key
    python calibrar_markov_gols_chutes.py
"""

from __future__ import annotations

import logging
import os
import sys
from collections import defaultdict
from typing import Callable

from supabase import create_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
logger = logging.getLogger("calibrar_markov_gols_chutes")

N_MINIMO_LIGA_ESTADO = 2000  # minutos totais, não partidas -- estado raro (ex. perdendo por muito tempo) precisa de volume
N_MINIMO_LIGA_MINUTO = 200   # nº de gols/chutes mínimo pra confiar num histograma de minuto por liga
MINUTE_BINS = [(0, 14), (15, 29), (30, 44), (45, 59), (60, 74), (75, 200)]  # 200 cobre acréscimos longos e prorrogação


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


def _bin_de(minuto: float) -> str:
    for lo, hi in MINUTE_BINS:
        if lo <= minuto <= hi:
            return f"{lo}-{min(hi, 89) if hi < 200 else 89}"
    return "75-89"


def carregar_game_state(supabase) -> dict[int, dict]:
    """match_team_game_state + league_id de matches, só placar_confere=true.
    Devolve {league_id: {estado: {"minutos":..,"gols_pro":..,"chutes_pro":..}}}."""
    linhas = _paginar(lambda ini, fim: (
        supabase.table("match_team_game_state")
        .select("match_id, estado, minutos, gols_pro, chutes_pro, placar_confere")
        .eq("placar_confere", True)
        .order("id")
        .range(ini, fim)
    ))
    match_ids = sorted({l["match_id"] for l in linhas})
    liga_por_match: dict[int, int] = {}
    for lote in _chunk(match_ids, 500):
        ms = _paginar(lambda ini, fim, lt=lote: (
            supabase.table("matches").select("id, league_id").in_("id", lt).order("id").range(ini, fim)
        ))
        for m in ms:
            if m["league_id"] is not None:
                liga_por_match[m["id"]] = m["league_id"]

    por_liga: dict[int, dict] = defaultdict(lambda: defaultdict(lambda: {"minutos": 0.0, "gols_pro": 0, "chutes_pro": 0}))
    for l in linhas:
        liga_id = liga_por_match.get(l["match_id"])
        if liga_id is None:
            continue
        acc = por_liga[liga_id][l["estado"]]
        acc["minutos"] += float(l["minutos"])
        acc["gols_pro"] += int(l["gols_pro"])
        acc["chutes_pro"] += int(l["chutes_pro"])
    return por_liga


def _chunk(lista, tamanho):
    for i in range(0, len(lista), tamanho):
        yield lista[i:i + tamanho]


def calcular_mult_estado(por_liga: dict[int, dict], campo: str) -> dict[int, dict]:
    """mult_estado_placar pra `campo` ('gols_pro' ou 'chutes_pro'), por liga.
    Normalizado pra que a média ponderada por minutos dos 3 estados dê 1 --
    preserva o total esperado (mesma taxa base) quando aplicado."""
    resultado: dict[int, dict] = {}
    for liga_id, por_estado in por_liga.items():
        minutos_totais = sum(v["minutos"] for v in por_estado.values())
        eventos_totais = sum(v[campo] for v in por_estado.values())
        if minutos_totais <= 0 or eventos_totais <= 0:
            continue
        taxa_geral = eventos_totais / minutos_totais
        multiplicadores = {}
        n_min_estado = min(v["minutos"] for v in por_estado.values()) if por_estado else 0
        for estado, v in por_estado.items():
            if v["minutos"] < N_MINIMO_LIGA_ESTADO:
                continue
            taxa_estado = v[campo] / v["minutos"]
            multiplicadores[estado] = taxa_estado / taxa_geral
        if len(multiplicadores) == 3:  # só grava se as 3 chaves (perdendo/empatando/ganhando) têm amostra suficiente
            resultado[liga_id] = {"mult": multiplicadores, "amostra_n": int(minutos_totais)}
    return resultado


def carregar_minutos_shotmap(supabase) -> dict[int, dict[str, list[tuple[float, bool]]]]:
    """Histograma de minuto pra gol e chute, por liga -- direto de
    match_shots_fotmob (minute exibível). Devolve {league_id: {"chute": [minutos...], "gol": [minutos...]}}."""
    shots = _paginar(lambda ini, fim: (
        supabase.table("match_shots_fotmob")
        .select("match_id, minute, minute_added, event_type, period")
        .neq("period", "PenaltyShootout")
        .not_.is_("minute", "null")
        .order("id")
        .range(ini, fim)
    ))
    match_ids = sorted({s["match_id"] for s in shots})
    liga_por_match: dict[int, int] = {}
    for lote in _chunk(match_ids, 500):
        ms = _paginar(lambda ini, fim, lt=lote: (
            supabase.table("matches").select("id, league_id").in_("id", lt).order("id").range(ini, fim)
        ))
        for m in ms:
            if m["league_id"] is not None:
                liga_por_match[m["id"]] = m["league_id"]

    por_liga: dict[int, dict[str, list[float]]] = defaultdict(lambda: {"chute": [], "gol": []})
    for s in shots:
        liga_id = liga_por_match.get(s["match_id"])
        if liga_id is None:
            continue
        minuto = float(s["minute"]) + float(s.get("minute_added") or 0)
        por_liga[liga_id]["chute"].append(minuto)
        if s["event_type"] == "Goal":
            por_liga[liga_id]["gol"].append(minuto)
    return por_liga


def calcular_mult_minuto(por_liga_minutos: dict[int, dict[str, list[float]]]) -> dict[int, dict[str, dict]]:
    resultado: dict[int, dict[str, dict]] = defaultdict(dict)
    for liga_id, por_evento in por_liga_minutos.items():
        for evento, minutos in por_evento.items():
            if len(minutos) < N_MINIMO_LIGA_MINUTO:
                continue
            contagem_bin: dict[str, int] = defaultdict(int)
            for m in minutos:
                contagem_bin[_bin_de(m)] += 1
            n_bins = len(MINUTE_BINS)
            media_por_bin = len(minutos) / n_bins
            mult = {b: (contagem_bin.get(b, 0) / media_por_bin) for b in [f"{lo}-{min(hi,89) if hi<200 else 89}" for lo, hi in MINUTE_BINS]}
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

    logger.info("Carregando match_team_game_state (placar_confere=true)...")
    por_liga_estado = carregar_game_state(supabase)

    logger.info("Calculando mult_estado_placar (gol)...")
    mult_estado_gol = calcular_mult_estado(por_liga_estado, "gols_pro")
    logger.info("Calculando mult_estado_placar (chute)...")
    mult_estado_chute = calcular_mult_estado(por_liga_estado, "chutes_pro")

    logger.info("Carregando histograma de minuto (match_shots_fotmob)...")
    por_liga_minutos = carregar_minutos_shotmap(supabase)
    mult_minuto = calcular_mult_minuto(por_liga_minutos)

    linhas: list[dict] = []
    for liga_id, info in mult_estado_gol.items():
        for chave, valor in info["mult"].items():
            linhas.append({
                "league_id": liga_id, "evento": "gol", "tipo": "mult_estado_placar", "chave": chave,
                "valor": round(valor, 4), "amostra_n": info["amostra_n"],
                "origem": "match_team_game_state (placar_confere=true, agregado por liga, não controlado por Elo -- conferir contra v_game_state_por_forca antes de usar em produção)",
            })
    for liga_id, info in mult_estado_chute.items():
        for chave, valor in info["mult"].items():
            linhas.append({
                "league_id": liga_id, "evento": "chute", "tipo": "mult_estado_placar", "chave": chave,
                "valor": round(valor, 4), "amostra_n": info["amostra_n"],
                "origem": "match_team_game_state (placar_confere=true, agregado por liga, não controlado por Elo -- conferir contra v_game_state_por_forca antes de usar em produção)",
            })
            # chute_no_alvo: proxy explícito, reaproveita o multiplicador de chute (ver docstring do módulo)
            linhas.append({
                "league_id": liga_id, "evento": "chute_no_alvo", "tipo": "mult_estado_placar", "chave": chave,
                "valor": round(valor, 4), "amostra_n": info["amostra_n"],
                "origem": "PROXY: reaproveita mult_estado_placar de chute -- match_team_game_state não separa chutes no alvo (sem coluna dedicada). Refinar exige nova migration.",
            })

    for liga_id, por_evento in mult_minuto.items():
        for evento_raw, info in por_evento.items():
            eventos_destino = {"gol": ["gol"], "chute": ["chute", "chute_no_alvo"]}[evento_raw]
            for evento in eventos_destino:
                origem = "match_shots_fotmob.minute (histograma direto por liga)"
                if evento == "chute_no_alvo":
                    origem = "PROXY: reaproveita mult_minuto_bin de chute -- match_shots_fotmob.is_on_target não foi separado neste histograma."
                for chave, valor in info["mult"].items():
                    linhas.append({
                        "league_id": liga_id, "evento": evento, "tipo": "mult_minuto_bin", "chave": chave,
                        "valor": round(valor, 4), "amostra_n": info["amostra_n"], "origem": origem,
                    })

    persistir(supabase, linhas)


if __name__ == "__main__":
    main()
