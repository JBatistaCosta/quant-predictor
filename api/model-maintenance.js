// api/model-maintenance.js
// Roda no SERVIDOR do Vercel. Variáveis de ambiente necessárias:
//   SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY -> mesmas de sync-matches.js
//
// Dois endpoints administrativos (chamados manualmente, não pelo frontend)
// FUNDIDOS num arquivo só: o plano Hobby do Vercel limita 12 Serverless
// Functions por deployment, e eram dois arquivos separados (compute-elo.js +
// fit-calibration.js) até o deploy do backtest de apostas estourar esse
// limite (13º arquivo) — erro `exceeded_serverless_functions_per_deployment`.
// Nenhum dos dois é referenciado pelo frontend, só chamados via curl direto,
// então fundir é seguro (só muda a URL de chamada).
//
// COMO CHAMAR:
//   ?tarefa=elo&liga_id=X       -> recalcula o Elo interno de UMA liga doméstica (1,4,7,10,13,16)
//   ?tarefa=elo&escopo=geral    -> recalcula o Elo geral (cross-liga, Champions League)
//   ?tarefa=calibracao          -> reajusta Platt Scaling + Isotonic Regression (todos os combos)
//   ?tarefa=calibracao&minimo=N -> idem, exigindo N amostras de treino mínimas (padrão 80)
//   ?tarefa=odds-descobrir      -> FASE 1 do sync de odds (OddsPapi): resolve torneios/mercados/
//                                   casas uma vez só (precisa de ODDSPAPI_KEY) — ver comentário
//                                   detalhado na função abaixo antes de rodar (cota é 250 req/mês)
//   ?tarefa=odds&liga_id=X      -> FASE 2: sincroniza odds de verdade de uma liga (3 chamadas,
//                                   uma por bookmaker) — pula sozinho se não houver jogo
//                                   suficiente no curto prazo, pra não gastar cota à toa
//   ?tarefa=odds-todas          -> roda tarefa=odds nas 6 ligas domésticas em sequência
//                                   (usado pelo cron em vercel.json)
//
// Documentação detalhada de cada tarefa nos comentários das funções abaixo
// (mesma lógica que estava nos arquivos originais compute-elo.js/fit-calibration.js).

import { createClient } from '@supabase/supabase-js';

function getSupabase() {
  return createClient(process.env.SUPABASE_URL, process.env.SUPABASE_SERVICE_ROLE_KEY);
}

const clamp = (p) => Math.min(Math.max(p, 1e-4), 1 - 1e-4);
const logit = (p) => Math.log(clamp(p) / (1 - clamp(p)));
const sigmoid = (x) => 1 / (1 + Math.exp(-x));

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

function* fatiar(array, tamanho) {
  for (let i = 0; i < array.length; i += tamanho) yield array.slice(i, i + tamanho);
}

// ============================================================
// TAREFA: elo — Elo interno (não confundir com team_elo_external, o rating
// do ClubElo). Dois escopos: 'liga' (parte de 1500, sem depender de nada
// externo) e 'geral' (cross-liga, só se ajusta em Champions League, semeado
// da média do ClubElo quando existe). Fórmula: Elo clássico com multiplicador
// de diferença de gols (G=1 se |dif|<=1, 1.5 se dif=2, (11+dif)/8 se dif>=3),
// K=20, vantagem de casa=65. Roda em lotes por escopo (uma liga por vez, ou
// o geral) — processar tudo numa chamada só estourava os 60s do Vercel.
// ============================================================

const RATING_INICIAL = 1500;
const K_ELO = 20;
const VANTAGEM_CASA = 65;
const LIGA_CHAMPIONS_ID = 19;
const LIGAS_DOMESTICAS = [1, 4, 7, 10, 13, 16];

function multiplicadorDiferenca(diferenca) {
  const d = Math.abs(diferenca);
  if (d <= 1) return 1;
  if (d === 2) return 1.5;
  return (11 + d) / 8;
}

function atualizarElo(ratingMandante, ratingVisitante, golsMandante, golsVisitante) {
  const diferenca = golsMandante - golsVisitante;
  const resultadoMandante = diferenca > 0 ? 1 : diferenca < 0 ? 0 : 0.5;
  const esperadoMandante = 1 / (1 + Math.pow(10, -((ratingMandante + VANTAGEM_CASA) - ratingVisitante) / 400));
  const delta = K_ELO * multiplicadorDiferenca(diferenca) * (resultadoMandante - esperadoMandante);
  return { novoMandante: ratingMandante + delta, novoVisitante: ratingVisitante - delta };
}

async function regravarElo(supabase, linhasElo, linhasHistorico, filtroDelete) {
  const filtro = filtroDelete.league_id === null ? { escopo: filtroDelete.escopo } : filtroDelete;
  await supabase.from('team_elo_history').delete().match(filtro);
  await supabase.from('team_elo').delete().match(filtro);

  for (const lote of fatiar(linhasElo, 500)) {
    const { error } = await supabase.from('team_elo').upsert(lote, { onConflict: 'team_id,escopo,league_id' });
    if (error) throw error;
  }
  for (const lote of fatiar(linhasHistorico, 500)) {
    const { error } = await supabase.from('team_elo_history').insert(lote);
    if (error) throw error;
  }
}

async function eloProcessarLiga(supabase, ligaId) {
  const partidas = await buscarTudoPaginado(() =>
    supabase.from('matches').select('id, round, match_date, home_team_id, away_team_id, home_goals, away_goals')
      .eq('league_id', ligaId).eq('status', 'finished').not('home_goals', 'is', null).not('away_goals', 'is', null)
  );
  partidas.sort((a, b) => new Date(a.match_date) - new Date(b.match_date));

  const rating = {}, contagem = {};
  const historico = [];

  for (const p of partidas) {
    const antesMandante = rating[p.home_team_id] ?? RATING_INICIAL;
    const antesVisitante = rating[p.away_team_id] ?? RATING_INICIAL;
    const { novoMandante, novoVisitante } = atualizarElo(antesMandante, antesVisitante, p.home_goals, p.away_goals);
    rating[p.home_team_id] = novoMandante;
    rating[p.away_team_id] = novoVisitante;
    contagem[p.home_team_id] = (contagem[p.home_team_id] || 0) + 1;
    contagem[p.away_team_id] = (contagem[p.away_team_id] || 0) + 1;

    historico.push(
      { team_id: p.home_team_id, escopo: 'liga', league_id: ligaId, match_id: p.id, rodada: p.round, rating_antes: antesMandante, rating_depois: novoMandante, match_date: p.match_date },
      { team_id: p.away_team_id, escopo: 'liga', league_id: ligaId, match_id: p.id, rodada: p.round, rating_antes: antesVisitante, rating_depois: novoVisitante, match_date: p.match_date },
    );
  }

  const linhasElo = Object.entries(rating).map(([team_id, r]) => ({
    team_id: Number(team_id), escopo: 'liga', league_id: ligaId, rating: r, partidas: contagem[team_id], atualizado_em: new Date().toISOString(),
  }));

  await regravarElo(supabase, linhasElo, historico, { escopo: 'liga', league_id: ligaId });
  return { escopo: 'liga', league_id: ligaId, partidas_processadas: partidas.length, times_com_elo: linhasElo.length };
}

async function eloProcessarGeral(supabase) {
  const [partidasChampions, seedsExternas, partidasDomesticas] = await Promise.all([
    buscarTudoPaginado(() =>
      supabase.from('matches').select('id, round, match_date, home_team_id, away_team_id, home_goals, away_goals')
        .eq('league_id', LIGA_CHAMPIONS_ID).eq('status', 'finished').not('home_goals', 'is', null).not('away_goals', 'is', null)
    ),
    buscarTudoPaginado(() => supabase.from('team_elo_external').select('team_id, elo, valido_ate').order('valido_ate', { ascending: false })),
    buscarTudoPaginado(() => supabase.from('matches').select('home_team_id, away_team_id').in('league_id', LIGAS_DOMESTICAS).eq('status', 'finished')),
  ]);
  partidasChampions.sort((a, b) => new Date(a.match_date) - new Date(b.match_date));

  const seedPorTime = {};
  for (const s of seedsExternas) if (!(s.team_id in seedPorTime)) seedPorTime[s.team_id] = Number(s.elo);

  const rating = {}, contagem = {};
  const historico = [];

  for (const p of partidasChampions) {
    if (!(p.home_team_id in rating)) { rating[p.home_team_id] = seedPorTime[p.home_team_id] ?? RATING_INICIAL; contagem[p.home_team_id] = 0; }
    if (!(p.away_team_id in rating)) { rating[p.away_team_id] = seedPorTime[p.away_team_id] ?? RATING_INICIAL; contagem[p.away_team_id] = 0; }
    const antesMandante = rating[p.home_team_id];
    const antesVisitante = rating[p.away_team_id];
    const { novoMandante, novoVisitante } = atualizarElo(antesMandante, antesVisitante, p.home_goals, p.away_goals);
    rating[p.home_team_id] = novoMandante;
    rating[p.away_team_id] = novoVisitante;
    contagem[p.home_team_id]++;
    contagem[p.away_team_id]++;

    historico.push(
      { team_id: p.home_team_id, escopo: 'geral', league_id: null, match_id: p.id, rodada: p.round, rating_antes: antesMandante, rating_depois: novoMandante, match_date: p.match_date },
      { team_id: p.away_team_id, escopo: 'geral', league_id: null, match_id: p.id, rodada: p.round, rating_antes: antesVisitante, rating_depois: novoVisitante, match_date: p.match_date },
    );
  }

  for (const p of partidasDomesticas) {
    for (const timeId of [p.home_team_id, p.away_team_id]) {
      if (!(timeId in rating)) { rating[timeId] = seedPorTime[timeId] ?? RATING_INICIAL; contagem[timeId] = 0; }
    }
  }

  const linhasElo = Object.entries(rating).map(([team_id, r]) => ({
    team_id: Number(team_id), escopo: 'geral', league_id: null, rating: r, partidas: contagem[team_id] || 0, atualizado_em: new Date().toISOString(),
  }));

  await regravarElo(supabase, linhasElo, historico, { escopo: 'geral', league_id: null });
  return { escopo: 'geral', partidas_processadas: partidasChampions.length, times_com_elo: linhasElo.length };
}

// ============================================================
// TAREFA: calibracao — Platt Scaling + Isotonic Regression, salvos em
// model_calibration por (model_name, market, selection). Split temporal
// 70/30. Platt via gradiente descendente, Isotonic via PAVA.
// ============================================================

function chaveMercadoCalib(m) {
  return m === '1X2' ? '1X2' : m === 'over_under_2.5' ? 'over_under_2_5' : 'corners_over_under_9_5';
}

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

function aplicarIsotonicPredicao(p, xs, ys) {
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

const logLossTermo = (p, y) => (y ? -Math.log(clamp(p)) : -Math.log(1 - clamp(p)));

async function tarefaCalibracao(supabase, minimo) {
  const predicoes = await buscarTudoPaginado(() => supabase.from('model_predictions').select('id, model_name, market, selection, probability, match_id'));
  const matches = await buscarTudoPaginado(() => supabase.from('matches').select('id, status, home_goals, away_goals, match_date').eq('status', 'finished').not('home_goals', 'is', null));
  const corneragens = await buscarTudoPaginado(() => supabase.from('match_stats').select('match_id, corners').not('corners', 'is', null));

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
    const chave = chaveMercadoCalib(market);
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
    const logLossBruto = teste.reduce((s, l) => s + logLossTermo(l.p, l.y), 0) / teste.length;

    const platt = ajustarPlatt(xsTreino, ysTreino);
    const logLossPlatt = teste.reduce((s, l) => s + logLossTermo(sigmoid(platt.a * logit(l.p) + platt.b), l.y), 0) / teste.length;

    const xsTreinoP = treino.map(l => l.p);
    const isotonic = ajustarIsotonic(xsTreinoP, ysTreino);
    const logLossIsotonic = teste.reduce((s, l) => s + logLossTermo(aplicarIsotonicPredicao(l.p, isotonic.x, isotonic.y), l.y), 0) / teste.length;

    const base = { model_name: g.model_name, market: g.market, selection: g.selection, n_treino: treino.length, n_teste: teste.length, log_loss_bruto: logLossBruto, fitted_at: new Date().toISOString() };

    const linhaPlatt = { ...base, method: 'platt', platt_coef: platt.a, platt_intercept: platt.b, log_loss_calibrado: logLossPlatt, isotonic_x: null, isotonic_y: null };
    const linhaIsotonic = { ...base, method: 'isotonic', platt_coef: null, platt_intercept: null, log_loss_calibrado: logLossIsotonic, isotonic_x: isotonic.x, isotonic_y: isotonic.y };

    const { error: erroUpsert } = await supabase.from('model_calibration').upsert([linhaPlatt, linhaIsotonic], { onConflict: 'model_name,market,selection,method' });
    if (erroUpsert) throw erroUpsert;

    resultado.ajustados.push({
      model_name: g.model_name, market: g.market, selection: g.selection,
      n_treino: treino.length, n_teste: teste.length,
      log_loss_bruto: logLossBruto, log_loss_platt: logLossPlatt, log_loss_isotonic: logLossIsotonic,
    });
  }

  return resultado;
}

// ============================================================
// TAREFA: odds-descobrir — FASE 1 do sync de odds (OddsPapi). Cota grátis é
// só 250 req/mês, então antes de escrever a lógica de parse "às cegas" (e
// arriscar queimar cota com tentativa e erro), essa tarefa faz UMA rodada de
// chamadas de descoberta: lista de torneios, mercados e casas de apostas
// (3 chamadas, cacheadas em oddspapi_cache pra nunca precisar rechamar), e
// devolve uma amostra CRUA de odds de UM torneio já resolvido (4ª chamada) —
// pra inspecionar o formato real da resposta antes de escrever o parser
// definitivo da tarefa "odds".
//
// IMPORTANTE: NÃO grava mais o mapeamento liga->torneio automaticamente — a
// heurística por nome errou duas vezes em produção (achados reais: 1. La
// Liga casou com uma copa feminina espanhola só pela palavra "la"; 2. Serie A
// Itália e Brasileirão colidiram no mesmo torneio "Serie A" genérico; 3. numa
// segunda tentativa já com filtro por país, Serie A Itália foi parar em
// "Coppa Italia Serie C" porque nosso label "(Itália)" batia token com
// "Italia" do nome do torneio errado). O custo de um mapeamento errado é alto
// (corrompe todo sync de odds daquela liga), então o mapeamento real das 6
// ligas foi resolvido manualmente por inspeção direta do cache via SQL e está
// fixo em liga_oddspapi_tournament — essa tarefa só REPORTA o que a
// heurística sugeriria (`sugestoes_heuristica`) pra quem ainda não tem
// mapeamento confirmado, nunca sobrescreve o que já está resolvido.
// ============================================================

const ODDSPAPI_BASE = 'https://api.oddspapi.io';
const SPORT_ID_FUTEBOL = 10;
const BOOKMAKERS_ALVO = ['pinnacle', 'bet365', 'betano'];

async function chamarOddspapi(caminho, params, apiKey) {
  const url = new URL(`${ODDSPAPI_BASE}${caminho}`);
  Object.entries(params).forEach(([k, v]) => { if (v != null) url.searchParams.set(k, v); });
  url.searchParams.set('apiKey', apiKey);
  const resposta = await fetch(url.toString());
  const dados = await resposta.json();
  if (!resposta.ok) throw new Error(`OddsPapi ${caminho}: HTTP ${resposta.status} — ${JSON.stringify(dados).slice(0, 300)}`);
  return dados;
}

function normalizarTexto(s) {
  return (s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/[^a-z0-9]+/g, ' ').trim();
}

// Times de futebol domésticas -> nome do país esperado na OddsPapi (categoryName),
// pra desambiguar nomes de torneio genéricos que colidem entre países (ex.: "Serie A"
// existe pra Itália E aparece como sufixo em vários campeonatos estaduais brasileiros;
// "La Liga"/"a" batia até com "Copa de la Reina" só pela palavra "la"). Achado real
// testado em produção: sem esse filtro, Brasileirão e Serie A (Itália) colidiam no
// mesmo torneio, e La Liga casava com uma copa feminina espanhola.
const PAIS_POR_LIGA = { 1: 'brazil', 4: 'england', 7: 'spain', 10: 'italy', 13: 'germany', 16: 'france' };

const STOPWORDS_LIGA = new Set(['de', 'da', 'do', 'a', 'the', 'liga', 'league']);
function tokensLiga(nome) {
  return normalizarTexto(nome).split(' ').filter(t => t && t.length > 2 && !STOPWORDS_LIGA.has(t));
}

function acharMelhorTorneio(nomeLiga, pais, torneios) {
  const tokensA = tokensLiga(nomeLiga);
  if (tokensA.length === 0) return null;
  // Restringe candidatos ao país esperado quando a categoria bate (evita colisão
  // entre ligas de países diferentes com nome genérico tipo "Serie A"/"Liga").
  const candidatos = torneios.filter(t => normalizarTexto(t.categoryName) === pais);
  const pool = candidatos.length > 0 ? candidatos : torneios;

  let melhor = null, melhorScore = 0;
  for (const t of pool) {
    const tokensB = tokensLiga(t.tournamentName);
    if (tokensB.length === 0) continue;
    const intersecao = tokensA.filter(x => tokensB.includes(x)).length;
    if (intersecao === 0) continue;
    const uniao = new Set([...tokensA, ...tokensB]).size;
    const score = intersecao / uniao; // Jaccard — mais rigoroso que intersecao/min, evita falso-positivo de 1 token só
    if (score > melhorScore) { melhorScore = score; melhor = t; }
  }
  return melhorScore >= 0.34 ? melhor : null;
}

async function buscarOuCache(supabase, chave, buscar) {
  const { data: existente } = await supabase.from('oddspapi_cache').select('valor').eq('chave', chave).maybeSingle();
  if (existente) return existente.valor;
  const valor = await buscar();
  await supabase.from('oddspapi_cache').upsert({ chave, valor, atualizado_em: new Date().toISOString() }, { onConflict: 'chave' });
  return valor;
}

async function tarefaOddsDescobrir(supabase, apiKey, forcar) {
  if (forcar) await supabase.from('oddspapi_cache').delete().in('chave', ['tournaments', 'markets', 'bookmakers']);

  const torneios = await buscarOuCache(supabase, 'tournaments', () => chamarOddspapi('/v4/tournaments', { sportId: SPORT_ID_FUTEBOL }, apiKey));
  const mercados = await buscarOuCache(supabase, 'markets', () => chamarOddspapi('/v4/markets', {}, apiKey));
  const casas = await buscarOuCache(supabase, 'bookmakers', () => chamarOddspapi('/v4/bookmakers', {}, apiKey));

  const agora = new Date().toISOString();
  const { data: ligas } = await supabase.from('leagues').select('id, name').in('id', LIGAS_DOMESTICAS);

  // IMPORTANTE: essa tarefa NÃO grava mais em liga_oddspapi_tournament
  // automaticamente. A heurística por nome já errou duas vezes em produção
  // pra essas 6 ligas (Brasileirão×Serie A colidindo, La Liga×copa feminina,
  // depois Serie A×Coppa Italia Serie C) — o custo de um mapeamento errado é
  // alto (corrompe TODO sync de odds daquela liga), então o mapeamento real
  // já foi resolvido manualmente por inspeção direta do cache (SQL) e está
  // fixo na tabela. Aqui só REPORTA o que a heurística acharia, pra
  // conferência — nunca sobrescreve o que já está confirmado.
  const { data: jaResolvidas } = await supabase.from('liga_oddspapi_tournament').select('league_id, tournament_id, tournament_name');
  const resolvidasPorLiga = Object.fromEntries((jaResolvidas || []).map(r => [r.league_id, r]));

  const resultado = { ja_resolvidas: jaResolvidas || [], sugestoes_heuristica: [], sem_correspondencia: [], casas_encontradas: null, amostra_odds: null };

  for (const liga of ligas || []) {
    if (resolvidasPorLiga[liga.id]) continue; // já confirmado manualmente, não precisa de sugestão
    const torneio = acharMelhorTorneio(liga.name, PAIS_POR_LIGA[liga.id], torneios);
    if (!torneio) { resultado.sem_correspondencia.push(liga.name); continue; }
    resultado.sugestoes_heuristica.push({ liga: liga.name, tournamentId: torneio.tournamentId, tournamentName: torneio.tournamentName, categoryName: torneio.categoryName, aviso: 'NÃO gravado automaticamente — confirme manualmente antes de usar.' });
  }

  // Confere se os slugs esperados (pinnacle/bet365/betano) existem na lista real de casas
  const nomesCasas = (casas || []).map(c => ({ nome: c.bookmakerName, slug: c.slug }));
  resultado.casas_encontradas = BOOKMAKERS_ALVO.map(alvo => {
    const achado = nomesCasas.find(c => c.slug === alvo);
    return { alvo, encontrado: achado || null };
  });

  // Amostra crua de odds de UM torneio já casado, com UM bookmaker só — a API exige
  // exatamente 1 bookmaker por chamada em /v4/odds-by-tournaments (`bookmaker`,
  // singular; descoberto testando em produção — a doc pública sugeria lista separada
  // por vírgula, que não é aceito). Pra pegar as 3 casas, a fase 2 (sync de verdade)
  // vai precisar de 1 chamada POR bookmaker.
  if ((jaResolvidas || []).length > 0) {
    const primeiro = jaResolvidas[0];
    const amostra = await chamarOddspapi('/v4/odds-by-tournaments', {
      tournamentIds: primeiro.tournament_id, bookmaker: 'pinnacle', oddsFormat: 'decimal', verbosity: 3,
    }, apiKey);
    resultado.amostra_odds = { torneio: primeiro.tournament_name, quantidade_fixtures: Array.isArray(amostra) ? amostra.length : null, primeiro_fixture: Array.isArray(amostra) ? amostra[0] : amostra };
  }

  return resultado;
}

// ============================================================
// TAREFA: odds — FASE 2 do sync de odds (OddsPapi): sincroniza de verdade.
//
// Captura o MÁXIMO possível por chamada: /v4/odds-by-tournaments devolve o
// quadro INTEIRO do torneio (todos os jogos que já têm linha aberta pra
// aquele bookmaker) numa única chamada — já é o mais eficiente possível, não
// tem "paginar por jogo". Custo fixo: 1 chamada por bookmaker (3 casas) por
// liga = 3 chamadas/liga, 18 pra sincronizar as 6 ligas de uma vez. A
// resposta já traz nome dos times e horário do jogo junto com as odds — não
// precisa de uma chamada extra em /v4/fixtures.
//
// JANELA ADAPTATIVA ("aumentar a base se tiver poucos jogos no prazo"): as
// casas só abrem linha alguns dias antes do jogo, então nem sempre há muita
// coisa pra capturar. Antes de gastar as 3 chamadas da liga, conta quantos
// jogos AGENDADOS existem nos próximos 7 dias (consulta local, sem custo de
// cota); se vier menos que MINIMO_JOGOS_JANELA, alarga pra 14 e depois 21
// dias antes de decidir se vale a pena chamar a API — evita gastar cota numa
// liga que não tem praticamente nada agendado no curto prazo (ex.: parada
// pra data FIFA).
//
// Guarda com timestamp (captured_at, coluna que já existia) SEM sobrescrever
// o snapshot anterior — cada sync é um INSERT novo, não upsert — pra dar pra
// montar a curva de movimento de linha ao longo do tempo, como pedido.
// ============================================================

const MINIMO_JOGOS_JANELA = 3;
const JANELAS_DIAS = [7, 14, 21];

function normalizarTexto2(s) {
  return (s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/[^a-z0-9]/g, '');
}
function nomesBatem(a, b) {
  const x = normalizarTexto2(a), y = normalizarTexto2(b);
  return x.length > 0 && y.length > 0 && (x.includes(y) || y.includes(x));
}

async function acharJanelaComJogos(supabase, ligaId) {
  for (const dias of JANELAS_DIAS) {
    const ate = new Date(Date.now() + dias * 86400000).toISOString();
    const { count } = await supabase.from('matches').select('id', { count: 'exact', head: true })
      .eq('league_id', ligaId).eq('status', 'scheduled').lte('match_date', ate);
    if ((count || 0) >= MINIMO_JOGOS_JANELA || dias === JANELAS_DIAS[JANELAS_DIAS.length - 1]) {
      return { dias, count: count || 0 };
    }
  }
  return { dias: JANELAS_DIAS[JANELAS_DIAS.length - 1], count: 0 };
}

function acharMercadoPorNome(marketsFixture, mercadosCache, nomeAlvo) {
  for (const [marketId, market] of Object.entries(marketsFixture || {})) {
    const info = mercadosCache[marketId];
    if (info?.marketName === nomeAlvo) return { marketId, market };
  }
  return null;
}

function extrairPrecosSelecoes(market, filtroOutcomeId) {
  const resultado = {};
  for (const outcome of Object.values(market.outcomes || {})) {
    const jogador = outcome.players?.['0'];
    if (!jogador || jogador.price == null) continue;
    const id = jogador.bookmakerOutcomeId;
    if (filtroOutcomeId && !filtroOutcomeId(id)) continue;
    resultado[id] = Number(jogador.price);
  }
  return resultado;
}

async function tarefaOddsSync(supabase, apiKey, ligaId) {
  if (!LIGAS_DOMESTICAS.includes(ligaId)) {
    return { error: `liga_id inválido — precisa ser uma das ligas domésticas: ${LIGAS_DOMESTICAS.join(', ')}.` };
  }

  const { data: mapa } = await supabase.from('liga_oddspapi_tournament').select('tournament_id, tournament_name').eq('league_id', ligaId).maybeSingle();
  if (!mapa) return { error: `Liga ${ligaId} ainda não tem torneio da OddsPapi resolvido em liga_oddspapi_tournament — rode tarefa=odds-descobrir e confirme manualmente primeiro.` };

  const janela = await acharJanelaComJogos(supabase, ligaId);
  if (janela.count === 0) {
    return { liga_id: ligaId, torneio: mapa.tournament_name, pulado: true, motivo: `Nenhum jogo agendado nos próximos ${janela.dias} dias — sync pulado pra não gastar cota à toa.` };
  }

  const { data: mercadosCacheRaw } = await supabase.from('oddspapi_cache').select('valor').eq('chave', 'markets').maybeSingle();
  const mercadosCache = Object.fromEntries((mercadosCacheRaw?.valor || []).map(m => [String(m.marketId), m]));

  const janelaAteMs = Date.now() + janela.dias * 86400000;
  const { data: candidatos } = await supabase.from('matches').select('id, match_date, home:teams!matches_home_team_id_fkey(id,name), away:teams!matches_away_team_id_fkey(id,name)')
    .eq('league_id', ligaId).eq('status', 'scheduled').lte('match_date', new Date(janelaAteMs).toISOString());

  const resultado = { liga_id: ligaId, torneio: mapa.tournament_name, janela_dias: janela.dias, jogos_candidatos: (candidatos || []).length, por_bookmaker: [], linhas_inseridas: 0 };

  for (const bookmaker of BOOKMAKERS_ALVO) {
    let fixtures;
    try {
      fixtures = await chamarOddspapi('/v4/odds-by-tournaments', { tournamentIds: mapa.tournament_id, bookmaker, oddsFormat: 'decimal', verbosity: 3 }, apiKey);
    } catch (e) {
      resultado.por_bookmaker.push({ bookmaker, erro: e.message });
      continue;
    }
    if (!Array.isArray(fixtures)) { resultado.por_bookmaker.push({ bookmaker, erro: 'resposta inesperada' }); continue; }

    let casados = 0, semCasar = 0;
    const linhas = [];
    const agora = new Date().toISOString();

    for (const fx of fixtures) {
      const dataFixture = fx.startTime ? new Date(fx.startTime) : null;
      const partida = (candidatos || []).find(m => {
        if (!dataFixture || !m.match_date) return false;
        const diffHoras = Math.abs(new Date(m.match_date) - dataFixture) / 3600000;
        if (diffHoras > 36) return false; // tolerância de fuso/horário de exibição
        return (nomesBatem(m.home?.name, fx.participant1Name) && nomesBatem(m.away?.name, fx.participant2Name));
      });
      if (!partida) { semCasar++; continue; }
      casados++;

      const marketsFixture = fx.bookmakerOdds?.[bookmaker]?.markets;
      if (!marketsFixture) continue;

      const mercado1x2 = acharMercadoPorNome(marketsFixture, mercadosCache, 'Full Time Result');
      if (mercado1x2) {
        const precos = extrairPrecosSelecoes(mercado1x2.market, id => ['home', 'draw', 'away'].includes(id));
        for (const [selecao, odd] of Object.entries(precos)) {
          linhas.push({ match_id: partida.id, bookmaker, market: '1X2', selection: selecao, odds: odd, snapshot: 'pre_closing', captured_at: agora });
        }
      }

      const mercadoTotals = acharMercadoPorNome(marketsFixture, mercadosCache, 'Over Under Full Time');
      if (mercadoTotals) {
        const precos = extrairPrecosSelecoes(mercadoTotals.market, id => id?.startsWith('2.5/'));
        for (const [id, odd] of Object.entries(precos)) {
          const selecao = id.endsWith('over') ? 'over' : 'under';
          linhas.push({ match_id: partida.id, bookmaker, market: 'over_under_2.5', selection: selecao, odds: odd, snapshot: 'pre_closing', captured_at: agora });
        }
      }
    }

    if (linhas.length > 0) {
      const { error: erroInsert } = await supabase.from('odds_market').insert(linhas);
      if (erroInsert) { resultado.por_bookmaker.push({ bookmaker, erro: erroInsert.message }); continue; }
    }

    resultado.linhas_inseridas += linhas.length;
    resultado.por_bookmaker.push({ bookmaker, fixtures_recebidos: fixtures.length, jogos_casados: casados, sem_correspondencia: semCasar, linhas_inseridas: linhas.length });
  }

  return resultado;
}

// Roda tarefaOddsSync nas 6 ligas domésticas em sequência — usado pelo cron
// (vercel.json) e pra sincronizar tudo manualmente numa chamada só. Cada liga
// já pula sozinha se não tiver jogo suficiente no curto prazo, então o custo
// real varia (pior caso: 18 chamadas, 3 por liga; normalmente bem menos,
// já que nem toda liga tem jogo dentro da janela toda vez que o cron roda).
async function tarefaOddsTodas(supabase, apiKey) {
  const porLiga = [];
  for (const ligaId of LIGAS_DOMESTICAS) {
    porLiga.push(await tarefaOddsSync(supabase, apiKey, ligaId));
  }
  return { ligas: porLiga, total_linhas_inseridas: porLiga.reduce((s, r) => s + (r.linhas_inseridas || 0), 0) };
}

export default async function handler(req, res) {
  const supabaseUrl = process.env.SUPABASE_URL, serviceKey = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!supabaseUrl || !serviceKey) {
    return res.status(500).json({ error: { message: 'SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY não configuradas.' } });
  }
  const supabase = getSupabase();
  const { tarefa, liga_id, escopo, minimo, forcar } = req.query;

  try {
    if (tarefa === 'odds-descobrir') {
      const apiKey = process.env.ODDSPAPI_KEY;
      if (!apiKey) return res.status(500).json({ error: { message: 'ODDSPAPI_KEY não configurada.' } });
      return res.status(200).json(await tarefaOddsDescobrir(supabase, apiKey, forcar === 'true'));
    }

    if (tarefa === 'odds') {
      const apiKey = process.env.ODDSPAPI_KEY;
      if (!apiKey) return res.status(500).json({ error: { message: 'ODDSPAPI_KEY não configurada.' } });
      if (!liga_id) return res.status(400).json({ error: { message: 'tarefa=odds precisa de ?liga_id=X.' } });
      const resultado = await tarefaOddsSync(supabase, apiKey, Number(liga_id));
      if (resultado.error) return res.status(400).json({ error: { message: resultado.error } });
      return res.status(200).json(resultado);
    }

    if (tarefa === 'odds-todas') {
      const apiKey = process.env.ODDSPAPI_KEY;
      if (!apiKey) return res.status(500).json({ error: { message: 'ODDSPAPI_KEY não configurada.' } });
      return res.status(200).json(await tarefaOddsTodas(supabase, apiKey));
    }

    if (tarefa === 'elo') {
      if (liga_id) {
        const ligaIdNum = Number(liga_id);
        if (!LIGAS_DOMESTICAS.includes(ligaIdNum)) {
          return res.status(400).json({ error: { message: `liga_id inválido — precisa ser uma das ligas domésticas: ${LIGAS_DOMESTICAS.join(', ')}.` } });
        }
        return res.status(200).json(await eloProcessarLiga(supabase, ligaIdNum));
      }
      if (escopo === 'geral') return res.status(200).json(await eloProcessarGeral(supabase));
      return res.status(400).json({ error: { message: 'tarefa=elo precisa de ?liga_id=X ou ?escopo=geral.' }, ligas_domesticas: LIGAS_DOMESTICAS });
    }

    if (tarefa === 'calibracao') {
      return res.status(200).json(await tarefaCalibracao(supabase, Number(minimo) || 80));
    }

    return res.status(400).json({
      error: { message: 'Especifique ?tarefa=elo (com liga_id ou escopo=geral), ?tarefa=calibracao, ?tarefa=odds-descobrir ou ?tarefa=odds&liga_id=X.' },
    });
  } catch (erro) {
    res.status(500).json({ error: { message: erro.message } });
  }
}
