// Testes de src/utils/zoneTransitionMatrix12.js (arquivo GERADO). Roda com: node --test src/utils/zoneTransitionMatrix12.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { ZONAS_12, TAXA_DESFECHO_12, MATRIZ_TRANSICAO_12, ACOES_POR_ZONA_12 } from './zoneTransitionMatrix12.js';

test('tem 12 zonas, na ordem de public.zona_campo12 (faixa*3 + corredor)', () => {
  assert.equal(ZONAS_12.length, 12);
  assert.deepEqual(ZONAS_12.map((z) => z.zona), [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]);
  assert.deepEqual(ZONAS_12.slice(0, 3).map((z) => z.faixa), ['defesa', 'defesa', 'defesa']);
  assert.deepEqual(ZONAS_12.slice(9).map((z) => z.faixa), Array(3).fill('grande_area_adversaria'));
  assert.deepEqual(ZONAS_12.slice(0, 3).map((z) => z.corredor), ['lado_y_baixo', 'centro', 'lado_y_alto']);
});

test('as dimensões batem e cada linha soma 1 (a transição) ou 1 (o desfecho)', () => {
  assert.equal(TAXA_DESFECHO_12.length, 12);
  assert.equal(MATRIZ_TRANSICAO_12.length, 12);
  assert.equal(ACOES_POR_ZONA_12.length, 12);
  for (let i = 0; i < 12; i += 1) {
    assert.equal(MATRIZ_TRANSICAO_12[i].length, 12);
    const t = TAXA_DESFECHO_12[i];
    assert.ok(Math.abs(t.chute + t.perda + t.continua - 1) < 0.0005, `desfecho da zona ${i + 1}`);
    assert.ok(Math.abs(MATRIZ_TRANSICAO_12[i].reduce((a, b) => a + b, 0) - 1) < 0.0006, `transição da zona ${i + 1}`);
    assert.ok(ACOES_POR_ZONA_12[i] > 5000, `amostra da zona ${i + 1}`);
  }
});

test('padrões que o Achado 15 já tinha e a grade fina precisa preservar', () => {
  // a bola tende a ficar na própria zona (a diagonal é o maior valor de cada linha)
  for (let i = 0; i < 12; i += 1) {
    assert.equal(MATRIZ_TRANSICAO_12[i].indexOf(Math.max(...MATRIZ_TRANSICAO_12[i])), i, `diagonal da zona ${i + 1}`);
  }
  // da defesa não se chega à grande área adversária numa ação só
  for (let i = 0; i < 3; i += 1) for (let j = 9; j < 12; j += 1) assert.ok(MATRIZ_TRANSICAO_12[i][j] < 0.005);
  // a grande área central é, de longe, a zona que mais vira chute
  const chutes = TAXA_DESFECHO_12.map((t) => t.chute);
  assert.equal(chutes.indexOf(Math.max(...chutes)), 10);
  assert.ok(chutes[10] > 0.4 && chutes[9] < 0.1 && chutes[11] < 0.1);
});
