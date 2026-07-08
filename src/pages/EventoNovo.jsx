// src/pages/EventoNovo.jsx — ESQUELETO: cadastro de novo evento (a preencher)
import React from 'react';
import { PlusCircle, Construction } from 'lucide-react';

export default function EventoNovo() {
  return (
    <div className="max-w-3xl mx-auto">
      <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 mb-4">
        <h1 className="text-2xl font-extrabold flex items-center gap-3 text-slate-100">
          <PlusCircle className="text-emerald-400" size={28} /> Novo Evento
        </h1>
        <p className="text-slate-400 mt-1 text-sm">Cadastrar um novo jogo pra acompanhar.</p>
      </div>

      <div className="bg-slate-800 border border-dashed border-slate-600 rounded-2xl p-10 text-center">
        <Construction className="text-slate-500 mx-auto mb-3" size={32} />
        <p className="text-slate-400 font-semibold">Página em construção</p>
        <p className="text-slate-500 text-sm mt-1">Por enquanto, use "Análise de Evento" — o botão "Salvar Evento Completo" será adicionado lá.</p>
      </div>
    </div>
  );
}
