-- Parte 2 do backfill de schema: constraints, índices extras, RLS e
-- políticas para as mesmas 52 tabelas de `_tabelas_legado.sql`. Depende
-- daquela migration ter rodado antes (por isso o timestamp 023100, um
-- minuto depois de 023000) -- nenhuma constraint aqui é adicionada antes
-- de TODAS as 52 tabelas existirem, então a ordem ENTRE elas na migration
-- anterior não importa.
--
-- Cada ALTER TABLE ADD CONSTRAINT é guardado por um bloco DO...IF NOT
-- EXISTS. Isso não é só estilo -- é necessário pra rodar em produção sem
-- erro: lá as tabelas (e as constraints, com o MESMO nome) já existem, e
-- Postgres não tem `ADD CONSTRAINT IF NOT EXISTS` nativo. Sem a guarda,
-- rodar esta migration em produção falharia com "constraint already
-- exists" na primeira linha. CREATE INDEX aceita IF NOT EXISTS direto;
-- ENABLE ROW LEVEL SECURITY é idempotente por natureza (habilitar de novo
-- não é erro); CREATE POLICY precisa da mesma guarda que as constraints.

-- ================= CONSTRAINTS (PK, UNIQUE, CHECK, FK) =================

DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'api_football_predictions_cache_pkey' AND conrelid = 'public.api_football_predictions_cache'::regclass
  ) THEN
    ALTER TABLE public.api_football_predictions_cache ADD CONSTRAINT api_football_predictions_cache_pkey PRIMARY KEY (fixture_id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'assinaturas_api_pkey' AND conrelid = 'public.assinaturas_api'::regclass
  ) THEN
    ALTER TABLE public.assinaturas_api ADD CONSTRAINT assinaturas_api_pkey PRIMARY KEY (id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'carteira_simulada_pkey' AND conrelid = 'public.carteira_simulada'::regclass
  ) THEN
    ALTER TABLE public.carteira_simulada ADD CONSTRAINT carteira_simulada_pkey PRIMARY KEY (id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'config_carteira_pkey' AND conrelid = 'public.config_carteira'::regclass
  ) THEN
    ALTER TABLE public.config_carteira ADD CONSTRAINT config_carteira_pkey PRIMARY KEY (id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'custom_model_ondemand_predictions_pkey' AND conrelid = 'public.custom_model_ondemand_predictions'::regclass
  ) THEN
    ALTER TABLE public.custom_model_ondemand_predictions ADD CONSTRAINT custom_model_ondemand_predictions_pkey PRIMARY KEY (id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'detalhes_clube_pkey' AND conrelid = 'public.detalhes_clube'::regclass
  ) THEN
    ALTER TABLE public.detalhes_clube ADD CONSTRAINT detalhes_clube_pkey PRIMARY KEY (equipe_id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'detalhes_selecao_pkey' AND conrelid = 'public.detalhes_selecao'::regclass
  ) THEN
    ALTER TABLE public.detalhes_selecao ADD CONSTRAINT detalhes_selecao_pkey PRIMARY KEY (equipe_id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'equipe_liga_temporada_pkey' AND conrelid = 'public.equipe_liga_temporada'::regclass
  ) THEN
    ALTER TABLE public.equipe_liga_temporada ADD CONSTRAINT equipe_liga_temporada_pkey PRIMARY KEY (id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'equipes_pkey' AND conrelid = 'public.equipes'::regclass
  ) THEN
    ALTER TABLE public.equipes ADD CONSTRAINT equipes_pkey PRIMARY KEY (id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'eventos_pkey' AND conrelid = 'public.eventos'::regclass
  ) THEN
    ALTER TABLE public.eventos ADD CONSTRAINT eventos_pkey PRIMARY KEY (id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'fases_pkey' AND conrelid = 'public.fases'::regclass
  ) THEN
    ALTER TABLE public.fases ADD CONSTRAINT fases_pkey PRIMARY KEY (id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'importacoes_pkey' AND conrelid = 'public.importacoes'::regclass
  ) THEN
    ALTER TABLE public.importacoes ADD CONSTRAINT importacoes_pkey PRIMARY KEY (id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'instituicao_nomes_pkey' AND conrelid = 'public.instituicao_nomes'::regclass
  ) THEN
    ALTER TABLE public.instituicao_nomes ADD CONSTRAINT instituicao_nomes_pkey PRIMARY KEY (id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'instituicao_simbolos_pkey' AND conrelid = 'public.instituicao_simbolos'::regclass
  ) THEN
    ALTER TABLE public.instituicao_simbolos ADD CONSTRAINT instituicao_simbolos_pkey PRIMARY KEY (id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'instituicao_sucessoes_pkey' AND conrelid = 'public.instituicao_sucessoes'::regclass
  ) THEN
    ALTER TABLE public.instituicao_sucessoes ADD CONSTRAINT instituicao_sucessoes_pkey PRIMARY KEY (id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'instituicoes_pkey' AND conrelid = 'public.instituicoes'::regclass
  ) THEN
    ALTER TABLE public.instituicoes ADD CONSTRAINT instituicoes_pkey PRIMARY KEY (id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'league_model_params_pkey' AND conrelid = 'public.league_model_params'::regclass
  ) THEN
    ALTER TABLE public.league_model_params ADD CONSTRAINT league_model_params_pkey PRIMARY KEY (id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'liga_fonte_externa_pkey' AND conrelid = 'public.liga_fonte_externa'::regclass
  ) THEN
    ALTER TABLE public.liga_fonte_externa ADD CONSTRAINT liga_fonte_externa_pkey PRIMARY KEY (id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'liga_oddspapi_tournament_pkey' AND conrelid = 'public.liga_oddspapi_tournament'::regclass
  ) THEN
    ALTER TABLE public.liga_oddspapi_tournament ADD CONSTRAINT liga_oddspapi_tournament_pkey PRIMARY KEY (league_id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'ligas_pkey' AND conrelid = 'public.ligas'::regclass
  ) THEN
    ALTER TABLE public.ligas ADD CONSTRAINT ligas_pkey PRIMARY KEY (id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'ligas_temporadas_pkey' AND conrelid = 'public.ligas_temporadas'::regclass
  ) THEN
    ALTER TABLE public.ligas_temporadas ADD CONSTRAINT ligas_temporadas_pkey PRIMARY KEY (id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'match_context_fotmob_pkey' AND conrelid = 'public.match_context_fotmob'::regclass
  ) THEN
    ALTER TABLE public.match_context_fotmob ADD CONSTRAINT match_context_fotmob_pkey PRIMARY KEY (match_id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'match_features_contexto_pkey' AND conrelid = 'public.match_features_contexto'::regclass
  ) THEN
    ALTER TABLE public.match_features_contexto ADD CONSTRAINT match_features_contexto_pkey PRIMARY KEY (match_id, team_id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'match_odds_pkey' AND conrelid = 'public.match_odds'::regclass
  ) THEN
    ALTER TABLE public.match_odds ADD CONSTRAINT match_odds_pkey PRIMARY KEY (id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'match_player_stats_fotmob_pkey' AND conrelid = 'public.match_player_stats_fotmob'::regclass
  ) THEN
    ALTER TABLE public.match_player_stats_fotmob ADD CONSTRAINT match_player_stats_fotmob_pkey PRIMARY KEY (id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'match_shots_fotmob_pkey' AND conrelid = 'public.match_shots_fotmob'::regclass
  ) THEN
    ALTER TABLE public.match_shots_fotmob ADD CONSTRAINT match_shots_fotmob_pkey PRIMARY KEY (id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'match_source_ids_pkey' AND conrelid = 'public.match_source_ids'::regclass
  ) THEN
    ALTER TABLE public.match_source_ids ADD CONSTRAINT match_source_ids_pkey PRIMARY KEY (id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'match_stats_pkey' AND conrelid = 'public.match_stats'::regclass
  ) THEN
    ALTER TABLE public.match_stats ADD CONSTRAINT match_stats_pkey PRIMARY KEY (id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'match_stats_fotmob_pkey' AND conrelid = 'public.match_stats_fotmob'::regclass
  ) THEN
    ALTER TABLE public.match_stats_fotmob ADD CONSTRAINT match_stats_fotmob_pkey PRIMARY KEY (id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'mercados_avaliados_pkey' AND conrelid = 'public.mercados_avaliados'::regclass
  ) THEN
    ALTER TABLE public.mercados_avaliados ADD CONSTRAINT mercados_avaliados_pkey PRIMARY KEY (id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'model_calibration_pkey' AND conrelid = 'public.model_calibration'::regclass
  ) THEN
    ALTER TABLE public.model_calibration ADD CONSTRAINT model_calibration_pkey PRIMARY KEY (id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'model_config_pkey' AND conrelid = 'public.model_config'::regclass
  ) THEN
    ALTER TABLE public.model_config ADD CONSTRAINT model_config_pkey PRIMARY KEY (model_name);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'model_predictions_pkey' AND conrelid = 'public.model_predictions'::regclass
  ) THEN
    ALTER TABLE public.model_predictions ADD CONSTRAINT model_predictions_pkey PRIMARY KEY (id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'model_stat_estimates_pkey' AND conrelid = 'public.model_stat_estimates'::regclass
  ) THEN
    ALTER TABLE public.model_stat_estimates ADD CONSTRAINT model_stat_estimates_pkey PRIMARY KEY (id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'notas_pkey' AND conrelid = 'public.notas'::regclass
  ) THEN
    ALTER TABLE public.notas ADD CONSTRAINT notas_pkey PRIMARY KEY (id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'odds_market_pkey' AND conrelid = 'public.odds_market'::regclass
  ) THEN
    ALTER TABLE public.odds_market ADD CONSTRAINT odds_market_pkey PRIMARY KEY (id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'oddspapi_cache_pkey' AND conrelid = 'public.oddspapi_cache'::regclass
  ) THEN
    ALTER TABLE public.oddspapi_cache ADD CONSTRAINT oddspapi_cache_pkey PRIMARY KEY (chave);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'player_availability_fotmob_pkey' AND conrelid = 'public.player_availability_fotmob'::regclass
  ) THEN
    ALTER TABLE public.player_availability_fotmob ADD CONSTRAINT player_availability_fotmob_pkey PRIMARY KEY (id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'player_career_history_fotmob_pkey' AND conrelid = 'public.player_career_history_fotmob'::regclass
  ) THEN
    ALTER TABLE public.player_career_history_fotmob ADD CONSTRAINT player_career_history_fotmob_pkey PRIMARY KEY (id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'player_details_fotmob_pkey' AND conrelid = 'public.player_details_fotmob'::regclass
  ) THEN
    ALTER TABLE public.player_details_fotmob ADD CONSTRAINT player_details_fotmob_pkey PRIMARY KEY (player_id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'player_ratings_pkey' AND conrelid = 'public.player_ratings'::regclass
  ) THEN
    ALTER TABLE public.player_ratings ADD CONSTRAINT player_ratings_pkey PRIMARY KEY (player_id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'player_trophies_fotmob_pkey' AND conrelid = 'public.player_trophies_fotmob'::regclass
  ) THEN
    ALTER TABLE public.player_trophies_fotmob ADD CONSTRAINT player_trophies_fotmob_pkey PRIMARY KEY (id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'predictions_log_pkey' AND conrelid = 'public.predictions_log'::regclass
  ) THEN
    ALTER TABLE public.predictions_log ADD CONSTRAINT predictions_log_pkey PRIMARY KEY (id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'simulacoes_pkey' AND conrelid = 'public.simulacoes'::regclass
  ) THEN
    ALTER TABLE public.simulacoes ADD CONSTRAINT simulacoes_pkey PRIMARY KEY (id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'team_coach_history_fotmob_pkey' AND conrelid = 'public.team_coach_history_fotmob'::regclass
  ) THEN
    ALTER TABLE public.team_coach_history_fotmob ADD CONSTRAINT team_coach_history_fotmob_pkey PRIMARY KEY (id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'team_elo_pkey' AND conrelid = 'public.team_elo'::regclass
  ) THEN
    ALTER TABLE public.team_elo ADD CONSTRAINT team_elo_pkey PRIMARY KEY (id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'team_elo_external_pkey' AND conrelid = 'public.team_elo_external'::regclass
  ) THEN
    ALTER TABLE public.team_elo_external ADD CONSTRAINT team_elo_external_pkey PRIMARY KEY (id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'team_elo_history_pkey' AND conrelid = 'public.team_elo_history'::regclass
  ) THEN
    ALTER TABLE public.team_elo_history ADD CONSTRAINT team_elo_history_pkey PRIMARY KEY (id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'team_source_ids_pkey' AND conrelid = 'public.team_source_ids'::regclass
  ) THEN
    ALTER TABLE public.team_source_ids ADD CONSTRAINT team_source_ids_pkey PRIMARY KEY (id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'team_stats_pkey' AND conrelid = 'public.team_stats'::regclass
  ) THEN
    ALTER TABLE public.team_stats ADD CONSTRAINT team_stats_pkey PRIMARY KEY (team_name);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'team_strengths_pkey' AND conrelid = 'public.team_strengths'::regclass
  ) THEN
    ALTER TABLE public.team_strengths ADD CONSTRAINT team_strengths_pkey PRIMARY KEY (id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'team_transfers_fotmob_pkey' AND conrelid = 'public.team_transfers_fotmob'::regclass
  ) THEN
    ALTER TABLE public.team_transfers_fotmob ADD CONSTRAINT team_transfers_fotmob_pkey PRIMARY KEY (id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'league_model_params_league_id_model_name_stat_param_name_key' AND conrelid = 'public.league_model_params'::regclass
  ) THEN
    ALTER TABLE public.league_model_params ADD CONSTRAINT league_model_params_league_id_model_name_stat_param_name_key UNIQUE (league_id, model_name, stat, param_name);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'liga_fonte_externa_league_id_sistema_key' AND conrelid = 'public.liga_fonte_externa'::regclass
  ) THEN
    ALTER TABLE public.liga_fonte_externa ADD CONSTRAINT liga_fonte_externa_league_id_sistema_key UNIQUE (league_id, sistema);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'match_odds_equipe_mandante_equipe_visitante_key' AND conrelid = 'public.match_odds'::regclass
  ) THEN
    ALTER TABLE public.match_odds ADD CONSTRAINT match_odds_equipe_mandante_equipe_visitante_key UNIQUE (equipe_mandante, equipe_visitante);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'match_player_stats_fotmob_match_id_fotmob_player_id_key' AND conrelid = 'public.match_player_stats_fotmob'::regclass
  ) THEN
    ALTER TABLE public.match_player_stats_fotmob ADD CONSTRAINT match_player_stats_fotmob_match_id_fotmob_player_id_key UNIQUE (match_id, fotmob_player_id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'match_shots_fotmob_fotmob_shot_id_key' AND conrelid = 'public.match_shots_fotmob'::regclass
  ) THEN
    ALTER TABLE public.match_shots_fotmob ADD CONSTRAINT match_shots_fotmob_fotmob_shot_id_key UNIQUE (fotmob_shot_id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'match_source_ids_match_id_source_key' AND conrelid = 'public.match_source_ids'::regclass
  ) THEN
    ALTER TABLE public.match_source_ids ADD CONSTRAINT match_source_ids_match_id_source_key UNIQUE (match_id, source);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'match_source_ids_source_source_id_key' AND conrelid = 'public.match_source_ids'::regclass
  ) THEN
    ALTER TABLE public.match_source_ids ADD CONSTRAINT match_source_ids_source_source_id_key UNIQUE (source, source_id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'match_stats_match_id_team_id_key' AND conrelid = 'public.match_stats'::regclass
  ) THEN
    ALTER TABLE public.match_stats ADD CONSTRAINT match_stats_match_id_team_id_key UNIQUE (match_id, team_id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'match_stats_fotmob_match_id_team_id_key' AND conrelid = 'public.match_stats_fotmob'::regclass
  ) THEN
    ALTER TABLE public.match_stats_fotmob ADD CONSTRAINT match_stats_fotmob_match_id_team_id_key UNIQUE (match_id, team_id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'model_calibration_unique_combo' AND conrelid = 'public.model_calibration'::regclass
  ) THEN
    ALTER TABLE public.model_calibration ADD CONSTRAINT model_calibration_unique_combo UNIQUE (model_name, market, selection, method);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'model_predictions_match_id_model_name_market_selection_key' AND conrelid = 'public.model_predictions'::regclass
  ) THEN
    ALTER TABLE public.model_predictions ADD CONSTRAINT model_predictions_match_id_model_name_market_selection_key UNIQUE (match_id, model_name, market, selection);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'model_stat_estimates_match_id_model_name_stat_key' AND conrelid = 'public.model_stat_estimates'::regclass
  ) THEN
    ALTER TABLE public.model_stat_estimates ADD CONSTRAINT model_stat_estimates_match_id_model_name_stat_key UNIQUE (match_id, model_name, stat);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'player_availability_fotmob_team_id_fotmob_player_id_key' AND conrelid = 'public.player_availability_fotmob'::regclass
  ) THEN
    ALTER TABLE public.player_availability_fotmob ADD CONSTRAINT player_availability_fotmob_team_id_fotmob_player_id_key UNIQUE (team_id, fotmob_player_id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'player_career_history_fotmob_player_id_team_fotmob_id_start_key' AND conrelid = 'public.player_career_history_fotmob'::regclass
  ) THEN
    ALTER TABLE public.player_career_history_fotmob ADD CONSTRAINT player_career_history_fotmob_player_id_team_fotmob_id_start_key UNIQUE (player_id, team_fotmob_id, start_date);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'player_trophies_fotmob_player_id_team_fotmob_id_league_fotm_key' AND conrelid = 'public.player_trophies_fotmob'::regclass
  ) THEN
    ALTER TABLE public.player_trophies_fotmob ADD CONSTRAINT player_trophies_fotmob_player_id_team_fotmob_id_league_fotm_key UNIQUE (player_id, team_fotmob_id, league_fotmob_id, season, result);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'team_coach_history_fotmob_team_id_coach_fotmob_id_season_fo_key' AND conrelid = 'public.team_coach_history_fotmob'::regclass
  ) THEN
    ALTER TABLE public.team_coach_history_fotmob ADD CONSTRAINT team_coach_history_fotmob_team_id_coach_fotmob_id_season_fo_key UNIQUE (team_id, coach_fotmob_id, season, fotmob_league_id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'team_elo_team_id_escopo_league_id_key' AND conrelid = 'public.team_elo'::regclass
  ) THEN
    ALTER TABLE public.team_elo ADD CONSTRAINT team_elo_team_id_escopo_league_id_key UNIQUE (team_id, escopo, league_id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'team_elo_external_fonte_clube_nome_externo_valido_de_key' AND conrelid = 'public.team_elo_external'::regclass
  ) THEN
    ALTER TABLE public.team_elo_external ADD CONSTRAINT team_elo_external_fonte_clube_nome_externo_valido_de_key UNIQUE (fonte, clube_nome_externo, valido_de);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'team_elo_history_team_id_escopo_match_id_key' AND conrelid = 'public.team_elo_history'::regclass
  ) THEN
    ALTER TABLE public.team_elo_history ADD CONSTRAINT team_elo_history_team_id_escopo_match_id_key UNIQUE (team_id, escopo, match_id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'team_source_ids_source_source_id_key' AND conrelid = 'public.team_source_ids'::regclass
  ) THEN
    ALTER TABLE public.team_source_ids ADD CONSTRAINT team_source_ids_source_source_id_key UNIQUE (source, source_id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'team_strengths_team_id_model_name_stat_key' AND conrelid = 'public.team_strengths'::regclass
  ) THEN
    ALTER TABLE public.team_strengths ADD CONSTRAINT team_strengths_team_id_model_name_stat_key UNIQUE (team_id, model_name, stat);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'team_transfers_fotmob_team_id_direction_fotmob_player_id_tr_key' AND conrelid = 'public.team_transfers_fotmob'::regclass
  ) THEN
    ALTER TABLE public.team_transfers_fotmob ADD CONSTRAINT team_transfers_fotmob_team_id_direction_fotmob_player_id_tr_key UNIQUE (team_id, direction, fotmob_player_id, transfer_date);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'equipe_liga_temporada_desfecho_check' AND conrelid = 'public.equipe_liga_temporada'::regclass
  ) THEN
    ALTER TABLE public.equipe_liga_temporada ADD CONSTRAINT equipe_liga_temporada_desfecho_check CHECK ((desfecho = ANY (ARRAY['campeao'::text, 'vice_campeao'::text, 'rebaixado'::text, 'promovido'::text, 'eliminado_fase_grupos'::text, 'eliminado_oitavas'::text, 'eliminado_quartas'::text, 'eliminado_semifinal'::text, 'eliminado_final'::text, 'em_andamento'::text])));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'equipes_categoria_check' AND conrelid = 'public.equipes'::regclass
  ) THEN
    ALTER TABLE public.equipes ADD CONSTRAINT equipes_categoria_check CHECK ((categoria = ANY (ARRAY['masculino_profissional'::text, 'feminino_profissional'::text, 'sub23'::text, 'sub21'::text, 'sub20'::text, 'sub17'::text, 'master'::text, 'futsal'::text])));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'equipes_tipo_check' AND conrelid = 'public.equipes'::regclass
  ) THEN
    ALTER TABLE public.equipes ADD CONSTRAINT equipes_tipo_check CHECK ((tipo = ANY (ARRAY['selecao'::text, 'clube'::text])));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'fases_formato_mata_mata_check' AND conrelid = 'public.fases'::regclass
  ) THEN
    ALTER TABLE public.fases ADD CONSTRAINT fases_formato_mata_mata_check CHECK ((formato_mata_mata = ANY (ARRAY['jogo_unico'::text, 'ida_e_volta'::text])));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'fases_tipo_check' AND conrelid = 'public.fases'::regclass
  ) THEN
    ALTER TABLE public.fases ADD CONSTRAINT fases_tipo_check CHECK ((tipo = ANY (ARRAY['liga'::text, 'grupos'::text, 'mata_mata'::text])));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'formato_so_em_mata_mata' AND conrelid = 'public.fases'::regclass
  ) THEN
    ALTER TABLE public.fases ADD CONSTRAINT formato_so_em_mata_mata CHECK ((((tipo = 'mata_mata'::text) AND (formato_mata_mata IS NOT NULL)) OR ((tipo <> 'mata_mata'::text) AND (formato_mata_mata IS NULL))));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'importacoes_status_check' AND conrelid = 'public.importacoes'::regclass
  ) THEN
    ALTER TABLE public.importacoes ADD CONSTRAINT importacoes_status_check CHECK ((status = ANY (ARRAY['sucesso'::text, 'parcial'::text, 'falha'::text])));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'instituicao_nomes_formalidade_check' AND conrelid = 'public.instituicao_nomes'::regclass
  ) THEN
    ALTER TABLE public.instituicao_nomes ADD CONSTRAINT instituicao_nomes_formalidade_check CHECK ((formalidade = ANY (ARRAY['completo'::text, 'popular'::text])));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'instituicao_nomes_tipo_check' AND conrelid = 'public.instituicao_nomes'::regclass
  ) THEN
    ALTER TABLE public.instituicao_nomes ADD CONSTRAINT instituicao_nomes_tipo_check CHECK ((tipo = ANY (ARRAY['original'::text, 'transliteracao'::text, 'localizado'::text])));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'instituicao_sucessoes_tipo_evento_check' AND conrelid = 'public.instituicao_sucessoes'::regclass
  ) THEN
    ALTER TABLE public.instituicao_sucessoes ADD CONSTRAINT instituicao_sucessoes_tipo_evento_check CHECK ((tipo_evento = ANY (ARRAY['renomeacao'::text, 'fragmentacao'::text, 'fusao'::text, 'dissolucao'::text])));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'ligas_tipo_check' AND conrelid = 'public.ligas'::regclass
  ) THEN
    ALTER TABLE public.ligas ADD CONSTRAINT ligas_tipo_check CHECK ((tipo = ANY (ARRAY['liga_domestica'::text, 'copa_nacional'::text, 'torneio_internacional'::text, 'copa_continental'::text])));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'model_calibration_method_check' AND conrelid = 'public.model_calibration'::regclass
  ) THEN
    ALTER TABLE public.model_calibration ADD CONSTRAINT model_calibration_method_check CHECK ((method = ANY (ARRAY['platt'::text, 'isotonic'::text])));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'model_predictions_probability_check' AND conrelid = 'public.model_predictions'::regclass
  ) THEN
    ALTER TABLE public.model_predictions ADD CONSTRAINT model_predictions_probability_check CHECK (((probability > (0)::numeric) AND (probability < (1)::numeric)));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'player_trophies_fotmob_result_check' AND conrelid = 'public.player_trophies_fotmob'::regclass
  ) THEN
    ALTER TABLE public.player_trophies_fotmob ADD CONSTRAINT player_trophies_fotmob_result_check CHECK ((result = ANY (ARRAY['won'::text, 'runner_up'::text])));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'team_elo_escopo_check' AND conrelid = 'public.team_elo'::regclass
  ) THEN
    ALTER TABLE public.team_elo ADD CONSTRAINT team_elo_escopo_check CHECK ((escopo = ANY (ARRAY['liga'::text, 'geral'::text, 'global'::text])));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'team_elo_history_escopo_check' AND conrelid = 'public.team_elo_history'::regclass
  ) THEN
    ALTER TABLE public.team_elo_history ADD CONSTRAINT team_elo_history_escopo_check CHECK ((escopo = ANY (ARRAY['liga'::text, 'geral'::text, 'global'::text])));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'team_transfers_fotmob_direction_check' AND conrelid = 'public.team_transfers_fotmob'::regclass
  ) THEN
    ALTER TABLE public.team_transfers_fotmob ADD CONSTRAINT team_transfers_fotmob_direction_check CHECK ((direction = ANY (ARRAY['in'::text, 'out'::text, 'contract_extension'::text])));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'carteira_simulada_match_id_fkey' AND conrelid = 'public.carteira_simulada'::regclass
  ) THEN
    ALTER TABLE public.carteira_simulada ADD CONSTRAINT carteira_simulada_match_id_fkey FOREIGN KEY (match_id) REFERENCES matches(id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'custom_model_ondemand_predictions_config_id_fkey' AND conrelid = 'public.custom_model_ondemand_predictions'::regclass
  ) THEN
    ALTER TABLE public.custom_model_ondemand_predictions ADD CONSTRAINT custom_model_ondemand_predictions_config_id_fkey FOREIGN KEY (config_id) REFERENCES custom_model_configs(id) ON DELETE CASCADE;
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'custom_model_ondemand_predictions_match_id_fkey' AND conrelid = 'public.custom_model_ondemand_predictions'::regclass
  ) THEN
    ALTER TABLE public.custom_model_ondemand_predictions ADD CONSTRAINT custom_model_ondemand_predictions_match_id_fkey FOREIGN KEY (match_id) REFERENCES matches(id) ON DELETE CASCADE;
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'detalhes_clube_equipe_id_fkey' AND conrelid = 'public.detalhes_clube'::regclass
  ) THEN
    ALTER TABLE public.detalhes_clube ADD CONSTRAINT detalhes_clube_equipe_id_fkey FOREIGN KEY (equipe_id) REFERENCES equipes(id) ON DELETE CASCADE;
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'detalhes_selecao_equipe_id_fkey' AND conrelid = 'public.detalhes_selecao'::regclass
  ) THEN
    ALTER TABLE public.detalhes_selecao ADD CONSTRAINT detalhes_selecao_equipe_id_fkey FOREIGN KEY (equipe_id) REFERENCES equipes(id) ON DELETE CASCADE;
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'equipe_liga_temporada_equipe_id_fkey' AND conrelid = 'public.equipe_liga_temporada'::regclass
  ) THEN
    ALTER TABLE public.equipe_liga_temporada ADD CONSTRAINT equipe_liga_temporada_equipe_id_fkey FOREIGN KEY (equipe_id) REFERENCES equipes(id) ON DELETE CASCADE;
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'equipe_liga_temporada_liga_temporada_id_fkey' AND conrelid = 'public.equipe_liga_temporada'::regclass
  ) THEN
    ALTER TABLE public.equipe_liga_temporada ADD CONSTRAINT equipe_liga_temporada_liga_temporada_id_fkey FOREIGN KEY (liga_temporada_id) REFERENCES ligas_temporadas(id) ON DELETE CASCADE;
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'equipes_importacao_id_fkey' AND conrelid = 'public.equipes'::regclass
  ) THEN
    ALTER TABLE public.equipes ADD CONSTRAINT equipes_importacao_id_fkey FOREIGN KEY (importacao_id) REFERENCES importacoes(id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'equipes_instituicao_id_fkey' AND conrelid = 'public.equipes'::regclass
  ) THEN
    ALTER TABLE public.equipes ADD CONSTRAINT equipes_instituicao_id_fkey FOREIGN KEY (instituicao_id) REFERENCES instituicoes(id) ON DELETE CASCADE;
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'equipes_pipeline_team_id_fkey' AND conrelid = 'public.equipes'::regclass
  ) THEN
    ALTER TABLE public.equipes ADD CONSTRAINT equipes_pipeline_team_id_fkey FOREIGN KEY (pipeline_team_id) REFERENCES teams(id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'eventos_equipe_mandante_id_fkey' AND conrelid = 'public.eventos'::regclass
  ) THEN
    ALTER TABLE public.eventos ADD CONSTRAINT eventos_equipe_mandante_id_fkey FOREIGN KEY (equipe_mandante_id) REFERENCES equipes(id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'eventos_equipe_visitante_id_fkey' AND conrelid = 'public.eventos'::regclass
  ) THEN
    ALTER TABLE public.eventos ADD CONSTRAINT eventos_equipe_visitante_id_fkey FOREIGN KEY (equipe_visitante_id) REFERENCES equipes(id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'eventos_fase_id_fkey' AND conrelid = 'public.eventos'::regclass
  ) THEN
    ALTER TABLE public.eventos ADD CONSTRAINT eventos_fase_id_fkey FOREIGN KEY (fase_id) REFERENCES fases(id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'fases_liga_temporada_id_fkey' AND conrelid = 'public.fases'::regclass
  ) THEN
    ALTER TABLE public.fases ADD CONSTRAINT fases_liga_temporada_id_fkey FOREIGN KEY (liga_temporada_id) REFERENCES ligas_temporadas(id) ON DELETE CASCADE;
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'instituicao_nomes_instituicao_id_fkey' AND conrelid = 'public.instituicao_nomes'::regclass
  ) THEN
    ALTER TABLE public.instituicao_nomes ADD CONSTRAINT instituicao_nomes_instituicao_id_fkey FOREIGN KEY (instituicao_id) REFERENCES instituicoes(id) ON DELETE CASCADE;
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'instituicao_simbolos_instituicao_id_fkey' AND conrelid = 'public.instituicao_simbolos'::regclass
  ) THEN
    ALTER TABLE public.instituicao_simbolos ADD CONSTRAINT instituicao_simbolos_instituicao_id_fkey FOREIGN KEY (instituicao_id) REFERENCES instituicoes(id) ON DELETE CASCADE;
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'instituicao_sucessoes_instituicao_anterior_id_fkey' AND conrelid = 'public.instituicao_sucessoes'::regclass
  ) THEN
    ALTER TABLE public.instituicao_sucessoes ADD CONSTRAINT instituicao_sucessoes_instituicao_anterior_id_fkey FOREIGN KEY (instituicao_anterior_id) REFERENCES instituicoes(id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'instituicao_sucessoes_instituicao_nova_id_fkey' AND conrelid = 'public.instituicao_sucessoes'::regclass
  ) THEN
    ALTER TABLE public.instituicao_sucessoes ADD CONSTRAINT instituicao_sucessoes_instituicao_nova_id_fkey FOREIGN KEY (instituicao_nova_id) REFERENCES instituicoes(id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'instituicoes_importacao_id_fkey' AND conrelid = 'public.instituicoes'::regclass
  ) THEN
    ALTER TABLE public.instituicoes ADD CONSTRAINT instituicoes_importacao_id_fkey FOREIGN KEY (importacao_id) REFERENCES importacoes(id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'league_model_params_league_id_fkey' AND conrelid = 'public.league_model_params'::regclass
  ) THEN
    ALTER TABLE public.league_model_params ADD CONSTRAINT league_model_params_league_id_fkey FOREIGN KEY (league_id) REFERENCES leagues(id) ON DELETE CASCADE;
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'liga_fonte_externa_league_id_fkey' AND conrelid = 'public.liga_fonte_externa'::regclass
  ) THEN
    ALTER TABLE public.liga_fonte_externa ADD CONSTRAINT liga_fonte_externa_league_id_fkey FOREIGN KEY (league_id) REFERENCES leagues(id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'liga_oddspapi_tournament_league_id_fkey' AND conrelid = 'public.liga_oddspapi_tournament'::regclass
  ) THEN
    ALTER TABLE public.liga_oddspapi_tournament ADD CONSTRAINT liga_oddspapi_tournament_league_id_fkey FOREIGN KEY (league_id) REFERENCES leagues(id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'ligas_importacao_id_fkey' AND conrelid = 'public.ligas'::regclass
  ) THEN
    ALTER TABLE public.ligas ADD CONSTRAINT ligas_importacao_id_fkey FOREIGN KEY (importacao_id) REFERENCES importacoes(id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'ligas_pipeline_league_id_fkey' AND conrelid = 'public.ligas'::regclass
  ) THEN
    ALTER TABLE public.ligas ADD CONSTRAINT ligas_pipeline_league_id_fkey FOREIGN KEY (pipeline_league_id) REFERENCES leagues(id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'ligas_temporadas_liga_id_fkey' AND conrelid = 'public.ligas_temporadas'::regclass
  ) THEN
    ALTER TABLE public.ligas_temporadas ADD CONSTRAINT ligas_temporadas_liga_id_fkey FOREIGN KEY (liga_id) REFERENCES ligas(id) ON DELETE CASCADE;
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'match_context_fotmob_match_id_fkey' AND conrelid = 'public.match_context_fotmob'::regclass
  ) THEN
    ALTER TABLE public.match_context_fotmob ADD CONSTRAINT match_context_fotmob_match_id_fkey FOREIGN KEY (match_id) REFERENCES matches(id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'match_features_contexto_match_id_fkey' AND conrelid = 'public.match_features_contexto'::regclass
  ) THEN
    ALTER TABLE public.match_features_contexto ADD CONSTRAINT match_features_contexto_match_id_fkey FOREIGN KEY (match_id) REFERENCES matches(id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'match_features_contexto_team_id_fkey' AND conrelid = 'public.match_features_contexto'::regclass
  ) THEN
    ALTER TABLE public.match_features_contexto ADD CONSTRAINT match_features_contexto_team_id_fkey FOREIGN KEY (team_id) REFERENCES teams(id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'match_player_stats_fotmob_match_id_fkey' AND conrelid = 'public.match_player_stats_fotmob'::regclass
  ) THEN
    ALTER TABLE public.match_player_stats_fotmob ADD CONSTRAINT match_player_stats_fotmob_match_id_fkey FOREIGN KEY (match_id) REFERENCES matches(id) ON DELETE CASCADE;
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'match_player_stats_fotmob_player_id_fkey' AND conrelid = 'public.match_player_stats_fotmob'::regclass
  ) THEN
    ALTER TABLE public.match_player_stats_fotmob ADD CONSTRAINT match_player_stats_fotmob_player_id_fkey FOREIGN KEY (player_id) REFERENCES players(id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'match_player_stats_fotmob_team_id_fkey' AND conrelid = 'public.match_player_stats_fotmob'::regclass
  ) THEN
    ALTER TABLE public.match_player_stats_fotmob ADD CONSTRAINT match_player_stats_fotmob_team_id_fkey FOREIGN KEY (team_id) REFERENCES teams(id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'match_shots_fotmob_match_id_fkey' AND conrelid = 'public.match_shots_fotmob'::regclass
  ) THEN
    ALTER TABLE public.match_shots_fotmob ADD CONSTRAINT match_shots_fotmob_match_id_fkey FOREIGN KEY (match_id) REFERENCES matches(id) ON DELETE CASCADE;
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'match_shots_fotmob_player_id_fkey' AND conrelid = 'public.match_shots_fotmob'::regclass
  ) THEN
    ALTER TABLE public.match_shots_fotmob ADD CONSTRAINT match_shots_fotmob_player_id_fkey FOREIGN KEY (player_id) REFERENCES players(id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'match_shots_fotmob_team_id_fkey' AND conrelid = 'public.match_shots_fotmob'::regclass
  ) THEN
    ALTER TABLE public.match_shots_fotmob ADD CONSTRAINT match_shots_fotmob_team_id_fkey FOREIGN KEY (team_id) REFERENCES teams(id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'match_source_ids_match_id_fkey' AND conrelid = 'public.match_source_ids'::regclass
  ) THEN
    ALTER TABLE public.match_source_ids ADD CONSTRAINT match_source_ids_match_id_fkey FOREIGN KEY (match_id) REFERENCES matches(id) ON DELETE CASCADE;
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'match_stats_match_id_fkey' AND conrelid = 'public.match_stats'::regclass
  ) THEN
    ALTER TABLE public.match_stats ADD CONSTRAINT match_stats_match_id_fkey FOREIGN KEY (match_id) REFERENCES matches(id) ON DELETE CASCADE;
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'match_stats_team_id_fkey' AND conrelid = 'public.match_stats'::regclass
  ) THEN
    ALTER TABLE public.match_stats ADD CONSTRAINT match_stats_team_id_fkey FOREIGN KEY (team_id) REFERENCES teams(id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'match_stats_fotmob_match_id_fkey' AND conrelid = 'public.match_stats_fotmob'::regclass
  ) THEN
    ALTER TABLE public.match_stats_fotmob ADD CONSTRAINT match_stats_fotmob_match_id_fkey FOREIGN KEY (match_id) REFERENCES matches(id) ON DELETE CASCADE;
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'match_stats_fotmob_team_id_fkey' AND conrelid = 'public.match_stats_fotmob'::regclass
  ) THEN
    ALTER TABLE public.match_stats_fotmob ADD CONSTRAINT match_stats_fotmob_team_id_fkey FOREIGN KEY (team_id) REFERENCES teams(id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'mercados_avaliados_evento_id_fkey' AND conrelid = 'public.mercados_avaliados'::regclass
  ) THEN
    ALTER TABLE public.mercados_avaliados ADD CONSTRAINT mercados_avaliados_evento_id_fkey FOREIGN KEY (evento_id) REFERENCES eventos(id) ON DELETE CASCADE;
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'model_predictions_match_id_fkey' AND conrelid = 'public.model_predictions'::regclass
  ) THEN
    ALTER TABLE public.model_predictions ADD CONSTRAINT model_predictions_match_id_fkey FOREIGN KEY (match_id) REFERENCES matches(id) ON DELETE CASCADE;
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'model_stat_estimates_match_id_fkey' AND conrelid = 'public.model_stat_estimates'::regclass
  ) THEN
    ALTER TABLE public.model_stat_estimates ADD CONSTRAINT model_stat_estimates_match_id_fkey FOREIGN KEY (match_id) REFERENCES matches(id) ON DELETE CASCADE;
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'odds_market_match_id_fkey' AND conrelid = 'public.odds_market'::regclass
  ) THEN
    ALTER TABLE public.odds_market ADD CONSTRAINT odds_market_match_id_fkey FOREIGN KEY (match_id) REFERENCES matches(id) ON DELETE CASCADE;
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'player_availability_fotmob_player_id_fkey' AND conrelid = 'public.player_availability_fotmob'::regclass
  ) THEN
    ALTER TABLE public.player_availability_fotmob ADD CONSTRAINT player_availability_fotmob_player_id_fkey FOREIGN KEY (player_id) REFERENCES players(id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'player_availability_fotmob_team_id_fkey' AND conrelid = 'public.player_availability_fotmob'::regclass
  ) THEN
    ALTER TABLE public.player_availability_fotmob ADD CONSTRAINT player_availability_fotmob_team_id_fkey FOREIGN KEY (team_id) REFERENCES teams(id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'player_career_history_fotmob_player_id_fkey' AND conrelid = 'public.player_career_history_fotmob'::regclass
  ) THEN
    ALTER TABLE public.player_career_history_fotmob ADD CONSTRAINT player_career_history_fotmob_player_id_fkey FOREIGN KEY (player_id) REFERENCES players(id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'player_details_fotmob_player_id_fkey' AND conrelid = 'public.player_details_fotmob'::regclass
  ) THEN
    ALTER TABLE public.player_details_fotmob ADD CONSTRAINT player_details_fotmob_player_id_fkey FOREIGN KEY (player_id) REFERENCES players(id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'player_ratings_player_id_fkey' AND conrelid = 'public.player_ratings'::regclass
  ) THEN
    ALTER TABLE public.player_ratings ADD CONSTRAINT player_ratings_player_id_fkey FOREIGN KEY (player_id) REFERENCES players(id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'player_trophies_fotmob_player_id_fkey' AND conrelid = 'public.player_trophies_fotmob'::regclass
  ) THEN
    ALTER TABLE public.player_trophies_fotmob ADD CONSTRAINT player_trophies_fotmob_player_id_fkey FOREIGN KEY (player_id) REFERENCES players(id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'team_coach_history_fotmob_team_id_fkey' AND conrelid = 'public.team_coach_history_fotmob'::regclass
  ) THEN
    ALTER TABLE public.team_coach_history_fotmob ADD CONSTRAINT team_coach_history_fotmob_team_id_fkey FOREIGN KEY (team_id) REFERENCES teams(id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'team_elo_league_id_fkey' AND conrelid = 'public.team_elo'::regclass
  ) THEN
    ALTER TABLE public.team_elo ADD CONSTRAINT team_elo_league_id_fkey FOREIGN KEY (league_id) REFERENCES leagues(id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'team_elo_team_id_fkey' AND conrelid = 'public.team_elo'::regclass
  ) THEN
    ALTER TABLE public.team_elo ADD CONSTRAINT team_elo_team_id_fkey FOREIGN KEY (team_id) REFERENCES teams(id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'team_elo_external_team_id_fkey' AND conrelid = 'public.team_elo_external'::regclass
  ) THEN
    ALTER TABLE public.team_elo_external ADD CONSTRAINT team_elo_external_team_id_fkey FOREIGN KEY (team_id) REFERENCES teams(id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'team_elo_history_league_id_fkey' AND conrelid = 'public.team_elo_history'::regclass
  ) THEN
    ALTER TABLE public.team_elo_history ADD CONSTRAINT team_elo_history_league_id_fkey FOREIGN KEY (league_id) REFERENCES leagues(id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'team_elo_history_match_id_fkey' AND conrelid = 'public.team_elo_history'::regclass
  ) THEN
    ALTER TABLE public.team_elo_history ADD CONSTRAINT team_elo_history_match_id_fkey FOREIGN KEY (match_id) REFERENCES matches(id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'team_elo_history_team_id_fkey' AND conrelid = 'public.team_elo_history'::regclass
  ) THEN
    ALTER TABLE public.team_elo_history ADD CONSTRAINT team_elo_history_team_id_fkey FOREIGN KEY (team_id) REFERENCES teams(id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'team_source_ids_team_id_fkey' AND conrelid = 'public.team_source_ids'::regclass
  ) THEN
    ALTER TABLE public.team_source_ids ADD CONSTRAINT team_source_ids_team_id_fkey FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE;
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'team_strengths_league_id_fkey' AND conrelid = 'public.team_strengths'::regclass
  ) THEN
    ALTER TABLE public.team_strengths ADD CONSTRAINT team_strengths_league_id_fkey FOREIGN KEY (league_id) REFERENCES leagues(id) ON DELETE CASCADE;
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'team_strengths_team_id_fkey' AND conrelid = 'public.team_strengths'::regclass
  ) THEN
    ALTER TABLE public.team_strengths ADD CONSTRAINT team_strengths_team_id_fkey FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE;
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'team_transfers_fotmob_player_id_fkey' AND conrelid = 'public.team_transfers_fotmob'::regclass
  ) THEN
    ALTER TABLE public.team_transfers_fotmob ADD CONSTRAINT team_transfers_fotmob_player_id_fkey FOREIGN KEY (player_id) REFERENCES players(id);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'team_transfers_fotmob_team_id_fkey' AND conrelid = 'public.team_transfers_fotmob'::regclass
  ) THEN
    ALTER TABLE public.team_transfers_fotmob ADD CONSTRAINT team_transfers_fotmob_team_id_fkey FOREIGN KEY (team_id) REFERENCES teams(id);
  END IF;
END $$;

-- ================= ÍNDICES (além dos que já vêm das constraints acima) =================

CREATE INDEX IF NOT EXISTS idx_carteira_simulada_match_id ON public.carteira_simulada USING btree (match_id);
CREATE INDEX IF NOT EXISTS idx_custom_ondemand_config ON public.custom_model_ondemand_predictions USING btree (config_id);
CREATE INDEX IF NOT EXISTS idx_custom_ondemand_match ON public.custom_model_ondemand_predictions USING btree (match_id);
CREATE INDEX IF NOT EXISTS idx_equipe_liga_temporada_equipe ON public.equipe_liga_temporada USING btree (equipe_id);
CREATE INDEX IF NOT EXISTS idx_equipe_liga_temporada_temporada ON public.equipe_liga_temporada USING btree (liga_temporada_id);
CREATE INDEX IF NOT EXISTS idx_equipes_instituicao ON public.equipes USING btree (instituicao_id);
CREATE INDEX IF NOT EXISTS idx_equipes_pipeline_team_id ON public.equipes USING btree (pipeline_team_id);
CREATE INDEX IF NOT EXISTS idx_eventos_fase ON public.eventos USING btree (fase_id);
CREATE INDEX IF NOT EXISTS idx_eventos_mandante ON public.eventos USING btree (equipe_mandante_id);
CREATE INDEX IF NOT EXISTS idx_eventos_visitante ON public.eventos USING btree (equipe_visitante_id);
CREATE INDEX IF NOT EXISTS idx_fases_liga_temporada ON public.fases USING btree (liga_temporada_id);
CREATE INDEX IF NOT EXISTS idx_instituicao_nomes_instituicao ON public.instituicao_nomes USING btree (instituicao_id);
CREATE INDEX IF NOT EXISTS idx_instituicao_simbolos_instituicao ON public.instituicao_simbolos USING btree (instituicao_id);
CREATE INDEX IF NOT EXISTS idx_instituicao_sucessoes_anterior ON public.instituicao_sucessoes USING btree (instituicao_anterior_id);
CREATE INDEX IF NOT EXISTS idx_instituicao_sucessoes_nova ON public.instituicao_sucessoes USING btree (instituicao_nova_id);
CREATE INDEX IF NOT EXISTS idx_ligas_temporadas_liga ON public.ligas_temporadas USING btree (liga_id);
CREATE INDEX IF NOT EXISTS idx_match_player_stats_fotmob_player_id ON public.match_player_stats_fotmob USING btree (player_id);
CREATE INDEX IF NOT EXISTS idx_match_shots_fotmob_match_id ON public.match_shots_fotmob USING btree (match_id);
CREATE INDEX IF NOT EXISTS idx_match_shots_fotmob_player_id ON public.match_shots_fotmob USING btree (player_id);
CREATE INDEX IF NOT EXISTS idx_stat_estimates_match ON public.model_stat_estimates USING btree (match_id);
CREATE INDEX IF NOT EXISTS idx_odds_match_market ON public.odds_market USING btree (match_id, market);
CREATE INDEX IF NOT EXISTS team_elo_team_idx ON public.team_elo USING btree (team_id);
CREATE INDEX IF NOT EXISTS team_elo_external_team_id_idx ON public.team_elo_external USING btree (team_id);
CREATE INDEX IF NOT EXISTS idx_team_elo_history_match_id ON public.team_elo_history USING btree (match_id);
CREATE INDEX IF NOT EXISTS team_elo_history_team_idx ON public.team_elo_history USING btree (team_id, escopo, match_date);

-- ================= ROW LEVEL SECURITY =================

ALTER TABLE public.api_football_predictions_cache ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.assinaturas_api ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.carteira_simulada ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.config_carteira ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.custom_model_ondemand_predictions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.detalhes_clube ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.detalhes_selecao ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.equipe_liga_temporada ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.equipes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.eventos ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.fases ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.importacoes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.instituicao_nomes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.instituicao_simbolos ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.instituicao_sucessoes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.instituicoes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.league_model_params ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.liga_fonte_externa ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.liga_oddspapi_tournament ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ligas ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ligas_temporadas ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.match_context_fotmob ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.match_features_contexto ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.match_odds ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.match_player_stats_fotmob ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.match_shots_fotmob ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.match_source_ids ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.match_stats ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.match_stats_fotmob ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.mercados_avaliados ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.model_calibration ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.model_config ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.model_predictions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.model_stat_estimates ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.notas ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.odds_market ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.oddspapi_cache ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.player_availability_fotmob ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.player_career_history_fotmob ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.player_details_fotmob ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.player_ratings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.player_trophies_fotmob ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.predictions_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.simulacoes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.team_coach_history_fotmob ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.team_elo ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.team_elo_external ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.team_elo_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.team_source_ids ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.team_stats ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.team_strengths ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.team_transfers_fotmob ENABLE ROW LEVEL SECURITY;

-- ================= POLÍTICAS =================

DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'api_football_predictions_cache' AND policyname = 'api_football_predictions_cache_public_select'
  ) THEN
    CREATE POLICY "api_football_predictions_cache_public_select" ON public.api_football_predictions_cache FOR SELECT TO public USING (true);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'assinaturas_api' AND policyname = 'atualizacao autenticada assinaturas_api'
  ) THEN
    CREATE POLICY "atualizacao autenticada assinaturas_api" ON public.assinaturas_api FOR UPDATE TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'assinaturas_api' AND policyname = 'delecao autenticada assinaturas_api'
  ) THEN
    CREATE POLICY "delecao autenticada assinaturas_api" ON public.assinaturas_api FOR DELETE TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'assinaturas_api' AND policyname = 'escrita autenticada assinaturas_api'
  ) THEN
    CREATE POLICY "escrita autenticada assinaturas_api" ON public.assinaturas_api FOR INSERT TO public WITH CHECK ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'assinaturas_api' AND policyname = 'leitura autenticada assinaturas_api'
  ) THEN
    CREATE POLICY "leitura autenticada assinaturas_api" ON public.assinaturas_api FOR SELECT TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'carteira_simulada' AND policyname = 'Permitir tudo em carteira_simulada'
  ) THEN
    CREATE POLICY "Permitir tudo em carteira_simulada" ON public.carteira_simulada FOR ALL TO public USING (true) WITH CHECK (true);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'config_carteira' AND policyname = 'Permitir tudo em config_carteira'
  ) THEN
    CREATE POLICY "Permitir tudo em config_carteira" ON public.config_carteira FOR ALL TO public USING (true) WITH CHECK (true);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'custom_model_ondemand_predictions' AND policyname = 'Leitura pública de estimativas sob demanda'
  ) THEN
    CREATE POLICY "Leitura pública de estimativas sob demanda" ON public.custom_model_ondemand_predictions FOR SELECT TO public USING (true);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'detalhes_clube' AND policyname = 'atualizacao autenticada detalhes_clube'
  ) THEN
    CREATE POLICY "atualizacao autenticada detalhes_clube" ON public.detalhes_clube FOR UPDATE TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'detalhes_clube' AND policyname = 'delecao autenticada detalhes_clube'
  ) THEN
    CREATE POLICY "delecao autenticada detalhes_clube" ON public.detalhes_clube FOR DELETE TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'detalhes_clube' AND policyname = 'escrita autenticada detalhes_clube'
  ) THEN
    CREATE POLICY "escrita autenticada detalhes_clube" ON public.detalhes_clube FOR INSERT TO public WITH CHECK ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'detalhes_clube' AND policyname = 'leitura autenticada detalhes_clube'
  ) THEN
    CREATE POLICY "leitura autenticada detalhes_clube" ON public.detalhes_clube FOR SELECT TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'detalhes_selecao' AND policyname = 'atualizacao autenticada detalhes_selecao'
  ) THEN
    CREATE POLICY "atualizacao autenticada detalhes_selecao" ON public.detalhes_selecao FOR UPDATE TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'detalhes_selecao' AND policyname = 'delecao autenticada detalhes_selecao'
  ) THEN
    CREATE POLICY "delecao autenticada detalhes_selecao" ON public.detalhes_selecao FOR DELETE TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'detalhes_selecao' AND policyname = 'escrita autenticada detalhes_selecao'
  ) THEN
    CREATE POLICY "escrita autenticada detalhes_selecao" ON public.detalhes_selecao FOR INSERT TO public WITH CHECK ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'detalhes_selecao' AND policyname = 'leitura autenticada detalhes_selecao'
  ) THEN
    CREATE POLICY "leitura autenticada detalhes_selecao" ON public.detalhes_selecao FOR SELECT TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'equipe_liga_temporada' AND policyname = 'atualizacao autenticada equipe_liga_temporada'
  ) THEN
    CREATE POLICY "atualizacao autenticada equipe_liga_temporada" ON public.equipe_liga_temporada FOR UPDATE TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'equipe_liga_temporada' AND policyname = 'delecao autenticada equipe_liga_temporada'
  ) THEN
    CREATE POLICY "delecao autenticada equipe_liga_temporada" ON public.equipe_liga_temporada FOR DELETE TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'equipe_liga_temporada' AND policyname = 'escrita autenticada equipe_liga_temporada'
  ) THEN
    CREATE POLICY "escrita autenticada equipe_liga_temporada" ON public.equipe_liga_temporada FOR INSERT TO public WITH CHECK ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'equipe_liga_temporada' AND policyname = 'leitura autenticada equipe_liga_temporada'
  ) THEN
    CREATE POLICY "leitura autenticada equipe_liga_temporada" ON public.equipe_liga_temporada FOR SELECT TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'equipes' AND policyname = 'atualizacao autenticada equipes'
  ) THEN
    CREATE POLICY "atualizacao autenticada equipes" ON public.equipes FOR UPDATE TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'equipes' AND policyname = 'delecao autenticada equipes'
  ) THEN
    CREATE POLICY "delecao autenticada equipes" ON public.equipes FOR DELETE TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'equipes' AND policyname = 'escrita autenticada equipes'
  ) THEN
    CREATE POLICY "escrita autenticada equipes" ON public.equipes FOR INSERT TO public WITH CHECK ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'equipes' AND policyname = 'leitura autenticada equipes'
  ) THEN
    CREATE POLICY "leitura autenticada equipes" ON public.equipes FOR SELECT TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'eventos' AND policyname = 'atualizacao autenticada eventos'
  ) THEN
    CREATE POLICY "atualizacao autenticada eventos" ON public.eventos FOR UPDATE TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'eventos' AND policyname = 'escrita autenticada eventos'
  ) THEN
    CREATE POLICY "escrita autenticada eventos" ON public.eventos FOR INSERT TO public WITH CHECK ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'eventos' AND policyname = 'leitura autenticada eventos'
  ) THEN
    CREATE POLICY "leitura autenticada eventos" ON public.eventos FOR SELECT TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'fases' AND policyname = 'atualizacao autenticada fases'
  ) THEN
    CREATE POLICY "atualizacao autenticada fases" ON public.fases FOR UPDATE TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'fases' AND policyname = 'delecao autenticada fases'
  ) THEN
    CREATE POLICY "delecao autenticada fases" ON public.fases FOR DELETE TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'fases' AND policyname = 'escrita autenticada fases'
  ) THEN
    CREATE POLICY "escrita autenticada fases" ON public.fases FOR INSERT TO public WITH CHECK ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'fases' AND policyname = 'leitura autenticada fases'
  ) THEN
    CREATE POLICY "leitura autenticada fases" ON public.fases FOR SELECT TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'importacoes' AND policyname = 'atualizacao autenticada importacoes'
  ) THEN
    CREATE POLICY "atualizacao autenticada importacoes" ON public.importacoes FOR UPDATE TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'importacoes' AND policyname = 'delecao autenticada importacoes'
  ) THEN
    CREATE POLICY "delecao autenticada importacoes" ON public.importacoes FOR DELETE TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'importacoes' AND policyname = 'escrita autenticada importacoes'
  ) THEN
    CREATE POLICY "escrita autenticada importacoes" ON public.importacoes FOR INSERT TO public WITH CHECK ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'importacoes' AND policyname = 'leitura autenticada importacoes'
  ) THEN
    CREATE POLICY "leitura autenticada importacoes" ON public.importacoes FOR SELECT TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'instituicao_nomes' AND policyname = 'atualizacao autenticada instituicao_nomes'
  ) THEN
    CREATE POLICY "atualizacao autenticada instituicao_nomes" ON public.instituicao_nomes FOR UPDATE TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'instituicao_nomes' AND policyname = 'delecao autenticada instituicao_nomes'
  ) THEN
    CREATE POLICY "delecao autenticada instituicao_nomes" ON public.instituicao_nomes FOR DELETE TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'instituicao_nomes' AND policyname = 'escrita autenticada instituicao_nomes'
  ) THEN
    CREATE POLICY "escrita autenticada instituicao_nomes" ON public.instituicao_nomes FOR INSERT TO public WITH CHECK ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'instituicao_nomes' AND policyname = 'leitura autenticada instituicao_nomes'
  ) THEN
    CREATE POLICY "leitura autenticada instituicao_nomes" ON public.instituicao_nomes FOR SELECT TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'instituicao_simbolos' AND policyname = 'atualizacao autenticada instituicao_simbolos'
  ) THEN
    CREATE POLICY "atualizacao autenticada instituicao_simbolos" ON public.instituicao_simbolos FOR UPDATE TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'instituicao_simbolos' AND policyname = 'delecao autenticada instituicao_simbolos'
  ) THEN
    CREATE POLICY "delecao autenticada instituicao_simbolos" ON public.instituicao_simbolos FOR DELETE TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'instituicao_simbolos' AND policyname = 'escrita autenticada instituicao_simbolos'
  ) THEN
    CREATE POLICY "escrita autenticada instituicao_simbolos" ON public.instituicao_simbolos FOR INSERT TO public WITH CHECK ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'instituicao_simbolos' AND policyname = 'leitura autenticada instituicao_simbolos'
  ) THEN
    CREATE POLICY "leitura autenticada instituicao_simbolos" ON public.instituicao_simbolos FOR SELECT TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'instituicao_sucessoes' AND policyname = 'atualizacao autenticada instituicao_sucessoes'
  ) THEN
    CREATE POLICY "atualizacao autenticada instituicao_sucessoes" ON public.instituicao_sucessoes FOR UPDATE TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'instituicao_sucessoes' AND policyname = 'delecao autenticada instituicao_sucessoes'
  ) THEN
    CREATE POLICY "delecao autenticada instituicao_sucessoes" ON public.instituicao_sucessoes FOR DELETE TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'instituicao_sucessoes' AND policyname = 'escrita autenticada instituicao_sucessoes'
  ) THEN
    CREATE POLICY "escrita autenticada instituicao_sucessoes" ON public.instituicao_sucessoes FOR INSERT TO public WITH CHECK ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'instituicao_sucessoes' AND policyname = 'leitura autenticada instituicao_sucessoes'
  ) THEN
    CREATE POLICY "leitura autenticada instituicao_sucessoes" ON public.instituicao_sucessoes FOR SELECT TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'instituicoes' AND policyname = 'atualizacao autenticada instituicoes'
  ) THEN
    CREATE POLICY "atualizacao autenticada instituicoes" ON public.instituicoes FOR UPDATE TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'instituicoes' AND policyname = 'delecao autenticada instituicoes'
  ) THEN
    CREATE POLICY "delecao autenticada instituicoes" ON public.instituicoes FOR DELETE TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'instituicoes' AND policyname = 'escrita autenticada instituicoes'
  ) THEN
    CREATE POLICY "escrita autenticada instituicoes" ON public.instituicoes FOR INSERT TO public WITH CHECK ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'instituicoes' AND policyname = 'leitura autenticada instituicoes'
  ) THEN
    CREATE POLICY "leitura autenticada instituicoes" ON public.instituicoes FOR SELECT TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'league_model_params' AND policyname = 'leitura publica league_model_params'
  ) THEN
    CREATE POLICY "leitura publica league_model_params" ON public.league_model_params FOR SELECT TO public USING (true);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'liga_fonte_externa' AND policyname = 'liga_fonte_externa_public_select'
  ) THEN
    CREATE POLICY "liga_fonte_externa_public_select" ON public.liga_fonte_externa FOR SELECT TO anon, authenticated USING (true);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'liga_oddspapi_tournament' AND policyname = 'liga_oddspapi_tournament_public_select'
  ) THEN
    CREATE POLICY "liga_oddspapi_tournament_public_select" ON public.liga_oddspapi_tournament FOR SELECT TO public USING (true);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'ligas' AND policyname = 'atualizacao autenticada ligas'
  ) THEN
    CREATE POLICY "atualizacao autenticada ligas" ON public.ligas FOR UPDATE TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'ligas' AND policyname = 'delecao autenticada ligas'
  ) THEN
    CREATE POLICY "delecao autenticada ligas" ON public.ligas FOR DELETE TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'ligas' AND policyname = 'escrita autenticada ligas'
  ) THEN
    CREATE POLICY "escrita autenticada ligas" ON public.ligas FOR INSERT TO public WITH CHECK ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'ligas' AND policyname = 'leitura autenticada ligas'
  ) THEN
    CREATE POLICY "leitura autenticada ligas" ON public.ligas FOR SELECT TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'ligas_temporadas' AND policyname = 'atualizacao autenticada ligas_temporadas'
  ) THEN
    CREATE POLICY "atualizacao autenticada ligas_temporadas" ON public.ligas_temporadas FOR UPDATE TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'ligas_temporadas' AND policyname = 'delecao autenticada ligas_temporadas'
  ) THEN
    CREATE POLICY "delecao autenticada ligas_temporadas" ON public.ligas_temporadas FOR DELETE TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'ligas_temporadas' AND policyname = 'escrita autenticada ligas_temporadas'
  ) THEN
    CREATE POLICY "escrita autenticada ligas_temporadas" ON public.ligas_temporadas FOR INSERT TO public WITH CHECK ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'ligas_temporadas' AND policyname = 'leitura autenticada ligas_temporadas'
  ) THEN
    CREATE POLICY "leitura autenticada ligas_temporadas" ON public.ligas_temporadas FOR SELECT TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'match_context_fotmob' AND policyname = 'leitura publica match_context_fotmob'
  ) THEN
    CREATE POLICY "leitura publica match_context_fotmob" ON public.match_context_fotmob FOR SELECT TO public USING (true);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'match_features_contexto' AND policyname = 'leitura publica match_features_contexto'
  ) THEN
    CREATE POLICY "leitura publica match_features_contexto" ON public.match_features_contexto FOR SELECT TO public USING (true);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'match_odds' AND policyname = 'atualizacao autenticada match_odds'
  ) THEN
    CREATE POLICY "atualizacao autenticada match_odds" ON public.match_odds FOR UPDATE TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'match_odds' AND policyname = 'escrita autenticada match_odds'
  ) THEN
    CREATE POLICY "escrita autenticada match_odds" ON public.match_odds FOR INSERT TO public WITH CHECK ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'match_odds' AND policyname = 'leitura autenticada match_odds'
  ) THEN
    CREATE POLICY "leitura autenticada match_odds" ON public.match_odds FOR SELECT TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'match_player_stats_fotmob' AND policyname = 'Public read access'
  ) THEN
    CREATE POLICY "Public read access" ON public.match_player_stats_fotmob FOR SELECT TO public USING (true);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'match_shots_fotmob' AND policyname = 'Public read access'
  ) THEN
    CREATE POLICY "Public read access" ON public.match_shots_fotmob FOR SELECT TO public USING (true);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'match_source_ids' AND policyname = 'Public read access'
  ) THEN
    CREATE POLICY "Public read access" ON public.match_source_ids FOR SELECT TO public USING (true);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'match_stats' AND policyname = 'leitura publica match_stats'
  ) THEN
    CREATE POLICY "leitura publica match_stats" ON public.match_stats FOR SELECT TO public USING (true);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'match_stats_fotmob' AND policyname = 'Public read access'
  ) THEN
    CREATE POLICY "Public read access" ON public.match_stats_fotmob FOR SELECT TO public USING (true);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'mercados_avaliados' AND policyname = 'atualizacao autenticada mercados_avaliados'
  ) THEN
    CREATE POLICY "atualizacao autenticada mercados_avaliados" ON public.mercados_avaliados FOR UPDATE TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'mercados_avaliados' AND policyname = 'escrita autenticada mercados_avaliados'
  ) THEN
    CREATE POLICY "escrita autenticada mercados_avaliados" ON public.mercados_avaliados FOR INSERT TO public WITH CHECK ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'mercados_avaliados' AND policyname = 'leitura autenticada mercados_avaliados'
  ) THEN
    CREATE POLICY "leitura autenticada mercados_avaliados" ON public.mercados_avaliados FOR SELECT TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'model_calibration' AND policyname = 'leitura publica model_calibration'
  ) THEN
    CREATE POLICY "leitura publica model_calibration" ON public.model_calibration FOR SELECT TO public USING (true);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'model_config' AND policyname = 'leitura publica model_config'
  ) THEN
    CREATE POLICY "leitura publica model_config" ON public.model_config FOR SELECT TO public USING (true);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'model_predictions' AND policyname = 'leitura publica model_predictions'
  ) THEN
    CREATE POLICY "leitura publica model_predictions" ON public.model_predictions FOR SELECT TO public USING (true);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'model_stat_estimates' AND policyname = 'leitura publica model_stat_estimates'
  ) THEN
    CREATE POLICY "leitura publica model_stat_estimates" ON public.model_stat_estimates FOR SELECT TO public USING (true);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'notas' AND policyname = 'escrita autenticada notas'
  ) THEN
    CREATE POLICY "escrita autenticada notas" ON public.notas FOR INSERT TO public WITH CHECK ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'notas' AND policyname = 'exclusao autenticada notas'
  ) THEN
    CREATE POLICY "exclusao autenticada notas" ON public.notas FOR DELETE TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'notas' AND policyname = 'leitura autenticada notas'
  ) THEN
    CREATE POLICY "leitura autenticada notas" ON public.notas FOR SELECT TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'odds_market' AND policyname = 'leitura publica odds_market'
  ) THEN
    CREATE POLICY "leitura publica odds_market" ON public.odds_market FOR SELECT TO public USING (true);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'oddspapi_cache' AND policyname = 'oddspapi_cache_public_select'
  ) THEN
    CREATE POLICY "oddspapi_cache_public_select" ON public.oddspapi_cache FOR SELECT TO public USING (true);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'player_availability_fotmob' AND policyname = 'leitura publica player_availability_fotmob'
  ) THEN
    CREATE POLICY "leitura publica player_availability_fotmob" ON public.player_availability_fotmob FOR SELECT TO public USING (true);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'player_career_history_fotmob' AND policyname = 'leitura publica player_career_history_fotmob'
  ) THEN
    CREATE POLICY "leitura publica player_career_history_fotmob" ON public.player_career_history_fotmob FOR SELECT TO public USING (true);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'player_details_fotmob' AND policyname = 'leitura publica player_details_fotmob'
  ) THEN
    CREATE POLICY "leitura publica player_details_fotmob" ON public.player_details_fotmob FOR SELECT TO public USING (true);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'player_ratings' AND policyname = 'leitura publica player_ratings'
  ) THEN
    CREATE POLICY "leitura publica player_ratings" ON public.player_ratings FOR SELECT TO public USING (true);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'player_trophies_fotmob' AND policyname = 'leitura publica player_trophies_fotmob'
  ) THEN
    CREATE POLICY "leitura publica player_trophies_fotmob" ON public.player_trophies_fotmob FOR SELECT TO public USING (true);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'predictions_log' AND policyname = 'atualizacao autenticada predictions_log'
  ) THEN
    CREATE POLICY "atualizacao autenticada predictions_log" ON public.predictions_log FOR UPDATE TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'predictions_log' AND policyname = 'escrita autenticada predictions_log'
  ) THEN
    CREATE POLICY "escrita autenticada predictions_log" ON public.predictions_log FOR INSERT TO public WITH CHECK ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'predictions_log' AND policyname = 'leitura autenticada predictions_log'
  ) THEN
    CREATE POLICY "leitura autenticada predictions_log" ON public.predictions_log FOR SELECT TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'simulacoes' AND policyname = 'escrita autenticada simulacoes'
  ) THEN
    CREATE POLICY "escrita autenticada simulacoes" ON public.simulacoes FOR INSERT TO public WITH CHECK ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'simulacoes' AND policyname = 'exclusao autenticada simulacoes'
  ) THEN
    CREATE POLICY "exclusao autenticada simulacoes" ON public.simulacoes FOR DELETE TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'simulacoes' AND policyname = 'leitura autenticada simulacoes'
  ) THEN
    CREATE POLICY "leitura autenticada simulacoes" ON public.simulacoes FOR SELECT TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'team_coach_history_fotmob' AND policyname = 'leitura publica team_coach_history_fotmob'
  ) THEN
    CREATE POLICY "leitura publica team_coach_history_fotmob" ON public.team_coach_history_fotmob FOR SELECT TO public USING (true);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'team_elo' AND policyname = 'team_elo_public_select'
  ) THEN
    CREATE POLICY "team_elo_public_select" ON public.team_elo FOR SELECT TO public USING (true);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'team_elo_external' AND policyname = 'team_elo_external_public_select'
  ) THEN
    CREATE POLICY "team_elo_external_public_select" ON public.team_elo_external FOR SELECT TO public USING (true);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'team_elo_history' AND policyname = 'team_elo_history_public_select'
  ) THEN
    CREATE POLICY "team_elo_history_public_select" ON public.team_elo_history FOR SELECT TO public USING (true);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'team_source_ids' AND policyname = 'leitura publica team_source_ids'
  ) THEN
    CREATE POLICY "leitura publica team_source_ids" ON public.team_source_ids FOR SELECT TO public USING (true);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'team_stats' AND policyname = 'atualizacao autenticada team_stats'
  ) THEN
    CREATE POLICY "atualizacao autenticada team_stats" ON public.team_stats FOR UPDATE TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'team_stats' AND policyname = 'escrita autenticada team_stats'
  ) THEN
    CREATE POLICY "escrita autenticada team_stats" ON public.team_stats FOR INSERT TO public WITH CHECK ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'team_stats' AND policyname = 'leitura autenticada team_stats'
  ) THEN
    CREATE POLICY "leitura autenticada team_stats" ON public.team_stats FOR SELECT TO public USING ((auth.uid() IS NOT NULL));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'team_strengths' AND policyname = 'leitura publica team_strengths'
  ) THEN
    CREATE POLICY "leitura publica team_strengths" ON public.team_strengths FOR SELECT TO public USING (true);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'team_transfers_fotmob' AND policyname = 'leitura publica team_transfers_fotmob'
  ) THEN
    CREATE POLICY "leitura publica team_transfers_fotmob" ON public.team_transfers_fotmob FOR SELECT TO public USING (true);
  END IF;
END $$;
