// src/components/ModeloCartoesFaltas.jsx
// Painel "Cartões e Faltas (automático)", usado em AnaliseEstatisticaJogo.jsx.
// Diferente de EstimativaModeloCustom.jsx (onde o usuário escolhe a config
// entre as 32 disponíveis manualmente), este painel dispara UM clique só e o
// backend (api/model-maintenance?tarefa=estimar-cartoes-faltas) escolhe
// sozinho a config certa pra Cartões geral e Faltas geral na linha padrão
// (2.5 e 22.5) -- ver comentário da tarefa em model-maintenance.js pra por
// que só 1 linha de cada mercado por clique (cada linha é um modelo treinado
// à parte, disparar todas de uma vez multiplicaria os workflows do GitHub
// Actions por clique). Pra outra linha, ou o recorte mandante/visitante,
// o painel manual (EstimativaModeloCustom) continua sendo o caminho.
import React, { useState, useCallback, useRef, useEffect } from 'react';
import { Flag, Loader2, ChevronDown, AlertTriangle } from 'lucide-react';
import { useAuth } from '../AuthContext';
import { supabase, supabaseAtivo } from '../supabaseClient';
import { apiUrl } from '../utils/apiUrl';

const POLL_MS = 6000;

const LABEL_MERCADO = { cartoes: 'Cartões', faltas: 'Faltas' };

function TabelaMercado({ mercado, item }) {
  if (!item) return null;
  const titulo = `${LABEL_MERCADO[mercado] || mercado} — O/U ${item.linha}`;

  if (item.erro) {
    return (
      <div>
        <p className="text-xs font-semibold text-slate-300 mb-1">{titulo}</p>
        <p className="text-xs text-red-400 flex items-center gap-1.5"><AlertTriangle size={13} /> {item.erro}</p>
      </div>
    );
  }

  const emAndamento = item.status === 'pendente' || item.status === 'processando';

  return (
    <div>
      <p className="text-xs font-semibold text-slate-300 mb-1">{titulo}</p>
      {emAndamento && (
        <p className="text-xs text-slate-400 flex items-center gap-1.5">
          <Loader2 size={13} className="animate-spin" />
          {item.status === 'pendente' ? 'Na fila...' : 'Aplicando o modelo...'}
        </p>
      )}
      {item.status === 'erro' && (
        <p className="text-xs text-red-400 flex items-center gap-1.5">
          <AlertTriangle size={13} /> {item.error_message || 'Falha ao calcular a estimativa.'}
        </p>
      )}
      {item.status === 'concluido' && (
        <table className="text-xs border-collapse">
          <thead>
            <tr className="border-b border-slate-700">
              <th className="text-left text-slate-400 py-1 pr-4 font-medium">Seleção</th>
              <th className="text-center text-slate-400 py-1 px-3 font-medium">Probabilidade</th>
              <th className="text-center text-slate-400 py-1 px-3 font-medium">Odd justa</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(item.probabilities || {}).map(([sel, prob]) => (
              <tr key={sel} className="border-b border-slate-800">
                <td className="py-1 pr-4 text-white">{sel === 'over' ? 'Over' : sel === 'under' ? 'Under' : sel}</td>
                <td className="text-center px-3 font-mono text-emerald-400 py-1">{(Number(prob) * 100).toFixed(1)}%</td>
                <td className="text-center px-3 font-mono text-white py-1">
                  {item.fair_odds?.[sel] != null ? Number(item.fair_odds[sel]).toFixed(2) : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export default function ModeloCartoesFaltas({ mandanteId, visitanteId }) {
  const { session } = useAuth();
  const [aberto, setAberto] = useState(false);
  const [disparando, setDisparando] = useState(false);
  const [erroGeral, setErroGeral] = useState('');
  const [resultados, setResultados] = useState(null); // { cartoes: {...}, faltas: {...} }
  const pollingRef = useRef(null);

  const authHeader = session?.access_token ? { Authorization: `Bearer ${session.access_token}` } : {};

  const pararPolling = useCallback(() => {
    if (pollingRef.current) clearInterval(pollingRef.current);
    pollingRef.current = null;
  }, []);

  useEffect(() => () => pararPolling(), [pararPolling]);

  async function dispararEstimativa() {
    if (!mandanteId || !visitanteId) return;
    setErroGeral('');
    setResultados(null);
    setDisparando(true);
    try {
      const resp = await fetch(apiUrl('/api/model-maintenance?tarefa=estimar-cartoes-faltas'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeader },
        body: JSON.stringify({ mandante_id: mandanteId, visitante_id: visitanteId }),
      });
      const dados = await resp.json();
      if (!resp.ok) throw new Error(dados.error?.message || `HTTP ${resp.status}`);

      const estadoInicial = {};
      for (const m of dados.mercados || []) {
        estadoInicial[m.mercado] = m.erro
          ? { linha: m.linha, erro: m.erro }
          : { linha: m.linha, request_id: m.request_id, status: 'pendente' };
      }
      setResultados(estadoInicial);

      pararPolling();
      pollingRef.current = setInterval(async () => {
        setResultados((atual) => {
          if (!atual) return atual;
          const pendentes = Object.entries(atual).filter(([, v]) => v.request_id && v.status !== 'concluido' && v.status !== 'erro');
          if (pendentes.length === 0) { pararPolling(); return atual; }
          (async () => {
            const ids = pendentes.map(([, v]) => v.request_id);
            const { data } = await supabase
              .from('custom_model_ondemand_predictions')
              .select('id, status, probabilities, fair_odds, error_message')
              .in('id', ids);
            if (!data) return;
            setResultados((prev) => {
              if (!prev) return prev;
              const proximo = { ...prev };
              for (const [mercado, valor] of Object.entries(prev)) {
                const encontrado = data.find((d) => d.id === valor.request_id);
                if (encontrado) proximo[mercado] = { ...valor, ...encontrado };
              }
              return proximo;
            });
          })();
          return atual;
        });
      }, POLL_MS);
    } catch (e) {
      setErroGeral(e.message);
    } finally {
      setDisparando(false);
    }
  }

  if (!supabaseAtivo) return null;

  const algumEmAndamento = resultados && Object.values(resultados).some((v) => v.request_id && v.status !== 'concluido' && v.status !== 'erro');

  return (
    <div className="bg-slate-800 rounded-lg border border-slate-700 p-4 mb-4">
      <button
        type="button"
        onClick={() => setAberto((a) => !a)}
        className="flex items-center gap-2 text-sm font-semibold text-amber-300 hover:text-amber-200 transition-colors"
      >
        <Flag size={16} />
        Cartões e Faltas (automático)
        <ChevronDown size={14} className={`transition-transform ${aberto ? 'rotate-180' : ''}`} />
      </button>

      {aberto && (
        <div className="mt-3 space-y-3">
          {!session ? (
            <p className="text-xs text-slate-500">Faça login pra estimar Cartões e Faltas dessa partida.</p>
          ) : (
            <>
              <button
                type="button"
                onClick={dispararEstimativa}
                disabled={!mandanteId || !visitanteId || disparando || algumEmAndamento}
                className="flex items-center gap-1.5 text-xs bg-amber-600 hover:bg-amber-500 disabled:bg-slate-700 disabled:text-slate-500 disabled:cursor-not-allowed text-white rounded-lg px-3 py-1.5 font-medium transition-colors"
              >
                {disparando || algumEmAndamento ? (
                  <><Loader2 size={13} className="animate-spin" /> Calculando...</>
                ) : (
                  'Estimar Cartões (O/U 2.5) e Faltas (O/U 22.5)'
                )}
              </button>

              <p className="text-[10px] text-slate-600">
                Escolhe sozinho as configs "geral" já treinadas nas linhas padrão. Pra outra linha, ou o recorte
                mandante/visitante, use o painel "Estimar com modelo personalizado" abaixo.
              </p>

              {erroGeral && (
                <p className="text-xs text-red-400 flex items-center gap-1.5"><AlertTriangle size={13} /> {erroGeral}</p>
              )}

              {resultados && (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <TabelaMercado mercado="cartoes" item={resultados.cartoes} />
                  <TabelaMercado mercado="faltas" item={resultados.faltas} />
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
