// api/fit-calibration.js
// Roda no SERVIDOR do Vercel. Variáveis de ambiente necessárias:
//   SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY -> mesmas de sync-matches.js
//
// Reajusta os coeficientes de calibração (Platt Scaling + Isotonic Regression)
// salvos em public.model_calibration, um por (model_name, market, selection).
// Diferente da primeira rodada (achado #7 do CONTEXTO_PROJETO.md, feita ad hoc
// em Python fora do repo), essa versão cobre TODAS as seleções de TODOS os
// mercados — inclusive 1X2 (calibração one-vs-rest por seleção: home/draw/away
// cada um como um problema binário independente), não só os 3 pares
// model+market que tinham amostra suficiente na rodada anterior.
//
// Split temporal 70/30 (treino = 70% mais antigo, teste = 30% mais recente)
// pra evitar vazamento de informação futura pro passado — mesmo critério já
// usado antes. Platt: regressão logística 1D (y ~ sigmoid(a*logit(p)+b)) via
// gradiente descendente. Isotonic: Pool Adjacent Violators Algorithm (PAVA)
// puro em JS, guardado como pontos (x,y) pra interpolação linear na hora de
// aplicar (não dá pra guardar como 2 números só, por isso as colunas
// isotonic_x/isotonic_y novas em jsonb).
//
// COMO CHAMAR:
//   /api/fit-calibration            (reajusta tudo)
//   /api/fit-calibration?minimo=100 (exige pelo menos N amostras de treino por combo, padrão 80)

import { createClient } from '@supabase/supabase-js';

function getSupabase() {
  return createClient(process.env.SUPABASE_URL, process.env.SUPABASE_SERVICE_ROLE_KEY);
}

const clamp = (p) => Math.min(Math.max(p, 1e-4), 1 - 1e-4);
const logit = (p) => Math.log(clamp(p) / (1 - clamp(p)));
const sigmoid = (x) => 1 / (1 + Math.exp(-x));
const logLoss = (p, y) => (y ? -Math.log(clamp(p)) : -Math.log(1 - clamp(p)));

function chaveMercado(m) {
  return m === '1X2' ? '1X2' : m === 'over_under_2.5' ? 'over_under_2_5' : 'corners_over_under_9_5';
}

async function buscarTudoPaginado(supabase, criarQuery) {
  const TAMANHO_PAGINA = 1000;
  const resultado = [];
  let pagina = 0;
  while (true) {
    const { data, error } = await criarQuery(supabase).range(pagina * TAMANHO_PAGINA, pagina * TAMANHO_PAGINA + TAMANHO_PAGINA - 1);
    if (error) throw error;
    resultado.push(...(data || []));
    if (!data || data.length < TAMANHO_PAGINA) break;
    pagina++;
  }
  return resultado;
}

// Regressão logística 1D por gradiente descendente: y ~ sigmoid(a*x + b)
function ajustarPlatt(xs, ys) {
  let a = 1, b = 0;
  const n = xs.length;
  const taxaAprendizado = 0.1;
  for (let iter = 0; iter < 800; iter++) {
    let gradA = 0, gradB = 0;
    for (let i = 0; i < n; i++) {
      const pred = sigmoid(a * xs[i] + b);
      const erro = pred - ys[i];
      gradA += erro * xs[i];
      gradB += erro;
    }
    a -= (taxaAprendizado * gradA) / n;
    b -= (taxaAprendizado * gradB) / n;
  }
  return { a, b };
}

// Pool Adjacent Violators Algorithm — regressão isotônica (não-decrescente) em x=p, y=resultado real
function ajustarIsotonic(xs, ys) {
  const ordem = xs.map((x, i) => i).sort((i, j) => xs[i] - xs[j]);
  const blocos = ordem.map(i => ({ somaX: xs[i], somaY: ys[i], n: 1 }));

  let mudou = true;
  while (mudou) {
    mudou = false;
    for (let i = 0; i < blocos.length - 1; i++) {
      const mediaAtual = blocos[i].somaY / blocos[i].n;
      const mediaProxima = blocos[i + 1].somaY / blocos[i + 1].n;
      if (mediaAtual > mediaProxima) {
        blocos[i] = { somaX: blocos[i].somaX + blocos[i + 1].somaX, somaY: blocos[i].somaY + blocos[i + 1].somaY, n: blocos[i].n + blocos[i + 1].n };
        blocos.splice(i + 1, 1);
        mudou = true;
        break;
      }
    }
  }

  return { x: blocos.map(bl => bl.somaX / bl.n), y: blocos.map(bl => bl.somaY / bl.n) };
}

export default async function handler(req, res) {
  const supabaseUrl = process.env.SUPABASE_URL, serviceKey = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!supabaseUrl || !serviceKey) {
    return res.status(500).json({ error: { message: 'SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY não configuradas.' } });
  }
  const supabase = getSupabase();
  const minimo = Number(req.query.minimo) || 80;

  try {
    const predicoes = await buscarTudoPaginado(supabase, (sb) => sb.from('model_predictions').select('id, model_name, market, selection, probability, match_id'));
    const matches = await buscarTudoPaginado(supabase, (sb) => sb.from('matches').select('id, status, home_goals, away_goals, match_date').eq('status', 'finished').not('home_goals', 'is', null));
    const corneragens = await buscarTudoPaginado(supabase, (sb) => sb.from('match_stats').select('match_id, corners').not('corners', 'is', null));

    const matchPorId = {};
    matches.forEach(m => { matchPorId[m.id] = m; });

    const cornersPorJogo = {};
    { const soma = {}, cont = {};
      corneragens.forEach(r => { soma[r.match_id] = (soma[r.match_id] || 0) + Number(r.corners); cont[r.match_id] = (cont[r.match_id] || 0) + 1; });
      Object.keys(soma).forEach(id => { if (cont[id] === 2) cornersPorJogo[id] = soma[id]; });
    }

    function resultadoReal(matchId, market, selection) {
      const m = matchPorId[matchId];
      if (!m) return null;
      const chave = chaveMercado(market);
      if (chave === '1X2') {
        const real = m.home_goals > m.away_goals ? 'home' : m.home_goals < m.away_goals ? 'away' : 'draw';
        return real === selection ? 1 : 0;
      }
      if (chave === 'over_under_2_5') {
        const total = m.home_goals + m.away_goals;
        const real = total > 2.5 ? 'over' : 'under';
        return real === selection ? 1 : 0;
      }
      if (cornersPorJogo[matchId] == null) return null;
      const real = cornersPorJogo[matchId] > 9.5 ? 'over' : 'under';
      return real === selection ? 1 : 0;
    }

    // Agrupa por model_name+market+selection, ordenado por data (pro split temporal)
    const grupos = {};
    for (const p of predicoes) {
      const m = matchPorId[p.match_id];
      if (!m) continue;
      const y = resultadoReal(p.match_id, p.market, p.selection);
      if (y == null) continue;
      const chave = `${p.model_name}__${p.market}__${p.selection}`;
      if (!grupos[chave]) grupos[chave] = { model_name: p.model_name, market: p.market, selection: p.selection, linhas: [] };
      grupos[chave].linhas.push({ p: Number(p.probability), y, data: m.match_date });
    }

    const resultado = { ajustados: [], ignorados_amostra_insuficiente: [] };

    for (const g of Object.values(grupos)) {
      g.linhas.sort((a, b) => new Date(a.data) - new Date(b.data));
      const corte = Math.floor(g.linhas.length * 0.7);
      const treino = g.linhas.slice(0, corte);
      const teste = g.linhas.slice(corte);

      if (treino.length < minimo || teste.length < 20) {
        resultado.ignorados_amostra_insuficiente.push({ model_name: g.model_name, market: g.market, selection: g.selection, n_treino: treino.length, n_teste: teste.length });
        continue;
      }

      const xsTreino = treino.map(l => logit(l.p));
      const ysTreino = treino.map(l => l.y);
      const logLossBruto = teste.reduce((s, l) => s + logLoss(l.p, l.y), 0) / teste.length;

      const platt = ajustarPlatt(xsTreino, ysTreino);
      const logLossPlatt = teste.reduce((s, l) => s + logLoss(sigmoid(platt.a * logit(l.p) + platt.b), l.y), 0) / teste.length;

      const xsTreinoP = treino.map(l => l.p);
      const isotonic = ajustarIsotonic(xsTreinoP, ysTreino);
      const aplicarIsotonic = (p) => {
        const xs = isotonic.x, ys = isotonic.y;
        if (p <= xs[0]) return ys[0];
        if (p >= xs[xs.length - 1]) return ys[ys.length - 1];
        for (let i = 0; i < xs.length - 1; i++) {
          if (p >= xs[i] && p <= xs[i + 1]) {
            const t = (p - xs[i]) / (xs[i + 1] - xs[i] || 1);
            return ys[i] + t * (ys[i + 1] - ys[i]);
          }
        }
        return p;
      };
      const logLossIsotonic = teste.reduce((s, l) => s + logLoss(aplicarIsotonic(l.p), l.y), 0) / teste.length;

      const base = { model_name: g.model_name, market: g.market, selection: g.selection, n_treino: treino.length, n_teste: teste.length, log_loss_bruto: logLossBruto, fitted_at: new Date().toISOString() };

      const linhaPlatt = { ...base, method: 'platt', platt_coef: platt.a, platt_intercept: platt.b, log_loss_calibrado: logLossPlatt, isotonic_x: null, isotonic_y: null };
      const linhaIsotonic = { ...base, method: 'isotonic', platt_coef: null, platt_intercept: null, log_loss_calibrado: logLossIsotonic, isotonic_x: isotonic.x, isotonic_y: isotonic.y };

      const { error: erroUpsert } = await supabase.from('model_calibration').upsert([linhaPlatt, linhaIsotonic], { onConflict: 'model_name,market,selection,method' });
      // (onConflict casa com a constraint model_calibration_unique_combo, criada
      // na migração — precisa ser um unique constraint em colunas literais pro
      // PostgREST conseguir gerar o ON CONFLICT, índice de expressão não serve)
      if (erroUpsert) throw erroUpsert;

      resultado.ajustados.push({
        model_name: g.model_name, market: g.market, selection: g.selection,
        n_treino: treino.length, n_teste: teste.length,
        log_loss_bruto: logLossBruto, log_loss_platt: logLossPlatt, log_loss_isotonic: logLossIsotonic,
      });
    }

    res.status(200).json(resultado);
  } catch (erro) {
    res.status(500).json({ error: { message: erro.message } });
  }
}
