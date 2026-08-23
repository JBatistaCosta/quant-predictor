-- Escopo de cada liga no pipeline do modelo misto (treinar_modelo_hibrido.py)
-- deixa de ser uma lista de nomes que precisa ser lembrada/digitada a cada
-- workflow_dispatch e vira um atributo por liga -- reclassificar uma liga
-- (incluir, excluir, mover de "extra" pra "treino") vira "mudar uma coluna",
-- não "editar código nem lembrar a string exata no próximo disparo".
--
-- NULL (default) = fora do pipeline. "treino" = entra no fit dos
-- regressores/calibração/avaliação. "extra" = só recebe inferência
-- (.predict, nunca .fit -- ver achado #17 do CONTEXTO_PROJETO.md).
ALTER TABLE public.leagues
    ADD COLUMN IF NOT EXISTS modelo_misto_escopo text
    CHECK (modelo_misto_escopo IN ('treino', 'extra'));

COMMENT ON COLUMN public.leagues.modelo_misto_escopo IS
    'Escopo desta liga no pipeline do modelo misto (treinar_modelo_hibrido.py) -- '
    '"treino" entra no fit/calibração/avaliação; "extra" só recebe inferência '
    '(.predict, nunca .fit, ver achado #17 do CONTEXTO_PROJETO.md); NULL fica '
    'fora do pipeline. Editar esta coluna é a única mudança necessária pra '
    'incluir, excluir ou reclassificar uma liga -- não precisa mexer em '
    'código nem passar --ligas/--ligas-extra no workflow_dispatch.';

-- As 8 ligas já usadas como --ligas no treino real disparado nesta sessão
-- (boa cobertura de xG/escanteios, ver achado #10 do CONTEXTO_PROJETO.md).
UPDATE public.leagues SET modelo_misto_escopo = 'treino' WHERE name IN (
    'Premier League', 'La Liga', 'Serie A (Itália)', 'Bundesliga', 'Ligue 1',
    'Brasileirão Série A', 'Copa Libertadores', 'UEFA Champions League'
);

-- As 3 ligas já usadas como --ligas-extra (sem xG real, achado #17) mais as
-- 9 que o usuário pediu pra incluir como "extra" agora (tinham dado real de
-- partida finalizada mas nunca entraram em nenhum treino disparado até
-- aqui). Virar "treino" fica pra quando houver verificação de cobertura
-- estatística suficiente -- não decidido nesta migration.
UPDATE public.leagues SET modelo_misto_escopo = 'extra' WHERE name IN (
    'Copa Sudamericana', 'Copa do Brasil', 'FIFA Intercontinental Cup',
    'Brasileirão Série B', 'MLS', 'Championship', 'Eredivisie', 'Primeira Liga',
    'Copa do Mundo FIFA', 'FIFA Club World Cup', 'Eurocopa', 'Copa America'
);
