# Odds Kaggle (2015-2025)

Fonte: [`brunobchinaglia/bets-on-soccer-2015-2025`](https://www.kaggle.com/datasets/brunobchinaglia/bets-on-soccer-2015-2025) (dataset associado ao kernel `notebook-bets-no-futebol`). Licença CC-BY-NC-SA-4.0 (não-comercial, compartilhamento pela mesma licença) — compatível com este projeto particular.

15 arquivos, um por liga, odds 1X2 (`odd_casa`/`odd_empate`/`odd_fora`) + placar, ~10 temporadas cada (2015-2025).

**Colunas** (Brasileirão): `temporada, semana, time_casa, time_fora, gols_casa, gols_fora, odd_casa, odd_empate, odd_fora, vencedor, odd_vencedora`. Argentina tem uma coluna extra `temporada_dropdown`.

**Sem coluna de data** — só temporada + rodada (`semana`). Junção com `matches` precisa ser por `(temporada, time_casa, time_fora)` (chave única dentro de uma temporada de returno duplo, não precisa da rodada) + matching de nome por token-subset (mesmo padrão de `ingestao_stats_fbref.py`).

**Status de uso:**
- **Brasileirão: IMPORTADO** (`arquivos_do_claude/ingestao_odds_kaggle_brasileirao.py`, `bookmaker='kaggle_oddspedia'` em `odds_market`). Mapeamento validado manualmente antes de escrever: 2.274/2.274 (100%) das partidas de 2019-2024 casaram por `(temporada, time_casa, time_fora)`, com ZERO divergência de placar entre CSV e banco — confirma que o matching está correto. 2015-2018 (1.425 linhas) ficam de fora — Brasileirão só existe no banco a partir de 2019, não é bug de nome.
  - **3 times duplicados reais encontrados e corrigidos durante a validação** (mesma classe do bug de identidade corrigido no início da sessão): América-MG (id 807 → 3 "América FC"), Sport Recife (id 727 → 42 "SC Recife"), CSA (id 710 → 854 "CS Alagoano") — cada um tinha um segundo registro sem `external_id`, criado por um fluxo diferente (API-Football) do que populou o resto do Brasileirão (football-data.org), mesmo clube.
  - `ALIASES_MANUAIS` no script: `athletico pr→club athletico paranaense`, `atletico go→ac goianiense`, `america mg→america fc`, `red bull bragantino→rb bragantino`, `sport recife→sc recife`, `csa→cs alagoano`.
- **Demais ligas domésticas** (Argentina, Bélgica, Colômbia, Rússia, Uruguai, Venezuela, MLS): **não existem em `leagues`** — precisam ser importadas primeiro (times + partidas) antes de qualquer odds fazer sentido.
- **5 ligas europeias de elite** (Bundesliga, Serie A, La Liga, Ligue 1, Premier League) + Eredivisie + Liga Portugal: já têm odds via `ingestao_odds_footballdata.py` (football-data.co.uk, múltiplas casas, abertura+fechamento) — esses CSVs do Kaggle são só 1 odd por seleção, sem casa/timing identificados, então só valeriam como fallback/cobertura extra, não substituem a fonte atual.
