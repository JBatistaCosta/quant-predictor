-- Parâmetros de distribuição por partida, do modelo misto/paramétrico.
--
-- `model_match_estimates` nasceu guardando só xG previsto por equipe
-- (colunas tipadas `xg_home_previsto`/`xg_away_previsto`, depois xGOT). O
-- modelo misto (`scripts/treinar_modelo_hibrido.py`) precisa persistir um
-- conjunto MAIOR e VARIÁVEL de parâmetros -- λ de gols por equipe, ρ do
-- Dixon-Coles, λ e dispersão r dos escanteios, α/β do split Beta-Binomial
-- -- e esse conjunto muda conforme a família de distribuição de cada
-- estatística.
--
-- Coluna jsonb em vez de uma coluna tipada por parâmetro, de propósito:
-- acrescentar cartões (v2) ou trocar a família de uma estatística passaria
-- a exigir migração de schema a cada vez. As colunas antigas continuam
-- onde estão -- `treinar_regressor_xg.py` e o relatório partida a partida
-- ainda dependem delas, e o modelo misto também preenche
-- `xg_home_previsto`/`xg_away_previsto` com seus λ de gols pra não quebrar
-- quem já lê dali.
--
-- A PK lógica (match_id, model_name) já existe e suporta várias linhas por
-- partida (uma por modelo), então nada muda no upsert.

ALTER TABLE model_match_estimates
  ADD COLUMN IF NOT EXISTS params jsonb;

COMMENT ON COLUMN model_match_estimates.params IS
  'Parâmetros da distribuição estimados por ML pra esta partida (modelo misto). '
  'Chaves usadas por scripts/distribuicoes.py: lambda_home, lambda_away, rho '
  '(gols: Poisson + correção Dixon-Coles); corners_lambda_total, corners_disp_r, '
  'corners_alpha, corners_beta (escanteios: Binomial Negativa no total + split '
  'Beta-Binomial). Formato aberto de propósito: acrescentar mercado ou trocar '
  'família de distribuição não deve exigir migração.';

-- O frontend (src/utils/lambdaFormulas.js, fórmula `ml_params`) lê esta
-- tabela por partida via supabase-js. RLS já está habilitado com leitura
-- pública; o índice abaixo é o que evita seq scan nessa consulta.
CREATE INDEX IF NOT EXISTS idx_model_match_estimates_params
  ON model_match_estimates (match_id)
  WHERE params IS NOT NULL;
