-- =============================================================================
-- Migration: adiciona "chutes ao gol" (on-target, excluindo bloqueado) como
-- mercado derivado por jogador -- afinamento de Poisson, sem modelo novo
-- =============================================================================
-- Pedido do usuário: distinguir chutes totais de chutes que efetivamente vão
-- na direção do gol (viram gol ou defesa do goleiro), pra dar noção de
-- "chance de finalizar no alvo até o fim da partida". Reaproveita o mesmo
-- afinamento de Poisson já usado pra gols (lambda_chutes x taxa_bayesiana),
-- só que um passo antes da conversão -- sem treinar CatBoost novo.
--
-- Definição confirmada por query real: is_on_target=true SOZINHO inclui
-- chute bloqueado por um defensor antes de chegar ao goleiro
-- (event_type='AttemptSaved' com is_blocked=true, ~26% dos chutes "no alvo"
-- pelo flag bruto) -- isso nunca vira "defesa do goleiro" no sentido pedido
-- pelo usuário. "Chute ao gol" aqui = is_on_target=true AND is_blocked=false
-- (bate com o padrão real de futebol, ~35% dos chutes totais nas 6 ligas do
-- escopo -- contra ~61% se usasse só is_on_target).
-- =============================================================================

alter table public.player_match_estimates
  add column if not exists taxa_no_alvo_bayesiana numeric,
  add column if not exists chutes_no_alvo_90_bayesiano numeric,
  add column if not exists chutes_no_alvo_por_jogo numeric,
  add column if not exists lambda_chutes_no_alvo_jogo numeric;

comment on column public.player_match_estimates.taxa_no_alvo_bayesiana is
  'Fração dos chutes do jogador que vão ao gol (is_on_target AND NOT is_blocked), com shrinkage bayesiano -- usada pra afinar lambda_chutes_jogo em lambda_chutes_no_alvo_jogo.';
comment on column public.player_match_estimates.chutes_no_alvo_90_bayesiano is
  'Chutes ao gol por 90min do próprio jogador, com shrinkage bayesiano -- mesmo padrão de chutes_90_bayesiano.';
comment on column public.player_match_estimates.chutes_no_alvo_por_jogo is
  'Média crua de chutes ao gol por partida disputada -- só pra leitura, mesmo padrão de chutes_por_jogo.';
comment on column public.player_match_estimates.lambda_chutes_no_alvo_jogo is
  'λ esperado de chutes ao gol do jogador na partida (afinamento de Poisson: lambda_chutes_jogo x taxa_no_alvo_bayesiana) -- não é um regressor treinado à parte.';

alter table public.player_match_walkforward
  add column if not exists lambda_chutes_no_alvo_jogo numeric;

alter table public.player_market_backtest
  drop constraint if exists player_market_backtest_mercado_check;

alter table public.player_market_backtest
  add constraint player_market_backtest_mercado_check
  check (mercado in ('chutes', 'gols_thinning', 'gols_direto', 'xg', 'chutes_no_alvo_thinning'));
