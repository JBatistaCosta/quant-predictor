---
name: workflow-quant-predictor
description: Convenções operacionais deste repo (quant-predictor) — fluxo de PR/deploy/merge, limite de funções serverless do Vercel Hobby, testagem em produção, e disciplina de cota com APIs externas pagas/limitadas. Use sempre que for implementar, testar ou publicar mudanças neste projeto.
---

# Workflow do quant-predictor

Convenções estabelecidas ao longo de várias sessões de trabalho neste repo (frontend React/Vite + `api/*.js` serverless no Vercel + Supabase Postgres). Siga isso por padrão, não repita descobertas já feitas.

## Antes de tudo: leia `CONTEXTO_PROJETO.md`
Esse arquivo é a fonte de verdade do projeto — arquitetura, achados testados (não repetir investigação), bugs já corrigidos e pendências. **Sempre leia inteiro no início da sessão.** A seção `⏸️ PENDÊNCIA IMEDIATA` no topo (se existir) é o ponto de retomada mais recente.

## Fluxo de PR (repetir pra cada mudança)
1. `git fetch origin main && git rebase origin/main` antes de commitar (branch de trabalho: `claude/project-analysis-3rre0m`). Squash-merges anteriores geram "warning: skipped previously applied commit" no rebase seguinte — **isso é esperado, não é erro**.
2. Commitar, rebasear de novo, `git push --force-with-lease -u origin <branch>`.
3. `mcp__github__create_pull_request` (draft: true) — se já existir PR aberto pra branch, empilhar commit nele em vez de criar outro.
4. Esperar o deploy da preview (`mcp__github__pull_request_read` method=get_status, ou `mcp__Vercel__get_deployment`) — **nunca usar `sleep` fixo em loop**, usar Monitor ou Bash com `run_in_background` e polling condicional.
5. `mcp__github__update_pull_request` (draft: false) → `mcp__github__merge_pull_request` (squash).
6. Esperar produção assumir o novo deploy (confirmar via `mcp__Vercel__get_deployment` no domínio de produção, olhando `githubCommitSha` — **não confiar só em "http 200", isso pode ser o deploy antigo ainda no ar**).
7. Testar em produção de verdade (nunca preview — preview tem SSO do Vercel que bloqueia curl com redirect 302).

## Limite de 12 Serverless Functions (plano Hobby)
Cada arquivo em `api/*.js` conta como 1 função. **Antes de criar um arquivo novo, rodar `ls api/*.js | wc -l` — se já estiver em 12, fundir com um endpoint administrativo existente** (`api/model-maintenance.js` é o lugar certo pra isso: dispatch por `?tarefa=X`, endpoints que só são chamados manualmente/por cron, nunca pelo frontend). Sintoma do erro: deploy falha com `exceeded_serverless_functions_per_deployment`.

## Paginação do Supabase/PostgREST
`.select()` sem `.range()` explícito corta em 1000 linhas **silenciosamente**, sem erro — e sem `ORDER BY`, as primeiras 1000 tendem a ser as mais antigas (ordem de inserção). Isso já causou bugs reais (temporadas recentes sumindo de seletores, estatísticas de modelo truncadas). **Qualquer tabela que pode passar de 1000 linhas precisa de paginação de verdade** (loop com `.range(pagina*1000, pagina*1000+999)` até a página vir incompleta) — não confiar em "parece que veio tudo".

## Testando o frontend
Não há acesso a browser autenticado nesta sessão (login do Supabase Auth bloqueia automação). Validação de features de frontend é feita **via query direta no Supabase** (MCP `execute_sql`) simulando a mesma lógica da UI, ou via `curl` contra a API REST do Supabase (anon key, disponível via `mcp__Supabase__get_publishable_keys`) reproduzindo exatamente a query que o componente faz — inclusive sintaxes menos óbvias tipo `.or(and(...),and(...))` foram validadas assim antes de subir.

## Disciplina de cota com APIs externas pagas/limitadas
Este projeto integra APIs de terceiros com cota mensal apertada (ex.: OddsPapi, 250 req/mês grátis). Regras:
- **Nunca adivinhar o formato de resposta de uma API paga/limitada e escrever o parser "às cegas"** — gastar 1-2 chamadas de descoberta primeiro, inspecionar o JSON real, cachear em tabela própria (`oddspapi_cache` é o padrão aqui) pra nunca precisar rechamar.
- **Documentação pública de APIs de terceiros pode estar errada ou parafraseada** (já aconteceu: parâmetro documentado como lista por vírgula que na prática só aceita 1 valor). Testar em produção com uma chamada mínima antes de generalizar.
- **Nunca escrever em tabelas de mapeamento crítico (ex.: liga→torneio externo) via heurística automática sem supervisão** — o custo de um mapeamento errado é alto (corrompe todo sync futuro). Preferir resolver manualmente por inspeção do cache (sem custo de cota) e só reportar sugestões da heurística pra confirmação humana.
- Ao propor automação via cron pra um endpoint que consome cota externa, **calcular e documentar o consumo mensal esperado no pior caso** antes de ativar — não presumir que "vai dar certo".

## Análise estatística
Este é um app de análise quantitativa de apostas esportivas. Ao lidar com métricas de modelo (log-loss, Brier Score, edge, ROI simulado): **nunca tratar log-loss/edge médio como prova de vantagem real** — a distinção entre "edge de verdade" e "ruído de amostra pequena" importa (ver `api/backtest-betting.js`, que usa bootstrap pra IC 95% do ROI antes de declarar `significativo`). Um edge isolado numa seleção só, dentro de um modelo pior que o mercado no agregado, é o padrão clássico de overfitting/ruído — desconfiar.
