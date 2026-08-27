// src/pages/ModelosStats.jsx
// Painel de estatísticas dos modelos de previsão: Brier Score, log-loss,
// comparação com a odd de fechamento média do mercado (devigada) e curva de
// calibração — agrupado por modelo + mercado + liga. Dados vêm todos de uma
// vez de /api/model-stats (poucas dezenas de grupos, filtro é só client-side).
import React, { useState, useEffect, useMemo } from 'react';
import { BarChart3, AlertTriangle, Loader2, Download, TrendingUp, PlayCircle, Settings2, RotateCcw, Save } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';
import { apiUrl } from '../utils/apiUrl';
import CurvaPnlEv from '../components/CurvaPnlEv';

const MERCADO_ROTULO = { '1X2': '1X2', 'over_under_2.5': 'Over/Under 2.5 gols', 'corners_over_under_9.5': 'Over/Under 9.5 escanteios' };
const SELECAO_ROTULO = { home: 'Mandante', draw: 'Empate', away: 'Visitante', over: 'Over', under: 'Under' };
// Mesmos 3 mercados de MERCADOS_COM_RESUMO_PRECALCULADO em api/model-stats.js
// -- a carga normal da página usa `model_stats_resumo` (pré-calculado) pra
// esses, que nunca traz `por_selecao`/calibração em quintis (evita o timeout
// de 3s da role anon num scan ao vivo de model_predictions). O botão
// "Calcular calibração ao vivo" só aparece nesses mercados, e só quando
// clicado -- nunca automático.
const MERCADOS_COM_RESUMO_PRECALCULADO = ['1X2', 'over_under_2.5', 'corners_over_under_9.5'];
// `mercado_pinnacle_devigado` (api/model-stats.js) é a odd de fechamento da
// Pinnacle devigada tratada como se fosse um modelo — mesmo pipeline de
// log-loss/Brier/calibração, só rótulo de exibição muda.
const MODELO_ROTULO = { mercado_pinnacle_devigado: 'Mercado (Pinnacle devigada)' };
const rotuloModelo = (nome) => MODELO_ROTULO[nome] || nome;

function fmt(v, formato) {
  return formato === 'pct' ? `${(v * 100).toFixed(1)}%` : v.toFixed(4);
}

// IC95% por bootstrap (achado #27, CONTEXTO_PROJETO.md — populado por
// scripts/avaliar_ic_modelos_por_liga.py, ausente = script nunca rodou pra
// essa combinação ou amostra <30 partidas, tratado como "sem IC calculado",
// nunca como erro).
function LinhaIc({ ic, formato }) {
  if (!ic || ic[0] == null || ic[1] == null) return null;
  return <div className="text-[10px] text-slate-500 mt-0.5">IC95% [{fmt(ic[0], formato)}, {fmt(ic[1], formato)}]</div>;
}

function Metrica({ label, modelo, mercado, ic, menorMelhor = true, formato = 'num' }) {
  if (modelo == null) {
    return (
      <div className="bg-slate-900 border border-slate-700/50 rounded-lg p-3 text-center">
        <div className="text-[10px] text-slate-500 uppercase">{label}</div>
        <div className="text-lg font-bold text-slate-600 mt-1">—</div>
        <div className="text-[10px] text-slate-600 mt-0.5">sem dado suficiente</div>
      </div>
    );
  }
  if (mercado == null) {
    return (
      <div className="bg-slate-900 border border-slate-700/50 rounded-lg p-3 text-center">
        <div className="text-[10px] text-slate-500 uppercase">{label}</div>
        <div className="text-lg font-bold text-slate-200 mt-1">{fmt(modelo, formato)}</div>
        <LinhaIc ic={ic} formato={formato} />
        <div className="text-[10px] text-slate-600 mt-0.5">sem odds pra comparar</div>
      </div>
    );
  }
  const modeloMelhor = menorMelhor ? modelo < mercado : modelo > mercado;
  return (
    <div className="bg-slate-900 border border-slate-700/50 rounded-lg p-3 text-center">
      <div className="text-[10px] text-slate-500 uppercase">{label}</div>
      <div className="flex items-center justify-center gap-2 mt-1">
        <span className={`text-lg font-bold ${modeloMelhor ? 'text-emerald-400' : 'text-red-400'}`}>{fmt(modelo, formato)}</span>
        <span className="text-slate-600 text-xs">vs</span>
        <span className={`text-lg font-bold ${!modeloMelhor ? 'text-emerald-400' : 'text-red-400'}`}>{fmt(mercado, formato)}</span>
      </div>
      <LinhaIc ic={ic} formato={formato} />
      <div className="text-[10px] text-slate-600 mt-0.5">modelo vs. mercado (fechamento)</div>
    </div>
  );
}

function Calibracao({ quintis }) {
  if (!quintis || quintis.length === 0) return <p className="text-xs text-slate-600">Sem dado suficiente.</p>;
  const temMercado = quintis.some(q => q.mercado_medio != null);
  return (
    <div className="space-y-1">
      {quintis.map((q, i) => {
        const diff = q.real - q.previsto_medio;
        return (
          <div key={i} className="flex items-center gap-2 text-xs">
            <span className="text-slate-500 w-16 shrink-0">faixa {i + 1}/5</span>
            <div className="flex-1 bg-slate-800 rounded h-3 relative overflow-hidden">
              <div className="absolute inset-y-0 bg-slate-600" style={{ width: `${q.previsto_medio * 100}%` }} />
              <div className={`absolute inset-y-0 ${Math.abs(diff) > 0.08 ? 'bg-red-500/70' : 'bg-emerald-500/70'}`} style={{ width: '2px', left: `${q.real * 100}%` }} />
              {q.mercado_medio != null && (
                <div className="absolute inset-y-0 bg-sky-400/80" style={{ width: '2px', left: `${q.mercado_medio * 100}%` }} />
              )}
            </div>
            <span className="text-slate-400 w-48 shrink-0 text-right">
              prev {(q.previsto_medio * 100).toFixed(0)}% · real {(q.real * 100).toFixed(0)}%{q.mercado_medio != null ? ` · mkt ${(q.mercado_medio * 100).toFixed(0)}%` : ''} (n={q.n})
            </span>
          </div>
        );
      })}
      <p className="text-[10px] text-slate-600 mt-1">
        Barra cinza = previsto médio. Traço verde/vermelho = frequência real{temMercado ? '. Traço azul = probabilidade implícita do mercado' : ''}.
      </p>
    </div>
  );
}

function AjusteCalibracao({ g }) {
  if (!g.calibracao_disponivel) return null;
  const linhas = [
    { rotulo: 'Sem ajuste', logLoss: g.log_loss_modelo, brier: g.brier_modelo, acc: g.accuracy_modelo },
    { rotulo: 'Platt Scaling', logLoss: g.log_loss_platt, brier: g.brier_platt, acc: g.accuracy_platt },
    { rotulo: 'Isotonic Regression', logLoss: g.log_loss_isotonic, brier: g.brier_isotonic, acc: g.accuracy_isotonic },
  ];
  const melhorLogLoss = Math.min(...linhas.filter(l => l.logLoss != null).map(l => l.logLoss));
  return (
    <div className="mb-4">
      <span className="text-[10px] uppercase font-bold text-slate-500 block mb-2">Ajuste de calibração (com e sem)</span>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-slate-500 uppercase text-[10px]">
              <th className="text-left p-1.5">Método</th>
              <th className="text-right p-1.5">Log-loss</th>
              <th className="text-right p-1.5">Brier</th>
              <th className="text-right p-1.5">Acurácia</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-700/50">
            {linhas.map((l, i) => (
              <tr key={i}>
                <td className="p-1.5 text-slate-300 font-semibold">{l.rotulo}</td>
                <td className={`p-1.5 text-right ${l.logLoss === melhorLogLoss ? 'text-emerald-400 font-bold' : 'text-slate-300'}`}>{l.logLoss != null ? l.logLoss.toFixed(4) : '—'}</td>
                <td className="p-1.5 text-right text-slate-300">{l.brier != null ? l.brier.toFixed(4) : '—'}</td>
                <td className="p-1.5 text-right text-slate-300">{l.acc != null ? `${(l.acc * 100).toFixed(1)}%` : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// Seleções canônicas por mercado, na ordem de exibição
const SELECOES_POR_MERCADO = {
  '1X2': ['home', 'draw', 'away'],
  'over_under_2.5': ['over', 'under'],
  'corners_over_under_9.5': ['over', 'under'],
  'faixa_gols': ['0-1', '2-3', '4-6', '7+'],
  'faixa_corners': ['≤8', '9-10', '11-12', '13+'],
};

function exportarCSV(partidas, filtroModelo, filtroMercado, ligasPorId) {
  const sels = SELECOES_POR_MERCADO[filtroMercado] || ['home', 'draw', 'away'];
  const headers = [
    'ID', 'Data-hora', 'Liga', 'Mandante', 'Visitante',
    'Gols mandante', 'Gols visitante',
    'Esc. mandante', 'Esc. visitante',
    ...sels.map(s => `Prob. ${SELECAO_ROTULO[s] || s} (%)`),
    ...sels.map(s => `Odd ${SELECAO_ROTULO[s] || s}`),
    'EV estimado (%)', 'Resultado real', 'Acertou',
    'xG mandante (prev)', 'xG mandante (real)',
    'xG visitante (prev)', 'xG visitante (real)',
    'xGOT mandante (prev)', 'xGOT mandante (real)',
    'xGOT visitante (prev)', 'xGOT visitante (real)',
  ];
  const csvEscape = v => `"${String(v ?? '').replace(/"/g, '""')}"`;
  const linhas = partidas.map(p => {
    const dt = p.match_date ? new Date(p.match_date).toLocaleString('pt-BR') : '';
    return [
      p.match_id, dt, ligasPorId[p.league_id] || p.league_id,
      p.mandante, p.visitante,
      p.home_goals ?? '', p.away_goals ?? '',
      p.corners_home ?? '', p.corners_away ?? '',
      ...sels.map(s => p.todas_probs?.[s] != null ? (p.todas_probs[s] * 100).toFixed(2) : ''),
      ...sels.map(s => p.todas_odds?.[s] != null ? p.todas_odds[s].toFixed(2) : ''),
      p.ev_estimado != null ? (p.ev_estimado * 100).toFixed(2) : '',
      p.resultado_real ?? '',
      p.acertou == null ? '' : p.acertou ? 'sim' : 'não',
      p.xg_home_previsto ?? '', p.xg_home_real ?? '',
      p.xg_away_previsto ?? '', p.xg_away_real ?? '',
      p.xgot_home_previsto ?? '', p.xgot_home_real ?? '',
      p.xgot_away_previsto ?? '', p.xgot_away_real ?? '',
    ].map(csvEscape).join(',');
  });
  const csv = [headers.map(csvEscape).join(','), ...linhas].join('\n');
  const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `relatorio_${filtroModelo}_${filtroMercado.replace(/[^a-z0-9]/gi, '_')}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

function RelatorioPartidas({ filtroModelo, filtroMercado, ligasPorId }) {
  const [carregando, setCarregando] = useState(false);
  const [erro, setErro] = useState('');
  const [partidas, setPartidas] = useState(null);

  useEffect(() => {
    if (!filtroModelo || !filtroMercado) { setPartidas(null); return; }
    (async () => {
      setCarregando(true); setErro('');
      try {
        const resp = await fetch(apiUrl(`/api/model-stats?modelo=${encodeURIComponent(filtroModelo)}&mercado=${encodeURIComponent(filtroMercado)}&formato=partidas`));
        const dados = await resp.json();
        if (!resp.ok) throw new Error(dados.error?.message || 'Erro ao carregar relatório.');
        setPartidas(dados.partidas || []);
      } catch (e) {
        setErro(e.message);
      } finally {
        setCarregando(false);
      }
    })();
  }, [filtroModelo, filtroMercado]);

  if (!filtroModelo || !filtroMercado) {
    return (
      <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 mb-4 text-center text-sm text-slate-500">
        Escolha um modelo E um mercado específicos acima pra ver o relatório partida a partida.
      </div>
    );
  }

  const temXg = partidas?.some(p => p.xg_home_previsto != null || p.xg_away_previsto != null);
  const temXgot = partidas?.some(p => p.xgot_home_previsto != null || p.xgot_away_previsto != null);
  const sels = SELECOES_POR_MERCADO[filtroMercado] || ['home', 'draw', 'away'];

  return (
    <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 mb-4">
      <div className="flex items-start justify-between mb-1 gap-2">
        <h2 className="text-sm font-bold text-slate-200">Relatório partida a partida</h2>
        {partidas && partidas.length > 0 && (
          <button
            onClick={() => exportarCSV(partidas, filtroModelo, filtroMercado, ligasPorId)}
            className="flex items-center gap-1.5 text-[11px] px-2.5 py-1 rounded-lg bg-slate-700 hover:bg-slate-600 text-slate-300 hover:text-white transition-colors shrink-0"
          >
            <Download size={12} /> Exportar CSV
          </button>
        )}
      </div>
      <p className="text-[11px] text-slate-500 mb-3">
        {rotuloModelo(filtroModelo)} — {MERCADO_ROTULO[filtroMercado] || filtroMercado}. EV estimado usa a probabilidade do modelo contra a odd real de fechamento (não devigada) na seleção favorecida.
        {(temXg || temXgot) && ' xG/xGOT só em modelos que calculam esses valores (Dixon-Coles, regressores dedicados).'}
      </p>

      {carregando ? (
        <div className="flex items-center gap-2 text-slate-500 text-sm py-6"><Loader2 className="animate-spin" size={16} /> Carregando...</div>
      ) : erro ? (
        <p className="text-xs text-red-400">{erro}</p>
      ) : !partidas || partidas.length === 0 ? (
        <p className="text-xs text-slate-600">Sem partidas finalizadas com previsão pra esse modelo/mercado ainda.</p>
      ) : (
        <div className="overflow-x-auto max-h-[40rem] overflow-y-auto">
          <table className="text-xs whitespace-nowrap">
            <thead className="sticky top-0 bg-slate-800 z-10">
              <tr className="text-slate-500 uppercase text-[10px]">
                <th className="text-left p-1.5">ID</th>
                <th className="text-left p-1.5">Data-hora</th>
                <th className="text-left p-1.5">Mandante</th>
                <th className="text-left p-1.5">Visitante</th>
                <th className="text-center p-1.5">Gols</th>
                <th className="text-center p-1.5">Esc. M</th>
                <th className="text-center p-1.5">Esc. V</th>
                {sels.map(s => (
                  <th key={`prob-${s}`} className="text-right p-1.5">P.{SELECAO_ROTULO[s] || s}</th>
                ))}
                {sels.map(s => (
                  <th key={`odd-${s}`} className="text-right p-1.5">@{SELECAO_ROTULO[s] || s}</th>
                ))}
                <th className="text-right p-1.5">EV</th>
                <th className="text-center p-1.5">✓</th>
                {temXg && <th className="text-right p-1.5">xG M (p/r)</th>}
                {temXg && <th className="text-right p-1.5">xG V (p/r)</th>}
                {temXgot && <th className="text-right p-1.5">xGOT M (p/r)</th>}
                {temXgot && <th className="text-right p-1.5">xGOT V (p/r)</th>}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-700/50">
              {partidas.map(p => {
                const isFav = s => p.selecao_prevista === s;
                return (
                  <tr key={p.match_id} className={p.acertou ? 'bg-emerald-500/5' : ''}>
                    <td className="p-1.5 text-slate-600">{p.match_id}</td>
                    <td className="p-1.5 text-slate-500">
                      {p.match_date ? new Date(p.match_date).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—'}
                    </td>
                    <td className="p-1.5 text-slate-300 max-w-[12rem] truncate">{p.mandante}</td>
                    <td className="p-1.5 text-slate-300 max-w-[12rem] truncate">{p.visitante}</td>
                    <td className="p-1.5 text-center text-slate-300 font-mono">
                      {p.home_goals != null && p.away_goals != null ? `${p.home_goals}×${p.away_goals}` : '—'}
                    </td>
                    <td className="p-1.5 text-center text-slate-400">{p.corners_home ?? '—'}</td>
                    <td className="p-1.5 text-center text-slate-400">{p.corners_away ?? '—'}</td>
                    {sels.map(s => (
                      <td key={`prob-${s}`} className={`p-1.5 text-right ${isFav(s) ? 'text-sky-300 font-semibold' : 'text-slate-400'}`}>
                        {p.todas_probs?.[s] != null ? `${(p.todas_probs[s] * 100).toFixed(1)}%` : '—'}
                      </td>
                    ))}
                    {sels.map(s => (
                      <td key={`odd-${s}`} className={`p-1.5 text-right ${isFav(s) ? 'text-slate-300' : 'text-slate-500'}`}>
                        {p.todas_odds?.[s] != null ? p.todas_odds[s].toFixed(2) : '—'}
                      </td>
                    ))}
                    <td className={`p-1.5 text-right font-bold ${p.ev_estimado == null ? 'text-slate-600' : p.ev_estimado > 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                      {p.ev_estimado != null ? `${p.ev_estimado > 0 ? '+' : ''}${(p.ev_estimado * 100).toFixed(1)}%` : '—'}
                    </td>
                    <td className="p-1.5 text-center">
                      {p.acertou == null ? <span className="text-slate-600">—</span> : p.acertou ? <span className="text-emerald-400 font-bold">✓</span> : <span className="text-red-400">✗</span>}
                    </td>
                    {temXg && <td className="p-1.5 text-right text-slate-500">{p.xg_home_previsto != null ? p.xg_home_previsto.toFixed(2) : '—'} / {p.xg_home_real != null ? p.xg_home_real.toFixed(2) : '—'}</td>}
                    {temXg && <td className="p-1.5 text-right text-slate-500">{p.xg_away_previsto != null ? p.xg_away_previsto.toFixed(2) : '—'} / {p.xg_away_real != null ? p.xg_away_real.toFixed(2) : '—'}</td>}
                    {temXgot && <td className="p-1.5 text-right text-slate-500">{p.xgot_home_previsto != null ? p.xgot_home_previsto.toFixed(2) : '—'} / {p.xgot_home_real != null ? p.xgot_home_real.toFixed(2) : '—'}</td>}
                    {temXgot && <td className="p-1.5 text-right text-slate-500">{p.xgot_away_previsto != null ? p.xgot_away_previsto.toFixed(2) : '—'} / {p.xgot_away_real != null ? p.xgot_away_real.toFixed(2) : '—'}</td>}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function BacktestApostas({ ligasPorId, filtroModelo, filtroMercado, filtroLiga }) {
  const [carregando, setCarregando] = useState(false);
  const [erro, setErro] = useState('');
  const [resultado, setResultado] = useState(null);
  const [edgeMinimo, setEdgeMinimo] = useState(0.02);
  const [staking, setStaking] = useState('flat');
  // Default 'platt' (não 'nenhuma') -- Platt Scaling melhorou o log-loss
  // calibrado na maioria das combinações modelo×mercado medidas (achado
  // #6/CONTEXTO_PROJETO.md); Isotonic piorou em ~metade dos casos com o
  // volume de amostra disponível hoje, então não é um default seguro.
  const [usarCalibracao, setUsarCalibracao] = useState('platt');
  const [grupoCurvaIdx, setGrupoCurvaIdx] = useState(0);

  const rodar = async () => {
    setCarregando(true);
    setErro('');
    try {
      const params = new URLSearchParams({ edge_minimo: edgeMinimo, staking, usar_calibracao: usarCalibracao });
      if (filtroModelo) params.set('modelo', filtroModelo);
      if (filtroMercado) params.set('mercado', filtroMercado);
      if (filtroLiga) params.set('liga_id', filtroLiga);
      const resp = await fetch(apiUrl(`/api/backtest-betting?${params}`));
      const dados = await resp.json();
      if (!resp.ok) throw new Error(dados.error?.message || 'Erro ao rodar backtest.');
      setResultado(dados);
      setGrupoCurvaIdx(0);
    } catch (e) {
      setErro(e.message);
    } finally {
      setCarregando(false);
    }
  };

  return (
    <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 mb-4">
      <h2 className="text-lg font-extrabold flex items-center gap-2 text-slate-100 mb-1">
        <TrendingUp className="text-emerald-400" size={22} /> Backtest de apostas simuladas (EV+)
      </h2>
      <p className="text-slate-400 text-sm mb-4">
        Aposta 1 lado só quando <code className="text-slate-300">p_modelo - p_mercado</code> passa do limiar, na odd real de fechamento — ROI com intervalo de confiança 95% via bootstrap. Só considera "significativo" quando o limite inferior do IC fica acima de zero (senão o edge pode ser só ruído de amostra pequena).
      </p>

      <div className="flex flex-wrap items-end gap-3 mb-4">
        <div>
          <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">Edge mínimo</label>
          <input type="number" step="0.005" min="0" value={edgeMinimo} onChange={(e) => setEdgeMinimo(e.target.value)}
            className="w-24 bg-slate-900 border border-slate-600 rounded-lg px-2 py-2 text-sm text-slate-100" />
        </div>
        <div>
          <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">Staking</label>
          <select value={staking} onChange={(e) => setStaking(e.target.value)} className="bg-slate-900 border border-slate-600 rounded-lg px-2 py-2 text-sm text-slate-100">
            <option value="flat">Flat (1 unidade)</option>
            <option value="kelly">Kelly fracionário</option>
          </select>
        </div>
        {staking === 'kelly' && (
          <div className="max-w-xs">
            <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">Fração Kelly</label>
            <p className="text-xs text-slate-400 py-2">
              Automática por faixa de odd (política de risco): 1/4 até @2.50, 1/5 até @4.00, 1/8 até @8.00, stake fixa acima disso — cada faixa também tem seu próprio corte mínimo de EV e teto de stake.
            </p>
          </div>
        )}
        <div>
          <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">Probabilidade</label>
          <select value={usarCalibracao} onChange={(e) => setUsarCalibracao(e.target.value)} className="bg-slate-900 border border-slate-600 rounded-lg px-2 py-2 text-sm text-slate-100">
            <option value="nenhuma">Crua (sem ajuste)</option>
            <option value="platt">Platt Scaling</option>
            <option value="isotonic">Isotonic Regression</option>
          </select>
        </div>
        <button onClick={rodar} disabled={carregando}
          className="flex items-center gap-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white font-bold px-4 py-2 rounded-lg text-sm">
          {carregando ? <Loader2 size={16} className="animate-spin" /> : <PlayCircle size={16} />} Rodar backtest
        </button>
      </div>

      {erro && <div className="bg-red-950/30 border border-red-600/40 text-red-300 text-sm px-4 py-3 rounded-xl mb-4">{erro}</div>}

      {resultado && (
        resultado.grupos.length === 0 ? (
          <p className="text-sm text-slate-500 text-center py-6">Nenhuma aposta passou do edge mínimo com os filtros atuais.</p>
        ) : (
          <>
            {resultado.resumo_geral && (
              <div className="bg-slate-900 border border-slate-700/50 rounded-xl p-4 mb-4 flex flex-wrap gap-6">
                <div>
                  <div className="text-[10px] text-slate-500 uppercase">Apostas (todos os grupos)</div>
                  <div className="text-lg font-bold text-slate-200">{resultado.resumo_geral.n_apostas}</div>
                </div>
                <div>
                  <div className="text-[10px] text-slate-500 uppercase">ROI geral</div>
                  <div className={`text-lg font-bold ${resultado.resumo_geral.roi > 0 ? 'text-emerald-400' : 'text-red-400'}`}>{(resultado.resumo_geral.roi * 100).toFixed(1)}%</div>
                </div>
                <div>
                  <div className="text-[10px] text-slate-500 uppercase">IC 95%</div>
                  <div className="text-lg font-bold text-slate-300">
                    {resultado.resumo_geral.roi_ic95_inferior != null ? `${(resultado.resumo_geral.roi_ic95_inferior * 100).toFixed(1)}% a ${(resultado.resumo_geral.roi_ic95_superior * 100).toFixed(1)}%` : '—'}
                  </div>
                </div>
                <div>
                  <div className="text-[10px] text-slate-500 uppercase">Estatisticamente EV+?</div>
                  <div className={`text-lg font-bold ${resultado.resumo_geral.significativo ? 'text-emerald-400' : 'text-slate-500'}`}>{resultado.resumo_geral.significativo ? 'Sim' : 'Não'}</div>
                </div>
              </div>
            )}

            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-slate-500 uppercase text-[10px]">
                    <th className="text-left p-1.5">Modelo</th>
                    <th className="text-left p-1.5">Mercado</th>
                    <th className="text-left p-1.5">Seleção</th>
                    <th className="text-left p-1.5">Liga</th>
                    <th className="text-right p-1.5">n</th>
                    <th className="text-right p-1.5">Acerto</th>
                    <th className="text-right p-1.5">ROI</th>
                    <th className="text-right p-1.5">IC 95%</th>
                    <th className="text-center p-1.5">EV+?</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-700/50">
                  {resultado.grupos.map((g, i) => (
                    <tr key={i} className={g.significativo ? 'bg-emerald-500/5' : ''}>
                      <td className="p-1.5 text-slate-300">{g.model_name}</td>
                      <td className="p-1.5 text-slate-400">{MERCADO_ROTULO[g.market] || g.market}</td>
                      <td className="p-1.5 text-slate-400">{SELECAO_ROTULO[g.selection] || g.selection}</td>
                      <td className="p-1.5 text-slate-400">{ligasPorId[g.league_id] || `#${g.league_id}`}</td>
                      <td className="p-1.5 text-right text-slate-400">{g.n_apostas}</td>
                      <td className="p-1.5 text-right text-slate-400">{(g.taxa_acerto * 100).toFixed(0)}%</td>
                      <td className={`p-1.5 text-right font-bold ${g.roi > 0 ? 'text-emerald-400' : 'text-red-400'}`}>{(g.roi * 100).toFixed(1)}%</td>
                      <td className="p-1.5 text-right text-slate-500">
                        {g.roi_ic95_inferior != null ? `${(g.roi_ic95_inferior * 100).toFixed(0)}% / ${(g.roi_ic95_superior * 100).toFixed(0)}%` : '—'}
                      </td>
                      <td className="p-1.5 text-center">{g.significativo ? <span className="text-emerald-400 font-bold">✓</span> : <span className="text-slate-600">—</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="text-[10px] text-slate-600 mt-2">Linhas destacadas em verde = IC 95% do ROI inteiramente acima de zero (EV+ estatisticamente sustentado nesse histórico, não só edge médio positivo).</p>

            <div className="mt-5 pt-4 border-t border-slate-700/50">
              <h3 className="text-sm font-bold text-slate-200 mb-2">Curva de Retorno (PnL) x Valor Esperado (EV)</h3>
              <p className="text-[11px] text-slate-500 mb-3">
                Acumulado cronológico, aposta a aposta — cada grupo (modelo/mercado/seleção/liga) tem sua própria curva, então dá pra ver especializações por campeonato escolhendo abaixo.
              </p>
              <select
                value={grupoCurvaIdx}
                onChange={(e) => setGrupoCurvaIdx(Number(e.target.value))}
                className="bg-slate-900 border border-slate-600 rounded-lg px-2 py-2 text-xs text-slate-100 mb-3 max-w-full"
              >
                {resultado.grupos.map((g, i) => (
                  <option key={i} value={i}>
                    {g.model_name} — {MERCADO_ROTULO[g.market] || g.market} — {SELECAO_ROTULO[g.selection] || g.selection} — {ligasPorId[g.league_id] || `#${g.league_id}`} ({g.n_apostas} apostas)
                  </option>
                ))}
              </select>
              <CurvaPnlEv serieTemporal={resultado.grupos[grupoCurvaIdx]?.serie_temporal} />
            </div>
          </>
        )
      )}
    </div>
  );
}

// Configuração editável do modelo de rating de jogador (player_elo_v1):
// cada parâmetro tem um peso E uma chave de ativar/desativar — pedido do
// usuário pra poder testar combinações e comparar qual configuração rende
// o melhor modelo. Leitura/gravação via api/model-maintenance
// (?tarefa=config-get / config-set) porque a tabela model_config só aceita
// escrita via service role (a UI não escreve direto no banco).
// IMPORTANTE: mudança de config só vale pra partidas processadas DEPOIS
// dela — reprocessar o histórico inteiro com os pesos novos exige o reset.
const PARAMETROS_PLAYER_ELO = [
  { chaveAtivo: 'ativo_gols', chavePeso: 'peso_gols', rotulo: 'Gols', descricao: '+peso por gol (até 3)' },
  { chaveAtivo: 'ativo_assistencias', chavePeso: 'peso_assistencias', rotulo: 'Assistências', descricao: '+peso por assistência (até 3)' },
  { chaveAtivo: 'ativo_finalizacao', chavePeso: 'peso_finalizacao', rotulo: 'Finalização (gols − xG)', descricao: '±peso pela diferença entre gols e xG' },
  { chaveAtivo: 'ativo_criacao', chavePeso: 'peso_criacao', rotulo: 'Criação (xA + chances)', descricao: '+peso por xA e chances criadas' },
];

function ConfigPlayerElo() {
  const [config, setConfig] = useState(null);
  const [carregando, setCarregando] = useState(true);
  const [salvando, setSalvando] = useState(false);
  const [processando, setProcessando] = useState(false);
  const [msg, setMsg] = useState('');
  const [erro, setErro] = useState('');
  const [aberto, setAberto] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const resp = await fetch(apiUrl('/api/model-maintenance?tarefa=config-get&model_name=player_elo_v1'));
        const dados = await resp.json();
        if (!resp.ok) throw new Error(dados.error?.message || 'Erro ao carregar config.');
        setConfig(dados.config);
      } catch (e) {
        setErro(e.message);
      } finally {
        setCarregando(false);
      }
    })();
  }, []);

  const setCampo = (chave, valor) => setConfig(prev => ({ ...prev, [chave]: valor }));

  const salvar = async () => {
    setSalvando(true); setMsg(''); setErro('');
    try {
      const resp = await fetch(apiUrl('/api/model-maintenance?tarefa=config-set&model_name=player_elo_v1'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config),
      });
      const dados = await resp.json();
      if (!resp.ok) throw new Error(dados.error?.message || 'Erro ao salvar.');
      setConfig(dados.config);
      setMsg('Configuração salva. Ela vale pra partidas processadas daqui pra frente — pra reprocessar o histórico inteiro com os pesos novos, use "Resetar e reprocessar".');
    } catch (e) {
      setErro(e.message);
    } finally {
      setSalvando(false);
    }
  };

  const resetarEReprocessar = async () => {
    if (!window.confirm('Isso apaga TODOS os ratings de jogador e o histórico, e reprocessa do zero com a configuração atual (pode levar dezenas de lotes, alguns minutos). Continuar?')) return;
    setProcessando(true); setMsg(''); setErro('');
    try {
      const respReset = await fetch(apiUrl('/api/model-maintenance?tarefa=player-elo-reset'));
      if (!respReset.ok) throw new Error('Falha no reset.');
      let restantes = null;
      let rodada = 0;
      // SEM cap arbitrário de rodadas — um limite fixo (usado antes, 40)
      // já causou reset incompleto em produção quando o volume de partidas
      // cresceu (backfill europeu) além do que o cap suportava, e ainda
      // por cima reportava "concluído" mesmo parando pela metade. O loop
      // real termina quando a API diz que não há mais nada pendente;
      // 500 rodadas (~100k partidas) é só uma rede de segurança contra
      // loop infinito em caso de erro real na API, nunca deveria ser
      // atingido em uso normal.
      for (rodada = 1; rodada <= 500; rodada++) {
        const resp = await fetch(apiUrl('/api/model-maintenance?tarefa=player-elo&limite=200'));
        const dados = await resp.json();
        if (!resp.ok) throw new Error(dados.error?.message || 'Falha ao processar lote.');
        restantes = dados.partidas_restantes;
        setMsg(`Reprocessando... rodada ${rodada}, ${restantes ?? '?'} partidas restantes.`);
        if (!restantes) break;
      }
      if (restantes) {
        setErro(`Parou depois de ${rodada} rodadas com ${restantes} partidas ainda restantes (limite de segurança atingido) — clique em "Resetar e reprocessar" de novo pra continuar, ou reporte se isso persistir.`);
      } else {
        setMsg(`Reprocessamento concluído com a configuração atual (${rodada} rodadas).`);
      }
    } catch (e) {
      setErro(`${e.message} — o reprocesso pode ser retomado sem perder progresso chamando ?tarefa=player-elo de novo.`);
    } finally {
      setProcessando(false);
    }
  };

  return (
    <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 mb-4">
      <button onClick={() => setAberto(a => !a)} className="w-full flex items-center justify-between text-left">
        <h2 className="text-sm font-bold text-slate-200 flex items-center gap-2">
          <Settings2 className="text-emerald-400" size={16} /> Configuração — Rating de jogador (player_elo_v1)
        </h2>
        <span className="text-xs text-slate-500">{aberto ? 'fechar' : 'abrir'}</span>
      </button>

      {aberto && (carregando ? (
        <div className="flex items-center gap-2 text-slate-500 text-sm py-4"><Loader2 className="animate-spin" size={14} /> Carregando...</div>
      ) : config && (
        <div className="mt-4 space-y-4">
          <p className="text-[11px] text-slate-500">
            Âncora: nota da partida do FotMob (holística). Cada parâmetro abaixo pode ser desligado ou ter o peso ajustado — a config vale pra partidas processadas dali em diante; reprocessar o histórico inteiro exige o reset. Pesos default são um chute inicial não calibrado (mesmo status do decaimento XI do Dixon-Coles).
          </p>

          <label className="flex items-center gap-2 text-xs text-slate-300">
            <input type="checkbox" checked={!!config.usar_nota_fotmob} onChange={e => setCampo('usar_nota_fotmob', e.target.checked)} />
            Usar nota do FotMob como âncora (desligado = só os bônus abaixo movem o rating)
          </label>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {PARAMETROS_PLAYER_ELO.map(p => (
              <div key={p.chavePeso} className={`bg-slate-900 rounded-lg p-3 ${config[p.chaveAtivo] ? '' : 'opacity-50'}`}>
                <label className="flex items-center gap-2 text-xs font-semibold text-slate-200">
                  <input type="checkbox" checked={!!config[p.chaveAtivo]} onChange={e => setCampo(p.chaveAtivo, e.target.checked)} />
                  {p.rotulo}
                </label>
                <p className="text-[10px] text-slate-500 mt-0.5 mb-1.5">{p.descricao}</p>
                <input
                  type="number" step="0.05" min="0" max="2"
                  value={config[p.chavePeso] ?? ''}
                  disabled={!config[p.chaveAtivo]}
                  onChange={e => setCampo(p.chavePeso, parseFloat(e.target.value) || 0)}
                  className="w-full bg-slate-800 border border-slate-600 rounded px-2 py-1 text-xs text-slate-100 disabled:opacity-50"
                />
              </div>
            ))}
          </div>

          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className="text-[10px] uppercase text-slate-500 block mb-1">K (velocidade)</label>
              <input type="number" step="1" min="1" max="100" value={config.k ?? ''} onChange={e => setCampo('k', parseFloat(e.target.value) || 20)}
                className="w-full bg-slate-900 border border-slate-600 rounded px-2 py-1 text-xs text-slate-100" />
            </div>
            <div>
              <label className="text-[10px] uppercase text-slate-500 block mb-1">Nota neutra</label>
              <input type="number" step="0.1" min="5" max="8" value={config.nota_neutra ?? ''} onChange={e => setCampo('nota_neutra', parseFloat(e.target.value) || 6.8)}
                className="w-full bg-slate-900 border border-slate-600 rounded px-2 py-1 text-xs text-slate-100" />
            </div>
            <div>
              <label className="text-[10px] uppercase text-slate-500 block mb-1">Minutos mínimos</label>
              <input type="number" step="5" min="0" max="90" value={config.minutos_minimos ?? ''} onChange={e => setCampo('minutos_minimos', parseInt(e.target.value, 10) || 0)}
                className="w-full bg-slate-900 border border-slate-600 rounded px-2 py-1 text-xs text-slate-100" />
            </div>
          </div>

          {msg && <p className="text-xs text-emerald-400">{msg}</p>}
          {erro && <p className="text-xs text-red-400 flex items-center gap-1"><AlertTriangle size={12} /> {erro}</p>}

          <div className="flex items-center gap-2 flex-wrap">
            <button onClick={salvar} disabled={salvando || processando}
              className="flex items-center gap-1.5 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 text-white font-bold px-3 py-2 rounded-lg text-xs">
              {salvando ? <Loader2 className="animate-spin" size={13} /> : <Save size={13} />} Salvar configuração
            </button>
            <button onClick={resetarEReprocessar} disabled={salvando || processando}
              className="flex items-center gap-1.5 bg-slate-700 hover:bg-slate-600 disabled:opacity-40 text-slate-200 font-bold px-3 py-2 rounded-lg text-xs">
              {processando ? <Loader2 className="animate-spin" size={13} /> : <RotateCcw size={13} />} Resetar e reprocessar histórico
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

function gerarMarkdown(grupos, ligasPorId) {
  let md = `# Relatório de estatísticas dos modelos\n\nGerado em ${new Date().toLocaleString('pt-BR')}\n\n`;
  for (const g of grupos) {
    md += `## ${g.model_name} — ${MERCADO_ROTULO[g.market] || g.market} — ${ligasPorId[g.league_id] || `Liga #${g.league_id}`}\n\n`;
    md += `- Jogos avaliados: ${g.n_jogos}\n`;
    md += `- Log-loss: modelo ${g.log_loss_modelo.toFixed(4)}${g.log_loss_mercado != null ? ` vs. mercado ${g.log_loss_mercado.toFixed(4)}` : ' (sem odds)'}${g.log_loss_ic_inf != null ? ` — IC95% [${g.log_loss_ic_inf.toFixed(4)}, ${g.log_loss_ic_sup.toFixed(4)}]` : ''}\n`;
    md += `- Brier Score: modelo ${g.brier_modelo.toFixed(4)}${g.brier_mercado != null ? ` vs. mercado ${g.brier_mercado.toFixed(4)}` : ' (sem odds)'}\n`;
    md += `- Acurácia: modelo ${g.accuracy_modelo != null ? (g.accuracy_modelo * 100).toFixed(1) + '%' : '—'}${g.accuracy_mercado != null ? ` vs. mercado ${(g.accuracy_mercado * 100).toFixed(1)}%` : ' (sem odds)'}${g.accuracy_ic_inf != null ? ` — IC95% [${(g.accuracy_ic_inf * 100).toFixed(1)}%, ${(g.accuracy_ic_sup * 100).toFixed(1)}%]` : ''}\n\n`;
    if (g.calibracao_disponivel) {
      md += `**Ajuste de calibração (com e sem)**\n\n`;
      md += `| Método | Log-loss | Brier | Acurácia |\n|---|---|---|---|\n`;
      md += `| Sem ajuste | ${g.log_loss_modelo.toFixed(4)} | ${g.brier_modelo.toFixed(4)} | ${g.accuracy_modelo != null ? (g.accuracy_modelo * 100).toFixed(1) + '%' : '—'} |\n`;
      md += `| Platt Scaling | ${g.log_loss_platt != null ? g.log_loss_platt.toFixed(4) : '—'} | ${g.brier_platt != null ? g.brier_platt.toFixed(4) : '—'} | ${g.accuracy_platt != null ? (g.accuracy_platt * 100).toFixed(1) + '%' : '—'} |\n`;
      md += `| Isotonic Regression | ${g.log_loss_isotonic != null ? g.log_loss_isotonic.toFixed(4) : '—'} | ${g.brier_isotonic != null ? g.brier_isotonic.toFixed(4) : '—'} | ${g.accuracy_isotonic != null ? (g.accuracy_isotonic * 100).toFixed(1) + '%' : '—'} |\n\n`;
    }
    md += `| Seleção | n | Prob. modelo | Prob. mercado | Edge |\n|---|---|---|---|---|\n`;
    for (const s of g.por_selecao) {
      md += `| ${SELECAO_ROTULO[s.selecao] || s.selecao} | ${s.n} | ${(s.p_modelo_medio * 100).toFixed(1)}% | ${s.p_mercado_medio != null ? (s.p_mercado_medio * 100).toFixed(1) + '%' : '—'} | ${s.edge_medio != null ? (s.edge_medio * 100).toFixed(1) + 'pp' : '—'} |\n`;
    }
    md += '\n';
  }
  return md;
}

export default function ModelosStats() {
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState('');
  const [grupos, setGrupos] = useState([]);
  const [ligasPorId, setLigasPorId] = useState({});

  const [filtroModelo, setFiltroModelo] = useState('');
  const [filtroMercado, setFiltroMercado] = useState('');
  const [filtroLiga, setFiltroLiga] = useState('');
  const [carregandoAoVivo, setCarregandoAoVivo] = useState(() => new Set());
  const [erroAoVivo, setErroAoVivo] = useState({});

  // Recalcula model_name+market ao vivo (todas as ligas de uma vez),
  // ignorando `model_stats_resumo` -- só chamado pelo botão "Calcular
  // calibração ao vivo", nunca automaticamente (ver MERCADOS_COM_RESUMO_
  // PRECALCULADO acima e api/model-stats.js).
  async function calcularAoVivo(modelName, market) {
    const chave = `${modelName}|${market}`;
    setCarregandoAoVivo(prev => new Set(prev).add(chave));
    setErroAoVivo(prev => ({ ...prev, [chave]: '' }));
    try {
      const resp = await fetch(apiUrl(`/api/model-stats?modelo=${encodeURIComponent(modelName)}&mercado=${encodeURIComponent(market)}&forcar_ao_vivo=true`));
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error?.message || 'Erro ao calcular ao vivo.');
      const novosGrupos = data.grupos || [];
      setGrupos(prev => [
        ...prev.filter(g => !(g.model_name === modelName && g.market === market)),
        ...novosGrupos,
      ]);
    } catch (e) {
      setErroAoVivo(prev => ({ ...prev, [chave]: e.message }));
    } finally {
      setCarregandoAoVivo(prev => { const next = new Set(prev); next.delete(chave); return next; });
    }
  }

  useEffect(() => {
    (async () => {
      setCarregando(true);
      setErro('');
      try {
        // A odd devigada da Pinnacle (`mercado_pinnacle_devigado`) vem numa
        // chamada SEPARADA, em paralelo com a chamada principal (sem
        // filtro) -- rodar as duas dentro da MESMA invocação do endpoint
        // (function única) estourava o `maxDuration`/statement_timeout do
        // Postgres na carga inicial do painel (bug real de produção, ver
        // api/model-stats.js). Falha nessa chamada extra não derruba o
        // painel inteiro -- só o "modelo" Pinnacle não aparece na lista.
        const [respStats, respPinnacle, ligasResp] = await Promise.all([
          fetch(apiUrl('/api/model-stats')),
          fetch(apiUrl('/api/model-stats?modelo=mercado_pinnacle_devigado')).catch(() => null),
          supabaseAtivo ? supabase.from('leagues').select('id, name') : Promise.resolve({ data: [] }),
        ]);
        const dataStats = await respStats.json();
        if (!respStats.ok) throw new Error(dataStats.error?.message || 'Erro ao carregar estatísticas.');
        const gruposPinnacle = respPinnacle && respPinnacle.ok ? (await respPinnacle.json()).grupos || [] : [];
        setGrupos([...(dataStats.grupos || []), ...gruposPinnacle]);
        const mapa = {};
        (ligasResp.data || []).forEach(l => { mapa[l.id] = l.name; });
        setLigasPorId(mapa);
      } catch (e) {
        setErro(e.message);
      } finally {
        setCarregando(false);
      }
    })();
  }, []);

  const modelos = useMemo(() => [...new Set(grupos.map(g => g.model_name))].sort(), [grupos]);
  const mercados = useMemo(() => [...new Set(grupos.map(g => g.market))].sort(), [grupos]);
  const ligas = useMemo(() => [...new Set(grupos.map(g => g.league_id))].sort((a, b) => a - b), [grupos]);

  const gruposFiltrados = useMemo(() => grupos.filter(g =>
    (!filtroModelo || g.model_name === filtroModelo) &&
    (!filtroMercado || g.market === filtroMercado) &&
    (!filtroLiga || String(g.league_id) === filtroLiga)
  ), [grupos, filtroModelo, filtroMercado, filtroLiga]);

  const exportarRelatorio = () => {
    const md = gerarMarkdown(gruposFiltrados, ligasPorId);
    const blob = new Blob([md], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `relatorio-modelos-${new Date().toISOString().slice(0, 10)}.md`;
    a.click();
    URL.revokeObjectURL(url);
  };

  if (!supabaseAtivo) {
    return (
      <div className="max-w-5xl mx-auto bg-slate-800 border border-red-500/30 rounded-2xl p-6 text-center">
        <AlertTriangle className="text-red-400 mx-auto mb-2" size={28} />
        <p className="text-slate-300">Supabase não configurado.</p>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto">
      <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 mb-4 flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-extrabold flex items-center gap-3 text-slate-100">
            <BarChart3 className="text-emerald-400" size={28} /> Estatísticas dos Modelos
          </h1>
          <p className="text-slate-400 mt-1 text-sm">Brier Score, log-loss e comparação com a odd de fechamento média do mercado, por modelo/mercado/liga.</p>
        </div>
        <button onClick={exportarRelatorio} disabled={gruposFiltrados.length === 0}
          className="flex items-center gap-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 text-white font-bold px-4 py-2.5 rounded-lg text-sm">
          <Download size={16} /> Exportar relatório (.md)
        </button>
      </div>

      {erro && <div className="bg-red-950/30 border border-red-600/40 text-red-300 text-sm px-4 py-3 rounded-xl mb-4">{erro}</div>}

      <ConfigPlayerElo />

      {!carregando && grupos.length > 0 && (
        <div className="bg-slate-800 border border-slate-700 rounded-2xl p-4 mb-4 flex flex-wrap gap-3">
          <select value={filtroModelo} onChange={(e) => setFiltroModelo(e.target.value)} className="bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100">
            <option value="">Todos os modelos</option>
            {modelos.map(m => <option key={m} value={m}>{rotuloModelo(m)}</option>)}
          </select>
          <select value={filtroMercado} onChange={(e) => setFiltroMercado(e.target.value)} className="bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100">
            <option value="">Todos os mercados</option>
            {mercados.map(m => <option key={m} value={m}>{MERCADO_ROTULO[m] || m}</option>)}
          </select>
          <select value={filtroLiga} onChange={(e) => setFiltroLiga(e.target.value)} className="bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100">
            <option value="">Todas as ligas</option>
            {ligas.map(l => <option key={l} value={l}>{ligasPorId[l] || `Liga #${l}`}</option>)}
          </select>
        </div>
      )}

      {!carregando && grupos.length > 0 && (
        <RelatorioPartidas filtroModelo={filtroModelo} filtroMercado={filtroMercado} ligasPorId={ligasPorId} />
      )}

      {!carregando && grupos.length > 0 && (
        <BacktestApostas ligasPorId={ligasPorId} filtroModelo={filtroModelo} filtroMercado={filtroMercado} filtroLiga={filtroLiga} />
      )}

      {carregando ? (
        <div className="flex items-center justify-center py-16 text-slate-500 gap-2">
          <Loader2 className="animate-spin" size={20} /> Calculando estatísticas...
        </div>
      ) : gruposFiltrados.length === 0 ? (
        <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 text-center text-slate-500 text-sm">Nenhum grupo encontrado.</div>
      ) : (
        <div className="space-y-4">
          {gruposFiltrados.map((g, i) => (
            <div key={i} className="bg-slate-800 border border-slate-700 rounded-2xl p-6">
              <div className="flex items-center justify-between flex-wrap gap-2 mb-4">
                <h2 className="text-sm font-bold text-slate-200">
                  {rotuloModelo(g.model_name)} <span className="text-slate-500">·</span> {MERCADO_ROTULO[g.market] || g.market} <span className="text-slate-500">·</span> {ligasPorId[g.league_id] || `Liga #${g.league_id}`}
                </h2>
                <span className="text-xs text-slate-500">{g.n_jogos} jogos avaliados</span>
              </div>

              <div className="grid grid-cols-3 gap-3 mb-4">
                <Metrica label="Log-loss" modelo={g.log_loss_modelo} mercado={g.log_loss_mercado} ic={[g.log_loss_ic_inf, g.log_loss_ic_sup]} />
                <Metrica label="Brier Score" modelo={g.brier_modelo} mercado={g.brier_mercado} />
                <Metrica label="Acurácia" modelo={g.accuracy_modelo} mercado={g.accuracy_mercado} menorMelhor={false} formato="pct" ic={[g.accuracy_ic_inf, g.accuracy_ic_sup]} />
              </div>

              <AjusteCalibracao g={g} />

              <div className="overflow-x-auto mb-4">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-slate-500 uppercase text-[10px]">
                      <th className="text-left p-1.5">Seleção</th>
                      <th className="text-right p-1.5">n</th>
                      <th className="text-right p-1.5">Prob. modelo</th>
                      <th className="text-right p-1.5">Prob. mercado</th>
                      <th className="text-right p-1.5">Edge</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-700/50">
                    {g.por_selecao.map((s, j) => (
                      <tr key={j}>
                        <td className="p-1.5 text-slate-300 font-semibold">{SELECAO_ROTULO[s.selecao] || s.selecao}</td>
                        <td className="p-1.5 text-right text-slate-400">{s.n}</td>
                        <td className="p-1.5 text-right text-slate-200">{(s.p_modelo_medio * 100).toFixed(1)}%</td>
                        <td className="p-1.5 text-right text-slate-200">{s.p_mercado_medio != null ? `${(s.p_mercado_medio * 100).toFixed(1)}%` : '—'}</td>
                        <td className={`p-1.5 text-right font-bold ${s.edge_medio == null ? 'text-slate-600' : s.edge_medio > 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                          {s.edge_medio != null ? `${s.edge_medio > 0 ? '+' : ''}${(s.edge_medio * 100).toFixed(1)}pp` : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="space-y-3">
                <span className="text-[10px] uppercase font-bold text-slate-500">Calibração por seleção (previsto vs. real, em quintis)</span>
                {g.por_selecao.length === 0 && MERCADOS_COM_RESUMO_PRECALCULADO.includes(g.market) ? (
                  <div className="flex items-center gap-3">
                    <button
                      onClick={() => calcularAoVivo(g.model_name, g.market)}
                      disabled={carregandoAoVivo.has(`${g.model_name}|${g.market}`)}
                      className="flex items-center gap-1.5 bg-slate-700 hover:bg-slate-600 disabled:opacity-40 text-slate-200 font-semibold px-3 py-1.5 rounded-lg text-xs">
                      {carregandoAoVivo.has(`${g.model_name}|${g.market}`)
                        ? <Loader2 className="animate-spin" size={13} />
                        : <PlayCircle size={13} />}
                      Calcular calibração ao vivo
                    </button>
                    <span className="text-[10px] text-slate-600">
                      Este mercado usa estatísticas pré-calculadas (evita timeout) -- não traz calibração por padrão.
                    </span>
                  </div>
                ) : (
                  g.por_selecao.map((s, j) => (
                    <div key={j}>
                      <span className="text-xs text-slate-400 font-semibold">{SELECAO_ROTULO[s.selecao] || s.selecao}</span>
                      <Calibracao quintis={s.calibracao} />
                    </div>
                  ))
                )}
                {erroAoVivo[`${g.model_name}|${g.market}`] && (
                  <p className="text-[11px] text-red-400">{erroAoVivo[`${g.model_name}|${g.market}`]}</p>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
