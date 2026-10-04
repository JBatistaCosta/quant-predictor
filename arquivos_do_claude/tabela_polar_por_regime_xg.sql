-- Tabela polar de chutes (14 zonas da Análise de Evento) a partir do banco, por regime de xG do FotMob.
-- Método do Achado 40, com o filtro de liga explícito (Achado 44): rode UMA vez por combinação de filtro, trocando o bloco FILTRO.
--   regime novo   : m.match_date >= '2021-08-01'   |  regime antigo: m.match_date < '2021-08-01'
--   só clubes     : l.type <> 'international'      |  todas as ligas: true
-- Chutes sem pênalti (situation = 'Penalty'), sem gol contra, sem disputa de pênaltis (period = 'PenaltyShootout'), com x, y e xG.
-- Zona = 7 anéis de distância ao centro do gol (105, 34) m: <6, 6-9, 9-12, 12-16,5, 16,5-22, 22-30, >=30  x  cone central (<= 30 graus) ou aberto.
-- IC95 = 1,96 x erro-padrão robusto a agrupamento por partida (estimador de razão: sum_m (a_m - r*n_m)^2 / (sum n)^2).
-- Saída: zona (0..13, na ordem de NOMES em scripts/zonas_polares.py), chutes, pct, pct_ic95, xg_chute, xg_ic95, gol_chute, gol_ic95; jogos e chutes totais na linha zona = -1.
set statement_timeout='58s';
with b as (
  select s.match_id, sqrt(power(105-s.x,2)+power(34-s.y,2)) d,
         degrees(atan2(abs(s.y-34), greatest(105-s.x,0.000001))) a, s.xg, (s.event_type='Goal')::int g
  from match_shots_fotmob s join matches m on m.id=s.match_id join leagues l on l.id=m.league_id
  where /* FILTRO */ m.match_date >= '2021-08-01' and l.type <> 'international'
    and coalesce(s.period,'')<>'PenaltyShootout' and not coalesce(s.is_own_goal,false) and s.situation is distinct from 'Penalty'
    and s.xg is not null and s.x is not null and s.y is not null),
z as (select match_id, xg, g,
        (case when d<6 then 0 when d<9 then 1 when d<12 then 2 when d<16.5 then 3 when d<22 then 4 when d<30 then 5 else 6 end)*2
        + (case when a<=30 then 0 else 1 end) zona from b),
nm as (select match_id, count(*) nt from b group by 1),
tot as (select count(*) jogos, sum(nt) sn, sum(nt::numeric*nt) sn2 from nm),
pm as (select zona, match_id, count(*) n, sum(xg) sx, sum(g) sg from z group by 1,2),
ag as (select pm.zona, sum(n) n, sum(sx) sx, sum(sg) sg, sum(n::numeric*n) nn, sum(n::numeric*nm.nt) nnt
       from pm join nm using(match_id) group by 1),
res as (select pm.zona, sum(power(pm.sx-(ag.sx/ag.n)*pm.n,2)) rx, sum(power(pm.sg-(ag.sg/ag.n)*pm.n,2)) rg
        from pm join ag using(zona) group by 1)
select ag.zona, ag.n chutes, round((100.0*ag.n/tot.sn)::numeric,2) pct,
       round((196*sqrt(greatest(ag.nn - 2*(ag.n::numeric/tot.sn)*ag.nnt + power(ag.n::numeric/tot.sn,2)*tot.sn2,0))/tot.sn)::numeric,2) pct_ic95,
       round((ag.sx/ag.n)::numeric,4) xg_chute, round((1.96*sqrt(res.rx)/ag.n)::numeric,4) xg_ic95,
       round((ag.sg::numeric/ag.n),4) gol_chute, round((1.96*sqrt(res.rg)/ag.n)::numeric,4) gol_ic95
from ag join res using(zona) cross join tot
union all select -1, tot.sn, tot.jogos, null, null, null, null, null from tot
order by 1;
