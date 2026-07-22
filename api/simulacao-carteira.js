// api/simulacao-carteira.js
// Roda no SERVIDOR do Vercel. Variáveis de ambiente necessárias:
//   SUPABASE_URL / SUPABASE_KEY  -> mesmas de model-stats.js/backtest-betting.js
//
// Simulação de carteira RODADA A RODADA (não só ROI agregado como em
// backtest-betting.js) com Quarter Kelly e gerenciamento de risco estrito,
// pedido do usuário:
//   - Filtro de EV bruto: p_modelo * odd >= 1,02 (SEM devig -- é a probabilidade
//     crua do próprio modelo, não a comparada contra o mercado devigado que
//     backtest-betting.js usa). Isso é uma escolha deliberada da tarefa, não
//     um bug: aceita apostas em azarão com qualquer excesso de confiança do
//     modelo, mesmo pequeno, porque a odd alta amplifica o produto p*odd.
//   - Piso de ruído: descarta stake calculada < 0,5% da banca da rodada.
//   - Quarter Kelly: f = 0,25 * max(0, (p*b - (1-p))/b), b = odd-1.
//   - Teto de exposição por rodada: soma das stakes <= 15% da banca da
//     rodada -- se exceder, escala TODAS as stakes da rodada proporcionalmente.
//   - Banca da rodada r = banca de FECHAMENTO da rodada r-1, nunca recalculada
//     intra-rodada.
//   - "Rodada" = data de calendário (UTC) do match_date -- não existe
//     matchday/rodada unificado entre ligas internacionais diferentes, então
//     agrupar por dia é a definição mais consistente disponível nos dados.
//
// Execução na odd MÉDIA de fechamento (mesmo bookmaker='media_mercado',
// snapshot='closing' de model-stats.js/backtest-betting.js, ou a média das
// odds de market_odds pro pipeline "Model Benchmarking"). CLV é informativo
// (não afeta stake): compara a odd de ABERTURA vs FECHAMENTO da Pinnacle pra
// a seleção apostada -- só existe pro pipeline antigo (odds_market tem
// snapshot pre_closing/closing; market_odds não tem esse conceito).
//
// `usar_calibracao=platt|isotonic` aplica a correção salva em
// `model_calibration` (Platt Scaling / Isotonic Regression, ver
// api/fit-calibration.js) sobre a probabilidade CRUA antes do filtro de EV
// e do dimensionamento Kelly -- só existe pro pipeline antigo (os modelos
// "Model Benchmarking" já calibrados entram como `model_name` PRÓPRIO,
// ex. catboost_v5_calibrado_platt -- selecionar esse nome direto já dá o
// efeito equivalente, sem precisar de correção on-the-fly).
//
// COMO CHAMAR:
//   /api/simulacao-carteira?modelo=dixon_coles_walkforward_v1
//   /api/simulacao-carteira?modelo=catboost_v5&liga_id=4&temporada=2024
//   /api/simulacao-carteira?modelo=dixon_coles_v1&banca_inicial=5000
//   /api/simulacao-carteira?modelo=dixon_coles_v1&usar_calibracao=isotonic
//   /api/modelos-disponiveis  (lista todos os model_name com histórico resolvido -- ver handler abaixo)

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

const EV_MINIMO_PADRAO = 1.02;
const STAKE_MINIMA_PCT_PADRAO = 0.005;
const TETO_EXPOSICAO_PCT_PADRAO = 0.15;
const FRACAO_KELLY = 0.25;
const BANCA_INICIAL_PADRAO = 1000;

function fracaoKelly(p, odd) {
  const b = odd - 1;
  if (b <= 0) return 0;
  const f = (p * b - (1 - p)) / b;
  return Math.max(0, f) * FRACAO_KELLY;
}

function calcularResultado1x2(m) {
  if (m.status !== 'finished' || m.home_goals == null || m.away_goals == null) return null;
  return m.home_goals > m.away_goals ? 'home' : m.home_goals < m.away_goals ? 'away' : 'draw';
}

// Mesma normalização de model-stats.js/backtest-betting.js -- ver esses
// arquivos pra explicação completa.
function normalizarPredicoesBenchmarking(rows) {
  const linhas = [];
  for (const r of rows) {
    linhas.push({ model_name: r.model_name, selection: 'home', probability: Number(r.prob_home), match_id: r.match_id });
    linhas.push({ model_name: r.model_name, selection: 'draw', probability: Number(r.prob_draw), match_id: r.match_id });
    linhas.push({ model_name: r.model_name, selection: 'away', probability: Number(r.prob_away), match_id: r.match_id });
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
    linhas.push({ match_id: Number(matchId), selection: 'home', odds: acc.home / acc.n });
    linhas.push({ match_id: Number(matchId), selection: 'away', odds: acc.away / acc.n });
    if (acc.draw_n > 0) linhas.push({ match_id: Number(matchId), selection: 'draw', odds: acc.draw / acc.draw_n });
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

export default async function handler(req, res) {
  const supabaseUrl = process.env.SUPABASE_URL, supabaseKey = process.env.SUPABASE_KEY;
  if (!supabaseUrl || !supabaseKey) return res.status(500).json({ error: { message: 'SUPABASE_URL / SUPABASE_KEY não configuradas.' } });
  const supabase = getSupabase();

  const { modelo, liga_id, temporada } = req.query;
  if (!modelo) return res.status(400).json({ error: { message: 'Parâmetro "modelo" é obrigatório.' } });

  const evMinimo = req.query.ev_minimo != null ? Number(req.query.ev_minimo) : EV_MINIMO_PADRAO;
  const stakeMinimaPct = req.query.stake_minima_pct != null ? Number(req.query.stake_minima_pct) : STAKE_MINIMA_PCT_PADRAO;
  const tetoExposicaoPct = req.query.teto_exposicao_pct != null ? Number(req.query.teto_exposicao_pct) : TETO_EXPOSICAO_PCT_PADRAO;
  const bancaInicial = req.query.banca_inicial != null ? Number(req.query.banca_inicial) : BANCA_INICIAL_PADRAO;
  const usarCalibracao = ['platt', 'isotonic'].includes(req.query.usar_calibracao) ? req.query.usar_calibracao : 'nenhuma';

  try {
    const [predicoesAntigas, predicoesBenchmarkingRaw, calibracoesRaw] = await Promise.all([
      buscarTudoPaginado(() =>
        supabase.from('model_predictions').select('match_id, selection, probability').eq('model_name', modelo).eq('market', '1X2')
      ),
      buscarTudoPaginado(() =>
        supabase.from('predicoes').select('match_id, model_name, prob_home, prob_draw, prob_away').eq('model_name', modelo)
      ),
      usarCalibracao === 'nenhuma'
        ? Promise.resolve([])
        : buscarTudoPaginado(() =>
            supabase.from('model_calibration').select('selection, method, platt_coef, platt_intercept, isotonic_x, isotonic_y').eq('model_name', modelo).eq('market', '1X2')
          ),
    ]);
    const predicoes = [...predicoesAntigas, ...normalizarPredicoesBenchmarking(predicoesBenchmarkingRaw)];

    const calibPorSelecao = {};
    calibracoesRaw.forEach((c) => {
      if (c.method !== usarCalibracao) return;
      calibPorSelecao[c.selection] = c.method === 'platt'
        ? { tipo: 'platt', a: Number(c.platt_coef), b: Number(c.platt_intercept) }
        : { tipo: 'isotonic', x: c.isotonic_x, y: c.isotonic_y };
    });
    if (predicoes.length === 0) {
      return res.status(200).json({ parametros: {}, sumario: null, rodadas: [] });
    }

    const matchIdsSet = new Set(predicoes.map((p) => p.match_id));

    const [todasMatches, oddsFechaAntigas, marketOddsRaw, pinnacleAberturaRaw, pinnacleFechaRaw] = await Promise.all([
      buscarTudoPaginado(() => supabase.from('matches').select('id, league_id, season, status, home_goals, away_goals, match_date')),
      buscarTudoPaginado(() =>
        supabase.from('odds_market').select('match_id, selection, odds').eq('market', '1X2').eq('snapshot', 'closing').eq('bookmaker', 'media_mercado')
      ),
      buscarTudoPaginado(() => supabase.from('market_odds').select('match_id, odd_home, odd_draw, odd_away')),
      buscarTudoPaginado(() =>
        supabase.from('odds_market').select('match_id, selection, odds').eq('market', '1X2').eq('snapshot', 'pre_closing').eq('bookmaker', 'pinnacle')
      ),
      buscarTudoPaginado(() =>
        supabase.from('odds_market').select('match_id, selection, odds').eq('market', '1X2').eq('snapshot', 'closing').eq('bookmaker', 'pinnacle')
      ),
    ]);
    const oddsFechamento = [...oddsFechaAntigas, ...normalizarOddsBenchmarking(marketOddsRaw)];

    const ligaIdNum = liga_id ? Number(liga_id) : null;
    const matchesValidos = todasMatches.filter(
      (m) => matchIdsSet.has(m.id) && (!ligaIdNum || m.league_id === ligaIdNum) && (!temporada || String(m.season) === String(temporada))
    );
    const matchIdsValidos = new Set(matchesValidos.map((m) => m.id));
    const matchPorId = {};
    matchesValidos.forEach((m) => { matchPorId[m.id] = m; });

    const oddPorChave = {}; // `${match_id}__${selection}` -> odd fechamento
    oddsFechamento.filter((r) => matchIdsValidos.has(r.match_id)).forEach((r) => { oddPorChave[`${r.match_id}__${r.selection}`] = Number(r.odds); });

    const pinnAberturaPorChave = {};
    pinnacleAberturaRaw.filter((r) => matchIdsValidos.has(r.match_id)).forEach((r) => { pinnAberturaPorChave[`${r.match_id}__${r.selection}`] = Number(r.odds); });
    const pinnFechaPorChave = {};
    pinnacleFechaRaw.filter((r) => matchIdsValidos.has(r.match_id)).forEach((r) => { pinnFechaPorChave[`${r.match_id}__${r.selection}`] = Number(r.odds); });

    // Monta candidatos: precisa de partida finalizada + odd de fechamento real
    const candidatos = [];
    for (const p of predicoes) {
      if (!matchIdsValidos.has(p.match_id)) continue;
      const match = matchPorId[p.match_id];
      const resultadoReal = calcularResultado1x2(match);
      if (!resultadoReal) continue;
      const chave = `${p.match_id}__${p.selection}`;
      const odd = oddPorChave[chave];
      if (odd == null) continue;

      let pModelo = Number(p.probability);
      if (usarCalibracao !== 'nenhuma') {
        const calib = calibPorSelecao[p.selection];
        if (!calib) continue; // pediu correção e não existe calibração salva pra essa seleção -- não entra
        pModelo = calib.tipo === 'platt' ? aplicarPlatt(pModelo, calib.a, calib.b) : aplicarIsotonic(pModelo, calib.x, calib.y);
        if (pModelo == null) continue;
      }
      const ev = pModelo * odd;
      if (ev < evMinimo) continue;

      const oddPinnAbertura = pinnAberturaPorChave[chave] ?? null;
      const oddPinnFecha = pinnFechaPorChave[chave] ?? null;
      const clv = oddPinnAbertura != null && oddPinnFecha != null ? (oddPinnAbertura / oddPinnFecha - 1) * 100 : null;

      candidatos.push({
        match_id: p.match_id,
        data: match.match_date.slice(0, 10),
        league_id: match.league_id,
        selection: p.selection,
        p_modelo: pModelo,
        odd,
        ev,
        resultado_real: resultadoReal,
        clv,
      });
    }

    // ---- Simulação rodada a rodada (rodada = dia de calendário UTC) ----
    const porDia = {};
    candidatos.forEach((c) => { (porDia[c.data] = porDia[c.data] || []).push(c); });
    const diasOrdenados = Object.keys(porDia).sort();

    let banca = bancaInicial;
    let pico = bancaInicial;
    const rodadas = [];
    const todasApostas = [];

    diasOrdenados.forEach((dia) => {
      const bancaInicialRodada = banca;
      const brutas = porDia[dia].map((c) => ({ ...c, stake_bruta: fracaoKelly(c.p_modelo, c.odd) * bancaInicialRodada }));
      const pisoStake = stakeMinimaPct * bancaInicialRodada;
      const validas = brutas.filter((b) => b.stake_bruta >= pisoStake);
      if (validas.length === 0) return;

      const somaStakes = validas.reduce((s, v) => s + v.stake_bruta, 0);
      const teto = tetoExposicaoPct * bancaInicialRodada;
      const fatorEscala = somaStakes > 0 ? Math.min(1, teto / somaStakes) : 1;

      const apostasRodada = validas.map((v) => {
        const stake = v.stake_bruta * fatorEscala;
        const venceu = v.resultado_real === v.selection;
        const lucro = venceu ? stake * (v.odd - 1) : -stake;
        return { ...v, stake, venceu, lucro };
      });

      const resultadoLiquido = apostasRodada.reduce((s, a) => s + a.lucro, 0);
      const exposicaoTotal = apostasRodada.reduce((s, a) => s + a.stake, 0);
      const bancaFinalRodada = bancaInicialRodada + resultadoLiquido;
      pico = Math.max(pico, bancaFinalRodada);
      const drawdownAtual = pico > 0 ? (pico - bancaFinalRodada) / pico : 0;
      const vitorias = apostasRodada.filter((a) => a.venceu).length;

      rodadas.push({
        rodada: rodadas.length + 1,
        data: dia,
        banca_inicial: bancaInicialRodada,
        qtd_apostas: apostasRodada.length,
        exposicao_pct: (exposicaoTotal / bancaInicialRodada) * 100,
        vitorias,
        derrotas: apostasRodada.length - vitorias,
        resultado_liquido: resultadoLiquido,
        retorno_pct: (resultadoLiquido / bancaInicialRodada) * 100,
        banca_final: bancaFinalRodada,
        drawdown_pct: drawdownAtual * 100,
        escalado: fatorEscala < 0.999999,
      });
      todasApostas.push(...apostasRodada);
      banca = bancaFinalRodada;
    });

    // ---- Sumário executivo ----
    const bancaFinal = banca;
    const roiTotalPct = ((bancaFinal - bancaInicial) / bancaInicial) * 100;

    let cagrPct = null, diasTotais = 0;
    if (diasOrdenados.length > 0 && rodadas.length > 0) {
      const d0 = new Date(diasOrdenados[0]);
      const d1 = new Date(rodadas[rodadas.length - 1].data);
      diasTotais = Math.max(Math.round((d1 - d0) / 86400000), 1);
      const anos = diasTotais / 365;
      if (bancaFinal > 0 && anos > 0) cagrPct = (Math.pow(bancaFinal / bancaInicial, 1 / anos) - 1) * 100;
    }

    const curva = [bancaInicial, ...rodadas.map((r) => r.banca_final)];
    let picoCorrente = curva[0], picoIdxCorrente = 0, mdd = 0, idxPicoMdd = 0, idxFundoMdd = 0;
    curva.forEach((v, i) => {
      if (v > picoCorrente) { picoCorrente = v; picoIdxCorrente = i; }
      const dd = picoCorrente > 0 ? (picoCorrente - v) / picoCorrente : 0;
      if (dd > mdd) { mdd = dd; idxPicoMdd = picoIdxCorrente; idxFundoMdd = i; }
    });

    const totalApostas = todasApostas.length;
    const vitoriasTotais = todasApostas.filter((a) => a.venceu).length;
    const winRatePct = totalApostas > 0 ? (vitoriasTotais / totalApostas) * 100 : null;

    const comClv = todasApostas.filter((a) => a.clv != null);
    const clvMedioPct = comClv.length > 0 ? comClv.reduce((s, a) => s + a.clv, 0) / comClv.length : null;

    const retornosRodada = rodadas.map((r) => r.retorno_pct / 100);
    let sharpe = null, sortino = null;
    if (retornosRodada.length > 1) {
      const media = retornosRodada.reduce((s, r) => s + r, 0) / retornosRodada.length;
      const variancia = retornosRodada.reduce((s, r) => s + (r - media) ** 2, 0) / (retornosRodada.length - 1);
      const desvio = Math.sqrt(variancia);
      sharpe = desvio > 0 ? media / desvio : null;
      const negativos = retornosRodada.filter((r) => r < 0);
      if (negativos.length > 1) {
        const downsideVar = negativos.reduce((s, r) => s + r ** 2, 0) / negativos.length;
        const downsideStd = Math.sqrt(downsideVar);
        sortino = downsideStd > 0 ? media / downsideStd : null;
      }
    }

    const sumario = {
      modelo,
      n_candidatos_brutos: predicoes.length,
      n_passaram_ev: candidatos.length,
      n_rodadas_com_aposta: rodadas.length,
      n_apostas_totais: totalApostas,
      banca_inicial: bancaInicial,
      banca_final: bancaFinal,
      roi_total_pct: roiTotalPct,
      cagr_pct: cagrPct,
      dias_totais: diasTotais,
      mdd_pct: mdd * 100,
      mdd_pico_valor: rodadas.length ? curva[idxPicoMdd] : null,
      mdd_pico_rodada: idxPicoMdd,
      mdd_fundo_valor: rodadas.length ? curva[idxFundoMdd] : null,
      mdd_fundo_rodada: idxFundoMdd,
      win_rate_pct: winRatePct,
      clv_medio_pct: clvMedioPct,
      n_bets_com_clv: comClv.length,
      sharpe_simplificado: sharpe,
      sortino_simplificado: sortino,
    };

    res.status(200).json({
      parametros: { modelo, liga_id: ligaIdNum, temporada: temporada || null, usar_calibracao: usarCalibracao, ev_minimo: evMinimo, stake_minima_pct: stakeMinimaPct, teto_exposicao_pct: tetoExposicaoPct, banca_inicial: bancaInicial },
      sumario,
      rodadas,
    });
  } catch (erro) {
    res.status(500).json({ error: { message: erro.message } });
  }
}
