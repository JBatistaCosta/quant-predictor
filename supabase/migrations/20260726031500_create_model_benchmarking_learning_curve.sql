-- =============================================================================
-- Migration: curva de aprendizado (loss por iteração do boosting) do
-- Model Benchmarking
-- =============================================================================
-- Pedido do usuário: comparar Treino/Validação/Teste ao longo do
-- treinamento de cada modelo de árvore (catboost_v1..v5/v3b,
-- xgboost_v1..v5/v3b, lightgbm_v1..v5/v3b) -- diferente de
-- model_benchmarking_backtest (só 3 números finais do Test Set), essa
-- tabela guarda 1 linha por ITERAÇÃO do boosting, log-loss multiclasse nos
-- 3 splits (Treino sempre preenchido; Validação/Teste vêm dos eval_set
-- nativos do CatBoost/XGBoost/LightGBM -- ver scripts/modelos_ml.py).
--
-- Só a config VENCEDORA do grid search (scripts/backtest_kelly.py,
-- tunar_treinar_e_calibrar) é capturada -- capturar todas as combinações
-- da grade seria custo de armazenamento sem valor de diagnóstico. Só o
-- backtest manual (workflow_dispatch, orçamento de 200min) grava aqui --
-- o cron diário de produção (rodar_predicoes.py) nunca passa val_df/
-- test_df pra scripts/modelos_ml.py, então nunca gera curva (evita custo
-- de CPU desnecessário no runner gratuito rodando todo dia). dixon_coles_v1
-- não entra aqui (Poisson puro, não é treino por iteração de boosting).
--
-- Cada rodada do script SUBSTITUI o resultado anterior por completo (mesma
-- disciplina de model_benchmarking_backtest) -- é sempre o retrato da
-- config vencedora da rodada mais recente, não faz sentido acumular.
-- =============================================================================

create table if not exists public.model_benchmarking_learning_curve (
  id            bigint generated always as identity primary key,
  model_name    text not null,
  mercado       text not null,
  iteracao      integer not null,
  treino        numeric(10, 6) not null,
  validacao     numeric(10, 6),
  teste         numeric(10, 6),
  executado_em  timestamptz not null default now(),

  constraint model_benchmarking_learning_curve_key unique (model_name, mercado, iteracao)
);

comment on table public.model_benchmarking_learning_curve is
  'Log-loss multiclasse por iteração do boosting (Treino/Validação/Teste) da config vencedora do grid search de cada modelo de árvore do Model Benchmarking, capturado via eval_set nativo do CatBoost/XGBoost/LightGBM (scripts/modelos_ml.py). Só gerado pelo backtest manual (scripts/backtest_kelly.py) -- o cron diário de produção não grava aqui. Cada rodada substitui o resultado anterior por completo.';

create index if not exists idx_model_benchmarking_learning_curve_lookup
  on public.model_benchmarking_learning_curve (model_name, mercado);

-- =============================================================================
-- RLS — mesmo padrão do restante do projeto: leitura pública, escrita só via
-- service_role (GitHub Actions).
-- =============================================================================
alter table public.model_benchmarking_learning_curve enable row level security;

drop policy if exists "model_benchmarking_learning_curve_public_read" on public.model_benchmarking_learning_curve;
create policy "model_benchmarking_learning_curve_public_read"
  on public.model_benchmarking_learning_curve
  for select
  to anon, authenticated
  using (true);

-- Nenhuma policy de insert/update/delete pra anon/authenticated: só o
-- service_role key grava, via scripts/backtest_kelly.py rodando no GitHub
-- Actions.
