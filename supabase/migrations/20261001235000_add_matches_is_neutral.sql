-- Cria `matches.is_neutral` no histórico de migrations.
--
-- Por que existe: em produção a coluna existe (boolean NOT NULL DEFAULT false: jogo em campo neutro), mas NENHUMA migration a
-- cria -- ela nasceu por SQL rodado direto no editor, como as outras lacunas que `20260817023000_backfill_schema_tabelas_legado.sql`
-- já fechou. A view `v_elo_ab_brier` (20261001240000_create_team_elo_xg.sql) usa `m.is_neutral`; num banco reconstruído do zero
-- (o check `Supabase Preview` de cada PR) essa view falhava com `column m.is_neutral does not exist`, e o check ficava vermelho
-- em todo PR com migration, sem relação com o que o PR mudava.
--
-- O timestamp fica logo ANTES de 20261001240000 para o preview criar a coluna antes da view. Em produção é um no-op
-- (`IF NOT EXISTS`). Verificado reaplicando as 123 migrations em ordem num Postgres vazio: sem esta migration a única falha
-- era a da `v_elo_ab_brier`; com ela, nenhuma.

ALTER TABLE public.matches ADD COLUMN IF NOT EXISTS is_neutral boolean NOT NULL DEFAULT false;
