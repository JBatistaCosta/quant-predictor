-- Curva de domínio/xT minuto a minuto, achado 10/09 (destacado pelo usuário
-- como candidato forte a virar feature de modelo em breve): content.momentum
-- .main.data no payload de matchDetails é uma série temporal
-- [{minute, value}] -- 1 ponto por minuto, ~90-95 pontos por partida.
-- `value` positivo = mandante domina naquele minuto, negativo = visitante
-- (debugTitle real do payload: "Using xT SA-version" -- baseado em xT, não
-- em posse simples). Complementar a match_team_game_state (que só enxerga
-- domínio via MUDANÇA de placar) e a match_shots_fotmob (evento discreto):
-- é a única fonte de "quem estava melhor NAQUELE minuto" independente de
-- gol ter saído ou não.
--
-- Ausente (content.momentum é `false`, não a chave faltando -- dado real de
-- "essa partida não tem") em payload de partida antiga, mesma limitação já
-- documentada pros outros achados desta auditoria (zonas de ataque,
-- estatística por tempo, substituição).
create table if not exists match_momentum_fotmob (
  id bigint generated always as identity primary key,
  match_id bigint not null references matches(id) on delete cascade,
  minute integer not null,
  value numeric not null,
  created_at timestamptz not null default now(),
  unique (match_id, minute)
);

create index if not exists idx_match_momentum_fotmob_match on match_momentum_fotmob (match_id);

alter table match_momentum_fotmob enable row level security;

create policy "Public read access" on match_momentum_fotmob
  for select using (true);

-- Mesmo padrão de attacking_zone_checked/periodo_checked: distingue "nunca
-- tentado" de "tentado, sem momentum na fonte" (partida antiga) -- sem isso
-- o backfill reprocessaria essas partidas pra sempre.
alter table match_stats_fotmob
  add column if not exists momentum_checked boolean not null default false;
