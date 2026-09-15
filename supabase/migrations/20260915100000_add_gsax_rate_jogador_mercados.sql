-- =============================================================================
-- Migration: player_match_estimates -- nova coluna gsax_rate (goleiro)
-- =============================================================================
-- `pricing_pipeline.py` (Camada 1, `PlayerToTeamAggregator.modular_por_
-- goleiro`) já implementa o ajuste de conversão por goleiro adversário
-- (`λ_gols,xGOT = λ_xGOT · (1 - GSAx_rate)`) desde que o módulo foi criado,
-- mas o runner de produção (`scripts/rodar_pricing_pipeline.py`) sempre
-- passou `gsax_rate=0.0` hardcoded -- GSAx (gols evitados acima do esperado
-- vs. xGOT enfrentado) não existia calculado em lugar nenhum do projeto.
--
-- GSAx é desempenho INDIVIDUAL de jogador (o goleiro), então pertence ao
-- mesmo pipeline que já estima desempenho individual doutros jogadores
-- (`scripts/rodar_jogador_mercados_previsto.py`, que já povoa
-- `player_match_estimates` com uma linha por jogador titular/reserva
-- provável de cada time, goleiro incluído -- `posicao_detalhe='GK'` já é um
-- dos códigos capturados) -- não uma função calculada ad-hoc dentro do
-- pricing pipeline, que passa a ser só consumidor.
--
-- Nullable de propósito: só populado pra jogadores com posição GK e com
-- amostra suficiente (>=5 partidas como titular, ver
-- dados_historicos.obter_gsax_atual) -- NULL nunca é confundido com "0.0
-- calculado" (goleiro realmente neutro), o lado consumidor
-- (`modular_por_goleiro`) já trata ausência como neutro.
-- =============================================================================

alter table public.player_match_estimates
  add column if not exists gsax_rate numeric;

comment on column public.player_match_estimates.gsax_rate is
  'GSAx_rate = 1 - (gols_sofridos_reais / xGOT_enfrentado), últimas 25 partidas como titular (match_lineup_fotmob.is_starter + match_player_stats_fotmob.is_goalkeeper), clipado em [-1,1]. Só populado pra jogador GK com amostra >=5 partidas -- NULL pra jogador de linha ou goleiro com histórico insuficiente. Consumido por scripts/pricing_pipeline.py::PlayerToTeamAggregator.modular_por_goleiro via scripts/rodar_pricing_pipeline.py.';
