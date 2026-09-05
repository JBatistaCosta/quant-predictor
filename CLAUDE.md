# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Visão geral

Quant System Predictor — app de análise quantitativa de apostas esportivas (futebol): modelos Dixon-Coles/Poisson/binomial negativa para 1X2, over/under de gols e escanteios, Elo interno, calibração de probabilidade, comparação com odds reais de mercado e backtest de apostas simuladas. OCR (Claude Vision / Gemini) para ler estatísticas/odds de prints de tela.

Stack: **frontend** React 18 + Vite + React Router + Tailwind, publicado no Vercel; **backend** funções serverless Node em `api/*.js` (mesmo projeto Vercel); **banco** Supabase Postgres (projeto `cgurxgfdmpmsnrshqycx`) com RLS.

**Leia `CONTEXTO_PROJETO.md` inteiro no início de qualquer sessão de trabalho** — é a fonte de verdade viva do projeto (arquitetura em mais detalhe, achados já testados, bugs corrigidos, pendências). A seção `⏸️ PENDÊNCIA IMEDIATA` no topo é o ponto de retomada mais recente. Este arquivo (`CLAUDE.md`) cobre estrutura de código e convenções estáveis; `CONTEXTO_PROJETO.md` cobre o estado do projeto, que muda a cada sessão.

Convenções operacionais de fluxo de trabalho (PR/deploy, disciplina de cota com APIs pagas, limite de funções serverless, testagem em produção) estão na skill `workflow-quant-predictor` (`.claude/skills/workflow-quant-predictor/SKILL.md`) — os pontos mais críticos também estão resumidos abaixo.

## Comandos

```
npm install       # instala dependências
npm run dev       # servidor de dev (Vite), http://localhost:5173
npm run build     # build de produção
npm run preview   # serve o build local
```

Não há suíte de testes nem linter configurados neste projeto. `vercel dev` roda o frontend junto com as funções de `api/*.js` localmente, se precisar testar uma function sem publicar.

## Arquitetura

### Frontend (`src/`)
- `main.jsx` → `App.jsx`: define as rotas via React Router, todas com `React.lazy` (code splitting por página) exceto `/login` e `/cadastro`. Todo o resto é protegido por `ProtectedRoute` (checa `AuthContext`) e envolvido em `Layout`.
- `AuthContext.jsx` / `supabaseClient.js`: sessão de autenticação via Supabase Auth. O client Supabase usa a chave **pública** (anon/publishable) — controle de acesso é via RLS nas tabelas, não pela chave. Se `VITE_SUPABASE_URL`/`VITE_SUPABASE_KEY` não estiverem setadas, o app roda mas sem persistência (fallback manual).
- `pages/`: uma página por rota. Principais: `AnaliseEvento.jsx` (calculadora do modelo preditivo — Poisson/Dixon-Coles, Monte Carlo, Kelly, OCR), `AnaliseHistorica.jsx` (forma recente + H2H, painel leve sem rodar modelo), `AnaliseEstatisticaJogo.jsx` (estatísticas para apostas), `ModelosStats.jsx` (log-loss/Brier/calibração/backtest), `LigaDetalhe.jsx`/`TimeDetalhe.jsx`/`Ligas.jsx`/`Times.jsx` (navegação do pipeline de dados reais), `Jogadores.jsx`, `RatingClubes.jsx` (Elo), `EventosLista.jsx`/`EventoNovo.jsx`/`ImportarJogos.jsx` (cadastro manual legado).
- `utils/`: `poisson.js`/`distributions.js` (distribuições de probabilidade), `lambdaFormulas.js` (cálculo de força ofensiva/defensiva), `ocr.js` (chamada ao endpoint `/api/ocr`), `format.js`.
- `data/selecoes.js`: Elo hardcoded de seleções nacionais (não vem do banco — Elo de clube é outro sistema, ver abaixo).

### Backend serverless (`api/*.js`)
Cada arquivo é uma Vercel Function independente. Cada um documenta no cabeçalho quais variáveis de ambiente usa — confira antes de mudar. Resumo:
- `fixtures.js` — jogos de um time, cascata de 3 fontes (API-Football → football-data.org → fallback).
- `leagues-search.js` — busca ligas por nome na API-Football (para cadastro).
- `team-stats.js` — estatísticas agregadas de time via API-Football.
- `corners-model.js` — previsão de escanteios (binomial negativa, `disp_r` calibrado por liga).
- `match-odds.js` — odds via the-odds-api.com.
- `ocr.js` — leitura de imagem (prints de estatística/odds) via Claude Vision ou Gemini, escolhido pela env var disponível.
- `sync-matches.js` — cron diário (06:00 UTC); mantém `matches` atualizado (placar + jogos futuros) para todas as ligas do pipeline via football-data.org; cria times novos automaticamente por `external_id`.
- `sync-match-stats.js` — chamado manualmente; completa escanteios/xG/chutes via API-Football para jogos que `sync-matches.js` já trouxe (casamento por data + nome de time, tolerante a ambiguidade).
- `sync-clubelo.js` — importa histórico de rating do ClubElo.com como semente do Elo interno (só cobre clubes europeus).
- `model-maintenance.js` — **endpoint administrativo único** (dispatch por `?tarefa=`) que concentra várias tarefas de manutenção (recompute de Elo, calibração Platt/Isotonic, sync de odds via OddsPapi, backfill de competições novas). Existe especificamente para não estourar o limite de 12 functions — ver convenção abaixo.
- `model-stats.js` — painel de métricas dos modelos (log-loss, Brier, acurácia, calibração) comparado contra odds de fechamento do mercado.
- `backtest-betting.js` — simula apostas reais (flat ou Kelly fracionário) por edge modelo-vs-mercado, com IC 95% via bootstrap para decidir "EV+ estatisticamente sustentado".
- `_lib/negbin.js` — utilitário compartilhado de binomial negativa.

### Banco de dados (Supabase Postgres)
Duas famílias de tabelas que **coexistem no mesmo banco, com propósitos diferentes**:
- `equipes`/`instituicoes`/`eventos` — cadastro manual feito pelo usuário na UI (RLS autenticado, CRUD normal). Uso legado/complementar.
- `teams`/`leagues`/`matches`/`match_stats`/`odds_market`/`model_predictions`/`team_strengths`/`league_model_params`/`team_elo`/`team_elo_history`/`players`/`match_formation_fotmob`/`match_goal_timeline`/`match_team_game_state`/`match_team_event_response` e as demais tabelas `*_fotmob` — pipeline de dados reais ingerido automaticamente (RLS leitura pública, escrita só via `SUPABASE_SERVICE_ROLE_KEY`, nunca pela chave anon do frontend).

### Esquema tático (`match_formation_fotmob`)
A formação de cada time em cada partida ("4-2-3-1", "3-5-2") **não vem pronta de nenhuma fonte** — é derivada da grade de posições dos 11 titulares que o FotMob usa pra desenhar o campinho (`verticalLayout.y`, guardado em `match_lineup_fotmob.raw`/`field_pos_y`). A regra "grade → formação" mora **só** na função SQL `derivar_formacoes_fotmob(p_match_ids)` (migration `20260903120000_create_match_formation_fotmob.sql`); os dois caminhos de ingestão de escalação (`api/model-maintenance.js` e `scripts/ingerir_escalacao_pre_jogo.py`) chamam essa RPC depois do upsert em vez de reimplementar a lógica. `?tarefa=derivar-formacoes` em `model-maintenance.js` é a rede de segurança (janela de dias, escopo limitado — o backfill histórico completo é operação de migration, estoura o timeout da function). A view `v_confronto_formacoes` dá uma linha por partida com os dois lados e os saldos setoriais.

Duas ressalvas que o código documenta e que não devem ser reintroduzidas como "sinal novo": (1) a grade do FotMob é **esquemática**, então métricas geométricas dela (altura do bloco, largura) são função determinística da própria formação, não medida de comportamento; (2) a formação é a estrutura de **entrada** em campo — não captura mudanças ao longo do jogo. `match_lineup_fotmob.formation` é coluna morta (nunca preenchida): use a tabela nova.

### Estado do jogo (`match_goal_timeline` / `match_team_game_state`)
Fase 2 da mesma frente. `match_shots_fotmob` (477 mil chutes, todos com minuto e xG) permite reconstruir o placar a cada instante, e daí quanto tempo cada time passou **perdendo/empatando/ganhando** e o que criou e sofreu nesse tempo. Regra derivada só em `derivar_game_state(p_match_ids)` (migration `20260904100000_create_game_state.sql`), chamada por RPC pelo ingestor depois do upsert do shotmap; `?tarefa=derivar-game-state` é a rede de segurança (backfill completo é operação de migration).

Três coisas que **precisam** ser respeitadas por quem usar essas tabelas:
- **Sempre normalize por `minutos`.** Somar `xg_pro` por estado mede quanto tempo o time passou naquela situação, não como ele joga — é exatamente o viés que a fase 2 existe pra remover. A view `v_time_game_state` já entrega tudo por 90 minutos naquele estado.
- **Gol contra: `match_shots_fotmob.team_id` é quem CHUTOU, não quem foi beneficiado** — o gol conta pro adversário. Ler errado derruba a reconstrução do placar de 99,8% para 91,5%. `match_goal_timeline.para_casa` já resolve isso.
- **`period='PenaltyShootout'` não conta pro placar** (454 "gols" de disputa) e **filtre por `placar_confere`** antes de treinar qualquer coisa.

### Resposta a eventos (`match_team_event_response`)
Fase 3. Mede o **transiente**: o que muda nos 5 e 15 minutos após um gol ou expulsão, *acima* da mudança de estado que o próprio evento causou. Derivada só em `derivar_resposta_evento(p_match_ids)` (migration `20260905100000_create_resposta_evento.sql`), chamada por RPC pelo ingestor depois de `derivar_game_state`; `?tarefa=derivar-resposta-evento` é a rede de segurança.

- **`estado` faz parte da chave, e não é opcional.** Comparar "após sofrer gol" com o jogo inteiro só remede a mudança de placar. A comparação válida é contra a linha `(evento='nenhum', janela='regime')` do **mesmo estado** — a view `v_resposta_evento` existe para isso.
- Cada instante pertence ao evento **mais recente** do time (janelas nunca se sobrepõem), e um chute no minuto exato do corte cai no intervalo que *termina* ali — senão o gol entraria como resposta à janela que ele mesmo abriu.
- A derivação é a mais pesada das três: no backfill só coube em lotes de ~500 partidas por transação. O endpoint usa teto de 150.
- Invariante que vale reconferir após qualquer mudança: somar `minutos`/`chutes_pro`/`xg_pro` por (partida, time) tem de bater **exatamente** com `match_team_game_state` — hoje bate em 37.540 de 37.540 pares.

`match_events` **não** é uma tabela de eventos gerais: só tem cartões (amarelo/vermelho/segundo amarelo), sem gols e sem substituições. Gols vêm do shotmap.

`equipes.pipeline_team_id` faz a ponte entre as duas famílias quando existe vínculo confirmado. Múltiplas tabelas de crosswalk (`team_source_ids`, `match_source_ids`, `liga_fonte_externa`) mapeiam os IDs internos para os de cada fonte externa (fbref, understat, API-Football, FotMob, football-data.org, OddsPapi, football-data.co.uk) — **nunca resolver esses mapeamentos por heurística de nome sem supervisão manual**: um mapeamento errado corrompe todo sync futuro (já aconteceu, ver `CONTEXTO_PROJETO.md`).

### Modelo de predição
- Dixon-Coles (gols, 1X2 e over/under) e GLM de estatísticas esperadas (xG/chutes/escanteios) são treinados em Python (scripts em `arquivos_do_claude/`, fora do deploy) e persistidos em `team_strengths`/`league_model_params`/`model_predictions`.
- Escanteios em produção usam binomial negativa (`api/corners-model.js`), não o GLM Poisson do script Python — calibrada direto em cima de `match_stats` via método dos momentos.
- Calibração de probabilidade (Platt Scaling / Isotonic Regression) é calculada e persistida em `model_calibration`, mas **não é aplicada automaticamente em nenhuma predição de produção** — só exibida lado a lado no painel `/modelos` para decisão manual.
- Antes de tratar qualquer edge/log-loss como "o modelo bate o mercado", ver o método de validação em `api/backtest-betting.js` (IC 95% via bootstrap) — edge médio isolado sem esse IC é o padrão clássico de ruído de amostra pequena neste projeto.
- Chutes/gols por jogador (`scripts/treinar_modelo_jogador_mercados.py`) é um regressor Poisson (CatBoost/LightGBM) por jogador, restrito às 6 ligas de `dados_historicos.LIGAS_MODEL_BENCHMARKING` (cobertura confiável de `match_shots_fotmob`). Gols não tem modelo de ML próprio no caminho principal — deriva por afinamento de Poisson (`lambda_gols = lambda_chutes × taxa_conversao_bayesiana`) em `scripts/rodar_jogador_mercados_previsto.py`, com um regressor direto como candidato alternativo avaliado no walk-forward (`scripts/backtest_jogador_mercados_walkforward.py`). Persiste em `player_match_estimates`, com `fonte_titular` distinguindo a previsão feita com o XI previsto (`xi_previsto`) da feita com a escalação oficial confirmada (`match_lineup_fotmob`) — as duas coexistem, nunca uma sobrescreve a outra.

### Scripts locais (`arquivos_do_claude/`)
Scripts Python de ingestão/treino que rodam fora do Vercel (não fazem parte do deploy): `ingestao_*.py` (uma fonte externa cada — API-Football, football-data.org, fbref, understat, football-data.co.uk, FotMob), `backfill_xg*.py`, `modelo_dixon_coles*.py`, `modelo_stats_esperadas*.py`. Ao criar um script novo aqui, exigir as credenciais via variável de ambiente **sem valor default hardcoded** (vários scripts antigos têm a `service_role key` do Supabase hardcoded como fallback — problema de segurança conhecido, documentado e deliberadamente adiado em `CONTEXTO_PROJETO.md`; não repita o padrão em código novo).

## Convenções críticas

- **Limite de 12 Serverless Functions (plano Vercel Hobby)**: cada arquivo em `api/*.js` conta como uma function. Rode `ls api/*.js | wc -l` antes de criar um arquivo novo; se já estiver em 12, funda a tarefa nova em `api/model-maintenance.js` (dispatch por `?tarefa=`), que existe justamente para isso — nunca colocar lá algo que o frontend chama diretamente. Sintoma do erro: deploy falha com `exceeded_serverless_functions_per_deployment`.
- **Paginação do Supabase/PostgREST**: `.select()` sem `.range()` explícito corta em 1000 linhas silenciosamente, sem erro — e sem `ORDER BY`, as primeiras 1000 tendem a ser as mais antigas (ordem de inserção). Qualquer tabela que pode passar de 1000 linhas precisa de paginação de verdade (loop de `.range()` até vir página incompleta). Já causou bugs reais (dados recentes somem de seletores).
- **Testando o frontend**: não há acesso a browser autenticado em sessão automatizada (login via Supabase Auth bloqueia). Validar via query direta ao Supabase (MCP `execute_sql`) ou `curl` contra a API REST do Supabase (chave anon) reproduzindo a mesma query que o componente faz.
- **APIs externas pagas/com cota limitada** (API-Football, OddsPapi, the-odds-api etc.): nunca escrever um parser "às cegas" a partir da documentação pública — ela já se mostrou errada/parafraseada mais de uma vez neste projeto. Gastar 1-2 chamadas de descoberta, inspecionar o JSON real, cachear (padrão: tabela `*_cache`) antes de generalizar.

## Variáveis de ambiente

Definidas no Vercel (Settings → Environment Variables) e, para rodar localmente, em `.env` na raiz (nunca commitar):

| Variável | Uso |
|---|---|
| `VITE_SUPABASE_URL` / `VITE_SUPABASE_KEY` | client Supabase no frontend (chave pública/anon) |
| `SUPABASE_URL` / `SUPABASE_KEY` | leitura pública nas functions `api/*.js` |
| `SUPABASE_SERVICE_ROLE_KEY` | escrita no pipeline (`sync-matches.js`, `sync-match-stats.js`, `sync-clubelo.js`, `model-maintenance.js`) — bypassa RLS, nunca usar no frontend |
| `API_FOOTBALL_KEY` | API-Football |
| `FOOTBALL_DATA_KEY` | football-data.org |
| `ODDS_API_KEY` | the-odds-api.com |
| `ODDSPAPI_KEY` | OddsPapi |
| `THE_STATSAPI_KEY` | TheStatsAPI |
| `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` | OCR em `api/ocr.js` (usa a que estiver configurada) |
| `CRON_SECRET` | protege endpoints chamados pelo Vercel Cron |

## Fluxo de PR/deploy (resumo — detalhes completos na skill `workflow-quant-predictor`)

1. Rebase na branch de trabalho antes de commitar; commit; push com `--force-with-lease`.
2. Abrir PR como draft; esperar o deploy de preview.
3. Marcar PR como pronto e mergear (squash) só depois de confirmar o preview.
4. Confirmar que a produção assumiu o novo deploy pelo `githubCommitSha` (não só HTTP 200 — pode ser deploy antigo ainda no ar) antes de testar em produção. Preview tem SSO do Vercel e bloqueia `curl`/automação; testes reais são sempre contra produção.
