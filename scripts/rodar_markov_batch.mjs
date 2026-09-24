// scripts/rodar_markov_batch.mjs
//
// Job em lote da Fase 6 do motor de Markov multi-evento (ver plano da
// sessão): roda `runMarkovSimulation` (src/utils/markovEngine.js) contra
// toda partida finalizada que já tem λ de gol estimado
// (model_match_estimates, model_name='hibrido_gols_v1') e persiste as
// probabilidades agregadas em `model_predictions`
// (model_name='markov_multievento_v1') -- placar_exato (grade 7x7) e os
// mercados over/under de cartão/escanteio/falta. Sem isso, o motor nunca
// caberia no crivo de validação (IC 95% via bootstrap) que
// `api/backtest-betting.js` já exige de qualquer modelo -- é client-side,
// on-demand, nada é persistido hoje.
//
// Por que roda fora do Vercel: escreve com SUPABASE_SERVICE_ROLE_KEY
// (model_predictions é tabela do pipeline -- RLS de escrita só service_role,
// nunca a chave anon usada por api/*.js), e o volume (~15.700 partidas ×
// Monte Carlo) não cabe no maxDuration=60s de uma function.
//
// Motor JS reaproveitado direto, não reimplementado em Python -- mesmo
// padrão de `scripts/verificar_paridade_js.mjs` (já importa direto de
// src/utils/*.js sem problema; markovEngine.js não depende de browser/DOM,
// `type:"module"` no package.json cobre o `import` daqui).
//
// USO:
//   node scripts/rodar_markov_batch.mjs                    # roda tudo
//   node scripts/rodar_markov_batch.mjs --limit=20          # smoke test
//   node scripts/rodar_markov_batch.mjs --desde=2026-01-01  # só partidas com match_date >= essa data
//
// Variáveis de ambiente necessárias: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY.

import { createClient } from '@supabase/supabase-js';
import { runMarkovSimulation } from '../src/utils/markovEngine.js';
import { LINHAS_CARTOES_OU, LINHAS_CORNERS_OU, LINHAS_FALTAS_OU } from '../api/_lib/resultadosReais.js';

const MODEL_NAME = 'markov_multievento_v1';
const LAMBDA_MODEL_NAME = 'hibrido_gols_v1';
// 2.000 sims -- a própria investigação empírica desta sessão (2.000 vs.
// 20.000 sorteios) já mostrou que a diferença de probabilidade/odds entre
// as duas contagens não é considerável; mantém o job em tempo razoável
// rodando as ~15.700 partidas.
const SIM_COUNT = 2000;
// Mesma janela de `statEsperado()` em api/corners-model.js.
const JANELA_HISTORICO = 10;
// Mesmo par de limites que `recalcular_model_stats_resumo` já usa pro
// log-loss (migration 20260825002000) -- aqui evita violar o
// `CHECK(0<probability<1)` de model_predictions quando uma célula/linha sai
// com 0 ocorrências em 2.000 sims.
const PROB_MIN = 0.0001, PROB_MAX = 0.9999;
const TAMANHO_LOTE_UPSERT = 500;

const args = Object.fromEntries(
  process.argv.slice(2).map((a) => {
    const [k, v] = a.replace(/^--/, '').split('=');
    return [k, v ?? true];
  }),
);
const LIMIT = args.limit ? Number(args.limit) : null;
const DESDE = args.desde || null;

function getSupabase() {
  const url = process.env.SUPABASE_URL;
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!url || !key) throw new Error('SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY não configuradas.');
  return createClient(url, key);
}

// Mesmo padrão de paginação de api/backtest-betting.js/api/model-stats.js --
// `.select()` sem `.range()` corta em 1000 linhas silenciosamente (CLAUDE.md).
// Duplicada aqui de propósito (script isolado, não importa dos arquivos de
// api/) -- `.order('id')` garante ordem estável entre páginas.
async function buscarTudoPaginado(criarQuery) {
  const TAMANHO_PAGINA = 1000;
  const resultado = [];
  let pagina = 0;
  for (;;) {
    const { data, error } = await criarQuery().order('id').range(pagina * TAMANHO_PAGINA, pagina * TAMANHO_PAGINA + TAMANHO_PAGINA - 1);
    if (error) throw error;
    resultado.push(...(data || []));
    if (!data || data.length < TAMANHO_PAGINA) break;
    pagina++;
  }
  return resultado;
}

async function carregarTudo(supabase) {
  console.log('Carregando dados em memória (matches, model_match_estimates, model_stat_estimates, match_stats_fotmob, match_disciplina, league_markov_params)...');

  const [matches, alvos, statEstimates, statsFotmob, disciplina, markovParamsRows] = await Promise.all([
    buscarTudoPaginado(() => supabase.from('matches').select('id, home_team_id, away_team_id, match_date, league_id, status, home_goals, away_goals')),
    buscarTudoPaginado(() => supabase.from('model_match_estimates').select('match_id, params').eq('model_name', LAMBDA_MODEL_NAME).not('params', 'is', null)),
    buscarTudoPaginado(() => supabase.from('model_stat_estimates').select('match_id, stat, home_expected, away_expected').in('stat', ['corners', 'shots', 'shots_on_target'])),
    buscarTudoPaginado(() => supabase.from('match_stats_fotmob').select('match_id, team_id, corners, total_shots, shots_on_target')),
    buscarTudoPaginado(() => supabase.from('match_disciplina').select('match_id, team_id, faltas_cometidas, cartoes_amarelos, cartoes_vermelhos_equiv, fonte_cartoes')),
    buscarTudoPaginado(() => supabase.from('league_markov_params').select('league_id, evento, tipo, chave, valor')),
  ]);

  console.log(
    `  matches: ${matches.length}, alvos (${LAMBDA_MODEL_NAME}): ${alvos.length}, model_stat_estimates: ${statEstimates.length}, ` +
    `match_stats_fotmob: ${statsFotmob.length}, match_disciplina: ${disciplina.length}, league_markov_params: ${markovParamsRows.length}`,
  );

  return { matches, alvos, statEstimates, statsFotmob, disciplina, markovParamsRows };
}

// Stats sem GLM Poisson treinado (fouls/cartão) só têm fonte real
// (match_disciplina) -- corners/shots/shots_on_target têm model_stat_estimates
// como fonte prioritária, com fallback pra match_stats_fotmob. Mesma
// convenção de STAT_TABELA_REAL em api/corners-model.js.
const STAT_COM_MODEL_ESTIMATE = new Set(['corners', 'shots', 'shots_on_target']);
// `fonte_cartoes` só existe pra filtrar contagem de cartão (achado real já
// documentado 2x no projeto, PRs #509/#511) -- faltas não tem esse problema.
const FONTE_CARTOES_PERMITIDA = new Set(['match_events', 'fallback_fotmob']);

// Índices em memória pra evitar 1 query por partida-alvo (proibitivo em
// ~15.700 partidas) -- construídos uma única vez a partir do bulk carregado
// acima.
function construirIndices({ matches, statEstimates, statsFotmob, disciplina }) {
  const matchesById = new Map(matches.map((m) => [m.id, m]));

  // Lista cronológica de jogos por time+lado (casa/fora) -- base da janela
  // walk-forward de cada stat.
  const jogosPorTimeLado = {};
  for (const m of matches) {
    if (m.home_team_id) (jogosPorTimeLado[`${m.home_team_id}_1`] ??= []).push({ id: m.id, match_date: m.match_date });
    if (m.away_team_id) (jogosPorTimeLado[`${m.away_team_id}_0`] ??= []).push({ id: m.id, match_date: m.match_date });
  }
  for (const lista of Object.values(jogosPorTimeLado)) lista.sort((a, b) => new Date(a.match_date) - new Date(b.match_date));

  // model_stat_estimates: match_id -> { stat -> linha }
  const estimativaPorMatch = {};
  for (const e of statEstimates) (estimativaPorMatch[e.match_id] ??= {})[e.stat] = e;

  // Valor bruto (fallback real) por partida+time, por stat -- chave
  // `${matchId}_${teamId}`.
  const brutoPorMatchTeam = { corners: {}, shots: {}, shots_on_target: {}, fouls: {}, cartao_amarelo: {}, cartao_vermelho: {} };
  for (const r of statsFotmob) {
    const chave = `${r.match_id}_${r.team_id}`;
    if (r.corners != null) brutoPorMatchTeam.corners[chave] = Number(r.corners);
    if (r.total_shots != null) brutoPorMatchTeam.shots[chave] = Number(r.total_shots);
    if (r.shots_on_target != null) brutoPorMatchTeam.shots_on_target[chave] = Number(r.shots_on_target);
  }
  for (const r of disciplina) {
    const chave = `${r.match_id}_${r.team_id}`;
    if (r.faltas_cometidas != null) brutoPorMatchTeam.fouls[chave] = Number(r.faltas_cometidas);
    const cartaoConfiavel = FONTE_CARTOES_PERMITIDA.has(r.fonte_cartoes);
    if (cartaoConfiavel && r.cartoes_amarelos != null) brutoPorMatchTeam.cartao_amarelo[chave] = Number(r.cartoes_amarelos);
    if (cartaoConfiavel && r.cartoes_vermelhos_equiv != null) brutoPorMatchTeam.cartao_vermelho[chave] = Number(r.cartoes_vermelhos_equiv);
  }

  return { matchesById, jogosPorTimeLado, estimativaPorMatch, brutoPorMatchTeam };
}

// Réplica em lote de `statEsperado()` (api/corners-model.js), com o filtro
// de data que falta lá -- aquele endpoint só é chamado pra jogo FUTURO
// (então "os últimos 10" já são todos anteriores por construção); aqui,
// rodando sobre histórico, é preciso filtrar explicitamente
// `match_date < data_da_partida_alvo` antes de pegar os últimos 10, senão
// vaza dado do futuro pra trás (a mesma disciplina de walk-forward que o
// resto do projeto já exige em qualquer treino/backfill).
function statMedioWalkForward(indices, teamId, isHome, stat, dataAlvo) {
  const lista = indices.jogosPorTimeLado[`${teamId}_${isHome ? 1 : 0}`] || [];
  const dataAlvoMs = new Date(dataAlvo).getTime();
  const candidatos = lista.filter((j) => new Date(j.match_date).getTime() < dataAlvoMs);
  const janela = candidatos.slice(-JANELA_HISTORICO);
  if (janela.length === 0) return null;

  if (STAT_COM_MODEL_ESTIMATE.has(stat)) {
    const valores = [];
    for (const j of janela) {
      const est = indices.estimativaPorMatch[j.id]?.[stat];
      const v = est ? (isHome ? est.home_expected : est.away_expected) : null;
      if (v != null) valores.push(Number(v));
    }
    if (valores.length > 0) return valores.reduce((a, b) => a + b, 0) / valores.length;
  }

  const brutos = janela.map((j) => indices.brutoPorMatchTeam[stat][`${j.id}_${teamId}`]).filter((v) => v != null);
  if (brutos.length > 0) return brutos.reduce((a, b) => a + b, 0) / brutos.length;

  // Sem histórico nenhum (ex.: primeira partida do time no dataset) -- `null`
  // vira taxa 0 no motor (`Number(null)/minutes || 0`), mesma degradação
  // graciosa já documentada em markovEngine.js pra "sem dado de
  // escanteio/falta" (não sorteia nada pra esse evento, não quebra).
  return null;
}

// Mesmo padrão de merge liga-específica + fallback global de
// `carregarMarkovParams` (src/utils/markovParams.js), replicado (não
// importado -- aquele é client-side, este é um script Node separado) sobre
// as linhas já carregadas em bulk.
function indexarParams(destino, linhas) {
  for (const linha of linhas) {
    destino[linha.evento] ??= {};
    destino[linha.evento][linha.tipo] ??= {};
    if (!(linha.chave in destino[linha.evento][linha.tipo])) destino[linha.evento][linha.tipo][linha.chave] = Number(linha.valor);
  }
}
function montarParamsPorLiga(todasLinhas, leagueId) {
  const indice = {};
  if (leagueId != null) indexarParams(indice, todasLinhas.filter((l) => l.league_id === leagueId));
  indexarParams(indice, todasLinhas.filter((l) => l.league_id === null));
  return indice;
}

function clamp(p) {
  return Math.min(Math.max(p, PROB_MIN), PROB_MAX);
}

// Monta as linhas de `model_predictions` pra uma partida a partir da saída
// de `runMarkovSimulation`. `fair_odds` é calculado a partir da MESMA
// probabilidade já clampada que vai pra `probability` -- calcular um a
// partir do valor bruto e o outro do clampado deixaria os dois campos
// inconsistentes (achado da própria sessão de planejamento desta fase).
function montarLinhasPredicao(matchId, resultado) {
  const linhas = [];
  const agora = new Date().toISOString();
  const linha = (market, selection, pBruto) => {
    const p = clamp(pBruto);
    linhas.push({ match_id: matchId, model_name: MODEL_NAME, market, selection, probability: p, fair_odds: +(1 / p).toFixed(4), created_at: agora });
  };

  for (let i = 0; i < 7; i++) {
    for (let j = 0; j < 7; j++) linha('placar_exato', `${i}-${j}`, resultado.heatGrid[i][j]);
  }

  const mercadosOU = [
    { grupo: 'cartoes', prefixo: 'cartoes_over_under_' },
    { grupo: 'escanteios', prefixo: 'corners_over_under_' },
    { grupo: 'faltas', prefixo: 'faltas_over_under_' },
  ];
  for (const { grupo, prefixo } of mercadosOU) {
    for (const [linhaStr, over] of Object.entries(resultado.overUnder[grupo])) {
      const market = `${prefixo}${linhaStr}`;
      linha(market, 'over', over);
      linha(market, 'under', 1 - over);
    }
  }
  return linhas;
}

async function gravarLote(supabase, linhas) {
  if (linhas.length === 0) return 0;
  const { error } = await supabase.from('model_predictions').upsert(linhas, { onConflict: 'match_id,model_name,market,selection' });
  if (error) throw error;
  return linhas.length;
}

async function processarLote(supabase, alvos, indices, getParams) {
  let processadas = 0, escritas = 0;
  let pendentes = [];

  for (const alvo of alvos) {
    const match = indices.matchesById.get(alvo.match_id);
    const lambda1 = Number(alvo.params?.lambda_home);
    const lambda2 = Number(alvo.params?.lambda_away);
    if (!Number.isFinite(lambda1) || !Number.isFinite(lambda2)) continue;

    const params = getParams(match.league_id);
    const taxa = (stat, isHome) => statMedioWalkForward(indices, isHome ? match.home_team_id : match.away_team_id, isHome, stat, match.match_date);

    const resultado = runMarkovSimulation({
      gols: { lambda1, lambda2 },
      chutes: { chutes1: taxa('shots', true), chutes2: taxa('shots', false), chutesNoAlvo1: taxa('shots_on_target', true), chutesNoAlvo2: taxa('shots_on_target', false) },
      cartaoAmarelo: { taxa1: taxa('cartao_amarelo', true), taxa2: taxa('cartao_amarelo', false) },
      cartaoVermelho: { taxa1: taxa('cartao_vermelho', true), taxa2: taxa('cartao_vermelho', false) },
      escanteio: { taxa1: taxa('corners', true), taxa2: taxa('corners', false) },
      falta: { taxa1: taxa('fouls', true), taxa2: taxa('fouls', false) },
      dynamics: true,
      simCount: SIM_COUNT,
      params,
      linhasOverUnder: { cartoes: LINHAS_CARTOES_OU, escanteios: LINHAS_CORNERS_OU, faltas: LINHAS_FALTAS_OU },
    });

    pendentes.push(...montarLinhasPredicao(match.id, resultado));
    processadas++;

    if (pendentes.length >= TAMANHO_LOTE_UPSERT) {
      escritas += await gravarLote(supabase, pendentes);
      pendentes = [];
    }
    if (processadas % 500 === 0) console.log(`  ${processadas}/${alvos.length} partidas processadas, ${escritas} linhas gravadas...`);
  }
  if (pendentes.length > 0) escritas += await gravarLote(supabase, pendentes);

  return { processadas, escritas };
}

async function main() {
  const supabase = getSupabase();
  const dados = await carregarTudo(supabase);
  const indices = construirIndices(dados);

  let alvos = dados.alvos.filter((a) => {
    const m = indices.matchesById.get(a.match_id);
    return m && m.status === 'finished' && m.home_goals != null && m.away_goals != null;
  });
  if (DESDE) alvos = alvos.filter((a) => indices.matchesById.get(a.match_id).match_date >= DESDE);
  alvos.sort((a, b) => new Date(indices.matchesById.get(a.match_id).match_date) - new Date(indices.matchesById.get(b.match_id).match_date));
  if (LIMIT) alvos = alvos.slice(0, LIMIT);

  console.log(`Rodando o motor de Markov (simCount=${SIM_COUNT}) contra ${alvos.length} partidas (model_name='${MODEL_NAME}')...`);

  const paramsCache = new Map();
  const getParams = (leagueId) => {
    if (!paramsCache.has(leagueId)) paramsCache.set(leagueId, montarParamsPorLiga(dados.markovParamsRows, leagueId));
    return paramsCache.get(leagueId);
  };

  const inicio = Date.now();
  const { processadas, escritas } = await processarLote(supabase, alvos, indices, getParams);
  const segundos = ((Date.now() - inicio) / 1000).toFixed(1);

  console.log(`Concluído: ${processadas} partidas processadas, ${escritas} linhas gravadas em model_predictions, em ${segundos}s.`);
}

main().catch((erro) => {
  console.error('Erro fatal:', erro);
  process.exit(1);
});
