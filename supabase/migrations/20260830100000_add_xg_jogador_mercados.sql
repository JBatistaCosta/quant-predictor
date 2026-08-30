-- =============================================================================
-- Migration: adiciona xG esperado por jogador (player_match_estimates/
-- player_match_walkforward/player_market_backtest)
-- =============================================================================
-- Extensão pedida pelo usuário depois de validar chutes/gols por jogador em
-- produção (PR #391/#392): xG por jogador reaproveita quase todo o pipeline
-- já existente -- match_shots_fotmob.xg já está ~99,7% preenchido nas 6
-- ligas do escopo (confirmado por query real antes desta migration), então
-- o rótulo é só a soma de xg por (match_id, team_id, player_id), sem
-- depender de nenhuma fonte nova.
--
-- Diferente de chutes/gols (contagem, perda de Poisson): xG por jogador é
-- CONTÍNUO, então usa regressor RMSE comum (mesmo padrão de
-- scripts/treinar_regressor_xgot.py em nível de TIME) em vez de Poisson --
-- só CatBoost, sem par LightGBM (mesma simplicidade do precedente de time).
-- =============================================================================

alter table public.player_match_estimates
  add column if not exists lambda_xg_jogo numeric;

comment on column public.player_match_estimates.lambda_xg_jogo is
  'xG esperado do jogador na partida (regressor CatBoost RMSE, alvo = soma de match_shots_fotmob.xg por jogador-partida) -- nulo se o modelo de xG ainda não tiver rodado pra essa linha.';

alter table public.player_match_walkforward
  add column if not exists lambda_xg_jogo numeric;

alter table public.player_market_backtest
  drop constraint if exists player_market_backtest_mercado_check;

alter table public.player_market_backtest
  add constraint player_market_backtest_mercado_check
  check (mercado in ('chutes', 'gols_thinning', 'gols_direto', 'xg'));
