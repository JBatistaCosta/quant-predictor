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

  return (fixtures || []).map(f => ({
    fixture_id: `af_${f.fixture.id}`,
    data: f.fixture.date?.slice(0, 10),
    status: f.fixture.status?.short,
    liga: f.league?.name,
    temporada: f.league?.season,
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

  return (dados.matches || []).map(m => ({
    fixture_id: `fd_${m.id}`,
    data: m.utcDate?.slice(0, 10),
    status: m.status,
    liga: m.competition?.name,
    temporada: m.season?.startDate?.slice(0, 4),
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

  const { time, dias_passado, dias_futuro } = req.query;
  if (!time) return res.status(400).json({ error: { message: 'Informe ?time=NomeDoTime na URL.' } });

  const diasPassado = parseInt(dias_passado, 10) || 30;
  const diasFuturo = parseInt(dias_futuro, 10) || 30;
  const hoje = new Date();
  const de = new Date(hoje); de.setDate(de.getDate() - diasPassado);
  const ate = new Date(hoje); ate.setDate(ate.getDate() + diasFuturo);

  let jogos, fonteUsada, avisoFallback;

  // Tenta a fonte 1 primeiro
  if (apiFootballKey) {
    try {
      jogos = await buscarViaApiFootball(time, de, ate, apiFootballKey);
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
      jogos = await buscarViaFootballData(time, de, ate, footballDataKey);
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

  res.status(200).json({ time_buscado: time, fonte_usada: fonteUsada, aviso: avisoFallback, total: jogos.length, jogos });
}
