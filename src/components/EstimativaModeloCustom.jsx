// src/components/EstimativaModeloCustom.jsx
// Painel "Estimar com modelo personalizado", usado em AnaliseEstatisticaJogo.jsx.
// Deixa o usuário escolher uma configuração já treinada em custom_model_configs
// (e, se for modo walk_forward_cv, um algoritmo específico ou grupo de
// stacking dela) e aplicar o modelo JÁ TREINADO pra essa partida —
// api/model-maintenance?tarefa=estimar-partida-custom dispara
// .github/workflows/estimar_partida_custom.yml, que roda
// scripts/estimar_partida_custom.py. Esse script NUNCA treina: só carrega
// o artefato persistido (custom_model_configs.model_artifacts, gravado no
// fim de um treino bem-sucedido no painel Treino Customizado) e prevê --
// por isso só entram no seletor de algoritmo as opções que já têm um
// artefato pronto. O resultado é pollado direto via cliente Supabase em
// custom_model_ondemand_predictions (policy de leitura pública).
import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Sparkles, Loader2, ChevronDown, AlertTriangle } from 'lucide-react';
import { useAuth } from '../AuthContext';
import { supabase, supabaseAtivo } from '../supabaseClient';
import { apiUrl } from '../utils/apiUrl';

const SELECAO_LABELS = {
  home: 'Casa', draw: 'Empate', away: 'Fora',
  over: 'Over', under: 'Under',
  yes: 'Sim', no: 'Não',
};

const POLL_MS = 6000;

export default function EstimativaModeloCustom({ matchId }) {
  const { session } = useAuth();
  const [aberto, setAberto] = useState(false);
  const [configs, setConfigs] = useState([]);
  const [carregandoConfigs, setCarregandoConfigs] = useState(false);
  const [configId, setConfigId] = useState('');
  const [algoritmo, setAlgoritmo] = useState('');
  const [disparando, setDisparando] = useState(false);
  const [resultado, setResultado] = useState(null); // { status, probabilities, fair_odds, error_message }
  const [erro, setErro] = useState('');
  const pollingRef = useRef(null);

  const authHeader = session?.access_token ? { Authorization: `Bearer ${session.access_token}` } : {};

  useEffect(() => {
    if (!aberto || configs.length > 0) return;
    (async () => {
      setCarregandoConfigs(true);
      try {
        const resp = await fetch(apiUrl('/api/model-maintenance?tarefa=listar-configs-custom'));
        const dados = await resp.json();
        // Só entram configs com pelo menos 1 algoritmo/grupo com artefato
        // persistido -- sem isso não tem o que aplicar (o script nunca
        // treina, só prevê com um modelo já pronto).
        setConfigs((dados.configs || []).filter((c) => c.status === 'treinado' && Object.keys(c.model_artifacts || {}).length > 0));
      } catch {
        setErro('Falha ao carregar modelos personalizados.');
      } finally {
        setCarregandoConfigs(false);
      }
    })();
  }, [aberto, configs.length]);

  const configSelecionada = configs.find((c) => c.id === configId) || null;
  const artefatosDisponiveis = configSelecionada?.model_artifacts || {};
  const opcoesAlgoritmo = !configSelecionada
    ? []
    : configSelecionada.mode === 'walk_forward_cv'
      ? [
          ...(configSelecionada.algorithms || []).map((a) => ({ value: a, label: a })),
          ...(configSelecionada.stacking_groups || []).map((g) => ({ value: `stacking:${g.name}`, label: `★ ${g.name}` })),
        ].filter((o) => artefatosDisponiveis[o.value])
      : configSelecionada.algorithm && artefatosDisponiveis[configSelecionada.algorithm]
        ? [{ value: configSelecionada.algorithm, label: configSelecionada.algorithm }]
        : [];

  useEffect(() => {
    if (!configSelecionada) { setAlgoritmo(''); return; }
    if (configSelecionada.mode !== 'walk_forward_cv') {
      setAlgoritmo(configSelecionada.algorithm || '');
    } else {
      setAlgoritmo((atual) => (opcoesAlgoritmo.some((o) => o.value === atual) ? atual : (opcoesAlgoritmo[0]?.value || '')));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [configId]);

  const pararPolling = useCallback(() => {
    if (pollingRef.current) clearInterval(pollingRef.current);
    pollingRef.current = null;
  }, []);

  useEffect(() => () => pararPolling(), [pararPolling]);

  async function dispararEstimativa() {
    if (!configId || !algoritmo) return;
    setErro('');
    setResultado(null);
    setDisparando(true);
    try {
      const resp = await fetch(apiUrl('/api/model-maintenance?tarefa=estimar-partida-custom'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeader },
        body: JSON.stringify({ config_id: configId, match_id: Number(matchId), algorithm: algoritmo }),
      });
      const dados = await resp.json();
      if (!resp.ok) throw new Error(dados.error?.message || `HTTP ${resp.status}`);

      setResultado({ status: 'pendente' });
      pararPolling();
      pollingRef.current = setInterval(async () => {
        const { data } = await supabase
          .from('custom_model_ondemand_predictions')
          .select('status, probabilities, fair_odds, error_message')
          .eq('id', dados.request_id)
          .maybeSingle();
        if (!data) return;
        setResultado(data);
        if (data.status === 'concluido' || data.status === 'erro') pararPolling();
      }, POLL_MS);
    } catch (e) {
      setErro(e.message);
    } finally {
      setDisparando(false);
    }
  }

  if (!supabaseAtivo) return null;

  const emAndamento = resultado?.status === 'pendente' || resultado?.status === 'processando';

  return (
    <div className="bg-slate-800 rounded-lg border border-slate-700 p-4 mb-4">
      <button
        type="button"
        onClick={() => setAberto((a) => !a)}
        className="flex items-center gap-2 text-sm font-semibold text-violet-300 hover:text-violet-200 transition-colors"
      >
        <Sparkles size={16} />
        Estimar com modelo personalizado
        <ChevronDown size={14} className={`transition-transform ${aberto ? 'rotate-180' : ''}`} />
      </button>

      {aberto && (
        <div className="mt-3 space-y-3">
          {!session ? (
            <p className="text-xs text-slate-500">Faça login pra estimar essa partida com um modelo personalizado.</p>
          ) : (
            <>
              <div className="flex flex-wrap gap-3 items-end">
                <div>
                  <label className="block text-[10px] uppercase font-bold text-slate-500 mb-1">Modelo</label>
                  <select
                    value={configId}
                    onChange={(e) => setConfigId(e.target.value)}
                    className="bg-slate-900 border border-slate-600 rounded-lg px-2 py-1.5 text-xs text-slate-100 min-w-[220px]"
                  >
                    <option value="">
                      {carregandoConfigs ? 'Carregando...' : configs.length === 0 ? 'Nenhum modelo treinado ainda' : 'Selecione...'}
                    </option>
                    {configs.map((c) => (
                      <option key={c.id} value={c.id}>{c.name} ({c.target})</option>
                    ))}
                  </select>
                </div>

                {configSelecionada?.mode === 'walk_forward_cv' && (
                  <div>
                    <label className="block text-[10px] uppercase font-bold text-slate-500 mb-1">Algoritmo</label>
                    <select
                      value={algoritmo}
                      onChange={(e) => setAlgoritmo(e.target.value)}
                      className="bg-slate-900 border border-slate-600 rounded-lg px-2 py-1.5 text-xs text-slate-100"
                    >
                      {opcoesAlgoritmo.map((o) => (
                        <option key={o.value} value={o.value}>{o.label}</option>
                      ))}
                    </select>
                  </div>
                )}

                <button
                  type="button"
                  onClick={dispararEstimativa}
                  disabled={!configId || !algoritmo || disparando || emAndamento}
                  className="flex items-center gap-1.5 text-xs bg-violet-600 hover:bg-violet-500 disabled:bg-slate-700 disabled:text-slate-500 disabled:cursor-not-allowed text-white rounded-lg px-3 py-1.5 font-medium transition-colors"
                >
                  {disparando || emAndamento ? (
                    <><Loader2 size={13} className="animate-spin" /> Calculando...</>
                  ) : (
                    'Calcular estimativa'
                  )}
                </button>
              </div>

              <p className="text-[10px] text-slate-600">
                Aplica o modelo já treinado (mesmos parâmetros/hiperparâmetros encontrados no treino) pra estimar só essa partida — não retreina.
              </p>

              {erro && (
                <p className="text-xs text-red-400 flex items-center gap-1.5"><AlertTriangle size={13} /> {erro}</p>
              )}

              {emAndamento && (
                <p className="text-xs text-slate-400 flex items-center gap-1.5">
                  <Loader2 size={13} className="animate-spin" />
                  {resultado.status === 'pendente' ? 'Na fila...' : 'Aplicando o modelo...'}
                </p>
              )}

              {resultado?.status === 'erro' && (
                <p className="text-xs text-red-400 flex items-center gap-1.5">
                  <AlertTriangle size={13} /> {resultado.error_message || 'Falha ao calcular a estimativa.'}
                </p>
              )}

              {resultado?.status === 'concluido' && (
                <div className="overflow-x-auto">
                  <table className="text-xs border-collapse">
                    <thead>
                      <tr className="border-b border-slate-700">
                        <th className="text-left text-slate-400 py-1.5 pr-4 font-medium">Seleção</th>
                        <th className="text-center text-slate-400 py-1.5 px-3 font-medium">Probabilidade</th>
                        <th className="text-center text-slate-400 py-1.5 px-3 font-medium">Odd justa</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(resultado.probabilities || {}).map(([sel, prob]) => (
                        <tr key={sel} className="border-b border-slate-800">
                          <td className="py-1.5 pr-4 text-white">{SELECAO_LABELS[sel] || sel}</td>
                          <td className="text-center px-3 font-mono text-emerald-400 py-1.5">{(Number(prob) * 100).toFixed(1)}%</td>
                          <td className="text-center px-3 font-mono text-white py-1.5">
                            {resultado.fair_odds?.[sel] != null ? Number(resultado.fair_odds[sel]).toFixed(2) : '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <p className="text-[10px] text-slate-600 mt-1.5">Modelo já treinado aplicado — sem retreino.</p>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
