-- =============================================================================
-- Migration: xi_backtest_walkforward -- backtest walk-forward do modelo de XI
-- =============================================================================
-- Guarda a saída de scripts/backtest_xi_walkforward.py: por temporada, o
-- modelo é RETREINADO do zero só com dado ESTRITAMENTE anterior àquela
-- temporada e avaliado nela (nunca vista) -- "fingir não saber" de verdade,
-- sem o vazamento que rodar o modelo de produção (treinado uma vez, split
-- fixo) sobre o histórico inteiro daria pras partidas que já estavam no
-- treino dele. Mesmo espírito do walk-forward já usado pro modelo de
-- resultado de partida (scripts/walkforward_cv_v9.py), aplicado ao
-- classificador de XI.
--
-- Deliberadamente SEPARADA de models_registry (aquela guarda o modelo de
-- PRODUÇÃO, um artefato por algoritmo, retreinado manualmente e usado ao
-- vivo por rodar_xi_previsto.py) -- os modelos treinados aqui são
-- descartáveis (um por temporada, só pra gerar a previsão de teste), nunca
-- persistidos como artefato, então não fazem sentido nessa tabela.
-- =============================================================================

create table if not exists public.xi_backtest_walkforward (
  id                      bigint generated always as identity primary key,
  season                  text not null,
  league_id               bigint not null references public.leagues(id),
  model_version           text not null default 'xi_titular_ensemble_walkforward',
  n_partidas              int not null,
  n_previsoes             int not null,
  precisao_media_top11    numeric,
  taxa_xi_exato           numeric,
  brier                   numeric,
  log_loss                numeric,
  calibracao              jsonb,
  gerado_em               timestamptz not null default now(),

  constraint xi_backtest_walkforward_temporada_liga_key unique (season, league_id, model_version)
);

comment on table public.xi_backtest_walkforward is
  'Backtest walk-forward por temporada do modelo de XI titular (scripts/backtest_xi_walkforward.py) -- retreina só com dado anterior a cada temporada e avalia nela, sem vazamento. Consumido por src/pages/XiModeloStats.jsx (seção "Backtest histórico").';

create index if not exists idx_xi_backtest_walkforward_season on public.xi_backtest_walkforward (season);
create index if not exists idx_xi_backtest_walkforward_league on public.xi_backtest_walkforward (league_id);

-- =============================================================================
-- RLS -- mesmo padrão de xi_previsto: leitura pública, escrita só via
-- service_role (script rodado manualmente via workflow_dispatch).
-- =============================================================================
alter table public.xi_backtest_walkforward enable row level security;

drop policy if exists "xi_backtest_walkforward_public_read" on public.xi_backtest_walkforward;
create policy "xi_backtest_walkforward_public_read"
  on public.xi_backtest_walkforward
  for select
  to anon, authenticated
  using (true);
