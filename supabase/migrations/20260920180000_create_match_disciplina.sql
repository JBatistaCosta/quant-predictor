-- =============================================================================
-- Migration: match_disciplina -- cartões (amarelo/vermelho, com a ponderação
-- real de liquidação) e faltas, por (partida, time), persistidos numa tabela
-- única em vez de recalculados ad-hoc a cada tela/script.
-- =============================================================================
-- Pedido do usuário (20/09): "quero que seja armazenado os cartões e faltas
-- em estatísticas individuais em cada jogo" -- depois de descobrir, ao
-- investigar a distinção amarelo/vermelho no alvo do cartoes_rf, que a
-- carteira restrita (Série B + bet365/betano) estava com o total de cartões
-- ZERADO em 84% das partidas sem match_events (106 de 126) -- confirmado
-- manualmente contra o FotMob ao vivo (match_id 110163, Ceará 1x3 Cuiabá:
-- FotMob mostra 3 cartões reais, o banco mostra 0-0 nas duas fontes
-- disponíveis). Ver AVISO CRÍTICO em model_betting_strategy.notas.
--
-- ESTA MIGRATION NÃO CORRIGE O BUG DE INGESTÃO -- não há dado real pra
-- recuperar nas fontes já ingeridas pras partidas afetadas (match_events
-- vazio, match_stats_fotmob zerado nas duas). O que ela faz:
--   1. Concentra num lugar só a mesma lógica de fallback que `dados_
--      historicos._carregar_total_cartoes_por_partida` já usa em Python
--      (match_events como fonte primária, match_stats_fotmob como
--      fallback), agora também separando amarelo de vermelho (`_TIPOS_
--      EVENTO_CARTAO` -- yellow_card/second_yellow_card/red_card) e
--      aplicando a ponderação real de liquidação (amarelo=1, vermelho=2,
--      onde "vermelho" já inclui o 2º amarelo que expulsa).
--   2. Marca explicitamente `fonte_cartoes='fallback_suspeito'` quando o
--      fallback bate 0-0 nos dois times -- o padrão que se mostrou muito
--      mais provável de ser dado faltando do que "jogo sem cartão nenhum"
--      (~84% das partidas de Série B recente nessa situação). Consumidores
--      (frontend, scripts de treino) devem tratar essa fonte como "sem
--      dado confiável", não como "zero cartões".
--   3. Adiciona faltas cometidas (`match_stats_fotmob.fouls_committed`,
--      que NÃO tem o mesmo bug de zeragem -- conferido caso a caso junto
--      com o achado acima, ex. match_id 110163 tinha fouls_committed=13/17
--      preenchido mesmo com cartões zerados).
--
-- PRÓXIMO PASSO (fora desta migration): investigar uma fonte alternativa
-- pra backfill real dos cartões nas partidas `fallback_suspeito` (ex.:
-- endpoint FotMob diferente do bloco `discipline` agregado, que é
-- especificamente o que zera -- ver ACHADOS anteriores em CONTEXTO_
-- PROJETO.md) antes de reabilitar a confiança em cartoes_rf.
-- =============================================================================

create table if not exists public.match_disciplina (
  id                        bigint generated always as identity primary key,
  match_id                  bigint  not null references public.matches(id) on delete cascade,
  team_id                   bigint  not null references public.teams(id),
  is_home                   boolean not null,
  cartoes_amarelos          integer not null default 0,
  cartoes_vermelhos_equiv   integer not null default 0,
  cartoes_total_flat        integer not null default 0,
  cartoes_total_ponderado   integer not null default 0,
  faltas_cometidas          integer,
  fonte_cartoes             text not null check (fonte_cartoes in ('match_events', 'fallback_fotmob', 'fallback_suspeito', 'sem_dado')),
  derivado_em               timestamptz not null default now(),

  constraint match_disciplina_key unique (match_id, team_id)
);

comment on table public.match_disciplina is
  'Cartões (amarelo/vermelho separados, com ponderação real de liquidação) e faltas cometidas, por (partida, time). Regerada por public.derivar_disciplina(). Fonte primária: match_events; fallback: match_stats_fotmob (marcado fallback_suspeito quando bate 0-0 nos dois times, padrão associado a dado faltando, não a jogo sem cartão -- ver comentário da migration).';
comment on column public.match_disciplina.cartoes_amarelos is
  'Conta só event_type=yellow_card (1º amarelo). O 2º amarelo (second_yellow_card) entra em cartoes_vermelhos_equiv, não aqui -- é o evento que expulsa, equivalente a vermelho pra pontuação.';
comment on column public.match_disciplina.cartoes_vermelhos_equiv is
  'second_yellow_card + red_card -- ambos resultam em expulsão. Vale 2 pontos na ponderação real de liquidação (amarelo=1, vermelho=2, teto de 3 por jogador).';
comment on column public.match_disciplina.cartoes_total_flat is
  'cartoes_amarelos + cartoes_vermelhos_equiv -- cada evento conta 1, é o jeito que dados_historicos._carregar_total_cartoes_por_partida conta hoje (SEM ponderar por cor). Mantido pra comparação/compatibilidade com o alvo já treinado.';
comment on column public.match_disciplina.cartoes_total_ponderado is
  '1×cartoes_amarelos + 2×cartoes_vermelhos_equiv -- a regra real de liquidação de mercados de cartões de apostas (print conferido pelo usuário, 20/09).';
comment on column public.match_disciplina.fonte_cartoes is
  'match_events = fonte primária confiável. fallback_fotmob = match_stats_fotmob usado por match_events estar vazio, com pelo menos 1 cartão registrado (razoavelmente confiável). fallback_suspeito = fallback bateu 0-0 nos dois times -- ~84% das vezes isso é dado faltando, não jogo limpo (achado 20/09). sem_dado = nenhuma das duas fontes tem qualquer linha pra essa partida.';

create index if not exists idx_match_disciplina_match on public.match_disciplina (match_id);
create index if not exists idx_match_disciplina_suspeito on public.match_disciplina (fonte_cartoes) where fonte_cartoes = 'fallback_suspeito';

-- =============================================================================
-- Função de derivação -- mesmo padrão de derivar_game_state/derivar_
-- formacoes_fotmob: p_match_ids NULL = tudo, array = só esse lote;
-- idempotente (delete+insert no escopo, nunca upsert parcial).
-- =============================================================================
create or replace function public.derivar_disciplina(p_match_ids bigint[] default null)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  v_gravadas integer;
begin
  if p_match_ids is not null then
    delete from match_disciplina where match_id = any(p_match_ids);
  else
    delete from match_disciplina;
  end if;

  -- Partidas com QUALQUER linha em match_events (não só cartão) -- mesmo
  -- gate de dados_historicos._partidas_com_match_events: usar ausência de
  -- evento de cartão como "0 cartões" seria pior que o bug que motivou a
  -- troca (rotularia todo jogo sem ingestão ainda como "sem cartão").
  create temp table tmp_com_eventos on commit drop as
    select distinct match_id
    from match_events
    where (p_match_ids is null or match_id = any(p_match_ids));

  create temp table tmp_via_eventos on commit drop as
    select
      e.match_id, e.team_id,
      count(*) filter (where e.event_type = 'yellow_card') as amarelo,
      count(*) filter (where e.event_type in ('second_yellow_card', 'red_card')) as vermelho_equiv
    from match_events e
    where e.event_type in ('yellow_card', 'second_yellow_card', 'red_card')
      and (p_match_ids is null or e.match_id = any(p_match_ids))
    group by e.match_id, e.team_id;

  create temp table tmp_via_fallback on commit drop as
    select
      s.match_id, s.team_id,
      coalesce(s.yellow_cards, 0) as amarelo,
      coalesce(s.red_cards, 0) as vermelho_equiv,
      coalesce(s.fouls_committed, null) as faltas
    from match_stats_fotmob s
    where (p_match_ids is null or s.match_id = any(p_match_ids))
      and s.match_id not in (select match_id from tmp_com_eventos);

  create temp table tmp_fallback_zerado on commit drop as
    select match_id
    from tmp_via_fallback
    group by match_id
    having sum(amarelo) = 0 and sum(vermelho_equiv) = 0;

  create temp table tmp_faltas on commit drop as
    select match_id, team_id, fouls_committed
    from match_stats_fotmob
    where (p_match_ids is null or match_id = any(p_match_ids));

  insert into match_disciplina (
    match_id, team_id, is_home,
    cartoes_amarelos, cartoes_vermelhos_equiv, cartoes_total_flat, cartoes_total_ponderado,
    faltas_cometidas, fonte_cartoes
  )
  select
    m.id, tm.team_id, (tm.team_id = m.home_team_id),
    tm.amarelo, tm.vermelho_equiv,
    tm.amarelo + tm.vermelho_equiv,
    tm.amarelo + 2 * tm.vermelho_equiv,
    f.fouls_committed,
    'match_events'
  from tmp_via_eventos tm
  join matches m on m.id = tm.match_id
  left join tmp_faltas f on f.match_id = tm.match_id and f.team_id = tm.team_id

  union all

  select
    m.id, tm.team_id, (tm.team_id = m.home_team_id),
    tm.amarelo, tm.vermelho_equiv,
    tm.amarelo + tm.vermelho_equiv,
    tm.amarelo + 2 * tm.vermelho_equiv,
    tm.faltas,
    case when fz.match_id is not null then 'fallback_suspeito' else 'fallback_fotmob' end
  from tmp_via_fallback tm
  join matches m on m.id = tm.match_id
  left join tmp_fallback_zerado fz on fz.match_id = tm.match_id;

  get diagnostics v_gravadas = row_count;
  return v_gravadas;
end;
$$;

comment on function public.derivar_disciplina(bigint[]) is
  'Deriva match_disciplina a partir de match_events (fonte primária) com fallback pra match_stats_fotmob (marcando fallback_suspeito quando bate 0-0 nos dois times). p_match_ids NULL = tudo; array = só esse lote. Idempotente (delete+insert no escopo).';

revoke all on function public.derivar_disciplina(bigint[]) from public, anon, authenticated;
grant execute on function public.derivar_disciplina(bigint[]) to service_role;

-- =============================================================================
-- RLS -- leitura pública, escrita só via service_role (a função roda com
-- security definer, então nem precisa de policy de escrita).
-- =============================================================================
alter table public.match_disciplina enable row level security;

drop policy if exists "match_disciplina_public_read" on public.match_disciplina;
create policy "match_disciplina_public_read"
  on public.match_disciplina
  for select
  to anon, authenticated
  using (true);
