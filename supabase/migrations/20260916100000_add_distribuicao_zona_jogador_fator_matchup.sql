-- =============================================================================
-- Migration: distribuição de zona por jogador (w_i,z) + fator de matchup zonal
-- =============================================================================
-- Continuação de `20260915120000_create_concessao_zona_partida_liga_historica.sql`.
-- Fecha a fórmula original da spec "Pricing Pipeline v2" (item 3A):
--
--   λ_i,j^chutes = λ_i^base × Σ_z (w_i,z · M_j,z^reg)
--
-- w_i,z = distribuição empírica histórica de chutes do ATLETA por zona
-- (Σw_i,z=1 sobre as zonas que ele já visitou). M_j,z^reg já existe
-- (`calcular_concessao_zona_historica`). λ_i^base é o que JÁ está
-- persistido em `player_match_estimates.lambda_chutes_jogo` (modelo de
-- chutes/xG por jogador) -- não recalculado aqui.
--
-- FIX na mesma migration: `calcular_concessao_zona_historica` (PR #564)
-- usava `K=15` com o comentário errado "mesma constante da spec original"
-- -- a fórmula real do usuário é `N/(N+12)` e `12/(N+12)`, ou seja `K=12`.
-- Corrigido abaixo (`create or replace function`, mesma assinatura).
--
-- ESCOPO: pedido explícito do usuário -- modelo NOVO derivado (usa o λ_base
-- já existente, não recalcula o modelo de chutes do zero), persistido em
-- COLUNA NOVA (`lambda_chutes_jogo_zona_ajustado`) sem sobrescrever
-- `lambda_chutes_jogo` -- mesmo tratamento que `lambda_gols_jogo_direto`
-- já recebe (candidato alternativo, promovido só depois de backtest
-- walk-forward mostrar vantagem real). A validação de sinal de 15/09 não
-- encontrou vantagem preditiva de concessão por zona sobre a média da
-- liga -- a expectativa correta pro backtest deste fator é empate ou
-- piora marginal, não melhora automática.
-- =============================================================================

-- =============================================================================
-- 0. Fix: K=12 (não 15) em calcular_concessao_zona_historica
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
  v_k constant numeric := 12.0;  -- fix 16/09: era 15.0 -- fórmula real do usuário usa K=12.
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
  'Matriz de concessão por zona de UM time, ponto-no-tempo (só partidas com match_date < p_data_corte, sem vazamento), regularizada por shrinkage bayesiano contra a média da liga na mesma janela (K=12, fórmula do usuário: N/(N+12) e 12/(N+12)). taxa_regularizada é um MULTIPLICADOR relativo à liga (1.0=neutro). EXPLORATÓRIO -- achado de 15/09 (ver v_concessao_zona_time) não encontrou sinal preditivo incremental nessa métrica; não usar pra decisão de modelo sem revalidar. Uso: select * from calcular_concessao_zona_historica(<team_id>, now(), 20);';

-- =============================================================================
-- 1. w_i,z -- distribuição histórica de chutes do JOGADOR por zona, ponto-no-tempo
-- =============================================================================
-- Mesma classificação de zona de v_chutes_zoneados (5 corredores x 3
-- profundidades, limiares em metros reais) -- só que aqui o filtro é por
-- QUEM CHUTOU (player_id), não pelo time que concedeu.
create or replace function public.calcular_distribuicao_zona_jogador(
  p_player_id bigint,
  p_data_corte timestamptz,
  p_n_max_partidas integer default 20
)
returns table (
  corredor integer,
  profundidade text,
  chutes_jogador bigint,
  peso numeric,
  n_partidas_amostra integer
)
language plpgsql
stable
as $$
begin
  return query
  with partidas_jogador as (
    select distinct s.match_id, m.match_date
    from public.match_shots_fotmob s
    join public.matches m on m.id = s.match_id
    where s.player_id = p_player_id and m.match_date < p_data_corte
    order by m.match_date desc
    limit p_n_max_partidas
  ),
  chutes_zoneados_jogador as (
    select
      case
        when s.y < 14.35 then 1
        when s.y < 25.02 then 2
        when s.y <= 42.98 then 3
        when s.y <= 53.65 then 4
        else 5
      end as corredor,
      case
        when s.x >= 98.91 then 'pequena_area'
        when s.x >= 87.15 then 'grande_area'
        else 'entrada_area'
      end as profundidade
    from public.match_shots_fotmob s
    join partidas_jogador pj on pj.match_id = s.match_id
    where s.player_id = p_player_id
      and s.x >= 68.25 and s.x <= 105 and s.y >= 0 and s.y <= 68
  ),
  agregado as (
    select c.corredor, c.profundidade, count(*) as chutes
    from chutes_zoneados_jogador c
    group by c.corredor, c.profundidade
  )
  select
    a.corredor,
    a.profundidade,
    a.chutes::bigint,
    round(a.chutes::numeric / nullif(sum(a.chutes) over (), 0), 4) as peso,
    (select count(*) from partidas_jogador)::integer as n_partidas_amostra
  from agregado a;
end;
$$;

comment on function public.calcular_distribuicao_zona_jogador is
  'w_i,z: distribuição empírica histórica de chutes do JOGADOR por zona (18 zonas, mesma classificação de v_chutes_zoneados), ponto-no-tempo (só partidas com match_date < p_data_corte). peso soma 1 sobre as zonas que o jogador já visitou na amostra -- zona nunca visitada fica ausente (peso implícito 0), não aparece com peso=0 explícito. EXPLORATÓRIO -- ver calcular_fator_zona_jogador e CONTEXTO_PROJETO.md.';

-- =============================================================================
-- 2. Fator de matchup: Σ_z (w_i,z · M_j,z^reg)
-- =============================================================================
create or replace function public.calcular_fator_zona_jogador(
  p_player_id bigint,
  p_opponent_team_id bigint,
  p_data_corte timestamptz,
  p_n_max_partidas integer default 20
)
returns numeric
language plpgsql
stable
as $$
declare
  v_fator numeric;
begin
  select sum(w.peso * coalesce(m.taxa_regularizada, 1.0))
  into v_fator
  from public.calcular_distribuicao_zona_jogador(p_player_id, p_data_corte, p_n_max_partidas) w
  left join public.calcular_concessao_zona_historica(p_opponent_team_id, p_data_corte, p_n_max_partidas) m
    on m.corredor = w.corredor and m.profundidade = w.profundidade;

  -- Jogador sem nenhum chute classificável na amostra (w_i,z vazio) ->
  -- SUM sobre zero linhas dá NULL -- fator neutro (1.0), mesmo raciocínio
  -- de ausência de dado usado em modular_por_goleiro (pricing_pipeline.py).
  return coalesce(v_fator, 1.0);
end;
$$;

comment on function public.calcular_fator_zona_jogador is
  'Fator de matchup zonal: Σ_z (w_i,z · M_j,z^reg) -- combina a distribuição de chutes do jogador (calcular_distribuicao_zona_jogador) com a concessão regularizada do time adversário na mesma zona (calcular_concessao_zona_historica). 1.0 = neutro (jogador sem amostra, ou concessão do adversário igual à média da liga em todas as zonas que ele visita). Multiplica lambda_chutes_jogo (já persistido) para compor lambda_chutes_jogo_zona_ajustado -- ver migration/coluna correspondente em player_match_estimates. EXPLORATÓRIO/CANDIDATO -- não promovido a produção sem backtest walk-forward mostrando vantagem real sobre lambda_chutes_jogo puro (achado de 15/09: zona não bate a média da liga como preditor).';

-- =============================================================================
-- 2b. Versão em LOTE de calcular_fator_zona_jogador -- 1 chamada RPC pra N
--     pares (player_id, opponent_team_id) em vez de 1 chamada por jogador
--     (uma rodada de cron processa centenas de titulares/dia -- N+1 round
--     trips seria o mesmo bug de desempenho já evitado em outros lugares
--     do projeto via paginação/lote). Reaproveita calcular_fator_zona_
--     jogador por par via LATERAL -- não duplica a lógica de shrinkage em
--     2 lugares (SQL e Python).
-- =============================================================================
create or replace function public.calcular_fator_zona_jogador_lote(
  p_player_ids bigint[],
  p_opponent_team_ids bigint[],
  p_data_corte timestamptz,
  p_n_max_partidas integer default 20
)
returns table (
  player_id bigint,
  opponent_team_id bigint,
  fator numeric
)
language plpgsql
stable
as $$
begin
  if array_length(p_player_ids, 1) is distinct from array_length(p_opponent_team_ids, 1) then
    raise exception 'p_player_ids e p_opponent_team_ids precisam ter o mesmo tamanho (% vs %)',
      array_length(p_player_ids, 1), array_length(p_opponent_team_ids, 1);
  end if;

  return query
  select
    pares.player_id,
    pares.opponent_team_id,
    public.calcular_fator_zona_jogador(pares.player_id, pares.opponent_team_id, p_data_corte, p_n_max_partidas) as fator
  from unnest(p_player_ids, p_opponent_team_ids) as pares(player_id, opponent_team_id);
end;
$$;

comment on function public.calcular_fator_zona_jogador_lote is
  'Versão em lote de calcular_fator_zona_jogador -- 1 chamada RPC pra N pares (player_id, opponent_team_id) em posições paralelas nos 2 arrays. Uso: select * from calcular_fator_zona_jogador_lote(ARRAY[1,2], ARRAY[10,20], now(), 20).';

-- =============================================================================
-- 3. Persistência -- colunas novas em player_match_estimates (candidato,
--    não sobrescreve lambda_chutes_jogo)
-- =============================================================================
alter table public.player_match_estimates
  add column if not exists fator_zona_aplicado numeric,
  add column if not exists lambda_chutes_jogo_zona_ajustado numeric;

comment on column public.player_match_estimates.fator_zona_aplicado is
  'Σ_z(w_i,z·M_j,z^reg) calculado por calcular_fator_zona_jogador no momento da geração -- guardado separado de lambda_chutes_jogo_zona_ajustado pra auditoria (permite saber se um valor esquisito veio do lambda_base ou do fator). 1.0 = neutro/sem ajuste.';
comment on column public.player_match_estimates.lambda_chutes_jogo_zona_ajustado is
  'lambda_chutes_jogo * fator_zona_aplicado -- candidato alternativo ao lambda_chutes_jogo, mesmo tratamento que lambda_gols_jogo_direto (coexiste, nunca sobrescreve, só promovido a uso real depois de backtest walk-forward mostrar vantagem). EXPLORATÓRIO -- achado de 15/09 não encontrou sinal preditivo incremental de concessão por zona.';
