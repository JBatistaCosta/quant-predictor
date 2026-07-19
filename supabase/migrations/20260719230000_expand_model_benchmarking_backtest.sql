-- =============================================================================
-- Migration: expande model_benchmarking_backtest pra análise completa
-- =============================================================================
-- Pedido do usuário: as mesmas análises de "Estatísticas dos Modelos" --
-- log-loss/Brier/Acurácia nos mercados previstos pelos modelos (1X2 E
-- Over/Under 2.5 gols), sempre que possível comparado com a Pinnacle sem
-- vig (odds justas), MAIS um teste de EV+ especificamente contra a odd de
-- ABERTURA da Pinnacle (com ROI) -- separado do teste de ROI já existente
-- (que usa a melhor odd real disponível entre TODOS os bookmakers, ver
-- `carregar_melhores_odds_fechamento`). Tudo quebrado por liga e
-- informando o período de teste (Test Set out-of-sample).
--
-- `mercado` entra na chave única -- cada modelo agora tem uma linha por
-- mercado ("1X2", "over_under_2.5"). Uma linha sintética adicional por
-- mercado (model_name = 'mercado_pinnacle_sem_vig') carrega só as métricas
-- de qualidade da própria Pinnacle (sem ROI, sem hiperparâmetros) --
-- referência de "linha eficiente" pra comparar com os modelos na mesma
-- tabela.
-- =============================================================================

alter table public.model_benchmarking_backtest
  add column if not exists mercado text not null default '1X2',
  add column if not exists periodo_inicio date,
  add column if not exists periodo_fim date,
  add column if not exists n_apostas_abertura integer,
  add column if not exists roi_abertura_medio numeric(8, 5),
  add column if not exists roi_abertura_ic95_inferior numeric(8, 5),
  add column if not exists roi_abertura_ic95_superior numeric(8, 5),
  add column if not exists significativo_abertura boolean,
  add column if not exists log_loss numeric(10, 6),
  add column if not exists brier numeric(10, 6),
  add column if not exists accuracy numeric(6, 5),
  add column if not exists n_amostras_qualidade integer;

alter table public.model_benchmarking_backtest
  alter column n_apostas drop not null,
  alter column roi_medio drop not null,
  alter column roi_ic95_inferior drop not null,
  alter column roi_ic95_superior drop not null,
  alter column significativo drop not null;

alter table public.model_benchmarking_backtest
  drop constraint if exists model_benchmarking_backtest_model_key;
alter table public.model_benchmarking_backtest
  add constraint model_benchmarking_backtest_model_mercado_key unique (model_name, mercado);

comment on table public.model_benchmarking_backtest is
  'Análise completa (Test Set out-of-sample) por variante de modelo do Model Benchmarking E por mercado (1X2, over_under_2.5): qualidade de probabilidade (log-loss/Brier/Acurácia) vs. Pinnacle sem vig, ROI simulado (Kelly 25%) vs. melhor odd real de fechamento (qualquer bookmaker) E vs. odd de abertura da Pinnacle especificamente, ambos com IC95% via bootstrap. Linha model_name=mercado_pinnacle_sem_vig carrega só a qualidade da própria Pinnacle (referência). Cada rodada do script substitui o resultado anterior por completo.';

alter table public.model_benchmarking_backtest_liga
  add column if not exists mercado text not null default '1X2',
  add column if not exists periodo_inicio date,
  add column if not exists periodo_fim date,
  add column if not exists n_apostas_abertura integer,
  add column if not exists roi_abertura_medio numeric(8, 5),
  add column if not exists roi_abertura_ic95_inferior numeric(8, 5),
  add column if not exists roi_abertura_ic95_superior numeric(8, 5),
  add column if not exists significativo_abertura boolean,
  add column if not exists log_loss numeric(10, 6),
  add column if not exists brier numeric(10, 6),
  add column if not exists accuracy numeric(6, 5),
  add column if not exists n_amostras_qualidade integer;

alter table public.model_benchmarking_backtest_liga
  alter column n_apostas drop not null,
  alter column roi_medio drop not null,
  alter column roi_ic95_inferior drop not null,
  alter column roi_ic95_superior drop not null,
  alter column significativo drop not null;

alter table public.model_benchmarking_backtest_liga
  drop constraint if exists model_benchmarking_backtest_liga_key;
alter table public.model_benchmarking_backtest_liga
  add constraint model_benchmarking_backtest_liga_model_liga_mercado_key unique (model_name, liga, mercado);

comment on table public.model_benchmarking_backtest_liga is
  'Quebra por liga da análise completa (ver model_benchmarking_backtest) -- qualidade + os 2 blocos de ROI, por mercado, com período de teste da liga. Linha model_name=mercado_pinnacle_sem_vig por liga carrega só a qualidade da Pinnacle nessa liga.';
