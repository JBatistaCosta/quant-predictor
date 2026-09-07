-- vw_jogadores_lista não tinha migration local rastreada (drift de schema já
-- documentado em CONTEXTO_PROJETO.md) — esta migration recria a view completa
-- (definição confirmada via pg_get_viewdef em produção) só pra:
--   1. computar `age` a partir de `players.birth_date` quando disponível,
--      caindo pro valor bruto já armazenado quando não (players.birth_date
--      hoje está 0/24.502 preenchido — bug de extração corrigido separadamente
--      em api/model-maintenance.js, ver CONTEXTO_PROJETO.md);
--   2. expor `birth_date` (útil pro frontend futuramente, não usado ainda).
CREATE OR REPLACE VIEW public.vw_jogadores_lista AS
SELECT p.id,
    p.name,
    p.photo_url,
    COALESCE(EXTRACT(YEAR FROM age(CURRENT_DATE, p.birth_date))::int, p.age) AS age,
    p.country_name,
    p.country_code,
    COALESCE(pd.current_market_value_eur, p.market_value) AS market_value,
    pr.rating,
    pr.n_partidas,
    t.id AS team_id,
    t.name AS team_name,
    t.crest_url AS team_crest_url,
    eq.id AS equipe_id,
    clube_atual.team_id IS NOT NULL AS clube_atual_confirmado,
    p.fotmob_player_id,
    p.perfil_importado = 1 AS perfil_sincronizado,
    p.perfil_atualizado_em,
    p.birth_date
   FROM players p
     LEFT JOIN player_ratings pr ON pr.player_id = p.id
     LEFT JOIN player_details_fotmob pd ON pd.player_id = p.id
     LEFT JOIN LATERAL ( SELECT ts.team_id
           FROM player_career_history_fotmob c
             JOIN team_source_ids ts ON ts.source = 'fotmob'::text AND ts.source_id = c.team_fotmob_id
          WHERE c.player_id = p.id AND c.start_date <= CURRENT_DATE AND (c.end_date IS NULL OR c.end_date >= CURRENT_DATE)
          ORDER BY c.start_date DESC
         LIMIT 1) clube_atual ON true
     LEFT JOIN teams t ON t.id = COALESCE(clube_atual.team_id, p.last_team_id)
     LEFT JOIN equipes eq ON eq.pipeline_team_id = t.id;
