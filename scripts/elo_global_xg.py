"""
Elo global atualizado por xG (EM PARALELO ao Elo global por resultado).

Escreve em `team_elo_xg` / `team_elo_xg_history` (tabelas PRÓPRIAS, ver migration
20261001240000) -- nada em `team_elo`/`team_elo_history` (escopos liga/geral/global) muda,
então o painel /ratings, o ajuste por Elo da Análise de Evento e os modelos treinados
seguem exatamente como estavam. A view `v_elo_ab_brier` compara os dois Elos em cima
dos mesmos jogos, com os ratings de ANTES de cada partida.

ESPECIFICAÇÃO (validada em experimento de 01/10/2026, ver CONTEXTO_PROJETO.md: 19.112 jogos
com xG dos dois lados, 08/2020-09/2026, K escolhido só na validação 2021-22, teste 14.910 jogos
de 08/2022 a 09/2026; Brier do escore esperado 0,1580-0,1584 contra 0,1610 do Elo por resultado):
  S (escore de atualização) = W_RES * resultado + (1 - W_RES) * logística(A_XG * (xG_casa - xG_fora))
  delta = K_XG * (S - esperado)             -- sem multiplicador de diferença de gols
  esperado = 1 / (1 + 10^(-((R_casa + HFA) - R_fora) / 400))
  W_RES=0,25, A_XG=1,6, K_XG=40, HFA=60. O platô é plano (W_RES 0,2-0,3; A_XG 1,2-2,2; K 32-56):
  não é "o" ótimo, é o centro da região onde o Brier não se distingue do melhor.

O QUE NÃO FOI VALIDADO (escolhas deste script, ainda não testadas):
  - Partidas SEM xG (ligas não cobertas, época pré-2020) atualizam só por resultado, com
    K_RES=24 (o melhor K do Elo por resultado no experimento), mesmo HFA e sem multiplicador.
  - Campo neutro: HFA=0 (o experimento excluiu jogos neutros).
  - Todos os times começam em 1500 em 2014 (o Elo por resultado do projeto usa seeds do
    ClubElo, HFA 65, K 20 e multiplicador de diferença de gols -- por isso as escalas dos
    dois Elos NÃO são idênticas; compare por Brier (v_elo_ab_brier), não por pontos).

Sempre recalcula do zero (delete-e-regrava numa transação): ~30 mil partidas em segundos, e
evita que um xG que chegue atrasado (match_stats_fotmob é preenchido depois do placar) deixe
uma partida processada só por resultado para sempre.

Uso:
    python scripts/elo_global_xg.py
"""

import math
import os
import sys
from datetime import datetime, timezone

from supabase import create_client

from elo_global import _paginar  # paginação explícita (evita o corte silencioso de 1000 linhas)

SUPABASE_URL = (os.environ.get("SUPABASE_URL") or "").strip()
SUPABASE_KEY = (os.environ.get("SUPABASE_KEY") or "").strip()

RATING_INICIAL = 1500.0
HFA = 60.0
W_RES = 0.25     # peso do resultado no escore; o resto (0,75) vem do xG
A_XG = 1.6       # inclinação da logística xG -> escore
K_XG = 40.0      # K quando a partida tem xG dos dois lados
K_RES = 24.0     # K quando só há resultado (fallback)
TAMANHO_LOTE = 5000


def esperado_casa(rating_casa, rating_fora, neutro):
    hfa = 0.0 if neutro else HFA
    return 1 / (1 + 10 ** (-((rating_casa + hfa) - rating_fora) / 400))


def escore_resultado(gols_casa, gols_fora):
    if gols_casa > gols_fora:
        return 1.0
    if gols_casa < gols_fora:
        return 0.0
    return 0.5


def escore_atualizacao(gols_casa, gols_fora, xg_casa, xg_fora):
    """Devolve (S, K, usou_xg). Com xG dos dois lados mistura resultado e xG; senão só resultado."""
    s_res = escore_resultado(gols_casa, gols_fora)
    if xg_casa is None or xg_fora is None:
        return s_res, K_RES, False
    s_xg = 1 / (1 + math.exp(-A_XG * (xg_casa - xg_fora)))
    return W_RES * s_res + (1 - W_RES) * s_xg, K_XG, True


def processar_partidas(partidas, xg_por_jogo):
    """Aplica o Elo por xG sequencialmente sobre `partidas` (ordenadas por data, id).
    Devolve (rating, contagem, historico)."""
    rating, contagem, historico = {}, {}, []
    for p in partidas:
        casa, fora = p["home_team_id"], p["away_team_id"]
        for t in (casa, fora):
            if t not in rating:
                rating[t] = RATING_INICIAL
                contagem[t] = 0
        antes_casa, antes_fora = rating[casa], rating[fora]
        e = esperado_casa(antes_casa, antes_fora, bool(p.get("is_neutral")))
        s, k, usou_xg = escore_atualizacao(
            p["home_goals"], p["away_goals"],
            xg_por_jogo.get((p["id"], casa)), xg_por_jogo.get((p["id"], fora)),
        )
        delta = k * (s - e)
        rating[casa], rating[fora] = antes_casa + delta, antes_fora - delta
        contagem[casa] += 1
        contagem[fora] += 1
        for time, antes, depois in ((casa, antes_casa, rating[casa]), (fora, antes_fora, rating[fora])):
            historico.append({
                "team_id": time, "match_id": p["id"], "rodada": p.get("round"),
                "rating_antes": antes, "rating_depois": depois,
                "match_date": p["match_date"], "usou_xg": usou_xg,
            })
    return rating, contagem, historico


def carregar_partidas(supabase):
    partidas = _paginar(
        lambda: supabase.table("matches")
            .select("id, round, match_date, home_team_id, away_team_id, home_goals, away_goals, is_neutral")
            .eq("status", "finished")
    )
    partidas = [p for p in partidas if p["home_goals"] is not None and p["away_goals"] is not None]
    partidas.sort(key=lambda p: (p["match_date"], p["id"]))
    return partidas


def carregar_xg(supabase):
    linhas = _paginar(lambda: supabase.table("match_stats_fotmob").select("match_id, team_id, xg"))
    return {(l["match_id"], l["team_id"]): float(l["xg"]) for l in linhas if l["xg"] is not None}


def gravar(supabase, rating, contagem, historico):
    agora = datetime.now(timezone.utc).isoformat()
    linhas_elo = [
        {"team_id": t, "rating": rating[t], "partidas": contagem[t], "atualizado_em": agora}
        for t in rating
    ]
    lotes = [historico[i:i + TAMANHO_LOTE] for i in range(0, len(historico), TAMANHO_LOTE)] or [[]]
    for i, lote in enumerate(lotes):
        supabase.rpc("registrar_team_elo_xg_lote", {
            "p_elo": linhas_elo if i == 0 else [],
            "p_historico": lote,
            "p_apagar": i == 0,
        }).execute()
    return len(linhas_elo)


def main():
    if not SUPABASE_URL or not SUPABASE_KEY:
        sys.exit("Defina SUPABASE_URL e SUPABASE_KEY (service_role) como variáveis de ambiente.")
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    print("Carregando partidas finalizadas...")
    partidas = carregar_partidas(supabase)
    print(f"  {len(partidas)} partidas.")
    print("Carregando xG (match_stats_fotmob)...")
    xg = carregar_xg(supabase)
    print(f"  {len(xg)} linhas de xG.")

    rating, contagem, historico = processar_partidas(partidas, xg)
    com_xg = sum(1 for h in historico if h["usou_xg"]) // 2
    print(f"Regravando team_elo_xg/team_elo_xg_history: {len(rating)} times, {len(historico)} linhas "
          f"({com_xg} partidas atualizadas com xG, {len(partidas) - com_xg} só por resultado)...")
    n = gravar(supabase, rating, contagem, historico)
    print(f"OK: {n} times, {len(partidas)} partidas processadas.")


if __name__ == "__main__":
    main()
