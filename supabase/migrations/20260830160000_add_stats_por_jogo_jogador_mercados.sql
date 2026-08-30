-- =============================================================================
-- Migration: adiciona estatísticas CRUAS por-jogo (chutes/gols/xG) em
-- player_match_estimates, complementando as por-90 já existentes
-- =============================================================================
-- Pedido do usuário: "seria melhor fazer análise de chutes/gols/xG por jogo
-- também por 90min" -- mostrar as duas visões lado a lado no frontend.
-- "por jogo" é a média crua (sem shrinkage bayesiano) do próprio jogador,
-- dividida por partidas disputadas (não por minutos) -- número mais
-- intuitivo de leitura direta, diferente do "por 90" (normalizado por tempo
-- em campo, com shrinkage, usado como feature de entrada do modelo).
-- =============================================================================

alter table public.player_match_estimates
  add column if not exists chutes_por_jogo numeric,
  add column if not exists gols_por_jogo numeric,
  add column if not exists xg_por_jogo numeric;

comment on column public.player_match_estimates.chutes_por_jogo is
  'Média crua de chutes por partida disputada do próprio jogador (sem shrinkage bayesiano, sem normalizar por minutos) -- só pra leitura, não é feature do modelo.';
comment on column public.player_match_estimates.gols_por_jogo is
  'Média crua de gols por partida disputada do próprio jogador -- só pra leitura.';
comment on column public.player_match_estimates.xg_por_jogo is
  'Média crua de xG por partida disputada do próprio jogador -- só pra leitura.';
