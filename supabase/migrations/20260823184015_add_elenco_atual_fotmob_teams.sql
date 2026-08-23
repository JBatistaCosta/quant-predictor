-- =============================================================================
-- teams.elenco_atual_fotmob -- snapshot JSON do elenco atual, só auditoria
-- =============================================================================
-- Pedido do usuário: "seria interessante que o squad real fosse um campo
-- JSON (se não prejudicasse o desempenho dos algoritmos)".
--
-- Deliberadamente ADITIVO, não substitui player_availability_fotmob: os
-- pipelines de ML (dados_historicos.py, rodar_xi_previsto.py) continuam
-- lendo a tabela relacional pra filtro/join eficiente -- é isso que dá
-- desempenho pra consulta em lote de milhares de jogadores. Este campo é só
-- pra INSPEÇÃO HUMANA/DEBUG: um JSON só, 1 linha por time, com o elenco
-- inteiro num lugar só (nome, posição, lesão) -- útil pra auditar rápido
-- "quem o FotMob diz que está nesse time agora" sem juntar N linhas, e é
-- exatamente o tipo de visão que teria exposto mais cedo o bug de
-- crosswalk duplicado (ver migration unique_team_source_fotmob).
--
-- Escrito por arquivos_do_claude/ingestao_fotmob_elenco.py, no mesmo loop
-- que já monta a lista `elenco` pro upsert relacional -- custo adicional é
-- 1 UPDATE pequeno por time, nenhuma chamada de API a mais.
-- =============================================================================

alter table public.teams
  add column if not exists elenco_atual_fotmob jsonb;

comment on column public.teams.elenco_atual_fotmob is
  'Snapshot JSON do elenco atual (nome, posição, lesão por jogador) -- só auditoria/debug, escrito por ingestao_fotmob_elenco.py. Pipelines de ML usam player_availability_fotmob (relacional), não este campo.';
