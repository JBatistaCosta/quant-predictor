-- Corrige 2 achados reais deixados em aberto numa sessão anterior (auditoria
-- de bugs dos sistemas de rating):
--
-- 1) Escritas não-atômicas em player_ratings/player_rating_history e em
--    team_elo/team_elo_history: o código hoje (api/model-maintenance.js
--    tarefaPlayerElo/regravarElo, scripts/elo_global.py) grava a tabela de
--    RATING primeiro e a de HISTÓRICO depois, em chamadas HTTP/round-trips
--    separadas (a de histórico ainda em lotes de 500). O cursor de retomada
--    dos dois sistemas é derivado da ÚLTIMA linha do HISTÓRICO (não do
--    rating) -- um crash/timeout entre as duas escritas (ou no meio de uma
--    delas) deixa o rating já refletindo um delta que o histórico não
--    registra. Na chamada seguinte, o cursor (que não avançou o
--    suficiente) reprocessa essas mesmas partidas por cima de um rating
--    que JÁ as tinha aplicado -- double-counting silencioso, sem erro
--    visível (confirmado como um risco real, não hipotético: FUNCTION_
--    INVOCATION_TIMEOUT aconteceu de fato 2x durante o reprocesso de
--    player-elo desta mesma sessão). As duas funções abaixo movem a
--    escrita de rating+histórico pra dentro de UMA função Postgres --
--    plpgsql roda como transação implícita, então ou as duas tabelas saem
--    consistentes ou nenhuma muda.
--
-- 2) team_elo_external.upsert (api/sync-clubelo.js) usa onConflict=
--    'fonte,clube_nome_externo,valido_de', SEM team_id -- se dois times
--    NOSSOS (team_id diferente) casarem com o mesmo nome externo do
--    ClubElo na mesma janela (ambiguidade de nome, já documentada como
--    risco conhecido no projeto -- CLAUDE.md: "nunca resolver esses
--    mapeamentos por heurística de nome sem supervisão manual: um
--    mapeamento errado corrompe todo sync futuro"), o upsert do segundo
--    time SOBRESCREVE a linha do primeiro (mesmo conflict key, team_id
--    diferente vira só mais um campo atualizado) em vez de criar uma linha
--    própria. Achado real, não hipotético: há hoje 2 times distintos
--    (team_id 129 e 964) cadastrados com o nome idêntico "Athletic Club" --
--    exatamente o cenário que dispara o bug se algum dia sincronizados.
--    Fix: adicionar team_id à chave.

-- ============================================================
-- 1a) player_ratings + player_rating_history, atômico
-- ============================================================
create or replace function public.registrar_player_elo_lote(p_ratings jsonb, p_historico jsonb)
returns void
language plpgsql
as $$
begin
  insert into public.player_ratings (player_id, rating, n_partidas, updated_at)
  select
    (r->>'player_id')::bigint,
    (r->>'rating')::numeric,
    (r->>'n_partidas')::integer,
    (r->>'updated_at')::timestamptz
  from jsonb_array_elements(p_ratings) as r
  on conflict (player_id) do update set
    rating = excluded.rating,
    n_partidas = excluded.n_partidas,
    updated_at = excluded.updated_at;

  insert into public.player_rating_history
    (player_id, match_id, rating_antes, rating_depois, indice_partida, fotmob_rating, minutes_played)
  select
    (h->>'player_id')::bigint,
    (h->>'match_id')::bigint,
    (h->>'rating_antes')::numeric,
    (h->>'rating_depois')::numeric,
    (h->>'indice_partida')::numeric,
    (h->>'fotmob_rating')::numeric,
    (h->>'minutes_played')::integer
  from jsonb_array_elements(p_historico) as h
  on conflict (player_id, match_id) do update set
    rating_antes = excluded.rating_antes,
    rating_depois = excluded.rating_depois,
    indice_partida = excluded.indice_partida,
    fotmob_rating = excluded.fotmob_rating,
    minutes_played = excluded.minutes_played;
end;
$$;

comment on function public.registrar_player_elo_lote(jsonb, jsonb) is
  'Grava player_ratings + player_rating_history numa transação só (ver api/model-maintenance.js::tarefaPlayerElo) -- evita o cursor (última linha de player_rating_history) ficar defasado do rating já gravado num crash/timeout no meio da escrita.';

-- ============================================================
-- 1b) team_elo + team_elo_history, atômico -- com delete opcional por
--     escopo (usado pelos caminhos "apaga e regrava do zero":
--     eloProcessarLiga/eloProcessarGeral em JS, --modo completo em
--     elo_global.py). p_delete_escopo NULL = só upsert, sem apagar nada
--     (caminho incremental de elo_global.py --modo incremental).
--     `league_id is not distinct from` trata NULL corretamente pros
--     escopos 'global'/'geral' (sem league_id), diferente de comparar com
--     `=` (que nunca bate com NULL).
-- ============================================================
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
  on conflict (team_id, escopo, league_id) do update set
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
  'Grava team_elo + team_elo_history numa transação só, com delete opcional por escopo (ver api/model-maintenance.js::regravarElo e scripts/elo_global.py) -- mesmo motivo de registrar_player_elo_lote.';

-- ============================================================
-- 2) team_elo_external: team_id entra na chave de conflito do upsert
-- ============================================================
alter table public.team_elo_external
  drop constraint if exists team_elo_external_fonte_clube_nome_externo_valido_de_key;

alter table public.team_elo_external
  add constraint team_elo_external_team_id_fonte_nome_valido_de_key
  unique (team_id, fonte, clube_nome_externo, valido_de);
