-- Adiciona coluna learning_curve que faltou na migration original
-- (model_v9_walkforward_results). O script walkforward_cv_v9.py já insere
-- esse campo desde a criação da tabela, mas a migration não incluía a coluna
-- → PostgREST retornava HTTP 201 com 0 linhas inseridas silenciosamente.
ALTER TABLE model_v9_walkforward_results
    ADD COLUMN IF NOT EXISTS learning_curve JSONB;
