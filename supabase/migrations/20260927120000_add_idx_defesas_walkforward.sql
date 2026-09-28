-- =============================================================================
-- Migration: índice parcial pra lambda_defesas_jogo em player_match_walkforward
-- =============================================================================
-- ACHADO 27/09 (rodando arquivos_do_claude/analisar_nb2_defesas.py via
-- workflow_dispatch em produção): a query de carregar_dados() filtra por
-- (league_id, fonte_titular='relacionados', lambda_defesas_jogo IS NOT NULL)
-- ordenando por id -- não existe índice pra esse predicado (só o análogo pra
-- lambda_xg_jogo, idx_player_match_walkforward_liga_temporada_fonte_id), então
-- o planner cai num Parallel Index Scan pela PK (ordenado por id) filtrando
-- linha a linha: 12,6s só na 1a página (limit 1000 offset 0) da liga 24,
-- removendo 660 mil linhas pra achar 591 -- estoura o statement_timeout do
-- PostgREST antes de terminar a 1a liga grande.
--
-- Mesma forma do índice de lambda_xg_jogo, só trocando a coluna do predicado
-- parcial -- sem season no índice porque a query real não filtra por season
-- (carrega o walk-forward inteiro por liga, split train/OOS é feito em
-- memória no script).
-- =============================================================================

create index if not exists idx_player_match_walkforward_liga_fonte_defesas_id
  on public.player_match_walkforward using btree (league_id, fonte_titular, id)
  where (lambda_defesas_jogo is not null);
