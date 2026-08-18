-- Mesma mudança de odds_market.origem refletida na view usada pelo painel
-- de Cobertura -- agora dá pra ver, por liga/casa, se o dado é da
-- OddsPapi, de football-data.co.uk, Kaggle, Footiqo ou legado (null).
drop materialized view if exists vw_cobertura_odds_bookmaker;

create materialized view vw_cobertura_odds_bookmaker as
select
  l.id as league_id,
  l.name as liga,
  om.bookmaker,
  om.origem,
  om.snapshot,
  count(distinct om.match_id) as jogos_distintos,
  count(distinct om.market) as mercados_distintos,
  count(*) as linhas
from odds_market om
join matches m on m.id = om.match_id
join leagues l on l.id = m.league_id
group by l.id, l.name, om.bookmaker, om.origem, om.snapshot;

create unique index vw_cobertura_odds_bookmaker_uidx on vw_cobertura_odds_bookmaker (league_id, bookmaker, origem, snapshot);
grant select on vw_cobertura_odds_bookmaker to anon, authenticated;
