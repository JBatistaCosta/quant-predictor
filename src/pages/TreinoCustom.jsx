// src/pages/TreinoCustom.jsx — rota /treino-custom
// Painel de Treino Customizado: permite criar configurações de modelos
// (algoritmo + features selecionadas + hiperparâmetros) e disparar o
// workflow treinar_modelo_custom.yml no GitHub Actions via
// api/model-maintenance?tarefa=salvar-config-custom /
// listar-configs-custom / disparar-treino-custom.
//
// O status de cada configuração é gerenciado pelo próprio workflow:
//   rascunho → aguardando_treino → treinando → treinado | erro
// O painel faz polling leve (a cada 15s) nos configs que estão em
// aguardando_treino ou treinando, atualizando o status/métricas quando
// o workflow conclui.
import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Plus, Play, Save, Trash2, ChevronDown, ChevronUp, Loader2,
  AlertTriangle, CheckCircle2, Clock, XCircle, Zap, Settings2,
  FlaskConical, BarChart3, RefreshCw,
} from 'lucide-react';
import { useAuth } from '../AuthContext';
import { apiUrl } from '../utils/apiUrl';

// -----------------------------------------------------------------------
// Catálogo de features disponíveis, agrupadas por tema
// -----------------------------------------------------------------------
const FEATURE_GROUPS = [
  {
    grupo: 'Força de time',
    features: [
      { key: 'elo_home', label: 'Elo mandante' },
      { key: 'elo_away', label: 'Elo visitante' },
      { key: 'elo_diff', label: 'Diferença de Elo' },
      { key: 'elo_externo_home', label: 'Elo externo (ClubElo) mandante' },
      { key: 'elo_externo_away', label: 'Elo externo (ClubElo) visitante' },
      { key: 'forca_elenco_home', label: 'Força do elenco mandante (valor de mercado)' },
      { key: 'forca_elenco_away', label: 'Força do elenco visitante (valor de mercado)' },
    ],
  },
  {
    grupo: 'Forma recente',
    features: [
      { key: 'media_gols_marcados_home_5j', label: 'Média gols marcados (mandante, 5j)' },
      { key: 'media_gols_sofridos_home_5j', label: 'Média gols sofridos (mandante, 5j)' },
      { key: 'media_gols_marcados_away_5j', label: 'Média gols marcados (visitante, 5j)' },
      { key: 'media_gols_sofridos_away_5j', label: 'Média gols sofridos (visitante, 5j)' },
      { key: 'pontos_home_5j', label: 'Pontos nos últimos 5 jogos (mandante)' },
      { key: 'pontos_away_5j', label: 'Pontos nos últimos 5 jogos (visitante)' },
    ],
  },
  {
    grupo: 'Descanso e fadiga',
    features: [
      { key: 'dias_descanso_home', label: 'Dias de descanso (mandante)' },
      { key: 'dias_descanso_away', label: 'Dias de descanso (visitante)' },
      { key: 'fadiga_home', label: 'Fadiga mandante (jogos acumulados)' },
      { key: 'fadiga_away', label: 'Fadiga visitante (jogos acumulados)' },
    ],
  },
  {
    grupo: 'Cartões e suspensões',
    features: [
      { key: 'risco_suspensao_home', label: 'Risco de suspensão (mandante)' },
      { key: 'risco_suspensao_away', label: 'Risco de suspensão (visitante)' },
      { key: 'cartoes_amarelos_home_5j', label: 'Cartões amarelos (mandante, 5j)' },
      { key: 'cartoes_amarelos_away_5j', label: 'Cartões amarelos (visitante, 5j)' },
    ],
  },
  {
    grupo: 'Tabela e contexto',
    features: [
      { key: 'posicao_tabela_home', label: 'Posição na tabela (mandante)' },
      { key: 'posicao_tabela_away', label: 'Posição na tabela (visitante)' },
      { key: 'pontos_tabela_home', label: 'Pontos na tabela (mandante)' },
      { key: 'pontos_tabela_away', label: 'Pontos na tabela (visitante)' },
      { key: 'h2h_vitorias_home', label: 'Confronto direto: vitórias mandante' },
      { key: 'h2h_empates', label: 'Confronto direto: empates' },
      { key: 'h2h_vitorias_away', label: 'Confronto direto: vitórias visitante' },
    ],
  },
  {
    grupo: 'Árbitro',
    features: [
      { key: 'tendencia_arbitro_home', label: 'Tendência do árbitro (favorável ao mandante)' },
      { key: 'arbitro_cartoes_por_jogo', label: 'Cartões por jogo do árbitro' },
    ],
  },
  {
    grupo: 'XI titular e mercado',
    features: [
      { key: 'forca_xi_home', label: 'Força do XI titular (mandante)' },
      { key: 'forca_xi_away', label: 'Força do XI titular (visitante)' },
      { key: 'valor_mercado_xi_home', label: 'Valor de mercado do XI (mandante, €M)' },
      { key: 'valor_mercado_xi_away', label: 'Valor de mercado do XI (visitante, €M)' },
    ],
  },
  {
    grupo: 'xG e estatísticas avançadas',
    features: [
      { key: 'xg_home_5j', label: 'xG médio (mandante, 5j)' },
      { key: 'xg_away_5j', label: 'xG médio (visitante, 5j)' },
      { key: 'xg_contra_home_5j', label: 'xG sofrido médio (mandante, 5j)' },
      { key: 'xg_contra_away_5j', label: 'xG sofrido médio (visitante, 5j)' },
    ],
  },
];

const ALGORITMOS = [
  { value: 'catboost', label: 'CatBoost' },
  { value: 'xgboost', label: 'XGBoost' },
  { value: 'lightgbm', label: 'LightGBM' },
  { value: 'logistic_regression', label: 'Regressão Logística' },
  { value: 'random_forest', label: 'Random Forest' },
  { value: 'dixon_coles', label: 'Dixon-Coles' },
];

const TARGETS = [
  { value: '1x2', label: 'Resultado 1X2' },
  { value: 'over_under_2.5', label: 'Over/Under 2.5' },
  { value: 'btts', label: 'Ambas marcam (BTTS)' },
];

const STATUS_INFO = {
  rascunho: { label: 'Rascunho', icon: Settings2, cor: 'text-slate-400', bg: 'bg-slate-700' },
  aguardando_treino: { label: 'Aguardando treino', icon: Clock, cor: 'text-yellow-400', bg: 'bg-yellow-900/30' },
  treinando: { label: 'Treinando...', icon: Loader2, cor: 'text-blue-400', bg: 'bg-blue-900/30' },
  treinado: { label: 'Treinado ✓', icon: CheckCircle2, cor: 'text-emerald-400', bg: 'bg-emerald-900/30' },
  erro: { label: 'Erro', icon: XCircle, cor: 'text-red-400', bg: 'bg-red-900/30' },
};

const ESTADO_FORM_INICIAL = {
  id: null,
  name: '',
  algorithm: 'catboost',
  features: [],
  target: '1x2',
  notes: '',
  hyperparameters: '',
};

// -----------------------------------------------------------------------
// Componente Principal
// -----------------------------------------------------------------------
export default function TreinoCustom() {
  const { session } = useAuth();
  const [configs, setConfigs] = useState([]);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState(null);
  const [form, setForm] = useState(ESTADO_FORM_INICIAL);
  const [salvando, setSalvando] = useState(false);
  const [disparando, setDisparando] = useState(null); // config_id em disparo
  const [mensagem, setMensagem] = useState(null); // { tipo: 'ok'|'erro', texto }
  const [expandidos, setExpandidos] = useState({}); // config_id → bool
  const [grupoExpandido, setGrupoExpandido] = useState({}); // grupo → bool
  const [formAberto, setFormAberto] = useState(false);
  const pollingRef = useRef(null);

  const authHeader = session?.access_token
    ? { Authorization: `Bearer ${session.access_token}` }
    : {};

  // --------- Carregamento ---------
  const carregarConfigs = useCallback(async (silencioso = false) => {
    if (!silencioso) setCarregando(true);
    setErro(null);
    try {
      const resp = await fetch(apiUrl('/api/model-maintenance?tarefa=listar-configs-custom'));
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const dados = await resp.json();
      setConfigs(dados.configs || []);
    } catch (e) {
      if (!silencioso) setErro(e.message);
    } finally {
      if (!silencioso) setCarregando(false);
    }
  }, []);

  useEffect(() => {
    carregarConfigs();
  }, [carregarConfigs]);

  // Polling leve: verifica configs em progresso a cada 15s
  useEffect(() => {
    const temEmProgresso = configs.some(
      (c) => c.status === 'aguardando_treino' || c.status === 'treinando',
    );
    if (temEmProgresso) {
      pollingRef.current = setInterval(() => carregarConfigs(true), 15000);
    } else {
      clearInterval(pollingRef.current);
    }
    return () => clearInterval(pollingRef.current);
  }, [configs, carregarConfigs]);

  // --------- Mensagens temporárias ---------
  function mostrarMensagem(tipo, texto) {
    setMensagem({ tipo, texto });
    setTimeout(() => setMensagem(null), 5000);
  }

  // --------- Seleção de features ---------
  function toggleFeature(key) {
    setForm((f) => ({
      ...f,
      features: f.features.includes(key)
        ? f.features.filter((k) => k !== key)
        : [...f.features, key],
    }));
  }

  function toggleGrupoCompleto(grupo) {
    const keysDoGrupo = FEATURE_GROUPS.find((g) => g.grupo === grupo)?.features.map((f) => f.key) || [];
    const todosAtivos = keysDoGrupo.every((k) => form.features.includes(k));
    if (todosAtivos) {
      setForm((f) => ({ ...f, features: f.features.filter((k) => !keysDoGrupo.includes(k)) }));
    } else {
      setForm((f) => ({ ...f, features: [...new Set([...f.features, ...keysDoGrupo])] }));
    }
  }

  // --------- Editar configuração existente ---------
  function editarConfig(cfg) {
    setForm({
      id: cfg.id,
      name: cfg.name,
      algorithm: cfg.algorithm,
      features: cfg.features || [],
      target: cfg.target || '1x2',
      notes: cfg.notes || '',
      hyperparameters: cfg.hyperparameters ? JSON.stringify(cfg.hyperparameters, null, 2) : '',
    });
    setFormAberto(true);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  function novaConfigForm() {
    setForm(ESTADO_FORM_INICIAL);
    setFormAberto(true);
  }

  // --------- Salvar configuração ---------
  async function salvarConfig(e) {
    e.preventDefault();
    if (!form.name.trim()) { mostrarMensagem('erro', 'Informe um nome para o modelo.'); return; }
    if (form.features.length === 0) { mostrarMensagem('erro', 'Selecione pelo menos 1 feature.'); return; }

    let hyperparameters = null;
    if (form.hyperparameters.trim()) {
      try { hyperparameters = JSON.parse(form.hyperparameters); }
      catch { mostrarMensagem('erro', 'Hiperparâmetros com JSON inválido.'); return; }
    }

    setSalvando(true);
    try {
      const resp = await fetch(apiUrl('/api/model-maintenance?tarefa=salvar-config-custom'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeader },
        body: JSON.stringify({
          id: form.id || undefined,
          name: form.name.trim(),
          algorithm: form.algorithm,
          features: form.features,
          target: form.target,
          notes: form.notes.trim() || null,
          hyperparameters,
        }),
      });
      const dados = await resp.json();
      if (!resp.ok) throw new Error(dados.error?.message || `HTTP ${resp.status}`);
      mostrarMensagem('ok', `Configuração "${dados.config.name}" salva com sucesso.`);
      setForm(ESTADO_FORM_INICIAL);
      setFormAberto(false);
      await carregarConfigs();
    } catch (e) {
      mostrarMensagem('erro', e.message);
    } finally {
      setSalvando(false);
    }
  }

  // --------- Disparar treino ---------
  async function dispararTreino(configId, configName) {
    if (!session) { mostrarMensagem('erro', 'Faça login para disparar o treino.'); return; }
    setDisparando(configId);
    try {
      const resp = await fetch(
        apiUrl(`/api/model-maintenance?tarefa=disparar-treino-custom&config_id=${configId}`),
        { method: 'POST', headers: authHeader },
      );
      const dados = await resp.json();
      if (!resp.ok) throw new Error(dados.error?.message || `HTTP ${resp.status}`);
      mostrarMensagem('ok', `Treino do modelo "${configName}" disparado! Acompanhe o status abaixo.`);
      await carregarConfigs();
    } catch (e) {
      mostrarMensagem('erro', e.message);
    } finally {
      setDisparando(null);
    }
  }

  // --------- Renderização ---------
  function fmtData(iso) {
    if (!iso) return '—';
    return new Date(iso).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  }

  function renderMetricas(metrics) {
    if (!metrics) return null;
    return (
      <div className="mt-2 flex flex-wrap gap-3">
        {Object.entries(metrics).map(([k, v]) => (
          <span key={k} className="bg-slate-700 rounded px-2 py-0.5 text-xs text-slate-300">
            <span className="text-slate-500">{k}: </span>
            {typeof v === 'number' ? v.toFixed(4) : String(v)}
          </span>
        ))}
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto px-4 py-8 space-y-8">
      {/* Cabeçalho */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-2">
            <FlaskConical className="text-violet-400" size={26} />
            Painel de Treino Customizado
          </h1>
          <p className="text-slate-400 text-sm mt-1">
            Crie suas próprias combinações de algoritmo + features e treine novos modelos.
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => carregarConfigs()}
            className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-sm text-slate-400 hover:text-white hover:bg-slate-700 transition-colors"
            title="Atualizar lista"
          >
            <RefreshCw size={14} />
          </button>
          <button
            onClick={novaConfigForm}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-violet-600 hover:bg-violet-500 text-white text-sm font-medium transition-colors"
          >
            <Plus size={16} /> Nova configuração
          </button>
        </div>
      </div>

      {/* Toast de mensagem */}
      {mensagem && (
        <div className={`flex items-center gap-2 px-4 py-3 rounded-lg text-sm font-medium ${
          mensagem.tipo === 'ok'
            ? 'bg-emerald-900/50 border border-emerald-700 text-emerald-300'
            : 'bg-red-900/50 border border-red-700 text-red-300'
        }`}>
          {mensagem.tipo === 'ok' ? <CheckCircle2 size={16} /> : <AlertTriangle size={16} />}
          {mensagem.texto}
        </div>
      )}

      {/* Formulário de nova / editar configuração */}
      {formAberto && (
        <div className="bg-slate-800 border border-slate-700 rounded-xl p-6 space-y-6">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold text-white flex items-center gap-2">
              <Settings2 size={18} className="text-violet-400" />
              {form.id ? 'Editar configuração' : 'Nova configuração'}
            </h2>
            <button onClick={() => setFormAberto(false)} className="text-slate-500 hover:text-white transition-colors text-sm">
              Cancelar
            </button>
          </div>

          <form onSubmit={salvarConfig} className="space-y-5">
            {/* Nome */}
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-1">Nome do modelo *</label>
              <input
                type="text"
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                placeholder="Ex: CatBoost com xG e descanso"
                className="w-full bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-violet-500"
              />
            </div>

            {/* Algoritmo e Target */}
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-slate-300 mb-1">Algoritmo *</label>
                <select
                  value={form.algorithm}
                  onChange={(e) => setForm((f) => ({ ...f, algorithm: e.target.value }))}
                  className="w-full bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:ring-2 focus:ring-violet-500"
                >
                  {ALGORITMOS.map((a) => (
                    <option key={a.value} value={a.value}>{a.label}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-300 mb-1">Mercado alvo *</label>
                <select
                  value={form.target}
                  onChange={(e) => setForm((f) => ({ ...f, target: e.target.value }))}
                  className="w-full bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:ring-2 focus:ring-violet-500"
                >
                  {TARGETS.map((t) => (
                    <option key={t.value} value={t.value}>{t.label}</option>
                  ))}
                </select>
              </div>
            </div>

            {/* Features */}
            <div>
              <div className="flex items-center justify-between mb-2">
                <label className="text-sm font-medium text-slate-300">
                  Features *{' '}
                  <span className="text-slate-500 font-normal">({form.features.length} selecionada{form.features.length !== 1 ? 's' : ''})</span>
                </label>
                <button
                  type="button"
                  onClick={() => setForm((f) => ({ ...f, features: [] }))}
                  className="text-xs text-slate-500 hover:text-slate-300 transition-colors"
                >
                  Limpar tudo
                </button>
              </div>

              <div className="space-y-2 max-h-96 overflow-y-auto pr-1">
                {FEATURE_GROUPS.map((grupo) => {
                  const aberto = grupoExpandido[grupo.grupo] !== false; // aberto por padrão
                  const todosAtivos = grupo.features.every((f) => form.features.includes(f.key));
                  const algumAtivo = grupo.features.some((f) => form.features.includes(f.key));
                  return (
                    <div key={grupo.grupo} className="border border-slate-700 rounded-lg overflow-hidden">
                      <button
                        type="button"
                        onClick={() => setGrupoExpandido((g) => ({ ...g, [grupo.grupo]: !aberto }))}
                        className="w-full flex items-center justify-between px-3 py-2 bg-slate-750 hover:bg-slate-700 transition-colors"
                      >
                        <span className="flex items-center gap-2 text-sm font-medium text-slate-300">
                          <span className={`w-2 h-2 rounded-full ${todosAtivos ? 'bg-violet-400' : algumAtivo ? 'bg-violet-600' : 'bg-slate-600'}`} />
                          {grupo.grupo}
                          <span className="text-slate-500 font-normal text-xs">
                            ({grupo.features.filter((f) => form.features.includes(f.key)).length}/{grupo.features.length})
                          </span>
                        </span>
                        <div className="flex items-center gap-2">
                          <span
                            role="button"
                            tabIndex={0}
                            onClick={(e) => { e.stopPropagation(); toggleGrupoCompleto(grupo.grupo); }}
                            onKeyDown={(e) => { if (e.key === 'Enter') { e.stopPropagation(); toggleGrupoCompleto(grupo.grupo); } }}
                            className="text-xs text-violet-400 hover:text-violet-300 transition-colors px-1"
                          >
                            {todosAtivos ? 'remover todos' : 'selecionar todos'}
                          </span>
                          {aberto ? <ChevronUp size={14} className="text-slate-500" /> : <ChevronDown size={14} className="text-slate-500" />}
                        </div>
                      </button>
                      {aberto && (
                        <div className="px-3 py-2 grid grid-cols-2 gap-1.5 bg-slate-800">
                          {grupo.features.map((f) => {
                            const ativo = form.features.includes(f.key);
                            return (
                              <label
                                key={f.key}
                                className={`flex items-center gap-2 cursor-pointer rounded px-2 py-1 text-xs transition-colors ${
                                  ativo ? 'bg-violet-900/40 text-violet-300' : 'text-slate-400 hover:text-slate-200 hover:bg-slate-700'
                                }`}
                              >
                                <input
                                  type="checkbox"
                                  checked={ativo}
                                  onChange={() => toggleFeature(f.key)}
                                  className="accent-violet-500 w-3 h-3"
                                />
                                {f.label}
                              </label>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Hiperparâmetros (opcional) */}
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-1">
                Hiperparâmetros <span className="text-slate-500 font-normal">(opcional, JSON)</span>
              </label>
              <textarea
                value={form.hyperparameters}
                onChange={(e) => setForm((f) => ({ ...f, hyperparameters: e.target.value }))}
                placeholder={'{\n  "n_estimators": 300,\n  "max_depth": 6\n}'}
                rows={4}
                className="w-full bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm font-mono placeholder-slate-600 focus:outline-none focus:ring-2 focus:ring-violet-500 resize-none"
              />
            </div>

            {/* Notas */}
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-1">
                Notas <span className="text-slate-500 font-normal">(opcional)</span>
              </label>
              <textarea
                value={form.notes}
                onChange={(e) => setForm((f) => ({ ...f, notes: e.target.value }))}
                placeholder="Objetivo, hipóteses, comparações..."
                rows={2}
                className="w-full bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-violet-500 resize-none"
              />
            </div>

            {/* Botão salvar */}
            <div className="flex justify-end gap-3">
              <button
                type="button"
                onClick={() => setFormAberto(false)}
                className="px-4 py-2 rounded-lg text-sm text-slate-400 hover:text-white hover:bg-slate-700 transition-colors"
              >
                Cancelar
              </button>
              <button
                type="submit"
                disabled={salvando}
                className="flex items-center gap-2 px-5 py-2 rounded-lg bg-violet-600 hover:bg-violet-500 disabled:opacity-50 text-white text-sm font-medium transition-colors"
              >
                {salvando ? <Loader2 size={15} className="animate-spin" /> : <Save size={15} />}
                {salvando ? 'Salvando...' : 'Salvar configuração'}
              </button>
            </div>
          </form>
        </div>
      )}

      {/* Lista de configurações */}
      {carregando ? (
        <div className="flex items-center justify-center py-12 text-slate-500 gap-2">
          <Loader2 className="animate-spin" size={20} />
          Carregando configurações...
        </div>
      ) : erro ? (
        <div className="flex items-center gap-2 text-red-400 text-sm py-6">
          <AlertTriangle size={16} /> Erro ao carregar: {erro}
        </div>
      ) : configs.length === 0 ? (
        <div className="text-center py-16 text-slate-500">
          <FlaskConical size={40} className="mx-auto mb-3 opacity-30" />
          <p className="text-sm">Nenhuma configuração criada ainda.</p>
          <button
            onClick={novaConfigForm}
            className="mt-3 text-violet-400 hover:text-violet-300 text-sm transition-colors"
          >
            Criar primeira configuração →
          </button>
        </div>
      ) : (
        <div className="space-y-3">
          <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wide">
            Configurações ({configs.length})
          </h2>
          {configs.map((cfg) => {
            const statusInfo = STATUS_INFO[cfg.status] || STATUS_INFO.rascunho;
            const StatusIcon = statusInfo.icon;
            const aberto = expandidos[cfg.id];
            const emProgresso = cfg.status === 'aguardando_treino' || cfg.status === 'treinando';

            return (
              <div key={cfg.id} className={`border rounded-xl overflow-hidden transition-colors ${statusInfo.bg} border-slate-700`}>
                {/* Cabeçalho da config */}
                <div className="flex items-center gap-3 px-4 py-3">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-semibold text-white text-sm truncate">{cfg.name}</span>
                      <span className={`flex items-center gap-1 text-xs font-medium ${statusInfo.cor}`}>
                        <StatusIcon size={12} className={emProgresso ? 'animate-spin' : ''} />
                        {statusInfo.label}
                      </span>
                    </div>
                    <div className="flex items-center gap-3 mt-0.5 flex-wrap">
                      <span className="text-xs text-slate-500">
                        {ALGORITMOS.find((a) => a.value === cfg.algorithm)?.label || cfg.algorithm}
                      </span>
                      <span className="text-xs text-slate-600">·</span>
                      <span className="text-xs text-slate-500">
                        {TARGETS.find((t) => t.value === cfg.target)?.label || cfg.target}
                      </span>
                      <span className="text-xs text-slate-600">·</span>
                      <span className="text-xs text-slate-500">{cfg.features?.length || 0} features</span>
                      <span className="text-xs text-slate-600">·</span>
                      <span className="text-xs text-slate-600">{fmtData(cfg.created_at)}</span>
                    </div>
                  </div>

                  <div className="flex items-center gap-1 shrink-0">
                    {/* Botão Treinar */}
                    {(cfg.status === 'rascunho' || cfg.status === 'treinado' || cfg.status === 'erro') && session && (
                      <button
                        onClick={() => dispararTreino(cfg.id, cfg.name)}
                        disabled={disparando === cfg.id}
                        title="Disparar treino"
                        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-violet-600 hover:bg-violet-500 disabled:opacity-50 text-white text-xs font-medium transition-colors"
                      >
                        {disparando === cfg.id
                          ? <Loader2 size={13} className="animate-spin" />
                          : <Play size={13} />}
                        Treinar
                      </button>
                    )}
                    {/* Botão Editar */}
                    {cfg.status === 'rascunho' && (
                      <button
                        onClick={() => editarConfig(cfg)}
                        title="Editar"
                        className="px-2 py-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-slate-700 transition-colors text-xs"
                      >
                        Editar
                      </button>
                    )}
                    {/* Expandir */}
                    <button
                      onClick={() => setExpandidos((e) => ({ ...e, [cfg.id]: !aberto }))}
                      className="p-1.5 rounded-lg text-slate-500 hover:text-white hover:bg-slate-700 transition-colors"
                    >
                      {aberto ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
                    </button>
                  </div>
                </div>

                {/* Detalhe expandido */}
                {aberto && (
                  <div className="border-t border-slate-700 px-4 py-3 space-y-3">
                    {/* Features */}
                    <div>
                      <p className="text-xs text-slate-500 mb-1 font-medium">Features selecionadas</p>
                      <div className="flex flex-wrap gap-1">
                        {(cfg.features || []).map((fk) => {
                          const meta = FEATURE_GROUPS.flatMap((g) => g.features).find((f) => f.key === fk);
                          return (
                            <span key={fk} className="bg-slate-700 text-slate-300 text-xs px-2 py-0.5 rounded">
                              {meta?.label || fk}
                            </span>
                          );
                        })}
                        {(!cfg.features || cfg.features.length === 0) && (
                          <span className="text-slate-600 text-xs">—</span>
                        )}
                      </div>
                    </div>

                    {/* Métricas (se treinado) */}
                    {cfg.metrics && (
                      <div>
                        <p className="text-xs text-slate-500 mb-1 font-medium flex items-center gap-1">
                          <BarChart3 size={12} /> Métricas
                        </p>
                        {renderMetricas(cfg.metrics)}
                        {cfg.trained_at && (
                          <p className="text-xs text-slate-600 mt-1">Treinado em: {fmtData(cfg.trained_at)}</p>
                        )}
                      </div>
                    )}

                    {/* Erro (se houver) */}
                    {cfg.error_message && (
                      <div className="bg-red-900/20 border border-red-800 rounded-lg px-3 py-2 text-xs text-red-300">
                        <span className="font-medium">Erro: </span>{cfg.error_message}
                      </div>
                    )}

                    {/* Notas */}
                    {cfg.notes && (
                      <div>
                        <p className="text-xs text-slate-500 mb-0.5 font-medium">Notas</p>
                        <p className="text-xs text-slate-400">{cfg.notes}</p>
                      </div>
                    )}

                    {/* Hiperparâmetros */}
                    {cfg.hyperparameters && (
                      <div>
                        <p className="text-xs text-slate-500 mb-1 font-medium">Hiperparâmetros</p>
                        <pre className="text-xs text-slate-400 bg-slate-900 rounded p-2 overflow-x-auto">
                          {JSON.stringify(cfg.hyperparameters, null, 2)}
                        </pre>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Legenda de status */}
      <div className="border-t border-slate-800 pt-4">
        <p className="text-xs text-slate-600 mb-2 font-medium">Ciclo de vida de um modelo customizado:</p>
        <div className="flex flex-wrap gap-3">
          {Object.entries(STATUS_INFO).map(([key, info]) => {
            const Icon = info.icon;
            return (
              <span key={key} className={`flex items-center gap-1 text-xs ${info.cor}`}>
                <Icon size={11} /> {info.label}
              </span>
            );
          })}
        </div>
      </div>
    </div>
  );
}
