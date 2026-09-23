#!/usr/bin/env python3
"""Calibra disp_r (Binomial Negativa) do TOTAL de faltas por partida
(mandante + visitante), por liga -- mesmo método e mesma tabela de destino
já usados pra escanteios/chutes/chutes no gol em `api/corners-model.js`.

Contexto: faltas nunca teve um GLM Poisson treinado (diferente de
corners/shots/shots_on_target, que vêm de `modelo_stats_esperadas.py` e
`model_stat_estimates`) -- o endpoint (`api/corners-model.js`, `?stat=
fouls`, ver STAT_TABELA_REAL) usa direto a média histórica real de
`match_disciplina.faltas_cometidas` (últimas 10 partidas do time,
mandante/visitante separado) como lambda. Este script calibra o parâmetro
de FORMA "r" da Binomial Negativa em cima dessa MESMA definição de lambda
(não de um lambda de modelo que o endpoint não usa) -- senão o "r" calibrado
não corresponderia ao lambda que o endpoint de fato serve.

MESMA FÓRMULA já documentada em `api/corners-model.js` (resíduo de Pearson
condicionado no lambda de CADA partida, não mean²/(variância-mean) da
distribuição agregada -- isso misturaria a variação ENTRE confrontos com a
variação DENTRO de uma partida, que é o que "r" deveria medir):

    alpha = Sum((real - lambda)^2 - lambda) / Sum(lambda^2)
    r = 1 / alpha

LAMBDA WALK-FORWARD (sem olhar pra frente): pra cada partida, lambda do
mandante = média real de faltas cometidas pelo time em até 10 partidas
ANTERIORES jogando em casa; lambda do visitante = mesma coisa jogando fora.
Times com menos de 3 partidas anteriores daquele mando ficam de fora (sinal
baixo demais pra confiar). lambda_total = lambda_mandante + lambda_visitante,
comparado contra o total real da partida (mesma variável NB que
`api/corners-model.js` usa: total mandante+visitante como UMA distribuição).

Este script fica em `arquivos_do_claude/` (fora do deploy/CI) porque é
recalibração manual/esporádica, mesmo padrão de `calibrar_disp_r_chutes.py`
-- rodar de novo se `match_disciplina` crescer o suficiente pra valer a
pena, ou como parte da Fase 1 do motor de simulação por Cadeia de Markov
multi-evento (ver CONTEXTO_PROJETO.md / plano da sessão).

Uso:
    set SUPABASE_URL=...
    set SUPABASE_KEY=sua_service_role_key
    python calibrar_disp_r_faltas.py
"""

from __future__ import annotations

import logging
import os
import sys
from collections import defaultdict
from typing import Callable

from supabase import create_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
logger = logging.getLogger("calibrar_disp_r_faltas")

MODEL_NAME = "faltas_negbin_v1"
STAT_LABEL = "faltas"
TAMANHO_PAGINA = 1000
N_MINIMO_LIGA = 100
JANELA_TRAILING = 10
MIN_PARTIDAS_TRAILING = 3


def obter_env(nome: str) -> str:
    valor = os.environ.get(nome)
    if not valor:
        sys.exit(f"Configure {nome} antes de rodar.")
    return valor


def _paginar(query_builder_factory: Callable[[int, int], object], tamanho_pagina: int = TAMANHO_PAGINA) -> list[dict]:
    """Mesma lógica de `dados_historicos._paginar` -- contorna o corte
    silencioso de 1000 linhas do PostgREST/Supabase por requisição."""
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


def carregar_partidas_por_liga(supabase) -> dict[int, list[dict]]:
    """Partidas finalizadas com match_disciplina dos dois lados, ordenadas
    por data -- uma lista por liga, pronta pra varredura walk-forward."""
    disciplina = _paginar(lambda ini, fim: (
        supabase.table("match_disciplina")
        .select("match_id, team_id, faltas_cometidas")
        .not_.is_("faltas_cometidas", "null")
        .order("id")
        .range(ini, fim)
    ))
    por_match: dict[int, dict[int, int]] = defaultdict(dict)
    for linha in disciplina:
        por_match[linha["match_id"]][linha["team_id"]] = int(linha["faltas_cometidas"])

    match_ids = list(por_match.keys())
    partidas: list[dict] = []
    for inicio in range(0, len(match_ids), 500):
        lote = match_ids[inicio: inicio + 500]
        linhas = _paginar(lambda ini, fim, lt=lote: (
            supabase.table("matches")
            .select("id, league_id, match_date, home_team_id, away_team_id, status")
            .in_("id", lt)
            .eq("status", "finished")
            .order("id")
            .range(ini, fim)
        ))
        partidas.extend(linhas)

    por_liga: dict[int, list[dict]] = defaultdict(list)
    for p in partidas:
        faltas_time = por_match.get(p["id"], {})
        if p["home_team_id"] not in faltas_time or p["away_team_id"] not in faltas_time:
            continue
        por_liga[p["league_id"]].append({
            "match_id": p["id"],
            "match_date": p["match_date"],
            "home_team_id": p["home_team_id"],
            "away_team_id": p["away_team_id"],
            "faltas_home": faltas_time[p["home_team_id"]],
            "faltas_away": faltas_time[p["away_team_id"]],
        })
    for liga_id in por_liga:
        por_liga[liga_id].sort(key=lambda x: x["match_date"])
    logger.info("%d ligas com match_disciplina completo (mandante+visitante).", len(por_liga))
    return por_liga


def calcular_disp_r_por_liga(por_liga: dict[int, list[dict]]) -> dict[int, dict]:
    """Varredura walk-forward: pra cada partida, lambda de cada lado é a
    média real das até JANELA_TRAILING partidas ANTERIORES daquele time
    naquele mando (só passado, nunca olha pra frente -- mesma disciplina do
    resto do projeto, ex. backtest_jogador_mercados_walkforward.py)."""
    resultado: dict[int, dict] = {}
    for league_id, partidas in por_liga.items():
        historico_home: dict[int, list[int]] = defaultdict(list)  # faltas cometidas jogando em casa, por time
        historico_away: dict[int, list[int]] = defaultdict(list)  # faltas cometidas jogando fora, por time
        pares: list[tuple[float, float]] = []

        for p in partidas:
            h, a = p["home_team_id"], p["away_team_id"]
            trail_home = historico_home[h][-JANELA_TRAILING:]
            trail_away = historico_away[a][-JANELA_TRAILING:]
            if len(trail_home) >= MIN_PARTIDAS_TRAILING and len(trail_away) >= MIN_PARTIDAS_TRAILING:
                lam_home = sum(trail_home) / len(trail_home)
                lam_away = sum(trail_away) / len(trail_away)
                lam_total = lam_home + lam_away
                real_total = float(p["faltas_home"] + p["faltas_away"])
                if lam_total > 0:
                    pares.append((lam_total, real_total))

            historico_home[h].append(p["faltas_home"])
            historico_away[a].append(p["faltas_away"])

        n = len(pares)
        if n == 0:
            continue
        soma_residuo = sum((real - lam) ** 2 - lam for lam, real in pares)
        soma_lambda2 = sum(lam ** 2 for lam, _ in pares)
        alpha = soma_residuo / soma_lambda2 if soma_lambda2 > 0 else 0.0
        r = (1.0 / alpha) if alpha > 0 else None
        resultado[league_id] = {"n": n, "alpha": alpha, "r": r}
    return resultado


def persistir(supabase, por_liga: dict[int, dict]) -> None:
    linhas = [
        {
            "league_id": league_id, "model_name": MODEL_NAME, "stat": STAT_LABEL,
            "param_name": "disp_r", "param_value": round(info["r"], 5),
        }
        for league_id, info in por_liga.items()
        if info["n"] >= N_MINIMO_LIGA and info["r"] is not None
    ]
    if not linhas:
        logger.warning("Nenhum disp_r calculado (amostra pequena demais ou dado subdisperso em todas as ligas) -- nada gravado.")
        return
    supabase.table("league_model_params").upsert(
        linhas, on_conflict="league_id,model_name,stat,param_name"
    ).execute()
    logger.info("%d disp_r de faltas gravados em league_model_params.", len(linhas))

    puladas = [lg for lg, info in por_liga.items() if info["n"] < N_MINIMO_LIGA or info["r"] is None]
    if puladas:
        logger.info(
            "%d liga(s) sem disp_r persistido (amostra < %d ou subdisperso) -- api/corners-model.js cai pro DISP_R_PADRAO_POR_STAT.fouls provisório: %s",
            len(puladas), N_MINIMO_LIGA, puladas,
        )


def main() -> None:
    supabase = create_client(obter_env("SUPABASE_URL"), obter_env("SUPABASE_KEY"))

    logger.info("Carregando faltas por partida (match_disciplina + matches)...")
    por_liga = carregar_partidas_por_liga(supabase)
    if not por_liga:
        sys.exit("Nenhuma liga com match_disciplina completo -- rode ?tarefa=derivar-disciplina primeiro.")

    resultado = calcular_disp_r_por_liga(por_liga)
    for league_id, info in sorted(resultado.items()):
        r_fmt = f"{info['r']:.3f}" if info["r"] is not None else "n/a (subdisperso)"
        logger.info("liga=%s n=%d alpha=%.6f r=%s", league_id, info["n"], info["alpha"], r_fmt)

    persistir(supabase, resultado)


if __name__ == "__main__":
    main()
