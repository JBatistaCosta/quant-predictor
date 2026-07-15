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
