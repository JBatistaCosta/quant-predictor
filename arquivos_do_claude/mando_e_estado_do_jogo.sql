-- Achado 50. (1) Mando decomposto em volume x qualidade x conversão, por liga e temporada (5 ligas, 2022-2025, jogos com xG dos dois lados).
select l.name liga, m.season, count(*) jogos,
 round((sum(sh.total_shots)::numeric/sum(sa.total_shots)),3) r_chutes,
 round(((sum(sh.xg)/sum(sh.total_shots))/(sum(sa.xg)/sum(sa.total_shots)))::numeric,3) r_qualidade,        -- xG por chute, casa / fora
 round((sum(m.home_goals)::numeric/sum(sh.xg)),3) gols_xg_casa, round((sum(m.away_goals)::numeric/sum(sa.xg)),3) gols_xg_fora,
 round((sum(sh.xg)/sum(sa.xg))::numeric,3) r_xg
from matches m join leagues l on l.id=m.league_id
join match_stats_fotmob sh on sh.match_id=m.id and sh.team_id=m.home_team_id
join match_stats_fotmob sa on sa.match_id=m.id and sa.team_id=m.away_team_id
where m.league_id in (4,7,10,13,16) and m.season in ('2022','2023','2024','2025') and m.status='finished'
  and sh.xg is not null and sa.xg is not null and sh.total_shots>0 and sa.total_shots>0
group by 1,2 order by 1,2;

-- (2) Estado do jogo por PERFIL de força (sinal da diferença de Elo, que v_game_state_por_forca.faixa_forca esconde: ela agrupa pelo tamanho).
-- Comparações ganhando x perdendo são pareadas no tempo (mesmos minutos, linhas espelhadas); 'empatando' NÃO é pareado.
select case when elo_dif <= -100 then '1 bem mais fraco' when elo_dif < -30 then '2 um pouco mais fraco' when elo_dif <= 30 then '3 parelho'
            when elo_dif < 100 then '4 um pouco mais forte' else '5 bem mais forte' end perfil, estado,
 count(*) linhas, round(sum(minutos)::numeric,0) minutos,
 round((sum(chutes_pro)/sum(minutos)*90)::numeric,2) chutes_90, round((sum(xg_pro)/sum(chutes_pro))::numeric,4) xg_por_chute
from v_game_state_por_forca
where league_id in (4,7,10,13,16) and minutos>0 and chutes_pro is not null
group by 1,2 order by 1,2;
