-- Odds por snapshot temporal — abertura, pré-fechamento e fechamento.
-- Permite calcular CLV (Closing Line Value) e detectar movimento de linhas
-- comparando abertura vs fechamento para o mesmo (match_id, bookmaker, market).
--
-- Uma linha por (match_id, bookmaker, market, snapshot_type):
--   • abertura      — capturado D-7 a D-10 (primeira captura; INSERT sem update)
--   • pre_fechamento — capturado D-1 (atualiza com odds mais recentes)
--   • fechamento    — capturado D-0 ~2h antes do apito (atualiza com odds mais recentes)
--
-- `outcomes` guarda o array raw de The-Odds-API (flexível por mercado):
--   h2h:     [{"name": "Arsenal", "price": 1.9}, {"name": "Draw", "price": 3.4}, ...]
--   spreads: [{"name": "Arsenal", "price": 1.9, "point": -0.5}, ...]
--   totals:  [{"name": "Over", "price": 1.9, "point": 2.5}, ...]

CREATE TABLE IF NOT EXISTS public.odds_snapshots (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    match_id      BIGINT NOT NULL REFERENCES public.matches(id) ON DELETE CASCADE,
    bookmaker     TEXT   NOT NULL,
    market        TEXT   NOT NULL CHECK (market IN ('h2h', 'spreads', 'totals')),
    snapshot_type TEXT   NOT NULL CHECK (snapshot_type IN ('abertura', 'pre_fechamento', 'fechamento')),
    snapshot_at   TIMESTAMPTZ NOT NULL,
    outcomes      JSONB  NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT odds_snapshots_unique UNIQUE (match_id, bookmaker, market, snapshot_type)
);

CREATE INDEX IF NOT EXISTS idx_odds_snapshots_match_id
    ON public.odds_snapshots (match_id);

CREATE INDEX IF NOT EXISTS idx_odds_snapshots_snapshot_type
    ON public.odds_snapshots (snapshot_type, snapshot_at DESC);

CREATE INDEX IF NOT EXISTS idx_odds_snapshots_outcomes_gin
    ON public.odds_snapshots USING GIN (outcomes);

COMMENT ON TABLE public.odds_snapshots IS
    'Odds da The-Odds-API por partida/bookmaker/mercado em 3 momentos: abertura, pré-fechamento e fechamento. Permite CLV e análise de movimento de linhas.';

COMMENT ON COLUMN public.odds_snapshots.snapshot_type IS
    'abertura = D-7 a D-10 (captura inicial, nunca sobrescrita); pre_fechamento = D-1; fechamento = D-0 ~2h antes.';

COMMENT ON COLUMN public.odds_snapshots.outcomes IS
    'Array raw de outcomes da The-Odds-API — estrutura varia por market (h2h/spreads/totals).';

ALTER TABLE public.odds_snapshots ENABLE ROW LEVEL SECURITY;

CREATE POLICY "odds_snapshots_public_read"
    ON public.odds_snapshots FOR SELECT TO anon, authenticated USING (true);
