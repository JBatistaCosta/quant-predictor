// api/_lib/negbin.js
// Mesma matemática de src/utils/distributions.js (Binomial Negativa por
// recorrência), duplicada aqui porque as funções serverless da Vercel não
// compartilham bundle com o front-end. Se alterar uma, altere a outra.

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

// Devolve o vetor de massa de probabilidade (não a CDF) de x=0 até xMax, pela
// mesma recorrência de negBinomialCDF -- usado por decomporMandanteVisitante
// (corners-model.js) pra montar a distribuição conjunta total×split sem
// recalcular a CDF ponto a ponto (custaria O(xMax²) em vez de O(xMax)).
export const negBinomialPMFArray = (media, disp, xMax) => {
  const n = Math.max(0, Math.floor(xMax));
  if (media <= 0) return [1, ...Array(n).fill(0)];

  const r = disp;
  const probParar = r / (r + media);
  const pmf = new Array(n + 1);
  pmf[0] = Math.pow(probParar, r);
  for (let i = 0; i < n; i++) {
    pmf[i + 1] = pmf[i] * ((i + r) / (i + 1)) * (1 - probParar);
  }
  return pmf;
};

// Beta-Binomial(n, p, rho): pmf de k=0..n, parametrizada por média p e
// correlação intraclasse rho (0<rho<1) em vez de alpha/beta -- rho=0 é o
// limite degenerado pra Binomial(n,p) comum (sem dispersão extra).
//
// Usada em corners-model.js pra modelar COMO um total de escanteios já
// sorteado (NB) se divide entre mandante e visitante: rho vem calibrado por
// liga em league_model_params (stat='corners', param_name='disp_rho_split'),
// achado real desta sessão via resíduo de Pearson quadrático (mesmo espírito
// do alpha/r da binomial negativa, só que na variável k|n em vez de x) --
// ver CONTEXTO_PROJETO.md pro valor e a amostra usados.
//
// Recorrência (sem gamma/log-gamma, mesmo estilo de negBinomialCDF):
//   pmf(0) = prod_{i=0}^{n-1} (beta+i)/(alpha+beta+i)
//   pmf(k) = pmf(k-1) * (n-k+1)/k * (k-1+alpha)/(n-k+beta)
export const betaBinomialPMFArray = (n, p, rho) => {
  const nInt = Math.max(0, Math.floor(n));
  const pClamp = Math.min(Math.max(p, 1e-6), 1 - 1e-6);
  if (nInt === 0) return [1];
  if (!(rho > 0)) rho = 1e-6; // rho<=0 -> praticamente Binomial(n,p) comum

  const alpha = (pClamp * (1 - rho)) / rho;
  const beta = ((1 - pClamp) * (1 - rho)) / rho;

  let pmf0 = 1;
  for (let i = 0; i < nInt; i++) pmf0 *= (beta + i) / (alpha + beta + i);

  const pmf = new Array(nInt + 1);
  pmf[0] = pmf0;
  for (let k = 1; k <= nInt; k++) {
    pmf[k] = pmf[k - 1] * ((nInt - k + 1) / k) * ((k - 1 + alpha) / (nInt - k + beta));
  }
  return pmf;
};
