-- =============================================================================
-- Seed: mult_minuto_bin de escanteio/falta -- forma FIXA e EXTERNA (Achado
-- 13, ACHADOS_COMPORTAMENTO.md, StatsBomb Open Data, La Liga 2015/16
-- completa, 380 partidas, 12.136 faltas / 3.841 escanteios). Decisão já
-- negociada na sessão: escanteios e faltas não têm minuto real em nenhuma
-- fonte própria do projeto (só totais por partida), então entram no
-- relógio minuto-a-minuto da simulação usando essa forma temporal como
-- multiplicador sobre o total já calibrado com dado do projeto
-- (api/corners-model.js) -- mesmo padrão arquitetural que os gols já usam
-- hoje (MARKOV_MINUTE_BINS em AnaliseEvento.jsx: shape externo × taxa
-- própria).
--
-- SEMPRE league_id NULL (fallback global) -- não existe base pra recalibrar
-- por liga (é uma amostra externa única), e apresentar como se fosse
-- calibrada por confronto/liga seria enganoso (ver Achado 16: mesma
-- ressalva já feita pra matriz de transição zona-a-zona).
--
-- Valores por bloco de 15min do Achado 13, normalizados pra média 1 (mesma
-- convenção de MARKOV_MINUTE_BINS): o bloco final (75-89) absorve também a
-- linha "90+" do achado (soma das duas taxas), mesma simplificação que o
-- multiplicador de gol hardcoded já faz hoje ("inclui efeito de
-- acréscimos/cansaço" num bin só).
--
-- Falta (/jogo por bloco): 4.68, 5.05, 5.36, 5.23, 5.02, (5.06+1.55)=6.61 -- média 5.325
-- Escanteio (/jogo por bloco): 1.47, 1.56, 1.52, 1.78, 1.68, (1.64+0.45)=2.09 -- média 1.6833
--
-- DELETE + INSERT no escopo (evento in ('falta','escanteio') e
-- tipo='mult_minuto_bin'), não ON CONFLICT: league_id é NULL nestas linhas
-- (fallback global), e o Postgres trata NULL como distinto de qualquer
-- outro NULL para fins de unicidade -- ON CONFLICT nos 4 campos não
-- deduplicaria numa reaplicação (ver comentário na migration
-- 20260922100000_create_league_markov_params.sql). Mesmo padrão de
-- idempotência já usado nas funções de derivação do projeto.
-- =============================================================================

delete from public.league_markov_params
where league_id is null
  and evento in ('falta', 'escanteio')
  and tipo = 'mult_minuto_bin';

insert into public.league_markov_params (league_id, evento, tipo, chave, valor, amostra_n, origem)
values
  (null, 'falta',     'mult_minuto_bin', '0-14',  0.8789, 12136, 'statsbomb_achado_13 (forma externa La Liga 2015/16, não recalibrável por liga -- ver ACHADOS_COMPORTAMENTO.md Achado 13)'),
  (null, 'falta',     'mult_minuto_bin', '15-29', 0.9484, 12136, 'statsbomb_achado_13 (forma externa La Liga 2015/16, não recalibrável por liga -- ver ACHADOS_COMPORTAMENTO.md Achado 13)'),
  (null, 'falta',     'mult_minuto_bin', '30-44', 1.0066, 12136, 'statsbomb_achado_13 (forma externa La Liga 2015/16, não recalibrável por liga -- ver ACHADOS_COMPORTAMENTO.md Achado 13)'),
  (null, 'falta',     'mult_minuto_bin', '45-59', 0.9822, 12136, 'statsbomb_achado_13 (forma externa La Liga 2015/16, não recalibrável por liga -- ver ACHADOS_COMPORTAMENTO.md Achado 13)'),
  (null, 'falta',     'mult_minuto_bin', '60-74', 0.9427, 12136, 'statsbomb_achado_13 (forma externa La Liga 2015/16, não recalibrável por liga -- ver ACHADOS_COMPORTAMENTO.md Achado 13)'),
  (null, 'falta',     'mult_minuto_bin', '75-89', 1.2413, 12136, 'statsbomb_achado_13 (forma externa La Liga 2015/16, não recalibrável por liga -- inclui bloco 90+ somado; ver ACHADOS_COMPORTAMENTO.md Achado 13)'),

  (null, 'escanteio', 'mult_minuto_bin', '0-14',  0.8734, 3841, 'statsbomb_achado_13 (forma externa La Liga 2015/16, não recalibrável por liga -- ver ACHADOS_COMPORTAMENTO.md Achado 13)'),
  (null, 'escanteio', 'mult_minuto_bin', '15-29', 0.9268, 3841, 'statsbomb_achado_13 (forma externa La Liga 2015/16, não recalibrável por liga -- ver ACHADOS_COMPORTAMENTO.md Achado 13)'),
  (null, 'escanteio', 'mult_minuto_bin', '30-44', 0.9030, 3841, 'statsbomb_achado_13 (forma externa La Liga 2015/16, não recalibrável por liga -- ver ACHADOS_COMPORTAMENTO.md Achado 13)'),
  (null, 'escanteio', 'mult_minuto_bin', '45-59', 1.0573, 3841, 'statsbomb_achado_13 (forma externa La Liga 2015/16, não recalibrável por liga -- ver ACHADOS_COMPORTAMENTO.md Achado 13)'),
  (null, 'escanteio', 'mult_minuto_bin', '60-74', 0.9980, 3841, 'statsbomb_achado_13 (forma externa La Liga 2015/16, não recalibrável por liga -- ver ACHADOS_COMPORTAMENTO.md Achado 13)'),
  (null, 'escanteio', 'mult_minuto_bin', '75-89', 1.2417, 3841, 'statsbomb_achado_13 (forma externa La Liga 2015/16, não recalibrável por liga -- inclui bloco 90+ somado; ver ACHADOS_COMPORTAMENTO.md Achado 13)');
