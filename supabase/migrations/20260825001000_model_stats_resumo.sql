-- Tabela resumo pré-calculada pro painel /modelos (api/model-stats.js).
--
-- A carga SEM FILTRO do painel (usada na abertura da página) precisa agregar
-- log-loss/Brier/acurácia de TODOS os model_name x mercado x liga de uma vez
-- -- mas model_predictions já tem 5,2M+ linhas (53 modelos distintos, cresce
-- via cron diário) e mesmo com o índice novo (migration
-- 20260825000500_indice_model_predictions_market) uma agregação AO VIVO
-- pra só um mercado leva 12-40s (confirmado via EXPLAIN ANALYZE em
-- produção) -- acima do maxDuration=30s da function e do statement_timeout
-- do Postgres. Não é problema de índice, é volume real de computação:
-- calcular isso a cada carga de página nunca vai caber no orçamento de uma
-- function serverless.
--
-- Por isso pré-calculado: uma tarefa de manutenção
-- (api/model-maintenance.js `?tarefa=recalcular-model-stats`, chamada
-- manualmente ou por cron) roda a agregação UMA VEZ e grava aqui; o painel
-- lê essa tabela pequena (~centenas de linhas, 1 por model_name+market+
-- league_id) em vez de agregar ao vivo. Comparação com o mercado (odds
-- devigadas) e calibração em quintis continuam vindo da chamada existente,
-- só quando o usuário filtra por um modelo específico (já rápida hoje,
-- porque filtra model_predictions por model_name no banco).
CREATE TABLE IF NOT EXISTS public.model_stats_resumo (
  model_name text NOT NULL,
  market text NOT NULL,
  league_id integer NOT NULL,
  n_jogos integer NOT NULL,
  log_loss_modelo double precision,
  brier_modelo double precision,
  accuracy_modelo double precision,
  atualizado_em timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT model_stats_resumo_pkey PRIMARY KEY (model_name, market, league_id)
);

ALTER TABLE public.model_stats_resumo ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'model_stats_resumo' AND policyname = 'leitura publica model_stats_resumo'
  ) THEN
    CREATE POLICY "leitura publica model_stats_resumo" ON public.model_stats_resumo FOR SELECT TO public USING (true);
  END IF;
END $$;
