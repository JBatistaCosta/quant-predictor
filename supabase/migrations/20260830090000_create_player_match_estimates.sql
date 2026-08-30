-- =============================================================================
-- Migration: player_match_estimates -- chutes/gols PREVISTOS por jogador
-- =============================================================================
-- Guarda a saída de scripts/rodar_jogador_mercados_previsto.py: lambda de
-- chutes e gols (Poisson) previsto por jogador pra partidas ainda não
-- disputadas (`matches.status='scheduled'`) -- primeira peça da feature
-- "predição de chutes/gols por jogador" pedida pelo usuário.
--
-- `fonte_titular` guarda DUAS previsões por jogador x partida x modelo, de
-- propósito (pedido explícito do usuário durante a revisão do plano):
--   'previsto' -- gerada com antecedência (dias/horas antes), usando o XI
--                 PREVISTO (`xi_previsto`/`xi_titular_walkforward`, via
--                 dados_historicos.obter_titular_atual) quando a escalação
--                 oficial ainda não saiu.
--   'real'     -- gerada perto do apito, assim que
--                 scripts/ingerir_escalacao_pre_jogo.py captura a escalação
--                 OFICIAL (`match_lineup_fotmob`) -- minutos esperados
--                 deixam de ser uma mistura probabilística e passam a ser
--                 determinísticos por papel (titular/reserva confirmado).
-- Nenhuma sobrescreve a outra -- é isso que permite comparar depois se a
-- escalação confirmada de fato melhora a previsão (ver
-- backtest_jogador_mercados_walkforward.py e o plano da sessão).
--
-- `lambda_gols_jogo_thinning` (afinamento de Poisson: lambda_chutes x
-- taxa_conversao_bayesiana, fórmula fechada, sem modelo próprio) e
-- `lambda_gols_jogo_direto` (regressor Poisson treinado direto no alvo
-- gols_partida, candidato alternativo) coexistem de propósito -- o backtest
-- walk-forward decide empiricamente qual usar, não é fixado no schema.
--
-- Mercados são DERIVADOS no cliente a partir do lambda (over/under de
-- chutes via PoissonCDF, "marcar a qualquer momento" via 1-exp(-lambda)) --
-- mesmo padrão de `model_match_estimates`, nunca 1 linha persistida por
-- linha de aposta.
-- =============================================================================

create table if not exists public.player_match_estimates (
  id                          bigint generated always as identity primary key,
  match_id                    bigint not null references public.matches(id),
  team_id                     bigint not null references public.teams(id),
  player_id                   bigint not null references public.players(id),
  fonte_titular               text not null check (fonte_titular in ('previsto', 'real')),
  prob_titular_usada          numeric,
  minutos_esperados           numeric not null,
  taxa_conversao_bayesiana    numeric not null,
  lambda_chutes_jogo          numeric not null,
  lambda_gols_jogo_thinning   numeric not null,
  lambda_gols_jogo_direto     numeric,
  model_version               text not null,
  gerado_em                   timestamptz not null default now(),

  constraint player_match_estimates_key unique (match_id, team_id, player_id, model_version, fonte_titular)
);

comment on table public.player_match_estimates is
  'Chutes/gols PREVISTOS por jogador (lambda de Poisson) pra partidas agendadas, gerado por scripts/rodar_jogador_mercados_previsto.py. fonte_titular distingue a passada "previsto" (XI previsto, dias antes) da passada "real" (escalação oficial confirmada perto do apito) -- as duas coexistem, nunca uma sobrescreve a outra, pra permitir comparar se a confirmação melhora a previsão de verdade.';

comment on column public.player_match_estimates.fonte_titular is
  '"previsto" = XI ainda não confirmado (dados_historicos.obter_titular_atual caiu pro xi_previsto). "real" = escalação oficial já capturada em match_lineup_fotmob.';

comment on column public.player_match_estimates.lambda_gols_jogo_thinning is
  'lambda_chutes_jogo x taxa_conversao_bayesiana (afinamento de Poisson, fórmula fechada, sem modelo de ML próprio).';

comment on column public.player_match_estimates.lambda_gols_jogo_direto is
  'Regressor Poisson treinado direto no alvo gols_partida (candidato alternativo ao thinning) -- nulo se o modelo direto não tiver sido treinado/aplicado.';

create index if not exists idx_player_match_estimates_match on public.player_match_estimates (match_id);
create index if not exists idx_player_match_estimates_player on public.player_match_estimates (player_id);

-- =============================================================================
-- RLS -- mesmo padrão de xi_previsto: leitura pública, escrita só via
-- service_role (script rodado pelo workflow prever_jogador_mercados.yml e
-- pelo step novo em ingerir_escalacao_pre_jogo.yml).
-- =============================================================================
alter table public.player_match_estimates enable row level security;

drop policy if exists "player_match_estimates_public_read" on public.player_match_estimates;
create policy "player_match_estimates_public_read"
  on public.player_match_estimates
  for select
  to anon, authenticated
  using (true);
