
import os
import pandas as pd
import numpy as np
import logging
from datetime import datetime
import joblib
from lightgbm import LGBMClassifier
from xgboost import XGBClassifier
from catboost import CatBoostClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, accuracy_score, log_loss, brier_score_loss
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

def carregar_dados(supabase: Client) -> pd.DataFrame:
    logger.info('Buscando dados de lineup no Supabase...')
    lineup_resp = supabase.table('match_lineup_fotmob').select('match_id, team_id, player_id, is_starter, is_home, position_id, rating').limit(50000).execute()
    if not lineup_resp.data:
        return pd.DataFrame()
    df_lineup = pd.DataFrame(lineup_resp.data)
    match_ids = df_lineup['match_id'].unique().tolist()
    matches_resp = supabase.table('matches').select('id, match_date, home_team_id, away_team_id').in_('id', match_ids).execute()
    df_matches = pd.DataFrame(matches_resp.data).rename(columns={'id': 'match_id'})
    player_ids = df_lineup['player_id'].unique().tolist()
    players_resp = supabase.table('players').select('id, position_id, market_value_eur, is_injured').in_('id', player_ids).execute()
    df_players = pd.DataFrame(players_resp.data).rename(columns={'id': 'player_id'})
    df = df_lineup.merge(df_matches, on='match_id', how='left').merge(df_players, on='player_id', how='left')
    return df

def engenharia_features(df: pd.DataFrame) -> pd.DataFrame:
    logger.info('Gerando features para o modelo XI...')
    df['match_date'] = pd.to_datetime(df['match_date'])
    df = df.sort_values(by=['player_id', 'match_date'])
    df['is_starter'] = df['is_starter'].fillna(False).astype(int)
    df['dias_desde_ultimo_jogo'] = df.groupby('player_id')['match_date'].diff().dt.days
    df['dias_desde_ultimo_jogo'] = df['dias_desde_ultimo_jogo'].fillna(14).clip(upper=30)
    df['jogos_acumulados'] = df.groupby('player_id').cumcount()
    df['titular_acumulado'] = df.groupby('player_id')['is_starter'].cumsum() - df['is_starter']
    df['hierarquia_elenco'] = np.where(df['jogos_acumulados'] > 0, df['titular_acumulado'] / df['jogos_acumulados'], 0.5)
    df['rating'] = pd.to_numeric(df['rating'], errors='coerce')
    df['media_rating_5j'] = df.groupby('player_id')['rating'].shift(1).rolling(5, min_periods=1).mean().fillna(6.0)
    df['posicao_num'] = df['position_id_y'].fillna(df['position_id_x']).fillna(0).astype(int)
    df['valor_mercado_eur'] = df['market_value_eur'].fillna(100000)
    df['is_lesionado'] = df['is_injured'].fillna(False).astype(int)
    return df.dropna(subset=['match_date', 'player_id', 'team_id'])

def treinar(df: pd.DataFrame, supabase: Client = None, config_id: str = None):
    logger.info('Treinando modelos (LightGBM, XGBoost, CatBoost) para Previsao de XI...')
    features = ['dias_desde_ultimo_jogo', 'hierarquia_elenco', 'media_rating_5j', 'posicao_num', 'valor_mercado_eur', 'is_lesionado']
    target = 'is_starter'
    X, y = df[features], df[target]
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)
    X_train_inner, X_val, y_train_inner, y_val = train_test_split(X_train, y_train, test_size=0.2, random_state=42, stratify=y_train)

    models = {
        'lightgbm': LGBMClassifier(n_estimators=300, max_depth=5, learning_rate=0.05, class_weight='balanced', random_state=42),
        'xgboost': XGBClassifier(n_estimators=300, max_depth=5, learning_rate=0.05, scale_pos_weight=(len(y_train)-sum(y_train))/sum(y_train), random_state=42, eval_metric='logloss'),
        'catboost': CatBoostClassifier(iterations=300, depth=5, learning_rate=0.05, auto_class_weights='Balanced', random_seed=42, verbose=0)
    }

    metrics_res = {}
    curves = {}

    for name, model in models.items():
        logger.info(f'Treinando {name}...')
        if name == 'lightgbm':
            model.fit(X_train_inner, y_train_inner, eval_set=[(X_val, y_val)])
            curves[name] = {'log_loss': model.evals_result_['valid_0']['binary_logloss']}
        elif name == 'xgboost':
            model.fit(X_train_inner, y_train_inner, eval_set=[(X_val, y_val)], verbose=False)
            curves[name] = {'log_loss': model.evals_result()['validation_0']['logloss']}
        elif name == 'catboost':
            model.fit(X_train_inner, y_train_inner, eval_set=(X_val, y_val), verbose=False)
            curves[name] = {'log_loss': model.get_evals_result()['validation']['Logloss']}

        y_pred = model.predict(X_test)
        y_proba = model.predict_proba(X_test)[:, 1]
        
        acc = accuracy_score(y_test, y_pred)
        ll = log_loss(y_test, y_proba)
        bs = brier_score_loss(y_test, y_proba)
        
        metrics_res[name] = {'accuracy': acc, 'log_loss': ll, 'brier_score': bs}
        logger.info(f'{name} - Acc: {acc:.4f} | LogLoss: {ll:.4f} | BS: {bs:.4f}')

        os.makedirs('modelos', exist_ok=True)
        joblib.dump(model, f'modelos/xi_predictor_{name}.pkl')

    # Update database if config_id is provided
    if supabase and config_id:
        final_metrics = {
            'models': metrics_res,
            'learning_curves': curves,
            'feature_importance': {f: float(models['lightgbm'].feature_importances_[i]) for i, f in enumerate(features)}
        }
        supabase.table('custom_model_configs').update({
            'status': 'concluido',
            'trained_at': datetime.utcnow().isoformat(),
            'metrics': final_metrics
        }).eq('id', config_id).execute()

if __name__ == '__main__':
    import sys
    url = os.environ['SUPABASE_URL']
    key = os.environ['SUPABASE_KEY']
    sb = create_client(url, key)
    
    config_id = sys.argv[1] if len(sys.argv) > 1 else None
    
    df = carregar_dados(sb)
    if not df.empty:
        treinar(engenharia_features(df), sb, config_id)
