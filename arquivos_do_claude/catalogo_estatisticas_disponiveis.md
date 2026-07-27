# Catálogo de estatísticas disponíveis pra features (conferência futura)

Levantamento feito em 27/07/2026, direto no banco (`cgurxgfdmpmsnrshqycx`), pra decidir quais estatísticas de jogo entram em novas camadas de features (V8+). Ponto de partida do pedido do usuário: "quero criar modelos que utilizem o mais estatísticas de jogos anteriores de FBref, FotMob etc". Cobre 3 tabelas com dado real parado (coletado, mas nunca virou feature de nenhum modelo) + o que já está em uso, pra não duplicar trabalho.

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

## 2. FotMob (`match_stats_fotmob`) — 31.888 linhas, o maior filão parado

Hoje só `xgot` é lido (e só como ALVO de regressão em `treinar_regressor_xgot.py`, nunca como feature). Todo o resto abaixo está 100% parado:

| Grupo | Colunas | Em uso? |
|---|---|---|
| Posse | `possession` | Não |
| xG detalhado | `xg`, `xg_open_play`, `xg_set_play`, `xg_non_penalty` | Não (`xg` redundante com FBref, mas fonte independente) |
| xGOT | `xgot` | Só como alvo de regressão |
| Finalização | `total_shots`, `shots_on_target`, `shots_off_target`, `shots_blocked`, `shots_inside_box`, `shots_outside_box` | Não |
| Chances claras | `big_chances`, `big_chances_missed`, `touches_opp_box` | Não |
| Construção de jogo | `accurate_passes`, `accurate_passes_total` (dá % de acerto), `accurate_long_balls`, `accurate_crosses` | Não |
| Defesa | `tackles`, `interceptions`, `blocks`, `clearances`, `keeper_saves` | Não |
| Duelos | `duels_won`, `aerial_duels_won`, `successful_dribbles` | Não |
| Disciplina | `fouls_committed`, `yellow_cards`, `red_cards`, `corners` | Não |

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

## 5. Resumo pra decisão

- **Maior ganho potencial**: FotMob (`match_stats_fotmob`) — ~20 colunas nunca usadas, cobertura ampla (2019-2026, 8 competições), granularidade que o FBref não tem (xG por tipo de jogada, chances claras, duelos, passes por tipo).
- **Ganho médio, escopo restrito**: `ppda`/`np_xg`/`expected_points`/`deep_completions` do FBref — só 5 ligas europeias, só 2023+.
- **Ganho médio, cobertura ampla**: `match_features_contexto` (viagem, técnico, desfalques, seleção) — 100% de cobertura em tudo que já está no banco, mas é dado mais "circunstancial" que estatístico.
