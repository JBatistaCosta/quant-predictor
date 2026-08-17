#!/usr/bin/env python3
"""Detecta e limpa partidas duplicadas em `matches` -- generalizado pra
QUALQUER liga (diferente de arquivos_do_claude/limpar_duplicatas_brasileirao.py,
que tratava só a duplicata INTER-liga específica do Brasileirão, quando a
mesma competição tinha 2 `league_id` distintos).

Causa raiz (corrigida em api/sync-matches.js + api/model-maintenance.js,
ver api/_lib/dedupMatches.js): até essa correção, os 3 pontos de ingestão
de calendário (football-data.org, API-Football, FotMob) faziam upsert só
dentro do próprio namespace de external_id (fd_/af_/fm_) -- nada impedia
as 3 fontes criarem uma linha CADA UMA pro MESMO jogo real quando a mesma
liga acaba coberta por mais de uma fonte. Achado real auditando o banco:
Premier League 2026 triplicada (fd_+af_+fm_), La Liga 2026 duplicada
(fd_+af_), Brasileirão Série A/B e Copa Sudamericana também afetados
(342/340/115 jogos duplicados).

Esse script limpa o que JÁ ficou duplicado (a correção na ingestão evita
NOVAS duplicatas, mas não desfaz as existentes). Escopo: só duplicata
INTRA-liga (mesmo league_id, mandante+visitante+dia iguais) -- é o padrão
real encontrado nessa auditoria.

Estratégia por grupo de N linhas duplicadas (mesmo liga+mandante+
visitante+dia):
  1. Escolhe a "vencedora": prioridade 1) tem dado do FotMob em
     match_source_ids; 2) maior "score" de completude (placar, round,
     stage, status=finished); 3) menor id (mais antiga, desempate
     determinístico).
  2. Mescla na vencedora os campos que ela não tem mas alguma perdedora
     tem (placar/round/stage/promoção pra status=finished) -- nunca
     sobrescreve dado já preenchido na vencedora.
  3. MIGRA (não apaga direto) as linhas de tabela satélite das perdedoras
     pra vencedora -- importante porque tabelas como model_predictions/
     paper_trading_apostas/odds_market podem ter dado real preso no
     match_id "errado" (ex: previsão gravada bateu na linha que virou
     perdedora). Se colidir com constraint UNIQUE (a vencedora já tem
     linha equivalente), só aí a linha da perdedora é descartada nessa
     tabela (a da vencedora prevalece).
  4. Deleta as partidas perdedoras.

Uso:
  pip install supabase
  python limpar_partidas_duplicadas.py --dry-run                 # todas as ligas, só relatório
  python limpar_partidas_duplicadas.py --dry-run --liga-id 4      # só uma liga
  python limpar_partidas_duplicadas.py --confirmar                # aplica de verdade (todas as ligas), pede "SIM"
  python limpar_partidas_duplicadas.py --confirmar --liga-id 4     # aplica só numa liga
  python limpar_partidas_duplicadas.py --confirmar --sem-prompt    # aplica sem pedir confirmação (uso em CI)

ATENÇÃO: o Supabase tem Point-in-Time Recovery, mas prefira sempre rodar
--dry-run primeiro e conferir o relatório antes de --confirmar.
"""

import argparse
import os
import sys
import time
from collections import defaultdict

from postgrest.exceptions import APIError
from supabase import create_client

TENTATIVAS_POR_GRUPO = 3

TAM_PAGINA = 1000
LOTE_MATCHES = 30

# Toda tabela com FK match_id -> matches.id, levantada via information_schema
# em 2026-08-16 (mcp Supabase execute_sql) -- reconferir se uma tabela nova
# com FK pra matches for criada e não aparecer aqui.
TABELAS_SATELITE_MATCH_ID = [
    "carteira_simulada", "custom_model_ondemand_predictions", "market_odds",
    "match_context_fotmob", "match_events", "match_features_contexto",
    "match_lineup_fotmob", "match_player_stats_fotmob", "match_shots_fotmob",
    "match_source_ids", "match_stats", "match_stats_fotmob",
    "model_match_estimates", "model_predictions", "model_stat_estimates",
    "odds_market", "paper_trading_apostas", "player_rating_history",
    "predicoes", "team_elo_history", "team_unavailable_fotmob", "xi_previsto",
]


def get_supabase():
    url = os.environ.get("SUPABASE_URL", "https://cgurxgfdmpmsnrshqycx.supabase.co")
    key = os.environ["SUPABASE_KEY"]  # precisa ser a service_role (escrita)
    return create_client(url, key)


def buscar_todas_paginado(supabase, tabela, colunas, eq_filtros=None):
    todas, pagina = [], 0
    while True:
        q = supabase.table(tabela).select(colunas)
        for campo, valor in (eq_filtros or {}).items():
            q = q.eq(campo, valor)
        lote = q.range(pagina * TAM_PAGINA, (pagina + 1) * TAM_PAGINA - 1).execute().data or []
        todas.extend(lote)
        if len(lote) < TAM_PAGINA:
            break
        pagina += 1
    return todas


def buscar_ids_com_fotmob(supabase, match_ids):
    if not match_ids:
        return set()
    resultado = set()
    tam = 500
    for i in range(0, len(match_ids), tam):
        lote = (
            supabase.table("match_source_ids")
            .select("match_id")
            .eq("source", "fotmob")
            .in_("match_id", match_ids[i:i + tam])
            .execute().data or []
        )
        resultado.update(r["match_id"] for r in lote)
    return resultado


def score_partida(m: dict) -> int:
    s = 0
    if m.get("home_goals") is not None: s += 10
    if m.get("away_goals") is not None: s += 10
    if m.get("round") is not None: s += 3
    if m.get("stage") is not None: s += 2
    if m.get("status") == "finished": s += 5
    return s


def escolher_vencedora(cluster: list[dict], ids_com_fotmob: set) -> dict:
    def chave_ordenacao(m):
        tem_fotmob = m["id"] in ids_com_fotmob
        return (not tem_fotmob, -score_partida(m), m["id"])
    return sorted(cluster, key=chave_ordenacao)[0]


def campos_mesclados(vencedora: dict, cluster: list[dict]) -> dict:
    atualizacao = {}
    for campo in ("home_goals", "away_goals", "round", "stage"):
        if vencedora.get(campo) is not None:
            continue
        for m in cluster:
            if m.get(campo) is not None:
                atualizacao[campo] = m[campo]
                break
    if vencedora.get("status") != "finished":
        for m in cluster:
            if m.get("status") == "finished":
                atualizacao["status"] = "finished"
                break
    return atualizacao


def migrar_satelites(supabase, loser_id: int, winner_id: int, contadores: dict) -> None:
    for tabela in TABELAS_SATELITE_MATCH_ID:
        try:
            supabase.table(tabela).update({"match_id": winner_id}).eq("match_id", loser_id).execute()
        except APIError as e:
            codigo = getattr(e, "code", None)
            if codigo == "23505":  # unique_violation -- vencedora já tem linha equivalente
                supabase.table(tabela).delete().eq("match_id", loser_id).execute()
                contadores[tabela] += 1
            elif codigo == "PGRST205":  # tabela não existe nesse ambiente
                continue
            else:
                raise
    # ponteiro simples (players -> "última partida vista"), sem risco de
    # conflito de unicidade -- sempre repontua pra vencedora.
    supabase.table("players").update({"last_seen_match_id": winner_id}).eq("last_seen_match_id", loser_id).execute()


def deletar_partidas(supabase, ids: list[int]) -> None:
    for i in range(0, len(ids), LOTE_MATCHES):
        supabase.table("matches").delete().in_("id", ids[i:i + LOTE_MATCHES]).execute()


def processar_grupo(supabase, cluster: list[dict], ids_com_fotmob: set, contadores_conflito: dict) -> int:
    vencedora = escolher_vencedora(cluster, ids_com_fotmob)
    perdedoras = [m for m in cluster if m["id"] != vencedora["id"]]

    atualizacao = campos_mesclados(vencedora, cluster)
    if atualizacao:
        supabase.table("matches").update(atualizacao).eq("id", vencedora["id"]).execute()

    for perdedora in perdedoras:
        migrar_satelites(supabase, perdedora["id"], vencedora["id"], contadores_conflito)

    deletar_partidas(supabase, [p["id"] for p in perdedoras])
    return len(perdedoras)


def com_retentativa(fn, *args, tentativas=TENTATIVAS_POR_GRUPO, espera_base=3, **kwargs):
    """Reexecuta `fn(*args, **kwargs)` em caso de erro transitório --
    achado real em produção: PGRST002 ("Could not query the database for
    the schema cache") logo no início do script -- persistiu além da
    janela de retentativa original (3 tentativas, ~18s no total), então
    quem chama pra essas consultas de setup usa mais tentativas/espera
    maior -- e httpx.RemoteProtocolError (conexão HTTP/2 reciclada pelo
    servidor) no meio do processamento, que se resolve mais rápido.
    Timeout de statement (57014) é um erro real (achado real: 2 índices
    faltantes) e propaga direto -- não adianta retentar, precisa investigar."""
    ultimo_erro = None
    for tentativa in range(1, tentativas + 1):
        try:
            return fn(*args, **kwargs)
        except APIError as e:
            if getattr(e, "code", None) == "57014":
                raise
            ultimo_erro = e
        except Exception as e:  # httpx.RemoteProtocolError e afins -- instabilidade de rede/conexão
            ultimo_erro = e
        if tentativa < tentativas:
            time.sleep(espera_base * tentativa)
    raise ultimo_erro


def processar_grupo_com_retentativa(supabase, cluster: list[dict], ids_com_fotmob: set, contadores_conflito: dict) -> int:
    """Reprocessar o MESMO grupo do zero em caso de erro transitório é
    seguro: todo passo (migrar_satelites, deletar_partidas) é idempotente
    -- rodar de novo sobre uma linha já migrada/já deletada só resulta em
    0 linhas afetadas, sem duplicar nem perder nada."""
    return com_retentativa(processar_grupo, supabase, cluster, ids_com_fotmob, contadores_conflito)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Só analisa e mostra o que faria, sem alterar nada (padrão se --confirmar não for passado).")
    parser.add_argument("--confirmar", action="store_true", help="Aplica as alterações de verdade.")
    parser.add_argument("--liga-id", type=int, default=None, help="Restringe a uma liga (league_id). Sem isso, roda em TODAS as ligas.")
    parser.add_argument("--sem-prompt", action="store_true", help="Não pede confirmação interativa 'SIM' antes de aplicar (uso em CI).")
    args = parser.parse_args()

    aplicar = args.confirmar and not args.dry_run
    supabase = get_supabase()

    print(f"\n{'='*70}")
    print("  LIMPEZA DE PARTIDAS DUPLICADAS (todas as fontes/ligas)")
    print(f"  Modo: {'*** EXECUÇÃO REAL ***' if aplicar else 'DRY-RUN (somente leitura)'}")
    if args.liga_id:
        print(f"  Restrito à liga_id={args.liga_id}")
    print(f"{'='*70}\n")

    campos = "id, external_id, league_id, season, home_team_id, away_team_id, match_date, home_goals, away_goals, status, round, stage"
    filtros = {"league_id": args.liga_id} if args.liga_id else None
    print("Carregando partidas...")
    todas = com_retentativa(buscar_todas_paginado, supabase, "matches", campos, filtros, tentativas=8, espera_base=5)
    print(f"{len(todas)} partida(s) carregada(s).\n")

    grupos = defaultdict(list)
    for m in todas:
        dia = (m.get("match_date") or "")[:10]
        chave = (m["league_id"], m["home_team_id"], m["away_team_id"], dia)
        grupos[chave].append(m)

    duplicados = {k: v for k, v in grupos.items() if len(v) > 1}
    if not duplicados:
        print("Nenhuma duplicata encontrada.\n")
        return

    total_excedentes = sum(len(c) - 1 for c in duplicados.values())
    todos_ids_envolvidos = [m["id"] for cluster in duplicados.values() for m in cluster]
    ids_com_fotmob = com_retentativa(buscar_ids_com_fotmob, supabase, todos_ids_envolvidos, tentativas=8, espera_base=5)

    por_liga = defaultdict(lambda: {"grupos": 0, "excedentes": 0})
    for (liga_id, *_resto), cluster in duplicados.items():
        por_liga[liga_id]["grupos"] += 1
        por_liga[liga_id]["excedentes"] += len(cluster) - 1
    resp_ligas = com_retentativa(lambda: supabase.table("leagues").select("id, name").execute(), tentativas=8, espera_base=5)
    ligas_nomes = {l["id"]: l["name"] for l in (resp_ligas.data or [])}

    print(f"{len(duplicados)} grupo(s) de partida duplicada, {total_excedentes} linha(s) excedente(s) no total:\n")
    for liga_id, info in sorted(por_liga.items(), key=lambda x: -x[1]["excedentes"]):
        print(f"  {ligas_nomes.get(liga_id, f'liga {liga_id}'):<35} {info['grupos']:>4} jogo(s) duplicado(s)  {info['excedentes']:>4} linha(s) excedente(s)")

    if not aplicar:
        print("\n(dry-run -- rode com --confirmar pra aplicar de verdade)\n")
        return

    if not args.sem_prompt:
        resp = input(f"\nDigite 'SIM' pra confirmar a remoção de {total_excedentes} partida(s) duplicada(s): ").strip()
        if resp != "SIM":
            print("Abortado.")
            sys.exit(0)

    print("\nAplicando...")
    total_deletadas = 0
    grupos_com_falha = []
    contadores_conflito = defaultdict(int)
    for cluster in duplicados.values():
        try:
            n = processar_grupo_com_retentativa(supabase, cluster, ids_com_fotmob, contadores_conflito)
        except Exception as e:
            grupos_com_falha.append(([m["id"] for m in cluster], str(e)))
            print(f"\n  Grupo {[m['id'] for m in cluster]} falhou após {TENTATIVAS_POR_GRUPO} tentativa(s) ({e}) -- pulando, rode de novo depois pra retomar (idempotente).")
            continue
        total_deletadas += n
        print(f"  {total_deletadas}/{total_excedentes} partida(s) deletada(s)...", end="\r", flush=True)

    print(f"\n\nConcluído: {total_deletadas} partida(s) duplicada(s) removida(s).")
    if contadores_conflito:
        print("\nLinhas satélite descartadas por já existir equivalente na vencedora:")
        for tabela, n in sorted(contadores_conflito.items(), key=lambda x: -x[1]):
            print(f"  {tabela}: {n}")
    if grupos_com_falha:
        print(f"\n{len(grupos_com_falha)} grupo(s) não processado(s) por erro persistente -- rode o script de novo pra retomar:")
        for ids, erro in grupos_com_falha:
            print(f"  {ids}: {erro}")
    print()


if __name__ == "__main__":
    main()
