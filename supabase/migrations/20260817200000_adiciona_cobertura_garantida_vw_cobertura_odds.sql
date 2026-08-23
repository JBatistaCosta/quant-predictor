-- Achado real (doc oficial da OddsPapi, GET /v4/historical-odds): "All
-- historical odds data since January 2026 is available." Confirmado
-- contra produção: partidas a partir de 01/01/2026 marcadas completas em
-- match_source_ids tinham 100% (1879/1879) com odds reais em odds_market;
-- antes disso, só 73,5% (3138/4266) -- o resto (sem_historico_na_fonte)
-- é teto real da fonte, não bug nem falta de apelido de time.
--
-- vw_cobertura_odds/CoberturaOdds.jsx mostravam só a % bruta (com_odds /
-- finalizadas), sem distinguir "ainda não processado" de "a fonte nunca
-- vai ter esse dado" -- usuário via ligas grandes travadas em ~25-30% e
-- não tinha como saber se é teto real ou trabalho pendente. Adiciona 3
-- colunas com a mesma métrica só pro subconjunto que a OddsPapi garante
-- (match_date >= 2026-01-01), pra dar um número honesto de "cobertura
-- dentro do que é possível hoje".
drop materialized view if exists vw_cobertura_odds;

create materialized view vw_cobertura_odds as
select
  l.id as league_id,
  l.name as liga,
  m.season,
  count(distinct m.id) filter (where m.status = 'finished') as finalizadas,
  count(distinct om.match_id) as com_odds,
  round(100.0 * count(distinct om.match_id)::numeric / nullif(count(distinct m.id) filter (where m.status = 'finished'), 0)::numeric, 1) as pct_cobertura,
  count(distinct om.bookmaker) as casas_distintas,
  count(distinct m.id) filter (where m.status = 'finished' and m.match_date >= '2026-01-01') as finalizadas_garantidas,
  count(distinct om.match_id) filter (where m.match_date >= '2026-01-01') as com_odds_garantidas,
  round(100.0 * count(distinct om.match_id) filter (where m.match_date >= '2026-01-01')::numeric / nullif(count(distinct m.id) filter (where m.status = 'finished' and m.match_date >= '2026-01-01'), 0)::numeric, 1) as pct_cobertura_garantida
from matches m
join leagues l on l.id = m.league_id
left join odds_market om on om.match_id = m.id
group by l.id, l.name, m.season;

create unique index vw_cobertura_odds_uidx on vw_cobertura_odds (league_id, season);
grant select on vw_cobertura_odds to anon, authenticated;
