# 📚 Documentação de Mercados e Ingestão da OddsPapi

Este documento detalha o funcionamento do script de ingestão de odds históricas de fechamento da OddsPapi (`ingestao_odds_oddspapi_brasileirao.py`), o formato dos dados salvos no Supabase (`odds_market`) e a referência completa dos mercados de futebol suportados pela OddsPapi.

---

## 📌 1. Visão Geral do Pipeline de Ingestão

O script `ingestao_odds_oddspapi_brasileirao.py` captura odds de partidas finalizadas do Campeonato Brasileiro (Série A 2026) e as salva no banco de dados.

### Endpoints Utilizados:
1. **`GET /v4/fixtures`**:
   - `tournamentId=325` (Brasileiro Série A na OddsPapi).
   - `statusId=2` (Partidas Encerradas/Finished).
   - Retorna a lista de jogos finalizados no período.
2. **`GET /v4/historical-odds`**:
   - `fixtureId={id}&bookmakers=pinnacle,bet365,betano`.
   - Retorna todo o histórico de variação da linha (tick-by-tick) antes e durante o jogo.

### 🛡️ Filtro Crítico de Fechamento (Pre-Match Closing Line):
As odds capturadas ao vivo (in-play) ou após o apito inicial são descartadas. O script filtra apenas os pontos onde:
```python
createdAt <= startTime (horário de início do jogo)
```
E seleciona a última odd registrada antes do kickoff, salvando com `snapshot='closing'`.

### ⚡ Idempotência e Cota de API:
- Partidas processadas com sucesso são registradas na tabela `match_source_ids` (`source='oddspapi_historico'`).
- Execuções subsequentes ignoram jogos já ingeridos (a menos que a flag `--forcar` seja passada).
- É aplicado um cooldown padrão de **5,0 segundos** entre requisições para respeitar o rate-limit da OddsPapi.

---

## 🔐 2. Configuração de Chaves e Ambiente

O script carrega automaticamente as variáveis do arquivo `functions/.env` ou `.env` da raiz do projeto:

- `ODDSPAPI_KEY`: Chave de API da OddsPapi.
- `SUPABASE_URL`: URL do projeto Supabase (`https://cgurxgfdmpmsnrshqycx.supabase.co`).
- `SUPABASE_KEY` ou `SUPABASE_SERVICE_ROLE_KEY`: Chave com permissão de escrita.

---

## 🗄️ 3. Estrutura da Tabela `odds_market`

Cada linha inserida em `odds_market` possui a seguinte estrutura:

| Coluna | Tipo | Descrição | Exemplo |
| :--- | :--- | :--- | :--- |
| `match_id` | `bigint` | ID interno da partida na tabela `matches` | `37326` |
| `bookmaker` | `text` | Nome da casa de apostas | `'pinnacle'`, `'bet365'`, `'betano'` |
| `market` | `text` | Código do mercado no projeto | `'1X2'`, `'over_under_2.5'`, `'btts'` |
| `selection` | `text` | Seleção/Lado da aposta | `'home'`, `'over'`, `'yes'`, `'1-0'` |
| `odds` | `numeric` | Odd decimal registrada no fechamento | `1.95` |
| `snapshot` | `text` | Estágio de captura | `'closing'` |
| `captured_at` | `timestamp` | Horário ISO de execução do script | `2026-07-25T14:30:00Z` |

---

## 📑 4. Referência de Mercados Mapeados e Disponíveis

Abaixo está o mapeamento dos mercados integrados no script e os códigos equivalentes na OddsPapi (`marketName`):

### ⚽ A. Mercados Principais (Mapeados no Script)

| Mercado no Banco (`market`) | `marketName` na OddsPapi | Handicap | Seleções Mapeadas (`selection`) |
| :--- | :--- | :--- | :--- |
| **`1X2`** | `Full Time Result` | `0` / `None` | `home` (Casa), `draw` (Empate), `away` (Fora) |
| **`over_under_2.5`** | `Over Under Full Time` | `2.5` | `over` (Acima de 2.5), `under` (Abaixo de 2.5) |
| **`btts`** | `Both Teams To Score` | `-` | `yes` (Sim), `no` (Não) |
| **`correct_score`** | `Correct Score` | `-` | `1-0`, `2-1`, `0-0`, `1-1`, `0-2`, etc. |
| **`corners_over_under_9.5`** | `Corner Over Under` / `Corner Total` | `9.5` | `over` (Acima de 9.5), `under` (Abaixo de 9.5) |
| **`double_chance`** | `Double Chance` | `-` | `1x` (Casa ou Empate), `x2` (Empate ou Fora), `12` (Casa ou Fora) |

---

### 🏆 B. Outros Mercados Disponíveis na OddsPapi (Mapeáveis se Necessário)

#### 1. Linhas e Handicaps de Gols:
* **Empate Anula Aposta (Draw No Bet)**: `marketName = "Draw No Bet"` -> Seleções: `1`, `2`
* **Handicap Asiático**: `marketName = "Asian Handicap"` -> Handicaps: `-0.5`, `+0.5`, `-1.0`, `+1.0`
* **Handicap Europeu**: `marketName = "European Handicap"` -> Handicaps: `-1`, `+1`
* **Gols por Equipe**: `marketName = "Over Under Team 1"` e `"Over Under Team 2"` -> Seleções: `over`, `under`
* **Par ou Ímpar**: `marketName = "Odd Even Full Time"` -> Seleções: `odd`, `even`

#### 2. Metades e Tempos do Jogo:
* **Resultado 1º Tempo**: `marketName = "First Half Result"` -> Seleções: `home`, `draw`, `away`
* **Resultado 2º Tempo**: `marketName = "Second Half Result"` -> Seleções: `home`, `draw`, `away`
* **Intervalo / Final (HT/FT)**: `marketName = "Half Time / Full Time"` -> Seleções: `1/1`, `1/X`, `X/2`, etc.
* **Ambas Marcam no 1º Tempo**: `marketName = "First Period Both Teams To Score"` -> Seleções: `yes`, `no`

#### 3. Escanteios e Cartões:
* **Escanteios 1X2**: `marketName = "Corners - 1X2 Full Time"` -> Seleções: `home`, `draw`, `away`
* **Handicap de Escanteios**: `marketName = "Corners - Handicap"`
* **Escanteios 1º Tempo**: `marketName = "Corners - Over Under First Half"`
* **Jogador a Receber Cartão**: `marketName = "Player To Be Carded (incl. overtime)"` -> Seleções: `yes`

#### 4. Desempenho e Especiais de Equipes:
* **Sem Levar Gols (Clean Sheet)**: `marketName = "Team 1 Clean Sheet"` / `"Team 2 Clean Sheet"` -> Seleções: `yes`, `no`
* **Vencer Sem Levar Gols (Win To Nil)**: `marketName = "Team 1 Win To Nil"` / `"Team 2 Win To Nil"` -> Seleções: `yes`, `no`
* **Marcadores de Gol**: `marketName = "First Goal Scorer"` e `"Player Goals (incl. overtime)"`

---

## 💻 5. Instruções de Execução do Script

### A partir da pasta `arquivos_do_claude/`:
```bash
# Executar simulação sem salvar (Dry Run)
python ingestao_odds_oddspapi_brasileirao.py --dry-run --limite 5

# Executar ingestão real de 50 partidas
python ingestao_odds_oddspapi_brasileirao.py --limite 50
```

### A partir da raiz do projeto (`quant-predictor`):
```bash
python arquivos_do_claude/ingestao_odds_oddspapi_brasileirao.py --limite 50
```

### Opções da Linha de Comando:
- `--limite N`: Quantidade máxima de jogos a processar por chamada (padrão: `10`).
- `--cooldown N.N`: Intervalo de espera em segundos entre requisições (padrão: `5.0`).
- `--dry-run`: Exibe as odds extraídas no console sem gravar no banco de dados.
- `--forcar`: Ignora a checagem de partidas já processadas em `match_source_ids`.
- `--temporada AAAA`: Temporada alvo no banco (padrão: `2026`).
- `--liga-id N`: ID da liga no banco de dados (padrão: `1` para Brasileirão Série A).
