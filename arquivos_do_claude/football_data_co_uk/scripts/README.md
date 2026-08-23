# Orientações pra tratar/ingerir esses dados

Não tem script pronto pra cada uma das ~37 competições aqui — só os dois já usados em produção (`ingestao_odds_footballdata.py` pras 5 europeias, `ingestao_odds_footballdata_brasil.py` pro Brasileirão). Esse arquivo é o roteiro pra criar os próximos, seguindo exatamente a disciplina já estabelecida no projeto (ver `CONTEXTO_PROJETO.md`).

## Passo a passo obrigatório (nessa ordem, nunca pular)

### 1. A liga/competição já existe no banco?
```sql
select id, name from leagues where name ilike '%nome da liga%';
```
Se não existir, **pare aqui** — importar odds sem ter as partidas correspondentes não serve pra nada. Precisa primeiro importar `teams`/`matches` daquela competição (fora do escopo deste README).

### 2. Escolha o tipo de arquivo certo
- **`principais_ligas/{temporada}/{codigo}.csv`**: uma temporada por arquivo, tem `Date` real, tem odds de abertura E fechamento (colunas com e sem sufixo "C"), tem estatísticas de jogo. Casamento por DATA (±3 dias) + nome de time — mesmo padrão de `ingestao_odds_footballdata.py`.
- **`ligas_extra/{codigo}.csv`**: um arquivo com todas as temporadas (filtra por `Season`), tem `Date` real, só odds de fechamento. Casamento por DATA (±3 dias) + nome de time — mesmo padrão de `ingestao_odds_footballdata_brasil.py`.

### 3. NUNCA confie em matching de nome sem validar antes
Antes de escrever qualquer linha em `odds_market`, rode um script de **diagnóstico** (sem gravar nada) que:
1. Carrega as partidas da liga/temporada do banco (`id`, `match_date`, nomes dos times).
2. Carrega o CSV correspondente.
3. Casa por `(data ±3 dias, nome_casa, nome_visitante)` usando token-subset (nunca substring — risco de colisão tipo "Atalanta BC" batendo com "ABC", já corrigido uma vez nesse projeto).
4. Reporta: % de casamento, **divergência de placar entre CSV e banco nas partidas casadas** (se houver qualquer divergência, o matching está errado — pare e investigue antes de prosseguir), e a lista de nomes que não casaram.
5. Só depois de ver 100% de casamento (ou um resto pequeno e explicado — ex: temporadas que não existem no banco) é que escreve o script de ingestão de verdade.

Essa é exatamente a sequência seguida pro Brasileirão (ver commits/PRs de `ingestao_odds_footballdata_brasil.py`): validação manual primeiro, script de produção depois.

### 4. Nomes de time vão divergir — é normal, tem padrão
football-data.co.uk usa nomes curtos/apelidos, nunca o nome oficial completo. Já apareceram 3 categorias de divergência neste projeto:
- **Sufixo de estado/cidade**: "Botafogo RJ" (fonte) vs "Botafogo FR" (banco) — nosso token-subset sozinho não resolve, precisa de alias manual.
- **Abreviação sem raiz comum**: "Atletico-MG" vs "Clube Atlético Mineiro", "CSA" vs "CS Alagoano", "Athletico-PR" vs "Club Athletico Paranaense" — mesma classe já resolvida em `sync-match-stats.js` e nos dois scripts de odds do Brasileirão.
- **Time genuinamente ausente do banco**: se um nome não casa em NENHUMA tentativa (mesmo com alias), confira se aquele time/temporada realmente existe em `matches` antes de assumir que é bug de nome — várias vezes nesse projeto o "sem casamento" era simplesmente um time/temporada que nunca foi importado (não um problema de matching).

Todo alias descoberto vai num dicionário `ALIASES_MANUAIS` no topo do script (nunca heurística automática/fuzzy sem supervisão) — mesmo padrão de `sync-match-stats.js`, `ingestao_stats_fbref.py`, `ingestao_odds_footballdata.py` e `ingestao_odds_footballdata_brasil.py`.

### 5. Bookmakers e snapshot
- Colunas **sem** "C" no fim = pré-fechamento (`pre_closing`, é o default da coluna `snapshot` em `odds_market` — não precisa passar explícito).
- Colunas **com** "C" no fim = fechamento (`closing` — passar explícito, não confiar no default).
- `Max*`/`MaxC*` não é uma casa de apostas de verdade (é o maior valor entre várias casas) — não gravar como `bookmaker`, é enganoso.
- `Avg*`/`AvgC*` é a média de mercado — gravar como `bookmaker='media_mercado'` (convenção já usada em todo o projeto).
- As demais (B365, PS/Pinnacle, WH/William Hill, BFE/Betfair Exchange, BMGM/BetMGM etc.) são casas reais — gravar com o nome da casa.

### 6. Mercados disponíveis
- **1X2**: sempre presente (`H`/`D`/`A` no fim da coluna).
- **Over/Under 2.5 gols**: só em `principais_ligas` (`>2.5`/`<2.5`), ausente em `ligas_extra`.
- **Handicap Asiático**: presente em `principais_ligas`, não usado em nenhum script deste projeto ainda — mercado novo, avaliar se vale a pena antes de implementar.

### 7. Referência de código
Os dois scripts já existentes (`arquivos_do_claude/ingestao_odds_footballdata.py` e `ingestao_odds_footballdata_brasil.py`) são o template — copiar a estrutura (`normalizar()`, `match_times()`, `ALIASES_MANUAIS`, casamento por data com tolerância de 3 dias, upsert em lotes de 500) pra qualquer nova liga, só trocando os nomes de coluna/país.
