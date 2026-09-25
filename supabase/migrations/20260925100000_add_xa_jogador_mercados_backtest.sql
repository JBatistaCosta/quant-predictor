-- =============================================================================
-- Migration: adiciona 'xa' como mercado válido em player_market_backtest e
-- lambda_xa_jogo em player_match_walkforward -- backtest walk-forward de xA
-- =============================================================================
-- Espelha exatamente 20260830100000_add_xg_jogador_mercados.sql (xG) --
-- infraestrutura de dado/feature/target de xA já existe em
-- treinar_modelo_jogador_mercados.py (TARGET_XA, FEATURES_XA, ewma_xa_90/
-- xa_90_bayesiano) desde 20260914100000_add_xa_jogador_mercados.sql (que só
-- cobriu player_match_estimates, produção) -- faltava o lado walk-forward
-- (player_match_walkforward/player_market_backtest), que chutes/xG já têm.
-- Pedido do usuário: validar xA com o mesmo rigor estatístico (walk-forward
-- de verdade, RMSE vs. baseline, IC95% bootstrap por liga x temporada) que
-- chutes/xG já têm, antes de confiar nele na agregação bottom-up de time.
-- =============================================================================

alter table public.player_match_walkforward
  add column if not exists lambda_xa_jogo numeric;

comment on column public.player_match_walkforward.lambda_xa_jogo is
  'xA esperado do jogador na partida no backtest walk-forward (regressor CatBoost RMSE treinado só com dado anterior à temporada, alvo = match_player_stats_fotmob.xa) -- nulo se a linha não tinha xa_partida não-nulo pra entrar no modelo dessa temporada (ver docstring de backtest_jogador_mercados_walkforward.py).';

alter table public.player_market_backtest
  drop constraint if exists player_market_backtest_mercado_check;

alter table public.player_market_backtest
  add constraint player_market_backtest_mercado_check
  check (mercado in ('chutes', 'gols_thinning', 'gols_direto', 'xg', 'chutes_no_alvo_thinning', 'xa'));
