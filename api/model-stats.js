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
// Também aplica a calibração salva em model_calibration (Platt Scaling e
// Isotonic Regression, ajustados por api/fit-calibration.js) quando existe
// pra aquele model_name+market+selection, devolvendo as métricas COM e SEM
// ajuste lado a lado — pra decidir se vale a pena aplicar a calibração em
// produção ou não, por mercado/liga.
//
// Agrupa por liga (matches.league_id) — filtros opcionais na URL.
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

// Resultado real de cada partida, por mercado — mesma lógica usada na
// avaliação de log-loss feita manualmente antes (ver CONTEXTO_PROJETO.md).
function calcularResultadosReais(matches, corners) {
  const porMatch = {};
  for (const m of matches) {
    if (m.status !== 'finished' || m.home_goals == null || m.away_goals == null) continue;
    const total = m.home_goals + m.away_goals;
    porMatch[m.id] = {
      league_id: m.league_id,
      '1X2': m.home_goals > m.away_goals ? 'home' : m.home_goals < m.away_goals ? 'away' : 'draw',
      over_under_2_5: total > 2.5 ? 'over' : 'under',
    };
  }
  for (const [matchId, totalCorners] of Object.entries(corners)) {
    if (porMatch[matchId]) porMatch[matchId]['corners_over_under_9_5'] = totalCorners > 9.5 ? 'over' : 'under';
  }
  return porMatch;
}

// Devigging: transforma odds em probabilidade normalizada (remove a margem da casa)
function devigar(oddsPorSelecao) {
  const implicitas = {};
  let soma = 0;
  for (const [sel, odd] of Object.entries(oddsPorSelecao)) {
    implicitas[sel] = 1 / odd;
    soma += implicitas[sel];
  }
  const normalizadas = {};
  for (const sel of Object.keys(implicitas)) normalizadas[sel] = implicitas[sel] / soma;
  return normalizadas;
}

function chaveMercado(m) {
  // v9 gravou '1x2' (minúscula) — normaliza antes do switch
  if (m === '1X2' || m === '1x2') return '1X2';
  if (m === 'over_under_2.5') return 'over_under_2_5';
  return 'corners_over_under_9_5';
}

// O Supabase (PostgREST) devolve no máximo 1000 linhas por chamada, mesmo sem
// LIMIT explícito no .select() — sem paginar de verdade, qualquer tabela/junção
// com mais de 1000 linhas vem cortada em silêncio (foi um bug real aqui: sem
// filtro, model_predictions tem 22k+ linhas e vinha só 1/22 do dado).
// Recebe uma FÁBRICA de query (não a query já construída) — cada página
// precisa de uma instância nova do builder, reaproveitar a mesma após
// executada não é seguro no supabase-js.
async function buscarTudoPaginado(criarQuery) {
  const TAMANHO_PAGINA = 1000;
  const resultado = [];
  let pagina = 0;
  while (true) {
    const { data, error } = await criarQuery().range(pagina * TAMANHO_PAGINA, pagina * TAMANHO_PAGINA + TAMANHO_PAGINA - 1);
    if (error) throw error;
    resultado.push(...(data || []));
    if (!data || data.length < TAMANHO_PAGINA) break;
    pagina++;
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
async function calcularStatsXi(supabase) {
  const previsoes = await buscarTudoPaginado(() =>
    supabase.from('xi_previsto').select('match_id, team_id, player_id, prob_titular, is_titular_previsto, model_version')
  );
  if (previsoes.length === 0) return [];

  const matchIds = [...new Set(previsoes.map((p) => p.match_id))];
  const lotes = [];
  for (let i = 0; i < matchIds.length; i += 1000) lotes.push(matchIds.slice(i, i + 1000));

  const [matchesRows, lineupRows] = await Promise.all([
    Promise.all(lotes.map((l) => buscarTudoPaginado(() => supabase.from('matches').select('id, league_id, season, status').in('id', l)))).then((r) => r.flat()),
    Promise.all(lotes.map((l) => buscarTudoPaginado(() => supabase.from('match_lineup_fotmob').select('match_id, team_id, player_id, is_starter').in('match_id', l)))).then((r) => r.flat()),
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
      porGrupoTime[chaveGrupo] = { model_version: p.model_version, league_id: match.league_id, season: match.season, linhas: [] };
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
      agregados[chave] = { model_version: g.model_version, league_id: g.league_id, season: g.season, precisoes: [], exatos: [], linhas: [] };
    }
    const reais = new Set(g.linhas.filter((l) => l.real).map((l) => l.player_id));
    const previstos = new Set(g.linhas.filter((l) => l.previsto).map((l) => l.player_id));
    const acertos = [...reais].filter((id) => previstos.has(id)).length;
    agregados[chave].precisoes.push(acertos / 11);
    agregados[chave].exatos.push(reais.size === previstos.size && [...reais].every((id) => previstos.has(id)) ? 1 : 0);
    agregados[chave].linhas.push(...g.linhas);
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
      n_partidas: a.precisoes.length,
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
    const [predicoesAntigas, predicoesBenchmarkingRaw] = await Promise.all([
      buscarTudoPaginado(() => {
        let q = supabase.from('model_predictions').select('id, model_name, market, selection, probability, match_id');
        if (modelo) q = q.eq('model_name', modelo);
        // v9 gravou '1x2' (minúscula) — incluir as duas variantes quando filtrar por 1X2
        if (mercado) q = mercado === '1X2' ? q.in('market', ['1X2', '1x2']) : q.eq('market', mercado);
        return q;
      }),
      // `predicoes` (Model Benchmarking) só tem 1X2 -- pedir outro mercado
      // já filtra tudo fora, sem precisar de query condicional separada.
      mercado && mercado !== '1X2'
        ? Promise.resolve([])
        : buscarTudoPaginado(() => {
            let q = supabase.from('predicoes').select('match_id, model_name, prob_home, prob_draw, prob_away').eq('mercado', '1X2');
            if (modelo) q = q.eq('model_name', modelo);
            return q;
          }),
    ]);
    // v9 gravou '1x2' (minúscula) — normalizar pra '1X2' antes de qualquer cálculo
    // pra garantir consistência em chaveMercado, chaveOdds e chaveGrupo.
    const predicoes = [
      ...predicoesAntigas.map(p => ({ ...p, market: p.market === '1x2' ? '1X2' : p.market })),
      ...normalizarPredicoesBenchmarking(predicoesBenchmarkingRaw),
    ];
    if (!predicoes || predicoes.length === 0) return res.status(200).json({ grupos: [] });

    const matchIdsSet = new Set(predicoes.map(p => p.match_id));

    // Busca as tabelas inteiras já filtradas pelos critérios FIXOS (bem menores
    // que o universo de match_ids das previsões) e filtra em JS — bem menos
    // round-trips do que quebrar em lotes de match_id.
    const [todasMatches, oddsRowsAntigas, marketOddsRaw, corneragensBrutas, calibracoes] = await Promise.all([
      buscarTudoPaginado(() => supabase.from('matches').select('id, league_id, status, home_goals, away_goals, match_date, home_team_id, away_team_id')),
      buscarTudoPaginado(() => supabase.from('odds_market').select('match_id, market, selection, odds').eq('snapshot', 'closing').eq('bookmaker', 'media_mercado')),
      buscarTudoPaginado(() => supabase.from('market_odds').select('match_id, odd_home, odd_draw, odd_away')),
      buscarTudoPaginado(() => supabase.from('match_stats').select('match_id, team_id, corners').not('corners', 'is', null)),
      buscarTudoPaginado(() => supabase.from('model_calibration').select('model_name, market, selection, method, platt_coef, platt_intercept, isotonic_x, isotonic_y')),
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
        const mercadoChave = chaveMercado(mercado);
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

      const mercadoChave = chaveMercado(p.market);
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
        log_loss_mercado: logLossMercado, brier_mercado: brierMercado,
        accuracy_modelo: accuracyModelo, accuracy_mercado: accuracyMercado,
        tem_odds: temOdds,
        calibracao_disponivel: calibPlatt.temCalib || calibIsotonic.temCalib,
        log_loss_platt: calibPlatt.logLoss, brier_platt: calibPlatt.brier, accuracy_platt: accuracyPlatt,
        log_loss_isotonic: calibIsotonic.logLoss, brier_isotonic: calibIsotonic.brier, accuracy_isotonic: accuracyIsotonic,
        por_selecao: selecoes,
      };
    });

    saida.sort((a, b) => a.model_name.localeCompare(b.model_name) || a.market.localeCompare(b.market) || a.league_id - b.league_id);
    res.status(200).json({ grupos: saida });
  } catch (erro) {
    res.status(500).json({ error: { message: erro.message } });
  }
}
