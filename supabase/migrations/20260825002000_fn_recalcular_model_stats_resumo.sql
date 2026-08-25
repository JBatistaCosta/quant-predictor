-- Função que popula model_stats_resumo (ver migration
-- 20260825001000_model_stats_resumo.sql) pra UM mercado por chamada --
-- 1X2 sozinho já leva ~39s (confirmado via EXPLAIN ANALYZE em produção,
-- 780k linhas de model_predictions), então processar os 3 mercados numa
-- chamada só arriscaria estourar o maxDuration=60s de
-- api/model-maintenance.js. Chamada 3x (uma por mercado) pela tarefa
-- `?tarefa=recalcular-model-stats&mercado=X`.
--
-- Métricas replicam EXATAMENTE a semântica já usada em api/model-stats.js
-- (log-loss/Brier só na linha da seleção REAL de cada partida, igual
-- `linhasClasseReal`; acurácia = seleção de maior probabilidade do modelo
-- por partida, igual ao "argmax" já usado lá) -- só que agregado em SQL
-- direto no Postgres em vez de puxar toda linha crua pro JS.
CREATE OR REPLACE FUNCTION public.recalcular_model_stats_resumo(p_mercado text)
RETURNS integer
LANGUAGE plpgsql
AS $$
DECLARE
  v_mercados text[];
  v_linhas integer;
BEGIN
  IF p_mercado = '1X2' THEN
    v_mercados := ARRAY['1X2', '1x2'];
  ELSE
    v_mercados := ARRAY[p_mercado];
  END IF;

  WITH predicoes AS (
    SELECT mp.model_name,
      CASE WHEN mp.market = '1x2' THEN '1X2' ELSE mp.market END AS market,
      mp.selection, mp.probability, mp.match_id
    FROM public.model_predictions mp
    WHERE mp.market = ANY(v_mercados)
  ),
  corners_por_partida AS (
    SELECT match_id, SUM(corners) AS total_corners
    FROM public.match_stats
    WHERE corners IS NOT NULL
    GROUP BY match_id
    HAVING COUNT(*) = 2
  ),
  resultados AS (
    SELECT m.id AS match_id, m.league_id,
      CASE
        WHEN p_mercado = '1X2' THEN
          CASE WHEN m.home_goals > m.away_goals THEN 'home'
               WHEN m.home_goals < m.away_goals THEN 'away'
               ELSE 'draw' END
        WHEN p_mercado = 'over_under_2.5' THEN
          CASE WHEN (m.home_goals + m.away_goals) > 2.5 THEN 'over' ELSE 'under' END
        WHEN p_mercado = 'corners_over_under_9.5' THEN
          CASE WHEN cp.total_corners > 9.5 THEN 'over' WHEN cp.total_corners IS NOT NULL THEN 'under' ELSE NULL END
        ELSE NULL
      END AS resultado
    FROM public.matches m
    LEFT JOIN corners_por_partida cp ON cp.match_id = m.id
    WHERE m.status = 'finished' AND m.home_goals IS NOT NULL AND m.away_goals IS NOT NULL
  ),
  linhas AS MATERIALIZED (
    SELECT p.model_name, p.market, r.league_id, p.match_id, p.selection, p.probability, r.resultado,
      (p.selection = r.resultado) AS y
    FROM predicoes p
    JOIN resultados r ON r.match_id = p.match_id
    WHERE r.resultado IS NOT NULL
  ),
  argmax AS (
    SELECT DISTINCT ON (model_name, market, league_id, match_id)
      model_name, market, league_id, match_id, selection AS prevista, resultado
    FROM linhas
    ORDER BY model_name, market, league_id, match_id, probability DESC
  ),
  metricas_prob AS (
    SELECT model_name, market, league_id, COUNT(*) AS n,
      AVG(-ln(GREATEST(LEAST(probability, 0.9999), 0.0001))) AS log_loss_modelo,
      AVG(POWER(GREATEST(LEAST(probability, 0.9999), 0.0001) - 1, 2)) AS brier_modelo
    FROM linhas WHERE y
    GROUP BY model_name, market, league_id
  ),
  metricas_acc AS (
    SELECT model_name, market, league_id,
      AVG(CASE WHEN prevista = resultado THEN 1.0 ELSE 0.0 END) AS accuracy_modelo
    FROM argmax
    GROUP BY model_name, market, league_id
  ),
  upsert AS (
    INSERT INTO public.model_stats_resumo (model_name, market, league_id, n_jogos, log_loss_modelo, brier_modelo, accuracy_modelo, atualizado_em)
    SELECT mp.model_name, mp.market, mp.league_id, mp.n, mp.log_loss_modelo, mp.brier_modelo, ma.accuracy_modelo, now()
    FROM metricas_prob mp
    JOIN metricas_acc ma USING (model_name, market, league_id)
    ON CONFLICT (model_name, market, league_id) DO UPDATE SET
      n_jogos = EXCLUDED.n_jogos,
      log_loss_modelo = EXCLUDED.log_loss_modelo,
      brier_modelo = EXCLUDED.brier_modelo,
      accuracy_modelo = EXCLUDED.accuracy_modelo,
      atualizado_em = EXCLUDED.atualizado_em
    RETURNING 1
  )
  SELECT COUNT(*) INTO v_linhas FROM upsert;

  RETURN v_linhas;
END;
$$;
