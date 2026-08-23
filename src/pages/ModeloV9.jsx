// src/pages/ModeloV9.jsx
// Painel do Modelo v9: walk-forward CV por fold, curvas de treinamento,
// importância de features e análise exploratória de cobertura.
import React, { useEffect, useState, useMemo } from 'react';
import { supabase } from '../supabaseClient';
import { BarChart3, GitBranch, Search, ChevronUp, ChevronDown, Layers, RefreshCw, TrendingUp } from 'lucide-react';

const COR_MODELO = {
  catboost_v9: '#f97316',
  xgboost_v9: '#3b82f6',
  lightgbm_v9: '#22c55e',
  mlp_v9: '#a855f7',
  stacking_v9: '#f59e0b',
};

function badgeModelo(nome) {
  const cor = COR_MODELO[nome] || '#64748b';
  return (
    <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-bold"
      style={{ background: cor + '22', color: cor, border: `1px solid ${cor}55` }}>
      {nome}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Gráfico de barras por fold
// ---------------------------------------------------------------------------
function MiniBarChart({ dados, metrica, altura = 160 }) {
  if (!dados.length) return null;
  const modelos = [...new Set(dados.map(d => d.modelo))];
  const folds = ['fold_1', 'fold_2', 'fold_3'];
  const valores = dados.map(d => d[metrica] ?? 0);
  const maxVal = Math.max(...valores);
  const minVal = Math.min(...valores);
  const range = maxVal - minVal || 1;
  const largura = 520;
  const margem = { top: 10, right: 10, bottom: 30, left: 46 };
  const innerW = largura - margem.left - margem.right;
  const innerH = altura - margem.top - margem.bottom;
  const foldW = innerW / folds.length;
  const barW = foldW / (modelos.length + 1);
  function yPos(val) { return innerH - ((val - minVal) / range) * innerH; }
  const ticks = 4;
  const tickVals = Array.from({ length: ticks + 1 }, (_, i) => minVal + (range * i) / ticks);
  return (
    <div className="overflow-x-auto">
      <svg width={largura} height={altura} className="text-slate-300">
        <g transform={`translate(${margem.left},${margem.top})`}>
          {tickVals.map((v, i) => (
            <g key={i}>
              <line x1={0} x2={innerW} y1={yPos(v)} y2={yPos(v)} stroke="#334155" strokeDasharray="3 3" />
              <text x={-4} y={yPos(v) + 4} textAnchor="end" fontSize={9} fill="#94a3b8">{v.toFixed(3)}</text>
            </g>
          ))}
          {folds.map((fold, fi) => (
            <g key={fold}>
              {modelos.map((modelo, mi) => {
                const item = dados.find(d => d.fold === fold && d.modelo === modelo);
                if (!item || item[metrica] == null) return null;
                const val = item[metrica];
                const x = fi * foldW + (mi + 0.5) * barW;
                const y = yPos(val);
                const h = innerH - y;
                const cor = COR_MODELO[modelo] || '#64748b';
                return (
                  <g key={modelo}>
                    <rect x={x} y={y} width={barW * 0.8} height={Math.max(h, 1)} fill={cor} opacity={0.8} rx={2} />
                    <title>{modelo} | {fold}: {val.toFixed(4)}</title>
                  </g>
                );
              })}
              <text x={fi * foldW + foldW / 2} y={innerH + 18} textAnchor="middle" fontSize={10} fill="#94a3b8">
                {fold.replace('_', ' ')}
              </text>
            </g>
          ))}
        </g>
      </svg>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Curva de treinamento (linha)
// ---------------------------------------------------------------------------
function CurvaChart({ curva, modelo, altura = 160 }) {
  if (!curva || !curva.length) return (
    <div className="flex items-center justify-center h-20 text-slate-600 text-xs">
      Curva não disponível (MLP / sem early stopping)
    </div>
  );
  const cor = COR_MODELO[modelo] || '#64748b';
  const largura = 460;
  const margem = { top: 8, right: 10, bottom: 24, left: 44 };
  const innerW = largura - margem.left - margem.right;
  const innerH = altura - margem.top - margem.bottom;
  const vals = curva.flatMap(p => [p.treino, p.validacao, p.teste].filter(v => v != null));
  const minV = Math.min(...vals);
  const maxV = Math.max(...vals);
  const range = maxV - minV || 1;
  const maxIt = curva[curva.length - 1].iteracao || curva.length - 1;
  function x(it) { return (it / maxIt) * innerW; }
  function y(v) { return innerH - ((v - minV) / range) * innerH; }
  function pontos(campo) {
    return curva
      .filter(p => p[campo] != null)
      .map(p => `${x(p.iteracao)},${y(p[campo])}`)
      .join(' ');
  }
  const ticks = 3;
  const tickVals = Array.from({ length: ticks + 1 }, (_, i) => minV + (range * i) / ticks);
  return (
    <div className="overflow-x-auto">
      <svg width={largura} height={altura}>
        <g transform={`translate(${margem.left},${margem.top})`}>
          {tickVals.map((v, i) => (
            <g key={i}>
              <line x1={0} x2={innerW} y1={y(v)} y2={y(v)} stroke="#334155" strokeDasharray="3 3" />
              <text x={-4} y={y(v) + 4} textAnchor="end" fontSize={8} fill="#94a3b8">{v.toFixed(3)}</text>
            </g>
          ))}
          <polyline points={pontos('treino')} fill="none" stroke={cor} strokeWidth={1.5} opacity={0.5} />
          {curva.some(p => p.validacao != null) && (
            <polyline points={pontos('validacao')} fill="none" stroke={cor} strokeWidth={2} />
          )}
          {curva.some(p => p.teste != null) && (
            <polyline points={pontos('teste')} fill="none" stroke="#94a3b8" strokeWidth={1.5} strokeDasharray="4 2" />
          )}
          <text x={innerW / 2} y={innerH + 18} textAnchor="middle" fontSize={9} fill="#475569">
            iteração (early-stop em {maxIt})
          </text>
        </g>
      </svg>
      <div className="flex gap-3 mt-1 ml-11 text-[10px] text-slate-500">
        <span style={{ color: cor }}>── treino</span>
        {curva.some(p => p.validacao != null) && <span style={{ color: cor }} className="font-bold">── val</span>}
        {curva.some(p => p.teste != null) && <span className="text-slate-400">- - teste</span>}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Barra horizontal
// ---------------------------------------------------------------------------
function ImportanciaBar({ valor, max }) {
  const pct = max > 0 ? (valor / max) * 100 : 0;
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 bg-slate-700 rounded-full">
        <div className="h-1.5 rounded-full bg-emerald-400" style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs text-slate-400 w-14 text-right">{valor.toFixed(2)}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Componente principal
// ---------------------------------------------------------------------------
export default function ModeloV9() {
  const [aba, setAba] = useState('walkforward');
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState(null);

  const [resultados, setResultados] = useState([]);
  const [importancia, setImportancia] = useState([]);
  const [cobertura, setCobertura] = useState([]);

  const [metricaWF, setMetricaWF] = useState('logloss_test');
  const [mercadoWF, setMercadoWF] = useState('1X2');
  const [ligaFold, setLigaFold] = useState('fold_3');
  const [filtroGrupo, setFiltroGrupo] = useState('Todos');
  const [buscaFeature, setBuscaFeature] = useState('');
  const [ordenacaoCob, setOrdenacaoCob] = useState({ col: 'coverage_pct', dir: 'desc' });

  async function carregar() {
    setCarregando(true);
    setErro(null);
    try {
      const [{ data: r }, { data: imp }, { data: cob }] = await Promise.all([
        supabase.from('model_v9_walkforward_results').select('*').order('fold'),
        supabase.from('model_v9_feature_importance').select('*').order('importance_mean', { ascending: false }),
        supabase.from('model_v9_feature_coverage').select('*').order('feature_group'),
      ]);
      setResultados(r || []);
      setImportancia(imp || []);
      setCobertura(cob || []);
    } catch (e) {
      setErro(e.message || 'Erro ao carregar dados.');
    } finally {
      setCarregando(false);
    }
  }

  useEffect(() => { carregar(); }, []);

  const resultadosFiltrados = useMemo(
    () => resultados.filter(r => (r.market || '1X2') === mercadoWF),
    [resultados, mercadoWF]
  );

  const grupos = useMemo(() => ['Todos', ...[...new Set(cobertura.map(c => c.feature_group))].sort()], [cobertura]);
  const coberturaFiltrada = useMemo(() => {
    let dados = cobertura;
    if (filtroGrupo !== 'Todos') dados = dados.filter(c => c.feature_group === filtroGrupo);
    if (buscaFeature) dados = dados.filter(c => c.feature_name.includes(buscaFeature));
    return [...dados].sort((a, b) => {
      const v = (x) => x[ordenacaoCob.col] ?? 0;
      return ordenacaoCob.dir === 'asc' ? v(a) - v(b) : v(b) - v(a);
    });
  }, [cobertura, filtroGrupo, buscaFeature, ordenacaoCob]);

  const importanciaAgrupada = useMemo(() => {
    const por_grupo = {};
    importancia.forEach(i => { por_grupo[i.feature_group] = (por_grupo[i.feature_group] || 0) + i.importance_mean; });
    return Object.entries(por_grupo).map(([grupo, total]) => ({ grupo, total })).sort((a, b) => b.total - a.total);
  }, [importancia]);

  // Ligas disponíveis no fold selecionado (união de todos os modelos)
  const ligasPorFold = useMemo(() => {
    const rows = resultadosFiltrados.filter(r => r.fold === ligaFold && r.logloss_por_liga);
    const ligas = new Set();
    rows.forEach(r => Object.keys(r.logloss_por_liga || {}).forEach(l => ligas.add(l)));
    return [...ligas].sort();
  }, [resultadosFiltrados, ligaFold]);

  const modelosDisponiveisFold = useMemo(() => {
    return resultadosFiltrados.filter(r => r.fold === ligaFold && r.logloss_por_liga).map(r => r.modelo);
  }, [resultadosFiltrados, ligaFold]);

  const maxImportanciaGrupo = importanciaAgrupada[0]?.total || 1;
  const maxImportanciaFeature = importancia[0]?.importance_mean || 1;
  const semDados = !carregando && resultados.length === 0 && importancia.length === 0 && cobertura.length === 0;

  function sortCob(col) {
    setOrdenacaoCob(prev => ({ col, dir: prev.col === col && prev.dir === 'desc' ? 'asc' : 'desc' }));
  }
  function SortIcon({ col }) {
    if (ordenacaoCob.col !== col) return <ChevronUp size={12} className="text-slate-600" />;
    return ordenacaoCob.dir === 'asc'
      ? <ChevronUp size={12} className="text-emerald-400" />
      : <ChevronDown size={12} className="text-emerald-400" />;
  }

  const abas = [
    { id: 'walkforward', label: 'Walk-forward CV', icone: GitBranch },
    { id: 'curvas', label: 'Curvas de Treino', icone: TrendingUp },
    { id: 'importancia', label: 'Importância de Features', icone: BarChart3 },
    { id: 'cobertura', label: 'Análise Exploratória', icone: Search },
  ];

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-slate-100 flex items-center gap-2">
            <Layers className="text-emerald-400" size={22} /> Modelo v9
          </h1>
          <p className="text-sm text-slate-400 mt-0.5">
            Walk-forward CV · MLP como 4ª família · Stacking (LogísticaRegressão sobre OOF)
          </p>
        </div>
        <button onClick={carregar} className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-slate-200 transition-colors">
          <RefreshCw size={14} /> Atualizar
        </button>
      </div>

      {/* Abas */}
      <div className="flex gap-1 border-b border-slate-700 overflow-x-auto">
        {abas.map(({ id, label, icone: Icone }) => (
          <button key={id} onClick={() => setAba(id)}
            className={`flex items-center gap-1.5 px-4 py-2 text-sm font-semibold border-b-2 whitespace-nowrap transition-colors ${
              aba === id ? 'border-emerald-400 text-emerald-400' : 'border-transparent text-slate-400 hover:text-slate-200'
            }`}>
            <Icone size={15} /> {label}
          </button>
        ))}
      </div>

      {carregando && <div className="text-center py-16 text-slate-500 text-sm">Carregando dados...</div>}
      {erro && <div className="bg-red-900/20 border border-red-700 rounded-lg p-4 text-red-400 text-sm">{erro}</div>}
      {semDados && !erro && (
        <div className="bg-slate-800 border border-slate-700 rounded-xl p-8 text-center text-slate-500 text-sm">
          <p className="font-semibold text-slate-300 mb-2">Nenhum dado disponível ainda.</p>
          <p>Dispare o workflow <code className="bg-slate-700 px-1 rounded">walkforward_cv_v9.yml</code> no GitHub Actions para gerar os resultados.</p>
        </div>
      )}

      {/* ─── Aba: Walk-forward CV ────────────────────────────────────────── */}
      {!carregando && aba === 'walkforward' && resultados.length > 0 && (
        <div className="space-y-6">
          {/* Legenda */}
          <div className="flex flex-wrap gap-2">
            {Object.entries(COR_MODELO).map(([nome, cor]) => (
              <span key={nome} className="text-xs px-2 py-0.5 rounded font-bold"
                style={{ background: cor + '22', color: cor, border: `1px solid ${cor}55` }}>{nome}</span>
            ))}
          </div>

          {/* Seletor de mercado */}
          <div className="flex gap-2 items-center">
            <span className="text-xs text-slate-500 font-semibold">Mercado:</span>
            {[
              { val: '1X2', label: '1X2' },
              { val: 'over_under_2.5', label: 'Over/Under 2.5' },
              { val: 'btts', label: 'BTTS' },
            ].map(({ val, label }) => (
              <button key={val} onClick={() => setMercadoWF(val)}
                className={`text-xs px-3 py-1 rounded-lg font-bold transition-colors ${
                  mercadoWF === val ? 'bg-amber-500/20 text-amber-300 border border-amber-500/40' : 'text-slate-400 hover:text-slate-200 border border-slate-700'
                }`}>
                {label}
              </button>
            ))}
          </div>

          {resultadosFiltrados.length === 0 && (
            <div className="bg-slate-800 border border-slate-700 rounded-xl p-8 text-center text-slate-500 text-sm">
              Nenhum resultado para o mercado <span className="font-bold text-slate-300">{mercadoWF}</span> ainda.
              Rode o workflow <code className="bg-slate-700 px-1 rounded">walkforward_cv_v9.yml</code> para gerar.
            </div>
          )}

          {resultadosFiltrados.length > 0 && (<>
          {/* Seletor de métrica */}
          <div className="flex gap-2 items-center">
            <span className="text-xs text-slate-500 font-semibold">Métrica:</span>
            {['logloss_test', 'brier_score_test', 'accuracy_test'].map(m => (
              <button key={m} onClick={() => setMetricaWF(m)}
                className={`text-xs px-3 py-1 rounded-lg font-bold transition-colors ${
                  metricaWF === m ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40' : 'text-slate-400 hover:text-slate-200 border border-slate-700'
                }`}>
                {m.replace('_test', '').replace('_', ' ')}
              </button>
            ))}
          </div>

          {/* Gráfico de barras */}
          <div className="bg-slate-800 border border-slate-700 rounded-xl p-4">
            <p className="text-xs text-slate-500 font-semibold mb-3 uppercase tracking-wide">
              {metricaWF.replace('_test', '').replace('_', ' ')} por fold
            </p>
            <MiniBarChart dados={resultadosFiltrados} metrica={metricaWF} />
          </div>

          {/* Tabela detalhada */}
          <div className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-700 text-xs text-slate-500 uppercase tracking-wide">
                    <th className="px-4 py-3 text-left">Modelo</th>
                    <th className="px-4 py-3 text-left">Fold</th>
                    <th className="px-4 py-3 text-right">Train</th>
                    <th className="px-4 py-3 text-right">Val</th>
                    <th className="px-4 py-3 text-right">Test</th>
                    <th className="px-4 py-3 text-right">Log-loss</th>
                    <th className="px-4 py-3 text-right">Brier</th>
                    <th className="px-4 py-3 text-right">Acurácia</th>
                  </tr>
                </thead>
                <tbody>
                  {resultadosFiltrados.map((r, i) => (
                    <tr key={i} className="border-b border-slate-700/50 hover:bg-slate-700/20">
                      <td className="px-4 py-2.5">{badgeModelo(r.modelo)}</td>
                      <td className="px-4 py-2.5 text-slate-300 font-mono text-xs">{r.fold}</td>
                      <td className="px-4 py-2.5 text-right text-slate-400 text-xs">{r.n_treino?.toLocaleString()}</td>
                      <td className="px-4 py-2.5 text-right text-slate-400 text-xs">{r.n_val?.toLocaleString()}</td>
                      <td className="px-4 py-2.5 text-right text-slate-400 text-xs">{r.n_test?.toLocaleString()}</td>
                      <td className="px-4 py-2.5 text-right font-mono text-xs text-blue-300">{r.logloss_test?.toFixed(4)}</td>
                      <td className="px-4 py-2.5 text-right font-mono text-xs text-purple-300">{r.brier_score_test?.toFixed(4)}</td>
                      <td className="px-4 py-2.5 text-right font-mono text-xs text-emerald-300">
                        {r.accuracy_test != null ? (r.accuracy_test * 100).toFixed(1) + '%' : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Breakdown por liga — todos os modelos */}
          {ligasPorFold.length > 0 && (
            <div className="bg-slate-800 border border-slate-700 rounded-xl p-4 space-y-4">
              <div className="flex items-center justify-between flex-wrap gap-2">
                <p className="text-xs text-slate-500 font-semibold uppercase tracking-wide">
                  Desempenho por liga
                </p>
                <div className="flex gap-1">
                  {['fold_1', 'fold_2', 'fold_3'].map(f => (
                    <button key={f} onClick={() => setLigaFold(f)}
                      className={`text-xs px-2.5 py-1 rounded font-bold transition-colors ${
                        ligaFold === f ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40' : 'text-slate-400 border border-slate-700 hover:text-slate-200'
                      }`}>
                      {f.replace('_', ' ')}
                    </button>
                  ))}
                </div>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-slate-700 text-slate-500 uppercase tracking-wide">
                      <th className="px-3 py-2 text-left">Liga</th>
                      {modelosDisponiveisFold.map(m => (
                        <th key={m} className="px-3 py-2 text-center" colSpan={2}>
                          {badgeModelo(m)}
                        </th>
                      ))}
                    </tr>
                    <tr className="border-b border-slate-700/50 text-slate-600">
                      <th className="px-3 py-1"></th>
                      {modelosDisponiveisFold.map(m => (
                        <React.Fragment key={m}>
                          <th className="px-2 py-1 text-center font-normal">log-loss</th>
                          <th className="px-2 py-1 text-center font-normal">acc</th>
                        </React.Fragment>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {ligasPorFold.map(liga => {
                      const rowsModelo = modelosDisponiveisFold.map(m =>
                        resultadosFiltrados.find(r => r.fold === ligaFold && r.modelo === m)?.logloss_por_liga?.[liga]
                      );
                      const loglossValues = rowsModelo.map(v => v?.logloss).filter(v => v != null);
                      const melhorLogloss = loglossValues.length ? Math.min(...loglossValues) : null;
                      return (
                        <tr key={liga} className="border-b border-slate-700/30 hover:bg-slate-700/20">
                          <td className="px-3 py-2 text-slate-300 font-medium">{liga}</td>
                          {rowsModelo.map((m, i) => {
                            const isMelhor = m?.logloss != null && m.logloss === melhorLogloss;
                            return (
                              <React.Fragment key={i}>
                                <td className={`px-2 py-2 text-center font-mono ${isMelhor ? 'text-emerald-300 font-bold' : 'text-blue-300'}`}>
                                  {m?.logloss?.toFixed(4) ?? '—'}
                                </td>
                                <td className="px-2 py-2 text-center text-slate-400">
                                  {m?.accuracy != null ? (m.accuracy * 100).toFixed(1) + '%' : '—'}
                                </td>
                              </React.Fragment>
                            );
                          })}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <p className="text-[10px] text-slate-600">Log-loss mais baixo por linha em verde.</p>
            </div>
          )}
          </>)}
        </div>
      )}

      {/* ─── Aba: Curvas de Treinamento ──────────────────────────────────── */}
      {!carregando && aba === 'curvas' && (
        <div className="space-y-6">
          {resultados.length === 0 ? (
            <div className="text-center py-12 text-slate-500 text-sm">Rode o workflow para gerar as curvas.</div>
          ) : (
            <>
              <p className="text-xs text-slate-500">
                Curvas de log-loss por iteração de boosting (early stopping). Linha sólida fina = treino; linha sólida grossa = validação; tracejada = teste.
                MLP e stacking não têm curva iterativa.
              </p>
              {['fold_1', 'fold_2', 'fold_3'].map(fold => {
                const rowsFold = resultados.filter(r => r.fold === fold && r.modelo !== 'stacking_v9');
                if (!rowsFold.length) return null;
                return (
                  <div key={fold} className="bg-slate-800 border border-slate-700 rounded-xl p-4 space-y-4">
                    <p className="text-xs text-slate-400 font-bold uppercase tracking-wide">
                      {fold.replace('_', ' ')} — test {rowsFold[0]?.test_ano}
                    </p>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                      {rowsFold.map(r => (
                        <div key={r.modelo} className="space-y-1">
                          <div className="flex items-center justify-between">
                            {badgeModelo(r.modelo)}
                            <span className="text-[10px] text-slate-500 font-mono">
                              logloss test {r.logloss_test?.toFixed(4)}
                            </span>
                          </div>
                          <CurvaChart curva={r.learning_curve} modelo={r.modelo} />
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })}
            </>
          )}
        </div>
      )}

      {/* ─── Aba: Importância de Features ────────────────────────────────── */}
      {!carregando && aba === 'importancia' && (
        <div className="space-y-6">
          {importancia.length === 0 ? (
            <div className="text-center py-12 text-slate-500 text-sm">Dados de importância não disponíveis. Rode o workflow para gerar.</div>
          ) : (
            <>
              <div className="bg-slate-800 border border-slate-700 rounded-xl p-4">
                <p className="text-xs text-slate-500 font-semibold mb-4 uppercase tracking-wide">Importância total por grupo de feature</p>
                <div className="space-y-2">
                  {importanciaAgrupada.map(({ grupo, total }) => (
                    <div key={grupo} className="flex items-center gap-3">
                      <span className="text-xs text-slate-300 w-40 shrink-0">{grupo}</span>
                      <ImportanciaBar valor={total} max={maxImportanciaGrupo} />
                    </div>
                  ))}
                </div>
              </div>
              <div className="bg-slate-800 border border-slate-700 rounded-xl p-4">
                <p className="text-xs text-slate-500 font-semibold mb-4 uppercase tracking-wide">Top 20 features individuais (CatBoost fold_3)</p>
                <div className="space-y-1.5">
                  {importancia.slice(0, 20).map((item, i) => (
                    <div key={item.feature_name} className="flex items-center gap-2">
                      <span className="text-[10px] text-slate-600 w-4 text-right">{i + 1}</span>
                      <span className="text-xs text-slate-300 w-64 truncate font-mono" title={item.feature_name}>{item.feature_name}</span>
                      <span className="text-[10px] bg-slate-700 text-slate-400 px-1.5 py-0.5 rounded">{item.feature_group}</span>
                      <ImportanciaBar valor={item.importance_mean} max={maxImportanciaFeature} />
                    </div>
                  ))}
                </div>
              </div>
            </>
          )}
        </div>
      )}

      {/* ─── Aba: Análise Exploratória (cobertura) ───────────────────────── */}
      {!carregando && aba === 'cobertura' && (
        <div className="space-y-4">
          {cobertura.length === 0 ? (
            <div className="text-center py-12 text-slate-500 text-sm">Dados de cobertura não disponíveis. Rode o workflow para gerar.</div>
          ) : (
            <>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                {importanciaAgrupada.slice(0, 8).map(({ grupo }) => {
                  const features_grupo = cobertura.filter(c => c.feature_group === grupo);
                  const media_cob = features_grupo.reduce((s, c) => s + c.coverage_pct, 0) / (features_grupo.length || 1);
                  return (
                    <div key={grupo} className="bg-slate-800 border border-slate-700 rounded-xl p-3">
                      <p className="text-xs text-slate-500">{grupo}</p>
                      <p className="text-lg font-bold text-emerald-400">{media_cob.toFixed(1)}%</p>
                      <p className="text-[10px] text-slate-600">{features_grupo.length} features</p>
                    </div>
                  );
                })}
              </div>
              <div className="flex flex-wrap gap-2 items-center">
                <div className="relative">
                  <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
                  <input type="text" value={buscaFeature} onChange={e => setBuscaFeature(e.target.value)}
                    placeholder="Buscar feature..."
                    className="pl-7 pr-3 py-1.5 text-xs bg-slate-800 border border-slate-600 rounded-lg text-slate-200 outline-none focus:border-emerald-500/50 w-52" />
                </div>
                <select value={filtroGrupo} onChange={e => setFiltroGrupo(e.target.value)}
                  className="text-xs bg-slate-800 border border-slate-600 rounded-lg px-2 py-1.5 text-slate-200 outline-none">
                  {grupos.map(g => <option key={g}>{g}</option>)}
                </select>
                <span className="text-xs text-slate-500">{coberturaFiltrada.length} features</span>
              </div>
              <div className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-slate-700 text-xs text-slate-500 uppercase tracking-wide">
                        <th className="px-4 py-3 text-left">Feature</th>
                        <th className="px-4 py-3 text-left">Grupo</th>
                        <th className="px-4 py-3 text-right cursor-pointer hover:text-slate-300" onClick={() => sortCob('coverage_pct')}>
                          <span className="flex items-center justify-end gap-1">Cobertura <SortIcon col="coverage_pct" /></span>
                        </th>
                        <th className="px-4 py-3 text-right cursor-pointer hover:text-slate-300" onClick={() => sortCob('mean_value')}>
                          <span className="flex items-center justify-end gap-1">Média <SortIcon col="mean_value" /></span>
                        </th>
                        <th className="px-4 py-3 text-right cursor-pointer hover:text-slate-300" onClick={() => sortCob('std_value')}>
                          <span className="flex items-center justify-end gap-1">Std <SortIcon col="std_value" /></span>
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {coberturaFiltrada.map((c, i) => {
                        const cobBaixa = c.coverage_pct < 50;
                        const cobMedia = c.coverage_pct >= 50 && c.coverage_pct < 90;
                        return (
                          <tr key={c.feature_name} className={`border-b border-slate-700/50 hover:bg-slate-700/20 ${i % 2 === 0 ? '' : 'bg-slate-800/50'}`}>
                            <td className="px-4 py-2 font-mono text-xs text-slate-300">{c.feature_name}</td>
                            <td className="px-4 py-2">
                              <span className="text-[10px] bg-slate-700 text-slate-400 px-1.5 py-0.5 rounded">{c.feature_group}</span>
                            </td>
                            <td className="px-4 py-2 text-right">
                              <span className={`font-bold text-xs ${cobBaixa ? 'text-red-400' : cobMedia ? 'text-yellow-400' : 'text-emerald-400'}`}>
                                {c.coverage_pct.toFixed(1)}%
                              </span>
                            </td>
                            <td className="px-4 py-2 text-right font-mono text-xs text-slate-400">
                              {c.mean_value != null ? c.mean_value.toFixed(3) : '—'}
                            </td>
                            <td className="px-4 py-2 text-right font-mono text-xs text-slate-400">
                              {c.std_value != null ? c.std_value.toFixed(3) : '—'}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
