-- DISTRIBUICAO_ZONA_CHUTE (src/utils/zoneTransitionMatrix.js): para cada zona 3x3 de origem do chute,
-- a fração dos chutes em cada uma das 14 zonas polares. Achado 45. Rode como está (cerca de 10 s).
-- Base: match_shots_fotmob x matches x leagues, SÓ LIGAS DE CLUBES (leagues.type <> 'international'), jogos desde 2021-08-01
-- (a mesma base de ESTATISTICA_ZONA_CHUTE, Achados 40 e 44), sem pênalti, gol contra e disputa de pênaltis.
-- Zona 3x3 = terço de comprimento (x < 35 defesa, 35-70 meio, >= 70 ataque) * 3 + terço de largura (y < 22,67 esquerda, < 45,33 centro, resto direita);
-- gol em (105, 34). Zona polar = 7 anéis (<6, 6-9, 9-12, 12-16,5, 16,5-22, 22-30, >=30 m) x cone central (<= 30 graus) ou aberto: zona = anel*2 + (0 central | 1 aberto).
-- Saída: xi (0 def, 1 meio, 2 atq), yi (0 esq, 1 cen, 2 dir), zona polar (0..13), n. A linha da matriz é 3*xi + yi; divida cada n pelo total da linha.
-- Para a base de 01/10 (todas as ligas e datas, antes do Achado 42), troque o WHERE por: not (l.id=22 and m.season='2020') and not (l.id=27 and m.season='2022').
set statement_timeout='58s';
with b as (
  select s.x, s.y, sqrt(power(105-s.x,2)+power(34-s.y,2)) d,
         degrees(atan2(abs(s.y-34), greatest(105-s.x,0.000001))) a
  from match_shots_fotmob s join matches m on m.id=s.match_id join leagues l on l.id=m.league_id
  where m.match_date >= '2021-08-01' and l.type <> 'international'
    and coalesce(s.period,'')<>'PenaltyShootout' and not coalesce(s.is_own_goal,false) and s.situation is distinct from 'Penalty'
    and s.xg is not null and s.x is not null and s.y is not null)
select least(2,floor(x/35.0))::int xi, least(2,greatest(0,floor(y/(68/3.0))))::int yi,
       (case when d<6 then 0 when d<9 then 1 when d<12 then 2 when d<16.5 then 3 when d<22 then 4 when d<30 then 5 else 6 end)*2 + (case when a<=30 then 0 else 1 end) zona,
       count(*) n
from b group by 1,2,3 order by 1,2,3;
