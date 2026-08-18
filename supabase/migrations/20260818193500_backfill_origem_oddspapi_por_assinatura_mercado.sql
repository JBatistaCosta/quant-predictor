-- Extensão da mesma técnica usada pra 1xbet (ver
-- corrige_origem_1xbet_e_backfill_betano): football-data.co.uk e o
-- Kaggle "felipe" NUNCA gravam mercado além de 1X2/over_under_2.5 pros
-- bookmakers pinnacle/bet365/william_hill -- qualquer partida com um
-- desses bookmakers em outro mercado (handicap, escanteios, cartões
-- etc.) só pode ter vindo da OddsPapi, que itera todos os mercados que
-- a API retorna. Reclassifica com confiança total (sem heurística no
-- escuro) as partidas com esse sinal; o resto (só 1X2/O-U 2.5, sem
-- sinal pra desambiguar) permanece null -- genuinamente ambíguo entre
-- OddsPapi e as fontes legadas.
--
-- Rodado em produção como 3 UPDATEs separados (um por bookmaker) por
-- causa do volume de dado (pinnacle sozinho tem 21 mil partidas) --
-- migrations grandes numa transação só estouravam o statement timeout.
-- Resultado real: william_hill 845, bet365 1.772, pinnacle 1.823
-- partidas reclassificadas com confiança.
do $$
declare
  bk text;
begin
  foreach bk in array array['pinnacle', 'bet365', 'william_hill'] loop
    execute format($f$
      update odds_market set origem = 'oddspapi'
      where bookmaker = %L and origem is null and match_id in (
        select distinct match_id from odds_market
        where bookmaker = %L and market not in ('1X2', 'over_under_2.5')
      )
    $f$, bk, bk);
  end loop;
end $$;
