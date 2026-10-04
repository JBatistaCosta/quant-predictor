# Resumos do StatsBomb Open Data (referência versionada)

Fonte: **StatsBomb Open Data** (https://github.com/statsbomb/open-data), dados públicos, uso sujeito à licença do StatsBomb (citar a fonte ao usar).
Aqui ficam só os RESUMOS (poucos KB); o bruto (mais de 150 MB de ações) fica fora do Git e se refaz com os scripts.

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
