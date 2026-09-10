-- Estatísticas por TEMPO de jogo (1º/2º tempo), achado 10/09: o FotMob
-- retorna content.stats.Periods com TRÊS chaves (All/FirstHalf/SecondHalf),
-- cada uma com o mesmo conjunto completo de grupos (shots, expected_goals,
-- passes, defence, duels, discipline) -- mas até agora só Periods.All era
-- lido (match_stats_fotmob). Tabela nova em vez de dobrar as colunas de
-- match_stats_fotmob (que já tem ~30 métricas + zonas de ataque) -- mesmo
-- padrão de granularidade separada usado em match_team_game_state.
--
-- Ausente em payload de partida antiga (mesma limitação já documentada pro
-- Achado 11 em shots/discipline, e agora também pras zonas de ataque) --
-- partida sem quebra por tempo simplesmente não gera linha aqui.
create table if not exists match_stats_fotmob_periodo (
  id bigint generated always as identity primary key,
  match_id bigint not null references matches(id) on delete cascade,
  team_id bigint not null references teams(id),
  periodo text not null check (periodo in ('primeiro_tempo', 'segundo_tempo')),
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
  created_at timestamptz not null default now(),
  unique (match_id, team_id, periodo)
);

create index if not exists idx_match_stats_fotmob_periodo_match on match_stats_fotmob_periodo (match_id);

alter table match_stats_fotmob_periodo enable row level security;

create policy "Public read access" on match_stats_fotmob_periodo
  for select using (true);

-- Marca "já tentei buscar estatística por tempo pra essa partida" (mesmo
-- quando o FotMob não tem -- partida antiga), pra distinguir de "nunca
-- tentado" e não reprocessar pra sempre. Espelha attacking_zone_checked.
alter table match_stats_fotmob
  add column if not exists periodo_checked boolean not null default false;
