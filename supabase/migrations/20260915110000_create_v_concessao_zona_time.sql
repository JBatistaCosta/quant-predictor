-- =============================================================================
-- Migration: concessão de chutes por zona espacial (18 zonas) -- INFRA/EXPLORAÇÃO
-- =============================================================================
-- Item 2/3A da spec "Pricing Pipeline v2" (matriz de 18 zonas: 5 corredores x
-- 3 profundidades, concessão de chute por zona do time adversário).
--
-- **NÃO alimenta nenhuma predição de produção.** Validado em 15/09 (split
-- temporal 70/30 por time, 6 ligas Tier 1, 2023-2025, 18 zonas e versão de 5
-- corredores): prever a distribuição de chutes por zona usando a taxa
-- histórica do PRÓPRIO time NÃO bate o baseline ingênuo "média da liga por
-- zona" (correlação empatada ~0,987-0,996, RMSE do modelo por time pior nos
-- dois casos). Onde o time concede chute não carrega sinal incremental sobre
-- quanto ele concede -- mesmo padrão já visto com zona de ataque pra
-- escanteios (ver CONTEXTO_PROJETO.md). Decisão do usuário (15/09): construir
-- mesmo assim como dado explorável/consultável, nunca como feature de modelo
-- -- quem usar esta view pra qualquer decisão preditiva deve reler essa
-- ressalva primeiro.
--
-- COORDENADAS: `match_shots_fotmob.x`/`.y` NÃO são normalizadas em 0-100 (a
-- spec original assumia isso) -- são metros de campo real, confirmado por
-- query em produção: x ∈ [~0, 105], y ∈ [~0, 68] (dimensão FIFA padrão). `x`
-- já vem orientado pro gol ATACADO (média global x≈90, chute médio perto do
-- gol -- só faz sentido se já normalizado por chute, não por mando de campo),
-- então não precisa inverter por time/tempo. Os limiares abaixo são os
-- percentuais da spec (0-100%) escalados pra metros reais (×1.05 em x,
-- ×0.68 em y) -- usar os números literais da spec (ex. "94.2 a 100.0") sem
-- essa conversão erra a zona de todo chute.
-- =============================================================================

-- =============================================================================
-- View 1: primitivo auditável -- 1 linha por chute no terço final, com zona
-- =============================================================================
-- Só chutes com x >= 68.25 (65% da spec, escalado) entram numa zona -- fora
-- do terço final ofensivo, a spec não define zona (não é chute "perto do
-- gol" o bastante pra fazer sentido classificar por concessão espacial).
create or replace view public.v_chutes_zoneados as
select
  s.id as shot_id,
  s.match_id,
  s.team_id as time_chutou,
  case when s.team_id = m.home_team_id then m.away_team_id else m.home_team_id end as time_concedeu,
  s.xg,
  s.xgot,
  s.is_on_target,
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
join public.matches m on m.id = s.match_id
where s.x >= 68.25 and s.x <= 105 and s.y >= 0 and s.y <= 68;

comment on view public.v_chutes_zoneados is
  'Um chute por linha, classificado nas 18 zonas espaciais (5 corredores x 3 profundidades, ver comentário da migration 20260915110000 pra escala real em metros). EXPLORATÓRIO -- validação de 15/09 não encontrou sinal preditivo incremental sobre a média da liga, não usar como feature de modelo sem revalidar. `time_concedeu` é quem sofreu o chute (adversário de `time_chutou`), já resolvendo mando de campo.';

-- =============================================================================
-- View 2: agregado por (time, zona) -- concessão histórica, toda a base
-- =============================================================================
create or replace view public.v_concessao_zona_time as
select
  time_concedeu as team_id,
  corredor,
  profundidade,
  count(*) as chutes_concedidos,
  round(100.0 * count(*) / sum(count(*)) over (partition by time_concedeu), 2) as pct_dos_chutes_do_time,
  round(avg(xg)::numeric, 4) as xg_medio_por_chute,
  count(distinct match_id) as partidas_com_chute_nessa_zona
from public.v_chutes_zoneados
group by time_concedeu, corredor, profundidade;

comment on view public.v_concessao_zona_time is
  'Concessão de chutes por (time, zona), agregado em TODA a história disponível de match_shots_fotmob -- sem corte de data, sem ponderação por Elo/força do adversário. EXPLORATÓRIO -- ver v_chutes_zoneados e CONTEXTO_PROJETO.md (achado de 15/09: sem sinal preditivo incremental sobre a média da liga). Não alimenta pricing_pipeline.py nem nenhum treino de modelo.';
