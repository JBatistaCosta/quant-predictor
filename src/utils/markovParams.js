// src/utils/markovParams.js
// Carrega e indexa `league_markov_params` (Fase 1 do motor de Markov
// multi-evento) pra uso por `src/utils/markovEngine.js`. Replica — não
// importa — o mesmo PADRÃO de fallback já usado em `dispRDaLiga`/
// `statEsperado` (`api/corners-model.js`): 1 query pela liga do confronto +
// 1 query de fallback global (`league_id is null`), com prioridade pro valor
// específico da liga quando presente. Não dá pra importar código de servidor
// no client, então o padrão é replicado, não compartilhado.
//
// Função simples (não hook `useMarkovParams`): o carregamento roda dentro do
// mesmo `useEffect` de auto-load que já busca chutes/chutes-no-alvo/cartões
// em `AnaliseEvento.jsx` (mesmo `Promise.all`), evitando mais um efeito com
// sua própria condição de corrida pra sincronizar com o resto.

const PAGINA = 1000;

// Pagina com `.range()` em loop -- disciplina do projeto (`.select()` sem
// `.range()` corta em 1000 linhas silenciosamente, CLAUDE.md). A query por
// liga tem poucas centenas de linhas hoje (7 eventos × ~4 tipos × poucos
// buckets), bem abaixo do corte, mas a query de fallback global agrega
// TODAS as ligas que só têm fallback -- pode crescer, então pagina mesmo
// assim por disciplina/futuro-proofing.
async function buscarPaginado(query) {
  const linhas = [];
  let inicio = 0;
  for (;;) {
    const { data, error } = await query.range(inicio, inicio + PAGINA - 1);
    if (error) throw error;
    linhas.push(...(data || []));
    if (!data || data.length < PAGINA) break;
    inicio += PAGINA;
  }
  return linhas;
}

// Insere uma linha `{evento,tipo,chave,valor}` no índice, sem sobrescrever
// um valor que já esteja lá (usado pra dar prioridade à liga específica
// sobre o fallback global, ao inserir a liga primeiro).
function indexar(destino, linhas) {
  for (const linha of linhas) {
    destino[linha.evento] ??= {};
    destino[linha.evento][linha.tipo] ??= {};
    if (!(linha.chave in destino[linha.evento][linha.tipo])) {
      destino[linha.evento][linha.tipo][linha.chave] = Number(linha.valor);
    }
  }
}

/**
 * Carrega os parâmetros calibrados de `league_markov_params` pra uma liga,
 * mesclando com o fallback global (`league_id is null`) — prioridade pro
 * valor específico da liga quando presente.
 *
 * @param {import('@supabase/supabase-js').SupabaseClient|null} supabase
 * @param {number|null} leagueId
 * @returns {Promise<object>} `{ [evento]: { [tipo]: { [chave]: number } } }` -- objeto vazio se `supabase` for null (sem calibração — motor cai pra multiplicadores neutros) ou se a query falhar.
 */
export async function carregarMarkovParams(supabase, leagueId) {
  if (!supabase) return {};

  const indice = {};
  try {
    if (leagueId) {
      const linhasLiga = await buscarPaginado(
        supabase.from('league_markov_params').select('evento, tipo, chave, valor').eq('league_id', leagueId)
      );
      indexar(indice, linhasLiga);
    }
    const linhasGlobais = await buscarPaginado(
      supabase.from('league_markov_params').select('evento, tipo, chave, valor').is('league_id', null)
    );
    indexar(indice, linhasGlobais);
  } catch {
    // Sem calibração carregada, o motor cai pros multiplicadores neutros
    // (=1, ver `getMultiplicador` em markovEngine.js) -- degradação
    // silenciosa proposital, mesmo espírito de `supabaseAtivo` no resto do
    // app (falha de rede/config não deve travar a calculadora).
    return {};
  }
  return indice;
}
