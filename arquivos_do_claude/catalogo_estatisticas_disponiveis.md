# Catálogo de estatísticas disponíveis pra features (conferência futura)

Levantamento feito em 27/07/2026, direto no banco (`cgurxgfdmpmsnrshqycx`), pra decidir quais estatísticas de jogo entram em novas camadas de features (V8+). Ponto de partida do pedido do usuário: "quero criar modelos que utilizem o mais estatísticas de jogos anteriores de FBref, FotMob etc". Cobre estatísticas de TIME (seções 1-5, 3 tabelas) e, a partir da seção 6, dados INDIVIDUAIS de jogadores (desempenho por partida + perfil/histórico) — incluindo recomendações (seção 7) pra um esforço de raspagem do FotMob em andamento em outro repositório da mesma conta.

**Importante**: todas as colunas aqui, se usadas, entram como **forma pré-jogo** (média móvel dos últimos N jogos, nunca o valor da própria partida — usar o valor bruto da partida que se quer prever é vazamento de dado, já que não dá pra saber posse/passes/desarmes antes do jogo acontecer). Mesmo padrão já usado em `_forma_por_mando`/`FEATURES_V7`.

---

## 1. FBref (`match_stats`) — 24.248 linhas

| Coluna | Em uso? | Linhas preenchidas |
|---|---|---|
| `xg` | Sim (v1+) | 17.874 |
| `possession`, `shots`, `shots_on_target`, `corners`, `fouls`, `yellow_cards`, `red_cards` | **Sim — V7 (PR #156)** | 22.018-24.168 |
| `ppda` (passes allowed per defensive action — proxy de intensidade de pressing) | **Não** | 10.508 |
| `np_xg` (xG sem pênalti) | **Não** | 10.510 |
| `expected_points` (pontos esperados, calculado a partir do xG do próprio jogo) | **Não** | 10.510 |
| `deep_completions` (passes completados próximo da área — proxy de criação) | **Não** | 10.510 |

### Cobertura de `ppda`/`np_xg`/`expected_points`/`deep_completions` por liga/temporada
Só nas 5 ligas de elite europeias, e só a partir de 2023 — 2022 e antes ficam sempre em 0 (não é bug, a fonte FBref não tinha essas colunas naquela época/formato de captura):

| Liga | Temporadas com dado | Temporadas SEM dado |
|---|---|---|
| Premier League | 2023, 2024, 2025 | 2022 |
| La Liga | 2023, 2024, 2025 | 2022 |
| Serie A (Itália) | 2023, 2024, 2025 | 2022 |
| Bundesliga | 2023 (306/306), 2024 (305/306), 2025 | 2022 |
| Ligue 1 | 2023, 2024, 2025 (305/306) | 2022 |

Nenhuma outra competição (Brasileirão, Libertadores, Championship, Eredivisie, Copa do Brasil, Champions League, Primeira Liga, Eurocopa, Copa do Mundo) tem essas 4 colunas preenchidas.

---

## 2. FotMob (`match_stats_fotmob`, nível de TIME) — 31.888 linhas

**Atualizado 27/07/2026**: as colunas com cobertura quase completa entraram na **V8** (PR #158) — só ficaram de fora `touches_opp_box` (cobertura irregular), `accurate_passes_total` (praticamente nunca populada) e `xg`/`xgot` (já usados/irregulares no FotMob).

| Grupo | Colunas | Em uso? |
|---|---|---|
| Posse | `possession` | **Sim — V8** |
| xG detalhado | `xg`, `xg_open_play`, `xg_set_play`, `xg_non_penalty` | Não (`xg` redundante com FBref, irregular em 2019-2022 no FotMob) |
| xGOT | `xgot` | Só como alvo de regressão |
| Finalização | `total_shots`, `shots_on_target`, `shots_off_target`, `shots_blocked`, `shots_inside_box`, `shots_outside_box` | **Sim — V8** |
| Chances claras | `big_chances`, `big_chances_missed` | **Sim — V8** |
| Chances claras (irregular) | `touches_opp_box` | Não (369/4460 em 2019, só 100% a partir de 2024) |
| Construção de jogo | `accurate_passes`, `accurate_long_balls`, `accurate_crosses` | **Sim — V8** |
| Construção de jogo (quebrada) | `accurate_passes_total` | Não (praticamente sempre NULL, bug de captura na fonte) |
| Defesa | `tackles`, `interceptions`, `blocks`, `clearances`, `keeper_saves` | **Sim — V8** |
| Duelos | `duels_won`, `aerial_duels_won`, `successful_dribbles` | **Sim — V8** |
| Disciplina | `fouls_committed`, `yellow_cards`, `red_cards`, `corners` | **Sim — V8** |

### Cobertura por liga/temporada (contagem de partidas com a linha; `com_xgot`/`com_tackles` como amostra — as demais ~20 colunas seguem o mesmo padrão de `tackles`, já que vêm do mesmo bloco de captura)

| Liga | Temporadas cobertas | Partidas com stats (aprox.) | Observação |
|---|---|---|---|
| Premier League | 2019-2025 | 380/temporada | `xgot` fraco em 2019 (3/380) e 2022 (372/380), completo 2020-2021/2023-2025 |
| La Liga | 2019-2025 | 379-380/temporada | `xgot` fraco em 2019 (2/380), completo nas demais |
| Serie A (Itália) | 2019-2025 | 379-380/temporada | `xgot` fraco em 2019 (2/380), completo 2020-2021/2024-2025 |
| Bundesliga | 2019-2025 | 306/temporada | `xgot` fraco só em 2019 (6/306) |
| Ligue 1 | 2019-2025 | 279-380/temporada | `xgot` fraco em 2019 (3/279, temporada encurtada pela Covid) |
| Brasileirão Série A | 2019-2026 | 177-380/temporada | `xgot` fraco em 2019-2022 (2-66/380), forte a partir de 2023 (349-377/380) |
| Copa Libertadores | 2019-2022, 2026 | 60-125/temporada | `xgot` bem irregular (1-125), `tackles`/demais quase sempre completo |
| Copa America | 2024 | 30 | Completo |

**Nota**: `tackles` (e o resto das ~20 colunas do bloco de finalização/construção/defesa/duelos) tem cobertura muito mais estável que `xgot` especificamente — `xgot` depende de uma métrica mais nova do FotMob que só ganhou cobertura completa a partir de ~2020, as demais colunas do bloco básico de estatísticas já vêm completas desde 2019 na maioria das ligas.

---

## 3. `match_features_contexto` — 37.782 linhas, 100% de cobertura em tudo, quase tudo parado

Hoje só `days_since_last_match`/`is_midweek_fatigue` viram feature (v3). Parados:

| Coluna | Descrição | Em uso? |
|---|---|---|
| `travel_distance_km` | Distância viajada até a partida (fadiga física, distinto de dias de descanso) | Não |
| `manager_match_count` + `is_honeymoon_phase` | Jogos do técnico atual no cargo + flag de "efeito lua de mel" (troca recente de comando) | Não |
| `key_players_missing_torneo` | Desfalques de jogadores-chave (além do que já é capturado por cartão/suspensão) | Não |
| `squad_natl_matches_window` | Jogos de seleção recentes do elenco (desgaste de convocação) | Não |

### Cobertura por liga/temporada
100% de cobertura em todas as ligas/temporadas onde a tabela tem linha — a tabela em si cobre as mesmas 14 competições já vistas em `match_stats`/`match_stats_fotmob` (Premier League/La Liga/Serie A/Bundesliga/Ligue 1 desde 2019, Brasileirão desde 2023, Championship/Eredivisie/Primeira Liga desde 2023, Libertadores/Champions League/Copa do Brasil/Eurocopa/Copa do Mundo conforme a competição existe no banco). Sem gaps parciais dentro de uma temporada coberta — ou a temporada tem 100% das partidas, ou não tem linha nenhuma.

---

## 4. O que já está em uso (referência, não repetir)

Elo (`elo_home`/`_away`), forma de gols/xG/estatísticas V7 (posse, chutes, chutes no alvo, escanteios, faltas, cartões), squad rating (força do elenco), fadiga (dias de descanso + midweek), cartões acumulados/pendurados, classificação (pontos/saldo/posição/jogos disputados), H2H, tendência de árbitro, XI titular confirmado (rating + valor de mercado), progresso da temporada (0-1).

---

## 5. Resumo pra decisão (nível de TIME)

- **Maior ganho potencial já capturado**: FotMob (`match_stats_fotmob`) — 22 colunas viraram feature na V8, cobertura ampla (2019-2026, 8 competições), granularidade que o FBref não tem (xG por tipo de jogada, chances claras, duelos, passes por tipo).
- **Ganho médio, escopo restrito**: `ppda`/`np_xg`/`expected_points`/`deep_completions` do FBref — só 5 ligas europeias, só 2023+.
- **Ganho médio, cobertura ampla**: `match_features_contexto` (viagem, técnico, desfalques, seleção) — 100% de cobertura em tudo que já está no banco, mas é dado mais "circunstancial" que estatístico.

---

## 6. Dados INDIVIDUAIS de jogadores (adicionado 27/07/2026)

Pergunta do usuário: "temos informações individuais dos jogadores a cada partida?" — sim, e é uma base rica, com duas dimensões: **desempenho por partida** e **perfil/histórico do jogador**.

### 6.1 Desempenho por partida (`match_player_stats_fotmob`) — 672.980 linhas, 15.875 partidas

Colunas promovidas (estruturadas): `rating`, `minutes_played`, `goals`, `assists`, `xg`, `xa`, `xgot`, `total_shots`, `accurate_passes`, `chances_created`, `touches`, `is_goalkeeper`.

| Coluna | Cobertura |
|---|---|
| `rating`, `accurate_passes`, `chances_created`, `touches` | ~445-482k (66-72%) |
| `xgot` | 82.244 (12%) |
| `xa` | 261.895 (39%) |
| `xg` | 176.740 (26%) |
| `minutes_played` | 481.721 (72%) |

### Cobertura por liga/temporada (nº de partidas com pelo menos 1 linha em `match_player_stats_fotmob`) — MESMAS 8 competições e temporadas de `match_stats_fotmob` (seção 2), praticamente 1:1

| Liga | Temporadas cobertas |
|---|---|
| Premier League | 2019-2025 (380/temporada) |
| La Liga | 2019-2025 (379-380/temporada) |
| Serie A (Itália) | 2019-2025 (379-380/temporada) |
| Bundesliga | 2019-2025 (306/temporada) |
| Ligue 1 | 2019-2025 (278-380/temporada, 2019 encurtada pela Covid) |
| Brasileirão Série A | 2019-2026 (177-380/temporada) |
| Copa Libertadores | 2019-2022, 2026 (60-125/temporada — falta 2023-2025) |
| Copa America | 2024 (30/30) |

Não tem `match_player_stats_fotmob` nenhuma pras outras 6 competições que aparecem em `match_stats` (Championship, Eredivisie, Primeira Liga, Champions League, Copa do Brasil, Eurocopa, Copa do Mundo) — essas só têm estatística a nível de TIME, nunca por jogador.

**Achado importante**: a coluna `stats_raw` (JSON, sempre presente quando a linha tem dado) guarda MUITO mais do que foi promovido pras colunas estruturadas acima — inspecionado diretamente em 3 amostras (jogador de linha, goleiro, jogador com stats mais completas):
- **Defesa**: `tackles`, `interceptions`, `clearances`, `recoveries`, `blocks` (`shot_blocks`), `dribbled_past`, `defensive_actions`
- **Duelos**: `duel_won`/`duel_lost`, `aerials_won`, `ground_duels_won`
- **Disciplina/contato**: `fouls`, `was_fouled`, `dispossessed`, `offsides`
- **Construção de jogo detalhada**: `accurate_crosses`, `accurate_long_balls`, `dribbles_succeeded`, `passes_into_final_third`, `touches_opp_box`
- **xG detalhado**: `expected_goals` (xG), `expected_assists` (xA), `expected_goals_on_target_variant` (xGOT), `expected_goals_non_penalty`, `xg_and_xa`
- **Goleiro (bloco próprio)**: `saves`, `saves_inside_box`, `goals_conceded`, `expected_goals_on_target_faced` (xGOT sofrido), `keeper_high_claim`, `keeper_diving_save`, `keeper_sweeper`, `player_throws`, `punches`
- **Métricas físicas** (bloco `physical_metrics`, visto em pelo menos 1 amostra): `distance_covered`, `running`/`walking`/`sprinting` (distância em cada faixa), `top_speed`

**Nenhuma dessas está em coluna própria hoje** — só dá pra usar fazendo `jsonb` parsing do `stats_raw` existente. **Isso NÃO precisa de raspagem nova** — é dado que já está no banco, só não extraído. Cobertura de cada campo dentro do `stats_raw` (se 100% das 672.980 linhas têm todos esses campos ou só uma parte) ainda não foi medida — precisa de um `jsonb_path_query`/contagem por chave antes de decidir promover pra coluna.

### 6.2 Perfil/histórico do jogador

| Tabela | Conteúdo | Cobertura | Em uso? |
|---|---|---|---|
| `players` | Cadastro (nome, país, idade, posição usual, valor de mercado, foto) | 14.133 jogadores | Parcial (`last_team_id` usado pra achar elenco atual) |
| `player_details_fotmob` | Altura, pé preferido, posição, fim de contrato, valor de mercado atual, características (`traits`) | 11.584/14.133 jogadores | **Não** |
| `player_market_value_history` | Snapshots de valor de mercado ao longo do tempo | 514.677 linhas | Sim (v3B, XI titular) |
| `player_rating_history` | Rating ANTES/DEPOIS de cada partida (ponto no tempo real) | 357.030 linhas | Sim (squad rating v2, titular rating v3B) |
| `player_ratings` | Rating atual + nº de partidas (proxy de titular regular) | 7.889 jogadores | Sim (squad rating v2) |
| `player_career_history_fotmob` | Histórico de transferências/passagens por clube (aparições, gols, assistências) | — | **Não** |
| `player_trophies_fotmob` | Títulos conquistados | — | **Não** |
| `player_availability_fotmob` | Status de lesão (só snapshot ATUAL, sem histórico) | 4.455 jogadores, 197 marcados lesionados agora | Sim, mas só em predições AO VIVO (squad rating exclui lesionado do elenco atual — nunca no treino/backtest, que já reflete quem jogou de verdade) |

### 6.3 O que já é usado (sempre AGREGADO a nível de time, nunca por jogador individual)
- `squad_rating` (v2): média ponderada do rating de todo o elenco, pesada por nº de partidas, exclui lesionados (só ao vivo)
- `titular_rating`/`titular_valor_mercado` (v3B): rating + valor de mercado do XI CONFIRMADO titular
- `key_players_missing_torneo`/`squad_natl_matches_window` (`match_features_contexto`): proxy de desfalque por convocação de seleção — usa `minutes_played` de `match_player_stats_fotmob` pra achar titulares regulares (**não** existe minutagem real de seleção no banco, é aproximação documentada em `arquivos_do_claude/features_contexto.py`)

### 6.4 O que nunca virou feature, nem agregado
- As métricas de desempenho por jogador em si (gols, assistências, xG, xA, chances criadas, toques, passes **de um jogador específico**) — só entra via `rating` agregado, nunca a forma recente de um jogador (ex.: "o artilheiro está em boa fase nos últimos 5 jogos?")
- Tudo do `stats_raw` não promovido (seção 6.1)
- `player_details_fotmob`, `player_career_history_fotmob`, `player_trophies_fotmob` inteiros

---

## 7. Recomendações pra completar dados de jogadores (raspagem FotMob em outro repositório)

Usuário está raspando dados do FotMob em outro repositório da mesma conta. Prioridades pra completar o que falta aqui, da mais pra menos valiosa:

1. **Minutagem/convocação REAL de seleção por jogador** — hoje é só um proxy (titular no clube + seleção jogou torneio na janela). Se o FotMob expõe o perfil de seleção do jogador (jogos/gols pela seleção, convocações recentes), isso substitui o proxy por dado real — gap **já documentado no código** (`features_contexto.py`: "minutos reais de seleção por jogador NÃO existem no banco").
2. **Histórico de lesões/suspensões (datas de início/fim)** — hoje só existe o snapshot ATUAL (`player_availability_fotmob.injured`), sem histórico. Sem isso, "desfalque" nunca pode ser feature de TREINO/BACKTEST (só de predição ao vivo) — com histórico, dava pra reconstruir "quantos jogos o time ficou sem o titular X" em qualquer partida passada.
3. **Eventos de partida por jogador (cartão amarelo/vermelho, substituição com minuto exato)** — não confirmado no banco atual (o bloco de stats por jogador que já temos parece ser só desempenho, não a timeline de eventos; FotMob normalmente expõe isso num endpoint/bloco separado de "match facts"/eventos). Cartão por jogador (não só total do time, que já temos) permitiria uma feature de risco de suspensão mais precisa que a atual (`cartoes_acumulados`, hoje calculada por outra fonte).
4. **Cobertura/consistência do `stats_raw`** — antes de gastar raspagem nova, vale medir quantas das 672.980 linhas já têm os campos avançados (tackles/duelos/xG detalhado/físico) descobertos na seção 6.1 — pode ser que grande parte do que parece "faltando" já esteja capturado e só precise de um script de extração, não de raspagem nova.
5. **`player_details_fotmob`/`player_career_history_fotmob`** completos pros ~2.500 jogadores sem perfil ainda (11.584/14.133 têm hoje) — menor prioridade, é dado mais demográfico/de carreira que tático.
