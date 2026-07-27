"""
Diagnóstico e Limpeza de Partidas Duplicadas do Brasileirão
===========================================================

Contexto da arquitetura:
  - A tabela `matches` pode ter partidas do Brasileirão vindas de DUAS fontes:
      1. football-data.org  → external_id = "fd_{id}"   (liga com external_id = "BSA")
      2. API-Football       → external_id = "{id}"       (liga com external_id = "71")

  - O FotMob NÃO cria partidas — ele enriquece partidas já existentes em tabelas satélite:
      match_stats_fotmob, match_player_stats_fotmob, match_shots_fotmob,
      match_context_fotmob, match_lineup_fotmob, match_source_ids (source='fotmob')

  - Portanto, as partidas que o FotMob enriqueceu são as CORRETAS a manter.
    A outra fonte (sem dados FotMob vinculados) é redundante e pode ser removida.

Estratégia:
  1. Identificar as ligas duplicadas do Brasileirão
  2. Descobrir qual liga_id tem dados do FotMob em match_source_ids → manter essa
  3. Para cada partida duplicada (mesmo time/dia), manter a da liga com FotMob
     (ou, se nenhuma tiver FotMob, manter a com mais dados preenchidos)
  4. Deletar as partidas redundantes (e a liga vazia, se ficar sem partidas)

Uso:
  pip install supabase
  python limpar_duplicatas_brasileirao.py [--dry-run]

  --dry-run : apenas analisa e exibe o que FARIA — sem deletar nada (recomendado rodar primeiro)

ATENÇÃO: O Supabase tem Point-in-Time Recovery. Faça um snapshot manual antes se preferir.
"""

import os
import sys
import time as _time
from postgrest.exceptions import APIError as _APIError
from supabase import create_client

# ---------------------------------------------------------------
# Configuração — usa service_role key para ter permissão de escrita
# ---------------------------------------------------------------
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://cgurxgfdmpmsnrshqycx.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNndXJ4Z2ZkbXBtc25yc2hxeWN4Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4MzM0NTU3NiwiZXhwIjoyMDk4OTIxNTc2fQ.FFp-jjSWJYS-2u_0sOdJzPIcJdDfE_wSfw_Kr11H8Us")

DRY_RUN = "--dry-run" in sys.argv

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def buscar_partidas_liga(liga_id: int) -> list[dict]:
    """Busca todas as partidas de uma liga com paginação (sem limite de 1000)."""
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
    if m.get("round")      is not None: s += 3
    if m.get("stage")      is not None: s += 2
    if m.get("status") == "finished":   s += 5
    return s


def chave_jogo(m: dict) -> tuple:
    """Chave de deduplicação: mandante + visitante + dia (sem hora)."""
    return (m["home_team_id"], m["away_team_id"], (m.get("match_date") or "")[:10])


# Tabelas satélite com FK match_id → DELETE antes de deletar a partida-mãe.
# Lista completa levantada varrendo todos os scripts Python do projeto:
_TABELAS_SATELITE = [
    # FotMob
    "match_context_fotmob",
    "match_stats_fotmob",
    "match_lineup_fotmob",
    "match_player_stats_fotmob",
    "match_shots_fotmob",
    "match_source_ids",
    "match_events",          # ingestao_fotmob_cartoes.py
    # FBref / Understat
    "match_stats_fbref",     # ingestao_stats_fbref.py / backfill_xg.py
    # Football-data corners
    "match_stats_escanteios", # ingestao_escanteios_footballdata.py
    # Features e contexto
    "match_features_contexto", # features_contexto.py
    # Modelos (estimativas e predições)
    "model_stat_estimates",  # modelo_stats_esperadas.py
    "model_predictions",     # modelo_stats_esperadas.py / modelo_dixon_coles.py
    "predicoes",             # rodar_predicoes.py
    # Odds
    "odds_market",           # rodar_predicoes.py
    "market_odds",           # rodar_predicoes.py
    # Histórico de rating de jogadores
    "player_rating_history", # scripts/dados_historicos.py
]

# Lotes para tabelas satélite — queries simples, podem ser maiores
_LOTE_SATELITE = 200
# Lotes para o DELETE final em matches — mais pesado (verifica constraints), menor
_LOTE_MATCHES = 30


# Cache das tabelas que não existem no banco (evita tentar de novo em cada lote)
_tabelas_inexistentes: set[str] = set()


def _deletar_satelites_lote(ids_lote: list[int]) -> None:
    """Limpa todas as FKs e deleta as linhas satélite de um lote de match IDs.
    Tabelas que ainda não existem no banco são ignoradas silenciosamente."""
    global _tabelas_inexistentes
    for tabela in _TABELAS_SATELITE:
        if tabela in _tabelas_inexistentes:
            continue
        try:
            supabase.table(tabela).delete().in_("match_id", ids_lote).execute()
        except _APIError as e:
            if e.code == "PGRST205":  # tabela não encontrada no schema cache
                _tabelas_inexistentes.add(tabela)
                print(f"\n    ⚠  '{tabela}' não existe no banco — pulando.")
            else:
                raise  # outros erros propagam normalmente
    # SET NULL em players (jogador continua existindo, só perde a referência à partida)
    supabase.table("players").update({"last_seen_match_id": None}).in_("last_seen_match_id", ids_lote).execute()


def deletar_em_lotes(ids: list[int], tabela: str = "matches", campo: str = "id") -> None:
    """
    Para matches: limpa FKs em lotes de _LOTE_SATELITE, depois deleta
    as partidas em lotes menores (_LOTE_MATCHES) para evitar timeout.
    """
    total = len(ids)
    if tabela == "matches":
        # Passo 1: limpar todas as referências FK em lotes grandes (mais rápido)
        print(f"    Limpando referências FK em {total} partidas...")
        for i in range(0, total, _LOTE_SATELITE):
            _deletar_satelites_lote(ids[i:i+_LOTE_SATELITE])
        print(f"    FKs limpas. Deletando partidas em lotes de {_LOTE_MATCHES}...")
        # Passo 2: deletar as próprias partidas em lotes pequenos
        for i in range(0, total, _LOTE_MATCHES):
            lote = ids[i:i+_LOTE_MATCHES]
            supabase.table(tabela).delete().in_(campo, lote).execute()
            prog = min(i + _LOTE_MATCHES, total)
            print(f"    {prog}/{total} deletados...", end="\r", flush=True)
    else:
        for i in range(0, total, _LOTE_SATELITE):
            lote = ids[i:i+_LOTE_SATELITE]
            supabase.table(tabela).delete().in_(campo, lote).execute()
            prog = min(i + _LOTE_SATELITE, total)
            print(f"    {prog}/{total} deletados...", end="\r", flush=True)
    print(f"    {total}/{total} deletados. ✓          ")


def main():
    print(f"\n{'='*65}")
    print("  LIMPEZA DE PARTIDAS DUPLICADAS DO BRASILEIRÃO")
    print(f"  Modo: {'DRY-RUN (somente leitura)' if DRY_RUN else '*** EXECUÇÃO REAL — VAI DELETAR ***'}")
    print(f"{'='*65}\n")

    if not DRY_RUN:
        resp = input("Digite 'SIM' para confirmar as deleções: ").strip()
        if resp != "SIM":
            print("Abortado.")
            sys.exit(0)

    # ──────────────────────────────────────────────
    # 1. Encontrar todas as ligas do Brasileirão
    # ──────────────────────────────────────────────
    print("── 1. Ligas do Brasileirão no banco ─────────────────────────")
    todas_ligas = supabase.table("leagues").select("id, external_id, name").execute().data or []

    ligas_bra = [
        l for l in todas_ligas
        if l.get("external_id") in ("BSA", "71")
        or "brasileir" in (l.get("name") or "").lower()
    ]

    for l in ligas_bra:
        n = (supabase.table("matches").select("id", count="exact").eq("league_id", l["id"]).execute()).count
        print(f"  [{l['id']:>4}] external_id={l['external_id']:<6}  name={l['name']:<30}  {n} partidas")

    if len(ligas_bra) < 2:
        print("\n  Apenas uma liga — sem duplicatas inter-liga.\n")
        if ligas_bra:
            _checar_intra(ligas_bra[0])
        return

    # ──────────────────────────────────────────────
    # 2. Descobrir qual liga tem dados do FotMob
    # ──────────────────────────────────────────────
    print("\n── 2. Detectando qual liga tem dados FotMob ─────────────────")
    liga_com_fotmob = None
    for l in ligas_bra:
        partidas = buscar_partidas_liga(l["id"])
        ids = [m["id"] for m in partidas]
        ids_fotmob = buscar_match_ids_com_fotmob(ids)
        print(f"  Liga [{l['id']}] '{l['external_id']}': {len(partidas)} partidas, {len(ids_fotmob)} com dados FotMob")
        if ids_fotmob:
            liga_com_fotmob = l

    # ──────────────────────────────────────────────
    # 3. Comparar pares e decidir o que remover
    # ──────────────────────────────────────────────
    print("\n── 3. Comparando partidas entre as ligas ────────────────────")

    from itertools import combinations
    total_deletar = []
    ligas_esvaziar = set()

    for liga_a, liga_b in combinations(ligas_bra, 2):
        partidas_a = buscar_partidas_liga(liga_a["id"])
        partidas_b = buscar_partidas_liga(liga_b["id"])

        idx_a = {}
        for m in partidas_a:
            idx_a.setdefault(chave_jogo(m), []).append(m)

        ids_deletar_desta_iter = []
        liga_perdedora_desta_iter = None

        # Contar quantas duplicatas existem e qual fonte tem FotMob
        duplicatas = []
        for m_b in partidas_b:
            k = chave_jogo(m_b)
            if k in idx_a:
                for m_a in idx_a[k]:
                    duplicatas.append((m_a, m_b))

        print(f"\n  Ligas [{liga_a['id']}] vs [{liga_b['id']}]: {len(duplicatas)} partidas duplicadas")

        if not duplicatas:
            continue

        # Determinar qual manter: prioridade 1 = tem FotMob, prioridade 2 = score de dados
        ids_a = [m_a["id"] for m_a, _ in duplicatas]
        ids_b = [m_b["id"] for _, m_b in duplicatas]
        fotmob_a = buscar_match_ids_com_fotmob(ids_a)
        fotmob_b = buscar_match_ids_com_fotmob(ids_b)

        score_a = sum(score_partida(m_a) for m_a, _ in duplicatas)
        score_b = sum(score_partida(m_b) for _, m_b in duplicatas)

        tem_fotmob_a = len(fotmob_a) > 0
        tem_fotmob_b = len(fotmob_b) > 0

        print(f"    Liga A [{liga_a['id']}] '{liga_a['external_id']}': score_dados={score_a}, fotmob={len(fotmob_a)} partidas")
        print(f"    Liga B [{liga_b['id']}] '{liga_b['external_id']}': score_dados={score_b}, fotmob={len(fotmob_b)} partidas")

        # Lógica de decisão
        if tem_fotmob_a and not tem_fotmob_b:
            manter, remover = liga_a, liga_b
            ids_remover = [m_b["id"] for _, m_b in duplicatas]
            print(f"    ✓ Mantendo A (tem FotMob). Removendo duplicatas de B.")
        elif tem_fotmob_b and not tem_fotmob_a:
            manter, remover = liga_b, liga_a
            ids_remover = [m_a["id"] for m_a, _ in duplicatas]
            print(f"    ✓ Mantendo B (tem FotMob). Removendo duplicatas de A.")
        elif score_a >= score_b:
            manter, remover = liga_a, liga_b
            ids_remover = [m_b["id"] for _, m_b in duplicatas]
            print(f"    ✓ Mantendo A (score maior). Removendo duplicatas de B.")
        else:
            manter, remover = liga_b, liga_a
            ids_remover = [m_a["id"] for m_a, _ in duplicatas]
            print(f"    ✓ Mantendo B (score maior). Removendo duplicatas de A.")

        print(f"    Partidas a remover desta liga: {len(ids_remover)}")
        total_deletar.extend(ids_remover)

        # Verificar se a liga perdedora ficará completamente vazia
        partidas_remover_source = partidas_b if manter == liga_a else partidas_a
        idx_manter = {chave_jogo(m) for m in (partidas_a if manter == liga_a else partidas_b)}
        unicas_na_perdedora = [m for m in partidas_remover_source if chave_jogo(m) not in idx_manter]

        if len(unicas_na_perdedora) == 0:
            print(f"    → Liga [{remover['id']}] ficará vazia → será deletada também.")
            ligas_esvaziar.add(remover["id"])
        else:
            print(f"    → Liga [{remover['id']}] ainda tem {len(unicas_na_perdedora)} partidas únicas → NÃO deletar a liga.")

    # ──────────────────────────────────────────────
    # 4. Aplicar deleções
    # ──────────────────────────────────────────────
    print(f"\n── 4. Execução {'(simulada)' if DRY_RUN else ''} ─────────────────────────────────")
    print(f"  Total de partidas a remover: {len(total_deletar)}")
    print(f"  Ligas a remover: {ligas_esvaziar or 'nenhuma'}")

    if not DRY_RUN and total_deletar:
        print("  Deletando partidas...")
        deletar_em_lotes(total_deletar)
        for liga_id in ligas_esvaziar:
            supabase.table("leagues").delete().eq("id", liga_id).execute()
            print(f"  Liga [{liga_id}] deletada.")

    # ──────────────────────────────────────────────
    # 5. Checar duplicatas dentro de cada liga
    # ──────────────────────────────────────────────
    print("\n── 5. Duplicatas internas por liga ──────────────────────────")
    ligas_restantes = [l for l in ligas_bra if l["id"] not in ligas_esvaziar]
    for l in ligas_restantes:
        _checar_intra(l)

    # ──────────────────────────────────────────────
    # Sumário
    # ──────────────────────────────────────────────
    print(f"\n{'='*65}")
    print(f"  Partidas {'que seriam ' if DRY_RUN else ''}removidas : {len(total_deletar)}")
    print(f"  Ligas {'que seriam ' if DRY_RUN else ''}removidas   : {len(ligas_esvaziar)}")
    if DRY_RUN:
        print("\n  Rode SEM --dry-run para aplicar as alterações.")
    print(f"{'='*65}\n")


def _checar_intra(liga: dict) -> None:
    """Detecta (e remove, se não for dry-run) partidas duplicadas dentro da MESMA liga."""
    partidas = buscar_partidas_liga(liga["id"])
    visto: dict[tuple, dict] = {}
    duplicatas_intra: list[tuple] = []
    for m in partidas:
        k = chave_jogo(m)
        if k in visto:
            duplicatas_intra.append((visto[k], m))
        else:
            visto[k] = m

    if duplicatas_intra:
        print(f"  Liga [{liga['id']}] '{liga['external_id']}': {len(duplicatas_intra)} duplicatas internas!")
        ids_del = []
        for orig, dup in duplicatas_intra:
            ids_del.append(dup["id"] if score_partida(orig) >= score_partida(dup) else orig["id"])
        if not DRY_RUN:
            deletar_em_lotes(ids_del)
            print(f"    → {len(ids_del)} removidas.")
        else:
            print(f"    → (dry-run) {len(ids_del)} seriam removidas.")
    else:
        print(f"  Liga [{liga['id']}] '{liga['external_id']}': sem duplicatas internas ✓")


if __name__ == "__main__":
    main()
