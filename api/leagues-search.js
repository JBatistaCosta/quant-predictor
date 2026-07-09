// api/leagues-search.js
// Roda no SERVIDOR do Vercel. Busca ligas/competições por nome na API-Football,
// devolvendo os dados prontos pra preencher o formulário de cadastro (não jogos —
// isso é diferente do /api/fixtures?liga=..., que busca os CONFRONTOS de uma liga).
//
// COMO CHAMAR:
//   /api/leagues-search?nome=Premier League

const BASE_URL = 'https://v3.football.api-sports.io';

// Heurística simples pra mapear o "type" da API-Football (só League/Cup) nas
// 4 categorias do nosso sistema — o usuário pode corrigir manualmente depois.
// A API não diz "seleção" ou "clube" diretamente, então usamos palavras-chave
// conhecidas de competições de clube pra separar dos torneios de seleção.
const PALAVRAS_COMPETICAO_DE_CLUBE = ['champions league', 'europa league', 'conference league', 'libertadores', 'sul-americana', 'copa do brasil', 'club world cup', 'recopa'];

function inferirTipo(apiType, nomeCompeticao, nomePais) {
  const internacional = !nomePais || nomePais === 'World';
  if (internacional) {
    const ehDeClube = PALAVRAS_COMPETICAO_DE_CLUBE.some(p => nomeCompeticao.toLowerCase().includes(p));
    return ehDeClube ? 'copa_continental' : 'torneio_internacional';
  }
  return apiType === 'Cup' ? 'copa_nacional' : 'liga_domestica';
}

export default async function handler(req, res) {
  const apiKey = process.env.API_FOOTBALL_KEY;
  if (!apiKey) return res.status(500).json({ error: { message: 'API_FOOTBALL_KEY não configurada.' } });

  const { nome } = req.query;
  if (!nome) return res.status(400).json({ error: { message: 'Informe ?nome=NomeDaLiga na URL.' } });

  try {
    const resposta = await fetch(`${BASE_URL}/leagues?name=${encodeURIComponent(nome)}`, {
      headers: { 'x-apisports-key': apiKey },
    });
    const dados = await resposta.json();
    if (!resposta.ok) throw new Error(`API-Football HTTP ${resposta.status}`);
    if (dados.errors && Object.keys(dados.errors).length > 0) {
      throw new Error(`API-Football: ${JSON.stringify(dados.errors)}`);
    }

    const resultados = (dados.response || []).slice(0, 10).map(r => ({
      nome: r.league.name,
      tipo: inferirTipo(r.league.type, r.league.name, r.country?.name),
      pais: r.country?.name !== 'World' ? r.country?.name : null,
      confederacao: r.country?.name === 'World' ? 'FIFA' : null,
      simbolo_url: r.league.logo || null,
      temporada_atual: r.seasons?.find(s => s.current)?.year || r.seasons?.[r.seasons.length - 1]?.year || null,
    }));

    res.status(200).json({ total: resultados.length, resultados });
  } catch (erro) {
    res.status(500).json({ error: { message: erro.message } });
  }
}
