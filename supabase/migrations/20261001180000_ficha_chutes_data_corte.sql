-- Ficha de chutes com DATA DE CORTE (point-in-time, sem vazamento), peso por
-- meia-vida e métricas de xGOT; mais série por temporada.
--
-- Evidência (walk-forward, cortes 01/08/2023, 2024 e 2025, previsão do gol da
-- temporada seguinte): o ganho de Brier da estimativa encolhida é minúsculo
-- (+0,2 a +0,5 x1e-4) e o decaimento NÃO ajuda -- meia-vida de 2 anos e sem decaimento
-- empatam ou vencem 6 meses/1 ano. Por isso o padrão é 2 anos. A estimativa bruta
-- (sem encolher) piora o Brier em 32-48 x1e-4: o encolhimento é obrigatório.
-- Persistência entre temporadas (>=60 chutes nas duas): distância média 0,89, setor
-- central 0,74, volume de chutes 0,51, precisão acima do esperado 0,15, gol acima 0,15.
--
-- xGOT (post-shot xG) existe só para chutes NO ALVO (160 mil; média 0,296 vs 0,292 de
-- gol -> bem calibrado). Medidas novas, ambas com confiabilidade por metades baixa
-- (0,16 e 0,13), por isso encolhimento forte:
--   colocação  = xGOT - E[xGOT | zona, cabeça, jogada, no alvo]  (qualidade do canto)
--   conversão  = gol - xGOT nos chutes no alvo                    (goleiro/sorte pesam)

-- 1) view: expõe xGOT e a data do jogo (colunas novas só no fim, requisito do CREATE OR REPLACE VIEW)
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
  s.xg::float8 as xg,
  s.xgot::float8 as xgot,
  m.match_date::date as d
from public.match_shots_fotmob s
join public.matches m on m.id = s.match_id
where s.x between 0 and 105
  and s.y between 0 and 68
  and s.xg is not null
  and coalesce(s.is_own_goal, false) = false
  and s.period is distinct from 'PenaltyShootout'
  and coalesce(s.situation, '') <> 'Penalty';

-- 2) baseline de xGOT por célula (média e variância entre chutes no alvo)
alter table public.chute_baseline_zona add column if not exists ex_xgot double precision;
alter table public.chute_baseline_zona add column if not exists var_xgot double precision;

create or replace function public.recalcular_baseline_chute()
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  delete from chute_baseline_zona where true;
  insert into chute_baseline_zona (z, cab, jogada, n, p_alvo, p_gol, ex_xgot, var_xgot)
  select z, cab, jogada, count(*)::int,
         sum(noalvo::int)::float8 / nullif(sum(desbl::int), 0),
         avg(g::int::float8),
         avg(xgot) filter (where noalvo and xgot is not null),
         var_pop(xgot) filter (where noalvo and xgot is not null)
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
           sum(b.p_gol * (1 - b.p_gol)) as var_gol,
           -- chutes no alvo com xGOT e baseline de xGOT
           sum((v.noalvo and v.xgot is not null and b.ex_xgot is not null)::int)::float8 as nt,
           sum((v.xgot - b.ex_xgot)) filter (where v.noalvo and v.xgot is not null and b.ex_xgot is not null) as soma_coloc,
           sum(b.var_xgot) filter (where v.noalvo and v.xgot is not null and b.ex_xgot is not null) as var_coloc,
           sum((v.g::int - v.xgot)) filter (where v.noalvo and v.xgot is not null and b.ex_xgot is not null) as soma_conv,
           sum(v.xgot * (1 - v.xgot)) filter (where v.noalvo and v.xgot is not null and b.ex_xgot is not null) as var_conv
    from v_chute_valido v
    join chute_baseline_zona b using (z, cab, jogada)
    where v.player_id is not null
    group by v.player_id
    having count(*) >= 150 and sum(v.desbl::int) > 0
  )
  insert into chute_baseline_meta (chave, valor, n_jogadores)
  select 'tau2_alvo',
         greatest(var_samp((alvo - alvo_esp) / nd) - avg(var_alvo / (nd * nd)), 1e-6), count(*)::int
  from j
  union all
  select 'tau2_gol',
         greatest(var_samp((gols - gol_esp) / n) - avg(var_gol / (n * n)), 1e-6), count(*)::int
  from j
  union all
  select 'tau2_coloc',
         greatest(var_samp(soma_coloc / nt) filter (where nt >= 30) - avg(var_coloc / (nt * nt)) filter (where nt >= 30), 1e-6),
         (count(*) filter (where nt >= 30))::int
  from j
  union all
  select 'tau2_conv',
         greatest(var_samp(soma_conv / nt) filter (where nt >= 30) - avg(var_conv / (nt * nt)) filter (where nt >= 30), 1e-6),
         (count(*) filter (where nt >= 30))::int
  from j;
end;
$$;

revoke all on function public.recalcular_baseline_chute() from public, anon, authenticated;

select public.recalcular_baseline_chute();

-- 3) encolhimento: dado Σ resíduo ponderado, Σw, Σw²·var e tau² -> {bruta, encolhida, ic, fator}
create or replace function public.shrink_chute(p_soma double precision, p_sw double precision, p_swv double precision, p_t2 double precision)
returns jsonb
language sql
immutable
as $$
  select case
    when p_sw is null or p_sw <= 0 or p_t2 is null then null
    else jsonb_build_object(
      'bruta', p_soma / p_sw,
      'encolhida', (p_t2 / (p_t2 + p_swv / (p_sw * p_sw))) * (p_soma / p_sw),
      'ic', 1.96 * sqrt(p_t2 * (p_swv / (p_sw * p_sw)) / (p_t2 + p_swv / (p_sw * p_sw))),
      'fator', p_t2 / (p_t2 + p_swv / (p_sw * p_sw))
    )
  end
$$;

-- 4) ficha com data de corte
drop function if exists public.ficha_chutes_jogador(bigint);

create or replace function public.ficha_chutes_jogador(
  p_player_id bigint,
  p_ate date default null,            -- corte: só chutes ANTERIORES a esta data (null = hoje)
  p_meia_vida_dias integer default 730 -- null/0 = sem decaimento
)
returns jsonb
language plpgsql
stable
set search_path = public
as $$
declare
  corte date := coalesce(p_ate, current_date);
  hl double precision := nullif(p_meia_vida_dias, 0);
  t2a double precision; t2g double precision; t2c double precision; t2v double precision;
  r record;
  zonas jsonb;
begin
  select valor into t2a from chute_baseline_meta where chave = 'tau2_alvo';
  select valor into t2g from chute_baseline_meta where chave = 'tau2_gol';
  select valor into t2c from chute_baseline_meta where chave = 'tau2_coloc';
  select valor into t2v from chute_baseline_meta where chave = 'tau2_conv';

  with x as (
    select v.*, b.p_alvo, b.p_gol, b.ex_xgot, b.var_xgot,
           case when hl is null then 1.0 else exp(-ln(2) * (corte - v.d) / hl) end as w,
           (v.noalvo and v.xgot is not null and b.ex_xgot is not null) as ot
    from v_chute_valido v
    join chute_baseline_zona b using (z, cab, jogada)
    where v.player_id = p_player_id and v.d < corte
  )
  select
    count(*)::float8 as n,
    sum(desbl::int)::float8 as nd,
    sum(noalvo::int)::float8 as alvo,
    sum(g::int)::float8 as gols,
    sum(xg) as sxg,
    sum(p_alvo * desbl::int) as alvo_esp,
    sum(p_gol) as gol_esp,
    avg(cab::int::float8) as pct_cab,
    avg(case when z % 2 = 0 then 1.0 else 0.0 end) as pct_central,
    avg(case when z / 2 <= 4 then 1.0 else 0.0 end) as pct_ate_22m,
    sum(ot::int)::float8 as nt,
    sum(xgot) filter (where ot) as xgot_soma,
    sum(ex_xgot) filter (where ot) as xgot_esp,
    sum(g::int) filter (where ot) as gols_ot,
    -- ponderados
    sum(w)::float8 as sw, sum(w * w) as sw2,
    -- precisão (no alvo entre não bloqueados)
    sum(w * desbl::int) as a_sw, sum(w * desbl::int * (noalvo::int - p_alvo)) as a_soma, sum(w * w * desbl::int * p_alvo * (1 - p_alvo)) as a_swv,
    -- gol vs baseline da zona
    sum(w * (g::int - p_gol)) as g_soma, sum(w * w * p_gol * (1 - p_gol)) as g_swv,
    -- colocação (xGOT - esperado) e conversão sobre xGOT, só no alvo
    sum(w) filter (where ot) as c_sw, sum(w * (xgot - ex_xgot)) filter (where ot) as c_soma, sum(w * w * var_xgot) filter (where ot) as c_swv,
    sum(w * (g::int - xgot)) filter (where ot) as v_soma, sum(w * w * xgot * (1 - xgot)) filter (where ot) as v_swv,
    min(d) as primeiro, max(d) as ultimo
  into r
  from x;

  if r.n is null or r.n = 0 then
    return jsonb_build_object('n', 0, 'corte', corte, 'meia_vida_dias', p_meia_vida_dias);
  end if;

  select coalesce(jsonb_agg(jsonb_build_object('z', z, 'n', n, 'gols', gols, 'alvo', alvo, 'desbl', desbl) order by z), '[]'::jsonb)
  into zonas
  from (
    select v.z, count(*) as n, sum(v.g::int) as gols, sum(v.noalvo::int) as alvo, sum(v.desbl::int) as desbl
    from v_chute_valido v
    where v.player_id = p_player_id and v.d < corte
    group by v.z
  ) q;

  return jsonb_build_object(
    'corte', corte, 'meia_vida_dias', p_meia_vida_dias,
    'primeiro_chute', r.primeiro, 'ultimo_chute', r.ultimo,
    'n', r.n, 'n_efetivo', (r.sw * r.sw) / r.sw2,
    'n_desbloqueados', r.nd, 'no_alvo', r.alvo, 'gols', r.gols, 'xg', r.sxg,
    'alvo_esperado', r.alvo_esp, 'gols_esperados', r.gol_esp,
    'n_no_alvo_xgot', r.nt, 'xgot', r.xgot_soma, 'xgot_esperado', r.xgot_esp, 'gols_no_alvo', r.gols_ot,
    'pct_cabeca', r.pct_cab, 'pct_central', r.pct_central, 'pct_ate_22m', r.pct_ate_22m,
    'zonas', zonas,
    'alvo', shrink_chute(r.a_soma, r.a_sw, r.a_swv, t2a),
    'gol', shrink_chute(r.g_soma, r.sw, r.g_swv, t2g),
    'colocacao', shrink_chute(r.c_soma, r.c_sw, r.c_swv, t2c),
    'conversao_xgot', shrink_chute(r.v_soma, r.c_sw, r.v_swv, t2v),
    'tau2', jsonb_build_object('alvo', t2a, 'gol', t2g, 'colocacao', t2c, 'conversao_xgot', t2v)
  );
end;
$$;

grant execute on function public.ficha_chutes_jogador(bigint, date, integer) to anon, authenticated;

-- 5) série por temporada (agosto-julho), cada temporada ISOLADA (sem peso nem dados de outras)
create or replace function public.serie_chutes_jogador(p_player_id bigint)
returns jsonb
language sql
stable
set search_path = public
as $$
  with x as (
    select extract(year from (v.d - interval '7 months'))::int as temp,
           v.desbl, v.noalvo, v.g, v.xgot, b.p_alvo, b.p_gol, b.ex_xgot, b.var_xgot,
           (v.noalvo and v.xgot is not null and b.ex_xgot is not null) as ot,
           v.z
    from v_chute_valido v
    join chute_baseline_zona b using (z, cab, jogada)
    where v.player_id = p_player_id
  ), t as (
    select 'tau2_alvo' k, valor from chute_baseline_meta where chave = 'tau2_alvo'
    union all select 'tau2_gol', valor from chute_baseline_meta where chave = 'tau2_gol'
    union all select 'tau2_coloc', valor from chute_baseline_meta where chave = 'tau2_coloc'
  ), a as (
    select temp,
      count(*) n, sum(desbl::int) nd, sum(g::int) gols, sum(ot::int) nt,
      avg(case when z % 2 = 0 then 1.0 else 0.0 end) pct_central,
      avg(z / 2) anel_medio,
      sum(desbl::int * (noalvo::int - p_alvo)) a_soma, sum(desbl::int * p_alvo * (1 - p_alvo)) a_swv,
      sum(g::int - p_gol) g_soma, sum(p_gol * (1 - p_gol)) g_swv,
      sum(xgot - ex_xgot) filter (where ot) c_soma, sum(var_xgot) filter (where ot) c_swv
    from x group by temp
  )
  select coalesce(jsonb_agg(jsonb_build_object(
    'temporada', temp, 'n', n, 'n_desbloqueados', nd, 'gols', gols, 'n_no_alvo_xgot', nt,
    'pct_central', pct_central,
    'alvo', shrink_chute(a_soma, nd::float8, a_swv, (select valor from t where k = 'tau2_alvo')),
    'gol', shrink_chute(g_soma, n::float8, g_swv, (select valor from t where k = 'tau2_gol')),
    'colocacao', shrink_chute(c_soma, nt::float8, c_swv, (select valor from t where k = 'tau2_coloc'))
  ) order by temp), '[]'::jsonb)
  from a
$$;

grant execute on function public.serie_chutes_jogador(bigint) to anon, authenticated;
grant select on public.v_chute_valido to anon, authenticated;
