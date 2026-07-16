// api/model-stats.js
// Roda no SERVIDOR do Vercel. Variáveis de ambiente necessárias:
//   SUPABASE_URL / SUPABASE_KEY  -> mesmas do team-stats.js (leitura pública, RLS)
//
// Painel de estatísticas dos modelos de previsão (model_predictions): calcula
// Brier Score e log-loss do modelo contra o resultado real de cada partida, e
// compara com a odd de fechamento MÉDIA do mercado (bookmaker='media_mercado',
// snapshot='closing' em odds_market — a mais precisa disponível, já devigada
// aqui dentro pra virar probabilidade). Escanteios não têm odds no banco (só
// existe histórico de odds de gols), então nesse mercado só sai a métrica do
// modelo, sem comparação.
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
const logLossTermo = (p, y) => (y ? -Math.log(clamp(p)) : -Math.log(1 - clamp(p)));
const brierTermo = (p, y) => (p - y) ** 2;

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

export default async function handler(req, res) {
  const supabaseUrl = process.env.SUPABASE_URL, supabaseKey = process.env.SUPABASE_KEY;
  if (!supabaseUrl || !supabaseKey) return res.status(500).json({ error: { message: 'SUPABASE_URL / SUPABASE_KEY não configuradas.' } });
  const supabase = getSupabase();

  const { modelo, mercado, liga_id } = req.query;

  try {
    let query = supabase.from('model_predictions').select('id, model_name, market, selection, probability, match_id');
    if (modelo) query = query.eq('model_name', modelo);
    if (mercado) query = query.eq('market', mercado);
    const { data: predicoes, error: predErro } = await query;
    if (predErro) throw predErro;
    if (!predicoes || predicoes.length === 0) return res.status(200).json({ grupos: [] });

    const matchIds = [...new Set(predicoes.map(p => p.match_id))];

    // Busca tudo em lotes de 1000 (limite de URL/IN do PostgREST)
    const matches = [];
    for (let i = 0; i < matchIds.length; i += 1000) {
      const lote = matchIds.slice(i, i + 1000);
      const { data, error } = await supabase.from('matches').select('id, league_id, status, home_goals, away_goals').in('id', lote);
      if (error) throw error;
      matches.push(...(data || []));
    }
    if (liga_id) {
      const ligaIdNum = Number(liga_id);
      matches.forEach((m, i) => { if (m.league_id !== ligaIdNum) matches[i] = null; });
    }
    const matchesValidos = matches.filter(Boolean);
    const matchIdsValidos = new Set(matchesValidos.map(m => m.id));

    const oddsRows = [];
    for (let i = 0; i < matchIds.length; i += 1000) {
      const lote = matchIds.slice(i, i + 1000);
      const { data, error } = await supabase.from('odds_market').select('match_id, market, selection, odds')
        .in('match_id', lote).eq('snapshot', 'closing').eq('bookmaker', 'media_mercado');
      if (error) throw error;
      oddsRows.push(...(data || []));
    }

    const corners = {};
    for (let i = 0; i < matchIds.length; i += 1000) {
      const lote = matchIds.slice(i, i + 1000);
      const { data, error } = await supabase.from('match_stats').select('match_id, corners').in('match_id', lote).not('corners', 'is', null);
      if (error) throw error;
      const somaPorJogo = {};
      const contPorJogo = {};
      (data || []).forEach(r => {
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

      grupos[chaveGrupo].linhas.push({
        match_id: p.match_id, selection: p.selection, p_modelo: Number(p.probability), y, p_mercado: pMercado,
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

        return {
          selecao, n: ls.length,
          p_modelo_medio: ls.reduce((s, l) => s + l.p_modelo, 0) / ls.length,
          p_mercado_medio: comOdds.length > 0 ? comOdds.reduce((s, l) => s + l.p_mercado, 0) / comOdds.length : null,
          edge_medio: edgeMedio,
          calibracao: quintis,
        };
      });

      return {
        model_name: g.model_name, market: g.market, league_id: g.league_id,
        n_jogos: nJogos,
        log_loss_modelo: logLossModelo, brier_modelo: brierModelo,
        log_loss_mercado: logLossMercado, brier_mercado: brierMercado,
        tem_odds: temOdds,
        por_selecao: selecoes,
      };
    });

    saida.sort((a, b) => a.model_name.localeCompare(b.model_name) || a.market.localeCompare(b.market) || a.league_id - b.league_id);
    res.status(200).json({ grupos: saida });
  } catch (erro) {
    res.status(500).json({ error: { message: erro.message } });
  }
}
