-- `models_registry.created_at` só é setado na inserção -- como o pipeline de
-- treino (scripts/treinar_modelo_hibrido.py::registrar_no_models_registry,
-- e o equivalente em treinar_modelo_custom_wf.py) faz UPSERT em
-- ON CONFLICT (name, market), toda atualização diária/manual sobrescreve
-- `metrics_test`/`hyperparameters` de verdade mas NUNCA move `created_at` --
-- a coluna fica presa na data da primeira inserção pra sempre. Achado real
-- (10/09): investigando por que `hibrido_gols_v1`/`hibrido_gols_xg_v1`
-- pareciam "sem retreino desde 18/08", confirmei via log do GitHub Actions
-- que o retreino de 09/09 rodou e gravou métricas frescas -- só o
-- `created_at` de 18/08 (da primeira inserção) que enganava, sem
-- `updated_at` nenhum pra desambiguar.
--
-- Reaproveita `public.set_updated_at()` (já criada em
-- 20260718120000_create_market_odds_and_predicoes.sql) -- mesmo padrão já
-- usado em `market_odds`, mantém `updated_at` correto em qualquer UPDATE
-- (inclusive o gerado por um upsert) sem depender de o cliente lembrar de
-- enviar o campo toda vez.
alter table public.models_registry
  add column if not exists updated_at timestamptz not null default now();

comment on column public.models_registry.updated_at is
  'Atualizado automaticamente (trigger) em todo UPDATE/upsert -- created_at NUNCA muda depois da primeira inserção, não serve pra saber se o modelo foi retreinado recentemente.';

drop trigger if exists trg_models_registry_updated_at on public.models_registry;
create trigger trg_models_registry_updated_at
  before update on public.models_registry
  for each row
  execute function public.set_updated_at();
