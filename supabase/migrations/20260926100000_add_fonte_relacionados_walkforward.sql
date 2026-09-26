-- =============================================================================
-- Migration: fonte_titular='relacionados' em player_match_walkforward e
-- player_market_backtest
-- =============================================================================
-- scripts/backtest_jogador_mercados_walkforward.py ganha uma terceira passada,
-- 'relacionados': o elenco relacionado inteiro da partida
-- (xi_titular_walkforward, ~21 jogadores por time, inclusive quem ficou no
-- banco sem entrar), com minutos esperados = mistura por prob_titular (a mesma
-- fórmula da fonte 'previsto' de produção em player_match_estimates).
--
-- Motivo: as passadas 'previsto'/'real' só têm linha pra quem ENTROU em campo.
-- Somar o λ delas por (partida, time) inclui os reservas que entraram --
-- informação pós-jogo que prevê o resultado além da Pinnacle de fechamento
-- (CONTEXTO_PROJETO.md, 26/09). 'relacionados' é a única fonte desta tabela
-- que pode ser somada por time.
-- =============================================================================

alter table public.player_match_walkforward
  drop constraint if exists player_match_walkforward_fonte_titular_check;
alter table public.player_match_walkforward
  add constraint player_match_walkforward_fonte_titular_check
  check (fonte_titular in ('previsto', 'real', 'relacionados'));

alter table public.player_market_backtest
  drop constraint if exists player_market_backtest_fonte_titular_check;
alter table public.player_market_backtest
  add constraint player_market_backtest_fonte_titular_check
  check (fonte_titular in ('previsto', 'real', 'relacionados'));

comment on column public.player_match_walkforward.fonte_titular is
  '''previsto''/''real'': só quem ENTROU em campo (não somar por time -- vaza os reservas que entraram). ''relacionados'': elenco relacionado inteiro com minutos misturados por prob_titular -- a única somável por (partida, time).';
