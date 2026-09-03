// api/backtest-betting.js
// Roda no SERVIDOR do Vercel. Variáveis de ambiente necessárias:
//   SUPABASE_URL / SUPABASE_KEY  -> mesmas do model-stats.js (leitura pública, RLS)
//
// Simula apostas reais (não só compara log-loss) pra responder "esse modelo
// tem EV+ de verdade contra o mercado, ou o edge é ruído?". Pra cada seleção
// com edge = p_modelo - p_mercado_devigado acima de um limiar, aposta na odd
// REAL de fechamento (não na probabilidade devigada — o vig é o que se paga
// de verdade), em ordem cronológica. Duas formas de banca: flat (1 unidade
// fixa por aposta) ou Kelly fracionário (fração do critério de Kelly sobre a
// banca INICIAL, não compondo — mantém as apostas comparáveis entre grupos
// em vez de path-dependentes). No modo Kelly, a fração aplicada, o corte
// mínimo de EV e o teto de stake por aposta vêm da política de risco por
// faixa de odd (api/_lib/stakingPolicy.js, pedida explicitamente pelo
// usuário) -- não são mais fixos: odd mais alta usa fração menor, corte de EV
// mais alto e teto de stake mais baixo. `edge_minimo` continua um filtro
// SEPARADO (significância do edge modelo-vs-mercado), aplicado antes; a
// política por faixa entra depois, só pro tamanho da stake e um corte de EV
// adicional específico da faixa.
//
// O número que importa de verdade pra "escolher o melhor modelo" não é o ROI
// simulado sozinho — com poucas centenas de jogos por liga, um ROI positivo
// isolado facilmente é ruído estatístico. Por isso cada grupo sai com um
// intervalo de confiança de 95% via bootstrap (reamostragem com reposição das
// apostas individuais, 2000 iterações) — só quando o limite INFERIOR do IC
// fica acima de zero é que dá pra considerar o edge estatisticamente real, não
// só sorte de amostra pequena.
//
// COMO CHAMAR:
//   /api/backtest-betting                                   (tudo, limiar/staking padrão)
//   /api/backtest-betting?edge_minimo=0.03&staking=kelly      (fração/corte de EV/teto vêm da faixa de odd de cada aposta)
//   /api/backtest-betting?modelo=dixon_coles_walkforward_v1&mercado=1X2&liga_id=4
//   /api/backtest-betting?usar_calibracao=platt   (usa a prob. calibrada em vez da crua, tanto pro edge quanto pro Kelly)
//
// Cada grupo em `grupos` traz `serie_temporal` (ver api/_lib/curvaPnlEv.js):
// Lucro Real e Valor Esperado (EV) acumulados cronologicamente + drawdown,
// aposta a aposta -- alimenta o gráfico "Curva de Retorno x EV" em
// ModelosStats.jsx. `league_id` já vai em cada ponto, então dá pra
// visualizar "especializações por campeonato" filtrando client-side sem
// chamada nova (ou repetindo a chamada com ?liga_id=X pra isolar 1 liga só).

import { createClient } from '@supabase/supabase-js';
import { applyCors } from './_lib/cors.js';
import { calcularCurvaPnlEv } from './_lib/curvaPnlEv.js';
import { calcularStakeKellyPorFaixa } from './_lib/stakingPolicy.js';
import { calcularResultadosReais } from './_lib/resultadosReais.js';

function getSupabase() {
  return createClient(process.env.SUPABASE_URL, process.env.SUPABASE_KEY);
}

const clamp = (p) => Math.min(Math.max(p, 1e-4), 1 - 1e-4);
const logit = (p) => Math.log(clamp(p) / (1 - clamp(p)));
const sigmoid = (x) => 1 / (1 + Math.exp(-x));

function aplicarPlatt(p, coef, intercept) { return sigmoid(coef * logit(p) + intercept); }
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

// v9 grava '1x2' (minúscula) em alguns pontos -- normaliza pro mesmo
// mercado antes de indexar `resultadosReais`.
function normalizarMercado(m) {
  return m === '1x2' ? '1X2' : m;
}

// `calcularResultadosReais` foi extraída pra api/_lib/resultadosReais.js
// (compartilhada com api/model-stats.js, era código idêntico duplicado nos
// dois arquivos) -- ver esse módulo pra explicação completa do porquê de
// mercado sem entrada ficar `undefined` de propósito.

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

// Normaliza `predicoes`/`market_odds` (pipeline "Model Benchmarking", ver
// scripts/rodar_predicoes.py) pro MESMO formato de `model_predictions`/
// `odds_market` (pipeline mais antigo, uma linha por seleção) -- ver
// api/model-stats.js pra explicação completa (mesma normalização,
// duplicada aqui pelo mesmo motivo do resto deste arquivo já duplicar
// devigar/buscarTudoPaginado/calcularResultadosReais em vez de importar).
function normalizarPredicoesBenchmarking(rows) {
  const linhas = [];
  for (const r of rows) {
    linhas.push({ model_name: r.model_name, market: '1X2', selection: 'home', probability: Number(r.prob_home), match_id: r.match_id });
    linhas.push({ model_name: r.model_name, market: '1X2', selection: 'draw', probability: Number(r.prob_draw), match_id: r.match_id });
    linhas.push({ model_name: r.model_name, market: '1X2', selection: 'away', probability: Number(r.prob_away), match_id: r.match_id });
  }
  return linhas;
}

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

// Bootstrap: reamostra os lucros individuais com reposição N vezes, devolve IC 95% do ROI
function bootstrapROI(apostas, iteracoes = 2000) {
  const n = apostas.length;
  if (n === 0) return { lo: null, hi: null };
  const rois = [];
  for (let iter = 0; iter < iteracoes; iter++) {
    let lucro = 0, staked = 0;
    for (let i = 0; i < n; i++) {
      const a = apostas[Math.floor(Math.random() * n)];
      lucro += a.lucro;
      staked += a.stake;
    }
    rois.push(staked > 0 ? lucro / staked : 0);
  }
  rois.sort((a, b) => a - b);
  return { lo: rois[Math.floor(iteracoes * 0.025)], hi: rois[Math.floor(iteracoes * 0.975)] };
}

export default async function handler(req, res) {
  if (applyCors(req, res)) return;
  const supabaseUrl = process.env.SUPABASE_URL, supabaseKey = process.env.SUPABASE_KEY;
  if (!supabaseUrl || !supabaseKey) return res.status(500).json({ error: { message: 'SUPABASE_URL / SUPABASE_KEY não configuradas.' } });
  const supabase = getSupabase();

  const { modelo, mercado, liga_id } = req.query;
  const edgeMinimo = req.query.edge_minimo != null ? Number(req.query.edge_minimo) : 0.02;
  const staking = req.query.staking === 'kelly' ? 'kelly' : 'flat';
  const usarCalibracao = ['platt', 'isotonic'].includes(req.query.usar_calibracao) ? req.query.usar_calibracao : 'nenhuma';

  try {
    const [predicoesAntigas, predicoesBenchmarkingRaw] = await Promise.all([
      buscarTudoPaginado(() => {
        let q = supabase.from('model_predictions').select('id, model_name, market, selection, probability, match_id');
        if (modelo) q = q.eq('model_name', modelo);
        if (mercado) q = q.eq('market', mercado);
        return q;
      }),
      // `predicoes` (Model Benchmarking) só tem 1X2 -- pedir outro mercado já filtra tudo fora.
      mercado && mercado !== '1X2'
        ? Promise.resolve([])
        : buscarTudoPaginado(() => {
            let q = supabase.from('predicoes').select('match_id, model_name, prob_home, prob_draw, prob_away').eq('mercado', '1X2');
            if (modelo) q = q.eq('model_name', modelo);
            return q;
          }),
    ]);
    const predicoes = [...predicoesAntigas, ...normalizarPredicoesBenchmarking(predicoesBenchmarkingRaw)];
    if (!predicoes || predicoes.length === 0) return res.status(200).json({ grupos: [], resumo_geral: null });

    const matchIdsSet = new Set(predicoes.map(p => p.match_id));

    const [todasMatches, oddsRowsAntigas, oddsRowsPinnacle, marketOddsRaw, corneragensBrutas, calibracoes] = await Promise.all([
      buscarTudoPaginado(() => supabase.from('matches').select('id, league_id, status, home_goals, away_goals, match_date')),
      buscarTudoPaginado(() => supabase.from('odds_market').select('match_id, market, selection, odds').eq('snapshot', 'closing').eq('bookmaker', 'media_mercado')),
      // Fallback pra `bookmaker='pinnacle'` -- `media_mercado` só existe pros
      // 3 mercados do pipeline antigo (1X2/over_under_2.5/btts, ver
      // `scripts/ingestar_felipe_kaggle_odds.py`, fonte histórica tipo
      // football-data.co.uk que nunca vai ter coluna de gol por time). Os
      // mercados novos de gols por time (`over_under_team_1/2_X.X`) só têm
      // odds reais em `odds_market` por bookmaker individual (pinnacle
      // incluída, ~20-25k linhas por linha desde 14/08/2026) -- sem esse
      // fallback, `backtest-betting` nunca encontraria odds pra eles e
      // `grupos` sairia sempre vazio. `pinnacle` aqui é odd de UMA casa
      // (referência de mercado eficiente, menor margem), não a média de
      // várias -- diferente de `media_mercado`, que já É uma média. Usado
      // só quando `media_mercado` não cobre o match+mercado (ver o merge
      // logo abaixo, `oddsRowsAntigas` tem prioridade).
      buscarTudoPaginado(() => supabase.from('odds_market').select('match_id, market, selection, odds').eq('snapshot', 'closing').eq('bookmaker', 'pinnacle')),
      buscarTudoPaginado(() => supabase.from('market_odds').select('match_id, odd_home, odd_draw, odd_away')),
      // Sem filtro `.not(...)` -- também precisamos de shots/shots_on_target
      // (mercados novos), que nem sempre são preenchidos junto com corners
      // (achado real: 2226 linhas têm shots sem corners, ou vice-versa).
      buscarTudoPaginado(() => supabase.from('match_stats').select('match_id, corners, shots, shots_on_target')),
      buscarTudoPaginado(() => supabase.from('model_calibration').select('model_name, market, selection, method, platt_coef, platt_intercept, isotonic_x, isotonic_y')),
    ]);
    // Merge com prioridade pra media_mercado: só usa pinnacle pro par
    // match_id+market que media_mercado NÃO cobre (evita duplicar/preferir
    // 1 casa só quando já existe uma média melhor pros 3 mercados antigos).
    const chavesComMediaMercado = new Set(oddsRowsAntigas.map((r) => `${r.match_id}__${r.market}`));
    const oddsRowsPinnacleFallback = oddsRowsPinnacle.filter((r) => !chavesComMediaMercado.has(`${r.match_id}__${r.market}`));
    const oddsRowsBrutas = [...oddsRowsAntigas, ...oddsRowsPinnacleFallback, ...normalizarOddsBenchmarking(marketOddsRaw)];

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
    const matchPorId = {};
    matchesValidos.forEach(m => { matchPorId[m.id] = m; });

    const oddsRows = oddsRowsBrutas.filter(r => matchIdsValidos.has(r.match_id));

    const corners = {};
    const shots = {};
    const shotsOnTarget = {};
    {
      // Cada stat com sua própria soma/contagem -- cobertura difere entre
      // elas (ver comentário na query acima), não dá pra reusar uma
      // contagem só entre as 3.
      const soma = { corners: {}, shots: {}, shots_on_target: {} };
      const cont = { corners: {}, shots: {}, shots_on_target: {} };
      corneragensBrutas.filter(r => matchIdsValidos.has(r.match_id)).forEach(r => {
        if (r.corners != null) { soma.corners[r.match_id] = (soma.corners[r.match_id] || 0) + Number(r.corners); cont.corners[r.match_id] = (cont.corners[r.match_id] || 0) + 1; }
        if (r.shots != null) { soma.shots[r.match_id] = (soma.shots[r.match_id] || 0) + Number(r.shots); cont.shots[r.match_id] = (cont.shots[r.match_id] || 0) + 1; }
        if (r.shots_on_target != null) { soma.shots_on_target[r.match_id] = (soma.shots_on_target[r.match_id] || 0) + Number(r.shots_on_target); cont.shots_on_target[r.match_id] = (cont.shots_on_target[r.match_id] || 0) + 1; }
      });
      Object.keys(soma.corners).forEach(id => { if (cont.corners[id] === 2) corners[id] = soma.corners[id]; });
      Object.keys(soma.shots).forEach(id => { if (cont.shots[id] === 2) shots[id] = soma.shots[id]; });
      Object.keys(soma.shots_on_target).forEach(id => { if (cont.shots_on_target[id] === 2) shotsOnTarget[id] = soma.shots_on_target[id]; });
    }

    const resultadosReais = calcularResultadosReais(matchesValidos, { corners, shots, shots_on_target: shotsOnTarget });

    // odds cruas (pra pagamento real) e devigadas (pra edge) por match+market
    const oddsPorMatchMercado = {};
    oddsRows.forEach(r => {
      const chave = `${r.match_id}__${r.market}`;
      if (!oddsPorMatchMercado[chave]) oddsPorMatchMercado[chave] = {};
      oddsPorMatchMercado[chave][r.selection] = Number(r.odds);
    });
    const probMercadoPorChave = {};
    Object.entries(oddsPorMatchMercado).forEach(([chave, oddsSel]) => { probMercadoPorChave[chave] = devigar(oddsSel); });

    // Monta as apostas candidatas: precisa de odds (senão não dá pra apostar de verdade)
    const candidatas = [];
    for (const p of predicoes) {
      if (!matchIdsValidos.has(p.match_id)) continue;
      const match = matchPorId[p.match_id];
      const resultado = resultadosReais[p.match_id];
      if (!resultado) continue; // não finalizada

      const chaveOdds = `${p.match_id}__${p.market}`;
      const oddReal = oddsPorMatchMercado[chaveOdds]?.[p.selection];
      const pMercado = probMercadoPorChave[chaveOdds]?.[p.selection];
      if (oddReal == null || pMercado == null) continue; // sem odds = não simula

      let pAposta = Number(p.probability);
      if (usarCalibracao !== 'nenhuma') {
        const calib = calibPorChave[`${p.model_name}__${p.market}__${p.selection}`]?.[usarCalibracao];
        if (!calib) continue; // pediu calibração e não tem — não entra no backtest
        pAposta = usarCalibracao === 'platt' ? aplicarPlatt(pAposta, calib.a, calib.b) : aplicarIsotonic(pAposta, calib.x, calib.y);
      }

      const edge = pAposta - pMercado;
      if (edge < edgeMinimo) continue;

      const venceu = resultadosReais[p.match_id][normalizarMercado(p.market)] === p.selection ? 1 : 0;
      let stakeUnitario = 1;
      if (staking === 'kelly') {
        const politica = calcularStakeKellyPorFaixa(pAposta, oddReal);
        if (!politica.apostar) continue; // fora da política por faixa (odd<1.30, EV abaixo do corte da faixa, ou Kelly completo negativo)
        stakeUnitario = politica.stakeFracaoBanca;
      }
      if (stakeUnitario <= 0) continue;

      const lucro = venceu ? stakeUnitario * (oddReal - 1) : -stakeUnitario;

      candidatas.push({
        match_id: p.match_id, model_name: p.model_name, market: p.market, selection: p.selection, league_id: match.league_id,
        match_date: match.match_date, edge, p_aposta: pAposta, odd: oddReal, stake: stakeUnitario, lucro, venceu,
      });
    }

    candidatas.sort((a, b) => new Date(a.match_date) - new Date(b.match_date));

    const porGrupo = {};
    candidatas.forEach(a => {
      const chave = `${a.model_name}__${a.market}__${a.selection}__${a.league_id}`;
      if (!porGrupo[chave]) porGrupo[chave] = { model_name: a.model_name, market: a.market, selection: a.selection, league_id: a.league_id, apostas: [] };
      porGrupo[chave].apostas.push(a);
    });

    const grupos = Object.values(porGrupo).map(g => {
      const staked = g.apostas.reduce((s, a) => s + a.stake, 0);
      const lucro = g.apostas.reduce((s, a) => s + a.lucro, 0);
      const roi = staked > 0 ? lucro / staked : 0;
      const vitorias = g.apostas.filter(a => a.venceu).length;
      const ic = bootstrapROI(g.apostas);
      return {
        model_name: g.model_name, market: g.market, selection: g.selection, league_id: g.league_id,
        n_apostas: g.apostas.length, taxa_acerto: vitorias / g.apostas.length,
        staked_total: staked, lucro_total: lucro, roi,
        roi_ic95_inferior: ic.lo, roi_ic95_superior: ic.hi,
        significativo: ic.lo != null && ic.lo > 0,
        edge_medio: g.apostas.reduce((s, a) => s + a.edge, 0) / g.apostas.length,
        // Curva de Retorno (PnL) x Valor Esperado (EV), cronológica -- pedido
        // do usuário. g.apostas já vem ordenado (candidatas é ordenado antes
        // de agrupar, .push preserva a ordem relativa dentro de cada grupo).
        serie_temporal: calcularCurvaPnlEv(g.apostas),
      };
    });

    grupos.sort((a, b) => (b.roi_ic95_inferior ?? -Infinity) - (a.roi_ic95_inferior ?? -Infinity));

    const stakedGeral = candidatas.reduce((s, a) => s + a.stake, 0);
    const lucroGeral = candidatas.reduce((s, a) => s + a.lucro, 0);
    const icGeral = bootstrapROI(candidatas);
    const resumoGeral = candidatas.length > 0 ? {
      n_apostas: candidatas.length, staked_total: stakedGeral, lucro_total: lucroGeral,
      roi: stakedGeral > 0 ? lucroGeral / stakedGeral : 0,
      roi_ic95_inferior: icGeral.lo, roi_ic95_superior: icGeral.hi,
      significativo: icGeral.lo != null && icGeral.lo > 0,
    } : null;

    res.status(200).json({
      parametros: { edge_minimo: edgeMinimo, staking, staking_por_faixa: staking === 'kelly', usar_calibracao: usarCalibracao },
      resumo_geral: resumoGeral,
      grupos,
    });
  } catch (erro) {
    res.status(500).json({ error: { message: erro.message } });
  }
}
