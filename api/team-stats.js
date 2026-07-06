// api/team-stats.js
// Roda no SERVIDOR do Vercel — a chave da API-Football (API_FOOTBALL_KEY) fica
// guardada nas variáveis de ambiente do Vercel, nunca exposta no navegador.
//
// COMO CHAMAR (depois de configurado e publicado):
//   https://SEU-APP.vercel.app/api/team-stats?mandante=Brazil&visitante=Norway&jogos=5
//
// Retorna o JSON já pronto no formato usado pelo Quant System Predictor e pelo
// Copa 2026 Bracket Predictor (confronto + estatistica_media).

const BASE_URL = 'https://v3.football.api-sports.io';

async function chamarAPI(caminho, apiKey) {
  const resposta = await fetch(`${BASE_URL}${caminho}`, {
    headers: { 'x-apisports-key': apiKey },
  });
  const dados = await resposta.json();
  if (dados.errors && Object.keys(dados.errors).length > 0) {
    throw new Error(`Erro da API em ${caminho}: ${JSON.stringify(dados.errors)}`);
  }
  return dados.response;
}

async function buscarIdDoTime(nome, apiKey) {
  const resultado = await chamarAPI(`/teams?search=${encodeURIComponent(nome)}`, apiKey);
  if (!resultado || resultado.length === 0) {
    throw new Error(`Não encontrei nenhum time chamado "${nome}".`);
  }
  return { id: resultado[0].team.id, ambiguo: resultado.length > 1, opcoes: resultado.map(r => ({ id: r.team.id, nome: r.team.name, pais: r.team.country })) };
}

function paraNumero(v) {
  if (v === null || v === undefined || v === 'N/A') return null;
  const n = parseFloat(String(v).replace('%', ''));
  return Number.isFinite(n) ? n : null;
}

async function buscarEstatisticasDoJogo(fixtureId, teamId, apiKey) {
  const stats = await chamarAPI(`/fixtures/statistics?fixture=${fixtureId}&team=${teamId}`, apiKey);
  if (!stats || stats.length === 0) return null;
  const porTipo = {};
  stats[0].statistics.forEach(s => { porTipo[s.type] = s.value; });
  return porTipo;
}

async function calcularMediaDoTime(nomeTime, quantidadeJogos, apiKey) {
  const { id: teamId, ambiguo, opcoes } = await buscarIdDoTime(nomeTime, apiKey);
  const jogos = await chamarAPI(`/fixtures?team=${teamId}&last=${quantidadeJogos}`, apiKey);

  const acumulado = { xg: [], xga: [], chutes: [], chutesNoGol: [], escanteios: [] };

  for (const jogo of jogos) {
    const fixtureId = jogo.fixture.id;
    const ehMandante = jogo.teams.home.id === teamId;
    const idAdversario = ehMandante ? jogo.teams.away.id : jogo.teams.home.id;

    const statsProprio = await buscarEstatisticasDoJogo(fixtureId, teamId, apiKey);
    const statsAdversario = await buscarEstatisticasDoJogo(fixtureId, idAdversario, apiKey);
    if (!statsProprio) continue;

    const xg = paraNumero(statsProprio['expected_goals']);
    const xga = statsAdversario ? paraNumero(statsAdversario['expected_goals']) : null;
    const chutes = paraNumero(statsProprio['Total Shots']);
    const chutesNoGol = paraNumero(statsProprio['Shots on Goal']);
    const escanteios = paraNumero(statsProprio['Corner Kicks']);

    if (xg !== null) acumulado.xg.push(xg);
    if (xga !== null) acumulado.xga.push(xga);
    if (chutes !== null) acumulado.chutes.push(chutes);
    if (chutesNoGol !== null) acumulado.chutesNoGol.push(chutesNoGol);
    if (escanteios !== null) acumulado.escanteios.push(escanteios);
  }

  const media = (lista) => (lista.length > 0 ? lista.reduce((a, b) => a + b, 0) / lista.length : null);

  return {
    metricas: {
      xg_gols_esperados: media(acumulado.xg) ?? 0,
      xga_xg_sofridos: media(acumulado.xga) ?? 0,
      chutes: media(acumulado.chutes),
      chutes_no_gol: media(acumulado.chutesNoGol),
      escanteios: media(acumulado.escanteios),
    },
    aviso_xg_ausente: media(acumulado.xg) === null,
    time_ambiguo: ambiguo ? opcoes : null,
  };
}

export default async function handler(req, res) {
  const apiKey = process.env.API_FOOTBALL_KEY;
  if (!apiKey) {
    return res.status(500).json({ error: { message: 'API_FOOTBALL_KEY não configurada nas variáveis de ambiente do Vercel.' } });
  }

  const { mandante, visitante, jogos } = req.query;
  if (!mandante || !visitante) {
    return res.status(400).json({ error: { message: 'Informe ?mandante=Nome&visitante=Nome na URL.' } });
  }
  const quantidadeJogos = parseInt(jogos, 10) || 5;

  try {
    const [dadosMandante, dadosVisitante] = await Promise.all([
      calcularMediaDoTime(mandante, quantidadeJogos, apiKey),
      calcularMediaDoTime(visitante, quantidadeJogos, apiKey),
    ]);

    const avisos = [];
    if (dadosMandante.aviso_xg_ausente) avisos.push(`Sem xG confiável para ${mandante} — usei 0, complete manualmente se quiser.`);
    if (dadosVisitante.aviso_xg_ausente) avisos.push(`Sem xG confiável para ${visitante} — usei 0, complete manualmente se quiser.`);
    if (dadosMandante.time_ambiguo) avisos.push(`Mais de um time encontrado para "${mandante}": ${JSON.stringify(dadosMandante.time_ambiguo)}`);
    if (dadosVisitante.time_ambiguo) avisos.push(`Mais de um time encontrado para "${visitante}": ${JSON.stringify(dadosVisitante.time_ambiguo)}`);

    res.status(200).json({
      confronto: { equipe_mandante: mandante, equipe_visitante: visitante },
      estatistica_media: {
        mandante: { metricas: dadosMandante.metricas },
        visitante: { metricas: dadosVisitante.metricas },
      },
      avisos: avisos.length > 0 ? avisos : undefined,
    });
  } catch (erro) {
    res.status(500).json({ error: { message: erro.message } });
  }
}
