// Testes de src/utils/zoneTransitionMatrix18.js (arquivo GERADO). Roda com: node --test src/utils/zoneTransitionMatrix18.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { ZONAS_18, TAXA_DESFECHO_18, MATRIZ_TRANSICAO_18, ACOES_POR_ZONA_18 } from './zoneTransitionMatrix18.js';
import { TAXA_DESFECHO_12, MATRIZ_TRANSICAO_12, ACOES_POR_ZONA_12 } from './zoneTransitionMatrix12.js';

// zona de 18 (índice) -> zona de 12 (índice) que a contém; igual a PAI_12_DE_18 do gerador e a zonas_campo18.zona12 do banco
const PAI = [0, 1, 2, 3, 4, 5, 3, 4, 5, 6, 7, 8, 6, 7, 8, 9, 10, 11];

test('tem 18 zonas, 6 faixas x 3 corredores, na ordem de public.zona_campo18', () => {
  assert.equal(ZONAS_18.length, 18);
  assert.deepEqual(ZONAS_18.map((z) => z.zona), Array.from({ length: 18 }, (_, i) => i + 1));
  assert.deepEqual([...new Set(ZONAS_18.map((z) => z.faixa))],
    ['defesa', 'meio_baixo', 'meio_alto', 'ataque_fora_da_area_baixo', 'ataque_fora_da_area_alto', 'grande_area_adversaria']);
  assert.deepEqual(ZONAS_18.slice(0, 3).map((z) => z.corredor), ['lado_y_baixo', 'centro', 'lado_y_alto']);
});

test('dimensões, linhas somam 1 e amostra mínima por zona', () => {
  assert.equal(TAXA_DESFECHO_18.length, 18);
  assert.equal(MATRIZ_TRANSICAO_18.length, 18);
  for (let i = 0; i < 18; i += 1) {
    assert.equal(MATRIZ_TRANSICAO_18[i].length, 18);
    const t = TAXA_DESFECHO_18[i];
    assert.ok(Math.abs(t.chute + t.perda + t.continua - 1) < 0.0005, `desfecho da zona ${i + 1}`);
    assert.ok(Math.abs(MATRIZ_TRANSICAO_18[i].reduce((a, b) => a + b, 0) - 1) < 0.0009, `transição da zona ${i + 1}`);
    assert.ok(ACOES_POR_ZONA_18[i] > 5000, `amostra da zona ${i + 1}`);
  }
});

test('refinamento exato: as 18 zonas agregadas em 12 reproduzem as taxas e as contagens da matriz de 12', () => {
  for (let p = 0; p < 12; p += 1) {
    const filhos = PAI.map((pai, z) => (pai === p ? z : -1)).filter((z) => z >= 0);
    const n = filhos.reduce((a, z) => a + ACOES_POR_ZONA_18[z], 0);
    assert.equal(n, ACOES_POR_ZONA_12[p], `ações da zona 12 número ${p + 1}`);
    for (const campo of ['chute', 'perda', 'continua']) {
      const agregada = filhos.reduce((a, z) => a + ACOES_POR_ZONA_18[z] * TAXA_DESFECHO_18[z][campo], 0) / n;
      assert.ok(Math.abs(agregada - TAXA_DESFECHO_12[p][campo]) < 0.001, `${campo} da zona 12 número ${p + 1}`);
    }
  }
});

test('o que a grade fina separa: logo antes da área o centro "alto" chuta muito mais que o "baixo"', () => {
  const baixo = TAXA_DESFECHO_18[10].chute;   // ataque_fora_da_area_baixo / centro
  const alto = TAXA_DESFECHO_18[13].chute;    // ataque_fora_da_area_alto / centro
  assert.ok(alto > 3 * baixo, `alto=${alto} baixo=${baixo}`);
  const chutes = TAXA_DESFECHO_18.map((t) => t.chute);
  assert.equal(chutes.indexOf(Math.max(...chutes)), 16);       // grande área central segue sendo a zona que mais vira chute
});

test('a bola nunca vai da defesa direto à grande área adversária numa ação só', () => {
  for (let i = 0; i < 3; i += 1) for (let j = 15; j < 18; j += 1) assert.ok(MATRIZ_TRANSICAO_18[i][j] < 0.005);
});
