-- Adiciona coluna `market` a model_v9_walkforward_results para distinguir
-- resultados por mercado (1X2, over_under_2.5, btts) após extensão do
-- pipeline walkforward_cv_v9.py para mercados binários adicionais.
-- Linhas existentes (1X2) recebem default '1X2'.

ALTER TABLE model_v9_walkforward_results
    ADD COLUMN IF NOT EXISTS market TEXT NOT NULL DEFAULT '1X2';
