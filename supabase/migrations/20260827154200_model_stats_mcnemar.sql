-- Teste de McNemar pareado (qui-quadrado, correção de continuidade de
-- Yates) comparando cada modelo contra o LÍDER (menor log-loss médio) do
-- mesmo (market, league_id) -- mesmo espírito de `model_stats_ic` (migration
-- 20260827063903), populada pelo mesmo script Python
-- (`scripts/avaliar_ic_modelos_por_liga.py`), sob demanda via
-- `.github/workflows/avaliar_ic_modelos_por_liga.yml`.
--
-- Motivação (CONTEXTO_PROJETO.md, achado #29): duas IC95% marginais que se
-- sobrepõem (achado #27/#28) não PROVAM que dois modelos empatam -- é uma
-- leitura conservadora, informal. McNemar usa só os jogos onde os dois
-- modelos DISCORDAM (um acerta, o outro erra) pra testar se a diferença de
-- acurácia entre eles é real, na MESMA amostra pareada de partidas.
CREATE TABLE IF NOT EXISTS public.model_stats_mcnemar (
  market text NOT NULL,
  league_id integer NOT NULL,
  model_name text NOT NULL,
  model_lider text NOT NULL,
  n_pareado integer NOT NULL,
  n_concordantes integer NOT NULL,
  n_favorece_model integer NOT NULL,
  n_favorece_lider integer NOT NULL,
  qui2 double precision NOT NULL,
  p_valor double precision NOT NULL,
  significativo boolean NOT NULL,
  confiavel boolean NOT NULL,
  atualizado_em timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT model_stats_mcnemar_pkey PRIMARY KEY (market, league_id, model_name)
);

ALTER TABLE public.model_stats_mcnemar ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'model_stats_mcnemar' AND policyname = 'leitura publica model_stats_mcnemar'
  ) THEN
    CREATE POLICY "leitura publica model_stats_mcnemar" ON public.model_stats_mcnemar FOR SELECT TO public USING (true);
  END IF;
END $$;
