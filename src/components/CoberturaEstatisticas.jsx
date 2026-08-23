// src/components/CoberturaEstatisticas.jsx
// Aba de Configurações: cobertura de estatísticas por liga/temporada, lendo
// direto da view vw_cobertura_estatisticas (migration
// cria_views_cobertura_estatisticas_odds, com xG combinado adicionado em
// vw_cobertura_estatisticas_xg_combinado). Três fontes distintas de stats
// no banco: match_stats (genérica, legada -- na prática pouco preenchida,
// ver CONTEXTO_PROJETO.md), match_stats.xg (xG via Understat, só 5 ligas
// europeias) e match_stats_fotmob (a fonte rica de verdade hoje, com xG
// próprio e cobertura bem mais ampla). A coluna "xG" usa pct_xg_combinado:
// conta a partida como coberta se QUALQUER uma das duas fontes tiver xG,
// usando uma como fallback da outra.
import React, { useEffect, useState } from 'react';
import { ChevronDown, ChevronRight, Loader2 } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';

function corPct(pct) {
  if (pct >= 90) return 'bg-emerald-500';
  if (pct >= 50) return 'bg-yellow-500';
  if (pct > 0) return 'bg-orange-500';
  return 'bg-slate-600';
}

function BarraPct({ pct, rotulo }) {
  const valor = Number(pct) || 0;
  return (
    <div className="flex items-center gap-1.5">
      {rotulo && <span className="text-[10px] text-slate-500 w-14 shrink-0">{rotulo}</span>}
      <div className="flex-1 h-1.5 bg-slate-700 rounded-full overflow-hidden min-w-[50px]">
        <div className={`h-full ${corPct(valor)}`} style={{ width: `${Math.min(valor, 100)}%` }} />
      </div>
      <span className="text-xs font-mono text-slate-400 w-9 text-right">{valor.toFixed(0)}%</span>
    </div>
  );
}

export default function CoberturaEstatisticas() {
  const [linhas, setLinhas] = useState([]);
  const [carregando, setCarregando] = useState(true);
  const [ligaAberta, setLigaAberta] = useState(null);

  useEffect(() => {
    if (!supabaseAtivo) return;
    (async () => {
      setCarregando(true);
      const { data } = await supabase.from('vw_cobertura_estatisticas').select('*').order('liga').order('season');
      setLinhas(data || []);
      setCarregando(false);
    })();
  }, []);

  if (!supabaseAtivo) return null;
  if (carregando) {
    return <div className="flex items-center gap-2 text-slate-400 text-sm py-8 justify-center"><Loader2 size={16} className="animate-spin" /> Carregando cobertura de estatísticas...</div>;
  }

  const porLiga = {};
  for (const l of linhas) {
    if (!porLiga[l.liga]) porLiga[l.liga] = { temporadas: [], finalizadas: 0, com_match_stats: 0, com_xg_combinado: 0, com_fotmob: 0 };
    porLiga[l.liga].temporadas.push(l);
    porLiga[l.liga].finalizadas += l.finalizadas || 0;
    porLiga[l.liga].com_match_stats += l.com_match_stats || 0;
    porLiga[l.liga].com_xg_combinado += l.com_xg_combinado || 0;
    porLiga[l.liga].com_fotmob += l.com_fotmob || 0;
  }

  return (
    <div className="space-y-3">
      <p className="text-slate-400 text-sm">
        Cobertura de estatísticas por liga e temporada — <span className="text-slate-300 font-semibold">Stats</span> (match_stats genérica),{' '}
        <span className="text-slate-300 font-semibold">xG</span> (Understat OU FotMob, o que estiver disponível) e{' '}
        <span className="text-slate-300 font-semibold">FotMob</span> (fonte rica atual: chutes, passes, duelos, xG detalhado).
      </p>

      {Object.entries(porLiga).map(([liga, info]) => {
        const pctFotmobTotal = info.finalizadas > 0 ? (100 * info.com_fotmob) / info.finalizadas : 0;
        const aberta = ligaAberta === liga;

        return (
          <div key={liga} className="bg-slate-900 border border-slate-700 rounded-xl overflow-hidden">
            <button
              onClick={() => setLigaAberta(aberta ? null : liga)}
              className="w-full flex items-center justify-between px-4 py-3 hover:bg-slate-800/50 transition-colors"
            >
              <div className="flex items-center gap-2">
                {aberta ? <ChevronDown size={16} className="text-slate-500" /> : <ChevronRight size={16} className="text-slate-500" />}
                <span className="font-bold text-slate-100 text-sm">{liga}</span>
                <span className="text-xs text-slate-500">{info.temporadas.length} temporada(s) · {info.finalizadas} partida(s) finalizada(s)</span>
              </div>
              <BarraPct pct={pctFotmobTotal} rotulo="FotMob" />
            </button>

            {aberta && (
              <div className="border-t border-slate-700 px-4 py-3">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-slate-500 uppercase text-[10px] tracking-wider">
                      <th className="text-left font-bold pb-1.5">Temporada</th>
                      <th className="text-right font-bold pb-1.5">Finalizadas</th>
                      <th className="text-left font-bold pb-1.5 pl-4">Stats</th>
                      <th className="text-left font-bold pb-1.5 pl-4">xG</th>
                      <th className="text-left font-bold pb-1.5 pl-4">FotMob</th>
                    </tr>
                  </thead>
                  <tbody>
                    {info.temporadas.map((t) => (
                      <tr key={t.season} className="border-t border-slate-800">
                        <td className="py-1.5 text-slate-300 font-mono">{t.season}</td>
                        <td className="py-1.5 text-right text-slate-400">{t.finalizadas}</td>
                        <td className="py-1.5 pl-4 w-32"><BarraPct pct={t.pct_match_stats} /></td>
                        <td className="py-1.5 pl-4 w-32" title={`Understat: ${t.pct_xg ?? 0}% · FotMob: ${t.pct_xg_fotmob ?? 0}%`}>
                          <BarraPct pct={t.pct_xg_combinado} />
                        </td>
                        <td className="py-1.5 pl-4 w-32"><BarraPct pct={t.pct_fotmob} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
