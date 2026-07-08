// src/pages/EventosLista.jsx — ESQUELETO: listagem de eventos salvos (a preencher)
import React from 'react';
import { Calendar, Construction } from 'lucide-react';

export default function EventosLista() {
  return (
    <div className="max-w-5xl mx-auto">
      <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 mb-4">
        <h1 className="text-2xl font-extrabold flex items-center gap-3 text-slate-100">
          <Calendar className="text-emerald-400" size={28} /> Eventos
        </h1>
        <p className="text-slate-400 mt-1 text-sm">Lista de todos os jogos analisados e salvos.</p>
      </div>

      <div className="bg-slate-800 border border-dashed border-slate-600 rounded-2xl p-10 text-center">
        <Construction className="text-slate-500 mx-auto mb-3" size={32} />
        <p className="text-slate-400 font-semibold">Página em construção</p>
        <p className="text-slate-500 text-sm mt-1">Em breve: lista dos eventos salvos, com filtro por pendente/resolvido.</p>
      </div>
    </div>
  );
}
