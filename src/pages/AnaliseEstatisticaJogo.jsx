// src/pages/AnaliseEstatisticaJogo.jsx — rota /estatisticas/:matchId
// Painel de análise estatística voltado pra apostas esportivas, linkado a
// partir de AnaliseHistorica.jsx (/historico/:matchId). Diferente daquela
// página (forma recente + confronto direto) e de AnaliseEvento.jsx (calculadora
// manual com modelo Dixon-Coles rodado à mão), aqui TUDO é importado
// automaticamente do pipeline por padrão:
//   1. Tendências de mercado por time (Over 2.5%, Ambas Marcam%, médias de
//      escanteios/cartões nos últimos N jogos) — o "cheat sheet" de apostador.
//   2. Comparação modelo (model_predictions) vs mercado (odds_market devigada)
//      pra essa partida específica, com o edge calculado.
//   3. Histórico de precisão do modelo (api/model-stats.js) filtrado pra
//      liga/mercado dessa partida, como contexto de confiabilidade.
//   4. Estimativa própria do modelo (1X2/Over-Under/Ambas Marcam/Escanteios) —
//      MESMO motor matemático de AnaliseEvento.jsx (poisson.js/lambdaFormulas.js,
//      fórmula "multiplicativo" padrão + correção Dixon-Coles), auto-alimentado
//      pela média real de xG/xGA (Understat, quando existe) ou por gols reais
//      como fallback (ligas sem xG). Escanteios reaproveita api/corners-model.js
//      (mesmo NB calibrado por liga já usado em AnaliseEvento). Cobre partidas
//      SEM previsão salva em model_predictions (só existem pra season 2025 hoje).
//      OPCIONAL: dá pra sobrescrever o xG/xGA auto-importado colando um
//      screenshot de "Estatística média" (Betano/SofaScore) — mesmo OCR de
//      AnaliseEvento.jsx (api/ocr.js + src/utils/ocr.js) — útil pras ligas sem
//      xG real no banco (Brasileirão e quase tudo fora das 5 europeias).
import React, { useState, useEffect, useMemo, useRef } from 'react';
import { useParams, Link } from 'react-router-dom';
import { ArrowLeft, AlertTriangle, Shield, Loader2, TrendingUp, Target, History, Calculator, Camera, ScanLine } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';
import { poisson, dixonColesTau, DIXON_COLES_RHO } from '../utils/poisson';
import { getLambdaFormula } from '../utils/lambdaFormulas';
import { extractJsonFromImage } from '../utils/ocr';
import { indexarCalibracao, calibrarProbabilidade } from '../utils/calibration';
import { negBinomialPMF } from '../utils/distributions';

// Mesmo prompt de AnaliseEvento.jsx (OCR_STATS_PROMPT) — extrai xG/xGA/chutes/
// escanteios dos DOIS times de uma vez a partir da tabela "Estatística média".
const OCR_STATS_PROMPT = `Você é um extrator de dados de screenshots de apps de estatísticas de futebol (como Betano/SofaScore).
A imagem mostra uma tabela "Estatística média" com duas colunas de valores: a coluna da ESQUERDA pertence à equipe mandante (primeira bandeira, à esquerda no topo) e a coluna da DIREITA pertence à equipe visitante.

Extraia os dados e responda APENAS com um JSON válido, sem markdown, sem explicações, exatamente neste formato:
{
  "confronto": {
    "equipe_mandante": "NomeDaEquipe1",
    "equipe_visitante": "NomeDaEquipe2"
  },
  "estatistica_media": {
    "mandante": { "metricas": { "xg_gols_esperados": 0.0, "xga_xg_sofridos": 0.0, "chutes": 0.0, "chutes_no_gol": 0.0, "escanteios": 0.0 } },
    "visitante": { "metricas": { "xg_gols_esperados": 0.0, "xga_xg_sofridos": 0.0, "chutes": 0.0, "chutes_no_gol": 0.0, "escanteios": 0.0 } }
  }
}

Regras:
- "Gols esperados (xG)" -> xg_gols_esperados; "xG sofridos" -> xga_xg_sofridos; "Chutes" -> chutes; "Chutes no gol" -> chutes_no_gol; "Escanteios" -> escanteios
- Escreva os nomes das seleções em português (ex: "Austrália", "Egito", "Brasil").
- Se algum valor não estiver visível, use null.`;

// Paleta categórica já validada no projeto (ver RatingClubes.jsx) — ordem fixa.
const COR_MODELO = '#3987e5';   // azul (PALETA[0])
const COR_MERCADO = '#c98500';  // âmbar (PALETA[3]) — evita colidir com o verde
                                 // já usado em outras telas pra "melhor odd"/edge positivo

const OPCOES_N = [10, 20, 38];
const MERCADO_ROTULO = { '1X2': '1X2', 'over_under_2.5': 'Over/Under 2,5 gols', 'corners_over_under_9.5': 'Escanteios O/U 9,5' };
const SELECAO_ROTULO = { home: 'Mandante', draw: 'Empate', away: 'Visitante', over: 'Over', under: 'Under' };

function Escudo({ url, tamanho = 20 }) {
  return url
    ? <img src={url} alt="" className="object-contain shrink-0" style={{ width: tamanho, height: tamanho }} />
    : <Shield size={tamanho * 0.8} className="text-slate-700 shrink-0" />;
}

// Últimos N jogos finalizados do time (qualquer competição) + estatísticas próprias.
async function buscarTendenciasTime(teamId, antesDe, n) {
  const { data } = await supabase
    .from('matches')
    .select('id, home_team_id, away_team_id, home_goals, away_goals')
    .or(`home_team_id.eq.${teamId},away_team_id.eq.${teamId}`)
    .eq('status', 'finished')
    .lt('match_date', antesDe)
    .order('match_date', { ascending: false })
    .limit(n);

  const jogos = data || [];
  if (jogos.length === 0) return null;

  const matchIds = jogos.map(j => j.id);
  const { data: stats } = matchIds.length > 0
    ? await supabase.from('match_stats').select('match_id, team_id, corners, yellow_cards, red_cards').in('match_id', matchIds)
    : { data: [] };
  const statsPorJogo = {};
  (stats || []).filter(s => s.team_id === teamId).forEach(s => { statsPorJogo[s.match_id] = s; });

  let over25 = 0, btts = 0;
  let somaCorners = 0, nCorners = 0, somaCartoes = 0, nCartoes = 0;

  jogos.forEach(j => {
    const total = (j.home_goals ?? 0) + (j.away_goals ?? 0);
    if (total > 2.5) over25++;
    if ((j.home_goals ?? 0) > 0 && (j.away_goals ?? 0) > 0) btts++;

    const s = statsPorJogo[j.id];
    if (s?.corners != null) { somaCorners += Number(s.corners); nCorners++; }
    if (s?.yellow_cards != null) {
      somaCartoes += Number(s.yellow_cards) + Number(s.red_cards || 0);
      nCartoes++;
    }
  });

  return {
    n: jogos.length,
    pctOver25: over25 / jogos.length,
    pctBtts: btts / jogos.length,
    mediaCorners: nCorners > 0 ? somaCorners / nCorners : null,
    mediaCartoes: nCartoes > 0 ? somaCartoes / nCartoes : null,
  };
}

// Média de gols pró/contra e xG próprio/xGA (xG do ADVERSÁRIO nas mesmas
// partidas — xGA não é coluna própria, ver CONTEXTO_PROJETO.md) dos últimos N
// jogos do time. xg/xga só existem via Understat (5 ligas europeias); nas
// outras, o chamador cai pro gol real como aproximação.
async function buscarMediasModelo(teamId, antesDe, n) {
  const { data: jogos } = await supabase
    .from('matches')
    .select('id, home_team_id, away_team_id, home_goals, away_goals')
    .or(`home_team_id.eq.${teamId},away_team_id.eq.${teamId}`)
    .eq('status', 'finished')
    .lt('match_date', antesDe)
    .order('match_date', { ascending: false })
    .limit(n);

  if (!jogos || jogos.length === 0) return null;

  const matchIds = jogos.map(j => j.id);
  const oponentePorJogo = {};
  jogos.forEach(j => { oponentePorJogo[j.id] = j.home_team_id === teamId ? j.away_team_id : j.home_team_id; });

  const { data: stats } = await supabase.from('match_stats').select('match_id, team_id, xg').in('match_id', matchIds);

  let somaGolsPro = 0, somaGolsContra = 0;
  jogos.forEach(j => {
    const mandante = j.home_team_id === teamId;
    somaGolsPro += mandante ? (j.home_goals ?? 0) : (j.away_goals ?? 0);
    somaGolsContra += mandante ? (j.away_goals ?? 0) : (j.home_goals ?? 0);
  });

  let somaXg = 0, nXg = 0, somaXga = 0, nXga = 0;
  (stats || []).forEach(s => {
    if (s.xg == null) return;
    if (s.team_id === teamId) { somaXg += Number(s.xg); nXg++; }
    else if (s.team_id === oponentePorJogo[s.match_id]) { somaXga += Number(s.xg); nXga++; }
  });

  const mediaGolsPro = somaGolsPro / jogos.length;
  const mediaGolsContra = somaGolsContra / jogos.length;
  const mediaXg = nXg > 0 ? somaXg / nXg : null;
  const mediaXga = nXga > 0 ? somaXga / nXga : null;

  return {
    n: jogos.length,
    xgUsado: mediaXg ?? mediaGolsPro,
    xgaUsado: mediaXga ?? mediaGolsContra,
    xgReal: mediaXg != null && mediaXga != null,
  };
}

// Aplica o override do OCR (se o usuário colou um screenshot) por cima da
// média auto-importada — xg_gols_esperados/xga_xg_sofridos têm prioridade
// sobre o que veio do banco quando presentes, campo a campo (não é tudo ou
// nada: se só um dos dois times foi lido, o outro continua automático).
function aplicarOverrideOcr(medias, metricasOcr) {
  if (!metricasOcr) return medias;
  const xg = Number(metricasOcr.xg_gols_esperados);
  const xga = Number(metricasOcr.xga_xg_sofridos);
  return {
    ...medias,
    xgUsado: Number.isFinite(xg) ? xg : medias.xgUsado,
    xgaUsado: Number.isFinite(xga) ? xga : medias.xgaUsado,
    xgReal: (Number.isFinite(xg) || medias.xgReal) && (Number.isFinite(xga) || medias.xgReal),
    origemOcr: Number.isFinite(xg) || Number.isFinite(xga),
  };
}

// Faixas de total de gols exibidas (bandas fechadas, a última é "6+") — mesma
// segmentação usada em casas de apostas pro mercado "Total de gols por faixa".
const FAIXAS_GOLS = [{ min: 0, max: 1, label: '0-1' }, { min: 2, max: 3, label: '2-3' }, { min: 4, max: 5, label: '4-5' }, { min: 6, max: Infinity, label: '6+' }];

// Estimativa 1X2/Over-Under/Ambas Marcam com o mesmo motor de AnaliseEvento.jsx
// (fórmula "multiplicativo" padrão + correção Dixon-Coles, ambos em utils/).
// Faixas de total de gols são acumuladas na MESMA grade Poisson×Dixon-Coles já
// calculada aqui (sem custo extra, sem endpoint novo) — não é um mercado
// diferente, é a mesma distribuição de placares já usada pro Over/Under 2.5,
// só reagrupada em bandas mais informativas que um único corte binário.
function estimarMercadosGols(mediasMandante, mediasVisitante) {
  const formula = getLambdaFormula('multiplicativo');
  const { trueXG1, trueXG2 } = formula.calc({
    m: { xg1: mediasMandante.xgUsado, xga2: mediasVisitante.xgaUsado, xg2: mediasVisitante.xgUsado, xga1: mediasMandante.xgaUsado },
  });
  const lambda1 = Math.max(0.1, trueXG1);
  const lambda2 = Math.max(0.1, trueXG2);

  const maxGoals = 10;
  let probWin1 = 0, probWin2 = 0, probDraw = 0, probOver25 = 0, probBtts = 0;
  const somaFaixas = FAIXAS_GOLS.map(() => 0);
  for (let i = 0; i <= maxGoals; i++) {
    for (let j = 0; j <= maxGoals; j++) {
      let p = poisson(lambda1, i) * poisson(lambda2, j);
      p *= dixonColesTau(i, j, lambda1, lambda2, DIXON_COLES_RHO);
      if (i > j) probWin1 += p;
      else if (i < j) probWin2 += p;
      else probDraw += p;
      if (i + j > 2.5) probOver25 += p;
      if (i > 0 && j > 0) probBtts += p;
      const totalGols = i + j;
      const idxFaixa = FAIXAS_GOLS.findIndex(f => totalGols >= f.min && totalGols <= f.max);
      if (idxFaixa !== -1) somaFaixas[idxFaixa] += p;
    }
  }
  const total = probWin1 + probWin2 + probDraw;
  return {
    lambda1, lambda2,
    probHome: probWin1 / total, probDraw: probDraw / total, probAway: probWin2 / total,
    probOver25: probOver25 / total, probBtts: probBtts / total,
    faixasGols: FAIXAS_GOLS.map((f, idx) => ({ label: f.label, prob: somaFaixas[idx] / total })),
    xgRealMandante: mediasMandante.xgReal, xgRealVisitante: mediasVisitante.xgReal,
    origemOcr: !!(mediasMandante.origemOcr || mediasVisitante.origemOcr),
  };
}

// Reaproveita api/corners-model.js (mesma Binomial Negativa calibrada por liga
// já usada em AnaliseEvento.jsx) em vez de reimplementar a calibração aqui.
// Manda os IDs junto (já sabemos exatamente qual time é, diferente da
// calculadora manual) — evita pegar o time errado quando dois clubes
// diferentes têm o mesmo nome literal (ex: "Liverpool FC" inglês x uruguaio).
async function buscarEstimativaEscanteios(nomeMandante, nomeVisitante, idMandante, idVisitante) {
  try {
    const params = new URLSearchParams({ mandante: nomeMandante, visitante: nomeVisitante, linhas: '9.5' });
    if (idMandante) params.set('mandante_id', idMandante);
    if (idVisitante) params.set('visitante_id', idVisitante);
    const resposta = await fetch(`/api/corners-model?${params.toString()}`);
    if (!resposta.ok) return null;
    const dados = await resposta.json();
    return dados.error ? null : dados;
  } catch {
    return null;
  }
}

// Faixas de total de escanteios (mesma ideia das faixas de gols) — reaproveita
// o total esperado (mandante+visitante) e o disp_r já devolvidos por
// api/corners-model.js (escanteios_esperados.total / modelo.disp_r), somando a
// PMF da Binomial Negativa em cada banda. Sem chamada extra: os dois números
// já vêm na mesma resposta usada pras linhas Over/Under 9,5 existentes.
const FAIXAS_ESCANTEIOS = [{ min: 0, max: 8, label: '≤8' }, { min: 9, max: 10, label: '9-10' }, { min: 11, max: 12, label: '11-12' }, { min: 13, max: 40, label: '13+' }];

function calcularFaixasEscanteios(mediaTotal, dispR) {
  if (mediaTotal == null || !dispR) return null;
  return FAIXAS_ESCANTEIOS.map(f => {
    let soma = 0;
    for (let x = f.min; x <= f.max; x++) soma += negBinomialPMF(mediaTotal, dispR, x);
    return { label: f.label, prob: soma };
  });
}

function devigar(oddsPorSelecao) {
  const implicitas = {};
  let soma = 0;
  for (const [sel, odd] of Object.entries(oddsPorSelecao)) {
    implicitas[sel] = 1 / odd;
    soma += implicitas[sel];
  }
  const normalizadas = {};
  for (const sel of Object.keys(implicitas)) normalizadas[sel] = implicitas[sel] / soma;
  return normalizadas;
}

// Previsões do modelo pra essa partida + a melhor fonte de odds disponível
// (prioriza a média de mercado no fechamento; sem isso, cai pra qualquer casa)
// + calibração Platt/Isotonic já ajustada (model_calibration) — aplicada aqui
// em cima da probabilidade crua, escolhendo por combinação o método com menor
// log-loss medido no teste (ver src/utils/calibration.js). O edge exibido usa
// a probabilidade CALIBRADA quando disponível, já que o achado documentado é
// que o modelo bruto é sistematicamente overconfident nos mercados binários —
// um edge calculado sobre probabilidade não calibrada superestima a vantagem
// real. Cai pra crua (sem calibração) só quando a combinação nunca teve
// amostra suficiente pra calibrar.
async function buscarComparacaoModeloMercado(matchId) {
  const [{ data: previsoes }, { data: oddsRows }, { data: calibracaoRows }] = await Promise.all([
    supabase.from('model_predictions').select('model_name, market, selection, probability').eq('match_id', matchId),
    supabase.from('odds_market').select('bookmaker, market, selection, odds, snapshot, captured_at').eq('match_id', matchId).order('captured_at', { ascending: false }),
    supabase.from('model_calibration').select('model_name, market, selection, method, platt_coef, platt_intercept, isotonic_x, isotonic_y, log_loss_calibrado, n_teste'),
  ]);
  if (!previsoes || previsoes.length === 0) return [];

  const indiceCalibracao = indexarCalibracao(calibracaoRows);

  const porMercado = {};
  (oddsRows || []).forEach(r => {
    if (!porMercado[r.market]) porMercado[r.market] = {};
    const fonte = r.bookmaker === 'media_mercado' && r.snapshot === 'closing' ? 'preferencial' : 'fallback';
    if (!porMercado[r.market][fonte]) porMercado[r.market][fonte] = {};
    if (!(r.selection in porMercado[r.market][fonte])) porMercado[r.market][fonte][r.selection] = r.odds;
  });

  const probMercadoPorMercado = {};
  const fonteUsadaPorMercado = {};
  Object.entries(porMercado).forEach(([mercado, fontes]) => {
    const oddsEscolhidas = fontes.preferencial || fontes.fallback;
    if (oddsEscolhidas) {
      probMercadoPorMercado[mercado] = devigar(oddsEscolhidas);
      fonteUsadaPorMercado[mercado] = fontes.preferencial ? 'Média de mercado (fechamento)' : 'Casa individual (mais recente)';
    }
  });

  const porModeloMercado = {};
  previsoes.forEach(p => {
    const chave = `${p.model_name}__${p.market}`;
    if (!porModeloMercado[chave]) porModeloMercado[chave] = { model_name: p.model_name, market: p.market, selecoes: [] };
    const pModelo = Number(p.probability);
    const calibrado = calibrarProbabilidade(pModelo, indiceCalibracao, p.model_name, p.market, p.selection);
    porModeloMercado[chave].selecoes.push({
      selecao: p.selection,
      pModelo,
      pCalibrado: calibrado?.probabilidade ?? null,
      metodoCalibracao: calibrado?.metodo ?? null,
      nTesteCalibracao: calibrado?.nTeste ?? null,
      pMercado: probMercadoPorMercado[p.market]?.[p.selection] ?? null,
    });
  });

  return Object.values(porModeloMercado).map(g => ({
    ...g,
    fonteOdds: fonteUsadaPorMercado[g.market] || null,
  }));
}

async function buscarPrecisaoModelo(ligaId) {
  try {
    const resposta = await fetch(`/api/model-stats?liga_id=${ligaId}`);
    if (!resposta.ok) return [];
    const dados = await resposta.json();
    return dados.grupos || [];
  } catch {
    return [];
  }
}

function BarraProgresso({ pct, cor = '#10b981' }) {
  return (
    <div className="w-full h-1.5 bg-slate-900 rounded-full overflow-hidden">
      <div className="h-full rounded-full" style={{ width: `${Math.max(0, Math.min(100, pct * 100))}%`, backgroundColor: cor }} />
    </div>
  );
}

function CardTendencia({ titulo, valor, sufixo = '', pct = null, cor = '#10b981' }) {
  return (
    <div className="bg-slate-900 rounded-lg p-3">
      <div className="text-[10px] text-slate-500 uppercase mb-1">{titulo}</div>
      <div className="text-lg font-bold text-slate-200 mb-1.5">{valor != null ? `${valor}${sufixo}` : '—'}</div>
      {pct != null && <BarraProgresso pct={pct} cor={cor} />}
    </div>
  );
}

function PainelTendencias({ nome, crestUrl, tendencias, ladoEsquerda }) {
  return (
    <div className="bg-slate-800 border border-slate-700 rounded-2xl p-5">
      <div className={`flex items-center gap-3 mb-4 ${ladoEsquerda ? '' : 'flex-row-reverse text-right'}`}>
        <div className="w-10 h-10 rounded-xl bg-slate-900 border border-slate-700 flex items-center justify-center overflow-hidden shrink-0">
          {crestUrl ? <img src={crestUrl} alt="" className="w-full h-full object-contain" /> : <Shield className="text-slate-600" size={18} />}
        </div>
        <h2 className="font-bold text-slate-100 text-sm">{nome}</h2>
      </div>

      {!tendencias ? (
        <p className="text-xs text-slate-600">Sem jogos suficientes no histórico.</p>
      ) : (
        <div className="grid grid-cols-2 gap-2">
          <CardTendencia titulo="Over 2,5 gols" valor={(tendencias.pctOver25 * 100).toFixed(0)} sufixo="%" pct={tendencias.pctOver25} cor="#10b981" />
          <CardTendencia titulo="Ambas marcam" valor={(tendencias.pctBtts * 100).toFixed(0)} sufixo="%" pct={tendencias.pctBtts} cor="#10b981" />
          <CardTendencia titulo="Escanteios (méd.)" valor={tendencias.mediaCorners?.toFixed(1)} />
          <CardTendencia titulo="Cartões (méd.)" valor={tendencias.mediaCartoes?.toFixed(1)} />
        </div>
      )}
      <p className="text-[10px] text-slate-600 mt-3">Últimos {tendencias?.n ?? 0} jogos (qualquer competição).</p>
    </div>
  );
}

function LinhaModeloMercado({ selecao, pModelo, pCalibrado, metodoCalibracao, pMercado }) {
  // Edge "de verdade" usa a probabilidade CALIBRADA quando existe (o modelo
  // bruto é sistematicamente overconfident nos mercados binários, ver
  // src/utils/calibration.js) — só cai pra crua se essa combinação nunca foi
  // calibrada (amostra insuficiente).
  const pParaEdge = pCalibrado ?? pModelo;
  const edge = pMercado != null ? pParaEdge - pMercado : null;
  return (
    <div className="py-2">
      <div className="flex items-center justify-between text-xs mb-1">
        <span className="text-slate-300 font-semibold">{SELECAO_ROTULO[selecao] || selecao}</span>
        {edge != null && (
          <span className={`font-mono text-[11px] font-bold ${edge > 0 ? 'text-emerald-400' : edge < 0 ? 'text-red-400' : 'text-slate-500'}`}>
            {edge > 0 ? '+' : ''}{(edge * 100).toFixed(1)}pp
          </span>
        )}
      </div>
      <div className="space-y-1">
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-slate-500 w-14 shrink-0">{pCalibrado != null ? 'Modelo*' : 'Modelo'}</span>
          <BarraProgresso pct={pParaEdge} cor={COR_MODELO} />
          <span className="text-[11px] font-mono text-slate-400 w-10 text-right shrink-0">{(pParaEdge * 100).toFixed(0)}%</span>
        </div>
        {pCalibrado != null && (
          <div className="flex items-center gap-2 opacity-60">
            <span className="text-[10px] text-slate-600 w-14 shrink-0">Cru</span>
            <span className="text-[10px] text-slate-600">{(pModelo * 100).toFixed(0)}% sem calibração</span>
          </div>
        )}
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-slate-500 w-14 shrink-0">Mercado</span>
          {pMercado != null
            ? <BarraProgresso pct={pMercado} cor={COR_MERCADO} />
            : <div className="w-full h-1.5 bg-slate-900 rounded-full" />}
          <span className="text-[11px] font-mono text-slate-400 w-10 text-right shrink-0">{pMercado != null ? `${(pMercado * 100).toFixed(0)}%` : '—'}</span>
        </div>
      </div>
      {pCalibrado != null && (
        <p className="text-[9px] text-slate-600 mt-1">*calibrado ({metodoCalibracao === 'platt' ? 'Platt Scaling' : 'Isotonic Regression'})</p>
      )}
    </div>
  );
}

function PainelEstimativaModelo({ estimativa, escanteios, faixasEscanteios, mandante, visitante, onOcrUpload, ocrLoading, ocrError, ocrSuccess, temOverrideOcr, onLimparOcr }) {
  const inputRef = useRef(null);
  return (
    <div className="bg-slate-800 border border-slate-700 rounded-2xl p-5">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-bold text-slate-300 uppercase tracking-wider flex items-center gap-2">
          <Calculator className="text-emerald-400" size={16} /> Estimativa do modelo
        </h2>
        <div className="flex items-center gap-2">
          {temOverrideOcr && (
            <button onClick={onLimparOcr} className="text-[10px] text-slate-500 hover:text-slate-300 underline">
              limpar OCR
            </button>
          )}
          <input ref={inputRef} type="file" accept="image/*" className="hidden" onChange={onOcrUpload} />
          <button
            onClick={() => inputRef.current?.click()}
            disabled={ocrLoading}
            title="Colar screenshot de 'Estatística média' (Betano/SofaScore) pra sobrescrever o xG auto-importado"
            className="flex items-center gap-1.5 bg-slate-900 hover:bg-slate-700 text-slate-400 hover:text-slate-200 text-[10px] font-bold px-2.5 py-1.5 rounded-lg transition-colors disabled:opacity-50"
          >
            {ocrLoading ? <Loader2 size={12} className="animate-spin" /> : <Camera size={12} />}
            {ocrLoading ? 'Lendo...' : 'Importar screenshot (OCR)'}
          </button>
        </div>
      </div>

      {ocrError && <p className="text-[10px] text-red-400 mb-2 flex items-center gap-1"><AlertTriangle size={11} /> {ocrError}</p>}
      {ocrSuccess && !ocrError && <p className="text-[10px] text-emerald-400 mb-2 flex items-center gap-1"><ScanLine size={11} /> {ocrSuccess}</p>}

      {!estimativa ? (
        <p className="text-xs text-slate-600">Sem jogos recentes suficientes pra estimar.</p>
      ) : (
        <div className="space-y-4">
          <div>
            <span className="text-[10px] uppercase font-bold text-slate-500 block mb-1.5">Resultado final (1X2)</span>
            <div className="grid grid-cols-3 gap-2">
              <CardTendencia titulo={mandante} valor={(estimativa.probHome * 100).toFixed(0)} sufixo="%" pct={estimativa.probHome} cor={COR_MODELO} />
              <CardTendencia titulo="Empate" valor={(estimativa.probDraw * 100).toFixed(0)} sufixo="%" pct={estimativa.probDraw} cor={COR_MODELO} />
              <CardTendencia titulo={visitante} valor={(estimativa.probAway * 100).toFixed(0)} sufixo="%" pct={estimativa.probAway} cor={COR_MODELO} />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-2">
            <CardTendencia titulo="Over 2,5 gols" valor={(estimativa.probOver25 * 100).toFixed(0)} sufixo="%" pct={estimativa.probOver25} cor="#10b981" />
            <CardTendencia titulo="Ambas marcam" valor={(estimativa.probBtts * 100).toFixed(0)} sufixo="%" pct={estimativa.probBtts} cor="#10b981" />
          </div>

          {estimativa.faixasGols && (
            <div>
              <span className="text-[10px] uppercase font-bold text-slate-500 block mb-1.5">Faixas de total de gols</span>
              <div className="grid grid-cols-4 gap-2">
                {estimativa.faixasGols.map(f => (
                  <CardTendencia key={f.label} titulo={f.label} valor={(f.prob * 100).toFixed(0)} sufixo="%" pct={f.prob} cor="#3987e5" />
                ))}
              </div>
            </div>
          )}

          {escanteios?.mercados?.[0] && (
            <div>
              <span className="text-[10px] uppercase font-bold text-slate-500 block mb-1.5">Escanteios (linha 9,5)</span>
              <div className="grid grid-cols-2 gap-2">
                <CardTendencia titulo="Over 9,5" valor={(escanteios.mercados[0].prob_over * 100).toFixed(0)} sufixo="%" pct={escanteios.mercados[0].prob_over} cor="#10b981" />
                <CardTendencia titulo="Under 9,5" valor={(escanteios.mercados[0].prob_under * 100).toFixed(0)} sufixo="%" pct={escanteios.mercados[0].prob_under} cor="#10b981" />
              </div>
            </div>
          )}

          {faixasEscanteios && (
            <div>
              <span className="text-[10px] uppercase font-bold text-slate-500 block mb-1.5">Faixas de total de escanteios</span>
              <div className="grid grid-cols-4 gap-2">
                {faixasEscanteios.map(f => (
                  <CardTendencia key={f.label} titulo={f.label} valor={(f.prob * 100).toFixed(0)} sufixo="%" pct={f.prob} cor="#3987e5" />
                ))}
              </div>
            </div>
          )}

          <p className="text-[10px] text-slate-600">
            λ {mandante.split(' ')[0]}={estimativa.lambda1.toFixed(2)} / {visitante.split(' ')[0]}={estimativa.lambda2.toFixed(2)} ·
            {' '}xG {estimativa.origemOcr ? 'com dado do screenshot importado' : estimativa.xgRealMandante && estimativa.xgRealVisitante ? 'real (Understat)' : 'aproximado por gols (xG indisponível pra essa liga)'} ·
            {' '}fórmula multiplicativa + Dixon-Coles, mesmo motor de Análise de Eventos.
          </p>
        </div>
      )}
    </div>
  );
}

function PainelModeloMercado({ grupos }) {
  return (
    <div className="bg-slate-800 border border-slate-700 rounded-2xl p-5">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-bold text-slate-300 uppercase tracking-wider flex items-center gap-2">
          <Target className="text-emerald-400" size={16} /> Modelo vs. mercado
        </h2>
        {grupos.length > 0 && (
          <div className="flex items-center gap-3 text-[10px] text-slate-500">
            <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full" style={{ backgroundColor: COR_MODELO }} /> Modelo</span>
            <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full" style={{ backgroundColor: COR_MERCADO }} /> Mercado</span>
          </div>
        )}
      </div>

      {grupos.length === 0 ? (
        <p className="text-xs text-slate-600">Sem previsão do modelo salva pra essa partida ainda.</p>
      ) : (
        <div className="space-y-4">
          <p className="text-[10px] text-slate-600 -mt-2">
            Quando marcado com *, "Modelo" já é a probabilidade calibrada (Platt/Isotonic ajustados em cima do histórico real — ver /modelos) em vez da crua; o edge usa essa versão calibrada.
          </p>
          {grupos.map(g => (
            <div key={`${g.model_name}__${g.market}`}>
              <div className="flex items-center justify-between mb-1">
                <span className="text-[10px] uppercase font-bold text-slate-500">{MERCADO_ROTULO[g.market] || g.market}</span>
                <span className="text-[10px] text-slate-600">{g.model_name}</span>
              </div>
              <div className="divide-y divide-slate-700/50">
                {g.selecoes.map(s => <LinhaModeloMercado key={s.selecao} {...s} />)}
              </div>
              {g.fonteOdds && <p className="text-[10px] text-slate-600 mt-1">Odds: {g.fonteOdds}</p>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function PainelPrecisaoModelo({ grupos }) {
  return (
    <div className="bg-slate-800 border border-slate-700 rounded-2xl p-5">
      <h2 className="text-sm font-bold text-slate-300 uppercase tracking-wider mb-3 flex items-center gap-2">
        <History className="text-emerald-400" size={16} /> Histórico de precisão do modelo
      </h2>

      {grupos.length === 0 ? (
        <p className="text-xs text-slate-600">Sem histórico de avaliação do modelo pra essa liga/mercado ainda.</p>
      ) : (
        <div className="space-y-3">
          {grupos.map(g => (
            <div key={`${g.model_name}__${g.market}`} className="bg-slate-900 rounded-lg p-3">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-bold text-slate-300">{MERCADO_ROTULO[g.market] || g.market}</span>
                <span className="text-[10px] text-slate-600">{g.model_name} · {g.n_jogos} jogos avaliados</span>
              </div>
              <div className="grid grid-cols-3 gap-2 text-center">
                <div>
                  <div className="text-[10px] text-slate-500 uppercase">Log-loss</div>
                  <div className="text-xs font-mono text-slate-300">
                    {g.log_loss_modelo?.toFixed(3) ?? '—'}
                    {g.log_loss_mercado != null && <span className="text-slate-600"> / {g.log_loss_mercado.toFixed(3)}</span>}
                  </div>
                </div>
                <div>
                  <div className="text-[10px] text-slate-500 uppercase">Brier</div>
                  <div className="text-xs font-mono text-slate-300">
                    {g.brier_modelo?.toFixed(3) ?? '—'}
                    {g.brier_mercado != null && <span className="text-slate-600"> / {g.brier_mercado.toFixed(3)}</span>}
                  </div>
                </div>
                <div>
                  <div className="text-[10px] text-slate-500 uppercase">Acurácia</div>
                  <div className="text-xs font-mono text-slate-300">
                    {g.accuracy_modelo != null ? `${(g.accuracy_modelo * 100).toFixed(0)}%` : '—'}
                    {g.accuracy_mercado != null && <span className="text-slate-600"> / {(g.accuracy_mercado * 100).toFixed(0)}%</span>}
                  </div>
                </div>
              </div>
            </div>
          ))}
          <p className="text-[10px] text-slate-600">Modelo / mercado, quando a comparação existe. Métricas mais baixas (log-loss, Brier) são melhores.</p>
        </div>
      )}
    </div>
  );
}

export default function AnaliseEstatisticaJogo() {
  const { matchId } = useParams();
  const [jogo, setJogo] = useState(null);
  const [n, setN] = useState(20);
  const [tendenciasMandante, setTendenciasMandante] = useState(null);
  const [tendenciasVisitante, setTendenciasVisitante] = useState(null);
  const [comparacaoModelo, setComparacaoModelo] = useState([]);
  const [precisaoModelo, setPrecisaoModelo] = useState([]);
  const [mediasBrutas, setMediasBrutas] = useState(null); // { mandante, visitante } antes de qualquer override de OCR
  const [estimativaEscanteios, setEstimativaEscanteios] = useState(null);
  const [ocrOverride, setOcrOverride] = useState(null); // { mandante: metricas|null, visitante: metricas|null }
  const [ocrLoading, setOcrLoading] = useState(false);
  const [ocrError, setOcrError] = useState('');
  const [ocrSuccess, setOcrSuccess] = useState('');
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState('');

  useEffect(() => {
    if (!supabaseAtivo) return;
    (async () => {
      setCarregando(true);
      setErro('');
      const { data: j, error: erroJogo } = await supabase
        .from('matches')
        .select('id, match_date, league_id, home_team_id, away_team_id, leagues(name), home:teams!matches_home_team_id_fkey(id,name,crest_url), away:teams!matches_away_team_id_fkey(id,name,crest_url)')
        .eq('id', matchId)
        .single();

      if (erroJogo || !j) { setErro('Jogo não encontrado.'); setCarregando(false); return; }
      setJogo(j);

      const referencia = j.match_date || new Date().toISOString();
      const [tM, tV, comparacao, precisao, mediasMandante, mediasVisitante, escanteios] = await Promise.all([
        buscarTendenciasTime(j.home_team_id, referencia, n),
        buscarTendenciasTime(j.away_team_id, referencia, n),
        buscarComparacaoModeloMercado(j.id),
        buscarPrecisaoModelo(j.league_id),
        buscarMediasModelo(j.home_team_id, referencia, n),
        buscarMediasModelo(j.away_team_id, referencia, n),
        buscarEstimativaEscanteios(j.home?.name, j.away?.name, j.home_team_id, j.away_team_id),
      ]);
      setTendenciasMandante(tM);
      setTendenciasVisitante(tV);
      setComparacaoModelo(comparacao);
      setMediasBrutas(mediasMandante && mediasVisitante ? { mandante: mediasMandante, visitante: mediasVisitante } : null);
      setEstimativaEscanteios(escanteios);
      setOcrOverride(null);
      setOcrError('');
      setOcrSuccess('');

      const mercadosDaPartida = new Set(comparacao.map(c => c.market));
      const modelosDaPartida = new Set(comparacao.map(c => c.model_name));
      setPrecisaoModelo((precisao || []).filter(g => mercadosDaPartida.has(g.market) && modelosDaPartida.has(g.model_name)));

      setCarregando(false);
    })();
  }, [matchId, n]);

  const estimativaModelo = useMemo(() => {
    if (!mediasBrutas) return null;
    const mandante = aplicarOverrideOcr(mediasBrutas.mandante, ocrOverride?.mandante);
    const visitante = aplicarOverrideOcr(mediasBrutas.visitante, ocrOverride?.visitante);
    return estimarMercadosGols(mandante, visitante);
  }, [mediasBrutas, ocrOverride]);

  const faixasEscanteios = useMemo(
    () => calcularFaixasEscanteios(estimativaEscanteios?.escanteios_esperados?.total, estimativaEscanteios?.modelo?.disp_r),
    [estimativaEscanteios]
  );

  const handleOcrUpload = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setOcrError(''); setOcrSuccess(''); setOcrLoading(true);
    try {
      const parsed = await extractJsonFromImage(file, OCR_STATS_PROMPT);
      const mMandante = parsed?.estatistica_media?.mandante?.metricas;
      const mVisitante = parsed?.estatistica_media?.visitante?.metricas;
      if (!mMandante && !mVisitante) throw new Error('Não consegui identificar a tabela de estatísticas nessa imagem.');
      setOcrOverride({ mandante: mMandante || null, visitante: mVisitante || null });
      setOcrSuccess(`Estatísticas de ${parsed.confronto?.equipe_mandante || 'mandante'} x ${parsed.confronto?.equipe_visitante || 'visitante'} importadas — conferindo se batem com o confronto certo antes de usar.`);
    } catch (error) {
      setOcrError(error.message || 'Não foi possível extrair as estatísticas dessa imagem.');
    } finally {
      setOcrLoading(false);
      event.target.value = '';
    }
  };

  if (!supabaseAtivo) {
    return (
      <div className="max-w-4xl mx-auto bg-slate-800 border border-red-500/30 rounded-2xl p-6 text-center">
        <AlertTriangle className="text-red-400 mx-auto mb-2" size={28} />
        <p className="text-slate-300">Supabase não configurado.</p>
      </div>
    );
  }

  if (carregando) {
    return (
      <div className="max-w-4xl mx-auto flex items-center justify-center py-16 text-slate-500 gap-2 text-sm">
        <Loader2 className="animate-spin" size={20} /> Carregando...
      </div>
    );
  }

  if (erro || !jogo) {
    return (
      <div className="max-w-4xl mx-auto bg-slate-800 border border-red-500/30 rounded-2xl p-6 text-center">
        <AlertTriangle className="text-red-400 mx-auto mb-2" size={28} />
        <p className="text-slate-300">{erro || 'Jogo não encontrado.'}</p>
        <Link to="/eventos" className="text-emerald-400 text-sm hover:underline mt-3 inline-block">← Voltar pra Eventos</Link>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto">
      <Link to={`/historico/${jogo.id}`} className="flex items-center gap-1.5 text-slate-400 hover:text-slate-200 text-sm mb-4 w-fit">
        <ArrowLeft size={16} /> Voltar pro confronto
      </Link>

      <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 mb-4">
        <p className="text-center text-xs text-slate-500 uppercase tracking-wider mb-1 flex items-center justify-center gap-1.5">
          <TrendingUp size={12} className="text-emerald-400" /> Análise estatística · {jogo.leagues?.name || 'Confronto'}
        </p>
        <div className="flex items-center justify-center gap-4 mt-2">
          <div className="flex items-center gap-2">
            <Escudo url={jogo.home?.crest_url} tamanho={28} />
            <span className="font-bold text-slate-100">{jogo.home?.name}</span>
          </div>
          <span className="text-slate-500 text-xs">vs</span>
          <div className="flex items-center gap-2">
            <span className="font-bold text-slate-100">{jogo.away?.name}</span>
            <Escudo url={jogo.away?.crest_url} tamanho={28} />
          </div>
        </div>

        <div className="flex items-center justify-center gap-2 mt-4">
          <span className="text-xs text-slate-500 mr-1">Tendências dos últimos:</span>
          {OPCOES_N.map(opcao => (
            <button
              key={opcao}
              onClick={() => setN(opcao)}
              className={`px-3 py-1 rounded-lg text-xs font-bold ${n === opcao ? 'bg-emerald-500/20 text-emerald-400' : 'bg-slate-900 text-slate-500 hover:text-slate-300'}`}
            >
              {opcao} jogos
            </button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
        <PainelTendencias nome={jogo.home?.name} crestUrl={jogo.home?.crest_url} tendencias={tendenciasMandante} ladoEsquerda />
        <PainelTendencias nome={jogo.away?.name} crestUrl={jogo.away?.crest_url} tendencias={tendenciasVisitante} ladoEsquerda={false} />
      </div>

      <div className="mb-4">
        <PainelEstimativaModelo
          estimativa={estimativaModelo}
          escanteios={estimativaEscanteios}
          faixasEscanteios={faixasEscanteios}
          mandante={jogo.home?.name}
          visitante={jogo.away?.name}
          onOcrUpload={handleOcrUpload}
          ocrLoading={ocrLoading}
          ocrError={ocrError}
          ocrSuccess={ocrSuccess}
          temOverrideOcr={!!ocrOverride}
          onLimparOcr={() => { setOcrOverride(null); setOcrSuccess(''); setOcrError(''); }}
        />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <PainelModeloMercado grupos={comparacaoModelo} />
        <PainelPrecisaoModelo grupos={precisaoModelo} />
      </div>
    </div>
  );
}
