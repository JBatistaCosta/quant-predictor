-- Corrige bug do backfill (#127): linhas de over_under_2.5 legitimamente não
-- têm prob_home/prob_draw/prob_away (montar_linhas_predicoes_over_under25 nem
-- inclui essas chaves), mas a migração add_mercado_predicoes só tornou
-- prob_under/prob_over nullable -- prob_home/draw/away continuavam NOT NULL
-- desde a criação original da tabela (quando só existia o mercado 1X2), o que
-- derrubou o upsert com "null value in column prob_home violates not-null
-- constraint" assim que a primeira leva de linhas over_under_2.5 chegou.
alter table public.predicoes
  alter column prob_home drop not null,
  alter column prob_draw drop not null,
  alter column prob_away drop not null;
