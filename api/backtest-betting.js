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
// em vez de path-dependentes).
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
//   /api/backtest-betting?edge_minimo=0.03&staking=kelly&fracao_kelly=0.25
//   /api/backtest-betting?modelo=dixon_coles_walkforward_v1&mercado=1X2&liga_id=4
//   /api/backtest-betting?usar_calibracao=platt   (usa a prob. calibrada em vez da crua, tanto pro edge quanto pro Kelly)

import { createClient } from '@supabase/supabase-js';

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

function chaveMercado(m) {
  return m === '1X2' ? '1X2' : m === 'over_under_2.5' ? 'over_under_2_5' : 'corners_over_under_9_5';
}

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

function devigar(oddsPorSelecao) {
  const implicitas = {};
  let soma = 0;
  for (const [sel, odd] of Object.entries(oddsPorSelecao)) { implicitas[sel] = 1 / odd; soma += implicitas[sel]; }
  const normalizadas = {};
  for (const sel of Object.keys(implicitas)) normalizadas[sel] = implicitas[sel] / soma;
  return normalizadas;
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

// Fração de Kelly clássica pra aposta binária: f* = (p*b - (1-p)) / b, b = odd-1
function fracaoKelly(p, odd) {
  const b = odd - 1;
  if (b <= 0) return 0;
  const f = (p * b - (1 - p)) / b;
  return Math.max(0, f);
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
  const supabaseUrl = process.env.SUPABASE_URL, supabaseKey = process.env.SUPABASE_KEY;
  if (!supabaseUrl || !supabaseKey) return res.status(500).json({ error: { message: 'SUPABASE_URL / SUPABASE_KEY não configuradas.' } });
  const supabase = getSupabase();

  const { modelo, mercado, liga_id } = req.query;
  const edgeMinimo = req.query.edge_minimo != null ? Number(req.query.edge_minimo) : 0.02;
  const staking = req.query.staking === 'kelly' ? 'kelly' : 'flat';
  const fracaoKellyMult = req.query.fracao_kelly != null ? Number(req.query.fracao_kelly) : 0.25;
  const usarCalibracao = ['platt', 'isotonic'].includes(req.query.usar_calibracao) ? req.query.usar_calibracao : 'nenhuma';

  try {
    const predicoes = await buscarTudoPaginado(() => {
      let q = supabase.from('model_predictions').select('id, model_name, market, selection, probability, match_id');
      if (modelo) q = q.eq('model_name', modelo);
      if (mercado) q = q.eq('market', mercado);
      return q;
    });
    if (!predicoes || predicoes.length === 0) return res.status(200).json({ grupos: [], resumo_geral: null });

    const matchIdsSet = new Set(predicoes.map(p => p.match_id));

    const [todasMatches, oddsRowsBrutas, corneragensBrutas, calibracoes] = await Promise.all([
      buscarTudoPaginado(() => supabase.from('matches').select('id, league_id, status, home_goals, away_goals, match_date')),
      buscarTudoPaginado(() => supabase.from('odds_market').select('match_id, market, selection, odds').eq('snapshot', 'closing').eq('bookmaker', 'media_mercado')),
      buscarTudoPaginado(() => supabase.from('match_stats').select('match_id, corners').not('corners', 'is', null)),
      buscarTudoPaginado(() => supabase.from('model_calibration').select('model_name, market, selection, method, platt_coef, platt_intercept, isotonic_x, isotonic_y')),
    ]);

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
    { const soma = {}, cont = {};
      corneragensBrutas.filter(r => matchIdsValidos.has(r.match_id)).forEach(r => {
        soma[r.match_id] = (soma[r.match_id] || 0) + Number(r.corners);
        cont[r.match_id] = (cont[r.match_id] || 0) + 1;
      });
      Object.keys(soma).forEach(id => { if (cont[id] === 2) corners[id] = soma[id]; });
    }

    const resultadosReais = calcularResultadosReais(matchesValidos, corners);

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

      const venceu = resultadosReais[p.match_id][chaveMercado(p.market)] === p.selection ? 1 : 0;
      const stakeUnitario = staking === 'kelly' ? Math.min(fracaoKelly(pAposta, oddReal) * fracaoKellyMult, 0.25) : 1;
      if (stakeUnitario <= 0) continue;

      const lucro = venceu ? stakeUnitario * (oddReal - 1) : -stakeUnitario;

      candidatas.push({
        model_name: p.model_name, market: p.market, selection: p.selection, league_id: match.league_id,
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
      parametros: { edge_minimo: edgeMinimo, staking, fracao_kelly: staking === 'kelly' ? fracaoKellyMult : null, usar_calibracao: usarCalibracao },
      resumo_geral: resumoGeral,
      grupos,
    });
  } catch (erro) {
    res.status(500).json({ error: { message: erro.message } });
  }
}
