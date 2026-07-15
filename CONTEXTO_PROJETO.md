# Contexto do projeto quant-futebol — resumo para Claude Code

## Arquitetura
- **quant-futebol-dados** (Supabase, projeto `cgurxgfdmpmsnrshqycx`, região sa-east-1): banco de dados e pipelines de ingestão
- **quant-predictor** (Vercel + GitHub `JBatistaCosta/quant-predictor`, projeto `prj_7fSfP9zv4i55F6qyWUvcv9SJueOk`): aplicação de predição

## Schema do banco (tabelas principais)
- `leagues`, `teams`, `matches`, `match_stats`, `match_events`, `odds_market`, `model_predictions`
- `team_strengths`, `league_model_params` — pesos treinados persistidos (ataque/defesa por time, mando/rho por liga)
- `odds_market.snapshot` — distingue `pre_closing` (capturado dias antes) de `closing` (fechamento real, coluna "C" do football-data.co.uk)
- View `v_market_edge` — compara odds do modelo com mercado (já devigado), separado por `snapshot`

## Modelo de produção
- **Dixon-Coles** (`modelo_dixon_coles.py`) — gols, mercados 1X2 e over/under 2.5
- Treino por liga (`TEMPORADAS_TREINO_POR_LIGA`): Brasileirão = 2023-2024; as 5 europeias (PL/PD/SA/BL1/FL1) = 2022-2024 (2022 melhorou 3 de 5 ligas, validado com log loss real em 2025)
- Teste sempre em 2025
- Decaimento temporal: XI=0.0018 (meia-vida ~13 meses) — **fixo, nunca foi calibrado por validação cruzada de verdade**, é um chute inicial razoável
- `modelo_dixon_coles_walkforward.py` — versão com retreino periódico dentro de 2025. Resultado vs. estático: **empate técnico** (BSA/PD/SA melhoram, PL/BL1/FL1 pioram) — sem vencedor claro, não decidido qual usar em produção
- `modelo_stats_esperadas.py` — GLM Poisson pra xG/chutes/chutes-no-gol/escanteios, deriva mercados extras do mesmo jeito (over/under por estatística)

## Achados importantes (testados, não repetir investigação)
1. **Mando por time**: testado com 6 temporadas reais (2019-2024, 5 ligas europeias) — **descartado**. Heterogeneidade que parecia real com pouco dado (Ligue 1 se destacando) era majoritariamente ruído de amostra pequena; com mais dado, virou uma das ligas MAIS homogêneas. Mando único por liga é adequado. Detalhes em `ideias_futuras.md`.
2. **Filtro bayesiano dinâmico (EKF) pra força dos times**: tentado, **não passou em validação** — bug de deriva de gauge parcialmente corrigido, mas erro residual não resolvido (provavelmente precisa de IEKF, linearização iterada). Pausado, não descartado — candidato a retomar comparando com walk-forward.
3. **Backtest de edge vs. mercado real (1X2, O/U gols)**: **sem evidência de vantagem** contra Bet365, Pinnacle ou média de mercado — nem em odds pré-fechamento nem em fechamento real. Brier Score do modelo é sistematicamente pior que o do mercado (diferença pequena mas consistente, ~0,005-0,009). Teste de Closing Line Value (correlação entre edge do modelo e movimento de linha) também negativo — modelo não antecipa movimento de mercado. Relatório completo em `relatorio_backtest_edge.md`.
4. **Escanteios — Brier Score revela overconfidence**: em 2 das 5 ligas (Bundesliga, La Liga) o Brier é PIOR que "chutar 50% sempre" (>0,25). Diagnóstico: calibração boa no meio da distribuição (35-55% previsto), mas excesso de confiança nas pontas (73% previsto → só 52,5% real, com 80 jogos de amostra, não é ruído).
5. **Causa raiz do overconfidence em escanteios — CONFIRMADA, NÃO IMPLEMENTADA AINDA**: escanteios são superdispersos (variância/média = 1,60-1,76 em todas as ligas) enquanto gols não são (1,04-1,15, Poisson serve bem). O modelo de escanteios usa Poisson (mesma lógica do Dixon-Coles aplicada por estatística, sem covariância entre elas) — **deveria ser binomial negativa**. NÃO IMPLEMENTADO ainda, só diagnosticado.

## PRÓXIMOS PASSOS PENDENTES (ordem sugerida)
1. ~~**Trocar Poisson por binomial negativa no modelo de escanteios**~~ — **feito no lado do produto** (não no `modelo_stats_esperadas.py` em si, que continua Poisson): `api/corners-model.js` (Vercel) trata o TOTAL de escanteios do jogo como uma única Binomial Negativa, com `disp_r` calibrado por liga via método dos momentos direto em `match_stats` (16k+ linhas já no Supabase — não precisou esperar o script Python rodar de novo). Parâmetros salvos em `league_model_params` (stat='corners', param_name='disp_r'): Premier League 88.2, La Liga 62.1, Serie A (Itália) 40.1, Bundesliga 68.1, Ligue 1 57.9 — fallback 63.3 (média) pra ligas sem dado calibrado (Brasileirão, Champions, Eurocopa). `AnaliseEvento.jsx` chama esse endpoint automaticamente ao trocar os times e pré-popula `cornersModel='negbin'` + `cornersDisp`. Ainda vale rodar a validação com dado sintético e migrar o próprio GLM pra NB quando o pipeline Python for retreinado — o que está em produção agora é um fit direto na variância real, não uma reestimação do modelo GLM inteiro.
2. **Testar pareamento de escanteios com chutes/xG como covariável** — hoje cada estatística (xg, shots, shots_on_target, corners) é modelada isolada; escanteios têm ligação mecânica com chutes bloqueados e pressão ofensiva que está sendo ignorada.
3. Recalibrar XI (decaimento temporal) por validação cruzada temporal — nunca foi feito, valor atual é chute.
4. Decidir entre modelo estático vs. walk-forward por liga (resultado empatado, não decidido).
5. Considerar fonte paga (TheStatsAPI) ou OddsPapi (250 créditos/mês, só viável como coleta gradual daqui pra frente) se quiser odds reais de escanteios.
6. Fadiga/dias de descanso: precisa de nova tabela de fixtures (todas competições, não só liga doméstica) — não iniciado, só documentado.

## Scripts existentes (pasta local do usuário)
`ingestao_api_football.py`, `ingestao_football_data_org.py`, `ingestao_stats_fbref.py`, `backfill_xg_understat.py`, `ingestao_odds_footballdata.py`, `ingestao_historico_ligas.py` (2019-2022, 5 ligas europeias), `ingestao_escanteios_footballdata.py` (backfill de escanteios/chutes/cartões via football-data.co.uk), `ingestao_odds_fechamento.py` (odds de fechamento real, colunas "C"), `modelo_dixon_coles.py`, `modelo_dixon_coles_walkforward.py`, `modelo_stats_esperadas.py`.

## Documentação existente
`modelo_predicao_documentacao.md`, `ideias_futuras.md` (mando por time descartado, fadiga documentada, escanteios com achados parciais), `relatorio_backtest_edge.md`, `forca_dinamica_desenho.md` (filtro bayesiano pausado).

## Front-end (quant-predictor) — dois schemas de "times" coexistindo no mesmo banco
- `equipes`/`instituicoes` (RLS autenticado, CRUD manual pela tela Times.jsx) — cadastro feito à mão pelo usuário, 48 registros.
- `teams`/`leagues`/`matches`/`team_strengths` (RLS leitura pública, escrita só via service_role do pipeline Python) — 221 times, 14k+ partidas reais ingeridas.
- Essas duas tabelas NÃO tinham nenhuma referência cruzada. Adicionada coluna `equipes.pipeline_team_id` (FK pra `teams.id`, nula até vincular manualmente) — a tela Times.jsx agora tem um botão "Vincular" por equipe que busca em `teams` e grava o vínculo; `TimeDetalhe.jsx` mostra `team_strengths` (ataque/defesa treinados) e médias reais de `match_stats` quando o vínculo existe. `team_strengths` está **vazia** até o pipeline Python persistir os pesos treinados lá — até isso acontecer, a seção mostra "ainda não treinado".
- **RLS estava desabilitado em 11 tabelas do pipeline** (`teams`, `leagues`, `matches`, `match_stats`, `odds_market`, `model_predictions`, `model_stat_estimates`, `team_strengths`, `league_model_params`, `match_events`, `team_source_ids`) — corrigido: RLS habilitado + policy de leitura pública (dado público de futebol, sem PII; escrita continua exclusiva do service_role key usado pelos scripts Python).
- **`equipes.pipeline_team_id` foi populado em massa**: as ~13 seleções cujo nome batia exato com `teams.name` foram vinculadas automaticamente; os 196 clubes de `teams` (`is_national_team=false`) ganharam uma equipe/instituição nova cada (tipo='clube'), já vinculada — total foi de 48 pra 244 equipes, 209 com vínculo. Registrado em `importacoes` pra rastreabilidade.
- **`ligas` ganhou 8 linhas espelhadas de `leagues`** (Premier League, La Liga, Serie A, Bundesliga, Ligue 1, Brasileirão, Champions League, Eurocopa), com `ligas.external_id` (nova coluna) guardando o código da football-data.org (`PL`, `BSA` etc) pra re-sincronizar sem duplicar depois.
- **Casamento de times na importação de jogos (`ImportarJogos.jsx`/`api/fixtures.js`) agora tenta por ID externo antes de por nome**: `teams.external_id` é o ID numérico da football-data.org (confirmado batendo com o ID real de times como Palmeiras=1769) — quando a fonte usada é football-data.org, `api/fixtures.js` devolve esse ID em cada jogo (`mandante_external_id`/`visitante_external_id`) e o front cruza com `teams.external_id` → `equipes.pipeline_team_id` antes de cair no casamento por nome de string (frágil: "Brazil" da API não batia com "Brasil" cadastrado). IDs da API-Football (fonte 1, tentada primeiro) continuam sem ponte salva — `team_source_ids` só tem IDs do fbref, não da API-Football — então esses jogos ainda caem no fallback por nome.

## Ambiente
- Supabase: `cgurxgfdmpmsnrshqycx`, service_role key trocada em algum momento por exposição prévia — confirmar qual está em uso
- football-data.org token: `eff3d4a516b74d96a357738d6e2a987f` (⚠️ foi exposto em screenshot antes, considerar trocar)
- Pasta local: `C:\Users\jbati\AntiGravity\quant-pred\quant-predictor\arquivos_do_claude\` (mudou de path em algum momento da conversa, confirmar qual é atual)
