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
| Matriz de transição zona-a-zona (Achado 15) | **Não** | Precisa de evento de passe/condução com local — FotMob nunca capturou isso |
| PPDA / deep completions (estilo Understat) | **Não** | Mesma razão — pressão/posicionamento exige stream de evento que o FotMob não tem |

Descritivo, sem IC 95%.

---

## Lição de método (vale além deste projeto)

**Invariantes internas provam que a derivação está certa. Não provam que a interpretação está.**

Aconteceu três vezes nesta frente:

- O **Achado 3** passou em todas as invariantes (espelhamento perfeito, minutos fechando) e mesmo assim a conclusão agregada estava confundida com força de equipe.
- O **Achado 5** era um bug de ordenação que reconciliava perfeitamente em todos os totais, porque totais não têm ordem.
- O **Achado 13** (escanteio/falta via StatsBomb) reproduziu **o mesmo bug do Achado 5** — 2º tempo com minuto reiniciando em vez de continuar — numa fonte de dado completamente diferente, meses depois de já saber exatamente que padrão procurar. Só não passou pro arquivo final porque o usuário perguntou "isso não pode ser artefato do intervalo?" antes de eu dar o achado por fechado.
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
