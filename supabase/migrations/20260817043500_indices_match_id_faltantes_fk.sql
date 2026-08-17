-- Achado real rodando scripts/limpar_partidas_duplicadas.py em produção: DELETE
-- FROM matches travava com "canceling statement due to statement timeout"
-- (57014) porque 3 tabelas com FK match_id -> matches.id não tinham índice
-- nessa coluna, forçando o Postgres a varrer a tabela inteira em toda
-- verificação de FK a cada DELETE de partida.
create index if not exists idx_match_shots_fotmob_match_id on public.match_shots_fotmob (match_id);
create index if not exists idx_team_elo_history_match_id on public.team_elo_history (match_id);
create index if not exists idx_carteira_simulada_match_id on public.carteira_simulada (match_id);
