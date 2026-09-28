-- =============================================================================
-- Migration: índice parcial pra lambda_xa_jogo em player_match_walkforward
-- =============================================================================
-- Mesmo problema já encontrado e corrigido pra lambda_defesas_jogo
-- (20260927120000_add_idx_defesas_walkforward.sql): a query de
-- carregar_dados() em analisar_nb2_assistencias.py filtra por (league_id,
-- fonte_titular IN ('real','previsto'), lambda_xa_jogo IS NOT NULL)
-- ordenando por id -- sem índice de suporte, cairia no mesmo Index Scan pela
-- PK filtrando linha a linha e estouraria o statement_timeout do PostgREST
-- (confirmado por EXPLAIN antes de rodar o workflow de verdade, mesma
-- assinatura do achado de defesas).
--
-- `fonte_titular` fica FORA da chave do índice (ao contrário do índice de
-- defesas, que usa `.eq()` de um valor só) porque a query usa `.in_()` com 2
-- valores -- incluir fonte_titular no meio do índice quebraria a ordenação
-- por `id` que a paginação depende (Postgres não mescla os dois ramos do
-- `IN` num único scan ordenado). Fica como filtro residual barato aplicado
-- sobre o resultado já filtrado por league_id+id.
-- =============================================================================

create index if not exists idx_player_match_walkforward_liga_xa_id
  on public.player_match_walkforward using btree (league_id, id)
  where (lambda_xa_jogo is not null);
