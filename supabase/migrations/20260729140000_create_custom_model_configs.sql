-- Painel de Treino Customizado: armazena as configurações de modelos criados
-- pelo usuário via interface. Cada linha representa uma "receita" de treino
-- (algoritmo + features selecionadas + hiperparâmetros opcionais). O workflow
-- treinar_modelo_custom.yml lê esta tabela pelo config_id, treina e salva o
-- artefato, atualizando status/metrics/model_key/trained_at ao final.
-- O model_key aponta para o artefato serializado no storage (mesmo padrão
-- de models_registry.artifact_url), permitindo que o painel use o modelo
-- para novas predições sem recarregar.
create table custom_model_configs (
  id          uuid primary key default gen_random_uuid(),
  name        text not null,
  algorithm   text not null check (algorithm in (
                'catboost', 'xgboost', 'lightgbm',
                'logistic_regression', 'random_forest', 'dixon_coles'
              )),
  features    text[] not null,
  hyperparameters jsonb,                   -- opcional: { "n_estimators": 300, ... }
  target      text not null default '1x2', -- mercado alvo do modelo
  notes       text,                        -- anotações livres do usuário
  status      text not null default 'rascunho' check (status in (
                'rascunho', 'aguardando_treino', 'treinando',
                'treinado', 'erro'
              )),
  metrics     jsonb,                       -- métricas retornadas pelo workflow
  model_key   text,                        -- chave do artefato no storage
  error_message text,                      -- preenchido pelo workflow em caso de erro
  created_at  timestamptz not null default now(),
  trained_at  timestamptz                  -- preenchido pelo workflow ao concluir
);

create index idx_custom_model_configs_status on custom_model_configs (status);

comment on table custom_model_configs is
  'Configurações de modelos criados via painel de treino customizado. '
  'O workflow treinar_modelo_custom.yml lê pelo config_id, treina e atualiza '
  'status/metrics/model_key/trained_at. '
  'model_key referencia o artefato serializado no storage (mesmo padrão de '
  'models_registry.artifact_url).';

comment on column custom_model_configs.algorithm is
  'Algoritmo de ML. Deve ser um dos suportados por scripts/treinar_modelo_custom.py.';
comment on column custom_model_configs.features is
  'Lista de colunas/features selecionadas pelo usuário no painel de treino.';
comment on column custom_model_configs.hyperparameters is
  'Hiperparâmetros opcionais em JSON (ex: {"n_estimators": 300, "max_depth": 6}). '
  'Se null, o script usa os defaults definidos em PARAMS_DEFAULT.';
comment on column custom_model_configs.target is
  'Mercado alvo: "1x2" (resultado), "btts", "over_under", etc.';
comment on column custom_model_configs.status is
  'Ciclo de vida: rascunho → aguardando_treino → treinando → treinado | erro.';
comment on column custom_model_configs.metrics is
  'Métricas de avaliação retornadas pelo workflow ao concluir o treino '
  '(ex: {"accuracy": 0.61, "log_loss": 0.88, "roc_auc": 0.67}).';
comment on column custom_model_configs.model_key is
  'Chave do artefato serializado (.pkl / .cbm) no Supabase Storage ou GCS. '
  'Usado pelo painel para carregar o modelo e gerar predições online.';

alter table custom_model_configs enable row level security;

-- Leitura pública (anon pode ver configurações e métricas no painel)
create policy "custom_model_configs_public_read"
  on custom_model_configs for select
  to anon, authenticated
  using (true);

-- Escrita apenas para usuários autenticados (criar/editar configurações)
create policy "custom_model_configs_auth_write"
  on custom_model_configs for all
  to authenticated
  using (true)
  with check (true);
