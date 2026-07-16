// src/pages/EventosLista.jsx
// Linha do tempo de TODOS os jogos reais (tabela `matches` do pipeline, não o
// cadastro manual de `eventos` — esse continua em /eventos/novo pra quem quiser
// registrar aposta própria). Data selecionada centralizada na barra horizontal
// (TimelineDatas), padrão = hoje; jogos do dia agrupados por liga; caixa de
// próximos jogos das datas seguintes. Cada jogo linka pra /historico/:id
// (forma recente + confronto direto, estilo sofascore/365score).
//
// OBS: `matches` não tem campo de "campo neutro" — só dá pra indicar
// mandante/visitante (a ordem já deixa isso claro: mandante à esquerda).
import React, { useState, useEffect, useMemo } from 'react';
import { Link } from 'react-router-dom';
import { Calendar, AlertTriangle, Shield, Loader2, ArrowRight } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';
import TimelineDatas from '../components/TimelineDatas';

const STATUS_ROTULO = { scheduled: 'A confirmar', finished: 'Encerrado', in_play: 'Ao vivo', postponed: 'Adiado', cancelled: 'Cancelado' };

function inicioDia(d) { const x = new Date(d); x.setHours(0, 0, 0, 0); return x; }
function fimDia(d) { const x = new Date(d); x.setHours(23, 59, 59, 999); return x; }

function corPlacar(golsA, golsB) {
  if (golsA == null || golsB == null) return 'text-slate-500';
  if (golsA === golsB) return 'text-slate-300';
  return golsA > golsB ? 'text-emerald-400' : 'text-red-400';
}

function LinhaJogo({ jogo }) {
  const jogado = jogo.status === 'finished';
  const horario = jogo.match_date ? new Date(jogo.match_date).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' }) : '--:--';
  return (
    <Link to={`/historico/${jogo.id}`} className="flex items-center gap-3 px-4 py-2.5 text-sm hover:bg-slate-700/20 transition-colors">
      <span className="text-slate-500 text-xs w-12 shrink-0">{jogado ? STATUS_ROTULO.finished : horario}</span>
      <div className="flex-1 flex items-center justify-end gap-2 min-w-0">
        <span className="truncate text-slate-200">{jogo.home?.name || '?'}</span>
        {jogo.home?.crest_url ? <img src={jogo.home.crest_url} alt="" className="w-5 h-5 object-contain shrink-0" /> : <Shield size={16} className="text-slate-700 shrink-0" />}
      </div>
      <span className={`font-mono font-bold w-14 text-center shrink-0 ${corPlacar(jogo.home_goals, jogo.away_goals)}`}>
        {jogado ? `${jogo.home_goals}-${jogo.away_goals}` : 'vs'}
      </span>
      <div className="flex-1 flex items-center gap-2 min-w-0">
        {jogo.away?.crest_url ? <img src={jogo.away.crest_url} alt="" className="w-5 h-5 object-contain shrink-0" /> : <Shield size={16} className="text-slate-700 shrink-0" />}
        <span className="truncate text-slate-200">{jogo.away?.name || '?'}</span>
      </div>
      <ArrowRight size={14} className="text-slate-600 shrink-0" />
    </Link>
  );
}

export default function EventosLista() {
  const [dataSelecionada, setDataSelecionada] = useState(() => { const d = new Date(); d.setHours(0, 0, 0, 0); return d; });
  const [jogosDoDia, setJogosDoDia] = useState([]);
  const [proximosJogos, setProximosJogos] = useState([]);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState('');

  useEffect(() => {
    if (!supabaseAtivo) return;
    (async () => {
      setCarregando(true);
      setErro('');
      const selectPadrao = 'id, match_date, home_goals, away_goals, status, round, home:teams!matches_home_team_id_fkey(id, name, crest_url), away:teams!matches_away_team_id_fkey(id, name, crest_url), leagues(id, name)';

      const [{ data: doDia, error: erroDia }, { data: proximos, error: erroProximos }] = await Promise.all([
        supabase.from('matches').select(selectPadrao)
          .gte('match_date', inicioDia(dataSelecionada).toISOString())
          .lte('match_date', fimDia(dataSelecionada).toISOString())
          .order('match_date', { ascending: true }),
        supabase.from('matches').select(selectPadrao)
          .gt('match_date', fimDia(dataSelecionada).toISOString())
          .eq('status', 'scheduled')
          .order('match_date', { ascending: true })
          .limit(8),
      ]);

      if (erroDia || erroProximos) setErro((erroDia || erroProximos).message);
      setJogosDoDia(doDia || []);
      setProximosJogos(proximos || []);
      setCarregando(false);
    })();
  }, [dataSelecionada]);

  const porLiga = useMemo(() => {
    const mapa = new Map();
    for (const j of jogosDoDia) {
      const chave = j.leagues?.id ?? 'sem-liga';
      if (!mapa.has(chave)) mapa.set(chave, { nome: j.leagues?.name || 'Sem liga cadastrada', jogos: [] });
      mapa.get(chave).jogos.push(j);
    }
    return [...mapa.values()].sort((a, b) => a.nome.localeCompare(b.nome));
  }, [jogosDoDia]);

  if (!supabaseAtivo) {
    return (
      <div className="max-w-5xl mx-auto bg-slate-800 border border-red-500/30 rounded-2xl p-6 text-center">
        <AlertTriangle className="text-red-400 mx-auto mb-2" size={28} />
        <p className="text-slate-300">Supabase não configurado.</p>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto">
      <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 mb-4">
        <h1 className="text-2xl font-extrabold flex items-center gap-3 text-slate-100">
          <Calendar className="text-emerald-400" size={28} /> Eventos
        </h1>
        <p className="text-slate-400 mt-1 text-sm">Linha do tempo de todos os jogos — navegue por data ou veja os próximos.</p>
      </div>

      <TimelineDatas dataSelecionada={dataSelecionada} onSelecionar={setDataSelecionada} />

      {erro && <div className="bg-red-950/30 border border-red-600/40 text-red-300 text-sm px-4 py-3 rounded-xl mb-4">{erro}</div>}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2 space-y-4">
          {carregando ? (
            <div className="flex items-center justify-center py-16 text-slate-500 gap-2">
              <Loader2 className="animate-spin" size={20} /> Carregando jogos...
            </div>
          ) : porLiga.length === 0 ? (
            <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 text-center text-slate-500 text-sm">
              Nenhum jogo nessa data.
            </div>
          ) : (
            porLiga.map((grupo, i) => (
              <div key={i} className="bg-slate-800 border border-slate-700 rounded-2xl overflow-hidden">
                <div className="bg-slate-900 px-4 py-2 text-xs font-bold text-slate-400 uppercase tracking-wider">{grupo.nome}</div>
                <div className="divide-y divide-slate-700/50">
                  {grupo.jogos.map(j => <LinhaJogo key={j.id} jogo={j} />)}
                </div>
              </div>
            ))
          )}
        </div>

        <div className="bg-slate-800 border border-slate-700 rounded-2xl p-4 h-fit">
          <h2 className="text-xs font-bold text-slate-400 uppercase tracking-wider mb-3">Próximos jogos</h2>
          {proximosJogos.length === 0 ? (
            <p className="text-sm text-slate-600">Nenhum jogo futuro agendado.</p>
          ) : (
            <div className="space-y-2">
              {proximosJogos.map(j => (
                <button
                  key={j.id}
                  onClick={() => setDataSelecionada(inicioDia(new Date(j.match_date)))}
                  className="w-full text-left text-xs bg-slate-900 hover:bg-slate-700/50 border border-slate-700 rounded-lg p-2.5 transition-colors"
                >
                  <div className="text-slate-500 mb-1">{new Date(j.match_date).toLocaleDateString('pt-BR', { day: '2-digit', month: 'short' })} · {j.leagues?.name || '—'}</div>
                  <div className="flex items-center justify-between gap-1 text-slate-300">
                    <span className="truncate">{j.home?.name}</span>
                    <span className="text-slate-600 shrink-0">vs</span>
                    <span className="truncate text-right">{j.away?.name}</span>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
