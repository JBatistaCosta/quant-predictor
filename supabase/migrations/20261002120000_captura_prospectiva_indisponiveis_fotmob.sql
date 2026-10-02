-- Captura PROSPECTIVA dos indisponíveis (lesão/suspensão) por jogo, vindos do FotMob.
--
-- Por que prospectiva: `matchDetails.content.lineup.{homeTeam,awayTeam}.unavailable` reflete a data da
-- COLETA, não a do jogo (jogo antigo lista jogadores que nem estavam no clube -- achado de 02/10/2026,
-- CONTEXTO_PROJETO.md). A única forma de ter histórico fiel é guardar a lista ANTES de cada jogo, a partir
-- de agora (scripts/ingerir_escalacao_pre_jogo.py, cron de 15 min na janela pré-jogo).
--
-- `team_unavailable_fotmob` já existia (migration 20260729150000), vazia. Aqui ela ganha as colunas que
-- faltavam e uma chave única por (jogo, jogador). Uma linha = o ÚLTIMO retrato do jogador na lista daquele
-- jogo (captured_at = momento da coleta; linhas de coletas anteriores que sumiram da lista são apagadas).

alter table public.team_unavailable_fotmob add column if not exists team_id bigint references public.teams (id) on delete cascade;
alter table public.team_unavailable_fotmob add column if not exists player_name text;
alter table public.team_unavailable_fotmob add column if not exists injury_id integer;  -- N de `injury_N` no perfil do jogador (76 = LCA)
create unique index if not exists ux_team_unavailable_match_player on public.team_unavailable_fotmob (match_id, fotmob_player_id);
create index if not exists idx_team_unavailable_team on public.team_unavailable_fotmob (team_id, match_id);

-- Registro de que a lista FOI coletada para (jogo, time), mesmo vazia: sem isso, "ninguém indisponível" e
-- "nunca coletado" seriam indistinguíveis (ausência de linhas nas duas situações).
create table if not exists public.match_unavailable_coleta_fotmob (
  match_id bigint not null references public.matches (id) on delete cascade,
  team_id bigint not null references public.teams (id) on delete cascade,
  n_indisponiveis integer not null,
  captured_at timestamptz not null default now(),
  primary key (match_id, team_id)
);

-- Dicionário de tipos de lesão do FotMob (injuryId == N de `injury_N`). Nomes vistos em 60 lesionados
-- atuais + jogos de 10/10/2026; NÃO é completo -- códigos novos aparecem em team_unavailable_fotmob com
-- injury_id e entram aqui depois. `grupo` é classificação NOSSA (não do FotMob):
--   lca | estrutural (ligamento/menisco/joelho/articulações) | fratura | muscular | leve_indefinida | doenca
create table if not exists public.fotmob_injury_types (
  injury_id integer primary key,
  nome text not null,
  grupo text not null check (grupo in ('lca', 'estrutural', 'fratura', 'muscular', 'leve_indefinida', 'doenca'))
);
insert into public.fotmob_injury_types (injury_id, nome, grupo) values
  (76, 'Cruciate ligament injury', 'lca'),
  (71, 'Ligament injury', 'estrutural'),
  (70, 'Meniscus injury', 'estrutural'),
  (14, 'Knee injury', 'estrutural'),
  (30, 'Ankle injury', 'estrutural'),
  (33, 'Elbow injury', 'estrutural'),
  (31, 'Hip injury', 'estrutural'),
  (45, 'Back injury', 'estrutural'),
  (74, 'Leg injury', 'estrutural'),
  (13, 'Broken arm', 'fratura'),
  (22, 'Broken rib', 'fratura'),
  (87, 'Muscle injury', 'muscular'),
  (42, 'Hamstring injury', 'muscular'),
  (69, 'Thigh injury', 'muscular'),
  (101, 'Calf injury', 'muscular'),
  (47, 'Groin injury', 'muscular'),
  (115, 'Strain injury', 'muscular'),
  (130, 'Knock', 'leve_indefinida'),
  (6, 'Injured', 'leve_indefinida'),
  (121, 'Physical discomfort', 'leve_indefinida'),
  (96, 'Virus', 'doenca')
on conflict (injury_id) do nothing;

alter table public.match_unavailable_coleta_fotmob enable row level security;
alter table public.fotmob_injury_types enable row level security;
drop policy if exists "leitura publica" on public.match_unavailable_coleta_fotmob;
drop policy if exists "leitura publica" on public.fotmob_injury_types;
create policy "leitura publica" on public.match_unavailable_coleta_fotmob for select using (true);
create policy "leitura publica" on public.fotmob_injury_types for select using (true);
