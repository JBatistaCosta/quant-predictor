-- v2 de modelos_custom_orfaos() -- a v1 (migration 20260825010000) fazia
-- Seq Scan + regex em TODA linha de model_predictions (5,2M+) pra achar os
-- ~87 model_name distintos -- 22-27s de execução real (bem mais que os
-- ~9s estimados pelo teste manual anterior, que tinha cache de buffer
-- "quente"). Via PostgREST (supabase.rpc, statement_timeout mais curto que
-- a sessão do SQL Editor) isso estourava `canceling statement due to
-- statement timeout` em produção (testado, `?tarefa=modelos-custom-orfaos`
-- retornava 500).
--
-- Fix: "loose index scan" via CTE recursiva sobre
-- idx_model_predictions_model_market_match (lidera por model_name) --
-- acha cada valor distinto pulando direto pro próximo maior via Index Only
-- Scan em vez de escanear tudo (mesma técnica clássica pra distinct em
-- coluna de baixa cardinalidade num índice grande; Postgres não tem um
-- "skip scan" nativo). Contagem por modelo órfão também usa esse índice
-- (Index Cond model_name = X, equality na coluna líder). Resultado: mesma
-- saída de antes, 54ms em vez de 27s (EXPLAIN ANALYZE validado em
-- produção).
CREATE OR REPLACE FUNCTION public.modelos_custom_orfaos()
RETURNS TABLE (nome_base text, variantes bigint, total_previsoes bigint)
LANGUAGE sql
STABLE
AS $$
  WITH RECURSIVE distintos AS (
    (SELECT model_name FROM public.model_predictions ORDER BY model_name LIMIT 1)
    UNION ALL
    SELECT (SELECT model_name FROM public.model_predictions WHERE model_name > d.model_name ORDER BY model_name LIMIT 1)
    FROM distintos d
    WHERE d.model_name IS NOT NULL
  ),
  nomes AS (
    SELECT
      trim(substring(regexp_replace(model_name, '_calibrado_(platt|isotonic)$', '') FROM '^(.*) \[[^\[\]]*\]$')) AS nome_base,
      model_name
    FROM distintos
    WHERE model_name IS NOT NULL AND model_name LIKE '% [%]%'
  ),
  orfaos AS (
    SELECT n.nome_base, n.model_name
    FROM nomes n
    WHERE n.nome_base IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM public.custom_model_configs c WHERE c.name = n.nome_base)
  )
  SELECT o.nome_base, COUNT(DISTINCT o.model_name) AS variantes,
    SUM((SELECT COUNT(*) FROM public.model_predictions mp WHERE mp.model_name = o.model_name)) AS total_previsoes
  FROM orfaos o
  GROUP BY o.nome_base
  ORDER BY total_previsoes DESC;
$$;
