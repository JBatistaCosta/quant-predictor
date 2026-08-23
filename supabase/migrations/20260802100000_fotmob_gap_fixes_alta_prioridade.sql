-- Correção dos gaps de alta prioridade nas tabelas FotMob existentes
-- Ref: análise de gaps 2026-08-02

-- 1. match_lineup_fotmob — posição tática no campo e capitão
--    Os valores já estão no raw JSONB (verticalLayout.x/y, isCaptain);
--    ADD COLUMN + backfill imediato sem nova chamada de API.
ALTER TABLE match_lineup_fotmob
  ADD COLUMN IF NOT EXISTS field_pos_x numeric,
  ADD COLUMN IF NOT EXISTS field_pos_y numeric,
  ADD COLUMN IF NOT EXISTS is_captain boolean;

UPDATE match_lineup_fotmob
SET
  field_pos_x = (raw->'verticalLayout'->>'x')::numeric,
  field_pos_y = (raw->'verticalLayout'->>'y')::numeric,
  is_captain  = (raw->>'isCaptain')::boolean
WHERE raw IS NOT NULL
  AND field_pos_x IS NULL;

-- 2. team_squad_fotmob — atributos físicos e identidade por membro do elenco
ALTER TABLE team_squad_fotmob
  ADD COLUMN IF NOT EXISTS age integer,
  ADD COLUMN IF NOT EXISTS height_cm integer,
  ADD COLUMN IF NOT EXISTS shirt_number text,
  ADD COLUMN IF NOT EXISTS country_code text,
  ADD COLUMN IF NOT EXISTS role text,
  ADD COLUMN IF NOT EXISTS is_captain boolean;

-- 3. teams_fotmob — identificadores adicionais do clube
ALTER TABLE teams_fotmob
  ADD COLUMN IF NOT EXISTS short_name text,
  ADD COLUMN IF NOT EXISTS primary_league_fotmob_id integer,
  ADD COLUMN IF NOT EXISTS latest_season text;

-- 4. players.country_code — backfill dos ~6.7k jogadores com country_code NULL
--    que já têm o dado disponível no raw JSONB de match_lineup_fotmob
UPDATE players p
SET country_code = sub.country_code
FROM (
  SELECT DISTINCT ON (fotmob_player_id)
    fotmob_player_id,
    raw->>'countryCode' AS country_code
  FROM match_lineup_fotmob
  WHERE raw->>'countryCode' IS NOT NULL
    AND raw->>'countryCode' <> ''
  ORDER BY fotmob_player_id, captured_at DESC
) sub
WHERE p.fotmob_player_id = sub.fotmob_player_id
  AND p.country_code IS NULL;
