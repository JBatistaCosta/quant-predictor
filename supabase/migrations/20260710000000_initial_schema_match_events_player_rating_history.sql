-- Schema inicial de match_events e player_rating_history.
-- Criadas originalmente fora do sistema de migrations; capturadas aqui para
-- que o Supabase Preview Branch consiga aplicar todas as migrations do zero.
-- Timestamp 20260710 garante execução ANTES de 20260720120000
-- (expand_match_events_cartoes) e 20260728002500 (idx player_rating_history).

-- -----------------------------------------------------------------------------
-- 1. match_events  (FK → matches, teams, players)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.match_events (
    id              BIGINT PRIMARY KEY,
    match_id        BIGINT NOT NULL REFERENCES public.matches(id),
    team_id         BIGINT REFERENCES public.teams(id),
    player_name     TEXT,
    event_type      TEXT NOT NULL,
    minute          INT,
    second          INT,
    location_x      NUMERIC,
    location_y      NUMERIC,
    outcome         TEXT,
    detail          JSONB,
    created_at      TIMESTAMPTZ DEFAULT now(),
    source          TEXT NOT NULL DEFAULT 'fotmob',
    player_id       BIGINT REFERENCES public.players(id),
    fotmob_event_id BIGINT,
    CONSTRAINT match_events_match_fotmob_event_unique UNIQUE (match_id, fotmob_event_id)
);

ALTER TABLE public.match_events ENABLE ROW LEVEL SECURITY;

-- -----------------------------------------------------------------------------
-- 2. player_rating_history  (FK → players, matches)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.player_rating_history (
    id               BIGINT PRIMARY KEY,
    player_id        BIGINT NOT NULL REFERENCES public.players(id),
    match_id         BIGINT NOT NULL REFERENCES public.matches(id),
    rating_antes     NUMERIC NOT NULL,
    rating_depois    NUMERIC NOT NULL,
    indice_partida   NUMERIC,
    fotmob_rating    NUMERIC,
    minutes_played   INT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT player_rating_history_player_id_match_id_key UNIQUE (player_id, match_id)
);

ALTER TABLE public.player_rating_history ENABLE ROW LEVEL SECURITY;
