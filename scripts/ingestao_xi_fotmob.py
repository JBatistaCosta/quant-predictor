
import os
import requests
import logging
from datetime import datetime, timedelta
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)
FOTMOB_API_URL = 'https://www.fotmob.com/api'

def _get_upcoming_matches(supabase: Client) -> list[dict]:
    hoje = datetime.utcnow().date()
    daqui_3_dias = hoje + timedelta(days=3)
    resp = supabase.table('matches').select('id').gte('match_date', str(hoje)).lte('match_date', str(daqui_3_dias)).execute()
    return resp.data or []

def fetch_match_details(match_id: int) -> dict | None:
    try:
        url = f'{FOTMOB_API_URL}/matchDetails?matchId={match_id}'
        resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f'Erro ao buscar {match_id}: {e}')
        return None

def processar_partida(supabase: Client, match_id: int, data: dict):
    lineup = data.get('content', {}).get('lineup', {})
    if not lineup: return
    
    lineup_rows = []
    player_updates = []
    
    for side, is_home in [('homeTeam', True), ('awayTeam', False)]:
        team_data = lineup.get(side, {})
        team_id = team_data.get('id')
        
        for p in team_data.get('starters', []):
            lineup_rows.append({
                'match_id': match_id, 'team_id': team_id,
                'player_id': p.get('id'), 'player_name': p.get('name'),
                'is_starter': True, 'is_home': is_home, 'position_id': p.get('positionId')
            })
            
        for p in team_data.get('subs', []):
            lineup_rows.append({
                'match_id': match_id, 'team_id': team_id,
                'player_id': p.get('id'), 'player_name': p.get('name'),
                'is_starter': False, 'is_home': is_home,
            })
            
        for p in team_data.get('unavailable', []):
            reason = p.get('reason', '')
            lineup_rows.append({
                'match_id': match_id, 'team_id': team_id,
                'player_id': p.get('id'), 'player_name': p.get('name'),
                'is_starter': None, 'is_home': is_home,
                'injury_reason': reason, 'is_unavailable': True
            })
            player_updates.append({
                'id': p.get('id'),
                'is_injured': 'injur' in reason.lower(),
                'injury_description': reason
            })

    if lineup_rows:
        supabase.table('match_lineup_fotmob').upsert(lineup_rows, on_conflict='match_id,player_id').execute()
    for p in player_updates:
        supabase.table('players').update({
            'is_injured': p['is_injured'], 'injury_description': p['injury_description']
        }).eq('id', p['id']).execute()

def run(supabase: Client):
    matches = _get_upcoming_matches(supabase)
    logger.info(f'Encontradas {len(matches)} partidas')
    for m in matches:
        data = fetch_match_details(m['id'])
        if data:
            processar_partida(supabase, m['id'], data)

if __name__ == '__main__':
    url = os.environ['SUPABASE_URL']
    key = os.environ['SUPABASE_KEY']
    run(create_client(url, key))
