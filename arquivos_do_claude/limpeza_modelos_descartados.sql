-- Limpeza de modelos descartados (03/10/2026) -- PASSOS QUE SÓ O USUÁRIO PODE RODAR (o ambiente do Claude cancela DDL).
-- As DELEÇÕES de linhas já foram feitas (2.840.181 previsões); ESTE arquivo cobre o que falta:
--   1) (ADIADO) apagar as 2 tabelas scratch_* (grupo C);   2) DEVOLVER O ESPAÇO AO DISCO.
-- Por que o passo 2 existe: no Postgres, DELETE só marca as linhas como mortas -- o arquivo no disco NÃO encolhe, e o
-- Supabase cobra/mede o tamanho em disco. Só a reescrita da tabela (VACUUM FULL) devolve o espaço.

-- 1) Grupo C -- sobras de teste, sem referência em código nem em views (conferido em 03/10).
--    ADIADO por decisão do usuário (03/10): NÃO apagar por enquanto. Descomente só quando quiser.
--    Há outras 12 tabelas scratch_* (~53 MB no total, ex.: scratch_zonas, scratch_ou); não foram conferidas uma a uma.
-- drop table if exists public.scratch_sq_val;
-- drop table if exists public.scratch_sq_app;

-- 2) Reescrever as tabelas que perderam linhas. VACUUM FULL trava a tabela (leituras e escritas esperam) enquanto roda:
--    faça em horário calmo, UMA tabela por vez, e rode cada comando SOZINHO no SQL Editor (não pode estar dentro de
--    transação). Precisa de espaço livre temporário do tamanho da tabela. Começando pelas menores para ver o tempo.
-- vacuum (full, analyze) public.model_stats_resumo;
-- vacuum (full, analyze) public.model_match_estimates;
-- vacuum (full, analyze) public.model_predictions;     -- a que importa: ~5,4 GB antes; esperado liberar ~1,2 GB

-- Conferir o tamanho antes e depois:
-- select pg_size_pretty(pg_total_relation_size('public.model_predictions'));
-- select pg_size_pretty(pg_database_size(current_database()));
