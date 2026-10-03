// Testes de src/utils/buscaJogadores.js. Roda com: node --test src/utils/buscaJogadores.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizarBusca, ordenarJogadores } from './buscaJogadores.js';

test('normalizarBusca tira espaços sobrando (o caso do espaço no fim)', () => {
  assert.equal(normalizarBusca('Gabriel Barbosa '), 'Gabriel Barbosa');
  assert.equal(normalizarBusca('  gabriel   barbosa'), 'gabriel barbosa');
  assert.equal(normalizarBusca(null), '');
});

test('ordenarJogadores por relevância desempata por valor de mercado', () => {
  const j = [{ id: 1, market_value: 10 }, { id: 2, market_value: 50 }, { id: 3, market_value: 5 }];
  const rel = new Map([[1, 1.0], [2, 1.0], [3, 0.9]]);
  const r = ordenarJogadores(j, { coluna: 'market_value', asc: false, relevancia: rel });
  assert.deepEqual(r.map(x => x.id), [2, 1, 3]);
});

test('ordenarJogadores sem relevância respeita a coluna e manda nulos para o fim', () => {
  const j = [{ id: 1, rating: null }, { id: 2, rating: 1500 }, { id: 3, rating: 1700 }];
  assert.deepEqual(ordenarJogadores(j, { coluna: 'rating', asc: false }).map(x => x.id), [3, 2, 1]);
  assert.deepEqual(ordenarJogadores(j, { coluna: 'rating', asc: true }).map(x => x.id), [2, 3, 1]);
});

test('ordenarJogadores por nome usa ordem alfabética pt-BR', () => {
  const j = [{ id: 1, name: 'Ítalo' }, { id: 2, name: 'Abel' }, { id: 3, name: 'Zé' }];
  assert.deepEqual(ordenarJogadores(j, { coluna: 'name', asc: true }).map(x => x.id), [2, 1, 3]);
});
