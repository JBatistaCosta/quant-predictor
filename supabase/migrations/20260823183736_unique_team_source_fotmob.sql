-- =============================================================================
-- Constraint: um team_id só pode ter UM source_id do FotMob
-- =============================================================================
-- Achado em produção: 21 times ficaram com 2-3 source_id do FotMob diferentes
-- mapeados pro mesmo team_id interno (ex. FC Barcelona -> ID certo + ID de um
-- clube brasileiro homônimo com 0 jogadores; 1.FC Union Berlin -> ID certo +
-- ID de um clube argentino "Union"). A constraint existente
-- (unique(source, source_id)) só impede o INVERSO -- não impedia isso.
--
-- Consequência real: arquivos_do_claude/ingestao_fotmob_elenco.py processa o
-- MESMO team_id uma vez por source_id duplicado, cada vez fazendo
-- delete-then-upsert completo do elenco daquele time -- sem `.order()` na
-- paginação de team_source_ids (corrigido junto, ver commit), a ordem de
-- processamento não é garantida estável entre execuções, então o elenco
-- final do time podia flutuar entre o certo e o de um clube totalmente
-- diferente de um dia pro outro -- causa raiz confirmada do "jogador que não
-- faz parte do elenco aparecendo às vezes" no XI previsto.
--
-- Os 21 duplicados de source='fotmob' já foram resolvidos manualmente
-- (verificados um a um contra o payload real do FotMob antes de remover o ID
-- errado) -- esta migration só garante que não volta a acontecer.
--
-- Escopada só a 'fotmob' (índice parcial, não `unique(team_id, source)` pra
-- toda fonte): existe duplicata equivalente em source='api_football' (9
-- times), mas essa fonte não é usada em nenhum lugar do pipeline de XI
-- titular -- fora do escopo desta correção, fica registrado aqui pra
-- investigar depois, não resolvido às cegas junto com isto.
-- =============================================================================

create unique index team_source_ids_team_fotmob_key
  on public.team_source_ids (team_id)
  where source = 'fotmob';
