// src/components/PainelInfoModelo.jsx — slide-out panel com o perfil
// completo de um modelo do Model Benchmarking: períodos do split
// cronológico (treino/validação/teste), hiperparâmetros escolhidos (grid
// search em Validation, ver scripts/backtest_kelly.py) e avaliação prévia
// (log-loss/Brier/Acurácia + ROI/EV+ quando aplicável) por mercado.
// Lê direto de `model_benchmarking_backtest` (RLS pública), reutilizável
// em qualquer tela que liste modelos (ModelBenchmarking.jsx,
// SimulacaoCarteira.jsx) via <BotaoInfoModelo onClick={...}/> + este painel.
import React, { useState, useEffect } from 'react';
import { X, Info, Loader2, AlertTriangle } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';

const ROTULO_MERCADO = {
  '1X2': '1X2',
  'over_under_2.5': 'Over/Under 2.5',
  corners_ou95: 'Escanteios O/U 9,5',
  faixa_gols: 'Faixa de gols',
};
const ORDEM_MERCADOS = ['1X2', 'over_under_2.5', 'corners_ou95', 'faixa_gols'];

function fmtData(d) {
  if (!d) return '—';
  return new Date(d).toLocaleDateString('pt-BR');
}
function fmtPeriodo(inicio, fim) {
  if (!inicio || !fim) return '—';
  return `${fmtData(inicio)} – ${fmtData(fim)}`;
}
function fmtNum(v, casas = 4) {
  return v == null ? '—' : Number(v).toFixed(casas);
}
function fmtPct(v) {
  return v == null ? '—' : `${(v * 100).toFixed(1)}%`;
}

export function BotaoInfoModelo({ onClick, className = '' }) {
  return (
    <button
      onClick={(e) => { e.stopPropagation(); onClick(); }}
      title="Ver detalhes do modelo"
      className={`text-slate-500 hover:text-emerald-400 shrink-0 ${className}`}
    >
      <Info size={14} />
    </button>
  );
}

export default function PainelInfoModelo({ modelName, rotulo, aberto, onFechar }) {
  const [linhas, setLinhas] = useState([]);
  const [carregando, setCarregando] = useState(false);
  const [erro, setErro] = useState(null);

  useEffect(() => {
    if (!aberto || !modelName) return;
    if (!supabaseAtivo) { setErro('Supabase não configurado.'); return; }
    setCarregando(true);
    setErro(null);
    supabase
      .from('model_benchmarking_backtest')
      .select('*')
      .eq('model_name', modelName)
      .then(({ data, error }) => {
        if (error) setErro(error.message);
        else setLinhas(data || []);
        setCarregando(false);
      });
  }, [aberto, modelName]);

  if (!aberto) return null;

  const linhaComPeriodo = linhas.find((l) => l.treino_periodo_inicio) || linhas[0];
  const linhaComHiper = linhas.find((l) => l.hiperparametros);
  const porMercado = ORDEM_MERCADOS.map((chave) => ({ chave, linha: linhas.find((l) => (l.mercado || '1X2') === chave) })).filter(
    (m) => m.linha
  );

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/60" onClick={onFechar} />
      <div className="relative w-full max-w-md bg-slate-900 border-l border-slate-700 h-full overflow-y-auto p-5 shadow-2xl">
        <div className="flex items-start justify-between mb-4">
          <div>
            <h3 className="text-base font-bold text-slate-100">{rotulo || modelName}</h3>
            <p className="text-[11px] text-slate-500 font-mono">{modelName}</p>
          </div>
          <button onClick={onFechar} className="text-slate-500 hover:text-slate-300">
            <X size={18} />
          </button>
        </div>

        {carregando && (
          <div className="flex items-center gap-2 text-slate-500 text-sm py-6 justify-center">
            <Loader2 className="animate-spin" size={16} /> Carregando...
          </div>
        )}
        {erro && (
          <div className="flex items-center gap-2 rounded-lg border border-red-800 bg-red-950/40 p-3 text-sm text-red-300 mb-4">
            <AlertTriangle size={16} /> {erro}
          </div>
        )}
        {!carregando && !erro && linhas.length === 0 && (
          <p className="text-sm text-slate-500">Sem backtest rodado ainda pra este modelo.</p>
        )}

        {!carregando && !erro && linhas.length > 0 && (
          <>
            <section className="mb-5">
              <h4 className="text-[11px] font-bold uppercase text-slate-500 mb-2">Períodos do split cronológico</h4>
              <div className="space-y-1.5 text-sm">
                <div className="flex justify-between gap-3">
                  <span className="text-slate-500">Treino (60%)</span>
                  <span className="text-slate-300 text-right">{fmtPeriodo(linhaComPeriodo?.treino_periodo_inicio, linhaComPeriodo?.treino_periodo_fim)}</span>
                </div>
                <div className="flex justify-between gap-3">
                  <span className="text-slate-500">Validação (20%)</span>
                  <span className="text-slate-300 text-right">{fmtPeriodo(linhaComPeriodo?.validacao_periodo_inicio, linhaComPeriodo?.validacao_periodo_fim)}</span>
                </div>
                <div className="flex justify-between gap-3">
                  <span className="text-slate-500">Teste, out-of-sample (20%)</span>
                  <span className="text-emerald-400 font-semibold text-right">{fmtPeriodo(linhaComPeriodo?.periodo_inicio, linhaComPeriodo?.periodo_fim)}</span>
                </div>
              </div>
              <p className="text-[10px] text-slate-600 mt-2">
                Split sempre cronológico (nunca aleatório) — treino/validação nunca veem o Test Set.
              </p>
            </section>

            <section className="mb-5">
              <h4 className="text-[11px] font-bold uppercase text-slate-500 mb-2">Hiperparâmetros</h4>
              {linhaComHiper?.hiperparametros ? (
                <div className="bg-slate-950 border border-slate-800 rounded-lg p-3 text-xs font-mono text-slate-300 space-y-0.5">
                  {Object.entries(linhaComHiper.hiperparametros).map(([k, v]) => (
                    <div key={k} className="flex justify-between gap-3">
                      <span className="text-slate-500">{k}</span>
                      <span>{String(v)}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-xs text-slate-500">
                  Sem hiperparâmetros de árvore (baseline Dixon-Coles/Poisson, força de time estimada por decaimento temporal, sem grid search).
                </p>
              )}
            </section>

            <section>
              <h4 className="text-[11px] font-bold uppercase text-slate-500 mb-2">Avaliação por mercado (Test Set)</h4>
              <div className="space-y-2">
                {porMercado.map(({ chave, linha }) => (
                  <div key={chave} className="bg-slate-800/60 border border-slate-700/50 rounded-lg p-3">
                    <div className="flex items-center justify-between mb-1.5">
                      <span className="text-xs font-bold text-slate-200">{ROTULO_MERCADO[chave] || chave}</span>
                      {linha.n_apostas > 0 && (
                        <span
                          className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${
                            linha.significativo ? 'bg-emerald-500/15 text-emerald-400' : 'bg-slate-700 text-slate-400'
                          }`}
                        >
                          {linha.significativo ? 'EV+ significativo' : 'sem EV+'}
                        </span>
                      )}
                    </div>
                    <div className="grid grid-cols-3 gap-2 text-[11px]">
                      <div>
                        <div className="text-slate-500">Log-loss</div>
                        <div className="text-slate-300">{fmtNum(linha.log_loss)}</div>
                      </div>
                      <div>
                        <div className="text-slate-500">Brier</div>
                        <div className="text-slate-300">{fmtNum(linha.brier)}</div>
                      </div>
                      <div>
                        <div className="text-slate-500">Acurácia</div>
                        <div className="text-slate-300">{fmtPct(linha.accuracy)}</div>
                      </div>
                    </div>
                    {linha.n_apostas > 0 ? (
                      <div className="grid grid-cols-2 gap-2 text-[11px] mt-2 pt-2 border-t border-slate-700/50">
                        <div>
                          <div className="text-slate-500">ROI (fech.)</div>
                          <div className={linha.roi_medio > 0 ? 'text-emerald-400 font-semibold' : 'text-red-400 font-semibold'}>
                            {fmtPct(linha.roi_medio)}
                          </div>
                        </div>
                        <div>
                          <div className="text-slate-500">Apostas</div>
                          <div className="text-slate-300">{linha.n_apostas}</div>
                        </div>
                      </div>
                    ) : (
                      <p className="text-[10px] text-slate-600 mt-2 pt-2 border-t border-slate-700/50">
                        Sem odds de mercado pra este mercado — só qualidade intrínseca acima.
                      </p>
                    )}
                  </div>
                ))}
              </div>
            </section>
          </>
        )}
      </div>
    </div>
  );
}
