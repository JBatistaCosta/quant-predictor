-- =============================================================================
-- Migration: cartões em match_team_event_response -- estende a resposta a
-- evento (transiente pós-gol/pós-expulsão) pra também contar cartões, além
-- de chutes/xG. Necessário pra calibrar mult_vantagem_numerica e
-- mult_janela_pos_evento de cartão com dado próprio (Fase 1 do motor de
-- Markov multi-evento).
-- =============================================================================
-- Reaproveita INTEIRAMENTE os intervalos (tmp_int_fim: partida × time × ini ×
-- fim × evento × janela × estado) que derivar_resposta_evento já calcula
-- pra chutes -- só adiciona um join contra match_events com o mesmo relógio
-- monótono já usado no cálculo de tmp_ev (cartão não tem período/acréscimo,
-- só minuto cru; do 2º tempo em diante desloca por fh_over).
-- =============================================================================

alter table public.match_team_event_response
  add column if not exists cartoes_amarelos_pro     integer not null default 0,
  add column if not exists cartoes_vermelhos_pro     integer not null default 0,
  add column if not exists cartoes_amarelos_contra   integer not null default 0,
  add column if not exists cartoes_vermelhos_contra  integer not null default 0;

comment on column public.match_team_event_response.cartoes_amarelos_pro is
  'Amarelos tomados por este time dentro da janela/estado, mesmo denominador minutos.';
comment on column public.match_team_event_response.cartoes_vermelhos_pro is
  'red_card + second_yellow_card combinados, tomados por este time dentro da janela/estado.';
comment on column public.match_team_event_response.cartoes_amarelos_contra is
  'Amarelos tomados pelo ADVERSÁRIO dentro da mesma janela/estado deste time.';
comment on column public.match_team_event_response.cartoes_vermelhos_contra is
  'Vermelhos (red_card + second_yellow_card) tomados pelo ADVERSÁRIO dentro da mesma janela/estado deste time.';

create or replace function public.derivar_resposta_evento(p_match_ids bigint[] default null)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  v_gravadas integer;
begin
  create temporary table tmp_esc on commit drop as
  select distinct g.match_id from public.match_team_game_state g
  where (p_match_ids is null or g.match_id = any (p_match_ids));

  create temporary table tmp_fh on commit drop as
  select s.match_id, greatest(0, max(s.minute + coalesce(s.minute_added,0)) - 45)::numeric as fh_over
  from public.match_shots_fotmob s
  join tmp_esc e on e.match_id = s.match_id
  where s.period = 'FirstHalf' and s.minute is not null
  group by s.match_id;

  create temporary table tmp_clock on commit drop as
  select s.id, s.match_id, s.team_id, s.xg, s.is_own_goal, s.period,
         case when s.period = 'FirstHalf'
              then (s.minute + coalesce(s.minute_added,0))::numeric
              else (s.minute + coalesce(s.minute_added,0))::numeric + coalesce(fh.fh_over, 0)
         end as clock
  from public.match_shots_fotmob s
  join tmp_esc e on e.match_id = s.match_id
  left join tmp_fh fh on fh.match_id = s.match_id
  where s.period is distinct from 'PenaltyShootout' and s.minute is not null;

  create index on tmp_clock (match_id, clock);

  -- TODOS os cartões (amarelo + vermelho + segundo amarelo), com o mesmo
  -- relógio monótono do resto da função -- tmp_ev abaixo usa só vermelho
  -- (é o que dispara janela de "expulsão"); esta tabela nova é pra CONTAR
  -- cartão como produção da janela, igual chute/xG já contam.
  create temporary table tmp_cartoes_clock on commit drop as
  select ev.match_id, ev.team_id, ev.event_type,
         case when ev.minute <= 45 then ev.minute::numeric
              else ev.minute::numeric + coalesce(fh.fh_over,0) end as clock
  from public.match_events ev
  join tmp_esc e on e.match_id = ev.match_id
  left join tmp_fh fh on fh.match_id = ev.match_id
  where ev.event_type in ('yellow_card','red_card','second_yellow_card')
    and ev.minute is not null and ev.team_id is not null;

  create index on tmp_cartoes_clock (match_id, clock);

  create temporary table tmp_fim on commit drop as
  select c.match_id, greatest(90 + coalesce(max(fh.fh_over),0), max(c.clock))::numeric as fim
  from tmp_clock c left join tmp_fh fh on fh.match_id = c.match_id
  group by c.match_id;

  create temporary table tmp_ev on commit drop as
  select t.match_id,
         case when t.para_casa then m.home_team_id else m.away_team_id end as team_id,
         t.clock as minuto, 'marcou'::text as evento
  from public.match_goal_timeline t
  join tmp_esc e on e.match_id = t.match_id
  join public.matches m on m.id = t.match_id
  union all
  select t.match_id,
         case when t.para_casa then m.away_team_id else m.home_team_id end,
         t.clock, 'sofreu'
  from public.match_goal_timeline t
  join tmp_esc e on e.match_id = t.match_id
  join public.matches m on m.id = t.match_id
  union all
  select ev.match_id, ev.team_id,
         case when ev.minute <= 45 then ev.minute::numeric
              else ev.minute::numeric + coalesce(fh.fh_over,0) end,
         'expulsao_pro'
  from public.match_events ev
  join tmp_esc e on e.match_id = ev.match_id
  left join tmp_fh fh on fh.match_id = ev.match_id
  where ev.event_type in ('red_card','second_yellow_card')
    and ev.minute is not null and ev.team_id is not null
  union all
  select ev.match_id,
         case when ev.team_id = m.home_team_id then m.away_team_id else m.home_team_id end,
         case when ev.minute <= 45 then ev.minute::numeric
              else ev.minute::numeric + coalesce(fh.fh_over,0) end,
         'expulsao_contra'
  from public.match_events ev
  join tmp_esc e on e.match_id = ev.match_id
  join public.matches m on m.id = ev.match_id
  left join tmp_fh fh on fh.match_id = ev.match_id
  where ev.event_type in ('red_card','second_yellow_card')
    and ev.minute is not null and ev.team_id is not null
    and ev.team_id in (m.home_team_id, m.away_team_id);

  create index on tmp_ev (match_id, team_id, minuto);

  create temporary table tmp_times on commit drop as
  select e.match_id, m.home_team_id as team_id from tmp_esc e join public.matches m on m.id = e.match_id
  union all
  select e.match_id, m.away_team_id from tmp_esc e join public.matches m on m.id = e.match_id;

  create temporary table tmp_cortes on commit drop as
  select x.match_id, x.team_id, x.ponto
  from (
    select t.match_id, t.team_id, 0::numeric as ponto from tmp_times t
    union
    select t.match_id, t.team_id, f.fim from tmp_times t join tmp_fim f on f.match_id = t.match_id
    union
    select t.match_id, t.team_id, gt.clock::numeric
    from tmp_times t join public.match_goal_timeline gt on gt.match_id = t.match_id
    union
    select v.match_id, v.team_id, v.minuto from tmp_ev v
    union
    select v.match_id, v.team_id, v.minuto + 5 from tmp_ev v
    union
    select v.match_id, v.team_id, v.minuto + 15 from tmp_ev v
  ) x
  join tmp_fim f on f.match_id = x.match_id
  where x.ponto >= 0 and x.ponto <= f.fim;

  create temporary table tmp_int on commit drop as
  select match_id, team_id, ponto as ini, prox as fim
  from (
    select c.match_id, c.team_id, c.ponto,
           lead(c.ponto) over (partition by c.match_id, c.team_id order by c.ponto) as prox
    from tmp_cortes c
  ) y
  where prox is not null and prox > ponto;

  create index on tmp_int (match_id, team_id, ini);

  create temporary table tmp_gols_time on commit drop as
  select t.match_id,
         case when t.para_casa then m.home_team_id else m.away_team_id end as team_pro,
         case when t.para_casa then m.away_team_id else m.home_team_id end as team_contra,
         t.clock::numeric as minuto
  from public.match_goal_timeline t
  join tmp_esc e on e.match_id = t.match_id
  join public.matches m on m.id = t.match_id;

  create index on tmp_gols_time (match_id, minuto);

  create temporary table tmp_int_score on commit drop as
  select i.match_id, i.team_id, i.ini, i.fim,
         count(g.minuto) filter (where g.team_pro    = i.team_id) as pro,
         count(g.minuto) filter (where g.team_contra = i.team_id) as contra
  from tmp_int i
  left join tmp_gols_time g on g.match_id = i.match_id and g.minuto <= i.ini
  group by i.match_id, i.team_id, i.ini, i.fim;

  create temporary table tmp_int_ev on commit drop as
  select distinct on (i.match_id, i.team_id, i.ini)
         i.match_id, i.team_id, i.ini, v.evento, v.minuto as ev_minuto
  from tmp_int i
  left join tmp_ev v on v.match_id = i.match_id and v.team_id = i.team_id and v.minuto <= i.ini
  order by i.match_id, i.team_id, i.ini, v.minuto desc nulls last;

  create temporary table tmp_int_fim on commit drop as
  select
    sc.match_id, sc.team_id, sc.ini, sc.fim,
    case when sc.pro > sc.contra then 'ganhando'
         when sc.pro < sc.contra then 'perdendo'
         else 'empatando' end as estado,
    case when ev.ev_minuto is null or (sc.ini - ev.ev_minuto) >= 15 then 'nenhum'
         else coalesce(ev.evento, 'nenhum') end as evento,
    case when ev.ev_minuto is null then 'regime'
         when sc.ini - ev.ev_minuto < 5  then '0-5'
         when sc.ini - ev.ev_minuto < 15 then '5-15'
         else 'regime' end as janela
  from tmp_int_score sc
  left join tmp_int_ev ev on ev.match_id = sc.match_id and ev.team_id = sc.team_id and ev.ini = sc.ini;

  create index on tmp_int_fim (match_id, ini, fim);

  create temporary table tmp_ch on commit drop as
  select
    it.match_id, it.team_id, it.evento, it.janela, it.estado,
    count(*) filter (where s.team_id = it.team_id)  as chutes_pro,
    coalesce(sum(coalesce(s.xg,0)) filter (where s.team_id = it.team_id), 0)  as xg_pro,
    count(*) filter (where s.team_id <> it.team_id) as chutes_contra,
    coalesce(sum(coalesce(s.xg,0)) filter (where s.team_id <> it.team_id), 0) as xg_contra
  from tmp_int_fim it
  join tmp_clock s
    on s.match_id = it.match_id
   and not coalesce(s.is_own_goal, false)
   and ( s.clock > it.ini or (it.ini = 0 and s.clock = 0) )
   and s.clock <= it.fim
  group by it.match_id, it.team_id, it.evento, it.janela, it.estado;

  -- Cartões dentro do mesmo intervalo -- mesma condição de fronteira dos
  -- chutes (> ini, <= fim, com a exceção do primeiro intervalo começando em 0).
  create temporary table tmp_cartoes_ch on commit drop as
  select
    it.match_id, it.team_id, it.evento, it.janela, it.estado,
    count(*) filter (where c.team_id = it.team_id and c.event_type = 'yellow_card') as cartoes_amarelos_pro,
    count(*) filter (where c.team_id = it.team_id and c.event_type in ('red_card','second_yellow_card')) as cartoes_vermelhos_pro,
    count(*) filter (where c.team_id <> it.team_id and c.event_type = 'yellow_card') as cartoes_amarelos_contra,
    count(*) filter (where c.team_id <> it.team_id and c.event_type in ('red_card','second_yellow_card')) as cartoes_vermelhos_contra
  from tmp_int_fim it
  join tmp_cartoes_clock c
    on c.match_id = it.match_id
   and ( c.clock > it.ini or (it.ini = 0 and c.clock = 0) )
   and c.clock <= it.fim
  group by it.match_id, it.team_id, it.evento, it.janela, it.estado;

  delete from public.match_team_event_response r
  where r.match_id in (select match_id from tmp_esc);

  insert into public.match_team_event_response
    (match_id, team_id, evento, janela, estado, minutos, chutes_pro, xg_pro, chutes_contra, xg_contra,
     cartoes_amarelos_pro, cartoes_vermelhos_pro, cartoes_amarelos_contra, cartoes_vermelhos_contra)
  select
    mi.match_id, mi.team_id, mi.evento, mi.janela, mi.estado, mi.minutos,
    coalesce(ch.chutes_pro, 0), coalesce(ch.xg_pro, 0),
    coalesce(ch.chutes_contra, 0), coalesce(ch.xg_contra, 0),
    coalesce(cc.cartoes_amarelos_pro, 0), coalesce(cc.cartoes_vermelhos_pro, 0),
    coalesce(cc.cartoes_amarelos_contra, 0), coalesce(cc.cartoes_vermelhos_contra, 0)
  from (
    select match_id, team_id, evento, janela, estado, sum(fim - ini) as minutos
    from tmp_int_fim group by match_id, team_id, evento, janela, estado
  ) mi
  left join tmp_ch ch
    on  ch.match_id = mi.match_id and ch.team_id = mi.team_id
    and ch.evento = mi.evento and ch.janela = mi.janela and ch.estado = mi.estado
  left join tmp_cartoes_ch cc
    on  cc.match_id = mi.match_id and cc.team_id = mi.team_id
    and cc.evento = mi.evento and cc.janela = mi.janela and cc.estado = mi.estado;

  get diagnostics v_gravadas = row_count;
  return v_gravadas;
end;
$$;

comment on function public.derivar_resposta_evento(bigint[]) is
  'Deriva match_team_event_response a partir de match_goal_timeline, match_events e match_shots_fotmob -- chutes/xG E cartões (amarelo/vermelho) por (evento recente, janela, estado). p_match_ids NULL = tudo. Idempotente (delete+insert no escopo).';

revoke all on function public.derivar_resposta_evento(bigint[]) from public, anon, authenticated;
grant execute on function public.derivar_resposta_evento(bigint[]) to service_role;
