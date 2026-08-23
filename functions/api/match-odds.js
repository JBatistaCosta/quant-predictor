// api/match-odds.js
// Roda no SERVIDOR do Vercel. Variáveis de ambiente necessárias:
//   ODDS_API_KEY   -> chave da the-odds-api.com
//   SUPABASE_URL   -> URL do projeto Supabase (mesma do team-stats.js)
//   SUPABASE_KEY   -> chave pública do Supabase (mesma do team-stats.js)
//
// CACHE: odds mudam mais rápido que estatísticas de forma, então o tempo de
// cache aqui é bem menor (2 horas, não 24). Depois disso, busca de novo.
//
// IMPORTANTE — leia antes de confiar 100%:
// A The Odds API NÃO inclui a Betano especificamente (nenhuma API gratuita
// inclui). O que ela devolve é uma MÉDIA de odds de mercado de várias casas
// (Bet365, Pinnacle etc.), que costuma ficar próxima da Betano mas não é
// idêntica. Além disso, a cobertura da Copa do Mundo 2026 pode depender do
// seu plano — se vier vazio, é provável que seja isso (não um bug).
//
// COMO CHAMAR:
//   /api/match-odds?mandante=Brazil&visitante=Norway

import { createClient } from '@supabase/supabase-js';
import { applyCors } from './_lib/cors.js';

const HORAS_DE_CACHE = 2;
const SPORT_KEY = 'soccer_fifa_world_cup';
const BASE_URL = 'https://api.the-odds-api.com/v4';

function getSupabase() {
  return createClient(process.env.SUPABASE_URL, process.env.SUPABASE_KEY);
}

// Faz a média de um mercado (ex: h2h) entre todas as casas retornadas
function mediaDeMercado(bookmakers, marketKey) {
  const somas = {};
  const contagens = {};

  bookmakers.forEach(bk => {
    const mercado = bk.markets?.find(m => m.key === marketKey);
    if (!mercado) return;
    mercado.outcomes.forEach(o => {
      // Para "totals", cada linha (point) é um sub-grupo separado
      const chave = o.point !== undefined ? `${o.name}|${o.point}` : o.name;
      somas[chave] = (somas[chave] || 0) + o.price;
      contagens[chave] = (contagens[chave] || 0) + 1;
    });
  });

  const medias = {};
  Object.keys(somas).forEach(chave => { medias[chave] = somas[chave] / contagens[chave]; });
  return medias;
}

function montarResultadoFinal(mediasH2H, nomeCasa, nomeFora) {
  if (Object.keys(mediasH2H).length === 0) return null;
  return {
    casa: mediasH2H[nomeCasa] ? Number(mediasH2H[nomeCasa].toFixed(2)) : null,
    empate: mediasH2H['Draw'] ? Number(mediasH2H['Draw'].toFixed(2)) : null,
    fora: mediasH2H[nomeFora] ? Number(mediasH2H[nomeFora].toFixed(2)) : null,
  };
}

function montarTotalGols(mediasTotals) {
  // Agrupa "Over 2.5" / "Under 2.5" pela mesma linha (point)
  const porLinha = {};
  Object.entries(mediasTotals).forEach(([chave, preco]) => {
    const [nome, point] = chave.split('|');
    if (point === undefined) return;
    if (!porLinha[point]) porLinha[point] = { linha: Number(point) };
    if (nome === 'Over') porLinha[point].mais = Number(preco.toFixed(2));
    if (nome === 'Under') porLinha[point].menos = Number(preco.toFixed(2));
  });
  const lista = Object.values(porLinha).sort((a, b) => a.linha - b.linha);
  return lista.length > 0 ? lista : undefined;
}

async function buscarDaOddsApi(nomeCasa, nomeFora, apiKey) {
  const url = `${BASE_URL}/sports/${SPORT_KEY}/odds?regions=eu&markets=h2h,totals&oddsFormat=decimal&apiKey=${apiKey}`;
  const resposta = await fetch(url);
  const dados = await resposta.json();

  if (!resposta.ok) {
    throw new Error(`Erro da The Odds API: ${dados.message || resposta.statusText}`);
  }
  if (!Array.isArray(dados) || dados.length === 0) {
    return { encontrado: false, motivo: 'A API não retornou nenhum jogo (pode ser cobertura da Copa 2026 fora do plano gratuito, ou não haver jogos futuros suficientemente próximos ainda listados).' };
  }

  // Procura o confronto pelo nome dos times (comparação flexível, sem acento/maiúsculas)
  const normalizar = (s) => s.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '');
  const evento = dados.find(e =>
    (normalizar(e.home_team).includes(normalizar(nomeCasa)) || normalizar(nomeCasa).includes(normalizar(e.home_team))) &&
    (normalizar(e.away_team).includes(normalizar(nomeFora)) || normalizar(nomeFora).includes(normalizar(e.away_team)))
  );

  if (!evento) {
    return { encontrado: false, motivo: `Não achei "${nomeCasa} x ${nomeFora}" na lista de ${dados.length} jogos retornados pela API.` };
  }
  if (!evento.bookmakers || evento.bookmakers.length === 0) {
    return { encontrado: false, motivo: 'Jogo encontrado, mas nenhuma casa de apostas retornou odds pra ele ainda.' };
  }

  const mediasH2H = mediaDeMercado(evento.bookmakers, 'h2h');
  const mediasTotals = mediaDeMercado(evento.bookmakers, 'totals');

  const odds = {};
  const resultadoFinal = montarResultadoFinal(mediasH2H, evento.home_team, evento.away_team);
  if (resultadoFinal) odds.resultado_final = resultadoFinal;
  const totalGols = montarTotalGols(mediasTotals);
  if (totalGols) odds.total_gols = totalGols;

  return {
    encontrado: true,
    odds,
    casas_usadas: evento.bookmakers.map(b => b.title),
    nomes_api: { casa: evento.home_team, fora: evento.away_team },
  };
}

export default async function handler(req, res) {
  if (applyCors(req, res)) return;
  const apiKey = process.env.ODDS_API_KEY;
  if (!apiKey) return res.status(500).json({ error: { message: 'ODDS_API_KEY não configurada.' } });
  const supabaseUrl = process.env.SUPABASE_URL;
  const supabaseKey = process.env.SUPABASE_KEY;
  if (!supabaseUrl || !supabaseKey) return res.status(500).json({ error: { message: 'SUPABASE_URL / SUPABASE_KEY não configuradas.' } });

  const { mandante, visitante } = req.query;
  if (!mandante || !visitante) {
    return res.status(400).json({ error: { message: 'Informe ?mandante=Nome&visitante=Nome na URL.' } });
  }

  const supabase = getSupabase();

  try {
    // 1) Verifica o cache primeiro
    const { data: existente } = await supabase
      .from('match_odds')
      .select('*')
      .eq('equipe_mandante', mandante)
      .eq('equipe_visitante', visitante)
      .maybeSingle();

    const idadeHoras = existente ? (Date.now() - new Date(existente.updated_at).getTime()) / 3600000 : Infinity;
    if (existente && idadeHoras < HORAS_DE_CACHE) {
      return res.status(200).json({
        confronto: { equipe_mandante: mandante, equipe_visitante: visitante },
        odds: existente.odds,
        origem: 'cache', idade_horas: Math.round(idadeHoras * 10) / 10,
      });
    }

    // 2) Busca de verdade na The Odds API
    const resultado = await buscarDaOddsApi(mandante, visitante, apiKey);
    if (!resultado.encontrado) {
      return res.status(404).json({ error: { message: resultado.motivo } });
    }

    await supabase.from('match_odds').upsert({
      equipe_mandante: mandante, equipe_visitante: visitante,
      odds: resultado.odds, updated_at: new Date().toISOString(),
    }, { onConflict: 'equipe_mandante,equipe_visitante' });

    res.status(200).json({
      confronto: { equipe_mandante: mandante, equipe_visitante: visitante },
      odds: resultado.odds,
      origem: 'the-odds-api',
      casas_usadas_na_media: resultado.casas_usadas,
      aviso: 'Odds são uma MÉDIA de mercado (várias casas), não da Betano especificamente.',
    });
  } catch (erro) {
    res.status(500).json({ error: { message: erro.message } });
  }
}
