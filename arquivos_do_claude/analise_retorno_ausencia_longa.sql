-- Proxy "retorno de ausência longa" (02/10/2026). SOMENTE LEITURA (execute no SQL Editor ou via MCP).
-- Ver a entrada de 02/10 em CONTEXTO_PROJETO.md para método, resultados e ressalvas.
--
-- Evento = jogador REGULAR (>= 15 jogos com minutos nos 400 dias até a última aparição) que volta a jogar
-- pelo MESMO time depois de >= 120 dias sem aparecer, com o time tendo disputado >= 8 jogos (cobertos pelo
-- banco) nesse intervalo. Linha de base = média dos 10 jogos anteriores do próprio jogador (minutos, nota).
-- Cobertura: só as competições com match_player_stats_fotmob (PL, La Liga, Serie A, Bundesliga, Ligue 1,
-- Brasileirão A, Libertadores, Copa América). Ausência = "não jogou" (lesão, suspensão, banco ou transferência
-- temporária): NÃO distingue LCA de outras causas.

-- CTE-base (repetida nas 3 consultas abaixo)
WITH ap0 AS (
  SELECT DISTINCT ON (s.fotmob_player_id, s.match_id)
    s.fotmob_player_id AS pid, s.team_id, s.match_id,
    (m.match_date AT TIME ZONE 'UTC')::date AS d, s.minutes_played AS mn, s.rating
  FROM match_player_stats_fotmob s JOIN matches m ON m.id = s.match_id
  WHERE m.status = 'finished' AND s.minutes_played > 0 AND s.fotmob_player_id IS NOT NULL
  ORDER BY s.fotmob_player_id, s.match_id
),
tm AS (
  SELECT team_id, match_id, d, ROW_NUMBER() OVER (PARTITION BY team_id ORDER BY d, match_id) AS tn
  FROM (SELECT DISTINCT team_id, match_id, d FROM ap0) x
),
ap AS (
  SELECT a.*, t.tn, ROW_NUMBER() OVER w AS rn,
    LAG(a.d) OVER w AS prev_d, LAG(t.tn) OVER w AS prev_tn,
    COUNT(*) OVER (PARTITION BY a.pid, a.team_id ORDER BY a.d RANGE BETWEEN INTERVAL '400 days' PRECEDING AND CURRENT ROW) AS c400,
    AVG(a.mn) OVER (PARTITION BY a.pid, a.team_id ORDER BY a.d, a.match_id ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING) AS bmin,
    AVG(a.rating) OVER (PARTITION BY a.pid, a.team_id ORDER BY a.d, a.match_id ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING) AS brat
  FROM ap0 a JOIN tm t ON t.team_id = a.team_id AND t.match_id = a.match_id
  WINDOW w AS (PARTITION BY a.pid, a.team_id ORDER BY a.d, a.match_id)
),
ap2 AS (
  SELECT ap.*, LAG(c400) OVER (PARTITION BY pid, team_id ORDER BY d, match_id) AS prev_c400,
         (d - prev_d) AS gap, (tn - prev_tn - 1) AS missed
  FROM ap
),
ev AS (
  SELECT pid, team_id, rn AS rn_e, d, gap, missed, bmin, brat,
         CASE WHEN gap < 180 THEN '1) 120-179 d' WHEN gap < 270 THEN '2) 180-269 d' ELSE '3) 270+ d' END AS faixa
  FROM ap2 WHERE gap >= 120 AND missed >= 8 AND prev_c400 >= 15 AND bmin IS NOT NULL
)
-- (1) Minutos e nota nos 6 primeiros jogos após o retorno, contra a linha de base do próprio jogador,
--     e o controle (aparições normais de regulares: <= 1 jogo perdido):
SELECT e.faixa, a.rn - e.rn_e + 1 AS k, COUNT(*) AS n,
       ROUND(AVG(a.mn - e.bmin)::numeric, 2) AS d_minutos,
       ROUND((STDDEV_SAMP(a.mn - e.bmin) / SQRT(COUNT(*)))::numeric, 2) AS ep_minutos,
       ROUND(AVG(a.rating - e.brat)::numeric, 3) AS d_nota,
       ROUND((STDDEV_SAMP(a.rating - e.brat) / SQRT(NULLIF(COUNT(a.rating - e.brat), 0)))::numeric, 3) AS ep_nota
FROM ev e JOIN ap2 a ON a.pid = e.pid AND a.team_id = e.team_id AND a.rn BETWEEN e.rn_e AND e.rn_e + 5
GROUP BY 1, 2 ORDER BY 1, 2;

-- (2) Nova ausência: >= 4 jogos do time perdidos dentro das 5 aparições seguintes ao retorno
--     (troque a CTE final para: MAX(CASE WHEN missed >= 4 THEN 1 ELSE 0 END) OVER (PARTITION BY pid, team_id
--      ORDER BY d, match_id ROWS BETWEEN 1 FOLLOWING AND 5 FOLLOWING)) e compare retornos (gap >= 120 AND missed >= 8)
--     com o grupo sem ausência longa (missed <= 1 AND prev_c400 >= 15 AND bmin IS NOT NULL).

-- (3) Nível do time: contagem de retornantes (3 primeiros jogos após o retorno) casa - fora contra o resíduo
--     (resultado - esperado) do Elo por xG (a=1,0149; h=57,23; t=0,6055; team_elo_xg_history.rating_antes),
--     partidas >= 2021-01-01 das competições cobertas. Regressão: REGR_SLOPE(res, d).
