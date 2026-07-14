# Backtest: Odds do Modelo vs. Mercado Real — Relatório

**Data**: 14/07/2026
**Modelo testado**: dixon_coles_v1 (estático, 1X2 e Over/Under 2.5)
**Fonte de odds real**: football-data.co.uk (Bet365, Pinnacle, média de mercado)
**Escopo**: 5 ligas europeias, temporada de teste 2025, ~14.400 apostas simuladas

## Metodologia

1. Odds de mercado convertidas em probabilidade implícita (1/odd).
2. Margem da casa removida por devigging proporcional (cada probabilidade
   implícita dividida pela soma das do mesmo mercado, normalizando pra 100%).
3. Edge = diferença percentual entre a probabilidade do modelo e a
   probabilidade de mercado (já sem margem).
4. Backtest: para cada faixa de edge, comparado contra o resultado REAL
   das partidas (não simulado) — taxa de acerto observada e ROI se tivesse
   apostado 1 unidade em cada seleção daquela faixa, na odd real.

## Resultado principal

**ROI negativo em todas as faixas de edge, sem relação clara entre
tamanho do edge e retorno.** Apostas com edge de 49% (discordância extrema
do modelo contra o mercado) teriam ROI de -3,6%; a faixa de maior
discordância no sentido oposto (-38%) teve ROI de -15,8%. Não há um
padrão de "quanto maior o edge, melhor o retorno" — o que seria esperado
se o modelo estivesse capturando ineficiência real do mercado.

## Achado mais revelador: calibração do mercado

Em toda faixa de edge testada, a **probabilidade implícita do mercado
bateu de perto com a taxa de acerto real observada** (ex: mercado previa
27,9% de chance, aconteceu 27,8% das vezes; mercado previa 43,3%,
aconteceu 44,0%). Isso vale inclusive nas faixas onde o modelo mais
discordava do mercado — ou seja, **quando modelo e mercado discordam
fortemente, é o mercado que costuma estar certo, não o modelo.**

## Interpretação

Isso não invalida o projeto — valida que o pipeline funciona de ponta a
ponta (coleta → modelo → comparação), e confirma uma expectativa realista:
bater um baseline ingênuo em log loss (o que o modelo faz, ver
`modelo_predicao_documentacao.md`) é um padrão bem mais baixo que bater um
mercado de apostas profissional, que incorpora informação que o modelo
não tem acesso — notícias de lesão, escalação, informação de bastidores,
e modelos proprietários mais sofisticados.

## O que isso sugere para os próximos passos

As lacunas de informação mais prováveis de fechar essa diferença são
exatamente as que já estão documentadas como fase futura em
`ideias_futuras.md`:
- Dados de jogador (lesões, ausências, escalação provável)
- Importância da partida (contexto de tabela — já vimos sinal disso na
  investigação de fadiga de fim de temporada)
- Estatísticas mais granulares (xG por chute, não só agregado por partida)

Sem essas informações, o modelo atual serve bem como **ferramenta de
análise e visão estruturada de probabilidades** — mas não há evidência
de que sirva, hoje, como fonte de vantagem sistemática contra casas de
apostas reais.
