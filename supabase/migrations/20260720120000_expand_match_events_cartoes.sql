-- Expande `match_events` (existia desde o schema original, mas nunca foi
-- populada -- 0 linhas) pra guardar eventos de cartão por JOGADOR, vindos
-- do FotMob (`content.matchFacts.events.events`, MESMO payload já usado
-- por match_stats_fotmob/match_player_stats_fotmob -- confirmado por
-- inspeção direta da resposta real da API antes de desenhar este schema).
--
-- `event_type` guarda o tipo já normalizado ('yellow_card' | 'red_card' |
-- 'second_yellow_card', mapeado a partir do campo `card` do FotMob:
-- 'Yellow' | 'Red' | 'YellowRed') -- não guardamos o valor cru do FotMob
-- direto pra manter o mesmo vocabulário usado no resto do pipeline
-- (`match_stats_fotmob.yellow_cards`/`red_cards`).
alter table public.match_events
  add column source text not null default 'fotmob',
  add column player_id bigint references public.players(id),
  add column fotmob_event_id bigint;

-- Único escritor por enquanto (fotmob) -- upsert idempotente por
-- (match_id, fotmob_event_id), mesmo padrão de match_source_ids/
-- match_shots_fotmob (on_conflict por id da fonte externa).
alter table public.match_events
  add constraint match_events_match_fotmob_event_unique unique (match_id, fotmob_event_id);

create index if not exists idx_match_events_player on public.match_events(player_id);
create index if not exists idx_match_events_team_type on public.match_events(team_id, event_type);

comment on column public.match_events.source is 'Fonte do evento (só ''fotmob'' por enquanto) -- mesma disciplina de match_stats.xg_source: nunca sobrescreve outra fonte sem identificação.';
comment on column public.match_events.fotmob_event_id is 'eventId do FotMob (content.matchFacts.events.events[].eventId) -- chave de idempotência do upsert.';
