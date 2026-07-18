// api/sync-match-stats.js
// Roda no SERVIDOR do Vercel. Variáveis de ambiente necessárias:
//   API_FOOTBALL_KEY           -> mesma chave usada em api/team-stats.js
//   SUPABASE_URL               -> mesma dos outros arquivos api/*.js
//   SUPABASE_SERVICE_ROLE_KEY  -> mesma de api/sync-matches.js (escrita em
//     match_stats também é bloqueada pra chave anon pelo RLS)
//
// Preenche public.match_stats (chutes, escanteios, cartões, xG por time por
// partida) pros jogos que api/sync-matches.js já trouxe mas que a
// football-data.org não tem estatística nenhuma no plano grátis — só placar.
// Usa a API-Football, que tem esse dado, mas o preço é: (1) sem ponte de ID
// salva com nossas partidas (matches.external_id é do formato football-data,
// não bate com o id da API-Football) — o casamento é por DATA + nome do time
// aproximado (matching por TOKEN de palavra, não substring — ver
// CONTEXTO_PROJETO.md sobre o bug de colisão corrigido nesse padrão). A liga
// em si é resolvida via liga_fonte_externa (sistema='api_football') quando
// já confirmada — buscar por nome falha pra ligas cujo nome interno diverge
// do nome real na API (Brasileirão -> "Serie A"/Brazil lá, achado em produção);
// (2) 2 chamadas de API por partida (uma por time), então processa em lotes
// (?limite=N, padrão 40) espaçados (400ms entre partidas) — plano pago
// contratado depois (300 req/min, 7500/dia), bem folgado; ainda assim mantém
// pacing e detecção de rate limit como rede de segurança (se bater o limite
// no meio do lote, para sem marcar nada como "sem casamento" — os jogos
// continuam pendentes, a próxima chamada retoma).
// "Pendente" = falta escanteios OU xG, não só falta a linha inteira — cobre
// ligas que já têm outros campos preenchidos por outra fonte.
//
// COMO CHAMAR:
//   /api/sync-match-stats?liga_id=1&temporada=2026&limite=40
//   (liga_id = id em public.leagues; sem limite, processa até 40 por vez)

import { createClient } from '@supabase/supabase-js';

const BASE_URL = 'https://v3.football.api-sports.io';

function getSupabase() {
  return createClient(process.env.SUPABASE_URL, process.env.SUPABASE_SERVICE_ROLE_KEY);
}

async function chamarAPI(caminho, apiKey) {
  const resposta = await fetch(`${BASE_URL}${caminho}`, { headers: { 'x-apisports-key': apiKey } });
  const dados = await resposta.json();
  if (dados.errors && Object.keys(dados.errors).length > 0) {
    throw new Error(`API-Football: ${JSON.stringify(dados.errors)}`);
  }
  return dados.response;
}

// Normaliza preservando espaços (vira tokens) em vez de colapsar tudo numa
// string única — colapsar tudo foi o que causou colisão por substring em
// api/model-maintenance.js (ver CONTEXTO_PROJETO.md, ex: "ABC" batendo com
// "Atalanta BC"). Mesmo padrão usado lá e em ingestao_stats_fbref.py.
function normalizar(s) {
  return (s || '')
    .toLowerCase()
    .normalize('NFD').replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
}

function nomesBatem(nomeA, nomeB) {
  const a = normalizar(nomeA), b = normalizar(nomeB);
  if (!a || !b) return false;
  if (a === b) return true;
  const tokensA = new Set(a.split(' '));
  const tokensB = new Set(b.split(' '));
  return [...tokensA].every(t => tokensB.has(t)) || [...tokensB].every(t => tokensA.has(t));
}

// Acha o id da liga na API-Football. Prioriza o crosswalk já confirmado em
// liga_fonte_externa (mesma tabela usada por backfill-api-football) — busca
// por nome falha pra ligas cujo nome interno diverge do nome real da API
// (ex: "Brasileirão Série A" aqui x "Serie A"/país Brazil lá, achado testando
// em produção). Só cai pra busca por nome quando não há crosswalk salvo.
async function acharLigaNaApiFootball(supabase, ligaId, nomeLiga, apiKey) {
  const { data: fonteRow } = await supabase
    .from('liga_fonte_externa').select('identificador')
    .eq('league_id', ligaId).eq('sistema', 'api_football').maybeSingle();
  if (fonteRow) return Number(fonteRow.identificador);

  const resultados = await chamarAPI(`/leagues?search=${encodeURIComponent(nomeLiga)}`, apiKey);
  if (!resultados || resultados.length === 0) return null;
  // prioriza o resultado cujo nome bate mais de perto (evita pegar copa/torneio homônimo)
  const exato = resultados.find(r => normalizar(r.league.name) === normalizar(nomeLiga));
  return (exato || resultados[0]).league.id;
}

function paraNumero(v) {
  if (v === null || v === undefined || v === 'N/A') return null;
  const n = parseFloat(String(v).replace('%', ''));
  return Number.isFinite(n) ? n : null;
}

async function buscarEstatisticasDoTime(fixtureId, teamIdApiFootball, apiKey) {
  const stats = await chamarAPI(`/fixtures/statistics?fixture=${fixtureId}&team=${teamIdApiFootball}`, apiKey);
  if (!stats || stats.length === 0) return null;
  const porTipo = {};
  stats[0].statistics.forEach(s => { porTipo[s.type] = s.value; });
  return {
    shots: paraNumero(porTipo['Total Shots']),
    shots_on_target: paraNumero(porTipo['Shots on Goal']),
    possession: paraNumero(porTipo['Ball Possession']),
    corners: paraNumero(porTipo['Corner Kicks']),
    fouls: paraNumero(porTipo['Fouls']),
    yellow_cards: paraNumero(porTipo['Yellow Cards']),
    red_cards: paraNumero(porTipo['Red Cards']),
    xg: paraNumero(porTipo['expected_goals']),
  };
}

export default async function handler(req, res) {
  const apiKey = process.env.API_FOOTBALL_KEY;
  if (!apiKey) return res.status(500).json({ error: { message: 'API_FOOTBALL_KEY não configurada.' } });
  const supabaseUrl = process.env.SUPABASE_URL, supabaseKey = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!supabaseUrl || !supabaseKey) return res.status(500).json({ error: { message: 'SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY não configuradas.' } });

  const { liga_id, temporada, limite } = req.query;
  if (!liga_id || !temporada) return res.status(400).json({ error: { message: 'Informe ?liga_id=ID (de public.leagues) e ?temporada=AAAA na URL.' } });
  const limiteJogos = parseInt(limite, 10) || 40; // 2 chamadas/jogo + espaçamento de 400ms — cabe dentro de maxDuration=60 no plano pago

  const supabase = getSupabase();

  try {
    const { data: ligaRow } = await supabase.from('leagues').select('id, name').eq('id', liga_id).maybeSingle();
    if (!ligaRow) return res.status(404).json({ error: { message: 'Liga não encontrada em leagues.' } });

    // Jogos já disputados dessa liga/temporada que ainda não têm estatística salva
    const { data: jogosSemStats } = await supabase
      .from('matches')
      .select('id, match_date, home_team_id, away_team_id, home:teams!matches_home_team_id_fkey(name), away:teams!matches_away_team_id_fkey(name)')
      .eq('league_id', liga_id)
      .eq('season', temporada)
      .eq('status', 'finished')
      .order('match_date', { ascending: true });

    if (!jogosSemStats || jogosSemStats.length === 0) {
      return res.status(200).json({ mensagem: 'Nenhum jogo finalizado encontrado pra essa liga/temporada.' });
    }

    // "Pendente" = falta QUALQUER campo de estatística, não só falta a linha
    // inteira — algumas ligas (ex: Brasileirão) já têm chutes/posse/cartões
    // preenchidos por outra fonte mas ficaram com escanteios/xG null, porque
    // a resposta da API-Football pra essas partidas não trouxe esse tipo de
    // stat (ou porque essa tarefa nunca rodou de fato pra essa liga).
    const { data: statsExistentes } = await supabase.from('match_stats').select('match_id, corners, xg').in('match_id', jogosSemStats.map(j => j.id));
    const idsCompletos = new Set(
      (statsExistentes || []).filter(r => r.corners !== null || r.xg !== null).map(r => r.match_id)
    );
    const pendentes = jogosSemStats.filter(j => !idsCompletos.has(j.id)).slice(0, limiteJogos);

    if (pendentes.length === 0) {
      return res.status(200).json({ mensagem: 'Todos os jogos dessa liga/temporada já têm escanteios ou xG salvos.', total_finalizados: jogosSemStats.length });
    }

    const ligaIdApiFootball = await acharLigaNaApiFootball(supabase, liga_id, ligaRow.name, apiKey);
    if (!ligaIdApiFootball) return res.status(404).json({ error: { message: `Liga "${ligaRow.name}" não encontrada na API-Football.` } });

    const fixturesApiFootball = await chamarAPI(`/fixtures?league=${ligaIdApiFootball}&season=${temporada}`, apiKey);

    let processados = 0, comStats = 0;
    const semCasamento = [];
    const esperar = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    let paradoPorRateLimit = false;

    for (const jogo of pendentes) {
      // cada jogo gasta 2 chamadas (home+away) — plano pago permite 300/min,
      // pacing modesto só como rede de segurança (detecção de rate limit abaixo
      // cobre o resto)
      if (processados > 0) await esperar(400);

      const dataJogo = jogo.match_date?.slice(0, 10);
      const fixture = (fixturesApiFootball || []).find(f =>
        f.fixture.date?.slice(0, 10) === dataJogo &&
        nomesBatem(f.teams.home.name, jogo.home?.name) &&
        nomesBatem(f.teams.away.name, jogo.away?.name)
      );
      if (!fixture) { processados++; semCasamento.push(`${jogo.home?.name} x ${jogo.away?.name} (${dataJogo})`); continue; }

      let statsHome, statsAway;
      try {
        [statsHome, statsAway] = await Promise.all([
          buscarEstatisticasDoTime(fixture.fixture.id, fixture.teams.home.id, apiKey),
          buscarEstatisticasDoTime(fixture.fixture.id, fixture.teams.away.id, apiKey),
        ]);
      } catch (erro) {
        // rate limit (por minuto ou diário): para o lote sem marcar nada como
        // sem-casamento — o jogo continua pendente e a próxima chamada tenta de novo
        if (/rate ?limit|limit for the day|request limit/i.test(erro.message)) { paradoPorRateLimit = true; break; }
        throw erro;
      }
      processados++;

      if (statsHome) {
        const { error } = await supabase.from('match_stats').upsert({ match_id: jogo.id, team_id: jogo.home_team_id, ...statsHome, xg_source: statsHome.xg != null ? 'api-football' : null }, { onConflict: 'match_id,team_id' });
        if (!error) comStats++;
      }
      if (statsAway) {
        const { error } = await supabase.from('match_stats').upsert({ match_id: jogo.id, team_id: jogo.away_team_id, ...statsAway, xg_source: statsAway.xg != null ? 'api-football' : null }, { onConflict: 'match_id,team_id' });
        if (!error) comStats++;
      }
    }

    res.status(200).json({
      liga: ligaRow.name, temporada,
      total_finalizados: jogosSemStats.length,
      ja_completos: idsCompletos.size,
      processados_agora: processados,
      linhas_de_stats_gravadas: comStats,
      restantes: jogosSemStats.length - idsCompletos.size - processados,
      parado_por_rate_limit: paradoPorRateLimit || undefined,
      sem_casamento: semCasamento.length > 0 ? semCasamento : undefined,
    });
  } catch (erro) {
    res.status(500).json({ error: { message: erro.message } });
  }
}
