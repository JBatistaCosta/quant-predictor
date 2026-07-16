// src/pages/ModelosStats.jsx
// Painel de estatísticas dos modelos de previsão: Brier Score, log-loss,
// comparação com a odd de fechamento média do mercado (devigada) e curva de
// calibração — agrupado por modelo + mercado + liga. Dados vêm todos de uma
// vez de /api/model-stats (poucas dezenas de grupos, filtro é só client-side).
import React, { useState, useEffect, useMemo } from 'react';
import { BarChart3, AlertTriangle, Loader2, Download } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';

const MERCADO_ROTULO = { '1X2': '1X2', 'over_under_2.5': 'Over/Under 2.5 gols', 'corners_over_under_9.5': 'Over/Under 9.5 escanteios' };
const SELECAO_ROTULO = { home: 'Mandante', draw: 'Empate', away: 'Visitante', over: 'Over', under: 'Under' };

function Metrica({ label, modelo, mercado, menorMelhor = true }) {
  if (mercado == null) {
    return (
      <div className="bg-slate-900 border border-slate-700/50 rounded-lg p-3 text-center">
        <div className="text-[10px] text-slate-500 uppercase">{label}</div>
        <div className="text-lg font-bold text-slate-200 mt-1">{modelo.toFixed(4)}</div>
        <div className="text-[10px] text-slate-600 mt-0.5">sem odds pra comparar</div>
      </div>
    );
  }
  const modeloMelhor = menorMelhor ? modelo < mercado : modelo > mercado;
  return (
    <div className="bg-slate-900 border border-slate-700/50 rounded-lg p-3 text-center">
      <div className="text-[10px] text-slate-500 uppercase">{label}</div>
      <div className="flex items-center justify-center gap-2 mt-1">
        <span className={`text-lg font-bold ${modeloMelhor ? 'text-emerald-400' : 'text-red-400'}`}>{modelo.toFixed(4)}</span>
        <span className="text-slate-600 text-xs">vs</span>
        <span className={`text-lg font-bold ${!modeloMelhor ? 'text-emerald-400' : 'text-red-400'}`}>{mercado.toFixed(4)}</span>
      </div>
      <div className="text-[10px] text-slate-600 mt-0.5">modelo vs. mercado (fechamento)</div>
    </div>
  );
}

function Calibracao({ quintis }) {
  if (!quintis || quintis.length === 0) return <p className="text-xs text-slate-600">Sem dado suficiente.</p>;
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
            </div>
            <span className="text-slate-400 w-32 shrink-0 text-right">prev {(q.previsto_medio * 100).toFixed(0)}% · real {(q.real * 100).toFixed(0)}% (n={q.n})</span>
          </div>
        );
      })}
      <p className="text-[10px] text-slate-600 mt-1">Barra cinza = previsto médio. Traço = frequência real (verde se perto, vermelho se longe &gt;8pp).</p>
    </div>
  );
}

function gerarMarkdown(grupos, ligasPorId) {
  let md = `# Relatório de estatísticas dos modelos\n\nGerado em ${new Date().toLocaleString('pt-BR')}\n\n`;
  for (const g of grupos) {
    md += `## ${g.model_name} — ${MERCADO_ROTULO[g.market] || g.market} — ${ligasPorId[g.league_id] || `Liga #${g.league_id}`}\n\n`;
    md += `- Jogos avaliados: ${g.n_jogos}\n`;
    md += `- Log-loss: modelo ${g.log_loss_modelo.toFixed(4)}${g.log_loss_mercado != null ? ` vs. mercado ${g.log_loss_mercado.toFixed(4)}` : ' (sem odds)'}\n`;
    md += `- Brier Score: modelo ${g.brier_modelo.toFixed(4)}${g.brier_mercado != null ? ` vs. mercado ${g.brier_mercado.toFixed(4)}` : ' (sem odds)'}\n\n`;
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

  useEffect(() => {
    (async () => {
      setCarregando(true);
      setErro('');
      try {
        const [respStats, ligasResp] = await Promise.all([
          fetch('/api/model-stats'),
          supabaseAtivo ? supabase.from('leagues').select('id, name') : Promise.resolve({ data: [] }),
        ]);
        const dataStats = await respStats.json();
        if (!respStats.ok) throw new Error(dataStats.error?.message || 'Erro ao carregar estatísticas.');
        setGrupos(dataStats.grupos || []);
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

      {!carregando && grupos.length > 0 && (
        <div className="bg-slate-800 border border-slate-700 rounded-2xl p-4 mb-4 flex flex-wrap gap-3">
          <select value={filtroModelo} onChange={(e) => setFiltroModelo(e.target.value)} className="bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100">
            <option value="">Todos os modelos</option>
            {modelos.map(m => <option key={m} value={m}>{m}</option>)}
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
                  {g.model_name} <span className="text-slate-500">·</span> {MERCADO_ROTULO[g.market] || g.market} <span className="text-slate-500">·</span> {ligasPorId[g.league_id] || `Liga #${g.league_id}`}
                </h2>
                <span className="text-xs text-slate-500">{g.n_jogos} jogos avaliados</span>
              </div>

              <div className="grid grid-cols-2 gap-3 mb-4">
                <Metrica label="Log-loss" modelo={g.log_loss_modelo} mercado={g.log_loss_mercado} />
                <Metrica label="Brier Score" modelo={g.brier_modelo} mercado={g.brier_mercado} />
              </div>

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
                {g.por_selecao.map((s, j) => (
                  <div key={j}>
                    <span className="text-xs text-slate-400 font-semibold">{SELECAO_ROTULO[s.selecao] || s.selecao}</span>
                    <Calibracao quintis={s.calibracao} />
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
