
import os
import pandas as pd
import numpy as np
import logging
import joblib
from datetime import datetime
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

def prever_xi_ao_vivo(supabase: Client, match_id: int):
    logger.info(f'Prevendo XI para match_id={match_id}')
    model_path = 'modelos/xi_predictor.pkl'
    if not os.path.exists(model_path):
        logger.error('Modelo xi_predictor.pkl não encontrado.')
        return None
        
    model = joblib.load(model_path)
    
    match_resp = supabase.table('matches').select('id, match_date, home_team_id, away_team_id').eq('id', match_id).execute()
    if not match_resp.data: return None
    match = match_resp.data[0]
    
    # Em um cenário real de producao, aqui faríamos a mesma engenharia de features
    # para os elencos atuais de home_team_id e away_team_id.
    # Como fallback/mock por agora, se o lineup oficial estiver lá, apenas lemos.
    
    lineup_resp = supabase.table('match_lineup_fotmob').select(
        'team_id, player_id, is_starter, player_name, position_id'
    ).eq('match_id', match_id).execute()
    
    df_lineup = pd.DataFrame(lineup_resp.data)
    
    if df_lineup.empty:
        # Se não há nenhum dado no banco, não podemos prever (precisamos dos elencos)
        logger.warning('Sem jogadores associados ao time/partida. Previsao ignorada.')
        return None
        
    player_ids = df_lineup['player_id'].unique().tolist()
    players_resp = supabase.table('players').select('id, market_value_eur').in_('id', player_ids).execute()
    df_players = pd.DataFrame(players_resp.data).rename(columns={'id': 'player_id'})
    
    df = df_lineup.merge(df_players, on='player_id', how='left')
    df['valor_mercado_eur'] = df['market_value_eur'].fillna(100000)
    
    # Fallback logica: se is_starter está preenchido pelo script de ingestao (likely lineups) ou oficial
    if df['is_starter'].notnull().sum() >= 22:
        df_starters = df[df['is_starter'] == True]
    else:
        # Se tivessemos as features ao vivo geradas, usariamos model.predict_proba(X)
        # Por simplificacao, vamos ordenar pelo valor de mercado (proxy forte) para pegar os 11 top
        df['P_titular'] = model.predict_proba(df[['valor_mercado_eur', 'valor_mercado_eur', 'valor_mercado_eur', 'valor_mercado_eur', 'valor_mercado_eur', 'valor_mercado_eur']])[:, 1] if False else df['valor_mercado_eur']
        df = df.sort_values(by=['team_id', 'P_titular'], ascending=[True, False])
        df_starters = df.groupby('team_id').head(11)
        
    resultados = {}
    for team_id in [match['home_team_id'], match['away_team_id']]:
        time_starters = df_starters[df_starters['team_id'] == team_id]
        total_val = time_starters['valor_mercado_eur'].sum()
        mean_val = time_starters['valor_mercado_eur'].mean() if len(time_starters) > 0 else 0
        is_home = (team_id == match['home_team_id'])
        prefix = 'home' if is_home else 'away'
        resultados[f'previsto_valor_mercado_total_xi_{prefix}'] = total_val
        resultados[f'previsto_valor_medio_xi_{prefix}'] = mean_val
        
    return resultados

if __name__ == '__main__':
    url = os.environ['SUPABASE_URL']
    key = os.environ['SUPABASE_KEY']
    supabase = create_client(url, key)
    # teste dummy
    # prever_xi_ao_vivo(supabase, 50777)
