-- =============================================================================
-- Migration: xi_titular_walkforward -- previsão de XI titular ponto-no-tempo
-- =============================================================================
-- Guarda a probabilidade de titular POR JOGADOR gerada por
-- scripts/backtest_xi_walkforward.py -- cada temporada é prevista por um
-- modelo treinado SÓ com dado estritamente anterior a ela (mesmo espírito do
-- walk-forward de scripts/walkforward_cv_v9.py), então é sem vazamento por
-- construção: representa "o que o modelo teria previsto ANTES da partida".
--
-- Até aqui (backtest_xi_walkforward.py) só gravava métrica agregada por
-- temporada+liga (xi_backtest_walkforward) e descartava a previsão bruta por
-- jogador. Essa tabela passa a persistir a previsão em si -- ela vira a
-- fonte de "abertura" (sinal de força do XI disponível ANTES da partida, sem
-- olhar a escalação real) pras features titular_rating_abertura/
-- titular_valor_mercado_abertura em dados_historicos.py, em contraste com
-- "fechamento" (escalação real confirmada, match_lineup_fotmob).
--
-- Deliberadamente separada de xi_previsto: aquela é a previsão AO VIVO da
-- versão de produção do modelo (scripts/rodar_xi_previsto.py, 1 modelo,
-- reprocessado a cada rodada); esta é a previsão HISTÓRICA de backtest (N
-- modelos descartáveis, 1 por temporada, nunca usada em predição ao vivo).
-- =============================================================================

create table if not exists public.xi_titular_walkforward (
  id                    bigint generated always as identity primary key,
  match_id              bigint not null references public.matches(id),
  team_id               bigint not null references public.teams(id),
  player_id             bigint not null references public.players(id),
  prob_titular          numeric not null,
  season                text not null,
  league_id             bigint not null,
  model_version         text not null,
  gerado_em             timestamptz not null default now(),

  constraint xi_titular_walkforward_match_team_player_key unique (match_id, team_id, player_id)
);

comment on table public.xi_titular_walkforward is
  'Probabilidade de titular por jogador prevista por scripts/backtest_xi_walkforward.py -- ponto-no-tempo por construção (cada temporada usa um modelo treinado só com dado anterior a ela). Fonte de "abertura" pras features titular_rating_abertura/titular_valor_mercado_abertura em dados_historicos.py, em contraste com "fechamento" (match_lineup_fotmob, escalação real).';

comment on column public.xi_titular_walkforward.model_version is
  'Identifica o run de backtest que gerou a linha (ex. "xi_titular_ensemble_walkforward", casa com xi_backtest_walkforward.model_version).';

create index if not exists idx_xi_titular_walkforward_match on public.xi_titular_walkforward (match_id);
create index if not exists idx_xi_titular_walkforward_team on public.xi_titular_walkforward (team_id);

-- =============================================================================
-- RLS -- mesmo padrão de xi_previsto: leitura pública, escrita só via
-- service_role (script rodado pelo workflow backtest_xi_walkforward.yml).
-- =============================================================================
alter table public.xi_titular_walkforward enable row level security;

drop policy if exists "xi_titular_walkforward_public_read" on public.xi_titular_walkforward;
create policy "xi_titular_walkforward_public_read"
  on public.xi_titular_walkforward
  for select
  to anon, authenticated
  using (true);
