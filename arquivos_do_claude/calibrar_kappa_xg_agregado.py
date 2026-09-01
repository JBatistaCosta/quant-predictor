#!/usr/bin/env python3
"""Calibra kappa_liga do modelo de odds agregado por xG de jogador.

Contexto: `player_match_estimates`/`player_match_walkforward` já persistem
`lambda_xg_jogo` por jogador-partida (xG esperado, ponderado por
`minutos_esperados` -- titular e banco incluídos, sem corte de XI, ver
`scripts/rodar_jogador_mercados_previsto.py`). Somando essa coluna por
`(match_id, team_id)` já dá um agregado de xG esperado de TIME sem nenhuma
lógica de ponderação nova: um reserva com pouca chance de entrar já
contribui pouco sozinho, porque seu `minutos_esperados` já é baixo (e
`minutos_esperados` já é uma feature de entrada do regressor que produz
`lambda_xg_jogo` -- `FEATURES_XG` em `treinar_modelo_jogador_mercados.py`).

Validado via SQL direto contra produção (não reproduzido aqui, ver o plano
da sessão): a soma crua já tem sinal real -- correlação 0,30 com gols
reais, viés agregado quase nulo (-0,023), RMSE 1,17 gols, sobre 30.693
observações time-partida em `player_match_walkforward` (histórico honesto,
fora da amostra de treino). `kappa_liga` = média(gols_reais) /
média(soma_lambda) por liga fica entre 0,97 e 1,07 na maioria das 12
ligas; Eredivisie (~1,20), Primeira Liga (~1,10) e Bundesliga (~1,07)
desviam mais -- por isso o fit é POR LIGA, não uma constante global, e por
isso os valores de exemplo que motivaram este modelo (ex. 0,88 pra Série
B) não bateram com o medido (Série B real ≈ 0,98): reforça fitar, não
assumir.

Sem gamma_mando: a mesma validação mostrou viés mandante (-0,040) e
visitante (-0,005) já pequenos SEM nenhuma correção de mando -- a feature
`mando` já entra no regressor que produz `lambda_xg_jogo`, então um fator
extra por cima arriscaria corrigir o mesmo efeito duas vezes. Decisão
documentada, não esquecimento -- revisitar só se o backtest real (ver
`scripts/backfill_xg_agregado_walkforward.py`) mostrar viés residual por
mando que essa correção não capturou.

Persiste em `league_model_params` -- tabela EAV genérica já existente
(mesma usada por `disp_r` de escanteios), sem migration nova:
    (league_id, model_name='xg_jogador_agregado_v1', stat='gols',
     param_name='kappa', param_value)

N < N_MINIMO_LIGA -> kappa = 1,0 (sem correção): amostra pequena demais
pra confiar no fit, mesmo espírito de shrinkage já usado no resto do
projeto -- não inventar sinal que o dado não sustenta.

Este script fica em `arquivos_do_claude/` (fora do deploy/CI, mesmo lugar
que `modelo_dixon_coles.py`) porque é recalibração manual/esporádica, não
um job diário -- rodar de novo só depois que `player_match_walkforward`
crescer o suficiente pra valer a pena (novo backtest de jogador-mercados)
ou se o backtest do modelo agregado (`backfill_xg_agregado_walkforward.py`
+ `api/backtest-betting.js`) mostrar viés real que peça reajuste.

Uso:
    set SUPABASE_URL=...
    set SUPABASE_KEY=sua_service_role_key
    python calibrar_kappa_xg_agregado.py
"""

from __future__ import annotations

import logging
import os
import sys
from collections import defaultdict
from typing import Callable

from supabase import create_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
logger = logging.getLogger("calibrar_kappa_xg_agregado")

MODEL_NAME = "xg_jogador_agregado_v1"
TAMANHO_PAGINA = 1000
TAMANHO_LOTE_IDS = 500
# Seasons conhecidas (confirmadas via SQL: "2021".."2026") + folga futura --
# combinado com league_id, mantém o OFFSET de cada fatia paginada baixo. A
# maior liga sozinha (MLS) soma ~63 mil linhas em player_match_walkforward;
# paginar isso de uma vez com OFFSET puro arrisca o mesmo custo quadrático
# já documentado em `dados_historicos._paginar_por_lotes_de_id` (timeout
# real já visto em produção em offsets bem menores, na função
# `_bayesiano_atual`). Uma temporada extra de folga é barata (só devolve 0
# linhas se não existir).
TEMPORADAS = [str(ano) for ano in range(2018, 2028)]
N_MINIMO_LIGA = 300


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


def carregar_lambda_agregado(supabase) -> dict[tuple[int, int], dict]:
    """Soma `lambda_xg_jogo` por `(match_id, team_id)` em
    `player_match_walkforward`, paginado por (league_id, temporada) -- ver
    docstring do módulo pra motivo."""
    ligas = supabase.table("leagues").select("id").execute().data or []
    league_ids = [linha["id"] for linha in ligas]

    agregados: dict[tuple[int, int], dict] = {}
    for league_id in league_ids:
        for temporada in TEMPORADAS:
            linhas = _paginar(lambda inicio, fim, lg=league_id, sz=temporada: (
                supabase.table("player_match_walkforward")
                .select("match_id, team_id, league_id, lambda_xg_jogo")
                .eq("league_id", lg).eq("season", sz)
                .not_.is_("lambda_xg_jogo", "null")
                .order("id")
                .range(inicio, fim)
            ))
            for linha in linhas:
                chave = (linha["match_id"], linha["team_id"])
                bucket = agregados.setdefault(chave, {"league_id": linha["league_id"], "soma_lambda": 0.0})
                bucket["soma_lambda"] += float(linha["lambda_xg_jogo"])
    logger.info("%d combinações (match_id, team_id) com lambda_xg_jogo agregado", len(agregados))
    return agregados


def carregar_gols_reais(supabase, match_ids: list[int]) -> dict[int, dict]:
    """`matches.home_goals/away_goals` + `home_team_id/away_team_id`, em
    lotes de id (mesmo padrão de `dados_historicos._paginar_por_lotes_de_id`,
    reimplementado localmente -- este script roda raro o bastante fora do
    pipeline de treino de jogador pra não valer a dependência cruzada de
    importar `dados_historicos.py`)."""
    partidas: dict[int, dict] = {}
    for inicio in range(0, len(match_ids), TAMANHO_LOTE_IDS):
        lote = match_ids[inicio: inicio + TAMANHO_LOTE_IDS]
        linhas = _paginar(lambda ini, fim, lt=lote: (
            supabase.table("matches")
            .select("id, home_team_id, away_team_id, home_goals, away_goals")
            .in_("id", lt)
            .not_.is_("home_goals", "null")
            .order("id")
            .range(ini, fim)
        ))
        for linha in linhas:
            partidas[linha["id"]] = linha
    return partidas


def calcular_kappa_por_liga(agregados: dict[tuple[int, int], dict], partidas: dict[int, dict]) -> dict[int, dict]:
    """kappa_liga = média(gols_reais) / média(soma_lambda), só com times
    identificados como mandante ou visitante da partida e gol real
    conhecido."""
    por_liga: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for (match_id, team_id), bucket in agregados.items():
        partida = partidas.get(match_id)
        if not partida:
            continue
        if team_id == partida["home_team_id"]:
            gols_reais = partida["home_goals"]
        elif team_id == partida["away_team_id"]:
            gols_reais = partida["away_goals"]
        else:
            continue
        if gols_reais is None:
            continue
        por_liga[bucket["league_id"]].append((bucket["soma_lambda"], float(gols_reais)))

    resultado: dict[int, dict] = {}
    for league_id, pares in por_liga.items():
        n = len(pares)
        media_lambda = sum(p[0] for p in pares) / n
        media_gols = sum(p[1] for p in pares) / n
        kappa = media_gols / media_lambda if (n >= N_MINIMO_LIGA and media_lambda > 0) else 1.0
        resultado[league_id] = {"n": n, "media_lambda": media_lambda, "media_gols": media_gols, "kappa": kappa}
    return resultado


def persistir(supabase, por_liga: dict[int, dict]) -> None:
    linhas = [{
        "league_id": league_id, "model_name": MODEL_NAME, "stat": "gols",
        "param_name": "kappa", "param_value": round(info["kappa"], 4),
    } for league_id, info in por_liga.items()]
    if not linhas:
        logger.warning("Nenhum kappa calculado -- nada gravado.")
        return
    supabase.table("league_model_params").upsert(
        linhas, on_conflict="league_id,model_name,stat,param_name"
    ).execute()
    logger.info("%d kappa_liga gravados em league_model_params.", len(linhas))


def main() -> None:
    supabase = create_client(obter_env("SUPABASE_URL"), obter_env("SUPABASE_KEY"))

    logger.info("Carregando lambda_xg_jogo agregado de player_match_walkforward...")
    agregados = carregar_lambda_agregado(supabase)
    if not agregados:
        sys.exit("Nenhuma linha com lambda_xg_jogo em player_match_walkforward -- rode o backtest de jogador primeiro.")

    match_ids = sorted({match_id for match_id, _ in agregados})
    logger.info("Carregando gols reais de %d partidas...", len(match_ids))
    partidas = carregar_gols_reais(supabase, match_ids)

    por_liga = calcular_kappa_por_liga(agregados, partidas)
    for league_id, info in sorted(por_liga.items()):
        logger.info(
            "liga=%s n=%d lambda_medio=%.3f gols_medio=%.3f kappa=%.4f",
            league_id, info["n"], info["media_lambda"], info["media_gols"], info["kappa"],
        )

    persistir(supabase, por_liga)


if __name__ == "__main__":
    main()
