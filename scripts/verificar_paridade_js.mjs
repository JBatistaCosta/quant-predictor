// scripts/verificar_paridade_js.mjs
//
// Confere que `src/utils/distribuicoesMercados.js` produz as MESMAS
// probabilidades que `scripts/distribuicoes.py`, comparando contra o fixture
// gerado por `scripts/gerar_golden_fixture.py`.
//
// Por que isso existe: a camada "parâmetros -> mercados" está implementada
// duas vezes, em linguagens diferentes (Python treina em lote, JS roda na
// calculadora do app). Se as duas divergirem, a mesma partida mostra número
// diferente dependendo de onde o usuário olha -- e sem teste ninguém
// perceberia. O projeto já tem duplicação parecida entre
// `src/utils/distributions.js` e `api/_lib/negbin.js`, protegida só por um
// comentário pedindo pra lembrar.
//
// Uso:
//     node scripts/verificar_paridade_js.mjs        # sai 1 se divergir
//
// Se você mudar a matemática de um lado, este teste falha até o outro lado
// acompanhar. Regenerar o fixture (`python scripts/gerar_golden_fixture.py`)
// sem olhar o diff derrota o propósito -- o fixture é o contrato.

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import { matrizPlacares, mercadosDeGols, mercadosDeEscanteios } from '../src/utils/distribuicoesMercados.js';

const AQUI = dirname(fileURLToPath(import.meta.url));
const FIXTURE = join(AQUI, '..', 'src', 'utils', '__fixtures__', 'mercados.golden.json');

// Tolerância. Python e JS somam em ordens diferentes e usam implementações
// distintas de Poisson/NB/lnGamma, então igualdade bit a bit é inatingível --
// mas 1e-9 é ordens de grandeza mais apertado que qualquer diferença que
// importaria numa odd (1e-9 em probabilidade move a odd justa na 7ª casa).
const TOLERANCIA = 1e-9;

const fixture = JSON.parse(readFileSync(FIXTURE, 'utf8'));

let comparadas = 0;
const divergencias = [];

const comparar = (rotuloCaso, esperado, obtido) => {
  for (const [mercado, selecoes] of Object.entries(esperado)) {
    for (const [selecao, valorEsperado] of Object.entries(selecoes)) {
      const valorObtido = obtido[mercado]?.[selecao];
      comparadas++;

      if (valorObtido === undefined) {
        divergencias.push(`${rotuloCaso} ${mercado}/${selecao}: ausente no JS (Python=${valorEsperado})`);
        continue;
      }
      const delta = Math.abs(valorObtido - valorEsperado);
      if (delta > TOLERANCIA) {
        divergencias.push(
          `${rotuloCaso} ${mercado}/${selecao}: Python=${valorEsperado.toExponential(12)} ` +
          `JS=${valorObtido.toExponential(12)} delta=${delta.toExponential(3)}`,
        );
      }
    }
  }

  // O JS não pode produzir mercado/seleção que o Python não produz — isso
  // indicaria divergência de escopo, não só de valor.
  for (const [mercado, selecoes] of Object.entries(obtido)) {
    for (const selecao of Object.keys(selecoes)) {
      if (esperado[mercado]?.[selecao] === undefined) {
        divergencias.push(`${rotuloCaso} ${mercado}/${selecao}: presente no JS, ausente no Python`);
      }
    }
  }
};

for (const { entrada, esperado } of fixture.gols) {
  const matriz = matrizPlacares(entrada.lambda_home, entrada.lambda_away, entrada.rho);
  comparar(`[gols/${entrada.nome}]`, esperado, mercadosDeGols(matriz));
}

for (const { entrada, esperado } of fixture.escanteios) {
  const obtido = mercadosDeEscanteios(entrada.media_total, entrada.disp_r, entrada.alpha, entrada.beta);
  comparar(`[escanteios/${entrada.nome}]`, esperado, obtido);
}

console.log(`Paridade Python<->JS: ${comparadas} probabilidades comparadas (tolerância ${TOLERANCIA}).`);

if (divergencias.length > 0) {
  console.error(`\n${divergencias.length} divergência(s):`);
  for (const linha of divergencias.slice(0, 40)) console.error(`  ${linha}`);
  if (divergencias.length > 40) console.error(`  ... e mais ${divergencias.length - 40}`);
  process.exit(1);
}

console.log('Tudo bate.');
