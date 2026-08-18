-- CORREÇÃO de bug real introduzido na migration anterior
-- (adiciona_origem_odds_market): assumi que bookmaker='1xbet' era
-- exclusivo do Footiqo, mas BOOKMAKERS_HISTORICO em
-- api/model-maintenance.js confirma que o backfill da OddsPapi TAMBÉM
-- grava '1xbet' (é um dos bookmakers do histórico, ao lado de
-- pinnacle/bet365/betano/williamhill). Achado ao investigar por que o
-- painel de Cobertura mostrava quase tudo como "origem desconhecida"
-- mesmo pra dado claramente recente da OddsPapi.
--
-- Footiqo só grava 1X2/over_under_{0.5..4.5}/btts (LINHAS_OU_FOOTIQO em
-- api/model-maintenance.js) -- qualquer partida com 1xbet em outro
-- mercado (handicap, escanteios, cartões etc.) só pode ter vindo da
-- OddsPapi, que itera TODOS os mercados retornados. Confirmado: 1.488
-- de 2.162 partidas com 1xbet tinham mercado fora do conjunto do
-- Footiqo -- a maioria das linhas marcadas 'footiqo' pela migration
-- anterior eram, na verdade, da OddsPapi.
--
-- Reclassifica: toda linha bookmaker=1xbet de uma partida que tem PELO
-- MENOS UM mercado fora do conjunto do Footiqo -> origem='oddspapi' (a
-- partida inteira veio de lá, incluindo as linhas de 1X2/O-U que
-- coincidem com o que o Footiqo também gravaria). O resto (só
-- 1X2/O-U/btts, sem sinal pra desambiguar) volta a ser null -- ambíguo
-- de verdade entre Footiqo e OddsPapi, não dá pra saber com confiança.
do $$
declare
  jogos_oddspapi bigint[];
begin
  select array_agg(distinct match_id) into jogos_oddspapi
  from odds_market
  where bookmaker = '1xbet'
    and market not in ('1X2','over_under_0.5','over_under_1.5','over_under_2.5','over_under_3.5','over_under_4.5','btts');

  update odds_market set origem = 'oddspapi'
  where bookmaker = '1xbet' and match_id = any(jogos_oddspapi);

  update odds_market set origem = null
  where bookmaker = '1xbet' and origem = 'footiqo' and not (match_id = any(jogos_oddspapi));
end $$;

-- Backfill SEGURO adicional: 'betano' só é escrito pela OddsPapi
-- (BOOKMAKERS_ALVO/BOOKMAKERS_HISTORICO) -- nenhum dos scripts legados
-- (football-data.co.uk, Kaggle, Footiqo) usa esse nome de casa.
update odds_market set origem = 'oddspapi' where bookmaker = 'betano' and origem is null;
