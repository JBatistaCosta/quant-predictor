-- Achado 39: teste, só com o banco, de uma mudança no xG que o FotMob/Opta serve para os chutes.
-- Base: match_shots_fotmob (xg, x, y em metros 0-105 x 0-68, gol no ponto (105, 34)) x matches x leagues.
-- Recorte que isola o MODELO de xG da mistura de chutes: 5 grandes ligas, jogo corrido (situation = 'RegularPlay'), chute de pé, sem pênalti e sem gol contra,
-- faixas fixas de distância/ângulo ao gol. O xG médio de um recorte fixo é saída do modelo (sem ruído de acerto), então um degrau nele é mudança de modelo.
-- Cada consulta roda em < 60 s (statement_timeout); rode uma de cada vez.

-- 1) xG médio por trimestre, 14-22 m em cone central (<= 30 graus): o degrau aparece no trimestre 2021-T3.
set statement_timeout='58s';
with b as (select to_char(date_trunc('quarter', m.match_date),'YYYY-"T"Q') tri, sqrt(power(105-s.x,2)+power(34-s.y,2)) d,
                  degrees(atan2(abs(s.y-34), greatest(105-s.x,0.000001))) a, s.xg, (s.event_type='Goal')::int g
           from match_shots_fotmob s join matches m on m.id=s.match_id join leagues l on l.id=m.league_id
           where l.name in ('Bundesliga','La Liga','Ligue 1','Premier League','Serie A (Itália)') and s.situation='RegularPlay'
             and s.shot_type in ('RightFoot','LeftFoot') and not coalesce(s.is_own_goal,false) and s.xg is not null and m.match_date>='2020-09-01')
select tri, count(*) n, round(avg(xg)::numeric,4) xg_medio, round(avg(g)::numeric,4) gol_por_chute, round((sum(g)/sum(xg))::numeric,3) gols_sobre_xg
from b where a<=30 and d>=14 and d<22 group by 1 order by 1;

-- 2) o mesmo por semana, de abril a outubro de 2021 (todas as ligas), para localizar a data: mude o recorte de d/a para outras zonas.
-- with b as (select date_trunc('week', m.match_date)::date semana, ... where m.match_date>='2021-04-15' and m.match_date<'2021-10-15') select semana, count(*), avg(xg) ... group by 1 order by 1;

-- 3) xG médio e gols por chute por zona, trimestres de 2020-T4 a 2022-T1 (mesmo recorte): ver a tabela do Achado 39.
-- case when a<=30 and d<6 then 'central 0-6' ... end zona, tri, count(*), avg(xg), avg(g) ... group by 1,2 order by 1,2;
