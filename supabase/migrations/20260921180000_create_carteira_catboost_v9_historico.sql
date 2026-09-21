-- =============================================================================
-- Migration: carteira_catboost_v9_historico -- ledger aposta a aposta da
-- carteira cronológica de catboost_v9 restrita à única célula confiavel=true
-- da matriz de confiabilidade EV: mandante, 1X2, odd [1.30,2.50), edge
-- [5%,10%) -- achado 21/09 (n=699 na célula agregada mandante+visitante,
-- mas a quebra por seleção mostra que o efeito é só do mandante: n=502,
-- ROI+11,4%, positivo nas 6 ligas do benchmarking, se sustenta incluindo
-- dados de 2026 fora da amostra de treino original). Registrado em
-- model_betting_strategy (mercado '1x2_mandante', sub_faixa
-- 'catboost_v9_odd1.30-2.50_edge5-10').
-- =============================================================================
-- Mesmo formato de `carteira_cartoes_rf_historico` (mesmo padrão já
-- estabelecido no projeto pra ledger jogo-a-jogo de uma carteira restrita) --
-- tabela própria em vez de reaproveitar aquela porque o nome já é
-- cartões-específico; estrutura idêntica.
-- =============================================================================

create table if not exists public.carteira_catboost_v9_historico (
  id             uuid primary key default gen_random_uuid(),
  sub_faixa      text not null,
  match_id       bigint not null references public.matches(id),
  match_date     timestamptz not null,
  mercado        text not null,
  linha          numeric not null,
  selecao        text not null,
  bookmaker      text,
  prob_modelo    numeric not null,
  odd_justa      numeric not null,
  odd_real       numeric not null,
  prob_devig     numeric,
  edge           numeric not null,
  ev             numeric not null,
  stake_pct      numeric not null,
  acertou        boolean not null,
  banca_antes    numeric not null,
  banca_depois   numeric not null,
  ordem          integer not null,
  criado_em      timestamptz not null default now(),

  constraint carteira_catboost_v9_historico_key
    unique (sub_faixa, match_id, mercado, selecao)
);

comment on table public.carteira_catboost_v9_historico is
  'Ledger aposta a aposta (não só o resumo agregado) da carteira cronológica de catboost_v9 restrita a mandante/1X2/odd 1.30-2.50/edge 5-10% (única célula confiavel=true da matriz de confiabilidade EV pra esse modelo) -- alimenta a aba "Carteira catboost_v9" em Sugestões de Valor (src/pages/ResumoValorApostas.jsx). Gerado por scripts/analisar_catboost_v9_liga_sinal.py.';

comment on column public.carteira_catboost_v9_historico.linha is
  '1X2 não tem uma linha real como cartões/escanteios O/U -- por convenção grava o piso da faixa de odd usada no filtro (1.30), documentado aqui pra não ficar implícito só no código do script.';

comment on column public.carteira_catboost_v9_historico.odd_justa is
  '1/prob_modelo -- odd que zeraria o EV do modelo pra essa seleção (não é a odd real oferecida pela casa).';

comment on column public.carteira_catboost_v9_historico.prob_devig is
  'Probabilidade implícita do mercado 1X2, devigada (backtest_kelly._devig_odds_ratio) a partir das odds reais de mandante/empate/visitante da MELHOR captura -- nula se alguma das 3 pontas não tinha odd real capturada.';

comment on column public.carteira_catboost_v9_historico.stake_pct is
  'Fração de Kelly (backtest_kelly.kelly_fracionario) da banca ATUAL no momento da aposta -- não da banca inicial. 0 nunca aparece aqui (apostas com stake=0 não entram na carteira).';

comment on column public.carteira_catboost_v9_historico.ordem is
  'Posição da aposta na sequência cronológica da carteira (0-based) -- usado pra reconstruir a ordem exata sem depender só de match_date, que pode empatar entre partidas do mesmo dia.';

create index if not exists idx_carteira_catboost_v9_historico_sub_faixa
  on public.carteira_catboost_v9_historico (sub_faixa, ordem);

create index if not exists idx_carteira_catboost_v9_historico_match
  on public.carteira_catboost_v9_historico (match_id);

-- =============================================================================
-- RLS -- mesmo padrão do resto do pipeline: leitura pública, escrita só via
-- service_role.
-- =============================================================================
alter table public.carteira_catboost_v9_historico enable row level security;

drop policy if exists "carteira_catboost_v9_historico_public_read" on public.carteira_catboost_v9_historico;
create policy "carteira_catboost_v9_historico_public_read"
  on public.carteira_catboost_v9_historico
  for select
  to anon, authenticated
  using (true);
