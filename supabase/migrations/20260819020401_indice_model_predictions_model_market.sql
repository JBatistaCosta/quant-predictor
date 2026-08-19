-- model_predictions já passou de 2,5M linhas (605 MB) e só tinha índice em
-- `id` (PK) e no unique composto (match_id, model_name, market, selection) --
-- útil pra ler UMA partida, mas inútil pro padrão inverso: filtrar por
-- model_name+market e varrer TODAS as partidas dele (ex.: comparar um
-- modelo contra o mercado em todo o histórico, scripts/avaliar_modelo_
-- misto_vs_mercado.py). Sem índice que sirva esse filtro, o Postgres faz
-- seq scan da tabela inteira + sort por match_id, e estourou o
-- statement_timeout do PostgREST na primeira tentativa real (erro 57014).
CREATE INDEX IF NOT EXISTS idx_model_predictions_model_market_match
    ON public.model_predictions USING btree (model_name, market, match_id);
