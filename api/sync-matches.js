// api/sync-matches.js
// Roda no SERVIDOR do Vercel. Variáveis de ambiente necessárias:
//   FOOTBALL_DATA_KEY          -> chave da football-data.org (mesma usada em api/fixtures.js)
//   SUPABASE_URL               -> mesma dos outros arquivos api/*.js
//   SUPABASE_SERVICE_ROLE_KEY  -> ATENÇÃO: chave DIFERENTE da SUPABASE_KEY usada no
//     resto do projeto. matches/teams só têm policy de LEITURA pública (RLS) —
//     escrita é reservada pro service_role, igual os scripts Python do pipeline.
//     A chave anon (SUPABASE_KEY) não consegue gravar aqui, vai falhar silenciosamente
//     com "new row violates row-level security policy". Pegue o service_role key em
//     supabase.com -> seu projeto -> Settings -> API -> Project API keys -> service_role
//     (é secreta — NUNCA usar essa chave no front-end, só em variável de ambiente do
//     servidor Vercel, e marcá-la como "Sensitive" lá nas configurações).
//
// Mantém public.matches atualizada com a temporada ATUAL de cada liga já
// ingerida pelo pipeline (as 8 com `leagues.external_id` preenchido) — isso
// inclui tanto jogos já disputados (atualiza placar/status) quanto jogos
// futuros ainda não jogados (status='scheduled', sem placar), que é o que
// faz esses jogos aparecerem em LigaDetalhe.jsx/TimeDetalhe.jsx.
//
// A "temporada atual" de cada liga é descoberta perguntando pra própria
// football-data.org (`currentSeason`), não calculada aqui — evita erro por
// causa de ligas com calendário diferente (Brasileirão é ano civil, as
// europeias são ago-mai).
//
// COMO CHAMAR:
//   /api/sync-matches                (todas as 8 ligas)
//   /api/sync-matches?liga=PL        (só uma, pelo código football-data.org)
//
// Pensado pra rodar via Vercel Cron (ver vercel.json) — mas também pode ser
// chamado manualmente.

import { createClient } from '@supabase/supabase-js';

const BASE_URL = 'https://api.football-data.org/v4';
const LIGAS_PIPELINE = ['PL', 'PD', 'SA', 'BL1', 'FL1', 'CL', 'BSA', 'EC'];

const MAPA_STATUS = {
  SCHEDULED: 'scheduled', TIMED: 'scheduled',
  IN_PLAY: 'live', PAUSED: 'live',
  FINISHED: 'finished',
  POSTPONED: 'postponed', SUSPENDED: 'postponed',
  CANCELLED: 'cancelled',
};

function getSupabase() {
  return createClient(process.env.SUPABASE_URL, process.env.SUPABASE_SERVICE_ROLE_KEY);
}

async function chamarFootballData(caminho, apiKey) {
  const resposta = await fetch(`${BASE_URL}${caminho}`, { headers: { 'X-Auth-Token': apiKey } });
  const dados = await resposta.json();
  if (!resposta.ok) throw new Error(`HTTP ${resposta.status}: ${dados.message || ''}`);
  return dados;
}

// Resolve o id interno de um time pelo external_id da football-data.org — cria
// o time (upsert por external_id) na hora se for a primeira vez que aparece
// (ex: promovido de divisão de acesso), pra não perder o jogo por causa disso.
async function resolverOuCriarTime(supabase, mapaExternalIdParaTeamId, timeApi) {
  const externalId = String(timeApi?.id ?? '');
  if (!externalId) return null;
  if (mapaExternalIdParaTeamId[externalId]) return mapaExternalIdParaTeamId[externalId];

  const { data: novoTime, error } = await supabase
    .from('teams')
    .upsert({ external_id: externalId, name: timeApi.name, crest_url: `https://crests.football-data.org/${externalId}.png` }, { onConflict: 'external_id' })
    .select('id')
    .single();
  if (error || !novoTime) return null;
  mapaExternalIdParaTeamId[externalId] = novoTime.id;
  return novoTime.id;
}

async function sincronizarLiga(codigo, apiKey, supabase, mapaExternalIdParaTeamId) {
  const { data: ligaRow } = await supabase.from('leagues').select('id').eq('external_id', codigo).maybeSingle();
  if (!ligaRow) return { codigo, erro: 'Liga não encontrada em leagues (external_id não bate).' };

  const competicao = await chamarFootballData(`/competitions/${codigo}`, apiKey);
  const temporada = competicao.currentSeason?.startDate?.slice(0, 4);
  if (!temporada) return { codigo, erro: 'Sem currentSeason retornado pela football-data.org.' };

  const dados = await chamarFootballData(`/competitions/${codigo}/matches?season=${temporada}`, apiKey);

  let inseridosOuAtualizados = 0;
  const timesCriados = new Set();

  for (const m of dados.matches || []) {
    const homeTeamId = await resolverOuCriarTime(supabase, mapaExternalIdParaTeamId, m.homeTeam);
    const awayTeamId = await resolverOuCriarTime(supabase, mapaExternalIdParaTeamId, m.awayTeam);
    if (!homeTeamId) timesCriados.add(`falhou: ${m.homeTeam?.name}`);
    if (!awayTeamId) timesCriados.add(`falhou: ${m.awayTeam?.name}`);
    if (!homeTeamId || !awayTeamId) continue;

    const { error } = await supabase.from('matches').upsert({
      external_id: `fd_${m.id}`,
      league_id: ligaRow.id,
      season: temporada,
      match_date: m.utcDate,
      home_team_id: homeTeamId,
      away_team_id: awayTeamId,
      home_goals: m.score?.fullTime?.home ?? null,
      away_goals: m.score?.fullTime?.away ?? null,
      status: MAPA_STATUS[m.status] || 'scheduled',
      round: m.matchday ?? null,
      stage: m.stage ?? null,
    }, { onConflict: 'external_id' });
    if (!error) inseridosOuAtualizados++;
  }

  return {
    codigo, temporada, total_jogos: dados.matches?.length ?? 0, sincronizados: inseridosOuAtualizados,
    times_com_problema: timesCriados.size > 0 ? [...timesCriados] : undefined,
  };
}

export default async function handler(req, res) {
  // Chamadas do Vercel Cron trazem esse header automaticamente; se CRON_SECRET
  // estiver configurada, bloqueia chamada externa sem o segredo.
  const cronSecret = process.env.CRON_SECRET;
  if (cronSecret && req.headers.authorization !== `Bearer ${cronSecret}`) {
    return res.status(401).json({ error: { message: 'Não autorizado.' } });
  }

  const apiKey = process.env.FOOTBALL_DATA_KEY;
  if (!apiKey) return res.status(500).json({ error: { message: 'FOOTBALL_DATA_KEY não configurada.' } });
  const supabaseUrl = process.env.SUPABASE_URL, supabaseKey = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!supabaseUrl || !supabaseKey) return res.status(500).json({ error: { message: 'SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY não configuradas (essa é diferente da SUPABASE_KEY normal — ver comentário no topo do arquivo).' } });

  const supabase = getSupabase();
  const { liga } = req.query;
  const ligas = liga ? [liga] : LIGAS_PIPELINE;

  try {
    // Mapa external_id (football-data.org) -> teams.id, carregado uma vez só
    const { data: timesData } = await supabase.from('teams').select('id, external_id').not('external_id', 'is', null);
    const mapaExternalIdParaTeamId = {};
    (timesData || []).forEach(t => { mapaExternalIdParaTeamId[t.external_id] = t.id; });

    const resultados = [];
    for (const codigo of ligas) {
      resultados.push(await sincronizarLiga(codigo, apiKey, supabase, mapaExternalIdParaTeamId));
    }

    res.status(200).json({ sincronizado_em: new Date().toISOString(), resultados });
  } catch (erro) {
    res.status(500).json({ error: { message: erro.message } });
  }
}
