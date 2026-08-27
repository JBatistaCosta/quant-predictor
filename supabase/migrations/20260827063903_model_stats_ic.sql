-- IC95% por bootstrap do log-loss/acurácia de cada modelo, por liga --
-- mesmo espírito de `model_stats_resumo` (migration 20260825001000), só que
-- pra guardar um dado que NÃO dá pra calcular numa agregação SQL/plpgsql:
-- bootstrap precisa reamostrar linha a linha com reposição (2000 vezes por
-- grupo), então quem popula esta tabela é um script Python
-- (`scripts/avaliar_ic_modelos_por_liga.py`, rodado sob demanda via
-- `.github/workflows/avaliar_ic_modelos_por_liga.yml`), não uma função
-- `recalcular_*` chamada por RPC.
--
-- Motivação (CONTEXTO_PROJETO.md, achado #27): o painel `/modelos` mostra
-- "melhor modelo por liga" comparando só o ponto estimado de log-loss/
-- acurácia -- sem intervalo de confiança, esse ranking confunde edge real
-- com ruído de amostra pequena (achado real: o "vencedor" em Bundesliga/
-- Champions League/Copa Libertadores em Over/Under 2.5 não resistia a
-- IC95%). Esta tabela guarda o IC de cada (model_name, market, league_id)
-- pra api/model-stats.js expor isso ao lado do ponto estimado.
CREATE TABLE IF NOT EXISTS public.model_stats_ic (
  model_name text NOT NULL,
  market text NOT NULL,
  league_id integer NOT NULL,
  n_jogos integer NOT NULL,
  log_loss double precision NOT NULL,
  log_loss_ic_inf double precision NOT NULL,
  log_loss_ic_sup double precision NOT NULL,
  accuracy double precision NOT NULL,
  accuracy_ic_inf double precision NOT NULL,
  accuracy_ic_sup double precision NOT NULL,
  atualizado_em timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT model_stats_ic_pkey PRIMARY KEY (model_name, market, league_id)
);

ALTER TABLE public.model_stats_ic ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'model_stats_ic' AND policyname = 'leitura publica model_stats_ic'
  ) THEN
    CREATE POLICY "leitura publica model_stats_ic" ON public.model_stats_ic FOR SELECT TO public USING (true);
  END IF;
END $$;
