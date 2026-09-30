import React from 'react';
import { History } from 'lucide-react';

const vazio = (v) => v === null || v === undefined;
const fmt = (casas) => (v) => (vazio(v) ? '—' : Number(v).toFixed(casas));

// Colunas da tabela: rótulo, casas decimais, cor e dica. Ordem = ordem dos cartões de equipe da tela.
const COLUNAS = [
  { id: 'xg', rotulo: 'xG', casas: 2, cor: 'text-emerald-400', dica: 'xG criado pelo time' },
  { id: 'xga', rotulo: 'xGA', casas: 2, cor: 'text-red-400', dica: 'xG sofrido (= xG do adversário no jogo)' },
  { id: 'chutes', rotulo: 'Chutes', casas: 1 },
  { id: 'chutesNoGol', rotulo: 'No gol', casas: 1 },
  { id: 'posse', rotulo: 'Posse%', casas: 0, cor: 'text-blue-400' },
  { id: 'escanteios', rotulo: 'Cantos', casas: 1, cor: 'text-yellow-400' },
  { id: 'faltas', rotulo: 'Faltas', casas: 1 },
  { id: 'amarelos', rotulo: 'Amar.', casas: 1, cor: 'text-yellow-400' },
  { id: 'vermelhos', rotulo: 'Verm.', casas: 2, cor: 'text-red-400' },
];

function Tabela({ nome, cor, dados }) {
  const { jogos, resumo, ajusteElo, eloReferencia } = dados;
  return (
    <div className="bg-slate-900 border border-slate-700 rounded-lg p-3 overflow-x-auto">
      <div className={`text-xs font-bold uppercase tracking-wide mb-2 ${cor}`}>{nome}</div>
      <table className="w-full text-xs text-slate-300">
        <thead>
          <tr className="text-slate-500 text-[10px] uppercase border-b border-slate-700">
            <th className="text-left py-1 pr-2">Data</th>
            <th className="text-left py-1 pr-2">Adversário</th>
            <th className="text-right py-1 px-1" title="Elo do adversário antes do jogo (~ = aproximado pelo Elo atual)">Elo adv.</th>
            {COLUNAS.map((c) => (
              <th key={c.id} className="text-right py-1 px-1" title={c.dica}>{c.rotulo}</th>
            ))}
            <th className="text-right py-1 pl-2" title="Peso do jogo na média com time-decay (ξ=0,2): o mais recente pesa mais">Peso</th>
          </tr>
        </thead>
        <tbody>
          {jogos.map((g, i) => (
            <tr key={i} className="border-b border-slate-800">
              <td className="py-1 pr-2 whitespace-nowrap">{g.data}</td>
              <td className="py-1 pr-2 truncate max-w-[140px]" title={g.adversario}>{g.local === 'C' ? '' : '@ '}{g.adversario}</td>
              <td className="py-1 px-1 text-right font-mono text-slate-500">{vazio(g.eloAdv) ? '—' : `${g.eloAproximado ? '~' : ''}${g.eloAdv}`}</td>
              {COLUNAS.map((c) => (
                <td key={c.id} className={`py-1 px-1 text-right font-mono ${c.cor || ''}`}>{fmt(c.casas)(g[c.id])}</td>
              ))}
              <td className="py-1 pl-2 text-right font-mono text-slate-500">{g.pesoPct.toFixed(0)}%</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr className="font-semibold text-slate-200 border-t border-slate-600">
            <td className="py-1 pr-2" colSpan={3}>Média simples</td>
            {COLUNAS.map((c) => (
              <td key={c.id} className={`py-1 px-1 text-right font-mono ${c.cor || ''}`}>{fmt(c.casas + 1 > 2 ? 2 : c.casas + 1)(resumo[c.id].simples)}</td>
            ))}
            <td />
          </tr>
          <tr className="font-semibold text-slate-200">
            <td className="py-1 pr-2" colSpan={3}>Média time-decay</td>
            {COLUNAS.map((c) => (
              <td key={c.id} className={`py-1 px-1 text-right font-mono ${c.cor || ''}`}>{fmt(c.casas + 1 > 2 ? 2 : c.casas + 1)(resumo[c.id].decay)}</td>
            ))}
            <td />
          </tr>
          <tr className="font-bold text-emerald-300">
            <td className="py-1 pr-2" colSpan={3}>
              {ajusteElo ? <>Time-decay ajustada por Elo <span className="text-slate-500 font-normal">(usada na simulação{eloReferencia ? `, ref. ${eloReferencia}` : ''})</span></> : <>Usada na simulação <span className="text-slate-500 font-normal">(ajuste por Elo desligado)</span></>}
            </td>
            {COLUNAS.map((c) => (
              <td key={c.id} className={`py-1 px-1 text-right font-mono ${c.cor || ''}`}>{fmt(c.casas + 1 > 2 ? 2 : c.casas + 1)(resumo[c.id].decayUsado)}</td>
            ))}
            <td />
          </tr>
          <tr className="text-[10px] text-slate-500">
            <td className="pt-1 pr-2" colSpan={3}>Jogos com dado</td>
            {COLUNAS.map((c) => (
              <td key={c.id} className="pt-1 px-1 text-right">{resumo[c.id].n}/{jogos.length}</td>
            ))}
            <td />
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

// Histórico jogo a jogo de TODOS os parâmetros importados do banco (xG, xGA, chutes, escanteios,
// faltas, cartões…), das duas equipes, com média simples, time-decay e time-decay ajustada por Elo.
export default function HistoricoImportado({ dados }) {
  if (!dados) return null;
  return (
    <details className="bg-slate-800/60 border border-slate-700 rounded-xl p-4" open>
      <summary className="cursor-pointer flex items-center gap-2 text-sm font-bold text-slate-200 uppercase tracking-wide">
        <History size={14} /> Histórico dos dados importados (jogo a jogo)
      </summary>
      <p className="text-[11px] text-slate-400 mt-2 mb-3">
        Jogos usados na importação, do mais recente ao mais antigo (@ = jogou fora). O <strong>Peso</strong> é o do time-decay
        (ξ=0,2). A <strong>média ajustada por Elo</strong> corrige cada jogo pela força do adversário daquele dia (só xG, xGA, chutes,
        chutes no gol e escanteios têm ajuste medido); posse, faltas e cartões não levam ajuste, então as duas últimas linhas coincidem nelas.
        "—" = sem dado confiável (o jogo sai da média e os pesos se renormalizam; cartões só contam quando a fonte é confiável).
        Os campos de xG, xGA, chutes, no gol e cantos dos cartões de equipe abaixo partem da linha em verde; a posse fica em 50% (editável) e faltas/cartões alimentam direto a simulação de Markov.
      </p>
      <div className="grid grid-cols-1 gap-4">
        <Tabela nome={dados.eq1.nome} cor="text-emerald-400" dados={dados.eq1} />
        <Tabela nome={dados.eq2.nome} cor="text-orange-400" dados={dados.eq2} />
      </div>
    </details>
  );
}
