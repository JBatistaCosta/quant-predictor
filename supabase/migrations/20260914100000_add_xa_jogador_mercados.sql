-- =============================================================================
-- Migration: adiciona xA (expected assists) esperado por jogador em
-- player_match_estimates
-- =============================================================================
-- Extensão do pipeline "Chutes & gols por jogador" pedida pelo usuário (ver
-- plano da sessão de tactical-schema-behavior-prediction): xA já existe como
-- coluna crua ingerida do FotMob (match_player_stats_fotmob.xa), mas nunca
-- foi modelada nem usada como feature/alvo -- diferente de xG, não precisa
-- de agregação chute a chute, já vem pronta por partida. Mesmo padrão de xG
-- (regressor CatBoost RMSE, contínuo, só CatBoost sem par LightGBM) e mesmas
-- 3 colunas espelhando as irmãs de xG (xg_90_bayesiano/xg_por_jogo/
-- lambda_xg_jogo), reunidas numa única migration em vez das 3 incrementais
-- históricas de xG.
-- =============================================================================

alter table public.player_match_estimates
  add column if not exists xa_90_bayesiano numeric,
  add column if not exists xa_por_jogo numeric,
  add column if not exists lambda_xa_jogo numeric;

comment on column public.player_match_estimates.xa_90_bayesiano is
  'xA (expected assists) por 90min do próprio jogador (EWMA + shrinkage bayesiano por posição x liga), "hoje" sem corte de data -- mesmo valor usado como feature de entrada do modelo de xA, exibido pra contexto no frontend.';
comment on column public.player_match_estimates.xa_por_jogo is
  'xA médio por jogo do próprio jogador (total histórico / jogos disputados, sem shrinkage) -- leitura direta, não é feature de modelo.';
comment on column public.player_match_estimates.lambda_xa_jogo is
  'xA esperado do jogador na partida (regressor CatBoost RMSE, alvo = match_player_stats_fotmob.xa por jogador-partida) -- nulo se o modelo de xA ainda não tiver rodado pra essa linha.';
