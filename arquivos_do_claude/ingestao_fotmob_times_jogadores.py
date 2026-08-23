import argparse
import gzip
import json
import os
import sys
from pathlib import Path
import datetime as dt

from supabase import create_client

SUPABASE_URL = (os.environ.get("SUPABASE_URL") or "").strip()
SUPABASE_KEY = (os.environ.get("SUPABASE_KEY") or "").strip()

TAMANHO_FATIA_UPSERT = 500

def _upsert_em_fatias(supabase, tabela: str, linhas: list, on_conflict: str):
    if not linhas:
        return
    for i in range(0, len(linhas), TAMANHO_FATIA_UPSERT):
        supabase.table(tabela).upsert(linhas[i : i + TAMANHO_FATIA_UPSERT], on_conflict=on_conflict).execute()


def parse_team_file(d: dict, fotmob_team_id: str):
    # Teams
    team_row = {
        "fotmob_team_id": fotmob_team_id,
        "name": d.get("name"),
        "short_name": d.get("shortName"),
        "country": d.get("country"),
        "coach_name": (d.get("coach") or {}).get("name"),
        "primary_league_fotmob_id": d.get("primaryLeagueId"),
        "latest_season": d.get("latestSeason"),
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat()
    }

    squad_rows = []
    for s in d.get("squad", []):
        for p in s.get("members", []):
            shirt = p.get("shirt")
            squad_rows.append({
                "fotmob_team_id": fotmob_team_id,
                "fotmob_player_id": str(p.get("id")),
                "is_coach": p.get("isCoach", False),
                "market_value": p.get("marketValue"),
                "age": p.get("age"),
                "height_cm": p.get("height"),
                "shirt_number": str(shirt) if shirt is not None else None,
                "country_code": p.get("ccode"),
                "role": (p.get("role") or {}).get("fallback"),
                "is_captain": p.get("isCaptain") or False,
                "captured_at": dt.datetime.now(dt.timezone.utc).isoformat()
            })
            
    # Also parse unavailable if any inside team snapshot (usually this might be in match details, but we handle it here if present)
    unavailable_rows = []
    # If there is a missingPlayers/unavailable section in team JSON (as per inventory)
    for p in d.get("unavailable", []):
        unavailable_rows.append({
            "fotmob_team_id": fotmob_team_id,
            "fotmob_player_id": str(p.get("id")),
            "type": p.get("type"),
            "expected_return": p.get("expectedReturn"),
            "captured_at": dt.datetime.now(dt.timezone.utc).isoformat()
        })
        
    return team_row, squad_rows, unavailable_rows


def parse_player_file(d: dict, fotmob_player_id: str):
    p_row = {
        "fotmob_player_id": fotmob_player_id,
        "name": d.get("name"),
        "birth_date": d.get("birthDate"),
        "position_description": d.get("positionDescription"),
        "height": d.get("height"),
        "preferred_foot": d.get("preferredFoot"),
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat()
    }
    
    mv_rows = []
    for mv in d.get("marketValues", []):
        mv_rows.append({
            "fotmob_player_id": fotmob_player_id,
            "date": mv.get("date"),
            "market_value": mv.get("value")
        })
        
    trait_rows = []
    # e.g., traits could be inside 'traits' or 'statSeasons' radars
    for t in d.get("traits", []):
        trait_rows.append({
            "fotmob_player_id": fotmob_player_id,
            "trait_name": t.get("name") or t.get("title"),
            "value": t.get("value"),
            "captured_at": dt.datetime.now(dt.timezone.utc).isoformat()
        })
        
    return p_row, mv_rows, trait_rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pasta-dados", required=True, help="pasta do repository-fotmob (contém data_teams/ e data_players/)")
    args = ap.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        sys.exit("Defina SUPABASE_URL e SUPABASE_KEY")

    pasta = Path(args.pasta_dados)
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    
    # Process Teams
    teams_dir = pasta / "data_teams"
    if teams_dir.is_dir():
        arquivos = list(teams_dir.glob("*.json.gz"))
        print(f"Lendo {len(arquivos)} arquivos de times...")
        
        teams, squads, unavailables = [], [], []
        for f in arquivos:
            try:
                with gzip.open(f) as fh:
                    d = json.load(fh)
                team_id = f.stem.split(".")[0]
                t, s, u = parse_team_file(d, team_id)
                teams.append(t)
                squads.extend(s)
                unavailables.extend(u)
            except Exception as e:
                print(f"Falha ao ler {f}: {e}")
                
        _upsert_em_fatias(supabase, "teams_fotmob", teams, "fotmob_team_id")
        _upsert_em_fatias(supabase, "team_squad_fotmob", squads, "fotmob_team_id,fotmob_player_id")
        # Since unavailable can be duplicated, we don't strict upsert or we upsert without constraint if serial id
        if unavailables:
            for i in range(0, len(unavailables), TAMANHO_FATIA_UPSERT):
                supabase.table("team_unavailable_fotmob").insert(unavailables[i:i+TAMANHO_FATIA_UPSERT]).execute()
    
    # Process Players
    players_dir = pasta / "data_players"
    if players_dir.is_dir():
        arquivos = list(players_dir.glob("*.json.gz"))
        print(f"Lendo {len(arquivos)} arquivos de jogadores...")
        
        players, mvs, traits = [], [], []
        for i, f in enumerate(arquivos):
            try:
                with gzip.open(f) as fh:
                    d = json.load(fh)
                player_id = f.stem.split(".")[0]
                p, mv, t = parse_player_file(d, player_id)
                players.append(p)
                mvs.extend(mv)
                traits.extend(t)
            except Exception as e:
                print(f"Falha ao ler {f}: {e}")
                
            if (i+1) % 500 == 0:
                print(f"  processados {i+1}...")
                _upsert_em_fatias(supabase, "players_fotmob", players, "fotmob_player_id")
                _upsert_em_fatias(supabase, "player_market_values_fotmob", mvs, "fotmob_player_id,date")
                _upsert_em_fatias(supabase, "player_traits_fotmob", traits, "fotmob_player_id,trait_name")
                players, mvs, traits = [], [], []
                
        # Remainder
        _upsert_em_fatias(supabase, "players_fotmob", players, "fotmob_player_id")
        _upsert_em_fatias(supabase, "player_market_values_fotmob", mvs, "fotmob_player_id,date")
        _upsert_em_fatias(supabase, "player_traits_fotmob", traits, "fotmob_player_id,trait_name")
        
    print("Concluído.")

if __name__ == "__main__":
    main()
