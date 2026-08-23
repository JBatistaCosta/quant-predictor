-- Adiciona xG combinado (Understat + FotMob, com fallback) à
-- vw_cobertura_estatisticas. Antes, a coluna "xG" só refletia
-- match_stats.xg (Understat, só 5 ligas europeias), ignorando
-- match_stats_fotmob.xg (cobertura bem mais ampla). Ver CONTEXTO_PROJETO.md.
--
-- com_xg_combinado/pct_xg_combinado: conta a partida como coberta se
-- QUALQUER uma das duas fontes tiver xG (Understat OR FotMob) -- na
-- ausência de uma, usa a outra. As colunas antigas pct_xg/pct_fotmob
-- (e as novas com_xg_fotmob/pct_xg_fotmob) são mantidas para quem
-- quiser o detalhamento por fonte.
CREATE OR REPLACE VIEW vw_cobertura_estatisticas AS
SELECT
    l.id AS league_id,
    l.name AS liga,
    m.season,
    count(DISTINCT m.id) FILTER (WHERE m.status = 'finished') AS finalizadas,
    count(DISTINCT ms.match_id) AS com_match_stats,
    count(DISTINCT ms.match_id) FILTER (WHERE ms.xg IS NOT NULL) AS com_xg,
    count(DISTINCT msf.match_id) AS com_fotmob,
    round(100.0 * count(DISTINCT ms.match_id)::numeric
        / NULLIF(count(DISTINCT m.id) FILTER (WHERE m.status = 'finished'), 0)::numeric, 1) AS pct_match_stats,
    round(100.0 * count(DISTINCT ms.match_id) FILTER (WHERE ms.xg IS NOT NULL)::numeric
        / NULLIF(count(DISTINCT m.id) FILTER (WHERE m.status = 'finished'), 0)::numeric, 1) AS pct_xg,
    round(100.0 * count(DISTINCT msf.match_id)::numeric
        / NULLIF(count(DISTINCT m.id) FILTER (WHERE m.status = 'finished'), 0)::numeric, 1) AS pct_fotmob,
    count(DISTINCT msf.match_id) FILTER (WHERE msf.xg IS NOT NULL) AS com_xg_fotmob,
    count(DISTINCT m.id) FILTER (WHERE ms.xg IS NOT NULL OR msf.xg IS NOT NULL) AS com_xg_combinado,
    round(100.0 * count(DISTINCT msf.match_id) FILTER (WHERE msf.xg IS NOT NULL)::numeric
        / NULLIF(count(DISTINCT m.id) FILTER (WHERE m.status = 'finished'), 0)::numeric, 1) AS pct_xg_fotmob,
    round(100.0 * count(DISTINCT m.id) FILTER (WHERE ms.xg IS NOT NULL OR msf.xg IS NOT NULL)::numeric
        / NULLIF(count(DISTINCT m.id) FILTER (WHERE m.status = 'finished'), 0)::numeric, 1) AS pct_xg_combinado
FROM matches m
JOIN leagues l ON l.id = m.league_id
LEFT JOIN match_stats ms ON ms.match_id = m.id
LEFT JOIN match_stats_fotmob msf ON msf.match_id = m.id
GROUP BY l.id, l.name, m.season;
