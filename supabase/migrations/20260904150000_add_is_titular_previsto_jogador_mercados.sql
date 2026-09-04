-- =============================================================================
-- Migration: adiciona is_titular_previsto (flag correta de 11 titulares)
-- em player_match_estimates
-- =============================================================================
-- BUG REAL confirmado via SQL em produção: a tabela "Chutes/gols/xG por
-- jogador" mostrava até 24 jogadores no bucket "Titular" pra fonte_titular=
-- 'previsto' (deveria ser sempre exatamente 11). Causa raiz: o bucket
-- Titular/Banco no frontend (AnaliseAvancadaEvento.jsx, LIMIAR_TITULAR=0.5)
-- usava um corte de 0.5 sobre `prob_titular_usada`, que pra 'previsto' vem
-- de `xi_previsto.prob_titular` -- a probabilidade CONTÍNUA e INDEPENDENTE
-- por jogador do modelo de XI (cada jogador é pontuado isoladamente, sem
-- restrição de somar 11 por time), não a seleção final. `xi_previsto` já
-- tem a flag CORRETA (`is_titular_previsto`, calculada por
-- `selecionar_titulares_por_posicao` em scripts/rodar_xi_previsto.py,
-- restrita por posição/formação, sempre exatamente 11 -- e com um bugfix
-- documentado no próprio arquivo pra exatamente essa classe de erro,
-- "grupos com até 18 titulares" antes de resetar a flag a cada rodada) e é
-- usada corretamente em `dados_historicos.obter_titular_atual` (força de
-- XI agregada por time) -- só não tinha sido propagada pra
-- `player_match_estimates`, que fez seu próprio join direto contra
-- `xi_previsto.prob_titular` em scripts/rodar_jogador_mercados_previsto.py.
--
-- `prob_titular_usada` continua existindo e continua contínua pra
-- 'previsto' (usada de propósito na mistura probabilística de
-- `minutos_esperados` -- ver módulo -- e como sinal de confiança bruto,
-- não descartado) -- essa coluna nova é o sinal correto e específico pro
-- bucket Titular/Banco, consistente entre 'real' (is_starter, já exato) e
-- 'previsto' (agora is_titular_previsto, também exato).
-- =============================================================================

alter table public.player_match_estimates
  add column if not exists is_titular_previsto boolean;

comment on column public.player_match_estimates.is_titular_previsto is
  'Seleção final de titular (sempre exatamente 11 por time) -- para fonte_titular=''real'' é match_lineup_fotmob.is_starter; para ''previsto'' é xi_previsto.is_titular_previsto (restrito por posição/formação via selecionar_titulares_por_posicao, NÃO um corte sobre a probabilidade contínua prob_titular_usada, que pode exceder 11 acima de qualquer limiar fixo). Nulo em linhas geradas antes desta migration -- frontend cai de volta no corte antigo sobre prob_titular_usada nesse caso, até o próximo ciclo de prever_jogador_mercados.yml sobrescrever.';
