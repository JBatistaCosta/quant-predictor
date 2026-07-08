// src/utils/poisson.js
// Funções matemáticas do modelo (Poisson, correção Dixon-Coles, mando de campo).
// Ver calibration/dixon_coles_calibration.json para os dados brutos por trás
// dos números calibrados abaixo (314 jogos reais, 6 torneios).

export const factorial = (n) => (n <= 1 ? 1 : n * factorial(n - 1));
export const poisson = (lambda, k) => (Math.exp(-lambda) * Math.pow(lambda, k)) / factorial(k);
export const poissonCDF = (lambda, k) => {
  let sum = 0;
  for (let i = 0; i <= k; i++) sum += poisson(lambda, i);
  return sum;
};
export const DIXON_COLES_RHO = -0.042;
export const dixonColesTau = (x, y, lam, mu, rho) => {
  if (x === 0 && y === 0) return 1 - (lam * mu * rho);
  if (x === 0 && y === 1) return 1 + (lam * rho);
  if (x === 1 && y === 0) return 1 + (mu * rho);
  if (x === 1 && y === 1) return 1 - rho;
  return 1.0;
};
export const LIGA_MEDIA_MANDANTE = 1.3854;
export const LIGA_MEDIA_VISITANTE = 1.1274;
export const LIGA_MEDIA_GERAL = (LIGA_MEDIA_MANDANTE + LIGA_MEDIA_VISITANTE) / 2; // 1.2564 — baseline neutro
export const GAMMA_MANDANTE = LIGA_MEDIA_MANDANTE / LIGA_MEDIA_GERAL;   // ≈1.1027 — vantagem de jogar em casa
export const GAMMA_VISITANTE = LIGA_MEDIA_VISITANTE / LIGA_MEDIA_GERAL; // ≈0.8973 — desvantagem de jogar fora
export const getPoissonRandom = (lambda) => {
  const L = Math.exp(-lambda);
  let k = 0;
  let p = 1;
  do {
    k++;
    p *= Math.random();
  } while (p > L);
  return k - 1;
};
