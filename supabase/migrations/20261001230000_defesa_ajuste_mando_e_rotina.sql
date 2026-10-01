-- Ficha defensiva, parte 2: (1) ajuste de MANDO DE CAMPO no esperado e (2) rotina diária de
-- atualização dentro do banco (pg_cron).
--
-- (1) Teste (01/10/2026, 34.689 defesas-jogo): quem defende EM CASA sofre 0,907x o esperado
--     (11,25 chutes vs 12,41) e FORA 1,128x (13,96 vs 12,38) -- o "esperado" (ataque médio do
--     adversário, misturando casa e fora) ignorava isso. No alvo/gol acima do esperado NÃO
--     dependem do mando (-0,0002/+0,0021 e -0,0006/+0,0009). Agora `esp_n` é multiplicado pelo
--     fator global do mando do DEFENSOR (calculado na própria atualização, sem constante fixa).
--     Efeito: confiabilidade por metades da razão 0,823 -> 0,835.
--
-- ESTADO DO JOGO (testado, NÃO corrigido): quem lidera sofre mais chutes (14,0/90 min vs 12,0
-- empatado vs 10,9 perdendo), mas o ranking dos times é praticamente o mesmo olhando só o
-- estado empatado (corr 0,88 entre chutes sofridos/90 empatado e a razão) -- viés de segunda
-- ordem (~17% x variação de ~10 p.p. no tempo liderando = ~2% da razão, contra dp entre
-- times de 12%). A razão correlaciona -0,71 com % do tempo liderando porque times bons lideram
-- mais E sofrem menos chutes: o índice mede "chutes permitidos", que mistura estrutura
-- defensiva e domínio de jogo -- não é "habilidade defensiva pura".
--
-- Meia-vida (walk-forward, cortes 01/08/2023-25, previsão dos chutes sofridos por jogo da
-- temporada seguinte, ganho de MSE vs o esperado sem a razão do time): 2 anos é o melhor
-- (2024, 2025) ou empata dentro do ruído (2023) com 6 meses, 1 ano, 4 anos e sem decaimento
-- -> mantido o padrão de 730 dias. Ganho pequeno mas real (~+0,6 a +1,2 de ~30 de MSE).

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
), linhas as (
  select
    case when a.team_id = m.home_team_id then m.away_team_id else m.home_team_id end as def_team_id,
    a.team_id as atq_team_id,
    (a.team_id <> m.home_team_id) as def_casa,   -- o DEFENSOR joga em casa
    a.match_id,
    a.d,
    a.z,
    a.jogos_antes::int as atq_jogos_antes,
    case when a.jogos_antes >= 10 then coalesce(a.cum_n, 0) / a.jogos_antes end as esp_bruto,
    coalesce(sf.n, 0) as conc_n,
    coalesce(sf.desbl, 0) as conc_desbl,
    coalesce(sf.alvo, 0) as conc_alvo,
    coalesce(sf.gols, 0) as conc_gols,
    coalesce(sf.xg, 0) as conc_xg,
    coalesce(sf.esp_alvo, 0) as esp_alvo,
    coalesce(sf.esp_gol, 0) as esp_gol
  from cum a
  join public.matches m on m.id = a.match_id
  left join sf on sf.team_id = a.team_id and sf.match_id = a.match_id and sf.z = a.z
  -- só os dois times da partida (32 chutes em 3 jogos têm team_id de um terceiro time e
  -- duplicariam a chave defensor+jogo+zona)
  where a.team_id in (m.home_team_id, m.away_team_id)
), fator as (   -- fator global do mando do defensor: Σ sofridos / Σ esperado
  select def_casa, sum(conc_n) / nullif(sum(esp_bruto), 0) as f
  from linhas where esp_bruto is not null group by def_casa
)
select l.def_team_id, l.atq_team_id, l.def_casa, l.match_id, l.d, l.z, l.atq_jogos_antes,
       l.esp_bruto * f.f as esp_n,
       l.conc_n, l.conc_desbl, l.conc_alvo, l.conc_gols, l.conc_xg, l.esp_alvo, l.esp_gol
from linhas l
left join fator f using (def_casa);

create unique index mv_chute_def_jogo_pk on public.mv_chute_def_jogo (def_team_id, match_id, z);
create index mv_chute_def_jogo_def_d_idx on public.mv_chute_def_jogo (def_team_id, d);
grant select on public.mv_chute_def_jogo to anon, authenticated;

-- refresh CONCURRENTLY: a ficha continua legível durante a atualização (~15 s)
create or replace function public.recalcular_chute_def()
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  refresh materialized view concurrently public.mv_chute_def_jogo;

  delete from chute_baseline_meta where chave like 'tau2_def_%';

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

select public.recalcular_chute_def();

-- (2) Rotina diária DENTRO do banco. Por quê não via Vercel/PostgREST: o `authenticator` tem
-- statement_timeout de 8 s e um SET dentro da função não prolonga um comando já em curso
-- (testado); os recálculos levam ~6 s (baseline) e ~20 s (defesa). pg_cron roda como postgres,
-- sem esse limite. 08:00 UTC = depois do sync-matches (06:00) e do Elo (07:00).
-- Auditar: select * from cron.job_run_details order by start_time desc limit 10;
do $$
begin
  create extension if not exists pg_cron with schema pg_catalog;
  perform cron.schedule(
    'recalcular-chutes-diario',
    '0 8 * * *',
    $cmd$select public.recalcular_baseline_chute(); select public.recalcular_chute_def();$cmd$
  );
exception when others then
  raise notice 'pg_cron indisponivel neste ambiente (%): agende recalcular_baseline_chute() e recalcular_chute_def() manualmente', sqlerrm;
end
$$;
