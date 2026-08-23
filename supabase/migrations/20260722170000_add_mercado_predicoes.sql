-- =============================================================================
-- Migration: predicoes ganha `mercado` + prob_under/prob_over
-- =============================================================================
-- Pedido do usuário: os modelos catboost/xgboost/lightgbm (v1-v5/v3B) JÁ
-- foram treinados e avaliados em Over/Under 2.5 gols (backtest_kelly.py,
-- ver model_benchmarking_backtest com mercado='over_under_2.5') -- mas a
-- previsão por partida nunca era persistida em lugar nenhum servível,
-- porque `predicoes` só tinha colunas de 1X2 (prob_home/draw/away). Isso
-- impedia a Simulação de Carteira de rodar esses modelos nesse mercado
-- (só dixon_coles_v1/_walkforward_v1, que vêm do pipeline antigo,
-- apareciam). Adiciona `mercado` (default '1X2', preserva as ~132 mil
-- linhas já existentes como 1X2 sem precisar de backfill) e prob_under/
-- prob_over (NULL pras linhas 1X2 existentes).
--
-- Chave única passa a ser (match_id, model_name, mercado) -- um mesmo
-- modelo pode ter uma previsão 1X2 E uma previsão over_under_2.5 pra
-- mesma partida, cada uma na sua própria linha.
-- =============================================================================

alter table public.predicoes
  add column if not exists mercado text not null default '1X2',
  add column if not exists prob_under numeric,
  add column if not exists prob_over numeric;

alter table public.predicoes
  add constraint predicoes_prob_under_check check (prob_under is null or (prob_under >= 0 and prob_under <= 1)),
  add constraint predicoes_prob_over_check check (prob_over is null or (prob_over >= 0 and prob_over <= 1));

alter table public.predicoes drop constraint if exists predicoes_match_model_key;
alter table public.predicoes add constraint predicoes_match_model_mercado_key unique (match_id, model_name, mercado);

comment on column public.predicoes.mercado is '1X2 (default, usa prob_home/draw/away) ou over_under_2.5 (usa prob_under/prob_over) -- outros mercados do Model Benchmarking (corners_ou95/faixa_gols) não entram aqui, não têm odds reais pra servir ao vivo.';
comment on column public.predicoes.prob_under is 'Probabilidade de Under 2.5 gols -- só preenchido quando mercado=over_under_2.5.';
comment on column public.predicoes.prob_over is 'Probabilidade de Over 2.5 gols -- só preenchido quando mercado=over_under_2.5.';
