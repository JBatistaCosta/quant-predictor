-- Fase 2 da granularidade de jogador (base para simulação de jogo por cadeia de Markov): ONDE cada jogador toca na bola.
--
-- Origem (descoberta em 03/10/2026, ver CONTEXTO_PROJETO.md): o FotMob serve, por partida, o mapa de calor de cada jogador
--   GET https://www.fotmob.com/api/data/heatmap/match/{id}/heatmaps?heatmapUrl=https%3A%2F%2Fpub.fotmob.com%2Fprod%2Fdb%2Fapi%2Fheatmap%2Fmatch%2F{id}
-- como SVG com um <circle cx cy> por ponto, em coordenadas 105 x 68 (mesmas do shotmap; o time sempre ataca rumo a x = 105).
-- O número de pontos acompanha os toques do jogador (correlação 0,996 com `touches`, razão mediana 1,15 em São Paulo x
-- Santos), então cada ponto é a posição aproximada de um toque. NÃO traz tipo de ação nem instante: dá "onde o jogador
-- participa do jogo", não "o que ele faz ali". Chaves do arquivo são `p<optaId>`; o `optaId` -> id FotMob vem do matchDetails.
-- Disponível só para jogos recentes (março/2026 em diante; antes disso a resposta é 404).
--
-- Armazenamento: `pontos` é um array achatado [x1, y1, x2, y2, ...] em DÉCIMOS de metro (x 0..1050, y 0..680), para caber
-- em smallint e permitir refazer a grade de zonas depois sem nova coleta. A regra ponto -> zona mora SÓ em
-- `zona_campo12` (migration 20261003140000); a view `v_toques_zona` apenas a aplica.

CREATE TABLE IF NOT EXISTS public.match_player_heatmap_fotmob (
  id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  match_id          bigint NOT NULL REFERENCES public.matches(id) ON DELETE CASCADE,
  team_id           bigint,
  fotmob_player_id  text   NOT NULL,
  player_id         bigint,
  opta_id           text,
  n_pontos          smallint NOT NULL CHECK (n_pontos >= 0),
  pontos            smallint[] NOT NULL CHECK (cardinality(pontos) = 2 * n_pontos),
  fonte_atualizada_em timestamptz,
  coletado_em       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (match_id, fotmob_player_id)
);
CREATE INDEX IF NOT EXISTS match_player_heatmap_player_idx ON public.match_player_heatmap_fotmob (player_id);

-- Registro de tentativas por partida: o 404 de jogo antigo não deve ser tentado de novo a cada execução.
CREATE TABLE IF NOT EXISTS public.match_heatmap_coleta_fotmob (
  match_id     bigint PRIMARY KEY REFERENCES public.matches(id) ON DELETE CASCADE,
  status       text NOT NULL CHECK (status IN ('ok', 'indisponivel', 'erro')),
  n_jogadores  smallint,
  tentativas   smallint NOT NULL DEFAULT 1,
  coletado_em  timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.match_player_heatmap_fotmob ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.match_heatmap_coleta_fotmob ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "heatmap_jogador leitura publica" ON public.match_player_heatmap_fotmob;
CREATE POLICY "heatmap_jogador leitura publica" ON public.match_player_heatmap_fotmob FOR SELECT USING (true);
DROP POLICY IF EXISTS "heatmap_coleta leitura publica" ON public.match_heatmap_coleta_fotmob;
CREATE POLICY "heatmap_coleta leitura publica" ON public.match_heatmap_coleta_fotmob FOR SELECT USING (true);

-- Toques por jogador, jogo e zona (as 12 zonas de `zona_campo12`). Zona sem toque não aparece.
CREATE OR REPLACE VIEW public.v_toques_zona WITH (security_invoker = true) AS
SELECT h.match_id, h.team_id, h.fotmob_player_id, h.player_id, z.zona, count(*)::integer AS toques
  FROM public.match_player_heatmap_fotmob h
 CROSS JOIN LATERAL generate_series(1, h.n_pontos) AS i
 CROSS JOIN LATERAL (SELECT public.zona_campo12(h.pontos[2 * i - 1] / 10.0, h.pontos[2 * i] / 10.0) AS zona) z
 GROUP BY h.match_id, h.team_id, h.fotmob_player_id, h.player_id, z.zona;

-- Distribuição média de toques por LINHA do jogador (goleiro/defesa/meio/ataque, `players.usual_position_id`) e zona:
-- participação na linha (soma dos toques na zona / soma dos toques da linha), só jogadores com >= 20 min.
CREATE OR REPLACE VIEW public.v_toques_zona_linha WITH (security_invoker = true) AS
WITH base AS (
  SELECT CASE p.usual_position_id WHEN 0 THEN 'goleiro' WHEN 1 THEN 'defesa' WHEN 2 THEN 'meio' WHEN 3 THEN 'ataque' END AS linha,
         t.zona, sum(t.toques) AS toques
    FROM public.v_toques_zona t
    JOIN public.players p ON p.id = t.player_id
    JOIN public.match_player_stats_fotmob s ON s.match_id = t.match_id AND s.fotmob_player_id = t.fotmob_player_id
   WHERE s.minutes_played >= 20 AND p.usual_position_id BETWEEN 0 AND 3
   GROUP BY 1, 2
)
SELECT b.linha, b.zona, c.faixa_nome, c.corredor_nome, b.toques,
       b.toques::numeric / sum(b.toques) OVER (PARTITION BY b.linha) AS participacao
  FROM base b
  JOIN public.zonas_campo12 c ON c.zona = b.zona;

-- A coleta grava com a chave de serviço; ninguém mais escreve.
REVOKE INSERT, UPDATE, DELETE ON public.match_player_heatmap_fotmob FROM anon, authenticated;
REVOKE INSERT, UPDATE, DELETE ON public.match_heatmap_coleta_fotmob FROM anon, authenticated;
