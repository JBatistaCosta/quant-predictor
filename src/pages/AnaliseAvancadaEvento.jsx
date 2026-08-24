// src/pages/AnaliseAvancadaEvento.jsx — rota /analise-avancada/:matchId
//
// Painel "inspirado" em AnaliseEvento.jsx (/analise), mas invertendo o fluxo:
// lá o usuário escolhe times e fórmula manualmente; aqui os parâmetros do
// modelo misto (λ dos gols, ρ de Dixon-Coles, λ/dispersão/α/β de escanteios)
// são IMPRESSOS AUTOMATICAMENTE do banco (`model_match_estimates.params`)
// pra uma partida específica, sem nenhum campo editável. Todo o aparato de
// AnaliseEvento.jsx (Monte Carlo, Kelly, scanner EV+ multi-mercado, odds de
// bookmaker) fica de fora — esse painel só mostra o que o modelo misto já
// estimou e persistiu.
//
// Reaproveita a mesma matemática de `src/utils/distribuicoesMercados.js`
// (gêmeo JS de `scripts/distribuicoes.py`, com teste de paridade) — nenhuma
// derivação de mercado é escrita de novo aqui.
//
// `model_match_estimates` tem RLS de leitura pública, então a consulta é
// direta via supabase-js (sem função serverless nova — api/ já está no teto
// de 12 do plano Hobby do Vercel).
import React, { useState, useEffect, useMemo } from 'react';
import { useParams, Link } from 'react-router-dom';
import { ArrowLeft, AlertTriangle, Shield, Loader2, FlaskConical, Target, TrendingUp, Percent, Scale } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';
import {
  matrizPlacares, mercadosDeGols, mercadosDeEscanteios, distribuicaoConjuntaEscanteios, lerParametrosPartida, rotuloLinha,
} from '../utils/distribuicoesMercados';
import { devigarOddsRatio, stakeKelly25 } from '../utils/devig';
import { toPct } from '../utils/format';

// Mercados em que o modelo misto (gols/escanteios) tem probabilidade
// calculada E que aparecem salvos em odds_market — únicos candidatos pra
// comparação de EV. A chave usada aqui é sempre a string de `market` como
// salva no banco; handicap (asiático e de escanteios) usa outra convenção
// de nome (`asian_handicap_<linha>`/`corners_handicap_<linha>`) e fica fora
// de escopo por ora.
const LINHAS_OU_EV = [0.5, 1.5, 2.5, 3.5, 4.5];
const LINHAS_OU_CORNERS_EV = [7.5, 8.5, 9.5, 10.5, 11.5]; // total do jogo — mesmas linhas de LINHAS_OU_CORNERS
const LINHAS_OU_CORNERS_TIME_EV = [3.5, 4.5, 5.5, 6.5]; // por time — mesmas linhas do card "Escanteios por time"

function mercadosComparaveis(mercadosGols, mercadosCorners) {
  const saida = {};
  if (mercadosGols) {
    saida['1X2'] = mercadosGols['1X2'];
    saida.btts = mercadosGols.btts;
    for (const linha of LINHAS_OU_EV) saida[`over_under_${rotuloLinha(linha)}`] = mercadosGols[`over_under_${rotuloLinha(linha)}`];
  }
  if (mercadosCorners) {
    saida.corners_1x2 = mercadosCorners.corners_1X2;
    for (const linha of LINHAS_OU_CORNERS_EV) {
      saida[`corners_over_under_full_time_${rotuloLinha(linha)}`] = mercadosCorners[`corners_over_under_${rotuloLinha(linha)}`];
    }
    for (const linha of LINHAS_OU_CORNERS_TIME_EV) {
      saida[`corners_over_under_team_1_${rotuloLinha(linha)}`] = mercadosCorners[`corners_home_over_under_${rotuloLinha(linha)}`];
      saida[`corners_over_under_team_2_${rotuloLinha(linha)}`] = mercadosCorners[`corners_away_over_under_${rotuloLinha(linha)}`];
    }
  }
  return saida;
}

// Todas as strings de `market` acima, num array — usado pra filtrar a
// consulta em odds_market direto no Supabase (`.in('market', ...)`), o que
// já evita de longe o corte silencioso de 1000 linhas do PostgREST (partidas
// antigas/muito negociadas podem ter dezenas de milhares de linhas somando
// TODOS os mercados salvos — a paginação abaixo cobre o resto).
const MERCADOS_EV = [
  '1X2', 'btts', ...LINHAS_OU_EV.map((l) => `over_under_${rotuloLinha(l)}`),
  'corners_1x2', ...LINHAS_OU_CORNERS_EV.map((l) => `corners_over_under_full_time_${rotuloLinha(l)}`),
  ...LINHAS_OU_CORNERS_TIME_EV.map((l) => `corners_over_under_team_1_${rotuloLinha(l)}`),
  ...LINHAS_OU_CORNERS_TIME_EV.map((l) => `corners_over_under_team_2_${rotuloLinha(l)}`),
];

function formatarSnapshot(capturedAt) {
  return new Date(capturedAt).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function rotuloMercado(mercado) {
  if (mercado === '1X2') return '1X2 (gols)';
  if (mercado === 'btts') return 'Ambas Marcam';
  if (mercado === 'corners_1x2') return 'Escanteios 1X2';
  const ouGols = mercado.match(/^over_under_(\d+\.\d)$/);
  if (ouGols) return `O/U ${ouGols[1]} gols`;
  const ouCornersTotal = mercado.match(/^corners_over_under_full_time_(\d+\.\d)$/);
  if (ouCornersTotal) return `O/U ${ouCornersTotal[1]} escanteios (total)`;
  const ouCornersTime1 = mercado.match(/^corners_over_under_team_1_(\d+\.\d)$/);
  if (ouCornersTime1) return `O/U ${ouCornersTime1[1]} escanteios (mandante)`;
  const ouCornersTime2 = mercado.match(/^corners_over_under_team_2_(\d+\.\d)$/);
  if (ouCornersTime2) return `O/U ${ouCornersTime2[1]} escanteios (visitante)`;
  return mercado;
}

// Confere se uma seleção específica bateu com o resultado REAL da partida --
// só usado pra partida já finalizada (ver "Resultado" na Verificação de EV).
// `resultado` = { golsHome, golsAway, cornersHome, cornersAway } — corners
// ficam null quando a fonte de estatística não cobre a partida (ver
// buscarCornersReais), e nesse caso os mercados de escanteios retornam null
// (sem dado real pra avaliar), não `false`.
function avaliarSelecao(mercado, selecao, resultado) {
  const totalGols = resultado.golsHome + resultado.golsAway;
  if (mercado === '1X2') {
    const vencedor = resultado.golsHome > resultado.golsAway ? 'home' : resultado.golsHome < resultado.golsAway ? 'away' : 'draw';
    return selecao === vencedor;
  }
  if (mercado === 'btts') {
    const ambasMarcaram = resultado.golsHome > 0 && resultado.golsAway > 0;
    return (selecao === 'yes') === ambasMarcaram;
  }
  const ouGols = mercado.match(/^over_under_(\d+\.\d)$/);
  if (ouGols) return (selecao === 'over') === (totalGols > Number(ouGols[1]));

  if (resultado.cornersHome == null || resultado.cornersAway == null) return null;
  if (mercado === 'corners_1x2') {
    const vencedor = resultado.cornersHome > resultado.cornersAway ? 'home' : resultado.cornersHome < resultado.cornersAway ? 'away' : 'draw';
    return selecao === vencedor;
  }
  const totalCorners = resultado.cornersHome + resultado.cornersAway;
  const ouCornersTotal = mercado.match(/^corners_over_under_full_time_(\d+\.\d)$/);
  if (ouCornersTotal) return (selecao === 'over') === (totalCorners > Number(ouCornersTotal[1]));
  const ouCornersTime1 = mercado.match(/^corners_over_under_team_1_(\d+\.\d)$/);
  if (ouCornersTime1) return (selecao === 'over') === (resultado.cornersHome > Number(ouCornersTime1[1]));
  const ouCornersTime2 = mercado.match(/^corners_over_under_team_2_(\d+\.\d)$/);
  if (ouCornersTime2) return (selecao === 'over') === (resultado.cornersAway > Number(ouCornersTime2[1]));
  return null;
}

// Escanteios reais (casa/fora) da partida finalizada -- mesmo fallback já
// usado no painel de cobertura de estatísticas (PR #328): prioriza
// match_stats (Understat/fbref/football-data.co.uk, 5 ligas europeias +
// Brasileirão via football-data.co.uk), cai pra match_stats_fotmob (FotMob,
// cobertura mais ampla) quando a primeira fonte não tem a partida.
async function buscarCornersReais(matchId, homeTeamId, awayTeamId) {
  const [{ data: ms }, { data: msf }] = await Promise.all([
    supabase.from('match_stats').select('team_id, corners').eq('match_id', matchId),
    supabase.from('match_stats_fotmob').select('team_id, corners').eq('match_id', matchId),
  ]);
  const cornersDoTime = (teamId) => {
    const doMs = ms?.find((r) => r.team_id === teamId)?.corners;
    if (doMs != null) return doMs;
    return msf?.find((r) => r.team_id === teamId)?.corners ?? null;
  };
  return { cornersHome: cornersDoTime(homeTeamId), cornersAway: cornersDoTime(awayTeamId) };
}

const LINHAS_OU_GOLS = [0.5, 1.5, 2.5, 3.5, 4.5];
const LINHAS_HANDICAP = [-1.5, -1, -0.5, 0, 0.5, 1, 1.5];
const LINHAS_OU_CORNERS = [7.5, 8.5, 9.5, 10.5, 11.5];
const N_PLACARES_EXATOS = 12;
// Tetos só da GRADE visual (matriz de mercados usa o teto padrão, mais alto,
// pra não perder precisão nas caudas -- ver GradeMatriz mais abaixo).
const MAX_GOLS_GRADE = 7;
const MAX_CORNERS_GRADE = 14;

function Escudo({ url, tamanho = 20 }) {
  return url
    ? <img src={url} alt="" className="object-contain shrink-0" style={{ width: tamanho, height: tamanho }} />
    : <Shield size={tamanho * 0.8} className="text-slate-700 shrink-0" />;
}

function fmtPct(v) { return v == null ? '—' : `${(v * 100).toFixed(1)}%`; }
function fmtNum(v, casas = 3) { return v == null ? '—' : Number(v).toFixed(casas); }

function CardParametro({ label, valor }) {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg p-3">
      <div className="text-[10px] text-slate-500 uppercase tracking-wider">{label}</div>
      <div className="text-lg font-mono text-slate-100 mt-0.5">{valor}</div>
    </div>
  );
}

function Secao({ titulo, icone: Icone, children }) {
  return (
    <div className="bg-slate-800 border border-slate-700 rounded-2xl p-5">
      <h3 className="text-sm font-bold text-slate-300 uppercase tracking-wider mb-3 flex items-center gap-2">
        {Icone && <Icone size={15} className="text-emerald-400" />} {titulo}
      </h3>
      {children}
    </div>
  );
}

// Grade de calor da matriz conjunta (placar ou escanteios casa×fora) --
// mesma matriz que já alimenta os mercados acima (1X2/placar exato ou
// corners_1X2/faixas), só que mostrada célula a célula em vez de agregada.
function GradeMatriz({ titulo, matriz, rotuloCasa, rotuloFora }) {
  if (!matriz?.length) return null;
  const n = matriz.length;
  const max = Math.max(...matriz.flat());
  return (
    <Secao titulo={titulo} icone={Target}>
      <div className="overflow-x-auto">
        <table className="text-[11px] font-mono border-separate" style={{ borderSpacing: 2 }}>
          <thead>
            <tr>
              <th className="p-1 text-slate-600 text-left align-bottom">
                <div className="leading-tight">{rotuloCasa}<br />↓ / {rotuloFora} →</div>
              </th>
              {Array.from({ length: n }, (_, j) => (
                <th key={j} className="p-1 text-slate-500 font-normal">{j}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {matriz.map((linha, i) => (
              <tr key={i}>
                <td className="p-1 text-slate-500 text-right">{i}</td>
                {linha.map((p, j) => (
                  <td
                    key={j}
                    className="p-1 text-center rounded text-slate-200"
                    style={{ background: max > 0 ? `rgba(16,185,129,${(p / max) * 0.7})` : undefined }}
                    title={`${rotuloCasa}=${i}, ${rotuloFora}=${j}: ${(p * 100).toFixed(2)}%`}
                  >
                    {(p * 100).toFixed(1)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Secao>
  );
}

function LinhaBinaria({ rotulo, over, under, rotuloOver = 'Over', rotuloUnder = 'Under' }) {
  return (
    <div className="flex items-center justify-between py-1.5 border-b border-slate-800 last:border-0 text-sm">
      <span className="text-slate-400 w-20 shrink-0">{rotulo}</span>
      <div className="flex gap-4 font-mono">
        <span className="text-emerald-400 w-24 text-right">{rotuloOver} {fmtPct(over)}</span>
        <span className="text-orange-400 w-24 text-right">{rotuloUnder} {fmtPct(under)}</span>
      </div>
    </div>
  );
}

// Busca TODAS as linhas de odds_market da partida nos mercados em escopo,
// paginado de verdade (loop de `.range()` até vir página incompleta) -- não
// é frescura: uma partida negociada por muito tempo/muitos bookmakers pode
// somar dezenas de milhares de linhas mesmo já filtrando por mercado, então
// confiar numa consulta sem `.range()` reproduziria o corte silencioso de
// 1000 linhas do PostgREST já documentado em várias partes do projeto.
//
// `apenasClosing` filtra pra `snapshot='closing'` (mesmo critério de
// api/backtest-betting.js) -- usado só pra partida já finalizada, onde faz
// sentido comparar contra a odd de FECHAMENTO real (a que valia de fato na
// hora do apito inicial), não contra qualquer captura ao longo do tempo.
async function buscarOddsPaginado(matchId, { apenasClosing = false } = {}) {
  const TAMANHO_PAGINA = 1000;
  const resultado = [];
  let pagina = 0;
  while (true) {
    let query = supabase
      .from('odds_market')
      .select('bookmaker, market, selection, odds, captured_at')
      .eq('match_id', matchId)
      .in('market', MERCADOS_EV);
    if (apenasClosing) query = query.eq('snapshot', 'closing');
    const { data, error } = await query
      .order('captured_at', { ascending: false })
      .range(pagina * TAMANHO_PAGINA, pagina * TAMANHO_PAGINA + TAMANHO_PAGINA - 1);
    if (error) throw error;
    resultado.push(...(data || []));
    if (!data || data.length < TAMANHO_PAGINA) break;
    pagina++;
  }
  return resultado;
}

export default function AnaliseAvancadaEvento() {
  const { matchId } = useParams();
  const [jogo, setJogo] = useState(null);
  const [estimativas, setEstimativas] = useState([]);
  const [modelSelecionado, setModelSelecionado] = useState(null);
  const [oddsRaw, setOddsRaw] = useState([]);
  const [snapshotSelecionado, setSnapshotSelecionado] = useState(''); // '' = mais recente de cada casa
  const [resultadoReal, setResultadoReal] = useState(null); // só preenchido pra partida finalizada
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState('');

  useEffect(() => {
    if (!supabaseAtivo) return;
    let cancelado = false;
    (async () => {
      setCarregando(true);
      setErro('');
      try {
        const { data: j, error: erroJogo } = await supabase
          .from('matches')
          .select('id, match_date, status, home_goals, away_goals, leagues(name), home:teams!matches_home_team_id_fkey(id,name,crest_url), away:teams!matches_away_team_id_fkey(id,name,crest_url)')
          .eq('id', matchId)
          .single();
        if (cancelado) return;
        if (erroJogo || !j) { setErro('Jogo não encontrado.'); setCarregando(false); return; }

        const finalizada = j.status === 'finished';
        // Agendada: todo o histórico de captured_at, pro seletor de snapshot.
        // Finalizada: só a odd de FECHAMENTO (snapshot='closing'), comparada
        // contra o resultado real -- ver Verificação de EV mais abaixo.
        // Qualquer outro status (ao vivo/adiado/cancelado): não busca odds.
        const [{ data: est, error: erroEst }, odds, corners] = await Promise.all([
          supabase
            .from('model_match_estimates')
            .select('model_name, params')
            .eq('match_id', matchId)
            .not('params', 'is', null),
          (j.status === 'scheduled' || finalizada) ? buscarOddsPaginado(matchId, { apenasClosing: finalizada }) : Promise.resolve([]),
          finalizada ? buscarCornersReais(matchId, j.home?.id, j.away?.id) : Promise.resolve(null),
        ]);
        if (cancelado) return;
        if (erroEst) { setErro(erroEst.message); setCarregando(false); return; }

        setJogo(j);
        setOddsRaw(odds);
        setSnapshotSelecionado('');
        setResultadoReal(finalizada ? { golsHome: j.home_goals, golsAway: j.away_goals, ...corners } : null);

        const validas = (est || []).filter((e) => lerParametrosPartida(e.params) != null);
        setEstimativas(validas);
        setModelSelecionado(validas[0]?.model_name ?? null);
        setCarregando(false);
      } catch (e) {
        if (!cancelado) { setErro(e.message); setCarregando(false); }
      }
    })();
    return () => { cancelado = true; };
  }, [matchId]);

  // Timestamps distintos observados em QUALQUER casa/mercado, mais recente
  // primeiro -- alimenta o seletor de snapshot. Cada casa sincroniza em
  // horários próprios (confirmado: Pinnacle e Betano nunca coincidem no
  // captured_at da mesma partida), então "escolher um snapshot" não filtra
  // por timestamp exato -- ver oddsPorBookmaker abaixo.
  const snapshotsDisponiveis = useMemo(
    () => [...new Set(oddsRaw.map((r) => r.captured_at))].sort((a, b) => new Date(b) - new Date(a)),
    [oddsRaw]
  );

  // Odd mais recente de cada (bookmaker, market, selection) NA DATA (ou
  // antes) do snapshot escolhido -- com snapshotSelecionado='' (padrão),
  // equivale a "mais recente de cada casa", sem exigir que todas as casas
  // tenham sincronizado no mesmo instante.
  const oddsPorBookmaker = useMemo(() => {
    const limite = snapshotSelecionado ? new Date(snapshotSelecionado).getTime() : Infinity;
    const porBookmaker = {};
    for (const r of oddsRaw) {
      if (new Date(r.captured_at).getTime() > limite) continue;
      if (!porBookmaker[r.bookmaker]) porBookmaker[r.bookmaker] = {};
      const chave = `${r.market}|${r.selection}`;
      // oddsRaw já vem ordenado captured_at desc -- a primeira ocorrência
      // dentro do limite é a mais recente até aquele ponto.
      if (!(chave in porBookmaker[r.bookmaker])) porBookmaker[r.bookmaker][chave] = r.odds;
    }
    return porBookmaker;
  }, [oddsRaw, snapshotSelecionado]);

  const estimativaAtiva = useMemo(
    () => estimativas.find((e) => e.model_name === modelSelecionado) || null,
    [estimativas, modelSelecionado]
  );

  const parametros = useMemo(
    () => (estimativaAtiva ? lerParametrosPartida(estimativaAtiva.params) : null),
    [estimativaAtiva]
  );

  const mercadosGols = useMemo(() => {
    if (!parametros) return null;
    const matriz = matrizPlacares(parametros.lambdaHome, parametros.lambdaAway, parametros.rho);
    return mercadosDeGols(matriz, { linhasOverUnder: LINHAS_OU_GOLS, linhasHandicap: LINHAS_HANDICAP });
  }, [parametros]);

  const mercadosCorners = useMemo(() => {
    if (!parametros?.cornersLambdaTotal || !parametros?.cornersDispR) return null;
    return mercadosDeEscanteios(
      parametros.cornersLambdaTotal, parametros.cornersDispR,
      parametros.cornersAlpha || 1, parametros.cornersBeta || 1,
      { linhasTotais: LINHAS_OU_CORNERS }
    );
  }, [parametros]);

  // Verificação de EV vs. cada mercado salvo (odds_market) -- útil tanto ANTES
  // (partida agendada, decisão de aposta de verdade) quanto DEPOIS (partida
  // finalizada, conferência de erros/acertos contra o resultado real) do
  // jogo. Pra cada casa de apostas com odds salvas (no snapshot escolhido, ou
  // a odd de fechamento se finalizada) nos mercados que o modelo também
  // precifica (gols E escanteios), devigamos (Odds Ratio) e comparamos com a
  // probabilidade do modelo -- edge = p_modelo - p_mercado devigada, EV usa a
  // odd REAL (não devigada, é o que se paga de fato), stake sugerida em
  // Kelly 1/4 (mesmo padrão de api/backtest-betting.js/SimulacaoCarteira.jsx).
  // Em partida finalizada, cada linha também ganha `acertou` (a seleção bateu
  // com o resultado real?) e `retorno` (lucro/prejuízo em % da banca SE a
  // stake de Kelly 25% sugerida tivesse sido apostada de verdade).
  const finalizada = jogo?.status === 'finished';
  const verificacaoEV = useMemo(() => {
    if (!(jogo?.status === 'scheduled' || finalizada) || (!mercadosGols && !mercadosCorners)) return [];
    const comparaveis = mercadosComparaveis(mercadosGols, mercadosCorners);
    const linhas = [];
    for (const [bookmaker, oddsChave] of Object.entries(oddsPorBookmaker)) {
      for (const [mercado, probsModelo] of Object.entries(comparaveis)) {
        if (!probsModelo) continue;
        const selecoes = Object.keys(probsModelo);
        const oddsSelecoes = {};
        for (const s of selecoes) {
          const odd = oddsChave[`${mercado}|${s}`];
          if (odd != null) oddsSelecoes[s] = odd;
        }
        if (Object.keys(oddsSelecoes).length !== selecoes.length) continue; // precisa de TODAS as pernas do mercado pra devigar
        const devigado = devigarOddsRatio(oddsSelecoes);
        for (const s of selecoes) {
          const pModelo = probsModelo[s];
          const oddReal = oddsSelecoes[s];
          const pMercado = devigado[s];
          const edge = pModelo - pMercado;
          const ev = pModelo * oddReal - 1;
          const kelly25 = stakeKelly25(pModelo, oddReal);
          const acertou = finalizada && resultadoReal ? avaliarSelecao(mercado, s, resultadoReal) : null;
          const retorno = finalizada && acertou != null && kelly25 > 0
            ? (acertou ? kelly25 * (oddReal - 1) : -kelly25)
            : null;
          linhas.push({ bookmaker, mercado, selecao: s, oddReal, pModelo, pMercado, edge, ev, kelly25, acertou, retorno });
        }
      }
    }
    return linhas.sort((a, b) => b.edge - a.edge);
  }, [jogo?.status, finalizada, mercadosGols, mercadosCorners, oddsPorBookmaker, resultadoReal]);

  // Mesma matriz que já alimenta 1X2/placar exato acima, só que truncada
  // pra uma grade menor (0-7) que cabe na tela célula a célula.
  const gradePlacar = useMemo(() => {
    if (!parametros) return null;
    return matrizPlacares(parametros.lambdaHome, parametros.lambdaAway, parametros.rho, MAX_GOLS_GRADE);
  }, [parametros]);

  // Mesma decomposição (NB do total × Beta-Binomial do split) de
  // `mercadosCorners` acima, só que devolvendo a matriz casa×fora inteira
  // em vez de agregada em over/under -- truncada em 0-14 pela SOMA
  // (casa+fora <= 14), não por lado independente, então o canto superior
  // direito da grade fica zerado (não é bug: escanteios totais raramente
  // passam de ~15-16 na prática).
  const gradeCorners = useMemo(() => {
    if (!parametros?.cornersLambdaTotal || !parametros?.cornersDispR) return null;
    return distribuicaoConjuntaEscanteios(
      parametros.cornersLambdaTotal, parametros.cornersDispR,
      parametros.cornersAlpha || 1, parametros.cornersBeta || 1,
      MAX_CORNERS_GRADE
    );
  }, [parametros]);

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
      <div className="flex items-center justify-between mb-4">
        <Link to="/eventos" className="flex items-center gap-1.5 text-slate-400 hover:text-slate-200 text-sm w-fit">
          <ArrowLeft size={16} /> Voltar
        </Link>
        <Link
          to={`/historico/${jogo.id}`}
          className="flex items-center gap-1.5 bg-slate-700/40 hover:bg-slate-700 text-slate-300 text-xs font-bold px-3 py-1.5 rounded-lg transition-colors"
        >
          Forma recente & confronto direto
        </Link>
      </div>

      <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 mb-4">
        <p className="text-center text-xs text-slate-500 uppercase tracking-wider mb-1 flex items-center justify-center gap-1.5">
          <FlaskConical size={12} className="text-emerald-400" /> Análise Avançada — Modelo Misto
        </p>
        <p className="text-center text-xs text-slate-600 mb-3">
          {jogo.leagues?.name || 'Confronto'}
          {jogo.match_date && ` · ${new Date(jogo.match_date).toLocaleString('pt-BR', { dateStyle: 'long', timeStyle: 'short' })}`}
        </p>
        <div className="flex items-center justify-center gap-4">
          <div className="flex items-center gap-2">
            <Escudo url={jogo.home?.crest_url} tamanho={32} />
            <span className="font-bold text-slate-100">{jogo.home?.name}</span>
          </div>
          <span className="text-slate-500 font-mono">
            {jogo.status === 'finished' ? `${jogo.home_goals}-${jogo.away_goals}` : 'vs'}
          </span>
          <div className="flex items-center gap-2">
            <span className="font-bold text-slate-100">{jogo.away?.name}</span>
            <Escudo url={jogo.away?.crest_url} tamanho={32} />
          </div>
        </div>
      </div>

      {estimativas.length === 0 ? (
        <div className="bg-slate-800 border border-slate-700 rounded-2xl p-8 text-center">
          <FlaskConical className="text-slate-600 mx-auto mb-3" size={32} />
          <p className="text-slate-300 font-semibold">Nenhuma estimativa do modelo misto para esta partida ainda.</p>
          <p className="text-slate-500 text-sm mt-2 max-w-md mx-auto">
            O modelo misto (λ estimado por ML + Dixon-Coles/Binomial Negativa) só cobre partidas
            que passaram pelo pipeline de treino (<code className="text-slate-400">scripts/treinar_modelo_hibrido.py</code> ou
            uma config de Treino Customizado com algoritmo <code className="text-slate-400">hibrido_parametrico</code>).
            Essa partida pode estar fora do escopo de liga/temporada treinado, ou o treino ainda não rodou.
          </p>
        </div>
      ) : (
        <>
          {estimativas.length > 1 && (
            <div className="flex gap-1 mb-4 bg-slate-800 border border-slate-700 rounded-2xl p-1.5 w-fit flex-wrap">
              {estimativas.map((e) => (
                <button
                  key={e.model_name}
                  onClick={() => setModelSelecionado(e.model_name)}
                  className={`px-3.5 py-2 rounded-xl text-sm font-bold transition-colors ${
                    modelSelecionado === e.model_name ? 'bg-emerald-500/20 text-emerald-400' : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  {e.model_name}
                </button>
              ))}
            </div>
          )}

          <Secao titulo="Parâmetros do modelo (direto do banco)" icone={FlaskConical}>
            <p className="text-[11px] text-slate-500 mb-3">
              Modelo: <span className="text-slate-300 font-mono">{estimativaAtiva?.model_name}</span> — leitura de
              {' '}<code className="text-slate-400">model_match_estimates.params</code>, sem edição manual.
            </p>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <CardParametro label="xG estimado — mandante" valor={fmtNum(parametros?.lambdaHome, 2)} />
              <CardParametro label="xG estimado — visitante" valor={fmtNum(parametros?.lambdaAway, 2)} />
              <CardParametro label="ρ (Dixon-Coles)" valor={fmtNum(parametros?.rho, 4)} />
              {parametros?.cornersLambdaTotal != null && (
                <CardParametro label="λ escanteios (total)" valor={fmtNum(parametros.cornersLambdaTotal, 2)} />
              )}
              {parametros?.cornersDispR != null && (
                <CardParametro label="Dispersão r (escanteios)" valor={fmtNum(parametros.cornersDispR, 1)} />
              )}
              {parametros?.cornersAlpha != null && (
                <CardParametro label="α (split casa/fora)" valor={fmtNum(parametros.cornersAlpha, 2)} />
              )}
              {parametros?.cornersBeta != null && (
                <CardParametro label="β (split casa/fora)" valor={fmtNum(parametros.cornersBeta, 2)} />
              )}
            </div>
          </Secao>

          {mercadosGols && (
            <>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mt-4">
                <div className="bg-slate-900 border-t-4 border-emerald-500 border border-slate-700 rounded-xl p-5 flex flex-col items-center text-center">
                  <span className="text-slate-400 text-[10px] uppercase tracking-wider mb-2 line-clamp-1">{jogo.home?.name}</span>
                  <span className="text-2xl font-black text-white">{fmtPct(mercadosGols['1X2'].home)}</span>
                </div>
                <div className="bg-slate-900 border-t-4 border-slate-500 border border-slate-700 rounded-xl p-5 flex flex-col items-center text-center">
                  <span className="text-slate-400 text-[10px] uppercase tracking-wider mb-2">Empate</span>
                  <span className="text-2xl font-black text-white">{fmtPct(mercadosGols['1X2'].draw)}</span>
                </div>
                <div className="bg-slate-900 border-t-4 border-orange-500 border border-slate-700 rounded-xl p-5 flex flex-col items-center text-center">
                  <span className="text-slate-400 text-[10px] uppercase tracking-wider mb-2 line-clamp-1">{jogo.away?.name}</span>
                  <span className="text-2xl font-black text-white">{fmtPct(mercadosGols['1X2'].away)}</span>
                </div>
              </div>

              {(jogo.status === 'scheduled' || finalizada) && (
                <div className="mt-4">
                  <Secao
                    titulo={finalizada ? 'Verificação de EV vs. mercado (fechamento) — erros e acertos' : 'Verificação de EV vs. mercado'}
                    icone={Scale}
                  >
                    {finalizada && (
                      <p className="text-[11px] text-slate-500 mb-3">
                        Comparação contra a odd de FECHAMENTO real (<code className="text-slate-400">snapshot='closing'</code>,
                        a que valia na hora do apito inicial) e o resultado real da partida — não é mais uma decisão
                        de aposta, é conferência do que teria acontecido.
                      </p>
                    )}
                    {!finalizada && snapshotsDisponiveis.length > 0 && (
                      <div className="flex items-center gap-2 mb-3">
                        <label className="text-[10px] text-slate-500 uppercase font-bold shrink-0">Snapshot</label>
                        <select
                          value={snapshotSelecionado}
                          onChange={(e) => setSnapshotSelecionado(e.target.value)}
                          className="bg-slate-900 border border-slate-700 rounded-md px-2 py-1.5 text-xs text-slate-200"
                        >
                          <option value="">Mais recente de cada casa</option>
                          {snapshotsDisponiveis.map((ts) => (
                            <option key={ts} value={ts}>{formatarSnapshot(ts)}</option>
                          ))}
                        </select>
                        <span className="text-[10px] text-slate-600">
                          "Mais recente de cada casa" não exige sincronismo entre bookmakers — cada um sincroniza no
                          seu próprio horário; um snapshot escolhido mostra o que estava salvo até aquele instante.
                        </span>
                      </div>
                    )}
                    {verificacaoEV.length === 0 ? (
                      <p className="text-sm text-slate-600">
                        {finalizada
                          ? 'Nenhuma odd de fechamento salva nos mercados que o modelo precifica (1X2, Over/Under de gols, Ambas Marcam, 1X2/Over-Under de escanteios) pra esta partida.'
                          : <>Nenhuma casa de apostas com odds salvas nos mercados que o modelo precifica (1X2, Over/Under
                              de gols, Ambas Marcam, 1X2/Over-Under de escanteios) pra esta partida
                              {snapshotSelecionado ? ' nesse snapshot' : ' ainda'} — importe odds pela tela de rodada,
                              {snapshotSelecionado ? ' escolha outro snapshot' : ' aguarde o próximo sync'}.</>}
                      </p>
                    ) : (
                      <>
                        {finalizada && (() => {
                          const avaliadas = verificacaoEV.filter((l) => l.edge > 0.02 && l.acertou != null);
                          const acertos = avaliadas.filter((l) => l.acertou).length;
                          return avaliadas.length > 0 ? (
                            <p className="text-sm mb-3">
                              Entre as seleções com edge {'>'} 2pp (as que teriam recebido stake de Kelly):{' '}
                              <span className="font-bold text-emerald-400">{acertos} acerto{acertos !== 1 ? 's' : ''}</span>
                              {' '}·{' '}
                              <span className="font-bold text-red-400">{avaliadas.length - acertos} erro{avaliadas.length - acertos !== 1 ? 's' : ''}</span>
                            </p>
                          ) : null;
                        })()}
                        <div className="overflow-x-auto">
                          <table className="w-full text-sm">
                            <thead>
                              <tr className="text-slate-500 text-xs uppercase">
                                <th className="text-left pb-2">Casa</th>
                                <th className="text-left pb-2">Mercado</th>
                                <th className="text-right pb-2">Odd real</th>
                                <th className="text-right pb-2">Prob. modelo</th>
                                <th className="text-right pb-2">Prob. mercado (devig)</th>
                                <th className="text-right pb-2">Edge</th>
                                <th className="text-right pb-2">EV</th>
                                <th className="text-right pb-2">Stake Kelly 25%</th>
                                {finalizada && <th className="text-right pb-2">Resultado</th>}
                                {finalizada && <th className="text-right pb-2">Retorno</th>}
                              </tr>
                            </thead>
                            <tbody>
                              {verificacaoEV.map((l, i) => (
                                <tr key={i} className="border-t border-slate-800">
                                  <td className="py-1.5 text-slate-300 capitalize">{l.bookmaker.replace(/_/g, ' ')}</td>
                                  <td className="py-1.5 text-slate-400 text-xs">{rotuloMercado(l.mercado)} · <span className="uppercase">{l.selecao}</span></td>
                                  <td className="py-1.5 text-right font-mono text-slate-200">{l.oddReal.toFixed(2)}</td>
                                  <td className="py-1.5 text-right font-mono text-slate-300">{toPct(l.pModelo)}</td>
                                  <td className="py-1.5 text-right font-mono text-slate-500">{toPct(l.pMercado)}</td>
                                  <td className={`py-1.5 text-right font-mono font-bold ${l.edge > 0.02 ? 'text-emerald-400' : l.edge < -0.02 ? 'text-red-400' : 'text-slate-400'}`}>
                                    {l.edge > 0 ? '+' : ''}{(l.edge * 100).toFixed(1)}pp
                                  </td>
                                  <td className={`py-1.5 text-right font-mono ${l.ev > 0 ? 'text-emerald-400' : 'text-slate-500'}`}>
                                    {l.ev > 0 ? '+' : ''}{(l.ev * 100).toFixed(1)}%
                                  </td>
                                  <td className="py-1.5 text-right font-mono text-slate-300">{l.kelly25 > 0 ? `${(l.kelly25 * 100).toFixed(2)}%` : '—'}</td>
                                  {finalizada && (
                                    <td className="py-1.5 text-right font-bold">
                                      {l.acertou == null ? <span className="text-slate-700">—</span>
                                        : l.acertou ? <span className="text-emerald-400">✓ Acertou</span>
                                        : <span className="text-red-400">✗ Errou</span>}
                                    </td>
                                  )}
                                  {finalizada && (
                                    <td className={`py-1.5 text-right font-mono ${l.retorno == null ? 'text-slate-700' : l.retorno > 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                                      {l.retorno == null ? '—' : `${l.retorno > 0 ? '+' : ''}${(l.retorno * 100).toFixed(2)}%`}
                                    </td>
                                  )}
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                        <p className="text-[11px] text-slate-600 mt-3 leading-relaxed">
                          Edge = probabilidade do modelo − probabilidade do mercado (devigada, Odds Ratio). EV usa a
                          odd REAL (com margem da casa), não a devigada. Stake Kelly 25% = ¼ do critério de Kelly
                          sobre a odd real, com teto de 25% da banca por aposta — mesma matemática de
                          `api/backtest-betting.js`.
                          {finalizada && ' Retorno = lucro/prejuízo em % da banca SE a stake de Kelly 25% sugerida tivesse sido apostada de verdade (só calculado quando havia stake > 0).'}
                          {' '}<strong className="text-slate-500">Contexto importante antes de
                          usar isso pra apostar de verdade:</strong> a avaliação pareada do modelo misto contra o
                          mercado (Pinnacle devigada) mostrou o mercado batendo o modelo em log-loss nos 3 mercados
                          testados (1X2, Over/Under 2.5, BTTS), com IC 95% não cruzando zero — um edge positivo
                          isolado aqui (ou um punhado de acertos numa partida só) pode ser ruído de amostra, não
                          vantagem real comprovada.
                        </p>
                      </>
                    )}
                  </Secao>
                </div>
              )}

              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
                <Secao titulo="Dupla chance & BTTS" icone={Percent}>
                  <div className="grid grid-cols-3 gap-2 mb-3">
                    <CardParametro label="1X" valor={fmtPct(mercadosGols.dupla_chance['1X'])} />
                    <CardParametro label="12" valor={fmtPct(mercadosGols.dupla_chance['12'])} />
                    <CardParametro label="X2" valor={fmtPct(mercadosGols.dupla_chance.X2)} />
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <CardParametro label="Ambas marcam — Sim" valor={fmtPct(mercadosGols.btts.yes)} />
                    <CardParametro label="Ambas marcam — Não" valor={fmtPct(mercadosGols.btts.no)} />
                  </div>
                </Secao>

                <Secao titulo="Faixa de gols" icone={Target}>
                  <div className="grid grid-cols-2 gap-2">
                    {Object.entries(mercadosGols.faixa_gols).map(([faixa, p]) => (
                      <CardParametro key={faixa} label={`${faixa} gols`} valor={fmtPct(p)} />
                    ))}
                  </div>
                </Secao>
              </div>

              <Secao titulo="Over / Under gols" icone={TrendingUp}>
                {LINHAS_OU_GOLS.map((linha) => {
                  const m = mercadosGols[`over_under_${rotuloLinha(linha)}`];
                  return <LinhaBinaria key={linha} rotulo={rotuloLinha(linha)} over={m?.over} under={m?.under} />;
                })}
              </Secao>

              <Secao titulo="Handicap asiático (mandante)" icone={Target}>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-slate-500 text-xs uppercase">
                        <th className="text-left pb-2">Linha</th>
                        <th className="text-right pb-2">{jogo.home?.name}</th>
                        <th className="text-right pb-2">Push</th>
                        <th className="text-right pb-2">{jogo.away?.name}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {LINHAS_HANDICAP.map((linha) => {
                        const h = mercadosGols[`handicap_${rotuloLinha(linha)}`];
                        return (
                          <tr key={linha} className="border-t border-slate-800">
                            <td className="py-1.5 text-slate-300 font-mono">{linha > 0 ? `+${rotuloLinha(linha)}` : rotuloLinha(linha)}</td>
                            <td className="py-1.5 text-right font-mono text-emerald-400">{fmtPct(h?.home)}</td>
                            <td className="py-1.5 text-right font-mono text-slate-500">{h?.push ? fmtPct(h.push) : '—'}</td>
                            <td className="py-1.5 text-right font-mono text-orange-400">{fmtPct(h?.away)}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </Secao>

              <Secao titulo="Placar exato (mais prováveis)" icone={Target}>
                <div className="grid grid-cols-3 sm:grid-cols-4 gap-2">
                  {Object.entries(mercadosGols.placar_exato).slice(0, N_PLACARES_EXATOS).map(([placar, p]) => (
                    <CardParametro key={placar} label={placar} valor={fmtPct(p)} />
                  ))}
                </div>
              </Secao>

              <GradeMatriz
                titulo={`Matriz de placar (${jogo.home?.name || 'casa'} × ${jogo.away?.name || 'fora'}, 0-${MAX_GOLS_GRADE})`}
                matriz={gradePlacar}
                rotuloCasa={jogo.home?.name?.slice(0, 12) || 'Casa'}
                rotuloFora={jogo.away?.name?.slice(0, 12) || 'Fora'}
              />
            </>
          )}

          {mercadosCorners && (
            <>
              <Secao titulo="Escanteios — Over / Under (total)" icone={TrendingUp}>
                {LINHAS_OU_CORNERS.map((linha) => {
                  const m = mercadosCorners[`corners_over_under_${rotuloLinha(linha)}`];
                  return <LinhaBinaria key={linha} rotulo={rotuloLinha(linha)} over={m?.over} under={m?.under} />;
                })}
              </Secao>

              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
                <Secao titulo="Escanteios — faixa & 1X2" icone={Target}>
                  <div className="grid grid-cols-2 gap-2 mb-3">
                    {Object.entries(mercadosCorners.faixa_corners).map(([faixa, p]) => (
                      <CardParametro key={faixa} label={`${faixa} escanteios`} valor={fmtPct(p)} />
                    ))}
                  </div>
                  <div className="grid grid-cols-3 gap-2">
                    <CardParametro label="Mais escanteios: casa" valor={fmtPct(mercadosCorners.corners_1X2.home)} />
                    <CardParametro label="Empate" valor={fmtPct(mercadosCorners.corners_1X2.draw)} />
                    <CardParametro label="Mais escanteios: fora" valor={fmtPct(mercadosCorners.corners_1X2.away)} />
                  </div>
                </Secao>

                <Secao titulo="Escanteios por time" icone={Percent}>
                  {[3.5, 4.5, 5.5, 6.5].map((linha) => (
                    <div key={linha} className="mb-3 last:mb-0">
                      <p className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">Linha {rotuloLinha(linha)}</p>
                      <LinhaBinaria
                        rotulo={jogo.home?.name?.slice(0, 10) || 'Casa'}
                        over={mercadosCorners[`corners_home_over_under_${rotuloLinha(linha)}`]?.over}
                        under={mercadosCorners[`corners_home_over_under_${rotuloLinha(linha)}`]?.under}
                      />
                      <LinhaBinaria
                        rotulo={jogo.away?.name?.slice(0, 10) || 'Fora'}
                        over={mercadosCorners[`corners_away_over_under_${rotuloLinha(linha)}`]?.over}
                        under={mercadosCorners[`corners_away_over_under_${rotuloLinha(linha)}`]?.under}
                      />
                    </div>
                  ))}
                </Secao>
              </div>

              <GradeMatriz
                titulo={`Matriz de escanteios (${jogo.home?.name || 'casa'} × ${jogo.away?.name || 'fora'}, 0-${MAX_CORNERS_GRADE})`}
                matriz={gradeCorners}
                rotuloCasa={jogo.home?.name?.slice(0, 12) || 'Casa'}
                rotuloFora={jogo.away?.name?.slice(0, 12) || 'Fora'}
              />
            </>
          )}
        </>
      )}
    </div>
  );
}
