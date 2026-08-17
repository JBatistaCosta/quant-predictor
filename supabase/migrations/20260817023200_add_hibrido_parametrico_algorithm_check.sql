-- Bug real do PR #305: o algoritmo `hibrido_parametrico` (modelo misto) foi
-- adicionado ao seletor do Treino Customizado (`TreinoCustom.jsx`) e ao
-- dispatch do treino (`treinar_modelo_custom.py`), mas a CHECK constraint
-- de `custom_model_configs.algorithm` nunca foi atualizada -- ela ainda só
-- aceitava os 6 algoritmos anteriores. Resultado: salvar uma config com
-- `hibrido_parametrico` quebrava com "new row ... violates check
-- constraint", só descoberto ao tentar usar de verdade (não foi pego antes
-- porque o teste de fiação do PR usava um Supabase falso em memória, que
-- não tem constraints de banco).
--
-- `DROP CONSTRAINT IF EXISTS` + recriar (em vez de tentar um ALTER direto
-- no CHECK, que o Postgres não suporta) é seguro aqui porque a constraint
-- é reaplicada na mesma transação, sem janela em que fica ausente pra
-- escrita concorrente colar um valor inválido.

ALTER TABLE public.custom_model_configs
  DROP CONSTRAINT IF EXISTS custom_model_configs_algorithm_check;

ALTER TABLE public.custom_model_configs
  ADD CONSTRAINT custom_model_configs_algorithm_check
  CHECK (
    algorithm IS NULL
    OR algorithm = ANY (ARRAY[
      'catboost'::text, 'xgboost'::text, 'lightgbm'::text,
      'logistic_regression'::text, 'random_forest'::text, 'dixon_coles'::text,
      'hibrido_parametrico'::text
    ])
  );
