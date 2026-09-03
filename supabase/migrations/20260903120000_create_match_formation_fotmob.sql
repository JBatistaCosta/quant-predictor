-- =============================================================================
-- Migration: match_formation_fotmob -- esquema tático (formação) por partida/time
-- =============================================================================
-- Primeiro tijolo da frente "comportamento das equipes ao longo do jogo e como
-- interagem entre si": antes de modelar comportamento é preciso ter a ESTRUTURA
-- com que cada time entra em campo, e a estrutura do adversário.
--
-- ORIGEM DO DADO -- nenhuma chamada de API nova. O FotMob não devolve a string
-- "4-2-3-1" no payload que este projeto já ingere; ele devolve, POR JOGADOR
-- TITULAR, a posição do jogador na grade do campinho desenhado na tela
-- (`verticalLayout: {x, y, width, height}`, com y=~0.1 no goleiro e y crescendo
-- na direção do gol adversário). Esse objeto já está guardado em
-- `match_lineup_fotmob.raw` desde a primeira ingestão (e parcialmente projetado
-- nas colunas `field_pos_x`/`field_pos_y`), em ~38 mil escalações de 2017 a
-- 2026. Agrupar os 11 titulares pelo `y` reconstrói as linhas da formação
-- exatamente -- validado antes de escrever esta migration: 37.951 de 39.106
-- team-matches (97,1%) são deriváveis, e a distribuição resultante é a que a
-- realidade do futebol prevê (4-2-3-1 > 4-3-3 > 4-4-2 > 3-4-2-1 > 3-5-2 ...),
-- sem nenhuma grade impossível.
--
-- POR QUE UMA TABELA NOVA, e não só preencher `match_lineup_fotmob.formation`
-- (coluna que existe desde o PR #114 e nunca foi preenchida -- 0 de 822 mil
-- linhas): a formação é um atributo de (partida, time), não de jogador.
-- Guardá-la repetida em 11-23 linhas por time convida a divergência interna e
-- não dá onde pendurar os atributos derivados (setores, confronto). A coluna
-- antiga fica como está, agora documentada como obsoleta -- ninguém lê dela.
--
-- SEM MÉTRICAS GEOMÉTRICAS DE PROPÓSITO. Seria tentador derivar "altura do
-- bloco" ou "largura" da média de x/y dos titulares, mas a grade do FotMob é
-- ESQUEMÁTICA (o desenho da telinha), não dado de rastreamento: para uma mesma
-- formação os x/y são sempre os mesmos, então qualquer "altura média" seria uma
-- função determinística da própria formação, não uma medida de comportamento.
-- Registrar isso aqui para ninguém reintroduzir a ideia achando que é sinal
-- novo -- comportamento real em campo virá de `match_events`/`match_shots_fotmob`
-- (fase seguinte), não desta grade.
-- =============================================================================

create table if not exists public.match_formation_fotmob (
  id               bigint generated always as identity primary key,
  match_id         bigint  not null references public.matches(id) on delete cascade,
  team_id          bigint  not null references public.teams(id),
  is_home          boolean,
  formation        text    not null,
  formation_grade  text    not null,
  n_linhas         smallint not null,
  n_def            smallint not null,
  n_mid            smallint not null,
  n_atk            smallint not null,
  fonte            text    not null default 'layout_fotmob',
  derivado_em      timestamptz not null default now(),

  constraint match_formation_fotmob_match_team_key unique (match_id, team_id)
);

comment on table public.match_formation_fotmob is
  'Esquema tático inicial (formação) por partida e time, derivado da grade de posições dos 11 titulares em match_lineup_fotmob (verticalLayout do FotMob). Uma linha por (match_id, team_id). Regerada por public.derivar_formacoes_fotmob().';

comment on column public.match_formation_fotmob.formation is
  'Formação no formato convencional, SEM o goleiro: "4-2-3-1", "3-5-2", "4-4-2".';
comment on column public.match_formation_fotmob.formation_grade is
  'Grade crua como sai do layout, COM o goleiro na frente: "1-4-2-3-1". Guardada para auditoria -- é o que permite conferir a derivação sem reabrir o raw.';
comment on column public.match_formation_fotmob.n_linhas is
  'Quantidade de linhas de campo (sem o goleiro). 4-4-2 tem 3; 4-2-3-1 tem 4; 4-1-2-1-2 (losango) tem 5.';
comment on column public.match_formation_fotmob.n_def is
  'Redução canônica a 3 setores: n_def = primeira linha de campo. Existe para comparar formações de nº de linhas diferente (3-4-2-1 vs 3-5-2 são ambas defesa de 3).';
comment on column public.match_formation_fotmob.n_mid is
  'Redução canônica a 3 setores: soma das linhas intermediárias. Em 4-2-3-1 é 2+3=5.';
comment on column public.match_formation_fotmob.n_atk is
  'Redução canônica a 3 setores: última linha de campo (nº de homens mais adiantados).';
comment on column public.match_formation_fotmob.fonte is
  'Como a formação foi obtida. Hoje só "layout_fotmob" (derivada da grade). Reservado para o dia em que uma fonte declarar a formação explicitamente -- aí dá para preferir a declarada sem perder o histórico derivado.';

comment on column public.match_lineup_fotmob.formation is
  'OBSOLETA -- nunca foi preenchida (0 de 822 mil linhas). Formação é atributo de (partida, time), não de jogador: use public.match_formation_fotmob.';

create index if not exists idx_match_formation_fotmob_match on public.match_formation_fotmob (match_id);
create index if not exists idx_match_formation_fotmob_team  on public.match_formation_fotmob (team_id);
create index if not exists idx_match_formation_fotmob_form  on public.match_formation_fotmob (formation);

-- =============================================================================
-- RLS -- mesmo padrão de match_lineup_fotmob/xi_previsto: leitura pública,
-- escrita só via service_role (a função de derivação roda com service_role).
-- =============================================================================
alter table public.match_formation_fotmob enable row level security;

drop policy if exists "match_formation_fotmob_public_read" on public.match_formation_fotmob;
create policy "match_formation_fotmob_public_read"
  on public.match_formation_fotmob
  for select
  to anon, authenticated
  using (true);

-- =============================================================================
-- Função de derivação -- ÚNICO lugar onde a regra "grade -> formação" existe.
-- =============================================================================
-- Deliberadamente em SQL, e não replicada em JS (api/model-maintenance.js) e
-- Python (scripts/ingerir_escalacao_pre_jogo.py): os dois caminhos de ingestão
-- gravam em match_lineup_fotmob e depois chamam esta função por RPC, então a
-- regra não pode divergir entre eles (o projeto já se queimou com lógica
-- duplicada entre o ingestor JS e o Python).
--
-- p_match_ids = NULL processa TUDO (backfill). Passar o array restringe ao
-- lote recém-ingerido, que é o uso do caminho forward.
--
-- Idempotente: upsert por (match_id, team_id), reprocessar não duplica.
--
-- AGRUPAMENTO DAS LINHAS POR TOLERÂNCIA, não por igualdade exata de y: os
-- valores vêm arredondados de forma levemente inconsistente entre partidas
-- (0.09 e 0.1 aparecem os dois para a linha do goleiro). O menor intervalo
-- REAL entre duas linhas observado nos dados é ~0.19, então 0.05 separa linhas
-- de verdade sem risco de fundir duas.
-- =============================================================================
create or replace function public.derivar_formacoes_fotmob(p_match_ids bigint[] default null)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  v_gravadas integer;
begin
  with base as (
    select
      l.match_id,
      l.team_id,
      coalesce(l.field_pos_y, (l.raw -> 'verticalLayout' ->> 'y')::numeric) as y
    from public.match_lineup_fotmob l
    where l.is_starter
      and (p_match_ids is null or l.match_id = any (p_match_ids))
  ),
  -- Só times com os 11 titulares E os 11 com posição na grade. Escalação
  -- parcial (jogo antigo sem layout, ou lineup truncado) fica de fora em vez
  -- de virar uma formação inventada de 9 jogadores.
  elegivel as (
    select match_id, team_id
    from base
    group by match_id, team_id
    having count(*) = 11 and count(y) = 11
  ),
  posicoes as (
    select b.match_id, b.team_id, b.y, count(*) as n_jogadores
    from base b
    join elegivel e on e.match_id = b.match_id and e.team_id = b.team_id
    group by b.match_id, b.team_id, b.y
  ),
  -- Marca o início de cada linha nova (salto de y maior que a tolerância).
  quebras as (
    select
      p.*,
      case
        when lag(p.y) over w is null then 1
        when p.y - lag(p.y) over w > 0.05 then 1
        else 0
      end as inicia_linha
    from posicoes p
    window w as (partition by p.match_id, p.team_id order by p.y)
  ),
  numeradas as (
    select
      q.*,
      sum(q.inicia_linha) over (
        partition by q.match_id, q.team_id order by q.y
        rows between unbounded preceding and current row
      ) as linha
    from quebras q
  ),
  linhas as (
    select match_id, team_id, linha, sum(n_jogadores)::int as n_jogadores
    from numeradas
    group by match_id, team_id, linha
  ),
  grade as (
    select
      match_id,
      team_id,
      array_agg(n_jogadores order by linha) as linhas
    from linhas
    group by match_id, team_id
  ),
  -- Linha 1 tem de ser o goleiro sozinho e ainda têm de sobrar >= 2 linhas de
  -- campo; qualquer coisa fora disso é grade que não sabemos ler (posição
  -- ausente, jogador desenhado fora da grade) e é descartada em silêncio.
  valida as (
    select
      g.match_id,
      g.team_id,
      g.linhas,
      g.linhas[2:array_length(g.linhas, 1)] as linhas_campo
    from grade g
    where g.linhas[1] = 1
      and array_length(g.linhas, 1) >= 3
  )
  insert into public.match_formation_fotmob as f (
    match_id, team_id, is_home, formation, formation_grade,
    n_linhas, n_def, n_mid, n_atk, fonte, derivado_em
  )
  select
    v.match_id,
    v.team_id,
    case when m.home_team_id = v.team_id then true
         when m.away_team_id = v.team_id then false
         else null end,
    array_to_string(v.linhas_campo, '-'),
    array_to_string(v.linhas, '-'),
    array_length(v.linhas_campo, 1),
    v.linhas_campo[1],
    -- n_mid = 10 - defesa - ataque: soma das linhas do meio sem precisar
    -- fatiar o array de novo (os 10 de linha sempre somam 10).
    10 - v.linhas_campo[1] - v.linhas_campo[array_length(v.linhas_campo, 1)],
    v.linhas_campo[array_length(v.linhas_campo, 1)],
    'layout_fotmob',
    now()
  from valida v
  join public.matches m on m.id = v.match_id
  on conflict (match_id, team_id) do update
    set is_home         = excluded.is_home,
        formation       = excluded.formation,
        formation_grade = excluded.formation_grade,
        n_linhas        = excluded.n_linhas,
        n_def           = excluded.n_def,
        n_mid           = excluded.n_mid,
        n_atk           = excluded.n_atk,
        fonte           = excluded.fonte,
        derivado_em     = excluded.derivado_em;

  get diagnostics v_gravadas = row_count;
  return v_gravadas;
end;
$$;

comment on function public.derivar_formacoes_fotmob(bigint[]) is
  'Deriva/atualiza match_formation_fotmob a partir da grade de titulares em match_lineup_fotmob. p_match_ids NULL = tudo (backfill); array = só esse lote (caminho de ingestão). Idempotente.';

revoke all on function public.derivar_formacoes_fotmob(bigint[]) from public, anon, authenticated;
grant execute on function public.derivar_formacoes_fotmob(bigint[]) to service_role;

-- =============================================================================
-- View de confronto -- o "como interagem entre si" na sua forma mais simples:
-- uma linha por partida com a formação dos dois lados lado a lado.
-- =============================================================================
create or replace view public.v_confronto_formacoes as
select
  m.id                as match_id,
  m.league_id,
  m.season,
  m.match_date,
  m.home_team_id,
  m.away_team_id,
  fc.formation        as formacao_casa,
  ff.formation        as formacao_fora,
  fc.formation || ' x ' || ff.formation as confronto,
  fc.n_def as n_def_casa, fc.n_mid as n_mid_casa, fc.n_atk as n_atk_casa,
  ff.n_def as n_def_fora, ff.n_mid as n_mid_fora, ff.n_atk as n_atk_fora,
  -- Vantagem numérica no meio: a leitura tática mais usada de um confronto de
  -- formações (3 do meio contra 2 = um homem a mais na construção).
  fc.n_mid - ff.n_mid as saldo_meio_casa,
  -- Atacantes de um lado contra defensores do outro -- o outro par clássico.
  fc.n_atk - ff.n_def as pressao_ataque_casa,
  ff.n_atk - fc.n_def as pressao_ataque_fora,
  m.home_goals,
  m.away_goals,
  m.status
from public.matches m
join public.match_formation_fotmob fc on fc.match_id = m.id and fc.team_id = m.home_team_id
join public.match_formation_fotmob ff on ff.match_id = m.id and ff.team_id = m.away_team_id;

comment on view public.v_confronto_formacoes is
  'Uma linha por partida com a formação dos dois times lado a lado e os saldos setoriais do confronto (saldo de meio-campo, atacantes vs defensores). Base para estudar interação tática entre as equipes; só aparece partida em que os DOIS lados foram derivados.';
