#!/usr/bin/env python3
import os
import sys
import pandas as pd
from supabase import create_client, Client

def obter_credenciais():
    """Tenta obter URL e KEY do Supabase do ambiente ou de .env.firebase"""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    
    if url and key:
        return url, key, "ambiente (write/read)"

    # Tenta ler do .env.firebase na raiz
    caminho_env = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env.firebase")
    if os.path.exists(caminho_env):
        config = {}
        with open(caminho_env, "r", encoding="utf-8") as f:
            for linha in f:
                linha = linha.strip()
                if not linha or linha.startswith("#"):
                    continue
                if "=" in linha:
                    k, v = linha.split("=", 1)
                    config[k.strip()] = v.strip()
        
        url_fb = config.get("VITE_SUPABASE_URL")
        key_fb = config.get("VITE_SUPABASE_KEY")
        if url_fb and key_fb:
            return url_fb, key_fb, ".env.firebase (public/read-only)"
            
    # Fallback para as chaves públicas conhecidas do Brasileirão/europeu
    url_publica = "https://cgurxgfdmpmsnrshqycx.supabase.co"
    key_publica = "sb_publishable_pOX8y7CjfFALS_dCZ61w7g_wEPu7T4g"
    return url_publica, key_publica, "pública fallback (read-only)"

def buscar_tudo_paginado(client: Client, tabela: str, colunas: str = "*") -> pd.DataFrame:
    """Busca todas as linhas de uma tabela usando paginação (limite de 1000 do Supabase)"""
    print(f"Buscando dados da tabela '{tabela}'...", end="", flush=True)
    dados = []
    pagina = 0
    tamanho_pagina = 1000
    while True:
        try:
            res = client.table(tabela).select(colunas).range(
                pagina * tamanho_pagina, 
                (pagina + 1) * tamanho_pagina - 1
            ).execute()
            fatia = res.data or []
            dados.extend(fatia)
            if len(fatia) < tamanho_pagina:
                break
            pagina += 1
        except Exception as e:
            print(f"\nErro ao buscar tabela {tabela}: {e}")
            break
    print(f" {len(dados)} linhas carregadas.")
    return pd.DataFrame(dados)

def main():
    url, key, origem = obter_credenciais()
    print(f"Conectando ao Supabase via origem: {origem}")
    print(f"URL: {url}")
    
    try:
        supabase = create_client(url, key)
    except Exception as e:
        print(f"Erro ao criar cliente Supabase: {e}")
        sys.exit(1)
        
    # 1. Carregar tabelas auxiliares para enriquecer o CSV
    df_matches = buscar_tudo_paginado(
        supabase, 
        "matches", 
        "id, league_id, home_team_id, away_team_id, match_date, season, status, home_goals, away_goals"
    )
    df_teams = buscar_tudo_paginado(supabase, "teams", "id, name")
    df_leagues = buscar_tudo_paginado(supabase, "leagues", "id, name")
    
    if df_matches.empty:
        print("Tabela 'matches' está vazia. Não é possível cruzar os dados.")
        sys.exit(1)
        
    # Mapeamentos para joins
    mapa_times = dict(zip(df_teams["id"], df_teams["name"]))
    mapa_leagues = dict(zip(df_leagues["id"], df_leagues["name"]))
    
    # Enriquecer partidas com nomes de times e ligas
    df_matches["home_team"] = df_matches["home_team_id"].map(mapa_times)
    df_matches["away_team"] = df_matches["away_team_id"].map(mapa_times)
    df_matches["league_name"] = df_matches["league_id"].map(mapa_leagues)
    
    # Determinar resultado real
    def obter_resultado_1X2(row):
        if row["status"] != "finished" or pd.isna(row["home_goals"]) or pd.isna(row["away_goals"]):
            return "scheduled"
        if row["home_goals"] > row["away_goals"]:
            return "home"
        elif row["home_goals"] < row["away_goals"]:
            return "away"
        else:
            return "draw"
            
    def obter_resultado_OU25(row):
        if row["status"] != "finished" or pd.isna(row["home_goals"]) or pd.isna(row["away_goals"]):
            return "scheduled"
        total = row["home_goals"] + row["away_goals"]
        return "over" if total > 2.5 else "under"

    df_matches["resultado_real_1X2"] = df_matches.apply(obter_resultado_1X2, axis=1)
    df_matches["resultado_real_OU25"] = df_matches.apply(obter_resultado_OU25, axis=1)
    
    # Colunas de interesse das partidas
    match_cols = [
        "id", "league_name", "season", "match_date", 
        "home_team", "away_team", "home_goals", "away_goals", 
        "resultado_real_1X2", "resultado_real_OU25"
    ]
    df_matches_clean = df_matches[match_cols].rename(columns={"id": "match_id"})
    
    # 2. Carregar e exportar a tabela de previsões moderna (predicoes)
    df_predicoes = buscar_tudo_paginado(supabase, "predicoes")
    
    if not df_predicoes.empty:
        # Fazer merge com os dados de partida
        df_export_pred = pd.merge(df_predicoes, df_matches_clean, on="match_id", how="inner")
        
        # Ordenar de forma legível (data do jogo desc, modelo, mercado)
        df_export_pred = df_export_pred.sort_values(
            by=["match_date", "model_name", "mercado"], 
            ascending=[False, True, True]
        )
        
        nome_csv_pred = "predicoes_modelos_ml.csv"
        df_export_pred.to_csv(nome_csv_pred, index=False, encoding="utf-8-sig")
        print(f"-> Previsões do pipeline ML exportadas com sucesso para: {os.path.abspath(nome_csv_pred)}")
    else:
        print("A tabela 'predicoes' está vazia no Supabase.")

    # 3. Carregar e exportar a tabela de previsões antiga (model_predictions)
    df_model_predictions = buscar_tudo_paginado(supabase, "model_predictions")
    
    if not df_model_predictions.empty:
        # Fazer merge com os dados de partida
        df_export_model = pd.merge(df_model_predictions, df_matches_clean, on="match_id", how="inner")
        
        # Ordenar de forma legível
        df_export_model = df_export_model.sort_values(
            by=["match_date", "model_name", "market", "selection"], 
            ascending=[False, True, True, True]
        )
        
        nome_csv_model = "previsoes_antigas.csv"
        df_export_model.to_csv(nome_csv_model, index=False, encoding="utf-8-sig")
        print(f"-> Previsões antigas (model_predictions) exportadas para: {os.path.abspath(nome_csv_model)}")
    else:
        print("A tabela 'model_predictions' está vazia no Supabase.")

if __name__ == "__main__":
    main()
