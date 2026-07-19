-- =============================================================================
-- Migration: crosswalk jogador↔Transfermarkt + histórico de valor de mercado
-- =============================================================================
-- Fase 1 do enriquecimento "valor de jogador" discutido pro Model
-- Benchmarking: `players.market_value` só guarda o valor ATUAL (sem
-- histórico), o que impede usá-lo em features de partidas passadas sem
-- vazamento (olhar o valor de hoje pra prever um jogo de anos atrás). Essas
-- duas tabelas resolvem isso com uma série temporal de verdade, casada por
-- jogador via a mesma disciplina de crosswalk já usada em `team_source_ids`
-- (nunca reaproveitar `fotmob_player_id` como se fosse id do Transfermarkt —
-- são fontes diferentes, precisam de mapeamento próprio).
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Tabela 1: player_source_ids
-- Mesmo padrão de `team_source_ids`: uma linha por (player_id, source).
-- O matching (nome+clube+idade) é feito pelo script, nunca por heurística
-- automática sem revisão — ver `scripts/matching_transfermarkt.py`.
-- -----------------------------------------------------------------------------
create table if not exists public.player_source_ids (
  id           bigint generated always as identity primary key,
  player_id    bigint not null references public.players(id) on delete cascade,
  source       text not null,
  source_id    text not null,
  source_name  text,
  confianca    numeric(4, 3),
  created_at   timestamptz not null default now(),

  constraint player_source_ids_player_source_key unique (player_id, source)
);

comment on table public.player_source_ids is
  'Crosswalk jogador interno -> id de fonte externa (ex.: source=''transfermarkt''). confianca é o score do matching (nome+clube+idade), 0-1.';

create index if not exists idx_player_source_ids_source on public.player_source_ids (source, source_id);

-- -----------------------------------------------------------------------------
-- Tabela 2: player_market_value_history
-- Série temporal real (marketValueHistory da transfermarkt-api), não um
-- snapshot único — permite usar "valor na época do jogo" como feature sem
-- olhar o futuro.
-- -----------------------------------------------------------------------------
create table if not exists public.player_market_value_history (
  id              bigint generated always as identity primary key,
  player_id       bigint not null references public.players(id) on delete cascade,
  value_date      date not null,
  value           numeric(14, 2) not null check (value >= 0),
  currency        varchar(10) not null default 'EUR',
  age_at_date     smallint,
  club_name       text,
  source          text not null default 'transfermarkt',
  created_at      timestamptz not null default now(),

  constraint player_market_value_history_key unique (player_id, source, value_date)
);

comment on table public.player_market_value_history is
  'Série temporal de valor de mercado por jogador (fonte: Transfermarkt, via transfermarkt-api). Upsert por (player_id, source, value_date).';

create index if not exists idx_player_market_value_history_player_id on public.player_market_value_history (player_id, value_date);

-- =============================================================================
-- RLS — mesmo padrão do restante do projeto: leitura pública, escrita só via
-- service_role (GitHub Actions).
-- =============================================================================
alter table public.player_source_ids enable row level security;
alter table public.player_market_value_history enable row level security;

drop policy if exists "player_source_ids_public_read" on public.player_source_ids;
create policy "player_source_ids_public_read"
  on public.player_source_ids
  for select
  to anon, authenticated
  using (true);

drop policy if exists "player_market_value_history_public_read" on public.player_market_value_history;
create policy "player_market_value_history_public_read"
  on public.player_market_value_history
  for select
  to anon, authenticated
  using (true);

-- Nenhuma policy de insert/update/delete pra anon/authenticated: só o
-- service_role key grava, via o workflow de sync do Transfermarkt.
