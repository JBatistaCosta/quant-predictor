-- =============================================================================
-- Migration: player_market_backtest -- métrica agregada do backtest de jogador
-- =============================================================================
-- Guarda a saída agregada (por temporada x liga x mercado) de
-- scripts/backtest_jogador_mercados_walkforward.py -- mesmo espírito de
-- xi_backtest_walkforward, adaptado pra 3 mercados por jogador:
--   'chutes'         -- regressor Poisson de chutes vs. baseline (RMSE + IC95%
--                        via bootstrap da diferença de erro absoluto).
--   'gols_thinning'  -- P(marcar>=1) via lambda_chutes x taxa_conversao
--                        (afinamento de Poisson) vs. baseline (log-loss/Brier).
--   'gols_direto'    -- P(marcar>=1) via regressor Poisson direto no alvo
--                        gols_partida vs. baseline (log-loss/Brier).
-- Os dois últimos existem lado a lado de propósito -- não há vencedor fixo
-- no código, quem for usar em produção decide olhando essa tabela (ver
-- docstring do script e o plano da sessão: "não declarar vencedor sem
-- medir").
--
-- Deliberadamente separada de models_registry (guarda o modelo de PRODUÇÃO,
-- retreinado manualmente e usado ao vivo por
-- scripts/rodar_jogador_mercados_previsto.py) -- os modelos treinados aqui
-- são descartáveis (um por temporada, só pra gerar a previsão de teste).
-- =============================================================================

create table if not exists public.player_market_backtest (
  id                bigint generated always as identity primary key,
  season            text not null,
  league_id         bigint not null references public.leagues(id),
  model_version     text not null,
  mercado           text not null check (mercado in ('chutes', 'gols_thinning', 'gols_direto')),
  n_partidas        int not null,
  n_previsoes       int not null,
  rmse_modelo       numeric,
  rmse_baseline     numeric,
  log_loss          numeric,
  brier             numeric,
  calibracao        jsonb,
  gerado_em         timestamptz not null default now(),

  constraint player_market_backtest_key unique (season, league_id, model_version, mercado)
);

comment on table public.player_market_backtest is
  'Backtest walk-forward por temporada x liga x mercado dos modelos de chutes/gols por jogador (scripts/backtest_jogador_mercados_walkforward.py) -- retreina só com dado anterior a cada temporada e avalia nela, sem vazamento. mercado in (chutes, gols_thinning, gols_direto) -- os dois últimos comparam o afinamento de Poisson contra um regressor direto de gols, sem vencedor fixo no schema.';

comment on column public.player_market_backtest.rmse_modelo is
  'Preenchido só pro mercado "chutes" (regressão de contagem) -- nulo pra gols_thinning/gols_direto, que usam log_loss/brier (evento raro, marcar>=1).';

comment on column public.player_market_backtest.log_loss is
  'Preenchido só pros mercados gols_thinning/gols_direto (P(marcar>=1)) -- nulo pro mercado "chutes".';

create index if not exists idx_player_market_backtest_season on public.player_market_backtest (season);
create index if not exists idx_player_market_backtest_league on public.player_market_backtest (league_id);

-- =============================================================================
-- RLS -- mesmo padrão de xi_backtest_walkforward: leitura pública, escrita só
-- via service_role (script rodado manualmente via workflow_dispatch).
-- =============================================================================
alter table public.player_market_backtest enable row level security;

drop policy if exists "player_market_backtest_public_read" on public.player_market_backtest;
create policy "player_market_backtest_public_read"
  on public.player_market_backtest
  for select
  to anon, authenticated
  using (true);
