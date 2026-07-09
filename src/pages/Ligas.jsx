// src/pages/Ligas.jsx — Cadastro de ligas/competições
import React, { useState, useEffect } from 'react';
import { Trophy, Plus, X, Loader2, AlertTriangle, Search } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';

const TIPOS_LIGA = [
  { valor: 'liga_domestica', rotulo: 'Liga Doméstica' },
  { valor: 'copa_nacional', rotulo: 'Copa Nacional' },
  { valor: 'torneio_internacional', rotulo: 'Torneio Internacional (seleções)' },
  { valor: 'copa_continental', rotulo: 'Copa Continental (clubes)' },
];

const FORM_VAZIO = { nome: '', nome_en: '', tipo: 'liga_domestica', pais: '', confederacao: '', simbolo_url: '' };

export default function Ligas() {
  const [ligas, setLigas] = useState([]);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState('');
  const [mostrarForm, setMostrarForm] = useState(false);
  const [form, setForm] = useState(FORM_VAZIO);
  const [salvando, setSalvando] = useState(false);
  const [buscaLiga, setBuscaLiga] = useState('');
  const [buscandoLiga, setBuscandoLiga] = useState(false);
  const [resultadosBusca, setResultadosBusca] = useState(null);
  const [erroBusca, setErroBusca] = useState('');

  const buscarNaApiFootball = async () => {
    if (!buscaLiga.trim()) return;
    setBuscandoLiga(true);
    setErroBusca('');
    setResultadosBusca(null);
    try {
      const resp = await fetch(`/api/leagues-search?nome=${encodeURIComponent(buscaLiga)}`);
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error?.message || 'Erro desconhecido.');
      setResultadosBusca(data.resultados);
    } catch (e) {
      setErroBusca(e.message);
    } finally {
      setBuscandoLiga(false);
    }
  };

  const usarResultado = (r) => {
    setForm({
      nome: r.nome, nome_en: r.nome, tipo: r.tipo,
      pais: r.pais || '', confederacao: r.confederacao || '', simbolo_url: r.simbolo_url || '',
    });
    setResultadosBusca(null);
    setBuscaLiga('');
  };

  const buscarLigas = async () => {
    setCarregando(true);
    const { data, error } = await supabase.from('ligas').select('*').order('nome');
    if (error) setErro(error.message);
    else setLigas(data || []);
    setCarregando(false);
  };

  useEffect(() => { if (supabaseAtivo) buscarLigas(); }, []);

  const salvar = async (e) => {
    e.preventDefault();
    setSalvando(true);
    setErro('');
    const { error } = await supabase.from('ligas').insert({
      nome: form.nome,
      nome_en: form.nome_en || null,
      tipo: form.tipo,
      pais: form.pais || null,
      confederacao: form.confederacao || null,
      simbolo_url: form.simbolo_url || null,
    });
    setSalvando(false);
    if (error) { setErro(error.message); return; }
    setForm(FORM_VAZIO);
    setMostrarForm(false);
    buscarLigas();
  };

  const apagar = async (id) => {
    if (!window.confirm('Apagar essa liga? Isso não pode ser desfeito.')) return;
    const { error } = await supabase.from('ligas').delete().eq('id', id);
    if (error) { setErro(error.message); return; }
    buscarLigas();
  };

  if (!supabaseAtivo) {
    return (
      <div className="max-w-5xl mx-auto bg-slate-800 border border-red-500/30 rounded-2xl p-6 text-center">
        <AlertTriangle className="text-red-400 mx-auto mb-2" size={28} />
        <p className="text-slate-300">Supabase não configurado.</p>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto">
      <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 mb-4 flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-extrabold flex items-center gap-3 text-slate-100">
            <Trophy className="text-emerald-400" size={28} /> Ligas
          </h1>
          <p className="text-slate-400 mt-1 text-sm">Ligas e competições (Copa do Mundo, Ligue 1, Serie A...).</p>
        </div>
        <button
          onClick={() => setMostrarForm(!mostrarForm)}
          className="flex items-center gap-2 bg-emerald-600 hover:bg-emerald-500 text-white font-bold px-4 py-2.5 rounded-lg transition-colors"
        >
          {mostrarForm ? <X size={18} /> : <Plus size={18} />} {mostrarForm ? 'Cancelar' : 'Nova Liga'}
        </button>
      </div>

      {erro && (
        <div className="bg-red-950/30 border border-red-600/40 text-red-300 text-sm px-4 py-3 rounded-xl mb-4">{erro}</div>
      )}

      {mostrarForm && (
        <form onSubmit={salvar} className="bg-slate-800 border border-emerald-500/30 rounded-2xl p-6 mb-4 space-y-4">
          <div className="bg-slate-900 border border-slate-700 rounded-xl p-4">
            <label className="block text-xs font-bold text-slate-400 uppercase mb-2">Buscar liga (preenche os campos abaixo automaticamente)</label>
            <div className="flex gap-2">
              <div className="relative flex-1">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" size={15} />
                <input
                  value={buscaLiga}
                  onChange={(e) => setBuscaLiga(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), buscarNaApiFootball())}
                  placeholder="Ex: Premier League, World Cup..."
                  className="w-full bg-slate-800 border border-slate-600 rounded-lg pl-9 pr-3 py-2 text-sm text-slate-100"
                />
              </div>
              <button type="button" onClick={buscarNaApiFootball} disabled={buscandoLiga || !buscaLiga.trim()}
                className="bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white font-bold px-4 py-2 rounded-lg text-sm flex items-center gap-2">
                {buscandoLiga && <Loader2 size={14} className="animate-spin" />} Buscar
              </button>
            </div>
            {erroBusca && <p className="text-red-400 text-xs mt-2">{erroBusca}</p>}
            {resultadosBusca && (
              <div className="mt-2 space-y-1 max-h-56 overflow-y-auto">
                {resultadosBusca.length === 0 ? (
                  <p className="text-slate-500 text-xs py-2">Nenhuma liga encontrada.</p>
                ) : resultadosBusca.map((r, i) => (
                  <button
                    key={i} type="button" onClick={() => usarResultado(r)}
                    className="w-full text-left flex items-center gap-2 px-3 py-2 bg-slate-800 hover:bg-slate-700 rounded-lg text-sm"
                  >
                    {r.simbolo_url && <img src={r.simbolo_url} alt="" className="w-5 h-5 object-contain" />}
                    <span className="text-slate-200 font-semibold">{r.nome}</span>
                    <span className="text-slate-500 text-xs">— {TIPOS_LIGA.find(t => t.valor === r.tipo)?.rotulo}</span>
                    <span className="text-slate-600 text-xs ml-auto">{r.pais || r.confederacao}</span>
                  </button>
                ))}
              </div>
            )}
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-bold text-slate-400 uppercase mb-1">Nome</label>
              <input required value={form.nome} onChange={(e) => setForm({ ...form, nome: e.target.value })}
                className="w-full bg-slate-900 border border-slate-600 rounded-lg p-2.5 text-sm text-slate-100" />
            </div>
            <div>
              <label className="block text-xs font-bold text-slate-400 uppercase mb-1">Nome em inglês (opcional)</label>
              <input value={form.nome_en} onChange={(e) => setForm({ ...form, nome_en: e.target.value })}
                className="w-full bg-slate-900 border border-slate-600 rounded-lg p-2.5 text-sm text-slate-100" />
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div>
              <label className="block text-xs font-bold text-slate-400 uppercase mb-1">Tipo</label>
              <select value={form.tipo} onChange={(e) => setForm({ ...form, tipo: e.target.value })}
                className="w-full bg-slate-900 border border-slate-600 rounded-lg p-2.5 text-sm text-slate-100">
                {TIPOS_LIGA.map(t => <option key={t.valor} value={t.valor}>{t.rotulo}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs font-bold text-slate-400 uppercase mb-1">País (se doméstica)</label>
              <input value={form.pais} onChange={(e) => setForm({ ...form, pais: e.target.value })}
                className="w-full bg-slate-900 border border-slate-600 rounded-lg p-2.5 text-sm text-slate-100" />
            </div>
            <div>
              <label className="block text-xs font-bold text-slate-400 uppercase mb-1">Confederação (se internacional)</label>
              <input value={form.confederacao} onChange={(e) => setForm({ ...form, confederacao: e.target.value })}
                className="w-full bg-slate-900 border border-slate-600 rounded-lg p-2.5 text-sm text-slate-100" />
            </div>
          </div>

          <div>
            <label className="block text-xs font-bold text-slate-400 uppercase mb-1">URL do símbolo (opcional)</label>
            <input value={form.simbolo_url} onChange={(e) => setForm({ ...form, simbolo_url: e.target.value })}
              className="w-full bg-slate-900 border border-slate-600 rounded-lg p-2.5 text-sm text-slate-100" />
          </div>

          <button type="submit" disabled={salvando}
            className="bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white font-bold px-6 py-2.5 rounded-lg flex items-center gap-2">
            {salvando && <Loader2 size={16} className="animate-spin" />} Salvar Liga
          </button>
        </form>
      )}

      <div className="bg-slate-800 border border-slate-700 rounded-2xl overflow-hidden">
        {carregando ? (
          <p className="text-slate-500 text-center py-10 text-sm">Carregando...</p>
        ) : ligas.length === 0 ? (
          <p className="text-slate-500 text-center py-10 text-sm">Nenhuma liga cadastrada ainda.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="bg-slate-900 text-slate-400 text-[10px] uppercase tracking-wider">
                  <th className="p-3">Nome</th>
                  <th className="p-3">Tipo</th>
                  <th className="p-3">País / Confederação</th>
                  <th className="p-3"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-700/50">
                {ligas.map(l => (
                  <tr key={l.id} className="hover:bg-slate-700/20">
                    <td className="p-3 font-semibold text-slate-200">{l.nome}</td>
                    <td className="p-3 text-slate-400">{TIPOS_LIGA.find(t => t.valor === l.tipo)?.rotulo || l.tipo}</td>
                    <td className="p-3 text-slate-400">{l.pais || l.confederacao || '—'}</td>
                    <td className="p-3 text-right">
                      <button onClick={() => apagar(l.id)} className="text-slate-500 hover:text-red-400 text-xs font-bold">Apagar</button>
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
