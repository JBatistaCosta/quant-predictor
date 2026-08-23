import { describe, it, expect, vi, afterEach } from 'vitest';
import {
  factorial,
  poisson,
  poissonCDF,
  dixonColesTau,
  DIXON_COLES_RHO,
  LIGA_MEDIA_MANDANTE,
  LIGA_MEDIA_VISITANTE,
  LIGA_MEDIA_GERAL,
  GAMMA_MANDANTE,
  GAMMA_VISITANTE,
  getPoissonRandom,
} from './poisson';

describe('factorial', () => {
  it('trata 0 e 1 como caso base', () => {
    expect(factorial(0)).toBe(1);
    expect(factorial(1)).toBe(1);
  });

  it('calcula fatoriais conhecidos', () => {
    expect(factorial(4)).toBe(24);
    expect(factorial(5)).toBe(120);
  });
});

describe('poisson', () => {
  it('bate com valores calculados à mão', () => {
    expect(poisson(2, 0)).toBeCloseTo(Math.exp(-2), 10);
    expect(poisson(2, 3)).toBeCloseTo((Math.exp(-2) * 8) / 6, 10);
  });

  it('soma ~1 sobre um intervalo grande de k (é uma distribuição de probabilidade)', () => {
    const lambda = 3.2;
    let soma = 0;
    for (let k = 0; k <= 50; k++) soma += poisson(lambda, k);
    expect(soma).toBeCloseTo(1, 8);
  });
});

describe('poissonCDF', () => {
  it('em k=0 é igual ao pmf de k=0', () => {
    expect(poissonCDF(2, 0)).toBeCloseTo(poisson(2, 0), 10);
  });

  it('é monotonicamente não decrescente', () => {
    const lambda = 2.5;
    let anterior = 0;
    for (let k = 0; k <= 20; k++) {
      const atual = poissonCDF(lambda, k);
      expect(atual).toBeGreaterThanOrEqual(anterior);
      anterior = atual;
    }
  });

  it('se aproxima de 1 pra k grande', () => {
    expect(poissonCDF(3, 30)).toBeCloseTo(1, 8);
  });
});

describe('dixonColesTau', () => {
  const lam = 1.5;
  const mu = 1.2;

  it('aplica a correção nos 4 placares baixos', () => {
    expect(dixonColesTau(0, 0, lam, mu, DIXON_COLES_RHO)).toBeCloseTo(1 - lam * mu * DIXON_COLES_RHO, 10);
    expect(dixonColesTau(0, 1, lam, mu, DIXON_COLES_RHO)).toBeCloseTo(1 + lam * DIXON_COLES_RHO, 10);
    expect(dixonColesTau(1, 0, lam, mu, DIXON_COLES_RHO)).toBeCloseTo(1 + mu * DIXON_COLES_RHO, 10);
    expect(dixonColesTau(1, 1, lam, mu, DIXON_COLES_RHO)).toBeCloseTo(1 - DIXON_COLES_RHO, 10);
  });

  it('não corrige nenhum outro placar', () => {
    expect(dixonColesTau(2, 2, lam, mu, DIXON_COLES_RHO)).toBe(1.0);
    expect(dixonColesTau(0, 2, lam, mu, DIXON_COLES_RHO)).toBe(1.0);
    expect(dixonColesTau(3, 0, lam, mu, DIXON_COLES_RHO)).toBe(1.0);
  });
});

describe('constantes de mando de campo', () => {
  it('média geral é a média simples de mandante/visitante', () => {
    expect(LIGA_MEDIA_GERAL).toBeCloseTo((LIGA_MEDIA_MANDANTE + LIGA_MEDIA_VISITANTE) / 2, 10);
  });

  it('gamma mandante > 1 e gamma visitante < 1 (vantagem de jogar em casa)', () => {
    expect(GAMMA_MANDANTE).toBeGreaterThan(1);
    expect(GAMMA_VISITANTE).toBeLessThan(1);
  });

  it('gammas são proporcionais às médias de mando', () => {
    expect(GAMMA_MANDANTE).toBeCloseTo(LIGA_MEDIA_MANDANTE / LIGA_MEDIA_GERAL, 10);
    expect(GAMMA_VISITANTE).toBeCloseTo(LIGA_MEDIA_VISITANTE / LIGA_MEDIA_GERAL, 10);
  });
});

describe('getPoissonRandom', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('segue o algoritmo de Knuth com sequência de aleatórios controlada', () => {
    // L = exp(-1) ≈ 0.36788: 0.9 e depois 0.5*0.9=0.45 ainda ficam acima de L,
    // 0.45*0.5=0.225 já fica abaixo -> 3 iterações -> retorna k-1 = 2.
    vi.spyOn(Math, 'random').mockReturnValueOnce(0.9).mockReturnValueOnce(0.5).mockReturnValueOnce(0.5);
    expect(getPoissonRandom(1)).toBe(2);
  });

  it('lambda=0 sempre retorna 0, independente do sorteio', () => {
    vi.spyOn(Math, 'random').mockReturnValue(0.99);
    expect(getPoissonRandom(0)).toBe(0);
  });

  it('retorna inteiros não negativos e com média aproximada de lambda em muitas amostras', () => {
    const lambda = 4;
    const n = 4000;
    let soma = 0;
    for (let i = 0; i < n; i++) {
      const k = getPoissonRandom(lambda);
      expect(Number.isInteger(k)).toBe(true);
      expect(k).toBeGreaterThanOrEqual(0);
      soma += k;
    }
    // tolerância generosa (estatística, não determinística) - só pra pegar
    // um bug grosseiro no algoritmo, não validar precisão da amostragem.
    expect(soma / n).toBeGreaterThan(lambda * 0.85);
    expect(soma / n).toBeLessThan(lambda * 1.15);
  });
});
