-- Substituição, achado 10/09: o FotMob já manda essa informação em
-- content.lineup.{homeTeam,awayTeam}.{starters,subs}[].performance.
-- substitutionEvents -- um array de {time, type: 'subIn'|'subOut', reason},
-- capturado desde sempre dentro de match_lineup_fotmob.raw (a ingestão já
-- guarda o objeto do jogador inteiro), mas NUNCA extraído pra coluna
-- própria. Diferente de zonas de ataque/estatística por tempo, aqui não
-- precisa re-buscar nada do FotMob -- o dado já está no banco, só falta
-- extrair do jsonb já persistido.
--
-- 99,7% dos jogadores com evento têm exatamente 1 (subIn OU subOut) ou 2
-- (SEMPRE na ordem subIn depois subOut -- um jogador que entrou e depois
-- saiu de novo na mesma partida, confirmado 1.031/1.031 casos reais em
-- produção) -- por isso 2 colunas de minuto + 2 de motivo bastam, sem
-- perder informação real. `reason` observado: "tactical"/"light injury"
-- (e ausente/null em parte dos casos).
--
-- Ausente (raw->'performance'->'substitutionEvents' é NULL, não array
-- vazio) em payload de partida antiga -- mesma limitação já documentada
-- pro Achado 11/zonas de ataque/estatística por tempo.
alter table match_lineup_fotmob
  add column if not exists substituted_in_minute integer,
  add column if not exists substituted_in_reason text,
  add column if not exists substituted_out_minute integer,
  add column if not exists substituted_out_reason text;

update match_lineup_fotmob
set
  substituted_in_minute = (
    select (e->>'time')::int
    from jsonb_array_elements(raw->'performance'->'substitutionEvents') e
    where e->>'type' = 'subIn'
    limit 1
  ),
  substituted_in_reason = (
    select e->>'reason'
    from jsonb_array_elements(raw->'performance'->'substitutionEvents') e
    where e->>'type' = 'subIn'
    limit 1
  ),
  substituted_out_minute = (
    select (e->>'time')::int
    from jsonb_array_elements(raw->'performance'->'substitutionEvents') e
    where e->>'type' = 'subOut'
    limit 1
  ),
  substituted_out_reason = (
    select e->>'reason'
    from jsonb_array_elements(raw->'performance'->'substitutionEvents') e
    where e->>'type' = 'subOut'
    limit 1
  )
where jsonb_typeof(raw->'performance'->'substitutionEvents') = 'array'
  and jsonb_array_length(raw->'performance'->'substitutionEvents') > 0;
