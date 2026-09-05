-- =============================================================================
-- Migration: v_game_state_por_forca -- controle de força de equipe
-- =============================================================================
-- CORRIGE UMA LEITURA ERRADA QUE ESTE PROJETO CHEGOU A REGISTRAR. A fase 2
-- (match_team_game_state) produziu, na média global, "quem está perdendo cria
-- MENOS xG que quem está ganhando" (1,370 contra 1,426 por 90). Esse número
-- estava CONFUNDIDO com força de equipe: quem está ganhando é, em média, o
-- time melhor, e a estrutura espelhada da fase 2 controla o TEMPO, não a FORÇA.
--
-- Controlando por Elo (`team_elo_history.rating_antes`, escopo 'global' --
-- rating ANTES da partida, sem vazamento), o sinal do efeito de xG total
-- INVERTE em jogos equilibrados:
--
--   |dif Elo| <= 25   perdendo 1,390  x  ganhando 1,281  (perdendo cria MAIS)
--   |dif Elo| > 75    perdendo 1,335  x  ganhando 1,526  (inverte -- é a força)
--
-- Segurando também o mando de campo (em jogo equilibrado por Elo o mandante
-- ainda ganha mais, então "ganhando" vinha enriquecido de mandantes):
--
--   mandante   perdendo 1,617 / 16,05 chutes   ganhando 1,388 / 11,38
--   visitante  perdendo 1,232 / 12,63 chutes   ganhando 1,127 /  9,25
--
-- O QUE SOBREVIVE A TODOS OS CONTROLES é a QUALIDADE por finalização, e ela é
-- notavelmente estável -- xG por chute ~0,122 ganhando contra ~0,098-0,101
-- perdendo, praticamente idêntico nas três faixas de Elo E nos dois lados.
-- Perseguir o jogo degrada a qualidade do chute em ~18%, independentemente de
-- quem é o time e de onde joga. O VOLUME (mais chutes perdendo) também
-- sobrevive. O xG TOTAL não sobrevive: é produto dos dois efeitos com a força
-- por cima, e por isso troca de sinal.
--
-- Esta view existe para que o controle seja reproduzível em vez de uma consulta
-- solta perdida num relatório: quem for agregar match_team_game_state deve
-- agregar POR `faixa_forca` (e de preferência também por `is_home`), sempre
-- dividindo por `minutos`.
-- =============================================================================
create or replace view public.v_game_state_por_forca
with (security_invoker = on) as
select
  g.match_id,
  g.team_id,
  g.is_home,
  g.estado,
  g.minutos,
  g.chutes_pro,
  g.xg_pro,
  g.chutes_contra,
  g.xg_contra,
  m.league_id,
  m.season,
  a.rating_antes                    as elo_time,
  b.rating_antes                    as elo_adversario,
  (a.rating_antes - b.rating_antes) as elo_dif,
  case when abs(a.rating_antes - b.rating_antes) <= 25 then 'equilibrado'
       when abs(a.rating_antes - b.rating_antes) <= 75 then 'leve'
       else 'desigual' end          as faixa_forca
from public.match_team_game_state g
join public.matches m on m.id = g.match_id
join public.team_elo_history a
  on a.match_id = g.match_id and a.team_id  = g.team_id and a.escopo = 'global'
join public.team_elo_history b
  on b.match_id = g.match_id and b.team_id <> g.team_id and b.escopo = 'global'
where g.placar_confere;

comment on view public.v_game_state_por_forca is
  'match_team_game_state com a diferença de Elo (escopo global, rating_antes -- sem vazamento) entre os dois times. EXISTE PARA IMPEDIR UM ERRO ESPECÍFICO: comparar estados do jogo sem controlar força de equipe mede quem é melhor, não como o time reage ao placar. Agregue SEMPRE por faixa_forca (e de preferência também por is_home), e sempre dividindo por minutos.';
