-- Bug real reportado pelo usuário: SE Palmeiras duplicado no ranking de Elo
-- (/ratings, nível "Federação"/Brasil) e a UI quebrando ao trocar pra
-- "Geral"/"Por confederação".
--
-- Causa raiz: a UNIQUE (team_id, escopo, league_id) de team_elo não protege
-- contra duplicata nos escopos 'global'/'geral', porque `league_id` é NULL
-- nesses escopos (só 'liga' preenche league_id) -- e o Postgres nunca trata
-- duas linhas com NULL na mesma coluna como conflitantes pra fins de UNIQUE/
-- ON CONFLICT (NULL <> NULL). `registrar_team_elo_lote` (migration
-- 20260902220000) já usa `is not distinct from` no DELETE, mas o
-- `insert ... on conflict (team_id, escopo, league_id)` do upsert normal
-- (sem delete-and-regrow) caiu nessa mesma armadilha: o índice de inferência
-- do ON CONFLICT também não casa linhas com league_id NULL, então uma
-- segunda chamada pro mesmo time em escopo global vira INSERT novo em vez
-- de UPDATE. Achado real (não hipotético): team_id=1 (SE Palmeiras) tinha 2
-- linhas idênticas em team_elo (ids 7476/8880, escopo='global', league_id
-- NULL) -- já removida a duplicata (id 8880) antes desta migration.
--
-- O duplicado em team_elo (usada como roster da tela) também duplicava a
-- key do React na tabela/timeline -- com o roster completo de "Geral"
-- (~700 times, todas as competições) e "Por confederação" isso é grande o
-- bastante pra reconciliação de lista do React quebrar de verdade (não só
-- warning), o que bate com o relato de a página dar erro nesses dois
-- níveis e funcionar (só com o dado feio) em "Federação".
--
-- Fix: índice único parcial pro caso league_id IS NULL (cobre 'global' e
-- 'geral'), e o insert de registrar_team_elo_lote separado em 2 ramos --
-- um por índice, já que um único ON CONFLICT não infere contra dois índices
-- diferentes na mesma instrução.

create unique index if not exists team_elo_team_id_escopo_key
  on public.team_elo (team_id, escopo)
  where league_id is null;

create or replace function public.registrar_team_elo_lote(
  p_elo jsonb, p_historico jsonb,
  p_delete_escopo text default null, p_delete_league_id bigint default null
)
returns void
language plpgsql
as $$
begin
  if p_delete_escopo is not null then
    delete from public.team_elo_history
      where escopo = p_delete_escopo and league_id is not distinct from p_delete_league_id;
    delete from public.team_elo
      where escopo = p_delete_escopo and league_id is not distinct from p_delete_league_id;
  end if;

  insert into public.team_elo (team_id, escopo, league_id, rating, partidas, atualizado_em)
  select
    (e->>'team_id')::bigint,
    e->>'escopo',
    (e->>'league_id')::bigint,
    (e->>'rating')::numeric,
    (e->>'partidas')::integer,
    (e->>'atualizado_em')::timestamptz
  from jsonb_array_elements(p_elo) as e
  where (e->>'league_id') is not null
  on conflict (team_id, escopo, league_id) do update set
    rating = excluded.rating,
    partidas = excluded.partidas,
    atualizado_em = excluded.atualizado_em;

  insert into public.team_elo (team_id, escopo, league_id, rating, partidas, atualizado_em)
  select
    (e->>'team_id')::bigint,
    e->>'escopo',
    null::bigint,
    (e->>'rating')::numeric,
    (e->>'partidas')::integer,
    (e->>'atualizado_em')::timestamptz
  from jsonb_array_elements(p_elo) as e
  where (e->>'league_id') is null
  on conflict (team_id, escopo) where league_id is null do update set
    rating = excluded.rating,
    partidas = excluded.partidas,
    atualizado_em = excluded.atualizado_em;

  insert into public.team_elo_history (team_id, escopo, league_id, match_id, rodada, rating_antes, rating_depois, match_date)
  select
    (h->>'team_id')::bigint,
    h->>'escopo',
    (h->>'league_id')::bigint,
    (h->>'match_id')::bigint,
    (h->>'rodada')::integer,
    (h->>'rating_antes')::numeric,
    (h->>'rating_depois')::numeric,
    (h->>'match_date')::date
  from jsonb_array_elements(p_historico) as h
  on conflict (team_id, escopo, match_id) do update set
    league_id = excluded.league_id,
    rodada = excluded.rodada,
    rating_antes = excluded.rating_antes,
    rating_depois = excluded.rating_depois,
    match_date = excluded.match_date;
end;
$$;

comment on function public.registrar_team_elo_lote(jsonb, jsonb, text, bigint) is
  'Grava team_elo + team_elo_history numa transação só, com delete opcional por escopo (ver api/model-maintenance.js::regravarElo e scripts/elo_global.py). O upsert de team_elo é feito em 2 INSERTs (league_id NULL vs NOT NULL) porque o índice único pros escopos global/geral (league_id NULL) é parcial -- um ON CONFLICT só infere contra um índice por vez (ver migration 20260910090000, que corrigiu duplicata real de team_id=1/SE Palmeiras causada por isso).';
