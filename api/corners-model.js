// api/corners-model.js
// Roda no SERVIDOR do Vercel. Variáveis de ambiente necessárias:
//   SUPABASE_URL / SUPABASE_KEY  -> mesmas do team-stats.js
//
// Gera a previsão de escanteios do "modelo de produção" (GLM Poisson treinado
// em modelo_stats_esperadas.py, cujos resultados por partida ficam salvos em
// model_stat_estimates) só que lida com Binomial Negativa em vez de Poisson —
// escanteios são superdispersos (ver CONTEXTO_PROJETO.md), então tratamos o
// TOTAL de escanteios do jogo (mandante + visitante) como uma única variável
// NB, com o parâmetro de forma "r" calibrado por liga (método dos momentos
// sobre dados reais em match_stats) e salvo em league_model_params.
//
// COMO CHAMAR:
//   /api/corners-model?mandante=Manchester City&visitante=Arsenal
//   /api/corners-model?mandante=...&visitante=...&linhas=8.5,9.5,10.5

import { createClient } from '@supabase/supabase-js';
import { negBinomialCDF } from './_lib/negbin.js';

// Usado só quando a liga do confronto não tem disp_r calibrado ainda (ex:
// Brasileirão, Champions, Eurocopa — sem match_stats de escanteios no banco).
// É a média dos 5 valores calibrados nas ligas europeias que têm dado.
const DISP_R_PADRAO = 63.3;

function getSupabase() {
  return createClient(process.env.SUPABASE_URL, process.env.SUPABASE_KEY);
}

async function encontrarTime(supabase, nome) {
  const { data, error } = await supabase
    .from('teams')
    .select('id, name')
    .ilike('name', `%${nome}%`)
    .limit(5);
  if (error) throw error;
  if (!data || data.length === 0) return null;
  const exato = data.find(t => t.name.toLowerCase() === nome.toLowerCase());
  return exato || data[0];
}

async function ligaMaisRecente(supabase, teamId) {
  const { data } = await supabase
    .from('matches')
    .select('league_id, match_date')
    .or(`home_team_id.eq.${teamId},away_team_id.eq.${teamId}`)
    .order('match_date', { ascending: false })
    .limit(1);
  return data?.[0]?.league_id ?? null;
}

// Média de escanteios esperados (modelo GLM) do time nas últimas partidas em
// que jogou em casa (mandante=true) ou fora (mandante=false). Cai pra média
// real de match_stats.corners se o modelo ainda não tiver estimativa salva
// pra nenhuma dessas partidas.
async function escanteiosEsperados(supabase, teamId, mandante) {
  const campoTime = mandante ? 'home_team_id' : 'away_team_id';
  const { data: partidas } = await supabase
    .from('matches')
    .select('id, match_date')
    .eq(campoTime, teamId)
    .order('match_date', { ascending: false })
    .limit(10);

  const idsPartidas = (partidas || []).map(p => p.id);
  if (idsPartidas.length > 0) {
    const { data: estimativas } = await supabase
      .from('model_stat_estimates')
      .select('home_expected, away_expected, match_id')
      .eq('stat', 'corners')
      .in('match_id', idsPartidas);

    const valores = (estimativas || [])
      .map(e => (mandante ? e.home_expected : e.away_expected))
      .filter(v => v !== null && v !== undefined);
    if (valores.length > 0) {
      return { valor: valores.reduce((a, b) => a + Number(b), 0) / valores.length, origem: 'model_stat_estimates' };
    }
  }

  // Fallback: média real de escanteios do time (jogando em casa ou fora, o que houver)
  const { data: statsReais } = await supabase
    .from('match_stats')
    .select('corners, match_id')
    .eq('team_id', teamId)
    .not('corners', 'is', null)
    .limit(10);
  const reais = (statsReais || []).map(s => Number(s.corners)).filter(Number.isFinite);
  if (reais.length > 0) {
    return { valor: reais.reduce((a, b) => a + b, 0) / reais.length, origem: 'match_stats (média real)' };
  }

  return { valor: null, origem: 'sem_dado' };
}

async function dispRDaLiga(supabase, leagueId) {
  if (!leagueId) return { valor: DISP_R_PADRAO, origem: 'padrao_generico' };
  const { data } = await supabase
    .from('league_model_params')
    .select('param_value')
    .eq('league_id', leagueId)
    .eq('stat', 'corners')
    .eq('param_name', 'disp_r')
    .maybeSingle();
  if (data?.param_value) return { valor: Number(data.param_value), origem: 'league_model_params' };
  return { valor: DISP_R_PADRAO, origem: 'padrao_generico (liga sem calibração própria)' };
}

export default async function handler(req, res) {
  const supabaseUrl = process.env.SUPABASE_URL;
  const supabaseKey = process.env.SUPABASE_KEY;
  if (!supabaseUrl || !supabaseKey) return res.status(500).json({ error: { message: 'SUPABASE_URL / SUPABASE_KEY não configuradas.' } });

  const { mandante, visitante, linhas } = req.query;
  if (!mandante || !visitante) {
    return res.status(400).json({ error: { message: 'Informe ?mandante=Nome&visitante=Nome na URL.' } });
  }
  const linhasPedidas = (linhas ? linhas.split(',') : ['8.5', '9.5', '10.5', '11.5'])
    .map(l => parseFloat(l.trim()))
    .filter(Number.isFinite);

  const supabase = getSupabase();

  try {
    const [timeMandante, timeVisitante] = await Promise.all([
      encontrarTime(supabase, mandante),
      encontrarTime(supabase, visitante),
    ]);
    if (!timeMandante) return res.status(404).json({ error: { message: `Time "${mandante}" não encontrado na tabela teams (times do pipeline Python).` } });
    if (!timeVisitante) return res.status(404).json({ error: { message: `Time "${visitante}" não encontrado na tabela teams (times do pipeline Python).` } });

    const [esperadoMandante, esperadoVisitante, ligaId] = await Promise.all([
      escanteiosEsperados(supabase, timeMandante.id, true),
      escanteiosEsperados(supabase, timeVisitante.id, false),
      ligaMaisRecente(supabase, timeMandante.id),
    ]);

    if (esperadoMandante.valor === null || esperadoVisitante.valor === null) {
      return res.status(404).json({
        error: { message: 'Sem histórico de escanteios suficiente pra esse confronto (nem no modelo, nem em match_stats).' },
      });
    }

    const { valor: dispR, origem: origemDispR } = await dispRDaLiga(supabase, ligaId);
    const lambdaCorners = esperadoMandante.valor + esperadoVisitante.valor;

    const linhasCalculadas = linhasPedidas.map(linha => {
      const probUnder = negBinomialCDF(lambdaCorners, dispR, Math.floor(linha));
      return { linha, prob_over: 1 - probUnder, prob_under: probUnder, odd_justa_over: +(1 / (1 - probUnder)).toFixed(2), odd_justa_under: +(1 / probUnder).toFixed(2) };
    });

    res.status(200).json({
      confronto: { equipe_mandante: timeMandante.name, equipe_visitante: timeVisitante.name },
      modelo: { nome: 'stats_glm_v1 + Binomial Negativa', league_id: ligaId, disp_r: dispR, origem_disp_r: origemDispR },
      escanteios_esperados: {
        mandante: esperadoMandante.valor, mandante_origem: esperadoMandante.origem,
        visitante: esperadoVisitante.valor, visitante_origem: esperadoVisitante.origem,
        total: lambdaCorners,
      },
      mercados: linhasCalculadas,
    });
  } catch (erro) {
    res.status(500).json({ error: { message: erro.message } });
  }
}
