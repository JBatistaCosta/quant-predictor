// src/pages/ModelBenchmarking.jsx — rota /model-benchmarking
// Painel dos 4 modelos do Model Benchmarking (dixon_coles_v1/catboost_v1/
// xgboost_v1/lightgbm_v1) competindo lado a lado, com o botão que dispara o
// workflow_dispatch do predict.yml no GitHub Actions (api/model-maintenance
// ?tarefa=disparar-predicoes). Leitura direta de `predicoes`/`market_odds`
// (RLS pública) -- só o disparo exige login (Authorization: Bearer do
// access_token do Supabase Auth, verificado no servidor).
import React, { useState, useEffect, useCallback } from 'react';
import { Zap, Loader2, AlertTriangle, TrendingUp } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';
import { useAuth } from '../AuthContext';

const MODELOS_BASE = ['dixon_coles_v1', 'catboost_v1', 'xgboost_v1', 'lightgbm_v1'];
const ROTULO_MODELO = {
  dixon_coles_v1: 'Dixon-Coles',
  catboost_v1: 'CatBoost',
  xgboost_v1: 'XGBoost',
  lightgbm_v1: 'LightGBM',
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
        texto: 'Disparado! O GitHub Actions roda os 4 modelos em background -- volte aqui em alguns minutos e clique em "Recarregar" pra ver o resultado.',
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
            4 modelos (Dixon-Coles, CatBoost, XGBoost, LightGBM) competindo lado a lado —
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
    </div>
  );
}
