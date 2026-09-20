// src/pages/CalculadoraAposta.jsx — rota /calculadora-aposta
// Pedido do usuário depois de conferir na mão um caso real (Cartões O/U 4.5
// do cartoes_rf, odd da Betano) contra a matriz de confiabilidade EV: uma
// calculadora que recebe modelo/mercado + probabilidade do modelo + odd
// real de uma casa e diz se aquela combinação cai numa célula (faixa de
// odd x faixa de edge) já validada como confiável (sobrevive Bonferroni/
// FDR) -- sem precisar reconferir a matriz de cabeça toda vez.
//
// Lê `matriz_confiabilidade_ev_historico` (atualizada diariamente por
// scripts/matriz_confiabilidade_ev.py) via src/utils/classificarAposta.js
// -- nenhuma lógica de classificação duplicada aqui, só UI.
//
// ESCOPO (decisão do usuário, 20/09): só o critério GERAL odd x edge x
// confiável, reutilizável pra qualquer modelo/mercado que a matriz cobre.
// NÃO embute a restrição extra de liga/casa de aposta descoberta nesta
// sessão pro cartoes_rf (Brasileirão Série B + bet365/betano) -- avisado
// explicitamente na tela pra quem for usar isso pra cartões.
import React, { useState, useEffect, useMemo } from 'react';
import { Calculator, AlertTriangle, Loader2, CheckCircle2, XCircle, HelpCircle } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';
import {
  buscarMatrizConfiabilidadeAtual,
  classificarComMatriz,
  listarModelosMercados,
} from '../utils/classificarAposta';

const COR_NIVEL = {
  bonferroni: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/40',
  fdr: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/40',
  fraco: 'text-amber-400 bg-amber-500/10 border-amber-500/40',
  nao_confiavel: 'text-red-400 bg-red-500/10 border-red-500/40',
  sem_dado: 'text-slate-400 bg-slate-700/30 border-slate-600/40',
};
const ICONE_NIVEL = {
  bonferroni: CheckCircle2,
  fdr: CheckCircle2,
  fraco: AlertTriangle,
  nao_confiavel: XCircle,
  sem_dado: HelpCircle,
};

function fmtPct(v, casas = 1) {
  if (v == null) return '—';
  return `${v >= 0 ? '+' : ''}${(v * 100).toFixed(casas)}%`;
}

export default function CalculadoraAposta() {
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState('');
  const [matriz, setMatriz] = useState([]);

  const [modelo, setModelo] = useState('');
  const [mercado, setMercado] = useState('');
  const [probPct, setProbPct] = useState('');
  const [oddReal, setOddReal] = useState('');

  useEffect(() => {
    if (!supabaseAtivo) { setCarregando(false); setErro('Supabase não configurado — a calculadora precisa da matriz de confiabilidade EV (matriz_confiabilidade_ev_historico).'); return; }
    let cancelado = false;
    (async () => {
      setCarregando(true);
      try {
        const linhas = await buscarMatrizConfiabilidadeAtual(supabase);
        if (cancelado) return;
        setMatriz(linhas);
        if (linhas.length === 0) setErro('Nenhum snapshot da matriz de confiabilidade EV encontrado ainda.');
      } catch (e) {
        if (!cancelado) setErro(e.message);
      } finally {
        if (!cancelado) setCarregando(false);
      }
    })();
    return () => { cancelado = true; };
  }, []);

  const modelosMercados = useMemo(() => listarModelosMercados(matriz), [matriz]);
  const modelosDisponiveis = useMemo(() => Object.keys(modelosMercados).sort(), [modelosMercados]);
  const mercadosDoModelo = modelosMercados[modelo] || [];

  useEffect(() => {
    if (modelosDisponiveis.length > 0 && !modelo) setModelo(modelosDisponiveis[0]);
  }, [modelosDisponiveis, modelo]);
  useEffect(() => {
    if (mercadosDoModelo.length > 0 && !mercadosDoModelo.includes(mercado)) setMercado(mercadosDoModelo[0]);
  }, [mercadosDoModelo, mercado]);

  const probModelo = probPct !== '' ? Number(probPct) / 100 : null;
  const odd = oddReal !== '' ? Number(oddReal) : null;

  const resultado = useMemo(() => {
    if (probModelo == null || odd == null || !modelo || !mercado || Number.isNaN(probModelo) || Number.isNaN(odd)) return null;
    return classificarComMatriz(matriz, modelo, mercado, probModelo, odd);
  }, [matriz, modelo, mercado, probModelo, odd]);

  const ev = probModelo != null && odd != null && !Number.isNaN(probModelo) && !Number.isNaN(odd) ? probModelo * odd - 1 : null;

  const Icone = resultado ? ICONE_NIVEL[resultado.nivel] : null;

  return (
    <div className="max-w-3xl mx-auto space-y-4">
      <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6">
        <h1 className="text-xl font-bold flex items-center gap-2 text-slate-100">
          <Calculator className="text-emerald-400" size={22} /> Calculadora de Aposta
        </h1>
        <p className="text-slate-400 mt-1 text-sm">
          Confere se uma probabilidade do modelo + odd real de uma casa cai numa célula (faixa de odd × faixa de edge)
          já validada como confiável na matriz de confiabilidade EV (<code className="text-slate-300">matriz_confiabilidade_ev_historico</code>,
          atualizada diariamente). Critério: IC95% da média E da mediana do ROI positivos, n≥50 — "confiável" de verdade
          exige também sobreviver à correção de Bonferroni ou FDR.
        </p>
        <div className="mt-3 bg-amber-950/30 border border-amber-600/40 text-amber-300 text-xs px-3 py-2 rounded-lg flex items-start gap-2">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          <span>
            Esta classificação agrega TODAS as ligas e casas de apostas numa célula só. Para <code>cartoes_rf</code>,
            a sessão de 20/09 encontrou que o edge está concentrado no Brasileirão Série B e só aparece contra
            bet365/betano (Pinnacle não mostra edge nenhum) — essa restrição adicional NÃO está aplicada aqui.
          </span>
        </div>
      </div>

      {erro && (
        <div className="bg-red-950/30 border border-red-600/40 text-red-300 text-sm px-4 py-3 rounded-xl flex items-center gap-2">
          <AlertTriangle size={16} /> {erro}
        </div>
      )}

      {carregando ? (
        <div className="flex items-center justify-center py-16 text-slate-500 gap-2">
          <Loader2 className="animate-spin" size={20} /> Carregando matriz de confiabilidade EV...
        </div>
      ) : (
        <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">Modelo</label>
              <select value={modelo} onChange={(e) => setModelo(e.target.value)}
                className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100">
                {modelosDisponiveis.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">Mercado</label>
              <select value={mercado} onChange={(e) => setMercado(e.target.value)}
                className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100">
                {mercadosDoModelo.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">Probabilidade do modelo (%)</label>
              <input type="number" step="0.1" min="0" max="100" value={probPct} onChange={(e) => setProbPct(e.target.value)}
                placeholder="ex: 61.7"
                className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100" />
            </div>
            <div>
              <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">Odd real da casa</label>
              <input type="number" step="0.01" min="1.01" value={oddReal} onChange={(e) => setOddReal(e.target.value)}
                placeholder="ex: 2.05"
                className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100" />
            </div>
          </div>

          {resultado && (
            <div className={`rounded-xl border p-4 ${COR_NIVEL[resultado.nivel]}`}>
              <div className="flex items-center gap-2 font-bold text-sm">
                <Icone size={18} /> {resultado.rotulo}
              </div>
              <div className="grid grid-cols-2 gap-3 mt-3 text-xs text-slate-300">
                <div>Edge (p − 1/odd): <span className="font-bold">{fmtPct(resultado.edge)}</span></div>
                <div>EV (p×odd − 1): <span className="font-bold">{fmtPct(ev)}</span></div>
              </div>
              {resultado.celula && (
                <div className="mt-3 pt-3 border-t border-current/20 text-xs text-slate-300 space-y-1">
                  <div>Célula: odd [{Number(resultado.celula.odd_min).toFixed(2)}, {Number(resultado.celula.odd_max) >= 999 ? '∞' : Number(resultado.celula.odd_max).toFixed(2)}) ×
                    edge [{(resultado.celula.edge_min * 100).toFixed(0)}%, {resultado.celula.edge_max >= 9.99 ? '∞' : `${(resultado.celula.edge_max * 100).toFixed(0)}%`})</div>
                  <div>n={resultado.celula.n} | ROI médio {fmtPct(resultado.celula.roi_medio)} IC95%[{fmtPct(resultado.celula.roi_medio_ic_inf)}, {fmtPct(resultado.celula.roi_medio_ic_sup)}]</div>
                  <div>ROI mediano {fmtPct(resultado.celula.roi_mediano)} IC95%[{fmtPct(resultado.celula.roi_mediano_ic_inf)}, {fmtPct(resultado.celula.roi_mediano_ic_sup)}]</div>
                  {resultado.celula.carteira_banca_final_x != null && (
                    <div>Carteira cronológica: banca final {Number(resultado.celula.carteira_banca_final_x).toFixed(2)}x, drawdown máx {(resultado.celula.carteira_drawdown_maximo * 100).toFixed(1)}% (n={resultado.celula.carteira_n_apostado})</div>
                  )}
                  <div className="text-slate-500">Snapshot: {resultado.celula.data_execucao}</div>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
