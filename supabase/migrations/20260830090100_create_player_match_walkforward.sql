-- =============================================================================
-- Migration: player_match_walkforward -- chutes/gols por jogador, walk-forward
-- =============================================================================
-- Guarda a previsão bruta (1 linha por jogador x partida x fonte_titular)
-- gerada por scripts/backtest_jogador_mercados_walkforward.py -- cada
-- temporada é prevista por um modelo treinado SÓ com dado estritamente
-- anterior a ela (mesmo espírito de xi_titular_walkforward), sem
-- vazamento por construção.
--
-- Deliberadamente separada de player_match_estimates: aquela é a previsão
-- AO VIVO da versão de produção (scripts/rodar_jogador_mercados_previsto.py,
-- reprocessada a cada rodada pra fixtures futuras); esta é a previsão
-- HISTÓRICA de backtest (um modelo descartável por temporada, nunca usado
-- em predição ao vivo) -- mesma separação já usada entre xi_previsto e
-- xi_titular_walkforward.
--
-- `fonte_titular` aqui é sempre 'previsto' na prática (o backtest walk-
-- forward avalia contra o passado inteiro usando minutos esperados como
-- média histórica do próprio jogador -- não há como "confirmar escalação
-- oficial" retroativamente pra um jogo de anos atrás sem reconstruir o
-- estado real de informação disponível hora a hora, fora de escopo do v1;
-- ver docstring de backtest_jogador_mercados_walkforward.py). A coluna
-- existe mesmo assim, com o mesmo domínio de player_match_estimates, pra
-- manter as duas tabelas espelhadas caso um backtest futuro cubra o caso
-- 'real'.
-- =============================================================================

create table if not exists public.player_match_walkforward (
  id                          bigint generated always as identity primary key,
  match_id                    bigint not null references public.matches(id),
  team_id                     bigint not null references public.teams(id),
  player_id                   bigint not null references public.players(id),
  fonte_titular               text not null check (fonte_titular in ('previsto', 'real')),
  prob_titular_usada          numeric,
  minutos_esperados           numeric not null,
  taxa_conversao_bayesiana    numeric not null,
  lambda_chutes_jogo          numeric not null,
  lambda_gols_jogo_thinning   numeric not null,
  lambda_gols_jogo_direto     numeric,
  season                      text not null,
  league_id                   bigint not null references public.leagues(id),
  model_version               text not null,
  gerado_em                   timestamptz not null default now(),

  constraint player_match_walkforward_key unique (match_id, team_id, player_id, model_version, fonte_titular)
);

comment on table public.player_match_walkforward is
  'Previsão bruta walk-forward (scripts/backtest_jogador_mercados_walkforward.py) de chutes/gols por jogador -- ponto-no-tempo por construção (cada temporada usa um modelo treinado só com dado anterior a ela). Espelha player_match_estimates (produção) na mesma separação já usada entre xi_titular_walkforward e xi_previsto.';

create index if not exists idx_player_match_walkforward_match on public.player_match_walkforward (match_id);
create index if not exists idx_player_match_walkforward_player on public.player_match_walkforward (player_id);
create index if not exists idx_player_match_walkforward_season_league on public.player_match_walkforward (season, league_id);

-- =============================================================================
-- RLS -- mesmo padrão de xi_titular_walkforward: leitura pública, escrita só
-- via service_role (script rodado manualmente via workflow_dispatch).
-- =============================================================================
alter table public.player_match_walkforward enable row level security;

drop policy if exists "player_match_walkforward_public_read" on public.player_match_walkforward;
create policy "player_match_walkforward_public_read"
  on public.player_match_walkforward
  for select
  to anon, authenticated
  using (true);
