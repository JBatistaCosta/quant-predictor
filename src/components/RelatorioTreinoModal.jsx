import React, { useState } from 'react';
import { X, BarChart3, TrendingUp, Target, Activity } from 'lucide-react';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip as RechartsTooltip, ResponsiveContainer,
  LineChart, Line, Legend
} from 'recharts';

export default function RelatorioTreinoModal({ config, onClose }) {
  const [abaAtiva, setAbaAtiva] = useState('resumo');

  if (!config || !config.metrics) return null;

  const { metrics } = config;
  const models = metrics.models || {};
  const learningCurves = metrics.learning_curves || {};
  const featImportanceRaw = metrics.feature_importance || {};

  // Formatar Feature Importance
  const featImportanceData = Object.entries(featImportanceRaw)
    .map(([name, value]) => ({ name, value }))
    .sort((a, b) => b.value - a.value)
    .slice(0, 15); // Top 15

  // Cores consistentes
  const COLORS = {
    lightgbm: '#8b5cf6', // violet-500
    xgboost: '#10b981',  // emerald-500
    catboost: '#f59e0b', // amber-500
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
      <div className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-4xl max-h-[90vh] flex flex-col shadow-2xl">
        
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-800">
          <div>
            <h2 className="text-xl font-bold text-white flex items-center gap-2">
              <BarChart3 className="text-violet-400" />
              Relatório de Treinamento
            </h2>
            <p className="text-sm text-slate-400 mt-1">Modelo: <span className="text-slate-300 font-semibold">{config.name}</span></p>
          </div>
          <button onClick={onClose} className="text-slate-500 hover:text-white p-2 rounded-lg hover:bg-slate-800 transition-colors">
            <X size={20} />
          </button>
        </div>

        {/* Abas */}
        <div className="flex border-b border-slate-800 px-6">
          <button
            className={`px-4 py-3 text-sm font-medium border-b-2 transition-colors ${abaAtiva === 'resumo' ? 'border-violet-500 text-violet-400' : 'border-transparent text-slate-400 hover:text-slate-300'}`}
            onClick={() => setAbaAtiva('resumo')}
          >
            Resumo & Métricas
          </button>
          <button
            className={`px-4 py-3 text-sm font-medium border-b-2 transition-colors ${abaAtiva === 'importancia' ? 'border-violet-500 text-violet-400' : 'border-transparent text-slate-400 hover:text-slate-300'}`}
            onClick={() => setAbaAtiva('importancia')}
          >
            Importância (Features)
          </button>
          <button
            className={`px-4 py-3 text-sm font-medium border-b-2 transition-colors ${abaAtiva === 'curvas' ? 'border-violet-500 text-violet-400' : 'border-transparent text-slate-400 hover:text-slate-300'}`}
            onClick={() => setAbaAtiva('curvas')}
          >
            Curvas de Aprendizado
          </button>
        </div>

        {/* Conteúdo */}
        <div className="flex-1 overflow-y-auto p-6">
          
          {abaAtiva === 'resumo' && (
            <div className="space-y-6">
              <div className="grid grid-cols-3 gap-4">
                {Object.entries(models).map(([modelName, mStats]) => (
                  <div key={modelName} className="bg-slate-800 rounded-lg p-4 border border-slate-700">
                    <h3 className="text-sm font-bold text-slate-300 uppercase tracking-wider mb-4 border-b border-slate-700 pb-2" style={{ color: COLORS[modelName] }}>
                      {modelName}
                    </h3>
                    <div className="space-y-3">
                      <div className="flex justify-between items-center">
                        <span className="text-sm text-slate-400 flex items-center gap-1"><Target size={14}/> Acurácia</span>
                        <span className="font-mono text-white">{(mStats.accuracy * 100).toFixed(2)}%</span>
                      </div>
                      <div className="flex justify-between items-center">
                        <span className="text-sm text-slate-400 flex items-center gap-1"><Activity size={14}/> Log Loss</span>
                        <span className="font-mono text-white">{mStats.log_loss?.toFixed(4)}</span>
                      </div>
                      <div className="flex justify-between items-center">
                        <span className="text-sm text-slate-400 flex items-center gap-1"><TrendingUp size={14}/> Brier Score</span>
                        <span className="font-mono text-white">
                          {(mStats.brier_score ?? mStats.brier_score_medio)?.toFixed(4) ?? '—'}
                        </span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {abaAtiva === 'importancia' && (
            <div className="h-[400px] w-full">
              <h3 className="text-sm font-medium text-slate-400 mb-4">Top 15 Features por Importância</h3>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={featImportanceData} layout="vertical" margin={{ top: 5, right: 30, left: 100, bottom: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#334155" horizontal={false} />
                  <XAxis type="number" stroke="#94a3b8" />
                  <YAxis dataKey="name" type="category" stroke="#94a3b8" width={150} tick={{ fontSize: 11 }} />
                  <RechartsTooltip contentStyle={{ backgroundColor: '#1e293b', borderColor: '#334155', color: '#fff' }} />
                  <Bar dataKey="value" fill="#8b5cf6" radius={[0, 4, 4, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}

          {abaAtiva === 'curvas' && (
            <div className="space-y-6">
              {Object.keys(learningCurves).length === 0 && (
                <p className="text-sm text-slate-500 text-center py-8">
                  Curvas de aprendizado disponíveis apenas para CatBoost, XGBoost e LightGBM.
                </p>
              )}
              {Object.entries(learningCurves).map(([modelName, curveData]) => {
                if (!Array.isArray(curveData) || curveData.length === 0) return null;
                const color = COLORS[modelName] || '#8b5cf6';
                return (
                  <div key={modelName} className="h-[280px] w-full bg-slate-800 p-4 rounded-lg border border-slate-700">
                    <h3 className="text-xs font-bold uppercase tracking-wider mb-2" style={{ color }}>
                      {modelName} — Log Loss por Iteração
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
          )}

        </div>
      </div>
    </div>
  );
}
