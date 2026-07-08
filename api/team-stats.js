// api/team-stats.js
// Roda no SERVIDOR do Vercel. Duas chaves nas variáveis de ambiente:
//   API_FOOTBALL_KEY   -> chave da API-Football
//   SUPABASE_URL       -> URL do projeto Supabase
//   SUPABASE_KEY       -> chave pública (publishable/anon) do Supabase
//
// LÓGICA DE CACHE (a "economia" que você pediu): antes de gastar uma chamada
// da API-Football, o time é procurado na tabela `team_stats` do Supabase.
// Se já tiver sido atualizado nas últimas HORAS_DE_CACHE horas, usa o que já
// está salvo — sem gastar cota nenhuma da API-Football. Só busca de novo se
// estiver ausente ou "velho".
//
// COMO CHAMAR (aceita 1 ou VÁRIOS times de uma vez, em lote):
//   /api/team-stats?mandante=Brazil&visitante=Norway&jogos=5
//   /api/team-stats?times=Brazil,Norway,Mexico,England&jogos=5

import { createClient } from '@supabase/supabase-js';

const BASE_URL = 'https://v3.football.api-sports.io';
const HORAS_DE_CACHE = 24; // depois de quanto tempo um time salvo é considerado "velho"

function getSupabase() {
  return createClient(process.env.SUPABASE_URL, process.env.SUPABASE_KEY);
}

async function chamarAPI(caminho, apiKey) {
  const resposta = await fetch(`${BASE_URL}${caminho}`, { headers: { 'x-apisports-key': apiKey } });
  const dados = await resposta.json();
  if (dados.errors && Object.keys(dados.errors).length > 0) {
    throw new Error(`Erro da API em ${caminho}: ${JSON.stringify(dados.errors)}`);
  }
  return dados.response;
}

async function buscarIdDoTime(nome, apiKey) {
  const resultado = await chamarAPI(`/teams?search=${encodeURIComponent(nome)}`, apiKey);
  if (!resultado || resultado.length === 0) throw new Error(`Não encontrei nenhum time chamado "${nome}".`);
  return resultado[0].team.id;
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

// Busca de verdade na API-Football (só é chamada quando o cache não serve)
async function buscarDaApiFootball(nomeTime, quantidadeJogos, apiKey) {
  const teamId = await buscarIdDoTime(nomeTime, apiKey);
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
    if (xg !== null) acumulado.xg.push(xg);
    if (xga !== null) acumulado.xga.push(xga);
    const chutes = paraNumero(statsProprio['Total Shots']);
    if (chutes !== null) acumulado.chutes.push(chutes);
    const cng = paraNumero(statsProprio['Shots on Goal']);
    if (cng !== null) acumulado.chutesNoGol.push(cng);
    const esc = paraNumero(statsProprio['Corner Kicks']);
    if (esc !== null) acumulado.escanteios.push(esc);
  }

  const media = (lista) => (lista.length > 0 ? lista.reduce((a, b) => a + b, 0) / lista.length : null);
  return {
    xg: media(acumulado.xg) ?? 0,
    xga: media(acumulado.xga) ?? 0,
    chutes: media(acumulado.chutes),
    chutes_no_gol: media(acumulado.chutesNoGol),
    escanteios: media(acumulado.escanteios),
    aviso_xg_ausente: media(acumulado.xg) === null,
  };
}

// Busca UM time: cache do Supabase primeiro, API-Football só se precisar
async function buscarTimeComCache(nomeTime, quantidadeJogos, apiKey, supabase) {
  const { data: existente } = await supabase
    .from('team_stats')
    .select('*')
    .eq('team_name', nomeTime)
    .maybeSingle();

  const agora = Date.now();
  const idadeHoras = existente ? (agora - new Date(existente.updated_at).getTime()) / 3600000 : Infinity;

  if (existente && idadeHoras < HORAS_DE_CACHE) {
    return {
      metricas: {
        xg_gols_esperados: existente.xg, xga_xg_sofridos: existente.xga,
        chutes: existente.chutes, chutes_no_gol: existente.chutes_no_gol, escanteios: existente.escanteios,
      },
      origem: 'cache', idade_horas: Math.round(idadeHoras * 10) / 10,
    };
  }

  const dados = await buscarDaApiFootball(nomeTime, quantidadeJogos, apiKey);
  await supabase.from('team_stats').upsert({
    team_name: nomeTime, xg: dados.xg, xga: dados.xga,
    chutes: dados.chutes, chutes_no_gol: dados.chutes_no_gol, escanteios: dados.escanteios,
    updated_at: new Date().toISOString(),
  });

  return {
    metricas: {
      xg_gols_esperados: dados.xg, xga_xg_sofridos: dados.xga,
      chutes: dados.chutes, chutes_no_gol: dados.chutes_no_gol, escanteios: dados.escanteios,
    },
    origem: 'api-football', aviso_xg_ausente: dados.aviso_xg_ausente,
  };
}

export default async function handler(req, res) {
  const apiKey = process.env.API_FOOTBALL_KEY;
  const supabaseUrl = process.env.SUPABASE_URL;
  const supabaseKey = process.env.SUPABASE_KEY;
  if (!apiKey) return res.status(500).json({ error: { message: 'API_FOOTBALL_KEY não configurada.' } });
  if (!supabaseUrl || !supabaseKey) return res.status(500).json({ error: { message: 'SUPABASE_URL / SUPABASE_KEY não configuradas.' } });

  const supabase = getSupabase();
  const { mandante, visitante, times, jogos } = req.query;
  const quantidadeJogos = parseInt(jogos, 10) || 5;

  try {
    // Modo em lote: ?times=Brazil,Norway,Mexico,England
    if (times) {
      const listaTimes = times.split(',').map(t => t.trim()).filter(Boolean);
      const resultados = {};
      for (const nome of listaTimes) {
        resultados[nome] = await buscarTimeComCache(nome, quantidadeJogos, apiKey, supabase);
      }
      return res.status(200).json({ times: resultados });
    }

    // Modo confronto único: ?mandante=X&visitante=Y (formato pronto pros apps)
    if (!mandante || !visitante) {
      return res.status(400).json({ error: { message: 'Informe ?mandante=Nome&visitante=Nome OU ?times=Nome1,Nome2,... na URL.' } });
    }
    const [dadosMandante, dadosVisitante] = [
      await buscarTimeComCache(mandante, quantidadeJogos, apiKey, supabase),
      await buscarTimeComCache(visitante, quantidadeJogos, apiKey, supabase),
    ];

    const avisos = [];
    if (dadosMandante.aviso_xg_ausente) avisos.push(`Sem xG confiável para ${mandante} — usei 0.`);
    if (dadosVisitante.aviso_xg_ausente) avisos.push(`Sem xG confiável para ${visitante} — usei 0.`);

    res.status(200).json({
      confronto: { equipe_mandante: mandante, equipe_visitante: visitante },
      estatistica_media: {
        mandante: { metricas: dadosMandante.metricas },
        visitante: { metricas: dadosVisitante.metricas },
      },
      origem: { mandante: dadosMandante.origem, visitante: dadosVisitante.origem },
      avisos: avisos.length > 0 ? avisos : undefined,
    });
  } catch (erro) {
    res.status(500).json({ error: { message: erro.message } });
  }
}
