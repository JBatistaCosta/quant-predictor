"""
Script para Unificação de Times Duplicados na Base Supabase
============================================================

Este script unifica IDs duplicados de clubes que representam o mesmo time no mundo real.
Ele atualiza todas as referências em tabelas satélites e na tabela `matches`, depois
remove os IDs secundários da tabela `teams`.

Mapeamento de Unificação:
  - Chapecoense:            ID 720 ('Chapecoense-sc') -> ID 501 ('Chapecoense AF')
  - Athletico Paranaense:   ID 752 ('Atletico Paranaense') -> ID 7 ('CA Paranaense')
  - Bayern de Munique:      ID 842 ('Bayern Munich') -> ID 242 ('FC Bayern München')
  - Schalke 04:             ID 509 ('FC Schalke 04') -> ID 490 ('Schalke 04')
  - Watford:                ID 477 ('Watford') -> ID 602 ('Watford FC')
  - Troyes:                 ID 511 ('ES Troyes AC') -> ID 498 ('Troyes')
  - Paderborn:              ID 507 ('SC Paderborn 07') -> ID 489 ('Paderborn')

Segunda leva (2026-08-23) -- achada investigando os "7 times com crosswalk
perdido pós-merge" da rodada acima: 4 desses 7 não eram só crosswalk faltando,
eram time duplicado de novo (outro team_id criado quando o crosswalk foi
refeito), com histórico de partida real dividido entre os dois. Direção
escolhida = manter o team_id com MAIS partidas (maximiza dado preservado); em
2 dos 4 casos isso também devolveu o crosswalk FotMob que tinha se perdido:
  - Schalke 04:             ID 852 ('FC Schalke 04') -> ID 490 ('Schalke 04')
  - Troyes:                 ID 853 ('ES Troyes AC') -> ID 498 ('Troyes')
  - West Bromwich Albion:   ID 479 ('West Brom') -> ID 594 ('West Bromwich Albion FC')
  - Norwich City:           ID 478 ('Norwich') -> ID 599 ('Norwich City FC')
Executado direto via SQL em produção (não por este script -- as tabelas
tabelas_fk abaixo já estavam desatualizadas frente ao schema atual, ver
xi_previsto/xi_titular_walkforward/team_elo_external que faltavam aqui;
usado dedupe-then-update em vez de update-then-except pra evitar travar em
transação longa contra tabelas grandes). Restam sem crosswalk resolvido:
Paderborn (489, sem duplicata achada) -- nenhum dos 3 tem jogo agendado.

Uso:
  python scripts/unificar_times_duplicados.py [--dry-run]
"""

import os
import sys
import time
from supabase import create_client

# Configurações do Supabase
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://cgurxgfdmpmsnrshqycx.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# Tenta carregar service_role key do .env se não estiver no ambiente
env_path = os.path.join(os.path.dirname(__file__), "..", "functions", ".env")
if os.path.exists(env_path):
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("SUPABASE_SERVICE_ROLE_KEY=") or line.startswith("SUPABASE_KEY="):
                val = line.split("=", 1)[1].strip().strip("'\"")
                if val and not SUPABASE_KEY:
                    SUPABASE_KEY = val

if not SUPABASE_KEY:
    sys.exit("ERRO: SUPABASE_KEY não encontrada.")

DRY_RUN = "--dry-run" in sys.argv
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Mapeamento: (id_secundario_para_deletar, id_principal_para_manter, nome_clube)
PARES_UNIFICACAO = [
    (751, 13,  "Clube Atlético Mineiro"),
    (720, 501, "Chapecoense"),
    (752, 7,   "Athletico Paranaense"),
    (842, 242, "Bayern de Munique"),
    (509, 490, "Schalke 04"),
    (477, 602, "Watford"),
    (511, 498, "Troyes"),
    (507, 489, "Paderborn"),
    # Segunda leva (2026-08-23) -- ver docstring do módulo
    (852, 490, "Schalke 04 (2a duplicata)"),
    (853, 498, "Troyes (2a duplicata)"),
    (479, 594, "West Bromwich Albion"),
    (478, 599, "Norwich City"),
]


def unificar_time(id_secundario: int, id_principal: int, nome_clube: str):
    print(f"\n--- Unificando {nome_clube}: ID {id_secundario} -> ID {id_principal} ---")

    # 1. Migrar partidas em `matches`
    r_home = supabase.table("matches").update({"home_team_id": id_principal}).eq("home_team_id", id_secundario).execute()
    r_away = supabase.table("matches").update({"away_team_id": id_principal}).eq("away_team_id", id_secundario).execute()
    cnt_home = len(r_home.data) if r_home.data else 0
    cnt_away = len(r_away.data) if r_away.data else 0
    print(f"  Partidas migradas em matches: {cnt_home} mandantes, {cnt_away} visitantes")

    # 2. Migrar `team_source_ids`
    sources_sec = supabase.table("team_source_ids").select("*").eq("team_id", id_secundario).execute().data or []
    for s in sources_sec:
        source_name = s["source"]
        ja_existe = supabase.table("team_source_ids").select("team_id").eq("team_id", id_principal).eq("source", source_name).execute().data
        if ja_existe:
            supabase.table("team_source_ids").delete().eq("team_id", id_secundario).eq("source", source_name).execute()
        else:
            supabase.table("team_source_ids").update({"team_id": id_principal}).eq("team_id", id_secundario).eq("source", source_name).execute()
    print(f"  Crosswalks migrados/resolvidos em team_source_ids: {len(sources_sec)}")

    # 3. Migrar tabelas com restrição de FK `team_id`
    tabelas_fk = [
        "match_features_contexto",
        "match_stats",
        "team_elo",
        "team_elo_history",
        "match_events",
        "team_strengths",
        "match_stats_fotmob",
        "match_player_stats_fotmob",
        "match_lineup_fotmob",
        "match_shots_fotmob",
        "player_availability_fotmob",
        "team_transfers_fotmob",
        "team_coach_history_fotmob",
        "match_stats_fbref",
        "match_stats_escanteios",
        "player_career_history_fotmob",
        "player_ratings",
        # Adicionadas 2026-08-23: tinham FK real pra teams.id (conferido via
        # information_schema) mas faltavam nesta lista -- teriam ficado com
        # linha órfã apontando pro id_secundario deletado no passo 6.
        "xi_previsto",
        "xi_titular_walkforward",
        "team_elo_external",
    ]

    for tab in tabelas_fk:
        try:
            r_tab = supabase.table(tab).update({"team_id": id_principal}).eq("team_id", id_secundario).execute()
            if r_tab.data:
                print(f"  Registros migrados em {tab}: {len(r_tab.data)}")
        except Exception:
            try:
                # Se update falhou (ex: chave única), deleta os registros do ID secundário
                supabase.table(tab).delete().eq("team_id", id_secundario).execute()
            except Exception:
                pass

    # 4. Migrar `equipes` (pipeline_team_id)
    try:
        r_eq = supabase.table("equipes").update({"pipeline_team_id": id_principal}).eq("pipeline_team_id", id_secundario).execute()
        if r_eq.data:
            print(f"  Equipes migradas em equipes: {len(r_eq.data)}")
    except Exception:
        pass

    # 5. Migrar `players`
    try:
        r_p = supabase.table("players").update({"last_team_id": id_principal}).eq("last_team_id", id_secundario).execute()
        if r_p.data:
            print(f"  Jogadores migrados em players: {len(r_p.data)}")
    except Exception:
        pass

    # 6. Deletar time secundário
    try:
        supabase.table("teams").delete().eq("id", id_secundario).execute()
        print(f"  -> ID secundário {id_secundario} removido com sucesso de teams!")
    except Exception as e:
        print(f"  [AVISO] Erro ao deletar time {id_secundario} de teams: {e}")


def main():
    print("=== SCRIPT DE UNIFICAÇÃO DE TIMES DUPLICADOS ===")
    print(f"Dry Run: {DRY_RUN} (passe sem --dry-run para aplicar alterações no banco)")

    for id_sec, id_princ, nome in PARES_UNIFICACAO:
        unificar_time(id_sec, id_princ, nome)

    print("\n=== RESUMO ===")
    if DRY_RUN:
        print("Execução em modo simulação (DRY RUN). Nenhuma alteração foi gravada.")
    else:
        print("Todas as unificações foram concluídas com sucesso!")


if __name__ == "__main__":
    main()
