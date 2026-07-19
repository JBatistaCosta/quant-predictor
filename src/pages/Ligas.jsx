// src/pages/Ligas.jsx — Cadastro de ligas/competições
import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { Trophy, Plus, X, Loader2, AlertTriangle, Search, Download, CheckCircle2 } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';

const TIPOS_LIGA = [
  { valor: 'liga_domestica', rotulo: 'Liga Doméstica' },
  { valor: 'copa_nacional', rotulo: 'Copa Nacional' },
  { valor: 'torneio_internacional', rotulo: 'Torneio Internacional (seleções)' },
  { valor: 'copa_continental', rotulo: 'Copa Continental (clubes)' },
];

const FORM_VAZIO = { nome: '', nome_en: '', tipo: 'liga_domestica', pais: '', confederacao: '', simbolo_url: '' };
const IMPORT_FOTMOB_VAZIO = { tipo: 'liga_domestica', pais: '', confederacao: '', temporada: '' };

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

  // Importação via FotMob (raspagem) — fluxo separado do cadastro manual
  // acima: cria a liga E já traz os jogos de uma temporada, tudo numa
  // chamada ao backend (?tarefa=backfill-fotmob-liga).
  const [mostrarImportFotmob, setMostrarImportFotmob] = useState(false);
  const [buscaFotmob, setBuscaFotmob] = useState('');
  const [buscandoFotmob, setBuscandoFotmob] = useState(false);
  const [resultadosFotmob, setResultadosFotmob] = useState(null);
  const [erroBuscaFotmob, setErroBuscaFotmob] = useState('');
  const [ligaFotmobSelecionada, setLigaFotmobSelecionada] = useState(null);
  const [formImportFotmob, setFormImportFotmob] = useState(IMPORT_FOTMOB_VAZIO);
  const [importando, setImportando] = useState(false);
  const [erroImportacao, setErroImportacao] = useState('');
  const [resultadoImportacao, setResultadoImportacao] = useState(null);

  const buscarNoFotmob = async () => {
    if (!buscaFotmob.trim()) return;
    setBuscandoFotmob(true);
    setErroBuscaFotmob('');
    setResultadosFotmob(null);
    try {
      const resp = await fetch(`/api/model-maintenance?tarefa=fotmob-liga-buscar&termo=${encodeURIComponent(buscaFotmob)}`);
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error?.message || 'Erro desconhecido.');
      setResultadosFotmob(data.resultados);
    } catch (e) {
      setErroBuscaFotmob(e.message);
    } finally {
      setBuscandoFotmob(false);
    }
  };

  const selecionarLigaFotmob = (r) => {
    setLigaFotmobSelecionada(r);
    setFormImportFotmob(IMPORT_FOTMOB_VAZIO);
    setResultadosFotmob(null);
    setBuscaFotmob('');
    setResultadoImportacao(null);
    setErroImportacao('');
  };

  const importarDoFotmob = async (e) => {
    e.preventDefault();
    if (!formImportFotmob.temporada.trim()) return;
    setImportando(true);
    setErroImportacao('');
    setResultadoImportacao(null);
    try {
      const params = new URLSearchParams({
        tarefa: 'backfill-fotmob-liga',
        fotmob_league_id: ligaFotmobSelecionada.fotmob_league_id,
        temporada: formImportFotmob.temporada.trim(),
        nome: ligaFotmobSelecionada.nome,
        tipo: formImportFotmob.tipo,
        pais: formImportFotmob.pais,
        confederacao: formImportFotmob.confederacao,
        simbolo_url: ligaFotmobSelecionada.simbolo_url,
      });
      const resp = await fetch(`/api/model-maintenance?${params}`);
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error?.message || 'Erro desconhecido.');
      setResultadoImportacao(data);
      buscarLigas();
    } catch (e) {
      setErroImportacao(e.message);
    } finally {
      setImportando(false);
    }
  };

  const fecharImportFotmob = () => {
    setMostrarImportFotmob(false);
    setBuscaFotmob('');
    setResultadosFotmob(null);
    setErroBuscaFotmob('');
    setLigaFotmobSelecionada(null);
    setFormImportFotmob(IMPORT_FOTMOB_VAZIO);
    setErroImportacao('');
    setResultadoImportacao(null);
  };

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
        <div className="flex gap-2">
          <button
            onClick={() => { setMostrarImportFotmob(false); setMostrarForm(!mostrarForm); }}
            className="flex items-center gap-2 bg-emerald-600 hover:bg-emerald-500 text-white font-bold px-4 py-2.5 rounded-lg transition-colors"
          >
            {mostrarForm ? <X size={18} /> : <Plus size={18} />} {mostrarForm ? 'Cancelar' : 'Nova Liga'}
          </button>
          <button
            onClick={() => { setMostrarForm(false); if (mostrarImportFotmob) fecharImportFotmob(); else setMostrarImportFotmob(true); }}
            className="flex items-center gap-2 bg-blue-600 hover:bg-blue-500 text-white font-bold px-4 py-2.5 rounded-lg transition-colors"
            title="Cria a liga e já importa os jogos de uma temporada, direto do FotMob"
          >
            {mostrarImportFotmob ? <X size={18} /> : <Download size={18} />} {mostrarImportFotmob ? 'Cancelar' : 'Importar do FotMob'}
          </button>
        </div>
      </div>

      {erro && (
        <div className="bg-red-950/30 border border-red-600/40 text-red-300 text-sm px-4 py-3 rounded-xl mb-4">{erro}</div>
      )}

      {mostrarImportFotmob && (
        <div className="bg-slate-800 border border-blue-500/30 rounded-2xl p-6 mb-4 space-y-4">
          <p className="text-slate-400 text-sm">
            Busca a liga no FotMob, cria o cadastro e já importa os confrontos de uma temporada (raspagem — pode levar alguns segundos).
          </p>

          {!ligaFotmobSelecionada ? (
            <div>
              <div className="flex gap-2">
                <div className="relative flex-1">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" size={15} />
                  <input
                    value={buscaFotmob}
                    onChange={(e) => setBuscaFotmob(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), buscarNoFotmob())}
                    placeholder="Ex: Eredivisie, Serie B, MLS..."
                    className="w-full bg-slate-900 border border-slate-600 rounded-lg pl-9 pr-3 py-2 text-sm text-slate-100"
                  />
                </div>
                <button type="button" onClick={buscarNoFotmob} disabled={buscandoFotmob || !buscaFotmob.trim()}
                  className="bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white font-bold px-4 py-2 rounded-lg text-sm flex items-center gap-2">
                  {buscandoFotmob && <Loader2 size={14} className="animate-spin" />} Buscar
                </button>
              </div>
              {erroBuscaFotmob && <p className="text-red-400 text-xs mt-2">{erroBuscaFotmob}</p>}
              {resultadosFotmob && (
                <div className="mt-2 space-y-1 max-h-56 overflow-y-auto">
                  {resultadosFotmob.length === 0 ? (
                    <p className="text-slate-500 text-xs py-2">Nenhuma liga encontrada.</p>
                  ) : resultadosFotmob.map((r, i) => (
                    <button
                      key={i} type="button" onClick={() => selecionarLigaFotmob(r)}
                      className="w-full text-left flex items-center gap-2 px-3 py-2 bg-slate-900 hover:bg-slate-700 rounded-lg text-sm"
                    >
                      <img src={r.simbolo_url} alt="" className="w-5 h-5 object-contain" onError={(e) => { e.currentTarget.style.visibility = 'hidden'; }} />
                      <span className="text-slate-200 font-semibold">{r.nome}</span>
                      <span className="text-slate-600 text-xs ml-auto">{r.pais_code || '—'} · id {r.fotmob_league_id}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <form onSubmit={importarDoFotmob} className="space-y-4">
              <div className="flex items-center justify-between bg-slate-900 border border-slate-700 rounded-xl p-3">
                <div className="flex items-center gap-2">
                  <img src={ligaFotmobSelecionada.simbolo_url} alt="" className="w-6 h-6 object-contain" onError={(e) => { e.currentTarget.style.visibility = 'hidden'; }} />
                  <span className="text-slate-100 font-semibold">{ligaFotmobSelecionada.nome}</span>
                  <span className="text-slate-500 text-xs">fotmob id {ligaFotmobSelecionada.fotmob_league_id}</span>
                </div>
                <button type="button" onClick={() => setLigaFotmobSelecionada(null)} className="text-slate-500 hover:text-slate-300 text-xs font-bold">Trocar</button>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-bold text-slate-400 uppercase mb-1">Tipo</label>
                  <select value={formImportFotmob.tipo} onChange={(e) => setFormImportFotmob({ ...formImportFotmob, tipo: e.target.value })}
                    className="w-full bg-slate-900 border border-slate-600 rounded-lg p-2.5 text-sm text-slate-100">
                    {TIPOS_LIGA.map(t => <option key={t.valor} value={t.valor}>{t.rotulo}</option>)}
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-bold text-slate-400 uppercase mb-1">
                    Temporada <span className="text-slate-600 normal-case font-normal">(formato do FotMob, ex: 2024 ou 2024/2025)</span>
                  </label>
                  <input required value={formImportFotmob.temporada} onChange={(e) => setFormImportFotmob({ ...formImportFotmob, temporada: e.target.value })}
                    placeholder="2024/2025"
                    className="w-full bg-slate-900 border border-slate-600 rounded-lg p-2.5 text-sm text-slate-100" />
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-bold text-slate-400 uppercase mb-1">País (se doméstica)</label>
                  <input value={formImportFotmob.pais} onChange={(e) => setFormImportFotmob({ ...formImportFotmob, pais: e.target.value })}
                    placeholder={ligaFotmobSelecionada.pais_code || ''}
                    className="w-full bg-slate-900 border border-slate-600 rounded-lg p-2.5 text-sm text-slate-100" />
                </div>
                <div>
                  <label className="block text-xs font-bold text-slate-400 uppercase mb-1">Confederação (se internacional)</label>
                  <input value={formImportFotmob.confederacao} onChange={(e) => setFormImportFotmob({ ...formImportFotmob, confederacao: e.target.value })}
                    className="w-full bg-slate-900 border border-slate-600 rounded-lg p-2.5 text-sm text-slate-100" />
                </div>
              </div>

              {erroImportacao && <p className="text-red-400 text-xs">{erroImportacao}</p>}

              {resultadoImportacao && (
                <div className="bg-emerald-950/20 border border-emerald-600/30 text-emerald-300 text-sm px-4 py-3 rounded-xl flex items-start gap-2">
                  <CheckCircle2 size={16} className="shrink-0 mt-0.5" />
                  <div>
                    <p>
                      {resultadoImportacao.liga_criada ? 'Liga criada e ' : 'Liga já existia — '}
                      {resultadoImportacao.sincronizados} de {resultadoImportacao.total_jogos} jogos importados pra temporada {resultadoImportacao.temporada}.
                    </p>
                    {resultadoImportacao.aviso && <p className="text-amber-300 mt-1">{resultadoImportacao.aviso}</p>}
                    {resultadoImportacao.liga_id && (
                      <Link to={`/ligas/${resultadoImportacao.liga_id}`} className="underline hover:text-emerald-200">Ver liga →</Link>
                    )}
                  </div>
                </div>
              )}

              <button type="submit" disabled={importando}
                className="bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white font-bold px-6 py-2.5 rounded-lg flex items-center gap-2">
                {importando && <Loader2 size={16} className="animate-spin" />} {importando ? 'Importando...' : 'Importar'}
              </button>
            </form>
          )}
        </div>
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
                    <td className="p-3 font-semibold text-slate-200">
                      <Link to={`/ligas/${l.id}`} className="flex items-center gap-2 hover:text-emerald-400 hover:underline w-fit">
                        {l.simbolo_url && <img src={l.simbolo_url} alt="" className="w-5 h-5 object-contain" />}
                        {l.nome}
                      </Link>
                    </td>
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
