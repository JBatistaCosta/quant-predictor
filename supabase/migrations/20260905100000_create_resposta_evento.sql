-- =============================================================================
-- Migration: resposta a eventos -- fase 3 da frente de comportamento
-- =============================================================================
-- Fase 1: com que ESTRUTURA o time entra em campo (match_formation_fotmob).
-- Fase 2: como o PLACAR VIGENTE muda o comportamento (match_team_game_state).
-- Fase 3 (esta): o TRANSIENTE -- o que muda nos minutos imediatamente após um
-- gol ou uma expulsão, acima e além da mudança de estado que o próprio evento
-- causou.
--
-- POR QUE `estado` FAZ PARTE DA CHAVE, e não é um detalhe. Sem ele, esta
-- tabela responderia "times criam mais depois de sofrer gol" -- que é só
-- "times criam mais quando estão perdendo", o achado da fase 2 reembalado.
-- A pergunta desta fase só existe DENTRO de um estado: entre dois trechos em
-- que o time está igualmente perdendo, o trecho logo após levar o gol é
-- diferente do resto? Guardar (evento, janela, estado) juntos é o que permite
-- comparar transiente com regime dentro do mesmo estado, em vez de comparar
-- estados diferentes e chamar isso de reação.
--
-- FONTES, as duas já no banco e nenhuma chamada de API nova:
--  - `match_goal_timeline` (fase 2) -- 51.873 gols com minuto efetivo, e o
--    `para_casa` que já resolve gol contra. Foi criada exatamente para isto.
--  - `match_events` -- expulsões (red_card + second_yellow_card): 2.996 com
--    minuto E time, em 2.211 partidas que também têm estado do jogo derivado.
--    Amostra pequena perto dos gols, mas real; é por isso que a expulsão entra
--    como tipo de evento separado e não misturada com gol.
--
-- ATRIBUIÇÃO POR EVENTO MAIS RECENTE, não por janelas sobrepostas. Dois gols
-- em 3 minutos criariam janelas que se cruzam, e somar as duas contaria o
-- mesmo chute duas vezes. Aqui cada instante pertence ao evento MAIS RECENTE
-- daquele time -- as janelas nunca se sobrepõem e o total de minutos de uma
-- partida continua fechando.
--
-- O CHUTE QUE É O PRÓPRIO EVENTO NÃO CONTA COMO RESPOSTA A SI MESMO. Um chute
-- no minuto exato de um corte é atribuído ao intervalo que TERMINA ali, não ao
-- que começa. Sem essa regra, o gol entraria na janela "0-5 depois de marcou"
-- que ele mesmo abriu, inflando em exatamente um chute (e um xG alto) cada
-- janela pós-gol -- o artefato faria parecer que times finalizam muito logo
-- depois de marcar.
-- =============================================================================

create table if not exists public.match_team_event_response (
  id             bigint generated always as identity primary key,
  match_id       bigint  not null references public.matches(id) on delete cascade,
  team_id        bigint  not null references public.teams(id),
  evento         text    not null check (evento in ('marcou','sofreu','expulsao_pro','expulsao_contra','nenhum')),
  janela         text    not null check (janela in ('0-5','5-15','regime')),
  estado         text    not null check (estado in ('perdendo','empatando','ganhando')),
  minutos        numeric not null,
  chutes_pro     integer not null default 0,
  xg_pro         numeric not null default 0,
  chutes_contra  integer not null default 0,
  xg_contra      numeric not null default 0,
  derivado_em    timestamptz not null default now(),

  constraint match_team_event_response_key unique (match_id, team_id, evento, janela, estado)
);

comment on table public.match_team_event_response is
  'Minutos e produção (chutes/xG criados e sofridos) por (partida, time, evento recente, janela, estado do placar). Mede o TRANSIENTE pós-gol/pós-expulsão dentro de um mesmo estado. Derivada de match_goal_timeline + match_events + match_shots_fotmob por public.derivar_resposta_evento().';
comment on column public.match_team_event_response.evento is
  'Evento mais recente deste time no instante: marcou / sofreu / expulsao_pro (ficou com um a menos) / expulsao_contra / nenhum (passou da janela de 15min, ou ainda não houve evento).';
comment on column public.match_team_event_response.janela is
  '"0-5" e "5-15" = minutos decorridos desde o evento. "regime" = fora de qualquer janela, o comportamento de base do time naquele estado -- é o COMPARADOR: transiente só significa alguma coisa contra o regime do mesmo estado.';
comment on column public.match_team_event_response.estado is
  'Estado do placar no trecho, do ponto de vista deste time. Faz parte da chave de propósito: sem ele, "reação ao gol" e "estar perdendo" ficam indistinguíveis.';
comment on column public.match_team_event_response.minutos is
  'Denominador obrigatório, como na fase 2. Uma janela de 0-5min tem no máximo 5 minutos; somar xG entre janelas de tamanhos diferentes sem dividir não significa nada.';

create index if not exists idx_mter_match on public.match_team_event_response (match_id);
create index if not exists idx_mter_team  on public.match_team_event_response (team_id, evento, janela, estado);

alter table public.match_team_event_response enable row level security;
drop policy if exists "match_team_event_response_public_read" on public.match_team_event_response;
create policy "match_team_event_response_public_read"
  on public.match_team_event_response for select to anon, authenticated using (true);

-- =============================================================================
-- Função de derivação
-- =============================================================================
-- Mesma disciplina das fases 1 e 2: regra em um lugar só, delete+insert no
-- escopo (reprocessar pode fazer sumir uma combinação), p_match_ids NULL = tudo.
-- =============================================================================
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

  -- Fim da partida: mesma aproximação da fase 2 (último minuto com chute, no
  -- mínimo 90), mantida idêntica de propósito -- é o que faz os minutos das
  -- duas tabelas fecharem entre si.
  create temporary table tmp_fim on commit drop as
  select s.match_id, greatest(90, max(s.minute + coalesce(s.minute_added, 0)))::numeric as fim
  from public.match_shots_fotmob s
  join tmp_esc e on e.match_id = s.match_id
  where s.period is distinct from 'PenaltyShootout' and s.minute is not null
  group by s.match_id;

  -- Eventos por (partida, time): um gol gera DUAS linhas (marcou para um,
  -- sofreu para o outro); uma expulsão idem (pro/contra).
  create temporary table tmp_ev on commit drop as
  select t.match_id,
         case when t.para_casa then m.home_team_id else m.away_team_id end as team_id,
         t.minuto::numeric as minuto, 'marcou'::text as evento
  from public.match_goal_timeline t
  join tmp_esc e on e.match_id = t.match_id
  join public.matches m on m.id = t.match_id
  union all
  select t.match_id,
         case when t.para_casa then m.away_team_id else m.home_team_id end,
         t.minuto::numeric, 'sofreu'
  from public.match_goal_timeline t
  join tmp_esc e on e.match_id = t.match_id
  join public.matches m on m.id = t.match_id
  union all
  select ev.match_id, ev.team_id, ev.minute::numeric, 'expulsao_pro'
  from public.match_events ev
  join tmp_esc e on e.match_id = ev.match_id
  where ev.event_type in ('red_card','second_yellow_card')
    and ev.minute is not null and ev.team_id is not null
  union all
  select ev.match_id,
         case when ev.team_id = m.home_team_id then m.away_team_id else m.home_team_id end,
         ev.minute::numeric, 'expulsao_contra'
  from public.match_events ev
  join tmp_esc e on e.match_id = ev.match_id
  join public.matches m on m.id = ev.match_id
  where ev.event_type in ('red_card','second_yellow_card')
    and ev.minute is not null and ev.team_id is not null
    and ev.team_id in (m.home_team_id, m.away_team_id);

  create index on tmp_ev (match_id, team_id, minuto);

  create temporary table tmp_times on commit drop as
  select e.match_id, m.home_team_id as team_id from tmp_esc e join public.matches m on m.id = e.match_id
  union all
  select e.match_id, m.away_team_id from tmp_esc e join public.matches m on m.id = e.match_id;

  -- CORTES LIMITADOS AO FIM DA PARTIDA. As janelas +5/+15 de um evento tardio
  -- caem depois do apito (gol aos 86' gera cortes em 91 e 101 numa partida que
  -- acaba aos 93') e sem o teto isso INVENTA minutos: foi exatamente assim que
  -- o erro apareceu -- 101 minutos aqui contra 93 na fase 2, diferença igual ao
  -- excesso da última janela.
  create temporary table tmp_cortes on commit drop as
  select x.match_id, x.team_id, x.ponto
  from (
    select t.match_id, t.team_id, 0::numeric as ponto from tmp_times t
    union
    select t.match_id, t.team_id, f.fim from tmp_times t join tmp_fim f on f.match_id = t.match_id
    union
    select t.match_id, t.team_id, gt.minuto::numeric
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
         t.minuto::numeric as minuto
  from public.match_goal_timeline t
  join tmp_esc e on e.match_id = t.match_id
  join public.matches m on m.id = t.match_id;

  create index on tmp_gols_time (match_id, minuto);

  -- Placar em cada `ini` por junção agregada, e evento mais recente por
  -- `distinct on`. A primeira versão usava dois `lateral` correlacionados por
  -- intervalo e não passava de 400 partidas por transação; assim o backfill
  -- coube em lotes de ~500.
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

  -- Fora da janela de 15min o evento deixa de importar: vira nenhum/regime (o
  -- comparador). Normalizar aqui evita linhas como (marcou, regime), que
  -- seriam o mesmo que 'nenhum' mas fragmentariam a amostra.
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
  left join tmp_int_ev ev
    on ev.match_id = sc.match_id and ev.team_id = sc.team_id and ev.ini = sc.ini;

  create index on tmp_int_fim (match_id, ini, fim);

  -- Chute no minuto exato de um corte pertence ao intervalo que TERMINA ali
  -- (ini < m <= fim) -- ver cabeçalho. Minuto 0 é a exceção (não há anterior).
  create temporary table tmp_ch on commit drop as
  select
    it.match_id, it.team_id, it.evento, it.janela, it.estado,
    count(*) filter (where s.team_id = it.team_id)  as chutes_pro,
    coalesce(sum(coalesce(s.xg,0)) filter (where s.team_id = it.team_id), 0)  as xg_pro,
    count(*) filter (where s.team_id <> it.team_id) as chutes_contra,
    coalesce(sum(coalesce(s.xg,0)) filter (where s.team_id <> it.team_id), 0) as xg_contra
  from tmp_int_fim it
  join public.match_shots_fotmob s
    on s.match_id = it.match_id
   and s.period is distinct from 'PenaltyShootout'
   and s.minute is not null
   and not coalesce(s.is_own_goal, false)
   and ( (s.minute + coalesce(s.minute_added,0)) > it.ini
         or (it.ini = 0 and (s.minute + coalesce(s.minute_added,0)) = 0) )
   and (s.minute + coalesce(s.minute_added,0)) <= it.fim
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

comment on function public.derivar_resposta_evento(bigint[]) is
  'Deriva match_team_event_response a partir de match_goal_timeline, match_events e match_shots_fotmob. p_match_ids NULL = tudo. Idempotente (delete+insert no escopo).';

revoke all on function public.derivar_resposta_evento(bigint[]) from public, anon, authenticated;
grant execute on function public.derivar_resposta_evento(bigint[]) to service_role;

-- =============================================================================
-- View de leitura -- transiente contra regime, DENTRO do mesmo estado.
-- =============================================================================
-- É a comparação que a fase inteira serve para permitir. `xg_criado_por_90`
-- de (evento='sofreu', janela='0-5', estado='perdendo') contra o mesmo estado
-- em (evento='nenhum', janela='regime') responde: "logo depois de levar o gol
-- o time faz algo diferente do que já faria por estar perdendo?".
create or replace view public.v_resposta_evento
with (security_invoker = on) as
select
  r.team_id,
  m.league_id,
  m.season,
  r.evento,
  r.janela,
  r.estado,
  count(*)                 as ocorrencias,
  round(sum(r.minutos), 1) as minutos_totais,
  round(sum(r.xg_pro)    / nullif(sum(r.minutos), 0) * 90, 3) as xg_criado_por_90,
  round(sum(r.xg_contra) / nullif(sum(r.minutos), 0) * 90, 3) as xg_concedido_por_90,
  round(sum(r.chutes_pro)::numeric / nullif(sum(r.minutos), 0) * 90, 2) as chutes_por_90
from public.match_team_event_response r
join public.matches m on m.id = r.match_id
group by r.team_id, m.league_id, m.season, r.evento, r.janela, r.estado;

comment on view public.v_resposta_evento is
  'Produção por 90 minutos em cada combinação (evento recente, janela, estado). Comparar sempre contra a linha (evento=nenhum, janela=regime) do MESMO estado -- é isso que separa reação ao evento de simples mudança de placar.';
