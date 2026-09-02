#!/usr/bin/env python3
"""Calibra disp_r (Binomial Negativa) do mercado de chutes totais por jogador.

Contexto: a curva de calibração/lift do mercado "Chutes (total)" (P(>=k)
prevista via Poisson, k=1..7, verificada contra `player_match_walkforward`
fonte_titular='real' -- ver sessão que gerou este script) mostrou que a
DISCRIMINAÇÃO do modelo é perfeita (lambda maior sempre implica taxa real
maior, em todo decil e limiar, sem exceção), mas a MAGNITUDE falha de um
jeito sistemático: nos decis de lambda BAIXO (jogador de banco/pouco
volume), a frequência real de um "jogo grande" surpresa (2+, 3+, 4+, 5+
chutes) é 30-70% maior do que o Poisson prevê. É a assinatura clássica de
superdispersão (variância real > média) -- Poisson força variância=média
por construção, e não captura isso. No decil de lambda mais alto (titular
de alto volume) a calibração já era boa em todos os 7 limiares -- o
problema é concentrado no banco/rotação, não uniforme.

Mesmo tratamento já em produção pra escanteios (`api/corners-model.js`):
Binomial Negativa (NB2) com a MÉDIA continuando a vir do modelo já treinado
(o CatBoost Poisson não muda -- ele já acerta a média, só a forma da
distribuição ao redor dela que está errada) e um parâmetro de forma "r"
calibrado à parte, por liga, e salvo em `league_model_params` -- mesma
tabela, mesmo padrão de chave, mesma fórmula de calibração:

    alpha = Sum((real - lambda)^2 - lambda) / Sum(lambda^2)
    r = 1 / alpha

(resíduo de Pearson condicionado no lambda de CADA observação -- NÃO
mean^2/(variancia-mean) da distribuição agregada da liga inteira, que
misturaria a variação ENTRE jogadores (lambda esperado diferente, já
capturado pelo modelo) com a variação DENTRO de um jogador-partida, que é
o que "r" deveria medir de verdade -- mesmo cuidado já documentado em
`api/corners-model.js`.)

Medido (SQL direto, `player_match_walkforward` fonte_titular='real' x
`match_shots_fotmob`, ~475 mil observações): alpha > 0 (superdispersão
real) nas 12 ligas, sem exceção. r varia de ~3,0 (Eredivisie) a ~10,3
(Premier League) -- variação real o bastante pra justificar fit POR LIGA,
não uma constante global (mesmo padrão de `disp_r` de escanteios).

N < N_MINIMO_LIGA ou alpha <= 0 (dado subdisperso, não superdisperso) ->
NÃO persiste nenhuma linha pra essa liga: o frontend (`AnaliseAvancadaEvento.
jsx::probPeloMenos`) já cai pra Poisson puro quando não encontra disp_r
calibrado pra uma liga -- mesmo espírito de shrinkage do resto do projeto
(não inventar correção que o dado não sustenta), mas sem precisar de um
valor "padrão genérico" como `DISP_R_PADRAO` de escanteios: aqui a
degradação segura já é o comportamento ORIGINAL (Poisson), não uma NB
com "r" chutado.

Só o mercado de CHUTES (total) precisa dessa troca -- "chutes ao gol"
(lambda_chutes_no_alvo_jogo) e "gols" (lambda_gols_jogo_direto/thinning)
já mostraram calibração boa com Poisson puro na mesma verificação
(previsto e observado a poucos pontos percentuais de distância em quase
todos os decis) -- não têm disp_r calibrado aqui nem usam NB.

Este script fica em `arquivos_do_claude/` (fora do deploy/CI, mesmo lugar
que `calibrar_kappa_xg_agregado.py`) porque é recalibração manual/esporádica,
não um job diário -- rodar de novo só se o walk-forward de jogador crescer
o suficiente pra valer a pena, ou se uma verificação de calibração futura
mostrar viés residual que peça reajuste.

Uso:
    set SUPABASE_URL=...
    set SUPABASE_KEY=sua_service_role_key
    python calibrar_disp_r_chutes.py
"""

from __future__ import annotations

import logging
import os
import sys
from collections import defaultdict
from typing import Callable

from supabase import create_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
logger = logging.getLogger("calibrar_disp_r_chutes")

MODEL_NAME = "jogador_chutes_negbin_v1"
MODEL_VERSION_WALKFORWARD = "jogador_mercados_catboost_walkforward_v1"
TAMANHO_PAGINA = 1000
TAMANHO_LOTE_IDS = 500
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


def carregar_lambda_chutes(supabase) -> dict[tuple[int, int, int], dict]:
    """lambda_chutes_jogo por (match_id, team_id, player_id) em
    `player_match_walkforward`, só fonte_titular='real' (escalação oficial
    confirmada -- é a população já usada na verificação de calibração que
    motivou este script) -- paginado por league_id, mesmo cuidado de custo
    quadrático de OFFSET já documentado no resto do projeto."""
    ligas = supabase.table("leagues").select("id").execute().data or []
    league_ids = [linha["id"] for linha in ligas]

    linhas_por_chave: dict[tuple[int, int, int], dict] = {}
    for league_id in league_ids:
        linhas = _paginar(lambda inicio, fim, lg=league_id: (
            supabase.table("player_match_walkforward")
            .select("match_id, team_id, player_id, league_id, lambda_chutes_jogo")
            .eq("league_id", lg)
            .eq("fonte_titular", "real")
            .eq("model_version", MODEL_VERSION_WALKFORWARD)
            .order("id")
            .range(inicio, fim)
        ))
        for linha in linhas:
            chave = (linha["match_id"], linha["team_id"], linha["player_id"])
            linhas_por_chave[chave] = {"league_id": linha["league_id"], "lambda": float(linha["lambda_chutes_jogo"])}
    logger.info("%d linhas (match_id, team_id, player_id) com lambda_chutes_jogo (fonte='real')", len(linhas_por_chave))
    return linhas_por_chave


def carregar_chutes_reais(supabase, match_ids: list[int]) -> dict[tuple[int, int, int], int]:
    """Conta linhas de `match_shots_fotmob` por (match_id, team_id,
    player_id) -- MESMA definição de `chutes_partida` usada no treino
    (`treinar_modelo_jogador_mercados.carregar_dados`), não
    `match_player_stats_fotmob.total_shots` (cobertura mais fraca, ver
    docstring daquele módulo)."""
    contagens: dict[tuple[int, int, int], int] = defaultdict(int)
    for inicio in range(0, len(match_ids), TAMANHO_LOTE_IDS):
        lote = match_ids[inicio: inicio + TAMANHO_LOTE_IDS]
        linhas = _paginar(lambda ini, fim, lt=lote: (
            supabase.table("match_shots_fotmob")
            .select("match_id, team_id, player_id")
            .in_("match_id", lt)
            .not_.is_("player_id", "null")
            .order("id")
            .range(ini, fim)
        ))
        for linha in linhas:
            chave = (linha["match_id"], linha["team_id"], linha["player_id"])
            contagens[chave] += 1
    return contagens


def calcular_disp_r_por_liga(
    previstos: dict[tuple[int, int, int], dict], reais: dict[tuple[int, int, int], int]
) -> dict[int, dict]:
    """alpha = Sum((real-lambda)^2 - lambda) / Sum(lambda^2), r = 1/alpha --
    mesma fórmula (resíduo de Pearson condicionado no lambda de cada
    observação) já usada e documentada em `api/corners-model.js` pra
    escanteios, aqui replicada em Python pra chutes de jogador. Jogador sem
    nenhum chute registrado naquela partida entra com real=0 (ausência em
    match_shots_fotmob != chute não aconteceu, mas ambos os lados desta
    fórmula já vieram da mesma população -- ver docstring do módulo)."""
    por_liga: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for chave, info in previstos.items():
        real = reais.get(chave, 0)
        por_liga[info["league_id"]].append((info["lambda"], float(real)))

    resultado: dict[int, dict] = {}
    for league_id, pares in por_liga.items():
        n = len(pares)
        soma_residuo = sum((real - lam) ** 2 - lam for lam, real in pares)
        soma_lambda2 = sum(lam ** 2 for lam, _ in pares)
        alpha = soma_residuo / soma_lambda2 if soma_lambda2 > 0 else 0.0
        r = (1.0 / alpha) if alpha > 0 else None
        resultado[league_id] = {"n": n, "alpha": alpha, "r": r}
    return resultado


def persistir(supabase, por_liga: dict[int, dict]) -> None:
    linhas = [
        {
            "league_id": league_id, "model_name": MODEL_NAME, "stat": "chutes",
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
    logger.info("%d disp_r gravados em league_model_params.", len(linhas))

    puladas = [lg for lg, info in por_liga.items() if info["n"] < N_MINIMO_LIGA or info["r"] is None]
    if puladas:
        logger.info(
            "%d liga(s) sem disp_r persistido (amostra < %d ou subdisperso) -- fica em Poisson puro: %s",
            len(puladas), N_MINIMO_LIGA, puladas,
        )


def main() -> None:
    supabase = create_client(obter_env("SUPABASE_URL"), obter_env("SUPABASE_KEY"))

    logger.info("Carregando lambda_chutes_jogo (fonte='real') de player_match_walkforward...")
    previstos = carregar_lambda_chutes(supabase)
    if not previstos:
        sys.exit("Nenhuma linha fonte_titular='real' em player_match_walkforward -- rode o backtest walk-forward primeiro.")

    match_ids = sorted({match_id for match_id, _, _ in previstos})
    logger.info("Carregando chutes reais de %d partidas (match_shots_fotmob)...", len(match_ids))
    reais = carregar_chutes_reais(supabase, match_ids)

    por_liga = calcular_disp_r_por_liga(previstos, reais)
    for league_id, info in sorted(por_liga.items()):
        r_fmt = f"{info['r']:.3f}" if info["r"] is not None else "n/a (subdisperso)"
        logger.info("liga=%s n=%d alpha=%.6f r=%s", league_id, info["n"], info["alpha"], r_fmt)

    persistir(supabase, por_liga)


if __name__ == "__main__":
    main()
