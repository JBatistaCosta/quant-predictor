-- Colunas de xGOT (expected goals on target) previsto por equipe, ao lado
-- das de xG já existentes -- mesmo padrão, populadas por
-- scripts/treinar_regressor_xgot.py, comparáveis com a realidade em
-- match_stats_fotmob.xgot (não em match_stats, que não tem essa coluna).
alter table model_match_estimates
  add column xgot_home_previsto numeric,
  add column xgot_away_previsto numeric;

comment on column model_match_estimates.xgot_home_previsto is 'xGOT previsto do mandante (scripts/treinar_regressor_xgot.py) -- comparar com match_stats_fotmob.xgot (realidade).';
comment on column model_match_estimates.xgot_away_previsto is 'xGOT previsto do visitante (scripts/treinar_regressor_xgot.py) -- comparar com match_stats_fotmob.xgot (realidade).';
