-- Índices nas 12 colunas que apontam para teams(id) e não tinham índice.
-- Motivo: apagar (ou conferir) um time faz o banco varrer, tabela por tabela,
-- a procura de linhas que ainda apontem para ele. Sem índice isso é uma leitura
-- da tabela inteira: 11 tabelas (somando ~3,4 milhões de linhas) fizeram o
-- DELETE de um time vazio estourar 60 s em 04/10/2026.
-- Já aplicados em produção um a um (24 MB no total); aqui ficam idempotentes
-- para um banco novo (ex.: branch de preview do Supabase) nascer igual.
create index if not exists idx_match_stats_team_id                       on public.match_stats (team_id);
create index if not exists idx_match_features_contexto_team_id           on public.match_features_contexto (team_id);
create index if not exists idx_match_disciplina_team_id                  on public.match_disciplina (team_id);
create index if not exists idx_match_goal_timeline_team_id               on public.match_goal_timeline (team_id);
create index if not exists idx_match_stats_fotmob_team_id                on public.match_stats_fotmob (team_id);
create index if not exists idx_match_stats_fotmob_periodo_team_id        on public.match_stats_fotmob_periodo (team_id);
create index if not exists idx_player_match_estimates_team_id            on public.player_match_estimates (team_id);
create index if not exists idx_players_last_team_id                      on public.players (last_team_id);
create index if not exists idx_match_unavailable_coleta_fotmob_team_id   on public.match_unavailable_coleta_fotmob (team_id);
create index if not exists idx_match_shots_fotmob_team_id                on public.match_shots_fotmob (team_id);
create index if not exists idx_match_player_stats_fotmob_team_id         on public.match_player_stats_fotmob (team_id);
create index if not exists idx_player_match_walkforward_team_id          on public.player_match_walkforward (team_id);
