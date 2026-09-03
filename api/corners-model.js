// api/corners-model.js
// Roda no SERVIDOR do Vercel. Variáveis de ambiente necessárias:
//   SUPABASE_URL / SUPABASE_KEY  -> mesmas do team-stats.js
//
// Gera a previsão de estatística de TIME do "modelo de produção" (GLM Poisson
// treinado em modelo_stats_esperadas.py, cujos resultados por partida ficam
// salvos em model_stat_estimates) só que lida com Binomial Negativa em vez de
// Poisson — a estatística é superdispersa (ver CONTEXTO_PROJETO.md), então
// tratamos o TOTAL do jogo (mandante + visitante) como uma única variável NB,
// com o parâmetro de forma "r" calibrado por liga e salvo em
// league_model_params.
//
// Nasceu só pra escanteios (nome do arquivo é histórico) e foi generalizado
// nesta sessão pra também cobrir chutes e chutes no gol de TIME
// (`?stat=shots`/`?stat=shots_on_target`) — mesmo padrão de λ por time via
// histórico recente + disp_r por liga, só a fonte de dado muda. Chutes/
// chutes no gol de time JÁ tinham a camada 1 pronta (`model_stat_estimates`
// com `stat='shots'`/`'shots_on_target'`, `disp_r` calibrado em
// `league_model_params` — achado real desta sessão: essas duas peças já
// existiam, só não estavam expostas em nenhum endpoint) -- diferente de
// escanteios/chutes/chutes no gol de TIME, chutes/chutes no gol de JOGADOR
// é OUTRO sistema (`player_match_estimates` etc.), não relacionado.
//
// `model_stat_estimates.stat` usa inglês (`corners`/`shots`/`shots_on_
// target`); `league_model_params.stat` usa português (`corners`/`chutes`/
// `chutes_no_alvo`) -- convenção divergente já existente nas duas tabelas
// (confirmado via SQL, não presumido), daí o mapa `STAT_LEAGUE_PARAMS_
// LABEL` abaixo. Nunca comparar `stat` cru contra `league_model_params.stat`
// sem passar por ele -- sem isso o lookup de disp_r falha silenciosamente e
// cai no fallback genérico pra QUALQUER liga, mesmo as calibradas.
//
// IMPORTANTE sobre como "r" é calibrado (2ª versão, corrigida): NÃO é
// mean²/(variância-mean) da distribuição agregada da liga inteira — isso
// mistura a variação ENTRE jogos (times diferentes = λ esperado diferente,
// já capturado pelo próprio modelo) com a variação DENTRO de um jogo (o que
// "r" deveria medir de verdade). Isso super-estimava a dispersão real e
// inflava demais as probabilidades de "over". A calibração certa usa resíduo
// de Pearson condicionado no λ de CADA partida: alpha = Σ((real-λ)²-λ) / Σλ²,
// r = 1/alpha (estimador padrão de dispersão NB2). Validado pra escanteios:
// com o r corrigido, NB bate Poisson em 18 de 20 combinações liga×linha
// testadas (vs. o r antigo, que só vencia por acidente em algumas). Mesmo
// método reaproveitado pros scripts de calibração de chutes/chutes no alvo
// (`arquivos_do_claude/calibrar_disp_r_chutes*.py`).
//
// COMO CHAMAR:
//   /api/corners-model?mandante=Manchester City&visitante=Arsenal
//   /api/corners-model?mandante=...&visitante=...&linhas=8.5,9.5,10.5
//   /api/corners-model?mandante=...&visitante=...&stat=shots
//   /api/corners-model?mandante=...&visitante=...&stat=shots_on_target&linhas=2.5,3.5,4.5

import { createClient } from '@supabase/supabase-js';
import { negBinomialCDF } from './_lib/negbin.js';
import { applyCors } from './_lib/cors.js';

const STATS_SUPORTADAS = ['corners', 'shots', 'shots_on_target'];

// Linhas padrão por stat, quando `?linhas=` não é informado -- medianas
// reais do TOTAL por partida (mandante+visitante), via SQL nesta sessão:
// chutes ~25 (linhas 20.5-26.5), chutes no gol ~9 (linhas 7.5-10.5).
// Escanteios mantém as linhas antigas (não medido de novo, já eram as
// linhas em uso).
const LINHAS_PADRAO_POR_STAT = {
  corners: ['8.5', '9.5', '10.5', '11.5'],
  shots: ['20.5', '22.5', '24.5', '26.5'],
  shots_on_target: ['7.5', '8.5', '9.5', '10.5'],
};

// Tradução `model_stat_estimates.stat` (inglês) -> `league_model_params.stat`
// (português) -- as duas tabelas usam vocabulário diferente (achado real
// desta sessão, não decisão de design; ver comentário no topo do arquivo).
const STAT_LEAGUE_PARAMS_LABEL = { corners: 'corners', shots: 'chutes', shots_on_target: 'chutes_no_alvo' };

// Coluna equivalente em `match_stats` (fallback quando não há estimativa do
// modelo ainda) -- mesmos nomes nas duas tabelas, `STATS_SUPORTADAS` já bate
// 1:1 com as colunas reais (confirmado via information_schema nesta sessão).
const STAT_COLUNA_MATCH_STATS = { corners: 'corners', shots: 'shots', shots_on_target: 'shots_on_target' };

// Usado só quando a liga do confronto não tem disp_r calibrado ainda (ex:
// Brasileirão, Champions, Eurocopa — sem model_stat_estimates da stat pra
// calibrar direito). Por stat: escanteios é a média dos 5 valores calibrados
// (método corrigido, já existia); chutes/chutes no gol são a média dos 12
// valores calibrados de cada um (`arquivos_do_claude/calibrar_disp_r_
// chutes*.py`) -- não faz sentido reusar o fallback de escanteios pra
// chutes, são distribuições/ligas diferentes.
// Médias reais calculadas via SQL nesta sessão (`avg(param_value)` sobre as
// 12 ligas calibradas de cada stat, mesmo método já usado pro valor de
// escanteios) -- não chutado.
const DISP_R_PADRAO_POR_STAT = { corners: 65.85, shots: 5.99, shots_on_target: 5.68 };

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

// Resolve por ID quando o chamador já sabe qual time é (ex: AnaliseEstatisticaJogo.jsx,
// que tem o match real) — evita a ambiguidade de nomes duplicados entre clubes
// diferentes (ex: dois times chamados "Liverpool FC", um inglês e um uruguaio
// da Libertadores, mesmo nome literal). Nome continua sendo o único jeito
// disponível pra quem usa a calculadora manual (AnaliseEvento.jsx).
async function resolverTime(supabase, nome, id) {
  if (id) {
    const { data, error } = await supabase.from('teams').select('id, name').eq('id', id).maybeSingle();
    if (error) throw error;
    if (data) return data;
  }
  return encontrarTime(supabase, nome);
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

// Média da estatística esperada (modelo GLM) do time nas últimas partidas em
// que jogou em casa (mandante=true) ou fora (mandante=false). Cai pra média
// real de match_stats[colunaStat] se o modelo ainda não tiver estimativa
// salva pra nenhuma dessas partidas. `stat` é a chave em inglês de
// `model_stat_estimates`/`STATS_SUPORTADAS` (não a de `league_model_params`,
// ver `STAT_LEAGUE_PARAMS_LABEL`).
async function statEsperado(supabase, teamId, mandante, stat) {
  const campoTime = mandante ? 'home_team_id' : 'away_team_id';
  const colunaStat = STAT_COLUNA_MATCH_STATS[stat];
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
      .eq('stat', stat)
      .in('match_id', idsPartidas);

    const valores = (estimativas || [])
      .map(e => (mandante ? e.home_expected : e.away_expected))
      .filter(v => v !== null && v !== undefined);
    if (valores.length > 0) {
      return { valor: valores.reduce((a, b) => a + Number(b), 0) / valores.length, origem: 'model_stat_estimates' };
    }
  }

  // Fallback: média real da estatística do time (jogando em casa ou fora, o que houver)
  const { data: statsReais } = await supabase
    .from('match_stats')
    .select(`${colunaStat}, match_id`)
    .eq('team_id', teamId)
    .not(colunaStat, 'is', null)
    .limit(10);
  const reais = (statsReais || []).map(s => Number(s[colunaStat])).filter(Number.isFinite);
  if (reais.length > 0) {
    return { valor: reais.reduce((a, b) => a + b, 0) / reais.length, origem: 'match_stats (média real)' };
  }

  return { valor: null, origem: 'sem_dado' };
}

// Parâmetros do modelo misto pra este confronto, quando existirem.
//
// O modelo misto (scripts/treinar_modelo_hibrido.py) estima λ por ML e grava
// em `model_match_estimates.params`, indexado por match_id. A calculadora
// manual (AnaliseEvento.jsx) trabalha com NOMES de time, não com partida, então
// a ponte é achar a partida entre os dois times: a próxima agendada, ou a mais
// recente disputada.
//
// Devolve null em silêncio quando não há partida ou não há parâmetro — a
// calculadora tem fallback (a fórmula multiplicativa de sempre), e um confronto
// hipotético que nunca aconteceu simplesmente não tem λ estimado.
async function parametrosModeloMisto(supabase, homeId, awayId) {
  const { data: partidas } = await supabase
    .from('matches')
    .select('id, match_date, status')
    .eq('home_team_id', homeId)
    .eq('away_team_id', awayId)
    .order('match_date', { ascending: false })
    .limit(20);

  if (!partidas || partidas.length === 0) return null;

  // Agendadas primeiro (é o caso de uso real: prever o que ainda vai acontecer);
  // entre as disputadas, a mais recente.
  const agendadas = partidas.filter(p => p.status === 'scheduled');
  const ordenadas = [...agendadas.reverse(), ...partidas.filter(p => p.status !== 'scheduled')];

  const { data: estimativas } = await supabase
    .from('model_match_estimates')
    .select('match_id, model_name, params')
    .in('match_id', ordenadas.map(p => p.id))
    .not('params', 'is', null);

  if (!estimativas || estimativas.length === 0) return null;

  // Respeita a ordem de preferência de partida definida acima.
  for (const partida of ordenadas) {
    const linha = estimativas.find(e => e.match_id === partida.id);
    if (linha?.params?.lambda_home) {
      return {
        match_id: partida.id,
        match_date: partida.match_date,
        status: partida.status,
        model_name: linha.model_name,
        params: linha.params,
      };
    }
  }
  return null;
}

async function dispRDaLiga(supabase, leagueId, stat) {
  const dispRPadrao = DISP_R_PADRAO_POR_STAT[stat];
  const statLeagueParams = STAT_LEAGUE_PARAMS_LABEL[stat];
  if (!leagueId) return { valor: dispRPadrao, origem: 'padrao_generico' };
  const { data } = await supabase
    .from('league_model_params')
    .select('param_value')
    .eq('league_id', leagueId)
    .eq('stat', statLeagueParams)
    .eq('param_name', 'disp_r')
    .maybeSingle();
  if (data?.param_value) return { valor: Number(data.param_value), origem: 'league_model_params' };
  return { valor: dispRPadrao, origem: 'padrao_generico (liga sem calibração própria)' };
}

export default async function handler(req, res) {
  if (applyCors(req, res)) return;
  const supabaseUrl = process.env.SUPABASE_URL;
  const supabaseKey = process.env.SUPABASE_KEY;
  if (!supabaseUrl || !supabaseKey) return res.status(500).json({ error: { message: 'SUPABASE_URL / SUPABASE_KEY não configuradas.' } });

  const { mandante, visitante, linhas, mandante_id, visitante_id, stat } = req.query;
  if (!mandante || !visitante) {
    return res.status(400).json({ error: { message: 'Informe ?mandante=Nome&visitante=Nome na URL.' } });
  }
  const statPedida = stat || 'corners';
  if (!STATS_SUPORTADAS.includes(statPedida)) {
    return res.status(400).json({ error: { message: `?stat inválido: "${statPedida}" -- use um de ${STATS_SUPORTADAS.join(', ')}.` } });
  }
  const linhasPedidas = (linhas ? linhas.split(',') : LINHAS_PADRAO_POR_STAT[statPedida])
    .map(l => parseFloat(l.trim()))
    .filter(Number.isFinite);

  const supabase = getSupabase();

  try {
    const [timeMandante, timeVisitante] = await Promise.all([
      resolverTime(supabase, mandante, mandante_id),
      resolverTime(supabase, visitante, visitante_id),
    ]);
    if (!timeMandante) return res.status(404).json({ error: { message: `Time "${mandante}" não encontrado na tabela teams (times do pipeline Python).` } });
    if (!timeVisitante) return res.status(404).json({ error: { message: `Time "${visitante}" não encontrado na tabela teams (times do pipeline Python).` } });

    const [esperadoMandante, esperadoVisitante, ligaId, modeloMisto] = await Promise.all([
      statEsperado(supabase, timeMandante.id, true, statPedida),
      statEsperado(supabase, timeVisitante.id, false, statPedida),
      ligaMaisRecente(supabase, timeMandante.id),
      parametrosModeloMisto(supabase, timeMandante.id, timeVisitante.id),
    ]);

    if (esperadoMandante.valor === null || esperadoVisitante.valor === null) {
      return res.status(404).json({
        error: { message: `Sem histórico de "${statPedida}" suficiente pra esse confronto (nem no modelo, nem em match_stats).` },
      });
    }

    const { valor: dispR, origem: origemDispR } = await dispRDaLiga(supabase, ligaId, statPedida);
    const lambdaTotal = esperadoMandante.valor + esperadoVisitante.valor;

    const linhasCalculadas = linhasPedidas.map(linha => {
      const probUnder = negBinomialCDF(lambdaTotal, dispR, Math.floor(linha));
      return { linha, prob_over: 1 - probUnder, prob_under: probUnder, odd_justa_over: +(1 / (1 - probUnder)).toFixed(2), odd_justa_under: +(1 / probUnder).toFixed(2) };
    });

    res.status(200).json({
      confronto: { equipe_mandante: timeMandante.name, equipe_visitante: timeVisitante.name },
      modelo: { nome: 'stats_glm_v1 + Binomial Negativa', stat: statPedida, league_id: ligaId, disp_r: dispR, origem_disp_r: origemDispR },
      // `stat_esperado`: nome genérico novo, serve pra qualquer uma das
      // STATS_SUPORTADAS. `escanteios_esperados`: MESMO objeto, mantido por
      // compatibilidade -- `src/pages/AnaliseEvento.jsx`/
      // `AnaliseEstatisticaJogo.jsx` já dependem desse nome (só fazia
      // sentido quando só existia escanteios); manter os dois evita quebrar
      // esses call-sites existentes sem precisar tocar neles nesta extensão.
      stat_esperado: {
        mandante: esperadoMandante.valor, mandante_origem: esperadoMandante.origem,
        visitante: esperadoVisitante.valor, visitante_origem: esperadoVisitante.origem,
        total: lambdaTotal,
      },
      escanteios_esperados: {
        mandante: esperadoMandante.valor, mandante_origem: esperadoMandante.origem,
        visitante: esperadoVisitante.valor, visitante_origem: esperadoVisitante.origem,
        total: lambdaTotal,
      },
      mercados: linhasCalculadas,
      // Presente só quando o modelo misto tem estimativa pra uma partida entre
      // esses dois times. `null` é resposta normal (confronto hipotético, ou
      // partida fora do escopo de treino), e a calculadora trata como tal.
      // Só faz sentido pra escanteios (o modelo misto/Dixon-Coles é de
      // gols/escanteios) -- vem null pra chutes/chutes no gol sem custo,
      // não filtrado explicitamente porque `parametrosModeloMisto` já não
      // teria lambda_home relevante pra essas stats de qualquer forma.
      modelo_misto: modeloMisto,
    });
  } catch (erro) {
    res.status(500).json({ error: { message: erro.message } });
  }
}
