-- Schema inicial das 4 tabelas base do projeto.
-- Criadas originalmente fora do sistema de migrations; capturadas aqui para
-- que o Supabase Preview Branch consiga aplicar todas as migrations do zero.
-- Usa CREATE TABLE IF NOT EXISTS / CREATE POLICY IF NOT EXISTS para ser
-- idempotente no banco de produção onde as tabelas já existem.

-- -----------------------------------------------------------------------------
-- 1. leagues  (sem dependências)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.leagues (
    id          BIGINT PRIMARY KEY,
    external_id TEXT UNIQUE,
    name        TEXT NOT NULL,
    country     TEXT,
    type        TEXT NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE public.leagues ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname='public' AND tablename='leagues'
      AND policyname='leitura publica leagues'
  ) THEN
    CREATE POLICY "leitura publica leagues" ON public.leagues FOR SELECT USING (true);
  END IF;
END $$;

-- -----------------------------------------------------------------------------
-- 2. teams  (sem dependências)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.teams (
    id               BIGINT PRIMARY KEY,
    external_id      TEXT UNIQUE,
    name             TEXT NOT NULL,
    country          TEXT,
    is_national_team BOOLEAN DEFAULT false,
    created_at       TIMESTAMPTZ DEFAULT now(),
    crest_url        TEXT,
    city             TEXT,
    stadium          TEXT
);

ALTER TABLE public.teams ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname='public' AND tablename='teams'
      AND policyname='leitura publica teams'
  ) THEN
    CREATE POLICY "leitura publica teams" ON public.teams FOR SELECT USING (true);
  END IF;
END $$;

-- -----------------------------------------------------------------------------
-- 3. matches  (FK → leagues, teams)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.matches (
    id           BIGINT PRIMARY KEY,
    external_id  TEXT UNIQUE,
    league_id    BIGINT REFERENCES public.leagues(id),
    season       TEXT,
    match_date   TIMESTAMPTZ NOT NULL,
    home_team_id BIGINT NOT NULL REFERENCES public.teams(id),
    away_team_id BIGINT NOT NULL REFERENCES public.teams(id),
    home_goals   INT,
    away_goals   INT,
    status       TEXT DEFAULT 'scheduled',
    created_at   TIMESTAMPTZ DEFAULT now(),
    round        INT,
    stage        TEXT
);

CREATE INDEX IF NOT EXISTS idx_matches_date         ON public.matches (match_date);
CREATE INDEX IF NOT EXISTS idx_matches_home_team    ON public.matches (home_team_id);
CREATE INDEX IF NOT EXISTS idx_matches_away_team    ON public.matches (away_team_id);
CREATE INDEX IF NOT EXISTS idx_matches_league_season ON public.matches (league_id, season);

ALTER TABLE public.matches ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname='public' AND tablename='matches'
      AND policyname='leitura publica matches'
  ) THEN
    CREATE POLICY "leitura publica matches" ON public.matches FOR SELECT USING (true);
  END IF;
END $$;

-- -----------------------------------------------------------------------------
-- 4. players  (FK → teams, matches)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.players (
    id                   BIGINT PRIMARY KEY,
    fotmob_player_id     TEXT NOT NULL UNIQUE,
    name                 TEXT,
    first_name           TEXT,
    last_name            TEXT,
    shirt_number         TEXT,
    country_name         TEXT,
    country_code         TEXT,
    age                  INT,
    market_value         NUMERIC,
    usual_position_id    INT,
    photo_url            TEXT,
    last_team_id         BIGINT REFERENCES public.teams(id),
    last_seen_match_id   BIGINT REFERENCES public.matches(id),
    raw_lineup           JSONB,
    first_synced_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    perfil_importado     SMALLINT NOT NULL DEFAULT 0,
    perfil_atualizado_em TIMESTAMPTZ
);

ALTER TABLE public.players ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname='public' AND tablename='players'
      AND policyname='Public read access'
  ) THEN
    CREATE POLICY "Public read access" ON public.players FOR SELECT USING (true);
  END IF;
END $$;
