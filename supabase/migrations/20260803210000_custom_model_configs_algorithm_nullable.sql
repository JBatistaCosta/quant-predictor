-- Torna `algorithm` nullable em custom_model_configs.
-- Configs no modo walk_forward_cv usam `algorithms[]` em vez de um único
-- algoritmo, portanto algorithm = NULL é semântica correta nesse modo.
-- A CHECK constraint original rejeita NULL; aqui a recriamos aceitando-o.

DO $$
DECLARE
  cname text;
BEGIN
  SELECT conname INTO cname
  FROM pg_constraint
  WHERE conrelid = 'custom_model_configs'::regclass
    AND contype = 'c'
    AND conname ILIKE '%algorithm%';
  IF cname IS NOT NULL THEN
    EXECUTE format('ALTER TABLE custom_model_configs DROP CONSTRAINT %I', cname);
  END IF;
END;
$$;

ALTER TABLE custom_model_configs
    ALTER COLUMN algorithm DROP NOT NULL;

ALTER TABLE custom_model_configs
    ADD CONSTRAINT custom_model_configs_algorithm_check
    CHECK (
        algorithm IS NULL
        OR algorithm IN (
            'catboost', 'xgboost', 'lightgbm',
            'logistic_regression', 'random_forest', 'dixon_coles'
        )
    );
