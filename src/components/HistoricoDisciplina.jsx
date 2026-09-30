import React from 'react';
import { History } from 'lucide-react';

const f2 = (v) => (v === null || v === undefined ? '—' : Number(v).toFixed(2));
const f0 = (v) => (v === null || v === undefined ? '—' : String(v));

function Tabela({ nome, cor, dados }) {
  const { jogos, resumo } = dados;
  return (
    <div className="bg-slate-900 border border-slate-700 rounded-lg p-3 overflow-x-auto">
      <div className={`text-xs font-bold uppercase tracking-wide mb-2 ${cor}`}>{nome}</div>
      <table className="w-full text-xs text-slate-300">
        <thead>
          <tr className="text-slate-500 text-[10px] uppercase border-b border-slate-700">
            <th className="text-left py-1 pr-2">Data</th>
            <th className="text-left py-1 pr-2">Adversário</th>
            <th className="text-right py-1 px-1">Faltas</th>
            <th className="text-right py-1 px-1">Amar.</th>
            <th className="text-right py-1 px-1">Verm.</th>
            <th className="text-right py-1 pl-2" title="Peso do jogo na média com time-decay (ξ=0,2): o mais recente pesa mais">Peso</th>
          </tr>
        </thead>
        <tbody>
          {jogos.map((g, i) => (
            <tr key={i} className="border-b border-slate-800">
              <td className="py-1 pr-2 whitespace-nowrap">{g.data}</td>
              <td className="py-1 pr-2 truncate max-w-[140px]" title={g.adversario}>{g.local === 'C' ? '' : '@ '}{g.adversario}</td>
              <td className="py-1 px-1 text-right font-mono">{f0(g.faltas)}</td>
              <td className="py-1 px-1 text-right font-mono text-yellow-400">{f0(g.amarelos)}</td>
              <td className="py-1 px-1 text-right font-mono text-red-400">{f0(g.vermelhos)}</td>
              <td className="py-1 pl-2 text-right font-mono text-slate-500">{g.pesoPct.toFixed(0)}%</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr className="font-semibold text-slate-200 border-t border-slate-600">
            <td className="py-1 pr-2" colSpan={2}>Média simples</td>
            <td className="py-1 px-1 text-right font-mono">{f2(resumo.faltas.simples)}</td>
            <td className="py-1 px-1 text-right font-mono text-yellow-400">{f2(resumo.amarelos.simples)}</td>
            <td className="py-1 px-1 text-right font-mono text-red-400">{f2(resumo.vermelhos.simples)}</td>
            <td />
          </tr>
          <tr className="font-bold text-emerald-300">
            <td className="py-1 pr-2" colSpan={2}>Média time-decay <span className="text-slate-500 font-normal">(usada na simulação)</span></td>
            <td className="py-1 px-1 text-right font-mono">{f2(resumo.faltas.decay)}</td>
            <td className="py-1 px-1 text-right font-mono text-yellow-400">{f2(resumo.amarelos.decay)}</td>
            <td className="py-1 px-1 text-right font-mono text-red-400">{f2(resumo.vermelhos.decay)}</td>
            <td />
          </tr>
          <tr className="text-[10px] text-slate-500">
            <td className="pt-1 pr-2" colSpan={2}>Jogos com dado</td>
            <td className="pt-1 px-1 text-right">{resumo.faltas.n}/{jogos.length}</td>
            <td className="pt-1 px-1 text-right">{resumo.amarelos.n}/{jogos.length}</td>
            <td className="pt-1 px-1 text-right">{resumo.vermelhos.n}/{jogos.length}</td>
            <td />
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

// Histórico jogo a jogo de faltas e cartões das duas equipes importadas do banco.
export default function HistoricoDisciplina({ dados }) {
  if (!dados) return null;
  return (
    <details className="bg-slate-800/60 border border-slate-700 rounded-xl p-4" open>
      <summary className="cursor-pointer flex items-center gap-2 text-sm font-bold text-slate-200 uppercase tracking-wide">
        <History size={14} /> Histórico de faltas e cartões (dados importados)
      </summary>
      <p className="text-[11px] text-slate-400 mt-2 mb-3">
        Jogos usados na importação, do mais recente ao mais antigo (@ = jogou fora). "—" = sem dado confiável
        (cartões só contam quando a fonte é confiável; jogos sem dado saem da média e os pesos se renormalizam).
        Faltas e cartões não levam ajuste por Elo: a média time-decay é exatamente a taxa que alimenta a simulação de Markov.
      </p>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Tabela nome={dados.eq1.nome} cor="text-emerald-400" dados={dados.eq1} />
        <Tabela nome={dados.eq2.nome} cor="text-orange-400" dados={dados.eq2} />
      </div>
    </details>
  );
}
