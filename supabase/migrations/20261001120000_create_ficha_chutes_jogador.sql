-- Ficha de chutes por jogador: setor preferido, precisão (no alvo) e gol, COMPARADOS
-- com o esperado para quem chuta das mesmas posições, com encolhimento (empirical
-- Bayes) para jogadores com poucos chutes.
--
-- Regra "posição -> zona" mora SÓ em zona_chute() (14 zonas polares: 7 anéis de
-- distância ao CENTRO do gol x 2 setores, central <=30° / aberto >30° do eixo do
-- campo; índice = anel*2 + setor). Espelha ZONAS_CHUTE de
-- src/utils/zoneTransitionMatrix.js -- mudar uma exige mudar a outra.
--
-- Por que comparar com o esperado em vez de usar a taxa bruta: a taxa bruta de gol
-- é quase só função da distância (corr -0,70 entre distância média e gol/chute);
-- controlando zona x cabeça/pé x bola rolando/parada, a preferência de setor não
-- prevê mais precisão nem gol (|r| <= 0,09 em 436 jogadores com >=200 chutes).
-- Habilidade individual existe mas é modesta (confiabilidade por metades: 0,31 no
-- alvo, 0,20 no gol) -- por isso o encolhimento: sem ele, 200 chutes viram
-- "craque"/"perna de pau" só por sorte.
--
-- "No alvo" = gol OU defesa do goleiro NÃO bloqueada. O FotMob marca chutes
-- bloqueados como is_on_target=true (127 mil), então is_on_target cru está errado.
-- Precisão é calculada entre os chutes NÃO bloqueados (bloqueio é majoritariamente
-- posição/defesa, não pontaria).

create or replace function public.zona_chute(p_x numeric, p_y numeric)
returns smallint
language sql
immutable
parallel safe
as $$
  select (
    width_bucket(
      sqrt(((105 - p_x)^2 + (p_y - 34)^2))::float8,
      array[6, 9, 12, 16.5, 22, 30]::float8[]
    ) * 2
    + case when degrees(atan2(abs(p_y - 34)::float8, greatest(105 - p_x, 0.001)::float8)) <= 30 then 0 else 1 end
  )::smallint
$$;

-- Chutes válidos: sem pênalti, gol contra e disputa de pênaltis; com coordenada e xG.
create or replace view public.v_chute_valido as
select
  s.id,
  s.match_id,
  s.player_id,
  public.zona_chute(s.x, s.y) as z,
  (s.shot_type = 'Header') as cab,
  (s.situation in ('RegularPlay', 'FastBreak', 'IndividualPlay')) as jogada,
  (not coalesce(s.is_blocked, false)) as desbl,
  (s.event_type = 'Goal' or (s.event_type = 'AttemptSaved' and not coalesce(s.is_blocked, false))) as noalvo,
  (s.event_type = 'Goal') as g,
  s.xg::float8 as xg
from public.match_shots_fotmob s
where s.x between 0 and 105
  and s.y between 0 and 68
  and s.xg is not null
  and coalesce(s.is_own_goal, false) = false
  and s.period is distinct from 'PenaltyShootout'
  and coalesce(s.situation, '') <> 'Penalty';

-- Taxa esperada por (zona, cabeça?, bola rolando?) em TODA a base.
create table if not exists public.chute_baseline_zona (
  z smallint not null,
  cab boolean not null,
  jogada boolean not null,
  n integer not null,
  p_alvo double precision not null,  -- no alvo entre os NÃO bloqueados
  p_gol double precision not null,   -- gol por chute
  primary key (z, cab, jogada)
);

-- Variância ENTRE jogadores (tau^2) da taxa acima do esperado; alimenta o encolhimento.
create table if not exists public.chute_baseline_meta (
  chave text primary key,
  valor double precision not null,
  n_jogadores integer,
  atualizado_em timestamptz not null default now()
);

alter table public.chute_baseline_zona enable row level security;
alter table public.chute_baseline_meta enable row level security;
drop policy if exists "leitura publica" on public.chute_baseline_zona;
drop policy if exists "leitura publica" on public.chute_baseline_meta;
create policy "leitura publica" on public.chute_baseline_zona for select using (true);
create policy "leitura publica" on public.chute_baseline_meta for select using (true);

-- Fotografia da base: refazer quando a base de chutes crescer muito.
create or replace function public.recalcular_baseline_chute()
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  delete from chute_baseline_zona where true;
  insert into chute_baseline_zona (z, cab, jogada, n, p_alvo, p_gol)
  select z, cab, jogada, count(*)::int,
         sum(noalvo::int)::float8 / nullif(sum(desbl::int), 0),
         avg(g::int::float8)
  from v_chute_valido
  group by z, cab, jogada
  having sum(desbl::int) > 0;

  delete from chute_baseline_meta where true;
  with j as (
    select v.player_id,
           count(*)::float8 as n,
           sum(v.desbl::int)::float8 as nd,
           sum(v.noalvo::int)::float8 as alvo,
           sum(v.g::int)::float8 as gols,
           sum(b.p_alvo * v.desbl::int) as alvo_esp,
           sum(b.p_gol) as gol_esp,
           sum(b.p_alvo * (1 - b.p_alvo) * v.desbl::int) as var_alvo,
           sum(b.p_gol * (1 - b.p_gol)) as var_gol
    from v_chute_valido v
    join chute_baseline_zona b using (z, cab, jogada)
    where v.player_id is not null
    group by v.player_id
    having count(*) >= 150 and sum(v.desbl::int) > 0
  )
  insert into chute_baseline_meta (chave, valor, n_jogadores)
  select 'tau2_alvo',
         greatest(var_samp((alvo - alvo_esp) / nd) - avg(var_alvo / (nd * nd)), 1e-6),
         count(*)::int
  from j
  union all
  select 'tau2_gol',
         greatest(var_samp((gols - gol_esp) / n) - avg(var_gol / (n * n)), 1e-6),
         count(*)::int
  from j;
end;
$$;

revoke all on function public.recalcular_baseline_chute() from public, anon, authenticated;

select public.recalcular_baseline_chute();

-- Ficha de um jogador (id de public.players).
create or replace function public.ficha_chutes_jogador(p_player_id bigint)
returns jsonb
language plpgsql
stable
set search_path = public
as $$
declare
  t2a double precision;
  t2g double precision;
  r record;
  zonas jsonb;
  v_resid_a double precision; v_s2a double precision; v_ka double precision;
  v_resid_g double precision; v_s2g double precision; v_kg double precision;
begin
  select valor into t2a from chute_baseline_meta where chave = 'tau2_alvo';
  select valor into t2g from chute_baseline_meta where chave = 'tau2_gol';

  select
    count(*)::float8 as n,
    sum(v.desbl::int)::float8 as nd,
    sum(v.noalvo::int)::float8 as alvo,
    sum(v.g::int)::float8 as gols,
    sum(v.xg) as sxg,
    sum(b.p_alvo * v.desbl::int) as alvo_esp,
    sum(b.p_gol) as gol_esp,
    sum(b.p_alvo * (1 - b.p_alvo) * v.desbl::int) as var_alvo,
    sum(b.p_gol * (1 - b.p_gol)) as var_gol,
    avg(v.cab::int::float8) as pct_cab,
    avg(case when v.z % 2 = 0 then 1.0 else 0.0 end) as pct_central,
    avg(case when v.z / 2 <= 4 then 1.0 else 0.0 end) as pct_ate_22m
  into r
  from v_chute_valido v
  join chute_baseline_zona b using (z, cab, jogada)
  where v.player_id = p_player_id;

  if r.n is null or r.n = 0 then
    return jsonb_build_object('n', 0);
  end if;

  select coalesce(jsonb_agg(jsonb_build_object('z', z, 'n', n, 'gols', gols, 'alvo', alvo, 'desbl', desbl) order by z), '[]'::jsonb)
  into zonas
  from (
    select v.z, count(*) as n, sum(v.g::int) as gols, sum(v.noalvo::int) as alvo, sum(v.desbl::int) as desbl
    from v_chute_valido v
    where v.player_id = p_player_id
    group by v.z
  ) q;

  if r.nd > 0 then
    v_resid_a := (r.alvo - r.alvo_esp) / r.nd;
    v_s2a := r.var_alvo / (r.nd * r.nd);
    v_ka := t2a / (t2a + v_s2a);
  end if;
  v_resid_g := (r.gols - r.gol_esp) / r.n;
  v_s2g := r.var_gol / (r.n * r.n);
  v_kg := t2g / (t2g + v_s2g);

  return jsonb_build_object(
    'n', r.n, 'n_desbloqueados', r.nd, 'no_alvo', r.alvo, 'gols', r.gols, 'xg', r.sxg,
    'alvo_esperado', r.alvo_esp, 'gols_esperados', r.gol_esp,
    'pct_cabeca', r.pct_cab, 'pct_central', r.pct_central, 'pct_ate_22m', r.pct_ate_22m,
    'zonas', zonas,
    -- acima do esperado por chute: observado, encolhido e IC95% (meia-largura) do encolhido
    'alvo_acima', v_resid_a,
    'alvo_acima_encolhido', v_ka * v_resid_a,
    'alvo_acima_ic', 1.96 * sqrt(t2a * v_s2a / (t2a + v_s2a)),
    'alvo_fator_encolhimento', v_ka,
    'gol_acima', v_resid_g,
    'gol_acima_encolhido', v_kg * v_resid_g,
    'gol_acima_ic', 1.96 * sqrt(t2g * v_s2g / (t2g + v_s2g)),
    'gol_fator_encolhimento', v_kg,
    'tau2_alvo', t2a, 'tau2_gol', t2g
  );
end;
$$;

grant execute on function public.ficha_chutes_jogador(bigint) to anon, authenticated;
grant select on public.v_chute_valido to anon, authenticated;
