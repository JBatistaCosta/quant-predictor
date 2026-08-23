import React, { useState, useEffect, useCallback } from 'react';
import { X, BarChart3, TrendingUp, Target, Activity, ChevronLeft, ChevronRight, Wallet, Loader2, AlertTriangle, Download } from 'lucide-react';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip as RechartsTooltip, ResponsiveContainer,
  LineChart, Line, Legend,
} from 'recharts';
import { apiUrl } from '../utils/apiUrl';

// Seleções de cada mercado (para cabeçalhos do relatório)
const TARGET_SELECTIONS = {
  '1x2': ['home', 'draw', 'away'],
  'over_under_2.5': ['over', 'under'],
  'btts': ['yes', 'no'],
  'faixa_gols': ['0-1', '2-3', '4-6', '7+'],
  'corners_over_under_9.5': ['over', 'under'],
  'faixa_corners': ['≤8', '9-10', '11-12', '13+'],
};

// Extrai nome do algoritmo de "Nome do Modelo [algo]"
function extrairAlgo(modelName) {
  return modelName.match(/\[([^\]]+)\]$/)?.[1] || modelName;
}

function fmt4(v) { return v != null ? Number(v).toFixed(4) : '—'; }
function fmtPct(v) { return v != null ? `${(Number(v) * 100).toFixed(1)}%` : '—'; }
function fmtData(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit', year: 'numeric' });
}

function downloadCSV(filename, rows) {
  const BOM = '﻿';
  const csv = BOM + rows.map(r =>
    r.map(cell => {
      const s = String(cell ?? '');
      return s.includes(',') || s.includes('"') || s.includes('\n') ? `"${s.replace(/"/g, '""')}"` : s;
    }).join(',')
  ).join('\r\n');
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename; a.click();
  URL.revokeObjectURL(url);
}

function BotaoExportar({ onClick, carregando = false, titulo = 'Exportar CSV' }) {
  return (
    <button
      onClick={onClick}
      disabled={carregando}
      className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg border border-slate-700 text-slate-400 hover:text-white hover:border-slate-500 disabled:opacity-50 transition-colors"
    >
      {carregando ? <Loader2 size={13} className="animate-spin" /> : <Download size={13} />}
      {titulo}
    </button>
  );
}

const COLORS = {
  catboost: '#8b5cf6',
  xgboost: '#10b981',
  lightgbm: '#f59e0b',
  logistic_regression: '#3b82f6',
  random_forest: '#ec4899',
};
const COLOR_DEFAULT = '#94a3b8';

function corAlgo(algo) { return COLORS[algo] || COLOR_DEFAULT; }

// Combina os algoritmos base com os grupos de stacking (se houver) numa
// única lista pra exibir nas tabelas de resumo por fold -- cada grupo de
// stacking guarda seus resultados em `fold.models["stacking:{name}"]`
// (ver treinar_modelo_custom_wf.py), sem feature_importance associada
// (por isso não entra em TabImportanciaWF, que deriva a lista direto de
// metrics.feature_importance).
function modelosParaResumoWF(metrics) {
  const algoritmos = (metrics.algorithms || []).map((a) => ({ key: a, label: a }));
  const grupos = (metrics.stacking_groups || []).map((g) => ({ key: `stacking:${g.name}`, label: `★ ${g.name}` }));
  return [...algoritmos, ...grupos];
}

// ──────────────────────────────────────────────────────────────
// Sub-componentes de tab
// ──────────────────────────────────────────────────────────────

// O modelo misto (hibrido_parametrico) grava métricas de
// scripts/treinar_modelo_hibrido.py (avaliar + baselines) -- log-verossimilhança
// do placar, log-loss/brier/acurácia do 1X2, baselines de climatologia e
// Poisson sem covariáveis, e opcionalmente escanteios. Nomes de chave
// diferentes dos classificadores (accuracy/log_loss/brier_score), então caem
// em branco (fmtPct/fmt4 de undefined) se renderizados pelo card padrão --
// detecta pela presença de log_verossimilhanca_placar, exclusiva desse modelo.
function ehMetricasParametrico(mStats) {
  return mStats.log_verossimilhanca_placar != null;
}

function MetricaLL({ label, valor, destaque = false }) {
  return (
    <div className="flex justify-between items-center">
      <span className={`text-sm ${destaque ? 'text-emerald-400' : 'text-slate-400'}`}>{label}</span>
      <span className={`font-mono ${destaque ? 'text-emerald-400 font-semibold' : 'text-white'}`}>{fmt4(valor)}</span>
    </div>
  );
}

// Mercados binários/1X2 com sufixo de chave em `mStats` -- "" pro 1X2 (chaves
// sem sufixo, herdadas de antes de existir mais de um mercado) e
// "_<mercado>" pros demais, todos calculados a partir da MESMA matriz de
// placar em `avaliar()` (scripts/treinar_modelo_hibrido.py) -- só os que têm
// odds Pinnacle reais em odds_market pra comparar/simular carteira entram
// aqui (ver MERCADOS_CARTEIRA_SUPORTADOS em api/model-maintenance.js);
// mercado sem odds pra comparar não tem como validar, só apareceria com
// acurácia/log-loss soltos sem contexto.
const MERCADOS_RESUMO = [
  { label: '1X2', sufixo: '1x2' },
  { label: 'Over/Under 2,5', sufixo: 'over_under_2.5' },
  { label: 'Ambas marcam (BTTS)', sufixo: 'btts' },
];

function CardMetricasParametrico({ modelName, mStats }) {
  const temCorners = mStats.log_verossimilhanca_corners != null;
  return (
    <div className="sm:col-span-3 bg-slate-800 rounded-lg p-4 border border-slate-700">
      <h3 className="text-sm font-bold uppercase tracking-wider mb-4 border-b border-slate-700 pb-2" style={{ color: corAlgo(modelName) }}>
        {modelName}
      </h3>
      <div>
        <p className="text-[11px] font-bold uppercase text-slate-500 mb-2">
          Log-verossimilhança do placar (maior = melhor)
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-x-6 gap-y-1.5">
          <MetricaLL label="Modelo misto" valor={mStats.log_verossimilhanca_placar} destaque />
          <MetricaLL label="Climatologia (baseline)" valor={mStats.climatologia_log_verossimilhanca} />
          <MetricaLL label="Poisson sem covariáveis (baseline)" valor={mStats.poisson_sem_covariaveis_log_verossimilhanca} />
        </div>
        <p className="text-[10px] text-slate-600 mt-2">
          Mede a distribuição conjunta inteira (todos os mercados coerentes por construção), não um mercado isolado —
          as métricas por mercado abaixo são uma projeção da mesma matriz, pra comparar direto com classificadores e odds.
        </p>
      </div>

      <div className="mt-4 pt-4 border-t border-slate-700/50">
        <p className="text-[11px] font-bold uppercase text-slate-500 mb-2">Por mercado (mesma escala dos classificadores)</p>
        <div className="overflow-x-auto">
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="border-b border-slate-700">
                <th className="text-left text-slate-500 py-1.5 pr-4 font-normal text-xs">Mercado</th>
                <th className="text-center text-slate-500 py-1.5 px-2 font-normal text-xs">Acurácia</th>
                <th className="text-center text-slate-500 py-1.5 px-2 font-normal text-xs">Log Loss</th>
                <th className="text-center text-slate-500 py-1.5 px-2 font-normal text-xs">Brier</th>
              </tr>
            </thead>
            <tbody>
              {MERCADOS_RESUMO.map(({ label, sufixo }) => (
                <tr key={sufixo} className="border-b border-slate-800">
                  <td className="py-1.5 pr-4 text-slate-300">{label}</td>
                  <td className="py-1.5 px-2 text-center font-mono text-white">{fmtPct(mStats[`acuracia_${sufixo}`])}</td>
                  <td className="py-1.5 px-2 text-center font-mono text-white">{fmt4(mStats[`log_loss_${sufixo}`])}</td>
                  <td className="py-1.5 px-2 text-center font-mono text-white">{fmt4(mStats[`brier_${sufixo}`])}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {temCorners && (
        <div className="mt-4 pt-4 border-t border-slate-700/50">
          <p className="text-[11px] font-bold uppercase text-slate-500 mb-2">
            Escanteios ({mStats.n_teste_corners ?? '—'} partidas com dado real)
          </p>
          <div className="grid grid-cols-2 gap-3 text-sm">
            <MetricaLL label="Log-verossimilhança (total)" valor={mStats.log_verossimilhanca_corners} />
            <MetricaLL label="Log-loss over/under 9,5" valor={mStats.log_loss_corners_ou95} />
          </div>
        </div>
      )}
      <p className="text-[10px] text-slate-600 mt-3 pt-3 border-t border-slate-700/50">
        n teste: {mStats.n_teste ?? '—'} · ρ (Dixon-Coles): {fmt4(mStats.rho)} · mercados gravados: {mStats.n_mercados_gravados ?? '—'}
        {' · '}modelo derivou dezenas de mercados a mais (placar exato, handicap, faixas) — só os com odds reais entram nesta tabela; use a Carteira Simulada pra ver o resultado prático em 1X2/O-U/BTTS.
      </p>
    </div>
  );
}

function TabResumoSimples({ metrics }) {
  const models = metrics.models || {};
  return (
    <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
      {Object.entries(models).map(([modelName, mStats]) =>
        ehMetricasParametrico(mStats) ? (
          <CardMetricasParametrico key={modelName} modelName={modelName} mStats={mStats} />
        ) : (
          <div key={modelName} className="bg-slate-800 rounded-lg p-4 border border-slate-700">
            <h3 className="text-sm font-bold uppercase tracking-wider mb-4 border-b border-slate-700 pb-2" style={{ color: corAlgo(modelName) }}>
              {modelName}
            </h3>
            <div className="space-y-3">
              <div className="flex justify-between items-center">
                <span className="text-sm text-slate-400 flex items-center gap-1"><Target size={14} /> Acurácia</span>
                <span className="font-mono text-white">{fmtPct(mStats.accuracy)}</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-sm text-slate-400 flex items-center gap-1"><Activity size={14} /> Log Loss</span>
                <span className="font-mono text-white">{fmt4(mStats.log_loss)}</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-sm text-slate-400 flex items-center gap-1"><TrendingUp size={14} /> Brier Score</span>
                <span className="font-mono text-white">{fmt4(mStats.brier_score ?? mStats.brier_score_medio)}</span>
              </div>
            </div>
          </div>
        )
      )}
    </div>
  );
}

function TabResumoWF({ metrics }) {
  const folds = metrics.folds || [];
  const modelos = modelosParaResumoWF(metrics);

  function melhorCalibrado(m) {
    const p = m.logloss_calibrado_platt;
    const i = m.logloss_calibrado_isotonic;
    if (p == null && i == null) return null;
    if (p == null) return i;
    if (i == null) return p;
    return Math.min(Number(p), Number(i));
  }

  function exportar() {
    const header = ['Fold', 'Ano Teste', 'N Teste',
      ...modelos.flatMap(({ label }) => [`${label} LogLoss`, `${label} Acurácia %`, `${label} Brier`, `${label} LL Cal`])
    ];
    const rows = folds.map(fold => [
      fold.fold, fold.test_ano, fold.n_test,
      ...modelos.flatMap(({ key }) => {
        const m = fold.models?.[key] || {};
        const cal = melhorCalibrado(m);
        return [
          m.logloss_test != null ? Number(m.logloss_test).toFixed(4) : '',
          m.accuracy_test != null ? (Number(m.accuracy_test) * 100).toFixed(1) : '',
          m.brier_score_test != null ? Number(m.brier_score_test).toFixed(4) : '',
          cal != null ? Number(cal).toFixed(4) : '',
        ];
      }),
    ]);
    downloadCSV('resumo_treinamento_wf.csv', [header, ...rows]);
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-xs text-slate-500">
          Fold 1: Test 2023 | Fold 2: Test 2024 | Fold 3: Test 2025 (out-of-sample real)
          {modelos.some(({ key }) => key.startsWith('stacking:')) && ' · ★ = grupo de stacking'}
        </p>
        <BotaoExportar onClick={exportar} />
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="border-b border-slate-700">
              <th className="text-left text-slate-400 py-2 pr-4 font-medium text-xs">Fold / Test</th>
              {modelos.map(({ key, label }) => (
                <th key={key} colSpan={4} className="text-center pb-2 px-2 font-medium text-xs" style={{ color: corAlgo(key) }}>
                  {label}
                </th>
              ))}
            </tr>
            <tr className="border-b border-slate-700">
              <th className="text-left text-slate-500 py-1 pr-4 text-xs font-normal">n_teste</th>
              {modelos.map(({ key }) => (
                <React.Fragment key={key}>
                  <th className="text-center text-slate-500 text-xs font-normal py-1 px-1">LogLoss</th>
                  <th className="text-center text-slate-500 text-xs font-normal py-1 px-1">Acurácia</th>
                  <th className="text-center text-slate-500 text-xs font-normal py-1 px-1">Brier</th>
                  <th className="text-center text-emerald-600 text-xs font-normal py-1 px-1" title="Melhor LogLoss calibrado (Platt ou Isotonic)">LL Cal</th>
                </React.Fragment>
              ))}
            </tr>
          </thead>
          <tbody>
            {folds.map((fold) => (
              <tr key={fold.fold} className="border-b border-slate-800 hover:bg-slate-800/50 transition-colors">
                <td className="py-2 pr-4">
                  <div className="text-white font-medium">{fold.fold}</div>
                  <div className="text-slate-500 text-xs">Test {fold.test_ano} · {fold.n_test?.toLocaleString()} jogos</div>
                </td>
                {modelos.map(({ key }) => {
                  const m = fold.models?.[key] || {};
                  const cal = melhorCalibrado(m);
                  const melhorou = cal != null && m.logloss_test != null && cal < Number(m.logloss_test);
                  return (
                    <React.Fragment key={key}>
                      <td className="text-center px-1 font-mono text-xs text-white py-2">{fmt4(m.logloss_test)}</td>
                      <td className="text-center px-1 font-mono text-xs text-white py-2">{fmtPct(m.accuracy_test)}</td>
                      <td className="text-center px-1 font-mono text-xs text-white py-2">{fmt4(m.brier_score_test)}</td>
                      <td className={`text-center px-1 font-mono text-xs py-2 ${cal != null ? (melhorou ? 'text-emerald-400' : 'text-slate-400') : 'text-slate-600'}`}>
                        {cal != null ? fmt4(cal) : '—'}
                      </td>
                    </React.Fragment>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function TabPorLiga({ metrics }) {
  const folds = metrics.folds || [];
  const fold3 = folds.find((f) => f.fold === 'fold_3') || folds[folds.length - 1];
  const modelos = modelosParaResumoWF(metrics);
  const [modeloAtivo, setModeloAtivo] = useState(modelos[0]?.key || '');

  if (!fold3) return <p className="text-slate-500 text-sm">Dados de folds não disponíveis.</p>;

  const ligasData = fold3.models?.[modeloAtivo]?.logloss_por_liga || {};
  const ligasList = Object.entries(ligasData)
    .sort((a, b) => a[1].logloss - b[1].logloss);

  function exportar() {
    const header = ['Liga', 'LogLoss', 'Acurácia %', 'N'];
    const rows = ligasList.map(([liga, s]) => [
      liga,
      s.logloss != null ? Number(s.logloss).toFixed(4) : '',
      s.accuracy != null ? (Number(s.accuracy) * 100).toFixed(1) : '',
      s.n ?? '',
    ]);
    downloadCSV(`por_liga_fold3_${modeloAtivo}.csv`, [header, ...rows]);
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-2">
      <div className="flex gap-2">
        {modelos.map(({ key, label }) => (
          <button
            key={key}
            onClick={() => setModeloAtivo(key)}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors border ${
              modeloAtivo === key
                ? 'border-violet-500 text-white'
                : 'border-slate-700 text-slate-400 hover:border-slate-600'
            }`}
            style={modeloAtivo === key ? { backgroundColor: corAlgo(key) + '33', borderColor: corAlgo(key) } : {}}
          >
            {label}
          </button>
        ))}
      </div>
        <BotaoExportar onClick={exportar} />
      </div>
      <p className="text-xs text-slate-500">Fold 3 — Test {fold3.test_ano} · {Object.keys(ligasData).length} ligas</p>
      {ligasList.length === 0 ? (
        <p className="text-slate-500 text-sm">Sem dados por liga para este algoritmo.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="border-b border-slate-700">
                <th className="text-left text-slate-400 py-2 pr-4 font-medium text-xs">Liga</th>
                <th className="text-center text-slate-400 py-2 px-3 font-medium text-xs">Log Loss</th>
                <th className="text-center text-slate-400 py-2 px-3 font-medium text-xs">Acurácia</th>
                <th className="text-center text-slate-400 py-2 px-3 font-medium text-xs">N</th>
              </tr>
            </thead>
            <tbody>
              {ligasList.map(([liga, stats]) => (
                <tr key={liga} className="border-b border-slate-800 hover:bg-slate-800/50">
                  <td className="py-2 pr-4 text-white">{liga}</td>
                  <td className="text-center px-3 font-mono text-xs text-white py-2">{fmt4(stats.logloss)}</td>
                  <td className="text-center px-3 font-mono text-xs text-white py-2">{fmtPct(stats.accuracy)}</td>
                  <td className="text-center px-3 font-mono text-xs text-slate-400 py-2">{stats.n}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function TabImportanciaWF({ fiRaw }) {
  const algos = Object.keys(fiRaw);
  const [algoAtivo, setAlgoAtivo] = useState(algos[0] || '');
  const data = Object.entries(fiRaw[algoAtivo] || {})
    .map(([name, value]) => ({ name, value }))
    .sort((a, b) => b.value - a.value)
    .slice(0, 15);
  return (
    <div className="space-y-4">
      <div className="flex gap-2 flex-wrap">
        {algos.map((algo) => (
          <button
            key={algo}
            onClick={() => setAlgoAtivo(algo)}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors border ${
              algoAtivo === algo ? 'border-violet-500 text-white' : 'border-slate-700 text-slate-400 hover:border-slate-600'
            }`}
            style={algoAtivo === algo ? { backgroundColor: corAlgo(algo) + '33', borderColor: corAlgo(algo) } : {}}
          >
            {algo}
          </button>
        ))}
      </div>
      <div className="h-[360px] w-full">
        <FeatImportanceChart data={data} cor={corAlgo(algoAtivo)} />
      </div>
    </div>
  );
}

function TabImportancia({ metrics, isWF }) {
  const fiRaw = metrics.feature_importance || {};
  const primeiroValor = Object.values(fiRaw)[0];
  const ehWF = isWF || (primeiroValor != null && typeof primeiroValor === 'object' && !Array.isArray(primeiroValor));

  if (ehWF) return <TabImportanciaWF fiRaw={fiRaw} />;

  const featData = Object.entries(fiRaw)
    .map(([name, value]) => ({ name, value }))
    .sort((a, b) => b.value - a.value)
    .slice(0, 15);
  return (
    <div className="h-[400px] w-full">
      <FeatImportanceChart data={featData} cor="#8b5cf6" />
    </div>
  );
}

function FeatImportanceChart({ data, cor }) {
  if (!data.length) return <p className="text-slate-500 text-sm">Sem dados de importância.</p>;
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data} layout="vertical" margin={{ top: 5, right: 30, left: 100, bottom: 5 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#334155" horizontal={false} />
        <XAxis type="number" stroke="#94a3b8" />
        <YAxis dataKey="name" type="category" stroke="#94a3b8" width={150} tick={{ fontSize: 11 }} />
        <RechartsTooltip contentStyle={{ backgroundColor: '#1e293b', borderColor: '#334155', color: '#fff' }} />
        <Bar dataKey="value" fill={cor} radius={[0, 4, 4, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

function TabCurvas({ metrics }) {
  const lc = metrics.learning_curves || {};
  const entries = Object.entries(lc).filter(([, v]) => Array.isArray(v) && v.length > 0);

  if (entries.length === 0) {
    return (
      <p className="text-sm text-slate-500 text-center py-8">
        Curvas de aprendizado disponíveis apenas para CatBoost, XGBoost e LightGBM.
      </p>
    );
  }

  return (
    <div className="space-y-6">
      {entries.map(([key, curveData]) => {
        // key pode ser "catboost" (simples) ou "catboost_fold_3" (WF)
        const algoRaw = key.replace(/_fold_\d+$/, '');
        const foldLabel = key.includes('_fold_') ? ` — ${key.match(/fold_\d+$/)?.[0]}` : '';
        const color = COLORS[algoRaw] || COLOR_DEFAULT;
        return (
          <div key={key} className="h-[280px] w-full bg-slate-800 p-4 rounded-lg border border-slate-700">
            <h3 className="text-xs font-bold uppercase tracking-wider mb-2" style={{ color }}>
              {algoRaw}{foldLabel} — Log Loss por Iteração
            </h3>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={curveData} margin={{ top: 5, right: 20, bottom: 20, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#334155" vertical={false} />
                <XAxis dataKey="iteracao" stroke="#94a3b8" tick={{ fontSize: 10 }} label={{ value: 'Iteração', position: 'insideBottom', offset: -10, fill: '#64748b', fontSize: 10 }} />
                <YAxis stroke="#94a3b8" domain={['auto', 'auto']} tick={{ fontSize: 10 }} />
                <RechartsTooltip contentStyle={{ backgroundColor: '#1e293b', borderColor: '#334155', color: '#fff' }} />
                <Legend wrapperStyle={{ fontSize: 11, color: '#94a3b8' }} />
                <Line type="monotone" dataKey="treino" stroke="#475569" dot={false} strokeWidth={1.5} name="Treino" />
                <Line type="monotone" dataKey="validacao" stroke={color} dot={false} strokeWidth={2} name="Validação" />
              </LineChart>
            </ResponsiveContainer>
          </div>
        );
      })}
    </div>
  );
}

function TabRelatorioTeste({ configId, target, modelNames }) {
  const [pagina, setPagina] = useState(0);
  const [dados, setDados] = useState(null);
  const [carregando, setCarregando] = useState(false);
  const [erro, setErro] = useState(null);
  const [exportando, setExportando] = useState(false);

  const carregar = useCallback(async (pg) => {
    setCarregando(true);
    setErro(null);
    try {
      const resp = await fetch(apiUrl(`/api/model-maintenance?tarefa=relatorio-teste&config_id=${configId}&pagina=${pg}`));
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const d = await resp.json();
      setDados(d);
      setPagina(pg);
    } catch (e) {
      setErro(e.message);
    } finally {
      setCarregando(false);
    }
  }, [configId]);

  useEffect(() => { carregar(0); }, [carregar]);

  const exportarCSV = useCallback(async () => {
    if (!dados) return;
    setExportando(true);
    try {
      const sels = TARGET_SELECTIONS[target] || [];
      const algList = (modelNames || []).map(extrairAlgo);

      // Cabeçalho
      const cabecalho = ['Data', 'Liga', 'Mandante', 'Visitante', 'Gols_Mandante', 'Gols_Visitante'];
      for (const algo of algList) {
        for (const sel of sels) {
          cabecalho.push(algList.length > 1 ? `${algo}_${sel}` : sel);
        }
      }

      // Busca todas as páginas
      const totalPgs = Math.ceil((dados.total || 0) / (dados.por_pagina || 50));
      const todasLinhas = [];
      for (let pg = 0; pg < totalPgs; pg++) {
        const resp = await fetch(apiUrl(`/api/model-maintenance?tarefa=relatorio-teste&config_id=${configId}&pagina=${pg}`));
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const d = await resp.json();
        for (const jogo of d.jogos || []) {
          const linha = [
            fmtData(jogo.match_date),
            jogo.league || '',
            jogo.home_team || '',
            jogo.away_team || '',
            jogo.goals_home ?? '',
            jogo.goals_away ?? '',
          ];
          for (let ai = 0; ai < (modelNames || []).length; ai++) {
            const mn = modelNames[ai] || '';
            const algo = algList[ai] || '';
            for (const sel of sels) {
              const prob = jogo.probabilities?.[`${mn}||${sel}`];
              linha.push(prob != null ? (prob * 100).toFixed(2) : '');
            }
          }
          todasLinhas.push(linha);
        }
      }

      const escapar = (v) => {
        const s = String(v);
        return s.includes(',') || s.includes('"') || s.includes('\n') ? `"${s.replace(/"/g, '""')}"` : s;
      };
      const csv = [cabecalho, ...todasLinhas].map((l) => l.map(escapar).join(',')).join('\n');
      const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8;' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `relatorio_teste_${configId.slice(0, 8)}.csv`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setErro(`Exportação falhou: ${e.message}`);
    } finally {
      setExportando(false);
    }
  }, [dados, configId, target, modelNames]);

  const selections = TARGET_SELECTIONS[target] || [];
  const algos = (modelNames || []).map(extrairAlgo);

  async function exportarTodos() {
    setExportando(true);
    try {
      const todosJogos = [];
      let pg = 0;
      let totalPg = 1;
      while (pg < totalPg) {
        const resp = await fetch(apiUrl(`/api/model-maintenance?tarefa=relatorio-teste&config_id=${configId}&pagina=${pg}`));
        if (!resp.ok) throw new Error(`Erro HTTP ${resp.status}`);
        const d = await resp.json();
        todosJogos.push(...(d.jogos || []));
        totalPg = Math.ceil((d.total || 0) / (d.por_pagina || 50));
        pg++;
      }
      const header = [
        'Data', 'Liga', 'Mandante', 'Visitante', 'Gols Casa', 'Gols Visitante',
        ...algos.flatMap(algo => selections.map(sel => `${sel}${algos.length > 1 ? ` [${algo.slice(0, 3)}]` : ''} %`)),
        ...selections.map(sel => `Aber. ${sel}`),
        ...selections.map(sel => `Fech. ${sel}`),
      ];
      const rows = todosJogos.map(jogo => [
        fmtData(jogo.match_date),
        jogo.league || '',
        jogo.home_team || '',
        jogo.away_team || '',
        jogo.goals_home ?? '',
        jogo.goals_away ?? '',
        ...algos.flatMap((algo, idx) => {
          const mn = modelNames?.[idx] || '';
          return selections.map(sel => {
            const p = jogo.probabilities?.[`${mn}||${sel}`];
            return p != null ? (p * 100).toFixed(1) : '';
          });
        }),
        ...selections.map(sel => jogo.odds_abertura?.[sel] != null ? Number(jogo.odds_abertura[sel]).toFixed(2) : ''),
        ...selections.map(sel => jogo.odds_fechamento?.[sel] != null ? Number(jogo.odds_fechamento[sel]).toFixed(2) : ''),
      ]);
      downloadCSV(`relatorio_teste_${configId}.csv`, [header, ...rows]);
    } catch (e) {
      setErro(`Exportação falhou: ${e.message}`);
    } finally {
      setExportando(false);
    }
  }

  if (carregando && !dados) {
    return (
      <div className="flex items-center justify-center py-16 gap-2 text-slate-500">
        <Loader2 size={18} className="animate-spin" /> Carregando partidas...
      </div>
    );
  }
  if (erro) {
    return (
      <div className="flex items-center gap-2 text-red-400 text-sm py-6">
        <AlertTriangle size={16} /> {erro}
      </div>
    );
  }
  if (!dados) return null;

  const total = dados.total || 0;
  const POR_PAGINA = dados.por_pagina || 50;
  const totalPaginas = Math.ceil(total / POR_PAGINA);
  const jogos = dados.jogos || [];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs text-slate-500">
          {total} partidas analisadas na fase de teste
          {total > 0 && ` · Página ${pagina + 1} de ${totalPaginas}`}
        </p>
        <div className="flex items-center gap-2">
          {carregando && <Loader2 size={14} className="animate-spin text-slate-500" />}
          {total > 0 && (
            <BotaoExportar
              onClick={exportarTodos}
              carregando={exportando}
              titulo={exportando ? 'Exportando…' : `Exportar CSV (${total.toLocaleString()})`}
            />
          )}
        </div>
      </div>

      {jogos.length === 0 ? (
        <p className="text-slate-500 text-sm text-center py-8">Nenhuma partida encontrada.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs border-collapse min-w-[700px]">
            <thead>
              <tr className="border-b border-slate-700">
                <th className="text-left text-slate-400 py-2 pr-3 font-medium whitespace-nowrap">Data</th>
                <th className="text-left text-slate-400 py-2 pr-3 font-medium">Liga</th>
                <th className="text-left text-slate-400 py-2 pr-3 font-medium">Partida</th>
                <th className="text-center text-slate-400 py-2 px-2 font-medium">Result.</th>
                {algos.map((algo) =>
                  selections.map((sel) => (
                    <th key={`${algo}||${sel}`} className="text-center text-slate-400 py-2 px-1 font-medium whitespace-nowrap" style={{ color: algos.length > 1 ? corAlgo(algo) + 'cc' : undefined }}>
                      {sel}
                      {algos.length > 1 && (
                        <><br /><span className="font-normal text-slate-600">[{algo.slice(0, 3)}]</span></>
                      )}
                    </th>
                  ))
                )}
                {selections.map((sel) => (
                  <th key={`aber_${sel}`} className="text-center py-2 px-1 font-medium whitespace-nowrap text-xs text-amber-400/70">
                    {sel}<br /><span className="font-normal text-slate-600 text-[10px]">aber.</span>
                  </th>
                ))}
                {selections.map((sel) => (
                  <th key={`fech_${sel}`} className="text-center py-2 px-1 font-medium whitespace-nowrap text-xs text-sky-400/70">
                    {sel}<br /><span className="font-normal text-slate-600 text-[10px]">fech.</span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {jogos.map((jogo) => (
                <tr key={jogo.match_id} className="border-b border-slate-800 hover:bg-slate-800/40 transition-colors">
                  <td className="py-2 pr-3 text-slate-400 whitespace-nowrap">{fmtData(jogo.match_date)}</td>
                  <td className="py-2 pr-3 text-slate-400 max-w-[100px] truncate">{jogo.league || '—'}</td>
                  <td className="py-2 pr-3 text-white whitespace-nowrap">
                    <span className="text-slate-300">{jogo.home_team}</span>
                    <span className="text-slate-600 mx-1">vs</span>
                    <span className="text-slate-300">{jogo.away_team}</span>
                  </td>
                  <td className="py-2 px-2 text-center font-mono text-white whitespace-nowrap">
                    {jogo.goals_home != null ? `${jogo.goals_home}–${jogo.goals_away}` : '—'}
                  </td>
                  {algos.map((algo, algoIdx) => {
                    const mn = modelNames?.[algoIdx] || '';
                    return selections.map((sel) => {
                      const prob = jogo.probabilities?.[`${mn}||${sel}`];
                      return (
                        <td key={`${algo}||${sel}`} className="py-2 px-1 text-center font-mono text-white">
                          {prob != null ? (prob * 100).toFixed(1) : '—'}
                        </td>
                      );
                    });
                  })}
                  {selections.map((sel) => (
                    <td key={`aber_${sel}`} className="py-2 px-1 text-center font-mono text-xs text-amber-300/70">
                      {jogo.odds_abertura?.[sel] != null ? Number(jogo.odds_abertura[sel]).toFixed(2) : '—'}
                    </td>
                  ))}
                  {selections.map((sel) => (
                    <td key={`fech_${sel}`} className="py-2 px-1 text-center font-mono text-xs text-sky-300/70">
                      {jogo.odds_fechamento?.[sel] != null ? Number(jogo.odds_fechamento[sel]).toFixed(2) : '—'}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Paginação */}
      {totalPaginas > 1 && (
        <div className="flex items-center justify-center gap-3 pt-2">
          <button
            onClick={() => carregar(pagina - 1)}
            disabled={pagina === 0 || carregando}
            className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-sm text-slate-400 hover:text-white hover:bg-slate-700 disabled:opacity-40 transition-colors"
          >
            <ChevronLeft size={14} /> Anterior
          </button>
          <span className="text-xs text-slate-500">{pagina + 1} / {totalPaginas}</span>
          <button
            onClick={() => carregar(pagina + 1)}
            disabled={pagina >= totalPaginas - 1 || carregando}
            className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-sm text-slate-400 hover:text-white hover:bg-slate-700 disabled:opacity-40 transition-colors"
          >
            Próxima <ChevronRight size={14} />
          </button>
        </div>
      )}
    </div>
  );
}

function TabCarteiraSimulada({ configId }) {
  const [dados, setDados] = useState(null);
  const [carregando, setCarregando] = useState(false);
  const [erro, setErro] = useState(null);

  useEffect(() => {
    setCarregando(true);
    setErro(null);
    fetch(apiUrl(`/api/model-maintenance?tarefa=backtest-custom&config_id=${configId}`))
      .then((r) => r.json())
      .then(setDados)
      .catch((e) => setErro(e.message))
      .finally(() => setCarregando(false));
  }, [configId]);

  if (carregando) {
    return (
      <div className="flex items-center justify-center py-16 gap-2 text-slate-500">
        <Loader2 size={18} className="animate-spin" /> Calculando carteira simulada...
      </div>
    );
  }
  if (erro) {
    return (
      <div className="flex items-center gap-2 text-red-400 text-sm py-6">
        <AlertTriangle size={16} /> {erro}
      </div>
    );
  }
  if (!dados) return null;

  if (!dados.suportado) {
    return (
      <div className="text-center py-12 text-slate-500">
        <Wallet size={36} className="mx-auto mb-3 opacity-30" />
        <p className="text-sm">{dados.mensagem}</p>
      </div>
    );
  }

  if (dados.n_apostas === 0) {
    return (
      <div className="text-center py-12 text-slate-500">
        <Wallet size={36} className="mx-auto mb-3 opacity-30" />
        <p className="text-sm">{dados.mensagem || 'Nenhuma aposta EV+ encontrada para simular.'}</p>
        <p className="text-xs mt-2">Verifique se há odds Pinnacle pre-closing no banco para as partidas do teste.</p>
      </div>
    );
  }

  const ganho = dados.banca_final - dados.banca_inicial;
  const roiColor = dados.roi_pct >= 0 ? 'text-emerald-400' : 'text-red-400';

  return (
    <div className="space-y-6">
      {/* Stats cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {[
          { label: 'ROI', valor: `${dados.roi_pct >= 0 ? '+' : ''}${dados.roi_pct?.toFixed(1)}%`, destaque: roiColor },
          { label: 'Apostas', valor: dados.n_apostas?.toLocaleString() },
          { label: 'Taxa de acerto', valor: fmtPct(dados.taxa_acerto) },
          { label: 'Banca final', valor: `€ ${dados.banca_final?.toLocaleString('pt-BR', { minimumFractionDigits: 0 })}`, destaque: ganho >= 0 ? 'text-emerald-400' : 'text-red-400' },
        ].map((s) => (
          <div key={s.label} className="bg-slate-800 rounded-lg p-4 border border-slate-700">
            <p className="text-xs text-slate-500 mb-1">{s.label}</p>
            <p className={`text-xl font-bold font-mono ${s.destaque || 'text-white'}`}>{s.valor}</p>
          </div>
        ))}
      </div>

      <p className="text-xs text-slate-500">
        Quarter Kelly · Teto 15%/rodada · EV ≥ 1,02 · Pinnacle pre-closing · Banca inicial: €1.000
        {dados.model_names?.length > 1 ? ' · Probabilidades: média do ensemble' : ''}
      </p>

      {/* Curva de banca */}
      {dados.curva_banca?.length > 1 && (
        <div className="h-[240px] w-full bg-slate-800 p-4 rounded-lg border border-slate-700">
          <h3 className="text-xs font-medium text-slate-400 mb-3">Evolução da Banca</h3>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={dados.curva_banca} margin={{ top: 5, right: 10, bottom: 5, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#334155" vertical={false} />
              <XAxis dataKey="data" stroke="#94a3b8" tick={{ fontSize: 9 }} interval="preserveStartEnd" />
              <YAxis stroke="#94a3b8" tick={{ fontSize: 10 }} domain={['auto', 'auto']} />
              <RechartsTooltip
                contentStyle={{ backgroundColor: '#1e293b', borderColor: '#334155', color: '#fff' }}
                formatter={(v) => [`€ ${Number(v).toFixed(0)}`, 'Banca']}
              />
              <Line type="monotone" dataKey="banca" stroke="#8b5cf6" dot={false} strokeWidth={2} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────
// Componente principal
// ──────────────────────────────────────────────────────────────

export default function RelatorioTreinoModal({ config, onClose }) {
  const [abaAtiva, setAbaAtiva] = useState('resumo');

  if (!config || !config.metrics) return null;

  const { metrics } = config;
  const isWF = metrics.mode === 'walk_forward_cv';
  const modelNames = isWF ? (metrics.model_names || []) : [config.name];

  const ABAS = [
    { id: 'resumo', label: isWF ? 'Resumo WF' : 'Resumo & Métricas' },
    ...(isWF ? [{ id: 'por_liga', label: 'Por Liga' }] : []),
    { id: 'importancia', label: 'Importância' },
    { id: 'curvas', label: 'Curvas' },
    { id: 'relatorio', label: 'Relatório de Teste' },
    { id: 'carteira', label: 'Carteira Simulada' },
  ];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
      <div className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-5xl max-h-[90vh] flex flex-col shadow-2xl">

        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-800 shrink-0">
          <div>
            <h2 className="text-xl font-bold text-white flex items-center gap-2">
              <BarChart3 className="text-violet-400" />
              Relatório de Treinamento
            </h2>
            <p className="text-sm text-slate-400 mt-0.5">
              <span className="text-slate-300 font-semibold">{config.name}</span>
              {isWF && <span className="ml-2 text-xs bg-violet-900/40 text-violet-400 border border-violet-800 rounded px-1.5 py-0.5">Walk-Forward CV</span>}
            </p>
          </div>
          <button onClick={onClose} className="text-slate-500 hover:text-white p-2 rounded-lg hover:bg-slate-800 transition-colors">
            <X size={20} />
          </button>
        </div>

        {/* Abas */}
        <div className="flex border-b border-slate-800 px-6 overflow-x-auto shrink-0">
          {ABAS.map((aba) => (
            <button
              key={aba.id}
              className={`px-4 py-3 text-sm font-medium border-b-2 whitespace-nowrap transition-colors ${
                abaAtiva === aba.id
                  ? 'border-violet-500 text-violet-400'
                  : 'border-transparent text-slate-400 hover:text-slate-300'
              }`}
              onClick={() => setAbaAtiva(aba.id)}
            >
              {aba.label}
            </button>
          ))}
        </div>

        {/* Conteúdo */}
        <div className="flex-1 overflow-y-auto p-6">
          {abaAtiva === 'resumo' && (
            isWF
              ? <TabResumoWF metrics={metrics} />
              : <TabResumoSimples metrics={metrics} />
          )}
          {abaAtiva === 'por_liga' && isWF && <TabPorLiga metrics={metrics} />}
          {abaAtiva === 'importancia' && <TabImportancia metrics={metrics} isWF={isWF} />}
          {abaAtiva === 'curvas' && <TabCurvas metrics={metrics} />}
          {abaAtiva === 'relatorio' && (
            <TabRelatorioTeste
              configId={config.id}
              target={config.target || metrics.target}
              modelNames={modelNames}
            />
          )}
          {abaAtiva === 'carteira' && <TabCarteiraSimulada configId={config.id} />}
        </div>
      </div>
    </div>
  );
}
