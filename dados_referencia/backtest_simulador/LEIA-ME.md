# Entrada do backtest do simulador (Premier League)

`premier_league_2024_25.csv`: 760 jogos finalizados da Premier League (temporadas `2024` = aquecimento da força dos times, `2025` = teste), extraídos do Supabase em 2026-10-04.

Colunas: `id, season, date, home, away, hg, ag` (ids e gols), `hs, as` (chutes totais FotMob), `hxg, axg` (xG FotMob), `hc, ac` (escanteios), `dc_h, dc_d, dc_a, dc_over` (probabilidades do `dixon_coles_v1` em `model_predictions`, vazio quando não houve previsão), `o_h, o_d, o_a, o_over, o_under` (média entre casas das odds de fechamento OddsPapi, vazio quando não há).

Usado por `scripts/backtest_simulador_preditivo.py` (workflow `backtest_simulador_preditivo.yml`). Nenhuma coluna é segredo.
