// src/components/IdentificadoresOddsPapi.jsx
// Aba de Configurações: lista os torneios da OddsPapi já mapeados pra cada
// liga do nosso cadastro (liga_oddspapi_tournament) -- pedido do usuário pra
// poder consultar rapidamente o tournament_id de cada campeonato sem precisar
// abrir o banco (ex: pra montar uma chamada manual à API ou conferir se uma
// liga nova já foi vinculada). Read-only, mesmo padrão de CoberturaOdds.jsx.
import React, { useEffect, useState } from 'react';
import { Loader2, AlertTriangle, Copy, Check, Hash } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';

function BotaoCopiar({ valor }) {
  const [copiado, setCopiado] = useState(false);
  return (
    <button
      onClick={() => {
        navigator.clipboard?.writeText(String(valor));
        setCopiado(true);
        setTimeout(() => setCopiado(false), 1500);
      }}
      title="Copiar"
      className="text-slate-500 hover:text-slate-200 shrink-0"
    >
      {copiado ? <Check size={13} className="text-emerald-400" /> : <Copy size={13} />}
    </button>
  );
}

export default function IdentificadoresOddsPapi() {
  const [linhas, setLinhas] = useState([]);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState('');
  const [filtro, setFiltro] = useState('');

  useEffect(() => {
    if (!supabaseAtivo) return;
    (async () => {
      setCarregando(true); setErro('');
      try {
        const { data, error } = await supabase
          .from('liga_oddspapi_tournament')
          .select('league_id, tournament_id, tournament_name, resolvido_em, leagues(name)')
          .order('tournament_name');
        if (error) throw new Error(error.message);
        setLinhas((data || []).map((l) => ({
          league_id: l.league_id,
          liga_nome: l.leagues?.name || `Liga #${l.league_id}`,
          tournament_id: l.tournament_id,
          tournament_name: l.tournament_name,
          resolvido_em: l.resolvido_em,
        })));
      } catch (err) {
        setErro(err.message || 'Falha ao carregar os identificadores.');
      } finally {
        setCarregando(false);
      }
    })();
  }, []);

  if (!supabaseAtivo) return null;
  if (carregando) {
    return <div className="flex items-center gap-2 text-slate-400 text-sm py-8 justify-center"><Loader2 size={16} className="animate-spin" /> Carregando identificadores...</div>;
  }

  const filtroLower = filtro.trim().toLowerCase();
  const linhasFiltradas = filtroLower
    ? linhas.filter((l) =>
        l.liga_nome.toLowerCase().includes(filtroLower) ||
        l.tournament_name.toLowerCase().includes(filtroLower) ||
        String(l.tournament_id).includes(filtroLower) ||
        String(l.league_id).includes(filtroLower))
    : linhas;

  return (
    <div className="space-y-3">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <p className="text-slate-400 text-sm max-w-2xl">
          <span className="text-slate-300 font-semibold">tournament_id</span> é o identificador de campeonato usado
          nas chamadas à OddsPapi (parâmetro <code className="text-[11px] bg-slate-900 border border-slate-700 rounded px-1 py-0.5">tournamentIds</code>) —
          vem do mapeamento em <span className="text-slate-300 font-mono text-xs">liga_oddspapi_tournament</span>,
          resolvido manualmente uma vez por liga (tarefa <span className="font-mono text-xs">odds-descobrir</span>).
          Ligas sem torneio mapeado aqui nunca recebem odds ao vivo nem históricas.
        </p>
        <input
          type="text"
          value={filtro}
          onChange={(e) => setFiltro(e.target.value)}
          placeholder="Filtrar por liga ou ID..."
          className="bg-slate-900 border border-slate-600 rounded-lg px-3 py-1.5 text-sm text-slate-100 placeholder:text-slate-500 w-full sm:w-64"
        />
      </div>

      {erro && (
        <div className="bg-red-950/30 border border-red-600/40 text-red-300 text-sm px-4 py-2.5 rounded-lg flex items-start gap-2">
          <AlertTriangle size={16} className="shrink-0 mt-0.5" /> {erro}
        </div>
      )}

      {linhas.length === 0 && !erro && (
        <div className="text-slate-500 text-sm py-8 text-center flex flex-col items-center gap-2">
          <Hash size={22} className="text-slate-600" />
          Nenhuma liga com torneio da OddsPapi mapeado ainda.
        </div>
      )}

      {linhasFiltradas.length > 0 && (
        <div className="bg-slate-900 border border-slate-700 rounded-xl overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-slate-500 uppercase text-[10px] tracking-wider border-b border-slate-700">
                <th className="text-left font-bold px-4 py-2.5">Liga (nosso cadastro)</th>
                <th className="text-right font-bold px-4 py-2.5">ID interno</th>
                <th className="text-left font-bold px-4 py-2.5">Torneio na OddsPapi</th>
                <th className="text-right font-bold px-4 py-2.5">tournament_id</th>
                <th className="text-left font-bold px-4 py-2.5">Mapeado em</th>
              </tr>
            </thead>
            <tbody>
              {linhasFiltradas.map((l) => (
                <tr key={l.league_id} className="border-t border-slate-800 hover:bg-slate-800/40">
                  <td className="px-4 py-2 text-slate-200 font-semibold">{l.liga_nome}</td>
                  <td className="px-4 py-2 text-right text-slate-400 font-mono text-xs">{l.league_id}</td>
                  <td className="px-4 py-2 text-slate-300">{l.tournament_name}</td>
                  <td className="px-4 py-2">
                    <div className="flex items-center justify-end gap-1.5">
                      <span className="text-emerald-300 font-mono text-xs font-bold">{l.tournament_id}</span>
                      <BotaoCopiar valor={l.tournament_id} />
                    </div>
                  </td>
                  <td className="px-4 py-2 text-slate-500 text-xs">
                    {l.resolvido_em ? new Date(l.resolvido_em).toLocaleDateString('pt-BR') : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {linhasFiltradas.length > 0 && (
        <p className="text-xs text-slate-500">{linhasFiltradas.length} de {linhas.length} liga(s) mapeada(s).</p>
      )}
    </div>
  );
}
