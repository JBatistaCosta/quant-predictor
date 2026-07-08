// api/fixtures.js
// Roda no SERVIDOR do Vercel. Busca jogos (passados e futuros) de um time,
// usando a API-Football (mesma chave já usada em team-stats.js).
//
// COMO CHAMAR:
//   /api/fixtures?time=Brazil&dias_passado=30&dias_futuro=60

const BASE_URL = 'https://v3.football.api-sports.io';

async function chamarAPI(caminho, apiKey) {
  const resposta = await fetch(`${BASE_URL}${caminho}`, { headers: { 'x-apisports-key': apiKey } });
  const dados = await resposta.json();
  if (dados.errors && Object.keys(dados.errors).length > 0) {
    throw new Error(`Erro da API em ${caminho}: ${JSON.stringify(dados.errors)}`);
  }
  return dados.response;
}

function formatarData(d) {
  return d.toISOString().slice(0, 10);
}

export default async function handler(req, res) {
  const apiKey = process.env.API_FOOTBALL_KEY;
  if (!apiKey) return res.status(500).json({ error: { message: 'API_FOOTBALL_KEY não configurada.' } });

  const { time, dias_passado, dias_futuro } = req.query;
  if (!time) return res.status(400).json({ error: { message: 'Informe ?time=NomeDoTime na URL.' } });

  const diasPassado = parseInt(dias_passado, 10) || 30;
  const diasFuturo = parseInt(dias_futuro, 10) || 30;

  try {
    const timesEncontrados = await chamarAPI(`/teams?search=${encodeURIComponent(time)}`, apiKey);
    if (!timesEncontrados || timesEncontrados.length === 0) {
      return res.status(404).json({ error: { message: `Nenhum time encontrado pra "${time}" na API-Football.` } });
    }
    const teamId = timesEncontrados[0].team.id;

    const hoje = new Date();
    const de = new Date(hoje); de.setDate(de.getDate() - diasPassado);
    const ate = new Date(hoje); ate.setDate(ate.getDate() + diasFuturo);

    const fixtures = await chamarAPI(
      `/fixtures?team=${teamId}&from=${formatarData(de)}&to=${formatarData(ate)}`,
      apiKey
    );

    const jogos = (fixtures || []).map(f => ({
      fixture_id: f.fixture.id,
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

    res.status(200).json({ time_buscado: time, time_id_api_football: teamId, total: jogos.length, jogos });
  } catch (erro) {
    res.status(500).json({ error: { message: erro.message } });
  }
}
