#!/usr/bin/env python3
"""
Diagnóstico e Limpeza Geral de Partidas Duplicadas no Banco de Dados
==================================================================

Identifica e remove partidas duplicadas (mesmo dia, mesmos times, mesma liga)
em todas as ligas cadastradas no banco de dados.

Uso:
  python scripts/limpar_duplicatas_geral.py [--dry-run]
"""

import os
import sys
import time
from postgrest.exceptions import APIError
from supabase import create_client

# Configurações de acesso ao Supabase (chave de service_role necessária para escrita/delete)
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://cgurxgfdmpmsnrshqycx.supabase.co")
SUPABASE_KEY = os.environ.get(
    "SUPABASE_KEY", 
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNndXJ4Z2ZkbXBtc25yc2hxeWN4Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4MzM0NTU3NiwiZXhwIjoyMDk4OTIxNTc2fQ.FFp-jjSWJYS-2u_0sOdJzPIcJdDfE_wSfw_Kr11H8Us"
)

DRY_RUN = "--dry-run" in sys.argv

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Tabelas satélite com FK match_id → DELETE antes de deletar a partida-mãe.
_TABELAS_SATELITE = [
    "match_context_fotmob",
    "match_stats_fotmob",
    "match_lineup_fotmob",
    "match_player_stats_fotmob",
    "match_shots_fotmob",
    "match_source_ids",
    "match_events",
    "match_stats_fbref",
    "match_stats_escanteios",
    "match_features_contexto",
    "model_stat_estimates",
    "model_predictions",
    "predicoes",
    "odds_market",
    "market_odds",
    "player_rating_history",
    "team_elo_history",
]

_LOTE_SATELITE = 50
_LOTE_MATCHES = 5
_tabelas_inexistentes = set()

def buscar_partidas_liga(liga_id: int) -> list[dict]:
    """Busca todas as partidas de uma liga com paginação."""
    todas, pagina, tam = [], 0, 1000
    while True:
        lote = (
            supabase.table("matches")
            .select("id, external_id, match_date, home_team_id, away_team_id, home_goals, away_goals, status, round, stage, season")
            .eq("league_id", liga_id)
            .range(pagina * tam, (pagina + 1) * tam - 1)
            .execute()
            .data or []
        )
        todas.extend(lote)
        if len(lote) < tam:
            break
        pagina += 1
    return todas

def buscar_match_ids_com_fotmob(match_ids: list[int]) -> set[int]:
    """Retorna o subconjunto de match_ids que têm dados do FotMob em match_source_ids."""
    if not match_ids:
        return set()
    resultado = set()
    tam = 500
    for i in range(0, len(match_ids), tam):
        lote = (
            supabase.table("match_source_ids")
            .select("match_id")
            .eq("source", "fotmob")
            .in_("match_id", match_ids[i:i+tam])
            .execute()
            .data or []
        )
        resultado.update(row["match_id"] for row in lote)
    return resultado

def score_partida(m: dict) -> int:
    """Pontuação de qualidade de dados: maior = mais completo."""
    s = 0
    if m.get("home_goals") is not None: s += 10
    if m.get("away_goals") is not None: s += 10
    if m.get("round") is not None: s += 3
    if m.get("stage") is not None: s += 2
    if m.get("status") == "finished": s += 5
    return s

def chave_jogo(m: dict) -> tuple:
    """Chave de deduplicação: mandante + visitante + dia (sem hora)."""
    return (m["home_team_id"], m["away_team_id"], (m.get("match_date") or "")[:10])

def _deletar_satelites_lote(ids_lote: list[int]) -> None:
    """Limpa todas as FKs e deleta as linhas satélite de um lote de match IDs."""
    global _tabelas_inexistentes
    for tabela in _TABELAS_SATELITE:
        if tabela in _tabelas_inexistentes:
            continue
        try:
            supabase.table(tabela).delete().in_("match_id", ids_lote).execute()
        except APIError as e:
            if e.code == "PGRST205":  # tabela não encontrada
                _tabelas_inexistentes.add(tabela)
                print(f"    [WARN] '{tabela}' nao existe no banco - pulando.")
            else:
                raise
    # SET NULL em players
    supabase.table("players").update({"last_seen_match_id": None}).in_("last_seen_match_id", ids_lote).execute()

def deletar_em_lotes(ids: list[int]) -> None:
    """Deleta as partidas e suas referências em lotes para evitar timeouts."""
    total = len(ids)
    if not ids:
        return
    print(f"    Limpando referências FK em {total} partidas...")
    for i in range(0, total, _LOTE_SATELITE):
        _deletar_satelites_lote(ids[i:i+_LOTE_SATELITE])
    
    print(f"    FKs limpas. Deletando partidas em matches...")
    for i in range(0, total, _LOTE_MATCHES):
        lote = ids[i:i+_LOTE_MATCHES]
        supabase.table("matches").delete().in_("id", lote).execute()
        prog = min(i + _LOTE_MATCHES, total)
        print(f"    {prog}/{total} deletados...", end="\r", flush=True)
    print(f"    {total}/{total} deletados. [OK]")

AUTO_CONFIRM = "--auto-confirm" in sys.argv or "--yes" in sys.argv

def main():
    print(f"\n{'='*65}")
    print("  DEDUPLICACAO GERAL DE PARTIDAS NO BANCO DE DADOS")
    print(f"  Modo: {'DRY-RUN (somente leitura)' if DRY_RUN else '*** EXECUCAO REAL - VAI DELETAR ***'}")
    print(f"{'='*65}\n")

    if not DRY_RUN and not AUTO_CONFIRM:
        try:
            confirmacao = input("Digite 'CONFIRMAR' para prosseguir com a exclusão real: ").strip()
            if confirmacao != "CONFIRMAR":
                print("Abortado.")
                sys.exit(0)
        except (EOFError, KeyboardInterrupt):
            sys.exit("Abortado (modo não-interativo). Use --auto-confirm para executar sem confirmação manual.")

    # 1. Carregar todas as ligas
    print("Carregando ligas...")
    leagues = supabase.table("leagues").select("id, name, external_id").execute().data or []
    print(f"Total de ligas cadastradas: {len(leagues)}")

    total_deletar_geral = []

    # 2. Processar cada liga
    for liga in leagues:
        liga_id = liga["id"]
        liga_name = liga["name"]
        print(f"\nProcessando liga: [{liga_id}] {liga_name} ({liga.get('external_id') or 'sem external_id'})...")
        
        partidas = buscar_partidas_liga(liga_id)
        if not partidas:
            print("  Nenhuma partida cadastrada.")
            continue
            
        print(f"  Total de partidas carregadas: {len(partidas)}")

        # Agrupar por chave do jogo
        jogos_agrupados = {}
        for m in partidas:
            key = chave_jogo(m)
            jogos_agrupados.setdefault(key, []).append(m)

        duplicatas_liga = []
        for key, grupo in jogos_agrupados.items():
            if len(grupo) > 1:
                duplicatas_liga.append(grupo)

        if not duplicatas_liga:
            print("  [OK] Sem duplicatas identificadas.")
            continue

        print(f"  Identificados {len(duplicatas_liga)} grupos com duplicidade.")

        ids_a_remover_liga = []
        for grupo in duplicatas_liga:
            # Pegar todos os IDs do grupo para verificar dados FotMob
            grupo_ids = [m["id"] for m in grupo]
            ids_com_fotmob = buscar_match_ids_com_fotmob(grupo_ids)

            # Decidir qual partida manter
            # 1. Priorizar partidas com dados FotMob
            # 2. Em caso de empate ou ausência, priorizar o maior score_partida
            # 3. Em caso de empate, manter o menor ID (registro mais antigo)
            melhor_partida = None
            melhor_score = -1

            for m in grupo:
                tem_fotmob = m["id"] in ids_com_fotmob
                score = score_partida(m)
                
                # Critério composto para comparação
                # (tem_fotmob, score, -id) -> quanto maior melhor
                criterio_atual = (tem_fotmob, score, -m["id"])
                
                if melhor_partida is None:
                    melhor_partida = m
                    melhor_criterio = criterio_atual
                else:
                    if criterio_atual > melhor_criterio:
                        melhor_partida = m
                        melhor_criterio = criterio_atual

            # Adicionar perdedoras para a lista de remoção
            for m in grupo:
                if m["id"] != melhor_partida["id"]:
                    ids_a_remover_liga.append(m["id"])

        print(f"  Partidas a remover nesta liga: {len(ids_a_remover_liga)}")
        total_deletar_geral.extend(ids_a_remover_liga)

    print(f"\n{'='*65}")
    print(f"RESUMO GERAL DO DIAGNOSTICO:")
    print(f"Total de partidas a remover: {len(total_deletar_geral)}")
    print(f"{'='*65}\n")

    if not DRY_RUN and total_deletar_geral:
        print("Iniciando remoções físicas no banco de dados...")
        deletar_em_lotes(total_deletar_geral)
        print("\nProcessamento concluído com sucesso!")
    elif DRY_RUN:
        print("Modo DRY-RUN: nenhuma alteração foi persistida no banco de dados.")

if __name__ == "__main__":
    main()
