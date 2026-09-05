-- =============================================================================
-- Migration: relógio monótono -- corrige defeito real das fases 2 e 3
-- =============================================================================
-- DEFEITO CORRIGIDO AQUI. As fases 2 e 3 definiam o relógio da partida como
-- `minute + minute_added`. Isso NÃO é monótono entre tempos: o 2º tempo também
-- começa no minuto 45, então um gol aos 45+3 (minuto efetivo 48) era ordenado
-- DEPOIS de lances do início do 2º tempo.
--
-- Raio de impacto medido antes da correção:
--   1.651 gols nos acréscimos do 1º tempo (3,2% dos gols)
--   1.601 partidas afetadas
--   1.102 chutes recebendo ESTADO DO JOGO ERRADO (0,23% de 477.715)
--
-- POR QUE AS INVARIANTES NÃO PEGARAM ISSO. Minutos, chutes e xG por partida são
-- insensíveis à ORDEM: eles reconciliavam perfeitamente (37.540/37.540) com a
-- ordenação errada. Invariante de total prova que a derivação soma certo, não
-- que ela atribui certo. É a mesma lição que já tinha aparecido no controle de
-- força de equipe, agora na forma de um bug de verdade.
--
-- A CORREÇÃO: coluna `clock` em match_goal_timeline, um relógio monótono que
-- desloca o 2º tempo (e a prorrogação) pelo excedente dos acréscimos do 1º
-- (`fh_over`). `minuto` continua sendo o valor EXIBÍVEL (45+3 = 48); `clock` é
-- o que ordena e o que define os segmentos.
--
--   clock = minuto                     no 1º tempo
--   clock = minuto + fh_over           do 2º tempo em diante
--   fim   = greatest(90 + fh_over, max(clock))
--
-- fh_over médio é 1,48 min (metade das partidas tem zero; só 55 passam de 10).
-- Os minutos por partida SOBEM um pouco, e isso é mais correto: antes os
-- acréscimos do 1º tempo eram sobrepostos ao início do 2º em vez de contados.
--
-- Verificado depois de re-derivar as duas tabelas inteiras:
--   0 de 10.712 partidas com gol nos dois tempos ainda mal ordenadas
--   37.542 de 37.542 pares (partida, time) reconciliando entre as duas fases
-- Nenhuma conclusão dos achados mudou -- só os valores, levemente.
-- =============================================================================

alter table public.match_goal_timeline add column if not exists clock numeric;

comment on column public.match_goal_timeline.minuto is
  'Minuto EXIBÍVEL (minute + minute_added). Um gol aos 45+3 fica 48. NÃO use para ordenar: não é monótono entre tempos -- use clock.';
comment on column public.match_goal_timeline.clock is
  'Relógio MONÓTONO da partida, usado para ordenação e para os segmentos das fases 2 e 3. Igual a minuto no 1º tempo; do 2º tempo em diante soma o excedente dos acréscimos do 1º tempo (fh_over), para que 45+3 venha ANTES do início do 2º tempo. Sem isso, 1.102 chutes recebiam estado do jogo errado.';

create or replace function public.derivar_game_state(p_match_ids bigint[] default null)
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

  -- Excedente dos acrescimos do 1o tempo. O 2o tempo tambem comeca no minuto
  -- 45, entao sem deslocar por este valor um gol aos 45+3 (minuto 48) seria
  -- ordenado DEPOIS de lances do inicio do 2o tempo.
  create temporary table tmp_fh on commit drop as
  select s.match_id,
         greatest(0, max(s.minute + coalesce(s.minute_added,0)) - 45)::numeric as fh_over
  from public.match_shots_fotmob s
  join tmp_escopo e on e.match_id = s.match_id
  where s.period = 'FirstHalf' and s.minute is not null
  group by s.match_id;

  create temporary table tmp_base on commit drop as
  select
    s.id,
    s.match_id,
    s.team_id,
    s.period,
    s.event_type,
    s.is_own_goal,
    coalesce(s.xg, 0)                      as xg,
    s.minute + coalesce(s.minute_added, 0) as minuto,
    case when s.period = 'FirstHalf'
         then (s.minute + coalesce(s.minute_added,0))::numeric
         else (s.minute + coalesce(s.minute_added,0))::numeric + coalesce(fh.fh_over, 0)
    end                                    as clock,
    coalesce(fh.fh_over, 0)                as fh_over,
    (s.team_id = m.home_team_id)           as chute_da_casa,
    case
      when s.event_type <> 'Goal' then null
      when s.is_own_goal then (s.team_id = m.away_team_id)
      else (s.team_id = m.home_team_id)
    end                                    as gol_para_casa
  from public.match_shots_fotmob s
  join tmp_escopo e on e.match_id = s.match_id
  join public.matches m on m.id = s.match_id
  left join tmp_fh fh on fh.match_id = s.match_id
  where s.period is distinct from 'PenaltyShootout'
    and s.minute is not null;

  create index on tmp_base (match_id, clock, id);

  create temporary table tmp_corrida on commit drop as
  select
    b.*,
    coalesce(sum(case when b.gol_para_casa is true  then 1 else 0 end) over w, 0) as casa_antes,
    coalesce(sum(case when b.gol_para_casa is false then 1 else 0 end) over w, 0) as fora_antes
  from tmp_base b
  window w as (
    partition by b.match_id order by b.clock, b.id
    rows between unbounded preceding and 1 preceding
  );

  delete from public.match_goal_timeline t
  where t.match_id in (select match_id from tmp_escopo);

  insert into public.match_goal_timeline
    (match_id, shot_id, team_id, minuto, clock, periodo, para_casa, is_own_goal, placar_casa, placar_fora, xg)
  select
    c.match_id, c.id, c.team_id, c.minuto, c.clock, c.period,
    c.gol_para_casa, coalesce(c.is_own_goal, false),
    c.casa_antes + (case when c.gol_para_casa then 1 else 0 end),
    c.fora_antes + (case when c.gol_para_casa then 0 else 1 end),
    c.xg
  from tmp_corrida c
  where c.event_type = 'Goal';

  -- Fim da partida no relogio monotono: 90 mais o excedente do 1o tempo.
  create temporary table tmp_fim on commit drop as
  select match_id, greatest(90 + max(fh_over), max(clock))::numeric as fim
  from tmp_base group by match_id;

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

  create temporary table tmp_chutes on commit drop as
  select match_id, team_id, estado,
         sum(pro) as chutes_pro, sum(xg_pro) as xg_pro,
         sum(contra) as chutes_contra, sum(xg_contra) as xg_contra
  from (
    select c.match_id, c.team_id,
           case when c.chute_da_casa then (case when c.casa_antes > c.fora_antes then 'ganhando' when c.casa_antes < c.fora_antes then 'perdendo' else 'empatando' end)
                else (case when c.fora_antes > c.casa_antes then 'ganhando' when c.fora_antes < c.casa_antes then 'perdendo' else 'empatando' end) end as estado,
           1 as pro, c.xg as xg_pro, 0 as contra, 0::numeric as xg_contra
    from tmp_corrida c where not coalesce(c.is_own_goal, false)
    union all
    select c.match_id,
           case when c.chute_da_casa then m.away_team_id else m.home_team_id end,
           case when c.chute_da_casa then (case when c.fora_antes > c.casa_antes then 'ganhando' when c.fora_antes < c.casa_antes then 'perdendo' else 'empatando' end)
                else (case when c.casa_antes > c.fora_antes then 'ganhando' when c.casa_antes < c.fora_antes then 'perdendo' else 'empatando' end) end,
           0, 0::numeric, 1, c.xg
    from tmp_corrida c join public.matches m on m.id = c.match_id
    where not coalesce(c.is_own_goal, false)
  ) y
  group by match_id, team_id, estado;

  create temporary table tmp_gols on commit drop as
  select match_id, team_id, estado, sum(gol_pro) as gols_pro, sum(gol_contra) as gols_contra
  from (
    select c.match_id,
           case when c.gol_para_casa then m.home_team_id else m.away_team_id end as team_id,
           case when c.gol_para_casa then (case when c.casa_antes > c.fora_antes then 'ganhando' when c.casa_antes < c.fora_antes then 'perdendo' else 'empatando' end)
                else (case when c.fora_antes > c.casa_antes then 'ganhando' when c.fora_antes < c.casa_antes then 'perdendo' else 'empatando' end) end as estado,
           1 as gol_pro, 0 as gol_contra
    from tmp_corrida c join public.matches m on m.id = c.match_id where c.event_type = 'Goal'
    union all
    select c.match_id,
           case when c.gol_para_casa then m.away_team_id else m.home_team_id end,
           case when c.gol_para_casa then (case when c.fora_antes > c.casa_antes then 'ganhando' when c.fora_antes < c.casa_antes then 'perdendo' else 'empatando' end)
                else (case when c.casa_antes > c.fora_antes then 'ganhando' when c.casa_antes < c.fora_antes then 'perdendo' else 'empatando' end) end,
           0, 1
    from tmp_corrida c join public.matches m on m.id = c.match_id where c.event_type = 'Goal'
  ) z
  group by match_id, team_id, estado;

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

  delete from public.match_team_game_state g
  where g.match_id in (select match_id from tmp_escopo);

  insert into public.match_team_game_state
    (match_id, team_id, is_home, estado, minutos,
     chutes_pro, xg_pro, chutes_contra, xg_contra, gols_pro, gols_contra, placar_confere)
  select
    mi.match_id, mi.team_id, mi.is_home, mi.estado, mi.minutos,
    coalesce(ch.chutes_pro, 0),  coalesce(ch.xg_pro, 0),
    coalesce(ch.chutes_contra, 0), coalesce(ch.xg_contra, 0),
    coalesce(go.gols_pro, 0),    coalesce(go.gols_contra, 0),
    coalesce(cf.confere, false)
  from tmp_minutos mi
  left join tmp_chutes ch on ch.match_id = mi.match_id and ch.team_id = mi.team_id and ch.estado = mi.estado
  left join tmp_gols go on go.match_id = mi.match_id and go.team_id = mi.team_id and go.estado = mi.estado
  left join tmp_confere cf on cf.match_id = mi.match_id;

  get diagnostics v_gravadas = row_count;
  return v_gravadas;
end;
$$;

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

  -- Mesmo relogio monotono da fase 2 (ver derivar_game_state): sem deslocar o
  -- 2o tempo pelo excedente dos acrescimos do 1o, 45+3 cairia depois do
  -- inicio do 2o tempo.
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
  -- Cartao: match_events nao tem periodo nem acrescimo, so o minuto cru. Um
  -- cartao no 1o tempo nao sofre deslocamento; do 2o em diante, desloca.
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

  delete from public.match_team_event_response r
  where r.match_id in (select match_id from tmp_esc);

  insert into public.match_team_event_response
    (match_id, team_id, evento, janela, estado, minutos, chutes_pro, xg_pro, chutes_contra, xg_contra)
  select
    mi.match_id, mi.team_id, mi.evento, mi.janela, mi.estado, mi.minutos,
    coalesce(ch.chutes_pro, 0), coalesce(ch.xg_pro, 0),
    coalesce(ch.chutes_contra, 0), coalesce(ch.xg_contra, 0)
  from (
    select match_id, team_id, evento, janela, estado, sum(fim - ini) as minutos
    from tmp_int_fim group by match_id, team_id, evento, janela, estado
  ) mi
  left join tmp_ch ch
    on  ch.match_id = mi.match_id and ch.team_id = mi.team_id
    and ch.evento = mi.evento and ch.janela = mi.janela and ch.estado = mi.estado;

  get diagnostics v_gravadas = row_count;
  return v_gravadas;
end;
$$;
