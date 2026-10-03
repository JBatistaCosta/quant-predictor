-- Busca de jogadores por APELIDO e por SEMELHANÇA (fuzzy).
--
-- Problema: a tela /jogadores filtrava `players.name` com ILIKE puro. `name` guarda só o nome oficial do FotMob
-- ("Gabriel Barbosa"), então "Gabigol" não achava nada, um erro de digitação (ou um espaço sobrando no fim) também
-- não, e acento atrapalhava ("Paqueta" x "Paquetá").
--
-- O que esta migration cria:
--   1. extensões pg_trgm (semelhança por trigramas) e unaccent (tirar acento), no schema `extensions`;
--   2. `norm_busca(texto)`: minúsculas + sem acento + espaços normalizados (IMMUTABLE, para poder indexar);
--   3. tabela `player_aliases` (apelido -> jogador), leitura pública, escrita só pela service_role;
--   4. índices de trigramas em `players.name` e em `player_aliases.alias`;
--   5. RPC `buscar_jogadores(p_termo, p_limite)`: devolve (player_id, score, apelido) ordenado por relevância;
--   6. semente com apelidos conferidos pelo `fotmob_player_id` (NUNCA por nome: ver CLAUDE.md sobre mapeamentos).
--
-- Relevância (score 0..1): 1,00 nome/apelido exatamente igual; 0,95 começa com o termo; 0,90 contém o termo;
-- senão a melhor entre `word_similarity` x 0,9 (termo contra o trecho mais parecido do nome; o desconto evita que
-- um nome que apenas CONTÉM o termo empate com o nome exato) e `similarity`. Só entram
-- candidatos com score >= 0,45 (>= 0,55 para termos de até 3 letras, para não trazer lixo).
-- O frontend cai no ILIKE antigo (já com trim) se esta função ainda não existir.

CREATE SCHEMA IF NOT EXISTS extensions;
CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA extensions;
CREATE EXTENSION IF NOT EXISTS unaccent WITH SCHEMA extensions;

CREATE OR REPLACE FUNCTION public.norm_busca(p_texto text)
RETURNS text
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
SET search_path = public, extensions
AS $$
  SELECT btrim(regexp_replace(lower(extensions.unaccent('extensions.unaccent'::regdictionary, coalesce(p_texto, ''))), '\s+', ' ', 'g'))
$$;

CREATE TABLE IF NOT EXISTS public.player_aliases (
  id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  player_id  bigint NOT NULL REFERENCES public.players(id) ON DELETE CASCADE,
  alias      text   NOT NULL CHECK (length(btrim(alias)) > 0),
  fonte      text   NOT NULL DEFAULT 'manual',
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS player_aliases_player_alias_uq ON public.player_aliases (player_id, (public.norm_busca(alias)));
CREATE INDEX IF NOT EXISTS player_aliases_alias_trgm ON public.player_aliases USING gin ((public.norm_busca(alias)) extensions.gin_trgm_ops);
CREATE INDEX IF NOT EXISTS players_name_trgm ON public.players USING gin ((public.norm_busca(name)) extensions.gin_trgm_ops);

ALTER TABLE public.player_aliases ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "player_aliases leitura publica" ON public.player_aliases;
CREATE POLICY "player_aliases leitura publica" ON public.player_aliases FOR SELECT USING (true);

CREATE OR REPLACE FUNCTION public.buscar_jogadores(p_termo text, p_limite integer DEFAULT 100)
RETURNS TABLE (player_id bigint, score real, apelido text)
LANGUAGE sql
STABLE
SET search_path = public, extensions
AS $$
  WITH q AS (
    SELECT public.norm_busca(p_termo) AS t
  ),
  cand AS (
    SELECT p.id AS player_id, public.norm_busca(p.name) AS n, NULL::text AS apelido
      FROM public.players p, q
     WHERE length(q.t) >= 2
       AND (public.norm_busca(p.name) LIKE '%' || q.t || '%'
            OR public.norm_busca(p.name) % q.t
            OR q.t <% public.norm_busca(p.name))
    UNION ALL
    SELECT a.player_id, public.norm_busca(a.alias), a.alias
      FROM public.player_aliases a, q
     WHERE length(q.t) >= 2
       AND (public.norm_busca(a.alias) LIKE '%' || q.t || '%'
            OR public.norm_busca(a.alias) % q.t
            OR q.t <% public.norm_busca(a.alias))
  ),
  pontuado AS (
    SELECT c.player_id, c.apelido,
           GREATEST(
             CASE WHEN c.n = q.t THEN 1.0
                  WHEN c.n LIKE q.t || '%' THEN 0.95
                  WHEN c.n LIKE '%' || q.t || '%' THEN 0.90
                  ELSE 0 END,
             word_similarity(q.t, c.n) * 0.9,
             similarity(q.t, c.n)
           )::real AS score,
           length(q.t) AS tam
      FROM cand c, q
  ),
  melhor AS (
    SELECT DISTINCT ON (player_id) player_id, score, apelido
      FROM pontuado
     WHERE score >= CASE WHEN tam <= 3 THEN 0.55 ELSE 0.45 END
     ORDER BY player_id, score DESC, apelido NULLS LAST
  )
  SELECT m.player_id, m.score, m.apelido
    FROM melhor m
    LEFT JOIN public.players p ON p.id = m.player_id
   ORDER BY m.score DESC, p.market_value DESC NULLS LAST, m.player_id
   LIMIT greatest(1, least(coalesce(p_limite, 100), 500))
$$;

GRANT EXECUTE ON FUNCTION public.buscar_jogadores(text, integer) TO anon, authenticated;
GRANT EXECUTE ON FUNCTION public.norm_busca(text) TO anon, authenticated;

-- Semente: apelidos conferidos pelo id do FotMob (nome oficial ao lado, para auditoria).
INSERT INTO public.player_aliases (player_id, alias, fonte)
SELECT p.id, v.alias, 'semente'
  FROM (VALUES
    ('450848', 'Gabigol'),         -- Gabriel Barbosa
    ('19533',  'Neymar Jr'),       -- Neymar
    ('19533',  'Neymar Junior'),
    ('846033', 'Vini Jr'),         -- Vinicius Junior
    ('846033', 'Vinicius Jr'),
    ('846033', 'Vini'),
    ('30893',  'CR7')             -- Cristiano Ronaldo
  ) AS v(fotmob_player_id, alias)
  JOIN public.players p ON p.fotmob_player_id = v.fotmob_player_id
ON CONFLICT DO NOTHING;
