-- =============================================================================
-- Migration: casas_aposta_fechamento em model_benchmarking_backtest(_liga)
-- =============================================================================
-- Pedido do usuário: saber contra qual casa de aposta o ROI de "fechamento"
-- (scripts/backtest_kelly.py::carregar_melhores_odds_fechamento -- a melhor
-- odd real entre TODAS as casas cadastradas em odds_market, ao contrário do
-- ROI de "abertura", que já é sempre especificamente a Pinnacle) está sendo
-- medido de verdade. Como a odd vencedora varia aposta a aposta, a coluna
-- guarda a DISTRIBUIÇÃO (contagem de apostas por bookmaker, ex.
-- {"pinnacle": 42, "bet365": 10, "betano": 3}) em vez de um nome único --
-- `scripts/backtest_kelly.py::_resumir_casas_aposta` calcula isso a partir
-- de `montar_apostas`/`montar_apostas_dupla_chance` (novo parâmetro opcional
-- `bookmakers_por_partida`), sobre as apostas que teriam stake>0 no Kelly
-- (mesmo universo do `n_apostas`). NULL = mercado sem essa info (nunca
-- aconteceu de fato desde essa migration, mas mantido nullable pra não
-- quebrar linhas antigas ainda não regravadas por um novo backtest).
-- =============================================================================

alter table public.model_benchmarking_backtest
  add column if not exists casas_aposta_fechamento jsonb;

alter table public.model_benchmarking_backtest_liga
  add column if not exists casas_aposta_fechamento jsonb;

comment on column public.model_benchmarking_backtest.casas_aposta_fechamento is
  'Contagem de apostas (universo de n_apostas, ROI de fechamento) por casa de aposta que ofereceu a melhor odd -- ex. {"pinnacle": 42, "bet365": 10}. NULL = ainda não regravado com essa info (backtest antigo) ou mercado sem odds reais de fechamento.';

comment on column public.model_benchmarking_backtest_liga.casas_aposta_fechamento is
  'Mesma métrica de public.model_benchmarking_backtest.casas_aposta_fechamento, recortada pra essa liga.';
