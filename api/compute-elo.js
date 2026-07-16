// api/compute-elo.js
// Roda no SERVIDOR do Vercel. Variáveis de ambiente necessárias:
//   SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY -> mesmas de sync-matches.js
//   (escrita em team_elo/team_elo_history é bloqueada pra chave anon pelo RLS)
//
// Calcula o Elo INTERNO do sistema (não confundir com team_elo_external, que
// é o rating externo do ClubElo — esse aqui é derivado só do nosso próprio
// histórico de partidas). Dois escopos:
//
//   'liga'  — um rating por liga doméstica, recalculado sobre TODAS as
//             partidas finalizadas daquela liga em ordem cronológica. Todo
//             time começa em 1500 (Elo clássico) — não depende de nenhuma
//             fonte externa, já que times da mesma liga não precisam de
//             calibração cross-liga pra se comparar entre si.
//   'geral' — um rating único cross-liga, que só se ajusta em confrontos
//             diretos internacionais (hoje, na prática, só Champions League
//             — liga 19 — já que Libertadores/Sudamericana ainda não estão
//             no pipeline). Semente: pra times de liga europeia com histórico
//             no ClubElo (team_elo_external), usa a média do rating externo
//             como ponto de partida — times sem essa referência (ex.: todo o
//             Brasileirão, já que o ClubElo não cobre clubes brasileiros)
//             começam em 1500 igual, e o rating vai convergindo só com o
//             próprio confronto direto ao longo do tempo.
//
// Fórmula: Elo clássico com multiplicador de diferença de gols (mesmo
// espírito do World Football Elo Ratings pra seleções): G=1 se |dif|<=1,
// 1.5 se dif=2, (11+dif)/8 se dif>=3. K=20, vantagem de casa=65 pontos.
//
// RECOMPUTE EM LOTES POR LIGA: processar as 6 ligas domésticas + o escopo
// geral tudo numa chamada só passava dos 60s do Vercel em runs mais lentos
// (14k+ partidas). Cada chamada agora recalcula só UM escopo por vez — bem
// mais rápido (cada liga tem ~1/6 do total de partidas) e sempre idempotente
// (apaga e regrava só a fatia daquele escopo/liga, não a tabela inteira).
//
// COMO CHAMAR (uma chamada por vez, repetir pra cada liga):
//   /api/compute-elo?liga_id=1     (recalcula só o escopo 'liga' do Brasileirão)
//   /api/compute-elo?liga_id=4     (idem pra Premier League — repetir pra 4,7,10,13,16)
//   /api/compute-elo?escopo=geral  (recalcula o escopo 'geral', cross-liga — rodar por último,
//                                    depois de todas as ligas, não é estritamente necessário
//                                    mas mantém a ordem lógica)

import { createClient } from '@supabase/supabase-js';

function getSupabase() {
  return createClient(process.env.SUPABASE_URL, process.env.SUPABASE_SERVICE_ROLE_KEY);
}

const RATING_INICIAL = 1500;
const K = 20;
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
  const delta = K * multiplicadorDiferenca(diferenca) * (resultadoMandante - esperadoMandante);
  return { novoMandante: ratingMandante + delta, novoVisitante: ratingVisitante - delta };
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

function* fatiar(array, tamanho) {
  for (let i = 0; i < array.length; i += tamanho) yield array.slice(i, i + tamanho);
}

async function regravar(supabase, linhasElo, linhasHistorico, filtroDelete) {
  // .match() vira .eq() por campo — "league_id: null" nunca bate com IS NULL
  // via eq(), por isso o escopo 'geral' (league_id sempre null) usa só o
  // filtro por escopo, que já é suficiente pra isolar as linhas certas.
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

async function processarLiga(supabase, ligaId) {
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

  await regravar(supabase, linhasElo, historico, { escopo: 'liga', league_id: ligaId });
  return { escopo: 'liga', league_id: ligaId, partidas_processadas: partidas.length, times_com_elo: linhasElo.length };
}

async function processarGeral(supabase) {
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

  // Garante que todo time visto em jogo doméstico tenha entrada no geral
  // (mesmo sem Champions), semeada do jeito combinado.
  for (const p of partidasDomesticas) {
    for (const timeId of [p.home_team_id, p.away_team_id]) {
      if (!(timeId in rating)) { rating[timeId] = seedPorTime[timeId] ?? RATING_INICIAL; contagem[timeId] = 0; }
    }
  }

  const linhasElo = Object.entries(rating).map(([team_id, r]) => ({
    team_id: Number(team_id), escopo: 'geral', league_id: null, rating: r, partidas: contagem[team_id] || 0, atualizado_em: new Date().toISOString(),
  }));

  await regravar(supabase, linhasElo, historico, { escopo: 'geral', league_id: null });
  return { escopo: 'geral', partidas_processadas: partidasChampions.length, times_com_elo: linhasElo.length };
}

export default async function handler(req, res) {
  const supabaseUrl = process.env.SUPABASE_URL, serviceKey = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!supabaseUrl || !serviceKey) {
    return res.status(500).json({ error: { message: 'SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY não configuradas.' } });
  }
  const supabase = getSupabase();
  const { liga_id, escopo } = req.query;

  try {
    if (liga_id) {
      const ligaIdNum = Number(liga_id);
      if (!LIGAS_DOMESTICAS.includes(ligaIdNum)) {
        return res.status(400).json({ error: { message: `liga_id inválido — precisa ser uma das ligas domésticas: ${LIGAS_DOMESTICAS.join(', ')}.` } });
      }
      return res.status(200).json(await processarLiga(supabase, ligaIdNum));
    }
    if (escopo === 'geral') {
      return res.status(200).json(await processarGeral(supabase));
    }
    return res.status(400).json({
      error: { message: 'Especifique ?liga_id=X (uma das ligas domésticas) ou ?escopo=geral — recompute em lote único foi removido por estourar o timeout do Vercel.' },
      ligas_domesticas: LIGAS_DOMESTICAS,
    });
  } catch (erro) {
    res.status(500).json({ error: { message: erro.message } });
  }
}
