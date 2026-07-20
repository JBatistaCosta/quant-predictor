// src/pages/ModelBenchmarking.jsx — rota /model-benchmarking
// Painel dos modelos do Model Benchmarking (dixon_coles_v1 + catboost/
// xgboost/lightgbm em v1, v2 e v3) competindo lado a lado, com o botão que
// dispara o workflow_dispatch do predict.yml no GitHub Actions
// (api/model-maintenance ?tarefa=disparar-predicoes). Leitura direta de
// `predicoes`/`market_odds` (RLS pública) -- só o disparo exige login
// (Authorization: Bearer do access_token do Supabase Auth, verificado no
// servidor).
//
// v2 soma força do elenco (rating Elo-like por jogador, ver
// scripts/dados_historicos.py) às mesmas features de time da v1; v3 soma
// descanso pré-jogo/fadiga (dias desde o último jogo + turnaround
// apertado) à v2 -- dixon_coles_v1 não tem v2/v3 (modelo Poisson de força
// de time, não aceita feature de jogador/fadiga).
import React, { useState, useEffect, useCallback } from 'react';
import { Zap, Loader2, AlertTriangle, TrendingUp, PlayCircle, ChevronDown, ChevronRight } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';
import { useAuth } from '../AuthContext';

const MODELOS_BASE = [
  'dixon_coles_v1',
  'catboost_v1', 'xgboost_v1', 'lightgbm_v1',
  'catboost_v2', 'xgboost_v2', 'lightgbm_v2',
  'catboost_v3', 'xgboost_v3', 'lightgbm_v3',
];
const ROTULO_MODELO = {
  dixon_coles_v1: 'Dixon-Coles',
  catboost_v1: 'CatBoost v1',
  xgboost_v1: 'XGBoost v1',
  lightgbm_v1: 'LightGBM v1',
  catboost_v2: 'CatBoost v2 (+ elenco)',
  xgboost_v2: 'XGBoost v2 (+ elenco)',
  lightgbm_v2: 'LightGBM v2 (+ elenco)',
  catboost_v3: 'CatBoost v3 (+ fadiga)',
  xgboost_v3: 'XGBoost v3 (+ fadiga)',
  lightgbm_v3: 'LightGBM v3 (+ fadiga)',
};

async function buscarPaginado(query) {
  const linhas = [];
  let pagina = 0;
  while (true) {
    const { data, error } = await query.range(pagina * 1000, pagina * 1000 + 999);
    if (error) throw error;
    linhas.push(...(data || []));
    if (!data || data.length < 1000) break;
    pagina++;
  }
  return linhas;
}

function fmtPct(v) {
  return v == null ? '—' : `${(v * 100).toFixed(1)}%`;
}

function fmtPctSigned(v) {
  return v == null ? '—' : `${v >= 0 ? '+' : ''}${(v * 100).toFixed(1)}%`;
}

function fmtNum(v, casas = 4) {
  return v == null ? '—' : Number(v).toFixed(casas);
}

function fmtPeriodo(inicio, fim) {
  if (!inicio || !fim) return '—';
  const f = (d) => new Date(d).toLocaleDateString('pt-BR');
  return `${f(inicio)} – ${f(fim)}`;
}

const MERCADOS_BACKTEST = [
  { chave: '1X2', rotulo: '1X2' },
  { chave: 'over_under_2.5', rotulo: 'Over/Under 2.5' },
];
const MODEL_NAME_MERCADO_REF = 'mercado_pinnacle_sem_vig';

// Painel de backtest (log-loss/Brier/Acurácia vs. Pinnacle sem vig + ROI/
// Kelly/IC95%/EV+) equivalente ao "Backtest de apostas simuladas (EV+)" de
// Estatísticas dos Modelos, mas lendo o resultado JÁ PERSISTIDO por
// scripts/backtest_kelly.py (via model_benchmarking_backtest/_liga) em vez
// de calcular na hora -- o backtest de verdade (grid search + tuning, 2
// mercados x 10 modelos) é caro demais pra rodar dentro de uma função
// serverless, então roda no GitHub Actions (backtest_kelly.yml, disparado
// por ?tarefa=disparar-backtest) e só o resultado final é lido aqui.
//
// Dois blocos de ROI por modelo: "fechamento" usa a melhor odd real
// disponível entre TODOS os bookmakers (mesmo teste original), "abertura"
// usa especificamente a odd de abertura da Pinnacle -- são universos de
// apostas diferentes (edge mínimo aplicado em cada odd separadamente), não
// comparáveis diretamente entre si.
function BacktestModelBenchmarking({ session }) {
  const [relatorio, setRelatorio] = useState([]);
  const [relatorioPorLiga, setRelatorioPorLiga] = useState([]);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState(null);
  const [disparando, setDisparando] = useState(false);
  const [mensagemDisparo, setMensagemDisparo] = useState(null);
  const [expandido, setExpandido] = useState(null);
  const [mercado, setMercado] = useState('1X2');

  const carregar = useCallback(async () => {
    if (!supabaseAtivo) { setErro('Supabase não configurado.'); setCarregando(false); return; }
    setCarregando(true);
    setErro(null);
    try {
      const [{ data: principal, error: e1 }, { data: porLiga, error: e2 }] = await Promise.all([
        supabase.from('model_benchmarking_backtest').select('*'),
        supabase.from('model_benchmarking_backtest_liga').select('*'),
      ]);
      if (e1) throw e1;
      if (e2) throw e2;
      setRelatorio(principal || []);
      setRelatorioPorLiga(porLiga || []);
    } catch (e) {
      setErro(e.message);
    } finally {
      setCarregando(false);
    }
  }, []);

  useEffect(() => { carregar(); }, [carregar]);

  async function dispararBacktest() {
    setDisparando(true);
    setMensagemDisparo(null);
    try {
      const resp = await fetch('/api/model-maintenance?tarefa=disparar-backtest', {
        method: 'POST',
        headers: { Authorization: `Bearer ${session?.access_token || ''}` },
      });
      const corpo = await resp.json();
      if (!resp.ok) throw new Error(corpo?.error?.message || `HTTP ${resp.status}`);
      setMensagemDisparo({
        tipo: 'ok',
        texto: 'Disparado! Grid search + tuning + simulação Kelly nos 10 modelos (v1, v2 e v3), em 2 mercados (1X2 e Over/Under 2.5) -- é bem mais pesado que as predições diárias, pode levar 30-60 minutos. Volte depois e clique em "Recarregar".',
      });
    } catch (e) {
      setMensagemDisparo({ tipo: 'erro', texto: e.message });
    } finally {
      setDisparando(false);
    }
  }

  const relatorioMercado = relatorio.filter((r) => (r.mercado || '1X2') === mercado);
  const relatorioPorLigaMercado = relatorioPorLiga.filter((r) => (r.mercado || '1X2') === mercado);
  const referenciaMercado = relatorioMercado.find((r) => r.model_name === MODEL_NAME_MERCADO_REF);
  const relatorioModelos = relatorioMercado
    .filter((r) => r.model_name !== MODEL_NAME_MERCADO_REF)
    .sort((a, b) => (b.roi_ic95_inferior ?? -Infinity) - (a.roi_ic95_inferior ?? -Infinity));
  const ultimaExecucao = relatorio.reduce((max, r) => (r.executado_em > max ? r.executado_em : max), '');
  const periodo = referenciaMercado || relatorioModelos[0];

  return (
    <div className="bg-slate-900 border border-slate-700/50 rounded-lg p-4 md:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3 mb-1">
        <h2 className="text-lg font-extrabold flex items-center gap-2 text-slate-100">
          <TrendingUp className="text-emerald-400" size={22} /> Backtest completo (qualidade + EV+)
        </h2>
        <div className="flex items-center gap-2">
          <button onClick={carregar} disabled={carregando}
            className="px-3 py-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-sm border border-slate-700 disabled:opacity-50">
            Recarregar
          </button>
          <button onClick={dispararBacktest} disabled={disparando || !session}
            title={!session ? 'Faça login pra disparar o backtest.' : ''}
            className="flex items-center gap-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 disabled:cursor-not-allowed text-white font-bold px-4 py-2 rounded-lg text-sm">
            {disparando ? <Loader2 size={16} className="animate-spin" /> : <PlayCircle size={16} />} Rodar backtest
          </button>
        </div>
      </div>
      <p className="text-slate-400 text-sm mb-3">
        Test Set out-of-sample (nunca visto pelo treino/tuning). Qualidade (log-loss/Brier/Acurácia) comparada com a
        Pinnacle sem vig (odds justas, devigadas). ROI simulado com Kelly fracionário 25% e edge mínimo 2pp, em dois
        testes separados: contra a <strong>melhor odd real de fechamento</strong> (qualquer bookmaker) e contra a{' '}
        <strong>odd de abertura da Pinnacle</strong> especificamente — IC 95% via bootstrap (2000 reamostragens), só
        considera "EV+" quando o limite inferior do IC fica acima de zero.
        {ultimaExecucao && <span className="text-slate-500"> Última rodada: {new Date(ultimaExecucao).toLocaleString('pt-BR')}.</span>}
      </p>

      <div className="flex items-center gap-2 mb-4">
        {MERCADOS_BACKTEST.map((m) => (
          <button key={m.chave} onClick={() => setMercado(m.chave)}
            className={`px-3 py-1.5 rounded-lg text-xs font-bold border ${mercado === m.chave ? 'bg-emerald-600 border-emerald-600 text-white' : 'bg-slate-800 border-slate-700 text-slate-400 hover:text-slate-200'}`}>
            {m.rotulo}
          </button>
        ))}
        {periodo?.periodo_inicio && (
          <span className="text-[11px] text-slate-500 ml-2">Período de teste: {fmtPeriodo(periodo.periodo_inicio, periodo.periodo_fim)}</span>
        )}
      </div>

      {mensagemDisparo && (
        <div className={`rounded-lg border p-3 text-sm mb-4 ${mensagemDisparo.tipo === 'ok' ? 'bg-emerald-950/40 border-emerald-800 text-emerald-300' : 'bg-red-950/40 border-red-800 text-red-300'}`}>
          {mensagemDisparo.texto}
        </div>
      )}

      {erro && (
        <div className="flex items-center gap-2 rounded-lg border border-red-800 bg-red-950/40 p-3 text-sm text-red-300 mb-4">
          <AlertTriangle size={16} /> {erro}
        </div>
      )}

      {carregando ? (
        <div className="flex items-center gap-2 text-slate-500 text-sm py-8 justify-center">
          <Loader2 size={16} className="animate-spin" /> Carregando backtest...
        </div>
      ) : relatorioMercado.length === 0 ? (
        <p className="text-sm text-slate-500 text-center py-6">
          Nenhum backtest rodado ainda pra {MERCADOS_BACKTEST.find((m) => m.chave === mercado)?.rotulo} — clique em
          "Rodar backtest" (leva uns 30-60 minutos, roda em background no GitHub Actions).
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-slate-500 uppercase text-[10px]">
                <th className="text-left p-1.5"></th>
                <th className="text-left p-1.5">Modelo</th>
                <th className="text-right p-1.5">Log-loss</th>
                <th className="text-right p-1.5">Brier</th>
                <th className="text-right p-1.5">Acurácia</th>
                <th className="text-right p-1.5">Apostas (fech.)</th>
                <th className="text-right p-1.5">ROI (fech.)</th>
                <th className="text-right p-1.5">IC 95% (fech.)</th>
                <th className="text-center p-1.5">EV+?</th>
                <th className="text-right p-1.5">Apostas (abert.)</th>
                <th className="text-right p-1.5">ROI (abert.)</th>
                <th className="text-right p-1.5">IC 95% (abert.)</th>
                <th className="text-center p-1.5">EV+?</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-700/50">
              {referenciaMercado && (
                <tr className="bg-sky-500/5 italic">
                  <td className="p-1.5"></td>
                  <td className="p-1.5 text-sky-300">Pinnacle sem vig (mercado)</td>
                  <td className="p-1.5 text-right text-sky-200">{fmtNum(referenciaMercado.log_loss)}</td>
                  <td className="p-1.5 text-right text-sky-200">{fmtNum(referenciaMercado.brier)}</td>
                  <td className="p-1.5 text-right text-sky-200">{fmtPct(referenciaMercado.accuracy)}</td>
                  <td className="p-1.5 text-right text-slate-700" colSpan={8}>— (referência, sem ROI)</td>
                </tr>
              )}
              {relatorioModelos.map((r) => {
                const ligasDoModelo = relatorioPorLigaMercado.filter((l) => l.model_name === r.model_name);
                const aberto = expandido === `${mercado}:${r.model_name}`;
                return (
                  <React.Fragment key={r.model_name}>
                    <tr className={r.significativo || r.significativo_abertura ? 'bg-emerald-500/5' : ''}>
                      <td className="p-1.5">
                        {ligasDoModelo.length > 0 && (
                          <button onClick={() => setExpandido(aberto ? null : `${mercado}:${r.model_name}`)} className="text-slate-500 hover:text-slate-300">
                            {aberto ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                          </button>
                        )}
                      </td>
                      <td className="p-1.5 text-slate-300">{r.model_name}</td>
                      <td className="p-1.5 text-right text-slate-400">{fmtNum(r.log_loss)}</td>
                      <td className="p-1.5 text-right text-slate-400">{fmtNum(r.brier)}</td>
                      <td className="p-1.5 text-right text-slate-400">{fmtPct(r.accuracy)}</td>
                      <td className="p-1.5 text-right text-slate-400">{r.n_apostas}</td>
                      <td className={`p-1.5 text-right font-bold ${r.roi_medio > 0 ? 'text-emerald-400' : 'text-red-400'}`}>{fmtPctSigned(r.roi_medio)}</td>
                      <td className="p-1.5 text-right text-slate-500">[{fmtPctSigned(r.roi_ic95_inferior)}, {fmtPctSigned(r.roi_ic95_superior)}]</td>
                      <td className="p-1.5 text-center">{r.significativo ? <span className="text-emerald-400 font-bold">✓</span> : <span className="text-slate-600">—</span>}</td>
                      <td className="p-1.5 text-right text-slate-400">{r.n_apostas_abertura}</td>
                      <td className={`p-1.5 text-right font-bold ${r.roi_abertura_medio > 0 ? 'text-emerald-400' : 'text-red-400'}`}>{fmtPctSigned(r.roi_abertura_medio)}</td>
                      <td className="p-1.5 text-right text-slate-500">[{fmtPctSigned(r.roi_abertura_ic95_inferior)}, {fmtPctSigned(r.roi_abertura_ic95_superior)}]</td>
                      <td className="p-1.5 text-center">{r.significativo_abertura ? <span className="text-emerald-400 font-bold">✓</span> : <span className="text-slate-600">—</span>}</td>
                    </tr>
                    {aberto && ligasDoModelo.map((l) => (
                      <tr key={l.liga} className="bg-slate-950/40">
                        <td className="p-1.5"></td>
                        <td className="p-1.5 pl-6 text-slate-500">
                          {l.liga} {l.periodo_inicio && <span className="text-slate-700">({fmtPeriodo(l.periodo_inicio, l.periodo_fim)})</span>}
                        </td>
                        <td className="p-1.5 text-right text-slate-500">{fmtNum(l.log_loss)}</td>
                        <td className="p-1.5 text-right text-slate-500">{fmtNum(l.brier)}</td>
                        <td className="p-1.5 text-right text-slate-500">{fmtPct(l.accuracy)}</td>
                        <td className="p-1.5 text-right text-slate-500">{l.n_apostas}</td>
                        <td className={`p-1.5 text-right ${l.roi_medio > 0 ? 'text-emerald-500/80' : 'text-red-500/80'}`}>{fmtPctSigned(l.roi_medio)}</td>
                        <td className="p-1.5 text-right text-slate-600">[{fmtPctSigned(l.roi_ic95_inferior)}, {fmtPctSigned(l.roi_ic95_superior)}]</td>
                        <td className="p-1.5 text-center">{l.significativo ? <span className="text-emerald-500/80 font-bold">✓</span> : <span className="text-slate-700">—</span>}</td>
                        <td className="p-1.5 text-right text-slate-500">{l.n_apostas_abertura}</td>
                        <td className={`p-1.5 text-right ${l.roi_abertura_medio > 0 ? 'text-emerald-500/80' : 'text-red-500/80'}`}>{fmtPctSigned(l.roi_abertura_medio)}</td>
                        <td className="p-1.5 text-right text-slate-600">[{fmtPctSigned(l.roi_abertura_ic95_inferior)}, {fmtPctSigned(l.roi_abertura_ic95_superior)}]</td>
                        <td className="p-1.5 text-center">{l.significativo_abertura ? <span className="text-emerald-500/80 font-bold">✓</span> : <span className="text-slate-700">—</span>}</td>
                      </tr>
                    ))}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
          <p className="text-[10px] text-slate-600 mt-2">
            Linhas verdes = IC 95% do ROI inteiramente acima de zero em pelo menos um dos dois testes (EV+ estatisticamente
            sustentado, não só edge médio positivo). "Fech." = melhor odd real de fechamento entre todos os bookmakers;
            "Abert." = odd de abertura da Pinnacle especificamente. Clique na seta pra ver a quebra por liga (só disponível
            pra variante crua de cada modelo).
          </p>
        </div>
      )}
    </div>
  );
}

export default function ModelBenchmarking() {
  const { session } = useAuth();
  const [partidas, setPartidas] = useState([]);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState(null);
  const [disparando, setDisparando] = useState(false);
  const [mensagemDisparo, setMensagemDisparo] = useState(null);

  const carregar = useCallback(async () => {
    if (!supabaseAtivo) { setErro('Supabase não configurado.'); setCarregando(false); return; }
    setCarregando(true);
    setErro(null);
    try {
      const [predicoes, odds, matches] = await Promise.all([
        buscarPaginado(supabase.from('predicoes').select('*').in('model_name', MODELOS_BASE)),
        buscarPaginado(supabase.from('market_odds').select('*')),
        buscarPaginado(supabase.from('matches').select('id, match_date, home_team_id, away_team_id')),
      ]);

      const idsTimes = [...new Set(matches.flatMap(m => [m.home_team_id, m.away_team_id]))];
      const { data: times } = await supabase.from('teams').select('id, name').in('id', idsTimes.length ? idsTimes : [0]);
      const nomeTime = Object.fromEntries((times || []).map(t => [t.id, t.name]));
      const matchPorId = Object.fromEntries(matches.map(m => [m.id, m]));

      const oddsPorPartida = {};
      for (const o of odds) {
        if (!oddsPorPartida[o.match_id]) oddsPorPartida[o.match_id] = [];
        oddsPorPartida[o.match_id].push(o);
      }

      const porPartida = {};
      for (const p of predicoes) {
        if (!porPartida[p.match_id]) porPartida[p.match_id] = { match_id: p.match_id, modelos: {} };
        porPartida[p.match_id].modelos[p.model_name] = p;
      }

      const lista = Object.values(porPartida).map(g => {
        const m = matchPorId[g.match_id];
        const melhorOdd = (oddsPorPartida[g.match_id] || []).reduce((melhor, o) => {
          if (!melhor) return o;
          return (o.odd_home ?? 0) > (melhor.odd_home ?? 0) ? o : melhor;
        }, null);
        return {
          match_id: g.match_id,
          time_casa: m ? nomeTime[m.home_team_id] : `#${g.match_id}`,
          time_fora: m ? nomeTime[m.away_team_id] : '',
          match_date: m?.match_date,
          modelos: g.modelos,
          melhor_odd_casa: melhorOdd?.odd_home ?? null,
        };
      }).sort((a, b) => a.match_id - b.match_id);

      setPartidas(lista);
    } catch (e) {
      setErro(e.message);
    } finally {
      setCarregando(false);
    }
  }, []);

  useEffect(() => { carregar(); }, [carregar]);

  async function dispararPredicoes() {
    setDisparando(true);
    setMensagemDisparo(null);
    try {
      const resp = await fetch('/api/model-maintenance?tarefa=disparar-predicoes', {
        method: 'POST',
        headers: { Authorization: `Bearer ${session?.access_token || ''}` },
      });
      const corpo = await resp.json();
      if (!resp.ok) throw new Error(corpo?.error?.message || `HTTP ${resp.status}`);
      setMensagemDisparo({
        tipo: 'ok',
        texto: 'Disparado! O GitHub Actions roda os modelos em background -- volte aqui em alguns minutos e clique em "Recarregar" pra ver o resultado.',
      });
    } catch (e) {
      setMensagemDisparo({ tipo: 'erro', texto: e.message });
    } finally {
      setDisparando(false);
    }
  }

  return (
    <div className="max-w-6xl mx-auto p-4 md:p-6 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-slate-100">Model Benchmarking</h1>
          <p className="text-sm text-slate-500">
            Dixon-Coles + CatBoost/XGBoost/LightGBM (v1 e v2, v2 soma força do elenco) competindo lado a lado —
            dados gerados de forma assíncrona pelo GitHub Actions (<code>scripts/rodar_predicoes.py</code>).
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={carregar}
            disabled={carregando}
            className="px-3 py-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-sm border border-slate-700 disabled:opacity-50"
          >
            Recarregar
          </button>
          <button
            onClick={dispararPredicoes}
            disabled={disparando || !session}
            title={!session ? 'Faça login pra disparar as predições.' : ''}
            className="flex items-center gap-2 px-3 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {disparando ? <Loader2 size={16} className="animate-spin" /> : <Zap size={16} />}
            Disparar predições
          </button>
        </div>
      </div>

      {mensagemDisparo && (
        <div className={`rounded-lg border p-3 text-sm ${mensagemDisparo.tipo === 'ok' ? 'bg-emerald-950/40 border-emerald-800 text-emerald-300' : 'bg-red-950/40 border-red-800 text-red-300'}`}>
          {mensagemDisparo.texto}
        </div>
      )}

      {erro && (
        <div className="flex items-center gap-2 rounded-lg border border-red-800 bg-red-950/40 p-3 text-sm text-red-300">
          <AlertTriangle size={16} /> {erro}
        </div>
      )}

      {carregando ? (
        <div className="flex items-center gap-2 text-slate-500 text-sm py-8 justify-center">
          <Loader2 size={16} className="animate-spin" /> Carregando predições...
        </div>
      ) : partidas.length === 0 ? (
        <div className="text-center text-slate-500 text-sm py-12 border border-dashed border-slate-700 rounded-lg">
          Nenhuma predição ainda — clique em "Disparar predições" e volte em alguns minutos.
        </div>
      ) : (
        <div className="space-y-4">
          {partidas.map(p => (
            <div key={p.match_id} className="bg-slate-900 border border-slate-700/50 rounded-lg overflow-hidden">
              <div className="px-4 py-2 bg-slate-800/50 flex items-center justify-between text-sm">
                <span className="font-medium text-slate-200">{p.time_casa} x {p.time_fora}</span>
                <span className="text-slate-500 text-xs">
                  {p.match_date ? new Date(p.match_date).toLocaleDateString('pt-BR') : ''}
                  {p.melhor_odd_casa != null && <span className="ml-2 text-slate-400">melhor odd casa: {Number(p.melhor_odd_casa).toFixed(2)}</span>}
                </span>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-slate-500 text-xs uppercase">
                      <th className="text-left px-4 py-2">Modelo</th>
                      <th className="text-right px-4 py-2">Casa</th>
                      <th className="text-right px-4 py-2">Empate</th>
                      <th className="text-right px-4 py-2">Fora</th>
                      <th className="text-right px-4 py-2">Edge</th>
                    </tr>
                  </thead>
                  <tbody>
                    {MODELOS_BASE.map(nome => {
                      const pred = p.modelos[nome];
                      return (
                        <tr key={nome} className="border-t border-slate-800">
                          <td className="px-4 py-2 text-slate-300">{ROTULO_MODELO[nome]}</td>
                          <td className="px-4 py-2 text-right text-slate-200">{fmtPct(pred?.prob_home)}</td>
                          <td className="px-4 py-2 text-right text-slate-200">{fmtPct(pred?.prob_draw)}</td>
                          <td className="px-4 py-2 text-right text-slate-200">{fmtPct(pred?.prob_away)}</td>
                          <td className={`px-4 py-2 text-right font-medium ${pred?.edge_detectado > 0 ? 'text-emerald-400' : 'text-slate-500'}`}>
                            {pred?.edge_detectado != null ? (
                              <span className="inline-flex items-center gap-1">
                                {pred.edge_detectado > 0 && <TrendingUp size={12} />}
                                {fmtPct(pred.edge_detectado)}
                              </span>
                            ) : '—'}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          ))}
        </div>
      )}

      <p className="text-[11px] text-slate-600">
        Cada modelo também grava variantes calibradas (Platt/Isotonic) em <code>predicoes</code>
        (sufixo <code>_calibrado_platt</code>/<code>_calibrado_isotonic</code>) — não mostradas aqui pra manter o painel legível.
      </p>

      <BacktestModelBenchmarking session={session} />
    </div>
  );
}
