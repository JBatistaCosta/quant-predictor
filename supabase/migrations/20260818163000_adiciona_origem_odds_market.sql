-- Achado real (usuário pediu pra investigar): pelo menos 5 pipelines
-- diferentes já gravaram em odds_market ao longo do projeto (OddsPapi,
-- football-data.co.uk em 4 variantes, Kaggle "felipe", Kaggle
-- "oddspedia", Footiqo), e o `bookmaker` sozinho NÃO identifica a
-- origem -- 'pinnacle'/'bet365'/'media_mercado' são usados por MAIS DE
-- UMA fonte. Isso já causava um risco ativo de perda de dado:
-- scripts/ingestar_felipe_kaggle_odds.py fazia
-- `delete().in_("match_id", lote)` (TODAS as odds da partida, de
-- qualquer origem) antes de re-inserir só 1X2/O-U 2.5 -- rodar de novo
-- destruiria os 90+ mercados que o backfill da OddsPapi importou.
--
-- `origem` é nullable de propósito: pras linhas já existentes, só dá pra
-- saber a origem com certeza onde o bookmaker já é exclusivo de uma
-- fonte (kaggle_oddspedia, 1xbet=footiqo) -- pra 'pinnacle'/'bet365'/
-- 'william_hill'/'media_mercado'/'betfair_exchange', que colidem entre
-- fontes, NÃO tem como reconstruir a origem retroativamente com
-- confiança (uma mesma partida pode ter linhas de fontes diferentes sob
-- o mesmo bookmaker, dependendo de qual pipeline rodou primeiro) --
-- ficam null, documentadas como "legado, origem não determinável".
-- Todo importador passa a gravar `origem` a partir de agora, e as
-- checagens de idempotência/delete passam a ser escopadas por origem,
-- nunca por "qualquer odd já existe pra essa partida".
alter table odds_market add column if not exists origem text;

update odds_market set origem = 'kaggle_oddspedia' where bookmaker = 'kaggle_oddspedia' and origem is null;
update odds_market set origem = 'footiqo' where bookmaker = '1xbet' and origem is null;

comment on column odds_market.origem is 'Pipeline que gravou a linha (oddspapi, football_data_co_uk, kaggle_felipe, kaggle_oddspedia, footiqo). Null = legado anterior a essa coluna, origem não determinável com certeza (bookmaker colide entre fontes).';

create index if not exists idx_odds_market_match_origem on odds_market (match_id, origem);
