# FotMob: Eurocopa e Copa América de seleções (versionado, fracionado)

Baixado em 04/10/2026 da API interna do FotMob (`www.fotmob.com/api/data/fixtures` e `matchDetails`) por `scripts/baixar_fotmob_torneios.py`. API não oficial, sem contrato; o dado é público no site.
Cada `parte-NNN.json.xz` guarda até 25 jogos: `{fotmob_match_id, fixture, matchDetails}`. `matchDetails` tem `general`, `header`, `content` (eventos em `matchFacts`, estatísticas do jogo em
`stats`, estatísticas e notas por jogador em `playerStats`, `shotmap` com xG/xGOT e posição de cada chute, `lineup`, `momentum`, `h2h`) e foi enxugado das chaves `nav`, `seo`, `ongoing` e `hasPendingVAR`.

| torneio | temporadas (jogos encerrados) |
|---|---|
| `euro` (FotMob 50) | 2024 (51), 2020 = disputada em 2021 (51), 2016 (51), 2012 (31) |
| `copa_america` (FotMob 44) | 2024 (32), 2021 (28), 2019 (26), 2016 (34), 2015 (26) |
| `copa_mundo` (FotMob 77) | 2022 (64), 2018 (64) |

Ler sem rede: `baixar_fotmob_torneios.carregar('dados_referencia/fotmob', 'euro', '2024')`. Refazer: `python scripts/baixar_fotmob_torneios.py` (retoma pelo cache em `/tmp`, ~12 min).
Mapa de chutes com xG: só Euro 2020 e 2024, Copa América 2024 e Copa do Mundo 2022 (as demais edições, inclusive a Copa de 2018, vêm sem `shotmap` e sem xG de time). Cruzamento com o StatsBomb (Euro 2020 e 2024, Copa América 2024 e Copas de 2018 e 2022, 262 jogos): `scripts/cruzar_fotmob_statsbomb_torneios.py`, saída em `cruzamento_statsbomb.json`.

**Isto NÃO está no banco.** A carga no Supabase usa `arquivos_do_claude/ingestao_fotmob.py`, que exige `SUPABASE_URL` e `SUPABASE_KEY` (service_role) e o crosswalk de times em `team_source_ids`; essas seleções ainda não têm linha em `teams`/`leagues`.
