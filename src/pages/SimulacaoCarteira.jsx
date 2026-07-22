// src/pages/SimulacaoCarteira.jsx — rota /simulacao-carteira
// Simulação de carteira RODADA A RODADA (Quarter Kelly + gerenciamento de
// risco estrito), servida por api/model-maintenance.js
// (?tarefa=simulacao-carteira/?tarefa=modelos-disponiveis -- fundidas lá
// em vez de arquivos próprios por causa do teto de 12 Serverless
// Functions do plano Hobby do Vercel, ver skill workflow-quant-predictor)
// -- diferente do "Backtest de apostas simuladas (EV+)" já existente em
// /modelos (api/backtest-betting.js, ROI agregado com edge devigado),
// aqui o filtro de entrada é EV bruto (p_modelo * odd >= 1,02, sem devig)
// e a banca evolui rodada a rodada (rodada = dia de calendário) com teto
// de exposição de 15% por rodada e piso de ruído de 0,5% da banca.
//
// "permita usar cada modelo para comparação" -- roda a simulação (uma
// banca independente por modelo, nunca somada) pra QUALQUER conjunto de
// modelos brutos escolhido, com filtro de liga/temporada e opção de
// correção (crua/Platt/Isotonic) -- lista completa vem de
// ?tarefa=modelos-disponiveis.
import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { Wallet, Loader2, AlertTriangle, PlayCircle, ChevronDown, ChevronRight, TrendingDown, FileDown } from 'lucide-react';
import PainelInfoModelo, { BotaoInfoModelo } from '../components/PainelInfoModelo';

const MERCADO_CONTEXTO = 'Restrito ao período de teste out-of-sample (temporada 2025+) — treino/validação (2019–2024) fica de fora. Execução comparada em duas odds da Pinnacle: abertura vs. fechamento; modelos sem esse par (ex.: só "Model Benchmarking") podem não ter apostas em nenhuma das duas.';

// Mercados oferecidos aqui -- restrito aos que têm odds REAIS de abertura/
// fechamento da Pinnacle em `odds_market` (sem isso a simulação nunca gera
// nenhuma aposta, ver MERCADOS_CARTEIRA_SUPORTADOS em api/model-maintenance.js).
// Escanteios (stats_glm_v1, pipeline antigo) e os mercados novos do Model
// Benchmarking (corners_ou95/faixa_gols) já têm modelo treinado, mas NENHUMA
// odd de mercado nesse projeto -- entrariam sempre com 0 apostas, por isso
// ficam de fora da carteira (aparecem só no backtest de qualidade, em
// /model-benchmarking).
const MERCADOS_CARTEIRA = [
  { chave: '1X2', rotulo: '1X2' },
  { chave: 'over_under_2.5', rotulo: 'Over/Under 2.5' },
];

const ROTULO_EXECUCAO = { abertura: 'Abertura', fechamento: 'Fechamento' };

function escaparHtml(valor) {
  return String(valor ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function fmtMoney(v) {
  if (v == null) return '—';
  return v.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function fmtPct(v, casas = 1) {
  if (v == null) return '—';
  return `${v >= 0 ? '+' : ''}${v.toFixed(casas)}%`;
}
function fmtPctPlain(v, casas = 1) {
  if (v == null) return '—';
  return `${v.toFixed(casas)}%`;
}
function fmtNum(v, casas = 3) {
  if (v == null) return '—';
  return v.toFixed(casas);
}

// Sparkline SVG minúsculo (uma cor só por card -- não precisa distinguir
// séries por matiz, cada card já é seu próprio "small multiple").
function Sparkline({ pontos, cor }) {
  if (!pontos || pontos.length < 2) return null;
  const W = 260, H = 56, PAD = 4;
  const max = Math.max(...pontos);
  const min = Math.min(...pontos);
  const range = max - min || 1;
  const x = (i) => PAD + (i / (pontos.length - 1)) * (W - PAD * 2);
  const y = (v) => PAD + (H - PAD * 2) - ((v - min) / range) * (H - PAD * 2);
  const d = pontos.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  const areaD = `${d} L${x(pontos.length - 1).toFixed(1)},${H - PAD} L${x(0).toFixed(1)},${H - PAD} Z`;
  const yBase = y(pontos[0]);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-14">
      <line x1={PAD} y1={yBase} x2={W - PAD} y2={yBase} stroke="currentColor" strokeOpacity="0.2" strokeDasharray="2 3" />
      <path d={areaD} fill={cor} opacity="0.12" />
      <path d={d} fill="none" stroke={cor} strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function CardModelo({ resultado, aberto, onToggle }) {
  const { modelo, execucao, sumario, rodadas } = resultado;
  if (!sumario || sumario.n_apostas_totais === 0) {
    return (
      <div className="bg-slate-900 border border-slate-700/50 rounded-xl p-4">
        <div className="flex items-center justify-between gap-2">
          <span className="text-sm font-bold text-slate-300 truncate" title={modelo}>{modelo}</span>
          <span className="text-[9px] font-bold uppercase px-1.5 py-0.5 rounded bg-slate-800 text-slate-500">{ROTULO_EXECUCAO[execucao] || execucao}</span>
        </div>
        <div className="text-xs text-slate-500 mt-2">Sem apostas que passem o filtro de EV com os filtros atuais.</div>
      </div>
    );
  }
  const roiPos = sumario.roi_total_pct >= 0;
  const cor = roiPos ? '#34d399' : '#f87171';
  const pontos = [sumario.banca_inicial, ...rodadas.map((r) => r.banca_final)];

  return (
    <div className="bg-slate-900 border border-slate-700/50 rounded-xl overflow-hidden">
      <div className="p-4">
        <div className="flex items-center justify-between gap-2 mb-1">
          <span className="text-sm font-bold text-slate-200 truncate" title={modelo}>{modelo}</span>
          <span className={`text-xs font-bold px-1.5 py-0.5 rounded ${roiPos ? 'bg-emerald-500/10 text-emerald-400' : 'bg-red-500/10 text-red-400'}`}>
            {fmtPct(sumario.roi_total_pct)}
          </span>
        </div>
        <div className="flex items-center justify-between gap-2 mb-2">
          <span className="text-[9px] font-bold uppercase px-1.5 py-0.5 rounded bg-slate-800 text-slate-400">{ROTULO_EXECUCAO[execucao] || execucao}</span>
        </div>
        <div className="text-[11px] text-slate-500 mb-2">
          {sumario.n_apostas_totais} apostas · {sumario.n_rodadas_com_aposta} rodadas · banca R$ {fmtMoney(sumario.banca_final)}
        </div>
        <Sparkline pontos={pontos} cor={cor} />
        <div className="grid grid-cols-3 gap-2 mt-3 text-center">
          <div>
            <div className="text-[9px] uppercase text-slate-500">MDD</div>
            <div className="text-xs font-bold text-red-400">−{fmtPctPlain(sumario.mdd_pct)}</div>
          </div>
          <div>
            <div className="text-[9px] uppercase text-slate-500">Acerto</div>
            <div className="text-xs font-bold text-slate-300">{fmtPctPlain(sumario.win_rate_pct)}</div>
          </div>
          <div>
            <div className="text-[9px] uppercase text-slate-500">CLV médio</div>
            <div className={`text-xs font-bold ${sumario.clv_medio_pct == null ? 'text-slate-600' : sumario.clv_medio_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
              {sumario.clv_medio_pct == null ? '—' : fmtPct(sumario.clv_medio_pct)}
            </div>
          </div>
        </div>
      </div>
      <button onClick={onToggle} className="w-full flex items-center justify-center gap-1 text-[11px] text-slate-500 hover:text-slate-300 py-2 border-t border-slate-800 bg-slate-900/60">
        {aberto ? <ChevronDown size={13} /> : <ChevronRight size={13} />} {aberto ? 'Ocultar rodadas' : `Ver ${rodadas.length} rodadas`}
      </button>
      {aberto && (
        <div className="max-h-96 overflow-y-auto border-t border-slate-800">
          <table className="w-full text-[11px]">
            <thead className="sticky top-0 bg-slate-800">
              <tr className="text-slate-500 uppercase text-[9px]">
                <th className="text-left p-1.5">Rod.</th><th className="text-left p-1.5">Data</th>
                <th className="text-right p-1.5">Banca Inicial</th><th className="text-right p-1.5">Qtd</th>
                <th className="text-right p-1.5">Exp.</th><th className="text-center p-1.5">V/D</th>
                <th className="text-right p-1.5">Result. Líq.</th><th className="text-right p-1.5">Retorno</th>
                <th className="text-right p-1.5">Banca Final</th><th className="text-right p-1.5">Drawdown</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800">
              {rodadas.map((r) => (
                <tr key={r.rodada} className={r.drawdown_pct > 0 ? '' : ''}>
                  <td className="p-1.5 text-slate-400">{r.rodada}</td>
                  <td className="p-1.5 text-slate-400">{r.data}</td>
                  <td className="p-1.5 text-right text-slate-400">{fmtMoney(r.banca_inicial)}</td>
                  <td className="p-1.5 text-right text-slate-400">{r.qtd_apostas}</td>
                  <td className="p-1.5 text-right text-slate-400">
                    {fmtPctPlain(r.exposicao_pct)}
                    {r.escalado && <span className="ml-1 text-[8px] border border-amber-500/50 text-amber-400 rounded px-1">cap</span>}
                  </td>
                  <td className="p-1.5 text-center"><span className="text-emerald-400">{r.vitorias}V</span> <span className="text-red-400">{r.derrotas}D</span></td>
                  <td className={`p-1.5 text-right font-semibold ${r.resultado_liquido >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{fmtMoney(r.resultado_liquido)}</td>
                  <td className={`p-1.5 text-right font-semibold ${r.retorno_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{fmtPct(r.retorno_pct)}</td>
                  <td className="p-1.5 text-right text-slate-300">{fmtMoney(r.banca_final)}</td>
                  <td className={`p-1.5 text-right ${r.drawdown_pct > 0 ? 'text-red-400' : 'text-slate-500'}`}>{fmtPctPlain(r.drawdown_pct)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default function SimulacaoCarteira() {
  const [modelosDisponiveis, setModelosDisponiveis] = useState([]);
  const [ligas, setLigas] = useState([]);
  const [carregandoOpcoes, setCarregandoOpcoes] = useState(true);
  const [erroOpcoes, setErroOpcoes] = useState('');

  const [mercado, setMercado] = useState('1X2');
  const [modelosSelecionados, setModelosSelecionados] = useState(new Set());
  const [ligaId, setLigaId] = useState('');
  const [temporada, setTemporada] = useState('');
  const [usarCalibracao, setUsarCalibracao] = useState('nenhuma');
  const [bancaInicial, setBancaInicial] = useState(1000);

  const [rodando, setRodando] = useState(false);
  const [erro, setErro] = useState('');
  const [resultados, setResultados] = useState(null); // [{ modelo, execucao, sumario, rodadas }] (2 linhas por modelo: abertura + fechamento)
  const [abertos, setAbertos] = useState({});
  const [modeloInfo, setModeloInfo] = useState(null);

  const carregarOpcoes = useCallback(async (mercadoAtual) => {
    setCarregandoOpcoes(true);
    setErroOpcoes('');
    try {
      const resp = await fetch(`/api/model-maintenance?tarefa=modelos-disponiveis&mercado=${encodeURIComponent(mercadoAtual)}`);
      const dados = await resp.json();
      if (!resp.ok) throw new Error(dados.error?.message || 'Erro ao carregar modelos.');
      setModelosDisponiveis(dados.modelos || []);
      setLigas(dados.ligas || []);
      // default: os 2 modelos com mais histórico resolvido, pra já sair com algo comparável.
      setModelosSelecionados(new Set((dados.modelos || []).slice(0, 2).map((m) => m.model_name)));
    } catch (e) {
      setErroOpcoes(e.message);
    } finally {
      setCarregandoOpcoes(false);
    }
  }, []);

  // Muda de mercado -> recarrega a lista de modelos (cada mercado tem um
  // universo diferente de modelos com histórico) e limpa o resultado
  // anterior (era doutro mercado, comparar os dois lado a lado não faz
  // sentido -- unidades de aposta diferentes).
  useEffect(() => {
    carregarOpcoes(mercado);
    setResultados(null);
  }, [mercado, carregarOpcoes]);

  const temporadasDisponiveis = useMemo(() => {
    const set = new Set();
    modelosDisponiveis.forEach((m) => {
      if (modelosSelecionados.size === 0 || modelosSelecionados.has(m.model_name)) {
        m.temporadas.forEach((t) => set.add(t));
      }
    });
    return [...set].sort();
  }, [modelosDisponiveis, modelosSelecionados]);

  const ligasPorId = useMemo(() => {
    const mapa = {};
    ligas.forEach((l) => { mapa[l.id] = l.name; });
    return mapa;
  }, [ligas]);

  const toggleModelo = (nome) => {
    setModelosSelecionados((prev) => {
      const novo = new Set(prev);
      if (novo.has(nome)) novo.delete(nome); else novo.add(nome);
      return novo;
    });
  };
  const selecionarTodos = () => setModelosSelecionados(new Set(modelosDisponiveis.map((m) => m.model_name)));
  const limparSelecao = () => setModelosSelecionados(new Set());

  const rodar = async () => {
    if (modelosSelecionados.size === 0) { setErro('Selecione pelo menos 1 modelo.'); return; }
    setRodando(true);
    setErro('');
    try {
      const promessas = [...modelosSelecionados].map(async (modelo) => {
        const params = new URLSearchParams({ tarefa: 'simulacao-carteira', modelo, mercado, usar_calibracao: usarCalibracao, banca_inicial: String(bancaInicial) });
        if (ligaId) params.set('liga_id', ligaId);
        if (temporada) params.set('temporada', temporada);
        const resp = await fetch(`/api/model-maintenance?${params}`);
        const dados = await resp.json();
        if (!resp.ok) throw new Error(`${modelo}: ${dados.error?.message || 'erro'}`);
        const execucoes = dados.execucoes && dados.execucoes.length > 0 ? dados.execucoes : [{ execucao: 'abertura', sumario: null, rodadas: [] }, { execucao: 'fechamento', sumario: null, rodadas: [] }];
        return execucoes.map((ex) => ({ modelo, execucao: ex.execucao, sumario: ex.sumario, rodadas: ex.rodadas || [] }));
      });
      const resultadosBrutos = (await Promise.all(promessas)).flat();
      resultadosBrutos.sort((a, b) => (b.sumario?.roi_total_pct ?? -Infinity) - (a.sumario?.roi_total_pct ?? -Infinity));
      setResultados(resultadosBrutos);
      setAbertos({});
    } catch (e) {
      setErro(e.message);
    } finally {
      setRodando(false);
    }
  };

  const comSumario = (resultados || []).filter((r) => r.sumario && r.sumario.n_apostas_totais > 0);

  const emitirRelatorio = () => {
    const rotuloMercado = MERCADOS_CARTEIRA.find((m) => m.chave === mercado)?.rotulo || mercado;
    const geradoEm = new Date().toLocaleString('pt-BR');
    const linhas = comSumario
      .map(
        (r) => `<tr class="${r.sumario.roi_total_pct >= 0 ? 'pos' : 'neg'}">
          <td>${escaparHtml(r.modelo)}</td>
          <td>${escaparHtml(ROTULO_EXECUCAO[r.execucao] || r.execucao)}</td>
          <td class="num">${fmtPct(r.sumario.roi_total_pct)}</td>
          <td class="num">${r.sumario.cagr_pct != null ? fmtPct(r.sumario.cagr_pct) : '—'}</td>
          <td class="num">−${fmtPctPlain(r.sumario.mdd_pct)}</td>
          <td class="num">${fmtPctPlain(r.sumario.win_rate_pct)}</td>
          <td class="num">${r.sumario.clv_medio_pct != null ? fmtPct(r.sumario.clv_medio_pct) : '—'}</td>
          <td class="num">${fmtNum(r.sumario.sharpe_simplificado)}</td>
          <td class="num">${fmtNum(r.sumario.sortino_simplificado)}</td>
          <td class="num">${r.sumario.n_apostas_totais}</td>
          <td class="num">R$ ${fmtMoney(r.sumario.banca_final)}</td>
        </tr>`
      )
      .join('');
    const html = `<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8" />
<title>Relatório — Simulação de Carteira</title>
<style>
  body { background:#0f172a; color:#e2e8f0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; margin:0; padding:32px; }
  h1 { font-size:22px; margin-bottom:4px; }
  .subtitulo { color:#94a3b8; font-size:13px; margin-bottom:24px; }
  table { width:100%; border-collapse:collapse; font-size:11px; font-variant-numeric:tabular-nums; }
  th { text-align:right; color:#64748b; text-transform:uppercase; font-size:9px; padding:6px 8px; border-bottom:1px solid #334155; }
  th:nth-child(1), th:nth-child(2), td:nth-child(1), td:nth-child(2) { text-align:left; }
  td { padding:6px 8px; border-bottom:1px solid #1e293b; color:#cbd5e1; }
  td.num { text-align:right; }
  tr.pos td:nth-child(3) { color:#34d399; font-weight:bold; }
  tr.neg td:nth-child(3) { color:#f87171; font-weight:bold; }
  @media print { body { background:#fff; color:#000; } }
</style>
</head>
<body>
  <h1>Relatório — Simulação de Carteira (Quarter Kelly)</h1>
  <p class="subtitulo">
    Gerado em ${geradoEm}. Mercado: ${escaparHtml(rotuloMercado)}. Banca inicial: R$ ${fmtMoney(Number(bancaInicial))}.
    Correção: ${escaparHtml(usarCalibracao === 'nenhuma' ? 'crua' : usarCalibracao)}.
    Test Set out-of-sample (temporada 2025+) — filtro de EV bruto p·odd ≥ 1,02, Quarter Kelly, teto de exposição 15% por
    rodada. Execuções "Abertura"/"Fechamento" usam a odd de abertura/fechamento da Pinnacle respectivamente.
  </p>
  <table>
    <thead><tr>
      <th>Modelo</th><th>Execução</th><th>ROI</th><th>CAGR</th><th>MDD</th><th>Acerto</th><th>CLV médio</th>
      <th>Sharpe</th><th>Sortino</th><th>Apostas</th><th>Banca final</th>
    </tr></thead>
    <tbody>${linhas || '<tr><td colspan="11">Sem resultado com apostas nesta rodada.</td></tr>'}</tbody>
  </table>
</body>
</html>`;
    const blob = new Blob([html], { type: 'text/html' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `relatorio-simulacao-carteira-${mercado}-${new Date().toISOString().slice(0, 10)}.html`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="max-w-6xl mx-auto space-y-4">
      <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6">
        <h1 className="text-xl font-bold flex items-center gap-2 text-slate-100">
          <Wallet className="text-emerald-400" size={22} /> Simulação de Carteira — Quarter Kelly
        </h1>
        <p className="text-slate-400 mt-1 text-sm">
          Banca evolui rodada a rodada (rodada = dia de calendário) com gerenciamento de risco estrito: filtro de EV bruto{' '}
          <code className="text-slate-300">p·odd ≥ 1,02</code>, piso de ruído <code className="text-slate-300">stake ≥ 0,5% da banca</code>,
          Quarter Kelly, teto de exposição <code className="text-slate-300">15% da banca por rodada</code> (escala proporcional se exceder).
          Banca da rodada = banca de fechamento da rodada anterior. {MERCADO_CONTEXTO}
        </p>
        <div className="flex items-center gap-2 mt-3">
          {MERCADOS_CARTEIRA.map((m) => (
            <button
              key={m.chave}
              onClick={() => setMercado(m.chave)}
              className={`px-3 py-1.5 rounded-lg text-xs font-bold border ${
                mercado === m.chave ? 'bg-emerald-600 border-emerald-600 text-white' : 'bg-slate-900 border-slate-700 text-slate-400 hover:text-slate-200'
              }`}
            >
              {m.rotulo}
            </button>
          ))}
        </div>
      </div>

      {erroOpcoes && (
        <div className="bg-red-950/30 border border-red-600/40 text-red-300 text-sm px-4 py-3 rounded-xl flex items-center gap-2">
          <AlertTriangle size={16} /> {erroOpcoes}
        </div>
      )}

      {carregandoOpcoes ? (
        <div className="flex items-center justify-center py-16 text-slate-500 gap-2">
          <Loader2 className="animate-spin" size={20} /> Carregando modelos disponíveis...
        </div>
      ) : (
        <>
          <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 space-y-4">
            <div>
              <div className="flex items-center justify-between mb-2">
                <label className="text-[11px] font-bold text-slate-500 uppercase">Modelos ({modelosSelecionados.size} selecionados)</label>
                <div className="flex gap-2">
                  <button onClick={selecionarTodos} className="text-[11px] text-emerald-400 hover:text-emerald-300 font-semibold">selecionar todos</button>
                  <span className="text-slate-600">·</span>
                  <button onClick={limparSelecao} className="text-[11px] text-slate-500 hover:text-slate-300 font-semibold">limpar</button>
                </div>
              </div>
              <div className="flex flex-wrap gap-2 max-h-40 overflow-y-auto bg-slate-900 border border-slate-700 rounded-lg p-3">
                {modelosDisponiveis.length === 0 && <span className="text-xs text-slate-500">Nenhum modelo com histórico resolvido ainda.</span>}
                {modelosDisponiveis.map((m) => (
                  <span
                    key={m.model_name}
                    className={`flex items-center gap-1.5 text-xs pl-2.5 pr-2 py-1.5 rounded-lg border font-semibold transition-colors ${
                      modelosSelecionados.has(m.model_name)
                        ? 'bg-emerald-500/15 border-emerald-500/50 text-emerald-300'
                        : 'bg-slate-800 border-slate-700 text-slate-400 hover:text-slate-200'
                    }`}
                  >
                    <button onClick={() => toggleModelo(m.model_name)} title={`${m.n_partidas_resolvidas} partidas resolvidas`}>
                      {m.model_name} <span className="opacity-60">({m.n_partidas_resolvidas})</span>
                    </button>
                    <BotaoInfoModelo onClick={() => setModeloInfo(m.model_name)} />
                  </span>
                ))}
              </div>
            </div>

            <div className="flex flex-wrap items-end gap-3">
              <div>
                <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">Liga</label>
                <select value={ligaId} onChange={(e) => setLigaId(e.target.value)} className="bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100">
                  <option value="">Todas as ligas</option>
                  {ligas.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
                </select>
              </div>
              <div>
                <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">Temporada</label>
                <select value={temporada} onChange={(e) => setTemporada(e.target.value)} className="bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100">
                  <option value="">Todas as temporadas</option>
                  {temporadasDisponiveis.map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
              </div>
              <div>
                <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">Correção</label>
                <select value={usarCalibracao} onChange={(e) => setUsarCalibracao(e.target.value)} className="bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100">
                  <option value="nenhuma">Crua (sem ajuste)</option>
                  <option value="platt">Platt Scaling</option>
                  <option value="isotonic">Isotonic Regression</option>
                </select>
              </div>
              <div>
                <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">Banca inicial (R$)</label>
                <input type="number" step="100" min="1" value={bancaInicial} onChange={(e) => setBancaInicial(e.target.value)}
                  className="w-28 bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100" />
              </div>
              <button onClick={rodar} disabled={rodando}
                className="flex items-center gap-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white font-bold px-4 py-2 rounded-lg text-sm">
                {rodando ? <Loader2 size={16} className="animate-spin" /> : <PlayCircle size={16} />} Rodar simulação
              </button>
            </div>

            {erro && <div className="bg-red-950/30 border border-red-600/40 text-red-300 text-sm px-4 py-3 rounded-xl">{erro}</div>}
          </div>

          {resultados && (
            <>
              {comSumario.length > 0 && (
                <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6">
                  <div className="flex items-center justify-between mb-3">
                    <h2 className="text-sm font-bold text-slate-200 flex items-center gap-2"><TrendingDown className="text-red-400" size={16} /> Comparativo (ordenado por ROI)</h2>
                    <button
                      onClick={emitirRelatorio}
                      className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-slate-900 hover:bg-slate-700 text-slate-200 text-xs border border-slate-700"
                    >
                      <FileDown size={14} /> Emitir relatório
                    </button>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="w-full text-xs">
                      <thead>
                        <tr className="text-slate-500 uppercase text-[10px]">
                          <th className="text-left p-1.5">Modelo</th><th className="text-left p-1.5">Execução</th><th className="text-right p-1.5">ROI</th><th className="text-right p-1.5">CAGR</th>
                          <th className="text-right p-1.5">MDD</th><th className="text-right p-1.5">Acerto</th><th className="text-right p-1.5">CLV médio</th>
                          <th className="text-right p-1.5">Sharpe</th><th className="text-right p-1.5">Sortino</th><th className="text-right p-1.5">Banca final</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-700/50">
                        {comSumario.map((r) => (
                          <tr key={`${r.modelo}__${r.execucao}`}>
                            <td className="p-1.5 text-slate-300 font-semibold">{r.modelo}</td>
                            <td className="p-1.5 text-slate-500">{ROTULO_EXECUCAO[r.execucao] || r.execucao}</td>
                            <td className={`p-1.5 text-right font-bold ${r.sumario.roi_total_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{fmtPct(r.sumario.roi_total_pct)}</td>
                            <td className="p-1.5 text-right text-slate-400">{r.sumario.cagr_pct != null ? fmtPct(r.sumario.cagr_pct) : '—'}</td>
                            <td className="p-1.5 text-right text-red-400">−{fmtPctPlain(r.sumario.mdd_pct)}</td>
                            <td className="p-1.5 text-right text-slate-400">{fmtPctPlain(r.sumario.win_rate_pct)}</td>
                            <td className="p-1.5 text-right text-slate-400">{r.sumario.clv_medio_pct != null ? fmtPct(r.sumario.clv_medio_pct) : '—'}</td>
                            <td className="p-1.5 text-right text-slate-400">{fmtNum(r.sumario.sharpe_simplificado)}</td>
                            <td className="p-1.5 text-right text-slate-400">{fmtNum(r.sumario.sortino_simplificado)}</td>
                            <td className="p-1.5 text-right text-slate-300">R$ {fmtMoney(r.sumario.banca_final)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                {resultados.map((r) => {
                  const chave = `${r.modelo}__${r.execucao}`;
                  return (
                    <CardModelo
                      key={chave}
                      resultado={r}
                      aberto={!!abertos[chave]}
                      onToggle={() => setAbertos((prev) => ({ ...prev, [chave]: !prev[chave] }))}
                    />
                  );
                })}
              </div>
            </>
          )}
        </>
      )}
      <PainelInfoModelo modelName={modeloInfo} aberto={!!modeloInfo} onFechar={() => setModeloInfo(null)} />
    </div>
  );
}
