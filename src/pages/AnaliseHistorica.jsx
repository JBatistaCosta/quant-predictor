// src/pages/AnaliseHistorica.jsx — rota /historico/:matchId
// Painel leve de forma recente + confronto direto, estilo sofascore/365score
// (NÃO roda modelo preditivo — isso continua em AnaliseEvento.jsx). Pra cada
// time: sequência V/E/D dos últimos N jogos (qualquer competição), médias de
// gols/escanteios/cartões quando disponível (match_stats), e o histórico de
// confrontos diretos entre os dois times.
import React, { useState, useEffect, useMemo } from 'react';
import { useParams, Link } from 'react-router-dom';
import { ArrowLeft, AlertTriangle, Shield, Loader2, Swords, Landmark } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';

const OPCOES_N = [5, 10, 20];
const CASAS_ROTULO = { pinnacle: 'Pinnacle', bet365: 'Bet365', betano: 'Betano' };
const MERCADO_ROTULO_ODDS = { '1X2': '1X2', 'over_under_2.5': 'Over/Under 2.5 gols' };
const SELECAO_ROTULO_ODDS = { home: 'Mandante', draw: 'Empate', away: 'Visitante', over: 'Over', under: 'Under' };

function resultado(golsTime, golsAdversario) {
  if (golsTime == null || golsAdversario == null) return null;
  if (golsTime > golsAdversario) return 'V';
  if (golsTime < golsAdversario) return 'D';
  return 'E';
}

const COR_RESULTADO = { V: 'bg-emerald-500 text-white', E: 'bg-slate-500 text-white', D: 'bg-red-500 text-white' };

function Escudo({ url, tamanho = 20 }) {
  return url
    ? <img src={url} alt="" className="object-contain shrink-0" style={{ width: tamanho, height: tamanho }} />
    : <Shield size={tamanho * 0.8} className="text-slate-700 shrink-0" />;
}

// Busca os últimos N jogos finalizados de um time (qualquer competição), antes de `antesDe`.
async function buscarFormaTime(teamId, antesDe, n) {
  const { data } = await supabase
    .from('matches')
    .select('id, match_date, home_team_id, away_team_id, home_goals, away_goals, leagues(name), home:teams!matches_home_team_id_fkey(id,name), away:teams!matches_away_team_id_fkey(id,name)')
    .or(`home_team_id.eq.${teamId},away_team_id.eq.${teamId}`)
    .eq('status', 'finished')
    .lt('match_date', antesDe)
    .order('match_date', { ascending: false })
    .limit(n);

  const jogos = (data || []).map(j => {
    const mandante = j.home_team_id === teamId;
    const golsPro = mandante ? j.home_goals : j.away_goals;
    const golsContra = mandante ? j.away_goals : j.home_goals;
    return { ...j, mandante, golsPro, golsContra, resultado: resultado(golsPro, golsContra), adversario: mandante ? j.away : j.home };
  });

  const matchIds = jogos.map(j => j.id);
  const { data: stats } = matchIds.length > 0
    ? await supabase.from('match_stats').select('match_id, team_id, corners, shots, yellow_cards, red_cards').in('match_id', matchIds)
    : { data: [] };
  const statsPorJogo = {};
  (stats || []).filter(s => s.team_id === teamId).forEach(s => { statsPorJogo[s.match_id] = s; });

  return jogos.map(j => ({ ...j, stats: statsPorJogo[j.id] || null }));
}

// Odds de fechamento/pré-jogo de Pinnacle/Bet365/Betano pra essa partida —
// só a captura mais recente por (casa, mercado, seleção), já que cada
// sincronização insere uma linha nova (não sobrescreve) pra dar pra montar a
// curva de movimento de linha com o tempo (não exibida aqui, só o snapshot atual).
async function buscarOddsComparativas(matchId) {
  const { data } = await supabase
    .from('odds_market')
    .select('bookmaker, market, selection, odds, captured_at')
    .eq('match_id', matchId)
    .in('bookmaker', Object.keys(CASAS_ROTULO))
    .order('captured_at', { ascending: false });

  const maisRecentePorChave = new Map();
  for (const linha of data || []) {
    const chave = `${linha.bookmaker}__${linha.market}__${linha.selection}`;
    if (!maisRecentePorChave.has(chave)) maisRecentePorChave.set(chave, linha);
  }
  return [...maisRecentePorChave.values()];
}

function WidgetOdds({ matchId }) {
  const [linhas, setLinhas] = useState([]);
  const [carregando, setCarregando] = useState(true);

  useEffect(() => {
    setCarregando(true);
    buscarOddsComparativas(matchId).then(l => { setLinhas(l); setCarregando(false); });
  }, [matchId]);

  const porMercado = useMemo(() => {
    const mapa = new Map();
    for (const l of linhas) {
      if (!mapa.has(l.market)) mapa.set(l.market, {});
      if (!mapa.get(l.market)[l.selection]) mapa.get(l.market)[l.selection] = {};
      mapa.get(l.market)[l.selection][l.bookmaker] = l.odds;
    }
    return mapa;
  }, [linhas]);

  const ultimaAtualizacao = linhas.length > 0 ? linhas.reduce((max, l) => (new Date(l.captured_at) > new Date(max) ? l.captured_at : max), linhas[0].captured_at) : null;

  return (
    <div className="bg-slate-800 border border-slate-700 rounded-2xl p-5 h-fit">
      <h2 className="text-sm font-bold text-slate-300 uppercase tracking-wider mb-3 flex items-center gap-2">
        <Landmark className="text-emerald-400" size={16} /> Odds comparativas
      </h2>

      {carregando ? (
        <div className="flex items-center justify-center py-6 text-slate-500 gap-2 text-xs">
          <Loader2 className="animate-spin" size={16} /> Carregando...
        </div>
      ) : porMercado.size === 0 ? (
        <p className="text-xs text-slate-600">Sem odds sincronizadas pra esse jogo ainda.</p>
      ) : (
        <div className="space-y-4">
          {[...porMercado.entries()].map(([mercado, porSelecao]) => (
            <div key={mercado}>
              <span className="text-[10px] uppercase font-bold text-slate-500 block mb-1.5">{MERCADO_ROTULO_ODDS[mercado] || mercado}</span>
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-slate-500">
                    <th className="text-left font-normal pb-1"></th>
                    {Object.keys(CASAS_ROTULO).map(c => <th key={c} className="text-right font-normal pb-1">{CASAS_ROTULO[c]}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(porSelecao).map(([selecao, porCasa]) => {
                    const melhor = Math.max(...Object.values(porCasa));
                    return (
                      <tr key={selecao} className="border-t border-slate-700/50">
                        <td className="py-1.5 text-slate-300 font-semibold">{SELECAO_ROTULO_ODDS[selecao] || selecao}</td>
                        {Object.keys(CASAS_ROTULO).map(c => (
                          <td key={c} className={`py-1.5 text-right font-mono ${porCasa[c] === melhor ? 'text-emerald-400 font-bold' : 'text-slate-400'}`}>
                            {porCasa[c] != null ? porCasa[c].toFixed(2) : '—'}
                          </td>
                        ))}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ))}
          {ultimaAtualizacao && (
            <p className="text-[10px] text-slate-600">Última captura: {new Date(ultimaAtualizacao).toLocaleString('pt-BR')}</p>
          )}
        </div>
      )}
    </div>
  );
}

async function buscarConfrontoDireto(timeA, timeB) {
  const { data } = await supabase
    .from('matches')
    .select('id, match_date, home_goals, away_goals, leagues(name), home:teams!matches_home_team_id_fkey(id,name,crest_url), away:teams!matches_away_team_id_fkey(id,name,crest_url)')
    .or(`and(home_team_id.eq.${timeA},away_team_id.eq.${timeB}),and(home_team_id.eq.${timeB},away_team_id.eq.${timeA})`)
    .eq('status', 'finished')
    .order('match_date', { ascending: false })
    .limit(10);
  return data || [];
}

function ResumoTime({ nome, crestUrl, jogos, ladoEsquerda }) {
  const n = jogos.length;
  const media = (campo) => n > 0 ? (jogos.reduce((s, j) => s + (j[campo] ?? 0), 0) / n) : null;
  const mediaStat = (campo) => {
    const comStat = jogos.filter(j => j.stats?.[campo] != null);
    return comStat.length > 0 ? comStat.reduce((s, j) => s + Number(j.stats[campo]), 0) / comStat.length : null;
  };
  const vitorias = jogos.filter(j => j.resultado === 'V').length;
  const empates = jogos.filter(j => j.resultado === 'E').length;
  const derrotas = jogos.filter(j => j.resultado === 'D').length;

  return (
    <div className="bg-slate-800 border border-slate-700 rounded-2xl p-5">
      <div className={`flex items-center gap-3 mb-4 ${ladoEsquerda ? '' : 'flex-row-reverse text-right'}`}>
        <div className="w-12 h-12 rounded-xl bg-slate-900 border border-slate-700 flex items-center justify-center overflow-hidden shrink-0">
          {crestUrl ? <img src={crestUrl} alt="" className="w-full h-full object-contain" /> : <Shield className="text-slate-600" size={22} />}
        </div>
        <h2 className="font-bold text-slate-100">{nome}</h2>
      </div>

      <div className={`flex gap-1 mb-4 ${ladoEsquerda ? '' : 'flex-row-reverse'}`}>
        {jogos.map(j => (
          <span key={j.id} title={`${j.adversario?.name} (${j.golsPro}-${j.golsContra})`}
            className={`w-6 h-6 rounded flex items-center justify-center text-[10px] font-bold ${COR_RESULTADO[j.resultado] || 'bg-slate-700 text-slate-400'}`}>
            {j.resultado || '?'}
          </span>
        ))}
        {jogos.length === 0 && <span className="text-xs text-slate-600">Sem jogos suficientes no histórico.</span>}
      </div>

      <div className={`grid grid-cols-3 gap-2 text-center mb-3 ${ladoEsquerda ? '' : ''}`}>
        <div className="bg-slate-900 rounded-lg py-2">
          <div className="text-[10px] text-slate-500 uppercase">V-E-D</div>
          <div className="text-sm font-bold text-slate-200">{vitorias}-{empates}-{derrotas}</div>
        </div>
        <div className="bg-slate-900 rounded-lg py-2">
          <div className="text-[10px] text-slate-500 uppercase">Gols pró</div>
          <div className="text-sm font-bold text-emerald-400">{media('golsPro')?.toFixed(2) ?? '—'}</div>
        </div>
        <div className="bg-slate-900 rounded-lg py-2">
          <div className="text-[10px] text-slate-500 uppercase">Gols contra</div>
          <div className="text-sm font-bold text-red-400">{media('golsContra')?.toFixed(2) ?? '—'}</div>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-2 text-center">
        <div className="bg-slate-900 rounded-lg py-2">
          <div className="text-[10px] text-slate-500 uppercase">Escanteios</div>
          <div className="text-sm font-bold text-slate-300">{mediaStat('corners')?.toFixed(1) ?? '—'}</div>
        </div>
        <div className="bg-slate-900 rounded-lg py-2">
          <div className="text-[10px] text-slate-500 uppercase">Chutes</div>
          <div className="text-sm font-bold text-slate-300">{mediaStat('shots')?.toFixed(1) ?? '—'}</div>
        </div>
        <div className="bg-slate-900 rounded-lg py-2">
          <div className="text-[10px] text-slate-500 uppercase">Cartões (A)</div>
          <div className="text-sm font-bold text-slate-300">{mediaStat('yellow_cards')?.toFixed(1) ?? '—'}</div>
        </div>
      </div>
    </div>
  );
}

export default function AnaliseHistorica() {
  const { matchId } = useParams();
  const [jogo, setJogo] = useState(null);
  const [n, setN] = useState(10);
  const [formaMandante, setFormaMandante] = useState([]);
  const [formaVisitante, setFormaVisitante] = useState([]);
  const [h2h, setH2h] = useState([]);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState('');

  useEffect(() => {
    if (!supabaseAtivo) return;
    (async () => {
      setCarregando(true);
      setErro('');
      const { data: j, error: erroJogo } = await supabase
        .from('matches')
        .select('id, match_date, league_id, home_team_id, away_team_id, home_goals, away_goals, status, leagues(name), home:teams!matches_home_team_id_fkey(id,name,crest_url), away:teams!matches_away_team_id_fkey(id,name,crest_url)')
        .eq('id', matchId)
        .single();

      if (erroJogo || !j) { setErro('Jogo não encontrado.'); setCarregando(false); return; }
      setJogo(j);

      const referencia = j.match_date || new Date().toISOString();
      const [fM, fV, confronto] = await Promise.all([
        buscarFormaTime(j.home_team_id, referencia, n),
        buscarFormaTime(j.away_team_id, referencia, n),
        buscarConfrontoDireto(j.home_team_id, j.away_team_id),
      ]);
      setFormaMandante(fM);
      setFormaVisitante(fV);
      setH2h(confronto);
      setCarregando(false);
    })();
  }, [matchId, n]);

  const resumoH2H = useMemo(() => {
    if (!jogo || h2h.length === 0) return null;
    let vMandante = 0, vVisitante = 0, empates = 0;
    h2h.forEach(m => {
      const golsMandante = m.home.id === jogo.home_team_id ? m.home_goals : m.away_goals;
      const golsVisitante = m.home.id === jogo.home_team_id ? m.away_goals : m.home_goals;
      if (golsMandante > golsVisitante) vMandante++;
      else if (golsMandante < golsVisitante) vVisitante++;
      else empates++;
    });
    return { vMandante, vVisitante, empates };
  }, [h2h, jogo]);

  if (!supabaseAtivo) {
    return (
      <div className="max-w-4xl mx-auto bg-slate-800 border border-red-500/30 rounded-2xl p-6 text-center">
        <AlertTriangle className="text-red-400 mx-auto mb-2" size={28} />
        <p className="text-slate-300">Supabase não configurado.</p>
      </div>
    );
  }

  if (carregando) {
    return (
      <div className="max-w-4xl mx-auto flex items-center justify-center py-16 text-slate-500 gap-2 text-sm">
        <Loader2 className="animate-spin" size={20} /> Carregando...
      </div>
    );
  }

  if (erro || !jogo) {
    return (
      <div className="max-w-4xl mx-auto bg-slate-800 border border-red-500/30 rounded-2xl p-6 text-center">
        <AlertTriangle className="text-red-400 mx-auto mb-2" size={28} />
        <p className="text-slate-300">{erro || 'Jogo não encontrado.'}</p>
        <Link to="/eventos" className="text-emerald-400 text-sm hover:underline mt-3 inline-block">← Voltar pra Eventos</Link>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto">
      <Link to="/eventos" className="flex items-center gap-1.5 text-slate-400 hover:text-slate-200 text-sm mb-4 w-fit">
        <ArrowLeft size={16} /> Voltar
      </Link>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-4">
        <div className="lg:col-span-2 bg-slate-800 border border-slate-700 rounded-2xl p-6">
          <p className="text-center text-xs text-slate-500 uppercase tracking-wider mb-1">{jogo.leagues?.name || 'Confronto'}</p>
          <p className="text-center text-xs text-slate-600 mb-3">{jogo.match_date ? new Date(jogo.match_date).toLocaleString('pt-BR', { dateStyle: 'long', timeStyle: 'short' }) : ''}</p>
          <div className="flex items-center justify-center gap-4">
            <div className="flex items-center gap-2">
              <Escudo url={jogo.home?.crest_url} tamanho={32} />
              <span className="font-bold text-slate-100">{jogo.home?.name}</span>
            </div>
            <span className="text-slate-500 font-mono">
              {jogo.status === 'finished' ? `${jogo.home_goals}-${jogo.away_goals}` : 'vs'}
            </span>
            <div className="flex items-center gap-2">
              <span className="font-bold text-slate-100">{jogo.away?.name}</span>
              <Escudo url={jogo.away?.crest_url} tamanho={32} />
            </div>
          </div>

          <div className="flex items-center justify-center gap-2 mt-4">
            <span className="text-xs text-slate-500 mr-1">Últimos:</span>
            {OPCOES_N.map(opcao => (
              <button
                key={opcao}
                onClick={() => setN(opcao)}
                className={`px-3 py-1 rounded-lg text-xs font-bold ${n === opcao ? 'bg-emerald-500/20 text-emerald-400' : 'bg-slate-900 text-slate-500 hover:text-slate-300'}`}
              >
                {opcao} jogos
              </button>
            ))}
          </div>
        </div>

        <WidgetOdds matchId={jogo.id} />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
        <ResumoTime nome={jogo.home?.name} crestUrl={jogo.home?.crest_url} jogos={formaMandante} ladoEsquerda />
        <ResumoTime nome={jogo.away?.name} crestUrl={jogo.away?.crest_url} jogos={formaVisitante} ladoEsquerda={false} />
      </div>

      <div className="bg-slate-800 border border-slate-700 rounded-2xl p-5">
        <h2 className="text-sm font-bold text-slate-300 uppercase tracking-wider mb-3 flex items-center gap-2">
          <Swords className="text-emerald-400" size={16} /> Confronto direto
        </h2>

        {h2h.length === 0 ? (
          <p className="text-sm text-slate-600">Sem confrontos anteriores registrados.</p>
        ) : (
          <>
            {resumoH2H && (
              <div className="grid grid-cols-3 gap-2 text-center mb-4">
                <div className="bg-slate-900 rounded-lg py-2">
                  <div className="text-[10px] text-slate-500 uppercase truncate">{jogo.home?.name}</div>
                  <div className="text-sm font-bold text-emerald-400">{resumoH2H.vMandante}</div>
                </div>
                <div className="bg-slate-900 rounded-lg py-2">
                  <div className="text-[10px] text-slate-500 uppercase">Empates</div>
                  <div className="text-sm font-bold text-slate-300">{resumoH2H.empates}</div>
                </div>
                <div className="bg-slate-900 rounded-lg py-2">
                  <div className="text-[10px] text-slate-500 uppercase truncate">{jogo.away?.name}</div>
                  <div className="text-sm font-bold text-emerald-400">{resumoH2H.vVisitante}</div>
                </div>
              </div>
            )}
            <div className="divide-y divide-slate-700/50">
              {h2h.map(m => (
                <div key={m.id} className="flex items-center gap-3 py-2.5 text-sm">
                  <span className="text-slate-500 text-xs w-20 shrink-0">{m.match_date?.slice(0, 10)}</span>
                  <div className="flex-1 flex items-center justify-end gap-2 min-w-0">
                    <span className="truncate text-slate-300">{m.home?.name}</span>
                    <Escudo url={m.home?.crest_url} tamanho={16} />
                  </div>
                  <span className="font-mono font-bold w-12 text-center shrink-0 text-slate-300">{m.home_goals}-{m.away_goals}</span>
                  <div className="flex-1 flex items-center gap-2 min-w-0">
                    <Escudo url={m.away?.crest_url} tamanho={16} />
                    <span className="truncate text-slate-300">{m.away?.name}</span>
                  </div>
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
