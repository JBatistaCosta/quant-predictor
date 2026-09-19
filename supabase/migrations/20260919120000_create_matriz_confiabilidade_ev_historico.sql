-- =============================================================================
-- Migration: matriz_confiabilidade_ev_historico -- acompanhamento diário da
-- matriz odd x edge de scripts/matriz_confiabilidade_ev.py
-- =============================================================================
-- Pedido do usuário: acompanhar no frontend, dia a dia, se as células "odd x
-- edge" que batem o mercado de forma repetível (ver CONTEXTO_PROJETO.md,
-- achado de Cartões linhas 4.5/5.5 sobrevivendo a Bonferroni/FDR/Diebold-
-- Mariano) continuam confiáveis conforme mais partidas entram no banco --
-- até agora o script só imprimia em log do GitHub Actions (perdido depois
-- que o run expira), sem persistência nenhuma.
--
-- Uma linha por (data_execucao, modelo, mercado, faixa de odd, faixa de
-- edge) -- upsert nessa chave permite reexecutar o workflow no mesmo dia
-- sem duplicar. `odd_max`/`edge_max` usam um SENTINELA numérico pra
-- representar "sem teto" (a última faixa de cada grade é aberta, ver
-- ODD_BUCKETS/EDGE_BUCKETS no script) em vez de NULL -- NULL quebraria a
-- unicidade da chave (Postgres trata NULL <> NULL), então:
--   odd_max  = 999.0 significa a faixa "8.00+" (sem teto).
--   edge_max = 9.99  significa a faixa "25%+" (sem teto).
--
-- `confiavel` é o critério "rigoroso" já decidido com o usuário (IC95% da
-- média E da mediana do ROI positivos, n>=50); `bonferroni_significativo`/
-- `fdr_significativo` só fazem sentido quando `confiavel=true` (a correção
-- múltipla é aplicada sobre as células candidatas, ver
-- corrigir_multiplas_comparacoes no script).
-- =============================================================================

create table if not exists public.matriz_confiabilidade_ev_historico (
  id                          bigint generated always as identity primary key,
  data_execucao               date not null,
  modelo                      text not null,
  mercado                     text not null,
  odd_min                     numeric not null,
  odd_max                     numeric not null,
  edge_min                    numeric not null,
  edge_max                    numeric not null,
  n                           integer not null,
  roi_medio                   numeric not null,
  roi_medio_ic_inf            numeric not null,
  roi_medio_ic_sup            numeric not null,
  p_media                     numeric not null,
  roi_mediano                 numeric not null,
  roi_mediano_ic_inf          numeric not null,
  roi_mediano_ic_sup          numeric not null,
  p_mediana                   numeric not null,
  confiavel                   boolean not null,
  dm_stat                     numeric not null,
  dm_p_valor                  numeric not null,
  p_celula                    numeric not null,
  bonferroni_significativo    boolean not null,
  fdr_significativo           boolean not null,
  carteira_banca_final_x      numeric not null,
  carteira_drawdown_maximo    numeric not null,
  carteira_n_apostado         integer not null,
  criado_em                   timestamptz not null default now(),

  constraint matriz_confiabilidade_ev_historico_key
    unique (data_execucao, modelo, mercado, odd_min, odd_max, edge_min, edge_max)
);

comment on table public.matriz_confiabilidade_ev_historico is
  'Snapshot diário (workflow matriz_confiabilidade_ev.yml, cron + workflow_dispatch) da matriz odd x edge de scripts/matriz_confiabilidade_ev.py -- acompanha se as células "confiáveis" (IC95% média e mediana do ROI positivos, n>=50) continuam sobrevivendo a Diebold-Mariano/Bonferroni/FDR conforme mais partidas entram no banco.';

comment on column public.matriz_confiabilidade_ev_historico.odd_max is
  'Sentinela 999.0 = faixa sem teto (odd 8.00+, ver ODD_BUCKETS no script) -- não é NULL de propósito, pra não quebrar a unicidade da chave.';

comment on column public.matriz_confiabilidade_ev_historico.edge_max is
  'Sentinela 9.99 = faixa sem teto (edge 25%+, ver EDGE_BUCKETS no script) -- mesma razão de odd_max.';

create index if not exists idx_matriz_confiabilidade_ev_historico_data
  on public.matriz_confiabilidade_ev_historico (data_execucao desc);

create index if not exists idx_matriz_confiabilidade_ev_historico_confiavel
  on public.matriz_confiabilidade_ev_historico (confiavel, data_execucao)
  where confiavel;

-- =============================================================================
-- RLS -- mesmo padrão do resto do pipeline: leitura pública (frontend em
-- /modelos), escrita só via service_role (o workflow roda com o secret
-- SUPABASE_KEY do GitHub Actions, que já é a service_role key neste repo --
-- ver outros scripts write como ingerir_escalacao_pre_jogo.py).
-- =============================================================================
alter table public.matriz_confiabilidade_ev_historico enable row level security;

drop policy if exists "matriz_confiabilidade_ev_historico_public_read" on public.matriz_confiabilidade_ev_historico;
create policy "matriz_confiabilidade_ev_historico_public_read"
  on public.matriz_confiabilidade_ev_historico
  for select
  to anon, authenticated
  using (true);
