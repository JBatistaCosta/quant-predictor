import { describe, it, expect } from 'vitest';
import { ELO_K, ELO_FATOR_MAXIMO, fatoresElo } from './eloLambda';

describe('fatoresElo', () => {
  it('times iguais: fator 1 para os dois lados', () => {
    const f = fatoresElo(0);
    expect(f.fator1).toBe(1);
    expect(f.fator2).toBe(1);
  });

  it('o favorito sobe e o outro cai na mesma proporção (produto 1)', () => {
    const f = fatoresElo(200);
    expect(f.fator1).toBeGreaterThan(1);
    expect(f.fator2).toBeLessThan(1);
    expect(f.fator1 * f.fator2).toBeCloseTo(1, 12);
  });

  it('a razão entre os lados é exp(k·x) no peso calibrado', () => {
    const f = fatoresElo(200, 50);
    const x = (200 / 400) * Math.LN10;
    expect(f.fator1 / f.fator2).toBeCloseTo(Math.exp(ELO_K * x), 10);
  });

  it('peso 0% desliga o Elo e 100% dobra o efeito (em log)', () => {
    expect(fatoresElo(300, 0)).toEqual({ fator1: 1, fator2: 1 });
    const f50 = fatoresElo(100, 50);
    const f100 = fatoresElo(100, 100);
    expect(Math.log(f100.fator1)).toBeCloseTo(2 * Math.log(f50.fator1), 10);
  });

  it('é simétrico: trocar os times troca os fatores', () => {
    const a = fatoresElo(150);
    const b = fatoresElo(-150);
    expect(a.fator1).toBeCloseTo(b.fator2, 12);
    expect(a.fator2).toBeCloseTo(b.fator1, 12);
  });

  it('não satura como o E/0,5 antigo e respeita a trava', () => {
    const f = fatoresElo(400, 50);
    expect(f.fator1).toBeLessThan(2);          // o antigo (peso 100%) já ia a ~1,8; com dif. extrema, a 2
    const extremo = fatoresElo(2000, 100);
    expect(extremo.fator1).toBeCloseTo(ELO_FATOR_MAXIMO, 10);
    expect(extremo.fator2).toBeCloseTo(1 / ELO_FATOR_MAXIMO, 10);
  });

  it('entrada inválida (NaN) vira diferença 0', () => {
    expect(fatoresElo(NaN)).toEqual({ fator1: 1, fator2: 1 });
  });
});
