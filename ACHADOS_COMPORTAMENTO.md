# Achados — frente de comportamento e interação entre equipes

Registro dos achados da frente aberta em 03/09/2026 ("fornecer dados que facilitem prever o comportamento das equipes ao longo do jogo e como interagem entre si").

**Este arquivo é para os ACHADOS** — o que os dados dizem, o que foi validado, o que foi refutado e o que continua em aberto. A arquitetura e as convenções de uso das tabelas estão em `CLAUDE.md`; o estado corrente e as pendências, em `CONTEXTO_PROJETO.md`.

**Regra que vale para tudo aqui:** nada nesta página entrou em nenhum modelo de produção. É camada de dado e leitura descritiva. Nenhum número foi validado com IC 95% via `api/backtest-betting.js`, então **nenhum deles é edge** até que passe por lá.

---

## As camadas construídas

| Fase | Tabela | O que captura | PR |
|---|---|---|---|
| 1 | `match_formation_fotmob` | Formação de entrada em campo (4-2-3-1, 3-5-2…) | #420 |
| 2 | `match_goal_timeline`, `match_team_game_state` | Placar minuto a minuto; tempo e produção por estado | #433 |
| 3 | `match_team_event_response` | Transiente pós-gol e pós-expulsão, dentro de cada estado | #434 |
| — | `v_game_state_por_forca` | Controle de força de equipe por Elo | #435 |

Nenhuma delas consumiu uma única chamada de API nova: tudo saiu de dado que já estava no banco.

---

## Achado 1 — a formação estava escondida no desenho da telinha

O FotMob **não** devolve a string `"4-2-3-1"` no payload que o projeto ingere. Devolve, por titular, a posição na grade do campinho (`verticalLayout.y`), e isso já estava guardado em `match_lineup_fotmob.raw` desde a primeira ingestão.

Agrupar os 11 titulares por `y` reconstrói a formação: **37.951 de 39.106 team-matches (97,1%)**, 2017–2026.

Validação de sanidade: a distribuição resultante é a que o futebol real prevê, sem nenhuma grade impossível.

| Formação | Ocorrências |
|---|---|
| 4-2-3-1 | 13.466 |
| 4-3-3 | 6.594 |
| 4-4-2 | 4.266 |
| 3-4-2-1 | 2.625 |
| 3-5-2 | 2.245 |

**Decisão de NÃO fazer, registrada para não ser reintroduzida:** métricas geométricas da grade (altura do bloco, largura) são função determinística da própria formação — a grade é *esquemática*, não rastreamento. Não são medida de comportamento.

---

## Achado 2 — o placar reconstruído depende de acertar o gol contra

`match_shots_fotmob` tem 477.686 chutes com minuto e xG, e os gols estão lá como `event_type='Goal'`. Dá para reconstruir o placar a cada instante.

Duas armadilhas, ambas confirmadas contra o placar oficial **antes** de implementar:

1. `period='PenaltyShootout'` traz 454 "gols" de disputa de pênaltis, que não contam.
2. **Em gol contra, `team_id` é quem CHUTOU, não o beneficiado.**

| Leitura do gol contra | Partidas cujo placar reconstruído bate com o oficial |
|---|---|
| Creditado ao adversário | **13.403 de 13.427 — 99,8%** |
| `team_id` como beneficiado | 12.284 — 91,5% |

Oito pontos percentuais separam a leitura certa da "óbvia". Escrever o parser pela intuição teria produzido um dataset que parece bom e está sistematicamente errado nos jogos com gol contra.

---

## Achado 3 — o efeito do placar, depois de controlar força de equipe

**Este achado passou por uma correção. A primeira versão estava errada.**

A média global de `match_team_game_state` dizia: *"quem está perdendo cria menos xG que quem está ganhando"* (1,370 contra 1,426 por 90). Esse número estava **confundido com força de equipe** — quem está ganhando é, em média, o time melhor. A estrutura espelhada da fase 2 controla o **tempo**, não a **força**.

Controle aplicado: `team_elo_history.rating_antes`, escopo `global` — Elo **antes** da partida, sem vazamento. Cobertura de 99,8%.

### Controlando por Elo, o xG total inverte de sinal

| Faixa de \|dif Elo\| | Estado | xG criado /90 | Chutes /90 | xG por chute |
|---|---|---|---|---|
| ≤ 25 (equilibrado) | perdendo | **1,390** | 14,04 | 0,0990 |
| ≤ 25 (equilibrado) | ganhando | 1,281 | 10,50 | 0,1220 |
| 26–75 | perdendo | 1,433 | 14,13 | 0,1014 |
| 26–75 | ganhando | 1,297 | 10,62 | 0,1221 |
| > 75 (desigual) | perdendo | 1,335 | 13,21 | 0,1011 |
| > 75 (desigual) | ganhando | **1,526** | 11,81 | 0,1292 |

Em jogo parelho, **quem perde cria mais**. Só na faixa desigual o sinal inverte — e ali o que está sendo medido é a diferença de qualidade dos times.

### Segurando também o mando de campo

Em jogo equilibrado por Elo o mandante ainda vence mais, então "ganhando" vinha enriquecido de mandantes. Com força **e** lado fixos (números após a correção do relógio, ver Achado 5):

| Lado | Estado | xG criado /90 | Chutes /90 | xG por chute |
|---|---|---|---|---|
| Mandante | perdendo | 1,585 | 15,74 | **0,1007** |
| Mandante | ganhando | 1,362 | 11,17 | **0,1219** |
| Visitante | perdendo | 1,211 | 12,41 | **0,0976** |
| Visitante | ganhando | 1,106 | 9,07 | **0,1219** |

### O que sobrevive a todos os controles

1. **Qualidade por finalização.** ~0,122 xG por chute ganhando contra ~0,098–0,101 perdendo. Notavelmente estável: valor praticamente idêntico nas **três** faixas de Elo e nos **dois** lados (0,1219 nos dois lados quando ganhando). **Perseguir o jogo degrada a qualidade do chute em ~18%, independentemente de quem é o time e de onde joga.**
2. **Volume.** Mais chutes perdendo, em toda faixa e dos dois lados.

### O que NÃO sobrevive

O **xG total por 90** — que era exatamente o número do resumo original da fase 2. Ele é o produto do volume (comportamental) pela qualidade (comportamental) com a força de equipe por cima, e por isso troca de sinal conforme a faixa.

**Como usar:** agregue `match_team_game_state` sempre por `faixa_forca` (view `v_game_state_por_forca`) e de preferência também por `is_home`, e sempre dividindo por `minutos`.

---

## Achado 4 — os 5 minutos após um gol são os mais parados da partida

Comparação feita **dentro do mesmo estado do placar** (contra a linha `evento='nenhum', janela='regime'`), que é o que separa reação ao evento de simples mudança de placar.

| Estado | Momento | xG criado /90 | Chutes /90 |
|---|---|---|---|
| Perdendo | **0-5 min após sofrer** | **0,949** | 9,87 |
| Perdendo | 5-15 min após sofrer | 1,320 | 13,06 |
| Perdendo | regime (>15 min) | **1,487** | 14,69 |
| Ganhando | **0-5 min após marcar** | **1,085** | 8,69 |
| Ganhando | 5-15 min após marcar | 1,493 | 11,86 |
| Ganhando | regime | 1,449 | 11,51 |

É o **oposto** da narrativa de "pressão depois de levar o gol": a criação de quem sofreu cai para dois terços do seu próprio regime e só volta ao normal depois de ~15 minutos. O efeito atinge os dois lados.

**Ressalva que precisa andar junto do número:** parte da queda é **mecânica, não tática**. Comemoração, reinício do meio e substituições consomem tempo real dentro da janela, então há menos bola rolando. Separar "o time recua" de "o cronômetro corre sem jogo" exigiria tempo efetivo de jogo, que não temos.

Caso de amostra menor mas interessante: time que **marcou e ainda assim segue perdendo** (2-1 para 2-2 não, mas 3-1 para 3-2) produz o valor mais alto da tabela — 1,750 xG/90 e 16,36 chutes/90 em 378 horas.

---

## Achado 5 — bug real: relógio não monótono entre os tempos

**Defeito introduzido nas fases 2 e 3, detectado e corrigido depois de já estar em produção.**

O relógio da partida estava definido como `minute + minute_added`. Isso não é monótono: o 2º tempo também começa no minuto 45, então um gol aos **45+3** (minuto efetivo 48) era ordenado **depois** de lances do início do 2º tempo.

| Medida | |
|---|---|
| Gols nos acréscimos do 1º tempo | 1.651 (3,2% dos gols) |
| Partidas afetadas | 1.601 |
| Chutes recebendo **estado do jogo errado** | **1.102 — 0,23%** de 477.715 |

**Por que as invariantes não pegaram:** minutos, chutes e xG por partida são insensíveis à ORDEM. Eles reconciliavam perfeitamente (37.540/37.540) com a ordenação errada.

**Correção:** coluna `clock` em `match_goal_timeline` — relógio monótono que desloca o 2º tempo pelo excedente dos acréscimos do 1º (`fh_over`, média de 1,48 min). `minuto` continua sendo o valor exibível.

**Depois de re-derivar as duas tabelas inteiras:** 0 de 10.712 partidas com gol nos dois tempos ainda mal ordenadas; 37.542 de 37.542 pares reconciliando. Nenhuma conclusão mudou — só os valores, levemente.

---

## Lição de método (vale além deste projeto)

**Invariantes internas provam que a derivação está certa. Não provam que a interpretação está.**

Aconteceu duas vezes nesta frente:

- O **Achado 3** passou em todas as invariantes (espelhamento perfeito, minutos fechando) e mesmo assim a conclusão agregada estava confundida com força de equipe.
- O **Achado 5** era um bug de ordenação que reconciliava perfeitamente em todos os totais, porque totais não têm ordem.

Em ambos os casos o que expôs o problema foi **procurar um confundidor específico**, não rodar mais verificações de consistência.

---

## Bugs de dado encontrados e NÃO corrigidos

Ficaram de fora de propósito — mexem em crosswalk, e o `CLAUDE.md` proíbe resolver esses mapeamentos sem supervisão manual.

**Troyes duplicado em `teams`.** Id `498` ("Troyes") tem 76 partidas, crosswalk `fotmob:10242`, 794 chutes e 775 escalações. Id `1019` ("ES Troyes AC") tem 34 partidas e **nenhum crosswalk, zero chutes, zero escalações**. São o mesmo clube; as 34 partidas do id 1019 nunca receberão dado do FotMob. Encontrado duas vezes de forma independente: na fase 1 pela `is_home` nula de uma escalação, e na fase 2 pela única partida (16034, Troyes x Paris FC) em que o xG criado por um lado não espelhava o concedido pelo outro. O projeto já tem `scripts/unificar_times_duplicados.py` para isso. Vale varrer o mesmo padrão (time sem crosswalk mas com partidas) atrás de outros casos.

**Drift de schema — varredura feita, escopo maior do que o achado da fase 1.** `match_lineup_fotmob.formation` e `.team_rating` não foram caso isolado. Cruzando os nomes das 102 migrations aplicadas no projeto (via `list_migrations`) contra os 79 arquivos versionados em `supabase/migrations/`, pelo menos estas mexem em schema e não têm arquivo correspondente no repo:

- `matches_add_context_columns` → `matches.is_neutral`, `.match_stage`, `.aggregate_advantage` (as três em produção, usadas em `api/model-maintenance.js` e em scripts de ingestão)
- `teams_add_aliases` → `teams.aliases` (em produção, usada em `src/utils/matchTeamNames.js`)
- `leagues_add_territory_columns` → `leagues.territory_type`, `.territory_code` (confirmadas em produção)
- `add_global_escopo_to_team_elo`, `create_team_federacao_view`, `create_custom_model_ondemand_predictions`, `create_custom_model_artifacts_bucket_and_column`, `cria_tabela_assinaturas_api`, e o grupo `custom_model_configs_add_*`/`add_mode_algorithms_to_custom_model_configs`/`add_calibrated_metrics_to_wf_results`/`fix_fair_odds_division_by_zero` — mesmo padrão pelo nome, não confirmados coluna a coluna.

Consequência prática: um replay limpo (Supabase Preview branch) reconstrói o schema só a partir dos arquivos versionados, então essas colunas/tabelas/views **não existem** numa preview — qualquer migration nova que assuma a presença delas quebra o replay sem aviso (foi exatamente o que aconteceu com `match_lineup_fotmob.formation` na fase 1, dentro de um guard; aqui não há guard nenhum). Não corrigido de propósito: escrever a migration retroativa exige decidir a favor de qual coluna hoje é lida em produção sem checar `information_schema` primeiro — risco de o texto da migration não bater byte a byte com o que já está rodando. Fica para correção supervisionada, com o mesmo cuidado que a fase 1 teve ao comparar arquivo-a-arquivo contra `pg_get_functiondef`/`information_schema` antes de commitar.

**`match_events` não é tabela de eventos gerais.** Só tem cartões (58.185 amarelos, 1.813 vermelhos, 1.183 segundos amarelos) — sem gols e sem substituições. Estava documentada de forma imprecisa; corrigido.

---

## Em aberto

- **Levar qualquer uma das camadas para dentro de um modelo.** É o salto que ainda não foi dado, e o que exigiria validação com IC 95% via `api/backtest-betting.js`. O candidato mais forte é o Achado 3 (qualidade por chute condicionada ao estado), por ser o mais estável aos controles.
- **Perfil temporal por faixa de minuto.** Começado e interrompido: a exposição por faixa mostra o confundidor com clareza (aos 0-15 min há 3.876 horas de "empatando" contra 298 de cada outro estado; aos 75+ são 1.588 contra 2.045 de cada), mas a análise completa foi o que expôs o bug do Achado 5 e não foi refeita depois da correção.
- **Tempo efetivo de bola rolando**, que é o que permitiria separar a parte tática da parte mecânica no Achado 4.
