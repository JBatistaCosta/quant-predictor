// src/utils/distribuicoesMercados.js
//
// Porte JS da CAMADA 2 do modelo misto: parâmetros de distribuição ->
// probabilidades de mercado. É o gêmeo de `scripts/distribuicoes.py`.
//
// A camada 1 (ML estimando λ/ρ/dispersão) vive só no Python -- treinar
// gradient boosting no browser não faria sentido. O que chega aqui são os
// parâmetros já estimados, lidos de `model_match_estimates.params`. Daí a
// divisão: Python treina e persiste; o JS lê e deriva.
//
// ATENÇÃO -- ESTE ARQUIVO PRECISA CONCORDAR COM `scripts/distribuicoes.py`.
// O projeto já tem um precedente de matemática duplicada entre front e
// serverless (`src/utils/distributions.js` <-> `api/_lib/negbin.js`, com
// aviso de "se alterar uma, altere a outra" que depende de alguém lembrar).
// Aqui a duplicação é entre linguagens diferentes, então nem o copiar-colar
// protege. Por isso existe o teste de paridade:
//
//     python scripts/gerar_golden_fixture.py    # regenera o fixture
//     node scripts/verificar_paridade_js.mjs    # confere JS contra ele
//
// Se você mudar a matemática de um lado, o teste de paridade falha até o
// outro lado acompanhar. Isso é intencional.

// Importes COM extensão `.js`, diferente do resto de `src/` (que omite, já
// que o Vite resolve). É deliberado: o teste de paridade roda este módulo em
// Node puro, e o ESM do Node exige a extensão. O Vite resolve os dois
// formatos, então não muda nada no build do app.
import { poisson, dixonColesTau } from './poisson.js';
import { negBinomialPMF } from './distributions.js';

export const MAX_GOLS = 10;
export const MAX_ESCANTEIOS = 40;

// Formata a linha de um mercado com UMA casa decimal, sempre — gêmeo de
// `rotulo_linha` em scripts/distribuicoes.py.
//
// Não é firula: essa string É a chave de `model_predictions.market`. Sem
// isso, `${-2}` vira "handicap_-2" no JS enquanto o Python grava
// "handicap_-2.0", e o front procura um mercado que existe no banco com
// outro nome. As linhas de meio gol coincidiam por acaso nas duas
// linguagens, então só as linhas INTEIRAS de handicap divergiam — o tipo de
// bug que passa despercebido até alguém reparar que o handicap -1 nunca
// aparece. Foi o teste de paridade que pegou.
export const rotuloLinha = (linha) => linha.toFixed(1);

// ---------------------------------------------------------------------------
// Gols: Poisson bivariada com a correção de Dixon-Coles
// ---------------------------------------------------------------------------

// Matriz (MAX_GOLS+1)² com P(placar = i x j), renormalizada.
// `lam`/`mu` são os gols esperados de mandante/visitante — é por aqui que o
// λ estimado por ML entra. `rho = 0` devolve a Poisson dupla independente.
export const matrizPlacares = (lam, mu, rho = 0, maxGols = MAX_GOLS) => {
  const matriz = [];
  let soma = 0;

  for (let i = 0; i <= maxGols; i++) {
    matriz[i] = [];
    for (let j = 0; j <= maxGols; j++) {
      let p = poisson(lam, i) * poisson(mu, j);
      // τ só mexe nas 4 células de placar baixo.
      if (i <= 1 && j <= 1) p *= dixonColesTau(i, j, lam, mu, rho);
      // Um ρ fora da faixa de validade pode gerar célula negativa —
      // probabilidade negativa não existe.
      p = Math.max(p, 0);
      matriz[i][j] = p;
      soma += p;
    }
  }

  if (soma <= 0) return matriz;
  for (let i = 0; i <= maxGols; i++) {
    for (let j = 0; j <= maxGols; j++) matriz[i][j] /= soma;
  }
  return matriz;
};

// Deriva os mercados de gols de uma matriz de placares. Todas as identidades
// (1X2 soma 1, over+under = 1, faixas somam 1) valem por construção, já que
// tudo é integral da MESMA matriz.
export const mercadosDeGols = (matriz, {
  linhasOverUnder = [0.5, 1.5, 2.5, 3.5, 4.5],
  linhasHandicap = [-2.5, -2, -1.5, -1, -0.5, 0, 0.5, 1, 1.5, 2, 2.5],
  maxPlacaresExatos = 20,
} = {}) => {
  const n = matriz.length - 1;
  const saida = {};

  let pHome = 0, pDraw = 0, pAway = 0, pBtts = 0;
  const porTotal = new Array(2 * n + 1).fill(0);
  const porMargem = new Array(2 * n + 1).fill(0); // índice = (i-j) + n
  const celulas = [];

  for (let i = 0; i <= n; i++) {
    for (let j = 0; j <= n; j++) {
      const p = matriz[i][j];
      if (i > j) pHome += p; else if (i === j) pDraw += p; else pAway += p;
      if (i > 0 && j > 0) pBtts += p;
      porTotal[i + j] += p;
      porMargem[i - j + n] += p;
      celulas.push({ placar: `${i}-${j}`, prob: p });
    }
  }

  saida['1X2'] = { home: pHome, draw: pDraw, away: pAway };
  saida.dupla_chance = { '1X': pHome + pDraw, 12: pHome + pAway, X2: pDraw + pAway };
  saida.btts = { yes: pBtts, no: 1 - pBtts };

  for (const linha of linhasOverUnder) {
    let over = 0;
    for (let t = 0; t < porTotal.length; t++) if (t > linha) over += porTotal[t];
    saida[`over_under_${rotuloLinha(linha)}`] = { over, under: 1 - over };
  }

  const faixas = [['0-1', 0, 1], ['2-3', 2, 3], ['4-6', 4, 6], ['7+', 7, Infinity]];
  saida.faixa_gols = {};
  for (const [rotulo, minimo, maximo] of faixas) {
    let acc = 0;
    for (let t = 0; t < porTotal.length; t++) if (t >= minimo && t <= maximo) acc += porTotal[t];
    saida.faixa_gols[rotulo] = acc;
  }

  // Handicap aplicado ao MANDANTE: `linha = -1` é "mandante começa 1 gol
  // atrás". Linha inteira pode empatar (devolução), daí a saída `push`
  // explícita — é ela que permite precificar a devolução em vez de diluí-la.
  for (const linha of linhasHandicap) {
    let ganha = 0, push = 0;
    for (let k = 0; k < porMargem.length; k++) {
      const margem = k - n + linha;
      if (margem > 0) ganha += porMargem[k];
      else if (margem === 0) push += porMargem[k];
    }
    const resultado = { home: ganha, away: 1 - ganha - push };
    if (push > 0) resultado.push = push;
    saida[`handicap_${rotuloLinha(linha)}`] = resultado;
  }

  // Só os N placares mais prováveis: a cauda tem centenas de células
  // irrisórias que só poluiriam a tela.
  celulas.sort((a, b) => b.prob - a.prob);
  saida.placar_exato = {};
  for (const { placar, prob } of celulas.slice(0, maxPlacaresExatos)) saida.placar_exato[placar] = prob;

  return saida;
};

// ---------------------------------------------------------------------------
// Escanteios: Binomial Negativa no total + split Beta-Binomial
// ---------------------------------------------------------------------------
// Escanteios de casa e fora são NEGATIVAMENTE correlacionados (-0,24 a -0,31,
// medido em 6 ligas) — posse é soma zero. Somar duas NB independentes
// superestimaria a variância do total. Modelar o total e repartir com uma
// Beta-Binomial gera essa correlação negativa naturalmente.

// PMF da NB truncada em MAX_ESCANTEIOS e RENORMALIZADA — gêmeo de
// `_nb_pmf_vetor` no Python.
//
// A renormalização não é detalhe: sem ela a massa residual acima do teto
// (~4e-6 numa cauda gorda) é tratada de um jeito no mercado de total e de
// outro na conjunta (que já normaliza), e o total deixa de bater com a
// própria marginal. Num modelo que se vende por coerência entre mercados,
// seria o defeito exato que ele promete não ter.
const pmfEscanteiosNormalizada = (mediaTotal, dispR) => {
  const pmf = new Array(MAX_ESCANTEIOS + 1);
  let soma = 0;
  for (let t = 0; t <= MAX_ESCANTEIOS; t++) {
    pmf[t] = negBinomialPMF(mediaTotal, dispR, t);
    soma += pmf[t];
  }
  if (soma > 0) for (let t = 0; t <= MAX_ESCANTEIOS; t++) pmf[t] /= soma;
  return pmf;
};

// ln Γ(x) por Lanczos — o JS não tem gamma nativa, e a Beta-Binomial precisa
// dela. Precisão ~1e-15 relativa, de sobra pro uso aqui.
const COEF_LANCZOS = [
  676.5203681218851, -1259.1392167224028, 771.32342877765313,
  -176.61502916214059, 12.507343278686905, -0.13857109526572012,
  9.9843695780195716e-6, 1.5056327351493116e-7,
];

const lnGamma = (x) => {
  if (x < 0.5) return Math.log(Math.PI / Math.sin(Math.PI * x)) - lnGamma(1 - x);
  const z = x - 1;
  let a = 0.99999999999980993;
  for (let i = 0; i < COEF_LANCZOS.length; i++) a += COEF_LANCZOS[i] / (z + i + 1);
  const t = z + COEF_LANCZOS.length - 0.5;
  return 0.5 * Math.log(2 * Math.PI) + (z + 0.5) * Math.log(t) - t + Math.log(a);
};

const lnBeta = (a, b) => lnGamma(a) + lnGamma(b) - lnGamma(a + b);

// P(k sucessos em n) da Beta-Binomial — a Binomial cuja probabilidade de
// sucesso é ela própria sorteada de uma Beta(alpha, beta). Modela "a fatia do
// mandante varia de jogo pra jogo" em vez de ser fixa. Em log, pra não
// estourar em n grande.
export const betaBinomialPMF = (n, alpha, beta, k) => {
  if (k < 0 || k > n) return 0;
  const lnBinom = lnGamma(n + 1) - lnGamma(k + 1) - lnGamma(n - k + 1);
  return Math.exp(lnBinom + lnBeta(k + alpha, n - k + beta) - lnBeta(alpha, beta));
};

// Matriz P(escanteios_casa = i, escanteios_fora = j), montada como
// P(total) x P(casa | total).
export const distribuicaoConjuntaEscanteios = (mediaTotal, dispR, alpha, beta, maxTotal = MAX_ESCANTEIOS) => {
  const matriz = Array.from({ length: maxTotal + 1 }, () => new Array(maxTotal + 1).fill(0));
  let soma = 0;

  const pmfTotal = pmfEscanteiosNormalizada(mediaTotal, dispR);
  for (let total = 0; total <= maxTotal; total++) {
    const pTotal = pmfTotal[total];
    if (pTotal <= 0) continue;
    for (let casa = 0; casa <= total; casa++) {
      const p = pTotal * betaBinomialPMF(total, alpha, beta, casa);
      matriz[casa][total - casa] += p;
      soma += p;
    }
  }

  if (soma <= 0) return matriz;
  for (let i = 0; i <= maxTotal; i++) {
    for (let j = 0; j <= maxTotal; j++) matriz[i][j] /= soma;
  }
  return matriz;
};

export const mercadosDeEscanteios = (mediaTotal, dispR, alpha, beta, {
  linhasTotais = [7.5, 8.5, 9.5, 10.5, 11.5, 12.5],
  linhasPorTime = [3.5, 4.5, 5.5, 6.5],
} = {}) => {
  const saida = {};

  const pTotal = pmfEscanteiosNormalizada(mediaTotal, dispR);

  for (const linha of linhasTotais) {
    let over = 0;
    for (let t = Math.floor(linha) + 1; t <= MAX_ESCANTEIOS; t++) over += pTotal[t];
    saida[`corners_over_under_${rotuloLinha(linha)}`] = { over, under: 1 - over };
  }

  saida.faixa_corners = {};
  for (const [rotulo, minimo, maximo] of [['≤8', 0, 8], ['9-10', 9, 10], ['11-12', 11, 12], ['13+', 13, MAX_ESCANTEIOS]]) {
    let acc = 0;
    for (let t = minimo; t <= maximo; t++) acc += pTotal[t];
    saida.faixa_corners[rotulo] = acc;
  }

  const conjunta = distribuicaoConjuntaEscanteios(mediaTotal, dispR, alpha, beta);
  const pCasa = new Array(MAX_ESCANTEIOS + 1).fill(0);
  const pFora = new Array(MAX_ESCANTEIOS + 1).fill(0);
  let cHome = 0, cDraw = 0, cAway = 0;

  for (let i = 0; i <= MAX_ESCANTEIOS; i++) {
    for (let j = 0; j <= MAX_ESCANTEIOS; j++) {
      const p = conjunta[i][j];
      pCasa[i] += p;
      pFora[j] += p;
      if (i > j) cHome += p; else if (i === j) cDraw += p; else cAway += p;
    }
  }

  for (const linha of linhasPorTime) {
    const corte = Math.floor(linha);
    let overCasa = 0, overFora = 0;
    for (let k = corte + 1; k <= MAX_ESCANTEIOS; k++) { overCasa += pCasa[k]; overFora += pFora[k]; }
    saida[`corners_home_over_under_${rotuloLinha(linha)}`] = { over: overCasa, under: 1 - overCasa };
    saida[`corners_away_over_under_${rotuloLinha(linha)}`] = { over: overFora, under: 1 - overFora };
  }

  saida.corners_1X2 = { home: cHome, draw: cDraw, away: cAway };
  return saida;
};

// ---------------------------------------------------------------------------
// Leitura dos parâmetros persistidos
// ---------------------------------------------------------------------------

// Normaliza o jsonb de `model_match_estimates.params` (ver a migration
// 20260817120000). Devolve null quando não há λ de gols utilizável — quem
// chama decide o fallback.
export const lerParametrosPartida = (params) => {
  if (!params) return null;
  const lam = Number(params.lambda_home);
  const mu = Number(params.lambda_away);
  if (!Number.isFinite(lam) || !Number.isFinite(mu) || lam <= 0 || mu <= 0) return null;

  const corners = Number(params.corners_lambda_total);
  return {
    lambdaHome: lam,
    lambdaAway: mu,
    rho: Number.isFinite(Number(params.rho)) ? Number(params.rho) : 0,
    cornersLambdaTotal: Number.isFinite(corners) && corners > 0 ? corners : null,
    cornersDispR: Number(params.corners_disp_r) || null,
    cornersAlpha: Number(params.corners_alpha) || null,
    cornersBeta: Number(params.corners_beta) || null,
  };
};
