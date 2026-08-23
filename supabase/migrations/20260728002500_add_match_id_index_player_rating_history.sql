-- `player_rating_history` só tinha um índice único (player_id, match_id) --
-- com match_id na posição SECUNDÁRIA, inútil pra filtrar só por match_id
-- (que é exatamente o padrão de `dados_historicos._carregar_squad_rating_
-- pre_jogo`, chamada por `montar_dataset_ml_empilhado`). Sem índice líder
-- em match_id, a query "match_id IN (...)" varre a tabela inteira -- ficou
-- borderline com as 5 ligas europeias e estourou o statement_timeout ao
-- incluir o Brasileirão (mais ~35 mil linhas, ~1.315 partidas com rating).
CREATE INDEX IF NOT EXISTS idx_player_rating_history_match_id
  ON public.player_rating_history (match_id);
