// src/components/PainelGithubActions.jsx
// Aba de Configurações: painel central pra disparar e acompanhar os
// workflows do GitHub Actions (.github/workflows/*.yml) sem sair do app.
// O mecanismo de disparo (GITHUB_ACTIONS_PAT + dispararWorkflow()) já
// existia em api/model-maintenance.js pra 5 workflows específicos
// (ModelBenchmarking.jsx/RodadaPrevisoes.jsx) -- aqui só generaliza pro
// allow-list completo (WORKFLOWS_DISPONIVEIS, mesmo arquivo, é a fonte
// única de verdade -- este componente só renderiza o que ela devolve) e
// adiciona o que não existia antes: acompanhamento de execução
// (tarefa=github-runs-listar), já que o disparo em si é "fire and forget"
// (a API do GitHub não devolve run_id de forma síncrona).
import React, { useEffect, useState, useCallback, useRef } from 'react';
import { Search, PlayCircle, Loader2, AlertTriangle, CheckCircle2, XCircle, Clock, RefreshCw, ChevronDown, ChevronRight, ExternalLink, ShieldAlert, Users } from 'lucide-react';
import { useAuth } from '../AuthContext';
import { apiUrl } from '../utils/apiUrl';

const CATEGORIAS = [
  { id: 'treino', label: 'Treino', aberta: true },
  { id: 'importacao', label: 'Importação', aberta: true },
  { id: 'duplicatas', label: 'Duplicatas', aberta: true },
  { id: 'outros', label: 'Outros', aberta: false },
];

function iconeExecucao(exec) {
  if (exec.status !== 'completed') {
    if (exec.status === 'in_progress') return <Loader2 size={14} className="text-sky-400 animate-spin" />;
    return <Clock size={14} className="text-amber-400" />;
  }
  if (exec.conclusao === 'success') return <CheckCircle2 size={14} className="text-emerald-400" />;
  if (exec.conclusao === 'failure') return <XCircle size={14} className="text-red-400" />;
  return <AlertTriangle size={14} className="text-amber-400" />;
}

function formatarQuando(iso) {
  if (!iso) return '—';
  const diffMs = Date.now() - new Date(iso).getTime();
  const min = Math.round(diffMs / 60000);
  if (min < 1) return 'agora';
  if (min < 60) return `${min}min atrás`;
  const h = Math.round(min / 60);
  if (h < 24) return `${h}h atrás`;
  return `${Math.round(h / 24)}d atrás`;
}

function CampoInput({ campo, valor, onChange }) {
  if (campo.tipo === 'boolean') {
    return (
      <label className="flex items-center gap-1.5 text-xs text-slate-300 cursor-pointer" title={campo.descricao}>
        <input type="checkbox" checked={!!valor} onChange={(e) => onChange(e.target.checked)}
          className="w-3.5 h-3.5 rounded accent-emerald-500" />
        {campo.id}
      </label>
    );
  }
  if (campo.tipo === 'choice') {
    return (
      <select value={valor ?? ''} onChange={(e) => onChange(e.target.value)} title={campo.descricao}
        className="bg-slate-900 border border-slate-600 rounded-lg px-2 py-1 text-xs text-slate-200">
        <option value="">{campo.id}{campo.default ? ` (padrão: ${campo.default})` : ''}</option>
        {campo.opcoes.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    );
  }
  return (
    <input value={valor ?? ''} onChange={(e) => onChange(e.target.value)} title={campo.descricao}
      placeholder={campo.id + (campo.obrigatorio ? ' *' : '') + (campo.default ? ` (${campo.default})` : '')}
      className="bg-slate-900 border border-slate-600 rounded-lg px-2 py-1 text-xs text-slate-200 placeholder:text-slate-500 w-44" />
  );
}

function LinhaWorkflow({ workflow, disparando, onDisparar, mensagem }) {
  const [inputs, setInputs] = useState({});
  const [mostrarInputs, setMostrarInputs] = useState(false);

  const setCampo = (id, valor) => setInputs((prev) => ({ ...prev, [id]: valor }));

  const disparar = () => {
    const faltando = workflow.inputs.filter((c) => c.obrigatorio && !inputs[c.id]);
    if (faltando.length > 0) { setMostrarInputs(true); return; }
    const confirmMsg = workflow.risco
      ? `ATENÇÃO: "${workflow.label}" não é curadoria de dado (${workflow.arquivo}) -- confirme que quer disparar mesmo assim.`
      : `Disparar "${workflow.label}" (${workflow.arquivo})?`;
    if (!window.confirm(confirmMsg)) return;
    onDisparar(workflow.arquivo, inputs);
  };

  return (
    <div className={`border rounded-xl p-3 ${workflow.risco ? 'border-red-600/40 bg-red-950/10' : 'border-slate-700 bg-slate-900/60'}`}>
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2 min-w-0">
          {workflow.risco && <ShieldAlert size={14} className="text-red-400 shrink-0" />}
          <div className="min-w-0">
            <p className="text-sm text-slate-200 font-medium truncate">{workflow.label}</p>
            <p className="text-[11px] text-slate-500 font-mono truncate">{workflow.arquivo}</p>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {workflow.inputs.length > 0 && (
            <button onClick={() => setMostrarInputs((v) => !v)}
              className="text-[11px] text-slate-400 hover:text-slate-200 flex items-center gap-1">
              {mostrarInputs ? <ChevronDown size={12} /> : <ChevronRight size={12} />} parâmetros
            </button>
          )}
          <button onClick={disparar} disabled={disparando}
            className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white font-medium">
            {disparando ? <Loader2 size={13} className="animate-spin" /> : <PlayCircle size={13} />} Disparar
          </button>
        </div>
      </div>

      {mostrarInputs && workflow.inputs.length > 0 && (
        <div className="mt-3 pt-3 border-t border-slate-800 flex flex-wrap gap-2">
          {workflow.inputs.map((campo) => (
            <CampoInput key={campo.id} campo={campo} valor={inputs[campo.id]} onChange={(v) => setCampo(campo.id, v)} />
          ))}
        </div>
      )}

      {mensagem && (
        <p className={`mt-2 text-[11px] ${mensagem.tipo === 'ok' ? 'text-emerald-400' : 'text-red-400'}`}>{mensagem.texto}</p>
      )}
    </div>
  );
}

export default function PainelGithubActions() {
  const { session } = useAuth();
  const [workflows, setWorkflows] = useState([]);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState('');
  const [busca, setBusca] = useState('');
  const [abertas, setAbertas] = useState(() => Object.fromEntries(CATEGORIAS.map((c) => [c.id, c.aberta])));
  const [disparandoArquivo, setDisparandoArquivo] = useState(null);
  const [mensagens, setMensagens] = useState({});
  const [sincronizandoPendentes, setSincronizandoPendentes] = useState(false);
  const [mensagemPendentes, setMensagemPendentes] = useState(null);

  const [execucoes, setExecucoes] = useState([]);
  const [carregandoExecucoes, setCarregandoExecucoes] = useState(false);
  const [erroExecucoes, setErroExecucoes] = useState('');
  const intervalRef = useRef(null);

  const authHeaders = useCallback(() => ({ Authorization: `Bearer ${session?.access_token || ''}` }), [session]);

  const carregarWorkflows = useCallback(async () => {
    if (!session) { setCarregando(false); return; }
    setCarregando(true);
    setErro('');
    try {
      const resp = await fetch(apiUrl('/api/model-maintenance?tarefa=github-workflows-listar'), { headers: authHeaders() });
      const corpo = await resp.json();
      if (!resp.ok) throw new Error(corpo?.error?.message || `HTTP ${resp.status}`);
      setWorkflows(corpo.workflows || []);
    } catch (e) {
      setErro(e.message);
    } finally {
      setCarregando(false);
    }
  }, [session, authHeaders]);

  const carregarExecucoes = useCallback(async () => {
    if (!session) return;
    setCarregandoExecucoes(true);
    setErroExecucoes('');
    try {
      const resp = await fetch(apiUrl('/api/model-maintenance?tarefa=github-runs-listar'), { headers: authHeaders() });
      const corpo = await resp.json();
      if (!resp.ok) throw new Error(corpo?.error?.message || `HTTP ${resp.status}`);
      setExecucoes(corpo.execucoes || []);
    } catch (e) {
      setErroExecucoes(e.message);
    } finally {
      setCarregandoExecucoes(false);
    }
  }, [session, authHeaders]);

  useEffect(() => { carregarWorkflows(); carregarExecucoes(); }, [carregarWorkflows, carregarExecucoes]);

  // Auto-refresh leve das execuções enquanto essa aba estiver montada -- só
  // pra "acompanhar" sem precisar clicar toda hora; para sozinho ao
  // desmontar (troca de aba/página), não fica rodando em segundo plano.
  useEffect(() => {
    intervalRef.current = setInterval(carregarExecucoes, 30000);
    return () => clearInterval(intervalRef.current);
  }, [carregarExecucoes]);

  const dispararWorkflow = async (arquivo, inputs) => {
    setDisparandoArquivo(arquivo);
    setMensagens((prev) => ({ ...prev, [arquivo]: null }));
    try {
      const resp = await fetch(apiUrl('/api/model-maintenance?tarefa=disparar-workflow-generico'), {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ arquivo, inputs }),
      });
      const corpo = await resp.json();
      if (!resp.ok) throw new Error(corpo?.error?.message || `HTTP ${resp.status}`);
      setMensagens((prev) => ({ ...prev, [arquivo]: { tipo: 'ok', texto: 'Disparado! Acompanhe abaixo em "Execuções recentes".' } }));
      setTimeout(carregarExecucoes, 5000);
    } catch (e) {
      setMensagens((prev) => ({ ...prev, [arquivo]: { tipo: 'erro', texto: e.message } }));
    } finally {
      setDisparandoArquivo(null);
    }
  };

  // Ação dedicada (não passa pelo dispatch genérico de LinhaWorkflow) --
  // acha as partidas pendentes no backend (tarefa=sincronizar-escalacao-
  // pendentes, mesma lógica de "descoberta" usada em EventosLista.jsx) e já
  // dispara todas de uma vez, num único workflow_dispatch de
  // ingerir_escalacao_pre_jogo.yml com --match-ids em lote. Não precisa que
  // o usuário saiba os IDs -- diferente de disparar esse mesmo workflow
  // pela lista genérica abaixo, que exigiria colar os match_ids à mão.
  const sincronizarEscalacoesPendentes = async () => {
    if (!window.confirm('Buscar partidas encerradas dos últimos 30 dias sem escalação real capturada e disparar a sincronização de todas de uma vez?')) return;
    setSincronizandoPendentes(true);
    setMensagemPendentes(null);
    try {
      const resp = await fetch(apiUrl('/api/model-maintenance?tarefa=sincronizar-escalacao-pendentes'), { headers: authHeaders() });
      const corpo = await resp.json();
      if (!resp.ok) throw new Error(corpo?.error?.message || `HTTP ${resp.status}`);
      setMensagemPendentes({
        tipo: 'ok',
        texto: corpo.disparado
          ? `${corpo.n_pendentes} partida(s) pendente(s) (de ${corpo.n_verificadas} verificadas) -- sincronização disparada, acompanhe abaixo em "Execuções recentes".`
          : (corpo.mensagem || 'Nenhuma partida pendente.'),
      });
      if (corpo.disparado) setTimeout(carregarExecucoes, 5000);
    } catch (e) {
      setMensagemPendentes({ tipo: 'erro', texto: e.message });
    } finally {
      setSincronizandoPendentes(false);
    }
  };

  if (!session) {
    return (
      <div className="text-center py-10 text-slate-500 text-sm flex flex-col items-center gap-2">
        <AlertTriangle size={20} />
        Faça login pra ver e disparar os workflows.
      </div>
    );
  }

  const buscaLower = busca.trim().toLowerCase();
  const workflowsFiltrados = buscaLower
    ? workflows.filter((w) => w.label.toLowerCase().includes(buscaLower) || w.arquivo.toLowerCase().includes(buscaLower))
    : workflows;

  return (
    <div className="space-y-6">
      <p className="text-slate-400 text-sm max-w-3xl">
        Dispara workflows do GitHub Actions (treino, importação, limpeza de duplicatas) sem precisar abrir o GitHub, e
        mostra as execuções recentes abaixo. O disparo é assíncrono -- o GitHub não devolve o resultado na hora, então
        acompanhe pela lista de execuções (atualiza sozinha a cada 30s enquanto esta aba estiver aberta).
      </p>

      <div className="border border-emerald-700/40 bg-emerald-950/10 rounded-xl p-3">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div className="flex items-center gap-2 min-w-0">
            <Users size={14} className="text-emerald-400 shrink-0" />
            <div className="min-w-0">
              <p className="text-sm text-slate-200 font-medium">Sincronizar escalações pendentes</p>
              <p className="text-[11px] text-slate-500">
                Acha partidas encerradas dos últimos 30 dias sem escalação real capturada (perderam a janela pré-jogo de 90min do cron) e dispara a sincronização de todas de uma vez — sem precisar saber os IDs.
              </p>
            </div>
          </div>
          <button onClick={sincronizarEscalacoesPendentes} disabled={sincronizandoPendentes}
            className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white font-medium shrink-0">
            {sincronizandoPendentes ? <Loader2 size={13} className="animate-spin" /> : <PlayCircle size={13} />} Sincronizar pendentes
          </button>
        </div>
        {mensagemPendentes && (
          <p className={`mt-2 text-[11px] ${mensagemPendentes.tipo === 'ok' ? 'text-emerald-400' : 'text-red-400'}`}>{mensagemPendentes.texto}</p>
        )}
      </div>

      <div className="relative max-w-sm">
        <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
        <input value={busca} onChange={(e) => setBusca(e.target.value)} placeholder="Buscar workflow..."
          className="w-full bg-slate-900 border border-slate-600 rounded-lg pl-8 pr-3 py-2 text-sm text-slate-200 placeholder:text-slate-500" />
      </div>

      {erro && (
        <div className="bg-red-950/30 border border-red-600/40 text-red-300 text-sm px-4 py-2.5 rounded-lg flex items-start gap-2">
          <AlertTriangle size={16} className="shrink-0 mt-0.5" /> {erro}
        </div>
      )}

      {carregando ? (
        <p className="text-slate-500 text-sm text-center py-10">Carregando workflows...</p>
      ) : (
        <div className="space-y-4">
          {CATEGORIAS.map((cat) => {
            const doGrupo = workflowsFiltrados.filter((w) => w.categoria === cat.id);
            if (doGrupo.length === 0) return null;
            return (
              <div key={cat.id}>
                <button onClick={() => setAbertas((prev) => ({ ...prev, [cat.id]: !prev[cat.id] }))}
                  className="flex items-center gap-1.5 text-xs font-bold uppercase tracking-wider text-slate-400 hover:text-slate-200 mb-2">
                  {abertas[cat.id] ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                  {cat.label} <span className="text-slate-600 font-normal normal-case">({doGrupo.length})</span>
                </button>
                {abertas[cat.id] && (
                  <div className="space-y-2">
                    {doGrupo.map((w) => (
                      <LinhaWorkflow key={w.arquivo} workflow={w} disparando={disparandoArquivo === w.arquivo}
                        onDisparar={dispararWorkflow} mensagem={mensagens[w.arquivo]} />
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      <div className="pt-4 border-t border-slate-800">
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-bold text-slate-300">Execuções recentes</h3>
          <button onClick={carregarExecucoes} disabled={carregandoExecucoes}
            className="flex items-center gap-1.5 text-[11px] text-slate-400 hover:text-slate-200 disabled:opacity-50">
            <RefreshCw size={12} className={carregandoExecucoes ? 'animate-spin' : ''} /> Atualizar
          </button>
        </div>

        {erroExecucoes && <p className="text-red-400 text-xs mb-2">{erroExecucoes}</p>}

        {execucoes.length === 0 ? (
          <p className="text-slate-500 text-xs">Nenhuma execução recente.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="text-slate-500 uppercase tracking-wider text-[10px]">
                  <th className="py-1.5 pr-3"></th>
                  <th className="py-1.5 pr-3">Workflow</th>
                  <th className="py-1.5 pr-3">Iniciado</th>
                  <th className="py-1.5 pr-3">Status</th>
                  <th className="py-1.5"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800">
                {execucoes.map((exec) => (
                  <tr key={exec.id}>
                    <td className="py-1.5 pr-3">{iconeExecucao(exec)}</td>
                    <td className="py-1.5 pr-3 text-slate-300">{exec.nome || exec.arquivo}</td>
                    <td className="py-1.5 pr-3 text-slate-500">{formatarQuando(exec.iniciado_em)}</td>
                    <td className="py-1.5 pr-3 text-slate-400">
                      {exec.status === 'completed' ? (exec.conclusao || 'concluído') : exec.status}
                    </td>
                    <td className="py-1.5">
                      <a href={exec.url} target="_blank" rel="noreferrer" className="text-slate-500 hover:text-emerald-400">
                        <ExternalLink size={12} />
                      </a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
