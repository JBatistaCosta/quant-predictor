-- Backfill histórico de match_player_stats_detalhe_fotmob (migration 20261003140000 já aplicada).
-- ~26 mil partidas não cabem numa chamada só (timeout), então a função processa ~40 s e devolve onde parou.
-- COMO USAR no SQL Editor do Supabase: rode a linha 1; copie `ultimo_match_id` do resultado e cole na linha seguinte;
-- repita até `terminou = true`. É seguro interromper e retomar (idempotente: refazer só regrava as mesmas linhas).

select * from public.backfill_detalhe_jogador_fotmob(0);
-- select * from public.backfill_detalhe_jogador_fotmob(<ultimo_match_id da chamada anterior>);

-- Conferência ao final (esperado: ~99% dos jogadores com >= 20 min têm linha):
-- select count(*) as linhas, count(distinct match_id) as partidas from public.match_player_stats_detalhe_fotmob;
-- Médias por linha (defesa/meio/ataque), por 90 min:
-- select * from public.v_detalhe_jogador_linha order by linha;
