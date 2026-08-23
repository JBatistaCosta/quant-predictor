"""
Backfill de escalação (titular/reserva) por partida (FotMob) -- fecha o gap
confirmado em `match_lineup_fotmob`: a tabela existe desde o PR #114 mas só
captura partidas processadas DAQUI PRA FRENTE (`ingestao_fotmob.py`), então
nasceu com 0 linhas. Base da feature "XI titular" (v3B do Model
Benchmarking, ver scripts/dados_historicos.py).

MESMO payload matchDetails já usado por match_stats_fotmob/match_events
(`ingestao_fotmob.py`/`ingestao_fotmob_cartoes.py`) -- este script RE-BUSCA
matchDetails pras partidas já em `match_source_ids` (source='fotmob') que
ainda não têm nenhuma linha em `match_lineup_fotmob`, mesmo endpoint/pacing
conservador já aceito neste projeto.

Escopo default (`--temporadas 2`, sem `--liga-id`): as N temporadas mais
recentes de CADA uma das 5 ligas de elite europeias (não Brasileirão --
fora do dataset "Feature Stacked" dos modelos de ML, mesma exclusão de
`dados_historicos.LIGAS_ELITE_EUROPEIAS`) -- decisão explícita do usuário
de fazer um backfill parcial (1-2 temporadas), não o backfill completo de
6 anos que a v4 (cartões) fez -- volume bem menor, prioriza ter ALGUM
sinal de treino rápido em vez de esperar o backfill de 6 anos inteiro.

Uso:
    python ingestao_fotmob_lineup_backfill.py [--liga-id N] [--temporadas 2] [--limite N] [--forcar]

Variáveis de ambiente obrigatórias: SUPABASE_URL, SUPABASE_KEY
(service_role -- sem default hardcoded, mesma disciplina de
ingestao_fotmob.py/ingestao_fotmob_cartoes.py).
"""

import argparse
import datetime as dt
import os
import sys
import time

import requests

# .strip() -- secrets do GitHub Actions colados via copy-paste às vezes
# carregam um '\n' final (mesmo bug já documentado/corrigido em
# rodar_predicoes.py/ingestao_fotmob_cartoes.py).
SUPABASE_URL = (os.environ.get("SUPABASE_URL") or "").strip()
SUPABASE_KEY = (os.environ.get("SUPABASE_KEY") or "").strip()

BASE = "https://www.fotmob.com/api/data"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
}
PACING_SEGUNDOS = 1.3

# Mesmo escopo do dataset "Feature Stacked" dos modelos de ML -- Brasileirão
# fica de fora de propósito (ver dados_historicos.LIGAS_ELITE_EUROPEIAS).
LIGAS_ELITE_EUROPEIAS = ["Premier League", "La Liga", "Serie A (Itália)", "Bundesliga", "Ligue 1"]


def extrair_lineup_rows(content: dict, match_id: int, fotmob_to_internal: dict[str, int]) -> tuple[list[dict], list[dict]]:
    """Extrai `content.lineup.{homeTeam,awayTeam}.{starters,subs}` -- função
    pura (sem I/O), mesma lógica já usada no caminho forward de
    ingestao_fotmob.py (reproduzida aqui pra não acoplar os dois scripts).
    Devolve (lineup_rows, player_dim_rows) -- placeholders de jogador sem
    perfil ("0"/"-1") ficam de fora dos dois, mesma ressalva documentada em
    `players`/`match_lineup_fotmob`."""
    lineup_rows: list[dict] = []
    player_dim_rows: list[dict] = []
    lineup = content.get("lineup") or {}
    for side in ("homeTeam", "awayTeam"):
        team = lineup.get(side) or {}
        team_id = fotmob_to_internal.get(str(team.get("id")))
        for grupo, is_starter in (("starters", True), ("subs", False)):
            for p in team.get(grupo) or []:
                pid = str(p.get("id"))
                if pid in ("0", "-1"):
                    continue
                player_dim_rows.append(
                    {
                        "fotmob_player_id": pid,
                        "name": p.get("name"),
                        "first_name": p.get("firstName") or None,
                        "last_name": p.get("lastName") or None,
                        "shirt_number": p.get("shirtNumber"),
                        "country_name": p.get("countryName"),
                        "country_code": p.get("countryCode"),
                        "age": p.get("age"),
                        "market_value": p.get("marketValue"),
                        "usual_position_id": p.get("usualPlayingPositionId"),
                        "photo_url": f"https://images.fotmob.com/image_resources/playerimages/{pid}.png",
                        "last_team_id": team_id,
                        "last_seen_match_id": match_id,
                        "raw_lineup": p,
                        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                    }
                )
                if team_id is not None:
                    lineup_rows.append(
                        {
                            "match_id": match_id,
                            "team_id": team_id,
                            "fotmob_player_id": pid,
                            "is_starter": is_starter,
                            "shirt_number": p.get("shirtNumber"),
                            "position_id": p.get("positionId"),
                            "raw": p,
                            "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                        }
                    )
    return lineup_rows, player_dim_rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--liga-id", type=int, default=None, help="restringe a um league_id interno")
    ap.add_argument("--temporadas", type=int, default=2, help="N temporadas mais recentes de cada liga (default 2)")
    ap.add_argument("--limite", type=int, default=None, help="processa só as N primeiras partidas pendentes")
    ap.add_argument("--forcar", action="store_true", help="reprocessa mesmo partidas que já têm linha em match_lineup_fotmob")
    args = ap.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        sys.exit("Defina SUPABASE_URL e SUPABASE_KEY (service_role) como variáveis de ambiente antes de rodar.")

    from supabase import create_client

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    # Crosswalk de time (fotmob_id -> nosso team_id) -- mesmo pré-requisito
    # de ingestao_fotmob.py/ingestao_fotmob_cartoes.py.
    crosswalk_res = supabase.table("team_source_ids").select("team_id, source_id").eq("source", "fotmob").execute()
    fotmob_to_internal: dict[str, int] = {row["source_id"]: row["team_id"] for row in crosswalk_res.data}
    if not fotmob_to_internal:
        sys.exit("Nenhum crosswalk source='fotmob' encontrado em team_source_ids.")
    print(f"Crosswalk fotmob carregado: {len(fotmob_to_internal)} times.")

    # league_id interno das ligas de elite -- restrito ao escopo do dataset
    # "Feature Stacked" (mesmo motivo de dados_historicos.LIGAS_ELITE_EUROPEIAS).
    if args.liga_id:
        ligas_alvo = [args.liga_id]
    else:
        ligas_res = supabase.table("leagues").select("id, name").in_("name", LIGAS_ELITE_EUROPEIAS).execute().data or []
        ligas_alvo = [row["id"] for row in ligas_res]
        print(f"Ligas de elite resolvidas: {[(r['name'], r['id']) for r in ligas_res]}")

    # N temporadas mais recentes de CADA liga -- season é texto livre (ex.
    # "2025"), então resolve dinamicamente por liga em vez de assumir um
    # valor fixo (temporadas não alinham entre ligas/calendários).
    match_ids: list[int] = []
    for league_id in ligas_alvo:
        temporadas_res = (
            supabase.table("matches")
            .select("season")
            .eq("league_id", league_id)
            .eq("status", "finished")
            .execute()
            .data
            or []
        )
        temporadas_disponiveis = sorted({r["season"] for r in temporadas_res if r["season"]}, reverse=True)
        temporadas_alvo = temporadas_disponiveis[: args.temporadas]
        if not temporadas_alvo:
            continue
        print(f"  liga {league_id}: temporadas {temporadas_alvo}")
        pagina = 0
        while True:
            chunk = (
                supabase.table("matches")
                .select("id")
                .eq("league_id", league_id)
                .eq("status", "finished")
                .in_("season", temporadas_alvo)
                .range(pagina * 1000, pagina * 1000 + 999)
                .execute()
                .data
                or []
            )
            match_ids.extend(row["id"] for row in chunk)
            if len(chunk) < 1000:
                break
            pagina += 1
    print(f"Partidas no escopo (temporadas/ligas): {len(match_ids)}")

    # Só partidas com fotmob_match_id conhecido (mesmo pré-requisito de
    # ingestao_fotmob_cartoes.py).
    fotmob_ids: dict[int, str] = {}
    for i in range(0, len(match_ids), 500):
        lote = match_ids[i : i + 500]
        chunk = (
            supabase.table("match_source_ids")
            .select("match_id, source_id")
            .eq("source", "fotmob")
            .in_("match_id", lote)
            .execute()
            .data
            or []
        )
        fotmob_ids.update({row["match_id"]: row["source_id"] for row in chunk})
    match_ids = [m for m in match_ids if m in fotmob_ids]
    print(f"Com fotmob_match_id conhecido: {len(match_ids)}")

    if not args.forcar:
        ja_processadas: set[int] = set()
        for i in range(0, len(match_ids), 500):
            lote = match_ids[i : i + 500]
            chunk = supabase.table("match_lineup_fotmob").select("match_id").in_("match_id", lote).execute().data or []
            ja_processadas.update(row["match_id"] for row in chunk)
        match_ids = [m for m in match_ids if m not in ja_processadas]
        print(f"Ainda pendentes (sem linha em match_lineup_fotmob): {len(match_ids)}")

    if args.limite:
        match_ids = match_ids[: args.limite]

    n_ok, n_falha, n_sem_lineup = 0, 0, 0
    for i, match_id in enumerate(match_ids):
        fotmob_match_id = fotmob_ids[match_id]
        try:
            r = requests.get(f"{BASE}/matchDetails", params={"matchId": fotmob_match_id}, headers=HEADERS, timeout=20)
            r.raise_for_status()
            d = r.json()
        except Exception as e:
            print(f"  falha em matchId={fotmob_match_id}: {e}")
            n_falha += 1
            time.sleep(PACING_SEGUNDOS)
            continue

        content = d.get("content") or {}
        lineup_rows, player_dim_rows = extrair_lineup_rows(content, match_id, fotmob_to_internal)
        if not lineup_rows:
            n_sem_lineup += 1
        else:
            fotmob_to_player_id: dict[str, int] = {}
            if player_dim_rows:
                resp = supabase.table("players").upsert(player_dim_rows, on_conflict="fotmob_player_id").execute()
                for row in resp.data:
                    fotmob_to_player_id[row["fotmob_player_id"]] = row["id"]
            for row in lineup_rows:
                row["player_id"] = fotmob_to_player_id.get(row["fotmob_player_id"])
            supabase.table("match_lineup_fotmob").upsert(lineup_rows, on_conflict="match_id,team_id,fotmob_player_id").execute()

        n_ok += 1
        if (i + 1) % 50 == 0:
            print(f"  processados {i + 1}/{len(match_ids)}...")
        time.sleep(PACING_SEGUNDOS)

    print(f"\nOK: {n_ok} partidas processadas ({n_sem_lineup} sem lineup no payload), {n_falha} falhas.")


if __name__ == "__main__":
    main()
