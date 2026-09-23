// src/utils/markovEngine.js
// Motor de simulação por Cadeia de Markov MULTI-EVENTO (Fase 2 do plano de
// extensão do app, com escanteio/falta adicionados na Fase 3) — generaliza o
// antigo `runMarkovSimulation` de `AnaliseEvento.jsx` (que só simulava gol)
// para simular, no MESMO laço minuto a minuto: gol (via cadeia chute →
// chute no alvo → gol, não mais um sorteio único), cartão amarelo, cartão
// vermelho, escanteio, falta.
//
// Mesmo contrato de `lambdaFormulas.js`: função pura, contexto explícito por
// parâmetro, sem hooks/closures sobre estado React, sem I/O (multiplicadores
// e taxas já vêm carregados de fora — ver `markovParams.js` pra
// `league_markov_params`, e `api/corners-model.js` pros totais de
// chute/chute-no-alvo/cartão/escanteio/falta). `AnaliseEvento.jsx` chama esta
// função de dentro de `src/workers/markovEngine.worker.js`.
//
// DECISÃO ARQUITETURAL: o gol deixa de ter taxa própria e passa a ser o
// produto final da cadeia chute → chute-no-alvo → gol — os três estágios
// lêem do MESMO estado de placar/minuto/vantagem numérica, então os três
// mercados relacionados (chutes, chutes no alvo, gols) ficam estatisticamente
// consistentes entre si (hoje, com sorteios independentes, é possível em
// tese simular mais "gols" do que "chutes no alvo").

export const MARKOV_MINUTES = 90;

// Mesmos 6 bins de 15 minutos usados na Fase 1 (`league_markov_params`,
// `tipo='mult_minuto_bin'`) e no seed de escanteio/falta (Achado 13) — os
// rótulos aqui têm que bater exatamente com as chaves gravadas no banco,
// senão o lookup falha silenciosamente e cai no fallback neutro (=1) sem
// avisar (mesma armadilha já documentada em `STAT_LEAGUE_PARAMS_LABEL`,
// `api/corners-model.js`).
export const MINUTE_BINS = [
  { label: '0-14', lo: 0, hi: 14 },
  { label: '15-29', lo: 15, hi: 29 },
  { label: '30-44', lo: 30, hi: 44 },
  { label: '45-59', lo: 45, hi: 59 },
  { label: '60-74', lo: 60, hi: 74 },
  { label: '75-89', lo: 75, hi: 89 },
];

function clamp01(x) {
  if (!Number.isFinite(x)) return 0;
  if (x < 0) return 0;
  if (x > 1) return 1;
  return x;
}

// Bucket de minuto — ramificação direta em vez de escanear MINUTE_BINS a
// cada chamada (roda dentro do laço mais quente do motor, uma vez por
// time por minuto por simulação).
function bucketMinuto(minute) {
  if (minute < 15) return '0-14';
  if (minute < 30) return '15-29';
  if (minute < 45) return '30-44';
  if (minute < 60) return '45-59';
  if (minute < 75) return '60-74';
  return '75-89';
}

function bucketEstado(diffPlacar) {
  if (diffPlacar > 0) return 'ganhando';
  if (diffPlacar < 0) return 'perdendo';
  return 'empatando';
}

// Lookup genérico nos parâmetros calibrados (ver `markovParams.js`):
// `params[evento][tipo][chave]`, fallback neutro (1 = sem efeito) quando a
// combinação não foi calibrada. Isso é o que permite o motor tratar TODOS os
// eventos (gol/chute/chute_no_alvo/cartão) do mesmo jeito genérico, sem `if`
// por evento — a Fase 1 só calibrou mult_vantagem_numerica/mult_janela_pos_
// evento pra cartão, por exemplo, mas o motor não precisa saber disso: o
// lookup pra gol/chute nesses dois tipos simplesmente devolve 1.
function getMultiplicador(params, evento, tipo, chave) {
  const valor = params?.[evento]?.[tipo]?.[chave];
  return Number.isFinite(valor) ? valor : 1;
}

// "0-5"/"5-15" de minutos desde o último evento (gol ou expulsão) do time —
// mesmos buckets calibrados em `mult_janela_pos_evento` (Fase 1). >=15min
// (ou sem evento nenhum) é "regime", que não tem multiplicador próprio (é o
// próprio baseline =1, por construção: o script de calibração nunca grava
// linha de regime pra esse tipo).
function bucketJanela(minutosDesdeEvento) {
  if (minutosDesdeEvento < 5) return '0-5';
  if (minutosDesdeEvento < 15) return '5-15';
  return null;
}

function multJanelaPosEvento(params, evento, ultimoEvento, minutoAtual) {
  if (!ultimoEvento) return 1;
  const bucket = bucketJanela(minutoAtual - ultimoEvento.minuto);
  if (!bucket) return 1;
  return getMultiplicador(params, evento, 'mult_janela_pos_evento', `${ultimoEvento.tipo}_${bucket}`);
}

// Vantagem numérica: PROXY documentado na Fase 1 (não há coluna de "jogadores
// em campo" contínua no schema) — usa a chave fixa calibrada em cima da
// janela "regime" pós-expulsão (`numerico_-1_regime`/`numerico_+1_regime`),
// mas aplica de forma persistente enquanto o placar de cartões vermelhos
// estiver desequilibrado (não só nos 15min seguintes ao cartão) — é o que o
// estado `vermelhos` do motor, que nunca "expira", naturalmente representa.
function multVantagemNumerica(params, evento, vermelhosProprio, vermelhosAdversario) {
  if (vermelhosProprio === vermelhosAdversario) return 1;
  const chave = vermelhosProprio > vermelhosAdversario ? 'numerico_-1_regime' : 'numerico_+1_regime';
  return getMultiplicador(params, evento, 'mult_vantagem_numerica', chave);
}

function multiplicadorCombinado(params, evento, ctx) {
  const mEstado = getMultiplicador(params, evento, 'mult_estado_placar', ctx.estado);
  const mMinuto = getMultiplicador(params, evento, 'mult_minuto_bin', ctx.minutoBucket);
  const mJanela = multJanelaPosEvento(params, evento, ctx.ultimoEvento, ctx.minutoAtual);
  const mVantagem = multVantagemNumerica(params, evento, ctx.vermelhosProprio, ctx.vermelhosAdversario);
  return mEstado * mMinuto * mJanela * mVantagem;
}

// Monta os parâmetros da cadeia chute → chute-no-alvo → gol a partir dos
// totais de PARTIDA (não por minuto) vindos de `/api/corners-model`.
// `pAcc`/`pConvBase` são "back-solved" pra que o produto da cadeia feche
// exatamente em `lambdaGol` (o λ já validado do Dixon-Coles/fórmula
// escolhida) em expectativa — se não houver dado de chute/chute-no-alvo,
// degrada graciosamente pro comportamento de hoje (sorteio único de gol,
// sem regressão).
function montarCadeiaChute(lambdaGol, chutesTotal, chutesNoAlvoTotal, minutes) {
  const temChute = Number.isFinite(chutesTotal) && chutesTotal > 0;
  if (!temChute) {
    return { taxaChutePorMinuto: lambdaGol / minutes, pAcc: 1, pConvBase: 1 };
  }
  const temNoAlvo = Number.isFinite(chutesNoAlvoTotal) && chutesNoAlvoTotal > 0;
  const pAcc = temNoAlvo ? clamp01(chutesNoAlvoTotal / chutesTotal) : 1;
  const baseConversao = temNoAlvo ? chutesNoAlvoTotal : chutesTotal;
  const pConvBase = clamp01(lambdaGol / baseConversao);
  return { taxaChutePorMinuto: chutesTotal / minutes, pAcc, pConvBase };
}

function criarEstadoTime() {
  return { gols: 0, chutes: 0, chutesNoAlvo: 0, amarelos: 0, vermelhos: 0, ultimoEvento: null };
}

// Decide o ultimoEvento do time ao fim do minuto, entre os eventos que
// aconteceram NESTE minuto (se mais de um, prioridade: vermelho próprio >
// vermelho do adversário > gol próprio > gol sofrido — cartão vermelho é o
// evento mais raro/impactante, então "vence" um gol no mesmo minuto; escolha
// arbitrária pra um empate raro, documentada aqui em vez de deixada
// implícita).
function proximoUltimoEvento(atual, minuto, { vermelhoProprio, vermelhoContra, golProprio, golContra }) {
  if (vermelhoProprio) return { tipo: 'expulsao_pro', minuto };
  if (vermelhoContra) return { tipo: 'expulsao_contra', minuto };
  if (golProprio) return { tipo: 'marcou', minuto };
  if (golContra) return { tipo: 'sofreu', minuto };
  return atual;
}

/**
 * Roda a simulação multi-evento por Cadeia de Markov, minuto a minuto.
 *
 * @param {object} entrada
 * @param {{lambda1:number, lambda2:number}} entrada.gols - λ de gol por time (já ajustado por Elo/posse/fórmula escolhida — mesmo `results.lambda1/lambda2` que a calculadora já usa hoje).
 * @param {{chutes1?:number, chutes2?:number, chutesNoAlvo1?:number, chutesNoAlvo2?:number}} [entrada.chutes] - Totais de PARTIDA esperados (não por minuto), de `/api/corners-model?stat=shots`/`shots_on_target`. Ausente = cadeia degrada pra sorteio único de gol (comportamento de hoje).
 * @param {{taxa1?:number, taxa2?:number}} [entrada.cartaoAmarelo] - Totais de PARTIDA esperados por time, de `/api/corners-model?stat=cartao_amarelo`.
 * @param {{taxa1?:number, taxa2?:number}} [entrada.cartaoVermelho] - Idem, `?stat=cartao_vermelho`.
 * @param {{taxa1?:number, taxa2?:number}} [entrada.escanteio] - Idem, `?stat=corners` (`stat_esperado.mandante/visitante` -- já é por time, apesar do `disp_r` da linha O/U ser calibrado sobre o TOTAL). `mult_estado_placar`/`mult_vantagem_numerica`/`mult_janela_pos_evento` não têm calibração própria pra este evento (Fase 1) -- só `mult_minuto_bin` (forma StatsBomb, Achado 13, fallback global); os demais tipos caem no neutro (=1) automaticamente via `getMultiplicador`.
 * @param {{taxa1?:number, taxa2?:number}} [entrada.falta] - Idem, `?stat=fouls`. Mesma ressalva de `escanteio` acima.
 * @param {boolean} entrada.dynamics - Equivalente a `markovDynamics` de hoje — liga/desliga TODOS os multiplicadores (estado/minuto/janela/vantagem numérica) de uma vez.
 * @param {number} entrada.simCount - Número de simulações Monte Carlo.
 * @param {object} [entrada.params] - Saída de `carregarMarkovParams` (`markovParams.js`): `{ [evento]: { [tipo]: { [chave]: valor } } }`.
 * @param {number} [entrada.minutes] - Default `MARKOV_MINUTES` (90).
 * @param {() => number} [entrada.rng] - Injeção de RNG (testes determinísticos); default `Math.random`.
 * @returns {object} `{ probWin1, probDraw, probWin2, topScores, minuteDistribution, heatGrid, heatMin, heatMax, cartoes, chutes, escanteios, faltas }`
 */
export function runMarkovSimulation(entrada) {
  const {
    gols, chutes = {}, cartaoAmarelo = {}, cartaoVermelho = {}, escanteio = {}, falta = {},
    dynamics = false, simCount, params = {}, minutes = MARKOV_MINUTES, rng = Math.random,
  } = entrada;

  const { lambda1, lambda2 } = gols;
  const cadeia1 = montarCadeiaChute(lambda1, chutes.chutes1, chutes.chutesNoAlvo1, minutes);
  const cadeia2 = montarCadeiaChute(lambda2, chutes.chutes2, chutes.chutesNoAlvo2, minutes);
  const taxaAmarelo = [Number(cartaoAmarelo.taxa1) / minutes || 0, Number(cartaoAmarelo.taxa2) / minutes || 0];
  const taxaVermelho = [Number(cartaoVermelho.taxa1) / minutes || 0, Number(cartaoVermelho.taxa2) / minutes || 0];
  const taxaEscanteio = [Number(escanteio.taxa1) / minutes || 0, Number(escanteio.taxa2) / minutes || 0];
  const taxaFalta = [Number(falta.taxa1) / minutes || 0, Number(falta.taxa2) / minutes || 0];
  const cadeiaChute = [cadeia1, cadeia2];

  let wins1 = 0, wins2 = 0, draws = 0;
  const scoreCounts = {};
  const goalMinuteBins = new Array(MINUTE_BINS.length).fill(0);
  const cardMinuteBins = [
    [new Array(MINUTE_BINS.length).fill(0), new Array(MINUTE_BINS.length).fill(0)], // amarelo: [time1, time2]
    [new Array(MINUTE_BINS.length).fill(0), new Array(MINUTE_BINS.length).fill(0)], // vermelho: [time1, time2]
  ];
  const escanteioMinuteBins = [new Array(MINUTE_BINS.length).fill(0), new Array(MINUTE_BINS.length).fill(0)];
  const faltaMinuteBins = [new Array(MINUTE_BINS.length).fill(0), new Array(MINUTE_BINS.length).fill(0)];
  const somaChutes = [0, 0], somaChutesNoAlvo = [0, 0], somaAmarelos = [0, 0], somaVermelhos = [0, 0];
  const somaEscanteios = [0, 0], somaFaltas = [0, 0];

  for (let sim = 0; sim < simCount; sim++) {
    const times = [criarEstadoTime(), criarEstadoTime()];

    for (let minute = 0; minute < minutes; minute++) {
      const minutoBucket = bucketMinuto(minute);
      const diffPlacar = times[0].gols - times[1].gols;
      const estados = [bucketEstado(diffPlacar), bucketEstado(-diffPlacar)];

      // Sorteios do minuto, por time (0=mandante, 1=visitante) — todas as
      // probabilidades usam o SNAPSHOT do início do minuto (times[].gols/
      // vermelhos/ultimoEvento), nunca o que acabou de ser decidido neste
      // mesmo minuto (nem pro próprio time, nem pro adversário).
      const golNesteMinuto = [false, false];
      const vermelhoNesteMinuto = [false, false];

      for (let t = 0; t < 2; t++) {
        const o = 1 - t;
        const ctx = {
          estado: estados[t], minutoBucket, minutoAtual: minute,
          ultimoEvento: times[t].ultimoEvento,
          vermelhosProprio: times[t].vermelhos, vermelhosAdversario: times[o].vermelhos,
        };

        // 1) Cartão amarelo, 2) cartão vermelho — sorteios independentes,
        // mesma fórmula taxa-base × multiplicadores combinados.
        const multAmarelo = dynamics ? multiplicadorCombinado(params, 'cartao_amarelo', ctx) : 1;
        if (rng() < clamp01(taxaAmarelo[t] * multAmarelo)) {
          times[t].amarelos++;
          somaAmarelos[t]++;
          cardMinuteBins[0][t][MINUTE_BINS.findIndex(b => b.label === minutoBucket)]++;
        }
        const multVermelho = dynamics ? multiplicadorCombinado(params, 'cartao_vermelho', ctx) : 1;
        if (rng() < clamp01(taxaVermelho[t] * multVermelho)) {
          vermelhoNesteMinuto[t] = true;
          times[t].vermelhos++;
          somaVermelhos[t]++;
          cardMinuteBins[1][t][MINUTE_BINS.findIndex(b => b.label === minutoBucket)]++;
        }

        // Escanteio, falta — mesmo padrão de sorteio independente dos
        // cartões, mas sem afetar estado/ultimoEvento do time (não são
        // eventos que mudam o jogo pros outros mercados, só contados). Só
        // `mult_minuto_bin` tem calibração própria pra estes dois eventos
        // (Fase 1/Achado 13) — os demais tipos caem no neutro via
        // `getMultiplicador`, então `multiplicadorCombinado` funciona sem
        // ajuste nenhum aqui.
        const multEscanteio = dynamics ? multiplicadorCombinado(params, 'escanteio', ctx) : 1;
        if (rng() < clamp01(taxaEscanteio[t] * multEscanteio)) {
          somaEscanteios[t]++;
          escanteioMinuteBins[t][MINUTE_BINS.findIndex(b => b.label === minutoBucket)]++;
        }
        const multFalta = dynamics ? multiplicadorCombinado(params, 'falta', ctx) : 1;
        if (rng() < clamp01(taxaFalta[t] * multFalta)) {
          somaFaltas[t]++;
          faltaMinuteBins[t][MINUTE_BINS.findIndex(b => b.label === minutoBucket)]++;
        }

        // 3) Cadeia chute → chute no alvo → gol.
        const multChute = dynamics ? multiplicadorCombinado(params, 'chute', ctx) : 1;
        const pChute = clamp01(cadeiaChute[t].taxaChutePorMinuto * multChute);
        if (rng() < pChute) {
          times[t].chutes++;
          somaChutes[t]++;
          if (rng() < cadeiaChute[t].pAcc) {
            times[t].chutesNoAlvo++;
            somaChutesNoAlvo[t]++;
            const multGol = dynamics ? multiplicadorCombinado(params, 'gol', ctx) : 1;
            // Multiplicador residual: o produto da cadeia (chute × conversão)
            // precisa reproduzir o multiplicador de GOL calibrado direto
            // (não o de chute, já aplicado no 1º sorteio) -- sem isso o
            // desvio por estado/minuto seria aplicado em dobro (ver
            // docstring do módulo e plano da sessão).
            const multResidual = multChute > 0 ? multGol / multChute : multGol;
            const pConv = clamp01(cadeiaChute[t].pConvBase * multResidual);
            if (rng() < pConv) {
              golNesteMinuto[t] = true;
              times[t].gols++;
              goalMinuteBins[MINUTE_BINS.findIndex(b => b.label === minutoBucket)]++;
            }
          }
        }
      }

      // Atualizações de estado só depois de TODOS os sorteios do minuto --
      // nenhum time reage a um evento que "ainda não tinha acontecido"
      // quando sua própria probabilidade foi calculada neste minuto.
      for (let t = 0; t < 2; t++) {
        const o = 1 - t;
        times[t].ultimoEvento = proximoUltimoEvento(times[t].ultimoEvento, minute, {
          vermelhoProprio: vermelhoNesteMinuto[t], vermelhoContra: vermelhoNesteMinuto[o],
          golProprio: golNesteMinuto[t], golContra: golNesteMinuto[o],
        });
      }
    }

    const g1 = times[0].gols, g2 = times[1].gols;
    if (g1 > g2) wins1++; else if (g1 < g2) wins2++; else draws++;
    const key = `${g1}-${g2}`;
    scoreCounts[key] = (scoreCounts[key] || 0) + 1;
  }

  const topScores = Object.entries(scoreCounts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 6)
    .map(([score, count]) => ({ score, prob: count / simCount }));

  const totalSimGoals = goalMinuteBins.reduce((a, b) => a + b, 0);
  const minuteDistribution = goalMinuteBins.map(c => (totalSimGoals > 0 ? c / totalSimGoals : 0));

  const heatGrid = [];
  let heatMin = Infinity, heatMax = -Infinity;
  for (let i = 0; i < 7; i++) {
    const row = [];
    for (let j = 0; j < 7; j++) {
      const count = scoreCounts[`${i}-${j}`] || 0;
      const p = count / simCount;
      row.push(p);
      if (p < heatMin) heatMin = p;
      if (p > heatMax) heatMax = p;
    }
    heatGrid.push(row);
  }

  const normalizarBins = (bins) => {
    const total = bins.reduce((a, b) => a + b, 0);
    return bins.map(c => (total > 0 ? c / total : 0));
  };

  return {
    probWin1: wins1 / simCount,
    probDraw: draws / simCount,
    probWin2: wins2 / simCount,
    topScores,
    minuteDistribution,
    heatGrid,
    heatMin,
    heatMax,
    chutes: {
      chutes1: somaChutes[0] / simCount, chutes2: somaChutes[1] / simCount,
      chutesNoAlvo1: somaChutesNoAlvo[0] / simCount, chutesNoAlvo2: somaChutesNoAlvo[1] / simCount,
    },
    cartoes: {
      amarelo1: somaAmarelos[0] / simCount, amarelo2: somaAmarelos[1] / simCount,
      vermelho1: somaVermelhos[0] / simCount, vermelho2: somaVermelhos[1] / simCount,
      distribuicaoAmareloMinuto1: normalizarBins(cardMinuteBins[0][0]),
      distribuicaoAmareloMinuto2: normalizarBins(cardMinuteBins[0][1]),
      distribuicaoVermelhoMinuto1: normalizarBins(cardMinuteBins[1][0]),
      distribuicaoVermelhoMinuto2: normalizarBins(cardMinuteBins[1][1]),
    },
    escanteios: {
      escanteio1: somaEscanteios[0] / simCount, escanteio2: somaEscanteios[1] / simCount,
      distribuicaoMinuto1: normalizarBins(escanteioMinuteBins[0]),
      distribuicaoMinuto2: normalizarBins(escanteioMinuteBins[1]),
    },
    faltas: {
      falta1: somaFaltas[0] / simCount, falta2: somaFaltas[1] / simCount,
      distribuicaoMinuto1: normalizarBins(faltaMinuteBins[0]),
      distribuicaoMinuto2: normalizarBins(faltaMinuteBins[1]),
    },
  };
}
