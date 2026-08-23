import { describe, it, expect } from 'vitest';
import { LAMBDA_FORMULAS, getLambdaFormula } from './lambdaFormulas';
import {
  LIGA_MEDIA_MANDANTE,
  LIGA_MEDIA_VISITANTE,
  LIGA_MEDIA_GERAL,
  GAMMA_MANDANTE,
  GAMMA_VISITANTE,
} from './poisson';

// Reimplementação independente da fórmula multiplicativa, só pra comparar
// contra a saída real do módulo (não importar a mesma função interna que
// estamos testando).
function multiplicativoEsperado(xgAtaque, xgaDefesa, gamma) {
  return (xgAtaque * xgaDefesa / LIGA_MEDIA_GERAL) * gamma;
}

// Reimplementação independente da média ponderada por decaimento exponencial
// usada na fórmula 'decay' (mediaPonderada não é exportada pelo módulo).
function pesoDecaimentoEsperado(historico, campo, xi) {
  if (!historico || historico.length === 0) return null;
  let somaPesos = 0;
  let somaValores = 0;
  historico.forEach((jogo, i) => {
    const valor = jogo?.[campo];
    if (valor === null || valor === undefined || !Number.isFinite(valor)) return;
    const peso = Math.exp(-xi * i);
    somaPesos += peso;
    somaValores += peso * valor;
  });
  return somaPesos > 0 ? somaValores / somaPesos : null;
}

const m = { xg1: 1.8, xga1: 1.4, xg2: 1.2, xga2: 1.0 };

describe('LAMBDA_FORMULAS / getLambdaFormula', () => {
  it('todos os ids são únicos', () => {
    const ids = LAMBDA_FORMULAS.map((f) => f.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it('getLambdaFormula acha pelo id', () => {
    expect(getLambdaFormula('media_simples').id).toBe('media_simples');
  });

  it('getLambdaFormula cai pra primeira fórmula quando o id não existe', () => {
    expect(getLambdaFormula('id-inexistente')).toBe(LAMBDA_FORMULAS[0]);
    expect(getLambdaFormula(undefined)).toBe(LAMBDA_FORMULAS[0]);
  });
});

describe('fórmula multiplicativo', () => {
  it('combina xG próprio x xGA do adversário com o gamma de mando certo', () => {
    const { trueXG1, trueXG2 } = getLambdaFormula('multiplicativo').calc({ m });
    expect(trueXG1).toBeCloseTo(multiplicativoEsperado(m.xg1, m.xga2, GAMMA_MANDANTE), 10);
    expect(trueXG2).toBeCloseTo(multiplicativoEsperado(m.xg2, m.xga1, GAMMA_VISITANTE), 10);
  });
});

describe('fórmula media_simples', () => {
  it('é a média entre xG próprio e xGA do adversário, sem mando', () => {
    const { trueXG1, trueXG2 } = getLambdaFormula('media_simples').calc({ m });
    expect(trueXG1).toBeCloseTo((m.xg1 + m.xga2) / 2, 10);
    expect(trueXG2).toBeCloseTo((m.xg2 + m.xga1) / 2, 10);
  });
});

describe('fórmula ataque_defesa', () => {
  it('normaliza pela média de gols de quem joga em casa/fora, não pela média geral', () => {
    const { trueXG1, trueXG2 } = getLambdaFormula('ataque_defesa').calc({ m });
    expect(trueXG1).toBeCloseTo(
      (m.xg1 / LIGA_MEDIA_MANDANTE) * (m.xga2 / LIGA_MEDIA_VISITANTE) * LIGA_MEDIA_MANDANTE,
      10
    );
    expect(trueXG2).toBeCloseTo(
      (m.xg2 / LIGA_MEDIA_VISITANTE) * (m.xga1 / LIGA_MEDIA_MANDANTE) * LIGA_MEDIA_VISITANTE,
      10
    );
  });
});

describe('fórmula shrinkage', () => {
  const calc = (params) => getLambdaFormula('shrinkage').calc({ m, params });

  it('com k=0 (sem prior) equivale à fórmula multiplicativa crua', () => {
    const semShrinkage = calc({ n: 5, k: 0 });
    const multiplicativo = getLambdaFormula('multiplicativo').calc({ m });
    expect(semShrinkage.trueXG1).toBeCloseTo(multiplicativo.trueXG1, 10);
    expect(semShrinkage.trueXG2).toBeCloseTo(multiplicativo.trueXG2, 10);
  });

  it('com n=0 (sem amostra) encolhe totalmente pra média da liga', () => {
    const totalmenteEncolhido = calc({ n: 0, k: 6 });
    const esperado = multiplicativoEsperado(LIGA_MEDIA_GERAL, LIGA_MEDIA_GERAL, GAMMA_MANDANTE);
    expect(totalmenteEncolhido.trueXG1).toBeCloseTo(esperado, 10);
  });

  it('usa os defaults (n=5, k=6) quando nenhum parâmetro é passado', () => {
    const { trueXG1 } = getLambdaFormula('shrinkage').calc({ m });
    const encolher = (valor) => (5 * valor + 6 * LIGA_MEDIA_GERAL) / 11;
    const esperado = multiplicativoEsperado(encolher(m.xg1), encolher(m.xga2), GAMMA_MANDANTE);
    expect(trueXG1).toBeCloseTo(esperado, 10);
  });
});

describe('fórmula decay', () => {
  const xi = 0.2;
  const historico1 = [
    { xg: 2.0, xga: 1.0 },
    { xg: 1.0, xga: 1.0 },
  ];
  const historico2 = [{ xg: 1.5, xga: 1.2 }];

  it('pondera o histórico jogo a jogo com decaimento exponencial', () => {
    const { trueXG1, trueXG2, aviso } = getLambdaFormula('decay').calc({
      m,
      historico1,
      historico2,
      params: { xi },
    });

    const xgDecay1 = pesoDecaimentoEsperado(historico1, 'xg', xi);
    const xgaDecay1 = pesoDecaimentoEsperado(historico1, 'xga', xi);
    const xgDecay2 = pesoDecaimentoEsperado(historico2, 'xg', xi);
    const xgaDecay2 = pesoDecaimentoEsperado(historico2, 'xga', xi);

    expect(trueXG1).toBeCloseTo(multiplicativoEsperado(xgDecay1, xgaDecay2, GAMMA_MANDANTE), 10);
    expect(trueXG2).toBeCloseTo(multiplicativoEsperado(xgDecay2, xgaDecay1, GAMMA_VISITANTE), 10);
    expect(aviso).toBeUndefined();
  });

  it('cai pra média informada e avisa quando não há histórico de um dos lados', () => {
    const { trueXG1, trueXG2, aviso } = getLambdaFormula('decay').calc({
      m,
      historico1: [],
      historico2,
      params: { xi },
    });

    const xgDecay2 = pesoDecaimentoEsperado(historico2, 'xg', xi);
    const xgaDecay2 = pesoDecaimentoEsperado(historico2, 'xga', xi);

    // sem histórico do mandante -> cai pro xg1/xga1 informados manualmente
    expect(trueXG1).toBeCloseTo(multiplicativoEsperado(m.xg1, xgaDecay2, GAMMA_MANDANTE), 10);
    expect(trueXG2).toBeCloseTo(multiplicativoEsperado(xgDecay2, m.xga1, GAMMA_VISITANTE), 10);
    expect(aviso).toMatch(/mandante/);
  });

  it('avisa dos dois lados quando nenhum histórico está disponível', () => {
    const { aviso } = getLambdaFormula('decay').calc({ m, historico1: null, historico2: null, params: { xi } });
    expect(aviso).toMatch(/mandante/);
    expect(aviso).toMatch(/visitante/);
  });

  it('usa xi=0.2 como default quando nenhum parâmetro é passado', () => {
    const comDefault = getLambdaFormula('decay').calc({ m, historico1, historico2, params: undefined });
    const comXiExplicito = getLambdaFormula('decay').calc({ m, historico1, historico2, params: { xi: 0.2 } });
    expect(comDefault.trueXG1).toBeCloseTo(comXiExplicito.trueXG1, 10);
  });
});

describe('fórmula dinamica', () => {
  it('cai pra multiplicativo com aviso quando os parâmetros estão incompletos', () => {
    const { trueXG1, trueXG2, aviso } = getLambdaFormula('dinamica').calc({ m, params: {} });
    const multiplicativo = getLambdaFormula('multiplicativo').calc({ m });
    expect(trueXG1).toBeCloseTo(multiplicativo.trueXG1, 10);
    expect(trueXG2).toBeCloseTo(multiplicativo.trueXG2, 10);
    expect(aviso).toBeTruthy();
  });

  it('normaliza pela força dos adversários enfrentados quando os parâmetros estão completos', () => {
    const params = { xgaAdv1: 1.1, xgAdv1: 1.3, xgaAdv2: 1.2, xgAdv2: 1.4 };
    const { trueXG1, trueXG2, aviso } = getLambdaFormula('dinamica').calc({ m, params });

    const forcaAtq1 = m.xg1 / params.xgaAdv1;
    const forcaDef2 = m.xga2 / params.xgAdv2;
    const forcaAtq2 = m.xg2 / params.xgaAdv2;
    const forcaDef1 = m.xga1 / params.xgAdv1;

    expect(trueXG1).toBeCloseTo(forcaAtq1 * forcaDef2 * LIGA_MEDIA_MANDANTE, 10);
    expect(trueXG2).toBeCloseTo(forcaAtq2 * forcaDef1 * LIGA_MEDIA_VISITANTE, 10);
    expect(aviso).toBeUndefined();
  });
});
