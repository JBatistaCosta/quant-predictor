// src/components/TimelineDatas.jsx
// Barra horizontal de datas: data selecionada centralizada, com botões de
// avançar/recuar (±1 dia) e clique direto em qualquer uma das datas visíveis
// (janela de 7 dias em volta da selecionada). Reutilizável — usada em
// EventosLista.jsx (linha do tempo de todos os jogos).
import React from 'react';
import { ChevronLeft, ChevronRight, CalendarDays } from 'lucide-react';

const DIAS_SEMANA = ['Dom', 'Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb'];
const MESES_ABREV = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez'];

function mesmodia(a, b) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

function somarDias(data, n) {
  const d = new Date(data);
  d.setDate(d.getDate() + n);
  return d;
}

export default function TimelineDatas({ dataSelecionada, onSelecionar }) {
  const hoje = new Date();
  const janela = [-3, -2, -1, 0, 1, 2, 3].map(offset => somarDias(dataSelecionada, offset));

  return (
    <div className="bg-slate-800 border border-slate-700 rounded-2xl p-3 mb-4">
      <div className="flex items-center gap-1">
        <button
          onClick={() => onSelecionar(somarDias(dataSelecionada, -1))}
          className="p-2 rounded-lg text-slate-400 hover:text-slate-200 hover:bg-slate-700/50 shrink-0"
          title="Dia anterior"
        >
          <ChevronLeft size={20} />
        </button>

        <div className="flex-1 grid grid-cols-7 gap-1">
          {janela.map((d, i) => {
            const selecionado = mesmodia(d, dataSelecionada);
            const ehHoje = mesmodia(d, hoje);
            return (
              <button
                key={i}
                onClick={() => onSelecionar(d)}
                className={`flex flex-col items-center py-2 rounded-lg transition-colors ${
                  selecionado ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/40 font-bold' : 'text-slate-400 hover:bg-slate-700/50 border border-transparent'
                }`}
              >
                <span className="text-[10px] uppercase">{DIAS_SEMANA[d.getDay()]}</span>
                <span className="text-sm">{d.getDate()} {MESES_ABREV[d.getMonth()]}</span>
                {ehHoje && !selecionado && <span className="w-1 h-1 rounded-full bg-emerald-400 mt-0.5" />}
              </button>
            );
          })}
        </div>

        <button
          onClick={() => onSelecionar(somarDias(dataSelecionada, 1))}
          className="p-2 rounded-lg text-slate-400 hover:text-slate-200 hover:bg-slate-700/50 shrink-0"
          title="Próximo dia"
        >
          <ChevronRight size={20} />
        </button>
      </div>

      <div className="flex items-center justify-center gap-3 mt-2 pt-2 border-t border-slate-700/50">
        {!mesmodia(dataSelecionada, hoje) && (
          <button onClick={() => onSelecionar(hoje)} className="text-xs font-bold text-emerald-400 hover:underline">
            Voltar pra hoje
          </button>
        )}
        <label className="flex items-center gap-1.5 text-xs text-slate-500 cursor-pointer">
          <CalendarDays size={14} />
          <input
            type="date"
            value={dataSelecionada.toISOString().slice(0, 10)}
            onChange={(e) => { if (e.target.value) onSelecionar(new Date(e.target.value + 'T12:00:00')); }}
            className="bg-transparent outline-none text-slate-400"
          />
        </label>
      </div>
    </div>
  );
}
