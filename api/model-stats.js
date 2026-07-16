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
  return m === '1X2' ? '1X2' : m === 'over_under_2.5' ? 'over_under_2_5' : 'corners_over_under_9_5';
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

export default async function handler(req, res) {
  const supabaseUrl = process.env.SUPABASE_URL, supabaseKey = process.env.SUPABASE_KEY;
  if (!supabaseUrl || !supabaseKey) return res.status(500).json({ error: { message: 'SUPABASE_URL / SUPABASE_KEY não configuradas.' } });
  const supabase = getSupabase();

  const { modelo, mercado, liga_id } = req.query;

  try {
    const predicoes = await buscarTudoPaginado(() => {
      let q = supabase.from('model_predictions').select('id, model_name, market, selection, probability, match_id');
      if (modelo) q = q.eq('model_name', modelo);
      if (mercado) q = q.eq('market', mercado);
      return q;
    });
    if (!predicoes || predicoes.length === 0) return res.status(200).json({ grupos: [] });

    const matchIdsSet = new Set(predicoes.map(p => p.match_id));

    // Busca as tabelas inteiras já filtradas pelos critérios FIXOS (bem menores
    // que o universo de match_ids das previsões) e filtra em JS — bem menos
    // round-trips do que quebrar em lotes de match_id.
    const [todasMatches, oddsRowsBrutas, corneragensBrutas, calibracoes] = await Promise.all([
      buscarTudoPaginado(() => supabase.from('matches').select('id, league_id, status, home_goals, away_goals')),
      buscarTudoPaginado(() => supabase.from('odds_market').select('match_id, market, selection, odds').eq('snapshot', 'closing').eq('bookmaker', 'media_mercado')),
      buscarTudoPaginado(() => supabase.from('match_stats').select('match_id, corners').not('corners', 'is', null)),
      buscarTudoPaginado(() => supabase.from('model_calibration').select('model_name, market, selection, method, platt_coef, platt_intercept, isotonic_x, isotonic_y')),
    ]);

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
    {
      const somaPorJogo = {};
      const contPorJogo = {};
      corneragensBrutas.filter(r => matchIdsValidos.has(r.match_id)).forEach(r => {
        somaPorJogo[r.match_id] = (somaPorJogo[r.match_id] || 0) + Number(r.corners);
        contPorJogo[r.match_id] = (contPorJogo[r.match_id] || 0) + 1;
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
            quintis.push({
              previsto_medio: fatia.reduce((s, l) => s + l.p_modelo, 0) / fatia.length,
              real: fatia.reduce((s, l) => s + l.y, 0) / fatia.length,
              n: fatia.length,
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
