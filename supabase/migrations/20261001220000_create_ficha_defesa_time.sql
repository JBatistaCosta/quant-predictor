-- Ficha DEFENSIVA: fragilidade de um time por zona de chute (chutes / no alvo / gols
-- sofridos), com data de corte, peso por meia-vida e encolhimento.
--
-- Evidência (234 times com >=40 jogos; metades = jogos pares x ímpares): o que uma defesa
-- permite é CARACTERÍSTICA DO TIME e estável -- chutes sofridos por jogo 0,80, % de
-- chutes de até 16,5 m 0,78, xG por chute sofrido 0,72 -- mas o que acontece DEPOIS do
-- chute (no alvo acima do esperado 0,19; gol acima do esperado 0,24) é fraco (goleiro e
-- sorte pesam). Por isso: volume por zona = razão com IC; qualidade = resíduo encolhido.
--
-- Controle de força do adversário (o erro clássico do projeto): o "esperado" de cada jogo é o
-- ataque MÉDIO do adversário por zona ANTES daquele jogo (point-in-time, mín. 10 jogos).
-- Razão > 1 = o time permite mais do que os adversários costumam produzir.
-- NÃO controla estado do jogo (quem lidera sofre mais chutes) nem mando de campo.

-- 1) uma linha por (time que DEFENDEU, jogo, zona)
drop materialized view if exists public.mv_chute_def_jogo;
create materialized view public.mv_chute_def_jogo as
with sf as (
  select s.team_id, v.match_id, v.d, v.z,
         count(*)::float8 as n,
         sum(v.desbl::int)::float8 as desbl,
         sum(v.noalvo::int)::float8 as alvo,
         sum(v.g::int)::float8 as gols,
         sum(v.xg) as xg,
         sum(b.p_alvo * v.desbl::int) as esp_alvo,
         sum(b.p_gol) as esp_gol
  from public.v_chute_valido v
  join public.match_shots_fotmob s on s.id = v.id
  join public.chute_baseline_zona b using (z, cab, jogada)
  group by s.team_id, v.match_id, v.d, v.z
), tm as (
  select distinct team_id, match_id, d from sf
), grid as (
  select tm.team_id, tm.match_id, tm.d, zz.z::smallint as z
  from tm cross join generate_series(0, 13) as zz(z)
), g2 as (
  select grid.team_id, grid.match_id, grid.d, grid.z, coalesce(sf.n, 0) as n
  from grid
  left join sf on sf.team_id = grid.team_id and sf.match_id = grid.match_id and sf.z = grid.z
), cum as (
  select team_id, match_id, d, z,
         sum(n) over w as cum_n,
         (dense_rank() over (partition by team_id, z order by d, match_id) - 1) as jogos_antes
  from g2
  window w as (partition by team_id, z order by d, match_id rows between unbounded preceding and 1 preceding)
)
select
  case when a.team_id = m.home_team_id then m.away_team_id else m.home_team_id end as def_team_id,
  a.team_id as atq_team_id,
  a.match_id,
  a.d,
  a.z,
  a.jogos_antes::int as atq_jogos_antes,
  case when a.jogos_antes >= 10 then coalesce(a.cum_n, 0) / a.jogos_antes end as esp_n,
  coalesce(sf.n, 0) as conc_n,
  coalesce(sf.desbl, 0) as conc_desbl,
  coalesce(sf.alvo, 0) as conc_alvo,
  coalesce(sf.gols, 0) as conc_gols,
  coalesce(sf.xg, 0) as conc_xg,
  coalesce(sf.esp_alvo, 0) as esp_alvo,
  coalesce(sf.esp_gol, 0) as esp_gol
from cum a
join public.matches m on m.id = a.match_id
left join sf on sf.team_id = a.team_id and sf.match_id = a.match_id and sf.z = a.z;

create index mv_chute_def_jogo_def_d_idx on public.mv_chute_def_jogo (def_team_id, d);
grant select on public.mv_chute_def_jogo to anon, authenticated;

-- 2) razão ponderada (cluster por jogo): R = Σw·x / Σw·y; var = Σw²(x-R·y)² / (Σw·y)² · G/(G-1)
create or replace function public.shrink_razao(
  p_r double precision, p_var double precision, p_t2 double precision, p_centro double precision
) returns jsonb
language sql
immutable
as $$
  select case
    when p_r is null or p_var is null or p_t2 is null then null
    else jsonb_build_object(
      'bruta', p_r,
      'encolhida', p_centro + (p_t2 / (p_t2 + p_var)) * (p_r - p_centro),
      'ic', 1.96 * sqrt(p_t2 * p_var / (p_t2 + p_var)),
      'fator', p_t2 / (p_t2 + p_var)
    )
  end
$$;

-- 3) atualização (fotografia): refaz a view e as variâncias ENTRE times (tau^2)
create or replace function public.recalcular_chute_def()
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  refresh materialized view public.mv_chute_def_jogo;

  delete from chute_baseline_meta where chave like 'tau2_def_%';

  -- volume total
  insert into chute_baseline_meta (chave, valor, n_jogadores)
  with pm as (
    select def_team_id, match_id, sum(conc_n) cn, sum(esp_n) en
    from mv_chute_def_jogo where esp_n is not null group by 1, 2
  ), r as (
    select def_team_id, sum(cn) / nullif(sum(en), 0) rr from pm group by 1
  ), v as (
    select pm.def_team_id, count(*)::float8 g, r.rr,
           sum(power(pm.cn - r.rr * pm.en, 2)) / power(sum(pm.en), 2) * count(*) / nullif(count(*) - 1, 0) as var
    from pm join r using (def_team_id) group by pm.def_team_id, r.rr
    having count(*) >= 40
  )
  select 'tau2_def_vol_tot',
         greatest(var_samp(rr) - avg(var), 1e-6), count(*)::int
  from v;

  -- volume por zona (0..13)
  insert into chute_baseline_meta (chave, valor, n_jogadores)
  with r as (
    select def_team_id, z, sum(conc_n) / nullif(sum(esp_n), 0) rr
    from mv_chute_def_jogo where esp_n is not null group by 1, 2
  ), v as (
    select m.def_team_id, m.z, r.rr, count(*)::float8 g,
           sum(power(m.conc_n - r.rr * m.esp_n, 2)) / nullif(power(sum(m.esp_n), 2), 0) * count(*) / nullif(count(*) - 1, 0) as var
    from mv_chute_def_jogo m join r on r.def_team_id = m.def_team_id and r.z = m.z
    where m.esp_n is not null
    group by m.def_team_id, m.z, r.rr
    having count(*) >= 40 and sum(m.esp_n) > 0
  )
  select 'tau2_def_vol_z' || z, greatest(var_samp(rr) - avg(var), 1e-6), count(*)::int
  from v group by z;

  -- qualidade: no alvo e gol acima do esperado por chute sofrido
  insert into chute_baseline_meta (chave, valor, n_jogadores)
  with pm as (
    select def_team_id, match_id, sum(conc_desbl) cd, sum(conc_alvo) ca, sum(conc_gols) cg, sum(conc_n) cn,
           sum(esp_alvo) ea, sum(esp_gol) eg
    from mv_chute_def_jogo where esp_n is not null group by 1, 2
  ), r as (
    select def_team_id,
           (sum(ca) - sum(ea)) / nullif(sum(cd), 0) ra,
           (sum(cg) - sum(eg)) / nullif(sum(cn), 0) rg
    from pm group by 1
  ), v as (
    select pm.def_team_id, count(*)::float8 g, r.ra, r.rg,
           sum(power((pm.ca - pm.ea) - r.ra * pm.cd, 2)) / nullif(power(sum(pm.cd), 2), 0) * count(*) / nullif(count(*) - 1, 0) as va,
           sum(power((pm.cg - pm.eg) - r.rg * pm.cn, 2)) / nullif(power(sum(pm.cn), 2), 0) * count(*) / nullif(count(*) - 1, 0) as vg
    from pm join r using (def_team_id) group by pm.def_team_id, r.ra, r.rg
    having count(*) >= 40
  )
  select 'tau2_def_alvo', greatest(var_samp(ra) - avg(va), 1e-6), count(*)::int from v
  union all
  select 'tau2_def_gol', greatest(var_samp(rg) - avg(vg), 1e-6), count(*)::int from v;
end;
$$;

revoke all on function public.recalcular_chute_def() from public, anon, authenticated;

-- 4) ficha defensiva de um time (id de public.teams), point-in-time (só jogos ANTERIORES ao corte)
create or replace function public.ficha_defesa_time(
  p_team_id bigint,
  p_ate date default null,            -- null = hoje
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
  t_tot double precision; t_alvo double precision; t_gol double precision;
  res jsonb;
begin
  select valor into t_tot from chute_baseline_meta where chave = 'tau2_def_vol_tot';
  select valor into t_alvo from chute_baseline_meta where chave = 'tau2_def_alvo';
  select valor into t_gol from chute_baseline_meta where chave = 'tau2_def_gol';

  with b as (
    select m.*, case when hl is null then 1.0 else exp(-ln(2) * (corte - m.d) / hl) end as w
    from mv_chute_def_jogo m
    where m.def_team_id = p_team_id and m.d < corte and m.esp_n is not null
  ), pm as (
    select match_id, min(d) as d, min(w) as w,
           sum(conc_n) as cn, sum(esp_n) as en, sum(conc_desbl) as cd, sum(conc_alvo) as ca, sum(conc_gols) as cg,
           sum(esp_alvo) as ea, sum(esp_gol) as eg, sum(conc_xg) as cx
    from b group by match_id
  ), rt as (
    select count(*)::float8 as g, sum(w) as sw, sum(w * w) as sw2,
           min(d) as primeiro, max(d) as ultimo,
           sum(w * cn) as wcn, sum(w * en) as wen, sum(w * cd) as wcd, sum(w * ca) as wca, sum(w * cg) as wcg,
           sum(w * ea) as wea, sum(w * eg) as weg, sum(w * cx) as wcx,
           sum(w * cn) / nullif(sum(w * en), 0) as rv,
           sum(w * (ca - ea)) / nullif(sum(w * cd), 0) as ra,
           sum(w * (cg - eg)) / nullif(sum(w * cn), 0) as rg
    from pm
  ), vt as (
    select rt.*,
      sum(pm.w * pm.w * power(pm.cn - rt.rv * pm.en, 2)) / nullif(power(rt.wen, 2), 0) * rt.g / nullif(rt.g - 1, 0) as var_v,
      sum(pm.w * pm.w * power((pm.ca - pm.ea) - rt.ra * pm.cd, 2)) / nullif(power(rt.wcd, 2), 0) * rt.g / nullif(rt.g - 1, 0) as var_a,
      sum(pm.w * pm.w * power((pm.cg - pm.eg) - rt.rg * pm.cn, 2)) / nullif(power(rt.wcn, 2), 0) * rt.g / nullif(rt.g - 1, 0) as var_g
    from pm, rt
    group by rt.g, rt.sw, rt.sw2, rt.primeiro, rt.ultimo, rt.wcn, rt.wen, rt.wcd, rt.wca, rt.wcg, rt.wea, rt.weg, rt.wcx, rt.rv, rt.ra, rt.rg
  ), rz as (
    select z, count(*)::float8 as g,
           sum(w * conc_n) as wc, sum(w * esp_n) as we, sum(w * conc_alvo) as wa, sum(w * conc_gols) as wg, sum(w * esp_gol) as weg,
           sum(w * conc_n) / nullif(sum(w * esp_n), 0) as rr
    from b group by z
  ), vz as (
    select rz.z, rz.rr, rz.wc, rz.we, rz.wa, rz.wg, rz.weg,
      sum(b.w * b.w * power(b.conc_n - rz.rr * b.esp_n, 2)) / nullif(power(rz.we, 2), 0) * rz.g / nullif(rz.g - 1, 0) as var
    from b join rz using (z)
    group by rz.z, rz.rr, rz.wc, rz.we, rz.wa, rz.wg, rz.weg, rz.g
  )
  select jsonb_build_object(
    'corte', corte, 'meia_vida_dias', p_meia_vida_dias,
    'primeiro_jogo', vt.primeiro, 'ultimo_jogo', vt.ultimo,
    'jogos', vt.g, 'jogos_efetivos', (vt.sw * vt.sw) / vt.sw2,
    'chutes_sofridos_por_jogo', vt.wcn / vt.sw,
    'chutes_esperados_por_jogo', vt.wen / vt.sw,
    'no_alvo_sofridos_por_jogo', vt.wca / vt.sw,
    'gols_sofridos_por_jogo', vt.wcg / vt.sw,
    'xg_por_chute_sofrido', vt.wcx / nullif(vt.wcn, 0),
    'volume', shrink_razao(vt.rv, vt.var_v, t_tot, 1),
    'alvo_acima', shrink_razao(vt.ra, vt.var_a, t_alvo, 0),
    'gol_acima', shrink_razao(vt.rg, vt.var_g, t_gol, 0),
    'zonas', coalesce((
      select jsonb_agg(jsonb_build_object(
        'z', vz.z,
        'chutes_por_jogo', vz.wc / vt.sw,
        'esperado_por_jogo', vz.we / vt.sw,
        'no_alvo_por_jogo', vz.wa / vt.sw,
        'gols_por_jogo', vz.wg / vt.sw,
        'gols_esperados_por_jogo', vz.weg / vt.sw,
        'razao', shrink_razao(vz.rr, vz.var,
                   (select valor from chute_baseline_meta where chave = 'tau2_def_vol_z' || vz.z), 1)
      ) order by vz.z)
      from vz
    ), '[]'::jsonb)
  )
  into res
  from vt;

  return coalesce(res, jsonb_build_object('jogos', 0, 'corte', corte, 'meia_vida_dias', p_meia_vida_dias));
end;
$$;

grant execute on function public.ficha_defesa_time(bigint, date, integer) to anon, authenticated;
grant execute on function public.shrink_razao(double precision, double precision, double precision, double precision) to anon, authenticated;
