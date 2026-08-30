-- =============================================================================
-- Migration: adiciona posição fina (posicao_detalhe) em player_match_estimates
-- =============================================================================
-- Pedido do usuário: aplicar na tabela de mercados por jogador o mesmo
-- refinamento de posição já feito em xi_previsto (PR #395) -- código bruto
-- do FotMob (GK/CB/RB/LB/RWB/LWB/CDM/CM/CAM/RM/LM/RW/LW/ST), fonte real
-- player_availability_fotmob.posicao_detalhe. Mesmo raciocínio da migration
-- add_posicao_detalhe_xi_previsto: grava o valor usado no momento da
-- previsão em vez de re-derivar depois via join (pode divergir do elenco
-- atual se o jogador for transferido).
-- =============================================================================

alter table public.player_match_estimates
  add column if not exists posicao_detalhe text;

comment on column public.player_match_estimates.posicao_detalhe is
  'Posição fina do jogador no momento da previsão (código FotMob: GK/CB/RB/LB/RWB/LWB/CDM/CM/CAM/RM/LM/RW/LW/ST, fonte player_availability_fotmob.posicao_detalhe) -- nulo quando a fonte ainda não capturou essa granularidade pro jogador (fallback pro bucket grosso usual_position_id no frontend).';
