-- =============================================================================
-- Migration: resultado persistido do backtest_kelly.py (Model Benchmarking)
-- =============================================================================
-- scripts/backtest_kelly.py já calculava ROI simulado (Kelly fracionário
-- 25%) com IC95% via bootstrap pros 4 modelos + variantes calibradas do
-- Model Benchmarking (mesma disciplina de api/backtest-betting.js: só
-- considera EV+ real quando o limite INFERIOR do IC fica acima de zero),
-- mas só imprimia no terminal -- essas duas tabelas persistem o resultado
-- pra virar um painel no frontend (equivalente ao "Backtest de apostas
-- simuladas (EV+)" já existente em Estatísticas dos Modelos, mas pros
-- modelos do benchmarking).
--
-- Cada rodada do script SUBSTITUI o resultado anterior (delete-e-regrava
-- por completo, não upsert incremental) -- é sempre um resultado fim-a-fim
-- contra o Test Set inteiro, não faz sentido acumular rodadas antigas.
-- =============================================================================

create table if not exists public.model_benchmarking_backtest (
  id                 bigint generated always as identity primary key,
  model_name         text not null,
  n_apostas          integer not null,
  roi_medio          numeric(8, 5) not null,
  roi_ic95_inferior  numeric(8, 5) not null,
  roi_ic95_superior  numeric(8, 5) not null,
  significativo      boolean not null,
  hiperparametros    jsonb,
  executado_em       timestamptz not null default now(),

  constraint model_benchmarking_backtest_model_key unique (model_name)
);

comment on table public.model_benchmarking_backtest is
  'ROI simulado (Kelly fracionário 25%, Test Set out-of-sample) + IC95% via bootstrap por variante de modelo do Model Benchmarking. significativo=true só quando o limite inferior do IC fica acima de zero (EV+ real, não ruído de amostra). Cada rodada do script substitui o resultado anterior por completo.';

create table if not exists public.model_benchmarking_backtest_liga (
  id                 bigint generated always as identity primary key,
  model_name         text not null,
  liga               text not null,
  n_apostas          integer not null,
  roi_medio          numeric(8, 5) not null,
  roi_ic95_inferior  numeric(8, 5) not null,
  roi_ic95_superior  numeric(8, 5) not null,
  significativo      boolean not null,
  executado_em       timestamptz not null default now(),

  constraint model_benchmarking_backtest_liga_key unique (model_name, liga)
);

comment on table public.model_benchmarking_backtest_liga is
  'Quebra do ROI simulado por liga -- mesma disciplina de não confiar em edge isolado (agregado pode esconder heterogeneidade real entre ligas). Só pras variantes CRUAS dos 4 modelos (não as calibradas), mesmo escopo de scripts/backtest_kelly.py.resumir_por_liga.';

create index if not exists idx_model_benchmarking_backtest_liga_model on public.model_benchmarking_backtest_liga (model_name);

-- =============================================================================
-- RLS — mesmo padrão do restante do projeto: leitura pública, escrita só via
-- service_role (GitHub Actions).
-- =============================================================================
alter table public.model_benchmarking_backtest enable row level security;
alter table public.model_benchmarking_backtest_liga enable row level security;

drop policy if exists "model_benchmarking_backtest_public_read" on public.model_benchmarking_backtest;
create policy "model_benchmarking_backtest_public_read"
  on public.model_benchmarking_backtest
  for select
  to anon, authenticated
  using (true);

drop policy if exists "model_benchmarking_backtest_liga_public_read" on public.model_benchmarking_backtest_liga;
create policy "model_benchmarking_backtest_liga_public_read"
  on public.model_benchmarking_backtest_liga
  for select
  to anon, authenticated
  using (true);

-- Nenhuma policy de insert/update/delete pra anon/authenticated: só o
-- service_role key grava, via scripts/backtest_kelly.py rodando no GitHub
-- Actions.
