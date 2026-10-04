# Resumos do StatsBomb Open Data (referência versionada)

Fonte: **StatsBomb Open Data** (https://github.com/statsbomb/open-data), dados públicos, uso sujeito à licença do StatsBomb (citar a fonte ao usar).
O StatsBomb pode mudar o que oferece de graça (jogos já foram e podem ser retirados), por isso o repositório guarda TRÊS níveis:
1. `completo/` (163 MB): TUDO de cada partida (todos os tipos de evento e campos, escalações e metadados), fracionado em arquivos `.json.xz` de até 25 partidas.
2. `brutos/` (28 MB): os dados REDUZIDOS de cada partida, em arquivos de até 50 partidas. Basta para refazer as análises dos Achados 25 a 28 SEM rede.
3. os RESUMOS (poucos KB) por grupo, ao lado deste arquivo.

| Arquivo | O que tem | Gerado por |
|---|---|---|
| `ligas_2015_16.json` | La Liga, Premier League, Serie A e Ligue 1 de 2015/16 (temporadas completas, ~380 jogos cada) | `scripts/comparar_competicoes_statsbomb.py resumir` |
| `ligas_recentes.json` | La Liga 2018/19 a 2020/21 (só jogos do Barcelona), Ligue 1 2021/22 e 2022/23 (só PSG), Bundesliga 2023/24 (só Bayer Leverkusen), Indian Super League 2021/22 (115 jogos) | idem |
| `copa_mundo.json`, `euro.json`, `copa_america.json` | Copa do Mundo (8 edições; 1958 a 1990 têm 1 a 6 jogos cada), Euro 2020 e 2024, Copa América 2024. A Copa Africana de Nações está **fora** de propósito | idem |
| `tempo_la_liga_2015_16.json` | taxas por janela de 15 minutos e acréscimos, duração das ações e das posses (La Liga 2015/16, 380 jogos) | `scripts/analisar_tempo_eventos_statsbomb.py --resumo` |

Cada competição-temporada nos quatro primeiros arquivos tem: `jogos`, `match_ids`, `n_acoes`, `contagens_18x20` (zona de origem 0..17 x desfecho 0..19:
0..17 = zona de destino da continuação, 18 = chute, 19 = perda; regra de zonas em `scripts/gerar_matriz_transicao_statsbomb.py`),
`acoes_por_origem` (cruzamento, passe, condução...), `escanteios_origem_por_zona` (origem do escanteio, 18 zonas + sem zona) e `corners_times`
(escanteios dos dois times em cada jogo).

Conferir sem baixar nada: `python scripts/comparar_competicoes_statsbomb.py conferir --saida dados_referencia/statsbomb`.
Refazer o bruto: `baixar` (cerca de 25 minutos para todos os grupos). Achados que dependem destes arquivos: 25, 26 e 27 de `ACHADOS_COMPORTAMENTO.md`.

## Dados reduzidos e fracionados (`brutos/`)

Layout: `brutos/<grupo>/<competição_temporada>/parte-NNN.json.xz` (até 50 partidas cada, ~16 KB por partida) e um `INDICE.json` por competição com o
`match_id` de cada parte. Cada partida guarda, em colunas, só os tipos de evento que as análises usam (passe, condução, chute, drible, desarme sofrido,
erro de domínio, falta e escalação) com: tipo, índice do time (0 ou 1), período, minuto, segundo, duração (arredondada a 0,01 s), posse, posição inicial
e final, resultado do passe ou do chute e bits (1 = cruzamento, 2 = escanteio cobrado); mais a **origem de cada escanteio já calculada** (último lance do
time que atacava, tipo e zona de 18), porque ela depende de eventos brutos que não são guardados.

Sem rede:
```
python scripts/comparar_competicoes_statsbomb.py reconstruir --cache /tmp/sb_comp --fracionado dados_referencia/statsbomb/brutos
python scripts/comparar_competicoes_statsbomb.py resumir     --cache /tmp/sb_comp --saida /tmp/resumos    # idêntico aos resumos versionados
python scripts/comparar_competicoes_statsbomb.py comparar    --cache /tmp/sb_comp --baseline <acoes_v2.json da La Liga>
python scripts/analisar_tempo_eventos_statsbomb.py --brutos dados_referencia/statsbomb/brutos
```
Verificado em 04/10/2026: a reconstrução reproduz os cinco resumos byte a byte (ações, contagens 18 x 20, origem e contagem dos escanteios) e as contagens
por janela do tempo; a única diferença é a duração mediana do passe (1,3915 s no bruto, 1,39 s aqui) por causa do arredondamento a 0,01 s.
Para refazer a partir do StatsBomb (enquanto o dado existir): `baixar --fracionado dados_referencia/statsbomb/brutos` (cerca de 15 minutos).

## Dados completos (`completo/`)

Layout: `completo/<grupo>/<competição_temporada>/parte-NNN.json.xz` (até 25 partidas cada, ~100 KB por partida) e um `INDICE.json` com os ids de cada parte.
Cada partida: `{match_id, partida (metadados do `matches/<competição>/<temporada>.json`: times, placar, data, estádio, árbitro, técnicos), escalacoes (lineups), eventos}`.
Os eventos são os do StatsBomb COMPLETOS (todos os tipos e campos, inclusive `freeze_frame` dos chutes e `play_pattern`), exceto `id` e `related_events`
(UUIDs que não comprimem; `index` e `timestamp` ficam). 21 competições-temporadas, 2.152 partidas. **A Copa Africana de Nações e a Indian Super League 2021/22 NÃO
estão** (a Indian Super League está nos dados reduzidos e nos resumos).

Ler (sem rede): `comparar_competicoes_statsbomb.carregar_completo('dados_referencia/statsbomb/completo', '<grupo>', '<rótulo>')` gera as partidas em ordem.
Integridade: `python scripts/comparar_competicoes_statsbomb.py conferir_completo --fracionado dados_referencia/statsbomb/brutos` refaz o registro reduzido de cada
partida a partir dos eventos completos e compara com `brutos/`. **Verificado em 04/10/2026: as 2.152 partidas das 21 competições-temporadas saem idênticas.**
Refazer a partir do StatsBomb (enquanto o dado existir): `completo --fracionado dados_referencia/statsbomb/completo [--grupos ...] [--so "Premier League 2015"]`.

Arquivos derivados do simulador (Achados 31 e 32), todos refazíveis com os eventos completos: `residuo_entradas_ligas_2015_16.json` (classe de entrada de cada linha de ação e núcleo empírico da próxima linha, `scripts/analisar_residuo_entradas_statsbomb.py`), `forca_e_janela_ligas_2015_16.json` (contagens por time, papel, zona e janela de 15 min, em 4 quartos de jogos, `scripts/analisar_forca_e_janela_statsbomb.py`) e `validacao_forca_ligas_2015_16.json` (saída de `scripts/validar_simulador_forca.py`).
