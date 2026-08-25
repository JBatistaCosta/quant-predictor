-- Modelos personalizados (custom_model_configs) excluídos pela UI (Treino
-- Customizado) até agora só removiam a linha de configuração --
-- model_predictions/model_calibration/model_match_estimates/
-- model_stat_estimates/model_stats_resumo continuavam com as previsões
-- desse modelo pra sempre (nome do modelo é só uma STRING
-- "{nome_da_config} [{algoritmo}]", sem FK/cascade nenhuma de volta pra
-- custom_model_configs.id -- ver scripts/prever_partidas_futuras_custom.py
-- `nome_do_modelo`). Efeito prático de "soft delete": a config some da UI,
-- os dados ficam órfãos no banco pra sempre, inflando o volume real de
-- model_predictions (motivo raiz do achado da migration
-- 20260825001000_model_stats_resumo).
--
-- Esta função audita isso: acha todo `model_name` de model_predictions no
-- formato "{base} [{algo}]" (com ou sem sufixo `_calibrado_platt`/
-- `_calibrado_isotonic`) cujo `{base}` NÃO bate com nenhum
-- custom_model_configs.name existente hoje -- ou seja, órfão de uma config
-- deletada. Não apaga nada sozinha (só relata) -- a exclusão de verdade é
-- via `excluirDadosDoModeloPorNomeBase` em api/model-maintenance.js
-- (`?tarefa=excluir-modelo-orfao`), autenticada.
CREATE OR REPLACE FUNCTION public.modelos_custom_orfaos()
RETURNS TABLE (nome_base text, variantes bigint, total_previsoes bigint)
LANGUAGE sql
STABLE
AS $$
  WITH base AS (
    SELECT
      regexp_replace(model_name, '_calibrado_(platt|isotonic)$', '') AS model_name_sem_calib,
      model_name
    FROM public.model_predictions
    WHERE model_name LIKE '% [%]%'
  ),
  nomes AS (
    SELECT
      trim(substring(model_name_sem_calib FROM '^(.*) \[[^\[\]]*\]$')) AS nome_base,
      model_name
    FROM base
  )
  SELECT n.nome_base, COUNT(DISTINCT n.model_name) AS variantes, COUNT(*) AS total_previsoes
  FROM nomes n
  WHERE n.nome_base IS NOT NULL
    AND NOT EXISTS (SELECT 1 FROM public.custom_model_configs c WHERE c.name = n.nome_base)
  GROUP BY n.nome_base
  ORDER BY total_previsoes DESC;
$$;
