-- =============================================================================
-- Migration: matriz de concessão por zona -- partida, liga e histórica (K-shrinkage)
-- =============================================================================
-- Continuação de `20260915110000_create_v_concessao_zona_time.sql` (que já
-- traz `v_chutes_zoneados` e `v_concessao_zona_time`, o agregado por time em
-- TODA a história). Pedido do usuário: mais 3 peças, mesma ressalva de
-- sempre -- **EXPLORATÓRIO, não alimenta pricing_pipeline.py nem treino de
-- modelo nenhum** (validação de 15/09: concessão por zona não bate o
-- baseline ingênuo de média da liga).
--
--  1. `v_concessao_zona_partida` -- matriz BRUTA de 1 partida (o nível que
--     faltava entre "1 chute" e "toda a história de um time").
--  2. `v_concessao_zona_liga` -- mesma lógica de `v_concessao_zona_time`,
--     trocando `team_id` por `league_id`.
--  3. `calcular_concessao_zona_historica(p_team_id, p_data_corte,
--     p_n_max_partidas)` -- matriz histórica PONTO-NO-TEMPO de um time (só
--     partidas antes de `p_data_corte`, sem vazamento), regularizada por
--     shrinkage bayesiano contra a média da liga NA MESMA JANELA.
--
-- POR QUE SHRINKAGE E NÃO TIME-DECAY: a fórmula de regularização já estava
-- especificada na spec original do usuário pra essa exata matriz --
-- `M_reg = N/(N+K) · (concessão_time/média_liga) + K/(N+K) · 1`, K=15 -- e o
-- projeto já usa esse padrão em outros lugares (`obter_gsax_atual`,
-- `_bayesiano_atual`). Time-decay (usado em escanteios, "janela 20j decay")
-- é complexidade adicional que só se justifica depois de sinal comprovado --
-- que esta métrica ainda não tem (ver migration anterior). Se o sinal for
-- revalidado no futuro com decay, é reaproveitar `v_concessao_zona_partida`
-- (já traz `match_date`) sem precisar tocar aqui.
--
-- `M_reg` é um MULTIPLICADOR relativo à média da liga na mesma zona/janela:
-- 1.0 = concede exatamente como a liga, >1.0 = concede mais que a liga
-- naquela zona, <1.0 = concede menos. N = chutes sofridos na zona (não
-- partidas) -- mesma unidade do numerador/denominador da razão.
-- =============================================================================

-- =============================================================================
-- 1. Matriz bruta por PARTIDA (mesma agregação de v_concessao_zona_time, só
--    que particionada também por match_id em vez de "toda a história")
-- =============================================================================
create or replace view public.v_concessao_zona_partida as
select
  match_id,
  time_concedeu as team_id,
  corredor,
  profundidade,
  count(*) as chutes_concedidos,
  round(100.0 * count(*) / sum(count(*)) over (partition by match_id, time_concedeu), 2) as pct_dos_chutes_do_time_na_partida,
  round(avg(xg)::numeric, 4) as xg_medio_por_chute,
  round(sum(xg)::numeric, 4) as xg_total_na_zona
from public.v_chutes_zoneados
group by match_id, time_concedeu, corredor, profundidade;

comment on view public.v_concessao_zona_partida is
  'Concessão de chutes por (partida, time, zona) -- matriz BRUTA de 1 jogo só, sem agregação histórica. EXPLORATÓRIO, mesma ressalva de v_concessao_zona_time (achado de 15/09: sem sinal preditivo incremental sobre a média da liga). Base pra `calcular_concessao_zona_historica` e pra qualquer análise ponto-no-tempo futura (já traz granularidade de partida, não precisa reagregar de v_chutes_zoneados).';

-- =============================================================================
-- 2. Matriz agregada por LIGA (mesma lógica de v_concessao_zona_time, por
--    league_id em vez de team_id)
-- =============================================================================
create or replace view public.v_concessao_zona_liga as
select
  m.league_id,
  v.corredor,
  v.profundidade,
  count(*) as chutes_concedidos,
  round(100.0 * count(*) / sum(count(*)) over (partition by m.league_id), 2) as pct_dos_chutes_da_liga,
  round(avg(v.xg)::numeric, 4) as xg_medio_por_chute,
  count(distinct v.match_id) as partidas_com_chute_nessa_zona
from public.v_chutes_zoneados v
join public.matches m on m.id = v.match_id
group by m.league_id, v.corredor, v.profundidade;

comment on view public.v_concessao_zona_liga is
  'Concessão de chutes por (liga, zona), agregado em TODA a história disponível -- mesma lógica de v_concessao_zona_time, por league_id em vez de team_id. EXPLORATÓRIO, mesma ressalva (achado de 15/09: sem sinal preditivo incremental). Usada como fallback/comparação em calcular_concessao_zona_historica.';

-- =============================================================================
-- 3. Matriz histórica PONTO-NO-TEMPO por time, regularizada (shrinkage K=15)
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
  v_k constant numeric := 15.0;  -- mesma constante K da spec original (shrinkage bayesiano)
begin
  -- Liga do time NA JANELA (mais recente antes do corte) -- usada como
  -- referência de "média da liga" pra normalizar; um time que trocou de
  -- liga usa a mais recente, não uma mistura de todas.
  select m.league_id into v_league_id
  from public.matches m
  where (m.home_team_id = p_team_id or m.away_team_id = p_team_id)
    and m.match_date < p_data_corte
    and m.status = 'finished'
  order by m.match_date desc
  limit 1;

  if v_league_id is null then
    return;  -- time sem histórico antes do corte -- nada a devolver, não é erro.
  end if;

  return query
  with partidas_time as (
    -- Últimas p_n_max_partidas do time ANTES do corte -- ponto-no-tempo,
    -- sem vazamento (mesmo espírito de dados_historicos.obter_gsax_atual,
    -- mas aqui com corte de data explícito por parâmetro em vez de "hoje").
    select p.match_id
    from public.v_concessao_zona_partida p
    join public.matches m on m.id = p.match_id
    where p.team_id = p_team_id and m.match_date < p_data_corte
    group by p.match_id, m.match_date
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
    -- M_reg = N/(N+K)*(taxa_time/taxa_liga) + K/(N+K)*1 -- taxa_liga=0
    -- (zona nunca vista na liga) cai direto no prior neutro (1.0), sem
    -- dividir por zero.
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

comment on function public.calcular_concessao_zona_historica is
  'Matriz de concessão por zona de UM time, ponto-no-tempo (só partidas com match_date < p_data_corte, sem vazamento), regularizada por shrinkage bayesiano contra a média da liga na mesma janela (K=15, mesma constante da spec original "Pricing Pipeline v2"). taxa_regularizada é um MULTIPLICADOR relativo à liga (1.0=neutro). EXPLORATÓRIO -- achado de 15/09 (ver v_concessao_zona_time) não encontrou sinal preditivo incremental nessa métrica; não usar pra decisão de modelo sem revalidar. Uso: select * from calcular_concessao_zona_historica(<team_id>, now(), 20);';
