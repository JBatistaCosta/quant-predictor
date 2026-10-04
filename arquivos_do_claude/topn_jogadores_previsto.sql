-- Consulta usada no Achado 48 (top-N de xG e xA previsto por jogador). Trocar :liga por 4, 7, 10, 13 ou 16 (Premier, La Liga, Serie A, Bundesliga, Ligue 1);
-- cada execução devolve UMA linha com as partidas separadas por '|' e campos por ','. Saída -> scripts/analisar_topn_jogadores.py
-- (colunas: id,season,gd,xgd,elod,x2,x3,x4,x5,xall,a2,a3,a4,a5,aall). Fonte: player_match_walkforward, fonte_titular='previsto' (XI previsto, anterior ao jogo).
-- D_N = soma do λ dos N maiores λ do mandante MENOS a do visitante; NULL de λ vira 0 (não selecionar por "existe λ": viés pós-jogo já visto no projeto).
with p as (
 select w.match_id, w.team_id, coalesce(w.lambda_xg_jogo,0) lx, coalesce(w.lambda_xa_jogo,0) la
 from player_match_walkforward w join matches m on m.id=w.match_id
 where m.league_id=:liga and m.season in ('2024','2025') and m.status='finished' and w.fonte_titular='previsto'),
r as (select *, row_number() over (partition by match_id, team_id order by lx desc) rx, row_number() over (partition by match_id, team_id order by la desc) ra from p),
t as (select match_id, team_id,
 sum(lx) filter (where rx<=2) x2, sum(lx) filter (where rx<=3) x3, sum(lx) filter (where rx<=4) x4, sum(lx) filter (where rx<=5) x5, sum(lx) xall,
 sum(la) filter (where ra<=2) a2, sum(la) filter (where ra<=3) a3, sum(la) filter (where ra<=4) a4, sum(la) filter (where ra<=5) a5, sum(la) aall
 from r group by 1,2)
select string_agg(concat_ws(',', m.id, m.season, m.home_goals-m.away_goals,
  coalesce(round((sh.xg-sa.xg)::numeric,3)::text,''), coalesce(round((eh.rating_antes-ea.rating_antes)::numeric,1)::text,''),
  round((th.x2-ta.x2)::numeric,4), round((th.x3-ta.x3)::numeric,4), round((th.x4-ta.x4)::numeric,4), round((th.x5-ta.x5)::numeric,4), round((th.xall-ta.xall)::numeric,4),
  round((th.a2-ta.a2)::numeric,4), round((th.a3-ta.a3)::numeric,4), round((th.a4-ta.a4)::numeric,4), round((th.a5-ta.a5)::numeric,4), round((th.aall-ta.aall)::numeric,4)), '|' order by m.match_date, m.id) csv
from matches m
join t th on th.match_id=m.id and th.team_id=m.home_team_id
join t ta on ta.match_id=m.id and ta.team_id=m.away_team_id
left join match_stats_fotmob sh on sh.match_id=m.id and sh.team_id=m.home_team_id
left join match_stats_fotmob sa on sa.match_id=m.id and sa.team_id=m.away_team_id
left join team_elo_history eh on eh.match_id=m.id and eh.team_id=m.home_team_id and eh.escopo='global'
left join team_elo_history ea on ea.match_id=m.id and ea.team_id=m.away_team_id and ea.escopo='global'
where m.league_id=:liga and m.season in ('2024','2025') and m.status='finished';
