-- =============================================================================
-- Migration: xi_previsto -- escalação (XI) PREVISTA por modelo, por partida
-- =============================================================================
-- Guarda a saída de scripts/rodar_xi_previsto.py: probabilidade de titular
-- prevista pra cada jogador do elenco atual de um time, pra partidas ainda
-- sem escalação oficial confirmada.
--
-- Deliberadamente uma tabela SEPARADA de match_lineup_fotmob -- aquela é o
-- ground-truth real (escalação oficial, capturada perto do apito ou depois
-- da partida), usado como alvo de treino do próprio modelo de XI
-- (scripts/treinar_modelo_xi.py). Gravar previsão ali misturaria dado
-- observado com dado inferido e corromperia qualquer retreino futuro.
--
-- model_version identifica o algoritmo/run que gerou a linha (ex.:
-- "xi_titular_lightgbm", casa com models_registry.name) -- permite reprocessar
-- com um modelo novo sem perder rastreabilidade de qual gerou cada previsão.
-- =============================================================================

create table if not exists public.xi_previsto (
  id                    bigint generated always as identity primary key,
  match_id              bigint not null references public.matches(id),
  team_id               bigint not null references public.teams(id),
  player_id             bigint not null references public.players(id),
  prob_titular          numeric not null,
  is_titular_previsto   boolean not null,
  model_version         text not null,
  gerado_em             timestamptz not null default now(),

  constraint xi_previsto_match_team_player_key unique (match_id, team_id, player_id)
);

comment on table public.xi_previsto is
  'Escalação (XI) PREVISTA por scripts/rodar_xi_previsto.py pra partidas ainda sem escalação oficial confirmada -- probabilidade de titular por jogador do elenco atual do time. Separada de match_lineup_fotmob (ground-truth real, usado pra treinar o modelo) de propósito, pra não misturar previsão com dado observado.';

comment on column public.xi_previsto.model_version is
  'Nome do modelo que gerou a linha (casa com models_registry.name, ex. "xi_titular_lightgbm") -- permite reprocessar com modelo novo sem perder rastreabilidade.';

create index if not exists idx_xi_previsto_match on public.xi_previsto (match_id);
create index if not exists idx_xi_previsto_team on public.xi_previsto (team_id);

-- =============================================================================
-- RLS -- mesmo padrão de match_lineup_fotmob: leitura pública, escrita só via
-- service_role (script de predição rodado pelo workflow prever_xi.yml).
-- =============================================================================
alter table public.xi_previsto enable row level security;

drop policy if exists "xi_previsto_public_read" on public.xi_previsto;
create policy "xi_previsto_public_read"
  on public.xi_previsto
  for select
  to anon, authenticated
  using (true);
