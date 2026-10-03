#!/bin/bash
# Reaplica TODAS as migrations em ordem num banco vazio (o que o check "Supabase Preview" faz) e lista as que falham, sem parar na primeira.
# Precisa de um Postgres descartável (mesmas variáveis dos testes SQL: aqui fixo em /tmp/pgtest2, porta 55433, usuário postgres) -- NUNCA aponte para produção:
# o script APAGA e recria o banco "replay". Uso: bash arquivos_do_claude/replay_migrations.sh [pasta de migrations]
DIR=${1:-supabase/migrations}
P="psql -h /tmp/pgtest2 -p 55433 -U postgres -q -At"
$P -d postgres -c "drop database if exists replay" -c "create database replay" >/dev/null 2>&1
$P -d replay >/dev/null 2>&1 <<'SQL'
do $$ begin
  if not exists (select 1 from pg_roles where rolname='anon') then create role anon; end if;
  if not exists (select 1 from pg_roles where rolname='authenticated') then create role authenticated; end if;
  if not exists (select 1 from pg_roles where rolname='service_role') then create role service_role; end if;
end $$;
create schema if not exists extensions; create schema if not exists auth;
create table auth.users(id uuid primary key default gen_random_uuid(), email text);
create function auth.uid() returns uuid language sql stable as $$ select null::uuid $$;
create function auth.role() returns text language sql stable as $$ select 'anon'::text $$;
create function auth.jwt() returns jsonb language sql stable as $$ select '{}'::jsonb $$;
SQL
falhas=0
for f in $(ls $DIR/*.sql | sort); do
  out=$($P -d replay -v ON_ERROR_STOP=1 -f "$f" 2>&1 >/dev/null | grep -E "ERROR" | head -2)
  if [ -n "$out" ]; then falhas=$((falhas+1)); echo "FALHA $(basename $f): $out"; fi
done
echo "total de migrations: $(ls $DIR/*.sql | wc -l) | com falha: $falhas"
