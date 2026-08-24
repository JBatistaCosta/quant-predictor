import { describe, it, expect } from 'vitest';
import { devigarOddsRatio, fracaoKelly, stakeKelly25 } from './devig';

describe('devigarOddsRatio', () => {
  it('devolve probabilidades que somam 1', () => {
    const p = devigarOddsRatio({ home: 2.1, draw: 3.4, away: 3.6 });
    const soma = Object.values(p).reduce((s, v) => s + v, 0);
    expect(soma).toBeCloseTo(1, 8);
  });

  it('preserva a ordenação de favorito/zebra da odd bruta', () => {
    const p = devigarOddsRatio({ home: 1.5, draw: 4.2, away: 6.0 });
    expect(p.home).toBeGreaterThan(p.draw);
    expect(p.draw).toBeGreaterThan(p.away);
  });

  it('em odds sem margem (probabilidade implícita já soma 1), não altera nada', () => {
    // odds "justas": 1/2 + 1/2 = 1 -> sem vig, devig é identidade.
    const p = devigarOddsRatio({ over: 2.0, under: 2.0 });
    expect(p.over).toBeCloseTo(0.5, 8);
    expect(p.under).toBeCloseTo(0.5, 8);
  });

  it('funciona com 2 ou 3 seleções (bisecção genérica, não fórmula fechada por N)', () => {
    const doisLados = devigarOddsRatio({ over: 1.85, under: 1.95 });
    const somaDois = doisLados.over + doisLados.under;
    expect(somaDois).toBeCloseTo(1, 8);

    const tresLados = devigarOddsRatio({ home: 1.9, draw: 3.5, away: 4.0 });
    const somaTres = tresLados.home + tresLados.draw + tresLados.away;
    expect(somaTres).toBeCloseTo(1, 8);
  });
});

describe('fracaoKelly', () => {
  it('é zero quando a odd não dá vantagem nenhuma (p implícita = p do modelo)', () => {
    // odd 2.0 -> p implícita 0.5; com p do modelo também 0.5, sem edge.
    expect(fracaoKelly(0.5, 2.0)).toBeCloseTo(0, 10);
  });

  it('é positiva quando o modelo dá mais probabilidade que a odd embute (EV+)', () => {
    expect(fracaoKelly(0.6, 2.0)).toBeGreaterThan(0);
  });

  it('nunca fica negativa (EV- vira 0, não aposta "ao contrário")', () => {
    expect(fracaoKelly(0.3, 2.0)).toBe(0);
  });

  it('bate com a fórmula clássica f* = (p·b - (1-p))/b', () => {
    const p = 0.55, odd = 2.5, b = odd - 1;
    const esperado = (p * b - (1 - p)) / b;
    expect(fracaoKelly(p, odd)).toBeCloseTo(esperado, 10);
  });

  it('odd <= 1 (degenerada) retorna 0 em vez de dividir por zero/negativo', () => {
    expect(fracaoKelly(0.9, 1.0)).toBe(0);
    expect(fracaoKelly(0.9, 0.8)).toBe(0);
  });
});

describe('stakeKelly25', () => {
  it('é 1/4 da fração de Kelly cheia quando isso fica abaixo do teto de 25%', () => {
    const p = 0.55, odd = 2.2;
    expect(stakeKelly25(p, odd)).toBeCloseTo(fracaoKelly(p, odd) * 0.25, 10);
  });

  it('nunca ultrapassa 25% da banca, mesmo com um edge enorme', () => {
    // odd baixa + p do modelo bem alta -> Kelly cheio dispara acima de 1.
    expect(stakeKelly25(0.95, 1.2)).toBeLessThanOrEqual(0.25);
  });

  it('é 0 quando não há edge', () => {
    expect(stakeKelly25(0.4, 2.5)).toBeCloseTo(0, 10);
  });
});
