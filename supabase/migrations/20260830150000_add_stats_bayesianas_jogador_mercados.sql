-- =============================================================================
-- Migration: adiciona estatísticas bayesianas por-90 (chutes/gols/xG) em
-- player_match_estimates, pra exibir no frontend ao lado do λ previsto
-- =============================================================================
-- Pedido do usuário: mostrar a média histórica (com shrinkage bayesiano) do
-- próprio jogador junto de cada previsão -- já é calculada internamente por
-- rodar_jogador_mercados_previsto.py (_bayesiano_atual), só não era
-- persistida. Sem essas colunas o frontend não tem como comparar "o que o
-- modelo previu" com "o que o jogador costuma fazer".
-- =============================================================================

alter table public.player_match_estimates
  add column if not exists chutes_90_bayesiano numeric,
  add column if not exists gols_90_bayesiano numeric,
  add column if not exists xg_90_bayesiano numeric;

comment on column public.player_match_estimates.chutes_90_bayesiano is
  'Chutes por 90min do próprio jogador (EWMA + shrinkage bayesiano por posição x liga), "hoje" sem corte de data -- mesmo valor usado como feature de entrada do modelo, exibido pra contexto no frontend.';
comment on column public.player_match_estimates.gols_90_bayesiano is
  'Gols por 90min do próprio jogador (mesmo shrinkage), usado internamente pra calcular taxa_conversao_bayesiana.';
comment on column public.player_match_estimates.xg_90_bayesiano is
  'xG por 90min do próprio jogador (mesmo shrinkage) -- feature de entrada do modelo de xG.';
