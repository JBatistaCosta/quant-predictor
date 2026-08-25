-- model_predictions (5M+ linhas) só tinha índices liderados por
-- (model_name, market, match_id) ou (match_id, model_name, market, selection)
-- -- nenhum serve o padrão de acesso "todas as previsões de UM mercado,
-- qualquer modelo" (usado pela carga sem filtro de /api/model-stats, que
-- precisa agregar log-loss/Brier/acurácia por modelo+mercado+liga pra TODOS
-- os 53 model_name distintos de uma vez).
--
-- Mesma classe de bug já corrigida em odds_market (migration
-- 20260824213000) e documentada como achado #19 (CONTEXTO_PROJETO.md, PR
-- #325) pro índice original desta tabela: sem um índice liderado por
-- `market`, o Postgres varre a tabela inteira (Parallel Seq Scan,
-- confirmado via EXPLAIN ANALYZE: ~12s só pra 1X2, descartando 2,25M linhas
-- de outros mercados por filtro) -- estourava statement_timeout em
-- produção na carga inicial do painel /modelos.
CREATE INDEX IF NOT EXISTS idx_model_predictions_market_match_selection
    ON public.model_predictions (market, match_id, selection);
