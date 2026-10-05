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

## Achado 6 — cartão vermelho é o sinal mais forte da tabela, e sobrevive ao controle de força

**Incidência.** 2.996 cartões vermelhos (1.813 diretos + 1.183 segundo amarelo) em 2.519 das 31.054 partidas — **8,1% dos jogos têm expulsão**. Minuto mediano: **72'**; quase metade (1.415 de 2.996) acontece depois dos 75', sobrando pouco jogo pra observar a resposta. `match_team_event_response` cobre 2.106 dessas 2.519 partidas (83,7%) — o resto fica fora do escopo da derivação (partidas sem `placar_confere`, ou sem chutes suficientes depois do cartão).

**O efeito, nos primeiros 5 minutos, é o dobro/metade da produção — muito maior que o do gol (achado 4).**

| Situação | Momento | xG criado /90 | xG concedido /90 |
|---|---|---|---|
| **Ficou com 10** (`expulsao_pro`) | regime (sem evento recente) | 1,22–1,49 (varia por estado) | mesmo valor, espelhado |
| Ficou com 10 | **0-5 min após o cartão** | **0,56–0,74** (≈ metade do regime) | **2,67–3,10** (≈ o dobro/triplo) |
| Ficou com 10 | 5-15 min após | 0,76–1,08 (recupera parcialmente) | 2,21–2,51 |
| **Adversário ficou com 10** (`expulsao_contra`) | regime | 1,22–1,49 | mesmo valor, espelhado |
| Adversário com 10 | **0-5 min após o cartão** | **2,69–3,13** (≈ o dobro/triplo) | **0,53–0,73** (≈ metade) |
| Adversário com 10 | 5-15 min após | 2,21–2,43 | 0,76–1,00 |

(comparação sempre dentro do mesmo estado de placar, contra `evento='nenhum', janela='regime'` — mesmo método do achado 4)

**Controle de força de equipe (mesmo método do achado 3): o efeito não muda.** Cortando por `faixa_forca` (Elo), quem fica com 10 cria ~0,60–0,65 xG/90 contra ~1,26–1,34 no regime, e concede ~2,52–2,96 contra o mesmo regime — **estável nas três faixas** (equilibrado/leve/desigual). Ao contrário do achado 3, aqui não há confusão com qualidade de elenco: a vantagem numérica pesa igual em qualquer confronto.

**E se o cartão sai em outro momento da partida?** Recorte feito só nas 2.121 partidas com **exatamente 1 cartão vermelho no jogo todo** (84% dos 2.519 casos — elimina a ambiguidade de somar duas expulsões distintas na mesma janela), bucketado pelo minuto do cartão. Perspectiva de quem fica com 10, nos primeiros 5 minutos:

| Cartão sai aos... | Cria (xG/90) | Sofre (xG/90) | Razão sofre/cria |
|---|---|---|---|
| 0-30' | 0,244 | 2,935 | ~12x |
| 30-60' | 0,363 | 2,839 | ~7,8x |
| 60-75' | 0,369 | 2,918 | ~7,9x |
| 75'+ | **1,043** | 3,084 | ~3,0x |

O que **não muda** com o momento do cartão: o quanto o adversário passa a criar (2,84–3,08 xG/90 nos quatro recortes — praticamente constante). O que **muda bastante**: o apagão ofensivo de quem fica com 10 é muito mais severo quando o cartão sai antes dos 75' (cria menos de 0,4 xG/90) do que nos minutos finais (1,04 xG/90) — times atrás no placar parecem seguir arriscando pra frente mesmo com um a menos quando o jogo está acabando, e isso é visível mesmo já sabendo que o efeito global de cartão não muda por força de equipe. Achado descritivo, não decomposto por estado de placar dentro de cada faixa de minuto (a amostra já fica pequena: 178-754 ocorrências por célula).

**O efeito não é um susto de 15 minutos que passa — é um platô que dura o resto do jogo.** Recorte de 5 em 5 minutos desde o cartão até o fim da partida (1.991 das 2.121 partidas com 1 cartão único, cálculo ad-hoc explicado na ressalva de metodologia abaixo), tempo desde o cartão no eixo, sempre pela perspectiva de quem fica com 10:

| Minutos desde o cartão | Partidas ainda em jogo | Cria (xG/90) | Adversário cria (xG/90) |
|---|---|---|---|
| 0-5 | 1.991 | 0,42 | 2,16 |
| 5-10 | 1.748 | 0,71 | 1,79 |
| 10-15 | 1.462 | 0,63 | 1,60 |
| 15-20 | 1.271 | 0,68 | 1,86 |
| 20-30 | 992–1.125 | 0,65–0,68 | 1,62–1,86 |
| 30-45 | 647–860 | 0,66–0,76 | 1,79–1,97 |
| 45-60 | 378–549 | 0,33–0,65 | 1,76–2,01 |
| 60'+ | < 300 (cai rápido) | instável — amostra pequena | instável — amostra pequena |

*(regime sem cartão, referência: ~1,22–1,49 xG/90 pros dois lados)*

O pico (0-5 min) é o já visto na tabela acima. Depois disso, o adversário **não volta ao normal**: ele segue criando 1,6–2,0 xG/90 (30-65% acima do regime) em praticamente todo bloco até os 55-60 minutos pós-cartão. A vantagem numérica pesa a partida inteira, não só o susto inicial. Os blocos depois de 60 minutos pós-cartão têm menos de 300 partidas contribuindo (só cartões muito cedo no jogo sobrevivem até lá) e não são confiáveis.

**Ressalva de metodologia (só desta tabela de 5 em 5 min, diferente do resto do achado 6).** Ao contrário das linhas 0-5/5-15/regime acima — que vêm direto de `match_team_event_response`, já validada — esta tabela foi calculada ad-hoc cruzando `match_shots_fotmob` (relógio `clock` reconstruído na hora, mesma fórmula da migration `20260905160000`) com o minuto do cartão em `match_events`. Duas limitações que não afetam o resto do achado: (1) `match_events` não guarda acréscimo separado como os chutes guardam — uns 5% dos cartões perto do intervalo (minuto 45) podem estar levemente deslocados no relógio; (2) quanto mais longe do cartão, menos partidas sobram (só cartão cedo deixa muito tempo de jogo depois) — efeito de seleção que enfraquece a leitura dos últimos blocos, não um viés de conteúdo.

**Ressalva importante, e diferente da do achado 4.** O baseline `nenhum/regime` não é limpo aqui: depois dos 15 minutos da janela de resposta, o tempo com um jogador a menos/mais **volta a ser contado como `regime`** (a tabela só distingue os primeiros 15 minutos após o evento, não o resto da partida em desvantagem numérica). Isso significa que o próprio regime já está um pouco contaminado por minutos jogados com um homem a menos/mais — o que **subestima**, não superestima, o efeito real de jogar com 10 pelo resto do jogo. Medir esse efeito completo exigiria cruzar `match_team_event_response` com quantos jogadores cada time tinha em campo minuto a minuto, o que a estrutura atual não guarda.

**Sobre previsão.** Isto é o candidato mais forte da frente inteira pra entrar num modelo de in-play: o efeito é grande (2-3x, não os ~18% do achado 3), imediato, mirrado nos dois lados, sustentado pelo resto do jogo (não só 15 min), e sobrevive ao controle de força. Mas **nada aqui foi validado com IC 95%** (regra do topo desta página) — e a janela de uso prático é estreita quando o cartão sai depois dos 75', que é quase metade dos casos.

---

## Achado 7 — quem resiste a um cartão vermelho antes dos 60': é quase todo qualidade de elenco, quase nada é o minuto

Pergunta natural depois do achado 6: dado que o time reduzido cria muito menos e sofre muito mais, **quantas vezes ele segura o resultado mesmo assim — e o que diferencia quem segura de quem não segura?**

**Recorte:** as 785 partidas (dentro das 2.121 com 1 cartão único) em que a expulsão saiu **antes dos 60 minutos**, cruzando o placar no momento do cartão (`match_goal_timeline`) com o placar final (`matches`) e a diferença de Elo (`team_elo_history`, escopo `global`, sem vazamento).

### O estado do placar no momento do cartão já decide a maior parte

| Estado do time punido, no momento do cartão | Resistiu (empatou ou venceu) | Empatou | Venceu |
|---|---|---|---|
| **Ganhando** (151 casos) | **76,2%** | 32,5% | 43,7% |
| Empatando (425 casos) | 37,9% | 24,7% | 13,2% |
| **Perdendo** (209 casos) | **11,0%** | 9,1% | 1,9% |

Nada surpreendente em si — ganhar de 11 é mais fácil que ganhar de 10 — mas o tamanho da diferença é grande: um time que já está perdendo quando toma o cartão praticamente não volta (1,9% de chance de vencer).

### Dentro do empate — o caso ambíguo — quem segura é quem já era melhor

Cortando só os 425 casos empatados no momento do cartão (o cenário em que a resistência não está pré-decidida pelo placar) por diferença de Elo entre punido e adversário:

| Força do time punido vs. adversário | Resistiu | Empatou | Venceu |
|---|---|---|---|
| **Bem melhor** (Elo ≥ +50) | **53,5%** | 33,3% | 20,1% |
| Parelho (-50 a +50) | 34,3% | 22,9% | 11,4% |
| **Bem pior** (Elo ≤ -50) | **27,3%** | 18,8% | 8,5% |

Gradiente limpo e monotônico: **jogar melhor antes do cartão prevê melhor quem aguenta depois dele.** O time reduzido não "compensa" a desvantagem numérica com um esforço tático especial que apareça nos dados — quem segura é, na maioria, quem já teria vantagem de qualidade de qualquer forma.

### Dois efeitos secundários, reais mas bem menores que o de força

- **Mando de campo:** mandante empatado no momento do cartão resiste em 44,9% dos casos, visitante em 32,2% — vantagem de ~13 pontos percentuais, bem menor que a de força.
- **Minuto do cartão dentro da janela 0-60':** resistência de 32,5% quando o cartão sai antes dos 30' contra 41,3% entre 30'-60' — direção esperada (menos tempo pra sofrer gol depois de um cartão mais cedo), mas o efeito é pequeno perto do de força.

### O que isso quer dizer

**Não há um padrão tático identificável de "como resistir"** nos dados — o achado 6 já mostrou que todo time reduzido cria menos e sofre mais, na mesma proporção, não importa a força. O que muda o resultado final não é comportamento diferente durante a desvantagem, é a distância de qualidade que já existia antes dela. Combinado com o achado 3 (mesma lição: controle de força muda a leitura), isso sugere que "seguraram o resultado com um a menos" é, na maior parte dos casos, a história de um time melhor absorvendo um choque, não a de uma tática de resistência que os dados consigam separar.

Descritivo, sem IC 95%, mesma ressalva de sempre antes de virar sinal de modelo.

---

## Achado 8 — a taxa de gols sobe ao longo do jogo, quase igual em todas as ligas — mas a cobertura de dado NÃO é igual

Pergunta: como é a taxa de gols ao longo dos 90 minutos, e existe diferença entre ligas? Achado técnico no meio do caminho: **a comparação honesta exigiu descobrir e contornar um problema de cobertura de dado que não estava documentado.**

### O formato geral — tentativa (chute) e sucesso (gol) juntos, mesma tabela de origem

Chute e gol vêm da mesma linha em `match_shots_fotmob` (gol é só `event_type='Goal'`), então dá pra ter os dois juntos na mesma granularidade de 5 minutos, sem precisar de tabela nova:

| Bloco (min) | Chutes /partida | Chutes ao gol /partida | Gols /100 partidas | Conversão (gol/chute) |
|---|---|---|---|---|
| 0-5 | 0,74 | 0,45 | 7,5 | 10,07% |
| 5-30 | 1,16–1,23 | 0,71–0,75 | 12,6–13,1 | 10,35–10,85% |
| 30-45 | 1,25–1,28 | 0,76–0,78 | 13,5 | 10,53–10,86% |
| **45-50** | **1,57** | **0,95** | **16,5** (salto na volta do intervalo) | 10,50% |
| 50-75 | 1,31–1,47 | 0,80–0,89 | 14,7–16,1 (declina devagar) | 11,06–11,27% |
| 75-90 | 1,31–1,33 | 0,80–0,81 | 14,6–14,7 | 10,98–11,24% |
| 90+ (acréscimos/prorrogação) | 2,36\* | 1,43\* | 26,4\* | 11,18% |

\*bucket mais largo que 5 min (acréscimo médio de 2º tempo leva o relógio a ~95', só 65 gols em toda a base passam de 105') — não comparável célula a célula com as outras linhas, mas confirma o salto real na reta final.

Formato clássico de futebol: começo mais frio, sobe ao longo da partida, um salto visível assim que o 2º tempo começa (times ajustados depois do intervalo), platô alto no meio do 2º tempo, e disparada nos minutos finais.

**O que a junção mostra e a tabela só de gol não deixava ver:** o gol não sobe no 2º tempo só porque tem mais chute — **a própria conversão sobe um pouco**, de ~10,5-10,9% no 1º tempo pra ~11,0-11,3% no 2º tempo (quase todo bloco do 2º tempo fica acima de qualquer bloco do 1º). Efeito pequeno mas consistente — compatível com chutes de melhor qualidade perto do fim (defesa mais cansada, jogo mais aberto), não só mais numerosos.

### A comparação entre ligas só ficou confiável depois de eu achar isto:

Fazendo o mesmo recorte por liga (Brasileirão, La Liga, Serie A, Premier League, Bundesliga, Ligue 1), a Brasileirão apareceu com uma taxa de gols **artificialmente baixa** (menos da metade das outras ligas em todo bloco). Investigando: `match_shots_fotmob` cobre só **42,5%** dos gols oficiais da Brasileirão Série A (8.827 gols oficiais em `matches`, 3.749 na timeline), contra 75-89% nas outras cinco ligas.

**Causa raiz, confirmada por temporada:** cobertura de shotmap por liga não é uniforme no tempo. Brasileirão só tem cobertura completa (380/380 partidas) a partir de **2023** — 2017-2022 têm entre 0 e 68 partidas de 380 cobertas por temporada. As cinco ligas europeias têm o mesmo padrão, só que a virada pra cobertura completa é bem mais cedo, em **2020** (exceto poucas partidas de 2019 residuais em todas).

| Liga | Temporadas sem cobertura (quase 0/380) | Primeira temporada 100% |
|---|---|---|
| Brasileirão Série A | 2017, 2018, 2019 (2), 2021, 2022 parciais | 2023 |
| Bundesliga / La Liga / Ligue 1 / Premier League / Serie A | 2018-2019 (residual) | 2020 |

**Correção aplicada nesta análise:** restringir a comparação às temporadas 2023-2025 (as únicas com as 6 ligas em cobertura completa). Com isso, os totais batem com o que se espera de cada liga (Bundesliga ~3,20 gols/jogo — liga mais ofensiva das seis, Brasileirão/Serie A ~2,5, Premier League ~3,0), validando que o recorte corrigiu o problema.

### Com a cobertura corrigida: o formato é quase idêntico em todas as ligas

| Liga | % dos gols no 1º tempo | % dos gols nos últimos 15min+acréscimos |
|---|---|---|
| Brasileirão Série A | 39,6% | 27,0% |
| Bundesliga | 41,1% | 25,3% |
| La Liga | 39,2% | 26,3% |
| Ligue 1 | 39,8% | 26,0% |
| Premier League | 39,4% | 27,0% |
| Serie A (Itália) | 39,7% | 24,8% |

**Praticamente igual nas seis** — ~39-41% dos gols saem no 1º tempo, ~25-27% saem nos 15 minutos finais + acréscimos, não importa a liga. **O que muda entre ligas é o volume total de gols por jogo (Bundesliga bem mais ofensiva, Brasileirão/Serie A mais defensivas), não o formato de quando eles saem.**

### Bug de dado real, registrado (não é do escopo desta pergunta corrigir)

A cobertura desigual de `match_shots_fotmob`/`match_goal_timeline` por liga-temporada não estava documentada em nenhum lugar do projeto antes desta análise. Qualquer análise futura que use essas tabelas **sem filtrar por temporada** e comparar entre ligas (ou entre temporadas de uma liga só) corre o risco de medir cobertura de dado, não comportamento de jogo — exatamente o que aconteceu aqui antes do filtro. Não é um bug pra corrigir agora (a cobertura tende a chegar sozinha conforme mais ligas/temporadas são ingeridas) mas é uma ressalva de uso: **sempre checar `count(*) filter (where exists (select 1 from match_shots_fotmob ...))` por liga+temporada antes de comparar ligas ou épocas usando o shotmap.**

Descritivo, sem IC 95%.

---

## Achado 9 — o mesmo estudo pra chutes, chutes ao gol, escanteios, faltas e cartões

Continuação natural do achado 8: repetir "taxa ao longo do jogo + diferença entre ligas" pras outras estatísticas de partida. **Duas delas não deram pra fazer da mesma forma** — registrado abaixo por quê.

### Chutes e chutes ao gol — dá pra fazer, mesmo método do achado 8 (`match_shots_fotmob`, relógio `clock`)

Formato geral (todas as partidas com shotmap, 18.780 partidas):

| Bloco | Chutes /100 partidas | Chutes ao gol /100 partidas | % ao gol |
|---|---|---|---|
| 0-15 | 310,0 | 189,0 | 61,0% |
| 15-30 | 367,2 | 223,7 | 60,9% |
| 30-45 | 380,9 | 231,2 | 60,7% |
| 45-60 | 446,6 | 270,7 | 60,6% |
| 60-75 | 403,5 | 247,3 | 61,4% |
| 75-90 | 395,9 | 241,3 | 61,0% |
| 90+ (bucket mais estreito, ver ressalva do achado 8) | 235,9 | 143,2 | 60,7% |

Mesmo formato do gol (sobe ao longo do jogo, pico logo depois do intervalo) — esperado, já que gol é numerador de chute. **A proporção que vai ao alvo é notavelmente constante (~61%) em toda a partida** — a taxa de conversão em chute-no-alvo não parece depender de quando no jogo o chute acontece.

Comparação entre ligas (2023-2025, mesma correção de cobertura do achado 8): 40-42% dos chutes no 1º tempo e 24-26% nos 15 min finais em **todas as seis ligas** — de novo, formato praticamente igual. `% ao gol` por liga varia um pouco mais (59,8% Brasileirão a 63,8% Premier League) — a única diferença de formato encontrada aqui, pequena.

### Cartões (amarelo + vermelho) — dá pra fazer com `match_events`, mas o formato entre ligas varia mais que gol/chute

Formato geral (13.439 partidas com evento registrado):

| Bloco | Amarelos /100 partidas | Vermelhos+2º amarelo /100 partidas |
|---|---|---|
| 0-15 | 20,6 | 0,71 |
| 15-30 | 44,2 | 1,53 |
| 30-45 | 65,3 | 2,08 |
| 45-60 | 79,1 | 3,59 |
| 60-75 | 76,1 | 3,85 |
| 75-90 | 89,7 | 5,25 |
| 90+ (bucket mais estreito) | 57,9 | 5,28 |

Cartão sobe de forma quase monotônica com o tempo de jogo — nada de "pico no intervalo" como em gol/chute, é uma escalada constante até o fim (cansaço, faltas táticas, ânimos mais exaltados). O ritmo de vermelho nos acréscimos finais (bucket mais estreito que os outros) é o mais alto de todos — o que já era esperado pelo achado 6/7 (minuto mediano do cartão vermelho: 72').

**Aqui, diferente de gol e chute, o formato entre ligas se parece mas não é igual, e o volume difere bastante:**

| Liga | % cartões no 1º tempo | % nos 15 min finais+acréscimos | Cartões/jogo | Vermelhos/jogo |
|---|---|---|---|---|
| Brasileirão | 28,5% | 35,4% | **5,50** | **0,306** |
| Bundesliga | 27,5% | 36,6% | 4,16 | 0,183 |
| La Liga | 26,8% | 39,3% | 4,91 | 0,261 |
| Ligue 1 | 30,5% | 32,9% | 4,12 | 0,252 |
| Premier League | 27,5% | 36,4% | 4,33 | **0,141** |
| Serie A (Itália) | 28,4% | 35,4% | 4,27 | 0,226 |

Brasileirão tem **mais que o dobro** de cartões vermelhos por jogo da Premier League (0,306 vs 0,141) e o maior volume total de cartões das seis. Bate com o achado abaixo (mais faltas por jogo também).

### Escanteios e faltas — NÃO dá pra fazer "ao longo do jogo" com o dado disponível hoje

Diferente de gol, chute e cartão, **não existe timeline de escanteio nem de falta no banco** — só totais por partida em `match_stats`/`match_stats_fotmob` (colunas `corners`, `fouls`), sem minuto. `match_events` só guarda cartões (regra já registrada no topo deste arquivo); não existe uma tabela `match_corners_fotmob` ou equivalente com minuto de cada escanteio/falta. Não é uma limitação de método desta análise — é ausência real de dado no schema atual.

O que dá pra responder com o que existe é só a diferença de **volume total** por liga (2023-2025, `match_stats`, cobertura completa nas 6 ligas):

| Liga | Escanteios/jogo | Faltas/jogo |
|---|---|---|
| Brasileirão | **10,57** | **27,22** |
| Bundesliga | 9,76 | 21,56 |
| La Liga | 9,52 | 25,25 |
| Ligue 1 | 9,45 | 24,35 |
| Premier League | 10,38 | 21,93 |
| Serie A (Itália) | 9,25 | 24,80 |

Brasileirão de novo no topo em ambos — consistente com ter mais cartões e mais faltas: times fazem mais faltas, e mais faltas geram mais cartões. Um triângulo coerente (faltas → cartões, achado 9) que os dados sustentam, mas sem conseguir decompor por minuto.

**Para ter a taxa de escanteio/falta por minuto no futuro**, seria preciso um ingestor novo com timeline de evento (o FotMob tem essa informação na tela ao vivo — não confirmado se o payload já capturado no projeto a carrega; precisaria de 1-2 chamadas de descoberta antes de generalizar, regra padrão do projeto pra APIs externas).

Descritivo, sem IC 95%.

---

## Achado 10 — de que jeito o gol é feito: cabeça, pé, escanteio, falta ou jogada ensaiada

`match_shots_fotmob` guarda `situation` (o tipo de jogada que originou o chute: `RegularPlay`, `FromCorner`, `SetPiece`, `FastBreak`, `FreeKick`, `Penalty`, `ThrowInSetPiece`, `IndividualPlay`) e `shot_type` (`RightFoot`, `LeftFoot`, `Header`, `OtherBodyParts`) — dá pra responder isso direto, sem precisar de nova tabela. **51.908 gols** (fora pênaltis batidos em disputa) analisados.

### De onde vêm os gols

| Origem da jogada | Gols | % do total | Conversão (gols/chutes) |
|---|---|---|---|
| Jogada normal (`RegularPlay`) | 32.590 | 62,8% | 10,4% |
| Pênalti | 4.474 | 8,6% | **78,9%** |
| Escanteio (`FromCorner`) | 6.435 | 12,4% | 8,4% |
| Contra-ataque (`FastBreak`) | 4.190 | 8,1% | **15,9%** |
| Jogada ensaiada de bola parada (`SetPiece`, não é escanteio nem falta direta) | 2.669 | 5,1% | 9,1% |
| Falta direta (`FreeKick`) | 836 | 1,6% | 5,1% |
| Lance de lateral ensaiado (`ThrowInSetPiece`) | 597 | 1,1% | 9,6% |
| Jogada individual (`IndividualPlay`) | 117 | 0,2% | 4,2% |

**A conversão de pênalti bate com o esperado do mundo real (76-80%)** — boa validação de que a categoria está bem populada. Contra-ataque é a situação mais eficiente depois do pênalti (quase 16% dos chutes viram gol, contra ~10% da jogada normal) — defesa desorganizada compensa menos volume. Falta direta e jogada individual são as menos eficientes (~4-5%) — times raramente treinam pra bater falta direto no gol com sucesso, e um chute solo geralmente sai sob pressão.

### E de cabeça?

| Tipo de finalização | Gols | Conversão | xG médio por chute |
|---|---|---|---|
| Pé direito | 23.407 | 10,0% | 0,099 |
| Pé esquerdo | 14.940 | 9,8% | 0,097 |
| **Cabeça** | 8.542 | **10,1%** | 0,109 |
| Outra parte do corpo (peito etc., amostra pequena) | 545 | 28,7%\* | 0,184 |

\*amostra pequena (1.902 chutes no total) — provavelmente sobrancelha, escoro no rebote a curta distância, não generalizar.

**Achado contra a intuição comum:** cabeceio **não** é menos eficiente que chute de pé — é ligeiramente melhor (10,1% de conversão contra ~10% de pé). Faz sentido geometricamente: cabeçada normalmente acontece mais perto do gol (cruzamento, escanteio, rebote na área), e essa proximidade compensa a dificuldade técnica.

### Mas cabeçada em escanteio é bem pior que cabeçada em jogo aberto

| Cabeçada, por origem | Chutes | Gols | Conversão |
|---|---|---|---|
| Escanteio | 36.683 | 3.204 | **8,7%** |
| Jogada normal (cruzamento, sobra) | 32.680 | 3.936 | **12,0%** |
| Jogada ensaiada (não-escanteio) | 12.568 | 1.091 | 8,7% |
| Contra-ataque | 647 | 143 | 22,1% |

A cabeçada clássica de escanteio (a imagem que vem à cabeça quando se fala em "gol de cabeça") **converte pior** que a cabeçada de jogo aberto — 8,7% contra 12,0%. Faz sentido: escanteio é jogada ensaiada, o adversário sabe que vem cruzamento e organiza a área; no jogo aberto, o cruzamento pega a defesa de surpresa com mais frequência.

### Juntando: quase metade dos gols de escanteio são de cabeça, mas menos da metade dos gols totais

- Dos 6.435 gols de escanteio, **3.204 (49,8%) são de cabeça** — a outra metade é pé (sobra, voleio, primeiro toque) ou outra parte do corpo.
- Do total de gols do banco, **16,5% são de cabeça** (qualquer origem), **12,4% saem de escanteio** (qualquer finalização), e só **6,2% são a combinação específica "cabeça + escanteio"**.

### Ressalva

Mesma base de dados do achado 8/9 — a cobertura de `match_shots_fotmob` não é uniforme por liga/temporada (Brasileirão só completa a partir de 2023, ver achado 8). Este achado é um agregado global de tudo que está na tabela, então está proporcionalmente mais pesado nas ligas/temporadas com mais cobertura (europeias, 2020+) — não foi refeito por liga aqui porque a pergunta original não pediu comparação entre ligas para este achado especificamente.

Descritivo, sem IC 95%.

---

## Achado 11 — bug de parsing FotMob em payload antigo, e correlação StatsBomb×FotMob (La Liga 2014/15-2015/16)

Ao importar La Liga 2014/15 (38 jogos, só Barcelona) e 2015/16 (380 jogos, temporada completa) do StatsBomb Open Data para comparar contra as estatísticas já ingeridas via FotMob, `match_stats_fotmob` estava com `total_shots`/`shots_on_target` quase todo `NULL` para essas duas temporadas — só `corners` vinha preenchido.

**Causa raiz**: o payload do FotMob para partidas antigas (pré-~2018) só traz a seção `top_stats`, sem as seções detalhadas (`shots`, `discipline`) que partidas recentes têm. `arquivos_do_claude/ingestao_fotmob.py` buscava `total_shots`/`shots_on_target` só em `shots`, que simplesmente não existe nesses payloads — mesmo `top_stats` já carregando os mesmos números (`total_shots`/`ShotsOnTarget` confirmados por inspeção direta do `stats_raw`). Corrigido com `pegar_com_fallback_top_stats()`: cai pra `top_stats` só quando `shots` está totalmente ausente do payload, nunca quando o valor individual é null (isso continua sendo lacuna real). `fouls_committed`/`yellow_cards`/`red_cards` não receberam esse fallback — `discipline` genuinamente não tem equivalente em `top_stats`.

Depois do fix, reimportar as 3.454 partidas de La Liga (`forcar=true`) expôs que o PostgREST do próprio Supabase estava em crash-loop recorrente naquela noite (`PGRST002`/`ReadTimeout`, confirmado nos logs do projeto — não é nada que o código do projeto cause ou resolva); o script `scripts/atualizar_partidas_finalizadas.py` não tinha nenhum retry, então qualquer soluço matava a execução inteira depois de já ter rodado dezenas de minutos. Ganhou `_exec_retry()` (backoff exponencial, ~8min de orçamento) em toda chamada ao Supabase.

### Correlação StatsBomb × FotMob (417 partidas, 820 linhas time-partida)

Crosswalk StatsBomb↔interno construído por (data + placar exato) com desempate por interseção de tokens do nome do time — 418/418 partidas casadas, 0 ambíguas.

**Correção feita numa revisão posterior: bug de atribuição de lado (home/away) no script de correlação.** A primeira versão resolvia qual time StatsBomb correspondia a cada lado (casa/fora) time a time, pegando "o primeiro nome que bater" — mas nomes internos como "Rayo Vallecano **de Madrid**" ou "RCD Espanyol **de Barcelona**" compartilham token com o adversário da capital ("Real **Madrid**", "**Barcelona**"), e o time visitante acabava recebendo por engano as estatísticas do time da casa (achado ao investigar partidas com diferença suspeita entre as fontes, ex. Real Madrid 10-2 Rayo Vallecano aparecia com o visitante "chutando" as mesmas 30 vezes do Real Madrid). Corrigido pra atribuição exclusiva (só 2 candidatos por partida; time da casa resolvido por interseção de token, o de fora é sempre o outro; partida descartada da amostra se os dois lados baterem com o mesmo nome ou nenhum). 7 das 418 partidas (14 linhas) ficaram ambíguas e foram descartadas — sobraram 417 partidas, 820 linhas.

| métrica | n | r (Pearson) | média SB | média FM | fm = a + b·sb |
|---|---|---|---|---|---|
| escanteios | 820 | 0,999 | 5,05 | 5,05 | fm = 0,02 + 0,997·sb |
| chutes totais | 820 | 0,904 | 12,10 | 9,90 | fm = 0,54 + 0,774·sb |
| chutes ao gol | 820 | 0,939 | 4,30 | 4,78 | fm = 0,43 + 1,013·sb |
| faltas / cartões / xG | 0-2 | — | — | — | dados insuficientes (payload antigo não tem `discipline`, ver acima) |

- **Escanteios**: praticamente 1:1 (r=0,999, 97,8% de acerto exato) — as duas fontes contam a mesma coisa.
- **Chutes totais**: FotMob conta sistematicamente **~18% menos** chutes que StatsBomb (só 12,3% de acerto exato) — provável diferença de critério do que conta como "chute" (ex. StatsBomb inclui mais desvios/bloqueios como tentativa).
- **Chutes ao gol**: na direção oposta, FotMob conta **~11% mais** que StatsBomb (52,7% de acerto exato).
- Faltas/cartões/xG não puderam ser comparados nesta amostra porque o FotMob de partida antiga genuinamente não expõe esses campos (não é lacuna do fix, é ausência real na fonte).

### Ressalva antes de usar como prior

Testado só numa liga (La Liga) e numa janela de payload "antigo" (2014-2016). Antes de aplicar `fm ≈ 0,54 + 0,774·sb` (chutes) ou `fm ≈ 0,43 + 1,013·sb` (chutes ao gol) como calibração pra outra liga/temporada sem StatsBomb, valeria confirmar que o viés não muda por liga — o motivo mais provável (diferença de critério de contagem) é da fonte FotMob em si, não da liga, mas isso é hipótese, não verificado aqui.

### Ressalva resolvida: a divergência é do payload antigo, não do FotMob atual

A pergunta acima (o viés muda por liga/temporada?) tinha uma lacuna maior por trás: o teste inteiro foi feito com payload **antigo** do FotMob (pré-2018, só `top_stats`). Não dava pra saber se o desvio (~18% menos chute, ~11% mais chute-ao-gol) era um viés de critério que persiste hoje, ou um artefato específico daquele formato de payload — porque não existe temporada completa e recente do StatsBomb Open Data pra nenhuma liga que o projeto acompanha (só ligas femininas 2023/24 + Indian Super League 2021/22, nenhuma delas no pipeline).

Solução: o projeto já tem uma fonte independente e **moderna** cobrindo as 5 grandes ligas europeias — `match_stats.xg_source='understat'` (via `arquivos_do_claude/backfill_xg_understat.py`), 10.510 linhas time-partida entre agosto/2023 e maio/2026, casando 1:1 com `match_stats_fotmob` (10.508/10.510, ambas com `total_shots` preenchido). Correlacionando Understat × FotMob nesse período:

| métrica | n | r (Pearson) | média Understat | média FotMob |
|---|---|---|---|---|
| chutes totais | 10.508 | 0,996 | 12,67 | 12,70 |
| chutes ao gol | 10.508 | 0,995 | 4,42 | 4,41 |
| escanteios | 10.508 | 0,998 | 4,84 | 4,84 |

**Sem viés em nenhuma métrica** — médias praticamente idênticas (diferença <1%) e correlação mais alta que a do StatsBomb×FotMob antigo (que tinha r=0,904/0,939 pra essas mesmas duas métricas). O desvio sistemático do Achado 11 era mesmo um artefato do payload antigo (`top_stats` sem os grupos detalhados), não um critério de contagem que persiste no FotMob atual.

**Conclusão prática: não aplicar `fm ≈ 0,54 + 0,774·sb` (chutes) nem `fm ≈ 0,43 + 1,013·sb` (chutes ao gol) a nenhuma liga/temporada atual do projeto.** Nelas o FotMob já bate direto com uma fonte independente moderna — a calibração só faria sentido pra outro caso de payload antigo (outra liga histórica pré-2018), que é justamente o cenário raro que gerou o Achado 11 em primeiro lugar.

---

## Achado 12 — índices de força por time (ataque, defesa, criação, embate, lateral, central), StatsBomb×FotMob (La Liga 2015/16)

Continuação do Achado 11: construídos 6 índices de força por time-temporada (z-score composto, 20 times de La Liga 2015/16, 38 jogos cada) a partir dos eventos brutos do StatsBomb (não dos agregados `match_stats`, que não têm passe/duelo/zona) — `arquivos_do_claude`/scripts de scratchpad, não versionados por serem exploratórios.

- **Ataque**: chutes + chutes na área + xG (por jogo).
- **Defesa**: interceptações + bloqueios + desarmes ganhos + cortes.
- **Criação**: passes-chave (`shot_assist`) + passes pra área + passes progressivos (≥15m à frente) + assistências.
- **Embate**: duelos ganhos (total) + taxa de duelos ganhos + duelos aéreos ganhos (aproximado pelo `Aerial Lost` do adversário na mesma partida — StatsBomb Open Data só registra o evento do lado perdedor).
- **Lateral / Central**: fração das ações com bola (passe+condução) nos corredores externos vs. central do campo (StatsBomb dá `location`/`end_location` em coordenadas x,y; lateral e central são espelho um do outro por construção, somam a mesma fração de ações).

### Tentativa de comparar com FotMob: só ataque tem contraparte real

Antes de tentar comparar índice a índice, testei o que o FotMob de 2015/16 realmente expõe: das 760 linhas time-partida (temporada completa), **758 têm `total_shots`/`corners` mas NULL em tudo mais** — `tackles`, `interceptions`, `blocks`, `clearances`, `duels_won`, `aerial_duels_won`, `accurate_passes`, `xg`, `big_chances` (só 2/760 preenchidos). Não é "menos granular" — para este payload antigo esses campos genuinemente não existem (mesma causa do Achado 11: só a seção `top_stats` chega, sem `defence`/`duels`/`passes`). Verifiquei também a única outra temporada masculina completa do StatsBomb (Indian Super League 2021/22, 11 times/115 jogos) como alternativa pra ter os dois lados granulares — não está no pipeline do projeto (sem liga cadastrada, sem crosswalk FotMob), então ficou fora do escopo desta rodada.

Consequência: **defesa/criação/embate/lateral/central só existem no lado StatsBomb** nesta amostra — documentados como achado StatsBomb-only, sem pretensão de comparação. Só **ataque** teve um proxy FotMob genuíno (`total_shots` + `corners`, os únicos campos populados) pra correlacionar.

| índice | StatsBomb | FotMob (proxy disponível) |
|---|---|---|
| ataque | chutes + chutes na área + xG | `total_shots` + `corners` (z-score composto) |
| defesa | interceptações+bloqueios+desarmes+cortes | — (campos ausentes no payload) |
| criação | passes-chave+passes p/área+progressivos+assist. | — (campos ausentes no payload) |
| embate | duelos ganhos+taxa+aéreos | — (campos ausentes no payload) |
| lateral/central | fração de ações por corredor (zona x,y) | — (FotMob não expõe zona de ação) |

**Correlação do índice de ataque** (20 times, z-score composto StatsBomb vs. z-score composto FotMob): **r = 0,933**, `ataque_fm ≈ 0,909 · ataque_sb` (sem intercepto relevante — ambos já centrados em zero por construção). Mais forte que chutes totais isolado (r=0,904, Achado 11) mas abaixo do escanteio isolado (r=0,999) — plausível que compor chutes+chutes na área+xG cancele parte do ruído de definição de "chute" entre as fontes, sem chegar ao nível de escanteio (que já é quase 1:1 por definição).

### Ranking (StatsBomb, 20 times, ordenado por ataque)

| time | ataque | defesa | criação | embate | lateral | central |
|---|---|---|---|---|---|---|
| Real Madrid | +2,78 | −0,90 | +1,83 | +0,10 | −0,53 | +0,53 |
| Barcelona | +2,45 | −2,12 | +1,28 | −1,15 | −1,04 | +1,04 |
| Sevilla | +0,69 | −1,00 | +0,39 | −0,13 | +0,02 | −0,02 |
| Atlético Madrid | +0,34 | +1,04 | +0,01 | +0,86 | +1,23 | −1,23 |
| Rayo Vallecano | +0,27 | −0,45 | +0,66 | −0,27 | +0,93 | −0,93 |
| Athletic Club | +0,15 | +0,27 | +0,51 | +0,54 | −1,05 | +1,05 |
| Real Sociedad | +0,03 | +0,15 | +0,31 | +0,37 | +1,83 | −1,83 |
| Celta Vigo | −0,14 | +0,62 | +0,36 | +0,57 | −2,36 | +2,36 |
| Eibar | −0,19 | +0,32 | +0,29 | +0,62 | +0,58 | −0,58 |
| Málaga | −0,22 | −0,04 | +0,31 | −0,00 | +1,05 | −1,05 |
| Espanyol | −0,39 | +0,25 | −0,69 | +0,31 | −0,13 | +0,13 |
| Valencia | −0,43 | +0,32 | −0,48 | −0,50 | −0,17 | +0,17 |
| RC Deportivo | −0,44 | +0,56 | −0,04 | +0,67 | +0,55 | −0,55 |
| Levante UD | −0,48 | −0,39 | −0,23 | −0,27 | −0,73 | +0,73 |
| Getafe | −0,49 | −0,22 | −0,73 | −0,16 | +0,86 | −0,86 |
| Granada | −0,64 | +0,20 | −0,56 | −0,39 | +0,44 | −0,44 |
| Real Betis | −0,66 | +0,75 | −1,07 | −0,21 | +0,76 | −0,76 |
| Las Palmas | −0,72 | −0,24 | −0,66 | +0,39 | −1,40 | +1,40 |
| Sporting Gijón | −0,77 | +0,43 | −0,68 | −0,42 | −0,25 | +0,25 |
| Villarreal | −1,14 | +0,45 | −0,80 | −0,92 | −0,61 | +0,61 |

Batem com a intuição futebolística da temporada: Real Madrid/Barcelona isolados no ataque; Atlético Madrid destacado em defesa+embate (identidade Simeone); Real Sociedad e Celta com os jogos mais abertos pelas pontas dessa liga.

### Ressalva

Amostra de uma liga/temporada só. `embate` (aéreos) usa uma aproximação (evento do adversário) porque o StatsBomb Open Data não marca o vencedor do duelo aéreo do lado que ganha. `lateral`/`central` são espelho perfeito por construção (fração de um total) — não são dois sinais independentes, é um eixo só.
## Achado 13 — StatsBomb Open Data: seis temporadas completas existem (não só La Liga 2015/16), e elas preenchem a lacuna de escanteio/falta por minuto

O Achado 9 registrou que não existe timeline de escanteio nem de falta no banco do projeto — só total por partida. O usuário indicou o **StatsBomb Open Data** (`github.com/statsbomb/open-data`, gratuito, evento a evento com minuto exato) como possível fonte.

**Correção de um erro meu na primeira verificação.** Checando só 2 das 18 "temporadas" de La Liga do dataset (2018/19 e 2020/21, ambas coincidentemente só com jogos do Barcelona), eu generalizei errado que **nenhuma** liga tinha temporada completa. Contando partida por partida das 18 temporadas de La Liga (não só 2), achei uma exceção real:

| Temporada de La Liga | Partidas | O que é |
|---|---|---|
| **2015/2016** | **380** | **Liga inteira** — 20 times, cada um com exatamente 38 jogos |
| As outras 17 (2004/05–2020/21, exceto 2015/16) | 7 a 38 cada | Jogos do Barcelona, e **nem sempre completos** (2004/05 só tem 7 dos 38 jogos do Barcelona daquele ano) |

Bundesliga, Ligue 1, Serie A e Premier League **masculinas** continuam sem nenhuma temporada completa (mesmo levantamento do que ficou registrado antes desta correção — um time notável cobrindo a temporada inteira, ou uma amostra de ~20-26% da liga espalhada pelos times).

**Varredura completa nas outras ~15 ligas domésticas do dataset (pedido de acompanhamento): mais 5 temporadas completas, todas em ligas femininas + uma masculina fora do "top 5" europeu.** Confirmado por contagem de jogos por time (completo = todos os times com o mesmo número de jogos):

| Liga | Temporada | Partidas | Times | Jogos/time |
|---|---|---|---|---|
| FA Women's Super League (Inglaterra) | 2023/24 | 132 | 12 | 22 — todos iguais |
| Frauen Bundesliga (Alemanha) | 2023/24 | 132 | 12 | 22 — todos iguais |
| Liga F (Espanha) | 2023/24 | 240 | 16 | 30 — todos iguais |
| Serie A Feminino (Itália) | 2023/24 | 130 | 10 | 26 — todos iguais |
| Indian Super League (masculina) | 2021/22 | 115 | 11 | 20-23 — pontos corridos completo (20 = todos contra todos ida/volta) + mata-mata dos classificados |

Ficaram de fora por estarem genuinamente incompletas (contagem desigual entre times): FA WSL 2020/21 (falta 1 jogo), FA WSL 2018/19, NWSL 2023/2018, MLS 2023 (só 6 jogos). FA WSL 2019/20 é caso especial — a temporada real foi encurtada pela pandemia, então a "incompletude" ali é do futebol de verdade, não do dataset.

**Total revisado:** das ~15 ligas domésticas de clubes no dataset, **6 temporadas são completas** — La Liga 2015/16 e as 5 acima. Nenhuma delas é uma liga que o projeto acompanha hoje (Brasileirão, La Liga atual, Bundesliga, Ligue 1, Serie A, Premier League) — a única sobreposição é La Liga, e só naquela temporada específica de 2015/16.

### Escanteio e falta por minuto, La Liga 2015/16 completa (380 partidas, evento a evento)

Baixei o evento de cada uma das 380 partidas (`events/{match_id}.json`), contei `type.name='Foul Committed'` e `type.name='Pass'` com `pass.type.name='Corner'`, por minuto. **12.136 faltas e 3.841 escanteios no total** — 31,9 faltas/jogo e 10,1 escanteios/jogo (o escanteio bate bem com a faixa que já tínhamos visto no Achado 9 pras ligas europeias via `match_stats`, 9,25–10,57/jogo — boa validação cruzada entre fontes independentes).

**Bug do relógio (2ª vez nesta frente, mesma classe do Achado 5) — pego antes de publicar, por pergunta direta do usuário.** A primeira versão desta tabela usava o campo `minute` do StatsBomb puro e mostrava um pico isolado enorme no bloco 45-50 (falta 200,3/100, escanteio 64,5/100), que eu descrevi como "o mesmo salto no intervalo do gol/chute/cartão". **Estava errado.** O StatsBomb usa `period` (1 ou 2) + `minute`, e o minuto do 2º tempo **também começa contando do 45** em vez de continuar de onde o 1º tempo parou (1º tempo termina em 46 nesta base, incluindo acréscimo; 2º tempo começa em 45) — exatamente o mesmo defeito que o Achado 5 já tinha achado e corrigido no `match_shots_fotmob`. O bloco "45-50" estava somando o fim dos acréscimos do 1º tempo com o começo do 2º tempo como se fossem contínuos, quando têm o intervalo inteiro (~15 min reais) entre os dois.

**Corrigido com relógio monótono** (`clock = minute` no 1º tempo; `clock = minute + fh_over` no 2º, `fh_over` = quanto o 1º tempo passou de 45 — mesma fórmula do Achado 5), o quadro muda:

| Bloco (15 min) | Faltas /jogo | Escanteios /jogo |
|---|---|---|
| 0-15 | 4,68 | 1,47 |
| 15-30 | 5,05 | 1,56 |
| 30-45 | 5,36 | 1,52 |
| 45-60 | 5,23 | 1,78 |
| 60-75 | 5,02 | 1,68 |
| 75-90 | 5,06 | 1,64 |
| 90+ | 1,55 | 0,45 |

**Conclusão revisada, mais modesta que a primeira versão:**
- **Falta não sobe no 2º tempo.** Fica achatada o jogo inteiro (4,68 a 5,36, sem padrão claro) — o "salto" que eu tinha reportado era, na maior parte, o artefato do relógio.
- **Escanteio ainda mostra uma diferença real, mas bem menor que o "salto" original.** 2º tempo (1,64-1,78) consistentemente acima do 1º tempo (1,47-1,56) — um efeito real, só que suave, não um pico isolado dramático.

**Não dá mais pra contar escanteio/falta como "quinto e sexto sinal" do mesmo salto de intervalo que gol/chute/cartão mostram (Achados 8/9)** — só o escanteio sustenta uma versão fraca dessa história; falta não sustenta nenhuma.

### Cartão amarelo e vermelho — mesma fonte, relógio corrigido desde o início, confirma o Achado 9 sem precisar de ajuste

Cartão sai de dois lugares no StatsBomb: dentro do evento `Foul Committed` (`foul_committed.card.name`) ou como evento próprio `Bad Behaviour` (`bad_behaviour.card.name`, ex.: reclamação sem falta). Contei os dois, já com o relógio monótono corrigido (aprendendo do erro do escanteio/falta acima).

**5,48 cartões amarelos/jogo e 0,287 vermelhos/jogo** (soma ~5,77 — um pouco acima do 4,91 que o Achado 9 achou pra La Liga 2023-2025 via FotMob; temporadas diferentes, plausível).

| Bloco (15 min) | Amarelo /jogo | Vermelho /jogo |
|---|---|---|
| 0-15 | 0,32 | 0,016 |
| 15-30 | 0,69 | 0,013 |
| 30-45 | 0,92 | 0,029 |
| 45-60 | 0,87 | 0,037 |
| 60-75 | 1,09 | 0,061 |
| 75-90 | 1,17 | 0,084 |
| 90+ | 0,44 | 0,047 |

**Isso bate exatamente com o Achado 9 (via FotMob), sem precisar de correção nenhuma desta vez:**
- **Amarelo sobe de forma constante o jogo inteiro**, sem salto no intervalo nem platô — igual ao formato já visto.
- **Vermelho se concentra muito mais no fim**: a taxa nos últimos 15 minutos (0,084/jogo) é mais de 5x a dos primeiros 15 (0,016/jogo) — bate com o Achado 6 (minuto mediano do vermelho: 72').

É uma validação cruzada genuína: mesma conclusão, fonte de dado totalmente diferente (StatsBomb vs FotMob), liga e temporada diferentes das que geraram o Achado 9. Ao contrário de escanteio/falta (que precisaram de correção), aqui o padrão já saiu certo na primeira tentativa — porque desta vez o relógio foi corrigido antes de calcular, não depois.

**O que isso NÃO prova:** é uma temporada, de uma liga, com o dado de outro fornecedor (StatsBomb, não FotMob) — não dá pra fundir com `match_shots_fotmob`/`match_events` pra virar coluna nova no banco sem decidir antes se vale a pena manter uma segunda fonte só pra essas métricas. Fica registrado como confirmação descritiva do padrão, não como pipeline novo.

Descritivo, sem IC 95%.

---

## Achado 14 — falta e cartão por zona do campo: a hipótese do "último homem" se sustenta com força

Pergunta: como se distribui falta/cartão pelas zonas do campo, e dá pra usar isso como indício de jogador predisposto a cometer falta/cartão — em particular, a hipótese de que o **último homem cometendo falta pra impedir contra-ataque** puxa o cartão pra cima? StatsBomb guarda a posição exata (x,y) e o `play_pattern` (como a jogada começou — inclui `From Counter`) de cada evento, então dá pra testar direto. Mesma base: La Liga 2015/16, 380 partidas, 12.136 faltas com localização.

### Por zona do campo

| Terço (da perspectiva de quem faz a falta) | % das faltas | Taxa de cartão na zona |
|---|---|---|
| Defensivo (perto do próprio gol) | 19,2% | **24,2%** |
| Meio-campo | 56,1% | 13,0% |
| Ofensivo | 24,7% | 9,2% |

Só 1 em cada 5 faltas sai no terço defensivo, mas é lá que o cartão sai com mais frequência — quase o dobro da taxa do meio-campo. (Lateralmente, falta se distribui 37,1% direita / 34,2% esquerda / 28,7% centro — mais nos corredores que no meio, efeito menor e não perseguido a fundo aqui.)

### Por situação de jogo (`play_pattern`)

| Situação | % das faltas | Taxa de cartão |
|---|---|---|
| Jogo normal | 49,4% | 14,8% |
| **Contra-ataque** | 3,3% | **31,4%** |
| Bola parada (lançamento/lateral/tiro de meta/escanteio) | ~42% | 11-16% |

Falta em contra-ataque é rara mas converte em cartão quase o dobro da média geral.

### O teste direto da hipótese: zagueiro/lateral + contra-ataque + terço defensivo

| Cenário | Faltas | % com cartão | % vira cartão vermelho |
|---|---|---|---|
| Base geral | 12.136 | 14,2% | 0,75% |
| Zagueiro/lateral, fora de contra-ataque | 3.705 | 18,9% | — |
| Zagueiro/lateral + contra-ataque, qualquer zona | 174 | 37,4% | — |
| **Zagueiro/lateral + contra-ataque + terço defensivo ("último homem" clássico)** | **48** | **52,1%** | **2,08%** |
| Volante + contra-ataque (segunda linha de contenção) | 119 | 30,3% | — |

**A hipótese se sustenta com força.** Quando um zagueiro/lateral comete falta durante um contra-ataque, no próprio terço defensivo, **mais da metade dessas faltas vira cartão** — 3,7x a taxa média geral — e a chance de virar vermelho é quase 3x maior que a média (falta que impede uma chance clara de gol tende a ser punida mais duro, como as regras preveem). Volante em contra-ataque (a segunda linha de contenção antes do zagueiro) mostra o mesmo efeito, um pouco mais fraco.

### Ressalvas

- **Amostra pequena no cenário mais específico** (48 faltas) — real e direcional, mas não é uma estatística robusta o suficiente pra virar peso de modelo sem mais temporadas.
- **Coordenadas dependem de `location` estar presente e o terço ser calculado do lado de quem comete a falta** — StatsBomb registra o evento na perspectiva de ataque de cada time, então terço "defensivo" aqui já é "perto do próprio gol de quem fez a falta", não precisa de ajuste extra.
- **Isso é StatsBomb (La Liga 2015/16), não a base do projeto** — mesma ressalva do resto do achado 11: confirma um padrão, não é pipeline pronto pro `match_events` (que nem guarda local do cartão hoje).

**Sobre previsão de jogador:** isto aponta pra uma direção concreta — jogadores que atuam como último homem (zagueiro central, lateral em sistema de linha 4) e times que sofrem mais contra-ataques têm exposição estrutural maior a cartão, independente de "personalidade agressiva" do jogador. Pra virar um indicador de jogador específico (não só de posição), precisaria de amostra por jogador ao longo de várias temporadas — o que essa única temporada de La Liga não permite com confiança individual.

Descritivo, sem IC 95%.

---

## Achado 15 — matriz de transição de bola entre zonas do campo, e como ela muda por força do time (base pra simulação de jogo)

Pergunta: taxas de transição da bola entre zonas do campo, visando uso futuro em simulação. Mesma base StatsBomb (La Liga 2015/16, 380 partidas), usando passe completo + condução (`Pass`/`Carry`, com local de início e fim) — 552.934 ações mapeadas numa grade de 9 zonas (3 terços de comprimento × 3 corredores de largura, mesma grade do achado 12).

### O que acontece quando o time tem a bola em cada zona (geral, todos os times)

| Zona | % vira chute | % perde a posse | % continua (passe/condução) |
|---|---|---|---|
| Defensiva-esquerda | 0,00% | 15,7% | 84,3% |
| Defensiva-centro | 0,00% | 18,0% | 82,0% |
| Defensiva-direita | 0,00% | 16,9% | 83,1% |
| Meio-esquerda | 0,01% | 12,8% | 87,2% |
| Meio-centro | 0,04% | 10,9% | 89,1% |
| Meio-direita | 0,01% | 13,8% | 86,2% |
| Ataque-esquerda | 1,40% | 21,4% | 77,2% |
| **Ataque-centro** | **19,94%** | 18,3% | 61,8% |
| Ataque-direita | 1,26% | 22,0% | 76,7% |

Ataque-centro é disparado a zona mais decisiva (quase 1 em cada 5 vezes que a bola chega lá vira chute). Perda de posse é maior nos corredores ofensivos (21-22%) que no meio da própria defesa (11-14%).

### Matriz de transição (condicional a manter a posse)

| De \ Para | Def-Esq | Def-Cen | Def-Dir | Meio-Esq | Meio-Cen | Meio-Dir | Atq-Esq | Atq-Cen | Atq-Dir |
|---|---|---|---|---|---|---|---|---|---|
| **Def-Esq** | 58,3% | 14,3% | 1,7% | 20,1% | 3,8% | 0,9% | 0,6% | 0,2% | 0,1% |
| **Def-Cen** | 11,6% | 49,7% | 11,3% | 7,7% | 10,7% | 7,6% | 0,5% | 0,4% | 0,5% |
| **Def-Dir** | 1,6% | 13,7% | 58,2% | 0,9% | 3,9% | 20,6% | 0,1% | 0,2% | 0,7% |
| **Meio-Esq** | 3,9% | 1,8% | 0,2% | 70,2% | 10,3% | 1,4% | 10,3% | 1,3% | 0,7% |
| **Meio-Cen** | 0,8% | 3,2% | 0,8% | 14,3% | 56,8% | 13,8% | 3,1% | 4,1% | 3,1% |
| **Meio-Dir** | 0,2% | 1,8% | 3,6% | 1,4% | 10,9% | 70,0% | 0,6% | 1,4% | 10,2% |
| **Atq-Esq** | 0,0% | 0,0% | 0,0% | 7,9% | 1,4% | 0,1% | 79,1% | 10,3% | 1,2% |
| **Atq-Cen** | 0,0% | 0,0% | 0,0% | 1,2% | 5,0% | 1,2% | 12,0% | 67,7% | 12,9% |
| **Atq-Dir** | 0,0% | 0,0% | 0,0% | 0,1% | 1,3% | 7,2% | 1,3% | 9,8% | 80,2% |

Dois padrões pra guardar: **a bola tende a ficar no próprio corredor** (a diagonal é sempre o maior valor de cada linha, 58-80%) e **quase nunca pula direto de defesa pra ataque numa ação só** (todas as células defesa→ataque ≤0,7%) — a progressão passa quase sempre pelo meio-campo.

### Segmentando por força do time — pedido de acompanhamento

Times classificados em 3 grupos por saldo de gol na temporada (top 7 / meio 6 / bottom 7 dos 20 times) e, separadamente, por % de passe certo (controle técnico puro, independente de resultado):

| Zona (média) | TOP 7 (saldo forte) — perda | MID 6 — perda | BOT 7 (saldo fraco) — perda |
|---|---|---|---|
| Defensiva | 14,2% | 17,6% | 19,1% |
| Meio-campo | 10,9% | 12,6% | 14,2% |
| Ataque-centro | 17,8% | 18,6% | 18,6% |
| Ataque-lados | 20,1% | 21,2% | 24,3% |

| Zona (média) | Melhor controle técnico — perda | Pior controle técnico — perda |
|---|---|---|
| Defensiva | 13,3% | 20,4% |
| Meio-campo | 9,2% | 15,5% |
| Ataque | 16,7% | 22,4% |

**Gradiente limpo e consistente:** time forte (por saldo de gol ou por % de passe) perde a bola menos em praticamente toda zona do campo, não só no ataque — efeito mais forte ainda quando segmentado por controle técnico puro (30-45% menos perda em toda zona, comparando melhor com pior).

**Achado que não é óbvio:** no ataque-centro, o time **fraco chuta mais** quando chega lá (23,3%) do que o forte (18,5%). Não é que ele cria mais — chega menos vezes na zona e, ao chegar, tende a "aproveitar logo" (situação mais desesperada/menos organizada), enquanto o time forte segura mais a posse procurando um chute melhor antes de finalizar.

### Como usar numa simulação

A matriz geral + a tabela de chute/perda por zona já dá pra montar uma cadeia de Markov simples: sorteia se a ação na zona atual vira chute, perde a posse (zona espelhada passa pro adversário) ou continua (sorteia a próxima zona pela matriz). A segmentação por força permite ajustar essas probabilidades por perfil de time em vez de usar a mesma matriz pra qualquer confronto.

### Ressalvas

- **Grade grossa (3×3 = 9 zonas).** Pra simulação mais realista, provavelmente compensa uma grade mais fina (ex.: 6×3 ou 5×3) — o código já está pronto pra isso, só trocar os limiares de `zone()`.
- **"Perda de posse" é só passe incompleto/saiu/impedimento + desarme sofrido + erro de controle** — não captura falta cometida contra o time, nem todo motivo possível de a bola sair de jogo.
- **Tiers de força têm amostra pequena** (6-7 times cada) — direção clara, mas não é uma curva suave, é um agrupamento grosso.
- **Mesma ressalva de sempre:** é uma temporada, de uma liga (La Liga 2015/16, StatsBomb) — não é pipeline do projeto, não teve validação de IC 95%, e não necessariamente generaliza pras ligas que o projeto acompanha.

Descritivo, sem IC 95%.

---

## Achado 16 — o que dá (e o que não dá) pra estimar via FotMob para ligas sem StatsBomb/Understat

Pergunta de acompanhamento aos Achados 11/15: dá pra estender a matriz de transição (Achado 15) ou os índices de força (Achado 12) pras ligas que só têm FotMob, sem Understat nem StatsBomb — Brasileirão, MLS, Championship, Libertadores etc.?

### O que **não dá**, e não é solução de calibração — é ausência de dado

A matriz de transição do Achado 15 vem de eventos `Pass`/`Carry` com local de início e fim — StatsBomb registra **cada ação com bola**, com coordenada. **O FotMob não tem isso em nenhuma liga, nenhuma era** — o único stream de evento com coordenada que ele expõe é o de chute (`match_shots_fotmob`, x/y por chute). Não existe fator de transformação, calibração ou "transformador" que reconstrua uma rede de passes a partir de dados que nunca foram capturados. O mesmo vale pra PPDA e "deep completions" (métricas de pressão do Understat, Achado 11) — também dependem de evento com local que o FotMob não tem. **Isso é limite de dado, não de método.**

### O que **dá**: o FotMob já tem o próprio xG por chute, em toda liga, e ele bate com o padrão do StatsBomb

`match_shots_fotmob` guarda x/y **e um `xg` de modelo próprio do FotMob** pra 99,3-100% dos 479.202 chutes do banco (18.823 partidas, 17 competições — incluindo Brasileirão Série A/B, Libertadores, MLS, Championship, nenhuma delas com Understat). Não é preciso estimar nada: o dado já existe, nativo, pra qualquer liga que o projeto acompanha.

Construí um mapa de valor por zona (distância ao gol × canal central/lateral) usando esse chute+resultado real (gol ou não), comparando La Liga (tem Understat) com Brasileirão Série A (não tem):

| distância ao gol | canal | La Liga: conversão | Brasileirão: conversão |
|---|---|---|---|
| ≤11m (muito perto) | central | 23,0% | 22,4% |
| 11-16,5m | central | 11,8% | 10,7% |
| 16,5-25m (entrada da área) | central | 6,0% | 5,8% |
| >25m (longe) | central | 5,2% | 4,0% |
| ≤11m | lateral | 12,3% | 6,8% (n baixo: 133 chutes) |
| 11-16,5m | lateral | 8,7% | 7,7% |
| 16,5-25m | lateral | 4,0% | 3,6% |
| >25m | lateral | 2,5% | 2,0% |

**O padrão é praticamente idêntico entre as duas ligas** (diferença ≤1,3pp na maioria das faixas, com N na casa de milhares) — central sempre converte mais que lateral na mesma distância, e a conversão cai suavemente com a distância nas duas ligas. Isso é o geometria do gol se impondo (ângulo e distância), então é esperado que generalize — mas é uma validação real, não só uma suposição: o modelo de xG do FotMob (calibrado internamente, sem StatsBomb nem Understat envolvidos) já reproduz o mesmo formato de "zona de valor" que o Achado 15 descreveu qualitativamente a partir de outra fonte inteira.

### Correção ao Achado 12: "defesa/criação/embate ausentes no FotMob" era do payload antigo, não da liga

O Achado 12 (StatsBomb×FotMob, La Liga 2015/16) tinha achado que `tackles`/`interceptions`/`accurate_passes`/`duels_won` vinham quase todo `NULL` no FotMob e concluiu que esses índices "só existem no lado StatsBomb". **Isso vale só pro payload antigo** (mesma causa do bug do Achado 11: pré-2018 só tem `top_stats`). Conferindo `match_stats_fotmob` pra partidas modernas (2023+) em qualquer liga, incluindo as sem Understat:

| liga | % linhas com `touches_opp_box` | % com `tackles` | % com `accurate_passes` |
|---|---|---|---|
| Brasileirão Série A | 94,4% | 99,9% | 99,9% |
| Brasileirão Série B | 96,3% | 99,9% | 99,9% |
| MLS | 93,2% | 100% | 100% |
| Championship | 100% | 100% | 100% |
| La Liga | 95,2% | 99,9% | 99,9% |

**Os campos de defesa/passe/duelo do FotMob existem em toda liga moderna do projeto, não só nas 5 europeias do Understat.** O índice de defesa/criação/embate do Achado 12 (construído só com StatsBomb, La Liga 2015/16) já teria contraparte real no FotMob se refeito com partidas modernas de qualquer liga — não foi feito aqui porque exigiria repetir o levantamento de eventos brutos numa temporada atual, fora do escopo desta verificação.

### Resumo prático

| o que | dá pra estender sem Understat/StatsBomb? | por quê |
|---|---|---|
| xG/valor por zona de chute | **Sim, já existe nativo** | FotMob tem xG próprio + x/y de chute em toda liga |
| Índices de defesa/criação/embate (estilo Achado 12) | **Sim, com dado moderno** | `match_stats_fotmob` tem os campos desde ~2019+, qualquer liga |
| Matriz de transição zona-a-zona (Achado 15) | **Não** | Precisa de evento de passe/condução com local — nem FotMob nem Understat expõem isso publicamente |
| PPDA real (estilo Understat) | **Não** (só nas 5 europeias que já têm Understat) | Understat calcula internamente (precisa rastrear passe pra isso), mas só publica o agregado por partida — não o evento bruto. Mesmo se quiséssemos, não dá pra pedir mais granularidade dessa fonte |
| Proxy de PPDA (aproximação, não o real) | **Sim** — ver Achado 19 | `accurate_passes` do adversário ÷ (`tackles`+`interceptions` do time) já em `match_stats_fotmob`; r=0,65-0,69 contra o PPDA real, estável por liga/temporada/força de time |

**Nota sobre o Understat especificamente**: pra calcular PPDA e deep completions o Understat precisa, por definição, rastrear cada passe com posição — a informação existe do lado deles. Mas a API pública só expõe o resultado agregado (`ppda`, `deep_completions` por time-partida, já ingerido em `match_stats`), nunca o evento passe-a-passe. Não é uma limitação de ingestão que dê pra contornar pedindo "mais dado" — é o que a fonte pública oferece. Do que o projeto tem acesso hoje, **só o StatsBomb Open Data expõe evento bruto com coordenada** o suficiente pra montar uma matriz de transição.

Descritivo, sem IC 95%.

---

## Achado 17 — a diferença entre time forte e fraco está em reter a posse, não em como a bola se move

Pergunta de acompanhamento ao Achado 15: a matriz de transição inteira muda por time, ou só a taxa de perda de posse (que o Achado 15 já tinha segmentado por tercil de força)? Construídas as matrizes 9×9 completas dos dois extremos da liga por saldo de gol — **Barcelona** (campeão, saldo +83, 38 jogos) e **Espanyol** (18º colocado, saldo −34) — a partir dos eventos brutos das 74 partidas dos dois (mesma base StatsBomb, La Liga 2015/16).

### Desfecho por zona (chute / perda de posse)

| Zona | Barcelona: perda | Espanyol: perda |
|---|---|---|
| Def-Esq | 9,1% | 19,6% |
| Def-Cen | 6,5% | 22,3% |
| Def-Dir | 8,8% | 20,6% |
| Meio-Esq | 6,2% | 13,5% |
| Meio-Cen | 6,0% | 13,7% |
| Meio-Dir | 6,4% | 15,4% |
| Atq-Esq | 14,2% | 24,1% |
| Atq-Cen | 16,2% | 19,3% |
| Atq-Dir | 14,2% | 23,2% |

| Zona | Barcelona: % vira chute | Espanyol: % vira chute |
|---|---|---|
| Atq-Esq | 0,78% | 1,45% |
| **Atq-Cen** | **13,4%** | **20,0%** |
| Atq-Dir | 0,82% | 1,25% |

**A diferença na perda de posse é brutal e consistente** — Espanyol perde a bola mais que o dobro do Barcelona em quase toda zona defensiva e de meio-campo (ex. Def-Cen: 22,3% vs 6,5%). E no ataque-centro o Espanyol chuta ~50% mais vezes ao chegar lá (20,0% vs 13,4%) — o mesmo padrão do Achado 15 ("time fraco aproveita logo em vez de segurar a posse"), agora confirmado nos dois extremos individuais da liga, não só nos tercis agregados.

### Matriz de transição (condicional a manter a posse) — Barcelona

| De \ Para | Def-Esq | Def-Cen | Def-Dir | Meio-Esq | Meio-Cen | Meio-Dir | Atq-Esq | Atq-Cen | Atq-Dir |
|---|---|---|---|---|---|---|---|---|---|
| **Def-Esq** | 56,1% | 17,8% | 3,5% | 17,3% | 4,5% | 0,8% | 0,0% | 0,1% | 0,0% |
| **Def-Cen** | 14,3% | 49,6% | 12,9% | 5,6% | 11,3% | 5,9% | 0,2% | 0,1% | 0,1% |
| **Def-Dir** | 2,8% | 14,6% | 58,5% | 1,3% | 4,3% | 18,0% | 0,1% | 0,0% | 0,3% |
| **Meio-Esq** | 3,5% | 1,7% | 0,3% | 68,3% | 12,5% | 2,0% | 9,5% | 1,5% | 0,8% |
| **Meio-Cen** | 0,5% | 1,7% | 0,6% | 12,3% | 58,4% | 14,0% | 4,2% | 4,9% | 3,3% |
| **Meio-Dir** | 0,2% | 1,3% | 3,5% | 1,7% | 13,6% | 68,6% | 0,9% | 1,5% | 8,7% |
| **Atq-Esq** | 0,0% | 0,0% | 0,0% | 7,3% | 2,2% | 0,1% | 75,5% | 13,9% | 0,9% |
| **Atq-Cen** | 0,0% | 0,0% | 0,0% | 0,9% | 4,7% | 1,2% | 12,3% | 70,4% | 10,4% |
| **Atq-Dir** | 0,0% | 0,0% | 0,0% | 0,4% | 2,2% | 9,1% | 1,5% | 10,6% | 76,3% |

### Matriz de transição — Espanyol

| De \ Para | Def-Esq | Def-Cen | Def-Dir | Meio-Esq | Meio-Cen | Meio-Dir | Atq-Esq | Atq-Cen | Atq-Dir |
|---|---|---|---|---|---|---|---|---|---|
| **Def-Esq** | 56,9% | 12,6% | 2,1% | 21,2% | 5,1% | 1,2% | 0,7% | 0,2% | 0,1% |
| **Def-Cen** | 11,0% | 49,5% | 11,1% | 7,6% | 10,5% | 8,9% | 0,3% | 0,6% | 0,6% |
| **Def-Dir** | 1,4% | 10,4% | 58,5% | 0,5% | 3,7% | 24,2% | 0,1% | 0,2% | 1,0% |
| **Meio-Esq** | 3,7% | 1,7% | 0,2% | 70,9% | 10,9% | 1,3% | 9,2% | 1,6% | 0,5% |
| **Meio-Cen** | 0,5% | 3,7% | 1,0% | 13,5% | 57,4% | 14,4% | 2,8% | 3,9% | 2,8% |
| **Meio-Dir** | 0,2% | 1,6% | 3,1% | 1,2% | 9,6% | 72,1% | 0,8% | 1,5% | 9,9% |
| **Atq-Esq** | 0,0% | 0,0% | 0,0% | 8,7% | 1,6% | 0,1% | 77,6% | 10,6% | 1,4% |
| **Atq-Cen** | 0,0% | 0,0% | 0,0% | 1,3% | 4,9% | 0,8% | 14,7% | 68,4% | 9,9% |
| **Atq-Dir** | 0,0% | 0,0% | 0,0% | 0,0% | 1,2% | 9,2% | 1,2% | 10,3% | 78,1% |

### O achado: a forma da matriz é quase idêntica — a diferença é a retenção, não a rota

Comparando célula a célula, **as duas matrizes são muito parecidas**: a diagonal domina em ambas (bola tende a ficar no próprio corredor, 49-79%), a probabilidade de pular direto de defesa pra ataque é ~0% nas duas, e a distribuição de "pra onde a bola vai quando sai de cada zona" tem o mesmo formato geral no melhor e no pior time da liga. **A diferença entre Barcelona e Espanyol não está em como a bola se move quando fica em jogo — está em com que frequência ela fica em jogo** (a tabela de perda de posse acima).

### Implicação pra simulação

Pra uma simulação Monte Carlo baseada em zona (a aplicação que o Achado 15 já apontava), isso sugere uma simplificação real: **a matriz de transição em si pode ser tratada como praticamente universal** (uma matriz só, compartilhada por todos os times), enquanto **a taxa de perda de posse por zona é o parâmetro que precisa variar por força de time** — em vez de recalibrar as duas coisas por time, recalibrar só uma reduz a complexidade do modelo sem perder o efeito real que a força de equipe tem no jogo.

### Ressalvas

- **Dois times, uma temporada.** São os extremos da tabela (1º e 18º colocado por saldo de gol), não uma amostra de vários pares forte/fraco — a generalização "matriz é universal" é uma hipótese forte apoiada em 2 pontos, não testada estatisticamente.
- **Barcelona 2015/16 é um caso extremo até pros padrões de "time forte"** (MSN no auge, título de liga+Champions) — o achado pode ser mais moderado comparando dois times de força mais parecida.
- Mesma base de sempre (StatsBomb, La Liga 2015/16) — não é dado do projeto.

Descritivo, sem IC 95%.

---

## Achado 18 — valor de elenco e Elo correlacionam com ataque/criação, mas "correlacionam negativo" com defesa é armadilha

Pergunta: há indício de que valor de elenco ou um Elo por setor interfiram na qualidade de cada dimensão (ataque/defesa/criação/embate)? O projeto **não tem Elo por setor** — `team_elo`/`team_elo_history` guardam um rating único por time (escopo liga/global), nunca separado por ataque/meio/defesa. Mas duas fontes já existentes no banco permitem testar uma versão real da pergunta: o Elo geral do próprio sistema, e valor de mercado por jogador (`player_market_value_history`, fonte SciSports, cobre 2014-12-31 até hoje — inclui a temporada 2015/16 usada nos Achados 12/15/17).

### Correlação nos 20 times de La Liga 2015/16

| índice | r (log valor de elenco) | r (Elo interno, set/2015) |
|---|---|---|
| **Ataque** | **+0,70** | **+0,74** |
| Criação | +0,51 | +0,54 |
| Defesa | −0,36 | −0,40 |
| Embate | −0,19 | −0,20 |

Valor de elenco e Elo se correlacionam fortemente entre si (r=0,81) — as duas fontes concordam sobre quem é forte, o que valida usar qualquer uma das duas como proxy de força.

### Ataque/criação: sinal real. Defesa/embate: confundido pela mesma armadilha do Achado 3

Ataque e criação correlacionam positivo com força de elenco/Elo, na direção esperada. **Defesa e embate correlacionam negativo — mas isso não quer dizer "time caro defende pior".** É o mesmo confundidor do Achado 3 desta frente: o índice de defesa conta **ações defensivas brutas** (desarme, interceptação, bloqueio, corte), e um time mais forte **precisa defender menos vezes** porque passa mais tempo com a bola — então acumula menos ações defensivas por construção, não porque os zagueiros são piores. Confirmado num caso concreto: o Barcelona tinha **3× mais valor de elenco investido em defesa** que o Espanyol, mas um índice de defesa pior (−2,12 vs +0,25) — porque jogava a maior parte da partida atacando, não defendendo.

### O teste que escapa da armadilha: valor por setor vs. retenção de posse

Comparando o valor de elenco **por setor** entre os dois extremos da liga (Barcelona x Espanyol, ver Achado 17):

| Setor | Barcelona | Espanyol | Razão |
|---|---|---|---|
| Ataque | €198,2M | €8,0M | 24,8× |
| Meio-campo | €92,7M | €5,8M | 16,0× |
| Defesa | €37,1M | €12,5M | 3,0× |
| Goleiro | €21,5M | €5,7M | 3,8× |

O gap de valor é **muito maior no ataque/meio do que na defesa** entre os dois — e isso bate com o Achado 17: a diferença de retenção de posse (perda de bola) entre eles também é proporcionalmente maior no meio-campo/defesa (onde o Espanyol perde a bola mais que o dobro) do que no ataque-centro (onde a diferença de conversão de chute, embora real, é menor em termos relativos). O padrão sugere que o dinheiro investido no setor se reflete mais em **não perder a bola** do que em **acumular ações defensivas** — a métrica de retenção de posse é a que escapa do confundidor de tempo de posse, a de ações defensivas brutas não.

### Ressalvas

- **n=20 times, uma liga, uma temporada** — direção clara e estatisticamente coerente (r consistente entre valor e Elo, duas fontes independentes concordando), mas não é validação com IC 95%.
- **Cobertura do valor de mercado é parcial**: 5-17 jogadores por time (não o elenco completo de ~25), então é mais um proxy de "onde o time investe/tem os jogadores mais caros" do que o valor total do elenco.
- **O teste "por setor" (Barcelona x Espanyol) é só 2 times** — mesma ressalva do Achado 17, hipótese com 2 pontos, não testada estatisticamente.
- Sem Elo por setor no projeto, não dá pra isolar "Elo de defesa" de "Elo geral" — só o valor de elenco por setor permite esse corte, e só pra times com cobertura de jogadores suficiente.

Descritivo, sem IC 95%.

---

## Achado 19 — proxy de PPDA a partir de dado 100% FotMob, estável entre liga/temporada/força de time

Continuação do Achado 16: PPDA (passes permitidos por ação defensiva, métrica de intensidade de pressão do Understat) foi listado como algo que "não dá" pra estimar sem Understat, por depender de evento com localização. Testei uma aproximação mais grosseira, só com campos que já existem em `match_stats_fotmob` pra qualquer liga (sem zona, sem localização): **passes certos do adversário ÷ (desarmes + interceptações do próprio time)**.

### Correlação contra o PPDA real (Understat), 10.506 partidas-time, 5 grandes ligas

| corte | n | r |
|---|---|---|
| Ligue 1 | 1.834 | 0,69 |
| La Liga | 2.278 | 0,68 |
| Serie A (Itália) | 2.280 | 0,67 |
| Premier League | 2.280 | 0,66 |
| Bundesliga | 1.834 | 0,65 |
| Temporada 2023/24 | 3.502 | 0,65 |
| Temporada 2024/25 | 3.502 | 0,68 |
| Temporada 2025/26 (parcial) | 3.502 | 0,67 |
| Tercil de Elo mais fraco | 1.619 | 0,65 |
| Tercil de Elo médio | 1.150 | 0,66 |
| Tercil de Elo mais forte | 7.737 | 0,67 |

**r=0,65-0,69 em todo corte testado** — liga, temporada e força de time. Essa estabilidade é o achado em si: não é uma correlação que só aparece numa liga específica ou um ano específico, é uma relação estrutural entre "quanto o adversário consegue passar" e "quanto o time desarma/intercepta", presente do mesmo jeito em qualquer configuração testada.

**Achado lateral**: o tercil de Elo mais forte tem PPDA médio real mais baixo (12,60) que o mais fraco (15,11) — times fortes pressionam mais alto, consistente com a intuição futebolística (só quem tem físico/tática pra sustentar tem como pressionar sem abrir espaço atrás).

### Sanity check pro Brasileirão (sem Understat, sem como validar direto)

A mesma proxy, calculada pro Brasileirão Série A (2.460 partidas-time, 2023+), dá média 15,04 — dentro da faixa das 5 ligas europeias (15,3 a 17,0 nas ligas sem torná-lo outlier). Não prova que acerta o PPDA real de lá (não existe fonte de verdade pra comparar), só que o número se comporta de forma comparável.

### Um resultado falso descartado no processo

Testada uma variante com `accurate_passes_total` (em vez de `accurate_passes`) no numerador — deu r=1,000 exato. Suspeito demais pra ser real: investigado e confirmado que `accurate_passes_total` está **sempre `NULL`** no banco (0% de cobertura, já documentado no Achado 12), então a "correlação perfeita" veio de um conjunto quase vazio depois do filtro de nulos, não de sinal genuíno. Descartado.

### Ressalvas

- É uma proxy **grosseira**: não distingue pressão no terço ofensivo (que é o que PPDA realmente mede) de desarme em qualquer parte do campo — o r=0,65-0,69 mostra correlação real, não equivalência. Não substitui o PPDA de verdade pra quem tem Understat disponível.
- Cobertura de `accurate_passes`/`tackles`/`interceptions` no FotMob só é boa (~95-100%) em payload moderno (~2019+, ver Achado 16) — a proxy não funciona em temporadas com payload antigo.
- O corte por tercil de Elo usou `team_elo` com `escopo='global'` (todas as ligas do banco, não só as 5 com Understat) — os tercis ficam desbalanceados (7.737 partidas no tercil mais forte contra 1.619 no mais fraco) porque a maioria dos times mais fracos do banco é de ligas fora do top-5 europeu. Não invalida o resultado (a correlação é medida só dentro das partidas com Understat), mas os tercis não são "top/meio/fundo" das 5 ligas especificamente.

Descritivo, sem IC 95%.

---

## Achado 20 — o que explica a diferença de retenção de posse: pressão sofrida, não pressão aplicada

Pergunta de acompanhamento ao Achado 17 (Barcelona perde a bola menos que a metade do Espanyol em quase toda zona): quanto disso é explicado por PPDA? Como a temporada 2015/16 não tem Understat (só existe a partir de 2023), calculei um PPDA equivalente **direto dos eventos brutos do StatsBomb** (mesma fonte/temporada do Achado 17, sem depender de proxy do FotMob — que também não daria, porque `tackles`/`interceptions`/`accurate_passes` não existem no payload antigo, ver Achado 11) para Barcelona e Espanyol, nas duas direções: quanto cada um **pressiona** e quanto cada um **sofre de pressão**.

### PPDA aplicado (quanto o time pressiona o adversário) — quase igual

| Time | PPDA aplicado |
|---|---|
| Barcelona | 11,04 |
| Espanyol | 11,40 |

Praticamente idêntico. **Isso não explica a diferença de retenção de posse** — os dois pressionam o adversário com intensidade parecida quando estão sem a bola.

### PPDA sofrido (quanta pressão o time recebe dos adversários) — aí está a diferença

| Time | PPDA sofrido |
|---|---|
| Barcelona | **18,68** |
| Espanyol | **11,04** |

PPDA mais alto = menos pressão sofrida. Barcelona sofre bem menos pressão dos adversários que o Espanyol — os times que enfrentam o Barcelona pressionam muito menos (provavelmente recuam, com receio de serem punidos), enquanto os que enfrentam o Espanyol pressionam bem mais.

### A cadeia causal que liga os Achados 17, 18, 19 e 20

Valor de elenco/Elo mais alto (Achado 18) → adversário respeita e pressiona menos (PPDA sofrido alto, este achado) → menos perda de posse nas zonas de construção (Achado 17) → matriz de transição mais "limpa" pro time forte. **A peça que faltava não era o quanto o próprio time pressiona — é o quanto ele é pressionado.** Isso também explica por que o índice de "embate" do Achado 12/18 não mostrou correlação forte com valor de elenco (r=−0,19): duelos brutos contam ação própria, não o que se recebe do adversário — o mesmo tipo de confundidor do índice de defesa (Achado 18), agora confirmado com um mecanismo concreto (pressão sofrida) em vez de só suspeitado.

### Ressalvas

- **Dois times, uma temporada** — mesma ressalva dos Achados 17/18: são os extremos da liga, não uma amostra de vários pares.
- **PPDA aqui é aproximado**: a definição padrão usa passe tentado (não só completo) do adversário nos 2/3 defensivos dele, e ação defensiva (desarme+interceptação+falta) do time nos 2/3 ofensivos dele — implementado com a mesma grade de terços (sem corredores) usada nos Achados 12/15/17, sobre os eventos StatsBomb já em cache. Valores absolutos podem diferir ligeiramente de PPDA "oficial" de outras fontes, mas a comparação relativa Barcelona×Espanyol é internamente consistente (mesma metodologia nos dois).

Descritivo, sem IC 95%.

---

## Achado 21 — bola parada: conversão por zona é robusta com dado 100% FotMob, mas pressão não explica volume de falta

Pergunta de acompanhamento: quais índices do FotMob interferem na previsão de falta/tiro-livre/escanteio e nos desfechos deles? Duas frentes testadas com dado agregado atual (`match_stats_fotmob`, `match_shots_fotmob`, todas as ligas modernas do projeto, sem StatsBomb/Understat envolvido).

### Frente 1 — volume de falta: a hipótese de "pressão alta gera mais falta" não se sustenta como esperado

Testada a correlação entre o proxy de PPDA aplicado (Achado 19: `accurate_passes` do adversário ÷ `tackles`+`interceptions` do time — quanto **menor**, mais pressão) e faltas cometidas, em 51.655 pares time-partida de ligas modernas:

| Comparação | r |
|---|---|
| Faltas cometidas × proxy de PPDA aplicado | −0,094 |
| Faltas cometidas × volume bruto de desarme+interceptação | +0,086 |
| **Falta por tentativa de desarme** (`faltas ÷ (tackles+interceptions)`) × proxy de PPDA aplicado | **+0,308** |

As duas primeiras são fracas — nem pressionar mais, nem tentar mais desarme isoladamente, explica bem quantas faltas um time comete no total. O terceiro corte inverte a intuição inicial: quando se normaliza por tentativa, **times que pressionam menos (proxy de PPDA mais alto) cometem falta com mais frequência por desarme tentado**, não menos. Leitura mais provável: desarme dentro de um esquema de pressão organizada (numérico, em bloco) tende a ser mais limpo; desarme de time recuado costuma ser isolado, de \"último homem\", contra jogador já lançado — maior risco de chegar atrasado e cometer falta. Ainda não controlado por Elo/força (mesmo cuidado do Achado 18 se aplica aqui: correlação fraca-a-moderada, não causal fechada).

**Prático**: proxy de PPDA sozinho é um preditor fraco de volume de falta. Não vale a pena usá-lo isolado pra estimar quantas faltas um confronto vai ter.

### Frente 2 — conversão de bola parada por zona: dado pronto, estável entre ligas

`match_shots_fotmob.situation` (`FreeKick`, `FromCorner`) + coordenada x/y do chute já bastam pra reproduzir o mapa de valor por zona do Achado 16, restrito a bola parada — nenhuma fonte externa nova necessária.

**Escanteio, canal central, por distância** (4 grandes ligas, chute com `event_type='Goal'` = gol):

| Liga | ≤11m | 11-16,5m | 16,5-25m |
|---|---|---|---|
| Brasileirão Série A | 11,8% (n=3.021) | 3,9% (n=1.427) | 3,1% (n=1.083) |
| Bundesliga | 13,3% (n=4.298) | 4,1% (n=1.390) | 3,3% (n=1.348) |
| La Liga | 12,4% (n=4.402) | 3,3% (n=1.709) | 2,7% (n=1.437) |
| Premier League | 12,9% (n=5.392) | 5,2% (n=1.792) | 3,4% (n=1.988) |

Conversão de escanteio a queima-roupa (≤11m do gol, canal central) fica em **12-13% em toda liga testada**, incluindo Brasileirão sem Understat — o mesmo padrão de estabilidade geométrica do Achado 16 (ângulo/distância domina, a fonte de dado é irrelevante).

**Tiro livre direto, canal central**: quase todo chute de falta cai fora de 16,5m (poucochíssimos dentro — faltas perto da área viram pênalti ou são cobradas indireto/cruzadas, não chute direto). Conversão de 7,6% na faixa 16,5-25m (n=4.315, a zona clássica do batedor especialista) caindo pra 4,1% acima de 25m (n=10.214).

**Anomalia não explicada**: escanteio central a >25m do gol tem 7.943 chutes registrados com 5,0% de conversão — contagem alta e conversão maior que a faixa 16,5-25m mais próxima, o que não devia acontecer por geometria pura. Hipótese mais provável: `situation='FromCorner'` do FotMob rotula toda a sequência iniciada pelo escanteio (inclusive segunda bola/rebote chutado de longe), não só o primeiro chute na área — não confirmado, fica registrado como suspeita a checar antes de usar essa faixa específica em produção.

### Prático

- **Já dá pra estimar conversão esperada de escanteio/falta por zona hoje**, em qualquer liga do projeto, sem Understat/StatsBomb — usar `situation`+x/y de `match_shots_fotmob` direto.
- **Não dá** pra prever bem o *volume* de falta/escanteio de um confronto só com o proxy de PPDA — o sinal é fraco demais sozinho; territorialidade (`touches_opp_box`, posse) provavelmente pesa mais pro volume de escanteio, mas isso ainda não foi testado.
- Ressalva: nem faltas nem PPDA aqui foram controlados por estado de jogo (time perdendo tende a cometer falta "de frustração" — mesmo viés de confundimento dos Achados 3/18) nem por força via Elo. A correlação de +0,308 é a mais interessante da frente e a menos robusta — vale reconfirmar com controle antes de usar como regra.

Descritivo, sem IC 95%.

---

## Achado 22 — pressão sofrida explica retenção jogo a jogo (não só time a time), abrindo caminho pra Markov condicionado por índice

Pergunta de acompanhamento aos Achados 12/17/18/19/20: os índices de força e o proxy de PPDA servem só pra comparar times inteiros (média de temporada), ou também explicam a variação **de um jogo pro outro do mesmo time** — o que é o requisito mínimo pra usá-los como covariável dentro de uma cadeia de Markov intra-jogo (a matriz de transição do Achado 15 precisaria de um parâmetro que mude jogo a jogo, não só time a time)?

### Frente 1 — o mesmo efeito do Achado 20, agora dentro do mesmo time, jogo a jogo

O Achado 20 comparou **médias de temporada** de Barcelona × Espanyol (2 times, 1 ponto cada) e achou que pressão sofrida (PPDA sofrida) explica a diferença de retenção entre eles. Aqui a pergunta é mais dura: **dentro dos 38 jogos do mesmo Barcelona**, jogos em que o adversário pressionou mais tiveram mais perda de posse no meio-campo/ataque? Recalculado por partida (não por temporada) a partir do mesmo cache de eventos brutos StatsBomb já usado no Achado 20:

| Time | Jogos | corr(PPDA sofrida, % perda posse meio+ataque) |
|---|---|---|
| Barcelona | 38 | **−0,463** |
| Espanyol | 38 | **−0,587** |
| Pooled (2 times) | 76 | −0,683 |

PPDA sofrida mais alto = menos pressão recebida (Achado 20). O sinal negativo confirma a mesma direção do Achado 20, mas agora **controlando força de equipe por construção** — é o mesmo time em todos os 38 jogos, só o adversário do dia muda. Isso é o que faltava provar: a pressão sofrida não é só uma média de temporada que diferencia time bom de time ruim, ela **varia jogo a jogo e essa variação prediz a retenção daquele jogo específico**, dentro do próprio time. É o requisito mínimo pra virar covariável de uma cadeia de Markov intra-jogo — sem isso, "ajustar a matriz pela força do adversário" seria só reproduzir a diferença entre os times, não uma dinâmica condicional real.

### Frente 2 — índice por jogo existe, mas é ruidoso: quantificado

Resposta à pergunta anterior sobre índices "por jogo": os ingredientes do Achado 12 (chutes, xG, desarme, interceptação, corredor) já vêm por partida no dado bruto — não é preciso nada novo pra calcular o índice em janela de 1 jogo em vez de 38. Mas o preço é ruído real, medido aqui pro Barcelona (38 jogos, StatsBomb 2015/16):

| Componente | Média por jogo | Desvio-padrão | Coeficiente de variação |
|---|---|---|---|
| xG | 2,41 | 1,04 | 43% |
| Desarme + interceptação | 19,5 | 6,29 | 32% |

Um único jogo pode ficar 40%+ acima ou abaixo da média de temporada do próprio time só por variação normal de jogo a jogo — nem toda oscilação é sinal. **Prático**: índice por jogo é calculável, mas não deve ser usado cru; precisa de encolhimento bayesiano (combinar o valor da temporada como prior com o jogo específico como atualização), do jeito que `team_strengths`/Dixon-Coles já faz pra força ofensiva/defensiva — usar o índice de 1 jogo isolado como se fosse a "verdadeira" força do time naquele dia é superestimar o quanto um jogo individual informa.

### Como as duas frentes se encaixam na simulação (Achado 15)

A Frente 1 mostra que existe uma covariável (pressão sofrida) que varia dentro do próprio time e prediz retenção jogo a jogo — candidata natural pra parametrizar a matriz de transição do Achado 15 por confronto, não só usar uma matriz genérica ou uma matriz fixa por tercil de força. A Frente 2 avisa que qualquer índice usado pra estimar "quanto esse time vai pressionar/reter hoje" antes do jogo (pré-jogo, sem saber o resultado) precisa vir da média de temporada com encolhimento — não do jogo anterior isolado.

### Ressalvas

- **Ainda só 2 times** (Barcelona, Espanyol) — a Frente 1 prova que o mecanismo existe *dentro* de um time, mas não testa se o tamanho do efeito (r≈−0,46 a −0,59) é parecido pra outros perfis de time. Estender pros 20 times exigiria recalcular PPDA por partida pra todos, o que não está no cache atual (só as 380 agregações de temporada do Achado 12 existem pra todos os 20 — não por partida; teria que buscar os eventos brutos das ~306 partidas que faltam).
- Não testado ainda: se o mesmo padrão vale pra retenção na própria zona defensiva (só meio+ataque foi medido aqui, seguindo o recorte que já rendeu sinal mais forte no Achado 17).
- PPDA aqui é o mesmo proxy aproximado (grade de terços, sem corredor) dos Achados 19/20 — ver ressalva de metodologia lá.

Descritivo, sem IC 95%.

---

## Achado 23 — bug real de extração: `tackles` por jogador está zerado por um erro de string, não por falta de dado

Pergunta de acompanhamento: por que `match_player_stats_fotmob.tackles` vem 100% `NULL` (1.082.777 linhas), enquanto o mesmo campo a nível de time (`match_stats_fotmob.tackles`) tem 99,9% de cobertura? E a cobertura de `minutes_played`/`rating` (66-72%) está concentrada nalguma liga ou é geral?

### O bug: comparando com a lição já registrada do Achado 11/12 (`fouls`→`'Fouls committed'`)

`arquivos_do_claude/ingestao_fotmob.py:340` busca o rótulo `"Tackles won"` dentro do grupo `"defense"` do JSON por jogador. Inspecionando uma linha real do `stats_raw` (já salvo no banco, sem precisar de nova raspagem):

```json
{"key": "defense", "stats": {
  "Tackles": {"key": "matchstats.headers.tackles", "stat": {"type": "integer", "value": 0}},
  "Interceptions": {"key": "interceptions", "stat": {"type": "integer", "value": 0}},
  ...
}}
```

O rótulo real é **`"Tackles"`**, não `"Tackles won"` — o código nunca encontra a chave, sempre cai no `None`. Mesma categoria de bug já documentada no projeto (rótulo em inglês que não bate com a chave interna do FotMob), só que dessa vez no extrator **por jogador**, não no de time.

### Quantificado: quanto já está recuperável sem raspar nada de novo

```sql
-- linhas com bloco "defense" no stats_raw (jsonb já salvo) x valor na coluna hoje
```

| | Linhas |
|---|---|
| Linhas com bloco `defense` no `stats_raw` | 697.299 (64,4% da tabela) |
| Dessas, com `"Tackles"` extraível do JSON | **697.299 (100%)** |
| Dessas, com a coluna `tackles` preenchida hoje | **0** |

Ou seja: **64,4% das linhas já têm o valor de desarme certinho guardado no `stats_raw`**, só não promovido pra coluna por causa do rótulo errado. Não é um gap de dado da fonte — é possível corrigir só reprocessando o JSON já armazenado (sem custo de API, sem nova raspagem).

`interceptions` **não** tem o mesmo bug (rótulo `"Interceptions"` bate certo — testado e confirmado num caso real), mas também estava sub-populado: 697.299 linhas tinham o valor extraível do `stats_raw`, e só 334.180 (47,9%) estavam na coluna.

### Backfill executado — e um incidente real no meio do caminho

Rodei o backfill de `tackles` e `interceptions` a partir do `stats_raw` já salvo (sem raspagem nova). **Primeira tentativa (um único `UPDATE` nas ~697 mil linhas de `tackles`) derrubou o banco de produção**: o volume de WAL gerado de uma vez estourou o disco (`PANIC: could not write to file "pg_wal/xlogtemp..." No space left on device`), o Postgres parou de aceitar conexões, e o Supabase auto-escalou o disco de 8GB pra 18GB de emergência (batendo no limite de 4 modificações de disco por 24h). Sem perda de dado — um `PANIC` não comita a transação em andamento, confirmado depois checando que `tackles` continuava em 0. Refeito em **14 lotes de 100 mil `id`s cada**, sem repetir o problema.

| Campo | Antes | Depois |
|---|---|---|
| `tackles` | 0 | **697.299** (64,4% da tabela) |
| `interceptions` | 343.116 | **706.550** (65,2% da tabela) |

**Lição de método**: em tabela de produção com mais de 1M linhas, nunca rodar `UPDATE` em massa numa tacada só — sempre em lotes por faixa de `id` (aqui, 100 mil por vez se mostrou seguro). O tamanho "seguro" depende do disco disponível no projeto, não tem como saber de antemão sem checar — a mesma lição de paginação do PostgREST (`.range()`) documentada nas convenções críticas deste projeto, agora do lado de escrita, não de leitura.

### Investigação dos outros campos de baixa cobertura

Aplicando a mesma técnica (comparar `stats_raw` já salvo com a coluna promovida) nos demais campos parcialmente cobertos:

| Campo | Recuperável no `stats_raw` | Na coluna hoje | Padrão |
|---|---|---|---|
| `touches_opp_box` | 697.299 | 334.180 (47,9%) | **Mesmo padrão de `interceptions`** |
| `ground_duels_won` | 642.976 | 307.367 (47,8%) | **Mesmo padrão** |
| `aerials_won` | 697.299 | 334.180 (47,9%) | **Mesmo padrão** |
| `xg` | 261.067 | 261.067 (100%) | Sem gap — ausência real por jogador |
| `xa` | 387.676 | 387.676 (100%) | Sem gap — ausência real por jogador |
| `xgot` | 261.130 | 121.394 (46,5%) | Sub-populado, mas **sem correlação com data** — provável ausência real (jogador sem chute no alvo), não bug |

**A causa do padrão "quase metade" ficou clara**: `interceptions`, `touches_opp_box`, `ground_duels_won` e `aerials_won` têm as linhas sem valor **todas concentradas entre 18/07/2026 e 26/07/2026** (uma janela de ~8 dias logo no início da ingestão desta tabela) — enquanto as linhas com valor preenchido cobrem o período inteiro, de 18/07 até hoje. Isso é o padrão clássico já visto no Achado 11: **um bug foi corrigido no meio do caminho, sem backfill retroativo das linhas antigas** — não é falta de dado da fonte, é histórico de ingestão não reprocessado. `xgot`, ao contrário, tem `NULL` espalhado por todo o período sem esse corte — condizente com ausência real (FotMob não reporta xGOT pra jogador sem chute no alvo), não bug.

**Backfill executado** (mesma técnica, lotes de 100 mil `id`s restritos à faixa afetada — `id` entre 1 e 700.000, onde estavam as linhas anteriores a 27/07/2026 — sem repetir o incidente de disco):

| Campo | Antes | Depois |
|---|---|---|
| `touches_opp_box` | 334.180 | **705.954** (65,2%) |
| `ground_duels_won` | 307.367 | **651.085** (60,1%) |
| `aerials_won` | 334.180 | **706.084** (65,2%) |

Restringir a faixa de `id` ao período do bug (em vez de varrer a tabela toda) deixou o backfill mais rápido e ainda mais seguro pro disco — só processa as linhas que de fato precisam.

### Cobertura de `minutes_played`/`rating`: uniforme entre ligas, não é problema de payload

| Liga | % com `minutes_played` | % com `rating` |
|---|---|---|
| Brasileirão Série A | 67,6% | 63,1% |
| Serie A (Itália) | 67,2% | 62,3% |
| La Liga | 71,0% | 65,8% |
| Premier League | 73,9% | 68,3% |
| Bundesliga | 77,0% | 70,8% |
| MLS | 77,0% | 70,8% |
| Championship | 76,4% | 70,4% |

Faixa estreita (67-78%) em 15 competições diferentes — não é um problema de uma liga específica ou de payload antigo (o padrão do Achado 11/12). Hipótese mais provável (não confirmada): toda a lista de relacionados/banco de reservas ganha uma linha em `match_player_stats_fotmob`, inclusive quem não entrou em campo — esses ficam sem `minutes_played`/`rating` por não terem jogado, não por falha de captura.

### Prático — o que isso destrava pra índice individual/setorial por jogo e presença/ausência

- **Correção aplicada** (`arquivos_do_claude/ingestao_fotmob.py`): troca `"Tackles won"` por `"Tackles"`. Vale pra ingestões novas a partir de agora.
- **Backfill executado** nas linhas já gravadas (ver acima) — `tackles`, `interceptions`, `touches_opp_box`, `ground_duels_won` e `aerials_won` já estão promovidos a partir do `stats_raw` existente, sem raspagem nova.
- Com o backfill feito, `match_player_stats_fotmob` já sustenta de verdade o que a resposta anterior propôs: índice individual por jogo (`rating`, `xg`, `chances_created`, `touches`, `accurate_passes`, `tackles`, `interceptions`), índice setorial por jogo (agrupando por posição), e comparação de presença/ausência normalizada por `minutes_played` — sem precisar de nenhuma raspagem nova, o dado já está no banco.
- Ressalva que continua valendo: nada disso tem localização em campo (zona) por jogador — só `match_shots_fotmob` tem x/y por jogador (chute). Desarme/interceptação por jogador dizem "quanto", não "onde".

Descritivo, sem IC 95%.

---

## Achado 24 — falta por zona e momento do jogo é real; falta por "estado do jogo" era confundida por força de equipe

Pergunta de acompanhamento ao Achado 21: dá pra estimar chance de falta por setor do campo e por momento da partida? Usando o mesmo cache de eventos brutos StatsBomb (Barcelona + Espanyol, 74 partidas, La Liga 2015/16) — é o único dado do projeto com localização E minuto por falta ao mesmo tempo; o FotMob moderno (outras ligas) só tem falta total por partida, sem zona nem minuto, mesma limitação já registrada no Achado 21.

Taxa = faltas cometidas ÷ ações com bola (`Pass`/`Carry`/`Dribble`/`Shot`/`Duel`/`Interception`/`Clearance`) na mesma zona/janela, ×1000 — normaliza por oportunidade, não é falta bruta.

### Por zona do campo — sobrevive ao controle por time

| Zona | Barcelona | Espanyol |
|---|---|---|
| Defensiva | 5,90 | 16,94 |
| Meio-campo | 8,82 | **26,86** |
| Ataque | 9,32 | 23,94 |

Meio-campo tem a maior taxa nos dois times (defesa é sempre a menor) — o padrão se mantém dentro de cada time separadamente, não é artefato de somar os dois.

### Por momento do jogo (bloco de 15min) — também sobrevive ao controle por time

| | 00-15 | 15-30 | 30-45 | 45-60 | 60-75 | 75+ |
|---|---|---|---|---|---|---|
| Barcelona | 6,18 | 9,09 | **9,88** | 6,15 | 9,36 | **10,09** |
| Espanyol | 20,65 | 24,66 | **27,81** | 21,39 | 21,13 | **25,68** |

Mesmo formato nos dois times — pico perto do intervalo (30-45) e pico no fim do jogo (75+), vale perto do início de cada tempo (00-15 e 45-60). Padrão de "fadiga por bloco de tempo" real, não confundido por time.

### Por estado do jogo (ganhando/empatando/perdendo) — **não sobrevive**, é o mesmo tipo de armadilha do Achado 3/18

Juntando os dois times, o resultado parecia bonito e intuitivo (hipótese "falta de frustração"):

| Estado | Taxa (pooled) |
|---|---|
| Ganhando | 12,71 |
| Empatando | 14,03 |
| Perdendo | **17,44** |

Só que **dentro de cada time** o padrão desaparece — e no Espanyol até inverte:

| Estado | Barcelona | Espanyol |
|---|---|---|
| Ganhando | 8,65 | **27,79** |
| Empatando | 7,89 | 24,47 |
| Perdendo | 8,43 | 20,58 |

Barcelona é essencialmente plano nos três estados (~8/1000, sem gradiente); Espanyol **cai** conforme perde, o oposto da hipótese de frustração. O resultado agregado (perdendo > empatando > ganhando) existia só porque **Espanyol comete falta ~3x mais que Barcelona em qualquer estado**, e Espanyol também é quem mais fica no estado "perdendo" na amostra (13.945 ações vs. só 4.864 do Barcelona) — o "efeito do estado" era na real o efeito de qual time domina aquele bucket, o exato padrão do Achado 3 (e do Achado 18, pra correlação de defesa/embate) com um recorte novo.

### O que sobra de real

- **Zona e momento do relógio são preditores válidos de falta**, testados com controle de time. Estado do jogo (placar) **não é** — nesta amostra, "time perde → comete mais falta" é confundido por "time fraco perde mais E comete mais falta sempre".
- **Achado lateral que já bate com o Achado 21**: Espanyol comete falta a uma taxa 2-3x maior que Barcelona em toda zona e todo momento — consistente com a hipótese ainda não fechada do Achado 21 (Frente 1) de que desarme sem pressão organizada/de time mais fraco tende a sair mais frequentemente em falta.

### Ressalvas

- 2 times, 1 temporada — mesma amostra de sempre nesta frente. Só dá pra checar "dentro do time" com 2 times; não dá pra saber se o achado de zona/momento generaliza pra times de força intermediária.
- Denominador é proxy de ações com bola, não minutagem real de posse.
- Estado do jogo aqui usa placar acumulado por ordem de `index` do evento (StatsBomb), reconstruído incluindo gol contra (`Own Goal For`/`Against`) — não usa `match_team_game_state` (Fase 2, FotMob) porque essa infraestrutura foi construída sobre chutes, não sobre falta.

Descritivo, sem IC 95%. **Lição de método reafirmada**: nunca aceitar um efeito por "estado"/"situação" sem testar dentro de cada unidade que poderia estar confundindo (aqui, o time) — é o terceiro recorte diferente (Achado 3, Achado 18, agora falta) em que a mesma armadilha aparece.

---

## Achado 25 — cartão por minuto/estado em toda liga do projeto (13.439 partidas); por zona não dá — FotMob não guarda local de cartão

Pergunta de acompanhamento ao Achado 24: dá pra estender falta-por-momento além das 2 partidas de StatsBomb, usando o que já cobre todas as ligas do projeto? `match_events` (cartão amarelo/vermelho/segundo amarelo, 61.181 linhas, 13.439 partidas distintas) é o candidato óbvio — mas cartão não é falta, é só o subconjunto que o árbitro decidiu punir, e isso importa (ver ressalva de árbitro no fim).

### Achado de dado no caminho: `minute` empilha o acréscimo em 45 e 90, mas o minuto real está escondido em `detail`

Antes de qualquer análise por momento, `minute=45` tinha 2.943 linhas (contra ~600-700 nos minutos vizinhos) e `minute=90` tinha 8.488 (contra ~970-980 nos vizinhos) — todo cartão de acréscimo caindo empilhado no minuto cheio, sem o `minute_added` que `match_shots_fotmob` já tem pra esse mesmo problema. Inspecionando `detail` (jsonb): o campo `overloadTime` já guarda o acréscimo (`{"card_raw":"Yellow","overloadTime":"3",...}`) — só não está promovido a coluna. Minuto real = `minute + overloadTime`, sem precisar de raspagem nova. Registrado aqui como achado de dado, não corrigido no schema (ficaria pra decisão de promover `overloadTime` a coluna própria, mesmo padrão do Achado 23).

### Por zona do campo: **não dá** — não é falta de técnica, é ausência real do dado

`location_x`/`location_y` de `match_events` estão **100% `NULL`** nas 61.181 linhas, e `detail` não guarda posição em lugar nenhum (só `card_raw`/`overloadTime`/`cardDescription`). Diferente do bug do Achado 23 (dado existia, só não promovido), aqui o campo genuinamente não vem do FotMob pra evento de cartão — só `match_shots_fotmob` tem x/y, e só pra chute. Sem alternativa dentro do que o projeto já ingere.

### Por momento do jogo — sobe ao longo da partida, mesmo padrão qualitativo do Achado 24, agora com N grande

Cartões por partida, minuto real corrigido (13.439 partidas):

| 00-15 | 15-30 | 30-45 | 45-60 | 60-75 | 75-90 | 90+ |
|---|---|---|---|---|---|---|
| 0,214 | 0,457 | 0,674 | 0,827 | 0,800 | **0,950** | 0,632 |

Sobe de forma quase monotônica até o fim do 2º tempo (75-90 é o pico) — mesma direção geral do Achado 24 (mais cartão/falta perto do fim), agora numa amostra ~180x maior e cobrindo todas as ligas do projeto, não só La Liga 2015/16.

### Por estado do jogo — **achatado**, reforça o Achado 24 numa escala muito maior

Reconstruído o placar a cada cartão via `match_goal_timeline` (ordenado por `clock`, incluindo gol contra) e comparado ao lado (casa/fora) que cometeu o cartão:

| Estado | Cartões | Minutos (aprox., via `match_team_game_state`) | Taxa /1000min |
|---|---|---|---|
| Ganhando | 15.988 | 929.021 | 17,21 |
| Empatando | 28.978 | 1.707.472 | 16,97 |
| Perdendo | 16.215 | 929.021 | 17,45 |

**Praticamente reto** — a diferença entre o estado mais alto (perdendo, 17,45) e o mais baixo (empatando, 16,97) é <3%. Não há sinal de "cartão de frustração" nesta escala, reforçando o Achado 24 (que já tinha achado o mesmo padrão achatado dentro do Barcelona, numa amostra 1000x menor).

### Ressalva que o próprio usuário levantou, e que confirmei ser real: **não dá pra controlar por árbitro**

Cartão depende do critério de quem apita, não só da gravidade da entrada — dois lances idênticos podem virar cartão com um árbitro rigoroso e nada com um mais tolerante. Conferido: **não existe identificador de árbitro em nenhuma tabela do projeto** (`matches` não tem coluna de árbitro). A taxa aqui é uma média sobre milhares de árbitros diferentes, sem poder isolar "o quanto do padrão por minuto/estado é comportamento de jogador vs. calibração de árbitro" — se um tipo de liga/árbitro pune mais no fim do jogo por convenção (ex.: mais atento a perda de tempo), isso entra misturado no mesmo número.

### Ressalvas adicionais

- Denominador (minutos por estado) vem de `match_team_game_state`, que cobre um universo de partidas derivado de `match_shots_fotmob` — não confirmado que é exatamente o mesmo conjunto das 13.439 partidas com cartão. Taxa é aproximada, não um IC fechado.
- Estado do jogo aqui usa `minuto_real` (cartão) comparado a `clock` (gol) — os dois resolvem o problema de acréscimo por caminhos diferentes (`overloadTime` vs. o deslocamento acumulado do Achado da migration `20260905160000`), então pode haver pequeno desalinhamento em lances bem no limite do intervalo/final — absorvido pela granularidade de 15min usada aqui, mas não zero.
- Cartão ⊂ falta grave — o achado de zona do Achado 24 (meio-campo > ataque > defesa) não foi testado aqui e não dá pra testar com o dado atual.

Descritivo, sem IC 95%.

---

## Achado 26 — o árbitro já está no banco (`match_context_fotmob`), nunca usado; e o padrão do Achado 25 sobrevive ao controle por árbitro

Pergunta de acompanhamento ao Achado 25: dá pra controlar por árbitro? Sim — `match_context_fotmob.referee` já vem do FotMob (`matchFacts.infoBox.Referee`) desde a primeira ingestão, **27.042 partidas, 98,5% preenchido, 796 árbitros distintos**, e nunca foi usado em nenhum modelo, feature ou painel do projeto (mesmo padrão de tabela capturada-e-esquecida do `player_details_fotmob`/`player_career_history_fotmob`).

### Armadilha dupla no meio do caminho, antes de confiar em qualquer número por árbitro

1. **Cobertura desigual entre tabelas**: `match_context_fotmob` cobre muito mais partida que `match_events` (cartão) — 840 partidas de La Liga e 2.325 do Brasileirão têm árbitro registrado e **zero linha de cartão**. Sem filtrar isso, vários árbitros apareciam com média de "0,00 cartão em 20+ partidas" — não é árbitro complacente, é ausência de dado.
2. **`referee = ''` (string vazia) escapa do filtro `is not null`** — 1.843 linhas. E comparar `coalesce(soma_stats, 0) = contagem_eventos` deixa passar casos onde **os dois lados estão vazios** (nenhuma fonte tem o cartão, "0 = 0" bate por acidente) — mesmo problema do Achado 5/13 (invariante que reconcilia por não ter o que reconciliar). Corrigido exigindo que `match_stats_fotmob.yellow_cards` seja genuinamente não-nulo antes de comparar.

Com os dois filtros aplicados (referee não-vazio + reconciliação com dado real): **12.988 partidas confiáveis** (de 23.479 com árbitro), cobrindo 201 árbitros com 20+ partidas cada.

### Variância por árbitro é real e grande — mas menor que o artefato de cobertura sugeria

| | Sem filtrar cobertura | Filtrado (12.988 partidas confiáveis) |
|---|---|---|
| Mínimo (cartões/partida) | 0,00 (artefato) | **1,20** |
| Máximo | 6,03 | 6,00 |
| Média | 2,35 | 3,97 |
| Desvio-padrão | 1,96 (CV 83%) | **0,77 (CV 19%)** |

Mesmo depois de tirar o artefato, um fator 5x entre o árbitro mais brando e o mais rigoroso (1,20 a 6,00 cartões/partida) — variância grande e real, exatamente a preocupação que motivou a pergunta.

### O padrão achatado do Achado 25 sobrevive ao controle por árbitro

Recalculado no subconjunto confiável, por momento (mesmo formato do Achado 25, pico em 75-90):

| 00-15 | 15-30 | 30-45 | 45-60 | 60-75 | 75-90 | 90+ |
|---|---|---|---|---|---|---|
| 0,181 | 0,385 | 0,569 | 0,692 | 0,676 | **0,793** | 0,504 |

Por estado do jogo, agora com denominador do mesmo subconjunto (`match_team_game_state`, minutos restritos às mesmas partidas):

| Estado | Taxa /1000min |
|---|---|
| Ganhando | 24,16 |
| Empatando | 22,85 |
| Perdendo | 24,59 |

Ainda achatado (diferença <8% entre extremos). E, crucial pra responder a pergunta original: testado **dentro** dos 3 árbitros com mais partidas confiáveis (Anthony Taylor, Michael Oliver, Chris Kavanagh — todos Premier League) — em nenhum dos três "perdendo" supera "ganhando" com folga (Taylor: 192 ganhando vs. 176 perdendo; Kavanagh: 140 vs. 154; Oliver: 176 vs. 175). O achado do Achado 24/25 (sem "cartão de frustração") não é um artefato de misturar árbitros brandos com rigorosos — se mantém árbitro a árbitro.

### Prático

- **O caveat do Achado 25 está fechado**: dá pra controlar por árbitro, o dado já existe, e o padrão achatado por estado sobrevive.
- **`match_context_fotmob.referee` é candidato real a feature** (rigor do árbitro daquela partida, calculável como cartões/partida histórico do árbitro) — hoje totalmente parado, apesar de 98,5% de cobertura.
- Variância por árbitro (CV 19%, fator 5x entre extremos) é ordem de grandeza maior que a variância por estado do jogo (<8%) — se o objetivo é prever cartão de uma partida específica, **quem apita importa mais que o placar do momento**.

### Ressalvas

- Só 3 árbitros testados individualmente (os de mais volume, todos Premier League) — não confirma se o padrão achatado vale pra árbitros de outras ligas/culturas de arbitragem.
- `referee` é nome em texto livre — não verificado se há variação de grafia pro mesmo árbitro (ex.: acento, nome do meio) inflando a contagem de "árbitros distintos" ou fragmentando o histórico de um mesmo árbitro em duas entradas.
- Mesma ressalva de desalinhamento `minuto_real`×`clock` do Achado 25 se aplica aqui.

Descritivo, sem IC 95%.

---

## Lição de método (vale além deste projeto)

**Invariantes internas provam que a derivação está certa. Não provam que a interpretação está.**

Aconteceu várias vezes nesta frente:

- O **Achado 3** passou em todas as invariantes (espelhamento perfeito, minutos fechando) e mesmo assim a conclusão agregada estava confundida com força de equipe.
- O **Achado 24** (falta por estado do jogo) reproduziu o mesmo confundidor do Achado 3, num recorte novo (falta, não xG) — o efeito "perdendo comete mais falta" só existia agregando dois times; dentro de cada time separadamente, desaparecia ou invertia. A checagem que expôs foi a mesma dos casos anteriores: separar por time antes de aceitar o efeito por estado.
- O **Achado 5** era um bug de ordenação que reconciliava perfeitamente em todos os totais, porque totais não têm ordem.
- O **Achado 13** (escanteio/falta via StatsBomb) reproduziu **o mesmo bug do Achado 5** — 2º tempo com minuto reiniciando em vez de continuar — numa fonte de dado completamente diferente, meses depois de já saber exatamente que padrão procurar. Só não passou pro arquivo final porque o usuário perguntou "isso não pode ser artefato do intervalo?" antes de eu dar o achado por fechado.
- O **Achado 26** (cobertura de cartão por árbitro) é uma variante do mesmo bug do Achado 5: comparar `coalesce(soma_stats, 0) = contagem_eventos` reconciliava "0 = 0" pra partidas onde **nenhuma das duas fontes tinha o dado**, não onde o cartão de fato era zero — mesmo defeito de "totais que não têm de onde discordar", agora num filtro de confiabilidade em vez de numa ordenação.
- O **Achado 11** (correlação StatsBomb×FotMob) publicou um r=0,983 pra escanteio que já parecia bom — só numa revisão pedida depois é que apareceu um bug de atribuição casa/fora (nomes tipo "Rayo Vallecano **de Madrid**" casando por engano com "Real **Madrid**") que estava jogando o r pra baixo sem parecer errado: 834 linhas com ~14 mal-atribuídas ainda dão uma correlação "boa o suficiente" pra não levantar suspeita. Corrigido, o r subiu pra 0,999. **Um coeficiente agregado plausível não garante que cada linha está emparelhada certo** — o que expôs foi olhar os maiores resíduos individuais (a partida com a maior diferença SB-FotMob), não o r em si.

Em todos os casos o que expôs o problema foi **procurar um confundidor específico** (ou o maior outlier individual), não rodar mais verificações de consistência agregada. E saber de um bug numa fonte não impede o mesmo bug de reaparecer despercebido numa fonte nova — vale a pena checar deliberadamente por ele toda vez que uma fonte externa nova trouxer relógio de partida ou crosswalk de nome de time.

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

**Cobertura de `match_shots_fotmob`/`match_goal_timeline` é muito desigual por liga-temporada (achado 8).** Não é um bug de lógica — é ingestão histórica incompleta: Brasileirão Série A só tem shotmap completo a partir de 2023 (2017-2022 têm entre 0 e 68 de 380 partidas cobertas por temporada); as cinco grandes ligas europeias viram completas em 2020. Cobertura agregada da Brasileirão nas temporadas disponíveis: 42,5% dos gols oficiais, contra 75-89% nas europeias. Não corrigido de propósito — a cobertura tende a completar sozinha com a ingestão de temporadas futuras, e preencher o histórico exigiria reingestão de temporadas antigas do FotMob, fora do escopo desta análise. **Regra de uso:** qualquer comparação entre ligas ou entre temporadas via shotmap precisa checar cobertura por liga+temporada antes — sem isso, o resultado mede completude de dado, não comportamento real.

---

## Em aberto

- **Levar qualquer uma das camadas para dentro de um modelo.** É o salto que ainda não foi dado, e o que exigiria validação com IC 95% via `api/backtest-betting.js`. O candidato mais forte agora é o **Achado 6** (resposta a cartão vermelho) — efeito de 2-3x, não os ~18% do Achado 3, e também sobrevive ao controle de força; a limitação prática é a janela de uso (quase metade dos cartões sai depois dos 75').
- **Medir o efeito completo do cartão vermelho, não só os 15 primeiros minutos.** O Achado 6 mostra o transiente (e o recorte de 5 em 5 min mostra que o platô dura o jogo inteiro), mas o `regime` usado como baseline já mistura minutos jogados em desvantagem numérica além da janela — exigiria saber quantos jogadores cada time tinha em campo minuto a minuto, o que não é guardado hoje.
- **Formalizar o recorte de 5 em 5 minutos do Achado 6 na infraestrutura, se for usado de novo.** Hoje é uma consulta ad-hoc (cruza `match_shots_fotmob` com `match_events` na hora, calculando o relógio na mão) — não uma coluna ou função versionada como o resto da frente. Vale a pena virar função/view só se essa granularidade for reaproveitada; senão, reconstruir na hora quando precisar evita manter mais uma peça de infraestrutura.
- ~~Perfil temporal por faixa de minuto~~ — **FEITO em 05/09 pro formato de gols, chutes, chutes ao gol e cartões entre ligas (Achados 8 e 9)**, e no processo apareceu um problema de cobertura de dado por liga-temporada não documentado antes (ver achado 8). Falta ainda o perfil temporal condicionado a estado de placar (achado 3/4) — essa parte específica foi começada e interrompida quando expôs o bug do Achado 5, e não foi refeita depois da correção do relógio.
- **Tempo efetivo de bola rolando**, que é o que permitiria separar a parte tática da parte mecânica no Achado 4.
- **Timeline de escanteio e falta como coluna permanente no banco.** O achado 9 mostrou que não existe hoje — só total por partida (`match_stats`). O achado 11 confirma o padrão temporal (salto no intervalo) usando uma temporada completa do StatsBomb Open Data (La Liga 2015/16, fonte externa, evento a evento), mas isso foi uma consulta pontual, não virou pipeline: continua em aberto decidir se vale ingerir isso de verdade (StatsBomb só cobre essa temporada de La Liga por completo — não dá pra generalizar pras outras ligas do projeto) ou confirmar se o payload do FotMob já capturado tem escanteio/falta por minuto (não verificado).

---

## Achado 18 — a matriz de transição do Achado 15 refeita na grade de 12 zonas do banco (4 faixas x 3 corredores)

Pergunta: a matriz de bola entre zonas (Achado 15, grade 3x3) refeita na MESMA grade de 12 zonas usada para os toques por jogador (`public.zona_campo12`, migration 20261003140000), para o simulador de Markov usar uma única definição de zona. Base: a mesma do Achado 15 (StatsBomb Open Data, La Liga 2015/16, 380 partidas, 552.934 ações que continuam). Código: `scripts/gerar_matriz_transicao_statsbomb.py` (gera `src/utils/zoneTransitionMatrix12.js`, que não deve ser editado à mão). Coordenadas do StatsBomb (120 x 80 jardas) convertidas para 105 x 68 m antes de aplicar a regra de zona do banco.

### Checagem do método: o 3x3 refeito reproduz o Achado 15
Refeito na grade 3x3 o script bate com o publicado: erro máximo **0,0004** nas taxas de desfecho e **0,0005** na matriz (só arredondamento), e o mesmo N (552.934). Isso só foi possível depois de achar a definição EXATA de "perda": testadas todas as combinações de subtipos, só `Dispossessed` + `Miscontrol` + passe `Incomplete`/`Out`/`Pass Offside` reproduz o publicado. **Drible incompleto (6.524), passe `Unknown` (2.465) e `Injury Clearance` (548) NÃO contam**; contá-los infla a perda em 1-2 pontos percentuais em toda zona.

### O que acontece quando o time tem a bola em cada uma das 12 zonas
| Zona (banco) | Ações que começam ali | % chute | % perda | % continua |
|---|---|---|---|---|
| 1 defesa / lado baixo | 47.949 | 0,0 | 15,7 | 84,3 |
| 2 defesa / centro | 66.345 | 0,0 | 18,0 | 82,0 |
| 3 defesa / lado alto | 45.068 | 0,0 | 16,9 | 83,1 |
| 4 meio / lado baixo | 122.588 | 0,0 | 12,8 | 87,2 |
| 5 meio / centro | 96.942 | 0,0 | 10,9 | 89,1 |
| 6 meio / lado alto | 118.101 | 0,0 | 13,8 | 86,2 |
| 7 ataque fora da área / lado baixo | 60.040 | 0,7 | 20,3 | 79,0 |
| 8 ataque fora da área / centro | 25.903 | **9,4** | 17,9 | 72,8 |
| 9 ataque fora da área / lado alto | 62.222 | 0,6 | 20,8 | 78,6 |
| 10 grande área / lado baixo | 5.682 | 8,5 | **33,5** | 58,0 |
| **11 grande área / centro** | 10.809 | **45,2** | 19,3 | 35,5 |
| 12 grande área / lado alto | 5.766 | 8,4 | **34,5** | 57,2 |

**O que a grade fina revela e a 3x3 escondia:** a "Ataque-Centro" (19,9% de chute) misturava duas coisas muito diferentes. Dentro da grande área, pelo centro, **45% das ações viram chute**; na faixa logo antes da área, pelo centro, só 9,4%; pelos lados da área, 8,5%. E a PERDA dentro da grande área pelos lados é a maior do campo (33-35%, contra 18-19% pelo centro): a bola que chega ao lado da área é recuperada pelo adversário uma em cada três vezes. Menor amostra por zona: 5.682 ações (as zonas da grande área).

### Para onde a bola vai (condicional a continuar)
Mantém os padrões do Achado 15: a diagonal é o maior valor de cada linha (50-77%) e nenhuma célula defesa -> grande área passa de 0,5%. Novidade da grade fina: da grande área pelos lados, ~20% das continuações voltam para o ataque fora da área do MESMO lado (cruzar/recuar) e ~21% vão para o centro da área.

### Ressalvas
- **Mesmas do Achado 15:** uma liga, uma temporada, sem IC 95%, sem validação contra o dado do projeto, nem por liga/confronto; não deve ser apresentada como específica de nenhuma liga.
- Taxa de chute é **por ação que começa na zona**, não por chegada da bola na zona.
- Lateralidade: corredor 1 do banco = `lado_y_baixo` = ESQUERDA de quem ataca, corredor 3 = `lado_y_alto` = DIREITA. Verificado com dados em 03/10/2026 nas duas fontes (ver Achado 19, seção "Lateralidade").
- Combinar com os toques por jogador (`match_player_heatmap_fotmob`) exige cuidado: a matriz descreve o que acontece com a bola, os toques descrevem onde cada jogador a toca; uma não gera a outra.
- Próxima etapa combinada com o usuário: migrar para 18 zonas (6 faixas x 3 corredores) e COMPARAR com esta de 12. O gerador recebe a grade como parâmetro, então é acrescentar uma função de zona.

---

## Achado 19 — 18 zonas contra 12: a grade fina prevê melhor a bola, e onde isso vem

Pergunta (combinada com o usuário no Achado 18): migrar a grade de 12 zonas (`zona_campo12`) para 18 e comparar. Mesma base: StatsBomb Open Data, La Liga 2015/16, 380 partidas, 667.415 ações (552.934 que continuam). Código: `scripts/gerar_matriz_transicao_statsbomb.py` (`--comparar`, `--js18`); migration `20261004120000_zona_campo18.sql`; módulo gerado `src/utils/zoneTransitionMatrix18.js`.

### Como as 18 zonas foram definidas
Um **refinamento exato** da grade de 12 (cada zona de 18 cabe dentro de uma de 12; dá para agregar de volta sem perda): 6 faixas x 3 corredores. Para escolher quais faixas dividir ao meio, testei dividir cada uma e medi o ganho de log-verossimilhança do destino/desfecho da bola (soma dos 3 corredores): **meio** 19.409 nats (corte em x=52,5 m), **ataque fora da área** 8.476 (corte em x=79,25 m), defesa 4.678, grande área 644. Foram divididas as duas primeiras; defesa e grande área ficam inteiras.

| Faixa | Região (x, metros) |
|---|---|
| defesa | 0 a 35 |
| meio baixo | 35 a 52,5 |
| meio alto | 52,5 a 70 |
| ataque fora da área, baixo | 70 a 79,25 |
| ataque fora da área, alto | a partir de 79,25, fora da grande área |
| grande área adversária | x >= 88,5 e 13,85 <= y <= 54,15 |

### Comparação fora da amostra (5 blocos contíguos de partidas, suavização de Laplace 0,5)
| Alvo da previsão | 12 zonas | 18 zonas | Ganho do 18 (nats por ação) |
|---|---|---|---|
| Fino: desfecho com destino em 18 zonas | -1,8874 | -1,7196 | **+0,1679 ± 0,0024** |
| Grosso: desfecho com destino em 12 zonas (conservador) | -1,4553 | -1,4136 | **+0,0417 ± 0,0003** |

O ganho tem o mesmo sinal nos 5 blocos. O teste FINO favorece o 18 por construção (a origem fina denuncia o destino fino); o **GROSSO** é o honesto: mesmo para prever só em 12 zonas, saber a origem em 18 reduz a perda de log-verossimilhança em ~2,9%. Isso mostra que a bola **não é "lumpable"** na grade de 12: o comportamento depende de onde dentro da zona ela está.

### O que a grade fina enxerga
- **Logo antes da área, pelo centro:** o centro "alto" (79,25 m a 88,5 m) converte **17,4%** das ações em chute; o "baixo" (70 a 79,25 m), **3,2%**. A zona de 12 (ataque fora da área / centro, 9,4%) misturava as duas. Nos lados, o "alto" perde muito mais a bola (24-25%) que o "baixo" (15%).
- **No meio-campo** as taxas de perda são praticamente iguais nas duas metades (10-14%); o ganho vem de **para onde a bola vai**: a metade alta joga mais para frente, a baixa mais para trás. Por isso o meio rende o maior ganho de verossimilhança.
- Defesa e grande área: ficam como no Achado 18 (grande área central 45,2% de chute; lados 8,5% e ~34% de perda).

### Custo
- Menor amostra por zona: 5.682 ações (igual à de 12; as zonas da grande área não foram divididas). Mais células de transição (324 contra 144): a diagonal média cai de 0,63 para 0,55, e a menor, de 0,50 para 0,43.
- **Toques por jogador (FotMob):** ~46 toques por jogador e jogo caem em 18 zonas, ~2,6 por zona (contra ~3,9 em 12). Por jogo isso é muito esparso; por jogador ao longo da temporada (dezenas de jogos) é viável, mas exige encolhimento para a média da linha. A grade nova NÃO exige coletar de novo: os pontos estão em décimos de metro e `v_toques_zona18` os reclassifica.

### Ressalvas
Mesmas do Achado 15/18: uma liga, uma temporada, sem IC 95% por célula (o erro-padrão acima é entre blocos de partidas, não por zona); a taxa de chute é por ação que começa na zona; a lateralidade (esquerda/direita) está verificada nas duas fontes (seção "Lateralidade" abaixo). A escolha das duas faixas divididas usou o mesmo conjunto que depois foi usado na comparação (dentro da amostra, para escolher; fora da amostra, só no desempenho dos blocos): o ganho da divisão em si foi enorme (milhares de nats) frente ao custo de parâmetros (~230), então a escolha é robusta, mas uma terceira divisão (defesa) ainda não foi avaliada fora da amostra.

### Lateralidade: `lado_y_baixo` é a esquerda de quem ataca (verificado nas duas fontes)
Pergunta do usuário: a lateralidade foi definida? Até aqui era uma dedução (StatsBomb) e um ponto aberto (FotMob). Verificado em 03/10/2026 cruzando a posição conhecida do jogador com a média do `y` dos seus toques:

| Posição | StatsBomb (50 jogos, `y` de 0 a 80) | FotMob (`y` de 0 a 68, ~1.900 jogadores) |
|---|---|---|
| Lateral esquerdo | 11,9 | 13,2 |
| Lateral direito | 68,7 | 53,1 |
| Ala esquerdo / direito | 10,8 / 68,4 | 16,9 / 53,5 |
| Ponta/meia esquerdo | 21,0 / 22,9 | 23,9 / 22,1 |
| Ponta/meia direito | 54,7 / 56,9 | 44,6 / 48,0 |

Nas duas fontes o lado esquerdo tem `y` baixo e o direito `y` alto, com o time sempre atacando rumo ao x máximo. Logo **`lado_y_baixo` = esquerda de quem ataca e `lado_y_alto` = direita**, e as matrizes do StatsBomb (Achados 18 e 19) valem para os toques do FotMob sem espelhar. Detalhe: a posição usada no FotMob é a principal do jogador (`player_details_fotmob.primary_position`), não o papel em cada jogo, o que só dilui o efeito (os pontas esquerdos ficam em 24, não em 13). Os nomes das zonas no banco não mudam (`lado_y_*`), para não quebrar nada; o significado está documentado aqui e nos módulos gerados.

### O mapa de calor informa o instante da ação? Não o minuto, mas a lista vem em ordem cronológica
Pergunta do usuário. Cada ponto do mapa de calor do FotMob tem só `cx`, `cy` e `r` (nenhum campo de tempo), mas a **ordem dos pontos de cada jogador é cronológica**, o que `match_player_heatmap_fotmob.pontos` preserva. Duas provas independentes (03/10/2026):
- **Pontapé inicial:** o ponto exato do centro do campo (52,5; 34) aparece na **primeira posição** da lista em 86,1% dos casos (783 de 909), contra 1,9% se a ordem fosse aleatória.
- **Chutes com minuto conhecido** (1.112 chutes de `match_shots_fotmob` casados com exatamente um ponto): correlação de **0,695** entre o minuto do chute e a posição relativa do ponto na lista; posição média 0,26 para chutes até os 30', 0,54 de 30' a 60' e 0,74 depois dos 60'.

**Precisão de estimar o minuto pela posição** (jogadores que jogaram os 90 minutos; minuto estimado = posição relativa x 90; 634 chutes): erro mediano **5,5 min**, médio 9,8 min, 72,9% dentro de 10 min e 88,5% dentro de 20 min. Serve para fases do jogo (terços, primeiro/segundo tempo, antes/depois de um gol), não para minuto exato; os toques não são uniformes no tempo, e quem entrou do banco ou saiu antes precisa usar entrada/saída (`match_lineup_fotmob`) em vez de 0 a 90.

**O que isso NÃO dá:** não há relógio comum entre jogadores; cada lista é ordenada só dentro do próprio jogador. Não dá para intercalar os toques de jogadores diferentes e reconstruir a sequência de posse da partida (quem tocou depois de quem) nem pares "bola saiu do jogador A e chegou ao B". A transição entre zonas continua vindo do StatsBomb.


## Achado 20 — o cruzamento separado do passe comum (StatsBomb, La Liga 2015/16, 18 zonas)

**Pergunta:** a matriz tratava o cruzamento (`pass.cross`) como passe comum. Isso esconde alguma coisa?

**Método:** `scripts/gerar_matriz_transicao_statsbomb.py --cruzamento` (cache `acoes_v2.json`, com a origem de cada ação: cruzamento / passe / condução / chute / falha). A matriz **não muda**: as contagens e os módulos JS de 12 e 18 zonas saem idênticos aos commitados (N = 667.415 ações; 552.934 continuações, como no Achado 15). Só se acrescentou o rótulo.

**Resultado:** 9.461 cruzamentos em 362.275 passes (2,6%); só 28,0% completos. Comparação por zona de origem:

| Zona de origem | Ação | n | Perda | Destino "grande área, centro" (entre completas) |
|---|---|---|---|---|
| ataque fora da área alto, lado | passe comum | ~15.400 / 16.500 | 28–29% | 6–7% |
| ataque fora da área alto, lado | **cruzamento** | ~3.100 / 3.400 | **73–74%** | **77–78%** |
| ataque fora da área alto, lado | junto (matriz atual) | ~18.500 / 19.900 | 36–37% | 11–12% |
| grande área, lado | passe comum | ~1.500 | 48–51% | 25–27% |
| grande área, lado | **cruzamento** | ~1.050 | 67–68% | **84%** |
| grande área, lado | junto (matriz atual) | ~2.600 | 56–58% | 43–45% |

- Cruzamento só aparece em 7 das 18 zonas (laterais da faixa de ataque e da grande área). Nas outras o rótulo não muda nada.
- Nas laterais do ataque fora da área alto, o cruzamento é ~17% dos passes e tem **perda de 73%** (contra 28% do passe comum) e quase sempre vai parar no centro da grande área. A matriz atual mistura os dois: perda de 36% e só 11–12% de chegada ao centro da área, valores que **não descrevem nenhuma das duas ações**.
- Cruzamento vindo de zona 10/12 (ataque baixo, lados) é raro (~400 em cada lado) mas igualmente arriscado (72% de perda, 71–75% de chegada ao centro da área).

**O que NÃO se concluiu:** testar por validação cruzada se "separar melhora a previsão" é vazio quando o alvo não informa se foi cruzamento: a mistura ponderada dos dois modelos reproduz a matriz junta (é a lei da probabilidade total). O ganho existe quando o simulador **decide** entre cruzar e passar (ação com custo e destino próprios), não como pura previsão da próxima ação. Não foi testado se a decisão de cruzar depende de contexto (placar, força), o que seria a parte realmente útil.

**Limites:** uma liga, uma temporada, sem IC 95%. Verificado em 25 partidas: o escanteio cobrado **não** é marcado como `cross` (266 de 266 com `cross` falso), então não entra nos números. Entram cruzamentos de **falta cobrada** (44 em 25 jogos, ~7% dos cruzamentos) e de bola rolando (569); separar por `pass.type` ficaria para uma versão futura.


## Achado 21 — volume de cruzamentos por time e jogo (FotMob): quanto é, de que depende, e o que não dá para concluir

**Pergunta:** o volume de cruzamentos que um time tenta num jogo (soma de `cruzamentos_total` dos jogadores, `match_player_stats_detalhe_fotmob`) é estável o bastante para ser entrada de um simulador? Amostra: 1.976 linhas time-jogo (988 partidas, 68 times, 5 competições; últimos jogos com `placar_confere` e >= 11 jogadores com estatística).

**Volume e dispersão.** Média 17,6 por time e jogo (percentis 10/50/90 = 5/17/31; máximo 81); precisão 24% (certos/total). Variância 102,6 contra 17,6 se fosse Poisson (~5,8x); mesmo **dentro do mesmo time** a variância é 84,8 (~4,8x). Uma binomial negativa (como a de escanteios) é o ponto de partida; Poisson subestima muito a cauda.

**De que depende** (médias por time e jogo):
- **Mando:** casa 19,9 x fora 15,4 (+4,5 por jogo; 988 de cada).
- **Elo relativo (`elo_dif`):** muito mais fraco 12,4 | mais fraco 16,4 | equilibrado 18,4 | mais forte 19,0 | muito mais forte 18,4. O efeito aparece só em quem é MAIS FRACO; acima do equilíbrio o volume praticamente não sobe.
- **Identidade do time:** variância entre médias de times 41,6 (68 times com >= 6 jogos), dentro do time 84,8; descontado o ruído amostral (~3,1), o time explicaria cerca de 30% da variância (ICC ~ 0,31). **CORREÇÃO (Achado 22):** esse 30% estava INFLADO pela Copa Libertadores (cobertura baixa do FotMob, 6,9 cruzamentos/jogo), que separava times de forma artificial. Só nas ligas brasileiras (A e B; 54 times, 10.915 time-jogos) a variância entre times é 12,2 contra 81,2 dentro: **ICC = 0,13**. O "estilo de cruzar" do time é bem menos estável do que o texto original dizia.
- **Estado do jogo** (só Brasileirão Série B, 1.260 time-jogos, tempo em `match_team_game_state`): quanto mais tempo PERDENDO, mais cruzamentos (nunca perdeu 17,8 | < 25% do tempo 18,3 | 25-50% 21,9 | >= 50% 25,1); quanto mais tempo GANHANDO, menos (nunca 22,5 | < 25% 22,2 | 25-50% 17,7 | >= 50% 14,8). **NÃO controlado por força de equipe** -- é exatamente a armadilha de ACHADOS (fase 2): quem passa o jogo ganhando é, em média, o time melhor. Só vale como hipótese; falta agregar por `faixa_forca` (e `is_home`) antes de dizer que "quem perde cruza mais".

**Ressalvas de qualidade do dado:**
- **Copa Libertadores (liga 23) destoa**: 6,9 cruzamentos por time-jogo (as outras: 13,7-20,1), com apenas 2,9 jogadores com cruzamento por jogo (contra 5,3-7,0) e 10,5% dos time-jogos com zero. Hipótese: cobertura incompleta do FotMob nessa competição (a estatística é omitida quando é zero ou ausente, não dá para separar). **Não usar o volume dessa liga sem checar.**
- A amostra é dominada pela Série B brasileira (64% das linhas); sem intervalo de confiança.
- Zero em `cruzamentos_total` ausente foi tratado como 0 (`coalesce`); só a Libertadores tem zeros significativos (0% nas demais).
- O primeiro corte por estado do jogo saiu ERRADO por NULL: `CASE WHEN mp/mt < x` com `mp` NULL (jogo sem tempo perdendo, ex.: 0 x 0) cai no `ELSE`, inflando a faixa ">= 50%". Sempre `coalesce(..., 0)` antes de comparar.

**Uso sugerido:** volume de cruzamentos = base do time + mando (+4,5) + ajuste para quem é bem mais fraco que o adversário, com dispersão de binomial negativa; destino e taxa de perda do cruzamento vêm do Achado 20 (StatsBomb), como constantes externas. Posição do cruzamento (zonas laterais) só pode ser estimada pelo mapa de calor do jogador, e só em jogos desde março/2026.


## Achado 22 — cabeceio e cruzamento: andam juntos no mesmo jogo, mas o histórico de cabeceio quase não prevê o próximo jogo

**Pergunta:** times que cabeceiam muito (ou bem) tendem a cruzar mais? Há um "índice de cabeceio"?

**Fontes de índice (no banco):** chutes de cabeça (`match_shots_fotmob.shot_type` com "head": 19% dos chutes na amostra recente) e seu xG; duelos aéreos totais (`duelos_aereos_total`, sem o número de ganhos, então só volume); `cortes_cabeca` (defensivo, só em 37% das linhas porque o FotMob omite o zero). Usei: chutes de cabeça por jogo, xG de cabeça, fração de chutes que são de cabeça e duelos aéreos, sempre da MÉDIA DOS 8 JOGOS ANTERIORES do time (a janela exclui o jogo previsto).

**Amostra:** Brasileirão A e B, 3.719 time-jogos (44 times) com >= 5 jogos anteriores e Elo conhecido. Controle: cada variável é residualizada pela média do grupo (mando x faixa de Elo relativo: < -100, -100..100, > 100), para não confundir com "time forte faz tudo mais" nem com mando.

| Relação | Correlação |
|---|---|
| cruzamentos do jogo x chutes de cabeça do MESMO jogo | **0,557** |
| chutes de cabeça (8 jogos anteriores) x cruzamentos do time, no nível "estilo" (ambos médias anteriores) | **0,546** |
| cruzamentos do jogo x chutes de cabeça ANTERIORES (bruta) | 0,045 |
| idem, controlando mando e Elo | 0,055 |
| idem, com xG de cabeça anterior | 0,072 |
| idem, com duelos aéreos anteriores | 0,064 |
| idem, com fração de chutes de cabeça anterior | 0,044 |
| cruzamentos do jogo x PRÓPRIOS cruzamentos anteriores (controlado) | 0,100 |

**Leitura:**
- No mesmo jogo, cruzar e cabecear caminham juntos (0,56): o cruzamento é a principal origem do chute de cabeça, então isso é em boa parte mecânico (causalidade provável: cruzar -> cabecear, não "bons cabeceadores -> cruzar").
- Como característica estável do time, o "estilo" também se alinha (0,55 entre as duas médias anteriores), mas **o histórico de cabeceio quase não prevê o volume do próximo jogo** (0,04 a 0,07; mesmo o histórico de cruzamentos prevê só 0,10). O jogo a jogo é dominado por ruído e adversário.
- Inclinação (controlada): ~0,6 cruzamento a mais por chute de cabeça a mais na média anterior -- pequena e sem folga estatística real (observações repetidas do mesmo time, janelas sobrepostas: o erro-padrão ingênuo de ~0,016 subestima).
- **A hipótese "bons cabeceadores cruzam mais" NÃO se sustenta como preditor**; o que existe é co-ocorrência, com a seta provável no sentido contrário.

**CORREÇÃO ao Achado 21:** o ICC de 0,31 (identidade do time explica ~30% da variância do volume de cruzamentos) estava inflado pela Libertadores. Só nas ligas brasileiras (54 times, 10.915 time-jogos): variância entre times 12,2, dentro 81,2, **ICC = 0,13**. Isso é coerente com a correlação baixa (0,10) entre cruzamentos anteriores e o próximo jogo. Consequência para o simulador: o volume de cruzamentos de um jogo é pouco previsível pelo histórico do time; usar média da liga + mando + dispersão de binomial negativa e dar peso pequeno ao histórico do time.

**Limites:** só Brasil (A e B) e janela de 8 jogos; sem IC 95% (série temporal por time, observações dependentes); "bom cabeceador" aqui é volume de chutes de cabeça, não habilidade individual (não se separou cabeceador de cruzador); duelos aéreos sem taxa de ganho.


## Achado 23 — a fraqueza aérea do adversário (ou uma zaga forte) não muda quanto o time cruza

**Pergunta:** o histórico aéreo do time ADVERSÁRIO interfere no volume de cruzamentos de um time? Uma proteção central mais forte reduz os cruzamentos?

**Método:** mesma amostra do Achado 22 (Brasileirão A e B; 3.588 time-jogos, 44 times, >= 5 jogos anteriores dos dois lados, Elo conhecido). Para cada time-jogo, características do ADVERSÁRIO na média dos 8 jogos anteriores a este (janela exclui o jogo): chutes de cabeça sofridos, xG de cabeça sofrido, fração dos chutes sofridos que são de cabeça, cortes de cabeça (`cortes_cabeca`) e cruzamentos sofridos. Controle: tudo residualizado por mando x faixa de Elo relativo (< -100, -100..100, > 100).

| Característica do adversário (8 jogos anteriores) | Correlação com os cruzamentos do time |
|---|---|
| cruzamentos sofridos por jogo | 0,106 |
| cortes de cabeça por jogo | 0,077 |
| chutes de cabeça sofridos | 0,045 |
| xG de cabeça sofrido | 0,035 |
| fração dos chutes sofridos que são de cabeça | 0,002 |

Inclinações (cruzamentos do time por unidade da variável do adversário): cruzamentos sofridos 0,25; chutes de cabeça sofridos 0,51 (1 desvio-padrão, 0,75 chute, vale ~0,4 cruzamento em ~17: ~2%); o histórico do PRÓPRIO time também pesa 0,25.

**Leitura:**
- **Não há efeito aéreo detectável**: quem sofre mais chutes de cabeça (zaga aérea fraca) não recebe mais cruzamentos de forma relevante, e quem tem zaga que corta muito não recebe menos.
- O sinal "cortes de cabeça" é **positivo**: zagas que cortam muito de cabeça são as que mais recebem cruzamentos. Mede **exposição**, não força, e vai no mesmo sentido do sinal de "cruzamentos sofridos" (0,106), o único com algum peso (mesmo peso do histórico do próprio time).
- Não há na base uma medida limpa de "força da zaga central": chutes de cabeça sofridos dependem de quantos cruzamentos o adversário recebe (circular) e `cortes_cabeca` só existe quando o FotMob não omite. Medida por zagueiro escalado (cruzando `match_lineup_fotmob` com o histórico de cada zagueiro) seria o teste que falta.

**Limites:** só Brasil (A e B), janela de 8 jogos, observações dependentes (mesmo time repetido, janelas sobrepostas: o erro-padrão ingênuo subestima), sem IC 95%. `cortes_cabeca` ausente foi tratado como zero.

**Consequência para o simulador:** reforça o Achado 22. O volume de cruzamentos é pouco previsível por histórico, do time ou do adversário; base = média da liga + mando (+4,5) + dispersão de binomial negativa, com ajuste mínimo por cruzamentos sofridos do adversário.


## Achado 24 — taxas de chute e de perda por zona: o time e o Elo quase não melhoram a previsão (quatro testes)

**Pergunta:** para o simulador de bola por zonas, vale ter taxas de transição por time? Ajustar pelo Elo? Por qual Elo? E por xG, ataque e defesa?

**Dados e método (todos fora da amostra, cronológico).** Mapa de calor do FotMob (desde março/2026) dá os TOQUES por time, jogo e zona de 12; `match_shots_fotmob` dá os chutes (12 zonas com a mesma regra); a "perda" é por time e jogo (sem posição): passes errados (`passes_total - accurate_passes`) + `perdas_posse`, ~10,6 por 100 toques. Para cada time, os 70% mais antigos dos jogos treinam e os 30% mais recentes testam; só times com >= 15 jogos (>= 12 nos testes de xG). Métrica: log-verossimilhança de Poisson (chutes ou perdas dados os toques) menos a da taxa ÚNICA de toda a amostra, dividida pelos eventos (nats por chute/perda). Positivo = melhor que a taxa única.

**1. Taxa por time (encolhimento para a taxa geral).** 163 times, 14.882 chutes de teste. Peso da média geral em toques: 5 = -0,043; 25 = -0,027; 100 = -0,011; 400 = +0,001; **1.600 = +0,005**; 6.400 = +0,003. Pouco encolhimento PIORA (ruído de poucos jogos); o melhor ganho é minúsculo. Para perda (911 time-jogos, 72.322 perdas), a taxa do time com encolhimento (3.000 toques) dá +0,007.

**2. Ajuste por Elo global (diferença time - adversário, 5 faixas).** Chutes: +0,0020 por chute (14.774). Chutes por 100 toques: 1,76 | 1,84 | 2,26 | 2,18 | 2,28 do muito mais fraco ao muito mais forte -- o efeito está só nos mais fracos (~20% menos). Perda por 100 toques: 11,96 | 11,40 | 10,89 | 10,43 | **8,72** (o muito mais forte perde 27% menos); ganho +0,0042 por perda. **Armadilha corrigida:** a estatística `perdas_posse` isolada dá só ~1 perda por 100 toques (é o "desarmado", não a perda de bola); a medida certa soma os passes errados.

**3. Qual Elo** (jogos com as escalas global e liga, 1.258 jogos). Chutes (10.476): diferença global +0,0012, diferença da liga +0,0011, nível do time no Elo global +0,0020. Perdas (737 time-jogos): +0,0039 / +0,0037 / +0,0037; taxa do próprio time +0,0058. **Diferenças entre os Elos dentro do ruído.** A escala `geral` tem só 66 linhas com mapa de calor e ficou fora.

**4. Elo xG, ataque e defesa** (Brasileirão A e B, 505 jogos; quintis de cada medida). Chutes (4.764): Elo global +0,0012 | ataque + defesa do adversário +0,0000 | Elo xG -0,0004 | defesa (xG sofrido, 8 jogos) -0,0022 | ataque (xG criado, 8 jogos) -0,0050. Perdas (175 time-jogos, 14.075 perdas): Elo global +0,0011 | Elo xG +0,0004 | ataque +0,0000 | ataque + defesa do adversário -0,0003 | defesa -0,0005. **`team_strengths` (Dixon-Coles ataque/defesa) está VAZIA no banco**; ataque e defesa foram montados como média móvel do xG dos 8 jogos anteriores. Nenhuma medida de xG supera o Elo global.

**Leitura.** (a) As taxas de chute e de perda por zona são quase as mesmas para todos os times: o ganho máximo de qualquer ajuste é de 0,1% a 0,7% de verossimilhança, bem abaixo do que a grade fina de zonas deu (0,04 a 0,17 por ação, Achado 19). (b) O único ajuste com cara de real é a perda: cai de forma contínua com o nível do time (11,96 -> 8,72 por 100 toques); nos chutes só os times bem mais fracos se desviam. (c) Elo global, da liga ou nível do time são equivalentes; Elo xG e médias de xG de 8 jogos não ajudam (ruído da janela curta + sobreposição com o que chute por toque já mede).

**Para o simulador:** taxa geral por zona para todos; ajuste por Elo global apenas na perda (e nos chutes dos times bem mais fracos); sem matriz por time.

**Limites:** o mapa de calor é de março/2026 em diante e mistura campeonatos (testes 1 a 3) ou só Brasileirão A e B (teste 4, 505 jogos; perdas com 175 time-jogos no teste); sem IC 95%, ganhos desta ordem podem ser ruído (o ganho do Elo em chutes foi de +0,0020 em uma amostra e +0,0012 em outra); faixas de Elo escolhidas por mim (100 em 100 pontos nos testes 2 e 3; quintis no 4); Poisson sem sobredispersão (as perdas variam mais do que isso); a perda não tem posição (a taxa de perda POR ZONA só existe no StatsBomb, La Liga 2015/16); atribuição de toques por zona via mapa de calor, que é aproximação.


### Achado 24 (continuação) — toques permitidos no último terço e média com decaimento no tempo

**Toques permitidos** (os toques do ADVERSÁRIO na zona de ataque dele, contra o time que defende; mapa de calor; Brasileirão A e B, 878 time-jogos, 40 times, >= 5 jogos anteriores). "Último terço" = x >= 70 m (os últimos 35 m do campo adversário); "grande área" = x >= 88,5 m e 13,85 <= y <= 54,15 m. Média de 170 toques permitidos no último terço por jogo (desvio 61; 23,6% dos toques do adversário) e 29 na grande área. Correlação no MESMO jogo com o xG sofrido: 0,33 (último terço) e **0,51** (grande área).

**Estabilidade e previsão com janela fixa de 8 jogos** (descontado o mando): o histórico prevê o próprio valor do jogo seguinte com correlação 0,22 (último terço), 0,20 (grande área) e 0,14 (xG sofrido); e prevê o xG sofrido do jogo seguinte com 0,154 (toques na grande área), 0,142 (xG sofrido anterior) e 0,125 (último terço).

**Média com decaimento (EWMA).** Peso de cada jogo anterior = 0,5 elevado a (distância em jogos − 1) / meia-vida. Correlações com o jogo seguinte (mesmos 878 time-jogos):

| Alvo (histórico → jogo seguinte) | meia-vida 2 | 4 | 8 | 16 | janela fixa 8 | todos os jogos anteriores (sem peso) |
|---|---|---|---|---|---|---|
| toques último terço → próprios | 0,196 | 0,229 | 0,247 | 0,253 | 0,221 | 0,257 |
| toques grande área → próprios | 0,191 | 0,216 | 0,227 | 0,229 | 0,203 | 0,227 |
| xG sofrido → próprio | 0,163 | 0,171 | 0,172 | 0,170 | 0,142 | 0,166 |
| toques grande área → xG sofrido | 0,148 | 0,163 | 0,165 | 0,162 | 0,154 | 0,155 |
| toques último terço → xG sofrido | 0,110 | 0,127 | 0,131 | 0,131 | 0,125 | 0,129 |

**Leitura:**
- Meia-vida **curta (2 jogos) é a pior**: dá peso demais a poucos jogos e perde sinal.
- Meia-vida de **8 a 16 jogos** é a melhor e **empata com a média de todos os jogos anteriores**: o decaimento no tempo não acrescenta nada além de "usar mais histórico". As características defensivas do time são estáveis ao longo da temporada.
- A janela fixa de 8 jogos fica **abaixo** da EWMA de meia-vida 8 em quase tudo (0,221 contra 0,247 nos toques do último terço; 0,142 contra 0,172 no xG sofrido): o corte brusco joga fora informação útil.
- O ganho de todas essas escolhas é de 0,01 a 0,03 em correlação; o erro-padrão de uma correlação com n = 878 é ~0,034, então só a diferença "meia-vida 2 pior" e "janela fixa de 8 um pouco pior" tem alguma sustentação, e mesmo assim fraca.

**Consequência (vale para os achados 21 a 24):** onde usamos a média dos 8 jogos anteriores como "histórico do time", trocar por EWMA com meia-vida de 8 a 16 jogos (ou pela média de todos os jogos anteriores da temporada) é o ajuste simples e correto; não vale uma meia-vida curta. A conclusão geral continua: toda característica de time tem estabilidade baixa (0,14 a 0,26), então o histórico entra com peso pequeno.

**Limites:** só Brasileirão A e B, sem intervalo de confiança; o "histórico" só inclui jogos com mapa de calor (março/2026 em diante); meias-vidas medidas em número de jogos, não em dias; correlações simples sem controlar a força do adversário.


## Achado 25 — o cabeceio é do jogador (estável), não do time; e de onde saem os escanteios

### Parte A — índice de cabeceio por jogador (FotMob, banco)

**Método:** jogos desde 2025, só jogadores de linha (`usual_position_id` 1 a 3). Cada jogador: primeiros 70% dos jogos treinam, últimos 30% testam; só jogadores com >= 15 jogos, >= 900 minutos no treino e >= 300 no teste. Chute de cabeça = `match_shots_fotmob.shot_type` com "head"; duelos aéreos e cortes de cabeça vêm de `match_player_stats_detalhe_fotmob`. O índice individual é a taxa por minuto do jogador encolhida para a média da posição (peso de 300, 900 ou 2.700 minutos).

| Posição | Jogadores | Chutes de cabeça por 90 | Estabilidade treino → teste | xG de cabeça por 90 (estabilidade) | Duelos aéreos por 90 → chutes de cabeça futuros | Ganho do índice (peso 300 / 900 / 2.700 min) |
|---|---|---|---|---|---|---|
| Defesa | 1.390 | 0,217 | **0,603** | 0,383 | 0,544 | +0,243 / +0,238 / +0,190 |
| Meio | 1.272 | 0,122 | **0,597** | 0,457 | 0,468 | +0,303 / +0,330 / +0,285 |
| Ataque | 860 | 0,356 | **0,717** | 0,569 | 0,637 | +0,297 / +0,282 / +0,218 |

Ganho = log-verossimilhança de Poisson do índice individual menos a da média da posição, por chute de cabeça (nats). Cortes de cabeça por 90 prevêem os chutes de cabeça futuros com 0,214 a 0,501 (cabeceio defensivo é um sinal mais fraco do ofensivo).

**Leitura:** o cabeceio é uma característica **estável do jogador** (correlação 0,60 a 0,72 entre treino e teste) e o índice individual supera a média da posição com folga (0,19 a 0,33 nats por cabeceio). Isso é de 50 a 100 vezes o ganho que qualquer ajuste por TIME, Elo ou xG deu (0,001 a 0,007, Achado 24): a informação mora no jogador, não na equipe. Duelos aéreos por 90 minutos são um bom substituto quando o jogador tem poucos chutes.

**Do jogador para o time** (4.149 time-jogos de 2026; índice de cada jogador calculado só com dados até 30/11/2025; índice da escalação = soma da taxa por 90 dos titulares com >= 60 min; 89% dos titulares eram conhecidos; os demais entram com a média da posição; correlações descontando o mando):

| Previsor | Chutes de cabeça do time no jogo | Cruzamentos do time (3.916 jogos) |
|---|---|---|
| índice da escalação | 0,143 | 0,088 |
| média dos 8 jogos anteriores do time | **0,230** | 0,097 |

A escalação prevê os cabeceios do time **pior** que o histórico recente do próprio time: o volume de cabeceio de um time no jogo depende de tática, do adversário e dos cruzamentos, não só de quem joga. Para os **cruzamentos**, nenhum dos dois ajuda, o que reforça o Achado 22 (cabecear bem não faz o time cruzar mais). A inclinação do índice da escalação sobre os cabeceios do time é de 0,64 cabeceio por unidade do índice (desvio-padrão do índice: 0,42).

**Limites da parte A:** posição pela posição habitual do jogador (não a posição que jogou naquele jogo); goleiros fora; sem intervalo de confiança; o teste do time usa um único corte temporal (treino até 30/11/2025, teste a partir de 01/01/2026) e mistura competições; não foi testado um modelo combinando índice da escalação e histórico do time.

### Parte B — de onde saem os escanteios (StatsBomb, La Liga 2015/16)

**Método:** `scripts/analisar_origem_escanteios_statsbomb.py`. No StatsBomb o evento imediatamente anterior a um escanteio é do time que DEFENDE (bloqueio 24,8%, corte 24,3%, disputa 13,3%, defesa do goleiro 23,3%); a causa é o **último lance do time que atacava** (procurado até 25 eventos antes). 3.841 escanteios em 380 jogos = **10,1 por jogo** (dois times).

| Último lance do time que atacou | % dos escanteios | Escanteios por 100 lances desse tipo |
|---|---|---|
| passe errado | 42,0% | 0,46 |
| chute | 26,4% | **11,06** |
| cruzamento errado | 14,7% | 5,95 (por 100 cruzamentos, certos ou errados) |
| drible | 7,7% | — |
| desarme sofrido (`Dispossessed`) | 7,2% | — |
| `Miscontrol` | 1,9% | — |

**Por zona de 18 onde começou o último lance** (escanteios por 100 ações que começam ali): grande área adversária 6,4 (centro) a 8,2 (lados); ataque fora da área alto 2,2 a 2,3; ataque fora da área baixo 0,2 a 0,4; meio 0,02 a 0,06; defesa 0,01. **Cerca de 91% dos escanteios** saem de ações nas duas faixas de ataque mais avançadas.

**Uso no simulador:** o escanteio é um **quarto desfecho** da ação (além de continua, chute e perda), com taxa por zona da tabela acima, relevante só nas duas faixas de ataque; o volume por time continua vindo do modelo de produção (`api/corners-model.js`, binomial negativa, calibrada sobre `match_stats`). Depois do escanteio, a bola reaparece na grande área; onde cai a cobrança (posição final do passe de escanteio) ainda NÃO foi medido.

**Limites da parte B:** uma liga e uma temporada, sem IC 95%; a atribuição ao "último lance do atacante" é aproximação (um desvio de defensor pode ser a causa real); a taxa de 11,06 por 100 chutes soma chutes de qualquer resultado; as taxas por tipo de lance excluem dribles e desarmes (a matriz do Achado 15 não os conta como ação); o FotMob não dá a posição de onde sai o escanteio (só chutes bloqueados e defendidos por zona, que daria para cruzar com os totais por time).


## Achado 26 — a matriz de transição entre ligas, ligas recentes e torneios de seleções; mediana e variância dos escanteios

**O que foi feito.** StatsBomb Open Data masculino gratuito, baixado com `scripts/comparar_competicoes_statsbomb.py`: Premier League, Serie A e Ligue 1 de 2015/16 (temporadas completas), La Liga 2015/16 (a baseline do Achado 18/19), Copa do Mundo (8 edições), Euro 2020 e 2024, Copa América 2024 e ligas recentes. **A Copa Africana de Nações foi excluída por pedido.** Como o StatsBomb pode mudar o que oferece de graça, o repositório guarda o RESTANTE completo de cada partida (todos os eventos e campos, escalações e metadados; `dados_referencia/statsbomb/completo/`, 163 MB, 2.152 partidas, fracionado em arquivos `.json.xz` de até 25 partidas, sem a Copa Africana de Nações e sem a Indian Super League), os dados REDUZIDOS de cada partida (`brutos/`, 28 MB, 56 arquivos) e também os resumos (contagens, escanteios por jogo, ids dos jogos) em `dados_referencia/statsbomb/`. `reconstruir` refaz o cache e os resumos SEM rede (verificado: os cinco resumos saem idênticos byte a byte), e `conferir` refaz as contas dos resumos.

**Método.** Matriz de 18 zonas (mesma regra dos Achados 18/19). Para cada competição, o ganho = log-verossimilhança média por ação (nats) do modelo PRÓPRIO treinado por validação cruzada em 5 blocos contíguos, menos a do modelo da La Liga 2015/16 inteira. Positivo = a matriz da La Liga serve pior para aquela competição.

| Competição | Jogos | Chute | Perda | Centro da grande área: chute / perda | Ganho próprio − La Liga |
|---|---|---|---|---|---|
| La Liga 2015/16 (base; conferência) | 380 | 1,37% | 15,8% | 45,2% / 19,3% | −0,0010 |
| Premier League 2015/16 | 380 | 1,47% | 15,6% | 45,4% / 19,5% | +0,0009 |
| Serie A 2015/16 | 380 | 1,47% | 14,9% | 47,5% / 18,9% | +0,0020 |
| Ligue 1 2015/16 | 377 | 1,28% | 15,4% | 48,1% / 20,1% | +0,0025 |
| Indian Super League 2021/22 | 115 | 1,78% | 17,5% | 50,5% / 16,3% | +0,0051 |
| Copa América 2024 | 32 | 1,53% | 13,9% | 50,4% / 17,4% | +0,0050 |
| Copa do Mundo 2018 | 64 | 1,45% | 12,7% | 50,7% / 15,6% | +0,0070 |
| Copa do Mundo 2022 | 64 | 1,18% | 11,8% | 49,1% / 18,5% | +0,0129 |
| Euro 2020 | 51 | 1,26% | 10,8% | 48,3% / 17,0% | +0,0142 |
| Euro 2024 | 51 | 1,32% | 10,2% | 49,3% / 17,1% | +0,0207 |
| La Liga 2018/19, 2019/20, 2020/21 (só Barcelona) | 34, 33, 35 | 1,25%, 1,08%, 1,11% | 10,2%, 9,6%, 9,2% | 45,2%, 42,5%, 42,9% / 15,7%, 17,6%, 17,1% | +0,0208, +0,0257, +0,0290 |
| Ligue 1 2021/22, 2022/23 (só PSG) | 26, 32 | 1,23%, 1,21% | 8,5%, 8,1% | 45,8%, 43,2% / 17,8%, 17,1% | +0,0308, +0,0400 |
| Bundesliga 2023/24 (só Bayer Leverkusen) | 34 | 1,24% | 10,0% | 42,4% / 18,3% | +0,0209 |

As Copas do Mundo de 1958 a 1990 têm 1 a 6 jogos cada (19 no total): os números oscilam muito (ganhos de −0,072 a +0,048) e não valem nada.

**Leitura.**
- **Entre ligas completas de 2015/16 a matriz da La Liga serve:** ganhos de +0,001 a +0,0025 nats por ação, dentro do que o próprio erro de amostragem permite. Premier League, Serie A e Ligue 1 se comportam como a La Liga.
- **O que muda é a taxa de perda:** 15 a 16% nas ligas de 2015/16, 10 a 14% nos torneios de seleções e 8 a 10% nas amostras de clube de elite (Barcelona, PSG, Leverkusen). O ganho da baseline própria acompanha: +0,005 a +0,021 nos torneios e +0,021 a +0,040 nas amostras de clube de elite. É de metade a quase o total do que a grade de 18 zonas ganhou sobre a de 12 no teste conservador (+0,042, Achado 19) e de 10 a 40 vezes o ganho de ajustar por Elo ou por time (Achado 24).
- **Época dos dados ou nível do time? Não se separa com estes dados.** As ligas parciais recentes são todas as partidas de UM clube de elite com muita posse (Barcelona, PSG, Leverkusen, com os adversários comuns); os torneios têm seleções de nível alto. A Indian Super League 2021/22, de época recente e com 11 times, tem perda de 17,5%, tão alta quanto (ou maior que) as ligas de 2015/16: isso é um indício contra "a marcação da época explica tudo" e a favor do nível do time (que cai de forma contínua com o Elo, 11,96 a 8,72 perdas por 100 toques, Achado 24), mas não é prova. Para separar de verdade faltam ligas recentes COMPLETAS, que o StatsBomb gratuito não tem.
- **Consequência para o simulador:** usar a matriz da La Liga como base e ajustar a taxa de perda pelo nível do time e do contexto (clube de elite ou seleção: perda de 8 a 14%; liga média: 15 a 16%). A estrutura de destino (para onde a bola vai a partir de cada zona) pode ficar a mesma.

**Mediana e variância dos escanteios (StatsBomb, total do jogo = os dois times).**

| Grupo | Jogos | Média | Mediana | Intervalo interquartil | Variância | Variância / média | r (NB, método bruto) |
|---|---|---|---|---|---|---|---|
| Ligas 2015/16 (4 ligas) | 1.517 | 10,20 | 10 | 8 a 12 | 12,23 | 1,20 | 51 |
| Torneios de seleções modernos (Copa 2018/22, Euro 2020/24, Copa América 2024) | 262 | 9,11 | 9 | 7 a 11 | 12,12 | 1,33 | 28 |
| Ligas recentes (clubes de elite e Indian Super League) | 309 | 9,45 | 9 | 7 a 11 | 11,85 | 1,25 | 37 |
| Copas do Mundo 1958 a 1990 (19 jogos; só para registro) | 19 | 11,32 | 10 | 9 a 13,5 | 18,89 | 1,67 | 17 |

Por time (um valor por time e jogo): ligas 2015/16 média 5,10, mediana 5, variância 8,00 (razão 1,57); torneios modernos média 4,56, mediana 4, variância 7,69 (razão 1,69); ligas recentes média 4,72, mediana 4, variância 7,65 (razão 1,62).

Por liga 2015/16 (total do jogo): La Liga média 10,11, mediana 10, variância 11,68 (r bruto 65); Premier League 10,81, 10, 12,99 (54); Serie A 10,37, 10, 11,57 (89); Ligue 1 9,52, 9, 11,89 (38). Ligas atuais (banco, 2021 a 2025, mesma conta): média de 9,29 (Serie A) a 10,31 (Premier League), mediana 9 a 10, variância 10,9 a 11,5 (razão 1,12 a 1,23). O r bruto é `média² / (variância − média)`: **sem condicionar no λ de cada partida**, é um teto de dispersão (r menor = mais dispersão), diferente do r de `league_model_params` (calibrado pelo resíduo de Pearson condicionado, `api/corners-model.js`), que vale 188 (Premier League), 68 (Serie A), 51 (Brasileirão A), 41 (La Liga), 30 (Bundesliga) e 25 (Ligue 1).

**Leitura dos escanteios.** A mediana do total é 9 a 10 em todos os grupos modernos; os torneios têm 1 escanteio a menos por jogo (9,1 contra 10,2) e dispersão um pouco maior (razão 1,33 contra 1,20), mas com 262 jogos a diferença de variância não é separável do ruído. A razão variância/média de 1,2 a 1,3 significa quase Poisson: com média de 10, r de 40 a 50 já descreve o total do jogo. A taxa de escanteios por chute e por cruzamento é parecida entre competições (11 a 14 por 100 chutes; 5 a 8 por 100 cruzamentos; 26% a 41% dos escanteios saem de um chute como último lance do atacante).

**Limites:** uma temporada por liga em 2015/16; ligas recentes parciais com um clube de elite cada (viés de seleção forte); torneios com 32 a 64 jogos cada e sem intervalo de confiança; variância bruta de total do jogo com poucas dezenas de jogos oscila muito (razões de 0,82 a 1,96 nos torneios isolados); StatsBomb pode ter mudado a marcação de passes errados entre 2015/16 e 2018+ (não verificável aqui).


## Achado 27 — as taxas dos eventos ao longo do jogo e a duração das ações (para simular a bola no tempo)

**Para quê.** Uma simulação no tempo (cadeia semi-Markov) precisa de duas peças que a matriz de transição não tem: o ritmo dos eventos por janela do jogo e quanto dura cada ação (o tempo de permanência na zona). `scripts/analisar_tempo_eventos_statsbomb.py` mede as duas no StatsBomb (La Liga 2015/16, 380 jogos, os dois times somados); o resumo versionado está em `dados_referencia/statsbomb/tempo_la_liga_2015_16.json` e a análise também roda a partir dos dados reduzidos versionados (`--brutos`, sem rede; as durações guardadas são arredondadas a 0,01 s). Os chutes e gols por janela também foram medidos no banco (FotMob, Premier League, La Liga, Serie A, Ligue 1 e Bundesliga, 2021 a 2025).

**Por janela (StatsBomb, por minuto de jogo; os acréscimos usam a duração média até o último evento: 0,8 min no 1º tempo e 3,3 min no 2º, aproximação):**

| Janela | Ações/min | Chutes/min | Gols/min | % perda | Faltas/min | Escanteios/min | Cruzamentos/min |
|---|---|---|---|---|---|---|---|
| 1-15 | 20,76 | 0,216 | 0,0258 | 16,0 | 0,312 | 0,098 | 0,243 |
| 16-30 | 19,26 | 0,244 | 0,0274 | 15,6 | 0,336 | 0,104 | 0,273 |
| 31-45 | 18,61 | 0,245 | 0,0256 | 15,6 | 0,357 | 0,102 | 0,266 |
| acréscimo 1º tempo | 15,41 | 0,305 | 0,0350 | 16,8 | 0,327 | 0,207 | 0,280 |
| 46-60 | 19,50 | 0,278 | 0,0295 | 15,9 | 0,345 | 0,111 | 0,291 |
| 61-75 | 17,71 | 0,267 | 0,0263 | 15,4 | 0,334 | 0,113 | 0,266 |
| 76-90 | 17,55 | 0,274 | 0,0339 | 15,2 | 0,340 | 0,110 | 0,272 |
| acréscimo 2º tempo | 15,45 | 0,303 | 0,0345 | 18,6 | 0,396 | 0,113 | 0,269 |

**Chutes, gols e qualidade por janela (banco, FotMob, 5 grandes ligas 2021 a 2025; por jogo, os dois times):** chutes 3,31 (1-15), 3,67 (16-30), 3,86 (31-45), 0,75 (acréscimo 1T), 4,11 (46-60), 4,00 (61-75), 3,99 (76-90), 1,66 (acréscimo 2T); gols 0,363, 0,394, 0,417, 0,084, 0,452, 0,454, 0,450, 0,197 (total 2,81 por jogo); xG por chute 0,108 a 0,113 em todas as janelas regulares e 0,120 no acréscimo do 2T.

**Duração das ações (campo `duration`, segundos):** passe média 1,60 (mediana 1,39; p90 2,85); condução 1,73 (1,32; p90 3,67); cruzamento 1,75 (1,50; p90 2,92); chute 0,91 (0,67; p90 1,36); drible, `Dispossessed` e `Miscontrol` são eventos instantâneos (duração ~0). **Posses:** 195 por jogo, duração média 14,2 s (mediana 9,0; p90 34), ações por posse média 9,2 (mediana 6; p90 21). Somando as durações das posses, o time em posse ocupa ~46 dos ~94 minutos até o último evento: o resto é bola parada, bola fora e transição.

**Leitura.**
- **O ritmo de ações cai ao longo do jogo** (20,8 por minuto nos primeiros 15 minutos, 17,6 nos últimos), mas **o ritmo de chutes sobe** (0,216 a 0,274 por minuto, +27%) e o de gols também (0,026 a 0,030 a 0,034): cada ação que sobra termina mais em chute. A taxa de perda é estável (15 a 16%, com 18,6% no acréscimo do 2T), faltas e escanteios sobem devagar (+15%). A qualidade por chute é constante (xG por chute de 0,108 a 0,113), então o aumento de gols vem de mais chutes e não de chutes melhores.
- **Os acréscimos são densos:** 0,30 chute por minuto e 0,035 gol por minuto, o maior de todas as janelas (amostra pequena e janela de duração aproximada).
- **Para o simulador:** relógio = ações com tempo de permanência de ~1,6 s (gama ou lognormal ajustável aos quantis acima), com os multiplicadores por janela acima aplicados às taxas de chute, perda de bola, falta e escanteio; a duração do jogo (45,8 e 93,3 minutos até o último evento) define os acréscimos. As taxas por janela são descritivas: não separam estado do jogo (placar), nível dos times nem substituições, que mudam o ritmo (Achados 15 a 18).

**Limites:** uma liga e uma temporada (La Liga 2015/16, StatsBomb) para o ritmo de ações e as durações; as taxas por minuto dos acréscimos dependem da duração média usada (até o último evento, não até o apito); a duração das posses conta só ações definidas no Achado 15 (não inclui recuperações, faltas e bolas paradas); chutes e gols por janela do banco juntam cinco ligas e cinco temporadas sem separar placar; sem intervalo de confiança.


## Achado 28 — a matriz de transição por zona ao longo do jogo: muda pouco, e o que muda é onde a bola está

**Pergunta:** os estados por zona (chute, perda, continua, para onde vai) têm as mesmas taxas nos primeiros 15 minutos e nos últimos 15? Há uma matriz diferente por janela do jogo?

**Método** (`scripts/analisar_matriz_no_tempo_statsbomb.py`, sem rede, a partir dos dados reduzidos versionados): quatro ligas de 2015/16 (La Liga, Premier League, Serie A, Ligue 1; 1.517 jogos; 2.586.996 ações). Cada ação, na definição do Achado 15 e nas 18 zonas, entra na janela de 15 minutos em que COMEÇA (1T: 0-15, 15-30, 30-45; 2T: 45-60, 60-75, 75-90; acréscimos e prorrogação ficam fora). Fora da amostra: 5 blocos contíguos de partidas por competição (20 blocos), treino nos outros 4, matriz por janela contra matriz única, em nats por ação.

**Por janela (todas as zonas):**

| Janela | Ações | Chute | Perda | Avanço médio de quem continua |
|---|---|---|---|---|
| 0-15 | 477.935 | 1,059% | 15,41% | 2,50 m |
| 15-30 | 441.320 | 1,297% | 14,96% | 2,92 m |
| 30-45 | 422.871 | 1,389% | 15,19% | 3,06 m |
| 45-60 | 442.220 | 1,399% | 15,64% | 3,02 m |
| 60-75 | 405.095 | 1,541% | 15,10% | 3,30 m |
| 75-90 | 397.555 | 1,618% | 15,34% | 3,43 m |

**Onde estão as ações (% das ações da janela, por faixa do campo de quem tem a bola):** defesa 26,5 → 24,1 → 23,5 → 23,6 → 22,8 → 22,3; meio 52,5 → 49,9; ataque fora da área 18,7 → 20,9 → 22,2 → 22,3 → 23,6 → 24,2; grande área 2,33 → 2,79 → 3,07 → 3,25 → 3,52 → 3,65.

**Taxas por faixa e janela** (chute % | perda %; as janelas na ordem 0-15 a 75-90):
- **Grande área:** chute 27,3 | 27,7 | 26,9 | 26,6 | 26,8 | 27,5 e perda 27,9 | 28,3 | 28,0 | 27,2 | 27,5 | 26,7: **praticamente constantes**.
- **Ataque fora da área:** chute 2,23 | 2,48 | 2,51 | 2,36 | 2,51 | 2,50; perda 22,0 → 19,5 (cai ao longo do jogo).
- **Meio:** perda 12,8 | 11,9 | 11,8 | 12,4 | 11,6 | 11,8 (estável, chute ~0,02).
- **Defesa:** perda 14,8 → 16,9 (sobe).
- **Avanço de quem continua:** cresce em todas as faixas (defesa 5,2 → 7,2 m, meio 1,7 → 3,0 m, ataque fora da área 0,8 → 1,1 m).

**Decomposição do chute por ação** (a) com a ocupação da janela e as taxas por zona de TODO o jogo e (b) com a ocupação de TODO o jogo e as taxas por zona da janela: (a) 1,029% → 1,633% (acompanha o chute observado); (b) 1,413% → 1,359% (**praticamente constante**). **O aumento de chutes por ação ao longo do jogo (+53%) vem de a bola estar em zonas mais avançadas, não de a taxa de chute DENTRO de cada zona subir.**

**Matriz por janela contra matriz única, fora da amostra: ganho de +0,00092 ± 0,00006 nats por ação** (20 blocos): real, mas pequeno (2% do que a grade de 18 zonas ganhou sobre a de 12 no teste conservador, +0,042, e da ordem do ajuste por Elo da perda, Achado 24).

**Leitura:**
- **A dinâmica ao longo do jogo é um deslocamento da bola rumo ao gol adversário** (menos ações na defesa e mais no ataque; jogadas que avançam mais metros), não uma mudança das taxas de chute e perda dentro de cada zona. A grande área é quase estacionária.
- **Exceções:** perda no ataque fora da área cai 2,5 pontos e perda na defesa sobe 2 pontos (mais bolas longas e entregas; time que ataca em bloco alto e defende com mais risco no fim do jogo), e a janela inicial tem chute um pouco menor no ataque (2,23% contra ~2,5%).
- **Para o simulador:** matriz única, com um **relógio que desloca a probabilidade de destino para zonas avançadas** (multiplicador de avanço de 1,0 a 1,4 ao longo dos 90 minutos) e o ritmo de ações por janela do Achado 27. Uma matriz inteira por janela não compensa a complexidade.

**Limites:** quatro ligas de 2015/16 somadas (a matriz das ligas é a mesma em termos do Achado 26); só 90 minutos (sem acréscimos); janela definida pelo início da ação; **não separa estado do jogo (placar), nível dos times nem substituições**, que mudam o ritmo e provavelmente explicam parte do deslocamento (um time perdendo avança mais): a análise por placar exige controlar a força da equipe (Achados 15 a 18) e ficou para depois; sem intervalo de confiança além do erro-padrão entre blocos.


## Achado 29 — tempo morto de cada reinício e recuperação da bola depois de uma perda (as duas peças que fecham o relógio e a troca de posse)

**Para quê.** Numa simulação semi-Markov a bola não anda o tempo todo: depois de uma bola fora, falta ou gol há um tempo morto até o reinício, e depois de uma perda em jogo a bola tem de ir para algum lugar do campo do adversário. `scripts/analisar_reinicios_e_recuperacoes_statsbomb.py` mede as duas coisas nos EVENTOS COMPLETOS versionados (`dados_referencia/statsbomb/completo`, sem rede) das quatro ligas de 2015/16 (1.517 jogos). Resumo e a matriz completa de 18 por 18 em `dados_referencia/statsbomb/reinicios_e_recuperacoes_ligas_2015_16.json`.

**Método.** Tempo morto = `timestamp` do primeiro evento da posse de um reinício (`play_pattern` da posse) menos o fim (`timestamp` + `duration`) do último evento da posse anterior, ignorando ruído (pressão, substituição, paralisação por lesão, câmera). Recuperação = primeiro evento do ADVERSÁRIO depois de cada perda (definição do Achado 15: passe incompleto, fora ou impedimento, `Dispossessed`, `Miscontrol`), com seu atraso e sua zona de 18 no referencial dele.

**Tempo morto por tipo de reinício** (segundos; por jogo, os dois times):

| Reinício | Por jogo | Mediana | Média | p10 | p90 | Minutos por jogo |
|---|---|---|---|---|---|---|
| Lateral | 46,3 | 11,5 | 14,1 | 4,9 | 23,4 | 10,9 |
| Falta cobrada | 30,6 | 24,9 | 29,3 | 8,1 | 56,1 | 14,9 |
| Tiro de meta | 16,7 | 24,8 | 25,8 | 12,6 | 36,4 | 7,2 |
| Escanteio | 10,2 | 26,3 | 27,9 | 17,2 | 38,5 | 4,7 |
| Saída de bola depois de gol | 2,6 | 55,0 | 56,3 | 40,0 | 71,6 | 2,4 |
| Reposição do goleiro | 6,6 | 0,0 | 4,9 | 0,0 | 16,3 | 0,5 |

- **O tempo morto dos reinícios soma 40,7 minutos por jogo.** Junto dos ~47 minutos de ações (Achado 27), dão ~88 dos ~94 minutos até o último evento: o relógio fecha com ~93%. Os ~6 minutos que faltam são pausas dentro da posse e paralisações (lesão, substituição) que a conta não pega.
- **A distribuição é assimétrica** (média acima da mediana; lateral p90 = 23 s, falta p90 = 56 s): um modelo lognormal ou gama por tipo de reinício serve melhor que a média.
- **A falta cobrada é o maior consumidor de tempo** (14,9 de 40,7 minutos), depois lateral (10,9).
- **A reposição do goleiro quase não tem tempo morto** (mediana 0): é continuidade de jogo (defesa seguida de reposição), e entra como posse comum.

**Causa do reinício (as mais frequentes; mediana em segundos):** falta cobrada depois de `Foul Won` 25,8 (n = 37.701); lateral depois de recepção 10,9, depois de corte 11,6, depois de disputa 11,4, depois de bloqueio 11,7, depois de passe fora 10,6; tiro de meta depois do goleiro 25,4; escanteio depois do goleiro 27,5 e depois de corte 25,7. O tempo morto depende pouco da causa dentro do mesmo tipo de reinício.

**O que acontece depois de uma perda** (417.332 perdas):

| Primeiro evento do adversário | % |
|---|---|
| Recuperação em jogo: passe | 25,3 |
| Recuperação em jogo: `Ball Recovery` | 17,5 |
| Recuperação em jogo: corte (`Clearance`) | 14,1 |
| Recuperação em jogo: disputa (`Duel`) | 12,1 |
| Recuperação em jogo: bloqueio | 9,3 |
| Recuperação em jogo: interceptação | 8,6 |
| Recuperação em jogo: goleiro | 1,9 |
| Reinício: lateral | 6,5 |
| Reinício: tiro de meta | 2,2 |
| Reinício: falta cobrada | 1,8 |
| Reinício: escanteio | 0,1 |

- **89,5% das perdas viram recuperação em jogo, com atraso praticamente zero** (mediana 0,0 s, média 0,2 s): a posse muda de lado no mesmo instante, sem tempo morto. Só 10,5% das perdas viram reinício, com os mesmos tempos mortos da tabela (lateral mediana 11,0 s; tiro de meta 23,6 s; falta 22,7 s; escanteio 26,6 s).
- **Onde o adversário fica com a bola** (recuperação em jogo; faixa no referencial dele, % por linha, dada a faixa em que a ação de perda COMEÇOU, que é onde o passe saiu):

| Perda começou em (referencial de quem perdeu) | Adversário: defesa | meio baixo | meio alto | ataque fora da área baixo | ataque fora da área alto | grande área | n |
|---|---|---|---|---|---|---|---|
| defesa | 14,7 | 32,7 | 26,3 | 14,1 | 10,6 | 1,4 | 92.348 |
| meio baixo | 25,7 | 32,3 | 40,5 | 1,3 | 0,1 | 0,0 | 73.871 |
| meio alto | 45,7 | 52,0 | 2,3 | 0,0 | 0,0 | 0,0 | 76.653 |
| ataque fora da área baixo | 94,8 | 5,1 | 0,1 | 0,0 | 0,0 | 0,0 | 36.661 |
| ataque fora da área alto | 99,8 | 0,2 | 0,0 | 0,0 | 0,0 | 0,0 | 72.843 |
| grande área | 99,9 | 0,0 | 0,0 | 0,0 | 0,0 | 0,0 | 21.021 |

**Leitura da matriz de recuperação:** a posição do evento de perda é a ORIGEM do passe, não onde a bola acabou. Por isso uma perda que começa na defesa do time quase nunca é recuperada em cima do gol (só 26% nas faixas de ataque do adversário): são passes longos interceptados no meio. Já uma perda que começa no ataque (faixas de ataque e grande área) vira recuperação do adversário na defesa dele em 95 a 100% dos casos. A matriz de 18 por 18 completa está no JSON.

**Consequência para a cadeia:** (a) depois de uma perda em jogo (89,5%), troca a posse com tempo zero e a zona do adversário é sorteada dessa matriz (dada a zona de origem da perda); (b) nos outros 10,5%, o relógio avança o tempo morto do reinício (lognormal ou gama por tipo, valores da tabela) e a posse recomeça pela zona do reinício; (c) escanteio e falta cobrada são os reinícios que mais gastam relógio; (d) gol: ~56 s de saída de bola.

**Limites:** quatro ligas de 2015/16 somadas; o StatsBomb não tem evento "bola fora" explícito, então o tempo morto parte do fim do último evento (inclui paralisações por lesão e substituição que caiam entre os dois); o primeiro evento do adversário depois da perda pode ser um corte ou bloqueio que não encerra a jogada (a bola continua em disputa), então a "zona de recuperação" é aproximada; não separa o tempo morto por janela do jogo, por placar nem por nível dos times (o tempo gasto com falta cobrada tende a crescer no fim do jogo); sem intervalo de confiança.


## Achado 30 — resultado do chute por zona, o que vem depois do chute, destino do lateral e do escanteio, falta e bola fora por zona

**O que foi medido** (`scripts/analisar_chutes_laterais_escanteios_faltas_statsbomb.py`, sem rede, a partir dos eventos completos das quatro ligas de 2015/16, 1.517 jogos; resumo em `dados_referencia/statsbomb/chutes_laterais_escanteios_faltas_ligas_2015_16.json`). Completa, com o Achado 29, as peças da cadeia semi-Markov: o que acontece DEPOIS de um chute, para onde vão os reinícios e onde ficam as faltas.

**1. Resultado do chute por faixa do campo** (37.888 chutes, 25,0 por jogo; % de cada resultado):

| Faixa de quem chuta | Chutes | Gol | Defendido | Bloqueado | Para fora | Sem direção | Trave | xG médio | De cabeça |
|---|---|---|---|---|---|---|---|---|---|
| meio alto (x 52,5 a 70 m) | 206 | 1,5 | 19,4 | 8,7 | 52,9 | 12,1 | 1,0 | 0,006 | 0% |
| ataque fora da área, baixo | 2.931 | 1,4 | 20,5 | 25,6 | 47,0 | 3,8 | 1,0 | 0,017 | 0% |
| ataque fora da área, alto | 11.682 | 3,7 | 22,7 | 32,0 | 36,0 | 3,7 | 1,4 | 0,037 | 0,1% |
| grande área | 23.025 | **14,7** | 23,8 | 21,6 | 30,1 | 6,8 | 2,3 | **0,142** | 26,5% |

(Na defesa e no meio baixo há 2 e 42 chutes: ignorados.) 61% dos chutes saem da grande área e geram 14,7% de gols; fora da área, 36 a 47% vão para fora e 26 a 32% são bloqueados. O xG médio por chute concorda com a taxa de gol dentro da área (0,142 contra 14,7%).

**2. O que vem depois do chute** (primeira posse diferente; `play_pattern` da nova posse e se ela continua com quem chutou):

| Resultado | n | Jogo corrido | Tiro de meta | Escanteio | Goleiro | Saída de bola | Lateral | Falta | Mesma equipe |
|---|---|---|---|---|---|---|---|---|---|
| Gol | 3.869 | 5,4 | 0 | 0 | 0 | **93,2** | 0 | 0 | 0,1% |
| Defendido | 8.783 | 38,7 | 3,6 | **23,2** | **20,3** | 2,4 | 6,7 | 3,2 | 30,7% |
| Bloqueado | 9.472 | 27,4 | 9,8 | **29,1** | 2,0 | 2,0 | 15,6 | 8,4 | 46,4% |
| Para fora | 12.637 | 0 | **98,7** | 0,1 | 0 | 0 | 0,1 | 0 | 0,2% |
| Sem direção | 2.154 | 26,2 | 26,8 | 6,9 | 2,6 | 3,3 | 17,8 | 10,6 | 18,5% |
| Trave | 710 | 23,4 | 36,5 | 8,3 | 1,3 | 5,8 | 12,3 | 8,0 | 20,7% |

Chute para fora termina em tiro de meta (98,7%), e gol em saída de bola (93,2%). Chute defendido ou bloqueado é o que gera escanteio (23% e 29%) ou deixa a bola em jogo (39% e 27%, com a equipe que chutou mantendo a posse em 31% e 46%).

**3. Lateral** (70.260 laterais, 46,3 por jogo; 83,2% completos; comprimento médio 18,4 jardas). Destino por faixa de origem (% por linha, zona onde a bola chega):

| Origem | n | Completos | defesa | meio baixo | meio alto | ataque baixo | ataque alto | grande área |
|---|---|---|---|---|---|---|---|---|
| defesa | 15.535 | 73,8% | 54,8 | 37,9 | 6,8 | 0,3 | 0 | 0 |
| meio baixo | 14.349 | 82,2% | 21,1 | 36,7 | 36,2 | 4,9 | 1,1 | 0 |
| meio alto | 15.481 | 84,9% | 2,1 | 18,7 | 41,7 | 22,3 | 14,5 | 0,8 |
| ataque fora baixo | 7.403 | 88,7% | 0,1 | 3,5 | 26,6 | 26,0 | 40,7 | 3,1 |
| ataque fora alto | 17.492 | 88,4% | 0 | 0,2 | 5,5 | 14,0 | 65,9 | 14,4 |

O lateral é um passe curto: a bola chega perto da origem (na mesma faixa ou na vizinha); só 14% dos laterais do ataque alto chegam à grande área.

**4. Escanteio cobrado** (15.475, 10,2 por jogo; só 42,3% chegam completos):
- **Onde a bola chega:** 77,1% na grande área central (no corredor do meio da grade de 18 zonas, que cobre a área toda), 16,3% no ataque fora da área alto pelos lados (escanteio curto ou bola afastada), 5,0% nos corredores laterais da grande área.
- **Técnica:** inswinging 39%, outswinging 37%, sem informação 17%, reto 6%.
- **O que gera:** **34,6% dos escanteios geram um chute da mesma equipe na mesma posse**; xG por escanteio **0,028**; **2,63% dos escanteios viram gol** (cerca de 0,27 gol por jogo).

**5. Falta e bola fora por zona** (45.517 faltas, 30,0 por jogo; **12,9% com cartão**; **23,0% são faltas de ataque**, cometidas por quem tem a bola). A zona é a de quem TINHA a bola (a falta de defesa é espelhada para o referencial dele; a de ataque não):

| Faixa | % das faltas | Faltas por 100 ações | Passes fora por 100 ações |
|---|---|---|---|
| defesa | 13,3 | 0,93 | 1,08 |
| meio baixo | 23,6 | 1,52 | 0,62 |
| meio alto | 30,2 | 2,05 | 0,67 |
| ataque fora baixo | 12,6 | 2,17 | 0,80 |
| ataque fora alto | 14,2 | 1,93 | 1,17 |
| grande área | 6,2 | **3,31** | 1,16 |

A taxa de falta por ação **sobe do primeiro terço (0,9 por 100 ações) até a grande área (3,3 por 100)**: quanto mais perto do gol adversário, mais a ação termina em falta. Passe fora (1,0 a 1,2 por 100 ações) é mais comum na defesa e no ataque do que no meio.

**Consequência para a cadeia** (completa as peças do Achado 29):
- **Resolução do chute:** gol, defendido, bloqueado, para fora, sem direção e trave com as probabilidades por faixa acima (na grande área: 14,7 / 23,8 / 21,6 / 30,1 / 6,8 / 2,3 por cento).
- **Depois do chute:** gol -> saída de bola (tempo morto ~56 s); para fora -> tiro de meta (~25 s); defendido -> escanteio (23%), reposição do goleiro (20%) ou bola em jogo; bloqueado -> escanteio (29%) ou bola em jogo (27%) ou lateral (16%).
- **Reinícios:** lateral é um passe curto de 83% de acerto que mantém a bola perto da origem; o escanteio chega à grande área em 77% das vezes, gera chute em 35% e gol em 2,6%.
- **Falta:** sorteada junto com a ação, com taxa que sobe de 0,9 para 3,3 por 100 ações do primeiro terço à grande área; 23% são faltas de ataque (a posse passa ao adversário no ponto da falta); 12,9% geram cartão.

**Limites:** quatro ligas de 2015/16 somadas; faixas de 6 do campo (as 18 zonas completas estão no JSON só para os escanteios); chutes defendidos incluem os que o goleiro segura e os que desvia (o `play_pattern` seguinte já separa); o `play_pattern` da próxima posse é o do StatsBomb e mistura causas (um lateral depois de chute bloqueado é um desvio para fora); a falta é atribuída à zona onde o evento foi marcado (o local da falta, não do contato); sem intervalo de confiança nem divisão por janela do jogo, placar ou nível dos times.

## Achado 31 — protótipo v0 do simulador semi-Markov da bola: reproduz o volume de chutes e gols, mas falha em lateral, tempo e posses

`scripts/simulador_cadeia_bola.py` junta as peças dos Achados 18–30 (4 ligas de 2015/16) e simula partidas; `test_simulador_cadeia_bola.py` (5 testes) cobre determinismo, normalização e estados válidos. 3.000 jogos, semente 2, contra o observado por jogo:

| por jogo | simulado | observado |
|---|---|---|
| chutes | 25,0 | 25,0 |
| gols / xG | 2,45 / 2,55 | 2,55 / 2,47 |
| tiros de meta / saídas de bola | 16,5 / 2,45 | 16,7 / 2,60 |
| faltas | 29,4 | 30,0 |
| escanteios | 9,4 | 10,2 |
| tiros livres | 35,8 | 30,6 |
| **laterais** | **21,8** | **46,3** |
| tempo morto (min) | 36,8 | 40,7 |
| tempo em ação (min) | 57,3 | 46,1 |
| ações | 2.092 | 1.786 |
| posses (trocas) | 341 | 195 (definição StatsBomb) |

**Leitura:** o essencial do jogo (chute, gol, xG, tiro de meta, falta) sai da cadeia sem ajuste. As falhas são concentradas e têm causa conhecida:
- **Laterais pela metade:** só os laterais que seguem uma perda (6,5% das perdas) entram; os outros ~25 por jogo seguem bola desviada pelo adversário/chute e não estão modelados. Isso explica ~4 min de tempo morto faltando e, como o relógio é preenchido por ações, ~17% a mais de ações.
- **Escanteios 8% abaixo e tiros livres 17% acima:** o encaminhamento depois do chute/perda precisa de calibração conjunta.
- **Posses:** a simulação conta cada troca (275 perdas/jogo); StatsBomb agrupa em 195 sequências. Definições diferentes, não é erro do modelo.
- Chutes por janela de 15 min ficam planos (3,6–4,2), como esperado de um protótipo estático; falta a inclinação por janela (Achado 28).
Próximo passo natural: modelar o lateral que mantém a posse (com tempo morto), recalibrar escanteio/tiro livre e então inserir janela e força dos times.

### Achado 31 — v1: lateral mantido, tiro livre sem contagem dupla, e o que sobrou (a cadeia sub-ocupa o terço final)

Mudanças do v1 em `simulador_cadeia_bola.py` (3.000 jogos, semente 2): (1) tiro livre saiu dos reinícios depois da perda, pois as faltas já são sorteadas por faixa (era contagem dupla: 35,8 -> 29,3 contra 30,6 observados); (2) entrou o **lateral que mantém a posse** (`P_LATERAL_MESMA = 0,085` por perda, CALIBRADO para fechar 46,3 laterais, não medido): laterais 21,8 -> 44,7; tempo morto 36,8 -> 38,8 min.

| por jogo | v0 | v1 | observado |
|---|---|---|---|
| laterais | 21,8 | 44,7 | 46,3 |
| tiros livres | 35,8 | 29,3 | 30,6 |
| escanteios | 9,4 | 9,6 | 10,2 |
| chutes / gols | 25,0 / 2,45 | 25,2 / 2,46 | 25,0 / 2,55 |
| ações | 2.092 | 2.019 | 1.786 |

**Falha que sobra e a causa:** as ações ainda são 13% acima e o tempo em ação é 55 min contra 46. Uma folga de 0,21 s entre ações (testada e descartada) fechou o relógio, mas derrubou chutes, gols e escanteios em ~10%. A razão: a ocupação de zonas da simulação não bate com a observada. Participação nas ações: grande área 2,4% contra 3,1%; ataque fora alto 9,2% contra 12,3%; ataque fora baixo 8,7% contra 9,7%; defesa 26,7% contra 23,8%; meio baixo 28,5% contra 26,2%. Ou seja, a cadeia (matriz de desfecho + recuperação + reinícios) **sub-ocupa o terço final** e por isso chuta 1,25% das ações contra 1,40% observado. Candidatos a investigar: destino dos reinícios fixado em zona de defesa/saída, escanteio e chute cortado voltando sempre à defesa, e a distribuição estacionária da matriz sem o efeito de quem ataca mais (força dos times).

### Achado 31 — v2: por que o terço final fica sub-ocupado (a cadeia só com fluxos em jogo não chega lá)

**Teste decisivo (sem simulação, resolvendo a distribuição estacionária da cadeia):** usando as matrizes observadas exatas (continua + perda->recuperação em jogo + chute reentrando na defesa), a cadeia converge para grande área 2,3% das ações e ataque fora alto 9,0%, contra 3,1% e 12,3% observados — o mesmo erro que a simulação tinha. Logo o defeito **não é bug do simulador nem ruído**: é estrutural.

**Contabilidade das entradas por zona (por jogo):** cada ação observada começa numa zona que veio de (a) ação que continua, (b) recuperação em jogo do adversário ou (c) um resíduo = linhas de ação que não vieram de nenhuma das duas (reinícios, saídas, bola mantida depois de desvio). O resíduo positivo é ~61 entradas por jogo (3,4% das ações) e **concentra-se no ataque**: ataque fora alto 25,1, meio alto 17,1, ataque fora baixo 8,7, meio baixo 6,6, grande área 3,6, defesa 0 (a recuperação em jogo até superestima os corredores da defesa: resíduo negativo nas zonas 0 a 2). Acrescentando esse resíduo à cadeia, a estacionária sobe para grande área 2,8% e ataque fora alto 11,3% (observado 3,1% e 12,3%): **fecha 70% do erro**.

**O que o v2 do simulador fez:** lateral (de reinício, depois de chute ou mantido) agora cai na linha lateral na faixa medida (Achado 30), tiro de meta na defesa central. Efeito pequeno: ataque fora alto 9,2% -> 10,0%, grande área 2,4% -> 2,5%; ações 2.012 (+13%), chutes 25,3, gols 2,46, escanteios 9,8 (-4%), laterais 44,7 e tiros livres 29,3. Pouco, porque o lateral ainda **substitui** uma recuperação em vez de somar à entrada em jogo, que é o que o resíduo observado mostra.

**Próximo passo:** modelar o resíduo como entradas adicionais por zona (distribuição acima), sem tirá-las das perdas — e checar com o StatsBomb de que evento vem cada linha de resíduo (lateral, tiro livre, escanteio) em vez de supor.

### Achado 31 — v3: o resíduo é bola parada, a recuperação do v2 estava no lugar errado, e o simulador passa a fechar sem constante de lateral/escanteio

`scripts/analisar_residuo_entradas_statsbomb.py` classifica TODA linha da matriz pela forma como a bola chegou (1.517 jogos, 4 ligas de 2015/16; saída em `dados_referencia/statsbomb/residuo_entradas_ligas_2015_16.json`). Por jogo:

| classe de entrada | linhas/jogo | faixas (defesa / meio baixo / meio alto / ataque baixo / ataque alto / grande área, %) |
|---|---|---|
| continua | 1.449,8 | 20,3 / 27,0 / 26,2 / 10,5 / 12,6 / 3,4 |
| recuperação em jogo | 229,5 | 40,9 / 26,1 / 17,9 / 6,4 / 6,1 / 2,5 |
| lateral | 45,6 | 22,1 / 20,4 / 22,0 / 10,5 / 25,0 / 0 |
| tiro livre | 30,1 | 35,0 / 25,1 / 22,0 / 8,9 / 8,8 / 0,3 |
| tiro de meta | 16,2 | 100% defesa |
| escanteio | 10,0 | 100% ataque fora alto (bandeirinha) |
| saída de bola | 4,6 | 100% meio alto/centro |

**Duas causas do v2, ambas corrigidas:** (1) o resíduo do Achado 31 v2 são as **bolas paradas** (≈106 linhas por jogo: lateral, tiro livre, tiro de meta, escanteio, saída de bola), e elas começam bem mais à frente do que uma recuperação; (2) a recuperação em jogo do v2 usava a zona do PRIMEIRO EVENTO do adversário (bloqueio, corte, duelo), mas a próxima linha de ação dele começa mais à frente: no v2 só 3,1% das recuperações caíam em ataque fora alto e 0,4% na grande área, contra 6,1% e 2,5% reais.

**Núcleo empírico (substitui 4 peças e 2 constantes calibradas à mão):** para cada linha que começa na zona z e termina em continua, perda ou chute, a próxima linha é sorteada diretamente dos dados: quem a tem (mesma equipe ou adversário), a classe e a zona. Isso inclui o lateral que fica com a mesma equipe (7,8% das perdas), a bola que a mesma equipe retoma em jogo (14,6% das perdas), o escanteio depois de perda (2,3%) e a falta depois de ação que continua. `P_LATERAL_MESMA`, `ESCALA_ESCANTEIO`, `PROB_CHUTE_NO_ESCANTEIO` e o modelo de falta por faixa saíram do código. A bola parada ganha o tempo morto lognormal do Achado 29. Restou **uma** constante calibrada: folga de 0,2 s entre linhas (os 46,1 min em ação + 40,7 de tempo morto somam só 86,8 dos ~94 min de relógio).

| por jogo (3.000 jogos simulados) | v2 | **v3** | observado |
|---|---|---|---|
| ações | 2.012 | **1.782** | 1.786 |
| chutes / gols / xG | 25,3 / 2,46 / 2,59 | **24,8 / 2,55 / 2,46** | 25,0 / 2,55 / 2,47 |
| escanteios | 9,8 | **10,0** | 10,2 |
| laterais / tiros livres | 44,7 / 29,3 | **45,6 / 30,3** | 46,3 / 30,6 |
| tiros de meta / saídas de bola | 15,8 / 2,46 | **16,1 / 2,55** | 16,7 / 2,60 |
| tempo morto (min) | 38,8 | **39,4** | 40,7 |
| ocupação grande área / ataque alto (%) | 2,5 / 10,0 | **3,1 / 12,2** | 3,1 / 12,3 |

**Todos os totais ficam a menos de 3,5% do observado e a ocupação do campo bate a menos de 0,6 ponto percentual por faixa** (testes automáticos exigem < 1 ponto). **Limites que continuam valendo:** (a) é dentro da amostra (mesmos jogos que geraram o núcleo), não prova de previsão; (b) a variância por jogo é só a de acaso: escanteios var/média 1,03 contra ~1,2 observado, gols 0,97, chutes 0,95, porque não há força dos times nem estado do jogo (falta de heterogeneidade entre jogos); (c) chutes por janela de 15 min continuam planos (3,9 a 4,1; a última janela sobe só porque o 2T dura 48 min); (d) posses (237 trocas) não são comparáveis às 195 sequências do StatsBomb; (e) 'Goalkeeper' reposições do tipo From Keeper (0,5 min/jogo) não têm tempo morto próprio. **Próximo passo:** inclinação por janela (Achado 28) e força/Elo dos times nas taxas de perda e chute, que é onde a variância por jogo deve aparecer.

## Achado 32 — força dos times e janela do jogo no simulador: a diferença de ataque é real, a de defesa é mais frágil, e o ganho fora da amostra é modesto

**Como a força é medida (sem Elo e sem casar nomes de clube).** `scripts/analisar_forca_e_janela_statsbomb.py` conta, para cada um dos 80 times das 4 ligas de 2015/16 e para cada papel (ATAQUE: linhas de ação em que ele tem a bola; DEFESA: linhas do adversário contra ele), quantas linhas terminam em chute e em perda. A razão observado/esperado de um time médio dá 4 números por time: `ataque_chute`, `ataque_perda`, `defesa_chute`, `defesa_perda`. Cada um é encolhido em direção a 1 com o peso de confiabilidade (correlação entre duas metades dos jogos, Spearman-Brown): `1 + w x (razão - 1)`. Os jogos são divididos em 4 quartos (jogo % 4) para medir a confiabilidade e para validar fora da amostra. No simulador o chute e a perda de cada linha viram `taxa da zona x ataque do time x defesa do adversário x janela`.

**Confiabilidade (quanto da diferença entre times se repete entre metades):**

| razão | ataque | defesa |
|---|---|---|
| chute (todos os jogos) | 0,67 | 0,68 |
| perda (todos os jogos) | **0,93** | 0,66 |
| chute (só treino, usada na validação) | 0,52 | 0,52 |
| perda (só treino, usada na validação) | 0,88 | 0,46 |

A tendência de perder a bola (estilo de jogo: Barcelona 0,59, PSG 0,56, Real Madrid 0,66 contra Eibar, West Bromwich e Carpi em torno de 1,39) é muito estável; o que o time cede ou força na defesa é bem mais ruído.

**Decisão de medida: razão por linha, SEM descontar a zona.** Descontar a mistura de zonas do time (primeiro teste) fazia o simulador exagerar o time forte, porque o simulador não dá ao time uma mistura de zonas própria. No nível do time (80 pontos, treino): sem descontar, correlação com o observado em chutes contra 0,83 e inclinação 1,00 (dispersão correta); descontando, 0,59 e 0,78.

**Janela do jogo (6 janelas de 15 min, razão por linha sem descontar zona):** chute 0,76 / 0,93 / 1,01 / 1,00 / 1,10 / 1,21 e quebra de jogo (falta, bola parada) 0,84 / 0,91 / 1,07 / 0,99 / 1,01 / 1,18; perda praticamente plana (0,97 a 1,04). Ou seja, a taxa de chute por linha sobe 60% da primeira à última janela. No Achado 28, descontando a zona, isso era plano: o aumento vem do avanço da bola no campo (mais linhas em zonas de chute), e aqui ele entra como multiplicador porque o simulador não tem inclinação de zona.

**Calibração do amortecimento, só no treino** (expoente sobre a razão; 1 = como medido):

| expoente da perda | linhas por jogo: corr / inclinação | chutes a favor: corr / inclinação | chutes contra: corr / inclinação |
|---|---|---|---|
| 1,0 | 0,97 / 1,81 | 0,76 / 0,58 | 0,83 / 1,00 |
| 1,5 | 0,97 / 1,21 | 0,73 / 0,36 | 0,75 / 0,63 |
| 2,0 | 0,97 / 0,93 | 0,70 / 0,26 | 0,70 / 0,46 |

(inclinação = observado sobre previsto entre os 80 times; 1 = dispersão certa, menor que 1 = o simulador exagera as diferenças). Subir o expoente da perda acerta a posse mas piora muito os chutes, então ficou **1,0, como medido, sem ajuste**.

**Validação fora da amostra** (razões estimadas nos quartos 0 e 2; 758 jogos dos quartos 1 e 3 simulados 30 vezes entre os dois times reais; erro quadrático contra o palpite "todo time igual" = média do treino):

| por time-jogo | correlação | ganho no erro quadrático |
|---|---|---|
| chutes | +0,37 | **+6,0%** |
| gols | +0,25 | **+5,5%** |
| escanteios | +0,18 | **+2,1%** |

A força dos times melhora a previsão de forma real mas pequena (o chute de um time num jogo tem muito acaso: desvio-padrão observado 5,1 contra 3,3 previsto).

**Limites que continuam valendo:** (a) mandante e visitante têm a mesma força (a vantagem de jogar em casa não está no modelo); (b) a posse fica sub-dispersa entre times (inclinação 1,81: a diferença de tempo de posse real é maior, em parte porque times de posse também passam mais rápido e a duração das ações é igual para todos) e os chutes a favor ficam super-dispersos (0,58): dominar a posse não vira chute na proporção que o simulador gera; (c) escanteios por jogo ainda têm variância só de acaso (observado var/média 1,2); (d) dentro da amostra de 4 ligas de 2015/16; (e) o teste com Elo do ClubElo, para estimar a força de times com poucos jogos e de outras ligas, ainda não foi feito.

## Achado 33 — posse, xT e momentum no simulador: a posse bate, o xT bate dentro de ±7% e o momentum existe e vem da MEMÓRIA DA POSSE; o Elo prevê o estilo de posse

`scripts/metricas_posse_xt_momentum.py` mede a MESMA coisa nos eventos reais e nas partidas simuladas (`scripts/comparar_posse_xt_momentum.py observar|comparar`; saída real em `posse_xt_momentum_observado_ligas_2015_16.json`). Corrida = linhas consecutivas da mesma equipe na mesma metade; o tempo morto de um reinício entra na corrida de quem perdeu a bola (como na posse do StatsBomb).

**1. Posse de bola (v3, sem memória): replicada em volume, subdispersa entre times.** Corridas por jogo 237,1 real contra 237,1 simulado (os "195" do StatsBomb eram outra definição de posse, não erro do simulador); duração média 24,0 s real, 23,7 simulado; linhas por corrida 7,53 contra 7,55. Dispersão da posse do mandante (dp): real 0,083 por tempo e 0,106 por linhas; neutro 0,035 / 0,028; com a força dos times 0,067 / 0,070. A força explica cerca de 60% da dispersão por tempo e 2/3 da por linhas.

**2. Momentum: existe, e é memória da posse.** Risco observado/esperado dada a zona, por posição da linha dentro da corrida:

| posição na corrida | 1 | 2 | 3 | 4-5 | 6-8 | 9-14 | 15+ |
|---|---|---|---|---|---|---|---|
| perda real | 1,32 | 1,40 | 1,12 | 1,04 | 0,92 | 0,82 | 0,75 |
| perda simulada sem memória (força) | 1,03 | 1,02 | 1,02 | 1,02 | 1,00 | 0,99 | 0,94 |

Quem acabou de ganhar a bola a perde com 32 a 40% mais chance do que a zona explica; quem a segura há 15 ou mais linhas, com 25% menos. O simulador sem memória não tem isso (e as razões de força de time quase não o produzem). Consequências medidas sem memória: autocorrelação dos chutes em janelas consecutivas de 5 min 0,056 real contra 0,037 (com força) e 0,000 (neutro), e chutes dentro de 60 s do anterior da mesma equipe 15,8% real contra 14,6% e 14,1%.

**3. Correção (v4): multiplicador de perda e de chute por posição na corrida**, medido no real (`risco_por_posicao_na_corrida` do arquivo observado; `memoria=True` no simulador, `--sem-memoria` desliga). Efeito: risco de perda simulado 1,24 / 1,34 / 1,10 / 1,03 / 0,93 / 0,83 / 0,75 (real 1,32 / 1,40 / 1,12 / 1,04 / 0,92 / 0,82 / 0,75); autocorrelação 0,046 (real 0,056); chutes até 60 s / 120 s / 300 s do anterior 15,0 / 25,6 / 48,8% (real 15,8 / 26,2 / 49,2%). Com a memória ligada o simulador passou a chutar ~9% a mais e a empurrar a bola para o ataque; uma constante global, `ESCALA_CHUTE_COM_MEMORIA = 0,88` (CALIBRADA, não medida), recupera os totais por jogo (chutes 25,3 contra 25,0; gols 2,63 contra 2,55; ações 1.788 contra 1.786).

**4. xT empírico por zona** (probabilidade de haver chute depois, na mesma corrida, a partir de uma linha na zona): sem memória o simulador ficava 10 a 17% abaixo do real nas zonas do meio (zona 4: 0,100 contra 0,122; zona 7: 0,139 contra 0,168). Com a memória ficou dentro de ±7% em quase todas as zonas (zona 4: 0,116; zona 7: 0,159; zona 10: 0,269 contra 0,289; zona 16: 0,669 contra 0,689). **O xG depois da linha ainda erra nas zonas da grande área e nos corredores do ataque:** zona 16 (centro da área) 0,101 simulado contra 0,126 real, enquanto os corredores sobem (zona 12: 0,032 contra 0,026), porque o simulador usa o xG médio da FAIXA e não da zona.

**5. Limites novos:** a ocupação do ataque passou a exagerar (grande área 3,7% contra 3,1%; ataque fora alto 13,2% contra 12,3%) e a defesa a ficar baixa (22,7% contra 23,8%); a validação fora da amostra da força dos times, refeita com a memória ligada, deu ganho de erro quadrático de +2,4% em chutes (antes +6,0%), +5,8% em gols e +2,9% em escanteios (correlação em chutes 0,37). O ganho de chutes caiu porque a memória aumenta a dispersão prevista entre times sem aumentar a correlação.

**6. ClubElo / Elo na força dos times.** O site do ClubElo está bloqueado na rede desta sessão (o proxy devolve 502 para `api.clubelo.com`); para liberar, o ambiente precisa de `api.clubelo.com` em Allowed domains (Network access do ambiente na nuvem). O banco só tem Elo de 2015/16 para uma liga (La Liga, 20 times, Elo interno do projeto, 3 promovidos no padrão 1500), então o teste foi feito ali: correlação entre o Elo do início da temporada e as razões de força medidas na temporada, 20 times (17 sem os promovidos com 1500):

| razão | correlação (20 / 17 times) | por 100 pontos de Elo |
|---|---|---|
| ataque_perda | **-0,74 / -0,79** | -0,13 |
| defesa_perda | -0,16 / -0,20 | -0,01 |
| ataque_chute | -0,13 / -0,21 | -0,01 |
| defesa_chute | +0,04 / +0,07 | +0,01 |

O Elo conhecido ANTES da temporada prevê bem o estilo de posse (quem tem Elo alto perde menos a bola, o mesmo resultado do Achado 24) e quase nada do resto. Serve, portanto, como informação a priori da razão `ataque_perda` (a mais estável, confiabilidade 0,93) para times sem histórico de jogos, mas não substitui a medida nos eventos para chute e defesa. Só 20 times de uma liga: a conclusão é um indício, não prova.

## Achado 34 — xG por zona polar e ocupação do ataque: o desvio de ocupação vinha da memória da posse, não do chute

**1. O xG deve vir das zonas polares (as da calculadora), não da zona de 18.** Com os 37.888 chutes do StatsBomb (4 ligas, 2015/16), R² do xG fora da amostra (treino = chutes pares, teste = ímpares; `scripts/analisar_xg_polar_vs_zona.py`): média da zona de 18: **0,22**; caixas de distância x ângulo: 0,48; árvores em (distância, ângulo): **0,61**; zona + cabeça + tipo de bola parada: 0,47; distância + ângulo + cabeça + tipo: **0,73**. A zona de 18 é grossa demais para o chute: a grande área central (zona 16) concentra 51% dos chutes e tem desvio-padrão de xG de 0,17. A calibração é boa onde há dados: gols/xG de 0,92 a 1,04 nas zonas 13 a 17.

**2. Fonte escolhida: a tabela polar do estudo do frontend.** A tela "Zona-a-zona: de onde vêm os chutes" da Análise de Evento usa 14 zonas polares (7 anéis: 0-6, 6-9, 9-12, 12-16,5, 16,5-22, 22-30, > 30 m x cone central de +-30 graus ou aberto) com ~477 mil chutes do FotMob sem pênaltis. Comparação com o StatsBomb nas mesmas 14 zonas (`scripts/zonas_polares.py`): fatias de chute dentro de ~2 p.p. na maioria das zonas (maior diferença: 16,5-22 m central, 13,0% StatsBomb contra 9,0% FotMob); xG por chute parecido (por exemplo 12-16,5 m central 0,121 contra 0,116) e o FotMob um pouco maior nos extremos (0-6 m central 0,443 contra 0,362; > 30 m 0,020 contra 0,009); total 0,1007 contra 0,0916 de xG por chute e 0,0976 contra 0,0952 gol por chute.

**3. Como o simulador usa as duas.** O chute simulado é um chute real do StatsBomb sorteado da zona de 18 em que a cadeia está (traz distância, ângulo, cabeça e tipo; pênalti fora do conjunto, e a linha de pênalti força um chute de pênalti); sua ZONA POLAR define o xG e a probabilidade de gol pela tabela do FotMob (`--fonte-xg fotmob`, padrão) ou usa o xG e o gol do próprio chute (`--fonte-xg statsbomb`). 4.000 jogos: com a tabela FotMob, gols 2,55 (observado 2,55), xG 2,65 (na escala do xG do FotMob; o alvo do StatsBomb é 2,47); com o chute do StatsBomb, gols 2,59 e xG 2,46. A escolha do padrão é pela amostra grande e pela consistência com o resto do projeto; o custo é misturar eras (FotMob recente, StatsBomb 2015/16) e perder a variância do xG dentro da zona polar.

**4. A ocupação exagerada do ataque era da memória da posse, não do chute.** Experimento (2.500 jogos cada): memória só do chute = ocupação intacta (grande área 3,1%); memória da perda (sozinha ou com a do chute) = grande área 3,5-3,7% (real 3,1%), ataque fora alto 13,0-13,1% (real 12,3%), defesa 22,7-22,9% (real 23,8%). Causa: o multiplicador foi medido contra a taxa esperada da zona, mas a distribuição de posições que o simulador gera em cada zona difere da real, e a média efetiva do multiplicador não ficava em 1 (1,14-1,17 na defesa, 0,86-0,87 na grande área). **Correção:** `scripts/calibrar_memoria_posse.py` divide o multiplicador, por zona, pela média efetiva que o simulador produz, em 3 iterações (convergiu para 1,00 +- 0,003 em todas as zonas); a taxa de perda e de chute DE CADA ZONA volta à da matriz e a memória só redistribui entre posições. Com isso a constante calibrada do Achado 33 (`ESCALA_CHUTE_COM_MEMORIA = 0,88`) saiu do código: chutes 24,9 (observado 25,0), ações 1.794 (1.786) e ocupação 23,8 / 26,8 / 24,3 / 9,7 / 12,2 / 3,1% contra 23,8 / 26,2 / 24,7 / 9,7 / 12,3 / 3,1% reais.

**5. Posse, xT e momentum depois da correção** (comparação com os eventos reais, `comparar_posse_xt_momentum.py`): risco de perda por posição 1,26 / 1,36 / 1,10 / 1,04 / 0,93 / 0,84 / 0,75 contra 1,32 / 1,40 / 1,12 / 1,04 / 0,92 / 0,82 / 0,75; autocorrelação dos chutes 0,052 (real 0,056); chutes a <= 60 / 120 / 300 s do anterior 14,6 / 25,2 / 48,5% (real 15,8 / 26,2 / 49,2%); xT (P de chute depois) dentro de +-8% nas zonas com muitos chutes. O xG depois da jogada nas zonas 12 e 14 ainda fica ~15% acima do real e na 15 e 17 ~14% abaixo.

**6. Força dos times fora da amostra, refeita com a memória normalizada:** ganho de erro quadrático +1,1% em chutes, +5,6% em gols, +2,5% em escanteios (correlação em chutes 0,36). O ganho de chutes continua baixo porque a memória aumenta a dispersão prevista entre times sem aumentar a correlação.

**7. Pendente:** a progressão da bola não muda com a posição na posse (avanço médio de faixa +0,25 nas primeiras linhas contra +0,13 nas longas, igual ao esperado dado a origem; arquivo `chutes_e_progressao_ligas_2015_16.json`), então não precisa de memória de destino; a validação em outra liga/temporada (ligas recentes e torneios) e o ClubElo (API do ClubElo respondendo 502 do próprio servidor) seguem em aberto.

## Achado 35 — validação do simulador em ligas recentes e torneios que ele não viu: chutes e ocupação do campo se transferem, o ritmo e a estrutura da posse não

`scripts/validar_simulador_outras_competicoes.py` (+ teste; saída em `dados_referencia/statsbomb/validacao_outras_competicoes.json`). Parâmetros só de La Liga, Premier League, Serie A e Ligue 1 de 2015/16; times neutros; 3.000 jogos simulados contra os eventos completos de cada conjunto. Diferença > 2 erros-padrão (EP) da média real marcada com *.

| por jogo (real ± EP -> simulado) | Ligas recentes, 1 clube (194 jogos) | Euro 2020 e 2024 (102) | Copa América 2024 (32) | Copas 2018 e 2022 (128) |
|---|---|---|---|---|
| chutes | 25,4 -> 24,9 | 23,8 ± 0,6 -> 24,9 | 23,2 ± 0,9 -> 24,9 | 23,7 ± 0,6 -> 24,9 * |
| gols | 3,20 ± 0,13 -> 2,55 * | 2,25 ± 0,14 -> 2,55 * | 2,12 ± 0,28 -> 2,55 | 2,49 ± 0,15 -> 2,55 |
| escanteios | 9,1 -> 10,0 * | 9,2 -> 10,0 * | 8,4 -> 10,0 * | 8,8 -> 10,0 * |
| laterais | 29,3 -> 45,6 * | 35,0 -> 45,6 * | 39,0 -> 45,6 * | 37,2 -> 45,6 * |
| tiros livres | 26,0 -> 29,4 * | 25,4 -> 29,4 * | 28,7 -> 29,4 | 23,9 -> 29,4 * |
| tiros de meta | 13,7 -> 16,3 * | 14,9 -> 16,3 * | 15,4 -> 16,3 | 12,9 -> 16,3 * |
| linhas de ação | 2.140 -> 1.790 * | 1.991 -> 1.790 * | 1.617 -> 1.790 * | 1.905 -> 1.790 * |
| corridas (posses) | 178 -> 237 | 171 -> 237 | 192 -> 237 | 198 -> 237 |
| linhas por corrida | 12,0 -> 7,6 | 11,2 -> 7,6 | 8,3 -> 7,6 | 9,4 -> 7,6 |

**O que se transfere:** o volume de chutes (de -2% a +7%) e a ocupação do campo por faixa (dentro de ~2 pontos percentuais; exceção: defesa da Copa América, 30,1% contra 23,9% simulado, com 32 jogos); os gols nas Copas (2,49 contra 2,55) e na Copa América (diferença dentro do erro).

**O que NÃO se transfere:** (1) o **ritmo**: o número de linhas de ação por jogo varia de 1.617 a 2.140 entre conjuntos, contra 1.786 em 2015/16 (as ligas recentes têm 20% mais ações por jogo); (2) a **estrutura da posse**: nas outras competições a posse é mais longa e menos fragmentada (9 a 12 linhas por corrida e 171 a 198 corridas por jogo, contra 7,6 e 237); (3) **laterais**, 22 a 36% menos que em 2015/16 em todos os conjuntos (29 a 39 contra 46), e tiros de meta e escanteios de 6 a 26% menores; (4) os **gols de clubes de elite** (3,20 contra 2,55) e de torneios de seleções (Euro 2,25) saem do que a média da amostra de 2015/16 prevê.

**A força dos times explica parte do que falha:** o simulador com a força de Barcelona ou PSG (medida em 2015/16) contra um adversário médio move as medidas na direção certa (corridas 237 -> 192-201 contra 178 reais; linhas por corrida 7,6 -> 9,1-9,7 contra 12,0; laterais 46 -> 36-38 contra 29), mas não chega ao real, pois os clubes de 2018 a 2024 jogam com mais posse e mais passes do que os de 2015/16. A autocorrelação dos chutes (momentum) é maior que a simulada nos quatro conjuntos (0,059 a 0,079 contra -0,001 do simulador neutro e 0,05 do com times), consistente com times de força muito desigual (clubes de elite e seleções) no mesmo jogo.

**Conclusão e uso:** os parâmetros de 2015/16 valem para o volume de chutes e para a ocupação do campo em qualquer competição testada; o ritmo, a estrutura da posse, as bolas paradas e os gols precisam de calibração por competição/época (por exemplo, núcleo da próxima linha e razão de perdas estimados dentro do próprio conjunto, como os `forca_dos_times` já fazem para times). **Não usar o simulador como está para prever gols de torneios de seleções ou de clubes de elite.** Amostras pequenas (Copa América, 32 jogos) têm erro-padrão grande.

**O que o banco tem do FotMob (pergunta do usuário):** Europa League (id 48) e Conference League (id 49) estão cadastradas em `leagues`, mas com 0 jogos em `matches` e portanto sem chutes em `match_shots_fotmob`; a Champions League (id 19) tem 647 jogos, 319 deles com chutes do FotMob (8.616 chutes, 19/09/2023 a 10/09/2026); a Eurocopa de seleções não está em `leagues` (nem a Copa América); a Copa Africana de Nações (id 61) está cadastrada com 0 jogos.

## Achado 36 — FotMob da Eurocopa e da Copa América importado e cruzado com o StatsBomb: gols e escanteios idênticos, chutes +3%, passes -8%, xG do FotMob 26 a 49% maior

**O que foi importado.** `scripts/baixar_fotmob_torneios.py` baixou do FotMob 330 jogos encerrados: Eurocopa 2024 (51), 2020 (51), 2016 (51) e 2012 (31); Copa América 2024 (32), 2021 (28), 2019 (26), 2016 (34) e 2015 (26). Cada jogo traz placar, eventos, estatísticas de time e de jogador, escalação, momentum e o mapa de chutes com xG e xGOT. Ficou versionado em `dados_referencia/fotmob/` (2,2 MB comprimido; ver o `LEIA-ME.md` da pasta), **sem gravar no banco**: a carga usual (`ingestao_fotmob.py`) exige a chave service_role do Supabase, que não existe no ambiente desta sessão, e as seleções ainda não têm linha em `teams`/`leagues`.

**Cruzamento com o StatsBomb** (`scripts/cruzar_fotmob_statsbomb_torneios.py`; casamento por par de seleções + placar, com tabela de apelidos explícita; **198 de 198 jogos casaram** em Euro 2020, Euro 2024, Copa América 2024 e Copa do Mundo 2022, esta baixada depois; o par Argentina x Canadá se repete na Copa América 2024 com o mesmo placar e foi desempatado pela ordem cronológica). Totais por jogo, razão FotMob/StatsBomb e correlação por jogo (valores CORRIGIDOS, ver abaixo):

| medida | FotMob | StatsBomb | razão | correlação por jogo |
|---|---|---|---|---|
| gols | 2,53 | 2,53 | **1,000** | 1,000 |
| escanteios | 9,16 | 9,14 | 1,003 | 0,999 |
| chutes | 24,16 | 24,10 | 1,003 | 0,996 |
| passes (FotMob = certos / taxa de acerto) | 956 | 1.036 | 0,923 | 0,993 |
| xG | 2,66 | 2,44 | **1,087** | 0,940 |

**CORREÇÃO (04/10, ao baixar a Copa de 2022).** A primeira versão desta seção dizia que o xG do FotMob era 26 a 49% maior que o do StatsBomb, com correlação de 0,33 a 0,55, e que chutes tinham razão 1,03. **Estava errado:** o mapa de chutes do FotMob inclui as cobranças da disputa de pênaltis (`period = PenaltyShootout`) e o StatsBomb as guarda em outro período; somá-las inflava o xG e os chutes do FotMob nos jogos decididos nos pênaltis. Sem elas, chutes batem (razão 1,003; correlação 0,996) e o xG fica 3 a 13% acima (correlação 0,94 a 0,96). O erro foi meu (não excluí a disputa no cruzamento, embora tivesse excluído na tabela polar do Achado 37). O teste `test_resumo_fotmob_ignora_a_disputa_de_penaltis_nos_chutes_e_no_xg` cobre.

**Leitura.** Gols, escanteios e chutes são os mesmos nas duas fontes; passes do FotMob são 8% menores (definição de passe); **o xG do FotMob é cerca de 9% maior que o do StatsBomb nos mesmos jogos** (modelos diferentes, correlação alta). As edições de 2012, 2016 (Euro) e 2015, 2016, 2019, 2021 (Copa América) só existem no FotMob e sem mapa de chutes.

**Não feito:** a tabela polar (14 zonas) refeita só com os chutes de seleções, e o casamento das seleções com `teams` para a carga no banco (precisa do crosswalk supervisionado e da chave de escrita).

## Achado 37 — tabela polar de seleções (FotMob) e a hipótese de mudança no xG da Opta: a distribuição dos chutes é a de clubes; a conversão é 11% menor; o xG por local é estável, mas os gols/xG caem ~8% de 2021 para 2023

**Dados.** `scripts/analisar_zonas_polares_selecoes_fotmob.py` (+ teste; saída em `dados_referencia/fotmob/zonas_polares_selecoes.json`). Dos 330 jogos baixados no Achado 36, **só 3 edições têm mapa de chutes no FotMob: Euro 2024 (1.300 chutes), Euro 2020 (1.226) e Copa América 2024 (739)**; Euro 2012 e 2016 e Copa América 2015, 2016, 2019 e 2021 vêm com o mapa vazio. Total: 3.265 chutes de 134 jogos, sem pênaltis, disputa de pênaltis e gol contra, mesma regra das 14 zonas polares da Análise de Evento. IC 95% por bootstrap de partidas.

| zona | chutes | % seleções ± IC | % clubes | xG/chute seleções ± IC | clubes | gol/chute seleções ± IC | clubes |
|---|---|---|---|---|---|---|---|
| 0–6 m central | 77 | 2,36 ± 0,56 | 2,32 | 0,418 ± 0,060 | 0,443 | 0,338 ± 0,105 | 0,401 |
| 0–6 m aberto | 81 | 2,48 ± 0,52 | 2,49 | 0,379 ± 0,044 | 0,404 | 0,346 ± 0,103 | 0,388 |
| 6–9 m central | 265 | 8,12 ± 0,97 | 7,64 | 0,197 ± 0,017 | 0,197 | 0,177 ± 0,045 | 0,193 |
| 6–9 m aberto | 117 | 3,58 ± 0,68 | 4,14 | 0,141 ± 0,018 | 0,162 | 0,111 ± 0,054 | 0,139 |
| 9–12 m central | 277 | 8,48 ± 1,10 | 7,87 | 0,123 ± 0,015 | 0,129 | 0,116 ± 0,040 | 0,126 |
| 9–12 m aberto | 166 | 5,08 ± 0,84 | 5,44 | 0,106 ± 0,015 | 0,118 | 0,114 ± 0,050 | 0,112 |
| 12–16,5 m central | 330 | 10,11 ± 0,96 | 10,22 | 0,123 ± 0,012 | 0,116 | 0,103 ± 0,034 | 0,117 |
| 12–16,5 m aberto | 371 | 11,36 ± 1,12 | 12,49 | 0,096 ± 0,009 | 0,093 | 0,073 ± 0,024 | 0,093 |
| 16,5–22 m central | 288 | 8,82 ± 1,02 | 9,02 | 0,065 ± 0,005 | 0,065 | 0,062 ± 0,032 | 0,067 |
| 16,5–22 m aberto | 304 | 9,31 ± 0,97 | 10,07 | 0,039 ± 0,003 | 0,044 | 0,036 ± 0,021 | 0,042 |
| 22–30 m central | 600 | 18,38 ± 1,35 | 17,14 | 0,032 ± 0,003 | 0,032 | 0,028 ± 0,014 | 0,032 |
| 22–30 m aberto | 211 | 6,46 ± 0,87 | 6,09 | 0,025 ± 0,002 | 0,028 | 0,028 ± 0,021 | 0,028 |
| > 30 m central | 146 | 4,47 ± 0,68 | 4,12 | 0,025 ± 0,008 | 0,020 | 0,034 ± 0,029 | 0,016 |
| > 30 m aberto | 32 | 0,98 ± 0,40 | 0,96 | 0,019 ± 0,004 | 0,022 | 0,031 ± 0,057 | 0,041 |

**Leitura da comparação.** (1) A **distribuição dos chutes pelas 14 zonas é a de clubes**: todas as fatias das seleções ficam dentro do IC de 95% das de clubes, exceto 12–16,5 m aberto e 16,5–22 m aberto, ligeiramente menores. (2) O **xG por chute em cada zona também**: as 14 zonas caem dentro do IC ou a menos de 0,02 (a única fora do IC é 6–9 m aberto, 0,141 contra 0,162). (3) A **conversão** é menor: 0,0870 gol por chute contra 0,0976 de clubes (-11%), com xG por chute 0,0978 contra 0,1007 (-3%); a diferença está nas zonas abertas e próximas (0–6 m central 0,338 contra 0,401; 6–9 m aberto 0,111 contra 0,139; 12–16,5 m aberto 0,073 contra 0,093), com ICs largos (amostra de 3,3 mil chutes). **Conclusão: a tabela de clubes serve de aproximação da distribuição de chutes de seleções, mas superestima os gols de seleções em ~11%, o que coincide com os gols simulados acima do real em torneios (Achado 35).**

**Hipótese: o xG que a Opta calcula para os chutes mudou?** O FotMob usa o xG da Opta. Testes com o que o banco e os dados têm:
1. **xG por local, em clubes (banco, `match_shots_fotmob`, 6 a 16,5 m em cone central, sem pênalti, só chutes extraídos antes de 08/2026):** xG médio por chute fica estável entre 2020 e 2026 (0,143 / 0,141 / 0,142 / 0,142 / 0,142 / 0,144 / 0,145 nos anos de 2020 a 2026), uma variação de +2%.
2. **Mas os gols por chute caem:** 0,151 (2020), 0,149 (2021), 0,144 (2022), 0,140 (2023), 0,139 (2024), 0,141 (2025), 0,142 (2026), e **gols/xG vai de 1,06 / 1,06 em 2020-21 para 1,01 em 2022 e 0,98 de 2023 em diante** (n de ~14 mil chutes por ano, erro-padrão ~2%): uma queda de ~8%, concentrada entre 2021 e 2023, com xG por local constante.
3. **Seleções (amostras pequenas):** gols/xG 0,99 (Euro 2020), 0,79 (Euro 2024), 0,89 (Copa América 2024); na faixa 0-12 m, 1,05 contra 0,75 e 0,88; a diferença entre as duas Euros (~0,2) está a ~1,5 erro-padrão, não é conclusiva.
4. **Contra o StatsBomb nos mesmos jogos:** ver o Achado 38 (a razão do Achado 36 que estava aqui foi corrigida: sem as cobranças da disputa de pênaltis, os valores são 1,03 na Euro 2020 e 1,11 na Euro 2024).

**[REVISADO NO ACHADO 39: houve, sim, um degrau no xG por local, em jul/ago de 2021; a queda de ~8% abaixo era também efeito da mistura de ligas.]** **O que isso permite dizer.** O xG por local do FotMob **não deu salto** entre 2020 e 2026 (mesmos locais, mesmo xG médio), então não há sinal de recalibração do tamanho que o xG precisaria ter. O que aparece é uma **queda gradual da conversão em relação ao xG** (~8%) entre 2021 e 2023. Duas explicações compatíveis: o xG passou a refletir mais do que a posição (por exemplo, tipo de passe, pressão, goleiro) e ficou mais generoso sem mudar a média por local, ou a conversão real caiu (ou o registro dos chutes/gols mudou). Dois dados do próprio FotMob não bastam para separar as duas: é preciso uma referência externa estável (o StatsBomb de 2015/16, que só tem ligas antigas, e a Copa 2022 inteira, que está no StatsBomb mas não no FotMob do banco). **Cautela para o simulador:** a tabela polar de clubes que ele usa mistura 2020-2026 e embute essa deriva; seu gols/xG médio é ~1,0 e deve ser recalculado por período se o simulador for usado para uma temporada específica.


## Achado 38 — Copa do Mundo de 2022 do FotMob e a hipótese de mudança no xG da Opta: o xG do FotMob subiu ~8% em relação ao StatsBomb entre a Euro 2020 e 2022, mas o xG por distância do FotMob não mudou

**Dados.** `baixar_fotmob_torneios.py` agora inclui a Copa do Mundo de 2022 (FotMob 77): 64 jogos em `dados_referencia/fotmob/copa_mundo/2022/`, todos casados com o StatsBomb (`FIFA World Cup 2022`).

**Razão do xG FotMob / StatsBomb por edição** (sem disputa de pênaltis; IC 95% por bootstrap de jogos):

| edição | razão | IC 95% | jogos |
|---|---|---|---|
| Euro 2020 (jun-jul 2021) | **1,026** | 0,994 a 1,060 | 51 |
| Copa do Mundo 2022 (nov-dez 2022) | 1,128 | 1,088 a 1,176 | 64 |
| Euro 2024 | 1,106 | 1,076 a 1,137 | 51 |
| Copa América 2024 | 1,076 | 1,034 a 1,125 | 32 |
| 2022 a 2024 juntas | **1,110** | 1,085 a 1,134 | 147 |

Os ICs da Euro 2020 e do conjunto 2022-2024 **não se sobrepõem**: o xG do FotMob subiu ~8% em relação ao do StatsBomb entre meados de 2021 e novembro de 2022 e ficou estável depois. É a mesma ordem de grandeza e o mesmo período da queda de ~8% nos gols/xG dos clubes do banco entre 2021 e 2023 (Achado 37).

**Mas não dá para atribuir à Opta.** Em faixas de distância fixas, xG por chute do FotMob (sem pênaltis) em 0-12 m: 0,197 (Euro 2020), **0,215 (Copa 2022)**, 0,180 (Euro 2024), 0,180 (Copa América 2024); em 12-22 m: 0,080 / 0,089 / 0,080 / 0,092; em >= 22 m: 0,029 / 0,030 / 0,030 / 0,028. Não há degrau monótono (a Copa de 2022 é a mais alta, não a mais recente) e, nos clubes do banco, o xG por local é estável de 2020 a 2026. O xG do StatsBomb em 0-12 m: 0,180 (Euro 2020), 0,181 (Copa 2022), 0,179 (Copa 2018), **0,154 (Euro 2024) e 0,164 (Copa América 2024)** — caiu cerca de 10% em 2024. A variação do xG de uma faixa de 0-12 m entre torneios (erro-padrão da média de ~0,008 por edição) mistura xG do local e mistura de chutes dentro da faixa (0-6 m contra 6-12 m). **[REVISADO NO ACHADO 39: o teste só com o banco, em recortes fixos de distância e ângulo, mostra um degrau nítido no xG do FotMob entre 12/07 e 02/08/2021; a hipótese de mudança no modelo de xG está CONFIRMADA para os chutes de clubes.]** ~~A evidência não sustenta uma mudança no cálculo do xG da Opta: a razão FotMob/StatsBomb subiu em 2022-2024 porque o xG do StatsBomb por distância caiu nos torneios de 2024 e o do FotMob variou sem tendência; a origem mais provável está no lado do StatsBomb ou na composição dos chutes.** A queda da conversão em relação ao xG nos clubes (Achado 37) fica sem explicação por mudança de modelo do FotMob.

**Atualização da tabela polar de seleções (Achado 37) com a Copa de 2022:** 4.698 chutes (sem pênaltis); xG por chute 0,1015 contra 0,1007 de clubes (+1%); **gols por chute 0,0930 contra 0,0976 (-5%)**, ou seja, a defasagem de conversão de seleções cai de -11% (Euro 2024 e Copa América 2024, 3.265 chutes) para -5% com a Copa de 2022, que converte 0,1068 por chute (gols/xG 0,97). O saldo menor de gols de seleções em 2024 não se repete em 2022; a tabela completa recalculada está em `dados_referencia/fotmob/zonas_polares_selecoes.json`.

**Limites:** cada torneio tem 1,2 a 1,6 mil chutes (IC largo por faixa de distância); o StatsBomb Open Data não carrega a versão do modelo de xG; a Copa de 2018 do StatsBomb não foi baixada do FotMob (um teste a mais: 64 jogos, 2 min).

**Copa do Mundo de 2018 do FotMob (baixada em seguida, 64 jogos, todos casados com o StatsBomb): o teste do xG não é possível com ela.** O FotMob não tem mapa de chutes nem xG de time para essa edição (0 chutes em 64 jogos; as estatísticas do jogo não trazem `expected_goals`); o cruzamento fica restrito ao que existe: gols idênticos (2,64), escanteios 9,41 contra 9,05 (razão 1,04; correlação 0,955), passes 0,92 e chutes da estatística `total_shots` 25,55 contra 26,05 (razão 0,98; correlação 0,97). Pelo registro do FotMob, o xG com mapa de chutes só existe a partir da Euro 2020 (junho de 2021), de modo que **não há como medir a mudança de 2021 a 2022 contra uma base anterior dentro do FotMob**; a hipótese continua sem teste direto. O que resta para ela: comparar contra um xG externo estável (StatsBomb, já feito: Achado 38) ou usar a Copa Africana de Nações de 2021/2023 ou a Eurocopa feminina, se tiverem mapa de chutes.


## Achado 39 — o xG que o FotMob serve mudou no começo da temporada 2021/22: degrau de +29% no xG de chutes centrais de 14-22 m e de -20% nos chutes abertos e de longe, com o gol por chute igual

**Revisa os Achados 37 e 38**, que concluíam que o xG por local era estável. Aqueles testes usavam zonas largas (6-16,5 m em cone central) e todas as ligas juntas, e a mistura de ligas (o banco passa de 3-7 mil chutes por mês em 2020-21 para ~10 mil depois) escondia o degrau e produzia uma "queda de 8% nos gols/xG" que na verdade não existe nas cinco grandes ligas.

**Desenho que isola o modelo.** Só as 5 grandes ligas (as presentes desde o início), jogo corrido (`situation = RegularPlay`), chute de pé, sem pênalti e sem gol contra, em faixas fixas de distância e ângulo ao centro do gol. Nesse recorte o xG médio é saída do modelo, sem ruído de acerto: um degrau nele só pode ser mudança do modelo (ou de quais chutes entram, o que o recorte fixo controla). Consultas em `arquivos_do_claude/analise_mudanca_xg_fotmob.sql`.

**1. Degrau no trimestre 2021-T3 (jul-set de 2021).** xG médio por chute, 14-22 m em cone central: 0,0645 / 0,0640 / 0,0632 / 0,0640 em 2020-T3 a 2021-T2, depois **0,0823** (2021-T3), 0,0807, 0,0836, 0,0837 e estável em 0,080 a 0,086 até 2026. Os gols por chute não se mexem (0,09 a 0,10), então os gols/xG caem de 1,43-1,61 para 0,94-1,2 (SE ~0,10-0,14 por trimestre).

**2. A data.** Por semana (todas as ligas): 12/04 a 17/05/2021, big 5: 0,061 a 0,075; 12/07 (Libertadores, n=14): 0,063; 02/08 em diante: 0,080 a 0,085 de forma contínua. O degrau está **entre 12/07 e 02/08/2021**, o início da temporada 2021/22. A Eurocopa 2020 (11/06 a 11/07/2021) ficou do lado antigo: o xG do FotMob em relação ao do StatsBomb é 1,026 nela e 1,11 em 2022-2024 (Achado 38). **Os jogos já gravados não foram recalculados**: o banco guarda xG do modelo antigo até julho de 2021 e do novo depois (cerca de 10% dos chutes, 46,6 mil de ~477 mil, são do modelo antigo).

**3. O degrau não é uniforme: redistribui o xG pelo campo.** xG médio por chute, big 5, jogo corrido, chute de pé, antes (2021-T1 e T2) contra depois (2021-T4 e 2022-T1):

| zona | antes | depois | variação |
|---|---|---|---|
| central 10-14 m | 0,152 | 0,184 | **+21%** |
| central 14-22 m | 0,064 | 0,082 | **+29%** |
| central 6-10 m | 0,318 | 0,306 | -4% |
| central 22-30 m | 0,030 | 0,028 | -7% |
| aberto 12-22 m | 0,077 | 0,071 | -8% |
| aberto 22-30 m | 0,030 | 0,025 | **-17%** |
| aberto 0-12 m | 0,299 | 0,230 | **-23%** |
| central 0-6 m | 0,648 | 0,570 | **-12%** |

A calibração melhorou: antes, os gols/xG ficavam em 1,4 a 1,6 nos chutes centrais de 14-22 m (xG baixo demais) e em 0,73 a 0,89 nos abertos de 0-12 m (xG alto demais); depois ficam em torno de 1,0.

**4. O que isso explica.** (a) A razão FotMob/StatsBomb subir de 1,03 (Euro 2020) para 1,11 (2022-2024) é este degrau, não o StatsBomb (Achado 38). (b) A queda de 6 a 8% nos gols/xG que apareceu nos clubes é em boa parte mistura de ligas e deste recálculo: nas 5 grandes ligas juntas, o xG médio por chute passa de 0,1053 (2020-09 a 2021-06) para 0,1005 (2021-07 em diante), -4,6%, e os gols/xG ficam em 0,99 antes de 2023 e ~0,96 depois, sem degrau.

**5. Consequências para o projeto.** (1) A tabela polar da Análise de Evento mistura os dois modelos (≈10% dos chutes são do antigo): para refletir o modelo atual deve usar só jogos a partir de 01/08/2021. (2) Qualquer coisa calculada com xG de time ou de chute que atravesse julho de 2021 (por exemplo, o Elo por xG e regressões que usam o xG do FotMob) tem um degrau de nível: o xG total por jogo cai ~4,6% na média e o de jogadas centrais de 10-22 m sobe 20 a 30%. Deve-se fixar o início da amostra em agosto de 2021 ou tratar o período antigo à parte. (3) O simulador, que usa a tabela polar de clubes para o xG e o gol do chute, herda a mistura.

**Limites.** O recorte de pé e jogo corrido deixa de fora cabeçadas e bolas paradas (o xG delas pode ter mudado de outra forma); não foi testada a hipótese de que o FotMob simplesmente trocou de provedor (o dado diz só que o modelo mudou); a data exata está numa janela de três semanas por falta de jogos de grandes ligas entre maio e agosto de 2021.

## Achado 40 — tabela polar de clubes recalculada só com jogos desde 01/08/2021 (modelo de xG atual): a da tela muda pouco (-3% a +2% no xG por zona), porque 90% dos chutes já são do modelo novo

**Consulta.** `match_shots_fotmob` x `matches` no banco, sem pênaltis, gol contra e disputa de pênaltis, mesmas 14 zonas polares da Análise de Evento (7 anéis de distância ao centro do gol x cone central de 30 graus ou aberto), IC 95% pelo erro-padrão robusto a agrupamento por partida. Dois regimes, separados em 01/08/2021 (Achado 39): **novo** (17.095 jogos, 432.218 chutes) e **antigo** (até 31/07/2021, 2.032 jogos, 47.647 chutes). A soma dos dois (479.865) reproduz os ~479,6 mil chutes da tela. Tabela completa com ICs em `dados_referencia/fotmob/tabela_polar_clubes_por_regime_xg.json`.

| zona | % chutes (novo) | xG/chute tela | xG/chute **novo** ± IC | xG/chute antigo | gol/chute novo ± IC | gols/xG novo |
|---|---|---|---|---|---|---|
| 0-6 m central | 2,34 | 0,443 | **0,4391** ± 0,0046 | 0,4856 | 0,3974 ± 0,0096 | 0,91 |
| 0-6 m aberto | 2,49 | 0,404 | **0,3978** ± 0,0045 | 0,4638 | 0,3848 ± 0,0092 | 0,97 |
| 6-9 m central | 7,64 | 0,197 | **0,1961** ± 0,0018 | 0,2027 | 0,1912 ± 0,0042 | 0,97 |
| 6-9 m aberto | 4,15 | 0,162 | **0,1586** ± 0,0018 | 0,1981 | 0,1375 ± 0,0051 | 0,87 |
| 9-12 m central | 7,82 | 0,129 | **0,1288** ± 0,0015 | 0,1324 | 0,1256 ± 0,0036 | 0,98 |
| 9-12 m aberto | 5,44 | 0,118 | **0,1161** ± 0,0015 | 0,1380 | 0,1107 ± 0,0041 | 0,95 |
| 12-16,5 m central | 10,22 | 0,116 | **0,1177** ± 0,0011 | 0,0985 | 0,1159 ± 0,0030 | 0,98 |
| 12-16,5 m aberto | 12,50 | 0,093 | **0,0923** ± 0,0008 | 0,0963 | 0,0926 ± 0,0025 | 1,00 |
| 16,5-22 m central | 9,06 | 0,065 | **0,0663** ± 0,0005 | 0,0538 | 0,0664 ± 0,0025 | 1,00 |
| 16,5-22 m aberto | 10,14 | 0,044 | **0,0438** ± 0,0004 | 0,0496 | 0,0420 ± 0,0019 | 0,96 |
| 22-30 m central | 17,03 | 0,032 | **0,0315** ± 0,0002 | 0,0326 | 0,0320 ± 0,0013 | 1,02 |
| 22-30 m aberto | 6,09 | 0,028 | **0,0274** ± 0,0003 | 0,0335 | 0,0285 ± 0,0020 | 1,04 |
| > 30 m central | 4,11 | 0,020 | **0,0198** ± 0,0004 | 0,0235 | 0,0169 ± 0,0019 | 0,85 |
| > 30 m aberto | 0,96 | 0,022 | **0,0217** ± 0,0009 | 0,0260 | 0,0416 ± 0,0061 | 1,92 |

**Leitura.** (1) A tabela da tela é uma boa aproximação do modelo atual: a mudança no xG por zona vai de -3% a +2% (a maior é -2% em 0-6 m aberto e em 6-9 m aberto), e as fatias de chute não mudam (no máximo 0,1 ponto percentual). (2) O que o modelo antigo fazia de diferente é grande (de -20% a +23% por zona, coluna "antigo") e fica nos ~10% dos chutes mais antigos. (3) O xG atual está calibrado: gols/xG entre 0,95 e 1,04 na maior parte das zonas. Exceções: 0-6 m central (0,91), 6-9 m aberto (0,87) e > 30 m central (0,85), em que o xG novo ainda é generoso, e > 30 m aberto (1,92, só 4,2 mil chutes), em que é baixo. (4) Totais: xG por chute 0,1002 (tela 0,1007; antigo 0,1040) e gols por chute 0,0970 (tela 0,0976; antigo 0,1024).

**Mudanças no repositório.** `scripts/zonas_polares.py`: `FOTMOB` passa a ser a tabela do regime novo (usada pelo simulador, `--fonte-xg fotmob`) e a copiada da tela fica em `FOTMOB_TELA`. Efeito no simulador (4.000 jogos): gols 2,52 (observado 2,55), xG 2,63 na escala do FotMob. **O frontend não foi alterado:** `ESTATISTICA_ZONA_CHUTE` em `src/utils/zoneTransitionMatrix.js` e o texto de `MapaZonasChute.jsx` continuam com a tabela antiga (todas as datas); atualizar depende de decisão do usuário, pois muda números exibidos.

## Achado 41 — recalibrar o simulador por competição e época: o erro cai de 21% para 7% fora da amostra; ritmo, posse e bolas paradas passam a bater, gols e chutes ficam no piso do ruído

**Método** (`scripts/calibrar_simulador_por_competicao.py`, 6 testes; saídas em `dados_referencia/statsbomb/`). Para cada conjunto re-estima: matriz de desfecho 18 x 20, núcleo da próxima linha, memória da posse (risco por posição + normalização por zona), tempo morto por tipo de reinício, duração de ação e de chute, duração dos dois tempos, **folga entre ações** e os chutes sorteáveis. A matriz e o núcleo são encolhidos em direção à base (contagens do conjunto + k pseudo-contagens da base), com k escolhido na verossimilhança fora da amostra da matriz. `Parametros.aplicar_perfil` e `--perfil` aplicam o resultado ao simulador. **Validação em 2 dobras** (jogos pares x ímpares): calibra numa metade e compara os totais por jogo com os da OUTRA metade. Métrica: erro absoluto relativo médio de 7 contagens por jogo (ações, chutes, gols, escanteios, laterais, tiros livres, tiros de meta) e 3 de posse (corridas por jogo, linhas por corrida, duração da corrida), além do desvio nas 6 faixas do campo.

**A folga deixa de ser constante ajustada e vira medida.** Folga = (relógio dos tempos − tempo em ação − tempo morto modelado) / linhas. Na La Liga 2015/16 dá **0,192 s**, a mesma 0,2 s que eu tinha ajustado à mão no Achado 31; nas ligas recentes dá ~0,165 s e nos torneios ~0,246 s. A duração do 2º tempo também é medida (48,3 min em 2015/16; 48,9 a 50,6 min nas outras).

**1. Por conjunto** (erro médio nas duas dobras; A = simulador de 2015/16, calibrado = perfil do conjunto):

| conjunto | A | calibrado |
|---|---|---|
| ligas recentes, clube único (194 jogos) | 23,8% / 22,7% | **1,9% / 1,8%** |
| torneios de seleções (262 jogos) | 16,9% / 16,6% | **3,6% / 3,2%** |
| La Liga 2015/16, controle (380 jogos) | 3,1% / 2,8% | 1,8% / 3,2% (sem ganho; numa dobra o k escolhido foi o máximo, ou seja, a própria base) |

Nas ligas recentes, as ações por jogo vão de 1.789 (simulador de 2015/16) para ~2.150 (real 2.126 a 2.153), as posses por jogo de 237 para ~180 (real 178) e as linhas por posse de 7,6 para ~12 (real 12), os laterais de 46 para ~30 (real 29 a 30) e os gols de 2,58 para 3,1 a 3,2 (real 3,15 a 3,25).

**2. Por época, com o conjunto como prior (leave-one-out).** Para cada época: **A** simulador de 2015/16; **B** perfil do conjunto SEM a época; **C** B + calibração da época com metade dos jogos, avaliado na outra metade (média das duas dobras):

| época | jogos | A | B | C |
|---|---|---|---|---|
| Euro 2020 | 51 | 18,3% | 7,3% | 8,4% |
| Euro 2024 | 51 | 21,8% | 12,3% | **8,1%** |
| Copa América 2024 | 32 | 14,6% | 11,3% | **8,3%** |
| Copa do Mundo 2018 | 64 | 20,9% | 14,0% | **9,1%** |
| Copa do Mundo 2022 | 64 | 14,5% | 7,4% | **5,0%** |
| Bundesliga 2023/24 | 34 | 23,3% | 6,0% | 5,3% |
| La Liga 2018/19 | 34 | 18,6% | 5,6% | **3,0%** |
| La Liga 2019/20 | 33 | 22,8% | 6,5% | 7,2% |
| La Liga 2020/21 | 35 | 23,2% | 4,5% | 5,1% |
| Ligue 1 2021/22 | 26 | 24,6% | 6,3% | 6,2% |
| Ligue 1 2022/23 | 32 | 33,0% | 13,2% | **7,6%** |
| **média das 11 épocas** | | **21,4%** | **8,6%** | **6,7%** |

A calibração da época melhora o conjunto em **8 das 11 épocas** (média 8,6% para 6,7%); piora ou empata nas que já eram bem descritas pelo conjunto (Euro 2020, La Liga 2019/20 e 2020/21), em que o k escolhido foi o máximo (a base) em quase todas as dobras. As edições que mais se afastam do conjunto são as que mais ganham: Ligue 1 2022/23 (13,2% para 7,6%), Euro 2024 (12,3% para 8,1%), Copa de 2018 (14,0% para 9,1%).

**3. O que cada medida ganha** (erro médio nas 11 épocas, A / B / C): ações 12,7 / 5,2 / **2,8%**; laterais 44,0 / 10,5 / **7,5%**; tiros livres 16,3 / 11,7 / **6,8%**; corridas por jogo 31,4 / 6,9 / **5,9%**; linhas por corrida 30,6 / 10,1 / **5,3%**; duração da corrida 25,7 / 6,6 / 5,7%; escanteios 11,8 / 6,5 / 6,1%; tiros de meta 18,6 / 10,8 / 9,1%; faixas do campo 1,3 / 1,0 / 0,8 pontos percentuais. **Não melhoram:** chutes por jogo (6,3 / 6,4 / 6,7%) e gols (16,8 / 11,0 / 10,8%).

**4. Chutes e gols estão no piso do ruído, não no do modelo.** Cada metade de época tem 13 a 18 jogos; o erro-padrão da média de chutes por jogo (desvio ~5) é ~5% e o dos gols (desvio ~1,6) é ~15%. O erro médio de 6,7% em chutes e 10,8% em gols é o que a amostragem da própria metade de teste produz, então esses dois números não diferenciam os modelos. Os totais de ritmo, posse e bolas paradas, medidos em milhares de linhas por jogo, têm erro-padrão muito menor e são os que mostram o ganho.

**5. Limites.** (a) Dentro da amostra de 2015-2024 do StatsBomb Open Data: as ligas recentes são **clubes únicos** (Barcelona, PSG, Leverkusen) e não ligas inteiras, então o perfil descreve um time de posse, não a Bundesliga ou a Ligue 1. (b) O k é escolhido na verossimilhança da matriz de desfecho (um único k para matriz e núcleo). (c) Não há força por time dentro do perfil: os perfis são para times neutros do conjunto. (d) A normalização da memória da posse usa 700 a 1.200 jogos por iteração e deixa ruído de ~1% por zona. (e) Os perfis só valem para competições/épocas parecidas com as treinadas; para uma competição nova é preciso medir de novo (folga, duração do tempo e tempo morto saem direto dos eventos).

**Uso.** `python scripts/simulador_cadeia_bola.py --perfil dados_referencia/statsbomb/perfil_torneios_selecoes.json` (ou `perfil_ligas_recentes.json`, `perfil_la_liga_2015_16.json`); o relatório completo está em `calibracao_por_competicao.json` e `calibracao_por_epoca.json`.


## Achado 42 — seleções no banco: as edições antigas de Euro, Copa América e Copa do Mundo entram, os dados batem 115/115, e o importador errou o vínculo de um time

**O que foi feito.** Pedido: baixar o FotMob pelo workflow do GitHub. Caminho usado: (1) verificar os 65 times de seleções do FotMob contra `team_source_ids` (52 já vinculados, 13 sem vínculo; só Portugal já existia em `teams`, vinculado à mão ao FotMob 8361); (2) `?tarefa=importar-jogos-fotmob` para cada liga/temporada (cria os jogos `fm_<id>`; não exige login); (3) workflow `temporadas_fotmob_backfill.yml` para liga 22/FotMob 50 (2020 2016 2012), liga 30/FotMob 44 (2021 2019 2016 2015) e liga 27/FotMob 77 (2022 2018). Nenhuma senha do site foi necessária.

| Competição | Edição | Jogos | Enriquecidos | Com chutes/xG |
|---|---|---|---|---|
| Eurocopa | 2012 | 31 | 31 | 0 |
| Eurocopa | 2016 | 51 | 51 | 0 |
| Eurocopa | 2020 | 51 | 51 | 51 |
| Copa América | 2015 | 26 | 26 | 0 |
| Copa América | 2016 | 34 | 34 | 0 |
| Copa América | 2019 | 26 | 26 | 0 |
| Copa América | 2021 | 38 (10 cancelados) | 28 | 0 |
| Copa do Mundo | 2018 | 64 | 64 | 0 |
| Copa do Mundo | 2022 | 64 | 64 | 64 |

**Validação.** (a) Dos 115 jogos com chutes, 115 batem com os arquivos baixados antes (`dados_referencia/fotmob/`) em número de chutes (sem disputa de pênaltis) e em xG: 2.712 chutes, 312,82 de xG nos dois lados. (b) O placar reconstruído pelos gols do mapa de chutes (gol contra creditado ao adversário, disputa de pênaltis fora) confere com o placar oficial em 115 de 115. Isso é a mesma invariante usada em `match_goal_timeline`.

**Erros encontrados e corrigidos.**
1. **Vínculo errado de time (o risco do CLAUDE.md, acontecido de novo).** Ao criar times novos, o importador resolve por nome e país; a Irlanda (FotMob 5791) caiu na Irlanda do Norte (10259), recém-criada. Sintoma: Irlanda do Norte com 11 jogos (3 de 2012 em que nem se classificou + 4 de 2016 + 4 da Irlanda de 2016). Correção: `teams` 1045 'Ireland' + `team_source_ids` (fotmob, 5791) + 7 jogos movidos por `external_id` (`fm_1139476/9/85`, `fm_1695445/48/60`, `fm_2148253`). Depois disso Irlanda do Norte tem 4 jogos. **Lição:** ao importar seleções novas, conferir contagem de jogos por time e nomes que contêm outro nome ("Ireland" dentro de "Northern Ireland"). A heurística de nome do importador (`escolherCandidatoPorNomeEPais`) continua sem correção no código; o ajuste foi só nos dados.
2. **Momentum em meio-minuto.** `arquivos_do_claude/ingestao_fotmob.py` falhava na Copa 2022 com `22P02: invalid input syntax for type integer: "45.5"` (a coluna `match_momentum_fotmob.minute` é inteira) e o jogo ficava sem nada gravado. Agora `montar_linhas_momentum` descarta pontos fracionários; arredondar colidiria com o ponto inteiro vizinho e quebraria o upsert. Um run (Copa 2022/2018) falhou no `main` antigo e foi relançado a partir da branch com a correção, com sucesso.

**Pegadinhas dos dados.** O banco tem 79 cobranças de disputa de pênaltis (`period='PenaltyShootout'`) nesses 115 jogos: sempre excluir. Copa América 2021 inclui 10 jogos `cancelled` (Austrália e Catar foram convidados e trocados). Copa América 2016 inclui 2 jogos de janeiro (Trinidad e Tobago × Haiti, Panamá × Cuba) que são eliminatórias da Copa Centenário, não o torneio. O FotMob não tem mapa de chutes antes da Euro 2020 (Euro 2012/2016, Copa América 2015-2021 e Copa 2018): essas edições servem para gols, escalações e estatísticas de time e jogador, não para xG.

**Limites.** (a) Só duas edições têm xG (Euro 2020 e Copa 2022); a tabela polar de seleções continua apoiada em 115 jogos + Euro 2024/Copa América 2024/Copa 2026. (b) Os 9 jogos da Euro 2024 sem chutes seguem sem chutes. (c) A correção da heurística do importador não foi feita (decisão para o usuário: tornar o vínculo por nome mais rígido, ou exigir lista de vínculos supervisionada para seleções).


## Achado 43 — a regra "nome contido no outro" corrompeu três times; casamento passa a ser exato, com cinco nomes por time

**Origem.** No Achado 42 a Irlanda (FotMob 5791) caiu na Irlanda do Norte. O pedido foi tornar o casamento mais rígido **mantendo redundância de identificação** (nome de exibição, português, inglês, apelido, língua original). Ao desenhar a regra nova, a checagem "seleção com jogo de liga doméstica" achou dois erros do mesmo tipo que já estavam no banco.

**Contaminações achadas (reparadas após aprovação; ver "Reparo" abaixo).**
1. **Time 466 'England' = Inglaterra + New England Revolution.** São 138 jogos da MLS (2022-2025, todos `fm_`, 69 em casa e 69 fora) ligados à seleção inglesa, mais 1.696 chutes, 2.737 linhas de estatística de jogador, 2.656 de escalação, 294 de estado de jogo e 291 de `team_elo_history`. O Elo, as forças e qualquer agregado por `team_id` da Inglaterra estão misturados com um clube da MLS. Não existe linha de New England Revolution em `teams`.
2. **Time 744 'Costa Rica ' (país 'Brazil') = seleção + clube.** Dois vínculos: API-Football 13103 (clube brasileiro, 2 jogos de Copa do Brasil 2022/2024) e FotMob 6705 (seleção, 12 jogos de Copa América 2016/2024 e Copa do Mundo 2018/2022).
3. (já corrigido no Achado 42) Irlanda × Irlanda do Norte.
Os três nasceram na regra de sub/superconjunto de palavras (`nomesBatemTime`), a mesma que o CLAUDE.md já condenava para heurística de nome.

**O que foi feito.**
- `api/_lib/nomesTimes.js`: igualdade exata (depois de normalizar, inclusive alfabetos não latinos: o normalizador antigo apagava tudo que não fosse a-z0-9 e deixava 'Россия' vazio) contra todos os nomes do time; afixos genéricos de clube são a única flexibilização; em liga de seleções, só seleção com seleção e só igualdade exata; ambiguidade não escolhe no chute. 14 testes novos reproduzem cada erro real.
- Integração em `api/model-maintenance.js`: `escolherCandidatoPorNomeEPais` usa o módulo novo; `resolverOuCriarTimeFotmob` recebe `estrito` (liga com `leagues.type='international'`), e time novo criado nesse modo já nasce com `is_national_team=true`. Os três `select` de `teams` trazem as colunas novas.
- Banco: `display_name`, `name_pt`, `name_en`, `name_native`, `nicknames text[]` em `teams`; 64 seleções preenchidas por vínculo `team_source_ids` (nunca por nome). A flag `is_national_team` estava `false` em 40 delas (inclusive Brasil e Argentina), o que as deixaria entrar na rotina de clubes do ClubElo.
- **Prova com dados reais** (746 times do banco): das 65 seleções FotMob, 64 reencontram o próprio time pelo nome, 0 casam com time errado, 'Ireland' vai para o time 1045, 'New England' não casa com a Inglaterra e 'Costa Rica' (seleção) não casa mais com o clube.

**Limites e riscos.**
- A regra nova é mais conservadora: time **ainda sem vínculo** cujo nome difere por palavra que não é afixo (ex.: FotMob 'Portland' × banco 'Portland Timbers') agora cria time novo em vez de juntar. Isso é a falha segura (duplicata visível) no lugar da falha silenciosa (fusão errada). Times com vínculo em `team_source_ids` não são afetados.
- Apelido repetido em dois times é ambíguo e não casa (ex.: 'La Roja' ficou só com o Chile; Espanha usa 'La Furia Roja').
- Os nomes em português/original/apelido das 64 seleções foram escritos por mim a partir de conhecimento geral e **precisam de revisão sua**. A interface ainda mostra `name`; `display_name` não foi ligado ao frontend.

**Reparo (04/10, após aprovação).** Método em `arquivos_do_claude/reparo_selecoes_contaminadas_achado43.sql`. (1) Criados `teams` 1046 'New England Revolution' (vínculo FotMob 6580) e 1047 'Costa Rica' (seleção; o vínculo FotMob 6705 saiu do 744). (2) Movidos os jogos: 138 da MLS (69 em casa, 69 fora) para o 1046 e 12 de Copa América/Copa do Mundo para o 1047. (3) Movidas as linhas por jogo, tabela a tabela, só para esses jogos, com contagem antes e depois (sempre iguais): por exemplo 1.696 chutes, 2.737 linhas de estatística de jogador, 2.656 de escalação, 294 de estado de jogo, 729 de resposta a eventos, 5.391 de walk-forward de XI, 276 de histórico de Elo no lado da Inglaterra; 21 chutes, 287 estatísticas de jogador e 210 escalações no lado da Costa Rica. (4) Sem `match_id`: Elo da liga MLS, 25 transferências do New England, 36 disponibilidades e o elenco da seleção da Costa Rica. (5) Verificação independente: o 466 só tem jogos de Eurocopa e Copa do Mundo (42); o 1046 só da MLS (138); o 1047 só de seleções (12); o 744 ficou com os 2 jogos da Copa do Brasil; em 7 tabelas principais, 0 linhas com `team_id` fora dos dois times do jogo. (6) O Elo global e o Elo de xG foram reconstruídos do zero (`elo_global.yml` em modo completo e `elo_global_xg.yml`, ambos com sucesso), porque o rating da Inglaterra tinha 153 jogos encadeados (138 de MLS) e o da Costa Rica 5. **Resultado da reconstrução:** Inglaterra 1499,1 -> 1665,4 (Elo global; 153 -> 42 partidas) e 1515,3 -> 1615,4 (Elo de xG); New England Revolution 1402,6 (global, 138 jogos) e 1403,7 na liga MLS (igual ao rating da liga antes do reparo, o que confirma que mover as linhas da liga estava certo); Costa Rica seleção 1463,8 (global, 12 jogos; xG 1430,7) e clube 744 1467,0 (2 jogos; xG 1460,8). O histórico de Elo da Inglaterra agora vai de 2012 a 2026 só com jogos de seleção; parte da subida vem também das edições antigas de Eurocopa e Copa do Mundo carregadas no Achado 42, que entraram na cadeia.

**Efeito nas análises anteriores (reconferido em 04/10).** A contaminação mexia só no `team_id` (os chutes e jogos em si não mudaram), então só atinge análise que agregue ou filtre por time ou por `is_national_team`. Conferido achado por achado: **Achados 36, 37, 38 e 41 não acessam o banco** (leem os arquivos de `dados_referencia/fotmob/` e o StatsBomb; nenhum script deles tem `create_client`, `team_id` ou `is_national_team`), logo não são afetados. **Achado 39** consulta o banco filtrando por nome de liga (as cinco grandes, `arquivos_do_claude/analise_mudanca_xg_fotmob.sql`) e a parte "todas as ligas por semana" agrega por data, sem time: não é afetado. **Achado 40** (tabela polar) agrega `match_shots_fotmob` × `matches` sem filtro por time: a contaminação da Inglaterra e da Costa Rica não o afeta. **Mas o achado 40 tem um problema de rótulo, que a conferência revelou:** a tabela chamada "de clubes" **inclui jogos de torneios de seleções** (Copa América e Eurocopa 2024 e Copa do Mundo 2026, cerca de 165 jogos, ~4.100 chutes, 0,9% do regime novo; o banco hoje tem 234 jogos de seleções no regime novo porque a Copa 2022 entrou em 04/10). Efeito medido: xG médio por chute 0,1002 nos clubes (428.248 chutes) contra 0,0983 nas seleções (4.102), e gol por chute 0,0970 contra 0,0990; os totais do achado (0,1002 e 0,0970) são os mesmos com ou sem elas, no quarto decimal. Por zona o efeito é no máximo da ordem de 0,1% do xG, bem abaixo do IC 95% da tabela. **Nada precisa ser recalculado**, mas "tabela de clubes" deve ser lido como "tabela de todos os chutes do banco desde 01/08/2021, ~99% de clubes". O mesmo vale para a tabela copiada para o frontend e para `FOTMOB` em `scripts/zonas_polares.py`. Um refinamento possível é filtrar `leagues.type <> 'international'` numa próxima recomputação, a critério do usuário.

**Revisão dos nomes (04/10, a pedido).** Reli os 64 registros de seleções. Corrigido: 'Tartan Army' e 'Green and White Army' eram nomes de torcida (removidos); variações de nome que estavam em `nicknames` foram para `aliases` (Holanda, República Tcheca, USA...) e entraram variações usadas por outras fontes (Korea Republic, IR Iran, Macedonia, Rep. of Ireland, Eire, EUA...). Teste de colisão entre todos os nomes de todas as seleções e contra os nomes de clubes: **achou a Turquia duplicada** (time 473 'Turkey', football-data, 8 jogos de Eurocopa 2024 e Copa 2026; time 1017 'Turkiye', FotMob 6595, 6 jogos de Eurocopa 2016/2020). É o mesmo país com dois `team_id`: qualquer agregado por time da Turquia está dividido em dois. Unida em seguida, com aprovação (ver abaixo). Única seleção sem vínculo FotMob no banco até então: o próprio 473.

**União da Turquia (04/10, após aprovação).** Mantido o time 473 'Turkey' (football-data 803, o mais antigo e com os jogos de 2024 e 2026). Do 1017 'Turkiye' passaram para ele: o vínculo FotMob 6595, os 6 jogos (Eurocopa 2016: Croácia, Espanha, Tchéquia; Eurocopa 2020: Itália, País de Gales, Suíça), as linhas por jogo (14 de eventos, 6 de estatística de time, 6 por período, 41 chutes, 138 de escalação, 138 de estatística de jogador, 6 de histórico de Elo e 6 de Elo de xG; contagens antes = depois), as 30 disponibilidades de jogadores e o elenco. O 473 recebeu os nomes (Turquia/Türkiye) e o alias 'Turkiye'. Elo global e de xG reconstruídos do zero (ambos com sucesso): Turquia (473) com Elo global 1454,8 e Elo de xG 1453,4, ambos com 14 jogos; o 1017 saiu das tabelas de Elo. **O time 1017 foi apagado depois.** O primeiro `DELETE` estourou 60 s (o banco confere, tabela por tabela, se algo ainda aponta para o time; 11 tabelas, ~3,4 milhões de linhas, não tinham índice na coluna do time) e foi desfeito; o 1017 ficou neutralizado (nome '[duplicata unida em 473] Turkiye', sem nomes alternativos, sem marcador, sem vínculos; 0 colisões de nomes entre as 77 seleções). Ainda havia 45 jogadores com `players.last_team_id=1017` (todos da seleção turca), repassados ao 473. Depois que 12 índices foram criados nas chaves para `teams`, o usuário apagou o 1017 (e a tabela temporária `_reparo_log`) no painel do Supabase, porque a ferramenta de banco da sessão cancelava `DROP` e `DELETE`; conferido por consulta. Lição técnica: duas CTEs que alteram a mesma linha na mesma instrução só aplicam uma; nomes e elenco foram refeitos em instruções separadas.


## Achado 44 — tabela polar refeita só com ligas de clubes: nada muda de forma relevante (xG por zona até ±0,0002; fatias até ±0,02 ponto percentual)

**Por que.** O Achado 40 chamou de "tabela de clubes" uma tabela que incluía ~4,1 mil chutes de torneios de seleções (Copa América e Eurocopa 2024, Copa do Mundo 2026; conferência dos Achados 36-42, Achado 43). Pedido: refazer sem as ligas de seleções (`leagues.type <> 'international'`).

**Método.** Mesmo do Achado 40, com o filtro de liga explícito e a consulta agora guardada em `arquivos_do_claude/tabela_polar_por_regime_xg.sql`: chutes sem pênalti, gol contra e disputa de pênaltis, 14 zonas polares (7 anéis x cone central de 30 graus ou aberto), IC 95% pelo erro-padrão robusto a agrupamento por partida. **Validação do método:** com o filtro antigo (todas as ligas, sem a Copa 2022 que entrou no banco em 04/10) a consulta reproduz a tabela publicada (zona 0-6 m central: 0,4391 ± 0,0046 de xG e 0,3974 ± 0,0096 de gol por chute, as mesmas casas decimais). O **regime antigo** (até 31/07/2021) só de clubes dá exatamente a tabela publicada (47.647 chutes, 2.032 jogos): ela já era só de clubes, porque os jogos de seleções entraram todos no regime novo.

**Resultado, regime novo (desde 01/08/2021), só clubes: 16.930 jogos, 428.248 chutes** (antes: 17.095 jogos e 432.218 chutes). Diferença para a tabela publicada:

| zona | xG/chute antes | agora | gol/chute antes | agora | % chutes antes | agora |
|---|---|---|---|---|---|---|
| 0-6 m central | 0,4391 | 0,4392 | 0,3974 | 0,3977 | 2,34 | 2,33 |
| 0-6 m aberto | 0,3978 | 0,3980 | 0,3848 | 0,3852 | 2,49 | 2,50 |
| 6-9 m central | 0,1961 | 0,1963 | 0,1912 | 0,1912 | 7,64 | 7,63 |
| 6-9 m aberto | 0,1586 | 0,1588 | 0,1375 | 0,1375 | 4,15 | 4,15 |
| 9-12 m central | 0,1288 | 0,1287 | 0,1256 | 0,1257 | 7,82 | 7,82 |
| 9-12 m aberto | 0,1161 | 0,1162 | 0,1107 | 0,1106 | 5,44 | 5,45 |
| 12-16,5 m central | 0,1177 | 0,1176 | 0,1159 | 0,1158 | 10,22 | 10,22 |
| 12-16,5 m aberto | 0,0923 | 0,0923 | 0,0926 | 0,0924 | 12,50 | 12,50 |
| 16,5-22 m central | 0,0663 | 0,0663 | 0,0664 | 0,0664 | 9,06 | 9,07 |
| 16,5-22 m aberto | 0,0438 | 0,0438 | 0,0420 | 0,0420 | 10,14 | 10,15 |
| 22-30 m central | 0,0315 | 0,0315 | 0,0320 | 0,0320 | 17,03 | 17,02 |
| 22-30 m aberto | 0,0274 | 0,0274 | 0,0285 | 0,0282 | 6,09 | 6,08 |
| > 30 m central | 0,0198 | 0,0198 | 0,0169 | 0,0168 | 4,11 | 4,11 |
| > 30 m aberto | 0,0217 | 0,0218 | 0,0416 | 0,0417 | 0,96 | 0,96 |

A maior diferença de xG é +0,0002 (+0,13% em 6-9 m aberto), a de gols por chute é -0,0003 (22-30 m aberto) e a das fatias é 0,02 ponto percentual; todas muito abaixo do IC 95% de cada zona. **Conclusão do Achado 40 inalterada:** a tabela da tela já era uma boa aproximação do modelo atual de xG. Simulador com a tabela nova, 4.000 jogos, semente 1: gols 2,55 (observado 2,55), xG 2,63; com a anterior: 2,54 e 2,63 (dentro do ruído).

**Mudanças no repositório.** `dados_referencia/fotmob/tabela_polar_clubes_por_regime_xg.json` (regime novo só clubes; a tabela antiga do Achado 40 ficou guardada na chave `achado40_todas_as_ligas_novo`); `FOTMOB` em `scripts/zonas_polares.py`; `XG_MEDIO_ZONA_CHUTE` e `ESTATISTICA_ZONA_CHUTE` em `src/utils/zoneTransitionMatrix.js` e os textos "~428 mil chutes ... de clubes" em `MapaZonasChute.jsx` e `AnaliseEvento.jsx`. Testes: 22 do frontend, 22 do simulador, build ok.

**Limites.** `DISTRIBUICAO_ZONA_CHUTE` (fotografia de 01/10 com todas as datas, que vem do cruzamento da matriz de passes com o FotMob) não foi recalculada: a fatia de cada zona dela difere em no máximo 0,11 ponto percentual das bases novas, e a mudança desta rodada nas fatias é de 0,02. A Copa 2022 e a Eurocopa 2020, que entraram no banco em 04/10, ficam fora da tabela por serem de seleções.


## Achado 45 — distribuição por zona de origem do chute (9 x 14) recalculada na mesma base das outras tabelas: as linhas de ataque mudam no máximo 0,002

**O que é.** `DISTRIBUICAO_ZONA_CHUTE` (`src/utils/zoneTransitionMatrix.js`) diz, para cada uma das 9 zonas 3x3 de origem do chute (terço de comprimento x terço de largura), em que fração dos chutes ele cai em cada uma das 14 zonas polares. O motor de posse sorteia a zona polar do chute a partir dela. Era uma fotografia de 01/10/2026 com todas as datas e todas as ligas, enquanto `XG_MEDIO_ZONA_CHUTE` e `ESTATISTICA_ZONA_CHUTE` já estavam só com clubes desde 01/08/2021 (Achados 40 e 44).

**Método e validação.** A consulta original não estava salva; reconstruída em `arquivos_do_claude/distribuicao_zona_chute.sql`. Zona 3x3 = terço de comprimento (x < 35, 35-70, >= 70) x terço de largura (y < 22,67 é esquerda, < 45,33 centro, resto direita), gol em (105, 34); zona polar como no Achado 44. **Validação:** com a população de 01/10 (todas as ligas e datas, sem o que entrou no banco em 04/10) a consulta reproduz a matriz do código nas quatro casas (ataque-esquerda: zona 12-16,5 aberto 0,2091 contra 0,2092, zona 16,5-22 aberto 0,3494 contra 0,3495; ataque-centro, 22-30 central: 0,1971 contra 0,1971). Isso também fixou a orientação: "esquerda" é a faixa de y menor.

**Base nova.** Só ligas de clubes, jogos desde 01/08/2021, 428.248 chutes (a mesma base de `ESTATISTICA_ZONA_CHUTE`; os totais por zona polar fecham com o Achado 44, por exemplo 9.996 chutes na zona 0-6 m central e 17.594 na > 30 m central). Por localização o chute não depende do modelo de xG, então a data de corte não vicia a matriz; ela foi mantida só para a base ser a mesma das outras tabelas.

**Resultado.** Chutes por linha: defesa 12 / 39 / 16; meio 575 / 1.559 / 539; ataque 45.629 / 329.189 / 50.690 (as três de ataque são 99,5% dos chutes). Comparação com a matriz anterior, célula a célula (126 células): a maior diferença é 0,0064 em `meio_esq` (zona > 30 m aberto 0,1571 -> 0,1635), linha que tem só 575 chutes e erro-padrão de ~0,015 naquela célula, ou seja, ruído; só 2 células passam de 0,003 e as duas são essa. Nas linhas de ataque a maior diferença é 0,0021 (ataque-esquerda, 22-30 m aberto, 0,2629 -> 0,2608). As linhas de defesa são ">30 m central" por geometria (de lá o ângulo ao gol nunca passa de 30 graus), então não mudam.

**Mudanças no repositório.** `DISTRIBUICAO_ZONA_CHUTE` e o comentário que descrevia as bases em `zoneTransitionMatrix.js` (agora as três tabelas dizem usar a mesma base); contagens e a matriz antiga em `dados_referencia/fotmob/distribuicao_zona_chute_clubes.json`; a consulta em `arquivos_do_claude/distribuicao_zona_chute.sql`. Testes: 132 do frontend passam; build ok.

**Limites.** As linhas de defesa e de meio têm poucos chutes (12 a 1.559) e valem pelo que a geometria impõe, não por frequência observada; elas pesam quase nada porque a posse quase nunca vira chute lá (`TAXA_DESFECHO` ~0). A matriz de passes (3x3) continua sendo a do StatsBomb La Liga 2015/16 e não foi mexida.


---

## Achado 46 — o simulador como previsor (Premier League 2025/26, 342 jogos): a força dos times ajuda de forma significativa, mas ele ainda não bate o Dixon-Coles nem o mercado

**Pergunta.** As probabilidades de 1X2 e de over/under 2,5 obtidas simulando cada jogo 1.000 vezes são melhores que as do Dixon-Coles (`dixon_coles_v1`) e as das odds de fechamento? Primeiro teste fora da amostra do simulador como previsor (o Achado 41 validou só totais por jogo, nunca placar nem empate).

**Método** (`scripts/backtest_simulador_preditivo.py`, workflow `backtest_simulador_preditivo.yml`, entrada em `dados_referencia/backtest_simulador/`). Premier League 2025/26, 380 jogos, dos quais 342 têm Dixon-Coles **e** odds (a régua é calculada só nesses). Walk-forward: a força de cada time para o jogo J usa só jogos anteriores a J (a temporada 2024/25 inteira de aquecimento + a de teste até J), a partir de chutes feitos e sofridos (FotMob), encolhida para 1 com 8 pseudo-jogos. Só o canal "chute" tem força por time: o FotMob não tem as coordenadas de passe que `forca_dos_times.py` usa (StatsBomb), então perda e quebra ficam em 1. Mando de campo entra como fator nos chutes; o nível de chutes por time é reescalado para a média da história. Simulador no perfil padrão de 2015/16 (a recalibração por competição do Achado 41 **não** foi usada). Controle: a mesma simulação com todos os times iguais (só nível e mando). Odds: média das casas no fechamento, sem a margem (proporcional). Diferença pareada por jogo, IC 95% por bootstrap sobre jogos (5.000).

**Resultado (log-loss médio; menor é melhor).**

| | 1X2 | over/under 2,5 |
|---|---|---|
| mercado (fechamento) | 0,9985 | 0,6823 |
| Dixon-Coles | 1,0347 | 0,6872 |
| simulador com força dos times | 1,0657 | 0,7021 |
| simulador, times iguais (controle) | 1,0880 | 0,7052 |
| régua: frequências fixas do próprio teste | 1,0796 | 0,6856 |
| régua: 50/50 | — | 0,6931 |

Diferenças pareadas (positivo = o primeiro é pior), com IC 95%:
- **força − controle, 1X2: −0,0223 [−0,0325; −0,0127]** (Brier −0,0161 [−0,0232; −0,0093]). A força dos times por chutes melhora o simulador de forma significativa.
- força − Dixon-Coles, 1X2: +0,0309 [−0,0038; +0,0646]. O simulador é pior, mas o IC inclui zero.
- **força − mercado, 1X2: +0,0671 [+0,0330; +0,0989]** (Brier +0,0454 [+0,0206; +0,0688]). Significativamente pior que o mercado. O Dixon-Coles também é pior que o mercado: +0,0362 [+0,0147; +0,0583].
- over/under: força − controle −0,0031 [−0,0092; +0,0028]; força − Dixon-Coles +0,0149 [−0,0187; +0,0470]; força − mercado +0,0198 [−0,0042; +0,0434]. Nenhuma diferença é distinguível de zero, e o simulador (0,7021) fica **pior que jogar 50/50** (0,6931).

**Por que erra (diagnóstico nos 342 jogos).**
- **Nível de gols baixo:** 2,49 gols por jogo simulados contra 2,75 reais; over 2,5 simulado 45,8% contra 56,1% real. Os chutes batem (24,5 contra 25,0). É o que derruba o over/under: o erro é de nível, não de ordenação.
- **Vantagem de jogar em casa subestimada:** vitória do mandante 37,9% simulada contra 42,4% real (visitante 35,3% contra 30,7%). O fator de mando só mexe nos chutes.
- **Empate certo:** 26,8% simulado contra 26,9% real. O Dixon-Coles dá 22,3% (subestima) e o mercado 24,7%.
- **Ordenação do over/under boa, nível ruim:** por quartil de probabilidade prevista pelo simulador, o over real sobe 45% → 56% → 58% → 65% (monotônico); no Dixon-Coles é 52% → 52% → 60% → 62%. Com ~85 jogos por quartil o erro-padrão é ~5 pontos, então isto é indício, **não** prova.

**O que não se pode concluir.** (1) Uma liga e uma temporada, 342 jogos: os IC são largos. (2) Não verifiquei se `dixon_coles_v1` em `model_predictions` foi gerado estritamente fora da amostra para esses jogos; se não foi, ele está favorecido, e mesmo assim o simulador não o supera. (3) Com 1.000 simulações por jogo, cada probabilidade tem ruído de ~±0,016 só da simulação; isso adiciona um pouco de log-loss a todos os resultados do simulador. (4) "Não bate o mercado" não é surpresa (nenhum dos modelos testados aqui bate); a pergunta útil é se chega perto do Dixon-Coles, e ainda não chega.

**Hipóteses a testar em seguida (não testadas; cada uma exige correção walk-forward, nunca ajustada com os dados do teste):** (a) recalibrar o nível de gols e o mando com a história anterior de cada jogo, o que atacaria direto o over/under e o 1X2; (b) usar o perfil recalibrado do Achado 41; (c) força de defesa e de perda de bola, não só de chutes; (d) previsões por jogador (`player_match_estimates`) como entrada da força do time — hoje só existem 66 jogos da Premier League 2025/26, todos com escalação oficial, o que é pouco para este teste; (e) outras ligas.

**Operação.** Rodar localmente na sessão falhou duas vezes (o contêiner reinicia e mata o processo; a rodada local chegou a 104/760). O workflow divide as 760 simulações em 8 jobs e termina em ~10 minutos; cada simulação tem semente fixa, então o resultado não depende do fatiamento. O script grava o progresso e retoma de onde parou.


---

## Achado 47 — camadas de ajuste: o nível de gols conserta o over/under (empata com o Dixon-Coles), o mando na conversão não ajuda, e o 1X2 não mexe

**Pergunta.** Dar ao simulador entradas modulares sobre a base fixa das transições (Achado 46 apontou nível de gols baixo e mando subestimado) fecha a distância para o Dixon-Coles e o mercado? Estrutura em `scripts/camadas_simulador.py`: a base não muda; cada camada olha só jogos anteriores, devolve multiplicadores perto de 1,0 sobre chute e conversão (chance do chute virar gol, botão novo `conversao_ataque/defesa` em `Multiplicadores`, neutro = jogo idêntico) e é combinada por produto com teto [0,6; 1,6]. Ablação: uma camada por vez, cada variante comparada com a anterior. Conferido antes de rodar: as variantes `neutro` e `forca` reproduzem exatamente as probabilidades do Achado 46.

**Método.** Mesmo teste do Achado 46 (Premier League 2025/26, 342 jogos com Dixon-Coles e odds, 1.000 simulações por jogo, IC 95% por bootstrap pareado sobre jogos). Camadas: `gols_nivel` = gols por chute da história sobre o do simulador neutro (0,1017, medido em 2.500 jogos); `gols_mando` = razão (gols por chute em casa)/(fora) da história, encolhida por jogos/(jogos+50), dividida entre os dois lados.

**Resultado (log-loss médio; menor é melhor).**

| | 1X2 | over/under 2,5 |
|---|---|---|
| mercado | 0,9985 | 0,6823 |
| Dixon-Coles | 1,0347 | 0,6872 |
| times iguais (controle) | 1,0880 | 0,7052 |
| + força por chutes | 1,0657 | 0,7021 |
| + nível de gols | 1,0667 | **0,6868** |
| + mando na conversão | 1,0692 | 0,6898 |

Diferenças pareadas (positivo = o primeiro é pior), IC 95%:
- **`gols_nivel` sobre a força: over/under −0,0153 [−0,0291; −0,0012]** (melhora significativa); 1X2 +0,0010 [−0,0064; +0,0087] (nada).
- **`gols_mando` sobre `gols_nivel`: 1X2 +0,0025 [−0,0043; +0,0092]; over/under +0,0030 [−0,0013; +0,0073]** (nada; levemente pior). **Fica fora.**
- Com o nível de gols, o simulador **empata com o Dixon-Coles no over/under** (−0,0004 [−0,0247; +0,0234]) e é indistinguível do mercado (+0,0045 [−0,0109; +0,0201]). Antes do nível ele estava pior que 50/50 (0,6931); agora 0,6868.
- 1X2 continua pior que o Dixon-Coles (+0,0320 [−0,0014; +0,0641]) e significativamente pior que o mercado (+0,0682 [+0,0348; +0,0992]). A variante com mando fica significativamente pior que o Dixon-Coles no 1X2 (+0,0345 [+0,0003; +0,0680]).

**Diagnóstico (342 jogos).** Gols por jogo simulados 2,74 com o nível (2,49 antes) contra 2,79 reais; over 2,5 médio 52,1% contra 56,1% real (ainda abaixo). Vitória do mandante simulada 38,6% contra 42,4% real (visitante 36,1% contra 30,7%): o erro de mando do Achado 46 **não** foi corrigido, e o 1X2 não melhora.

**Por que o mando falhou: a estimativa vinda da história fica atrasada.** O mando mudou entre as duas temporadas do CSV. Razão de chutes casa/fora: 1,130 em 2024/25 contra 1,240 em 2025/26. Gols por chute casa/fora: 0,943 contra 1,006. Mandante vence 40,8% contra 42,6%; visitante 34,7% contra 30,0%. Um ajuste que olha a história (quase toda de 2024/25 durante o teste) subestima o mandante de 2025/26 por construção; nenhuma camada walk-forward "adivinha" uma mudança de nível entre temporadas. Isto é uma explicação consistente com os números, não um teste dela.

**O que ficou decidido.** `gols_nivel` fica ligada; `gols_mando` fica no código e desligada (decisão pela ablação, não por gosto). O ganho real foi só no over/under; o 1X2 depende de discriminar bem a força dos times, e hoje essa força vem só de chutes feitos e sofridos.

**Próximas camadas a testar (cada uma por ablação, nenhuma ajustada com dados do teste):** (a) mando com janela recente ou ponderada pelo tempo, para seguir mudanças entre temporadas (hipótese acima); (b) Elo de xG (`team_elo_xg`) como força geral; (c) fragilidade defensiva em chutes sofridos e xG sofrido por chute (`conversao_defesa` já existe e não foi usada); (d) jogadores, só quando houver cobertura (hoje 66 jogos da Premier League 2025/26 em `player_match_estimates`).

**Limites.** Uma liga e uma temporada, 342 jogos: os IC são largos e várias diferenças "sem efeito" aqui podem ser efeitos pequenos não detectáveis. O ajuste de nível usa a constante do simulador neutro (0,1017) medida com ruído de ~±1,5%. Dixon-Coles com a mesma ressalva do Achado 46 (não verifiquei se foi gerado fora da amostra para esses jogos).


---

## Achado 48 — xG e xA previstos por jogador: top-2 a top-5 não batem o elenco inteiro, e quase tudo que se correlaciona com o saldo do jogo é a força do time

**Pergunta.** Somar só os N jogadores de maior xG (ou xA) previsto, em vez do elenco inteiro, correlaciona melhor com o saldo do jogo? E isso é sinal dos jogadores ou da força do time?

**Método** (`scripts/analisar_topn_jogadores.py`, consulta em `arquivos_do_claude/topn_jogadores_previsto.sql`). Cinco ligas (Premier League, La Liga, Serie A, Bundesliga, Ligue 1), temporadas 2024 e 2025, 3.504 partidas com Elo. λ de `player_match_walkforward` (walk-forward) na fonte `previsto` (XI previsto, anterior ao jogo). Para cada partida, D_N = soma do λ dos N maiores do mandante menos a do visitante (N = 2, 3, 4, 5 e elenco inteiro); o ranking usa só o λ previsto. Alvos: saldo de gols e saldo de xG reais. Como a força do time confunde (armadilha já vista 3x no projeto), reporta também a **correlação parcial controlando pela diferença de Elo** (`team_elo_history`, escopo global, rating antes do jogo) e o IC 95% por bootstrap sobre partidas da diferença top-N menos elenco inteiro. λ nulo vira 0 (nunca selecionar por "existe λ", o viés pós-jogo do Achado 25/09).

**Resultado: correlação r com o saldo de xG (alvo menos ruidoso).**

| | r | r parcial (Elo) | top-N menos elenco inteiro [IC95%] |
|---|---|---|---|
| Elo sozinho | 0,506 | — | — |
| xG previsto top-2 / 3 / 4 / 5 / inteiro | 0,477 / 0,481 / 0,483 / 0,484 / 0,494 | 0,104 / 0,105 / 0,107 / 0,109 / 0,127 | -0,017 [-0,027; -0,007] / -0,013 / -0,011 / -0,010 [-0,015; -0,004] |
| xA previsto top-2 / 3 / 4 / 5 / inteiro | 0,478 / 0,492 / 0,497 / 0,500 / 0,510 | 0,105 / 0,121 / 0,127 / 0,131 / 0,149 | -0,032 [-0,041; -0,021] / -0,018 / -0,013 / -0,009 [-0,015; -0,004] |

Contra o saldo de **gols**: Elo sozinho 0,430; xG top-2/3/4/5/inteiro 0,412 / 0,411 / 0,409 / 0,406 / 0,405 (top-N sobre o inteiro +0,006 [-0,004; +0,016] no top-2, sem efeito); xA 0,410 / 0,421 / 0,425 / 0,427 / 0,430 (top-2 -0,020 [-0,029; -0,010], top-3 -0,009 [-0,017; -0,001], top-4 e 5 sem diferença). Correlação parcial com gols: 0,07 a 0,11.

**Leitura.**
1. **Somar o elenco inteiro é igual ou melhor que qualquer top-N.** Contra o saldo de xG todos os top-N ficam abaixo, de forma monotônica (quanto menos jogadores, pior) e com IC fora de zero. Contra gols só o xA top-2 e top-3 ficam abaixo; o xG top-2/3 parece ligeiramente acima, mas dentro do ruído. Confirma, com xG incluído e em 3.504 partidas, o que o projeto já tinha para xA (elenco inteiro 0,403, top-3 0,389, top-2 0,381 em 15.089 partidas) e o descarte do top-N como agregação.
2. **O Elo sozinho já correlaciona tanto quanto os jogadores** (0,430 e 0,506 contra 0,405-0,430 e 0,477-0,510). O que os jogadores trazem **além do Elo** é pequeno: correlação parcial de 0,07 a 0,15, ou seja, 0,5% a 2% da variância do saldo. É o mesmo padrão do rating de jogador do projeto (sinal real, pequeno, e o Elo explica a maior parte).
3. **Por que o top-N perde:** descartar jogadores joga fora informação de profundidade do elenco e de quem entra; somar mais jogadores dilui o acaso individual (cada xG por jogador explica só ~24% da variação, ver acima).

**Limites.** Cinco ligas empilhadas e duas temporadas: diferenças entre ligas podem se misturar. A correlação parcial é linear. Elo de resultado, não o de xG. Bundesliga e Ligue 1 têm 612 partidas cada (18 times). Não rodei `relacionados` (convocados), só `previsto`. Correlação com o saldo não é o mesmo que ganho de previsão em log-loss: o ganho de log-loss de jogadores (saber quem começa, -0,015 no 1X2) está registrado à parte.


---

## Achado 49 — mando por janela recente não ajuda, e o motivo é outro: o simulador entrega só ~55% da razão de chutes que o multiplicador pede

**Pergunta.** A hipótese do Achado 47: o mando estimado pela história inteira fica atrasado quando o mando muda de temporada (razão de chutes casa/fora 1,13 em 2024/25, 1,24 em 2025/26). Olhar só os últimos N jogos resolve?

**Método.** Mesmo teste do Achado 47 (Premier League 2025/26, 342 jogos, 1.000 simulações por jogo, IC 95% por bootstrap pareado). Referência = `forca_gols_nivel` (mando pela história inteira). Variantes: `mando_j200` e `mando_j100` trocam o mando dos chutes pelos últimos 200 / 100 jogos; `mando_j200_gols` soma o mando da conversão pela janela de 200. Camadas em `scripts/camadas_simulador.py` (`mando_chutes_janela`, `gols_mando_janela`), três testes novos.

**Resultado: nulo.** Log-loss 1X2 / over-under: referência 1,0667 / 0,6868; `mando_j200` 1,0655 / 0,6865 (1X2 -0,0012 [-0,0078; +0,0054]); `mando_j100` 1,0672 / 0,6888 (+0,0005 [-0,0062; +0,0071]); `mando_j200_gols` 1,0665 / 0,6878 (sobre `mando_j200` +0,0010 [-0,0070; +0,0092]). Todos os IC incluem zero. Vitória do mandante simulada: 38,6% (referência), 38,8% (j200), 39,2% (j100), contra 42,4% real. A janela mexe 0,2 a 0,6 ponto percentual; faltam ~3,5. **Fica fora do ajuste**, como as outras camadas de mando, e é a mesma conclusão do EWM de mando que o projeto já tinha descartado.

**O que a janela não era: o problema.** Diagnóstico (`scripts/diagnostico_elasticidade_mando.py`, 2.800 jogos por linha): time médio contra time médio, impondo ao simulador uma razão de chutes casa/fora r (multiplicadores raiz de r e 1/raiz de r):

| razão imposta | razão de chutes realizada | casa / empate / fora | gols casa / fora |
|---|---|---|---|
| 1,00 | 1,012 | 0,361 / 0,271 / 0,368 | 1,24 / 1,26 |
| 1,13 (2024/25) | 1,071 | 0,376 / 0,268 / 0,355 | 1,27 / 1,22 |
| 1,24 (2025/26) | 1,126 | 0,405 / 0,256 / 0,339 | 1,31 / 1,18 |

A razão realizada é ~55% da pedida em escala logarítmica (ln 1,126 / ln 1,24 = 0,55; ln 1,071 / ln 1,13 = 0,56). Mesmo impondo a razão real de 2025/26, o simulador dá 40,5% de vitória em casa e gols 1,31 x 1,18 (razão 1,11), contra 42,6% e 1,53 x 1,22 (razão 1,25) no mundo real. Ou seja: **o multiplicador de chute é atenuado pela dinâmica de posse** (quem chuta mais por linha também cede a bola nas outras), e o erro de ~3,5 pontos de mando não é atraso de estimativa.

**Consequência provável, ainda não testada.** A mesma atenuação deve valer para a camada de força dos times (`forca_chutes`, ataque x defesa), o que explicaria por que a força melhora o 1X2 só de leve (Achado 46) e por que o simulador discrimina pouco os times. O projeto já tem um expoente de amortecimento (`calibrar_expoente_forca.py`, Achado 32), mas no sentido contrário (amortecer a perda); aqui o que parece faltar é um expoente de **amplificação** (~1/0,55 = 1,8) nos multiplicadores de chute.

**Próximo teste (proposto).** Camada `expoente_chute`: elevar os multiplicadores de chute (mando e força) a 1/0,55. O expoente sai deste diagnóstico no próprio simulador, não dos dados do teste, então não há ajuste em cima do que se mede; validar por ablação como as outras camadas.

**Limites.** O diagnóstico usa times médios, sem camada de nível de gols; a elasticidade pode variar com a magnitude e entre ligas. Comparação de mando real com 2025/26 de uma liga só.


---

## Achado 50 — o mando é volume de chutes (não qualidade), e quem perde chuta mais e pior em qualquer perfil de força

**Pergunta (do usuário).** (1) O mando multiplica também o xG e o λ, não só os chutes? (2) Times fracos que estão perdendo chutam mais, com menor qualidade? Consultas em `arquivos_do_claude/mando_e_estado_do_jogo.sql`.

**(1) Mando.** Cinco ligas, 2022 a 2025 (20 liga-temporadas, jogos com xG dos dois lados). Razão casa/fora: **chutes 1,13 a 1,31** (típico 1,17 a 1,30); **xG por chute 0,95 a 1,09, média 1,017** (Bundesliga +2,6%, La Liga +3,8%, Ligue 1 +4,2%, Premier League +0,7%, Serie A -2,8%); gols por xG de casa e de fora ficam em 0,92 a 1,14, sem lado sistemático. Logo o xG do mandante é ~1,15 a 1,36 vezes o do visitante, e **quase tudo isso é volume**: a qualidade por chute entra com ~+2% (varia de sinal entre ligas) e a conversão com ~0. O multiplicador de mando pertence ao volume de chutes; pôr mando na conversão ou na qualidade não tem base nos dados, o que explica o `gols_mando` nulo do Achado 47.

**(2) Estado do jogo por perfil de força** (5 ligas, `v_game_state_por_forca` separada pelo SINAL da diferença de Elo; a coluna `faixa_forca` da view agrupa pelo tamanho e mistura forte e fraco). Chutes por 90 minutos / xG por chute:

| perfil | ganhando | empatando | perdendo |
|---|---|---|---|
| bem mais fraco (Elo <= -100) | 8,18 / 0,1207 | 9,12 / 0,1005 | 11,05 / 0,1004 |
| parelho (|Elo| <= 30) | 10,17 / 0,1215 | 11,31 / 0,1055 | 13,60 / 0,1034 |
| bem mais forte (Elo >= 100) | 13,33 / 0,1359 | 14,62 / 0,1147 | 16,94 / 0,1097 |

- **Volume:** perdendo, o time chuta bem mais que ganhando, em todos os perfis (fraco +35%, parelho +34%, forte +27%).
- **Qualidade:** perdendo, a qualidade por chute é 15 a 19% menor que ganhando (fraco 0,1004 contra 0,1207; forte 0,1097 contra 0,1359). Em relação a empatando, o time perdendo mal piora (fraco 0,1005 -> 0,1004; forte 0,1147 -> 0,1097): a diferença vem sobretudo de quem está ganhando ter chute melhor.
- **Os dois efeitos quase se cancelam no xG total:** fraco perdendo gera 1,11 xG/90 contra 0,99 ganhando (+12%); forte, 1,86 contra 1,81 (+3%). É por isso que o xG agregado quase não mostra efeito de estado (conclusão que o projeto já tinha), enquanto os componentes mostram.
- **A hipótese do usuário vale, mas não é específica de time fraco:** é efeito do placar em todo perfil; a força separa o nível (o forte tem chute ~14% melhor em qualquer estado), não a reação.

**O simulador ignora o placar.** `simular_partida` não tem nenhuma reação ao placar: nem mais volume de quem perde, nem melhor chance de quem ganha. Os efeitos acima (+27 a +35% em volume, 15 a 19% em qualidade) são grandes e têm sentido regressivo, isto é, puxam o placar de volta, o que afeta a distribuição de saldos e o 1X2.

**Limites.** Comparar ganhando com perdendo é pareado no tempo (linhas espelhadas, mesmos minutos); comparar com empatando não é (empate tem muito 0 a 0 inicial, mais cauteloso), então não leio o "empatando" como base causal. Não controla casa e fora dentro do estado. Perfil de Elo de resultado.

**Próximos testes (propostos):** (a) expoente de amplificação nos multiplicadores de chute (Achado 49), (b) camada de estado do jogo no simulador (multiplicador de volume e de conversão por saldo de gols), com parâmetros estimados só nas temporadas 2022 a 2024 e avaliada em 2025/26.

**Complemento do Achado 48 — a diferença entre os times no top-N contra a diferença no resto do elenco** (`scripts/decompor_topn_resto.py`, mesmas 3.504 partidas). O R² do saldo de xG só com o Elo é 0,256 (desvio-padrão da diferença de Elo entre os times: 177 pontos). Somar a diferença de xG previsto entre os times eleva o R² em apenas +0,008 (top-2) a +0,012 (elenco inteiro); com xA, +0,008 (top-2) a +0,016 (elenco inteiro), isto é, de 0,8 a 1,6 ponto percentual de variância além do Elo. A diferença entre os times é maior no elenco inteiro do que no top-N (desvio-padrão em xG: top-2 0,34, top-3 0,44, top-5 0,58, inteiro 0,80; em xA: 0,17, 0,23, 0,34, 0,62). **O resto do elenco acrescenta tanto ou mais que os craques:** com Elo mais top-3 e resto separados, o resto sozinho ganha +0,010 (xG) e +0,015 (xA) contra +0,008 e +0,011 do top-3 sozinho; separar top-N e resto não melhora o elenco inteiro (R² 0,2683 contra 0,2682 em xG). Ou seja, profundidade de elenco pesa pelo menos tanto quanto os melhores jogadores, o que combina com o top-N não bater o elenco inteiro.


---

## Achado 51 — amplificar o multiplicador de força dos times (expoente 2) melhora o 1X2 de forma significativa e leva o simulador a um empate técnico com o Dixon-Coles; o mesmo no mando não ajuda

**Pergunta.** O Achado 49 mostrou que o simulador realiza só ~50% (em log) da razão de chutes pedida por um multiplicador. Compensar com um expoente nos multiplicadores de chute melhora a previsão?

**Calibração (sem dado de jogo real).** `scripts/calibrar_elasticidade_chute.py`: com times médios, o simulador realiza razão de chutes pedida^e com **e = 0,44 (m 1,06), 0,48 (1,12), 0,49 (1,25)**, idêntico para ataque e defesa (o código multiplica os dois no mesmo ponto); erro-padrão ~0,05, 0,03 e 0,014; compatível com 0,49 a 0,50. Expoente de compensação = 1/e ≈ **2,0**, fixado ANTES de ver qualquer resultado de teste. Fica como parâmetro: `--exp-mando`, `--exp-forca`, `--cfg-extra` e entradas do workflow (padrão 1,0 nas camadas = comportamento dos Achados 46 a 49). O progresso do backtest só é reaproveitado se os parâmetros forem idênticos, e os valores usados ficam em `resumo["parametros"]`.

**Método.** Mesmo teste dos Achados 46 a 49 (Premier League 2025/26, 342 jogos, 1.000 simulações por jogo, IC 95% por bootstrap pareado). Referência `forca_gols_nivel`; variantes: expoente 2 só no mando (`exp_mando`), só na força dos times (`exp_forca`, ataque e defesa), nos dois (`exp_ambos`), com teto dos multiplicadores alargado para [0,5; 2,0].

**Resultado (log-loss médio 1X2 / Brier 1X2 / log-loss over-under).**

| | 1X2 | Brier 1X2 | over/under 2,5 |
|---|---|---|---|
| mercado | 0,9985 | 0,5980 | 0,6823 |
| Dixon-Coles | 1,0347 | 0,6219 | 0,6872 |
| referência (Achado 47) | 1,0667 | 0,6435 | 0,6868 |
| expoente 2 só no mando | 1,0615 | 0,6400 | 0,6849 |
| **expoente 2 só na força** | **1,0481** | **0,6301** | 0,6901 |
| expoente 2 nos dois | 1,0487 | 0,6305 | 0,6901 |

Diferenças pareadas (negativo = melhora), IC 95%:
- **força amplificada sobre a referência: 1X2 -0,0186 [-0,0279; -0,0096]**, Brier -0,0134 [-0,0199; -0,0072]. Melhora significativa, maior que a de qualquer camada anterior.
- mando amplificado sobre a referência: -0,0052 [-0,0126; +0,0022]. Sem efeito detectável. E nos dois sobre só na força: sem ganho extra.
- **força amplificada contra o Dixon-Coles: +0,0134 [-0,0172; +0,0421]**; antes eram +0,0320 [-0,0014; +0,0641]. Passa a ser indistinguível do Dixon-Coles no 1X2. Contra o mercado continua pior: +0,0496 [+0,0201; +0,0770] (era +0,0682).
- over/under: força amplificada +0,0033 [-0,0035; +0,0097], sem diferença detectável.

**Diagnóstico.** Gols por jogo simulados 2,74 (referência), 2,66 (força amplificada) contra 2,79 reais; over 2,5 médio 52,1% e 50,3% contra 56,1% real. Vitória do mandante 38,6% (referência), 39,9% (mando amplificado), 38,3% (força), contra 42,4% real; empate 25,4 a 25,6% contra 26,9%. A força amplificada melhora o log-loss porque **discrimina melhor os times**, não porque corrija as médias.

**Leitura.** A atenuação do multiplicador de chute era a causa principal da fraqueza em discriminar a força dos times: com o expoente que compensa, o simulador chega ao patamar do Dixon-Coles no 1X2 numa janela de 342 jogos. O mando não se beneficia: mesmo com razão de chutes agora realizada, a vitória do mandante continua ~2,5 pontos abaixo do real (39,9% contra 42,4%); o que falta não está no volume de chutes, e o Achado 50 aponta o estado do jogo como candidato.

**Limites.** (1) Uma liga e uma temporada. (2) A elasticidade foi medida com times médios e magnitudes de 1,06 a 1,25; vale supor o mesmo expoente para forças mais extremas, sem teste. (3) O nível de gols piorou com a amplificação (2,66 contra 2,79 reais) porque `gols_nivel` usa a constante do simulador neutro (0,1017); recalibrá-la para a configuração amplificada é o ajuste natural para o over/under. (4) Dixon-Coles com a mesma ressalva do Achado 46 (não verifiquei se foi gerado fora da amostra para esses jogos).

**Próximos testes (propostos):** recalibrar `gols_nivel` sob a amplificação; camada de estado do jogo (Achado 50); repetir em outras ligas e temporadas, que é o que decide se o expoente 2 se mantém.


---

## Achado 52 — resposta ao placar por saldo exato de gols: o volume de chutes sobe e a qualidade cai de quem perde, e a curva é a mesma em todos os contextos de liga

**Pergunta (do usuário).** Como o placar altera a probabilidade de chute, a qualidade do chute e a permissão de toques no último terço, e como entra o "placar desejado" de cada time (vitória simples, empate, vitória por 2, aceitar perder por 1 em jogo de volta; empate fora, vitória em casa, empate em casa contra um time muito superior)?

**Método** (consultas somente de leitura, mesma regra de relógio da `derivar_game_state`, com `clock`). 5 ligas, temporadas 2022 a 2024 (a avaliação fica para 2025/26, sem vazamento), estado = saldo de gols de quem chuta, truncado em -2 e +2. Separado por perfil de força (sinal da diferença de Elo antes do jogo: fraco <= -100, forte >= +100) e por mando. Multiplicador = taxa do estado dividida pela taxa do empate do mesmo grupo.

**Resultado agrupado (ponderado por minutos; volume = chutes por 90 minutos; qualidade = xG por chute):**

| saldo de quem chuta | volume | qualidade |
|---|---|---|
| -2 ou menos | x1,231 | x0,966 |
| -1 | x1,185 | x0,979 |
| 0 | 1,000 | 1,000 |
| +1 | x0,883 | x1,148 |
| +2 ou mais | x0,941 | x1,235 |

- Quem perde chuta 18% a 23% mais, com qualidade 2% a 3% menor; quem ganha por 1 chuta 12% menos e com qualidade 15% maior; ganhando por 2 ou mais, a qualidade sobe 24%.
- A qualidade piora pouco para quem perde em relação ao empate (a queda grande do Achado 50 é contra quem ganha); o que sobe forte é a qualidade de quem ganha (contra-ataques em campo aberto).
- **Sem controlar força, a curva engana:** o volume de quem ganha por 2 ou mais parece subir (12,2 chutes por 90 minutos contra 10,9 ganhando por 1) porque quem está duas bolas na frente costuma ser o time melhor; dentro de cada perfil a curva é limpa e igual.

**A curva é a mesma em casa e fora, em fraco, parelho e forte.** Volume com saldo -1: x1,16 a x1,24; com +1: x0,86 a x0,89; qualidade com +1: x1,13 a x1,20; entre os seis grupos (casa/fora x perfil) a variação cabe no ruído. O time fraco jogando fora, candidato a jogar pelo empate, não mostra curva deslocada. **Conclusão: em liga não aparece objetivo diferente por contexto no nível de detalhe disponível**; o objetivo padrão pode ser o mesmo para todos.

**Placar desejado como parâmetro.** Cada time recebe um alvo = saldo final que considera satisfatório (padrão +1, "ganhar"). A resposta usa o saldo efetivo = saldo atual + (1 - alvo), truncado em -2 e +2: empate satisfatório (alvo 0) faz o 0 a 0 valer como +1; vitória por 2 (alvo +2) faz o 1 a 0 valer como 0 a 0; aceitar perder por 1 (alvo -1) faz o 0 a 1 valer como +1. Em jogo de volta com vantagem do agregado L trazida da ida, alvo = 1 - L.

**Limites.** (1) O placar desejado só pode ser verificado em jogos de volta de mata-mata, e `aggregate_advantage` está vazio nos 32.307 jogos; reconstruído pelo pareamento das partidas há 89 confrontos com chutes nas duas partidas (Libertadores 54, Champions 35; a Copa do Brasil não tem chutes): pouco poder estatístico, então o efeito do alvo fica como parâmetro, não como estimativa. (2) "Empatando" mistura o início cauteloso dos jogos; a comparação limpa é entre os estados de placar, pareada no tempo. (3) A permissão de toques no último terço por placar ainda NÃO foi medida: o FotMob só tem chutes por estado; precisa dos eventos do StatsBomb (La Liga e ligas recentes, torneios), com o placar reconstruído pelos gols.


---

## Achado 53 — a reação ao placar (volume e qualidade de chute) não melhora a previsão do 1X2, e ajustá-la por mando e favoritismo também não

**Pergunta.** Dar ao simulador a resposta ao placar medida no Achado 52 (volume e qualidade do chute por saldo de gols, `simular_partida(estado=...)`) melhora a previsão? E ajustá-la por mando (casa/fora/neutro), favoritismo (Elo) e placar desejado (`montar_estado_jogo`) melhora mais?

**Método.** Mesmo teste dos Achados 46 a 51 (Premier League 2025/26, 342 jogos com Dixon-Coles e odds, 1.000 simulações por jogo, IC 95% por bootstrap pareado). Referência = `exp_forca` (melhor configuração do Achado 51). A tabela de resposta vem só de 2022 a 2024 (sem vazamento); o expoente 2 do Achado 51 amplifica o multiplicador de volume (a qualidade age direto na chance de gol). Variantes: `estado_v` (só volume), `estado_q` (só qualidade), `estado_vq` (os dois), `estado_ctx` (os dois, com a tabela do contexto de cada time: casa/fora x forte/parelho/fraco pelo Elo do jogo, corte de 100 pontos; alvo padrão +1 para todos). O Elo antes do jogo veio de `team_elo_history`; o CSV ganhou as colunas `elod` e `neutro`. As variantes repetidas entre as duas rodadas (`exp_forca`, `estado_vq`) coincidem exatamente.

**Resultado (log-loss médio 1X2 / Brier 1X2 / log-loss over-under).**

| | 1X2 | Brier | over/under |
|---|---|---|---|
| mercado | 0,9985 | 0,5980 | 0,6823 |
| Dixon-Coles | 1,0347 | 0,6219 | 0,6872 |
| referência `exp_forca` | 1,0481 | 0,6301 | 0,6901 |
| `estado_v` | 1,0522 | 0,6336 | 0,6877 |
| `estado_q` | 1,0532 | 0,6332 | 0,6897 |
| `estado_vq` | 1,0529 | 0,6340 | 0,6853 |
| `estado_ctx` | 1,0553 | 0,6357 | 0,6887 |

Diferenças pareadas, IC 95% (positivo = pior):
- reação ao placar sobre a referência, 1X2: `estado_v` +0,0041 [-0,0040; +0,0120], `estado_q` +0,0051 [-0,0021; +0,0123], `estado_vq` +0,0047 [-0,0017; +0,0111]. Nenhuma diferença distinguível de zero, e o sinal é de leve piora.
- reação ao placar sobre a referência, over/under: `estado_vq` -0,0047 [-0,0115; +0,0021], `estado_v` -0,0024, `estado_q` -0,0003. Também sem significância.
- **por contexto sobre o agrupado: 1X2 +0,0024 [-0,0038; +0,0090]**, Brier +0,0018 [-0,0024; +0,0060], over/under +0,0034 [-0,0011; +0,0080]. O ajuste por mando e favoritismo não acrescenta nada.
- contra o Dixon-Coles, 1X2: `estado_vq` +0,0181 [-0,0137; +0,0487], `estado_ctx` +0,0206 [-0,0119; +0,0522]; contra o mercado: +0,0543 [+0,0232; +0,0833] e +0,0568 [+0,0249; +0,0861].

**Diagnóstico das médias.** A reação ao placar melhora as médias de resultado sem melhorar o log-loss: empate simulado 25,6% (referência) para 26,3% (`estado_vq`) e 26,2% (`estado_ctx`), contra 26,9% real; gols por jogo 2,66 para 2,77 (real 2,79); over 2,5 médio 50,3% para 52,6% (real 56,1%). Parte do aumento de gols vem do multiplicador de qualidade (`estado_q` sozinho: 2,66 para 2,76) porque quem está ganhando passa a converter mais; o nível de gols (`gols_nivel`) não foi recalibrado para isso.

**Leitura.** O log-loss do 1X2 é decidido pela capacidade de discriminar a força dos times, que é o que o expoente do Achado 51 corrigiu; a reação ao placar mexe na dinâmica dentro do jogo, regressiva (puxa o saldo de volta), e melhora as proporções de empate e de gols, mas não separa melhor quem é favorito. O resultado para o contexto é consistente com o Achado 52: as curvas de resposta são praticamente iguais nos 6 contextos de liga, então escolher a curva pelo contexto não adiciona informação.

**O que não se pode concluir.** (1) Uma liga e uma temporada, 342 jogos: os IC são largos e efeitos de ±0,01 não são detectáveis. (2) O expoente 2 foi calibrado para multiplicadores fixos e aqui o multiplicador muda durante o jogo. (3) A tabela vem de taxas por minuto que misturam a fase do jogo. (4) O placar desejado não foi exercitado: todos os times usam alvo +1, porque em liga não há contexto em que ele seja conhecido (o campo neutro também não existe no banco) e os jogos de volta com chutes são só 89. A camada fica disponível como parâmetro (`--alvo-casa`, `--alvo-fora`, `--alvo-ctx`), desligada por padrão: a melhor configuração segue sendo `exp_forca`.

**Próximos testes (propostos):** recalibrar `gols_nivel` sob amplificação e reação ao placar; repetir em outras ligas e temporadas (decide se o expoente 2 e o resultado nulo do placar se mantêm); toques no último terço por placar com os eventos do StatsBomb; placar desejado em jogos de volta quando houver mais confrontos com chutes.

## Achado 54 — recalibrar o nível de gols numa temporada e aplicar na seguinte não melhora a previsão de forma distinguível, e o fator ideal muda de uma temporada para outra

**Pergunta.** O simulador com força amplificada (`exp_forca`) e o com reação ao placar (`estado_vq`) erram o total de gols (2,66 e 2,77 por jogo, contra 2,79 real). Um fator fixo de conversão (`gols_fator`, camada nova) calibrado numa temporada corrige isso na seguinte e melhora over/under e 1X2?

**Método.** (1) Calibração em 2024/25 (280 jogos simulados, 2,954 gols reais por jogo): `exp_forca` simulava 2,686 e `estado_vq` 2,787, o que dá fatores de 1,0997 e 1,0598. (2) Os fatores ficaram **fixos** e foram aplicados em 2025/26 (mesmos 342 jogos com Dixon-Coles e odds, 1.000 simulações por jogo, mesmas sementes dos Achados 51 e 53). Comparação pareada jogo a jogo com a versão sem fator, bootstrap de 2.000 reamostragens.

**Resultado (342 jogos, diferença novo menos sem fator; negativo = melhor):**

| | gols/jogo (real 2,79) | P(over) médio (real 56,1%) | 1X2 log-loss | over/under log-loss |
|---|---|---|---|---|
| `exp_forca` sem fator | 2,664 | 50,3% | 1,0481 | 0,6901 |
| `exp_forca` com 1,0997 | 2,903 | 56,1% | 1,0472 (-0,0009 [-0,0070; +0,0052]) | 0,6835 (-0,0066 [-0,0192; +0,0061]) |
| `estado_vq` sem fator | 2,769 | 52,6% | 1,0529 | 0,6853 |
| `estado_vq` com 1,0598 | 2,924 | 56,4% | 1,0491 (-0,0037 [-0,0103; +0,0028]) | 0,6844 (-0,0010 [-0,0092; +0,0079]) |

Referências no mesmo conjunto: Dixon-Coles 1X2 1,0347 / over/under 0,6872; mercado 0,9985 / 0,6823. Contra o Dixon-Coles, `exp_forca` com fator: 1X2 +0,0125 [-0,0175; +0,0412], over/under -0,0038 [-0,0261; +0,0177]. Todos os intervalos incluem zero.

**O que os números dizem.**
- O fator corrige o sentido certo (over simulado sobe de 50,3% para 56,1%, igual ao real), mas **nenhuma melhora é distinguível de zero**. O over/under de `exp_forca` passa a ficar um pouco abaixo do Dixon-Coles e praticamente igual ao mercado (0,6835 contra 0,6823), sem significância.
- O fator **ultrapassou o alvo em gols**: 2,903 e 2,924 simulados contra 2,79 reais (+4,0% e +4,7%). A temporada de calibração tinha 2,95 gols por jogo e a de teste 2,79; o fator ideal em 2025/26 teria sido cerca de 1,044 para `exp_forca` (1,0997 × 0,950) e cerca de 1,0 para `estado_vq` (1,0598 × 0,943), ou seja, **para `estado_vq` o fator não era necessário**.
- Por isso o fator fixo transporta o viés da temporada em que foi calibrado. Quem se adapta à temporada é `gols_nivel` (usa o histórico anterior a cada jogo); o fator corrige só o viés da configuração e não deveria absorver o nível da temporada.

**O que não se pode concluir.** Uma liga, duas temporadas, 342 jogos: efeitos de ±0,01 não são detectáveis. Não testei fator estimado só com o histórico anterior a cada jogo (calibração móvel), que é o caso que importaria em produção.

**Próximos testes (propostos):** calibração móvel do fator (só com jogos anteriores); outras ligas; toques no último terço por placar (StatsBomb); corrigir o rótulo de `p_quebra` no simulador (100% em todas as zonas por filtro antigo `mesma|recuperação|`).

## Achado 55 — a qualidade do chute não cai de forma consistente ao longo do jogo; o que muda depende do estado do placar, e o reserva chuta com qualidade um pouco maior que o titular

**Pergunta.** Os Achados 8 e 27 mostravam xG por chute plano ao longo do jogo (0,108 a 0,113) e conversão subindo um pouco no 2º tempo. A hipótese era que a qualidade cai com a fadiga e que a entrada de reservas seria a exceção. Esses achados mediam a média de tudo junto, sem separar estado do placar, força do time nem quem chuta.

**Método** (`arquivos_do_claude/qualidade_chute_no_tempo.sql`, só leitura; `scripts/analisar_qualidade_chute_no_tempo.py`, sem rede; dados agregados em `dados_referencia/qualidade_chute/`). Premier League, La Liga, Serie A, Bundesliga e Ligue 1, 2021 a 2025: 223.000 chutes, sem pênalti, gol contra e disputa de pênaltis. Estado do placar ANTES do chute e relógio monótono (`clock`), a mesma regra de `derivar_game_state`. Células: liga × estado (perde/empata/ganha) × perfil de força (Elo global, corte 100) × tipo de lance (jogo corrido ou bola parada). Cada faixa de 15 minutos é padronizada para a mesma composição de células. Fadiga medida só em titulares. Reserva comparado com titular na mesma célula e faixa de tempo, ponderando pelos chutes do reserva. Papel do reserva por minutos desde a entrada (`match_lineup_fotmob.substituted_in_minute`).

**Resultado 1: sem controle, a qualidade é plana, e a alta no fim vem dos reservas.**

| faixa | 0-15 | 15-30 | 30-45 | 45-60 | 60-75 | 75-90 |
|---|---|---|---|---|---|---|
| xG/chute, todos | 0,1023 | 0,1003 | 0,1011 | 0,1016 | 0,1042 | 0,1035 |
| xG/chute, só titulares | 0,1022 | 0,1004 | 0,1012 | 0,1016 | 0,1038 | 0,1005 |
| titulares, padronizado | 0,1031 | 0,1009 | 0,1010 | 0,1007 | 0,1023 | 0,0997 |
| reservas entre os chutes | 0% | 0,2% | 0,6% | 3,5% | 13% | 28,8% |

A inclinação padronizada dos titulares é **-0,00021 xG/chute por faixa de 15 min (ep 0,00023; z = -0,9)**, ou -0,0011 do início ao fim (-1%): não significativa. O gols/xG também fica plano (0,95 a 0,98 em todas as faixas).

**Resultado 2: o efeito depende do estado do placar (titulares, padronizado por liga, perfil e tipo).**

| estado | 0-15 | 15-30 | 30-45 | 45-60 | 60-75 | 75-90 | inclinação por faixa (z) |
|---|---|---|---|---|---|---|---|
| perdendo | 0,0939 | 0,0929 | 0,0925 | 0,0946 | 0,0942 | 0,0890 | -0,00056 (-1,4) |
| empatando | 0,1014 | 0,0978 | 0,0992 | 0,0972 | 0,0985 | 0,0955 | **-0,00086 (-3,1)** |
| ganhando | 0,1168 | 0,1162 | 0,1141 | 0,1147 | 0,1191 | 0,1199 | **+0,00114 (+2,2)** |

Empatando, o xG por chute cai cerca de 6% do começo ao fim; ganhando, sobe cerca de 3%; perdendo, cai nos últimos 15 minutos. Os dois lados se cancelam na média. Isso é compatível com fadiga ou desespero de quem precisa do gol e com espaço para contra-ataque de quem está ganhando, mas os dados não separam as duas leituras.

**Resultado 3: o padrão não é robusto entre ligas.** Inclinação padronizada de titulares por liga (z): Premier League -0,00116 (-2,3), Bundesliga -0,00046 (-0,8), La Liga +0,00001, Ligue 1 0,00000, Serie A +0,00075 (+1,5). Só a Premier League mostra queda (-0,0058 de 0-15 a 75-90); a Serie A sobe. "Ganhando sobe" aparece com z ≥ 2 em duas ligas (Ligue 1, Serie A). Jogo corrido (-0,00035, z = -1,2) e bola parada (+0,00010, z = +0,3) também não distinguem de zero.

**Resultado 4: o reserva chuta com qualidade maior que o titular, e o efeito não some com o tempo em campo.** Mesma célula (liga, faixa, estado, perfil, tipo), diferença reserva menos titular em xG por chute: 0 a 15 min desde a entrada **+0,0053 (ep 0,0014; z = +3,8; 12.098 chutes)**, 15 a 30 min +0,0054 (z = +3,0), mais de 30 min +0,0048 (z = +1,8). A partir dos 60 minutos, **+0,0052 (ep 0,0012)**, cerca de +5% sobre os ~0,102 do titular. A direção é a mesma nas cinco ligas (de +0,0024 a +0,0063 a partir dos 60 minutos).

**Leitura.**
- A hipótese de queda geral por fadiga **não se sustenta**: a média é plana, e as diferenças por estado não se repetem em todas as ligas.
- A "exceção do reserva" aparece, mas **não tem cara de pernas descansadas**: o efeito não decai com o tempo desde a entrada (+0,0053, +0,0054, +0,0048), como seria se fosse frescor que se esgota. É compatível com **composição**: reservas costumam ser atacantes, que chutam de mais perto, e a comparação não controla a posição.
- Para o simulador: não há base para um multiplicador de qualidade por janela. Se existir um, o candidato é o estado do placar (já tratado no Achado 52), não o relógio.

**O que não se pode concluir.** (1) A posição do jogador não foi controlada: sem isso, "reserva" mistura frescor e perfil de posição. (2) xG mede a qualidade da chance (lugar e tipo do chute), não a finalização; a fadiga na execução apareceria em gols/xG, que ficou plano mas é ruidoso. (3) Erro-padrão trata chutes como independentes (mesmo jogo e mesmo time se repetem), então é otimista. (4) Cinco ligas europeias, 2021 a 2025; o resultado não vale para outras competições. (5) `sem_escalacao` (4,8% dos chutes: jogador sem correspondência na escalação) tem o mesmo sinal que os reservas (+0,0067) e foi tratado à parte.

**Próximo teste (proposto):** repetir a comparação reserva contra titular DENTRO da mesma posição (atacante, meia, defensor) com `match_lineup_fotmob.position_id`, para separar frescor de perfil de posição.

## Achado 56 — o empate técnico com o Dixon-Coles (Achado 51) não se repete em outras ligas; o rótulo de quebra corrigido e o fator de gols móvel dão ganhos pequenos e semelhantes no 1X2

**Perguntas.** (1) O simulador com força amplificada (`exp_forca`) continua empatado com o Dixon-Coles fora da Premier League? (2) Corrigir o rótulo de quebra (`p_quebra`) muda a previsão? (3) Um fator de gols que se atualiza com os jogos anteriores (móvel) funciona onde o fator fixo do Achado 54 falhou?

**Método.** La Liga, Serie A, Bundesliga e Ligue 1 (temporada 2025/26 de teste, 2024/25 de aquecimento; dados de `scripts/exportar_csv_backtest_simulador.py`, que reproduz o CSV da Premier League) mais a Premier League dos achados anteriores. 1.000 simulações por jogo, mesmas sementes entre variantes (a diferença pareada é só o efeito da mudança), jogos com Dixon-Coles e odds de fechamento: 342 + 306 + 342 + 272 + 271 = **1.533**. Bootstrap de 4.000 reamostragens por jogo (`scripts/comparar_resultados_backtest.py`).
- *Quebra corrigida* (`exp_forca_quebra`): o filtro de quebra usava o rótulo antigo `mesma|recuperação|`, que não existe nos dados; toda linha que continua contava como quebra (`p_quebra` = 100% em todas as zonas) e a bola seguia do núcleo em 95% das vezes (teto). Corrigido, `p_quebra` fica entre 1,5% e 4% (falta, lateral, escanteio, recuperação do adversário) e quem continua usa a matriz 18 × 20. O padrão continua o antigo (os Achados 41 a 54 seguem reproduzíveis).
- *Fator de gols móvel* (`exp_forca_movel`): em cada jogo, razão gols reais / gols simulados sem fator dos **últimos 200 jogos anteriores** (data estritamente anterior), piso 0,85, teto 1,25, mínimo de 50 jogos; a base sem fator vem das rodadas de `exp_forca` de 2024 e 2025. Parâmetros fixados antes de olhar o teste.

**Resultado 1: o simulador é pior que o Dixon-Coles no 1X2 e empata no over/under.** `exp_forca` menos Dixon-Coles, log-loss do 1X2 (positivo = pior): Premier League +0,0134 [-0,0165; +0,0419]; La Liga **+0,0506** [+0,0168; +0,0850]; Serie A +0,0290 [-0,0074; +0,0647]; Bundesliga **+0,0717** [+0,0361; +0,1065]; Ligue 1 **+0,0336** [+0,0028; +0,0637]. Agregado das cinco: **+0,0382 [+0,0228; +0,0530]** (Brier +0,0266 [+0,0159; +0,0367]). No over/under: **-0,0044 [-0,0169; +0,0077]**, empate. O próprio Dixon-Coles também fica atrás do mercado (agregado, 1X2 +0,0235 [+0,0128; +0,0346]; over/under +0,0192 [+0,0097; +0,0287]); `exp_forca` contra o mercado: 1X2 +0,0617 [+0,0467; +0,0765], over/under +0,0148 [+0,0049; +0,0243].

**Resultado 2: rótulo de quebra corrigido, ganho pequeno no 1X2.** Contra `exp_forca`, log-loss do 1X2, por liga: Premier League +0,0015; La Liga -0,0056; Serie A -0,0019; Bundesliga -0,0069 [-0,0143; +0,0002]; Ligue 1 -0,0034; nenhuma isolada distingue de zero. Agregado: **-0,0030 [-0,0060; -0,0000]** (no limite da significância), Brier -0,0017 [-0,0037; +0,0003], over/under +0,0019 [-0,0005; +0,0045]. Os gols por jogo caem cerca de 1% a 1,5% (Premier League 2,664 para 2,630).

**Resultado 3: o fator de gols móvel melhora o 1X2, o over/under não muda.** Contra `exp_forca`, agregado: 1X2 **-0,0032 [-0,0058; -0,0004]**, Brier **-0,0022 [-0,0039; -0,0004]**, over/under 0,0000 [-0,0042; +0,0039]. Por liga (1X2): Premier League -0,0003; La Liga **-0,0077** [-0,0135; -0,0014]; Serie A 0,0000; Bundesliga -0,0028; Ligue 1 -0,0062 [-0,0125; +0,0001]. Contra o Dixon-Coles continua pior no 1X2: **+0,0351 [+0,0199; +0,0495]**; over/under -0,0044 [-0,0172; +0,0078].

**Viés de nível de gols (jogos com Dixon-Coles e odds; gols reais; sem fator; com fator móvel):**

| liga | reais | sem fator | com fator móvel | fator médio (faixa) |
|---|---|---|---|---|
| Premier League | 2,792 | 2,664 (-4,6%) | 2,758 (-1,2%) | 1,038 (0,994-1,090) |
| La Liga | 2,739 | 2,512 (-8,3%) | 2,629 (-4,0%) | 1,053 (1,008-1,116) |
| Serie A | 2,412 | 2,428 (+0,6%) | 2,386 (-1,1%) | 0,983 (0,909-1,067) |
| Bundesliga | 3,294 | 2,883 (-12,5%) | 3,115 (-5,4%) | 1,090 (1,042-1,142) |
| Ligue 1 | 2,827 | 2,723 (-3,6%) | 2,847 (+0,7%) | 1,051 (0,993-1,102) |

O nível de gols que o simulador erra é diferente em cada liga (a Bundesliga é subestimada em 12,5%; a Serie A, nada), o que explica por que um fator fixo não serve (Achado 54). O fator móvel acompanha cada liga, e ainda deixa viés de -4% a -5% na La Liga e na Bundesliga: a razão pura corrige só parte (um fator de 1,0997 deu +9% nos gols, elasticidade ~0,9) e a janela de 200 jogos atrasa.

**Leitura.**
- O "empate técnico" do Achado 51 era da Premier League: lá o intervalo incluía zero e a amostra era de 342 jogos. Com 1.533 jogos em cinco ligas, o simulador é pior que o Dixon-Coles no 1X2 (cerca de +0,04) e igual no over/under. Para decisões de aposta o simulador ainda não bate nenhuma das duas referências no 1X2.
- Cada ajuste testado aqui (quebra e nível de gols) vale cerca de -0,003 no 1X2 e não fecha uma diferença de +0,038 contra o Dixon-Coles. O que falta provavelmente está em outro lugar: a força dos times, que o Dixon-Coles estima direto dos gols do histórico e o simulador estima só pelo canal dos chutes (hipótese, não testada aqui).
- No over/under, o simulador acompanha o Dixon-Coles, mas ambos perdem para o mercado (e na Bundesliga e Ligue 1 o simulador perde significativamente).

**O que não se pode concluir.** (1) Cinco ligas, uma temporada de teste cada; os jogos da mesma liga e do mesmo time se repetem, então os IC são otimistas. (2) Só os jogos com previsão do Dixon-Coles entram (271 a 342 de 306 a 380): é o conjunto em que se pode comparar, não a temporada inteira. (3) A janela de 200 jogos, o piso e o teto do fator móvel não foram otimizados; o expoente 1,0 deixa parte do viés. (4) Quebra e fator móvel foram testados separados, não combinados.

**Próximos testes (propostos):** combinar quebra corrigida e fator móvel; força dos times por gols (como o Dixon-Coles) no lugar de só chutes, que é onde está a diferença; outras ligas e temporadas anteriores.

## Achado 57 — força dos times por gols no lugar de só chutes melhora o 1X2 só um pouco (mistura chutes + gols: -0,0059) e fecha menos de um sexto da diferença para o Dixon-Coles

**Pergunta.** O simulador estima a força dos times só pelo canal dos chutes; o Dixon-Coles a estima pelos gols. Usar gols fecha a diferença de +0,0382 no 1X2 (Achado 56)?

**Método.** Mesmas cinco ligas, 1.533 jogos com Dixon-Coles e odds, 1.000 simulações, mesmas sementes de `exp_forca` (diferença pareada), bootstrap por jogo. Duas variantes novas:
- `exp_gols`: ataque e defesa de cada time pelos gols feitos e sofridos, divididos pela média da liga e encolhidos para 1 com 20 pseudo-jogos (`k_gols`, fixado antes de olhar o teste; os chutes usam 8), só jogos anteriores; entra pelo volume de chutes com o expoente 2 do Achado 51.
- `exp_misto`: força = chutes^0,5 × gols^0,5 (`peso_gols` = 0,5, fixado antes).
Parâmetros ajustáveis por simulação: `--k-gols`, `--peso-gols` e as entradas `k_gols`/`peso_gols` do workflow. Sem opção de ajuste pelo adversário nem de decaimento no tempo.

**Resultado (diferença contra `exp_forca`; negativo = melhor; * = IC 95% exclui zero).**

| | log-loss 1X2 | Brier 1X2 | log-loss over/under |
|---|---|---|---|
| `exp_gols`, 5 ligas | -0,0032 [-0,0092; +0,0032] | -0,0022 [-0,0065; +0,0022] | +0,0038 [-0,0008; +0,0086] |
| `exp_misto`, 5 ligas | **-0,0059 [-0,0099; -0,0018]*** | **-0,0039 [-0,0067; -0,0011]*** | +0,0013 [-0,0015; +0,0041] |

`exp_misto` por liga (1X2): Premier League +0,0011; La Liga -0,0074; Serie A **-0,0137** [-0,0224; -0,0052]; Bundesliga -0,0071; Ligue 1 -0,0019. `exp_gols` por liga: +0,0081; -0,0020; -0,0129; -0,0084; -0,0012 (nenhum isolado distingue de zero).

**Contra o Dixon-Coles (agregado, positivo = pior):**

| | 1X2 | Brier 1X2 | over/under |
|---|---|---|---|
| `exp_forca` (só chutes) | +0,0382 [+0,0228; +0,0530] | +0,0266 | -0,0044 [-0,0169; +0,0077] |
| `exp_gols` | +0,0351 [+0,0216; +0,0483] | +0,0243 | -0,0006 [-0,0119; +0,0105] |
| `exp_misto` | **+0,0324 [+0,0180; +0,0460]** | +0,0227 | -0,0031 [-0,0149; +0,0084] |

O gols por jogo simulado quase não muda (Premier League 2,664 contra 2,667 e 2,673; Bundesliga 2,883, 2,792 e 2,841).

**Leitura.**
- Misturar gols à força melhora o 1X2 em 0,006, e esse ganho é significativo; gols sozinhos (com 20 pseudo-jogos) não se distinguem de zero. O over/under não muda.
- A hipótese de que a diferença para o Dixon-Coles vinha de a força sair dos chutes **não se sustenta como causa principal**: usando gols, a diferença cai de +0,0382 para +0,0324 (15%) e continua significativa em La Liga, Bundesliga e Ligue 1.
- Falta o que o Dixon-Coles faz além de ter gols: ajuste da força pelo adversário (os ratios aqui ignoram contra quem o time jogou), ataque e defesa estimados juntos por máxima verossimilhança, correção de baixos placares e decaimento no tempo. Esses itens não foram testados aqui. Também pode haver perda na tradução da força para o placar simulado (cadeia de ações sorteadas).

**O que não se pode concluir.** (1) `k_gols` e `peso_gols` fixos em 20 e 0,5; não varridos, para não escolher parâmetro com o teste. (2) Cinco ligas, uma temporada de teste cada; IC otimistas por jogos repetidos do mesmo time. (3) Só o 1X2 melhora; o over/under (onde o simulador já empatava com o Dixon-Coles) não se mexe.

**Próximos testes (propostos):** ajuste da força pelo adversário (iterar ataque/defesa até estabilizar, como um Elo de gols); decaimento no tempo; comparar a força simulada com a esperada de um Poisson direto, para separar erro de força de perda na simulação.
