-- =============================================================================
-- Migration: carteira_cartoes_rf_historico -- ledger aposta a aposta da
-- carteira cronológica restrita de cartoes_rf (Brasileirão Série B +
-- bet365/betano, linhas O/U 4.5 e 5.5, edge>=25%) já registrada em
-- model_betting_strategy (sub_faixa 'rf_confiavel_odd1.30-2.50_edge25+_
-- serieB_bet365betano', confianca='alta').
-- =============================================================================
-- Pedido do usuário: ver os dados da carteira no frontend (aba dedicada em
-- Sugestões de Valor) -- até agora essa carteira só existia de forma
-- transiente dentro da execução do script `analisar_cartoes_liga_sinal.py`
-- (log do GitHub Actions, perdido quando o run expira), sem nenhuma linha
-- persistida. Guarda uma linha por aposta da simulação COMBINADA (as 2
-- linhas 4.5+5.5 juntas, mesma banca compartilhada em ordem cronológica --
-- é essa simulação, não as 2 separadas, que corresponde a "a carteira que
-- eu teria rodado de verdade").
--
-- `sub_faixa` identifica QUAL execução da carteira restrita gerou a linha
-- (hoje só existe uma: a combinada) -- reexecutar o workflow no mesmo dia
-- apaga e recria todas as linhas dessa sub_faixa (ver
-- `persistir_carteira_restrita` em analisar_cartoes_liga_sinal.py), não é
-- upsert incremental por partida.
-- =============================================================================

create table if not exists public.carteira_cartoes_rf_historico (
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

  constraint carteira_cartoes_rf_historico_key
    unique (sub_faixa, match_id, mercado, selecao)
);

comment on table public.carteira_cartoes_rf_historico is
  'Ledger aposta a aposta (não só o resumo agregado) da carteira cronológica restrita de cartoes_rf (Série B + bet365/betano, O/U 4.5/5.5, edge>=25%) -- alimenta a aba "Carteira" em Sugestões de Valor (src/pages/ResumoValorApostas.jsx). Gerado por scripts/analisar_cartoes_liga_sinal.py.';

comment on column public.carteira_cartoes_rf_historico.odd_justa is
  '1/prob_modelo -- odd que zeraria o EV do modelo pra essa seleção (não é a odd real oferecida pela casa).';

comment on column public.carteira_cartoes_rf_historico.prob_devig is
  'Probabilidade implícita do mercado, devigada (backtest_kelly._devig_odds_ratio, mesma convenção do resto do projeto) a partir de odd_over/odd_under da MELHOR odd real capturada. Pode ser nula se alguma das duas pontas do mercado não tinha odd real capturada.';

comment on column public.carteira_cartoes_rf_historico.stake_pct is
  'Fração de Kelly (backtest_kelly.kelly_fracionario) da banca ATUAL no momento da aposta -- não da banca inicial. 0 nunca aparece aqui (apostas com stake=0 não entram na carteira, ver simular_carteira_cronologica_detalhada).';

comment on column public.carteira_cartoes_rf_historico.ordem is
  'Posição da aposta na sequência cronológica da carteira (0-based) -- usado pra reconstruir a ordem exata sem depender só de match_date, que pode empatar entre partidas do mesmo dia.';

create index if not exists idx_carteira_cartoes_rf_historico_sub_faixa
  on public.carteira_cartoes_rf_historico (sub_faixa, ordem);

create index if not exists idx_carteira_cartoes_rf_historico_match
  on public.carteira_cartoes_rf_historico (match_id);

-- =============================================================================
-- RLS -- mesmo padrão do resto do pipeline: leitura pública (frontend em
-- /sugestoes-valor), escrita só via service_role (o workflow
-- analisar_cartoes_liga_sinal.yml roda com o secret SUPABASE_KEY do GitHub
-- Actions, que já é a service_role key neste repo).
-- =============================================================================
alter table public.carteira_cartoes_rf_historico enable row level security;

drop policy if exists "carteira_cartoes_rf_historico_public_read" on public.carteira_cartoes_rf_historico;
create policy "carteira_cartoes_rf_historico_public_read"
  on public.carteira_cartoes_rf_historico
  for select
  to anon, authenticated
  using (true);
