-- VERIFICAÇÃO PRÉVIA (só leitura) para a migration 20261003120000_busca_jogadores_apelido_fuzzy.sql.
-- Rode no SQL Editor do Supabase ANTES de aplicar a migration. Não altera nada.
-- Cada linha traz `item`, `situacao` e `ok` (true = pode seguir; false = leia a coluna `situacao`).

with
ext as (
  select a.name,
         a.installed_version,
         (select n.nspname from pg_extension e join pg_namespace n on n.oid = e.extnamespace where e.extname = a.name) as schema_atual
    from pg_available_extensions a
   where a.name in ('pg_trgm', 'unaccent')
),
semente(fotmob_player_id, nome_esperado) as (
  values ('450848', 'Gabriel Barbosa'), ('19533', 'Neymar'), ('846033', 'Vinicius Junior'), ('30893', 'Cristiano Ronaldo')
)
select 1 as ordem, 'extensão ' || name as item,
       case when installed_version is null then 'disponível, ainda NÃO instalada (a migration instala no schema extensions)'
            else 'já instalada (v' || installed_version || ') no schema ' || coalesce(schema_atual, '?') end as situacao,
       (installed_version is null or schema_atual in ('extensions', 'public')) as ok
  from ext
union all
select 2, 'schema extensions',
       case when exists (select 1 from pg_namespace where nspname = 'extensions') then 'existe' else 'não existe (a migration cria)' end, true
union all
select 3, 'tabela player_aliases',
       case when to_regclass('public.player_aliases') is null then 'não existe (a migration cria)'
            else 'JÁ EXISTE com ' || (select count(*) from public.player_aliases) || ' linha(s): a migration usa IF NOT EXISTS e não a recria; confira as colunas' end,
       true
union all
select 4, 'função norm_busca(text)',
       case when to_regprocedure('public.norm_busca(text)') is null then 'não existe (a migration cria)' else 'já existe (a migration a substitui por CREATE OR REPLACE)' end, true
union all
select 5, 'função buscar_jogadores(text, integer)',
       case when to_regprocedure('public.buscar_jogadores(text,integer)') is null then 'não existe (a migration cria)' else 'já existe (a migration a substitui por CREATE OR REPLACE)' end, true
union all
select 6, 'índice ' || i.nome,
       case when to_regclass('public.' || i.nome) is null then 'não existe (a migration cria)' else 'já existe' end, true
  from (values ('players_name_trgm'), ('player_aliases_alias_trgm'), ('player_aliases_player_alias_uq')) as i(nome)
union all
select 7, 'players.id e players.fotmob_player_id',
       (select string_agg(column_name || '=' || data_type, ', ' order by column_name) from information_schema.columns
         where table_schema = 'public' and table_name = 'players' and column_name in ('id', 'fotmob_player_id')),
       (select count(*) = 2 and bool_and((column_name = 'id' and data_type = 'bigint') or (column_name = 'fotmob_player_id' and data_type = 'text'))
          from information_schema.columns where table_schema = 'public' and table_name = 'players' and column_name in ('id', 'fotmob_player_id'))
union all
select 8, 'papéis anon e authenticated (usados nos GRANT)',
       (select string_agg(rolname, ', ' order by rolname) from pg_roles where rolname in ('anon', 'authenticated')),
       (select count(*) = 2 from pg_roles where rolname in ('anon', 'authenticated'))
union all
select 9, 'semente: fotmob ' || s.fotmob_player_id || ' deve ser ' || s.nome_esperado,
       coalesce((select 'players.id ' || p.id || ' = ' || p.name from public.players p where p.fotmob_player_id = s.fotmob_player_id limit 1),
                'NÃO ENCONTRADO: o apelido desse jogador não será criado'),
       exists (select 1 from public.players p where p.fotmob_player_id = s.fotmob_player_id and lower(p.name) = lower(s.nome_esperado))
  from semente s
union all
select 10, 'players: total de linhas e nomes vazios',
       (select count(*) || ' jogadores, ' || count(*) filter (where name is null or btrim(name) = '') || ' sem nome' from public.players),
       true
order by 1, 2;
