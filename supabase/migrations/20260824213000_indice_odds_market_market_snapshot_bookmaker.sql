-- odds_market (2,5M linhas / 692MB) só tinha índices liderados por
-- match_id -- (match_id), (match_id, market), (match_id, origem). Nenhum
-- serve o filtro inverso (market+snapshot+bookmaker fixos, match_id em
-- lote via .in_()), usado por scripts/backtest_kelly.py
-- (_carregar_odds_pinnacle_brutas, todo o backtest de qualidade/ROI/CLV
-- contra a Pinnacle) e scripts/avaliar_modelo_misto_vs_mercado.py.
--
-- Estourou statement_timeout (57014) de verdade em produção rodando a
-- avaliação abertura/fechamento do modelo misto (2026-08-24) -- mesma
-- classe de bug já corrigida uma vez em model_predictions (achado #19 do
-- CONTEXTO_PROJETO.md, PR #325): tabela grande, sem índice que sirva o
-- padrão de acesso "todas as odds de UM mercado/snapshot/bookmaker",
-- fazendo o Postgres varrer via idx_odds_match_market (liderado por
-- match_id) e descartar linha por linha até achar as poucas que batem
-- market+snapshot+bookmaker, por match_id do lote inteiro.
CREATE INDEX IF NOT EXISTS idx_odds_market_market_snapshot_bookmaker_match
    ON public.odds_market (market, snapshot, bookmaker, match_id);
