// src/utils/devig.js — devig de odds (Odds Ratio) e fração de Kelly.
// Mesma matemática de api/backtest-betting.js, reimplementada aqui de
// propósito (mesma disciplina já documentada lá: cada superfície duplica em
// vez de importar através da fronteira api/ <-> frontend) — usada pela
// verificação de EV/Kelly em AnaliseAvancadaEvento.jsx, que roda no browser
// direto contra o Supabase (sem function nova, api/ já está no teto de 12).

// Bisecção genérica pra achar o parâmetro c do devig Odds Ratio (Cheung) que
// zera g(c) = Σ qi/(c+qi-c·qi) - 1, onde qi = 1/odd_i (prob. implícita bruta).
function resolverParametroDevig(g) {
  const g0 = g(0);
  if (Math.abs(g0) < 1e-14) return 0;
  let tHi = 0, gHi = g0;
  while (gHi > 0) {
    tHi = tHi === 0 ? 1 : tHi * 2;
    gHi = g(tHi);
    if (tHi > 1e15) break; // odds degeneradas (<=1) não deveriam chegar aqui
  }
  let tLo = 0, gLo = g0;
  for (let i = 0; i < 200; i++) {
    const tMid = (tLo + tHi) / 2;
    const gMid = g(tMid);
    if (Math.abs(gMid) < 1e-12) return tMid;
    if ((gMid > 0) === (gLo > 0)) { tLo = tMid; gLo = gMid; } else { tHi = tMid; }
  }
  return (tLo + tHi) / 2;
}

// Remove a margem da casa (Odds Ratio, Cheung) de um grupo de odds do MESMO
// mercado (ex.: { home, draw, away } ou { over, under }) — devolve
// probabilidades que somam 1.
export function devigarOddsRatio(oddsPorSelecao) {
  const selecoes = Object.keys(oddsPorSelecao);
  const q = selecoes.map((s) => 1 / oddsPorSelecao[s]);
  const t = resolverParametroDevig((x) => {
    const c = 1 + x;
    return q.reduce((acc, qi) => acc + qi / (c + qi - c * qi), 0) - 1;
  });
  const c = 1 + t;
  const normalizadas = {};
  selecoes.forEach((s, i) => { normalizadas[s] = q[i] / (c + q[i] - c * q[i]); });
  return normalizadas;
}

// Fração de Kelly clássica pra aposta binária: f* = (p·b - (1-p)) / b, b = odd-1.
export function fracaoKelly(p, odd) {
  const b = odd - 1;
  if (b <= 0) return 0;
  const f = (p * b - (1 - p)) / b;
  return Math.max(0, f);
}

// Stake sugerida em Kelly fracionário (25% do critério cheio, "quarter
// Kelly" — mesmo padrão default de api/backtest-betting.js e
// SimulacaoCarteira.jsx), com teto de segurança de 25% da banca por aposta
// independente do resultado da fórmula.
export function stakeKelly25(p, odd) {
  return Math.min(fracaoKelly(p, odd) * 0.25, 0.25);
}
