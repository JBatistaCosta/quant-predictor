// src/pages/Ligas.jsx — ESQUELETO: cadastro de ligas/competições (a preencher)
import React from 'react';
import { Trophy, Construction } from 'lucide-react';

export default function Ligas() {
  return (
    <div className="max-w-5xl mx-auto">
      <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 mb-4">
        <h1 className="text-2xl font-extrabold flex items-center gap-3 text-slate-100">
          <Trophy className="text-emerald-400" size={28} /> Ligas
        </h1>
        <p className="text-slate-400 mt-1 text-sm">Cadastro de ligas e competições (Copa do Mundo, Ligue 1, Serie A...).</p>
      </div>

      <div className="bg-slate-800 border border-dashed border-slate-600 rounded-2xl p-10 text-center">
        <Construction className="text-slate-500 mx-auto mb-3" size={32} />
        <p className="text-slate-400 font-semibold">Página em construção</p>
        <p className="text-slate-500 text-sm mt-1">Em breve: organizar eventos por liga/competição.</p>
      </div>
    </div>
  );
}
