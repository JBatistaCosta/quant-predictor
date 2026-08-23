import { describe, it, expect } from 'vitest';
import { binomialPMF, binomialCDF, negBinomialPMF, negBinomialCDF } from './distributions';
import { poisson } from './poisson';

describe('binomialPMF', () => {
  it('bate com C(n,k) p^k (1-p)^(n-k) calculado à mão', () => {
    expect(binomialPMF(4, 0.5, 2)).toBeCloseTo(0.375, 10); // C(4,2)*0.5^4
    expect(binomialPMF(10, 0.3, 3)).toBeCloseTo(0.2668279, 6); // C(10,3)*0.3^3*0.7^7
  });

  it('rejeita k fora de [0,n] ou não inteiro', () => {
    expect(binomialPMF(5, 0.5, -1)).toBe(0);
    expect(binomialPMF(5, 0.5, 6)).toBe(0);
    expect(binomialPMF(5, 0.5, 2.5)).toBe(0);
  });

  it('casos degenerados de p', () => {
    expect(binomialPMF(5, 0, 0)).toBe(1);
    expect(binomialPMF(5, 0, 3)).toBe(0);
    expect(binomialPMF(5, 1, 5)).toBe(1);
    expect(binomialPMF(5, 1, 3)).toBe(0);
  });

  it('soma 1 sobre todo k (é uma distribuição de probabilidade)', () => {
    let soma = 0;
    for (let k = 0; k <= 6; k++) soma += binomialPMF(6, 0.35, k);
    expect(soma).toBeCloseTo(1, 10);
  });
});

describe('binomialCDF', () => {
  it('é a soma cumulativa do pmf', () => {
    expect(binomialCDF(4, 0.5, 2)).toBeCloseTo(0.6875, 10);
  });

  it('casos de borda de k', () => {
    expect(binomialCDF(5, 0.5, -1)).toBe(0);
    expect(binomialCDF(5, 0.5, 5)).toBeCloseTo(1, 10);
  });

  it('casos degenerados de p', () => {
    expect(binomialCDF(5, 0, 3)).toBe(1);
    expect(binomialCDF(5, 1, 3)).toBe(0);
    expect(binomialCDF(5, 1, 5)).toBe(1);
  });
});

describe('negBinomialPMF', () => {
  it('bate com a fórmula clássica NB(r,p) parametrizada por média', () => {
    // media=2, disp(r)=3 -> p = r/(r+media) = 0.6
    expect(negBinomialPMF(2, 3, 0)).toBeCloseTo(0.6 ** 3, 10);
    expect(negBinomialPMF(2, 3, 1)).toBeCloseTo(0.6 ** 3 * 3 * 0.4, 10);
  });

  it('rejeita x negativo ou não inteiro', () => {
    expect(negBinomialPMF(2, 3, -1)).toBe(0);
    expect(negBinomialPMF(2, 3, 1.5)).toBe(0);
  });

  it('media<=0 concentra toda massa em x=0', () => {
    expect(negBinomialPMF(0, 3, 0)).toBe(1);
    expect(negBinomialPMF(0, 3, 2)).toBe(0);
  });

  it('soma ~1 sobre um intervalo grande de x', () => {
    let soma = 0;
    for (let x = 0; x <= 200; x++) soma += negBinomialPMF(3, 5, x);
    expect(soma).toBeCloseTo(1, 6);
  });

  it('converge pra Poisson quando a dispersão (r) é muito grande', () => {
    const media = 3;
    for (const x of [0, 1, 2, 3, 5, 8]) {
      expect(negBinomialPMF(media, 1e6, x)).toBeCloseTo(poisson(media, x), 4);
    }
  });
});

describe('negBinomialCDF', () => {
  it('é a soma cumulativa do pmf', () => {
    expect(negBinomialCDF(2, 3, 1)).toBeCloseTo(0.6 ** 3 + 0.6 ** 3 * 3 * 0.4, 10);
  });

  it('casos de borda', () => {
    expect(negBinomialCDF(2, 3, -1)).toBe(0);
    expect(negBinomialCDF(0, 3, 5)).toBe(1);
  });
});
