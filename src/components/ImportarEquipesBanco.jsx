import React, { useState, useEffect } from 'react';
import { Search, Database, Loader2, X } from 'lucide-react';
import { buscarEquipes } from '../utils/equipeBanco';

// Caixa de busca de UMA equipe do banco (autocomplete por nome, com debounce).
function BuscaEquipe({ supabase, rotulo, cor, selecionada, onSelecionar }) {
  const [termo, setTermo] = useState('');
  const [opcoes, setOpcoes] = useState([]);
  const [buscando, setBuscando] = useState(false);

  useEffect(() => {
    if (selecionada || termo.trim().length < 2) { setOpcoes([]); return undefined; }
    let cancelado = false;
    const t = setTimeout(async () => {
      setBuscando(true);
      try {
        const r = await buscarEquipes(supabase, termo);
        if (!cancelado) setOpcoes(r);
      } catch {
        if (!cancelado) setOpcoes([]);
      } finally {
        if (!cancelado) setBuscando(false);
      }
    }, 300);
    return () => { cancelado = true; clearTimeout(t); };
  }, [termo, selecionada, supabase]);

  return (
    <div className="relative">
      <label className={`block text-[10px] uppercase font-bold mb-1 ${cor}`}>{rotulo}</label>
      {selecionada ? (
        <div className="flex items-center justify-between bg-slate-800 border border-slate-600 rounded-lg p-2.5 text-sm text-slate-100">
          <span className="truncate font-semibold">{selecionada.name} <span className="text-slate-500 font-normal">{selecionada.country || ''}</span></span>
          <button type="button" onClick={() => { onSelecionar(null); setTermo(''); }} className="text-slate-400 hover:text-red-400 ml-2" title="Trocar equipe"><X size={14} /></button>
        </div>
      ) : (
        <>
          <div className="relative">
            <Search size={14} className="absolute left-2.5 top-3 text-slate-500" />
            <input
              type="text" value={termo} onChange={(e) => setTermo(e.target.value)}
              placeholder="Digite o nome da equipe…"
              className="w-full bg-slate-800 border border-slate-600 rounded-lg p-2.5 pl-8 text-sm text-slate-100 outline-none"
            />
            {buscando && <Loader2 size={14} className="absolute right-2.5 top-3 animate-spin text-slate-500" />}
          </div>
          {opcoes.length > 0 && (
            <ul className="absolute z-20 mt-1 w-full max-h-56 overflow-auto bg-slate-800 border border-slate-600 rounded-lg shadow-xl">
              {opcoes.map((o) => (
                <li key={o.id}>
                  <button type="button" onClick={() => { onSelecionar(o); setOpcoes([]); }} className="w-full text-left px-3 py-2 text-sm text-slate-100 hover:bg-slate-700">
                    {o.name} <span className="text-slate-500 text-xs">{o.country || ''}{o.is_national_team ? ' · seleção' : ''}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {!buscando && termo.trim().length >= 2 && opcoes.length === 0 && (
            <p className="text-[11px] text-slate-500 mt-1">Nenhuma equipe encontrada.</p>
          )}
        </>
      )}
    </div>
  );
}

export default function ImportarEquipesBanco({ supabase, hoje, carregando, mensagem, onImportar }) {
  const [eq1, setEq1] = useState(null);
  const [eq2, setEq2] = useState(null);
  const [data, setData] = useState(hoje);

  const podeImportar = eq1 && eq2 && eq1.id !== eq2.id && data && !carregando;

  return (
    <div className="bg-slate-900 border border-blue-500/30 rounded-xl p-5 space-y-4">
      <div className="flex items-center gap-2 text-blue-400 font-bold text-sm uppercase tracking-wide">
        <Database size={16} /> Importar do banco (qualquer equipe)
      </div>
      <p className="text-xs text-slate-400">
        Busque duas equipes e uma data. Elo e médias (xG, xGA, chutes, escanteios…) vêm dos últimos 10 jogos terminados
        <strong> antes </strong>da data — sem usar informação do futuro. Equipe 1 = mandante.
      </p>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <BuscaEquipe supabase={supabase} rotulo="Equipe 1 (mandante)" cor="text-emerald-400" selecionada={eq1} onSelecionar={setEq1} />
        <BuscaEquipe supabase={supabase} rotulo="Equipe 2 (visitante)" cor="text-orange-400" selecionada={eq2} onSelecionar={setEq2} />
        <div>
          <label className="block text-[10px] uppercase font-bold mb-1 text-slate-400">Data do jogo</label>
          <input type="date" value={data} onChange={(e) => setData(e.target.value)} className="w-full bg-slate-800 border border-slate-600 rounded-lg p-2.5 text-sm text-slate-100" />
        </div>
      </div>
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <span className="text-xs text-slate-300">{mensagem}</span>
        <button
          type="button" disabled={!podeImportar} onClick={() => onImportar(eq1, eq2, data)}
          className="bg-blue-600 hover:bg-blue-500 disabled:opacity-40 disabled:cursor-not-allowed text-white px-5 py-2 rounded-lg font-bold text-sm flex items-center gap-2"
        >
          {carregando && <Loader2 size={14} className="animate-spin" />} Importar dados
        </button>
      </div>
    </div>
  );
}
