// src/utils/classificarAposta.js
// Classifica uma aposta (modelo/mercado/probabilidade/odd real) contra a
// matriz de confiabilidade odd x EV (`matriz_confiabilidade_ev_historico`,
// atualizada diariamente por scripts/matriz_confiabilidade_ev.py, ver
// CLAUDE.md/CONTEXTO_PROJETO.md "ACHADO REFORÇADO 19/09" e a reanálise de
// 20/09 pós-fix de cartões) -- pedido do usuário depois de conferir na mão
// se uma odd real de uma casa específica (ex.: Betano) cai ou não na faixa
// odd x edge que já se mostrou confiável (sobrevive Bonferroni/FDR) pra
// aquele modelo/mercado, em vez de reconferir a matriz de cabeça toda vez.
//
// ESCOPO DELIBERADO (decisão do usuário, 20/09): só o critério GERAL da
// matriz (odd x edge x confiável), reutilizável pra qualquer modelo/mercado
// que ela cobre (gols, escanteios, cartões, catboost_v9) -- NÃO embute a
// restrição extra de liga/casa de aposta descoberta nesta sessão pro
// cartoes_rf (Brasileirão Série B + bet365/betano), porque essa restrição
// é específica desse modelo e não está persistida em nenhuma tabela ainda.
// Quem usar esta função pra cartoes_rf deve considerar essa ressalva à
// parte (ver AVISO na UI que consome isto).

// Sentinelas usadas por matriz_confiabilidade_ev.py pra persistir a faixa
// "sem teto" (última de cada grade) sem NULL -- ver comentário da coluna na
// migration 20260919120000_create_matriz_confiabilidade_ev_historico.sql.
const ODD_MAX_SENTINELA = 999.0;
const EDGE_MAX_SENTINELA = 9.99;

/** edge = p_modelo - probabilidade implícita da odd (sem devig) -- MESMA
 * convenção de scripts/matriz_confiabilidade_ev.py (`aposta["edge"] =
 * aposta["prob_modelo"] - (1 / aposta["odd"])`), não confundir com o edge
 * devigado (p_modelo - p_mercado_devigado) que outras partes do app usam. */
export function calcularEdgeMatriz(probModelo, oddReal) {
  if (!(probModelo > 0) || !(oddReal > 1)) return null;
  return probModelo - 1 / oddReal;
}

/** Busca o snapshot mais recente (maior `data_execucao`) da matriz inteira
 * -- uma linha por (modelo, mercado, faixa de odd, faixa de edge) com
 * n>=50 (células com menos amostra nem são persistidas, ver
 * MIN_N_CELULA em matriz_confiabilidade_ev.py). Chamar uma vez por
 * carregamento de página e reaproveitar via `classificarComMatriz`. */
export async function buscarMatrizConfiabilidadeAtual(supabase) {
  const { data, error } = await supabase
    .from('matriz_confiabilidade_ev_historico')
    .select('data_execucao, modelo, mercado, odd_min, odd_max, edge_min, edge_max, n, roi_medio, roi_medio_ic_inf, roi_medio_ic_sup, roi_mediano, roi_mediano_ic_inf, roi_mediano_ic_sup, confiavel, bonferroni_significativo, fdr_significativo, carteira_banca_final_x, carteira_drawdown_maximo, carteira_n_apostado')
    .order('data_execucao', { ascending: false });
  if (error || !data || data.length === 0) return [];
  const maisRecente = data[0].data_execucao;
  return data.filter((linha) => linha.data_execucao === maisRecente);
}

/** Lista os pares (modelo, mercado) distintos presentes na matriz -- pra
 * popular os seletores da Calculadora de Aposta sem hardcoded. */
export function listarModelosMercados(linhasMatriz) {
  const porModelo = {};
  for (const linha of linhasMatriz) {
    (porModelo[linha.modelo] = porModelo[linha.modelo] || new Set()).add(linha.mercado);
  }
  return Object.fromEntries(Object.entries(porModelo).map(([modelo, mercados]) => [modelo, [...mercados].sort()]));
}

const NIVEL_ROTULO = {
  bonferroni: 'Confiável (sobrevive à correção de Bonferroni)',
  fdr: 'Confiável (sobrevive à correção FDR/Benjamini-Hochberg)',
  fraco: 'Confiável pelo critério simples, mas NÃO sobrevive à correção múltipla',
  nao_confiavel: 'Não confiável (IC95% cruza zero, ou mediana não confirma)',
  sem_dado: 'Sem célula avaliada nessa combinação de odd/edge (amostra insuficiente, edge abaixo de 2%, ou modelo/mercado fora do escopo da matriz)',
};

/** Classifica (modelo, mercado, probModelo, oddReal) contra as linhas já
 * buscadas por `buscarMatrizConfiabilidadeAtual`. Não faz I/O -- pura,
 * pra poder rodar por linha numa tabela sem uma query por linha. */
export function classificarComMatriz(linhasMatriz, modelo, mercado, probModelo, oddReal) {
  const edge = calcularEdgeMatriz(probModelo, oddReal);
  if (edge == null) return { edge: null, nivel: 'sem_dado', rotulo: NIVEL_ROTULO.sem_dado, celula: null };

  const celula = (linhasMatriz || []).find((l) => {
    if (l.modelo !== modelo || l.mercado !== mercado) return false;
    const oddMax = Number(l.odd_max) >= ODD_MAX_SENTINELA ? Infinity : Number(l.odd_max);
    const edgeMax = Number(l.edge_max) >= EDGE_MAX_SENTINELA ? Infinity : Number(l.edge_max);
    return oddReal >= Number(l.odd_min) && oddReal < oddMax && edge >= Number(l.edge_min) && edge < edgeMax;
  });

  if (!celula) return { edge, nivel: 'sem_dado', rotulo: NIVEL_ROTULO.sem_dado, celula: null };

  let nivel = 'nao_confiavel';
  if (celula.confiavel) {
    nivel = celula.bonferroni_significativo ? 'bonferroni' : celula.fdr_significativo ? 'fdr' : 'fraco';
  }
  return { edge, nivel, rotulo: NIVEL_ROTULO[nivel], celula };
}
