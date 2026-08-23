-- Pedido do usuário: mostrar a cobertura de odds separada por fonte
-- (OddsPapi/Kaggle/football-data.co.uk/...) na barra de cobertura da aba
-- Cobertura de Odds. Atribui cada partida à fonte de MAIOR prioridade
-- que ela tem (OddsPapi primeiro -- é a mais rica em mercados -- depois
-- as legadas, depois "sem origem determinável"), pra virar um breakdown
-- MUTUAMENTE EXCLUSIVO que soma exatamente ao total com odds -- dá pra
-- desenhar uma barra empilhada de verdade (confirmado: soma das 6
-- colunas == com_odds de vw_cobertura_odds, testado contra produção).
create materialized view vw_cobertura_odds_origem as
with prioridade as (
  select om.match_id,
    min(case om.origem
      when 'oddspapi' then 1
      when 'football_data_co_uk' then 2
      when 'kaggle_felipe' then 3
      when 'kaggle_oddspedia' then 4
      when 'footiqo' then 5
      else 6
    end) as prioridade_min
  from odds_market om
  group by om.match_id
)
select
  l.id as league_id,
  l.name as liga,
  m.season,
  count(distinct m.id) filter (where m.status = 'finished') as finalizadas,
  count(distinct m.id) filter (where p.prioridade_min = 1) as com_oddspapi,
  count(distinct m.id) filter (where p.prioridade_min = 2) as com_football_data_co_uk,
  count(distinct m.id) filter (where p.prioridade_min = 3) as com_kaggle_felipe,
  count(distinct m.id) filter (where p.prioridade_min = 4) as com_kaggle_oddspedia,
  count(distinct m.id) filter (where p.prioridade_min = 5) as com_footiqo,
  count(distinct m.id) filter (where p.prioridade_min = 6) as com_legado_sem_origem
from matches m
join leagues l on l.id = m.league_id
left join prioridade p on p.match_id = m.id
group by l.id, l.name, m.season;

create unique index vw_cobertura_odds_origem_uidx on vw_cobertura_odds_origem (league_id, season);
grant select on vw_cobertura_odds_origem to anon, authenticated;
