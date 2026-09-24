import { describe, it, expect } from 'vitest';
import { runMarkovSimulation, MARKOV_MINUTES, MINUTE_BINS } from './markovEngine';

// Média marginal de gols do time 1/2 a partir do heatGrid 7x7 -- mesma ideia
// de reconstruir o valor esperado de uma distribuição a partir do
// histograma (a simulação em si é o que está sob teste, não reimplementada
// aqui: só a leitura do resultado).
function mediaGols(heatGrid, eixo) {
  let media = 0;
  for (let i = 0; i < 7; i++) {
    for (let j = 0; j < 7; j++) {
      const p = heatGrid[i][j];
      media += (eixo === 1 ? i : j) * p;
    }
  }
  return media;
}

describe('MARKOV_MINUTES / MINUTE_BINS', () => {
  it('90 minutos, 6 bins de 15 minutos cobrindo 0-89', () => {
    expect(MARKOV_MINUTES).toBe(90);
    expect(MINUTE_BINS).toHaveLength(6);
    expect(MINUTE_BINS[0]).toEqual({ label: '0-14', lo: 0, hi: 14 });
    expect(MINUTE_BINS[5]).toEqual({ label: '75-89', lo: 75, hi: 89 });
  });
});

describe('runMarkovSimulation -- convergência (lei dos grandes números)', () => {
  it('sem dynamics e sem chutes, a média de gols simulados converge pro lambda de entrada', () => {
    const lambda1 = 1.8, lambda2 = 1.2;
    const r = runMarkovSimulation({
      gols: { lambda1, lambda2 },
      dynamics: false,
      simCount: 40000,
    });
    expect(mediaGols(r.heatGrid, 1)).toBeCloseTo(lambda1, 0);
    expect(mediaGols(r.heatGrid, 2)).toBeCloseTo(lambda2, 0);
    // toBeCloseTo(x, 0) só garante a casa das unidades -- confere também a
    // tolerância relativa (Monte Carlo com 40k sims deve ficar bem mais
    // perto que 0.5 de diferença absoluta).
    expect(Math.abs(mediaGols(r.heatGrid, 1) - lambda1)).toBeLessThan(0.1);
    expect(Math.abs(mediaGols(r.heatGrid, 2) - lambda2)).toBeLessThan(0.1);
  });

  it('probWin1 + probDraw + probWin2 soma 1', () => {
    const r = runMarkovSimulation({ gols: { lambda1: 1.5, lambda2: 1.5 }, dynamics: false, simCount: 10000 });
    expect(r.probWin1 + r.probDraw + r.probWin2).toBeCloseTo(1, 10);
  });
});

describe('runMarkovSimulation -- invariante estrutural da cadeia chute -> chute no alvo -> gol', () => {
  it('gols <= chutesNoAlvo <= chutes, em média, quando a cadeia tem dado de chute', () => {
    const r = runMarkovSimulation({
      gols: { lambda1: 1.8, lambda2: 1.2 },
      chutes: { chutes1: 14, chutes2: 10, chutesNoAlvo1: 5, chutesNoAlvo2: 3.5 },
      dynamics: false,
      simCount: 20000,
    });
    expect(r.chutes.chutesNoAlvo1).toBeLessThanOrEqual(r.chutes.chutes1);
    expect(r.chutes.chutesNoAlvo2).toBeLessThanOrEqual(r.chutes.chutes2);
    // gol é subconjunto de chute-no-alvo por construção (só sorteado depois
    // de "foi no alvo"), então a média de gols simulados nunca pode passar
    // da média de chutes no alvo simulados.
    const mediaGols1 = mediaGols(r.heatGrid, 1);
    const mediaGols2 = mediaGols(r.heatGrid, 2);
    expect(mediaGols1).toBeLessThanOrEqual(r.chutes.chutesNoAlvo1 + 1e-9);
    expect(mediaGols2).toBeLessThanOrEqual(r.chutes.chutesNoAlvo2 + 1e-9);
  });

  it('sem dado de chute, degrada pro comportamento de um sorteio único de gol (sem regressão)', () => {
    const r = runMarkovSimulation({ gols: { lambda1: 1.8, lambda2: 1.2 }, dynamics: false, simCount: 20000 });
    // chutes/chutesNoAlvo devem convergir pro mesmo valor do gol (pAcc=pConv=1)
    expect(r.chutes.chutes1).toBeCloseTo(mediaGols(r.heatGrid, 1), 1);
    expect(r.chutes.chutesNoAlvo1).toBeCloseTo(mediaGols(r.heatGrid, 1), 1);
  });
});

describe('runMarkovSimulation -- fallback de league_id (via markovParams, testado à parte) e params vazio', () => {
  it('params ausente/vazio não quebra e se comporta como multiplicadores neutros', () => {
    const r = runMarkovSimulation({
      gols: { lambda1: 1.5, lambda2: 1.1 },
      dynamics: true, // dynamics ligado, mas sem nenhum multiplicador calibrado
      simCount: 15000,
      params: {},
    });
    expect(mediaGols(r.heatGrid, 1)).toBeCloseTo(1.5, 0);
    expect(mediaGols(r.heatGrid, 2)).toBeCloseTo(1.1, 0);
  });
});

describe('runMarkovSimulation -- dynamics=false ignora multiplicadores mesmo com params preenchido', () => {
  it('resultado com dynamics=false é o mesmo (em expectativa) com ou sem params calibrados', () => {
    const entradaBase = { gols: { lambda1: 1.6, lambda2: 1.3 }, simCount: 30000 };
    const paramsCalibrados = {
      gol: { mult_estado_placar: { perdendo: 1.5, empatando: 0.6, ganhando: 1.8 } },
    };
    const semParams = runMarkovSimulation({ ...entradaBase, dynamics: false, params: {} });
    const comParamsIgnorados = runMarkovSimulation({ ...entradaBase, dynamics: false, params: paramsCalibrados });

    const media1Sem = mediaGols(semParams.heatGrid, 1);
    const media1Com = mediaGols(comParamsIgnorados.heatGrid, 1);
    expect(Math.abs(media1Sem - media1Com)).toBeLessThan(0.15);
  });
});

describe('runMarkovSimulation -- cadeia não duplica o efeito de multiplicador de estado', () => {
  it('reproduz o multiplicador de GOL calibrado, não o de CHUTE, no produto da cadeia', () => {
    // Simulação de 1 minuto só: o estado é sempre "empatando" (0-0), então
    // isola a fórmula da cadeia de qualquer efeito de mistura de estados ao
    // longo do jogo (o que uma simulação de 90 minutos misturaria).
    const chutesTotal = 0.3, chutesNoAlvoTotal = 0.12, lambdaGol = 0.03;
    const multChute = 2.0, multGol = 1.5;

    const r = runMarkovSimulation({
      gols: { lambda1: lambdaGol, lambda2: lambdaGol },
      chutes: {
        chutes1: chutesTotal, chutes2: chutesTotal,
        chutesNoAlvo1: chutesNoAlvoTotal, chutesNoAlvo2: chutesNoAlvoTotal,
      },
      dynamics: true,
      simCount: 300000,
      minutes: 1,
      params: {
        chute: { mult_estado_placar: { empatando: multChute, perdendo: 1, ganhando: 1 } },
        gol: { mult_estado_placar: { empatando: multGol, perdendo: 1, ganhando: 1 } },
      },
    });

    const pGolSimulado = mediaGols(r.heatGrid, 1);
    const pAcc = chutesNoAlvoTotal / chutesTotal;
    const pConvBase = lambdaGol / chutesNoAlvoTotal;
    const baseP = chutesTotal * pAcc * pConvBase; // == lambdaGol, por construção
    const esperadoComMultGol = baseP * multGol; // multChute deve CANCELAR algebricamente na cadeia

    expect(pGolSimulado).toBeCloseTo(esperadoComMultGol, 2);
  });
});

describe('runMarkovSimulation -- cartões', () => {
  it('taxa de cartão simulada converge pra taxa de entrada sem dynamics', () => {
    const r = runMarkovSimulation({
      gols: { lambda1: 1.5, lambda2: 1.2 },
      cartaoAmarelo: { taxa1: 2.2, taxa2: 2.6 },
      cartaoVermelho: { taxa1: 0.1, taxa2: 0.12 },
      dynamics: false,
      simCount: 30000,
    });
    expect(r.cartoes.amarelo1).toBeCloseTo(2.2, 1);
    expect(r.cartoes.amarelo2).toBeCloseTo(2.6, 1);
    expect(r.cartoes.vermelho1).toBeCloseTo(0.1, 1);
    expect(r.cartoes.vermelho2).toBeCloseTo(0.12, 1);
  });

  it('distribuições de minuto de cartão somam 1 (são histogramas normalizados)', () => {
    const r = runMarkovSimulation({
      gols: { lambda1: 1.5, lambda2: 1.2 },
      cartaoAmarelo: { taxa1: 2.2, taxa2: 2.6 },
      cartaoVermelho: { taxa1: 0.1, taxa2: 0.12 },
      dynamics: false,
      simCount: 10000,
    });
    const soma = (arr) => arr.reduce((a, b) => a + b, 0);
    expect(soma(r.cartoes.distribuicaoAmareloMinuto1)).toBeCloseTo(1, 8);
    expect(soma(r.cartoes.distribuicaoAmareloMinuto2)).toBeCloseTo(1, 8);
    expect(soma(r.cartoes.distribuicaoVermelhoMinuto1)).toBeCloseTo(1, 8);
    expect(soma(r.cartoes.distribuicaoVermelhoMinuto2)).toBeCloseTo(1, 8);
    expect(r.cartoes.distribuicaoAmareloMinuto1).toHaveLength(6);
  });
});

describe('runMarkovSimulation -- escanteios e faltas (Fase 3)', () => {
  it('taxa de escanteio/falta simulada converge pra taxa de entrada sem dynamics', () => {
    const r = runMarkovSimulation({
      gols: { lambda1: 1.5, lambda2: 1.2 },
      escanteio: { taxa1: 5.2, taxa2: 4.1 },
      falta: { taxa1: 11.3, taxa2: 12.7 },
      dynamics: false,
      simCount: 30000,
    });
    expect(r.escanteios.escanteio1).toBeCloseTo(5.2, 1);
    expect(r.escanteios.escanteio2).toBeCloseTo(4.1, 1);
    expect(r.faltas.falta1).toBeCloseTo(11.3, 0);
    expect(r.faltas.falta2).toBeCloseTo(12.7, 0);
  });

  it('distribuições de minuto de escanteio/falta somam 1 (histogramas normalizados)', () => {
    const r = runMarkovSimulation({
      gols: { lambda1: 1.5, lambda2: 1.2 },
      escanteio: { taxa1: 5.2, taxa2: 4.1 },
      falta: { taxa1: 11.3, taxa2: 12.7 },
      dynamics: false,
      simCount: 10000,
    });
    const soma = (arr) => arr.reduce((a, b) => a + b, 0);
    expect(soma(r.escanteios.distribuicaoMinuto1)).toBeCloseTo(1, 8);
    expect(soma(r.escanteios.distribuicaoMinuto2)).toBeCloseTo(1, 8);
    expect(soma(r.faltas.distribuicaoMinuto1)).toBeCloseTo(1, 8);
    expect(soma(r.faltas.distribuicaoMinuto2)).toBeCloseTo(1, 8);
    expect(r.escanteios.distribuicaoMinuto1).toHaveLength(6);
  });

  it('com dynamics ligado e mult_minuto_bin calibrado, a forma temporal simulada reflete o multiplicador (mais eventos no bin com mult>1)', () => {
    const r = runMarkovSimulation({
      gols: { lambda1: 1.5, lambda2: 1.2 },
      escanteio: { taxa1: 5, taxa2: 5 },
      dynamics: true,
      simCount: 40000,
      params: {
        escanteio: {
          mult_minuto_bin: { '0-14': 0.5, '15-29': 0.5, '30-44': 0.5, '45-59': 0.5, '60-74': 0.5, '75-89': 3.0 },
        },
      },
    });
    // bin '75-89' (índice 5) tem multiplicador 6x maior que os outros bins
    // (3.0 vs 0.5) -- a fração de escanteios simulados nesse bin deve ser a
    // maior de longe, mesmo os bins tendo o mesmo nº de minutos (15 cada).
    const dist = r.escanteios.distribuicaoMinuto1;
    const maiorIndex = dist.indexOf(Math.max(...dist));
    expect(maiorIndex).toBe(5);
  });

  it('sem dado de escanteio/falta (taxa ausente), não sorteia nada e não quebra', () => {
    const r = runMarkovSimulation({ gols: { lambda1: 1.5, lambda2: 1.2 }, dynamics: false, simCount: 5000 });
    expect(r.escanteios.escanteio1).toBe(0);
    expect(r.escanteios.escanteio2).toBe(0);
    expect(r.faltas.falta1).toBe(0);
    expect(r.faltas.falta2).toBe(0);
  });
});

describe('runMarkovSimulation -- linhasOverUnder/overUnder (Fase 6, job em lote)', () => {
  it('sem linhasOverUnder, overUnder é null (não regride o caminho da UI/Fase 5)', () => {
    const r = runMarkovSimulation({
      gols: { lambda1: 1.5, lambda2: 1.2 },
      cartaoAmarelo: { taxa1: 2.2, taxa2: 2.6 },
      dynamics: false,
      simCount: 5000,
    });
    expect(r.overUnder).toBeNull();
  });

  it('com linhasOverUnder, devolve fração over em [0,1] pra cada linha pedida, monotonicamente decrescente', () => {
    const r = runMarkovSimulation({
      gols: { lambda1: 1.5, lambda2: 1.2 },
      cartaoAmarelo: { taxa1: 2.2, taxa2: 2.6 },
      cartaoVermelho: { taxa1: 0.1, taxa2: 0.12 },
      escanteio: { taxa1: 5.2, taxa2: 4.1 },
      falta: { taxa1: 11.3, taxa2: 12.7 },
      dynamics: false,
      simCount: 20000,
      linhasOverUnder: {
        cartoes: [1.5, 2.5, 3.5, 4.5, 5.5, 6.5],
        escanteios: [7.5, 8.5, 9.5, 10.5, 11.5, 12.5],
        faltas: [20.5, 22.5, 24.5, 26.5, 28.5, 30.5],
      },
    });
    expect(r.overUnder).not.toBeNull();
    for (const grupo of ['cartoes', 'escanteios', 'faltas']) {
      const linhas = Object.keys(r.overUnder[grupo]).map(Number).sort((a, b) => a - b);
      let anterior = 1;
      for (const linha of linhas) {
        const p = r.overUnder[grupo][linha.toFixed(1)];
        expect(p).toBeGreaterThanOrEqual(0);
        expect(p).toBeLessThanOrEqual(1);
        expect(p).toBeLessThanOrEqual(anterior + 1e-9); // linha maior nunca tem over mais provável
        anterior = p;
      }
    }
  });

  it('taxa muito alta -> over ~1 numa linha baixa; taxa muito baixa -> over ~0 numa linha alta (controle positivo da contagem)', () => {
    const r = runMarkovSimulation({
      gols: { lambda1: 1.5, lambda2: 1.2 },
      cartaoAmarelo: { taxa1: 20, taxa2: 20 }, // taxa altíssima, quase sempre > 1.5 no total da partida
      dynamics: false,
      simCount: 5000,
      linhasOverUnder: { cartoes: [1.5, 200.5] }, // 200.5 é impossível de estourar em 90 min
    });
    expect(r.overUnder.cartoes['1.5']).toBeGreaterThan(0.99);
    expect(r.overUnder.cartoes['200.5']).toBe(0);
  });
});

describe('runMarkovSimulation -- contrato mínimo de saída (compatibilidade com a UI atual)', () => {
  it('devolve todos os campos que a UI de AnaliseEvento.jsx já consome hoje', () => {
    const r = runMarkovSimulation({ gols: { lambda1: 1.5, lambda2: 1.2 }, dynamics: false, simCount: 5000 });
    expect(r).toHaveProperty('probWin1');
    expect(r).toHaveProperty('probDraw');
    expect(r).toHaveProperty('probWin2');
    expect(r).toHaveProperty('topScores');
    expect(r).toHaveProperty('minuteDistribution');
    expect(r).toHaveProperty('heatGrid');
    expect(r).toHaveProperty('heatMin');
    expect(r).toHaveProperty('heatMax');
    expect(r.minuteDistribution).toHaveLength(6);
    expect(r.heatGrid).toHaveLength(7);
    expect(r.heatGrid[0]).toHaveLength(7);
    expect(r.topScores.length).toBeLessThanOrEqual(6);
  });
});
