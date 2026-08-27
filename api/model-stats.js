// api/model-stats.js
// Roda no SERVIDOR do Vercel. Variáveis de ambiente necessárias:
//   SUPABASE_URL / SUPABASE_KEY  -> mesmas do team-stats.js (leitura pública, RLS)
//
// Painel de estatísticas dos modelos de previsão (model_predictions): calcula
// Brier Score, log-loss e acurácia (taxa de acerto do favorito do modelo) do
// modelo contra o resultado real de cada partida, e compara com a odd de
// fechamento MÉDIA do mercado (bookmaker='media_mercado', snapshot='closing'
// em odds_market — a mais precisa disponível, já devigada aqui dentro pra
// virar probabilidade). Escanteios não têm odds no banco (só existe histórico
// de odds de gols), então nesse mercado só sai a métrica do modelo, sem
// comparação.
//
// A odd de FECHAMENTO da Pinnacle (bookmaker='pinnacle') entra também como um
// "modelo" sintético próprio (model_name='mercado_pinnacle_devigado', ver
// normalizarPinnacleDevigada) — devigada pelo mesmo método Odds Ratio usado
// no resto do arquivo, passa pelo MESMO pipeline de log-loss/Brier/acurácia/
// log-verossimilhança/calibração em quintis que qualquer modelo real, em
// qualquer mercado/liga com odds da Pinnacle disponíveis. Serve pra tratar a
// Pinnacle devigada como referência de "mercado eficiente" com as mesmas
// métricas de qualidade probabilística usadas nos modelos do projeto, não só
// como comparação de edge.
//
// Também aplica a calibração salva em model_calibration (Platt Scaling e
// Isotonic Regression, ajustados por api/fit-calibration.js) quando existe
// pra aquele model_name+market+selection, devolvendo as métricas COM e SEM
// ajuste lado a lado — pra decidir se vale a pena aplicar a calibração em
// produção ou não, por mercado/liga.
//
// Agrupa por liga (matches.league_id) — filtros opcionais na URL.
//
// Também anexa (quando existe) o IC95% por bootstrap de log_loss/accuracy de
// `model_stats_ic` (migration 20260827063903), populada sob demanda por
// `scripts/avaliar_ic_modelos_por_liga.py` — responde "essa diferença entre
// modelos no ranking por liga é real ou é ruído de amostra pequena?" (achado
// #27, CONTEXTO_PROJETO.md). Ausente (amostra <30 partidas, ou script nunca
// rodado pra esse combo) vira `null` nos 4 campos `log_loss_ic_*`/
// `accuracy_ic_*`, tratado como "IC não calculado" pelo front.
//
// COMO CHAMAR:
//   /api/model-stats                                  (tudo)
//   /api/model-stats?modelo=dixon_coles_v1&mercado=1X2&liga_id=4

import { createClient } from '@supabase/supabase-js';
import { applyCors } from './_lib/cors.js';

function getSupabase() {
  return createClient(process.env.SUPABASE_URL, process.env.SUPABASE_KEY);
}

const clamp = (p) => Math.min(Math.max(p, 1e-4), 1 - 1e-4);
const logit = (p) => Math.log(clamp(p) / (1 - clamp(p)));
const sigmoid = (x) => 1 / (1 + Math.exp(-x));
const logLossTermo = (p, y) => (y ? -Math.log(clamp(p)) : -Math.log(1 - clamp(p)));
const brierTermo = (p, y) => (p - y) ** 2;

function aplicarPlatt(p, coef, intercept) {
  return sigmoid(coef * logit(p) + intercept);
}

function aplicarIsotonic(p, xs, ys) {
  if (!xs || xs.length === 0) return null;
  if (p <= xs[0]) return ys[0];
  if (p >= xs[xs.length - 1]) return ys[ys.length - 1];
  for (let i = 0; i < xs.length - 1; i++) {
    if (p >= xs[i] && p <= xs[i + 1]) {
      const t = (p - xs[i]) / (xs[i + 1] - xs[i] || 1);
      return ys[i] + t * (ys[i + 1] - ys[i]);
    }
  }
  return p;
}

// Resultado real de cada partida, por mercado — indexado pela MESMA
// string usada em odds_market.market/model_predictions.market. Antes
// disso era um ternário de 3 opções (chaveMercado) que jogava QUALQUER
// mercado desconhecido (btts, dupla_chance, handicap etc) no bucket de
// escanteios O/U 9,5 -- corrigido aqui e em api/backtest-betting.js
// (mesma duplicação, mesmo bug). Mercado sem entrada aqui agora fica
// undefined em vez de comparar contra o resultado errado.
function calcularResultadosReais(matches, corners) {
  const porMatch = {};
  for (const m of matches) {
    if (m.status !== 'finished' || m.home_goals == null || m.away_goals == null) continue;
    const total = m.home_goals + m.away_goals;
    porMatch[m.id] = {
      league_id: m.league_id,
      '1X2': m.home_goals > m.away_goals ? 'home' : m.home_goals < m.away_goals ? 'away' : 'draw',
      'over_under_2.5': total > 2.5 ? 'over' : 'under',
      btts: (m.home_goals > 0 && m.away_goals > 0) ? 'yes' : 'no',
    };
  }
  for (const [matchId, totalCorners] of Object.entries(corners)) {
    if (porMatch[matchId]) porMatch[matchId]['corners_over_under_9.5'] = totalCorners > 9.5 ? 'over' : 'under';
  }
  return porMatch;
}

// Devigging: remove a margem da casa das odds. Dois métodos, ambos derivados
// da mesma família de fórmulas (ver true_odds_calculator.xlsm, planilha de
// referência): "Odds Ratio" (Cheung) resolve c em
// sum(q_i / (c + q_i - c*q_i)) = 1, onde q_i = 1/odd_i (prob. implícita
// bruta); "Logarithmic function" (power) resolve c em sum(odd_i^c) = 1 e usa
// odd_i^c direto como probabilidade. A planilha só tem fórmula fechada pra
// 2/3 seleções (quadrática/cúbica via Cardano) — aqui é bissecção genérica,
// que resolve pra qualquer N e bate com a planilha até a 10ª casa decimal
// (validado manualmente). Substitui a normalização simples (implícita/soma)
// usada antes, que não corrige a distorção de margem entre favorito e zebra.
function resolverParametroDevig(g) {
  const g0 = g(0);
  if (Math.abs(g0) < 1e-14) return 0;
  let tHi = 0, gHi = g0;
  while (gHi > 0) {
    tHi = tHi === 0 ? 1 : tHi * 2;
    gHi = g(tHi);
    if (tHi > 1e15) break; // odds degeneradas (<=1) não deveriam chegar aqui
  }
  let tLo = 0, gLo = g0;
  for (let i = 0; i < 200; i++) {
    const tMid = (tLo + tHi) / 2;
    const gMid = g(tMid);
    if (Math.abs(gMid) < 1e-12) return tMid;
    if ((gMid > 0) === (gLo > 0)) { tLo = tMid; gLo = gMid; } else { tHi = tMid; }
  }
  return (tLo + tHi) / 2;
}

function devigarOddsRatio(oddsPorSelecao) {
  const selecoes = Object.keys(oddsPorSelecao);
  const q = selecoes.map((s) => 1 / oddsPorSelecao[s]);
  const t = resolverParametroDevig((x) => {
    const c = 1 + x;
    return q.reduce((acc, qi) => acc + qi / (c + qi - c * qi), 0) - 1;
  });
  const c = 1 + t;
  const normalizadas = {};
  selecoes.forEach((s, i) => { normalizadas[s] = q[i] / (c + q[i] - c * q[i]); });
  return normalizadas;
}

function devigarLogaritmico(oddsPorSelecao) {
  const selecoes = Object.keys(oddsPorSelecao);
  const odds = selecoes.map((s) => oddsPorSelecao[s]);
  const t = resolverParametroDevig((x) => odds.reduce((acc, o) => acc + Math.pow(o, -x), 0) - 1);
  const c = -t;
  const normalizadas = {};
  selecoes.forEach((s, i) => { normalizadas[s] = Math.pow(odds[i], c); });
  return normalizadas;
}

// Padrão Odds Ratio (método mais citado na literatura pra devig de odds
// esportivas); passe metodo='logaritmico' pra usar o outro.
function devigar(oddsPorSelecao, metodo = 'odds_ratio') {
  return metodo === 'logaritmico' ? devigarLogaritmico(oddsPorSelecao) : devigarOddsRatio(oddsPorSelecao);
}

// Odds de FECHAMENTO da Pinnacle (referência de "linha eficiente", menor
// margem do mercado) tratadas como se fossem previsões de um modelo próprio
// (`model_name='mercado_pinnacle_devigado'`) -- devigadas pelo mesmo método
// Odds Ratio já padrão neste arquivo, entram no MESMO pipeline de
// agrupamento/log-loss/Brier/acurácia/calibração em quintis usado pros
// modelos reais, então log-verossimilhança, Diagrama de Confiabilidade etc.
// saem de graça pra qualquer mercado/liga com odds da Pinnacle -- sem
// duplicar cálculo. Só markets com conjunto de seleções fechado conhecido
// (não dá pra devigar um conjunto parcial sem viés); escanteios não têm odds
// no banco, então não geram linha aqui (mesma limitação já documentada pro
// resto do painel).
const MERCADO_SELECOES_PINNACLE = {
  '1X2': ['home', 'draw', 'away'],
  'over_under_2.5': ['over', 'under'],
  btts: ['yes', 'no'],
};

function normalizarPinnacleDevigada(oddsRows) {
  const porChave = {};
  oddsRows.forEach((r) => {
    const chave = `${r.match_id}__${r.market}`;
    if (!porChave[chave]) porChave[chave] = {};
    porChave[chave][r.selection] = Number(r.odds);
  });
  const linhas = [];
  Object.entries(porChave).forEach(([chave, oddsSel]) => {
    const [matchIdStr, market] = chave.split('__');
    const selecoesEsperadas = MERCADO_SELECOES_PINNACLE[market];
    if (!selecoesEsperadas || !selecoesEsperadas.every((s) => oddsSel[s] != null)) return;
    const probs = devigar(oddsSel);
    selecoesEsperadas.forEach((s) => {
      linhas.push({ model_name: 'mercado_pinnacle_devigado', market, selection: s, probability: probs[s], match_id: Number(matchIdStr) });
    });
  });
  return linhas;
}

// v9 gravou '1x2' (minúscula) em alguns pontos — normaliza pro mesmo
// mercado antes de indexar `resultadosReais`.
function normalizarMercado(m) {
  return m === '1x2' ? '1X2' : m;
}

// O Supabase (PostgREST) devolve no máximo 1000 linhas por chamada, mesmo sem
// LIMIT explícito no .select() — sem paginar de verdade, qualquer tabela/junção
// com mais de 1000 linhas vem cortada em silêncio (foi um bug real aqui: sem
// filtro, model_predictions tem 22k+ linhas e vinha só 1/22 do dado).
// Recebe uma FÁBRICA de query (não a query já construída) — cada página
// precisa de uma instância nova do builder, reaproveitar a mesma após
// executada não é seguro no supabase-js.
//
// Páginas são buscadas em LOTES PARALELOS (não uma de cada vez): `.range()`
// endereça um offset absoluto, então a página N não depende da N-1 terminar.
// Achado real em produção: com `odds_market` já em milhões de linhas, uma
// busca de ~32 mil linhas da Pinnacle (33 páginas) ou ~23 mil da média de
// mercado (24 páginas) rodando uma página de cada vez soma dezenas de
// segundos só de round-trip (cada página individual é rápida no Postgres,
// ~tempo de rede é o gargalo) -- fazia `?modelo=mercado_pinnacle_devigado`
// estourar o `statement_timeout`/`maxDuration` de forma intermitente
// (funcionava às vezes, dependendo da carga do momento). Buscar em lotes de
// `CONCORRENCIA_PAGINACAO` páginas por vez, em paralelo, corta esse tempo
// pelo mesmo fator.
const CONCORRENCIA_PAGINACAO = 8;

// Buscar 8 páginas em paralelo (ver comentário acima) tem uma consequência
// real, testada em produção: se UMA das 8 esbarrar num `statement_timeout`
// pontual do Postgres (contenção passageira, não um erro de verdade -- as
// outras 7 do mesmo lote passam normal), `Promise.all` propaga o erro e
// derruba o endpoint inteiro mesmo a chamada geral tendo terminado rápido
// (achado: `500` em só 9s, bem abaixo do `maxDuration`). Retry curto SÓ na
// página que falhou, não no lote inteiro -- barato (mesmo offset, mesma
// query) e cobre a variância pontual sem esconder um erro de verdade
// (schema/permissão continuam falhando depois das tentativas).
async function buscarPaginaComRetry(criarQuery, inicio, fim, colunasOrdem, tentativas = 3) {
  let ultimoErro;
  for (let i = 0; i < tentativas; i++) {
    // ORDER BY é OBRIGATÓRIO aqui, não só estilo -- sem ele o Postgres não
    // garante NENHUMA ordem estável de linha entre chamadas de `.range()`
    // (OFFSET/LIMIT) separadas, e as `CONCORRENCIA_PAGINACAO` páginas de um
    // lote são disparadas como consultas INDEPENDENTES em paralelo -- sem
    // ordenação, cada uma pode escolher um plano/ordem de varredura
    // ligeiramente diferente, produzindo linha duplicada em mais de uma
    // "página" (e outra pulada). Achado real em produção rodando o painel
    // filtrado por model_name+market: `model_predictions` tinha exatamente
    // 842 linhas (421 partidas × 2 seleções) pra hibrido_gols_v1/
    // corners_over_under_9.5 na liga 1 (conferido via SQL direto), mas o
    // endpoint devolvia 1538 (770+768) -- quase o dobro, inflando o
    // denominador de acurácia/log-loss e derrubando a acurácia reportada
    // pra 13,5% (o valor real, batendo com o `model_stats_resumo`
    // pré-calculado via SQL, é ~51%).
    //
    // `colunasOrdem` é OBRIGATÓRIO no caller (não tem default aqui de
    // propósito) -- `id` sozinho é seguro em qualquer tabela (é a PK), mas
    // pra `model_predictions`/`odds_market`/`match_stats` filtradas pelas
    // colunas líderes dos índices reais (achado #19, migration
    // `20260824213000_indice_odds_market_...`) ordenar por `id` faz o
    // planner IGNORAR esse índice e varrer em ordem de `id` (mesmo achado
    // de `rodar_xi_previsto.py`: ORDER BY desalinhado do índice de filtro
    // vira quase full-scan) -- por isso os callers dessas tabelas passam
    // `['match_id', 'id']` (alinha com o índice E garante ordem 100%
    // estável via o `id` como desempate, já que `match_id` sozinho repete
    // entre seleções/bookmakers).
    let query = criarQuery();
    for (const coluna of colunasOrdem) query = query.order(coluna);
    const { data, error } = await query.range(inicio, fim);
    if (!error) return data;
    ultimoErro = error;
    if (i < tentativas - 1) await new Promise((r) => setTimeout(r, 300 * (i + 1)));
  }
  throw ultimoErro;
}

async function buscarTudoPaginado(criarQuery, colunasOrdem = ['id']) {
  const TAMANHO_PAGINA = 1000;
  const resultado = [];
  let pagina = 0;
  let acabou = false;
  while (!acabou) {
    const paginasDoLote = Array.from({ length: CONCORRENCIA_PAGINACAO }, (_, i) => pagina + i);
    const respostas = await Promise.all(
      paginasDoLote.map((p) => buscarPaginaComRetry(criarQuery, p * TAMANHO_PAGINA, p * TAMANHO_PAGINA + TAMANHO_PAGINA - 1, colunasOrdem))
    );
    for (const data of respostas) {
      resultado.push(...(data || []));
      if (!data || data.length < TAMANHO_PAGINA) acabou = true;
    }
    pagina += CONCORRENCIA_PAGINACAO;
  }
  return resultado;
}

// KEYSET (cursor composto), não OFFSET -- pra tabelas GRANDES filtradas
// pelas colunas líderes de um índice real (`model_predictions` por
// model_name+market, achado #19; `odds_market` por market+snapshot+
// bookmaker) onde a consulta varre o índice inteiro, não uma fatia
// pequena. Achado real testando em produção logo depois do fix de
// `.order()` acima: adicionar `ORDER BY match_id, id` a essas consultas
// corrigiu a duplicação, mas fez `?modelo=hibrido_gols_v1&mercado=
// corners_over_under_9.5` estourar `statement_timeout` (500 em 30s) --
// forçar ordem de verdade reabriu o mesmo problema já resolvido em Python
// nesta sessão (`avaliar_modelo_misto_vs_mercado._carregar_predicoes`) e
// em `rodar_xi_previsto.py`: OFFSET profundo custa O(offset) mesmo com o
// índice certo, porque o Postgres ainda precisa pular N linhas por
// página, e a versão SEM ordenação só parecia rápida porque cada
// `.range()` conseguia escapar sem varrer de verdade (ao custo de
// resultado errado). Cursor composto `(match_id, id)` via
// `.or("match_id.gt.X,and(match_id.eq.X,id.gt.Y)")` vira um predicado de
// índice em vez de "pular N linhas" -- custo ~constante por página,
// independente da profundidade. Sequencial (não paralelo como
// `buscarTudoPaginado`) porque o cursor da página N depende da última
// linha da página N-1 -- aceitável aqui (poucas dezenas de páginas nas
// consultas que usam isto, não milhares).
async function buscarTudoPaginadoKeyset(criarQuery, colunasOrdem) {
  const TAMANHO_PAGINA = 1000;
  const resultado = [];
  let cursor = null;
  while (true) {
    let query = criarQuery();
    for (const coluna of colunasOrdem) query = query.order(coluna);
    query = query.limit(TAMANHO_PAGINA);
    if (cursor !== null) {
      const clausulas = colunasOrdem.map((coluna, i) => {
        const partes = colunasOrdem.slice(0, i).map((c, j) => `${c}.eq.${cursor[j]}`);
        partes.push(`${coluna}.gt.${cursor[i]}`);
        return partes.length > 1 ? `and(${partes.join(',')})` : partes[0];
      });
      query = query.or(clausulas.join(','));
    }
    let data, error;
    for (let tentativa = 0; tentativa < 3; tentativa++) {
      ({ data, error } = await query);
      if (!error) break;
      if (tentativa < 2) await new Promise((r) => setTimeout(r, 300 * (tentativa + 1)));
    }
    if (error) throw error;
    resultado.push(...(data || []));
    if (!data || data.length < TAMANHO_PAGINA) break;
    const ultimo = data[data.length - 1];
    cursor = colunasOrdem.map((c) => ultimo[c]);
  }
  return resultado;
}

// Normaliza as saídas do pipeline "Model Benchmarking" (`predicoes`/
// `market_odds`, ver scripts/rodar_predicoes.py) pro MESMO formato usado
// pelo pipeline mais antigo (`model_predictions`/`odds_market`, uma linha
// por seleção) -- assim o resto deste arquivo (agrupamento, log-loss,
// Brier, acurácia, calibração em quintis) funciona idêntico pros dois
// pipelines sem duplicar lógica de cálculo, só a normalização de formato.
// `predicoes` só cobre 1X2 (não tem Over/Under 2.5 salvo por partida, só
// o agregado do backtest em model_benchmarking_backtest) -- variantes
// calibradas (`_calibrado_platt`/`_calibrado_isotonic`) já entram como
// `model_name` PRÓPRIO (a probabilidade na linha já É a calibrada), por
// isso não passam pelo mesmo cruzamento com `model_calibration` que os
// modelos do pipeline antigo passam mais abaixo.
function normalizarPredicoesBenchmarking(rows) {
  const linhas = [];
  for (const r of rows) {
    linhas.push({ model_name: r.model_name, market: '1X2', selection: 'home', probability: Number(r.prob_home), match_id: r.match_id });
    linhas.push({ model_name: r.model_name, market: '1X2', selection: 'draw', probability: Number(r.prob_draw), match_id: r.match_id });
    linhas.push({ model_name: r.model_name, market: '1X2', selection: 'away', probability: Number(r.prob_away), match_id: r.match_id });
  }
  return linhas;
}

// `market_odds` é uma linha por (match_id, bookmaker) -- consensus de
// mercado equivalente ao `bookmaker='media_mercado'` do pipeline antigo é
// a MÉDIA das odds de todas as casas capturadas por partida (mesmo
// espírito, dado diferente: lá é uma linha só pré-calculada, aqui calcula
// na hora a partir de várias linhas por bookmaker).
function normalizarOddsBenchmarking(rows) {
  const somaPorMatch = {};
  for (const r of rows) {
    const acc = somaPorMatch[r.match_id] || { home: 0, draw: 0, draw_n: 0, away: 0, n: 0 };
    acc.home += Number(r.odd_home);
    acc.away += Number(r.odd_away);
    acc.n += 1;
    if (r.odd_draw != null) { acc.draw += Number(r.odd_draw); acc.draw_n += 1; }
    somaPorMatch[r.match_id] = acc;
  }
  const linhas = [];
  for (const [matchId, acc] of Object.entries(somaPorMatch)) {
    if (acc.n === 0) continue;
    linhas.push({ match_id: Number(matchId), market: '1X2', selection: 'home', odds: acc.home / acc.n });
    linhas.push({ match_id: Number(matchId), market: '1X2', selection: 'away', odds: acc.away / acc.n });
    if (acc.draw_n > 0) linhas.push({ match_id: Number(matchId), market: '1X2', selection: 'draw', odds: acc.draw / acc.draw_n });
  }
  return linhas;
}

// --- XI titular previsto (scripts/rodar_xi_previsto.py) ---------------------
// Acurácia do pipeline de XI contra a escalação REAL (match_lineup_fotmob),
// por liga/temporada/versão de modelo. Fonte de dado totalmente separada do
// resto deste arquivo (não é resultado de partida nem odds) -- dividido por
// query param em vez de arquivo próprio porque api/*.js já está no teto de
// 12 serverless functions do plano Hobby (ver skill workflow-quant-predictor).
//
// Mesma definição de precisao_media_top11/taxa_xi_exato já usada em
// scripts/treinar_modelo_xi._metricas_top11 (acertos/11 por (match,time);
// "exato" = o SET de 11 previstos bate com o SET de 11 reais) -- só que aqui
// contra a escalação real de PARTIDAS JÁ JOGADAS em produção, não um split
// de teste no treino. brier/log_loss usam prob_titular (contínua) contra
// is_starter (0/1) de CADA jogador do elenco avaliado, não só o top-11 --
// mede o quão bem calibrada é a probabilidade, não só o ranking.

// `xi_previsto` cresce todo dia (previsão nova sobrescreve/soma a cada
// rodada de scripts/rodar_xi_previsto.py) -- `buscarTudoPaginado` genérico
// pagina por OFFSET sem ORDER BY, que já deu `statement timeout` em
// produção aqui (achado idêntico ao das tabelas grandes em
// rodar_xi_previsto.py: custo do OFFSET cresce com a profundidade da
// página). Keyset por `id` (chave primária, monotônica) resolve com custo
// constante por página, igual ao fix aplicado lá.
async function buscarXiPrevistoCompleto(supabase) {
  const TAMANHO_PAGINA = 1000;
  const resultado = [];
  let cursor = 0;
  while (true) {
    const { data, error } = await supabase
      .from('xi_previsto')
      .select('id, match_id, team_id, player_id, prob_titular, is_titular_previsto, model_version')
      .gt('id', cursor)
      .order('id')
      .limit(TAMANHO_PAGINA);
    if (error) throw error;
    resultado.push(...(data || []));
    if (!data || data.length < TAMANHO_PAGINA) break;
    cursor = data[data.length - 1].id;
  }
  return resultado;
}

async function calcularStatsXi(supabase) {
  const previsoes = await buscarXiPrevistoCompleto(supabase);
  if (previsoes.length === 0) return [];

  const matchIds = [...new Set(previsoes.map((p) => p.match_id))];
  const lotes = [];
  for (let i = 0; i < matchIds.length; i += 1000) lotes.push(matchIds.slice(i, i + 1000));

  const [matchesRows, lineupRows] = await Promise.all([
    Promise.all(lotes.map((l) => buscarTudoPaginado(() => supabase.from('matches').select('id, league_id, season, status').in('id', l)))).then((r) => r.flat()),
    Promise.all(lotes.map((l) => buscarTudoPaginadoKeyset(() => supabase.from('match_lineup_fotmob').select('id, match_id, team_id, player_id, is_starter').in('match_id', l), ['match_id', 'id']))).then((r) => r.flat()),
  ]);

  const matchPorId = {};
  matchesRows.forEach((m) => { matchPorId[m.id] = m; });
  const realPorChave = {};
  lineupRows.forEach((l) => { realPorChave[`${l.match_id}__${l.team_id}__${l.player_id}`] = !!l.is_starter; });

  // Agrupa por (match_id, team_id) -- só partidas já finalizadas e com
  // escalação real capturada pra pelo menos os jogadores previstos.
  const porGrupoTime = {};
  for (const p of previsoes) {
    const match = matchPorId[p.match_id];
    if (!match || match.status !== 'finished' || match.league_id == null) continue;
    const chaveReal = `${p.match_id}__${p.team_id}__${p.player_id}`;
    if (!(chaveReal in realPorChave)) continue;
    const chaveGrupo = `${p.match_id}__${p.team_id}`;
    if (!porGrupoTime[chaveGrupo]) {
      porGrupoTime[chaveGrupo] = { matchId: p.match_id, model_version: p.model_version, league_id: match.league_id, season: match.season, linhas: [] };
    }
    porGrupoTime[chaveGrupo].linhas.push({
      player_id: p.player_id,
      prob: Number(p.prob_titular),
      previsto: !!p.is_titular_previsto,
      real: realPorChave[chaveReal],
    });
  }

  const agregados = {};
  Object.values(porGrupoTime).forEach((g) => {
    // Elenco avaliado incompleto (menos de 11 candidatos com escalação real
    // conhecida) não dá pra julgar um top-11 de verdade -- mesma guarda de
    // _metricas_top11 no treino.
    if (g.linhas.length < 11 || !g.linhas.some((l) => l.real)) return;

    const chave = `${g.model_version}__${g.league_id}__${g.season}`;
    if (!agregados[chave]) {
      agregados[chave] = { model_version: g.model_version, league_id: g.league_id, season: g.season, precisoes: [], exatos: [], linhas: [], matchIds: new Set() };
    }
    const reais = new Set(g.linhas.filter((l) => l.real).map((l) => l.player_id));
    const previstos = new Set(g.linhas.filter((l) => l.previsto).map((l) => l.player_id));
    const acertos = [...reais].filter((id) => previstos.has(id)).length;
    agregados[chave].precisoes.push(acertos / 11);
    agregados[chave].exatos.push(reais.size === previstos.size && [...reais].every((id) => previstos.has(id)) ? 1 : 0);
    agregados[chave].linhas.push(...g.linhas);
    agregados[chave].matchIds.add(g.matchId);
  });

  return Object.values(agregados).map((a) => {
    const { linhas } = a;
    const n = linhas.length;
    const brier = linhas.reduce((s, l) => s + brierTermo(l.prob, l.real ? 1 : 0), 0) / n;
    const logLoss = linhas.reduce((s, l) => s + logLossTermo(l.prob, l.real ? 1 : 0), 0) / n;

    const ordenado = [...linhas].sort((x, y) => x.prob - y.prob);
    const calibracao = [];
    const tamanho = Math.floor(ordenado.length / 5);
    if (tamanho > 0) {
      for (let i = 0; i < 5; i++) {
        const fatia = ordenado.slice(i * tamanho, i === 4 ? ordenado.length : (i + 1) * tamanho);
        if (fatia.length === 0) continue;
        calibracao.push({
          previsto_medio: fatia.reduce((s, l) => s + l.prob, 0) / fatia.length,
          real: fatia.reduce((s, l) => s + (l.real ? 1 : 0), 0) / fatia.length,
          n: fatia.length,
        });
      }
    }

    return {
      model_version: a.model_version,
      league_id: a.league_id,
      season: a.season,
      // partidas DISTINTAS avaliadas -- a.precisoes.length conta pares
      // (partida, time), que dobraria a contagem (achado testando em
      // produção: Brasileirão 2018 aparecia com o dobro de "partidas" do
      // que a temporada real tem -- parte é essa contagem errada, parte é
      // duplicata real de linha em `matches`, ver nota registrada no repo).
      n_partidas: a.matchIds.size,
      n_previsoes: n,
      precisao_media_top11: a.precisoes.reduce((s, v) => s + v, 0) / a.precisoes.length,
      taxa_xi_exato: a.exatos.reduce((s, v) => s + v, 0) / a.exatos.length,
      brier,
      log_loss: logLoss,
      calibracao,
    };
  }).sort((x, y) =>
    x.model_version.localeCompare(y.model_version) ||
    x.league_id - y.league_id ||
    String(x.season).localeCompare(String(y.season))
  );
}

export default async function handler(req, res) {
  if (applyCors(req, res)) return;
  const supabaseUrl = process.env.SUPABASE_URL, supabaseKey = process.env.SUPABASE_KEY;
  if (!supabaseUrl || !supabaseKey) return res.status(500).json({ error: { message: 'SUPABASE_URL / SUPABASE_KEY não configuradas.' } });
  const supabase = getSupabase();

  if (req.query.formato === 'xi') {
    try {
      const grupos_xi = await calcularStatsXi(supabase);
      return res.status(200).json({ grupos_xi });
    } catch (erro) {
      return res.status(500).json({ error: { message: erro.message } });
    }
  }

  const { modelo, mercado, liga_id } = req.query;

  try {
    // A busca de odds da Pinnacle só roda quando `modelo` pede ESPECIFICAMENTE
    // o sintético -- diferente do resto deste endpoint (que serve a chamada
    // SEM filtro nenhum da carga inicial do painel, com todo o resto das
    // tabelas trazido por inteiro). Rodar essa query também na chamada sem
    // filtro empurrava o tempo total (paginação de mais uma tabela grande,
    // em paralelo com todas as outras) pra além do `statement_timeout` do
    // Postgres e do `maxDuration` da function (bug real, testado em produção
    // -- ver ModelosStats.jsx, que faz uma segunda chamada só pra esse
    // modelo, em paralelo com a chamada sem filtro, exatamente pra não
    // empilhar esse custo na carga inicial).
    const mercadosPinnacleAlvo = mercado ? Object.keys(MERCADO_SELECOES_PINNACLE).filter((m) => m === mercado) : Object.keys(MERCADO_SELECOES_PINNACLE);
    const precisaPinnacleDevigada = modelo === 'mercado_pinnacle_devigado' && mercadosPinnacleAlvo.length > 0;
    // `model_stats_resumo` só cobre estes 3 mercados (ver
    // `recalcular_model_stats_resumo` -- migration
    // 20260825002000_fn_recalcular_model_stats_resumo.sql).
    const MERCADOS_COM_RESUMO_PRECALCULADO = ['1X2', 'over_under_2.5', 'corners_over_under_9.5'];
    // A tabela `model_predictions` tem 5,2M+ linhas (53 model_name distintos,
    // cresce via cron diário) -- pra um `model_name`+`market` grande (ex.:
    // hibrido_gols_v1/corners_over_under_9.5, 30k+ linhas / ~31 páginas de
    // 1000), mesmo com keyset (índice certo, ~300-600ms por página medido
    // via EXPLAIN ANALYZE com role privilegiado) a busca AO VIVO ainda
    // estourou de verdade em produção (500 "statement timeout", 3 tentativas
    // sucessivas nesta sessão: #359 ORDER BY, #360 keyset, #361
    // paralelização). Causa raiz real, só encontrada depois de checar
    // `pg_roles`: a role usada por `SUPABASE_KEY` (`anon`) tem
    // `statement_timeout=3s` (`authenticated`=8s) -- BEM abaixo do
    // `maxDuration=60s` da function e do que os testes via `execute_sql`
    // (role privilegiada, sem esse limite) mediam. 31 requisições
    // SEQUENCIAIS via PostgREST (overhead de rede/RLS por cima do tempo de
    // execução puro no Postgres) somam tempo real suficiente pra alguma
    // página eventualmente estourar os 3s sob carga -- nenhuma quantidade de
    // otimização de índice/paralelização de OUTRAS consultas resolve isso,
    // porque o teto é por-consulta, não pro tempo total do endpoint.
    //
    // Por isso: pros 3 mercados que `model_stats_resumo` já cobre, usa o
    // pré-calculado MESMO quando `modelo` está filtrado (antes só usava sem
    // `modelo`) -- evita o scan gigante de `model_predictions` inteiramente
    // pra esse caso. Comparação com o mercado e calibração em quintis
    // ficam ausentes nesses grupos (frontend já trata "sem odds pra
    // comparar"/`calibracao_disponivel=false`) -- só a qualidade intrínseca
    // do modelo (log-loss/Brier/acurácia), que é exatamente o que
    // `model_stats_resumo` guarda. Mercados fora dessa lista (btts,
    // handicap, faixa_gols, placar_exato, as outras linhas de escanteios
    // etc.) continuam no cálculo ao vivo -- não têm alternativa
    // pré-calculada, e sozinhos (um `market` só, não o `model_name` inteiro)
    // costumam ter bem menos linhas que corners_over_under_9.5.
    // `forcar_ao_vivo=true` -- botão "Calcular calibração ao vivo" do
    // frontend (ModelosStats.jsx), só aparece quando modelo+mercado JÁ
    // estão filtrados pro usuário (nunca automático, nunca sem os dois
    // filtros -- senão reproduziria o mesmo timeout que motivou usar o
    // resumo pré-calculado pra esses 3 mercados). Pedido do usuário:
    // restaurar "Calibração por seleção (previsto vs. real, em quintis)"
    // pra 1X2/over_under_2.5/corners_over_under_9.5 sem mexer na tabela
    // pré-calculada nem na function SQL (que já foi afinada recentemente
    // pra evitar o mesmo timeout) -- o risco de estourar os 3s fica
    // restrito a um clique explícito, não à carga normal da página.
    const forcarAoVivo = req.query.forcar_ao_vivo === 'true' && !!modelo && !!mercado;
    const precisaResumoPreCalculado = !forcarAoVivo && (!modelo || (mercado && MERCADOS_COM_RESUMO_PRECALCULADO.includes(mercado)));

    // As duas levas de consultas abaixo (predições/pinnacle/resumo E as
    // tabelas "fixas" matches/odds_market/market_odds/match_stats/
    // model_calibration) são DISPARADAS juntas, não uma leva depois da
    // outra -- nenhuma delas depende do resultado da outra (a segunda leva
    // filtra só por critérios constantes, nunca por `predicoes`/
    // `matchIdsSet`; o cruzamento com `matchIdsSet` acontece só depois,
    // em JS). Achado real em produção: mesmo com a paginação por keyset já
    // corrigida (#360), rodar as duas levas em SÉRIE (await a primeira,
    // só then começar a segunda) somava os dois tempos e estourava os 30s
    // de `maxDuration` pra `?modelo=hibrido_gols_v1&mercado=
    // corners_over_under_9.5` (cada leva sozinha cabia no orçamento, a
    // SOMA não). Disparando as 9 consultas juntas, o tempo total passa a
    // ser o MÁXIMO entre as duas levas, não a soma.
    const promisePredicoesAntigas = precisaResumoPreCalculado
      ? Promise.resolve([])
      : buscarTudoPaginadoKeyset(() => {
          let q = supabase.from('model_predictions').select('id, model_name, market, selection, probability, match_id');
          if (modelo) q = q.eq('model_name', modelo);
          // v9 gravou '1x2' (minúscula) — incluir as duas variantes quando filtrar por 1X2
          if (mercado) q = mercado === '1X2' ? q.in('market', ['1X2', '1x2']) : q.eq('market', mercado);
          return q;
        }, ['match_id', 'id']);
    // `predicoes` (Model Benchmarking) só tem 1X2 -- pedir outro mercado
    // já filtra tudo fora, sem precisar de query condicional separada.
    const promisePredicoesBenchmarking = mercado && mercado !== '1X2'
      ? Promise.resolve([])
      : buscarTudoPaginado(() => {
          let q = supabase.from('predicoes').select('match_id, model_name, prob_home, prob_draw, prob_away').eq('mercado', '1X2');
          if (modelo) q = q.eq('model_name', modelo);
          return q;
        });
    // `odds_market` tem DEZENAS de outros mercados da Pinnacle (handicap
    // asiático/europeu em várias linhas, placar exato, cartões, 1º/2º tempo
    // etc. -- ver api/model-maintenance.js `tarefaOddsHistorico`), então
    // SEM filtro de `market` aqui essa query pagina um volume gigante de
    // linhas irrelevantes e estoura o `statement_timeout` do Postgres (bug
    // real, achado testando em produção) -- `.in('market', ...)` restringe
    // ao mesmo conjunto que `MERCADO_SELECOES_PINNACLE` sabe devigar.
    //
    // Esta consulta e `promiseOddsRowsAntigas`/`promiseCorneragensBrutas`
    // abaixo usam `buscarTudoPaginado` (OFFSET paralelo com `.order('id')`
    // default), não `buscarTudoPaginadoKeyset` -- achado testando em
    // produção depois do #363: sem filtro de `market`, nenhum índice
    // existente servia `snapshot+bookmaker` (só havia índices liderados por
    // `market` ou por `match_id`), e tanto o plano SEM ordenação (4s no
    // offset 20000) quanto COM `ORDER BY id` (11,8s, sequential scan +
    // sort) estouravam o `statement_timeout=3s` da role `anon`. Resolvido
    // com um índice novo (`idx_odds_market_snapshot_bookmaker_id`,
    // migration 20260825050000) -- com ele, `.order('id')` vira Index Scan
    // direto (502ms no mesmo offset), então o OFFSET paralelo (mais rápido
    // que keyset sequencial pra esse volume) volta a ser seguro.
    const promisePinnacleOdds = precisaPinnacleDevigada
      ? buscarTudoPaginado(() => supabase.from('odds_market').select('id, match_id, market, selection, odds').eq('snapshot', 'closing').eq('bookmaker', 'pinnacle').in('market', mercadosPinnacleAlvo))
      : Promise.resolve([]);
    const promiseResumoRows = precisaResumoPreCalculado
      ? buscarTudoPaginado(() => {
          let q = supabase.from('model_stats_resumo').select('model_name, market, league_id, n_jogos, log_loss_modelo, brier_modelo, accuracy_modelo');
          if (modelo) q = q.eq('model_name', modelo);
          if (mercado) q = q.eq('market', mercado);
          if (liga_id) q = q.eq('league_id', Number(liga_id));
          return q;
        // `model_stats_resumo` não tem coluna `id` -- sua PK é o composto
        // (model_name, market, league_id) (migration
        // 20260825001000_model_stats_resumo.sql). O default `['id']` de
        // `buscarTudoPaginado` quebrava aqui com "column
        // model_stats_resumo.id does not exist" -- passar a PK real.
        }, ['model_name', 'market', 'league_id'])
      : Promise.resolve([]);
    // `model_stats_ic` (migration 20260827063903) -- IC95% por bootstrap do
    // log-loss/acurácia (achado #27, CONTEXTO_PROJETO.md: o ranking "melhor
    // por liga" sem intervalo de confiança confunde edge real com ruído de
    // amostra pequena). Populada por `scripts/avaliar_ic_modelos_por_liga.py`
    // (fora do Vercel -- bootstrap não cabe no orçamento de uma function),
    // não pelo mesmo mecanismo de `model_stats_resumo`. Tabela pequena (uma
    // linha por model_name+market+league_id com amostra suficiente) -- busca
    // sempre, sem o gate de `precisaResumoPreCalculado`, já que é uma tabela
    // independente e não sofre do mesmo risco de timeout.
    const promiseStatsIcRows = buscarTudoPaginado(() => {
      let q = supabase.from('model_stats_ic').select('model_name, market, league_id, log_loss_ic_inf, log_loss_ic_sup, accuracy_ic_inf, accuracy_ic_sup');
      if (modelo) q = q.eq('model_name', modelo);
      if (mercado) q = q.eq('market', mercado);
      if (liga_id) q = q.eq('league_id', Number(liga_id));
      return q;
    }, ['model_name', 'market', 'league_id']);

    // Busca as tabelas inteiras já filtradas pelos critérios FIXOS (bem menores
    // que o universo de match_ids das previsões) e filtra em JS — bem menos
    // round-trips do que quebrar em lotes de match_id.
    const promiseTodasMatches = buscarTudoPaginado(() => supabase.from('matches').select('id, league_id, status, home_goals, away_goals, match_date, home_team_id, away_team_id'));
    const promiseOddsRowsAntigas = buscarTudoPaginado(() => supabase.from('odds_market').select('id, match_id, market, selection, odds').eq('snapshot', 'closing').eq('bookmaker', 'media_mercado'));
    const promiseMarketOddsRaw = buscarTudoPaginado(() => supabase.from('market_odds').select('match_id, odd_home, odd_draw, odd_away'));
    const promiseCorneragensBrutas = buscarTudoPaginado(() => supabase.from('match_stats').select('id, match_id, team_id, corners').not('corners', 'is', null));
    const promiseCalibracoes = buscarTudoPaginado(() => supabase.from('model_calibration').select('model_name, market, selection, method, platt_coef, platt_intercept, isotonic_x, isotonic_y'));

    const [predicoesAntigas, predicoesBenchmarkingRaw, pinnacleOddsRaw, resumoRows, statsIcRows] = await Promise.all([
      promisePredicoesAntigas, promisePredicoesBenchmarking, promisePinnacleOdds, promiseResumoRows, promiseStatsIcRows,
    ]);
    let pinnacleLinhas = normalizarPinnacleDevigada(pinnacleOddsRaw);
    if (mercado) pinnacleLinhas = pinnacleLinhas.filter((l) => l.market === mercado);

    // IC95% por bootstrap (ver comentário de `promiseStatsIcRows`) -- indexado
    // por model_name+market+league_id pra anexar em QUALQUER grupo de saída
    // (pré-calculado ou ao vivo) que bater a mesma chave; ausente (amostra
    // menor que 30, ver AMOSTRA_MINIMA do script) vira `null`, tratado como
    // "IC não calculado" pelo front, nunca um erro.
    const icPorChave = {};
    statsIcRows.forEach(r => { icPorChave[`${r.model_name}__${r.market}__${r.league_id}`] = r; });
    function anexarIc(modelName, market, leagueId) {
      const ic = icPorChave[`${modelName}__${market}__${leagueId}`];
      return {
        log_loss_ic_inf: ic ? Number(ic.log_loss_ic_inf) : null,
        log_loss_ic_sup: ic ? Number(ic.log_loss_ic_sup) : null,
        accuracy_ic_inf: ic ? Number(ic.accuracy_ic_inf) : null,
        accuracy_ic_sup: ic ? Number(ic.accuracy_ic_sup) : null,
      };
    }

    // Grupos pré-calculados (ver comentário acima sobre `model_stats_resumo`)
    // -- não tem comparação com mercado nem calibração em quintis, só a
    // qualidade do MODELO em si; campos correspondentes ficam null/vazios,
    // no mesmo formato que o resto do endpoint devolve pros outros grupos.
    const gruposResumo = resumoRows.map(r => ({
      model_name: r.model_name, market: r.market, league_id: r.league_id,
      n_jogos: r.n_jogos,
      log_loss_modelo: r.log_loss_modelo != null ? Number(r.log_loss_modelo) : null,
      brier_modelo: r.brier_modelo != null ? Number(r.brier_modelo) : null,
      log_likelihood_modelo: r.log_loss_modelo != null ? -Number(r.log_loss_modelo) * r.n_jogos : null,
      log_likelihood_mercado: null,
      log_loss_mercado: null, brier_mercado: null,
      accuracy_modelo: r.accuracy_modelo != null ? Number(r.accuracy_modelo) : null, accuracy_mercado: null,
      tem_odds: false,
      calibracao_disponivel: false,
      log_loss_platt: null, brier_platt: null, accuracy_platt: null,
      log_loss_isotonic: null, brier_isotonic: null, accuracy_isotonic: null,
      por_selecao: [],
      ...anexarIc(r.model_name, r.market, r.league_id),
    }));

    // v9 gravou '1x2' (minúscula) — normalizar pra '1X2' antes de qualquer cálculo
    // pra garantir consistência em chaveMercado, chaveOdds e chaveGrupo.
    const predicoes = [
      ...predicoesAntigas.map(p => ({ ...p, market: p.market === '1x2' ? '1X2' : p.market })),
      ...normalizarPredicoesBenchmarking(predicoesBenchmarkingRaw),
      ...pinnacleLinhas,
    ];
    if ((!predicoes || predicoes.length === 0) && gruposResumo.length === 0) return res.status(200).json({ grupos: [] });
    if (predicoes.length === 0) {
      // Só tem grupos pré-calculados (caso normal da chamada sem `modelo`
      // quando `predicoesBenchmarkingRaw`/`pinnacleLinhas` vêm vazios) --
      // nada pra computar ao vivo, devolve direto.
      gruposResumo.sort((a, b) => a.model_name.localeCompare(b.model_name) || a.market.localeCompare(b.market) || a.league_id - b.league_id);
      return res.status(200).json({ grupos: gruposResumo });
    }

    const matchIdsSet = new Set(predicoes.map(p => p.match_id));

    // As 5 promessas abaixo já foram disparadas mais acima (em paralelo com
    // a primeira leva) -- só falta esperar.
    const [todasMatches, oddsRowsAntigas, marketOddsRaw, corneragensBrutas, calibracoes] = await Promise.all([
      promiseTodasMatches, promiseOddsRowsAntigas, promiseMarketOddsRaw, promiseCorneragensBrutas, promiseCalibracoes,
    ]);
    const oddsRowsBrutas = [...oddsRowsAntigas, ...normalizarOddsBenchmarking(marketOddsRaw)];

    // calibração salva por model_name+market+selection -> { platt: {a,b}, isotonic: {x,y} }
    const calibPorChave = {};
    calibracoes.forEach(c => {
      const chave = `${c.model_name}__${c.market}__${c.selection}`;
      if (!calibPorChave[chave]) calibPorChave[chave] = {};
      if (c.method === 'platt') calibPorChave[chave].platt = { a: Number(c.platt_coef), b: Number(c.platt_intercept) };
      if (c.method === 'isotonic') calibPorChave[chave].isotonic = { x: c.isotonic_x, y: c.isotonic_y };
    });

    const ligaIdNum = liga_id ? Number(liga_id) : null;
    const matchesValidos = todasMatches.filter(m => matchIdsSet.has(m.id) && (!ligaIdNum || m.league_id === ligaIdNum));
    const matchIdsValidos = new Set(matchesValidos.map(m => m.id));

    const oddsRows = oddsRowsBrutas.filter(r => matchIdsValidos.has(r.match_id));

    const corners = {};
    const cornersDetalhado = {}; // { [match_id]: { home, away } } — usado no relatório partida a partida
    {
      const somaPorJogo = {};
      const contPorJogo = {};
      const homePorMatch = {};
      matchesValidos.forEach(m => { homePorMatch[m.id] = m.home_team_id; });
      corneragensBrutas.filter(r => matchIdsValidos.has(r.match_id)).forEach(r => {
        somaPorJogo[r.match_id] = (somaPorJogo[r.match_id] || 0) + Number(r.corners);
        contPorJogo[r.match_id] = (contPorJogo[r.match_id] || 0) + 1;
        if (homePorMatch[r.match_id] != null) {
          if (!cornersDetalhado[r.match_id]) cornersDetalhado[r.match_id] = {};
          if (Number(r.team_id) === Number(homePorMatch[r.match_id])) {
            cornersDetalhado[r.match_id].home = Number(r.corners);
          } else {
            cornersDetalhado[r.match_id].away = Number(r.corners);
          }
        }
      });
      Object.keys(somaPorJogo).forEach(id => { if (contPorJogo[id] === 2) corners[id] = somaPorJogo[id]; });
    }

    const resultadosReais = calcularResultadosReais(matchesValidos, corners);

    // odds devigadas por match+market -> { selecao: prob }
    const oddsPorMatchMercado = {};
    oddsRows.forEach(r => {
      const chave = `${r.match_id}__${r.market}`;
      if (!oddsPorMatchMercado[chave]) oddsPorMatchMercado[chave] = {};
      oddsPorMatchMercado[chave][r.selection] = Number(r.odds);
    });
    const probMercado = {};
    Object.entries(oddsPorMatchMercado).forEach(([chave, oddsSel]) => {
      probMercado[chave] = devigar(oddsSel);
    });

    // Relatório partida a partida: exige modelo+mercado (senão a lista fica
    // grande e sem sentido de leitura) -- reaproveita TODO o pipeline acima
    // (predições, resultado real, odds, corners), só não agrega em métricas.
    // EV usa a odd REAL (não devigada -- devig é só pra comparar probabilidade,
    // EV precisa do payout de verdade) contra a probabilidade do próprio
    // modelo, sempre na seleção que o modelo mais favorece naquela partida
    // (mesmo "argmax" já usado no cálculo de acurácia agregada acima).
    // xG previsto/real só entra quando existe em model_match_estimates
    // (só modelos baseados em gols esperados -- Dixon-Coles/Poisson --
    // gravam isso; ver CONTEXTO_PROJETO.md sobre por que classificação pura
    // não ganha um "xG implícito" back-derivado).
    if (req.query.formato === 'partidas') {
      if (!modelo || !mercado) {
        return res.status(400).json({ error: { message: 'formato=partidas exige modelo e mercado.' } });
      }
      const previsoesDoModelo = predicoes.filter(p => matchIdsValidos.has(p.match_id));
      const porMatch = {};
      previsoesDoModelo.forEach(p => {
        if (!porMatch[p.match_id]) porMatch[p.match_id] = [];
        porMatch[p.match_id].push(p);
      });

      const matchIdsRelatorio = Object.keys(porMatch).map(Number);
      const [timesRows, estimativasRows, xgRows, xgotRows] = await Promise.all([
        buscarTudoPaginado(() => supabase.from('teams').select('id, name')),
        buscarTudoPaginado(() => supabase.from('model_match_estimates').select('match_id, model_name, xg_home_previsto, xg_away_previsto, xgot_home_previsto, xgot_away_previsto').eq('model_name', modelo).in('match_id', matchIdsRelatorio.length ? matchIdsRelatorio : [0])),
        buscarTudoPaginado(() => supabase.from('match_stats').select('match_id, team_id, xg').in('match_id', matchIdsRelatorio.length ? matchIdsRelatorio : [0])),
        // xGOT só existe em match_stats_fotmob (não em match_stats) -- ver
        // dados_historicos._anexar_xgot_por_partida sobre essa fonte.
        buscarTudoPaginado(() => supabase.from('match_stats_fotmob').select('match_id, team_id, xgot').in('match_id', matchIdsRelatorio.length ? matchIdsRelatorio : [0])),
      ]);
      const nomePorTime = {};
      timesRows.forEach(t => { nomePorTime[t.id] = t.name; });
      const estimativaPorMatch = {};
      estimativasRows.forEach(e => { estimativaPorMatch[e.match_id] = e; });
      const xgRealPorMatchTime = {};
      xgRows.forEach(r => {
        if (!xgRealPorMatchTime[r.match_id]) xgRealPorMatchTime[r.match_id] = {};
        xgRealPorMatchTime[r.match_id][r.team_id] = r.xg != null ? Number(r.xg) : null;
      });
      const xgotRealPorMatchTime = {};
      xgotRows.forEach(r => {
        if (!xgotRealPorMatchTime[r.match_id]) xgotRealPorMatchTime[r.match_id] = {};
        xgotRealPorMatchTime[r.match_id][r.team_id] = r.xgot != null ? Number(r.xgot) : null;
      });

      const partidas = matchIdsRelatorio.map(matchId => {
        const match = matchesValidos.find(m => m.id === matchId);
        const selecoes = porMatch[matchId];
        const mercadoChave = normalizarMercado(mercado);
        const resultado = resultadosReais[matchId];
        const chaveOdds = `${matchId}__${mercado}`;
        const oddsSel = oddsPorMatchMercado[chaveOdds] || {};

        const previstaMaior = selecoes.reduce((a, b) => (Number(b.probability) > Number(a.probability) ? b : a));
        const oddsUsada = oddsSel[previstaMaior.selection] ?? null;
        const evEstimado = oddsUsada != null ? Number(previstaMaior.probability) * oddsUsada - 1 : null;
        const resultadoReal = resultado ? resultado[mercadoChave] : null;
        const estimativa = estimativaPorMatch[matchId];
        const xgReal = xgRealPorMatchTime[matchId] || {};
        const xgotReal = xgotRealPorMatchTime[matchId] || {};

        return {
          match_id: matchId,
          match_date: match?.match_date ?? null,
          mandante: nomePorTime[match?.home_team_id] || `Time #${match?.home_team_id}`,
          visitante: nomePorTime[match?.away_team_id] || `Time #${match?.away_team_id}`,
          league_id: match?.league_id ?? null,
          home_goals: match?.home_goals ?? null,
          away_goals: match?.away_goals ?? null,
          corners_home: cornersDetalhado[matchId]?.home ?? null,
          corners_away: cornersDetalhado[matchId]?.away ?? null,
          todas_probs: Object.fromEntries(selecoes.map(s => [s.selection, Number(s.probability)])),
          todas_odds: Object.keys(oddsSel).length > 0 ? { ...oddsSel } : null,
          selecao_prevista: previstaMaior.selection,
          probabilidade_modelo: Number(previstaMaior.probability),
          odds_usada: oddsUsada,
          ev_estimado: evEstimado,
          resultado_real: resultadoReal,
          acertou: resultadoReal != null ? resultadoReal === previstaMaior.selection : null,
          xg_home_previsto: estimativa?.xg_home_previsto != null ? Number(estimativa.xg_home_previsto) : null,
          xg_away_previsto: estimativa?.xg_away_previsto != null ? Number(estimativa.xg_away_previsto) : null,
          xg_home_real: match ? (xgReal[match.home_team_id] ?? null) : null,
          xg_away_real: match ? (xgReal[match.away_team_id] ?? null) : null,
          xgot_home_previsto: estimativa?.xgot_home_previsto != null ? Number(estimativa.xgot_home_previsto) : null,
          xgot_away_previsto: estimativa?.xgot_away_previsto != null ? Number(estimativa.xgot_away_previsto) : null,
          xgot_home_real: match ? (xgotReal[match.home_team_id] ?? null) : null,
          xgot_away_real: match ? (xgotReal[match.away_team_id] ?? null) : null,
        };
      }).filter(p => p.resultado_real != null) // só partidas já finalizadas, mesmo filtro do resto do endpoint
        .sort((a, b) => (a.match_date || '').localeCompare(b.match_date || ''));

      return res.status(200).json({ partidas });
    }

    // Agrupa previsões por model_name+market+league_id
    const grupos = {};
    for (const p of predicoes) {
      if (!matchIdsValidos.has(p.match_id)) continue;
      const match = matchesValidos.find(m => m.id === p.match_id);
      const resultado = resultadosReais[p.match_id];
      if (!resultado) continue; // partida não finalizada ainda

      const chaveGrupo = `${p.model_name}__${p.market}__${match.league_id}`;
      if (!grupos[chaveGrupo]) {
        grupos[chaveGrupo] = {
          model_name: p.model_name, market: p.market, league_id: match.league_id,
          linhas: [],
        };
      }

      const mercadoChave = normalizarMercado(p.market);
      const y = resultado[mercadoChave] === p.selection ? 1 : 0;
      const chaveOdds = `${p.match_id}__${p.market}`;
      const pMercado = probMercado[chaveOdds]?.[p.selection] ?? null;

      const pModelo = Number(p.probability);
      const calib = calibPorChave[`${p.model_name}__${p.market}__${p.selection}`];
      const pPlatt = calib?.platt ? aplicarPlatt(pModelo, calib.platt.a, calib.platt.b) : null;
      const pIsotonic = calib?.isotonic ? aplicarIsotonic(pModelo, calib.isotonic.x, calib.isotonic.y) : null;

      grupos[chaveGrupo].linhas.push({
        match_id: p.match_id, selection: p.selection, p_modelo: pModelo, y, p_mercado: pMercado,
        p_platt: pPlatt, p_isotonic: pIsotonic,
      });
    }

    const saida = Object.values(grupos).map(g => {
      const { linhas } = g;
      const nJogos = new Set(linhas.map(l => l.match_id)).size;

      // log-loss / brier do modelo — só a linha da CLASSE REAL de cada jogo (evita duplicar)
      const linhasClasseReal = linhas.filter(l => l.y === 1);
      const logLossModelo = linhasClasseReal.reduce((s, l) => s + logLossTermo(l.p_modelo, 1), 0) / linhasClasseReal.length;
      const brierModelo = linhasClasseReal.reduce((s, l) => s + brierTermo(l.p_modelo, 1), 0) / linhasClasseReal.length;

      const linhasComOdds = linhasClasseReal.filter(l => l.p_mercado != null);
      const temOdds = linhasComOdds.length > 0;
      const logLossMercado = temOdds ? linhasComOdds.reduce((s, l) => s + logLossTermo(l.p_mercado, 1), 0) / linhasComOdds.length : null;
      const brierMercado = temOdds ? linhasComOdds.reduce((s, l) => s + brierTermo(l.p_mercado, 1), 0) / linhasComOdds.length : null;

      // Métricas com calibração aplicada (Platt/Isotonic) — só entram no cálculo
      // se TODA seleção daquele jogo teve calibração disponível (senão o
      // argmax da acurácia ficaria injusto comparando prob crua com calibrada).
      function metricasCalibradas(campo) {
        const comCalib = linhasClasseReal.filter(l => l[campo] != null);
        const temCalib = comCalib.length > 0;
        const logLoss = temCalib ? comCalib.reduce((s, l) => s + logLossTermo(l[campo], 1), 0) / comCalib.length : null;
        const brier = temCalib ? comCalib.reduce((s, l) => s + brierTermo(l[campo], 1), 0) / comCalib.length : null;
        return { logLoss, brier, temCalib };
      }
      const calibPlatt = metricasCalibradas('p_platt');
      const calibIsotonic = metricasCalibradas('p_isotonic');

      // acurácia: por partida, a seleção de maior probabilidade (modelo e mercado)
      // é comparada com a seleção real (y=1) — diferente de log-loss/brier, aqui
      // interessa só o "acertou o vencedor", não a qualidade da probabilidade em si.
      const porJogo = {};
      linhas.forEach(l => {
        if (!porJogo[l.match_id]) porJogo[l.match_id] = [];
        porJogo[l.match_id].push(l);
      });
      let acertosModelo = 0, totalJogosAcc = 0, acertosMercado = 0, totalJogosAccMercado = 0;
      let acertosPlatt = 0, totalJogosAccPlatt = 0, acertosIsotonic = 0, totalJogosAccIsotonic = 0;
      Object.values(porJogo).forEach(ls => {
        totalJogosAcc++;
        const maiorModelo = ls.reduce((a, b) => (b.p_modelo > a.p_modelo ? b : a));
        if (maiorModelo.y === 1) acertosModelo++;

        const comOdds = ls.filter(l => l.p_mercado != null);
        if (comOdds.length === ls.length) {
          totalJogosAccMercado++;
          const maiorMercado = comOdds.reduce((a, b) => (b.p_mercado > a.p_mercado ? b : a));
          if (maiorMercado.y === 1) acertosMercado++;
        }

        const comPlatt = ls.filter(l => l.p_platt != null);
        if (comPlatt.length === ls.length) {
          totalJogosAccPlatt++;
          const maiorPlatt = comPlatt.reduce((a, b) => (b.p_platt > a.p_platt ? b : a));
          if (maiorPlatt.y === 1) acertosPlatt++;
        }

        const comIsotonic = ls.filter(l => l.p_isotonic != null);
        if (comIsotonic.length === ls.length) {
          totalJogosAccIsotonic++;
          const maiorIsotonic = comIsotonic.reduce((a, b) => (b.p_isotonic > a.p_isotonic ? b : a));
          if (maiorIsotonic.y === 1) acertosIsotonic++;
        }
      });
      const accuracyModelo = totalJogosAcc > 0 ? acertosModelo / totalJogosAcc : null;
      const accuracyMercado = totalJogosAccMercado > 0 ? acertosMercado / totalJogosAccMercado : null;
      const accuracyPlatt = totalJogosAccPlatt > 0 ? acertosPlatt / totalJogosAccPlatt : null;
      const accuracyIsotonic = totalJogosAccIsotonic > 0 ? acertosIsotonic / totalJogosAccIsotonic : null;

      // edge e calibração por seleção
      const porSelecao = {};
      linhas.forEach(l => {
        if (!porSelecao[l.selection]) porSelecao[l.selection] = [];
        porSelecao[l.selection].push(l);
      });
      const selecoes = Object.entries(porSelecao).map(([selecao, ls]) => {
        const comOdds = ls.filter(l => l.p_mercado != null);
        const edgeMedio = comOdds.length > 0 ? comOdds.reduce((s, l) => s + (l.p_modelo - l.p_mercado), 0) / comOdds.length : null;

        // calibração em quintis, ordenado por p_modelo
        const ordenado = [...ls].sort((a, b) => a.p_modelo - b.p_modelo);
        const quintis = [];
        const tamanho = Math.floor(ordenado.length / 5);
        if (tamanho > 0) {
          for (let i = 0; i < 5; i++) {
            const fatia = ordenado.slice(i * tamanho, i === 4 ? ordenado.length : (i + 1) * tamanho);
            if (fatia.length === 0) continue;
            const fatiaComMkt = fatia.filter(l => l.p_mercado != null);
            quintis.push({
              previsto_medio: fatia.reduce((s, l) => s + l.p_modelo, 0) / fatia.length,
              real: fatia.reduce((s, l) => s + l.y, 0) / fatia.length,
              n: fatia.length,
              mercado_medio: fatiaComMkt.length > 0 ? fatiaComMkt.reduce((s, l) => s + l.p_mercado, 0) / fatiaComMkt.length : null,
            });
          }
        }

        const comPlatt = ls.filter(l => l.p_platt != null);
        const comIsotonic = ls.filter(l => l.p_isotonic != null);

        return {
          selecao, n: ls.length,
          p_modelo_medio: ls.reduce((s, l) => s + l.p_modelo, 0) / ls.length,
          p_mercado_medio: comOdds.length > 0 ? comOdds.reduce((s, l) => s + l.p_mercado, 0) / comOdds.length : null,
          p_platt_medio: comPlatt.length > 0 ? comPlatt.reduce((s, l) => s + l.p_platt, 0) / comPlatt.length : null,
          p_isotonic_medio: comIsotonic.length > 0 ? comIsotonic.reduce((s, l) => s + l.p_isotonic, 0) / comIsotonic.length : null,
          edge_medio: edgeMedio,
          calibracao: quintis,
        };
      });

      return {
        model_name: g.model_name, market: g.market, league_id: g.league_id,
        n_jogos: nJogos,
        log_loss_modelo: logLossModelo, brier_modelo: brierModelo,
        // log-verossimilhança TOTAL (soma de log p, não a média) -- log-loss já
        // é a média de -log(p), então log_likelihood = -log_loss * n; exposto à
        // parte porque cresce com o tamanho da amostra (não é comparável entre
        // grupos de n diferente do jeito que log-loss médio é).
        log_likelihood_modelo: -logLossModelo * linhasClasseReal.length,
        log_likelihood_mercado: temOdds ? -logLossMercado * linhasComOdds.length : null,
        log_loss_mercado: logLossMercado, brier_mercado: brierMercado,
        accuracy_modelo: accuracyModelo, accuracy_mercado: accuracyMercado,
        tem_odds: temOdds,
        calibracao_disponivel: calibPlatt.temCalib || calibIsotonic.temCalib,
        log_loss_platt: calibPlatt.logLoss, brier_platt: calibPlatt.brier, accuracy_platt: accuracyPlatt,
        log_loss_isotonic: calibIsotonic.logLoss, brier_isotonic: calibIsotonic.brier, accuracy_isotonic: accuracyIsotonic,
        por_selecao: selecoes,
        ...anexarIc(g.model_name, g.market, g.league_id),
      };
    });

    const saidaCompleta = [...saida, ...gruposResumo];
    saidaCompleta.sort((a, b) => a.model_name.localeCompare(b.model_name) || a.market.localeCompare(b.market) || a.league_id - b.league_id);
    res.status(200).json({ grupos: saidaCompleta });
  } catch (erro) {
    res.status(500).json({ error: { message: erro.message } });
  }
}
