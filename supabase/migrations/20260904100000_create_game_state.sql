-- =============================================================================
-- Migration: estado do jogo (game state) -- fase 2 da frente de comportamento
-- =============================================================================
-- A fase 1 (match_formation_fotmob) capturou a estrutura de ENTRADA em campo.
-- Esta fase captura o que acontece DEPOIS do apito: como o placar vigente muda
-- o comportamento dos dois times, minuto a minuto.
--
-- POR QUE ISTO VEM ANTES DE QUALQUER OUTRA ANÁLISE TEMPORAL. "Time que ataca"
-- e "time que estava perdendo e precisou atacar" produzem os mesmos números
-- agregados e significam coisas opostas. Sem separar os dois, toda média deste
-- projeto por time/formação/faixa de minuto mistura escolha tática com reação
-- ao placar -- inclusive a leitura de confronto de formações da fase 1. É
-- também a forma mais direta de "como as equipes interagem entre si": o estado
-- de um time é, por definição, criado pelo outro.
--
-- ORIGEM DO DADO -- de novo, nenhuma chamada de API nova. `match_shots_fotmob`
-- tem 477.686 chutes em 18.770 partidas, TODOS com minuto, xG, situação e
-- coordenadas, e os gols estão lá como `event_type='Goal'` (52.327). Isso
-- basta para reconstruir o placar a cada instante.
--
-- DUAS ARMADILHAS DO SHOTMAP, as duas confirmadas contra o placar oficial
-- antes de escrever esta migration:
--
--  1. `period='PenaltyShootout'` traz 454 "gols" de disputa de pênaltis, que
--     não contam para o placar da partida. Excluídos em todo lugar aqui.
--
--  2. GOL CONTRA: o `team_id` da linha é o time de QUEM CHUTOU, não o
--     beneficiado -- então um gol contra conta para o ADVERSÁRIO. Não é
--     detalhe: das 13.427 partidas conferidas, creditar ao adversário
--     reconstrói o placar oficial em 13.403 (99,8%), contra 12.284 (91,5%)
--     se o team_id fosse lido como time beneficiado. A diferença entre as
--     duas hipóteses é exatamente o volume de gols contra (1.557).
--
-- `placar_confere` marca, por partida, se o placar reconstruído bate com
-- matches.home_goals/away_goals. As ~0,2% que não fecham (chute faltando na
-- ingestão) ficam gravadas com a flag em false em vez de serem silenciosamente
-- descartadas ou, pior, passarem por boas: quem for treinar em cima disto
-- filtra por `placar_confere`.
-- =============================================================================

-- =============================================================================
-- Tabela 1: linha do tempo de gols -- o primitivo auditável
-- =============================================================================
-- Pequena (~52 mil linhas) e derivada de uma regra só. Existe separada do
-- agregado porque é ela que permite conferir a derivação a olho e reclassificar
-- qualquer coisa depois (resposta a gol, janelas pós-evento) sem recomputar o
-- shotmap inteiro.
create table if not exists public.match_goal_timeline (
  id            bigint generated always as identity primary key,
  match_id      bigint  not null references public.matches(id) on delete cascade,
  shot_id       bigint  not null,
  team_id       bigint  not null references public.teams(id),
  minuto        integer not null,
  periodo       text,
  para_casa     boolean not null,
  is_own_goal   boolean not null default false,
  placar_casa   smallint not null,
  placar_fora   smallint not null,
  xg            numeric,
  derivado_em   timestamptz not null default now(),

  constraint match_goal_timeline_shot_key unique (shot_id)
);

comment on table public.match_goal_timeline is
  'Um gol por linha, em ordem cronológica, derivado de match_shots_fotmob (event_type=Goal, exceto disputa de pênaltis). placar_casa/placar_fora é o placar DEPOIS deste gol. Regerada por public.derivar_game_state().';
comment on column public.match_goal_timeline.team_id is
  'Time de QUEM CHUTOU -- em gol contra NÃO é quem foi beneficiado. Use para_casa para saber a favor de quem o gol contou.';
comment on column public.match_goal_timeline.para_casa is
  'true = o gol contou para o mandante. Já resolve o gol contra (invertido em relação a team_id).';
comment on column public.match_goal_timeline.minuto is
  'Minuto efetivo = minute + minute_added (acréscimos somados), para que a ordenação funcione dentro do tempo parado.';

create index if not exists idx_match_goal_timeline_match on public.match_goal_timeline (match_id, minuto);

-- =============================================================================
-- Tabela 2: agregado por (partida, time, estado do jogo)
-- =============================================================================
-- MINUTOS SÃO A PARTE QUE NÃO PODE FALTAR. Um time pode acumular muito xG
-- "perdendo" só porque passou 80 minutos perdendo. Sem o tempo em cada estado,
-- qualquer total por estado é uma armadilha e volta a confundir escolha com
-- circunstância -- por isso `minutos` é gravado junto com os totais, e toda
-- leitura desta tabela deve ser por MINUTO, não por soma.
create table if not exists public.match_team_game_state (
  id              bigint generated always as identity primary key,
  match_id        bigint  not null references public.matches(id) on delete cascade,
  team_id         bigint  not null references public.teams(id),
  is_home         boolean not null,
  estado          text    not null check (estado in ('perdendo','empatando','ganhando')),
  minutos         numeric not null,
  chutes_pro      integer not null default 0,
  xg_pro          numeric not null default 0,
  chutes_contra   integer not null default 0,
  xg_contra       numeric not null default 0,
  gols_pro        integer not null default 0,
  gols_contra     integer not null default 0,
  placar_confere  boolean not null,
  derivado_em     timestamptz not null default now(),

  constraint match_team_game_state_key unique (match_id, team_id, estado)
);

comment on table public.match_team_game_state is
  'Quanto tempo cada time passou perdendo/empatando/ganhando em cada partida, e o que criou e concedeu nesse tempo. Derivada de match_shots_fotmob. Regerada por public.derivar_game_state().';
comment on column public.match_team_game_state.minutos is
  'Minutos que ESTE time passou neste estado. Denominador obrigatório: comparar xg_pro entre estados sem dividir por minutos confunde comportamento com quanto tempo o time ficou naquela situação.';
comment on column public.match_team_game_state.estado is
  'Situação do placar do ponto de vista deste time, no instante de cada chute/intervalo.';
comment on column public.match_team_game_state.gols_pro is
  'Gols marcados por este time ENQUANTO estava neste estado (o gol é classificado pelo placar imediatamente ANTES dele, senão nenhum gol seria marcado "empatando").';
comment on column public.match_team_game_state.placar_confere is
  'false = o placar reconstruído do shotmap não bate com matches.home_goals/away_goals (chute faltando na ingestão). Filtre por true antes de treinar qualquer coisa.';

create index if not exists idx_match_team_game_state_match on public.match_team_game_state (match_id);
create index if not exists idx_match_team_game_state_team  on public.match_team_game_state (team_id, estado);

-- =============================================================================
-- RLS -- mesmo padrão das demais tabelas do pipeline: leitura pública, escrita
-- só via service_role.
-- =============================================================================
alter table public.match_goal_timeline    enable row level security;
alter table public.match_team_game_state  enable row level security;

drop policy if exists "match_goal_timeline_public_read" on public.match_goal_timeline;
create policy "match_goal_timeline_public_read"
  on public.match_goal_timeline for select to anon, authenticated using (true);

drop policy if exists "match_team_game_state_public_read" on public.match_team_game_state;
create policy "match_team_game_state_public_read"
  on public.match_team_game_state for select to anon, authenticated using (true);

-- =============================================================================
-- Função de derivação -- ÚNICO lugar onde a regra de estado do jogo existe.
-- =============================================================================
-- Mesma disciplina da fase 1 (derivar_formacoes_fotmob): a regra mora em SQL e
-- os caminhos de ingestão chamam por RPC, nunca reimplementam.
--
-- DELETE + INSERT no escopo, em vez de upsert: reprocessar uma partida pode
-- FAZER SUMIR um estado (um gol corrigido pode significar que o time nunca
-- esteve perdendo). Upsert deixaria a linha velha para trás.
--
-- p_match_ids NULL = tudo.
-- =============================================================================
create or replace function public.derivar_game_state(p_match_ids bigint[] default null)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  v_gravadas integer;
begin
  -- ---------------------------------------------------------------------
  -- Escopo: partidas com pelo menos um chute (fora disputa de pênaltis).
  -- ---------------------------------------------------------------------
  create temporary table tmp_escopo on commit drop as
  select distinct s.match_id
  from public.match_shots_fotmob s
  where s.period is distinct from 'PenaltyShootout'
    and (p_match_ids is null or s.match_id = any (p_match_ids));

  -- ---------------------------------------------------------------------
  -- Base: todo chute com minuto efetivo e, quando for gol, a favor de quem
  -- ele contou (gol contra invertido -- ver cabeçalho).
  -- ---------------------------------------------------------------------
  create temporary table tmp_base on commit drop as
  select
    s.id,
    s.match_id,
    s.team_id,
    s.period,
    s.event_type,
    s.is_own_goal,
    coalesce(s.xg, 0)                            as xg,
    s.minute + coalesce(s.minute_added, 0)       as minuto,
    (s.team_id = m.home_team_id)                 as chute_da_casa,
    case
      when s.event_type <> 'Goal' then null
      when s.is_own_goal then (s.team_id = m.away_team_id)
      else (s.team_id = m.home_team_id)
    end                                          as gol_para_casa
  from public.match_shots_fotmob s
  join tmp_escopo e on e.match_id = s.match_id
  join public.matches m on m.id = s.match_id
  where s.period is distinct from 'PenaltyShootout'
    and s.minute is not null;

  create index on tmp_base (match_id, minuto, id);

  -- ---------------------------------------------------------------------
  -- Placar IMEDIATAMENTE ANTES de cada linha. `rows ... and 1 preceding`
  -- exclui a própria linha, então um gol é classificado pelo placar de antes
  -- dele -- é o que faz um gol de empate contar como marcado "empatando", e
  -- não "ganhando".
  -- ---------------------------------------------------------------------
  create temporary table tmp_corrida on commit drop as
  select
    b.*,
    coalesce(sum(case when b.gol_para_casa is true  then 1 else 0 end) over w, 0) as casa_antes,
    coalesce(sum(case when b.gol_para_casa is false then 1 else 0 end) over w, 0) as fora_antes
  from tmp_base b
  window w as (
    partition by b.match_id order by b.minuto, b.id
    rows between unbounded preceding and 1 preceding
  );

  -- ---------------------------------------------------------------------
  -- Linha do tempo de gols (placar DEPOIS de cada um).
  -- ---------------------------------------------------------------------
  delete from public.match_goal_timeline t
  where t.match_id in (select match_id from tmp_escopo);

  insert into public.match_goal_timeline
    (match_id, shot_id, team_id, minuto, periodo, para_casa, is_own_goal, placar_casa, placar_fora, xg)
  select
    c.match_id, c.id, c.team_id, c.minuto, c.period,
    c.gol_para_casa, coalesce(c.is_own_goal, false),
    c.casa_antes + (case when c.gol_para_casa then 1 else 0 end),
    c.fora_antes + (case when c.gol_para_casa then 0 else 1 end),
    c.xg
  from tmp_corrida c
  where c.event_type = 'Goal';

  -- ---------------------------------------------------------------------
  -- Fim da partida: greatest(90, último minuto com chute). Aproximação
  -- explícita -- o shotmap não diz quando o juiz apitou. Em partida com
  -- prorrogação isso vira ~120 naturalmente. Erra para menos quando os
  -- últimos minutos não tiveram chute, o que encurta um pouco o ÚLTIMO
  -- estado; aceitável porque o efeito é o mesmo para todos os times e
  -- pequeno perto da diferença entre estados.
  -- ---------------------------------------------------------------------
  create temporary table tmp_fim on commit drop as
  select match_id, greatest(90, max(minuto))::numeric as fim
  from tmp_base group by match_id;

  -- ---------------------------------------------------------------------
  -- Segmentos de placar constante: [0, 1º gol), [gol_i, gol_i+1), ..., até o fim.
  -- ---------------------------------------------------------------------
  create temporary table tmp_segmentos on commit drop as
  with gols as (
    select t.match_id, t.minuto, t.shot_id, t.placar_casa, t.placar_fora
    from public.match_goal_timeline t
    join tmp_escopo e on e.match_id = t.match_id
  )
  -- segmento inicial, sempre 0-0
  select
    f.match_id,
    0::numeric as ini,
    coalesce((select min(g.minuto) from gols g where g.match_id = f.match_id), f.fim)::numeric as fim,
    0::int as h,
    0::int as a
  from tmp_fim f
  union all
  -- um segmento após cada gol
  select
    g.match_id,
    g.minuto::numeric,
    coalesce(
      lead(g.minuto) over (partition by g.match_id order by g.minuto, g.shot_id),
      f.fim
    )::numeric,
    g.placar_casa,
    g.placar_fora
  from gols g
  join tmp_fim f on f.match_id = g.match_id;

  -- ---------------------------------------------------------------------
  -- Minutos por (partida, time, estado).
  -- ---------------------------------------------------------------------
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

  -- ---------------------------------------------------------------------
  -- Chutes por (partida, time, estado) -- duas linhas por chute: "pro" para
  -- quem chutou e "contra" para o adversário, cada um com o SEU estado.
  --
  -- GOL CONTRA FICA DE FORA daqui: é um chute na PRÓPRIA meta, contá-lo como
  -- finalização ofensiva de quem chutou (e como perigo sofrido pelo outro)
  -- inverteria o sinal das duas métricas. São 1.557 em 477 mil (0,3%), e eles
  -- entram normalmente na contagem de GOLS logo abaixo, pelo beneficiado.
  -- ---------------------------------------------------------------------
  create temporary table tmp_chutes on commit drop as
  select match_id, team_id, estado,
         sum(pro)       as chutes_pro,
         sum(xg_pro)    as xg_pro,
         sum(contra)    as chutes_contra,
         sum(xg_contra) as xg_contra
  from (
    -- perspectiva de quem chutou
    select c.match_id,
           c.team_id,
           case
             when c.chute_da_casa then (case when c.casa_antes > c.fora_antes then 'ganhando' when c.casa_antes < c.fora_antes then 'perdendo' else 'empatando' end)
             else                      (case when c.fora_antes > c.casa_antes then 'ganhando' when c.fora_antes < c.casa_antes then 'perdendo' else 'empatando' end)
           end as estado,
           1 as pro, c.xg as xg_pro, 0 as contra, 0::numeric as xg_contra
    from tmp_corrida c
    where not coalesce(c.is_own_goal, false)
    union all
    -- perspectiva do adversário (mesmo chute, estado espelhado)
    select c.match_id,
           case when c.chute_da_casa then m.away_team_id else m.home_team_id end,
           case
             when c.chute_da_casa then (case when c.fora_antes > c.casa_antes then 'ganhando' when c.fora_antes < c.casa_antes then 'perdendo' else 'empatando' end)
             else                      (case when c.casa_antes > c.fora_antes then 'ganhando' when c.casa_antes < c.fora_antes then 'perdendo' else 'empatando' end)
           end,
           0, 0::numeric, 1, c.xg
    from tmp_corrida c
    join public.matches m on m.id = c.match_id
    where not coalesce(c.is_own_goal, false)
  ) y
  group by match_id, team_id, estado;

  -- ---------------------------------------------------------------------
  -- Gols por (partida, time, estado), atribuídos pelo BENEFICIADO -- é o que
  -- faz o gol contra cair no lado certo dos dois times.
  -- ---------------------------------------------------------------------
  create temporary table tmp_gols on commit drop as
  select match_id, team_id, estado, sum(gol_pro) as gols_pro, sum(gol_contra) as gols_contra
  from (
    -- para quem o gol contou
    select c.match_id,
           case when c.gol_para_casa then m.home_team_id else m.away_team_id end as team_id,
           case
             when c.gol_para_casa then (case when c.casa_antes > c.fora_antes then 'ganhando' when c.casa_antes < c.fora_antes then 'perdendo' else 'empatando' end)
             else                      (case when c.fora_antes > c.casa_antes then 'ganhando' when c.fora_antes < c.casa_antes then 'perdendo' else 'empatando' end)
           end as estado,
           1 as gol_pro, 0 as gol_contra
    from tmp_corrida c
    join public.matches m on m.id = c.match_id
    where c.event_type = 'Goal'
    union all
    -- quem sofreu
    select c.match_id,
           case when c.gol_para_casa then m.away_team_id else m.home_team_id end,
           case
             when c.gol_para_casa then (case when c.fora_antes > c.casa_antes then 'ganhando' when c.fora_antes < c.casa_antes then 'perdendo' else 'empatando' end)
             else                      (case when c.casa_antes > c.fora_antes then 'ganhando' when c.casa_antes < c.fora_antes then 'perdendo' else 'empatando' end)
           end,
           0, 1
    from tmp_corrida c
    join public.matches m on m.id = c.match_id
    where c.event_type = 'Goal'
  ) z
  group by match_id, team_id, estado;

  -- ---------------------------------------------------------------------
  -- Placar reconstruído x placar oficial, por partida.
  -- ---------------------------------------------------------------------
  create temporary table tmp_confere on commit drop as
  select
    e.match_id,
    coalesce(
      -- placar depois do ÚLTIMO gol, na mesma ordenação (minuto, shot_id) usada
      -- em toda a derivação. `order by ... limit 1` e não um bool_and sobre o
      -- minuto máximo: dois gols no mesmo minuto reprovariam a partida inteira.
      (select (t.placar_casa = m.home_goals and t.placar_fora = m.away_goals)
       from public.match_goal_timeline t
       where t.match_id = e.match_id
       order by t.minuto desc, t.shot_id desc
       limit 1),
      -- sem gol nenhum: confere se a partida realmente terminou 0-0
      (m.home_goals = 0 and m.away_goals = 0)
    ) as confere
  from tmp_escopo e
  join public.matches m on m.id = e.match_id
  where m.status = 'finished' and m.home_goals is not null;

  -- ---------------------------------------------------------------------
  -- Grava o agregado.
  -- ---------------------------------------------------------------------
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
  left join tmp_chutes ch
    on ch.match_id = mi.match_id and ch.team_id = mi.team_id and ch.estado = mi.estado
  left join tmp_gols go
    on go.match_id = mi.match_id and go.team_id = mi.team_id and go.estado = mi.estado
  left join tmp_confere cf on cf.match_id = mi.match_id;

  get diagnostics v_gravadas = row_count;
  return v_gravadas;
end;
$$;

comment on function public.derivar_game_state(bigint[]) is
  'Deriva match_goal_timeline e match_team_game_state a partir de match_shots_fotmob. p_match_ids NULL = tudo; array = só esse lote. Idempotente (delete+insert no escopo).';

revoke all on function public.derivar_game_state(bigint[]) from public, anon, authenticated;
grant execute on function public.derivar_game_state(bigint[]) to service_role;

-- =============================================================================
-- View de leitura -- perfil de um time por estado, já normalizado POR MINUTO.
-- =============================================================================
-- Existe para que ninguém precise lembrar de dividir por `minutos`: somar
-- xg_pro por estado sem o denominador é o erro que esta fase inteira serve
-- para evitar. Só partidas com placar reconciliado.
create or replace view public.v_time_game_state
with (security_invoker = on) as
select
  g.team_id,
  m.league_id,
  m.season,
  g.estado,
  count(*)                                                as partidas,
  round(sum(g.minutos), 1)                                as minutos_totais,
  round(sum(g.xg_pro)    / nullif(sum(g.minutos), 0) * 90, 3) as xg_criado_por_90,
  round(sum(g.xg_contra) / nullif(sum(g.minutos), 0) * 90, 3) as xg_concedido_por_90,
  round(sum(g.chutes_pro)::numeric    / nullif(sum(g.minutos), 0) * 90, 2) as chutes_por_90,
  round(sum(g.chutes_contra)::numeric / nullif(sum(g.minutos), 0) * 90, 2) as chutes_sofridos_por_90,
  sum(g.gols_pro)    as gols_marcados,
  sum(g.gols_contra) as gols_sofridos
from public.match_team_game_state g
join public.matches m on m.id = g.match_id
where g.placar_confere
group by g.team_id, m.league_id, m.season, g.estado;

comment on view public.v_time_game_state is
  'Perfil de cada time por estado do jogo (liga/temporada), já normalizado por 90 minutos NAQUELE estado -- a forma correta de comparar comportamento entre estados. Só partidas com placar reconciliado.';
