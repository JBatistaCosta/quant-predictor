-- `odds_market` e `model_predictions` só tinham CREATE TABLE em
-- `20260817023000_backfill_schema_tabelas_legado.sql` (achado #1 do
-- CONTEXTO_PROJETO.md, PR #306) -- mas essa migration é datada DEPOIS de
-- `20260811190000_devig_odds_ratio_logaritmico.sql`, que cria a view
-- `v_market_edge` referenciando as DUAS (`FROM odds_market` na CTE
-- `grupos`, `FROM model_predictions mp` no SELECT final). O `Supabase
-- Preview` reconstrói um banco vazio replicando as migrations EM ORDEM
-- DE TIMESTAMP -- então a view tentava se criar antes das tabelas
-- existirem, e falhava com "relation odds_market does not exist"
-- (SQLSTATE 42P01) toda vez que qualquer PR trouxesse uma migration
-- nova (só não tinha sido pego porque, desde o PR #306, nenhum PR
-- anterior a este trouxe migration nova de verdade pra disparar o
-- replay completo -- confirmado rodando o PR #345 de verdade).
--
-- Corrigido com uma migration extra, datada ANTES de
-- 20260811190000 (não dá pra simplesmente renomear/mover as migrations
-- já mergeadas -- o Supabase rastreia migration aplicada por nome de
-- arquivo, renomear uma já aplicada em produção faria o sistema achar
-- que é uma migration NOVA e tentar reaplicar). Mesma definição EXATA
-- das duas tabelas no backfill original (`IF NOT EXISTS`, então roda
-- como no-op tanto em produção -- onde já existem -- quanto na segunda
-- vez que o replay chegar em 20260817023000).
CREATE TABLE IF NOT EXISTS public.odds_market (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  match_id bigint NOT NULL,
  bookmaker text NOT NULL,
  market text NOT NULL,
  selection text NOT NULL,
  odds numeric(8,3) NOT NULL,
  captured_at timestamp with time zone DEFAULT now(),
  snapshot text NOT NULL DEFAULT 'pre_closing'::text
);

CREATE TABLE IF NOT EXISTS public.model_predictions (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  match_id bigint NOT NULL,
  model_name text NOT NULL,
  market text NOT NULL,
  selection text NOT NULL,
  probability numeric(7,5) NOT NULL,
  created_at timestamp with time zone DEFAULT now(),
  fair_odds numeric GENERATED ALWAYS AS (round((1.0 / NULLIF(probability, (0)::numeric)), 3)) STORED
);
