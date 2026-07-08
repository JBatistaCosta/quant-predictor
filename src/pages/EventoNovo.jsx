// src/pages/EventoNovo.jsx — Cadastro de novo evento (mandante, visitante, data, casa de apostas)
import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { PlusCircle, Loader2, AlertTriangle, Check, Search, X } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';

// Campo de busca + seleção de equipe (evita listar centenas de times numa caixa só)
function SeletorEquipe({ label, selecionado, onSelecionar }) {
  const [busca, setBusca] = useState('');
  const [buscaDebounced, setBuscaDebounced] = useState('');
  const [resultados, setResultados] = useState([]);
  const [buscando, setBuscando] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => setBuscaDebounced(busca), 350);
    return () => clearTimeout(timer);
  }, [busca]);

  useEffect(() => {
    if (!buscaDebounced || buscaDebounced.length < 2) { setResultados([]); return; }
    (async () => {
      setBuscando(true);
      const { data } = await supabase
        .from('vw_equipes_completo')
        .select('id, nome_popular, tipo, categoria')
        .ilike('nome_popular', `%${buscaDebounced}%`)
        .order('nome_popular')
        .limit(8);
      setResultados(data || []);
      setBuscando(false);
    })();
  }, [buscaDebounced]);

  if (selecionado) {
    return (
      <div>
        <label className="block text-xs font-bold text-slate-400 uppercase mb-1">{label}</label>
        <div className="flex items-center justify-between bg-slate-900 border border-emerald-600/40 rounded-lg p-2.5">
          <span className="text-sm font-semibold text-slate-100">{selecionado.nome_popular}</span>
          <button type="button" onClick={() => onSelecionar(null)} className="text-slate-500 hover:text-red-400">
            <X size={16} />
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="relative">
      <label className="block text-xs font-bold text-slate-400 uppercase mb-1">{label}</label>
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" size={15} />
        <input
          value={busca}
          onChange={(e) => setBusca(e.target.value)}
          placeholder="Digite pra buscar..."
          className="w-full bg-slate-900 border border-slate-600 rounded-lg pl-9 pr-3 py-2.5 text-sm text-slate-100"
        />
      </div>
      {buscaDebounced.length >= 2 && (
        <div className="absolute z-10 w-full mt-1 bg-slate-800 border border-slate-600 rounded-lg shadow-xl max-h-56 overflow-y-auto">
          {buscando ? (
            <p className="text-slate-500 text-xs text-center py-3">Buscando...</p>
          ) : resultados.length === 0 ? (
            <p className="text-slate-500 text-xs text-center py-3">Nenhum time encontrado.</p>
          ) : (
            resultados.map(r => (
              <button
                key={r.id} type="button"
                onClick={() => { onSelecionar(r); setBusca(''); setResultados([]); }}
                className="w-full text-left px-3 py-2 text-sm text-slate-200 hover:bg-slate-700"
              >
                {r.nome_popular} <span className="text-slate-500 text-xs capitalize">— {r.tipo}</span>
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}

export default function EventoNovo() {
  const navigate = useNavigate();
  const [mandante, setMandante] = useState(null);
  const [visitante, setVisitante] = useState(null);
  const [dataEvento, setDataEvento] = useState('');
  const [casaDeApostas, setCasaDeApostas] = useState('');
  const [salvando, setSalvando] = useState(false);
  const [erro, setErro] = useState('');
  const [sucesso, setSucesso] = useState(false);

  // Fase da competição (opcional): Liga -> Temporada -> Fase, em cascata
  const [ligas, setLigas] = useState([]);
  const [temporadas, setTemporadas] = useState([]);
  const [fases, setFases] = useState([]);
  const [ligaId, setLigaId] = useState('');
  const [temporadaId, setTemporadaId] = useState('');
  const [faseId, setFaseId] = useState('');

  useEffect(() => {
    if (!supabaseAtivo) return;
    supabase.from('ligas').select('id, nome').order('nome').then(({ data }) => setLigas(data || []));
  }, []);

  useEffect(() => {
    setTemporadaId(''); setFaseId(''); setFases([]);
    if (!ligaId) { setTemporadas([]); return; }
    supabase.from('ligas_temporadas').select('id, nome').eq('liga_id', ligaId).order('nome', { ascending: false })
      .then(({ data }) => setTemporadas(data || []));
  }, [ligaId]);

  useEffect(() => {
    setFaseId('');
    if (!temporadaId) { setFases([]); return; }
    supabase.from('fases').select('id, nome, tipo').eq('liga_temporada_id', temporadaId).order('ordem')
      .then(({ data }) => setFases(data || []));
  }, [temporadaId]);


  const salvar = async (e) => {
    e.preventDefault();
    setErro('');

    if (!mandante || !visitante) { setErro('Selecione os dois times.'); return; }
    if (mandante.id === visitante.id) { setErro('Mandante e visitante não podem ser o mesmo time.'); return; }

    setSalvando(true);
    const { error } = await supabase.from('eventos').insert({
      equipe_mandante_id: mandante.id,
      equipe_visitante_id: visitante.id,
      data_evento: dataEvento || null,
      casa_de_apostas: casaDeApostas || null,
      fase_id: faseId || null,
      resolvido: false,
    });
    setSalvando(false);

    if (error) { setErro(error.message); return; }
    setSucesso(true);
    setTimeout(() => navigate(`/times/${mandante.id}`), 1200);
  };

  if (!supabaseAtivo) {
    return (
      <div className="max-w-3xl mx-auto bg-slate-800 border border-red-500/30 rounded-2xl p-6 text-center">
        <AlertTriangle className="text-red-400 mx-auto mb-2" size={28} />
        <p className="text-slate-300">Supabase não configurado.</p>
      </div>
    );
  }

  return (
    <div className="max-w-3xl mx-auto">
      <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 mb-4">
        <h1 className="text-2xl font-extrabold flex items-center gap-3 text-slate-100">
          <PlusCircle className="text-emerald-400" size={28} /> Novo Evento
        </h1>
        <p className="text-slate-400 mt-1 text-sm">
          Cadastra o confronto — probabilidades e mercados do Scanner ainda são calculados em "Análise de Evento" (integração entre as duas telas vem a seguir).
        </p>
      </div>

      <form onSubmit={salvar} className="bg-slate-800 border border-slate-700 rounded-2xl p-6 space-y-4">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <SeletorEquipe label="Mandante" selecionado={mandante} onSelecionar={setMandante} />
          <SeletorEquipe label="Visitante" selecionado={visitante} onSelecionar={setVisitante} />
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="block text-xs font-bold text-slate-400 uppercase mb-1">Data do evento</label>
            <input type="date" value={dataEvento} onChange={(e) => setDataEvento(e.target.value)}
              className="w-full bg-slate-900 border border-slate-600 rounded-lg p-2.5 text-sm text-slate-100" />
          </div>
          <div>
            <label className="block text-xs font-bold text-slate-400 uppercase mb-1">Casa de apostas (opcional)</label>
            <input value={casaDeApostas} onChange={(e) => setCasaDeApostas(e.target.value)} placeholder="Ex: Betano"
              className="w-full bg-slate-900 border border-slate-600 rounded-lg p-2.5 text-sm text-slate-100" />
          </div>
        </div>

        <div className="pt-3 border-t border-slate-700">
          <span className="block text-xs font-bold text-slate-400 uppercase mb-2">Fase da competição (opcional)</span>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <select value={ligaId} onChange={(e) => setLigaId(e.target.value)}
              className="bg-slate-900 border border-slate-600 rounded-lg p-2.5 text-sm text-slate-100">
              <option value="">Liga...</option>
              {ligas.map(l => <option key={l.id} value={l.id}>{l.nome}</option>)}
            </select>
            <select value={temporadaId} onChange={(e) => setTemporadaId(e.target.value)} disabled={!ligaId}
              className="bg-slate-900 border border-slate-600 rounded-lg p-2.5 text-sm text-slate-100 disabled:opacity-40">
              <option value="">Temporada...</option>
              {temporadas.map(t => <option key={t.id} value={t.id}>{t.nome}</option>)}
            </select>
            <select value={faseId} onChange={(e) => setFaseId(e.target.value)} disabled={!temporadaId}
              className="bg-slate-900 border border-slate-600 rounded-lg p-2.5 text-sm text-slate-100 disabled:opacity-40">
              <option value="">Fase...</option>
              {fases.map(f => <option key={f.id} value={f.id}>{f.nome}</option>)}
            </select>
          </div>
          {ligas.length === 0 && (
            <p className="text-[11px] text-slate-500 mt-2">Nenhuma liga cadastrada ainda — cadastre em "Ligas" pra poder ligar eventos a uma fase.</p>
          )}
        </div>

        {erro && <div className="bg-red-950/30 border border-red-600/40 text-red-300 text-sm px-4 py-2.5 rounded-lg">{erro}</div>}
        {sucesso && (
          <div className="bg-emerald-950/30 border border-emerald-600/40 text-emerald-300 text-sm px-4 py-2.5 rounded-lg flex items-center gap-2">
            <Check size={16} /> Evento salvo! Levando você pra página do time...
          </div>
        )}

        <button type="submit" disabled={salvando}
          className="bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white font-bold px-6 py-2.5 rounded-lg flex items-center gap-2">
          {salvando && <Loader2 size={16} className="animate-spin" />} Salvar Evento
        </button>
      </form>
    </div>
  );
}
