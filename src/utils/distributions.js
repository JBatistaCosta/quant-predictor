// src/utils/distributions.js
// PMF/CDF de Binomial e Binomial Negativa, calculadas por recorrência (sem
// fatorial direto, que estoura pra n grande) — mesmo espírito de poissonCDF
// em poisson.js, só que somando os termos da distribuição em vez de recalcular
// cada um do zero.

// P(X = k) para X ~ Binomial(n, p) — "k sucessos em n tentativas independentes".
// Usada pra chutes-no-gol (n = chutes totais, p = precisão) e gols (n = chutes
// no gol, p = conversão) no modo "cadeia" da calculadora.
export const binomialPMF = (n, p, k) => {
  if (k < 0 || k > n || !Number.isInteger(k)) return 0;
  if (p <= 0) return k === 0 ? 1 : 0;
  if (p >= 1) return k === n ? 1 : 0;

  let pmf = Math.pow(1 - p, n); // pmf(0)
  for (let i = 0; i < k; i++) {
    pmf *= ((n - i) / (i + 1)) * (p / (1 - p));
  }
  return pmf;
};

export const binomialCDF = (n, p, k) => {
  const kMax = Math.min(Math.floor(k), n);
  if (kMax < 0) return 0;
  if (p <= 0) return 1;
  if (p >= 1) return kMax >= n ? 1 : 0;

  let pmf = Math.pow(1 - p, n);
  let sum = pmf;
  for (let i = 0; i < kMax; i++) {
    pmf *= ((n - i) / (i + 1)) * (p / (1 - p));
    sum += pmf;
  }
  return sum;
};

// P(X = x) para X ~ BinomialNegativa(media, disp) — parametrizada por MÉDIA
// (em vez do "p" clássico) pra encaixar direto no lugar de um lambda de
// Poisson. "disp" é o parâmetro de forma r: quanto maior, mais a distribuição
// se aproxima da Poisson(media); quanto menor, mais dispersa (cauda mais gorda)
// — útil pra escanteios, que variam mais entre jogos do que a Poisson prevê.
export const negBinomialPMF = (media, disp, x) => {
  if (x < 0 || !Number.isInteger(x)) return 0;
  if (media <= 0) return x === 0 ? 1 : 0;

  const r = disp;
  const probParar = r / (r + media); // "p" da parametrização clássica NB(r, p)

  let pmf = Math.pow(probParar, r); // pmf(0)
  for (let i = 0; i < x; i++) {
    pmf *= ((i + r) / (i + 1)) * (1 - probParar);
  }
  return pmf;
};

export const negBinomialCDF = (media, disp, x) => {
  const xMax = Math.floor(x);
  if (xMax < 0) return 0;
  if (media <= 0) return 1;

  const r = disp;
  const probParar = r / (r + media);

  let pmf = Math.pow(probParar, r);
  let sum = pmf;
  for (let i = 0; i < xMax; i++) {
    pmf *= ((i + r) / (i + 1)) * (1 - probParar);
    sum += pmf;
  }
  return sum;
};
