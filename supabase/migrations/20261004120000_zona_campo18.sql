-- Grade de 18 zonas: refinamento EXATO da grade de 12 (`zona_campo12`, migration 20261003140000).
--
-- Cada zona de 18 cabe dentro de uma de 12: as faixas `meio` e `ataque_fora_da_area` são divididas ao meio; `defesa` e
-- `grande_area_adversaria` ficam inteiras. Resultado: 6 faixas x 3 corredores. As duas faixas divididas foram escolhidas pelos
-- dados (StatsBomb, Achado 19 em ACHADOS_COMPORTAMENTO.md): testando dividir cada faixa ao meio, são as que mais separam o
-- comportamento da bola (ganho de log-verossimilhança de 19.409 e 8.476 nats, contra 4.678 da defesa e 644 da grande área).
--
--   faixa18: 0 defesa (x < 35) | 1 meio_baixo (35 <= x < 52,5) | 2 meio_alto (52,5 <= x < 70)
--          | 3 ataque_fora_da_area_baixo (70 <= x < 79,25, fora da grande área) | 4 ataque_fora_da_area_alto (x >= 79,25, fora da grande área)
--          | 5 grande_area_adversaria (x >= 88,5 e 13,85 <= y <= 54,15)
--   corredor: o mesmo de `zona_campo12` (1 lado_y_baixo | 2 centro | 3 lado_y_alto)
--   zona18 = faixa18 * 3 + corredor  (1..18)
--
-- A regra de zona NÃO é reescrita aqui: parte-se de `zona_campo12` (única fonte da regra de grande área e de corredor) e só refina.
-- Os pontos de toque (`match_player_heatmap_fotmob.pontos`) estão em décimos de metro, então esta grade não exige nova coleta.

CREATE OR REPLACE FUNCTION public.zona_campo18(p_x double precision, p_y double precision)
RETURNS smallint
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
  SELECT CASE WHEN z12 IS NULL THEN NULL ELSE (
    (CASE (z12 - 1) / 3
       WHEN 0 THEN 0
       WHEN 1 THEN (CASE WHEN p_x < 52.5 THEN 1 ELSE 2 END)
       WHEN 2 THEN (CASE WHEN p_x < 79.25 THEN 3 ELSE 4 END)
       ELSE 5
     END) * 3 + (z12 - 1) % 3 + 1
  )::smallint END
  FROM (SELECT public.zona_campo12(p_x, p_y)::integer AS z12) t
$$;

CREATE TABLE IF NOT EXISTS public.zonas_campo18 (
  zona          smallint PRIMARY KEY CHECK (zona BETWEEN 1 AND 18),
  zona12        smallint NOT NULL REFERENCES public.zonas_campo12 (zona),   -- a zona de 12 que contém esta
  faixa         smallint NOT NULL,
  faixa_nome    text     NOT NULL,
  corredor      smallint NOT NULL,
  corredor_nome text     NOT NULL
);
INSERT INTO public.zonas_campo18 (zona, zona12, faixa, faixa_nome, corredor, corredor_nome)
SELECT f.faixa * 3 + c.corredor,
       (CASE f.faixa WHEN 0 THEN 0 WHEN 1 THEN 1 WHEN 2 THEN 1 WHEN 3 THEN 2 WHEN 4 THEN 2 ELSE 3 END) * 3 + c.corredor,
       f.faixa, f.nome, c.corredor, c.nome
  FROM (VALUES (0, 'defesa'), (1, 'meio_baixo'), (2, 'meio_alto'), (3, 'ataque_fora_da_area_baixo'),
               (4, 'ataque_fora_da_area_alto'), (5, 'grande_area_adversaria')) AS f(faixa, nome)
  CROSS JOIN (VALUES (1, 'lado_y_baixo'), (2, 'centro'), (3, 'lado_y_alto')) AS c(corredor, nome)
ON CONFLICT (zona) DO NOTHING;
ALTER TABLE public.zonas_campo18 ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "zonas_campo18 leitura publica" ON public.zonas_campo18;
CREATE POLICY "zonas_campo18 leitura publica" ON public.zonas_campo18 FOR SELECT USING (true);

-- Toques por jogador, jogo e zona de 18 (mesma ideia de `v_toques_zona`). Zona sem toque não aparece.
CREATE OR REPLACE VIEW public.v_toques_zona18 WITH (security_invoker = true) AS
SELECT h.match_id, h.team_id, h.fotmob_player_id, h.player_id, z.zona, count(*)::integer AS toques
  FROM public.match_player_heatmap_fotmob h
 CROSS JOIN LATERAL generate_series(1, h.n_pontos) AS i
 CROSS JOIN LATERAL (SELECT public.zona_campo18(h.pontos[2 * i - 1] / 10.0, h.pontos[2 * i] / 10.0) AS zona) z
 GROUP BY h.match_id, h.team_id, h.fotmob_player_id, h.player_id, z.zona;

GRANT EXECUTE ON FUNCTION public.zona_campo18(double precision, double precision) TO anon, authenticated;
