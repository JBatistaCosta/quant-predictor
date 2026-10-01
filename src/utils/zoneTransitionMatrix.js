// src/utils/zoneTransitionMatrix.js
// Camada de zona-a-zona (Fase 4 do plano de extensão do motor de Markov) —
// MÓDULO À PARTE, independente do motor multi-evento (`markovEngine.js`).
// Não alimenta gol/chute/cartão/escanteio/falta em nenhuma simulação real:
// serve só pra uma visualização opcional (heatmap de zona de origem do
// chute), atrás de feature flag, em `AnaliseEvento.jsx`.
//
// ORIGEM DOS NÚMEROS: Achado 15 (`ACHADOS_COMPORTAMENTO.md`) — StatsBomb
// Open Data, La Liga 2015/16 completa (380 partidas), 552.934 ações de
// passe/condução mapeadas numa grade de 9 zonas (3 terços de comprimento ×
// 3 corredores de largura). **CONSTANTE UNIVERSAL EXTERNA, NÃO CALIBRADA
// por liga/confronto** — mesma ressalva já documentada pra
// `MARKOV_MINUTE_BIN`/Achado 13 (escanteio/falta): é uma amostra externa
// única (uma liga, uma temporada), sem IC 95%, sem validação contra o dado
// do próprio projeto (FotMob não tem evento de passe com coordenada — ver
// Achado 16, é limite de dado, não de método). Nunca apresentar como se
// fosse específica de uma liga ou de um confronto.
//
// Achado 17 (mesmo arquivo) testou se a matriz de transição em si muda por
// força de time (Barcelona vs. Espanyol, os dois extremos da liga) e achou
// que NÃO — o que muda é a taxa de PERDA DE POSSE por zona, não pra onde a
// bola vai quando continua. Por isso o ajuste por força fica isolado em
// `ajustarPerdaPorForca` (função disponível, mas NUNCA aplicada por padrão
// -- ver comentário na função).

// Grade 3×3 (3 terços de comprimento × 3 corredores de largura), mesma
// convenção do Achado 12/15. Índice do array = posição na matriz/tabelas
// abaixo -- NUNCA reordenar sem atualizar as duas juntas.
export const ZONAS = [
  { id: 'def_esq', label: 'Defensiva-Esquerda', linha: 0, coluna: 0 },
  { id: 'def_cen', label: 'Defensiva-Centro', linha: 0, coluna: 1 },
  { id: 'def_dir', label: 'Defensiva-Direita', linha: 0, coluna: 2 },
  { id: 'meio_esq', label: 'Meio-Esquerda', linha: 1, coluna: 0 },
  { id: 'meio_cen', label: 'Meio-Centro', linha: 1, coluna: 1 },
  { id: 'meio_dir', label: 'Meio-Direita', linha: 1, coluna: 2 },
  { id: 'atq_esq', label: 'Ataque-Esquerda', linha: 2, coluna: 0 },
  { id: 'atq_cen', label: 'Ataque-Centro', linha: 2, coluna: 1 },
  { id: 'atq_dir', label: 'Ataque-Direita', linha: 2, coluna: 2 },
];

// Quando a bola chega numa zona: vira chute, perde a posse, ou continua
// (passe/condução pra outra zona) -- tabela "O que acontece quando o time
// tem a bola em cada zona", Achado 15. Cada linha soma ~100% (pequeno
// arredondamento da fonte original, normalizado em `sortearCategoria`).
export const TAXA_DESFECHO_POR_ZONA = [
  { chute: 0.0000, perda: 0.1570, continua: 0.8430 }, // def_esq
  { chute: 0.0000, perda: 0.1800, continua: 0.8200 }, // def_cen
  { chute: 0.0000, perda: 0.1690, continua: 0.8310 }, // def_dir
  { chute: 0.0001, perda: 0.1280, continua: 0.8719 }, // meio_esq
  { chute: 0.0004, perda: 0.1090, continua: 0.8906 }, // meio_cen
  { chute: 0.0001, perda: 0.1380, continua: 0.8619 }, // meio_dir
  { chute: 0.0140, perda: 0.2140, continua: 0.7720 }, // atq_esq
  { chute: 0.1994, perda: 0.1830, continua: 0.6176 }, // atq_cen -- zona mais decisiva do campo
  { chute: 0.0126, perda: 0.2200, continua: 0.7674 }, // atq_dir
];

// Matriz de transição condicional a MANTER a posse (Achado 15) -- cada linha
// já soma 100%, é a distribuição de "pra onde a bola vai" DADO que a ação
// não virou chute nem perda (essas duas saídas já foram sorteadas por
// TAXA_DESFECHO_POR_ZONA antes de consultar esta matriz).
//
// Dois padrões documentados no achado, úteis pra quem for mexer aqui: a
// diagonal é sempre o maior valor da linha (bola tende a ficar no próprio
// corredor, 58-80%), e toda célula defesa→ataque é ≤0,7% (a bola quase
// nunca pula direto de defesa pra ataque numa ação só -- progressão passa
// pelo meio-campo).
export const MATRIZ_TRANSICAO = [
  // de def_esq
  [0.583, 0.143, 0.017, 0.201, 0.038, 0.009, 0.006, 0.002, 0.001],
  // de def_cen
  [0.116, 0.497, 0.113, 0.077, 0.107, 0.076, 0.005, 0.004, 0.005],
  // de def_dir
  [0.016, 0.137, 0.582, 0.009, 0.039, 0.206, 0.001, 0.002, 0.007],
  // de meio_esq
  [0.039, 0.018, 0.002, 0.702, 0.103, 0.014, 0.103, 0.013, 0.007],
  // de meio_cen
  [0.008, 0.032, 0.008, 0.143, 0.568, 0.138, 0.031, 0.041, 0.031],
  // de meio_dir
  [0.002, 0.018, 0.036, 0.014, 0.109, 0.700, 0.006, 0.014, 0.102],
  // de atq_esq
  [0.000, 0.000, 0.000, 0.079, 0.014, 0.001, 0.791, 0.103, 0.012],
  // de atq_cen
  [0.000, 0.000, 0.000, 0.012, 0.050, 0.012, 0.120, 0.677, 0.129],
  // de atq_dir
  [0.000, 0.000, 0.000, 0.001, 0.013, 0.072, 0.013, 0.098, 0.802],
];

// =============================================================================
// CAMADA FINA DE CHUTE -- 14 zonas polares (dado do PRÓPRIO projeto)
// =============================================================================
// A grade 3×3 acima decide ONDE a posse termina em chute (constante externa do
// StatsBomb). Mas 3×3 é grossa demais pro chute em si: junta a pequena área com
// a entrada da grande (R² do xG médio por zona = 6%). Esta camada resolve o
// chute em 14 zonas polares centradas no gol: 7 anéis de distância × 2 setores
// de ângulo (central ≤30° do eixo do campo; aberto >30°) -- R² = 43,8%.
// Testado contra esquemas anatômicos (11/14 zonas: 34-35%) e grade uniforme
// 12×8 (30%); cruzar as zonas polares com os limites das áreas dá 44,0% com 21
// zonas (+0,2 p.p.), então os limites anatômicos entram só como linhas de
// referência no desenho, não como cortes.
//
// ORIGEM: `match_shots_fotmob` (chutes com x,y,xG; sem pênaltis, gols contra e
// disputa de pênaltis), coordenadas FotMob x∈[0,105] (gol em x=105), y∈[0,68].
// Distância = até o CENTRO do gol; ângulo = do eixo do campo. XG_MEDIO_ZONA_CHUTE
// e DISTRIBUICAO_ZONA_CHUTE são fotografias desse dado (não recalculadas em
// runtime); refazer se a base crescer muito.
export const ANEIS_CHUTE_M = [0, 6, 9, 12, 16.5, 22, 30, Infinity];
export const ANGULO_CONE_GRAUS = 30;

// Índice = anel*2 + setor (0 = central, 1 = aberto). NUNCA reordenar.
export const ZONAS_CHUTE = Array.from({ length: 14 }, (_, i) => {
  const anel = Math.floor(i / 2);
  const setor = i % 2 === 0 ? 'central' : 'aberto';
  const de = ANEIS_CHUTE_M[anel];
  const ate = ANEIS_CHUTE_M[anel + 1];
  const faixa = Number.isFinite(ate) ? `${de}–${ate} m` : `> ${de} m`;
  return { id: `a${anel}_${setor}`, anel, setor, de, ate, label: `${faixa} · ${setor}` };
});

// xG médio por chute em cada uma das 14 zonas (chutes de bola rolando e bola parada, sem pênalti).
export const XG_MEDIO_ZONA_CHUTE = [
  0.4433, 0.4043, 0.1968, 0.1624, 0.1292, 0.1183, 0.1158,
  0.0928, 0.0651, 0.0443, 0.0316, 0.0280, 0.0202, 0.0222,
];

// Dado a zona 3×3 onde a posse terminou em chute (mesmo índice de ZONAS), a
// distribuição do chute pelas 14 zonas. Cada linha soma ~1. Zonas defensivas e
// de meio raramente chutam (TAXA_DESFECHO ~0): o chute de longe cai em >30 m.
export const DISTRIBUICAO_ZONA_CHUTE = [
  /* def_esq  */ [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0],
  /* def_cen  */ [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0],
  /* def_dir  */ [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0],
  /* meio_esq */ [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.8429, 0.1571],
  /* meio_cen */ [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0],
  /* meio_dir */ [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.8717, 0.1283],
  /* atq_esq  */ [0, 0, 0, 0, 0, 0.0023, 0, 0.2092, 0, 0.3495, 0.0903, 0.2629, 0.0473, 0.0385],
  /* atq_cen  */ [0.0301, 0.0323, 0.0993, 0.0538, 0.1023, 0.0701, 0.1328, 0.1031, 0.1173, 0.0307, 0.1971, 0.0003, 0.031, 0],
  /* atq_dir  */ [0, 0, 0, 0, 0, 0.002, 0, 0.198, 0, 0.3387, 0.0867, 0.277, 0.0536, 0.0439],
];

// Sorteia um índice de `pesos` (não precisa somar exatamente 1 -- normaliza
// pelo total, tolera o arredondamento de 4 casas da fonte). Último índice é
// sempre o fallback se o RNG bater no limite (evita `undefined` por erro de
// ponto flutuante).
function sortearIndice(pesos, rng) {
  const total = pesos.reduce((a, b) => a + b, 0);
  const alvo = rng() * total;
  let acumulado = 0;
  for (let i = 0; i < pesos.length; i++) {
    acumulado += pesos[i];
    if (alvo < acumulado) return i;
  }
  return pesos.length - 1;
}

// Início de posse default: uniforme entre as 3 zonas defensivas (índices
// 0,1,2) -- aproximação de "a posse recomeça depois de tiro de meta/saída de
// bola do goleiro", não uma calibração (o Achado 15 não mediu isso
// especificamente; é a escolha mais razoável pra simular "como uma posse
// típica se desenrola", documentada aqui em vez de escondida).
const ZONAS_INICIO_DEFAULT = [0, 1, 2];

/**
 * Simula UMA posse: começa numa zona (sorteada entre `zonasInicio`, default
 * as 3 defensivas), aplica o desfecho por zona (chute/perda/continua) e, se
 * continuar, sorteia a próxima zona pela matriz de transição -- repete até
 * a posse acabar (chute ou perda) ou até `maxPasses` (guarda contra loop
 * infinito; a estrutura da matriz já torna isso extremamente raro, já que
 * toda linha tem >60% de probabilidade cumulativa de sair pra chute/perda
 * dentro de poucos passes, mas nunca é matematicamente impossível).
 *
 * @param {object} [opcoes]
 * @param {number[]} [opcoes.zonasInicio] - Índices (0-8) elegíveis pra começar a posse.
 * @param {() => number} [opcoes.rng] - Injeção de RNG (testes determinísticos); default `Math.random`.
 * @param {number} [opcoes.maxPasses] - Default 40.
 * @returns {{ desfecho: 'chute'|'perda'|'max_passes', zonaChute: number|null, zonaChuteFina: number|null, passes: number }}
 *   `zonaChute` = zona 3×3 (0-8); `zonaChuteFina` = zona polar do chute (0-13, ver ZONAS_CHUTE).
 */
export function simularPosse(opcoes = {}) {
  const { zonasInicio = ZONAS_INICIO_DEFAULT, rng = Math.random, maxPasses = 40 } = opcoes;
  let zona = zonasInicio[sortearIndice(zonasInicio.map(() => 1), rng)];

  for (let passo = 0; passo < maxPasses; passo++) {
    const { chute, perda, continua } = TAXA_DESFECHO_POR_ZONA[zona];
    const desfechoIdx = sortearIndice([chute, perda, continua], rng);
    if (desfechoIdx === 0) {
      const zonaChuteFina = sortearIndice(DISTRIBUICAO_ZONA_CHUTE[zona], rng);
      return { desfecho: 'chute', zonaChute: zona, zonaChuteFina, passes: passo + 1 };
    }
    if (desfechoIdx === 1) return { desfecho: 'perda', zonaChute: null, zonaChuteFina: null, passes: passo + 1 };
    zona = sortearIndice(MATRIZ_TRANSICAO[zona], rng);
  }
  return { desfecho: 'max_passes', zonaChute: null, zonaChuteFina: null, passes: maxPasses };
}

/**
 * Simula `nPosses` posses independentes e devolve a distribuição de zona de
 * origem do chute (só entre as posses que terminaram em chute), normalizada
 * pra somar 1 -- é o dado que alimenta o heatmap 3×3 da UI (Fase 4).
 *
 * @param {number} nPosses
 * @param {object} [opcoes] - Mesmas opções de `simularPosse`.
 * @returns {{ distribuicaoPorZona: number[], distribuicaoPorZonaFina: number[], taxaChutePorPosse: number, nPosses: number }}
 */
export function simularOrigemChutes(nPosses, opcoes = {}) {
  const contagem = ZONAS.map(() => 0);
  const contagemFina = ZONAS_CHUTE.map(() => 0);
  let totalChutes = 0;
  for (let i = 0; i < nPosses; i++) {
    const { desfecho, zonaChute, zonaChuteFina } = simularPosse(opcoes);
    if (desfecho === 'chute') {
      contagem[zonaChute]++;
      contagemFina[zonaChuteFina]++;
      totalChutes++;
    }
  }
  return {
    distribuicaoPorZona: contagem.map(c => (totalChutes > 0 ? c / totalChutes : 0)),
    distribuicaoPorZonaFina: contagemFina.map(c => (totalChutes > 0 ? c / totalChutes : 0)),
    taxaChutePorPosse: nPosses > 0 ? totalChutes / nPosses : 0,
    nPosses,
  };
}

// =============================================================================
// AJUSTE POR FORÇA DE TIME -- FUNÇÃO DISPONÍVEL, NUNCA APLICADA POR PADRÃO.
// =============================================================================
// Achado 17: a diferença entre time forte e fraco está em RETER A POSSE, não
// em como a bola se move quando continua -- a matriz de transição em si é
// tratada como praticamente universal; só a taxa de perda por zona precisa
// variar por força. `match_stats_fotmob` tem posse/`touches_opp_box` que
// poderiam alimentar um fator de ajuste real (por time, por confronto), mas
// isso NUNCA foi validado com dado do PRÓPRIO projeto, controlado por Elo --
// é exatamente a armadilha de confusão por força de equipe já documentada 3x
// neste projeto (game state, resposta a evento, qualidade por chute: ver
// CLAUDE.md "Estado do jogo"). Enquanto essa validação exploratória (SQL
// direto, `v_game_state_por_forca`-style) não confirmar que o efeito
// sobrevive, esta função fica disponível mas fora do caminho de simulação
// (`simularPosse`/`simularOrigemChutes` NUNCA a chamam).
//
// `fatorPerda`: multiplicador sobre `perda` de cada zona (>1 = perde mais,
// <1 = perde menos); o delta é redistribuído em `continua` pra manter a
// linha somando 1. `fatorPerda=1` é no-op (devolve as taxas originais).
export function ajustarPerdaPorForca(taxaDesfechoPorZona, fatorPerda) {
  if (fatorPerda === 1) return taxaDesfechoPorZona;
  return taxaDesfechoPorZona.map(({ chute, perda, continua }) => {
    const perdaAjustada = Math.min(Math.max(perda * fatorPerda, 0), 1 - chute);
    return { chute, perda: perdaAjustada, continua: 1 - chute - perdaAjustada };
  });
}
