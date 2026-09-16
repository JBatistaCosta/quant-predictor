-- =============================================================================
-- Migration: fix de performance em calcular_concessao_zona_historica
-- =============================================================================
-- Achado real (16/09), tentando rodar uma amostra maior de validação:
-- consultas com mais de ~15-20 linhas (cada uma chamando esta função)
-- estouravam timeout de 60s. Causa raiz: a CTE `partidas_time` selecionava
-- os match_ids do time filtrando `p.team_id = p_team_id` DIRETO em cima de
-- `v_concessao_zona_partida` (view sobre `v_chutes_zoneados`, que computa
-- `time_concedeu` via CASE a partir de match_shots_fotmob JOIN matches) --
-- esse filtro não é indexável (é um valor calculado, não uma coluna crua),
-- então a cada chamada a função forçava escanear/computar a zona de uma
-- fatia grande de `match_shots_fotmob` inteiro antes de saber quais
-- partidas pertenciam ao time.
--
-- Fix: resolver os match_ids do time direto em `matches` (`home_team_id`/
-- `away_team_id`, colunas cruas, indexáveis) -- mesmo padrão que a função já
-- usava alguns comandos acima pra resolver `v_league_id`. Só DEPOIS de ter
-- a lista pequena (`p_n_max_partidas`) de match_ids é que
-- `v_concessao_zona_partida` entra, já filtrada por esses poucos
-- match_ids -- ordem de grandeza menor de linhas escaneadas.
-- =============================================================================
create or replace function public.calcular_concessao_zona_historica(
  p_team_id bigint,
  p_data_corte timestamptz,
  p_n_max_partidas integer default 20
)
returns table (
  corredor integer,
  profundidade text,
  chutes_concedidos_time bigint,
  chutes_totais_time bigint,
  taxa_time numeric,
  taxa_liga numeric,
  taxa_regularizada numeric,
  n_partidas_amostra integer
)
language plpgsql
stable
as $$
declare
  v_league_id bigint;
  v_k constant numeric := 12.0;
begin
  select m.league_id into v_league_id
  from public.matches m
  where (m.home_team_id = p_team_id or m.away_team_id = p_team_id)
    and m.match_date < p_data_corte
    and m.status = 'finished'
  order by m.match_date desc
  limit 1;

  if v_league_id is null then
    return;
  end if;

  return query
  with partidas_time as (
    -- FIX 16/09: resolvido direto em `matches` (colunas cruas, indexáveis),
    -- não mais filtrando por cima de v_concessao_zona_partida/v_chutes_
    -- zoneados (campo calculado via CASE, força scan grande a cada chamada).
    select m.id as match_id
    from public.matches m
    where (m.home_team_id = p_team_id or m.away_team_id = p_team_id)
      and m.match_date < p_data_corte
      and m.status = 'finished'
    order by m.match_date desc
    limit p_n_max_partidas
  ),
  agregado_time as (
    select p.corredor, p.profundidade,
      sum(p.chutes_concedidos) as chutes_zona,
      sum(sum(p.chutes_concedidos)) over () as chutes_total
    from public.v_concessao_zona_partida p
    join partidas_time pt on pt.match_id = p.match_id
    where p.team_id = p_team_id
    group by p.corredor, p.profundidade
  ),
  agregado_liga as (
    select vl.corredor as corredor_liga, vl.profundidade as profundidade_liga,
      vl.chutes_concedidos::numeric / nullif(sum(vl.chutes_concedidos) over (), 0) as taxa_liga
    from public.v_concessao_zona_liga vl
    where vl.league_id = v_league_id
  )
  select
    a.corredor,
    a.profundidade,
    a.chutes_zona::bigint,
    a.chutes_total::bigint,
    round(a.chutes_zona::numeric / nullif(a.chutes_total, 0), 4) as taxa_time,
    round(coalesce(l.taxa_liga, 0), 4) as taxa_liga,
    round(
      (a.chutes_zona / (a.chutes_zona + v_k)) *
        (case when l.taxa_liga > 0 then (a.chutes_zona::numeric / nullif(a.chutes_total, 0)) / l.taxa_liga else 1.0 end)
      + (v_k / (a.chutes_zona + v_k)) * 1.0
    , 4) as taxa_regularizada,
    (select count(*) from partidas_time)::integer as n_partidas_amostra
  from agregado_time a
  left join agregado_liga l on l.corredor_liga = a.corredor and l.profundidade_liga = a.profundidade;
end;
$$;
