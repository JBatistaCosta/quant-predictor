// api/fixtures.js
// Roda no SERVIDOR do Vercel. Busca jogos (passados e futuros) de um time.
//
// DUAS FONTES, com fallback automático:
//   1ª) API-Football — tentada primeiro (cobertura maior: 1.200+ ligas)
//   2ª) football-data.org — só entra em ação se a 1ª falhar (chave inválida,
//       cota estourada, erro de rede etc.). Limitação conhecida: a busca por
//       nome do time nessa fonte só cobre as 12 competições do plano grátis
//       deles — hoje implementada especificamente pra Copa do Mundo (WC),
//       que é o uso principal do sistema agora. Fora da Copa, o fallback
//       pode não encontrar o time.
//
// COMO CHAMAR:
//   /api/fixtures?time=Brazil&dias_passado=30&dias_futuro=60

const API_FOOTBALL_URL = 'https://v3.football.api-sports.io';
const FOOTBALL_DATA_URL = 'https://api.football-data.org/v4';

function formatarData(d) {
  return d.toISOString().slice(0, 10);
}

// --- Fonte 1: API-Football ---
async function chamarApiFootball(caminho, apiKey) {
  const resposta = await fetch(`${API_FOOTBALL_URL}${caminho}`, { headers: { 'x-apisports-key': apiKey } });
  const dados = await resposta.json();
  if (!resposta.ok) throw new Error(`API-Football HTTP ${resposta.status}`);
  if (dados.errors && Object.keys(dados.errors).length > 0) {
    throw new Error(`API-Football: ${JSON.stringify(dados.errors)}`);
  }
  return dados.response;
}

async function buscarViaApiFootball(time, de, ate, apiKey) {
  const timesEncontrados = await chamarApiFootball(`/teams?search=${encodeURIComponent(time)}`, apiKey);
  if (!timesEncontrados || timesEncontrados.length === 0) {
    throw new Error(`Nenhum time encontrado pra "${time}" na API-Football.`);
  }
  const teamId = timesEncontrados[0].team.id;
  const fixtures = await chamarApiFootball(`/fixtures?team=${teamId}&from=${formatarData(de)}&to=${formatarData(ate)}`, apiKey);
  return normalizarFixturesApiFootball(fixtures);
}

// Busca TODOS os jogos de uma liga inteira numa temporada — não precisa de time nenhum
async function buscarLigaViaApiFootball(nomeLiga, temporada, apiKey) {
  const ligasEncontradas = await chamarApiFootball(`/leagues?name=${encodeURIComponent(nomeLiga)}`, apiKey);
  if (!ligasEncontradas || ligasEncontradas.length === 0) {
    throw new Error(`Nenhuma liga encontrada pra "${nomeLiga}" na API-Football.`);
  }
  const leagueId = ligasEncontradas[0].league.id;
  const fixtures = await chamarApiFootball(`/fixtures?league=${leagueId}&season=${temporada}`, apiKey);
  return normalizarFixturesApiFootball(fixtures);
}

function normalizarFixturesApiFootball(fixtures) {
  return (fixtures || []).map(f => ({
    fixture_id: `af_${f.fixture.id}`,
    data: f.fixture.date?.slice(0, 10),
    status: f.fixture.status?.short,
    liga: f.league?.name,
    temporada: f.league?.season,
    rodada: f.league?.round,
    mandante: f.teams.home.name,
    visitante: f.teams.away.name,
    placar_mandante: f.goals.home,
    placar_visitante: f.goals.away,
    resolvido: ['FT', 'AET', 'PEN'].includes(f.fixture.status?.short),
  }));
}

// --- Fonte 2: football-data.org (fallback) ---
async function chamarFootballData(caminho, apiKey) {
  const resposta = await fetch(`${FOOTBALL_DATA_URL}${caminho}`, { headers: { 'X-Auth-Token': apiKey } });
  const dados = await resposta.json();
  if (!resposta.ok) throw new Error(`football-data.org HTTP ${resposta.status}: ${dados.message || ''}`);
  return dados;
}

async function buscarViaFootballData(time, de, ate, apiKey) {
  // Limitação conhecida (ver comentário no topo): só procura dentro da Copa do Mundo (WC)
  const timesDaCopa = await chamarFootballData('/competitions/WC/teams', apiKey);
  const timeEncontrado = (timesDaCopa.teams || []).find(t =>
    t.name.toLowerCase().includes(time.toLowerCase()) || time.toLowerCase().includes(t.name.toLowerCase())
  );
  if (!timeEncontrado) {
    throw new Error(`Nenhum time encontrado pra "${time}" na football-data.org (busca limitada à Copa do Mundo nessa fonte).`);
  }

  const dados = await chamarFootballData(
    `/teams/${timeEncontrado.id}/matches?dateFrom=${formatarData(de)}&dateTo=${formatarData(ate)}`,
    apiKey
  );

  return normalizarMatchesFootballData(dados.matches);
}

// As 12 competições do plano grátis da football-data.org — usado pra achar o
// código a partir do nome digitado (a API deles usa código, não nome, na URL)
const CODIGOS_FOOTBALL_DATA = {
  'premier league': 'PL', 'la liga': 'PD', 'bundesliga': 'BL1', 'serie a': 'SA',
  'ligue 1': 'FL1', 'champions league': 'CL', 'eredivisie': 'DED', 'primeira liga': 'PPL',
  'championship': 'ELC', 'brasileirao': 'BSA', 'brazil': 'BSA', 'world cup': 'WC', 'copa do mundo': 'WC',
  'european championship': 'EC', 'euro': 'EC',
};

async function buscarLigaViaFootballData(nomeLiga, temporada, apiKey) {
  const codigo = CODIGOS_FOOTBALL_DATA[nomeLiga.toLowerCase().trim()];
  if (!codigo) {
    throw new Error(`"${nomeLiga}" não está entre as 12 competições gratuitas da football-data.org.`);
  }
  const dados = await chamarFootballData(`/competitions/${codigo}/matches?season=${temporada}`, apiKey);
  return normalizarMatchesFootballData(dados.matches);
}

function normalizarMatchesFootballData(matches) {
  return (matches || []).map(m => ({
    fixture_id: `fd_${m.id}`,
    data: m.utcDate?.slice(0, 10),
    status: m.status,
    liga: m.competition?.name,
    temporada: m.season?.startDate?.slice(0, 4),
    rodada: m.matchday,
    mandante: m.homeTeam?.name,
    visitante: m.awayTeam?.name,
    placar_mandante: m.score?.fullTime?.homeTeam ?? null,
    placar_visitante: m.score?.fullTime?.awayTeam ?? null,
    resolvido: m.status === 'FINISHED',
  }));
}

export default async function handler(req, res) {
  const apiFootballKey = process.env.API_FOOTBALL_KEY;
  const footballDataKey = process.env.FOOTBALL_DATA_KEY;

  const { time, liga, temporada, dias_passado, dias_futuro } = req.query;
  const modoLiga = !!liga;

  if (!modoLiga && !time) return res.status(400).json({ error: { message: 'Informe ?time=NomeDoTime OU ?liga=NomeDaLiga&temporada=AAAA na URL.' } });
  if (modoLiga && !temporada) return res.status(400).json({ error: { message: 'No modo liga, informe também ?temporada=AAAA (ex: 2026).' } });

  const diasPassado = parseInt(dias_passado, 10) || 30;
  const diasFuturo = parseInt(dias_futuro, 10) || 30;
  const hoje = new Date();
  const de = new Date(hoje); de.setDate(de.getDate() - diasPassado);
  const ate = new Date(hoje); ate.setDate(ate.getDate() + diasFuturo);

  let jogos, fonteUsada, avisoFallback;

  // Tenta a fonte 1 primeiro
  if (apiFootballKey) {
    try {
      jogos = modoLiga
        ? await buscarLigaViaApiFootball(liga, temporada, apiFootballKey)
        : await buscarViaApiFootball(time, de, ate, apiFootballKey);
      fonteUsada = 'API-Football';
    } catch (erro1) {
      avisoFallback = `API-Football falhou (${erro1.message}), tentando football-data.org...`;
    }
  } else {
    avisoFallback = 'API_FOOTBALL_KEY não configurada, tentando football-data.org...';
  }

  // Se a 1ª não deu certo, tenta a fonte 2
  if (!jogos && footballDataKey) {
    try {
      jogos = modoLiga
        ? await buscarLigaViaFootballData(liga, temporada, footballDataKey)
        : await buscarViaFootballData(time, de, ate, footballDataKey);
      fonteUsada = 'football-data.org';
    } catch (erro2) {
      return res.status(500).json({
        error: { message: `As duas fontes falharam. ${avisoFallback || ''} football-data.org: ${erro2.message}` }
      });
    }
  }

  if (!jogos) {
    return res.status(500).json({ error: { message: avisoFallback || 'Nenhuma fonte de dados configurada (API_FOOTBALL_KEY ou FOOTBALL_DATA_KEY).' } });
  }

  // No modo liga, filtra pelo intervalo de datas também (API-Football não aceita from/to junto com league+season)
  if (modoLiga) {
    const deStr = formatarData(de), ateStr = formatarData(ate);
    jogos = jogos.filter(j => j.data >= deStr && j.data <= ateStr);
  }

  res.status(200).json({ busca: modoLiga ? liga : time, modo: modoLiga ? 'liga' : 'time', fonte_usada: fonteUsada, aviso: avisoFallback, total: jogos.length, jogos });
}
