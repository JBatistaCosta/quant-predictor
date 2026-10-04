-- Revisão dos nomes das seleções (migration 20261005100100):
--  * 'Tartan Army' (Escócia) e 'Green and White Army' (Irlanda do Norte) são nomes
--    das TORCIDAS, não das seleções: removidos dos apelidos.
--  * Variações de nome (Holanda, República Tcheca, USA...) estavam em `nicknames`;
--    o lugar delas é `aliases`. Acrescentadas também variações comuns em fontes
--    externas (Korea Republic, IR Iran, Macedonia, Turkey, Rep. of Ireland...).
-- Casa SÓ por team_source_ids (source='fotmob'). Idempotente.
with ajustes(fotmob_id, novos_aliases, novos_apelidos, name_en) as (values
  (10259, array[]::text[], array[]::text[], null),
  (8498,  array[]::text[], array[]::text[], null),
  (8496,  array['República Tcheca','Czech Republic'], array[]::text[], null),
  (6708,  array['Holanda','Holland'], array['Oranje','Laranja Mecânica'], null),
  (6713,  array['USA','EUA','United States of America','Estados Unidos da América'], array['USMNT'], null),
  (6595,  array[]::text[], array[]::text[], 'Türkiye'),  -- sem alias 'Turkey': já existe o time 473 'Turkey' (duplicata a resolver)
  (7804,  array['Korea Republic','Korea Rep.'], array['Taegeuk Warriors'], null),
  (6711,  array['IR Iran'], array['Team Melli'], null),
  (8260,  array['Macedonia'], array[]::text[], null),
  (5791,  array['Rep. of Ireland','Eire'], array['Boys in Green'], null),
  (8256,  array['Seleção Brasileira'], array['Seleção Canarinho','Canarinho'], null)
)
update public.teams t set
  aliases   = (select coalesce(array_agg(distinct x), '{}') from unnest(coalesce(t.aliases,'{}') || a.novos_aliases) x),
  nicknames = a.novos_apelidos,
  name_en   = coalesce(a.name_en, t.name_en)
from ajustes a
join public.team_source_ids s on s.source = 'fotmob' and s.source_id = a.fotmob_id::text
where t.id = s.team_id;
