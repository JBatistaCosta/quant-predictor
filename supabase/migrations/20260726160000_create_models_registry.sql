-- Model Registry: única fonte de verdade sobre qual versão de modelo está em
-- produção por mercado, e o "passaporte" de cada uma (features usadas no
-- treino, hiperparâmetros, métricas de teste e o link do artefato
-- serializado, quando existir). Sem isso, "qual modelo tá rodando" e "quais
-- colunas ele espera" viviam só na memória de quem rodou o treino -- exatamente
-- o tipo de coisa que quebra silenciosamente meses depois, na hora do
-- refitting. `name`/`market` casam com `model_predictions.model_name`+
-- `market` e com `predicoes.model_name` (mercado implícito por tabela --
-- `predicoes` só cobre 1X2 hoje).
create table models_registry (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  market text not null,
  algorithm text not null,
  status text not null default 'testing' check (status in ('active', 'deprecated', 'testing')),
  features_used text[],
  hyperparameters jsonb,
  metrics_test jsonb,
  artifact_url text,
  created_at timestamptz not null default now(),
  unique (name, market)
);

create index idx_models_registry_market_status on models_registry (market, status);

comment on table models_registry is 'Passaporte de cada versão de modelo (nome+mercado): features usadas no treino, hiperparâmetros, métricas de teste e status em produção (active/deprecated/testing). Backfill inicial em arquivos_do_claude/sync_models_registry.py, a partir de scripts/modelos_ml.py (FEATURES_POR_MODELO/PARAMS_DEFAULT) e model_benchmarking_backtest (métricas).';

alter table models_registry enable row level security;
create policy "models_registry_public_read" on models_registry for select to anon, authenticated using (true);

-- Estimativa de xG por equipe feita pelo modelo, pra comparar com a
-- realidade (match_stats.xg). Só populada por modelos que calculam gols
-- esperados por equipe como etapa intermediária de verdade (Dixon-Coles/
-- Poisson) -- modelos de classificação pura (CatBoost/XGBoost/LightGBM
-- prevendo 1X2 direto) não entram aqui: back-derivar um "xG implícito" das
-- probabilidades seria aproximação, não o que o modelo de fato calculou.
create table model_match_estimates (
  id bigint generated always as identity primary key,
  match_id bigint not null references matches(id),
  model_name text not null,
  xg_home_previsto numeric,
  xg_away_previsto numeric,
  created_at timestamptz not null default now(),
  unique (match_id, model_name)
);

create index idx_model_match_estimates_match on model_match_estimates (match_id);

comment on table model_match_estimates is 'xG previsto por equipe (modelo) por partida, só pra modelos baseados em gols esperados (Dixon-Coles/Poisson) -- comparado com match_stats.xg (realidade) no relatório partida a partida.';

alter table model_match_estimates enable row level security;
create policy "model_match_estimates_public_read" on model_match_estimates for select to anon, authenticated using (true);
