-- =============================================================================
-- Migration: match_lineup_fotmob -- escalação (titular/reserva) por partida
-- =============================================================================
-- O payload matchDetails do FotMob (mesmo já usado por match_stats_fotmob/
-- match_player_stats_fotmob, ver arquivos_do_claude/ingestao_fotmob.py) traz
-- content.lineup.{homeTeam,awayTeam}.{starters,subs} -- a distinção exata
-- entre quem começou titular e quem ficou no banco, mais a posição do
-- jogador no gráfico de formação. Até agora esse bloco só era usado pra
-- atualizar o "último visto" de cada jogador (players.raw_lineup, sobrescrito
-- a cada partida) -- esta tabela persiste um HISTÓRICO por partida, base
-- pra features de "XI titular" (v3B do Model Benchmarking) sem depender de
-- aproximação por minutos jogados (que mistura reserva que entrou).
--
-- is_starter=true = veio do grupo "starters" (começou o jogo); false = veio
-- de "subs" (começou no banco, PODE ou não ter entrado -- minutes_played em
-- match_player_stats_fotmob é que diz se entrou de verdade).
--
-- position_id é o enum interno do FotMob, cru -- mesma ressalva de
-- players.usual_position_id: NÃO tem legenda confiável pra decodificar em
-- nome de posição (GK/DEF/MEI/ATA) sem adivinhar, então fica só como dado
-- bruto disponível pra uso futuro, não como feature pronta.
-- =============================================================================

create table if not exists public.match_lineup_fotmob (
  id                 bigint generated always as identity primary key,
  match_id           bigint not null references public.matches(id),
  team_id            bigint not null references public.teams(id),
  player_id          bigint references public.players(id),
  fotmob_player_id   text not null,
  is_starter         boolean not null,
  shirt_number       text,
  position_id        integer,
  raw                jsonb,
  captured_at        timestamptz not null default now(),

  constraint match_lineup_fotmob_match_team_player_key unique (match_id, team_id, fotmob_player_id)
);

comment on table public.match_lineup_fotmob is
  'Escalação histórica por partida (titular vs. reserva), extraída de content.lineup do matchDetails do FotMob -- base pra features de "XI titular" (v3B) sem depender de aproximação por minutos jogados. position_id é dado cru (enum FotMob não decodificado, não adivinhar); raw guarda o bloco completo do jogador (inclui verticalLayout/horizontalLayout -- coordenada no gráfico de formação) pra uso futuro sem precisar rebuscar a fonte.';

create index if not exists idx_match_lineup_fotmob_match on public.match_lineup_fotmob (match_id);
create index if not exists idx_match_lineup_fotmob_team on public.match_lineup_fotmob (team_id);
create index if not exists idx_match_lineup_fotmob_player on public.match_lineup_fotmob (player_id);

-- =============================================================================
-- RLS -- mesmo padrão do restante do projeto: leitura pública, escrita só via
-- service_role (script de ingestão manual, arquivos_do_claude/ingestao_fotmob.py).
-- =============================================================================
alter table public.match_lineup_fotmob enable row level security;

drop policy if exists "match_lineup_fotmob_public_read" on public.match_lineup_fotmob;
create policy "match_lineup_fotmob_public_read"
  on public.match_lineup_fotmob
  for select
  to anon, authenticated
  using (true);
