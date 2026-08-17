// src/pages/CarteiraPaperTrading.jsx — rota /carteira-paper-trading
// Carteira (Paper Trading): diferente da Simulação de Carteira (sempre
// recalculada do zero sobre histórico JÁ RESOLVIDO, ver SimulacaoCarteira.jsx),
// aqui a banca é PERSISTENTE e evolui sobre partidas AINDA NÃO disputadas,
// usando odds ao vivo reais já sincronizadas (odds_market, snapshot=
// pre_closing, só existe pras ligas domésticas -- tarefaOddsSync/tarefaOddsTodas)
// e as predições que já existem pra essas partidas agendadas (predicoes/
// model_predictions). Servido por api/model-maintenance.js
// (?tarefa=paper-carteira-*, fundido lá em vez de arquivo próprio por causa
// do teto de 12 Serverless Functions do plano Hobby do Vercel).
import React, { useState, useEffect, useCallback } from 'react';
import { Landmark, Loader2, AlertTriangle, Plus, ChevronDown, ChevronRight, PlayCircle, RotateCcw, Pause, Play, Trash2, X } from 'lucide-react';
import { useAuth } from '../AuthContext';
import { apiUrl } from '../utils/apiUrl';

const MERCADOS = [
  { chave: '1X2', rotulo: '1X2' },
  { chave: 'over_under_2.5', rotulo: 'Over/Under 2.5' },
  { chave: 'btts', rotulo: 'Ambas Marcam (BTTS)' },
];

const ROTULO_STATUS = { pendente: 'Pendente', ganhou: 'Ganhou', perdeu: 'Perdeu', anulada: 'Anulada' };
const COR_STATUS = { pendente: 'text-amber-400 bg-amber-500/10', ganhou: 'text-emerald-400 bg-emerald-500/10', perdeu: 'text-red-400 bg-red-500/10', anulada: 'text-slate-400 bg-slate-700/30' };

function fmtMoney(v) {
  if (v == null) return '—';
  return Number(v).toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function fmtPct(v, casas = 1) {
  if (v == null) return '—';
  return `${v >= 0 ? '+' : ''}${v.toFixed(casas)}%`;
}
function fmtData(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit', year: 'numeric' });
}

const ESTADO_FORM_INICIAL = {
  nome: '', modelo: '', mercado: '1X2', liga_id: '',
  usar_calibracao: 'nenhuma', tipo_stake: 'kelly', stake_fixa: 10, kelly_multiplier: 0.25,
  teto_exposicao_pct: 0.15, ev_minimo: 1.02, ev_maximo: 2.0, stake_minima_pct: 0.005, banca_inicial: 1000,
};

function CardCarteira({ carteira, sessao, authHeader, onMudou }) {
  const [aberto, setAberto] = useState(false);
  const [detalhe, setDetalhe] = useState(null);
  const [carregandoDetalhe, setCarregandoDetalhe] = useState(false);
  const [ocupado, setOcupado] = useState(null); // 'apostar' | 'resolver' | 'alternar' | 'excluir'
  const [confirmarExclusao, setConfirmarExclusao] = useState(false);
  const [mensagem, setMensagem] = useState(null);

  const r = carteira.resumo || {};
  const roiPos = (r.roi_total_pct ?? 0) >= 0;

  const carregarDetalhe = useCallback(async () => {
    setCarregandoDetalhe(true);
    try {
      const resp = await fetch(apiUrl(`/api/model-maintenance?tarefa=paper-carteira-detalhe&carteira_id=${carteira.id}`));
      const dados = await resp.json();
      if (!resp.ok) throw new Error(dados.error?.message || 'Erro ao carregar detalhe.');
      setDetalhe(dados);
    } catch (e) {
      setMensagem({ tipo: 'erro', texto: e.message });
    } finally {
      setCarregandoDetalhe(false);
    }
  }, [carteira.id]);

  useEffect(() => { if (aberto) carregarDetalhe(); }, [aberto, carregarDetalhe]);

  async function acao(tarefa, sucesso) {
    if (!sessao) { setMensagem({ tipo: 'erro', texto: 'Faça login para esta ação.' }); return; }
    setOcupado(tarefa);
    setMensagem(null);
    try {
      const resp = await fetch(apiUrl(`/api/model-maintenance?tarefa=${tarefa}`), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeader },
        body: JSON.stringify({ carteira_id: carteira.id }),
      });
      const dados = await resp.json();
      if (!resp.ok) throw new Error(dados.error?.message || `HTTP ${resp.status}`);
      setMensagem({ tipo: 'ok', texto: sucesso(dados) });
      await onMudou();
      if (aberto) await carregarDetalhe();
    } catch (e) {
      setMensagem({ tipo: 'erro', texto: e.message });
    } finally {
      setOcupado(null);
    }
  }

  const excluir = async () => {
    if (!sessao) { setMensagem({ tipo: 'erro', texto: 'Faça login para esta ação.' }); return; }
    setOcupado('excluir');
    try {
      const resp = await fetch(apiUrl('/api/model-maintenance?tarefa=paper-carteira-excluir'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeader },
        body: JSON.stringify({ carteira_id: carteira.id }),
      });
      const dados = await resp.json();
      if (!resp.ok) throw new Error(dados.error?.message || `HTTP ${resp.status}`);
      await onMudou();
    } catch (e) {
      setMensagem({ tipo: 'erro', texto: e.message });
      setOcupado(null);
    }
  };

  return (
    <div className={`bg-slate-900 border rounded-xl overflow-hidden ${carteira.ativa ? 'border-slate-700/50' : 'border-slate-800 opacity-70'}`}>
      <div className="p-4">
        <div className="flex items-center justify-between gap-2 mb-1">
          <span className="text-sm font-bold text-slate-200 truncate" title={carteira.nome}>{carteira.nome}</span>
          <span className={`text-xs font-bold px-1.5 py-0.5 rounded ${roiPos ? 'bg-emerald-500/10 text-emerald-400' : 'bg-red-500/10 text-red-400'}`}>
            {fmtPct(r.roi_total_pct)}
          </span>
        </div>
        <div className="flex items-center gap-1.5 flex-wrap mb-2">
          <span className="text-[9px] font-bold uppercase px-1.5 py-0.5 rounded bg-slate-800 text-slate-400">{carteira.modelo}</span>
          <span className="text-[9px] font-bold uppercase px-1.5 py-0.5 rounded bg-slate-800 text-slate-400">{MERCADOS.find((m) => m.chave === carteira.mercado)?.rotulo || carteira.mercado}</span>
          {!carteira.ativa && <span className="text-[9px] font-bold uppercase px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-400">Pausada</span>}
        </div>
        <div className="text-[11px] text-slate-500 mb-2">
          banca R$ {fmtMoney(carteira.banca_atual)} (inicial R$ {fmtMoney(carteira.banca_inicial)}) · {r.n_apostas_pendentes || 0} pendentes · {r.n_apostas_resolvidas || 0} resolvidas
        </div>
        <div className="grid grid-cols-3 gap-2 mt-3 text-center">
          <div>
            <div className="text-[9px] uppercase text-slate-500">Acerto</div>
            <div className="text-xs font-bold text-slate-300">{r.win_rate_pct == null ? '—' : `${r.win_rate_pct.toFixed(1)}%`}</div>
          </div>
          <div>
            <div className="text-[9px] uppercase text-slate-500">Ganhas</div>
            <div className="text-xs font-bold text-emerald-400">{r.n_apostas_ganhas ?? 0}</div>
          </div>
          <div>
            <div className="text-[9px] uppercase text-slate-500">Perdidas</div>
            <div className="text-xs font-bold text-red-400">{r.n_apostas_perdidas ?? 0}</div>
          </div>
        </div>

        {mensagem && (
          <div className={`mt-3 text-[11px] px-2 py-1.5 rounded-lg ${mensagem.tipo === 'ok' ? 'bg-emerald-950/40 text-emerald-300' : 'bg-red-950/40 text-red-300'}`}>
            {mensagem.texto}
          </div>
        )}

        <div className="flex flex-wrap gap-1.5 mt-3">
          <button
            onClick={() => acao('paper-carteira-apostar', (d) => d.apostas_novas > 0 ? `${d.apostas_novas} aposta(s) nova(s).` : (d.motivo || 'Nenhuma aposta nova.'))}
            disabled={ocupado != null || !carteira.ativa}
            className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white text-[11px] font-bold"
          >
            {ocupado === 'paper-carteira-apostar' ? <Loader2 size={12} className="animate-spin" /> : <PlayCircle size={12} />} Apostar
          </button>
          <button
            onClick={() => acao('paper-carteira-resolver', (d) => `${d.total_resolvidas} aposta(s) resolvida(s).`)}
            disabled={ocupado != null}
            className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 disabled:opacity-50 text-slate-200 text-[11px] font-bold border border-slate-700"
          >
            {ocupado === 'paper-carteira-resolver' ? <Loader2 size={12} className="animate-spin" /> : <RotateCcw size={12} />} Resolver
          </button>
          <button
            onClick={() => acao('paper-carteira-alternar-ativa', (d) => d.carteira.ativa ? 'Carteira reativada.' : 'Carteira pausada.')}
            disabled={ocupado != null}
            className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 disabled:opacity-50 text-slate-200 text-[11px] font-bold border border-slate-700"
          >
            {ocupado === 'paper-carteira-alternar-ativa' ? <Loader2 size={12} className="animate-spin" /> : carteira.ativa ? <Pause size={12} /> : <Play size={12} />}
            {carteira.ativa ? 'Pausar' : 'Reativar'}
          </button>
          {confirmarExclusao ? (
            <div className="flex items-center gap-1">
              <button onClick={excluir} disabled={ocupado != null} className="px-2.5 py-1.5 rounded-lg bg-red-600 hover:bg-red-500 disabled:opacity-50 text-white text-[11px] font-bold">Confirmar</button>
              <button onClick={() => setConfirmarExclusao(false)} className="px-2 py-1.5 rounded-lg bg-slate-800 text-slate-400 text-[11px]"><X size={12} /></button>
            </div>
          ) : (
            <button onClick={() => setConfirmarExclusao(true)} disabled={ocupado != null} className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-slate-800 hover:bg-red-950/40 disabled:opacity-50 text-slate-400 hover:text-red-400 text-[11px] font-bold border border-slate-700">
              <Trash2 size={12} />
            </button>
          )}
        </div>
      </div>

      <button onClick={() => setAberto((a) => !a)} className="w-full flex items-center justify-center gap-1 text-[11px] text-slate-500 hover:text-slate-300 py-2 border-t border-slate-800 bg-slate-900/60">
        {aberto ? <ChevronDown size={13} /> : <ChevronRight size={13} />} {aberto ? 'Ocultar apostas' : 'Ver apostas'}
      </button>
      {aberto && (
        <div className="max-h-96 overflow-y-auto border-t border-slate-800">
          {carregandoDetalhe ? (
            <div className="flex items-center justify-center py-8 text-slate-500 gap-2 text-xs"><Loader2 className="animate-spin" size={14} /> Carregando...</div>
          ) : !detalhe || detalhe.apostas.length === 0 ? (
            <div className="text-xs text-slate-500 text-center py-8">Nenhuma aposta ainda.</div>
          ) : (
            <table className="w-full text-[11px]">
              <thead className="sticky top-0 bg-slate-800">
                <tr className="text-slate-500 uppercase text-[9px]">
                  <th className="text-left p-1.5">Partida</th><th className="text-left p-1.5">Data</th>
                  <th className="text-left p-1.5">Seleção</th><th className="text-right p-1.5">Odd</th>
                  <th className="text-right p-1.5">EV</th><th className="text-right p-1.5">Stake</th>
                  <th className="text-center p-1.5">Status</th><th className="text-right p-1.5">Resultado</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800">
                {detalhe.apostas.map((a) => (
                  <tr key={a.id}>
                    <td className="p-1.5 text-slate-300 max-w-[160px] truncate" title={a.partida ? `${a.partida.home} x ${a.partida.away}` : ''}>
                      {a.partida ? `${a.partida.home} x ${a.partida.away}` : `#${a.match_id}`}
                    </td>
                    <td className="p-1.5 text-slate-500">{fmtData(a.data_partida)}</td>
                    <td className="p-1.5 text-slate-400">{a.selection} <span className="text-slate-600">({a.bookmaker})</span></td>
                    <td className="p-1.5 text-right text-slate-300">{Number(a.odd).toFixed(2)}</td>
                    <td className="p-1.5 text-right text-slate-400">{((Number(a.ev) - 1) * 100).toFixed(1)}%</td>
                    <td className="p-1.5 text-right text-slate-300">{fmtMoney(a.stake)}</td>
                    <td className="p-1.5 text-center"><span className={`text-[9px] font-bold px-1.5 py-0.5 rounded ${COR_STATUS[a.status]}`}>{ROTULO_STATUS[a.status]}</span></td>
                    <td className={`p-1.5 text-right font-semibold ${a.resultado_liquido == null ? 'text-slate-500' : a.resultado_liquido >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                      {a.resultado_liquido == null ? '—' : fmtMoney(a.resultado_liquido)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}

export default function CarteiraPaperTrading() {
  const { session } = useAuth();
  const authHeader = session?.access_token ? { Authorization: `Bearer ${session.access_token}` } : {};

  const [carteiras, setCarteiras] = useState([]);
  const [ligasDomesticas, setLigasDomesticas] = useState([]);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState('');
  const [modelosDisponiveis, setModelosDisponiveis] = useState([]);
  const [formAberto, setFormAberto] = useState(false);
  const [form, setForm] = useState(ESTADO_FORM_INICIAL);
  const [salvando, setSalvando] = useState(false);
  const [erroForm, setErroForm] = useState('');

  const carregar = useCallback(async () => {
    try {
      const resp = await fetch(apiUrl('/api/model-maintenance?tarefa=paper-carteira-listar'));
      const dados = await resp.json();
      if (!resp.ok) throw new Error(dados.error?.message || 'Erro ao carregar carteiras.');
      setCarteiras(dados.carteiras || []);
      setLigasDomesticas(dados.ligas_domesticas || []);
    } catch (e) {
      setErro(e.message);
    } finally {
      setCarregando(false);
    }
  }, []);

  useEffect(() => { carregar(); }, [carregar]);

  useEffect(() => {
    fetch(apiUrl(`/api/model-maintenance?tarefa=modelos-disponiveis&mercado=${encodeURIComponent(form.mercado)}`))
      .then((r) => r.json())
      .then((d) => setModelosDisponiveis(d.modelos || []))
      .catch(() => setModelosDisponiveis([]));
  }, [form.mercado]);

  async function criarCarteira() {
    if (!session) { setErroForm('Faça login para criar uma carteira.'); return; }
    if (!form.nome.trim() || !form.modelo) { setErroForm('Nome e modelo são obrigatórios.'); return; }
    setSalvando(true);
    setErroForm('');
    try {
      const resp = await fetch(apiUrl('/api/model-maintenance?tarefa=paper-carteira-criar'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeader },
        body: JSON.stringify({
          ...form,
          liga_id: form.liga_id || null,
          stake_fixa: Number(form.stake_fixa),
          kelly_multiplier: Number(form.kelly_multiplier),
          teto_exposicao_pct: Number(form.teto_exposicao_pct),
          ev_minimo: Number(form.ev_minimo),
          ev_maximo: Number(form.ev_maximo),
          stake_minima_pct: Number(form.stake_minima_pct),
          banca_inicial: Number(form.banca_inicial),
        }),
      });
      const dados = await resp.json();
      if (!resp.ok) throw new Error(dados.error?.message || `HTTP ${resp.status}`);
      setForm(ESTADO_FORM_INICIAL);
      setFormAberto(false);
      await carregar();
    } catch (e) {
      setErroForm(e.message);
    } finally {
      setSalvando(false);
    }
  }

  return (
    <div className="max-w-6xl mx-auto space-y-4">
      <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6">
        <h1 className="text-xl font-bold flex items-center gap-2 text-slate-100">
          <Landmark className="text-emerald-400" size={22} /> Carteira — Paper Trading
        </h1>
        <p className="text-slate-400 mt-1 text-sm">
          Apostas simuladas com dinheiro fictício sobre partidas <strong className="text-slate-300">ainda não disputadas</strong>, usando
          odds ao vivo reais já sincronizadas e as predições que já existem pra elas. A banca é <strong className="text-slate-300">persistente</strong>:
          "Apostar" varre partidas agendadas e reserva stake das que passam o filtro de EV; "Resolver" credita/zera a banca conforme os jogos terminam.
          Restrito às ligas domésticas com odds ao vivo{ligasDomesticas.length > 0 ? `: ${ligasDomesticas.map((l) => l.name).join(', ')}` : ''}.
        </p>
        <button
          onClick={() => setFormAberto((a) => !a)}
          className="flex items-center gap-2 mt-4 bg-emerald-600 hover:bg-emerald-500 text-white font-bold px-4 py-2 rounded-lg text-sm"
        >
          <Plus size={16} /> {formAberto ? 'Cancelar' : 'Nova carteira'}
        </button>

        {formAberto && (
          <div className="mt-4 bg-slate-900 border border-slate-700 rounded-xl p-4 space-y-3">
            <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
              <div>
                <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">Nome</label>
                <input type="text" value={form.nome} onChange={(e) => setForm((f) => ({ ...f, nome: e.target.value }))}
                  placeholder="Ex: 1X2 Brasileirão Kelly 25%" className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100" />
              </div>
              <div>
                <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">Mercado</label>
                <select value={form.mercado} onChange={(e) => setForm((f) => ({ ...f, mercado: e.target.value, modelo: '' }))}
                  className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100">
                  {MERCADOS.map((m) => <option key={m.chave} value={m.chave}>{m.rotulo}</option>)}
                </select>
              </div>
              <div>
                <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">Modelo</label>
                <select value={form.modelo} onChange={(e) => setForm((f) => ({ ...f, modelo: e.target.value }))}
                  className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100">
                  <option value="">Selecione...</option>
                  {modelosDisponiveis.map((m) => <option key={m.model_name} value={m.model_name}>{m.model_name}</option>)}
                </select>
              </div>
              <div>
                <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">Liga</label>
                <select value={form.liga_id} onChange={(e) => setForm((f) => ({ ...f, liga_id: e.target.value }))}
                  className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100">
                  <option value="">Todas as domésticas</option>
                  {ligasDomesticas.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
                </select>
              </div>
              <div>
                <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">Correção</label>
                <select value={form.usar_calibracao} onChange={(e) => setForm((f) => ({ ...f, usar_calibracao: e.target.value }))}
                  className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100">
                  <option value="nenhuma">Crua (sem ajuste)</option>
                  <option value="platt">Platt Scaling</option>
                  <option value="isotonic">Isotonic Regression</option>
                </select>
              </div>
              <div>
                <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">Banca inicial (R$)</label>
                <input type="number" step="100" min="1" value={form.banca_inicial} onChange={(e) => setForm((f) => ({ ...f, banca_inicial: e.target.value }))}
                  className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100" />
              </div>
              <div>
                <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">Tipo Stake</label>
                <select value={form.tipo_stake} onChange={(e) => setForm((f) => ({ ...f, tipo_stake: e.target.value }))}
                  className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100">
                  <option value="kelly">Kelly</option>
                  <option value="fixa">Fixa</option>
                </select>
              </div>
              {form.tipo_stake === 'fixa' ? (
                <div>
                  <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">Stake (R$)</label>
                  <input type="number" step="5" min="1" value={form.stake_fixa} onChange={(e) => setForm((f) => ({ ...f, stake_fixa: e.target.value }))}
                    className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100" />
                </div>
              ) : (
                <div>
                  <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">% de Kelly</label>
                  <select value={form.kelly_multiplier} onChange={(e) => setForm((f) => ({ ...f, kelly_multiplier: e.target.value }))}
                    className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100">
                    <option value="0.10">10%</option>
                    <option value="0.25">25% (Padrão)</option>
                    <option value="0.50">50%</option>
                    <option value="0.75">75%</option>
                    <option value="1.00">100%</option>
                  </select>
                </div>
              )}
              <div>
                <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">Teto Exp. %</label>
                <input type="number" step="0.05" min="0.05" max="1.00" value={form.teto_exposicao_pct} onChange={(e) => setForm((f) => ({ ...f, teto_exposicao_pct: e.target.value }))}
                  className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100" title="Teto de exposição da banca por lote de apostas" />
              </div>
              <div>
                <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">EV Min</label>
                <input type="number" step="0.01" min="1.00" value={form.ev_minimo} onChange={(e) => setForm((f) => ({ ...f, ev_minimo: e.target.value }))}
                  className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100" title="Ex: 1.02 para 2% de EV mínimo" />
              </div>
              <div>
                <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">EV Max</label>
                <input type="number" step="0.01" min="1.00" value={form.ev_maximo} onChange={(e) => setForm((f) => ({ ...f, ev_maximo: e.target.value }))}
                  className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100" />
              </div>
            </div>
            {erroForm && <div className="bg-red-950/30 border border-red-600/40 text-red-300 text-xs px-3 py-2 rounded-lg">{erroForm}</div>}
            <button onClick={criarCarteira} disabled={salvando}
              className="flex items-center gap-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white font-bold px-4 py-2 rounded-lg text-sm">
              {salvando ? <Loader2 size={16} className="animate-spin" /> : <Plus size={16} />} Criar carteira
            </button>
          </div>
        )}
      </div>

      {erro && (
        <div className="bg-red-950/30 border border-red-600/40 text-red-300 text-sm px-4 py-3 rounded-xl flex items-center gap-2">
          <AlertTriangle size={16} /> {erro}
        </div>
      )}

      {carregando ? (
        <div className="flex items-center justify-center py-16 text-slate-500 gap-2">
          <Loader2 className="animate-spin" size={20} /> Carregando carteiras...
        </div>
      ) : carteiras.length === 0 ? (
        <div className="text-center py-16 text-slate-500 text-sm">Nenhuma carteira ainda. Crie a primeira acima.</div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {carteiras.map((c) => (
            <CardCarteira key={c.id} carteira={c} sessao={session} authHeader={authHeader} onMudou={carregar} />
          ))}
        </div>
      )}
    </div>
  );
}
