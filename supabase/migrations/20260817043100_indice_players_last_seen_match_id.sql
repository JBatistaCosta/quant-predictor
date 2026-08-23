-- Mesma causa do índice anterior (idx_match_shots_fotmob_match_id etc.,
-- migration 20260817043500): scripts/limpar_partidas_duplicadas.py repontua
-- players.last_seen_match_id pra vencedora antes de deletar a perdedora --
-- sem índice, cada UPDATE varre a tabela inteira (23k linhas) pra achar
-- quem aponta pra ela, travando com "statement timeout" (57014).
create index if not exists idx_players_last_seen_match_id on public.players (last_seen_match_id);
