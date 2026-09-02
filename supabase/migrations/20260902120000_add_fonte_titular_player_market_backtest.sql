-- =============================================================================
-- Migration: adiciona fonte_titular a player_market_backtest (passada 'real'
-- do backtest walk-forward de chutes/gols por jogador)
-- =============================================================================
-- Até aqui o backtest walk-forward só cobria fonte_titular='previsto' -- o
-- comentário original de player_match_walkforward dizia que a comparação
-- 'previsto' vs. 'real' "não é replicável retroativamente sem re-simular
-- qual escalação teria saído pra cada jogo antigo". Isso ficou desatualizado:
-- match_lineup_fotmob (escalação oficial confirmada) já cobre ~18 mil
-- partidas históricas nas 12 ligas do escopo, vindas de um backfill
-- dedicado (arquivos_do_claude/ingestao_fotmob_lineup_backfill.py), não só
-- do cron ao vivo. scripts/backtest_jogador_mercados_walkforward.py passa a
-- rodar também a passada 'real' (minutos_esperados determinístico por papel
-- confirmado, mesmo padrão de scripts/rodar_jogador_mercados_previsto.py em
-- produção) nas partidas onde essa escalação já é conhecida -- sem
-- re-simular nada, só reaproveitando o dado que já está na base.
--
-- player_match_walkforward já tinha fonte_titular no domínio ('previsto',
-- 'real') desde a criação (mesmo domínio de player_match_estimates, "pra
-- manter as duas tabelas espelhadas caso um backtest futuro cubra o caso
-- 'real'") -- só player_market_backtest (métrica agregada por liga/temporada)
-- ainda não tinha essa coluna.
-- =============================================================================

alter table public.player_market_backtest
  add column if not exists fonte_titular text not null default 'previsto';

alter table public.player_market_backtest
  drop constraint if exists player_market_backtest_fonte_titular_check;

alter table public.player_market_backtest
  add constraint player_market_backtest_fonte_titular_check
  check (fonte_titular in ('previsto', 'real'));

alter table public.player_market_backtest
  drop constraint if exists player_market_backtest_key;

alter table public.player_market_backtest
  add constraint player_market_backtest_key
  unique (season, league_id, model_version, mercado, fonte_titular);

comment on column public.player_market_backtest.fonte_titular is
  'previsto = minutos_esperados por média histórica incondicional do jogador (todo o histórico do backtest); real = minutos_esperados determinístico por papel confirmado em match_lineup_fotmob, só nas partidas históricas onde a escalação oficial já foi capturada -- ver scripts/backtest_jogador_mercados_walkforward.py.';
