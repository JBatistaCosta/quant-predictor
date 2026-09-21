// src/pages/ResumoValorApostas.jsx
// Resumo de todas as sugestões de valor (edge modelo-vs-mercado positivo) de
// TODOS os modelos, filtrável por modelo/liga/mercado, cobrindo toda a janela
// de produção (partidas já finalizadas E ainda pendentes) -- não só o
// histórico resolvido que api/backtest-betting.js normalmente exige pra
// simular ROI. Pedido do usuário: uma tabela pra conferir manualmente numa
// planilha as sugestões que cada modelo fez, sem precisar decorar/rodar nada.
// Reaproveita a MESMA lógica de edge/devig de api/backtest-betting.js
// (?formato=candidatas), só sem a agregação em grupos/bootstrap.
import React, { useState, useEffect, useMemo } from 'react';
import { Target, AlertTriangle, Loader2, Download, Search, ChevronLeft, ChevronRight, Wallet, Info } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';
import { apiUrl } from '../utils/apiUrl';
import { toPct } from '../utils/format';

// Identifica em `carteira_cartoes_rf_historico` a carteira restrita
// combinada (4.5+5.5, Série B + bet365/betano, mesma banca) -- ver
// SUB_FAIXA_CARTEIRA em scripts/analisar_cartoes_liga_sinal.py e
// CONTEXTO_PROJETO.md ("ACHADO REFORÇADO" de cartões).
const SUB_FAIXA_CARTEIRA = 'rf_confiavel_serieB_bet365betano_combinado';

// Carteira restrita de catboost_v9 (achado 21/09): mandante, 1X2, odd
// [1.30,2.50), edge [5%,10%) -- única célula confiavel=true da matriz de
// confiabilidade EV pra esse modelo, positiva nas 6 ligas do benchmarking
// (diferente da carteira de cartões, esta NÃO é restrita a uma liga só,
// por isso tem filtro de liga na própria aba). Ver SUB_FAIXA_CARTEIRA em
// scripts/analisar_catboost_v9_liga_sinal.py e model_betting_strategy
// (mercado='1x2_mandante').
const SUB_FAIXA_CARTEIRA_CATBOOST_V9 = 'catboost_v9_odd1.30-2.50_edge5-10';

// Cartões/faltas por partida vêm de `match_disciplina` (migration
// 20260920180000_create_match_disciplina.sql, regerada por
// public.derivar_disciplina()) -- não mais calculados ao vivo aqui.
// ACHADO CRÍTICO (20/09): boa parte da carteira depende do fallback
// `match_stats_fotmob` (match_events está vazio pra maioria da janela
// histórica de Série B), e esse fallback bate 0-0 numa fração muito alta
// dos casos sem match_events (~84% na Série B recente) -- confirmado
// manualmente contra o FotMob ao vivo (match_id 110163: 3 cartões reais,
// banco mostrava 0-0). `fonte_cartoes='fallback_suspeito'` marca
// exatamente esses casos -- NÃO tratar como "0 cartões confirmado".
// model_betting_strategy foi rebaixado pra confianca='em_revisao' por
// causa disso -- ver notas na tabela.
const FONTE_CARTOES_ROTULO = {
  match_events: { texto: 'confiável', cor: 'text-emerald-400' },
  fallback_fotmob: { texto: 'fallback', cor: 'text-amber-400' },
  fallback_suspeito: { texto: 'SUSPEITO', cor: 'text-red-400' },
};

const MERCADO_ROTULO = { '1X2': '1X2', 'over_under_2.5': 'Over/Under 2.5 gols', 'corners_over_under_9.5': 'Over/Under 9.5 escanteios' };
const SELECAO_ROTULO = { home: 'Mandante', draw: 'Empate', away: 'Visitante', over: 'Over', under: 'Under' };
// `casa_aposta` (api/backtest-betting.js) -- de qual fonte a odd real dessa
// sugestão veio. "media_mercado" é uma média sintética entre várias casas
// (não uma casa real específica, ver `comFonte`/`normalizarOddsBenchmarking`
// no backend); as demais são uma casa real de referência.
const CASA_APOSTA_ROTULO = { pinnacle: 'Pinnacle', betano: 'Betano', media_mercado: 'Média do mercado' };
const rotuloCasaAposta = (c) => CASA_APOSTA_ROTULO[c] || c || '—';
const MODELO_ROTULO = {
  mercado_pinnacle_devigado: 'Mercado (Pinnacle devigada)',
  pricing_pipeline_v1: 'Pricing Pipeline (melhor fonte disponível)',
  pricing_pipeline_previsto_v1: 'Pricing Pipeline (XI previsto)',
  pricing_pipeline_real_v1: 'Pricing Pipeline (escalação real)',
};
const rotuloModelo = (nome) => MODELO_ROTULO[nome] || nome;
const rotuloMercado = (m) => MERCADO_ROTULO[m] || m;
const rotuloSelecao = (s) => SELECAO_ROTULO[s] || s;

const STATUS_ROTULO = {
  pendente: { texto: 'Pendente', cor: 'text-slate-400' },
  ganhou: { texto: 'Ganhou', cor: 'text-emerald-400' },
  perdeu: { texto: 'Perdeu', cor: 'text-red-400' },
};

// Backend já pagina TUDO internamente (`buscarTudoPaginado`/`buscarTudoPaginadoIn`
// em api/backtest-betting.js contornam o corte silencioso de 1000 linhas do
// PostgREST) -- essa paginação aqui é só de EXIBIÇÃO, pra não renderizar uma
// tabela HTML gigante quando `candidatas` vem com milhares de linhas.
const LINHAS_POR_PAGINA = 100;

function statusDaLinha(c) {
  if (c.status !== 'finalizada') return 'pendente';
  return c.venceu ? 'ganhou' : 'perdeu';
}

// Mesmo padrão de export CSV genérico já usado em XiModeloStats.jsx/
// AnaliseAvancadaEvento.jsx (Blob + BOM UTF-8, sem lib externa).
function exportarCSV(linhas, colunas, nomeArquivo) {
  const csvEscape = (v) => `"${String(v ?? '').replace(/"/g, '""')}"`;
  const cabecalho = colunas.map((c) => csvEscape(c.header)).join(',');
  const corpo = linhas.map((l) => colunas.map((c) => csvEscape(c.get(l))).join(','));
  const csv = [cabecalho, ...corpo].join('\n');
  const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = nomeArquivo;
  a.click();
  URL.revokeObjectURL(url);
}

export default function ResumoValorApostas() {
  const [aba, setAba] = useState('sugestoes'); // 'sugestoes' | 'carteira' | 'carteira_catboost'

  const [carregandoOpcoes, setCarregandoOpcoes] = useState(true);
  const [opcoesModelos, setOpcoesModelos] = useState([]);
  const [opcoesMercados, setOpcoesMercados] = useState([]);
  const [opcoesLigas, setOpcoesLigas] = useState([]);
  const [ligasPorId, setLigasPorId] = useState({});
  const [timesPorId, setTimesPorId] = useState({});

  const [carregandoCarteira, setCarregandoCarteira] = useState(false);
  const [erroCarteira, setErroCarteira] = useState('');
  const [carteira, setCarteira] = useState([]);
  const [carteiraCarregada, setCarteiraCarregada] = useState(false);
  const [cartoesPorMatch, setCartoesPorMatch] = useState({}); // match_id -> {amareloHome, amareloAway, vermelhoHome, vermelhoAway, faltasHome, faltasAway, fonteHome, fonteAway}

  const [carregandoCarteiraCB, setCarregandoCarteiraCB] = useState(false);
  const [erroCarteiraCB, setErroCarteiraCB] = useState('');
  const [carteiraCB, setCarteiraCB] = useState([]);
  const [carteiraCBCarregada, setCarteiraCBCarregada] = useState(false);
  const [filtroLigaCarteiraCB, setFiltroLigaCarteiraCB] = useState('');

  const [filtroModelo, setFiltroModelo] = useState('');
  const [filtroMercado, setFiltroMercado] = useState('');
  const [filtroLiga, setFiltroLiga] = useState('');
  const [edgeMinimo, setEdgeMinimo] = useState('0.02');
  const [dataInicio, setDataInicio] = useState('');
  const [dataFim, setDataFim] = useState('');

  const [carregando, setCarregando] = useState(false);
  const [erro, setErro] = useState('');
  const [candidatas, setCandidatas] = useState([]);
  const [buscou, setBuscou] = useState(false);
  const [paginaAtual, setPaginaAtual] = useState(0);

  // Combos válidos de modelo/mercado/liga vêm de /api/model-stats (mesma
  // fonte que ModelosStats.jsx usa pros dropdowns) -- barato (poucas dezenas
  // de grupos pré-calculados), evita hardcodar uma lista que desatualiza.
  useEffect(() => {
    (async () => {
      setCarregandoOpcoes(true);
      try {
        const [respStats, ligasResp] = await Promise.all([
          fetch(apiUrl('/api/model-stats')),
          supabaseAtivo ? supabase.from('leagues').select('id, name') : Promise.resolve({ data: [] }),
        ]);
        const dataStats = await respStats.json();
        const grupos = respStats.ok ? (dataStats.grupos || []) : [];
        setOpcoesModelos([...new Set(grupos.map(g => g.model_name))].sort());
        setOpcoesMercados([...new Set(grupos.map(g => g.market))].sort());
        setOpcoesLigas([...new Set(grupos.map(g => g.league_id))].sort((a, b) => a - b));
        const mapa = {};
        (ligasResp.data || []).forEach(l => { mapa[l.id] = l.name; });
        setLigasPorId(mapa);
      } catch {
        // Falha aqui não impede buscar sugestões -- só os dropdowns ficam
        // vazios (usuário ainda pode digitar filtro em branco = "todos").
      } finally {
        setCarregandoOpcoes(false);
      }
    })();
  }, []);

  async function buscarSugestoes() {
    // Sem liga, algumas combinações modelo+mercado (as com mais previsões
    // acumuladas) estouram o `statement timeout` do Postgres no backend --
    // confirmado em produção mesmo no endpoint antigo, sem relação com o
    // `formato=candidatas` em si (api/backtest-betting.js já filtra TODAS as
    // consultas por `liga_id` quando ele vem, cortando o volume de banco
    // inteiro pra só a liga escolhida). Exigir liga aqui evita cair nesse
    // timeout em vez de deixar o usuário descobrir só depois de esperar.
    if (!filtroLiga) {
      setErro('Escolha uma liga antes de buscar -- sem esse filtro, algumas combinações de modelo/mercado estouram o tempo limite da consulta.');
      setBuscou(false);
      return;
    }
    setCarregando(true);
    setErro('');
    setBuscou(true);
    try {
      const params = new URLSearchParams({ formato: 'candidatas' });
      if (filtroModelo) params.set('modelo', filtroModelo);
      if (filtroMercado) params.set('mercado', filtroMercado);
      if (filtroLiga) params.set('liga_id', filtroLiga);
      if (edgeMinimo !== '') params.set('edge_minimo', edgeMinimo);
      if (dataInicio) params.set('data_inicio', dataInicio);
      if (dataFim) params.set('data_fim', dataFim);

      const resp = await fetch(apiUrl(`/api/backtest-betting?${params.toString()}`));
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error?.message || 'Erro ao buscar sugestões de valor.');
      const lista = data.candidatas || [];
      setCandidatas(lista);
      setPaginaAtual(0);

      if (supabaseAtivo && lista.length > 0) {
        const idsTimes = [...new Set(lista.flatMap(c => [c.home_team_id, c.away_team_id]).filter(Boolean))];
        if (idsTimes.length > 0) {
          const { data: times } = await supabase.from('teams').select('id, name').in('id', idsTimes);
          const mapaTimes = {};
          (times || []).forEach(t => { mapaTimes[t.id] = t.name; });
          setTimesPorId(mapaTimes);
        }
      }
    } catch (e) {
      setErro(e.message);
      setCandidatas([]);
    } finally {
      setCarregando(false);
    }
  }

  const nomeTime = (id) => timesPorId[id] || (id ? `Time #${id}` : '—');
  const nomeLiga = (id) => ligasPorId[id] || `Liga #${id}`;

  // Aba "Carteira" -- ledger aposta a aposta da carteira cronológica
  // restrita de cartoes_rf (Série B + bet365/betano, O/U 4.5/5.5,
  // edge>=25%), persistida por scripts/analisar_cartoes_liga_sinal.py em
  // carteira_cartoes_rf_historico (não existe endpoint dedicado -- é
  // leitura pública direto do Supabase, mesmo padrão de outras tabelas
  // *_historico deste projeto).
  async function buscarCarteira() {
    if (!supabaseAtivo) return;
    setCarregandoCarteira(true);
    setErroCarteira('');
    try {
      const { data, error } = await supabase
        .from('carteira_cartoes_rf_historico')
        .select('*, matches(round, league_id, home_team_id, away_team_id)')
        .eq('sub_faixa', SUB_FAIXA_CARTEIRA)
        .order('ordem', { ascending: true });
      if (error) throw error;
      const linhas = data || [];
      setCarteira(linhas);
      setCarteiraCarregada(true);

      const idsTimes = [...new Set(linhas.flatMap(l => [l.matches?.home_team_id, l.matches?.away_team_id]).filter(Boolean))];
      if (idsTimes.length > 0) {
        const { data: times } = await supabase.from('teams').select('id, name').in('id', idsTimes);
        const mapaTimes = {};
        (times || []).forEach(t => { mapaTimes[t.id] = t.name; });
        setTimesPorId(prev => ({ ...prev, ...mapaTimes }));
      }

      // Cartões/faltas persistidos em match_disciplina (uma linha por
      // partida/time, já com a fonte marcada -- ver comentário no topo do
      // arquivo). Nada de agregação client-side de match_events aqui.
      const idsPartidas = [...new Set(linhas.map(l => l.match_id))];
      if (idsPartidas.length > 0) {
        const { data: disciplina } = await supabase
          .from('match_disciplina')
          .select('match_id, is_home, cartoes_amarelos, cartoes_vermelhos_equiv, faltas_cometidas, fonte_cartoes')
          .in('match_id', idsPartidas);
        const porPartida = {};
        for (const l of linhas) {
          porPartida[l.match_id] = {
            amareloHome: 0, amareloAway: 0, vermelhoHome: 0, vermelhoAway: 0,
            faltasHome: null, faltasAway: null, fonteHome: null, fonteAway: null,
          };
        }
        for (const d of (disciplina || [])) {
          const agregado = porPartida[d.match_id];
          if (!agregado) continue;
          const lado = d.is_home ? 'Home' : 'Away';
          agregado[`amarelo${lado}`] = d.cartoes_amarelos;
          agregado[`vermelho${lado}`] = d.cartoes_vermelhos_equiv;
          agregado[`faltas${lado}`] = d.faltas_cometidas;
          agregado[`fonte${lado}`] = d.fonte_cartoes;
        }
        setCartoesPorMatch(porPartida);
      }
    } catch (e) {
      setErroCarteira(e.message);
      setCarteira([]);
    } finally {
      setCarregandoCarteira(false);
    }
  }

  useEffect(() => {
    if (aba === 'carteira' && !carteiraCarregada && !carregandoCarteira) buscarCarteira();
    if (aba === 'carteira_catboost' && !carteiraCBCarregada && !carregandoCarteiraCB) buscarCarteiraCatboostV9();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [aba]);

  // Aba "Carteira catboost_v9" -- ledger aposta a aposta da carteira
  // cronológica restrita a mandante/1X2/odd 1.30-2.50/edge 5-10% (única
  // célula confiavel=true da matriz de confiabilidade EV pra esse modelo),
  // persistida por scripts/analisar_catboost_v9_liga_sinal.py em
  // carteira_catboost_v9_historico. Mesmo padrão de leitura direta do
  // Supabase que a aba de cartões já usa.
  async function buscarCarteiraCatboostV9() {
    if (!supabaseAtivo) return;
    setCarregandoCarteiraCB(true);
    setErroCarteiraCB('');
    try {
      const { data, error } = await supabase
        .from('carteira_catboost_v9_historico')
        .select('*, matches(round, league_id, home_team_id, away_team_id)')
        .eq('sub_faixa', SUB_FAIXA_CARTEIRA_CATBOOST_V9)
        .order('ordem', { ascending: true });
      if (error) throw error;
      const linhas = data || [];
      setCarteiraCB(linhas);
      setCarteiraCBCarregada(true);

      const idsTimes = [...new Set(linhas.flatMap(l => [l.matches?.home_team_id, l.matches?.away_team_id]).filter(Boolean))];
      if (idsTimes.length > 0) {
        const { data: times } = await supabase.from('teams').select('id, name').in('id', idsTimes);
        const mapaTimes = {};
        (times || []).forEach(t => { mapaTimes[t.id] = t.name; });
        setTimesPorId(prev => ({ ...prev, ...mapaTimes }));
      }
    } catch (e) {
      setErroCarteiraCB(e.message);
      setCarteiraCB([]);
    } finally {
      setCarregandoCarteiraCB(false);
    }
  }

  const linhaRotulo = (l) => `${l.selecao === 'over' ? 'Over' : 'Under'} ${Number(l.linha).toFixed(1)}`;
  const resultadoCarteira = (l) => (l.acertou ? { texto: 'Green', cor: 'text-emerald-400' } : { texto: 'Red', cor: 'text-red-400' });
  const diferencaBanca = (l) => l.banca_depois - l.banca_antes;

  // Ponderação real de liquidação de mercados de cartões (ver TIPO_EVENTO_
  // AMARELO/VERMELHO): amarelo=1, vermelho=2 (onde "vermelho" já inclui o
  // 2º amarelo, que é o evento que expulsa). Isso é DIFERENTE do jeito que
  // dados_historicos._carregar_total_cartoes_por_partida conta o alvo hoje
  // (cada evento = 1 ponto, sem essa ponderação) -- exibido aqui só como
  // informação adicional, não substitui o resultado Green/Red já gravado.
  const cartoesDaPartida = (matchId) => cartoesPorMatch[matchId] || {
    amareloHome: 0, amareloAway: 0, vermelhoHome: 0, vermelhoAway: 0,
    faltasHome: null, faltasAway: null, fonteHome: null, fonteAway: null,
  };
  const totalPonderado = (matchId) => {
    const c = cartoesDaPartida(matchId);
    return 1 * (c.amareloHome + c.amareloAway) + 2 * (c.vermelhoHome + c.vermelhoAway);
  };
  const fonteSuspeita = (matchId) => {
    const c = cartoesDaPartida(matchId);
    return c.fonteHome === 'fallback_suspeito' || c.fonteAway === 'fallback_suspeito';
  };

  const resumoCarteira = useMemo(() => {
    if (carteira.length === 0) return null;
    const ultima = carteira[carteira.length - 1];
    const pico = carteira.reduce((max, l) => Math.max(max, l.banca_depois), carteira[0]?.banca_antes ?? 1);
    let drawdownMax = 0;
    let picoCorrente = carteira[0]?.banca_antes ?? 1;
    for (const l of carteira) {
      picoCorrente = Math.max(picoCorrente, l.banca_depois);
      if (picoCorrente > 0) drawdownMax = Math.max(drawdownMax, (picoCorrente - l.banca_depois) / picoCorrente);
    }
    return { bancaFinalX: ultima.banca_depois, drawdownMax, n: carteira.length, pico };
  }, [carteira]);

  const colunasCSVCarteira = useMemo(() => ([
    { header: 'Data', get: (l) => new Date(l.match_date).toLocaleDateString('pt-BR') },
    { header: 'Liga', get: (l) => nomeLiga(l.matches?.league_id) },
    { header: 'Rodada', get: (l) => l.matches?.round ?? '—' },
    { header: 'Time 1 (mandante)', get: (l) => nomeTime(l.matches?.home_team_id) },
    { header: 'Time 2 (visitante)', get: (l) => nomeTime(l.matches?.away_team_id) },
    { header: 'Mercado', get: () => 'Cartões — Total' },
    { header: 'Linha', get: (l) => linhaRotulo(l) },
    {
      header: 'Amarelo (casa-visitante (total))',
      get: (l) => { const c = cartoesDaPartida(l.match_id); return `${c.amareloHome}-${c.amareloAway} (${c.amareloHome + c.amareloAway})`; },
    },
    {
      header: 'Vermelho (casa-visitante (total))',
      get: (l) => { const c = cartoesDaPartida(l.match_id); return `${c.vermelhoHome}-${c.vermelhoAway} (${c.vermelhoHome + c.vermelhoAway})`; },
    },
    { header: 'Total ponderado (1×A+2×V)', get: (l) => totalPonderado(l.match_id) },
    {
      header: 'Faltas (casa-visitante)',
      get: (l) => { const c = cartoesDaPartida(l.match_id); return `${c.faltasHome ?? '—'}-${c.faltasAway ?? '—'}`; },
    },
    { header: 'Fonte dos cartões', get: (l) => (fonteSuspeita(l.match_id) ? 'SUSPEITO (fallback 0-0)' : (FONTE_CARTOES_ROTULO[cartoesDaPartida(l.match_id).fonteHome]?.texto || '—')) },
    { header: 'Prob. modelo', get: (l) => (l.prob_modelo * 100).toFixed(2) + '%' },
    { header: 'Odd justa', get: (l) => l.odd_justa.toFixed(3) },
    { header: 'Odd real', get: (l) => l.odd_real.toFixed(3) },
    { header: 'Prob. devig (mercado)', get: (l) => (l.prob_devig != null ? (l.prob_devig * 100).toFixed(2) + '%' : '—') },
    { header: 'Edge (pp)', get: (l) => (l.edge * 100).toFixed(2) },
    { header: 'EV', get: (l) => (l.ev * 100).toFixed(2) + '%' },
    { header: 'Stake sugerida (% banca)', get: (l) => (l.stake_pct * 100).toFixed(2) + '%' },
    { header: 'Resultado', get: (l) => resultadoCarteira(l).texto },
    { header: 'Diferença na banca', get: (l) => (diferencaBanca(l) * 100).toFixed(2) + '%' },
  ]), [timesPorId, ligasPorId, cartoesPorMatch]);

  const exportarCarteira = () => {
    const nome = `carteira-cartoes-rf-serieB-${new Date().toISOString().slice(0, 10)}.csv`;
    exportarCSV(carteira, colunasCSVCarteira, nome);
  };

  // Filtro por liga da carteira catboost_v9 -- diferente da carteira de
  // cartões (já restrita a uma liga só na origem), esta carteira cobre as 6
  // ligas do benchmarking, então o filtro é útil de verdade aqui.
  const opcoesLigasCarteiraCB = useMemo(() => {
    const ids = [...new Set(carteiraCB.map(l => l.matches?.league_id).filter(Boolean))];
    return ids.sort((a, b) => nomeLiga(a).localeCompare(nomeLiga(b)));
  }, [carteiraCB, ligasPorId]);

  const carteiraCBFiltrada = useMemo(() => {
    if (!filtroLigaCarteiraCB) return carteiraCB;
    return carteiraCB.filter(l => String(l.matches?.league_id) === String(filtroLigaCarteiraCB));
  }, [carteiraCB, filtroLigaCarteiraCB]);

  const resumoCarteiraCB = useMemo(() => {
    if (carteiraCBFiltrada.length === 0) return null;
    const acertos = carteiraCBFiltrada.filter(l => l.acertou).length;
    // Banca final/drawdown recalculados só sobre o subconjunto filtrado --
    // com filtro de liga ativo, isso mostra "e se eu tivesse apostado só
    // nessa liga", não a banca real combinada (que é a sem filtro).
    let banca = 1;
    let pico = 1;
    let drawdownMax = 0;
    for (const l of carteiraCBFiltrada) {
      banca += (l.banca_depois - l.banca_antes);
      pico = Math.max(pico, banca);
      if (pico > 0) drawdownMax = Math.max(drawdownMax, (pico - banca) / pico);
    }
    return { bancaFinalX: banca, drawdownMax, n: carteiraCBFiltrada.length, taxaAcerto: acertos / carteiraCBFiltrada.length };
  }, [carteiraCBFiltrada]);

  const colunasCSVCarteiraCB = useMemo(() => ([
    { header: 'Data', get: (l) => new Date(l.match_date).toLocaleDateString('pt-BR') },
    { header: 'Liga', get: (l) => nomeLiga(l.matches?.league_id) },
    { header: 'Rodada', get: (l) => l.matches?.round ?? '—' },
    { header: 'Mandante', get: (l) => nomeTime(l.matches?.home_team_id) },
    { header: 'Visitante', get: (l) => nomeTime(l.matches?.away_team_id) },
    { header: 'Mercado', get: () => '1X2' },
    { header: 'Seleção', get: () => 'Mandante' },
    { header: 'Casa de aposta', get: (l) => rotuloCasaAposta(l.bookmaker) },
    { header: 'Prob. modelo', get: (l) => (l.prob_modelo * 100).toFixed(2) + '%' },
    { header: 'Odd justa', get: (l) => l.odd_justa.toFixed(3) },
    { header: 'Odd real', get: (l) => l.odd_real.toFixed(3) },
    { header: 'Prob. devig (mercado)', get: (l) => (l.prob_devig != null ? (l.prob_devig * 100).toFixed(2) + '%' : '—') },
    { header: 'Edge (pp)', get: (l) => (l.edge * 100).toFixed(2) },
    { header: 'EV', get: (l) => (l.ev * 100).toFixed(2) + '%' },
    { header: 'Stake sugerida (% banca)', get: (l) => (l.stake_pct * 100).toFixed(2) + '%' },
    { header: 'Resultado', get: (l) => resultadoCarteira(l).texto },
    { header: 'Diferença na banca', get: (l) => (diferencaBanca(l) * 100).toFixed(2) + '%' },
  ]), [timesPorId, ligasPorId]);

  const exportarCarteiraCB = () => {
    const sufixoLiga = filtroLigaCarteiraCB ? `-${nomeLiga(filtroLigaCarteiraCB).toLowerCase().replace(/\s+/g, '-')}` : '';
    const nome = `carteira-catboost-v9-mandante${sufixoLiga}-${new Date().toISOString().slice(0, 10)}.csv`;
    exportarCSV(carteiraCBFiltrada, colunasCSVCarteiraCB, nome);
  };

  const colunasCSV = useMemo(() => ([
    { header: 'Data', get: (c) => new Date(c.match_date).toLocaleString('pt-BR') },
    { header: 'Liga', get: (c) => nomeLiga(c.league_id) },
    { header: 'Mandante', get: (c) => nomeTime(c.home_team_id) },
    { header: 'Visitante', get: (c) => nomeTime(c.away_team_id) },
    { header: 'Modelo', get: (c) => rotuloModelo(c.model_name) },
    { header: 'Mercado', get: (c) => rotuloMercado(c.market) },
    { header: 'Seleção', get: (c) => rotuloSelecao(c.selection) },
    { header: 'Prob. modelo', get: (c) => (c.p_modelo * 100).toFixed(2) + '%' },
    { header: 'Prob. mercado (devigada)', get: (c) => (c.p_mercado * 100).toFixed(2) + '%' },
    { header: 'Edge (pp)', get: (c) => (c.edge * 100).toFixed(2) },
    { header: 'Odd real', get: (c) => c.odd.toFixed(3) },
    { header: 'Casa de aposta', get: (c) => rotuloCasaAposta(c.casa_aposta) },
    { header: 'Status', get: (c) => STATUS_ROTULO[statusDaLinha(c)].texto },
  ]), [timesPorId, ligasPorId]);

  const exportar = () => {
    const nome = `sugestoes-valor-${filtroModelo || 'todos-modelos'}-${new Date().toISOString().slice(0, 10)}.csv`;
    exportarCSV(candidatas, colunasCSV, nome); // exporta TUDO, não só a página exibida
  };

  const totalPaginas = Math.max(1, Math.ceil(candidatas.length / LINHAS_POR_PAGINA));
  const candidatasDaPagina = candidatas.slice(paginaAtual * LINHAS_POR_PAGINA, (paginaAtual + 1) * LINHAS_POR_PAGINA);

  if (!supabaseAtivo) {
    return (
      <div className="max-w-5xl mx-auto bg-slate-800 border border-red-500/30 rounded-2xl p-6 text-center">
        <AlertTriangle className="text-red-400 mx-auto mb-2" size={28} />
        <p className="text-slate-300">Supabase não configurado.</p>
      </div>
    );
  }

  return (
    <div className="max-w-6xl mx-auto">
      <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 mb-4 flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-extrabold flex items-center gap-3 text-slate-100">
            <Target className="text-emerald-400" size={28} /> Sugestões de Valor
          </h1>
          <p className="text-slate-400 mt-1 text-sm">
            {aba === 'sugestoes'
              ? 'Todas as apostas com edge positivo (probabilidade do modelo acima da probabilidade devigada do mercado) que cada modelo sugeriu, jogo a jogo — inclui partidas ainda pendentes, não só o histórico já resolvido.'
              : aba === 'carteira'
              ? 'Ledger aposta a aposta da carteira cronológica restrita de Cartões (cartoes_rf, Brasileirão Série B + bet365/betano, O/U 4.5 e 5.5, edge≥25%) — a banca composta de verdade, em ordem cronológica, achado registrado em model_betting_strategy.'
              : 'Ledger aposta a aposta da carteira cronológica de catboost_v9 (mandante, 1X2, odd 1.30-2.50, edge 5-10%) — única célula confiável da matriz de confiabilidade EV pra esse modelo, positiva nas 6 ligas do benchmarking, achado registrado em model_betting_strategy.'}
          </p>
        </div>
        {aba === 'sugestoes' ? (
          <button onClick={exportar} disabled={candidatas.length === 0}
            className="flex items-center gap-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 text-white font-bold px-4 py-2.5 rounded-lg text-sm">
            <Download size={16} /> Exportar CSV
          </button>
        ) : aba === 'carteira' ? (
          <button onClick={exportarCarteira} disabled={carteira.length === 0}
            className="flex items-center gap-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 text-white font-bold px-4 py-2.5 rounded-lg text-sm">
            <Download size={16} /> Exportar CSV
          </button>
        ) : (
          <button onClick={exportarCarteiraCB} disabled={carteiraCBFiltrada.length === 0}
            className="flex items-center gap-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 text-white font-bold px-4 py-2.5 rounded-lg text-sm">
            <Download size={16} /> Exportar CSV
          </button>
        )}
      </div>

      <div className="flex gap-2 mb-4">
        <button onClick={() => setAba('sugestoes')}
          className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-bold border ${aba === 'sugestoes' ? 'bg-emerald-600/20 border-emerald-500 text-emerald-300' : 'bg-slate-800 border-slate-700 text-slate-400 hover:text-slate-200'}`}>
          <Target size={15} /> Sugestões de Valor
        </button>
        <button onClick={() => setAba('carteira')}
          className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-bold border ${aba === 'carteira' ? 'bg-emerald-600/20 border-emerald-500 text-emerald-300' : 'bg-slate-800 border-slate-700 text-slate-400 hover:text-slate-200'}`}>
          <Wallet size={15} /> Carteira (Cartões — Série B)
        </button>
        <button onClick={() => setAba('carteira_catboost')}
          className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-bold border ${aba === 'carteira_catboost' ? 'bg-emerald-600/20 border-emerald-500 text-emerald-300' : 'bg-slate-800 border-slate-700 text-slate-400 hover:text-slate-200'}`}>
          <Wallet size={15} /> Carteira (catboost_v9 — Mandante)
        </button>
      </div>

      {aba === 'sugestoes' && (
      <>
      <div className="bg-slate-800 border border-slate-700 rounded-2xl p-4 mb-4 flex flex-wrap items-end gap-3">
        <div>
          <label className="block text-[10px] uppercase text-slate-500 mb-1">Modelo</label>
          <select value={filtroModelo} onChange={(e) => setFiltroModelo(e.target.value)} disabled={carregandoOpcoes}
            className="bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100">
            <option value="">Todos os modelos</option>
            {opcoesModelos.map(m => <option key={m} value={m}>{rotuloModelo(m)}</option>)}
          </select>
        </div>
        <div>
          <label className="block text-[10px] uppercase text-slate-500 mb-1">Mercado</label>
          <select value={filtroMercado} onChange={(e) => setFiltroMercado(e.target.value)} disabled={carregandoOpcoes}
            className="bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100">
            <option value="">Todos os mercados</option>
            {opcoesMercados.map(m => <option key={m} value={m}>{rotuloMercado(m)}</option>)}
          </select>
        </div>
        <div>
          <label className="block text-[10px] uppercase text-slate-500 mb-1">Liga <span className="text-amber-500">(obrigatório)</span></label>
          <select value={filtroLiga} onChange={(e) => setFiltroLiga(e.target.value)} disabled={carregandoOpcoes}
            className="bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100">
            <option value="">Escolha uma liga...</option>
            {opcoesLigas.map(l => <option key={l} value={l}>{nomeLiga(l)}</option>)}
          </select>
          <span className="block text-[10px] text-slate-600 mt-1 max-w-[16rem]">Sem liga, algumas combinações de modelo/mercado estouram o tempo limite da consulta.</span>
        </div>
        <div>
          <label className="block text-[10px] uppercase text-slate-500 mb-1">Edge mínimo</label>
          <input type="number" step="0.01" value={edgeMinimo} onChange={(e) => setEdgeMinimo(e.target.value)}
            className="w-24 bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100" />
        </div>
        <div>
          <label className="block text-[10px] uppercase text-slate-500 mb-1">Data início</label>
          <input type="date" value={dataInicio} onChange={(e) => setDataInicio(e.target.value)}
            className="bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100" />
        </div>
        <div>
          <label className="block text-[10px] uppercase text-slate-500 mb-1">Data fim</label>
          <input type="date" value={dataFim} onChange={(e) => setDataFim(e.target.value)}
            className="bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100" />
        </div>
        <button onClick={buscarSugestoes} disabled={carregando || !filtroLiga}
          className="flex items-center gap-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white font-bold px-4 py-2.5 rounded-lg text-sm">
          {carregando ? <Loader2 className="animate-spin" size={16} /> : <Search size={16} />} Buscar sugestões
        </button>
      </div>

      {erro && <div className="bg-red-950/30 border border-red-600/40 text-red-300 text-sm px-4 py-3 rounded-xl mb-4">{erro}</div>}

      {carregando ? (
        <div className="flex items-center justify-center py-16 text-slate-500 gap-2">
          <Loader2 className="animate-spin" size={20} /> Buscando sugestões de valor...
        </div>
      ) : !buscou ? (
        <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 text-center text-slate-500 text-sm">
          Ajuste os filtros e clique em "Buscar sugestões".
        </div>
      ) : candidatas.length === 0 ? (
        <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 text-center text-slate-500 text-sm">
          Nenhuma sugestão de valor encontrada com esses filtros.
        </div>
      ) : (
        <div className="bg-slate-800 border border-slate-700 rounded-2xl p-4 overflow-x-auto">
          <div className="text-xs text-slate-500 mb-2">{candidatas.length} sugestões encontradas</div>
          <table className="w-full text-xs">
            <thead>
              <tr className="text-slate-500 uppercase text-[10px]">
                <th className="text-left p-1.5">Data</th>
                <th className="text-left p-1.5">Liga</th>
                <th className="text-left p-1.5">Confronto</th>
                <th className="text-left p-1.5">Modelo</th>
                <th className="text-left p-1.5">Mercado</th>
                <th className="text-left p-1.5">Seleção</th>
                <th className="text-right p-1.5">Prob. modelo</th>
                <th className="text-right p-1.5">Prob. mercado</th>
                <th className="text-right p-1.5">Edge</th>
                <th className="text-right p-1.5">Odd</th>
                <th className="text-left p-1.5">Casa</th>
                <th className="text-right p-1.5">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-700/50">
              {candidatasDaPagina.map((c, i) => {
                const st = statusDaLinha(c);
                return (
                  <tr key={`${c.match_id}-${c.model_name}-${c.market}-${c.selection}-${i}`}>
                    <td className="p-1.5 text-slate-400 whitespace-nowrap">{new Date(c.match_date).toLocaleDateString('pt-BR')}</td>
                    <td className="p-1.5 text-slate-400">{nomeLiga(c.league_id)}</td>
                    <td className="p-1.5 text-slate-300 font-semibold whitespace-nowrap">{nomeTime(c.home_team_id)} x {nomeTime(c.away_team_id)}</td>
                    <td className="p-1.5 text-slate-300">{rotuloModelo(c.model_name)}</td>
                    <td className="p-1.5 text-slate-400">{rotuloMercado(c.market)}</td>
                    <td className="p-1.5 text-slate-300 font-semibold">{rotuloSelecao(c.selection)}</td>
                    <td className="p-1.5 text-right text-slate-200">{toPct(c.p_modelo)}</td>
                    <td className="p-1.5 text-right text-slate-200">{toPct(c.p_mercado)}</td>
                    <td className="p-1.5 text-right font-bold text-emerald-400">+{(c.edge * 100).toFixed(1)}pp</td>
                    <td className="p-1.5 text-right text-slate-200">{c.odd.toFixed(2)}</td>
                    <td className="p-1.5 text-slate-400">{rotuloCasaAposta(c.casa_aposta)}</td>
                    <td className={`p-1.5 text-right font-bold ${STATUS_ROTULO[st].cor}`}>{STATUS_ROTULO[st].texto}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          {totalPaginas > 1 && (
            <div className="flex items-center justify-between mt-3 pt-3 border-t border-slate-700">
              <button
                onClick={() => setPaginaAtual(p => Math.max(0, p - 1))}
                disabled={paginaAtual === 0}
                className="flex items-center gap-1 text-xs font-bold text-slate-400 hover:text-slate-200 disabled:opacity-30 disabled:cursor-not-allowed"
              >
                <ChevronLeft size={14} /> Anterior
              </button>
              <span className="text-[11px] text-slate-500">
                Página {paginaAtual + 1} de {totalPaginas} ({candidatasDaPagina.length} de {candidatas.length} linhas)
              </span>
              <button
                onClick={() => setPaginaAtual(p => Math.min(totalPaginas - 1, p + 1))}
                disabled={paginaAtual >= totalPaginas - 1}
                className="flex items-center gap-1 text-xs font-bold text-slate-400 hover:text-slate-200 disabled:opacity-30 disabled:cursor-not-allowed"
              >
                Próxima <ChevronRight size={14} />
              </button>
            </div>
          )}
        </div>
      )}
      </>
      )}

      {aba === 'carteira' && (
        <CarteiraCartoesTab
          carregando={carregandoCarteira}
          erro={erroCarteira}
          carteira={carteira}
          resumo={resumoCarteira}
          nomeLiga={nomeLiga}
          nomeTime={nomeTime}
          linhaRotulo={linhaRotulo}
          resultadoCarteira={resultadoCarteira}
          diferencaBanca={diferencaBanca}
          cartoesDaPartida={cartoesDaPartida}
          totalPonderado={totalPonderado}
          fonteSuspeita={fonteSuspeita}
        />
      )}

      {aba === 'carteira_catboost' && (
        <CarteiraCatboostV9Tab
          carregando={carregandoCarteiraCB}
          erro={erroCarteiraCB}
          carteira={carteiraCBFiltrada}
          resumo={resumoCarteiraCB}
          nomeLiga={nomeLiga}
          nomeTime={nomeTime}
          rotuloCasaAposta={rotuloCasaAposta}
          resultadoCarteira={resultadoCarteira}
          diferencaBanca={diferencaBanca}
          opcoesLigas={opcoesLigasCarteiraCB}
          filtroLiga={filtroLigaCarteiraCB}
          onFiltroLigaChange={setFiltroLigaCarteiraCB}
        />
      )}
    </div>
  );
}

function CarteiraCartoesTab({ carregando, erro, carteira, resumo, nomeLiga, nomeTime, linhaRotulo, resultadoCarteira, diferencaBanca, cartoesDaPartida, totalPonderado, fonteSuspeita }) {
  const [paginaAtual, setPaginaAtual] = useState(0);
  const totalPaginas = Math.max(1, Math.ceil(carteira.length / LINHAS_POR_PAGINA));
  const linhasDaPagina = carteira.slice(paginaAtual * LINHAS_POR_PAGINA, (paginaAtual + 1) * LINHAS_POR_PAGINA);

  if (erro) return <div className="bg-red-950/30 border border-red-600/40 text-red-300 text-sm px-4 py-3 rounded-xl mb-4">{erro}</div>;

  if (carregando) {
    return (
      <div className="flex items-center justify-center py-16 text-slate-500 gap-2">
        <Loader2 className="animate-spin" size={20} /> Carregando carteira...
      </div>
    );
  }

  if (carteira.length === 0) {
    return (
      <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 text-center text-slate-500 text-sm">
        Nenhuma aposta na carteira restrita ainda — ela é populada pelo workflow `analisar_cartoes_liga_sinal.yml`.
      </div>
    );
  }

  return (
    <>
      {resumo && (
        <div className="grid grid-cols-3 gap-3 mb-4">
          <div className="bg-slate-800 border border-slate-700 rounded-2xl p-4">
            <div className="text-[10px] uppercase text-slate-500 font-bold">Banca final</div>
            <div className="text-xl font-extrabold text-emerald-400 mt-1">{resumo.bancaFinalX.toFixed(2)}x</div>
          </div>
          <div className="bg-slate-800 border border-slate-700 rounded-2xl p-4">
            <div className="text-[10px] uppercase text-slate-500 font-bold">Drawdown máximo</div>
            <div className="text-xl font-extrabold text-amber-400 mt-1">{(resumo.drawdownMax * 100).toFixed(1)}%</div>
          </div>
          <div className="bg-slate-800 border border-slate-700 rounded-2xl p-4">
            <div className="text-[10px] uppercase text-slate-500 font-bold">Apostas na carteira</div>
            <div className="text-xl font-extrabold text-slate-100 mt-1">{resumo.n}</div>
          </div>
        </div>
      )}

      <div className="bg-slate-800 border border-slate-700 rounded-2xl p-4 overflow-x-auto">
        <div className="text-xs text-slate-500 mb-2">{carteira.length} apostas na carteira (ordem cronológica)</div>
        <table className="w-full text-xs">
          <thead>
            <tr className="text-slate-500 uppercase text-[10px]">
              <th className="text-left p-1.5">Data</th>
              <th className="text-left p-1.5">Liga</th>
              <th className="text-right p-1.5">Rodada</th>
              <th className="text-left p-1.5">Confronto</th>
              <th className="text-left p-1.5">Mercado</th>
              <th className="text-left p-1.5">Linha</th>
              <th className="text-right p-1.5">
                <span className="inline-flex items-center gap-1 justify-end w-full">
                  <span className="text-amber-400">Amarelo</span>
                  <Info size={11} className="text-slate-600" title="Cartões Amarelos" />
                </span>
              </th>
              <th className="text-right p-1.5">
                <span className="inline-flex items-center gap-1 justify-end w-full">
                  <span className="text-red-400">Vermelho</span>
                  <Info size={11} className="text-slate-600" title="Cartões Vermelhos (inclui o 2º amarelo, que expulsa)" />
                </span>
              </th>
              <th className="text-right p-1.5">
                <span className="inline-flex items-center gap-1 justify-end w-full">
                  Total ponderado
                  <Info size={11} className="text-slate-600" title="1×Amarelo + 2×Vermelho (regra real de liquidação do mercado de cartões)" />
                </span>
              </th>
              <th className="text-right p-1.5">Faltas</th>
              <th className="text-right p-1.5">Prob. modelo</th>
              <th className="text-right p-1.5">Odd justa</th>
              <th className="text-right p-1.5">Odd real</th>
              <th className="text-right p-1.5">Prob. devig</th>
              <th className="text-right p-1.5">Edge</th>
              <th className="text-right p-1.5">EV</th>
              <th className="text-right p-1.5">Stake</th>
              <th className="text-right p-1.5">Resultado</th>
              <th className="text-right p-1.5">Δ Banca</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-700/50">
            {linhasDaPagina.map((l) => {
              const resultado = resultadoCarteira(l);
              const diff = diferencaBanca(l);
              const cartoes = cartoesDaPartida(l.match_id);
              return (
                <tr key={l.id}>
                  <td className="p-1.5 text-slate-400 whitespace-nowrap">{new Date(l.match_date).toLocaleDateString('pt-BR')}</td>
                  <td className="p-1.5 text-slate-400">{nomeLiga(l.matches?.league_id)}</td>
                  <td className="p-1.5 text-right text-slate-400">{l.matches?.round ?? '—'}</td>
                  <td className="p-1.5 text-slate-300 font-semibold whitespace-nowrap">{nomeTime(l.matches?.home_team_id)} x {nomeTime(l.matches?.away_team_id)}</td>
                  <td className="p-1.5 text-slate-400">Cartões — Total</td>
                  <td className="p-1.5 text-slate-300 font-semibold">{linhaRotulo(l)}</td>
                  <td className="p-1.5 text-right text-amber-300">{cartoes.amareloHome}-{cartoes.amareloAway} ({cartoes.amareloHome + cartoes.amareloAway})</td>
                  <td className="p-1.5 text-right text-red-300">{cartoes.vermelhoHome}-{cartoes.vermelhoAway} ({cartoes.vermelhoHome + cartoes.vermelhoAway})</td>
                  <td className="p-1.5 text-right">
                    <span className={`font-bold ${fonteSuspeita(l.match_id) ? 'text-red-400' : 'text-slate-200'}`} title={fonteSuspeita(l.match_id) ? 'Fonte suspeita: fallback bateu 0-0, provável dado faltando (não jogo sem cartão)' : ''}>
                      {totalPonderado(l.match_id)}{fonteSuspeita(l.match_id) && ' ⚠'}
                    </span>
                  </td>
                  <td className="p-1.5 text-right text-slate-400">{cartoes.faltasHome ?? '—'}-{cartoes.faltasAway ?? '—'}</td>
                  <td className="p-1.5 text-right text-slate-200">{toPct(l.prob_modelo)}</td>
                  <td className="p-1.5 text-right text-slate-400">{l.odd_justa.toFixed(2)}</td>
                  <td className="p-1.5 text-right text-slate-200">{l.odd_real.toFixed(2)}</td>
                  <td className="p-1.5 text-right text-slate-400">{l.prob_devig != null ? toPct(l.prob_devig) : '—'}</td>
                  <td className="p-1.5 text-right font-bold text-emerald-400">+{(l.edge * 100).toFixed(1)}pp</td>
                  <td className="p-1.5 text-right text-slate-200">+{(l.ev * 100).toFixed(1)}%</td>
                  <td className="p-1.5 text-right text-slate-300">{(l.stake_pct * 100).toFixed(1)}%</td>
                  <td className={`p-1.5 text-right font-bold ${resultado.cor}`}>{resultado.texto}</td>
                  <td className={`p-1.5 text-right font-bold ${diff >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                    {diff >= 0 ? '+' : ''}{(diff * 100).toFixed(2)}%
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>

        {totalPaginas > 1 && (
          <div className="flex items-center justify-between mt-3 pt-3 border-t border-slate-700">
            <button
              onClick={() => setPaginaAtual(p => Math.max(0, p - 1))}
              disabled={paginaAtual === 0}
              className="flex items-center gap-1 text-xs font-bold text-slate-400 hover:text-slate-200 disabled:opacity-30 disabled:cursor-not-allowed"
            >
              <ChevronLeft size={14} /> Anterior
            </button>
            <span className="text-[11px] text-slate-500">
              Página {paginaAtual + 1} de {totalPaginas} ({linhasDaPagina.length} de {carteira.length} linhas)
            </span>
            <button
              onClick={() => setPaginaAtual(p => Math.min(totalPaginas - 1, p + 1))}
              disabled={paginaAtual >= totalPaginas - 1}
              className="flex items-center gap-1 text-xs font-bold text-slate-400 hover:text-slate-200 disabled:opacity-30 disabled:cursor-not-allowed"
            >
              Próxima <ChevronRight size={14} />
            </button>
          </div>
        )}
      </div>
    </>
  );
}

function CarteiraCatboostV9Tab({ carregando, erro, carteira, resumo, nomeLiga, nomeTime, rotuloCasaAposta, resultadoCarteira, diferencaBanca, opcoesLigas, filtroLiga, onFiltroLigaChange }) {
  const [paginaAtual, setPaginaAtual] = useState(0);
  const totalPaginas = Math.max(1, Math.ceil(carteira.length / LINHAS_POR_PAGINA));
  const linhasDaPagina = carteira.slice(paginaAtual * LINHAS_POR_PAGINA, (paginaAtual + 1) * LINHAS_POR_PAGINA);

  // Reseta pra 1ª página sempre que o filtro de liga muda -- senão o usuário
  // pode ficar numa página que não existe mais no subconjunto filtrado.
  useEffect(() => { setPaginaAtual(0); }, [filtroLiga]);

  if (erro) return <div className="bg-red-950/30 border border-red-600/40 text-red-300 text-sm px-4 py-3 rounded-xl mb-4">{erro}</div>;

  if (carregando) {
    return (
      <div className="flex items-center justify-center py-16 text-slate-500 gap-2">
        <Loader2 className="animate-spin" size={20} /> Carregando carteira...
      </div>
    );
  }

  return (
    <>
      <div className="bg-slate-800 border border-slate-700 rounded-2xl p-4 mb-4 flex flex-wrap items-end gap-3">
        <div>
          <label className="block text-[10px] uppercase text-slate-500 mb-1">Liga</label>
          <select value={filtroLiga} onChange={(e) => onFiltroLigaChange(e.target.value)}
            className="bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100">
            <option value="">Todas as ligas</option>
            {opcoesLigas.map(l => <option key={l} value={l}>{nomeLiga(l)}</option>)}
          </select>
        </div>
      </div>

      {carteira.length === 0 ? (
        <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 text-center text-slate-500 text-sm">
          {filtroLiga
            ? 'Nenhuma aposta dessa liga na carteira restrita.'
            : 'Nenhuma aposta na carteira restrita ainda — ela é populada pelo workflow `analisar_catboost_v9_liga_sinal.yml`.'}
        </div>
      ) : (
        <>
          {resumo && (
            <div className="grid grid-cols-4 gap-3 mb-4">
              <div className="bg-slate-800 border border-slate-700 rounded-2xl p-4">
                <div className="text-[10px] uppercase text-slate-500 font-bold">Banca final</div>
                <div className="text-xl font-extrabold text-emerald-400 mt-1">{resumo.bancaFinalX.toFixed(2)}x</div>
              </div>
              <div className="bg-slate-800 border border-slate-700 rounded-2xl p-4">
                <div className="text-[10px] uppercase text-slate-500 font-bold">Drawdown máximo</div>
                <div className="text-xl font-extrabold text-amber-400 mt-1">{(resumo.drawdownMax * 100).toFixed(1)}%</div>
              </div>
              <div className="bg-slate-800 border border-slate-700 rounded-2xl p-4">
                <div className="text-[10px] uppercase text-slate-500 font-bold">Taxa de acerto</div>
                <div className="text-xl font-extrabold text-slate-100 mt-1">{(resumo.taxaAcerto * 100).toFixed(1)}%</div>
              </div>
              <div className="bg-slate-800 border border-slate-700 rounded-2xl p-4">
                <div className="text-[10px] uppercase text-slate-500 font-bold">Apostas na carteira</div>
                <div className="text-xl font-extrabold text-slate-100 mt-1">{resumo.n}</div>
              </div>
            </div>
          )}

          <div className="bg-slate-800 border border-slate-700 rounded-2xl p-4 overflow-x-auto">
            <div className="text-xs text-slate-500 mb-2">{carteira.length} apostas na carteira (ordem cronológica)</div>
            <table className="w-full text-xs">
              <thead>
                <tr className="text-slate-500 uppercase text-[10px]">
                  <th className="text-left p-1.5">Data</th>
                  <th className="text-left p-1.5">Liga</th>
                  <th className="text-right p-1.5">Rodada</th>
                  <th className="text-left p-1.5">Confronto</th>
                  <th className="text-left p-1.5">Seleção</th>
                  <th className="text-left p-1.5">Casa</th>
                  <th className="text-right p-1.5">Prob. modelo</th>
                  <th className="text-right p-1.5">Odd justa</th>
                  <th className="text-right p-1.5">Odd real</th>
                  <th className="text-right p-1.5">Prob. devig</th>
                  <th className="text-right p-1.5">Edge</th>
                  <th className="text-right p-1.5">EV</th>
                  <th className="text-right p-1.5">Stake</th>
                  <th className="text-right p-1.5">Resultado</th>
                  <th className="text-right p-1.5">Δ Banca</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-700/50">
                {linhasDaPagina.map((l) => {
                  const resultado = resultadoCarteira(l);
                  const diff = diferencaBanca(l);
                  return (
                    <tr key={l.id}>
                      <td className="p-1.5 text-slate-400 whitespace-nowrap">{new Date(l.match_date).toLocaleDateString('pt-BR')}</td>
                      <td className="p-1.5 text-slate-400">{nomeLiga(l.matches?.league_id)}</td>
                      <td className="p-1.5 text-right text-slate-400">{l.matches?.round ?? '—'}</td>
                      <td className="p-1.5 text-slate-300 font-semibold whitespace-nowrap">{nomeTime(l.matches?.home_team_id)} x {nomeTime(l.matches?.away_team_id)}</td>
                      <td className="p-1.5 text-slate-300 font-semibold">Mandante</td>
                      <td className="p-1.5 text-slate-400">{rotuloCasaAposta(l.bookmaker)}</td>
                      <td className="p-1.5 text-right text-slate-200">{toPct(l.prob_modelo)}</td>
                      <td className="p-1.5 text-right text-slate-400">{l.odd_justa.toFixed(2)}</td>
                      <td className="p-1.5 text-right text-slate-200">{l.odd_real.toFixed(2)}</td>
                      <td className="p-1.5 text-right text-slate-400">{l.prob_devig != null ? toPct(l.prob_devig) : '—'}</td>
                      <td className="p-1.5 text-right font-bold text-emerald-400">+{(l.edge * 100).toFixed(1)}pp</td>
                      <td className="p-1.5 text-right text-slate-200">+{(l.ev * 100).toFixed(1)}%</td>
                      <td className="p-1.5 text-right text-slate-300">{(l.stake_pct * 100).toFixed(1)}%</td>
                      <td className={`p-1.5 text-right font-bold ${resultado.cor}`}>{resultado.texto}</td>
                      <td className={`p-1.5 text-right font-bold ${diff >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                        {diff >= 0 ? '+' : ''}{(diff * 100).toFixed(2)}%
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>

            {totalPaginas > 1 && (
              <div className="flex items-center justify-between mt-3 pt-3 border-t border-slate-700">
                <button
                  onClick={() => setPaginaAtual(p => Math.max(0, p - 1))}
                  disabled={paginaAtual === 0}
                  className="flex items-center gap-1 text-xs font-bold text-slate-400 hover:text-slate-200 disabled:opacity-30 disabled:cursor-not-allowed"
                >
                  <ChevronLeft size={14} /> Anterior
                </button>
                <span className="text-[11px] text-slate-500">
                  Página {paginaAtual + 1} de {totalPaginas} ({linhasDaPagina.length} de {carteira.length} linhas)
                </span>
                <button
                  onClick={() => setPaginaAtual(p => Math.min(totalPaginas - 1, p + 1))}
                  disabled={paginaAtual >= totalPaginas - 1}
                  className="flex items-center gap-1 text-xs font-bold text-slate-400 hover:text-slate-200 disabled:opacity-30 disabled:cursor-not-allowed"
                >
                  Próxima <ChevronRight size={14} />
                </button>
              </div>
            )}
          </div>
        </>
      )}
    </>
  );
}
