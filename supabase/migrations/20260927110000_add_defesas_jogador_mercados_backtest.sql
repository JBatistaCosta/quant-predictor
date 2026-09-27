-- =============================================================================
-- Migration: adiciona 'defesas' como mercado válido em player_market_backtest
-- e lambda_defesas_jogo em player_match_walkforward -- backtest walk-forward
-- de defesas de goleiro
-- =============================================================================
-- Espelha 20260925100000_add_xa_jogador_mercados_backtest.sql (xA) --
-- infraestrutura de dado/feature/target de defesas já existe em
-- treinar_modelo_jogador_mercados.py (TARGET_DEFESAS, FEATURES_DEFESAS,
-- defesas_90_bayesiano) desde 20260927100000_add_defesas_jogador_mercados.sql
-- (que só cobriu player_match_estimates, produção) -- faltava o lado
-- walk-forward (player_match_walkforward/player_market_backtest), que
-- chutes/xG/xA já têm.
--
-- Pedido do usuário: além do modelo, validar defesas com o mesmo rigor
-- estatístico (walk-forward de verdade, RMSE vs. baseline) que os demais
-- mercados de jogador já têm, antes de expor como mercado na UI.
-- =============================================================================

alter table public.player_match_walkforward
  add column if not exists lambda_defesas_jogo numeric;

comment on column public.player_match_walkforward.lambda_defesas_jogo is
  'Defesas esperadas do goleiro na partida no backtest walk-forward (regressor CatBoost Poisson treinado só com dado anterior à temporada, alvo = Saves real extraído de match_player_stats_fotmob.stats_raw) -- nulo se a linha não tinha defesas_partida não-nula pra entrar no modelo dessa temporada (ver docstring de backtest_jogador_mercados_walkforward.py).';

alter table public.player_market_backtest
  drop constraint if exists player_market_backtest_mercado_check;

alter table public.player_market_backtest
  add constraint player_market_backtest_mercado_check
  check (mercado in ('chutes', 'gols_thinning', 'gols_direto', 'xg', 'chutes_no_alvo_thinning', 'xa', 'gsax_gols_adversario', 'xa_modulador_1x2', 'defesas'));
