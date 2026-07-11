// src/utils/lambdaFormulas.js
// Registro de fórmulas alternativas pra combinar xG próprio + xGA do
// adversário no "verdadeiro" xG esperado de cada time (trueXG1/trueXG2),
// que depois vira o lambda da Poisson em AnaliseEvento.jsx. Cada fórmula
// recebe o mesmo "contexto" e devolve { trueXG1, trueXG2, aviso? } — troca
// de fórmula é só trocar qual função roda, o resto do pipeline (Poisson,
// Dixon-Coles, Monte Carlo, Markov) não muda.
import { LIGA_MEDIA_MANDANTE, LIGA_MEDIA_VISITANTE, LIGA_MEDIA_GERAL, GAMMA_MANDANTE, GAMMA_VISITANTE } from './poisson';

// Média ponderada por decaimento exponencial: index 0 do histórico = jogo mais
// recente, peso maior; jogos mais antigos pesam cada vez menos. Ignora
// entradas sem valor (null/NaN) em vez de tratar como zero.
function mediaPonderada(historico, campo, xi) {
  if (!historico || historico.length === 0) return null;
  let somaPesos = 0, somaValores = 0;
  historico.forEach((jogo, i) => {
    const valor = jogo?.[campo];
    if (valor === null || valor === undefined || !Number.isFinite(valor)) return;
    const peso = Math.exp(-xi * i);
    somaPesos += peso;
    somaValores += peso * valor;
  });
  return somaPesos > 0 ? somaValores / somaPesos : null;
}

function combinacaoMultiplicativa(xgAtaque, xgaDefesa, gamma) {
  return (xgAtaque * xgaDefesa / LIGA_MEDIA_GERAL) * gamma;
}

export const LAMBDA_FORMULAS = [
  {
    id: 'multiplicativo',
    nome: 'Multiplicativo (xG × xGA, com mando real)',
    descricao: 'xG próprio × xGA do adversário ÷ média da liga, com vantagem de mando embutida (casa ×1,10, fora ×0,90, calibrado com 314 jogos reais). Times muito acima ou abaixo da média ficam com previsões mais decisivas, em vez de "amassadas" pela média do adversário.',
    precisaHistorico: false,
    calc: ({ m }) => ({
      trueXG1: combinacaoMultiplicativa(m.xg1, m.xga2, GAMMA_MANDANTE),
      trueXG2: combinacaoMultiplicativa(m.xg2, m.xga1, GAMMA_VISITANTE),
    }),
  },
  {
    id: 'media_simples',
    nome: 'Média simples (xG + xGA) / 2',
    descricao: 'Comportamento mais antigo do app: só tira a média entre o xG próprio e o xGA do adversário, sem mando de campo embutido. "Dilui" ataques/defesas muito fora do normal — mais conservador.',
    precisaHistorico: false,
    calc: ({ m }) => ({
      trueXG1: (m.xg1 + m.xga2) / 2,
      trueXG2: (m.xg2 + m.xga1) / 2,
    }),
  },
  {
    id: 'ataque_defesa',
    nome: 'Ataque × Defesa vs média da liga (Dixon-Coles clássico)',
    descricao: 'Versão clássica do modelo Dixon-Coles: força de ataque (xG próprio ÷ média de gols de quem joga em casa/fora) × força de defesa do adversário (xGA ÷ a mesma média), multiplicado de volta pela média casa/fora. O mando de campo já está embutido nas médias separadas — não soma gamma extra.',
    precisaHistorico: false,
    calc: ({ m }) => ({
      trueXG1: (m.xg1 / LIGA_MEDIA_MANDANTE) * (m.xga2 / LIGA_MEDIA_VISITANTE) * LIGA_MEDIA_MANDANTE,
      trueXG2: (m.xg2 / LIGA_MEDIA_VISITANTE) * (m.xga1 / LIGA_MEDIA_MANDANTE) * LIGA_MEDIA_VISITANTE,
    }),
  },
  {
    id: 'shrinkage',
    nome: 'Bayesian Shrinkage (encolhimento pra média da liga)',
    descricao: 'Times com poucos jogos de amostra têm o xG/xGA "encolhido" em direção à média da liga (proteção contra amostra pequena e ruidosa); conforme mais jogos entram na amostra, o modelo confia cada vez mais no desempenho observado. Depois disso, combina do jeito multiplicativo de sempre.',
    precisaHistorico: false,
    parametros: [
      { id: 'n', label: 'Jogos na amostra', default: 5, min: 1, max: 38 },
      { id: 'k', label: 'Força do prior (peso da média da liga)', default: 6, min: 0, max: 30 },
    ],
    calc: ({ m, params }) => {
      const n = params?.n ?? 5;
      const k = params?.k ?? 6;
      const encolher = (valor) => (n * valor + k * LIGA_MEDIA_GERAL) / (n + k);
      const xg1 = encolher(m.xg1), xga1 = encolher(m.xga1);
      const xg2 = encolher(m.xg2), xga2 = encolher(m.xga2);
      return {
        trueXG1: combinacaoMultiplicativa(xg1, xga2, GAMMA_MANDANTE),
        trueXG2: combinacaoMultiplicativa(xg2, xga1, GAMMA_VISITANTE),
      };
    },
  },
  {
    id: 'decay',
    nome: 'Time Decay (peso maior pros jogos recentes)',
    descricao: 'Usa o xG/xGA jogo a jogo (não só a média) e dá peso exponencialmente maior pros jogos mais recentes — reage mais rápido a uma mudança de forma do time, sem "ancorar" nos jogos de várias rodadas atrás. Precisa do histórico preenchido (manual ou pela busca automática); sem histórico, cai de volta pra fórmula multiplicativa com a média informada.',
    precisaHistorico: true,
    parametros: [
      { id: 'xi', label: 'Taxa de decaimento (ξ) — maior = esquece mais rápido', default: 0.2, min: 0.01, max: 1 },
    ],
    calc: ({ m, historico1, historico2, params }) => {
      const xi = params?.xi ?? 0.2;
      const xgDecay1 = mediaPonderada(historico1, 'xg', xi);
      const xgaDecay1 = mediaPonderada(historico1, 'xga', xi);
      const xgDecay2 = mediaPonderada(historico2, 'xg', xi);
      const xgaDecay2 = mediaPonderada(historico2, 'xga', xi);

      const avisos = [];
      const xg1 = xgDecay1 ?? m.xg1;
      const xga1 = xgaDecay1 ?? m.xga1;
      const xg2 = xgDecay2 ?? m.xg2;
      const xga2 = xgaDecay2 ?? m.xga2;
      if (xgDecay1 === null || xgaDecay1 === null) avisos.push('Sem histórico suficiente pro time mandante — usando a média informada.');
      if (xgDecay2 === null || xgaDecay2 === null) avisos.push('Sem histórico suficiente pro time visitante — usando a média informada.');

      return {
        trueXG1: combinacaoMultiplicativa(xg1, xga2, GAMMA_MANDANTE),
        trueXG2: combinacaoMultiplicativa(xg2, xga1, GAMMA_VISITANTE),
        aviso: avisos.length > 0 ? avisos.join(' ') : undefined,
      };
    },
  },
  {
    id: 'dinamica',
    nome: 'Normalização Dinâmica (relativa aos adversários enfrentados)',
    descricao: 'Em vez de comparar com a média geral da liga, normaliza o ataque/defesa de cada time pela força média dos adversários que ELE enfrentou nos últimos jogos — isola o desempenho do time do "ruído" do resto da tabela. Precisa dos campos de "força dos adversários enfrentados"; sem eles, cai de volta pra fórmula multiplicativa.',
    precisaHistorico: false,
    parametros: [
      { id: 'xgaAdv1', label: 'xGA médio dos adversários do mandante', default: '' },
      { id: 'xgAdv1', label: 'xG médio dos adversários do mandante', default: '' },
      { id: 'xgaAdv2', label: 'xGA médio dos adversários do visitante', default: '' },
      { id: 'xgAdv2', label: 'xG médio dos adversários do visitante', default: '' },
    ],
    calc: ({ m, params }) => {
      const { xgaAdv1, xgAdv1, xgaAdv2, xgAdv2 } = params || {};
      const completo = [xgaAdv1, xgAdv1, xgaAdv2, xgAdv2].every(v => Number.isFinite(v) && v > 0);

      if (!completo) {
        return {
          trueXG1: combinacaoMultiplicativa(m.xg1, m.xga2, GAMMA_MANDANTE),
          trueXG2: combinacaoMultiplicativa(m.xg2, m.xga1, GAMMA_VISITANTE),
          aviso: 'Preencha a força dos adversários enfrentados pelos dois times pra usar a normalização dinâmica — usando a fórmula multiplicativa por enquanto.',
        };
      }

      const forcaAtq1 = m.xg1 / xgaAdv1;
      const forcaDef2 = m.xga2 / xgAdv2;
      const forcaAtq2 = m.xg2 / xgaAdv2;
      const forcaDef1 = m.xga1 / xgAdv1;

      return {
        trueXG1: forcaAtq1 * forcaDef2 * LIGA_MEDIA_MANDANTE,
        trueXG2: forcaAtq2 * forcaDef1 * LIGA_MEDIA_VISITANTE,
      };
    },
  },
];

export const getLambdaFormula = (id) => LAMBDA_FORMULAS.find(f => f.id === id) || LAMBDA_FORMULAS[0];
