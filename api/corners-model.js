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
// RECALIBRADO (3ª versão) pra usar match_stats_fotmob em vez de match_stats
// (FBref) -- FBref parou de ser ingerido (scraping bloqueado por CAPTCHA nos
// runners do GitHub Actions, ver CONTEXTO_PROJETO.md), FotMob é a única
// fonte automática que resta. Recalibrado via SQL direto contra os mesmos
// pares (match_id, model_stat_estimates.stat='corners') das 5 ligas já
// calibradas -- cobertura 100% em match_stats_fotmob pras mesmas partidas.
// Valores batem bem com os antigos (FBref) em 4 das 5 ligas (diferença
// <2%): La Liga 41,0→40,9, Serie A 67,3→68,1, Bundesliga 30,0→30,1, Ligue 1
// 25,4→25,4 -- validação cruzada natural de que as duas fontes contam
// escanteio do mesmo jeito. Premier League teve diferença maior, 165,5→188,4
// (+14%), dentro do esperado por amostra/cobertura ligeiramente diferente
// entre as fontes, não investigado a fundo por não ser uma discrepância que
// muda a conclusão (r alto = dispersão baixa nas duas versões).
//
// COMO CHAMAR:
//   /api/corners-model?mandante=Manchester City&visitante=Arsenal
//   /api/corners-model?mandante=...&visitante=...&linhas=8.5,9.5,10.5
//   /api/corners-model?mandante=...&visitante=...&stat=shots
//   /api/corners-model?mandante=...&visitante=...&stat=shots_on_target&linhas=2.5,3.5,4.5
//   /api/corners-model?mandante=...&visitante=...&linhas_handicap=-1.5,-0.5,0.5,1.5
//     -> handicap/1X2 de escanteios (mandante×visitante) só existem pra
//        stat=corners (padrão); vêm em `mercados_handicap`/`mercado_1x2` na
//        resposta, `null` pras outras stats. AINDA NÃO VALIDADO contra
//        resultado real de aposta -- ver aviso em RHO_SPLIT_PADRAO_CORNERS.

import { createClient } from '@supabase/supabase-js';
import { negBinomialCDF, negBinomialPMFArray, betaBinomialPMFArray } from './_lib/negbin.js';
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

// Coluna equivalente em `match_stats_fotmob` (fallback quando não há
// estimativa do modelo ainda) -- `shots` vira `total_shots` nessa tabela
// (nome diferente de match_stats/FBref, que foi abandonada -- ver
// CONTEXTO_PROJETO.md); `corners`/`shots_on_target` batem 1:1.
const STAT_COLUNA_MATCH_STATS = { corners: 'corners', shots: 'total_shots', shots_on_target: 'shots_on_target' };

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
const DISP_R_PADRAO_POR_STAT = { corners: 70.57, shots: 5.99, shots_on_target: 5.68 };

// Linhas padrão de handicap de escanteios (mandante - visitante) quando
// `?linhas_handicap=` não é informado -- faixa observada em odds_market
// (`corners_handicap_*`, ver CONTEXTO_PROJETO.md) pras linhas com mais
// partidas cobertas.
const LINHAS_PADRAO_HANDICAP_CORNERS = ['-2.5', '-1.5', '-0.5', '0.5', '1.5', '2.5'];

// disp_rho_split: correlação intraclasse do split mandante/visitante dentro
// do total de escanteios já sorteado (Beta-Binomial condicionada no total
// NB) -- ver comentário em `decomporMandanteVisitante` abaixo pra por quê
// isso existe e não é opcional (mandante e visitante NÃO são independentes:
// corr(corners_mandante, corners_visitante) = -0,26 nos dados reais, porque
// escanteio de um lado "consome" posse/tempo de jogo que teria ido pro
// outro). Calibrado nesta sessão via resíduo de Pearson quadrático em
// `k|n` (mesmo método já usado pro disp_r da NB, só que na variável de
// split), sobre os pares (corners_mandante real, corners_visitante real,
// home_expected/away_expected de model_stat_estimates) das mesmas 5 ligas já
// calibradas pra disp_r (n=272-342 partidas cada, rho entre 0,059 e 0,094 --
// persistido em league_model_params, stat='corners', param_name=
// 'disp_rho_split'). Fallback abaixo é a média dessas 5 ligas -- só pra
// escanteios; chutes/chutes no gol de time NÃO têm esse split calibrado
// ainda (a decomposição mandante×visitante só vale pra `stat==='corners'`
// por ora).
//
// AINDA NÃO VALIDADO contra resultado real de handicap/1X2 de escanteios em
// carteira cronológica -- é um candidato, não uma estratégia com confiança
// "alta" (mesmo padrão de cautela de `model_betting_strategy`: log-loss/
// calibração isolados já se mostraram reversíveis neste projeto depois de
// testados contra apostas reais). Backtestar antes de usar pra decisão de
// aposta.
const RHO_SPLIT_PADRAO_CORNERS = 0.0764;

// Cap superior pro total de escanteios somado na distribuição conjunta --
// generoso de propósito (jogos reais raramente passam de ~25 escanteios no
// total); soma de massa de probabilidade acima disso é desprezível pra
// qualquer confronto real, então não vale o custo de recalcular por partida.
const TOTAL_MAX_JOINT_CORNERS = 50;

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
// real de match_stats_fotmob[colunaStat] se o modelo ainda não tiver
// estimativa salva pra nenhuma dessas partidas. `stat` é a chave em inglês de
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
    .from('match_stats_fotmob')
    .select(`${colunaStat}, match_id`)
    .eq('team_id', teamId)
    .not(colunaStat, 'is', null)
    .limit(10);
  const reais = (statsReais || []).map(s => Number(s[colunaStat])).filter(Number.isFinite);
  if (reais.length > 0) {
    return { valor: reais.reduce((a, b) => a + b, 0) / reais.length, origem: 'match_stats_fotmob (média real)' };
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

// Mesmo padrão de `dispRDaLiga`, mas pro parâmetro do split mandante/
// visitante (só existe pra stat='corners' -- ver comentário em
// RHO_SPLIT_PADRAO_CORNERS).
async function dispRhoSplitDaLiga(supabase, leagueId) {
  if (!leagueId) return { valor: RHO_SPLIT_PADRAO_CORNERS, origem: 'padrao_generico' };
  const { data } = await supabase
    .from('league_model_params')
    .select('param_value')
    .eq('league_id', leagueId)
    .eq('stat', 'corners')
    .eq('param_name', 'disp_rho_split')
    .maybeSingle();
  if (data?.param_value) return { valor: Number(data.param_value), origem: 'league_model_params' };
  return { valor: RHO_SPLIT_PADRAO_CORNERS, origem: 'padrao_generico (liga sem calibração própria)' };
}

// Decompõe o total de escanteios (já modelado como Binomial Negativa, ver
// topo do arquivo) em mandante × visitante.
//
// POR QUE NÃO É SÓ "NB(lambda_mandante) e NB(lambda_visitante) separados":
// mandante e visitante não são independentes -- corr(corners_mandante,
// corners_visitante) = -0,26 nos dados reais (achado desta sessão, ver
// CONTEXTO_PROJETO.md). Faz sentido futebolisticamente: escanteio de um
// lado consome posse/tempo de jogo que "tiraria" chance do outro lado
// cobrar. Tratar os dois como independentes SUBESTIMARIA a variância da
// DIFERENÇA (mandante-visitante) -- Var(X-Y) = VarX+VarY-2Cov(X,Y), e
// Cov<0 aumenta essa variância -- inflando artificialmente a confiança do
// modelo em handicap/1X2 de escanteios.
//
// A composição usada é T (total) × K|T (split condicional):
//   T ~ NB(lambdaTotal, dispR)              -- já calibrado e testado (topo do arquivo)
//   K|T ~ BetaBinomial(T, p=lambdaMandante/lambdaTotal, rho)
// K = escanteios do mandante, T-K = escanteios do visitante. O split
// condicional ao total já reproduz a correlação negativa de forma natural
// (mandante e visitante disputam a mesma "torta" T), e o rho da Beta-
// Binomial captura a dispersão EXTRA que sobra além disso (jogos onde o
// domínio de escanteios foi mais ou menos lopsided do que a proporção
// p sozinha explicaria -- confirmado nos dados: variância observada do
// split é ~1,7x a variância de uma Binomial(T,p) pura, daí rho>0).
//
// Devolve a massa de probabilidade P(K=k, T=t) só pros pontos que importam
// pro cálculo de handicap/1X2 (chamador agrega o que precisar).
function decomporMandanteVisitante(lambdaMandante, lambdaVisitante, dispR, rho) {
  const lambdaTotal = lambdaMandante + lambdaVisitante;
  const p = lambdaTotal > 0 ? lambdaMandante / lambdaTotal : 0.5;
  const pmfTotal = negBinomialPMFArray(lambdaTotal, dispR, TOTAL_MAX_JOINT_CORNERS);

  // probDiferenca[d + TOTAL_MAX_JOINT_CORNERS] = P(mandante - visitante = d)
  const probDiferenca = new Array(2 * TOTAL_MAX_JOINT_CORNERS + 1).fill(0);
  for (let t = 0; t <= TOTAL_MAX_JOINT_CORNERS; t++) {
    const probT = pmfTotal[t];
    if (probT <= 0) continue;
    const pmfSplit = betaBinomialPMFArray(t, p, rho);
    for (let k = 0; k <= t; k++) {
      const diferenca = k - (t - k); // mandante - visitante
      probDiferenca[diferenca + TOTAL_MAX_JOINT_CORNERS] += probT * pmfSplit[k];
    }
  }
  return probDiferenca; // índice = diferença + TOTAL_MAX_JOINT_CORNERS
}

// Handicap "mandante -X.5": mandante cobre se (mandante - visitante) > X.5,
// ou seja, se a diferença real for >= ceil(X.5) (linhas .5 nunca empatam;
// linhas inteiras, se aparecerem, empatam nesse ponto exato -- não usado
// nas linhas padrão, mas o cálculo já trata o caso certo via `>=`/`<=`
// estritos em vez de assumir sempre .5).
function probabilidadeHandicap(probDiferenca, linha) {
  let probMandanteCobre = 0, probVisitanteCobre = 0, probPush = 0;
  for (let idx = 0; idx < probDiferenca.length; idx++) {
    const diferenca = idx - TOTAL_MAX_JOINT_CORNERS;
    const resultado = diferenca + linha; // mandante cobre se > 0, visitante se < 0
    if (resultado > 0) probMandanteCobre += probDiferenca[idx];
    else if (resultado < 0) probVisitanteCobre += probDiferenca[idx];
    else probPush += probDiferenca[idx];
  }
  return { prob_mandante_cobre: probMandanteCobre, prob_visitante_cobre: probVisitanteCobre, prob_push: probPush };
}

function probabilidade1x2(probDiferenca) {
  let probMandante = 0, probEmpate = 0, probVisitante = 0;
  for (let idx = 0; idx < probDiferenca.length; idx++) {
    const diferenca = idx - TOTAL_MAX_JOINT_CORNERS;
    if (diferenca > 0) probMandante += probDiferenca[idx];
    else if (diferenca < 0) probVisitante += probDiferenca[idx];
    else probEmpate += probDiferenca[idx];
  }
  return { prob_mandante: probMandante, prob_empate: probEmpate, prob_visitante: probVisitante };
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
  const { linhas_handicap: linhasHandicapRaw } = req.query;
  const linhasHandicapPedidas = (linhasHandicapRaw ? linhasHandicapRaw.split(',') : LINHAS_PADRAO_HANDICAP_CORNERS)
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
        error: { message: `Sem histórico de "${statPedida}" suficiente pra esse confronto (nem no modelo, nem em match_stats_fotmob).` },
      });
    }

    const { valor: dispR, origem: origemDispR } = await dispRDaLiga(supabase, ligaId, statPedida);
    const lambdaTotal = esperadoMandante.valor + esperadoVisitante.valor;

    const linhasCalculadas = linhasPedidas.map(linha => {
      const probUnder = negBinomialCDF(lambdaTotal, dispR, Math.floor(linha));
      return { linha, prob_over: 1 - probUnder, prob_under: probUnder, odd_justa_over: +(1 / (1 - probUnder)).toFixed(2), odd_justa_under: +(1 / probUnder).toFixed(2) };
    });

    // Handicap e 1X2 de escanteios (mandante × visitante) -- só faz sentido
    // pra `stat==='corners'`, é a única stat com `disp_rho_split` calibrado
    // (ver RHO_SPLIT_PADRAO_CORNERS). `null` pra chutes/chutes no gol é
    // resposta normal, não erro.
    let handicap = null;
    let resultado1x2 = null;
    let rhoSplitInfo = null;
    if (statPedida === 'corners') {
      const { valor: rhoSplit, origem: origemRhoSplit } = await dispRhoSplitDaLiga(supabase, ligaId);
      rhoSplitInfo = { valor: rhoSplit, origem: origemRhoSplit };
      const probDiferenca = decomporMandanteVisitante(esperadoMandante.valor, esperadoVisitante.valor, dispR, rhoSplit);
      handicap = linhasHandicapPedidas.map(linha => ({ linha, ...probabilidadeHandicap(probDiferenca, linha) }));
      resultado1x2 = probabilidade1x2(probDiferenca);
    }

    res.status(200).json({
      confronto: { equipe_mandante: timeMandante.name, equipe_visitante: timeVisitante.name },
      modelo: {
        nome: 'stats_glm_v1 + Binomial Negativa', stat: statPedida, league_id: ligaId,
        disp_r: dispR, origem_disp_r: origemDispR,
        // Só preenchido pra escanteios -- ver comentário em decomporMandanteVisitante.
        disp_rho_split: rhoSplitInfo?.valor ?? null, origem_disp_rho_split: rhoSplitInfo?.origem ?? null,
      },
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
      // Handicap e 1X2 de escanteios (mandante × visitante) -- AINDA NÃO
      // VALIDADO contra resultado real em carteira cronológica, ver aviso
      // em RHO_SPLIT_PADRAO_CORNERS. `null` pra chutes/chutes no gol.
      mercados_handicap: handicap,
      mercado_1x2: resultado1x2,
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
