ALTER TABLE public.teams
    ADD COLUMN IF NOT EXISTS stadium_capacity INTEGER,
    ADD COLUMN IF NOT EXISTS stadium_surface  TEXT;
