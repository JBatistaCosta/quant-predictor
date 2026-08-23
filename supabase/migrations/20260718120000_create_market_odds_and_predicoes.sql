-- =============================================================================
-- Migration: Model Benchmarking — mercado de odds + saídas dos 4 modelos
-- =============================================================================
-- Estas tabelas alimentam o pipeline assíncrono (GitHub Actions, Python) do
-- painel de "Model Benchmarking": odds capturadas via The-Odds-API em
-- `market_odds` e as probabilidades/edge de cada um dos 4 modelos em
-- `predicoes`. São tabelas novas, propositalmente separadas de
-- `odds_market`/`model_predictions` (pipeline de produção já existente) —
-- não fazem upsert/join cruzado com aquelas.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Tabela 1: market_odds
-- Odds de abertura/fechamento por casa de apostas, uma linha por
-- (match_id, bookmaker). A constraint única composta permite UPSERT
-- (`on_conflict=match_id,bookmaker`) sem duplicar dado a cada execução do
-- pipeline — cada rodada apenas atualiza a linha existente.
-- -----------------------------------------------------------------------------
create table if not exists public.market_odds (
  id          bigint generated always as identity primary key,
  match_id    bigint not null references public.matches(id) on delete cascade,
  bookmaker   varchar(50) not null,
  odd_home    numeric(6, 3) not null check (odd_home > 1),
  odd_draw    numeric(6, 3) check (odd_draw > 1),
  odd_away    numeric(6, 3) not null check (odd_away > 1),
  updated_at  timestamptz not null default now(),

  constraint market_odds_match_bookmaker_key unique (match_id, bookmaker)
);

comment on table public.market_odds is
  'Odds de mercado (The-Odds-API) por partida/casa de apostas. Upsert por (match_id, bookmaker).';

create index if not exists idx_market_odds_match_id on public.market_odds (match_id);

-- Mantém updated_at correto em qualquer UPDATE (inclusive o gerado por um
-- UPSERT que colide com a constraint única acima) sem depender de o cliente
-- lembrar de enviar o campo toda vez.
create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists trg_market_odds_updated_at on public.market_odds;
create trigger trg_market_odds_updated_at
  before update on public.market_odds
  for each row
  execute function public.set_updated_at();

-- -----------------------------------------------------------------------------
-- Tabela 2: predicoes
-- Saída de cada um dos 4 modelos por partida. `model_name` diferencia os
-- modelos que competem lado a lado no painel (dixon_coles_v1, catboost_v1,
-- xgboost_v1, lightgbm_v1). Constraint única (match_id, model_name) permite
-- reprocessar uma partida sem duplicar a predição do mesmo modelo.
-- -----------------------------------------------------------------------------
create table if not exists public.predicoes (
  id              bigint generated always as identity primary key,
  match_id        bigint not null references public.matches(id) on delete cascade,
  model_name      varchar(50) not null,
  prob_home       numeric(6, 5) not null check (prob_home between 0 and 1),
  prob_draw       numeric(6, 5) not null check (prob_draw between 0 and 1),
  prob_away       numeric(6, 5) not null check (prob_away between 0 and 1),
  edge_detectado  numeric(6, 5),
  created_at      timestamptz not null default now(),

  constraint predicoes_match_model_key unique (match_id, model_name)
);

comment on table public.predicoes is
  'Probabilidades 1X2 geradas por cada modelo do benchmarking + maior edge encontrado contra a melhor odd de market_odds.';

create index if not exists idx_predicoes_match_id on public.predicoes (match_id);
create index if not exists idx_predicoes_model_name on public.predicoes (model_name);

-- =============================================================================
-- RLS — mesmo padrão já usado no restante do pipeline (`teams`, `matches`,
-- `odds_market`, `model_predictions` etc.): leitura pública (dado não
-- sensível), escrita restrita ao service_role key usado pelo GitHub Actions.
-- =============================================================================
alter table public.market_odds enable row level security;
alter table public.predicoes enable row level security;

drop policy if exists "market_odds_public_read" on public.market_odds;
create policy "market_odds_public_read"
  on public.market_odds
  for select
  to anon, authenticated
  using (true);

drop policy if exists "predicoes_public_read" on public.predicoes;
create policy "predicoes_public_read"
  on public.predicoes
  for select
  to anon, authenticated
  using (true);

-- Nenhuma policy de insert/update/delete é criada para anon/authenticated:
-- só o service_role key (que ignora RLS por padrão) grava nessas tabelas,
-- via `scripts/rodar_predicoes.py` rodando no GitHub Actions.
