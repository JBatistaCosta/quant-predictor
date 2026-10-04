-- Achado 55 (qualidade do chute ao longo do jogo, por estado do placar, perfil de força e quem chuta: titular x reserva).
-- Só leitura. Troque :LIGA pelo nome da liga (Premier League, La Liga, 'Serie A (Itália)', Bundesliga, 'Ligue 1'); temporadas 2021 a 2025 (cobertura completa do shotmap).
-- Estado e relógio seguem a mesma regra de derivar_game_state: placar ANTES do chute, ordenado por `clock` (não por `minuto`); gol contra e disputa de pênaltis fora.
-- Pênalti fora (a qualidade dele é outra coisa). xG NULL conta como 0, como na derivação.
-- bucket = faixa de 15 min do relógio monótono (0 = 0-15 ... 5 = 75-90, 6 = acima de 90). perfil = Elo global do time que chuta menos o do adversário, corte 100.
-- papel: titular | reserva_0_15 | reserva_15_30 | reserva_30mais (minutos desde a entrada) | sem_escalacao.
with m as (
  select id, home_team_id, away_team_id from matches
  where league_id = (select id from leagues where name = :LIGA) and season in ('2021','2022','2023','2024','2025') and status = 'finished'),
fh as (
  select s.match_id, greatest(0, max(s.minute + coalesce(s.minute_added,0)) - 45)::numeric as fh_over
  from match_shots_fotmob s join m on m.id = s.match_id
  where s.period = 'FirstHalf' and s.minute is not null group by s.match_id),
b as (
  select s.id, s.match_id, s.team_id, s.player_id, coalesce(s.xg,0) as xg, s.event_type, coalesce(s.is_own_goal,false) as own, s.situation,
         (s.minute + coalesce(s.minute_added,0)) as minuto,
         (s.minute + coalesce(s.minute_added,0))::numeric + case when s.period = 'FirstHalf' then 0 else coalesce(fh.fh_over,0) end as clock,
         (s.team_id = m.home_team_id) as casa,
         case when s.team_id = m.home_team_id then m.away_team_id else m.home_team_id end as rival,
         case when s.event_type <> 'Goal' then null when s.is_own_goal then (s.team_id = m.away_team_id) else (s.team_id = m.home_team_id) end as gpc
  from match_shots_fotmob s join m on m.id = s.match_id left join fh on fh.match_id = s.match_id
  where s.period is distinct from 'PenaltyShootout' and s.minute is not null),
c as (
  select b.*,
         coalesce(sum(case when gpc is true then 1 else 0 end) over w, 0) as ca,
         coalesce(sum(case when gpc is false then 1 else 0 end) over w, 0) as fa
  from b window w as (partition by match_id order by clock, id rows between unbounded preceding and 1 preceding))
select least(floor(c.clock / 15), 6)::int as bucket,
       case when (case when c.casa then c.ca - c.fa else c.fa - c.ca end) > 0 then 'ganhando'
            when (case when c.casa then c.ca - c.fa else c.fa - c.ca end) < 0 then 'perdendo' else 'empatando' end as estado,
       case when e1.rating_antes - e2.rating_antes > 100 then 'forte' when e1.rating_antes - e2.rating_antes < -100 then 'fraco'
            when e1.rating_antes is null or e2.rating_antes is null then 'sem_elo' else 'parelho' end as perfil,
       case when c.situation in ('FromCorner','SetPiece','ThrowInSetPiece','FreeKick') then 'bola_parada' else 'jogo_corrido' end as tipo,
       case when l.is_starter then 'titular'
            when l.substituted_in_minute is null then 'sem_escalacao'
            when c.minuto - l.substituted_in_minute < 15 then 'reserva_0_15'
            when c.minuto - l.substituted_in_minute < 30 then 'reserva_15_30' else 'reserva_30mais' end as papel,
       count(*) as n, sum(c.xg) as sxg, sum(c.xg * c.xg) as sxg2, sum(case when c.event_type = 'Goal' then 1 else 0 end) as gols
from c
left join team_elo_history e1 on e1.match_id = c.match_id and e1.team_id = c.team_id and e1.escopo = 'global'
left join team_elo_history e2 on e2.match_id = c.match_id and e2.team_id = c.rival and e2.escopo = 'global'
left join match_lineup_fotmob l on l.match_id = c.match_id and l.player_id = c.player_id
where not c.own and c.situation is distinct from 'Penalty'
group by 1, 2, 3, 4, 5
order by 1, 2, 3, 4, 5;
