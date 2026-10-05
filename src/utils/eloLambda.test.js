import { describe, it, expect } from 'vitest';
import { ELO_K, ELO_FATOR_MAXIMO, FORMA_EXPOENTE, fatoresElo, encolherForca } from './eloLambda';
import { GAMMA_MANDANTE, GAMMA_VISITANTE, LIGA_MEDIA_GERAL } from './poisson';

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

describe('encolherForca', () => {
  it('um confronto médio (razão 1) não muda', () => {
    const base = LIGA_MEDIA_GERAL * GAMMA_MANDANTE;
    expect(encolherForca(base, GAMMA_MANDANTE)).toBeCloseTo(base, 12);
    const baseFora = LIGA_MEDIA_GERAL * GAMMA_VISITANTE;
    expect(encolherForca(baseFora, GAMMA_VISITANTE)).toBeCloseTo(baseFora, 12);
  });

  it('encolhe a razão em direção à média (acima e abaixo), mantendo a ordem', () => {
    const base = LIGA_MEDIA_GERAL * GAMMA_MANDANTE;
    const alto = encolherForca(base * 3, GAMMA_MANDANTE);
    const baixo = encolherForca(base / 3, GAMMA_MANDANTE);
    expect(alto).toBeGreaterThan(base);
    expect(alto).toBeLessThan(base * 3);
    expect(baixo).toBeLessThan(base);
    expect(baixo).toBeGreaterThan(base / 3);
    expect(encolherForca(base * 2, GAMMA_MANDANTE)).toBeLessThan(encolherForca(base * 3, GAMMA_MANDANTE));
  });

  it('a razão encolhida é a razão original elevada ao expoente', () => {
    const base = LIGA_MEDIA_GERAL * GAMMA_MANDANTE;
    expect(encolherForca(base * 2.5, GAMMA_MANDANTE) / base).toBeCloseTo(Math.pow(2.5, FORMA_EXPOENTE), 12);
  });

  it('expoente 1 não muda nada; valor inválido ou zero passa direto', () => {
    expect(encolherForca(2.2, GAMMA_MANDANTE, 1)).toBeCloseTo(2.2, 12);
    expect(encolherForca(0, GAMMA_MANDANTE)).toBe(0);
    expect(Number.isNaN(encolherForca(NaN, GAMMA_MANDANTE))).toBe(true);
  });

  it('as constantes calibradas são razoáveis', () => {
    expect(ELO_K).toBeGreaterThan(0.3);
    expect(ELO_K).toBeLessThan(0.7);
    expect(FORMA_EXPOENTE).toBeGreaterThan(0.1);
    expect(FORMA_EXPOENTE).toBeLessThan(0.6);
  });
});

// Paridade com scripts/calibrar_calculadora_completa.py (mesmo pipeline em Python: forma multiplicativa
// encolhida x mando x fator de Elo, posse neutra): valores gerados lá com k = ELO_K e expoente = FORMA_EXPOENTE.
import { getLambdaFormula } from './lambdaFormulas';

describe('paridade com a calibração em Python', () => {
  const casos = [
    [614, 2.44, 1.12, 1.64, 0.71, 3.377603, 0.492543],
    [200, 1.9, 1.0, 1.2, 1.4, 2.102421, 0.791921],
    [-150, 1.1, 1.5, 1.7, 0.9, 0.993786, 1.576963],
    [0, 1.35, 1.2, 1.35, 1.2, 1.394698, 1.134966],
    [850, 3.2, 0.6, 0.9, 2.4, 6.25129, 0.284947],
  ];
  it.each(casos)('Elo %d, xG %d/%d x %d/%d', (d, xg1, xga1, xg2, xga2, l1Esperado, l2Esperado) => {
    const formula = getLambdaFormula('multiplicativo');
    const { trueXG1, trueXG2 } = formula.calc({ m: { xg1, xga1, xg2, xga2 } });
    const { fator1, fator2 } = fatoresElo(d, 50);
    const l1 = Math.max(0.1, encolherForca(trueXG1, GAMMA_MANDANTE) * fator1);
    const l2 = Math.max(0.1, encolherForca(trueXG2, GAMMA_VISITANTE) * fator2);
    expect(formula.calibraForma).toBe(true);
    expect(l1).toBeCloseTo(l1Esperado, 4);
    expect(l2).toBeCloseTo(l2Esperado, 4);
  });
});
