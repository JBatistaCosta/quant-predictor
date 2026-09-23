import { describe, it, expect } from 'vitest';
import {
  ZONAS, TAXA_DESFECHO_POR_ZONA, MATRIZ_TRANSICAO,
  simularPosse, simularOrigemChutes, ajustarPerdaPorForca,
} from './zoneTransitionMatrix';

describe('constantes -- integridade dos dados do Achado 15', () => {
  it('9 zonas, matriz 9x9, tabela de desfecho com 9 linhas', () => {
    expect(ZONAS).toHaveLength(9);
    expect(MATRIZ_TRANSICAO).toHaveLength(9);
    MATRIZ_TRANSICAO.forEach(linha => expect(linha).toHaveLength(9));
    expect(TAXA_DESFECHO_POR_ZONA).toHaveLength(9);
  });

  it('cada linha da matriz de transição soma ~1 (condicional a manter a posse)', () => {
    MATRIZ_TRANSICAO.forEach((linha) => {
      const soma = linha.reduce((a, b) => a + b, 0);
      expect(soma).toBeCloseTo(1, 2);
    });
  });

  it('cada zona tem chute+perda+continua somando ~1', () => {
    TAXA_DESFECHO_POR_ZONA.forEach(({ chute, perda, continua }) => {
      expect(chute + perda + continua).toBeCloseTo(1, 2);
    });
  });

  it('diagonal é o maior valor de cada linha (bola tende a ficar no próprio corredor)', () => {
    MATRIZ_TRANSICAO.forEach((linha, i) => {
      const maior = Math.max(...linha);
      expect(linha[i]).toBeCloseTo(maior, 6);
    });
  });

  it('ataque-centro (índice 7) é a zona com maior taxa de chute', () => {
    const taxasChute = TAXA_DESFECHO_POR_ZONA.map(z => z.chute);
    const maiorIdx = taxasChute.indexOf(Math.max(...taxasChute));
    expect(maiorIdx).toBe(7);
  });
});

describe('simularPosse', () => {
  it('rng()=0 na zona ataque-centro (única com chute≥20%) termina em chute imediato', () => {
    // Zonas defensivas têm chute=0,00% na fonte -- rng()=0 lá cairia em
    // "perda" (1ª categoria com peso>0), não em "chute". Forçar o início em
    // atq_cen (índice 7, chute=19,94%) isola o caminho "chute" de propósito.
    const r = simularPosse({ zonasInicio: [7], rng: () => 0 });
    expect(r.desfecho).toBe('chute');
    expect(r.passes).toBe(1);
    expect(r.zonaChute).toBe(7);
  });

  it('rng()=0.1 na zona defensiva (chute=0) termina em perda, nunca em chute', () => {
    // def_esq: chute=0, perda=15,7% -- rng()=0.1 cai dentro da faixa de
    // perda (entre 0 e 0,157), nunca poderia cair em chute (peso zero).
    const r = simularPosse({ zonasInicio: [0], rng: () => 0.1 });
    expect(r.desfecho).toBe('perda');
    expect(r.zonaChute).toBeNull();
  });

  it('zonaChute, quando existe, é sempre um índice de zona válido (0-8)', () => {
    for (let i = 0; i < 500; i++) {
      const r = simularPosse();
      expect(r.zonaChute === null || (r.zonaChute >= 0 && r.zonaChute <= 8)).toBe(true);
    }
  });
});

describe('simularOrigemChutes', () => {
  it('distribuição por zona soma 1 quando há chutes', () => {
    const r = simularOrigemChutes(20000);
    const soma = r.distribuicaoPorZona.reduce((a, b) => a + b, 0);
    expect(soma).toBeCloseTo(1, 6);
    expect(r.taxaChutePorPosse).toBeGreaterThan(0);
    expect(r.taxaChutePorPosse).toBeLessThan(1);
  });

  it('ataque-centro concentra a maior fatia dos chutes simulados (bate com o achado: zona mais decisiva)', () => {
    const r = simularOrigemChutes(20000);
    const maiorIdx = r.distribuicaoPorZona.indexOf(Math.max(...r.distribuicaoPorZona));
    expect(maiorIdx).toBe(7); // atq_cen
  });

  it('zero posses não quebra (distribuição toda zero, taxa zero)', () => {
    const r = simularOrigemChutes(0);
    expect(r.distribuicaoPorZona.every(v => v === 0)).toBe(true);
    expect(r.taxaChutePorPosse).toBe(0);
  });
});

describe('ajustarPerdaPorForca -- disponível mas nunca chamada pelo caminho de simulação', () => {
  it('fatorPerda=1 é no-op (mesma referência/valores)', () => {
    const ajustado = ajustarPerdaPorForca(TAXA_DESFECHO_POR_ZONA, 1);
    expect(ajustado).toBe(TAXA_DESFECHO_POR_ZONA);
  });

  it('fatorPerda>1 aumenta perda e reduz continua, mantendo a soma em 1', () => {
    const ajustado = ajustarPerdaPorForca(TAXA_DESFECHO_POR_ZONA, 1.5);
    ajustado.forEach((zona, i) => {
      expect(zona.perda).toBeGreaterThanOrEqual(TAXA_DESFECHO_POR_ZONA[i].perda);
      expect(zona.chute + zona.perda + zona.continua).toBeCloseTo(1, 6);
    });
  });

  it('simularPosse/simularOrigemChutes nunca aplicam o ajuste por padrão (usam TAXA_DESFECHO_POR_ZONA crua)', () => {
    // Roda com rng determinístico forçando sempre "chute" na 1ª zona sorteada
    // -- se o motor estivesse aplicando algum ajuste, o comportamento não
    // mudaria aqui (o teste é mais uma trava de intenção: simularPosse não
    // aceita nenhum parâmetro de força/ajuste na assinatura). Mesmo cuidado
    // do primeiro teste: força início em atq_cen (única zona com chute
    // dominante em rng=0) pra isolar o caminho "chute".
    const r = simularPosse({ zonasInicio: [7], rng: () => 0 });
    expect(r.desfecho).toBe('chute');
  });
});
