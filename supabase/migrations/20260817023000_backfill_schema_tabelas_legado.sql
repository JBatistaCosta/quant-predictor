-- Backfill de schema: 52 tabelas que existem em produção mas nunca foram
-- criadas por uma migration (nasceram por SQL rodado direto no editor do
-- Supabase, ou por scripts anteriores a este repositório usar migrations).
--
-- Por que isso importa: o check `Supabase Preview` do CI reconstrói um
-- banco VAZIO replicando as migrations em ordem, pra validar cada PR contra
-- um schema limpo. Com ~52 tabelas fora do histórico de migrations, esse
-- banco vazio nunca bateu com produção -- qualquer PR que adicionasse uma
-- migration nova ficava com o check vermelho, mesmo sem relação com o que
-- estava faltando (foi o caso do #305: a migration daquele PR era sã, mas
-- rodava depois de `20260817024116_add_posicao_detalhe_player_availability.sql`,
-- que falha num banco vazio por faltar a tabela-base).
--
-- Gerado por introspecção do schema REAL de produção (pg_catalog via MCP),
-- não por dedução -- exatamente o tipo de tabela crítica que as convenções
-- deste projeto (ver `.claude/skills/workflow-quant-predictor`) mandam não
-- escrever heuristicamente. `CREATE TABLE IF NOT EXISTS` é seguro rodar
-- contra produção (onde as tabelas já existem, vira no-op) e contra um
-- banco vazio de preview (onde efetivamente cria a tabela).
--
-- Timestamp escolhido (23:00 no dia 17) especificamente ANTES de
-- `20260817024116_add_posicao_detalhe_player_availability.sql` (00:41:16) --
-- essa migration não é idempotente (`ADD COLUMN` sem `IF NOT EXISTS`), então
-- `player_availability_fotmob` aqui é criada SEM a coluna `posicao_detalhe`
-- de propósito, pra aquela migration adicionar a coluna normalmente logo
-- em seguida, igual já aconteceu em produção.
--
-- Constraints (PK/UNIQUE/CHECK/FK), índices extras, RLS e políticas ficam
-- na migration seguinte (`_constraints_indices_rls.sql`) -- separado em duas
-- fases pra não precisar de ordem topológica entre as 52 tabelas: todas as
-- tabelas existem antes de qualquer FK ser adicionada.

CREATE TABLE IF NOT EXISTS public.api_football_predictions_cache (
  fixture_id text NOT NULL,
  payload jsonb NOT NULL,
  fetched_at timestamp with time zone NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.assinaturas_api (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  plataforma text NOT NULL,
  tipo_dados text,
  nome_conta text,
  login text,
  chave_api text,
  variavel_ambiente text,
  url_painel text,
  cota_diaria integer,
  cota_semanal integer,
  cota_mensal integer,
  data_reinicio date,
  uso_atual integer,
  limite_atingido boolean NOT NULL DEFAULT false,
  ativa boolean NOT NULL DEFAULT true,
  notas text,
  criado_em timestamp with time zone NOT NULL DEFAULT now(),
  atualizado_em timestamp with time zone NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.carteira_simulada (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  match_id integer,
  data_aposta timestamp with time zone DEFAULT timezone('utc'::text, now()),
  mercado text NOT NULL,
  selecao text NOT NULL,
  odd_mercado numeric NOT NULL,
  odd_justa numeric NOT NULL,
  edge numeric NOT NULL,
  stake numeric NOT NULL,
  status text DEFAULT 'pendente'::text,
  lucro_prejuizo numeric DEFAULT 0
);

CREATE TABLE IF NOT EXISTS public.config_carteira (
  id integer NOT NULL DEFAULT 1,
  banca_inicial numeric DEFAULT 1000,
  banca_atual numeric DEFAULT 1000,
  tipo_stake text DEFAULT 'kelly'::text,
  stake_fixa numeric DEFAULT 10,
  kelly_multiplier numeric DEFAULT 0.25,
  max_stake_per_round_pct numeric DEFAULT 0.15,
  min_ev_pct numeric DEFAULT 4.0,
  max_ev_pct numeric DEFAULT 10.0
);

CREATE TABLE IF NOT EXISTS public.custom_model_ondemand_predictions (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  config_id uuid NOT NULL,
  match_id bigint NOT NULL,
  algorithm text NOT NULL,
  status text NOT NULL DEFAULT 'pendente'::text,
  probabilities jsonb,
  fair_odds jsonb,
  n_treino integer,
  error_message text,
  requested_by uuid,
  created_at timestamp with time zone NOT NULL DEFAULT timezone('utc'::text, now()),
  completed_at timestamp with time zone
);

CREATE TABLE IF NOT EXISTS public.detalhes_clube (
  equipe_id bigint NOT NULL,
  cidade_sede text,
  estadio text,
  pais text
);

CREATE TABLE IF NOT EXISTS public.detalhes_selecao (
  equipe_id bigint NOT NULL,
  pais_territorio text,
  confederacao text,
  federacao_filiada text,
  data_filiacao_fifa date
);

CREATE TABLE IF NOT EXISTS public.equipe_liga_temporada (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  equipe_id bigint NOT NULL,
  liga_temporada_id bigint NOT NULL,
  data_inicio date,
  data_fim date,
  posicao_final integer,
  desfecho text,
  observacao text
);

CREATE TABLE IF NOT EXISTS public.equipes (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  instituicao_id bigint NOT NULL,
  tipo text NOT NULL,
  categoria text NOT NULL,
  importacao_id bigint,
  criado_em timestamp with time zone NOT NULL DEFAULT now(),
  pipeline_team_id bigint
);

CREATE TABLE IF NOT EXISTS public.eventos (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  data_evento date,
  lambda1 numeric,
  lambda2 numeric,
  casa_de_apostas text,
  placar_mandante integer,
  placar_visitante integer,
  chutes_mandante numeric,
  chutes_visitante numeric,
  escanteios_mandante numeric,
  escanteios_visitante numeric,
  resolvido boolean NOT NULL DEFAULT false,
  criado_em timestamp with time zone NOT NULL DEFAULT now(),
  resolvido_em timestamp with time zone,
  equipe_mandante_id bigint,
  equipe_visitante_id bigint,
  fase_id bigint
);

CREATE TABLE IF NOT EXISTS public.fases (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  liga_temporada_id bigint NOT NULL,
  nome text NOT NULL,
  ordem integer NOT NULL DEFAULT 0,
  tipo text NOT NULL,
  formato_mata_mata text,
  data_inicio date,
  data_fim date
);

CREATE TABLE IF NOT EXISTS public.importacoes (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  fonte text NOT NULL,
  descricao text NOT NULL,
  url_fonte text,
  snapshot_antes jsonb,
  executado_em timestamp with time zone NOT NULL DEFAULT now(),
  status text NOT NULL,
  revertido boolean NOT NULL DEFAULT false,
  revertido_em timestamp with time zone
);

CREATE TABLE IF NOT EXISTS public.instituicao_nomes (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  instituicao_id bigint NOT NULL,
  tipo text NOT NULL,
  formalidade text NOT NULL,
  idioma text,
  nome text NOT NULL,
  sistema_escrita text,
  data_inicio date,
  data_fim date
);

CREATE TABLE IF NOT EXISTS public.instituicao_simbolos (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  instituicao_id bigint NOT NULL,
  url text NOT NULL,
  data_inicio date,
  data_fim date,
  observacao text
);

CREATE TABLE IF NOT EXISTS public.instituicao_sucessoes (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  instituicao_anterior_id bigint NOT NULL,
  instituicao_nova_id bigint,
  tipo_evento text NOT NULL,
  data_evento date,
  observacao text
);

CREATE TABLE IF NOT EXISTS public.instituicoes (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  ativo boolean NOT NULL DEFAULT true,
  data_fundacao date,
  data_encerramento date,
  importacao_id bigint,
  criado_em timestamp with time zone NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.league_model_params (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  league_id bigint NOT NULL,
  model_name text NOT NULL,
  stat text,
  param_name text NOT NULL,
  param_value numeric(8,5) NOT NULL,
  updated_at timestamp with time zone DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.liga_fonte_externa (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  league_id bigint NOT NULL,
  sistema text NOT NULL,
  identificador text NOT NULL,
  confirmado boolean NOT NULL DEFAULT true,
  cobertura text,
  notas text,
  criado_em timestamp with time zone NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.liga_oddspapi_tournament (
  league_id bigint NOT NULL,
  tournament_id bigint NOT NULL,
  tournament_name text NOT NULL,
  resolvido_em timestamp with time zone NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.ligas (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  nome text NOT NULL,
  nome_en text,
  tipo text NOT NULL,
  pais text,
  confederacao text,
  simbolo_url text,
  importacao_id bigint,
  criado_em timestamp with time zone NOT NULL DEFAULT now(),
  external_id text,
  pipeline_league_id bigint
);

CREATE TABLE IF NOT EXISTS public.ligas_temporadas (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  liga_id bigint NOT NULL,
  nome text NOT NULL,
  data_inicio date,
  data_fim date,
  criado_em timestamp with time zone NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.match_context_fotmob (
  match_id bigint NOT NULL,
  fotmob_match_id text,
  stadium_name text,
  stadium_city text,
  stadium_country text,
  stadium_lat double precision,
  stadium_long double precision,
  attendance integer,
  referee text,
  weather_temperature_c numeric,
  weather_wind_speed numeric,
  weather_wind_direction text,
  weather_humidity numeric,
  weather_precipitation numeric,
  weather_snow numeric,
  weather_cloud_cover numeric,
  weather_description text,
  weather_api_used text,
  weather_last_updated timestamp with time zone,
  stats_raw jsonb,
  captured_at timestamp with time zone NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.match_features_contexto (
  match_id bigint NOT NULL,
  team_id bigint NOT NULL,
  days_since_last_match numeric,
  hours_since_last_match numeric,
  days_filled boolean NOT NULL DEFAULT false,
  is_midweek_fatigue smallint NOT NULL DEFAULT 0,
  travel_distance_km numeric,
  travel_filled boolean NOT NULL DEFAULT false,
  manager_match_count integer,
  manager_filled boolean NOT NULL DEFAULT false,
  is_honeymoon_phase smallint NOT NULL DEFAULT 0,
  coach_name text,
  key_players_missing_torneo integer NOT NULL DEFAULT 0,
  squad_natl_matches_window integer NOT NULL DEFAULT 0,
  computed_at timestamp with time zone NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.match_odds (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  equipe_mandante text NOT NULL,
  equipe_visitante text NOT NULL,
  odds jsonb NOT NULL,
  updated_at timestamp with time zone NOT NULL DEFAULT now(),
  casa_de_apostas text
);

CREATE TABLE IF NOT EXISTS public.match_player_stats_fotmob (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  match_id bigint NOT NULL,
  team_id bigint NOT NULL,
  fotmob_player_id text NOT NULL,
  player_name text NOT NULL,
  is_goalkeeper boolean DEFAULT false,
  rating numeric,
  minutes_played integer,
  goals integer,
  assists integer,
  xg numeric,
  xa numeric,
  xgot numeric,
  total_shots integer,
  accurate_passes integer,
  chances_created integer,
  touches integer,
  stats_raw jsonb,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  player_id bigint,
  tackles integer,
  interceptions integer,
  ground_duels_won integer,
  aerials_won integer,
  touches_opp_box integer
);

CREATE TABLE IF NOT EXISTS public.match_shots_fotmob (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  fotmob_shot_id bigint NOT NULL,
  match_id bigint NOT NULL,
  team_id bigint NOT NULL,
  fotmob_player_id text,
  player_name text,
  minute integer,
  minute_added integer,
  x numeric,
  y numeric,
  xg numeric,
  xgot numeric,
  shot_type text,
  situation text,
  event_type text,
  is_on_target boolean,
  is_blocked boolean,
  is_own_goal boolean,
  period text,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  player_id bigint,
  minute_add integer,
  on_goal boolean
);

CREATE TABLE IF NOT EXISTS public.match_source_ids (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  match_id bigint NOT NULL,
  source text NOT NULL,
  source_id text NOT NULL,
  source_name text,
  created_at timestamp with time zone NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.match_stats (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  match_id bigint NOT NULL,
  team_id bigint NOT NULL,
  shots integer,
  shots_on_target integer,
  possession numeric(5,2),
  corners integer,
  fouls integer,
  yellow_cards integer,
  red_cards integer,
  xg numeric(6,3),
  xg_source text,
  ppda numeric(6,3),
  expected_points numeric(4,2),
  np_xg numeric(6,3),
  deep_completions integer
);

CREATE TABLE IF NOT EXISTS public.match_stats_fotmob (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  match_id bigint NOT NULL,
  team_id bigint NOT NULL,
  possession numeric,
  xg numeric,
  xg_open_play numeric,
  xg_set_play numeric,
  xg_non_penalty numeric,
  xgot numeric,
  total_shots integer,
  shots_on_target integer,
  shots_off_target integer,
  shots_blocked integer,
  shots_inside_box integer,
  shots_outside_box integer,
  big_chances integer,
  big_chances_missed integer,
  touches_opp_box integer,
  accurate_passes integer,
  accurate_passes_total integer,
  accurate_long_balls integer,
  accurate_crosses integer,
  corners integer,
  tackles integer,
  interceptions integer,
  blocks integer,
  clearances integer,
  keeper_saves integer,
  duels_won integer,
  aerial_duels_won integer,
  successful_dribbles integer,
  fouls_committed integer,
  yellow_cards integer,
  red_cards integer,
  stats_raw jsonb,
  created_at timestamp with time zone NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.mercados_avaliados (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  evento_id bigint NOT NULL,
  mercado text NOT NULL,
  categoria text,
  probabilidade_modelo numeric NOT NULL,
  odd_justa numeric,
  odd_casa numeric NOT NULL,
  ev numeric NOT NULL,
  stake_kelly numeric,
  bateu boolean,
  criado_em timestamp with time zone NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.model_calibration (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  model_name text NOT NULL,
  market text NOT NULL,
  method text NOT NULL,
  platt_coef numeric,
  platt_intercept numeric,
  log_loss_bruto numeric NOT NULL,
  log_loss_calibrado numeric NOT NULL,
  n_treino integer NOT NULL,
  n_teste integer NOT NULL,
  fitted_at timestamp with time zone NOT NULL DEFAULT now(),
  selection text,
  isotonic_x jsonb,
  isotonic_y jsonb
);

CREATE TABLE IF NOT EXISTS public.model_config (
  model_name text NOT NULL,
  config jsonb NOT NULL,
  updated_at timestamp with time zone NOT NULL DEFAULT now()
);

-- `fair_odds` é coluna GERADA (STORED), não um DEFAULT simples -- confirmado
-- via `pg_attribute.attgenerated = 's'`. Um `DEFAULT` de verdade não pode
-- referenciar outra coluna da mesma linha (`probability`); só
-- `GENERATED ALWAYS AS (...) STORED` permite isso. Foi exatamente esse erro
-- que o `Supabase Preview` pegou na primeira tentativa desta migration
-- (SQLSTATE 0A000, "cannot use column reference in DEFAULT expression") --
-- a introspecção original não distinguia `attgenerated` de um DEFAULT comum.
CREATE TABLE IF NOT EXISTS public.model_predictions (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  match_id bigint NOT NULL,
  model_name text NOT NULL,
  market text NOT NULL,
  selection text NOT NULL,
  probability numeric(7,5) NOT NULL,
  created_at timestamp with time zone DEFAULT now(),
  fair_odds numeric GENERATED ALWAYS AS (round((1.0 / NULLIF(probability, (0)::numeric)), 3)) STORED
);

CREATE TABLE IF NOT EXISTS public.model_stat_estimates (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  match_id bigint NOT NULL,
  model_name text NOT NULL,
  stat text NOT NULL,
  home_expected numeric(8,3) NOT NULL,
  away_expected numeric(8,3) NOT NULL,
  created_at timestamp with time zone DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.notas (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  conteudo text NOT NULL,
  criado_em timestamp with time zone NOT NULL DEFAULT now(),
  titulo text
);

CREATE TABLE IF NOT EXISTS public.odds_market (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  match_id bigint NOT NULL,
  bookmaker text NOT NULL,
  market text NOT NULL,
  selection text NOT NULL,
  odds numeric(8,3) NOT NULL,
  captured_at timestamp with time zone DEFAULT now(),
  snapshot text NOT NULL DEFAULT 'pre_closing'::text
);

CREATE TABLE IF NOT EXISTS public.oddspapi_cache (
  chave text NOT NULL,
  valor jsonb NOT NULL,
  atualizado_em timestamp with time zone NOT NULL DEFAULT now()
);

-- SEM `posicao_detalhe` de propósito -- ver comentário no topo do arquivo.
CREATE TABLE IF NOT EXISTS public.player_availability_fotmob (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  team_id bigint NOT NULL,
  fotmob_player_id text NOT NULL,
  player_id bigint,
  player_name text,
  role text,
  injured boolean NOT NULL DEFAULT false,
  injury_id text,
  expected_return text,
  captured_at timestamp with time zone NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.player_career_history_fotmob (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  player_id bigint NOT NULL,
  team_fotmob_id text,
  team_name text,
  start_date date,
  end_date date,
  active boolean,
  transfer_type text,
  appearances integer,
  goals integer,
  assists integer,
  captured_at timestamp with time zone NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.player_details_fotmob (
  player_id bigint NOT NULL,
  height_cm integer,
  preferred_foot text,
  contract_end date,
  current_market_value_eur numeric,
  primary_position text,
  all_positions jsonb,
  traits jsonb,
  captured_at timestamp with time zone NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.player_ratings (
  player_id bigint NOT NULL,
  rating numeric NOT NULL DEFAULT 1500,
  n_partidas integer NOT NULL DEFAULT 0,
  updated_at timestamp with time zone NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.player_trophies_fotmob (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  player_id bigint NOT NULL,
  team_fotmob_id text,
  team_name text,
  league_fotmob_id text,
  league_name text,
  season text,
  result text NOT NULL,
  country_code text,
  captured_at timestamp with time zone NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.predictions_log (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  equipe_mandante text NOT NULL,
  equipe_visitante text NOT NULL,
  mercado text NOT NULL,
  outcome text NOT NULL,
  probabilidade numeric NOT NULL,
  odd_oferecida numeric,
  resultado boolean,
  criado_em timestamp with time zone NOT NULL DEFAULT now(),
  resolvido_em timestamp with time zone
);

CREATE TABLE IF NOT EXISTS public.simulacoes (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  nome text NOT NULL,
  equipe_mandante text NOT NULL,
  equipe_visitante text NOT NULL,
  config jsonb NOT NULL,
  criado_em timestamp with time zone NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.team_coach_history_fotmob (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  team_id bigint NOT NULL,
  coach_fotmob_id text NOT NULL,
  coach_name text,
  season text NOT NULL,
  fotmob_league_id integer,
  wins integer,
  draws integer,
  losses integer,
  order_idx integer NOT NULL,
  captured_at timestamp with time zone NOT NULL DEFAULT now()
);

-- Produção usa `id bigint DEFAULT nextval('team_elo_id_seq')` (sequence
-- nomeada à parte, estilo antigo). Aqui usamos `GENERATED BY DEFAULT AS
-- IDENTITY` -- equivalente pra inserts, e não exige recriar a sequence
-- nomeada à mão num banco vazio. Como a tabela já existe em produção,
-- `IF NOT EXISTS` nunca chega a avaliar esta definição lá.
CREATE TABLE IF NOT EXISTS public.team_elo (
  id bigint GENERATED BY DEFAULT AS IDENTITY NOT NULL,
  team_id bigint NOT NULL,
  escopo text NOT NULL,
  league_id bigint,
  rating numeric NOT NULL,
  partidas integer NOT NULL DEFAULT 0,
  atualizado_em timestamp with time zone NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.team_elo_external (
  id bigint GENERATED BY DEFAULT AS IDENTITY NOT NULL,
  team_id bigint,
  clube_nome_externo text NOT NULL,
  pais text,
  elo numeric NOT NULL,
  valido_de date NOT NULL,
  valido_ate date NOT NULL,
  fonte text NOT NULL DEFAULT 'clubelo'::text,
  atualizado_em timestamp with time zone NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.team_elo_history (
  id bigint GENERATED BY DEFAULT AS IDENTITY NOT NULL,
  team_id bigint NOT NULL,
  escopo text NOT NULL,
  league_id bigint,
  match_id bigint,
  rodada integer,
  rating_antes numeric NOT NULL,
  rating_depois numeric NOT NULL,
  match_date date NOT NULL,
  created_at timestamp with time zone NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.team_source_ids (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  team_id bigint NOT NULL,
  source text NOT NULL,
  source_id text NOT NULL,
  source_name text,
  created_at timestamp with time zone DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.team_stats (
  team_name text NOT NULL,
  xg numeric,
  xga numeric,
  chutes numeric,
  chutes_no_gol numeric,
  escanteios numeric,
  updated_at timestamp with time zone NOT NULL DEFAULT now(),
  historico jsonb
);

CREATE TABLE IF NOT EXISTS public.team_strengths (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  team_id bigint NOT NULL,
  league_id bigint NOT NULL,
  model_name text NOT NULL,
  stat text,
  attack numeric(8,5) NOT NULL,
  defense numeric(8,5) NOT NULL,
  trained_through timestamp with time zone,
  updated_at timestamp with time zone DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.team_transfers_fotmob (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  team_id bigint NOT NULL,
  direction text NOT NULL,
  fotmob_player_id text,
  player_id bigint,
  player_name text,
  position_label text,
  transfer_date timestamp with time zone,
  from_club text,
  from_club_fotmob_id text,
  to_club text,
  to_club_fotmob_id text,
  fee_value numeric,
  on_loan boolean,
  transfer_type text,
  raw jsonb,
  captured_at timestamp with time zone NOT NULL DEFAULT now()
);
