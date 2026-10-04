-- teams.aliases existe em produção desde antes, mas foi criada fora das
-- migrations: nenhum arquivo em supabase/migrations a criava. Um banco montado
-- só a partir dos arquivos (ex.: o branch de preview do Supabase de cada PR)
-- nascia sem a coluna, e a migration 20261005100200 (corrige_nomes_selecoes) e o
-- código de api/_lib/nomesTimes.js, que a lê, falhavam com
-- "column t.aliases does not exist" (PR #768).
--
-- Mesma definição que já existe em produção (text[], aceita nulo, padrão '{}').
-- Em produção é um no-op. Versão anterior à 20261005100000 para rodar antes de
-- qualquer migration que use a coluna.
alter table public.teams
  add column if not exists aliases text[] default '{}'::text[];

comment on column public.teams.aliases is 'Nomes alternativos do time (grafias de outras fontes, nome popular). Entram no casamento exato de api/_lib/nomesTimes.js. Diferente de nicknames (apelidos).';
