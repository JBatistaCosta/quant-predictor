// src/utils/format.js
// Funções puras de formatação e cor — não dependem de nenhum estado do
// componente, então podem ser reaproveitadas por qualquer página.

// Converte texto de input em número seguro para cálculo. Texto vazio/inválido
// vira 0 SÓ na hora de calcular — nunca no campo em si, para não "empurrar"
// um 0 de volta na caixa enquanto o usuário digita.
export const toNumber = (v) => {
  const n = parseFloat(v);
  return Number.isFinite(n) ? n : 0;
};

export const toOdd = (prob) => (prob > 0.0001 ? (1 / prob).toFixed(2) : '> 1000');
export const toPct = (prob) => (prob > 0.00001 ? (prob * 100).toFixed(1) + '%' : '< 0.1%');

export const getEloColor = (rating) => {
  if (rating >= 1950) return 'text-emerald-400';
  if (rating >= 1800) return 'text-blue-400';
  if (rating >= 1650) return 'text-yellow-400';
  return 'text-orange-400';
};

// Gradiente azul -> amarelo -> verde, usado nos mapas de calor (placares, xT).
export const heatColor = (p, min, max) => {
  const t = max > min ? (p - min) / (max - min) : 0;
  const blue = { r: 37, g: 99, b: 235 };
  const yellow = { r: 250, g: 204, b: 21 };
  const green = { r: 22, g: 163, b: 74 };

  let c1, c2, localT;
  if (t < 0.5) { c1 = blue; c2 = yellow; localT = t / 0.5; }
  else { c1 = yellow; c2 = green; localT = (t - 0.5) / 0.5; }

  const r = Math.round(c1.r + (c2.r - c1.r) * localT);
  const g = Math.round(c1.g + (c2.g - c1.g) * localT);
  const b = Math.round(c1.b + (c2.b - c1.b) * localT);

  const luminance = 0.299 * r + 0.587 * g + 0.114 * b;
  const textColor = luminance > 150 ? '#0f172a' : '#ffffff';

  return { background: `rgb(${r},${g},${b})`, color: textColor };
};
