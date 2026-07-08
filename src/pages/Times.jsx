// src/pages/Times.jsx — ESQUELETO: cadastro de times (a preencher)
import React from 'react';
import { Users, Construction } from 'lucide-react';

export default function Times() {
  return (
    <div className="max-w-5xl mx-auto">
      <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 mb-4">
        <h1 className="text-2xl font-extrabold flex items-center gap-3 text-slate-100">
          <Users className="text-emerald-400" size={28} /> Times
        </h1>
        <p className="text-slate-400 mt-1 text-sm">Cadastro de times/seleções — hoje ainda fixos no código.</p>
      </div>

      <div className="bg-slate-800 border border-dashed border-slate-600 rounded-2xl p-10 text-center">
        <Construction className="text-slate-500 mx-auto mb-3" size={32} />
        <p className="text-slate-400 font-semibold">Página em construção</p>
        <p className="text-slate-500 text-sm mt-1">Em breve: adicionar/editar times sem precisar mexer no código.</p>
      </div>
    </div>
  );
}
