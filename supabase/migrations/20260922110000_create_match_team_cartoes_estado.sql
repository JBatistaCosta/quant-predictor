-- =============================================================================
-- Migration: cartões por estado do jogo -- peça que faltava pra calibrar o
-- motor de Markov multi-evento com dado próprio (gol/chute já têm
-- match_team_game_state; cartão precisa do equivalente, contando
-- match_events em vez de match_shots_fotmob).
-- =============================================================================
-- MESMA REGRA da fase 2 (match_team_game_state, migration 20260904100000):
-- "time que toma cartão" e "time que toma cartão porque está perdendo e
-- precisa arriscar mais" produzem os mesmos números agregados e significam
-- coisas opostas -- sem separar por estado do placar, qualquer taxa de
-- cartão por minuto mistura disciplina com circunstância.
--
-- POR QUE NÃO REIMPLEMENTA derivar_game_state DO ZERO: os segmentos de
-- placar constante já estão corretos e testados ali (relógio monótono
-- corrigido na migration 20260905160000, 0 partidas mal ordenadas depois da
-- correção). Esta função recalcula os MESMOS segmentos (mesma fórmula de
-- fh_over/clock) em vez de tentar reconstruí-los a partir do agregado já
-- persistido -- é o padrão que o próprio projeto já usa: derivar_resposta_
-- evento também recalcula fh_over/clock de forma independente em vez de
-- compartilhar estado entre funções plpgsql (mais simples e auditável que
-- funções auxiliares table-returning encadeadas).
--
-- Cartão vermelho = red_card + second_yellow_card combinados (mesma
-- convenção de match_disciplina.cartoes_vermelhos_equiv).
-- =============================================================================

create table if not exists public.match_team_cartoes_estado (
  id                       bigint generated always as identity primary key,
  match_id                 bigint  not null references public.matches(id) on delete cascade,
  team_id                  bigint  not null references public.teams(id),
  is_home                  boolean not null,
  estado                   text    not null check (estado in ('perdendo','empatando','ganhando')),
  minutos                  numeric not null,
  cartoes_amarelos_pro     integer not null default 0,
  cartoes_vermelhos_pro    integer not null default 0,
  cartoes_amarelos_contra  integer not null default 0,
  cartoes_vermelhos_contra integer not null default 0,
  placar_confere           boolean not null,
  derivado_em              timestamptz not null default now(),

  constraint match_team_cartoes_estado_key unique (match_id, team_id, estado)
);

comment on table public.match_team_cartoes_estado is
  'Quanto tempo cada time passou perdendo/empatando/ganhando, e quantos cartões tomou/o adversário tomou nesse tempo. Espelha match_team_game_state, mas conta match_events em vez de match_shots_fotmob. Regerada por public.derivar_cartoes_estado().';
comment on column public.match_team_cartoes_estado.minutos is
  'Minutos que ESTE time passou neste estado -- mesmo denominador de match_team_game_state.minutos. Nunca somar cartões por estado sem dividir por minutos.';
comment on column public.match_team_cartoes_estado.cartoes_vermelhos_pro is
  'red_card + second_yellow_card combinados, tomados por este time enquanto estava neste estado.';
comment on column public.match_team_cartoes_estado.cartoes_vermelhos_contra is
  'Vermelhos tomados pelo ADVERSÁRIO (vantagem numérica deste time) enquanto ESTE time estava neste estado.';
comment on column public.match_team_cartoes_estado.placar_confere is
  'false = o placar reconstruído do shotmap (via match_goal_timeline) não bate com matches.home_goals/away_goals. Filtre por true antes de calibrar.';

create index if not exists idx_match_team_cartoes_estado_match on public.match_team_cartoes_estado (match_id);
create index if not exists idx_match_team_cartoes_estado_team  on public.match_team_cartoes_estado (team_id, estado);

alter table public.match_team_cartoes_estado enable row level security;

drop policy if exists "match_team_cartoes_estado_public_read" on public.match_team_cartoes_estado;
create policy "match_team_cartoes_estado_public_read"
  on public.match_team_cartoes_estado for select to anon, authenticated using (true);

-- =============================================================================
-- Função de derivação. Escopo: partidas com pelo menos um chute (mesma base
-- de derivar_game_state, já que precisamos do relógio/segmentos de placar
-- derivados de match_shots_fotmob) -- partida sem shotmap não tem como saber
-- em que estado o cartão aconteceu.
--
-- DELETE + INSERT no escopo (não upsert): mesma razão de derivar_game_state
-- -- reprocessar pode fazer um estado sumir (correção de gol muda os
-- segmentos), upsert deixaria linha velha para trás.
-- =============================================================================
create or replace function public.derivar_cartoes_estado(p_match_ids bigint[] default null)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  v_gravadas integer;
begin
  create temporary table tmp_escopo on commit drop as
  select distinct s.match_id
  from public.match_shots_fotmob s
  where s.period is distinct from 'PenaltyShootout'
    and (p_match_ids is null or s.match_id = any (p_match_ids));

  -- Mesmo relógio monótono de derivar_game_state/derivar_resposta_evento:
  -- o 2º tempo também começa contando do minuto 45, então sem deslocar pelo
  -- excedente dos acréscimos do 1º (fh_over) um cartão aos 45+3 ordenaria
  -- antes de lances do início do 2º tempo.
  create temporary table tmp_fh on commit drop as
  select s.match_id,
         greatest(0, max(s.minute + coalesce(s.minute_added,0)) - 45)::numeric as fh_over
  from public.match_shots_fotmob s
  join tmp_escopo e on e.match_id = s.match_id
  where s.period = 'FirstHalf' and s.minute is not null
  group by s.match_id;

  create temporary table tmp_fim on commit drop as
  select s.match_id,
         greatest(90 + max(coalesce(fh.fh_over,0)), max(s.minute + coalesce(s.minute_added,0)))::numeric as fim
  from public.match_shots_fotmob s
  join tmp_escopo e on e.match_id = s.match_id
  left join tmp_fh fh on fh.match_id = s.match_id
  where s.period is distinct from 'PenaltyShootout' and s.minute is not null
  group by s.match_id;

  -- Segmentos de placar constante: direto de match_goal_timeline, já
  -- persistido com clock monótono e placar por derivar_game_state -- não
  -- precisa reconstruir gol a partir do shotmap de novo.
  create temporary table tmp_segmentos on commit drop as
  with gols as (
    select t.match_id, t.clock, t.shot_id, t.placar_casa, t.placar_fora
    from public.match_goal_timeline t
    join tmp_escopo e on e.match_id = t.match_id
  )
  select
    f.match_id, 0::numeric as ini,
    coalesce((select min(g.clock) from gols g where g.match_id = f.match_id), f.fim)::numeric as fim,
    0::int as h, 0::int as a
  from tmp_fim f
  union all
  select
    g.match_id, g.clock::numeric,
    coalesce(lead(g.clock) over (partition by g.match_id order by g.clock, g.shot_id), f.fim)::numeric,
    g.placar_casa, g.placar_fora
  from gols g join tmp_fim f on f.match_id = g.match_id;

  create index on tmp_segmentos (match_id, ini, fim);

  create temporary table tmp_minutos on commit drop as
  select match_id, team_id, is_home, estado, sum(dur) as minutos
  from (
    select s.match_id, m.home_team_id as team_id, true as is_home,
           case when s.h > s.a then 'ganhando' when s.h < s.a then 'perdendo' else 'empatando' end as estado,
           greatest(s.fim - s.ini, 0) as dur
    from tmp_segmentos s join public.matches m on m.id = s.match_id
    union all
    select s.match_id, m.away_team_id, false,
           case when s.a > s.h then 'ganhando' when s.a < s.h then 'perdendo' else 'empatando' end,
           greatest(s.fim - s.ini, 0)
    from tmp_segmentos s join public.matches m on m.id = s.match_id
  ) x
  group by match_id, team_id, is_home, estado;

  -- Cartões com o mesmo relógio monótono (match_events não tem período nem
  -- acréscimo, só o minuto cru -- mesma regra já usada em
  -- derivar_resposta_evento pra expulsão).
  create temporary table tmp_cartoes_clock on commit drop as
  select ev.match_id, ev.team_id, ev.event_type,
         case when ev.minute <= 45 then ev.minute::numeric
              else ev.minute::numeric + coalesce(fh.fh_over,0) end as clock
  from public.match_events ev
  join tmp_escopo e on e.match_id = ev.match_id
  left join tmp_fh fh on fh.match_id = ev.match_id
  where ev.event_type in ('yellow_card','red_card','second_yellow_card')
    and ev.minute is not null and ev.team_id is not null;

  -- Estado do placar NO INSTANTE do cartão (join por intervalo, mesmo padrão
  -- de tmp_int_score em derivar_resposta_evento -- não subquery correlacionada).
  create temporary table tmp_cartoes_estado on commit drop as
  select c.match_id, c.team_id, c.event_type,
         case when c.team_id = m.home_team_id then s.h - s.a else s.a - s.h end as diff
  from tmp_cartoes_clock c
  join public.matches m on m.id = c.match_id
  join tmp_segmentos s
    on s.match_id = c.match_id and c.clock >= s.ini and c.clock < s.fim;

  create temporary table tmp_cartoes_agg on commit drop as
  select match_id, team_id,
    case when diff > 0 then 'ganhando' when diff < 0 then 'perdendo' else 'empatando' end as estado,
    count(*) filter (where event_type = 'yellow_card') as amarelos,
    count(*) filter (where event_type in ('red_card','second_yellow_card')) as vermelhos
  from tmp_cartoes_estado
  group by match_id, team_id,
    case when diff > 0 then 'ganhando' when diff < 0 then 'perdendo' else 'empatando' end;

  -- "Contra": cartão do adversário, do ponto de vista deste time (mesmo
  -- estado que o time sofredor estava, espelhado).
  create temporary table tmp_cartoes_contra on commit drop as
  select ca.match_id,
         case when ca.team_id = m.home_team_id then m.away_team_id else m.home_team_id end as team_id,
         case ca.estado when 'ganhando' then 'perdendo' when 'perdendo' then 'ganhando' else 'empatando' end as estado,
         ca.amarelos, ca.vermelhos
  from tmp_cartoes_agg ca
  join public.matches m on m.id = ca.match_id;

  create temporary table tmp_confere on commit drop as
  select e.match_id,
    coalesce(
      (select (t.placar_casa = m.home_goals and t.placar_fora = m.away_goals)
       from public.match_goal_timeline t
       where t.match_id = e.match_id order by t.clock desc, t.shot_id desc limit 1),
      (m.home_goals = 0 and m.away_goals = 0)
    ) as confere
  from tmp_escopo e join public.matches m on m.id = e.match_id
  where m.status = 'finished' and m.home_goals is not null;

  delete from public.match_team_cartoes_estado g
  where g.match_id in (select match_id from tmp_escopo);

  insert into public.match_team_cartoes_estado
    (match_id, team_id, is_home, estado, minutos,
     cartoes_amarelos_pro, cartoes_vermelhos_pro, cartoes_amarelos_contra, cartoes_vermelhos_contra, placar_confere)
  select
    mi.match_id, mi.team_id, mi.is_home, mi.estado, mi.minutos,
    coalesce(pro.amarelos, 0), coalesce(pro.vermelhos, 0),
    coalesce(contra.amarelos, 0), coalesce(contra.vermelhos, 0),
    coalesce(cf.confere, false)
  from tmp_minutos mi
  left join tmp_cartoes_agg pro
    on pro.match_id = mi.match_id and pro.team_id = mi.team_id and pro.estado = mi.estado
  left join tmp_cartoes_contra contra
    on contra.match_id = mi.match_id and contra.team_id = mi.team_id and contra.estado = mi.estado
  left join tmp_confere cf on cf.match_id = mi.match_id;

  get diagnostics v_gravadas = row_count;
  return v_gravadas;
end;
$$;

comment on function public.derivar_cartoes_estado(bigint[]) is
  'Deriva match_team_cartoes_estado a partir de match_events + os segmentos de placar de match_goal_timeline. p_match_ids NULL = tudo; array = só esse lote. Idempotente (delete+insert no escopo). Depende de derivar_game_state já ter rodado pra essas partidas (usa match_goal_timeline).';

revoke all on function public.derivar_cartoes_estado(bigint[]) from public, anon, authenticated;
grant execute on function public.derivar_cartoes_estado(bigint[]) to service_role;

-- =============================================================================
-- View de leitura -- perfil de cartão por time/estado, já normalizado por 90
-- minutos NAQUELE estado. Mesma disciplina de v_time_game_state: ninguém
-- precisa lembrar de dividir por minutos.
-- =============================================================================
create or replace view public.v_time_cartoes_estado
with (security_invoker = on) as
select
  g.team_id,
  m.league_id,
  m.season,
  g.estado,
  count(*)                                                             as partidas,
  round(sum(g.minutos), 1)                                             as minutos_totais,
  round(sum(g.cartoes_amarelos_pro)::numeric  / nullif(sum(g.minutos), 0) * 90, 3) as amarelos_tomados_por_90,
  round(sum(g.cartoes_vermelhos_pro)::numeric / nullif(sum(g.minutos), 0) * 90, 3) as vermelhos_tomados_por_90,
  round(sum(g.cartoes_amarelos_contra)::numeric  / nullif(sum(g.minutos), 0) * 90, 3) as amarelos_sofridos_pelo_rival_por_90,
  round(sum(g.cartoes_vermelhos_contra)::numeric / nullif(sum(g.minutos), 0) * 90, 3) as vermelhos_sofridos_pelo_rival_por_90
from public.match_team_cartoes_estado g
join public.matches m on m.id = g.match_id
where g.placar_confere
group by g.team_id, m.league_id, m.season, g.estado;

comment on view public.v_time_cartoes_estado is
  'Perfil de cartão de cada time por estado do jogo (liga/temporada), normalizado por 90 minutos NAQUELE estado. Só partidas com placar reconciliado. Controlar por força de equipe (Elo) antes de aceitar qualquer efeito de estado como real -- mesma armadilha já documentada para xG por estado.';
