-- =============================================================================
-- Migration: períodos de treino/validação em model_benchmarking_backtest
-- =============================================================================
-- Pedido do usuário: painel de informação do modelo (ícone "i", slide-out
-- panel) precisa mostrar não só o período de TESTE (já existente,
-- `periodo_inicio`/`periodo_fim`) mas também os períodos de TREINO e
-- VALIDAÇÃO usados no split cronológico 60/20/20 (`dados_historicos.
-- split_cronologico`, ver `backtest_kelly.py`). São os mesmos pra todo
-- modelo dentro do mesmo mercado (split é feito uma vez sobre o dataset
-- inteiro, não por modelo) -- só varia entre mercados quando o filtro de
-- NaN por mercado (ex.: partida sem escanteio ainda ingerido) reduz um
-- pouco a fatia de treino/validação daquele mercado especificamente.
-- =============================================================================

alter table public.model_benchmarking_backtest
  add column if not exists treino_periodo_inicio date,
  add column if not exists treino_periodo_fim date,
  add column if not exists validacao_periodo_inicio date,
  add column if not exists validacao_periodo_fim date;

comment on column public.model_benchmarking_backtest.treino_periodo_inicio is 'Início do período usado no Train Set (split cronológico 60/20/20), pro mercado desta linha.';
comment on column public.model_benchmarking_backtest.treino_periodo_fim is 'Fim do período usado no Train Set, pro mercado desta linha.';
comment on column public.model_benchmarking_backtest.validacao_periodo_inicio is 'Início do período usado no Validation Set (ajuste de calibração/tuning), pro mercado desta linha.';
comment on column public.model_benchmarking_backtest.validacao_periodo_fim is 'Fim do período usado no Validation Set, pro mercado desta linha.';
