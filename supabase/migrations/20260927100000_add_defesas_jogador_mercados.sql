-- =============================================================================
-- Migration: adiciona defesas de goleiro esperadas por jogador em
-- player_match_estimates
-- =============================================================================
-- Extensão do pipeline "Chutes & gols por jogador" (mesmo padrão de xA/xG,
-- ver 20260914100000_add_xa_jogador_mercados.sql) pro mercado de defesas de
-- goleiro. Ao contrário de xA, o FotMob não tem coluna própria pra defesas
-- (nem em match_player_stats_fotmob nem em nenhuma outra tabela) -- o número
-- mora dentro de `match_player_stats_fotmob.stats_raw` (JSON bruto,
-- `top_stats.Saves.stat.value`), extraído por
-- `treinar_modelo_jogador_mercados.extrair_saves_stats_raw`.
--
-- Verificado contra produção (27/09, a pedido do usuário: "verifique as
-- defesas do goleiro") antes de escrever este pipeline: 98% de cobertura em
-- goleiro com 90 min jogados (56.487 de 64.808 aparições). A identidade
-- ingênua "defesas = chutes no alvo sofridos - gols sofridos" NÃO bate
-- (defasagem média de ~3,5 chutes/jogo) porque "chute no alvo" neste projeto
-- inclui bloqueio de defensor na linha do gol, não só o que o goleiro toca
-- -- por isso o alvo do modelo é o `Saves` real do FotMob direto, nunca
-- derivado algebricamente de chutes no alvo.
--
-- `lambda_defesas_jogo` é gravado pra QUALQUER jogador (mesmo padrão de
-- lambda_xg_jogo/lambda_xa_jogo, que também não se restringem por posição) --
-- pra jogador de linha, o modelo naturalmente prevê perto de zero (feature
-- de volume primário é `defesas_90_bayesiano`, que é ~0 pra quem nunca fez
-- uma defesa). O consumidor (frontend/mercado de assistência goleiro) é que
-- filtra por goleiro, não a gravação.
-- =============================================================================

alter table public.player_match_estimates
  add column if not exists defesas_90_bayesiano numeric,
  add column if not exists defesas_por_jogo numeric,
  add column if not exists lambda_defesas_jogo numeric;

comment on column public.player_match_estimates.defesas_90_bayesiano is
  'Defesas por 90min do próprio jogador (EWMA + shrinkage bayesiano por posição x liga), "hoje" sem corte de data -- mesmo valor usado como feature de entrada do modelo de defesas, exibido pra contexto no frontend. Perto de 0 pra jogador de linha (nunca faz defesa).';
comment on column public.player_match_estimates.defesas_por_jogo is
  'Defesas médias por jogo do próprio jogador (total histórico / jogos disputados, sem shrinkage) -- leitura direta, não é feature de modelo.';
comment on column public.player_match_estimates.lambda_defesas_jogo is
  'Defesas esperadas do goleiro na partida (regressor CatBoost Poisson, alvo = Saves real extraído de match_player_stats_fotmob.stats_raw por goleiro-partida) -- nulo se o modelo de defesas ainda não tiver rodado pra essa linha.';
