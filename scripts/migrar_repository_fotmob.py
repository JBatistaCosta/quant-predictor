
# migrar_repository_fotmob.py
# See full implementation in this file
# Run: python scripts/migrar_repository_fotmob.py

import gzip, json, os, logging
from pathlib import Path
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

REPO_PATH = Path(__file__).parent.parent / 'scratch_fotmob_repo'
BATCH_SIZE = 100

FOTMOB_LEAGUE_MAP = {
    '1': 1, '4': 4, '7': 7, '10': 10, '13': 13, '16': 16, '23': 23, '30': 30,
}

STAT_KEY_MAP = {
    'BallPossesion': 'possession', 'total_shots': 'total_shots',
    'ShotsOnTarget': 'shots_on_target', 'ShotsOffTarget': 'shots_off_target',
    'blocked_shots': 'shots_blocked', 'shots_inside_box': 'shots_inside_box',
    'shots_outside_box': 'shots_outside_box', 'big_chance': 'big_chances',
    'big_chance_missed_title': 'big_chances_missed', 'accurate_passes': 'accurate_passes',
    'long_balls_accurate': 'accurate_long_balls', 'accurate_crosses': 'accurate_crosses',
    'matchstats.headers.tackles': 'tackles', 'interceptions': 'interceptions',
    'shot_blocks': 'blocks', 'clearances': 'clearances', 'keeper_saves': 'keeper_saves',
    'duel_won': 'duels_won', 'aerials_won': 'aerial_duels_won',
    'dribbles_succeeded': 'successful_dribbles', 'fouls': 'fouls_committed',
    'yellow_cards': 'yellow_cards', 'red_cards': 'red_cards', 'corners': 'corners',
    'touches_opp_box': 'touches_opp_box', 'Offsides': 'offsides',
    'shots_woodwork': 'shots_woodwork', 'own_half_passes': 'own_half_passes',
    'opposition_half_passes': 'opposition_half_passes', 'player_throws': 'throws',
    'xg': 'xg', 'xgot': 'xgot',
}


def _load_gz(path):
    try:
        with gzip.open(path, 'rt', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f'Erro ao ler {path}: {e}')
        return None


def _parse_val(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        return int(v.split(' ')[0]) if v and v[0].isdigit() else None
    return None


def _upsert_batch(supabase, table, rows, on_conflict='id'):
    for i in range(0, len(rows), BATCH_SIZE):
        supabase.table(table).upsert(rows[i:i+BATCH_SIZE], on_conflict=on_conflict).execute()


def _extrair_partida(match_id, data, league_id, year):
    general = data.get('general', {})
    header = data.get('header', {})
    home_team = general.get('homeTeam', {})
    away_team = general.get('awayTeam', {})
    teams_header = header.get('teams', [])
    home_score = teams_header[0].get('score') if len(teams_header) > 0 else None
    away_score = teams_header[1].get('score') if len(teams_header) > 1 else None
    return {
        'id': int(match_id),
        'league_id': league_id,
        'season': year,
        'match_date': general.get('matchTimeUTC') or general.get('matchTimeUTCDate'),
        'home_team_id': home_team.get('id'),
        'away_team_id': away_team.get('id'),
        'home_goals': home_score,
        'away_goals': away_score,
        'status': 'finished' if general.get('finished') else ('live' if general.get('started') else 'scheduled'),
        'source': 'fotmob',
    }


def _extrair_stats_times(match_id, data, home_team_id, away_team_id):
    rows = []
    stats_section = data.get('content', {}).get('stats', {})
    all_period = stats_section.get('Periods', {}).get('All', {})
    if not all_period:
        return rows
    stat_groups = all_period.get('stats', [])
    home_vals = {'match_id': match_id, 'team_id': home_team_id}
    away_vals = {'match_id': match_id, 'team_id': away_team_id}
    for group in stat_groups:
        for stat in group.get('stats', []):
            key = stat.get('key')
            values = stat.get('stats', [None, None])
            col = STAT_KEY_MAP.get(key)
            if not col:
                continue
            home_vals[col] = _parse_val(values[0] if values else None)
            away_vals[col] = _parse_val(values[1] if len(values) > 1 else None)
    rows.append(home_vals)
    rows.append(away_vals)
    return rows


def _extrair_lineup(match_id, data):
    rows = []
    lineup = data.get('content', {}).get('lineup', {})
    if not lineup:
        return rows
    for side, is_home in [('homeTeam', True), ('awayTeam', False)]:
        td = lineup.get(side, {})
        team_id = td.get('id')
        team_rating = td.get('rating')
        formation = td.get('formation')
        for player in td.get('starters', []):
            perf = player.get('performance', {})
            rows.append({
                'match_id': match_id, 'team_id': team_id,
                'player_id': player.get('id'), 'player_name': player.get('name'),
                'is_starter': True, 'is_home': is_home,
                'shirt_number': player.get('shirtNumber'),
                'position_id': player.get('positionId'),
                'usual_position_id': player.get('usualPlayingPositionId'),
                'rating': perf.get('rating'),
                'is_player_of_match': perf.get('playerOfTheMatch', False),
                'formation': formation, 'team_rating': team_rating,
            })
        for player in td.get('subs', []):
            perf = player.get('performance', {})
            rows.append({
                'match_id': match_id, 'team_id': team_id,
                'player_id': player.get('id'), 'player_name': player.get('name'),
                'is_starter': False, 'is_home': is_home,
                'shirt_number': player.get('shirtNumber'),
                'position_id': player.get('positionId'),
                'usual_position_id': player.get('usualPlayingPositionId'),
                'rating': perf.get('rating'), 'is_player_of_match': False,
                'formation': formation, 'team_rating': team_rating,
            })
        for player in td.get('unavailable', []):
            rows.append({
                'match_id': match_id, 'team_id': team_id,
                'player_id': player.get('id'), 'player_name': player.get('name'),
                'is_starter': None, 'is_home': is_home,
                'injury_reason': player.get('reason'), 'is_unavailable': True,
            })
    return rows


def _extrair_shots(match_id, data):
    rows = []
    shotmap = data.get('content', {}).get('shotmap', {})
    for shot in shotmap.get('shots', []):
        rows.append({
            'match_id': match_id,
            'player_id': shot.get('playerId'), 'team_id': shot.get('teamId'),
            'event_type': shot.get('eventType'), 'situation': shot.get('situation'),
            'shot_type': shot.get('shotType'),
            'x': shot.get('x'), 'y': shot.get('y'),
            'xg': shot.get('expectedGoals'), 'xgot': shot.get('expectedGoalsOnTarget'),
            'on_goal': shot.get('onGoal'), 'is_blocked': shot.get('isBlocked'),
            'period': shot.get('period'),
            'minute': shot.get('min'), 'minute_add': shot.get('minAdded'),
        })
    return rows


def _extrair_player(player_id, data):
    pi = data.get('playerInformation', [])
    height_cm = market_value = None
    for item in pi:
        title = (item.get('title', {}).get('key') or '').lower()
        if 'height' in title:
            try:
                height_cm = float(str(item.get('value', {}).get('fallback', '')).replace('cm','').strip())
            except: pass
        if 'market' in title or 'value' in title:
            nv = item.get('value', {}).get('numberValue')
            if nv:
                market_value = float(nv)
    pos_desc = data.get('positionDescription', {})
    injury = data.get('injuryInformation')
    primary_team = data.get('primaryTeam', {})
    return {
        'id': int(player_id), 'name': data.get('name'),
        'birth_date': data.get('birthDate'), 'height_cm': height_cm,
        'market_value_eur': market_value,
        'position': (pos_desc.get('primaryPosition') or {}).get('label'),
        'position_id': (pos_desc.get('primaryPosition') or {}).get('id'),
        'team_id': (primary_team or {}).get('teamId'),
        'is_injured': bool(injury),
        'injury_description': (injury or {}).get('injuryName') if injury else None,
        'source': 'fotmob',
    }


def migrar(supabase, repo_path=REPO_PATH):
    data_path = Path(repo_path) / 'data'
    players_path = Path(repo_path) / 'data_players'

    for league_dir in sorted(data_path.iterdir()):
        league_str = league_dir.name
        league_id = FOTMOB_LEAGUE_MAP.get(league_str)
        if league_id is None:
            logger.warning(f'Liga {league_str} nao mapeada')
            continue

        for year_dir in sorted(league_dir.iterdir()):
            year = year_dir.name
            match_rows, stats_rows, lineup_rows, shot_rows = [], [], [], []
            gz_files = list(year_dir.glob('*.json.gz'))
            logger.info(f'Liga {league_str} / {year}: {len(gz_files)} partidas')

            for gz_file in gz_files:
                match_id = gz_file.stem.replace('.json', '')
                data = _load_gz(gz_file)
                if not data: continue
                general = data.get('general', {})
                home_id = (general.get('homeTeam') or {}).get('id')
                away_id = (general.get('awayTeam') or {}).get('id')
                m = _extrair_partida(match_id, data, league_id, year)
                if m: match_rows.append(m)
                if home_id and away_id:
                    stats_rows.extend(_extrair_stats_times(int(match_id), data, home_id, away_id))
                    lineup_rows.extend(_extrair_lineup(int(match_id), data))
                    shot_rows.extend(_extrair_shots(int(match_id), data))

            if match_rows: _upsert_batch(supabase, 'matches', match_rows)
            if stats_rows: _upsert_batch(supabase, 'match_stats_fotmob', stats_rows, on_conflict='match_id,team_id')
            if lineup_rows: _upsert_batch(supabase, 'match_lineup_fotmob', lineup_rows, on_conflict='match_id,player_id')
            if shot_rows: _upsert_batch(supabase, 'match_shots_fotmob', shot_rows, on_conflict='match_id,player_id,minute')
            logger.info(f'  OK {year}: {len(match_rows)} matches, {len(stats_rows)//2} stats, {len(lineup_rows)} lineup, {len(shot_rows)} shots')

    # Jogadores
    player_rows = []
    for gz_file in players_path.glob('*.json.gz'):
        data = _load_gz(gz_file)
        if data:
            player_rows.append(_extrair_player(gz_file.stem.replace('.json',''), data))
    if player_rows:
        _upsert_batch(supabase, 'players', player_rows)
        logger.info(f'Jogadores migrados: {len(player_rows)}')


if __name__ == '__main__':
    url = os.environ['SUPABASE_URL']
    key = os.environ['SUPABASE_KEY']
    migrar(create_client(url, key))
