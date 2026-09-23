// src/workers/markovEngine.worker.js
// Wrapper de Web Worker em volta de `src/utils/markovEngine.js` — roda o
// laço minuto-a-minuto multi-evento FORA da main thread. Substitui o
// `setTimeout(fn, 50)` que `runMarkovSimulation` (o handler antigo, em
// `AnaliseEvento.jsx`) usava só pra liberar o event loop antes do laço
// bloqueante; o worker já roda isolado, então esse hack não é mais
// necessário no caminho principal (só sobra como fallback degradado, ver
// `AnaliseEvento.jsx`, pro caso raro de `Worker` não existir).
//
// Contrato de mensagem:
//   entrada:  { requestId, input }   -- `input` é exatamente o objeto que
//                                        `runMarkovSimulation` recebe.
//   saída:    { requestId, ok: true, resultado } | { requestId, ok: false, erro }
//
// `requestId` existe pra quem chama descartar uma resposta obsoleta se o
// usuário disparar "Rodar" de novo antes da simulação anterior terminar.

import { runMarkovSimulation } from '../utils/markovEngine.js';

self.onmessage = (event) => {
  const { requestId, input } = event.data || {};
  try {
    const resultado = runMarkovSimulation(input);
    self.postMessage({ requestId, ok: true, resultado });
  } catch (erro) {
    self.postMessage({ requestId, ok: false, erro: erro?.message || String(erro) });
  }
};
