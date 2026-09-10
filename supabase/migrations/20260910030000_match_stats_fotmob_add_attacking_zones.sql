-- Zonas de ataque (esquerda/centro/direita) por time, do widget "Attacking
-- zones" do FotMob -- content.attackingZones no payload de matchDetails,
-- uma chave TOTALMENTE separada de content.stats (a árvore que a extração
-- já lia). Formato real confirmado por inspeção direta (matchId 6106264,
-- Barcelona x Feyenoord):
--   content.attackingZones.{home,away}.{total,firstHalf,secondHalf}.{left,center,right}
-- Percentual de ataques por lado do campo -- os 3 valores de cada período
-- somam ~100 (não exatamente por arredondamento do FotMob). Ausente em
-- payload de partida antiga (confirmado matchId 1778037, La Liga 2014/15
-- -- mesma limitação de fonte já documentada pra shots/discipline em
-- partida pré-~2018, ver Achado 11 em ACHADOS_COMPORTAMENTO.md), então
-- NULL nesses casos é dado ausente na fonte, não bug de extração.
alter table match_stats_fotmob
  add column if not exists attacking_zone_left numeric,
  add column if not exists attacking_zone_center numeric,
  add column if not exists attacking_zone_right numeric,
  add column if not exists attacking_zone_left_1t numeric,
  add column if not exists attacking_zone_center_1t numeric,
  add column if not exists attacking_zone_right_1t numeric,
  add column if not exists attacking_zone_left_2t numeric,
  add column if not exists attacking_zone_center_2t numeric,
  add column if not exists attacking_zone_right_2t numeric,
  -- distingue "nunca tentado" de "tentado, FotMob não tem o dado" (partida
  -- antiga) -- sem isso o backfill reprocessaria pra sempre as partidas
  -- onde attacking_zone_* fica NULL por limitação real da fonte, não erro.
  add column if not exists attacking_zone_checked boolean not null default false;
