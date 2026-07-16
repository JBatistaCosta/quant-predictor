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
// Recomputa do zero a cada chamada (idempotente) — mais simples que estado
// incremental, e o custo (dezenas de milhares de partidas, aritmética
// simples) é barato.
//
// COMO CHAMAR:
//   /api/compute-elo   (recalcula tudo, sem parâmetro)

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

// Retorna { novoMandante, novoVisitante }
function atualizarElo(ratingMandante, ratingVisitante, golsMandante, golsVisitante) {
  const diferenca = golsMandante - golsVisitante;
  const resultadoMandante = diferenca > 0 ? 1 : diferenca < 0 ? 0 : 0.5;
  const esperadoMandante = 1 / (1 + Math.pow(10, -((ratingMandante + VANTAGEM_CASA) - ratingVisitante) / 400));
  const delta = K * multiplicadorDiferenca(diferenca) * (resultadoMandante - esperadoMandante);
  return { novoMandante: ratingMandante + delta, novoVisitante: ratingVisitante - delta };
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

export default async function handler(req, res) {
  const supabaseUrl = process.env.SUPABASE_URL, serviceKey = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!supabaseUrl || !serviceKey) {
    return res.status(500).json({ error: { message: 'SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY não configuradas.' } });
  }
  const supabase = getSupabase();

  try {
    const partidas = await buscarTudoPaginado(supabase, (sb) =>
      sb.from('matches').select('id, league_id, season, round, match_date, home_team_id, away_team_id, home_goals, away_goals, status')
        .eq('status', 'finished').not('home_goals', 'is', null).not('away_goals', 'is', null)
    );
    partidas.sort((a, b) => new Date(a.match_date) - new Date(b.match_date));

    // Semente do escopo geral: média do rating ClubElo mais recente de cada time (quando existe)
    const seedsExternas = await buscarTudoPaginado(supabase, (sb) => sb.from('team_elo_external').select('team_id, elo, valido_ate').order('valido_ate', { ascending: false }));
    const seedGeralPorTime = {};
    for (const s of seedsExternas) {
      if (!(s.team_id in seedGeralPorTime)) seedGeralPorTime[s.team_id] = Number(s.elo); // primeira ocorrência = mais recente (ordenado desc)
    }

    const ratingLiga = {};     // `${league_id}__${team_id}` -> rating
    const partidasLiga = {};   // idem -> contagem
    const ratingGeral = {};    // team_id -> rating
    const partidasGeral = {};  // team_id -> contagem

    const historicoLiga = [];
    const historicoGeral = [];

    for (const p of partidas) {
      const ehDomestica = LIGAS_DOMESTICAS.includes(p.league_id);
      const ehChampions = p.league_id === LIGA_CHAMPIONS_ID;
      if (!ehDomestica && !ehChampions) continue; // Eurocopa etc não entram (seleções, não clubes)

      if (ehDomestica) {
        const chaveMandante = `${p.league_id}__${p.home_team_id}`;
        const chaveVisitante = `${p.league_id}__${p.away_team_id}`;
        const antesMandante = ratingLiga[chaveMandante] ?? RATING_INICIAL;
        const antesVisitante = ratingLiga[chaveVisitante] ?? RATING_INICIAL;
        const { novoMandante, novoVisitante } = atualizarElo(antesMandante, antesVisitante, p.home_goals, p.away_goals);
        ratingLiga[chaveMandante] = novoMandante;
        ratingLiga[chaveVisitante] = novoVisitante;
        partidasLiga[chaveMandante] = (partidasLiga[chaveMandante] || 0) + 1;
        partidasLiga[chaveVisitante] = (partidasLiga[chaveVisitante] || 0) + 1;

        historicoLiga.push(
          { team_id: p.home_team_id, escopo: 'liga', league_id: p.league_id, match_id: p.id, rodada: p.round, rating_antes: antesMandante, rating_depois: novoMandante, match_date: p.match_date },
          { team_id: p.away_team_id, escopo: 'liga', league_id: p.league_id, match_id: p.id, rodada: p.round, rating_antes: antesVisitante, rating_depois: novoVisitante, match_date: p.match_date },
        );
      }

      if (ehChampions) {
        if (!(p.home_team_id in ratingGeral)) ratingGeral[p.home_team_id] = seedGeralPorTime[p.home_team_id] ?? RATING_INICIAL;
        if (!(p.away_team_id in ratingGeral)) ratingGeral[p.away_team_id] = seedGeralPorTime[p.away_team_id] ?? RATING_INICIAL;
        const antesMandante = ratingGeral[p.home_team_id];
        const antesVisitante = ratingGeral[p.away_team_id];
        const { novoMandante, novoVisitante } = atualizarElo(antesMandante, antesVisitante, p.home_goals, p.away_goals);
        ratingGeral[p.home_team_id] = novoMandante;
        ratingGeral[p.away_team_id] = novoVisitante;
        partidasGeral[p.home_team_id] = (partidasGeral[p.home_team_id] || 0) + 1;
        partidasGeral[p.away_team_id] = (partidasGeral[p.away_team_id] || 0) + 1;

        historicoGeral.push(
          { team_id: p.home_team_id, escopo: 'geral', league_id: null, match_id: p.id, rodada: p.round, rating_antes: antesMandante, rating_depois: novoMandante, match_date: p.match_date },
          { team_id: p.away_team_id, escopo: 'geral', league_id: null, match_id: p.id, rodada: p.round, rating_antes: antesVisitante, rating_depois: novoVisitante, match_date: p.match_date },
        );
      }
    }

    // Garante que todo time visto em algum jogo doméstico também tenha uma
    // entrada no escopo geral (mesmo sem nunca ter jogado Champions), semeada
    // do jeito combinado — assim o "geral" fica consultável pra qualquer time.
    for (const p of partidas) {
      if (!LIGAS_DOMESTICAS.includes(p.league_id)) continue;
      for (const timeId of [p.home_team_id, p.away_team_id]) {
        if (!(timeId in ratingGeral)) { ratingGeral[timeId] = seedGeralPorTime[timeId] ?? RATING_INICIAL; partidasGeral[timeId] = 0; }
      }
    }

    const linhasEloLiga = Object.entries(ratingLiga).map(([chave, rating]) => {
      const [league_id, team_id] = chave.split('__').map(Number);
      return { team_id, escopo: 'liga', league_id, rating, partidas: partidasLiga[chave], atualizado_em: new Date().toISOString() };
    });
    const linhasEloGeral = Object.entries(ratingGeral).map(([team_id, rating]) => ({
      team_id: Number(team_id), escopo: 'geral', league_id: null, rating, partidas: partidasGeral[team_id] || 0, atualizado_em: new Date().toISOString(),
    }));

    // Limpa e regrava do zero (recompute idempotente)
    await supabase.from('team_elo_history').delete().neq('id', 0);
    await supabase.from('team_elo').delete().neq('id', 0);

    for (const lote of fatiar([...linhasEloLiga, ...linhasEloGeral], 500)) {
      const { error } = await supabase.from('team_elo').upsert(lote, { onConflict: 'team_id,escopo,league_id' });
      if (error) throw error;
    }
    for (const lote of fatiar([...historicoLiga, ...historicoGeral], 500)) {
      const { error } = await supabase.from('team_elo_history').insert(lote);
      if (error) throw error;
    }

    res.status(200).json({
      partidas_processadas: partidas.length,
      times_com_elo_liga: linhasEloLiga.length,
      times_com_elo_geral: linhasEloGeral.length,
      linhas_historico: historicoLiga.length + historicoGeral.length,
    });
  } catch (erro) {
    res.status(500).json({ error: { message: erro.message } });
  }
}

function* fatiar(array, tamanho) {
  for (let i = 0; i < array.length; i += tamanho) yield array.slice(i, i + tamanho);
}
