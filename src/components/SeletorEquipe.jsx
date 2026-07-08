// src/components/SeletorEquipe.jsx
// Campo de busca + seleção de equipe (evita listar centenas de times numa caixa só).
// Compartilhado entre EventoNovo e ImportarJogos.
import React, { useState, useEffect } from 'react';
import { Search, X } from 'lucide-react';
import { supabase } from '../supabaseClient';

export default function SeletorEquipe({ label, selecionado, onSelecionar }) {
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
