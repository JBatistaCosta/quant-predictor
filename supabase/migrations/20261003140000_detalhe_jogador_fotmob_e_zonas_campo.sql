-- Fase 1 da granularidade de jogador (base para simulação de jogo por cadeia de Markov):
--   (A) as ~45 estatísticas por jogador e jogo que o FotMob já entregou em `match_player_stats_fotmob.stats_raw`
--       e que NÃO viraram colunas, extraídas para `match_player_stats_detalhe_fotmob` (nenhuma chamada de API);
--   (B) a definição ÚNICA das zonas do campo: 12 zonas gerais (`zona_campo12`); a decisão do chute reaproveita a `zona_chute`
--       (14 zonas polares) que já existe; view `v_chutes_zona` com as duas;
--   (C) passes/precisão/defesa médios por LINHA (defesa, meio, ataque) em `v_detalhe_jogador_linha`: não temos eventos
--       de passe com coordenadas, então a granularidade possível é por posição do jogador, não por zona do campo.
--
-- Mesma disciplina da formação/estado do jogo: a regra de extração mora SÓ na função SQL
-- `derivar_detalhe_jogador_fotmob(p_match_ids)`; o ingestor (api/model-maintenance.js) chama por RPC depois do
-- upsert das estatísticas, e `?tarefa=derivar-detalhe-jogador` é a rede de segurança. O backfill histórico NÃO está
-- nesta migration (26 mil partidas estouram o tempo): ver arquivos_do_claude/backfill_detalhe_jogador.sql.
--
-- FORMATO DO stats_raw (conferido em produção em 03/10/2026): array de grupos {key, title, stats:{Rótulo:{key, stat:
-- {type, value[, total]}}}}; ~2% das linhas antigas vêm embrulhadas em {id, name, stats:[...]}; ~27% são `[]` (reservas
-- que não entraram, 0 delas com >= 20 min). A mesma estatística (`key`) aparece em grupos diferentes (ex.: `touches`
-- em `attack` para jogador de linha e em `top_stats` para goleiro), por isso a extração é pela `key` interna, sem
-- olhar o grupo. Estatística ausente fica NULL (FotMob omite quando não houve a ação: para chutes/dribles/cruzamentos
-- costuma significar zero, mas não é garantido -- quem consome decide). Coordenadas de chute: x 0..105 (105 = gol
-- atacado), y 0..68; a orientação esquerda/direita do y NÃO foi verificada, por isso os nomes dizem "lado_y_baixo/alto".

-- ============================================================================ (B) zonas do campo
-- 12 zonas gerais = 4 faixas de profundidade x 3 corredores, sempre da perspectiva de quem ataca.
--   faixa: 0 defesa (x < 35) | 1 meio (35 <= x < 70) | 2 ataque fora da área (x >= 70, fora da grande área)
--          | 3 grande área adversária (x >= 88,5 e 13,85 <= y <= 54,15)
--   corredor: 1 lado_y_baixo (y < 22,67) | 2 centro | 3 lado_y_alto (y > 45,33)
--   zona = faixa * 3 + corredor  (1..12)
CREATE OR REPLACE FUNCTION public.zona_campo12(p_x double precision, p_y double precision)
RETURNS smallint
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
  SELECT CASE WHEN p_x IS NULL OR p_y IS NULL THEN NULL ELSE (
    (CASE WHEN p_x < 35 THEN 0
          WHEN p_x < 70 THEN 1
          WHEN p_x >= 88.5 AND p_y >= 13.85 AND p_y <= 54.15 THEN 3
          ELSE 2 END) * 3
    + (CASE WHEN p_y < 68.0 / 3 THEN 1 WHEN p_y <= 2 * 68.0 / 3 THEN 2 ELSE 3 END)
  )::smallint END
$$;

CREATE TABLE IF NOT EXISTS public.zonas_campo12 (
  zona      smallint PRIMARY KEY CHECK (zona BETWEEN 1 AND 12),
  faixa     smallint NOT NULL,
  faixa_nome text    NOT NULL,
  corredor  smallint NOT NULL,
  corredor_nome text NOT NULL
);
INSERT INTO public.zonas_campo12 (zona, faixa, faixa_nome, corredor, corredor_nome)
SELECT f.faixa * 3 + c.corredor, f.faixa, f.nome, c.corredor, c.nome
  FROM (VALUES (0, 'defesa'), (1, 'meio'), (2, 'ataque_fora_da_area'), (3, 'grande_area_adversaria')) AS f(faixa, nome)
  CROSS JOIN (VALUES (1, 'lado_y_baixo'), (2, 'centro'), (3, 'lado_y_alto')) AS c(corredor, nome)
ON CONFLICT (zona) DO NOTHING;
ALTER TABLE public.zonas_campo12 ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "zonas_campo12 leitura publica" ON public.zonas_campo12;
CREATE POLICY "zonas_campo12 leitura publica" ON public.zonas_campo12 FOR SELECT USING (true);

-- Decisão do chute: usa a `zona_chute(numeric, numeric) -> smallint` JÁ EXISTENTE (14 zonas polares, migration
-- 20261001120000_create_ficha_chutes_jogador.sql). Não se define outra aqui: duas regras de zona concorrentes (ou uma
-- sobrecarga com tipos parecidos) é exatamente o que a disciplina de "regra num só lugar" evita.
CREATE OR REPLACE VIEW public.v_chutes_zona WITH (security_invoker = true) AS
SELECT s.*, public.zona_campo12(s.x::double precision, s.y::double precision) AS zona12, public.zona_chute(s.x::numeric, s.y::numeric) AS zona_chute
  FROM public.match_shots_fotmob s;

-- ============================================================================ (A) detalhe por jogador e jogo
CREATE TABLE IF NOT EXISTS public.match_player_stats_detalhe_fotmob (
  stat_id           bigint PRIMARY KEY REFERENCES public.match_player_stats_fotmob(id) ON DELETE CASCADE,
  match_id          bigint NOT NULL,
  team_id           bigint,
  fotmob_player_id  text   NOT NULL,
  player_id         bigint,
  -- ataque
  perdas_posse smallint, passes_total smallint, passes_terco_final smallint,
  bolas_longas_certas smallint, bolas_longas_total smallint,
  dribles_certos smallint, dribles_total smallint,
  cruzamentos_certos smallint, cruzamentos_total smallint,
  chutes_no_alvo smallint, chutes_fora smallint, chutes_bloqueados smallint, chutes_trave smallint,
  xg_sem_penalti real, grandes_chances_criadas smallint, grandes_chances_perdidas smallint,
  passes_quebra_linha smallint, escanteios_cobrados smallint, impedimentos smallint,
  -- defesa
  acoes_defensivas smallint, recuperacoes smallint, cortes smallint, cortes_cabeca smallint, bloqueios smallint,
  driblado smallint, desarme_ultimo_homem smallint, corte_linha_gol smallint,
  -- duelos
  duelos_ganhos smallint, duelos_perdidos smallint, duelos_aereos_total smallint, duelos_chao_total smallint,
  faltas_cometidas smallint, faltas_sofridas smallint,
  -- goleiro
  defesas smallint, defesas_dentro_area smallint, defesas_mergulho smallint, saidas_bola_alta smallint, socos smallint,
  saidas_libero smallint, reposicoes_mao smallint, gols_sofridos smallint, xgot_enfrentado real, gols_evitados real,
  penaltis_defendidos smallint,
  -- eventos raros
  erros_levaram_gol smallint, penaltis_cometidos smallint, penaltis_sofridos smallint, penaltis_perdidos smallint,
  gols_contra smallint, pontos_fantasy real,
  -- físico (só ~2% dos jogos; distâncias em metros, velocidade em km/h)
  dist_total_m integer, dist_sprint_m integer, dist_corrida_m integer, dist_trote_m integer, dist_caminhada_m integer,
  vel_max_kmh real, n_sprints smallint,
  derivado_em timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS match_player_stats_detalhe_match_idx ON public.match_player_stats_detalhe_fotmob (match_id);
CREATE INDEX IF NOT EXISTS match_player_stats_detalhe_player_idx ON public.match_player_stats_detalhe_fotmob (player_id);
ALTER TABLE public.match_player_stats_detalhe_fotmob ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "detalhe_jogador leitura publica" ON public.match_player_stats_detalhe_fotmob;
CREATE POLICY "detalhe_jogador leitura publica" ON public.match_player_stats_detalhe_fotmob FOR SELECT USING (true);

-- Achata o stats_raw (os dois formatos) em {key -> stat}. Chave nula (ex.: o "Shotmap" booleano) é ignorada; se a mesma
-- key vier em dois grupos, vale a última (os valores coincidem). Função pura, usada pela derivação e pelos testes.
CREATE OR REPLACE FUNCTION public.detalhe_jogador_kv(p_stats_raw jsonb)
RETURNS jsonb
LANGUAGE plpgsql
IMMUTABLE
PARALLEL SAFE
AS $$
DECLARE
  grupos jsonb;
BEGIN
  grupos := CASE WHEN jsonb_typeof(p_stats_raw) = 'object' THEN p_stats_raw -> 'stats' ELSE p_stats_raw END;
  -- ramo explícito: jsonb_array_elements dá erro em não-array e o SQL não garante curto-circuito no WHERE
  IF grupos IS NULL OR jsonb_typeof(grupos) <> 'array' THEN
    RETURN '{}'::jsonb;
  END IF;
  RETURN COALESCE((
    SELECT jsonb_object_agg(s.value ->> 'key', s.value -> 'stat')
      FROM jsonb_array_elements(grupos) g
      CROSS JOIN LATERAL jsonb_each(CASE WHEN jsonb_typeof(g -> 'stats') = 'object' THEN g -> 'stats' ELSE '{}'::jsonb END) s
     WHERE (s.value ->> 'key') IS NOT NULL
  ), '{}'::jsonb);
END
$$;

-- Deriva o detalhe das partidas dadas (ou de todas, com NULL -- NÃO use NULL em produção: estoura o tempo).
-- Devolve o número de linhas gravadas. Só grava jogadores que têm ao menos uma estatística no JSON.
CREATE OR REPLACE FUNCTION public.derivar_detalhe_jogador_fotmob(p_match_ids bigint[])
RETURNS integer
LANGUAGE plpgsql
SET search_path = public
AS $$
DECLARE
  n integer;
BEGIN
  WITH base AS (
    SELECT m.id AS stat_id, m.match_id, m.team_id, m.fotmob_player_id, m.player_id,
           public.detalhe_jogador_kv(m.stats_raw) AS kv
      FROM public.match_player_stats_fotmob m
     WHERE m.stats_raw IS NOT NULL
       AND (p_match_ids IS NULL OR m.match_id = ANY (p_match_ids))
  ),
  linhas AS (
    SELECT b.stat_id, b.match_id, b.team_id, b.fotmob_player_id, b.player_id,
      (b.kv #>> '{dispossessed,value}')::numeric AS perdas_posse,
      (b.kv #>> '{accurate_passes,total}')::numeric AS passes_total,
      (b.kv #>> '{passes_into_final_third,value}')::numeric AS passes_terco_final,
      (b.kv #>> '{long_balls_accurate,value}')::numeric AS bolas_longas_certas,
      (b.kv #>> '{long_balls_accurate,total}')::numeric AS bolas_longas_total,
      (b.kv #>> '{dribbles_succeeded,value}')::numeric AS dribles_certos,
      (b.kv #>> '{dribbles_succeeded,total}')::numeric AS dribles_total,
      (b.kv #>> '{accurate_crosses,value}')::numeric AS cruzamentos_certos,
      (b.kv #>> '{accurate_crosses,total}')::numeric AS cruzamentos_total,
      (b.kv #>> '{ShotsOnTarget,value}')::numeric AS chutes_no_alvo,
      (b.kv #>> '{ShotsOffTarget,value}')::numeric AS chutes_fora,
      (b.kv #>> '{blocked_shots,value}')::numeric AS chutes_bloqueados,
      (b.kv #>> '{shots_woodwork,value}')::numeric AS chutes_trave,
      (b.kv #>> '{expected_goals_non_penalty,value}')::numeric AS xg_sem_penalti,
      (b.kv #>> '{big_chance_created_team_title,value}')::numeric AS grandes_chances_criadas,
      (b.kv #>> '{big_chance_missed_title,value}')::numeric AS grandes_chances_perdidas,
      (b.kv #>> '{line_breaking_passes,value}')::numeric AS passes_quebra_linha,
      (b.kv #>> '{corners,value}')::numeric AS escanteios_cobrados,
      (b.kv #>> '{Offsides,value}')::numeric AS impedimentos,
      (b.kv #>> '{defensive_actions,value}')::numeric AS acoes_defensivas,
      (b.kv #>> '{recoveries,value}')::numeric AS recuperacoes,
      (b.kv #>> '{clearances,value}')::numeric AS cortes,
      (b.kv #>> '{headed_clearance,value}')::numeric AS cortes_cabeca,
      (b.kv #>> '{shot_blocks,value}')::numeric AS bloqueios,
      (b.kv #>> '{dribbled_past,value}')::numeric AS driblado,
      (b.kv #>> '{last_man_tackle,value}')::numeric AS desarme_ultimo_homem,
      (b.kv #>> '{clearance_off_the_line,value}')::numeric AS corte_linha_gol,
      (b.kv #>> '{duel_won,value}')::numeric AS duelos_ganhos,
      (b.kv #>> '{duel_lost,value}')::numeric AS duelos_perdidos,
      (b.kv #>> '{aerials_won,total}')::numeric AS duelos_aereos_total,
      (b.kv #>> '{ground_duels_won,total}')::numeric AS duelos_chao_total,
      (b.kv #>> '{fouls,value}')::numeric AS faltas_cometidas,
      (b.kv #>> '{was_fouled,value}')::numeric AS faltas_sofridas,
      (b.kv #>> '{saves,value}')::numeric AS defesas,
      (b.kv #>> '{saves_inside_box,value}')::numeric AS defesas_dentro_area,
      (b.kv #>> '{keeper_diving_save,value}')::numeric AS defesas_mergulho,
      (b.kv #>> '{keeper_high_claim,value}')::numeric AS saidas_bola_alta,
      (b.kv #>> '{punches,value}')::numeric AS socos,
      (b.kv #>> '{keeper_sweeper,value}')::numeric AS saidas_libero,
      (b.kv #>> '{player_throws,value}')::numeric AS reposicoes_mao,
      (b.kv #>> '{goals_conceded,value}')::numeric AS gols_sofridos,
      (b.kv #>> '{expected_goals_on_target_faced,value}')::numeric AS xgot_enfrentado,
      (b.kv #>> '{goals_prevented,value}')::numeric AS gols_evitados,
      (b.kv #>> '{saved_penalties,value}')::numeric AS penaltis_defendidos,
      (b.kv #>> '{errors_led_to_goal,value}')::numeric AS erros_levaram_gol,
      (b.kv #>> '{conceded_penalties,value}')::numeric AS penaltis_cometidos,
      (b.kv #>> '{penalties_won,value}')::numeric AS penaltis_sofridos,
      (b.kv #>> '{missed_penalty,value}')::numeric AS penaltis_perdidos,
      (b.kv #>> '{owngoal,value}')::numeric AS gols_contra,
      (b.kv #>> '{fantasy_points,value}')::numeric AS pontos_fantasy,
      (b.kv #>> '{physical_metrics_distance_covered,value}')::numeric AS dist_total_m,
      (b.kv #>> '{physical_metrics_sprinting,value}')::numeric AS dist_sprint_m,
      (b.kv #>> '{physical_metrics_running,value}')::numeric AS dist_corrida_m,
      (b.kv #>> '{physical_metrics_jogging,value}')::numeric AS dist_trote_m,
      (b.kv #>> '{physical_metrics_walking,value}')::numeric AS dist_caminhada_m,
      (b.kv #>> '{physical_metrics_topspeed,value}')::numeric AS vel_max_kmh,
      (b.kv #>> '{physical_metrics_number_of_sprints,value}')::numeric AS n_sprints
      FROM base b
     WHERE b.kv <> '{}'::jsonb
  )
  INSERT INTO public.match_player_stats_detalhe_fotmob AS d (
    stat_id, match_id, team_id, fotmob_player_id, player_id,
    perdas_posse, passes_total, passes_terco_final, bolas_longas_certas, bolas_longas_total, dribles_certos, dribles_total,
    cruzamentos_certos, cruzamentos_total, chutes_no_alvo, chutes_fora, chutes_bloqueados, chutes_trave, xg_sem_penalti,
    grandes_chances_criadas, grandes_chances_perdidas, passes_quebra_linha, escanteios_cobrados, impedimentos,
    acoes_defensivas, recuperacoes, cortes, cortes_cabeca, bloqueios, driblado, desarme_ultimo_homem, corte_linha_gol,
    duelos_ganhos, duelos_perdidos, duelos_aereos_total, duelos_chao_total, faltas_cometidas, faltas_sofridas,
    defesas, defesas_dentro_area, defesas_mergulho, saidas_bola_alta, socos, saidas_libero, reposicoes_mao, gols_sofridos,
    xgot_enfrentado, gols_evitados, penaltis_defendidos, erros_levaram_gol, penaltis_cometidos, penaltis_sofridos,
    penaltis_perdidos, gols_contra, pontos_fantasy, dist_total_m, dist_sprint_m, dist_corrida_m, dist_trote_m,
    dist_caminhada_m, vel_max_kmh, n_sprints, derivado_em)
  SELECT l.stat_id, l.match_id, l.team_id, l.fotmob_player_id, l.player_id,
    l.perdas_posse, l.passes_total, l.passes_terco_final, l.bolas_longas_certas, l.bolas_longas_total, l.dribles_certos, l.dribles_total,
    l.cruzamentos_certos, l.cruzamentos_total, l.chutes_no_alvo, l.chutes_fora, l.chutes_bloqueados, l.chutes_trave, l.xg_sem_penalti,
    l.grandes_chances_criadas, l.grandes_chances_perdidas, l.passes_quebra_linha, l.escanteios_cobrados, l.impedimentos,
    l.acoes_defensivas, l.recuperacoes, l.cortes, l.cortes_cabeca, l.bloqueios, l.driblado, l.desarme_ultimo_homem, l.corte_linha_gol,
    l.duelos_ganhos, l.duelos_perdidos, l.duelos_aereos_total, l.duelos_chao_total, l.faltas_cometidas, l.faltas_sofridas,
    l.defesas, l.defesas_dentro_area, l.defesas_mergulho, l.saidas_bola_alta, l.socos, l.saidas_libero, l.reposicoes_mao, l.gols_sofridos,
    l.xgot_enfrentado, l.gols_evitados, l.penaltis_defendidos, l.erros_levaram_gol, l.penaltis_cometidos, l.penaltis_sofridos,
    l.penaltis_perdidos, l.gols_contra, l.pontos_fantasy, l.dist_total_m, l.dist_sprint_m, l.dist_corrida_m, l.dist_trote_m,
    l.dist_caminhada_m, l.vel_max_kmh, l.n_sprints, now()
    FROM linhas l
  ON CONFLICT (stat_id) DO UPDATE SET
    match_id = EXCLUDED.match_id, team_id = EXCLUDED.team_id, fotmob_player_id = EXCLUDED.fotmob_player_id, player_id = EXCLUDED.player_id,
    perdas_posse = EXCLUDED.perdas_posse, passes_total = EXCLUDED.passes_total, passes_terco_final = EXCLUDED.passes_terco_final,
    bolas_longas_certas = EXCLUDED.bolas_longas_certas, bolas_longas_total = EXCLUDED.bolas_longas_total,
    dribles_certos = EXCLUDED.dribles_certos, dribles_total = EXCLUDED.dribles_total,
    cruzamentos_certos = EXCLUDED.cruzamentos_certos, cruzamentos_total = EXCLUDED.cruzamentos_total,
    chutes_no_alvo = EXCLUDED.chutes_no_alvo, chutes_fora = EXCLUDED.chutes_fora, chutes_bloqueados = EXCLUDED.chutes_bloqueados,
    chutes_trave = EXCLUDED.chutes_trave, xg_sem_penalti = EXCLUDED.xg_sem_penalti,
    grandes_chances_criadas = EXCLUDED.grandes_chances_criadas, grandes_chances_perdidas = EXCLUDED.grandes_chances_perdidas,
    passes_quebra_linha = EXCLUDED.passes_quebra_linha, escanteios_cobrados = EXCLUDED.escanteios_cobrados, impedimentos = EXCLUDED.impedimentos,
    acoes_defensivas = EXCLUDED.acoes_defensivas, recuperacoes = EXCLUDED.recuperacoes, cortes = EXCLUDED.cortes,
    cortes_cabeca = EXCLUDED.cortes_cabeca, bloqueios = EXCLUDED.bloqueios, driblado = EXCLUDED.driblado,
    desarme_ultimo_homem = EXCLUDED.desarme_ultimo_homem, corte_linha_gol = EXCLUDED.corte_linha_gol,
    duelos_ganhos = EXCLUDED.duelos_ganhos, duelos_perdidos = EXCLUDED.duelos_perdidos,
    duelos_aereos_total = EXCLUDED.duelos_aereos_total, duelos_chao_total = EXCLUDED.duelos_chao_total,
    faltas_cometidas = EXCLUDED.faltas_cometidas, faltas_sofridas = EXCLUDED.faltas_sofridas,
    defesas = EXCLUDED.defesas, defesas_dentro_area = EXCLUDED.defesas_dentro_area, defesas_mergulho = EXCLUDED.defesas_mergulho,
    saidas_bola_alta = EXCLUDED.saidas_bola_alta, socos = EXCLUDED.socos, saidas_libero = EXCLUDED.saidas_libero,
    reposicoes_mao = EXCLUDED.reposicoes_mao, gols_sofridos = EXCLUDED.gols_sofridos, xgot_enfrentado = EXCLUDED.xgot_enfrentado,
    gols_evitados = EXCLUDED.gols_evitados, penaltis_defendidos = EXCLUDED.penaltis_defendidos,
    erros_levaram_gol = EXCLUDED.erros_levaram_gol, penaltis_cometidos = EXCLUDED.penaltis_cometidos,
    penaltis_sofridos = EXCLUDED.penaltis_sofridos, penaltis_perdidos = EXCLUDED.penaltis_perdidos, gols_contra = EXCLUDED.gols_contra,
    pontos_fantasy = EXCLUDED.pontos_fantasy, dist_total_m = EXCLUDED.dist_total_m, dist_sprint_m = EXCLUDED.dist_sprint_m,
    dist_corrida_m = EXCLUDED.dist_corrida_m, dist_trote_m = EXCLUDED.dist_trote_m, dist_caminhada_m = EXCLUDED.dist_caminhada_m,
    vel_max_kmh = EXCLUDED.vel_max_kmh, n_sprints = EXCLUDED.n_sprints, derivado_em = now();
  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN n;
END
$$;

GRANT EXECUTE ON FUNCTION public.zona_campo12(double precision, double precision) TO anon, authenticated;
-- A derivação grava em tabela de pipeline: só service_role (o ingestor e a tarefa de manutenção usam essa chave).
REVOKE ALL ON FUNCTION public.derivar_detalhe_jogador_fotmob(bigint[]) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.derivar_detalhe_jogador_fotmob(bigint[]) TO service_role;

-- Backfill histórico RETOMÁVEL (~26 mil partidas não cabem numa chamada só): processa lotes de partidas em ordem de
-- match_id durante `p_segundos` e devolve onde parou. Rodar repetidamente no SQL Editor, passando o `ultimo_match_id`
-- devolvido, até `terminou = true`:
--     select * from public.backfill_detalhe_jogador_fotmob(0);
--     select * from public.backfill_detalhe_jogador_fotmob(<ultimo_match_id da chamada anterior>);
-- Idempotente (refazer um trecho só regrava as mesmas linhas). Partidas cujo stats_raw é todo vazio são puladas.
CREATE OR REPLACE FUNCTION public.backfill_detalhe_jogador_fotmob(
  p_apos_match_id bigint DEFAULT 0, p_segundos integer DEFAULT 40, p_lote integer DEFAULT 40)
RETURNS TABLE (ultimo_match_id bigint, partidas integer, linhas integer, terminou boolean)
LANGUAGE plpgsql
SET search_path = public
AS $$
DECLARE
  v_ult bigint := COALESCE(p_apos_match_id, 0);
  v_ids bigint[];
  v_linhas integer := 0;
  v_partidas integer := 0;
  v_ini timestamptz := clock_timestamp();
  v_fim boolean := false;
BEGIN
  LOOP
    SELECT array_agg(t.match_id ORDER BY t.match_id) INTO v_ids
      FROM (SELECT DISTINCT s.match_id
              FROM public.match_player_stats_fotmob s
             WHERE s.match_id > v_ult
               AND s.stats_raw IS NOT NULL
               AND (jsonb_typeof(s.stats_raw) = 'object' OR (jsonb_typeof(s.stats_raw) = 'array' AND jsonb_array_length(s.stats_raw) > 0))
             ORDER BY s.match_id
             LIMIT GREATEST(1, p_lote)) t;
    IF v_ids IS NULL THEN
      v_fim := true;
      EXIT;
    END IF;
    v_linhas := v_linhas + public.derivar_detalhe_jogador_fotmob(v_ids);
    v_partidas := v_partidas + cardinality(v_ids);
    v_ult := v_ids[cardinality(v_ids)];
    EXIT WHEN clock_timestamp() - v_ini > make_interval(secs => GREATEST(1, p_segundos));
  END LOOP;
  RETURN QUERY SELECT v_ult, v_partidas, v_linhas, v_fim;
END
$$;
REVOKE ALL ON FUNCTION public.backfill_detalhe_jogador_fotmob(bigint, integer, integer) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.backfill_detalhe_jogador_fotmob(bigint, integer, integer) TO service_role;

-- ============================================================================ (C) por linha (defesa/meio/ataque)
-- Sem eventos de passe com coordenadas (FotMob só entrega totais por jogador), "passes e perdas por zona" não existe.
-- O que existe é a média por LINHA do jogador (players.usual_position_id: 0 goleiro, 1 defesa, 2 meio, 3 ataque),
-- sempre por 90 min (soma de ações / soma de minutos), nunca média de médias.
CREATE OR REPLACE VIEW public.v_detalhe_jogador_linha WITH (security_invoker = true) AS
SELECT CASE p.usual_position_id WHEN 0 THEN 'goleiro' WHEN 1 THEN 'defesa' WHEN 2 THEN 'meio' WHEN 3 THEN 'ataque' END AS linha,
       count(*) AS jogos, sum(s.minutes_played) AS minutos,
       90.0 * sum(d.passes_total) / nullif(sum(s.minutes_played), 0) AS passes_90,
       sum(s.accurate_passes)::numeric / nullif(sum(d.passes_total), 0) AS precisao_passe,
       90.0 * sum(d.passes_terco_final) / nullif(sum(s.minutes_played), 0) AS passes_terco_final_90,
       sum(d.bolas_longas_certas)::numeric / nullif(sum(d.bolas_longas_total), 0) AS precisao_bola_longa,
       90.0 * sum(d.perdas_posse) / nullif(sum(s.minutes_played), 0) AS perdas_posse_90,
       90.0 * sum(d.recuperacoes) / nullif(sum(s.minutes_played), 0) AS recuperacoes_90,
       sum(d.dribles_certos)::numeric / nullif(sum(d.dribles_total), 0) AS precisao_drible,
       sum(d.duelos_ganhos)::numeric / nullif(sum(d.duelos_ganhos) + sum(d.duelos_perdidos), 0) AS taxa_duelos
  FROM public.match_player_stats_detalhe_fotmob d
  JOIN public.match_player_stats_fotmob s ON s.id = d.stat_id
  JOIN public.players p ON p.id = d.player_id
 WHERE s.minutes_played >= 20 AND p.usual_position_id BETWEEN 0 AND 3
 GROUP BY 1;
