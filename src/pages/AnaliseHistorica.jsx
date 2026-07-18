// src/pages/AnaliseHistorica.jsx — rota /historico/:matchId
// Painel leve de forma recente + confronto direto, estilo sofascore/365score
// (NÃO roda modelo preditivo — isso continua em AnaliseEvento.jsx). Pra cada
// time: sequência V/E/D dos últimos N jogos (qualquer competição), médias de
// gols/escanteios/cartões quando disponível (match_stats), e o histórico de
// confrontos diretos entre os dois times.
//
// Abaixo do resumo, 3 abas: "Visão Geral" (conteúdo acima, já carregado no
// load da página), "Estatísticas do Jogo" e "Jogadores" (ambas dados do
// FotMob — match_stats_fotmob/match_player_stats_fotmob, ver
// arquivos_do_claude/ingestao_fotmob.py). As duas últimas só disparam
// consulta ao banco na primeira vez que a aba é aberta (nunca no load da
// página) — partida antiga sem cobertura FotMob nunca gasta uma consulta à
// toa se o usuário não clicar na aba.
import React, { useState, useEffect, useMemo } from 'react';
import { useParams, Link } from 'react-router-dom';
import { ArrowLeft, AlertTriangle, Shield, Loader2, Swords, Landmark, TrendingUp, LayoutGrid, BarChart3, UserRound } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';
import WidgetOddsTheOddsAPI from '../components/WidgetOddsTheOddsAPI';

const OPCOES_N = [5, 10, 20];
const CASAS_ROTULO = { pinnacle: 'Pinnacle', bet365: 'Bet365', betano: 'Betano' };
const MERCADO_ROTULO_ODDS = { '1X2': '1X2', 'over_under_2.5': 'Over/Under 2.5 gols' };
const SELECAO_ROTULO_ODDS = { home: 'Mandante', draw: 'Empate', away: 'Visitante', over: 'Over', under: 'Under' };

const ABAS = [
  { valor: 'geral', rotulo: 'Visão Geral', icone: LayoutGrid },
  { valor: 'estatisticas', rotulo: 'Estatísticas do Jogo', icone: BarChart3 },
  { valor: 'jogadores', rotulo: 'Jogadores', icone: UserRound },
];

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

// --- Aba "Estatísticas do Jogo" (match_stats_fotmob) ---

const METRICAS_JOGO = [
  { campo: 'possession', rotulo: 'Posse de bola', formato: 'pct' },
  { campo: 'xg', rotulo: 'Expected goals (xG)', formato: 'dec' },
  { campo: 'xgot', rotulo: 'xG no alvo (xGOT)', formato: 'dec' },
  { campo: 'total_shots', rotulo: 'Chutes', formato: 'int' },
  { campo: 'shots_on_target', rotulo: 'Chutes no alvo', formato: 'int' },
  { campo: 'big_chances', rotulo: 'Grandes chances', formato: 'int' },
  { campo: 'corners', rotulo: 'Escanteios', formato: 'int' },
  { campo: 'accurate_passes', rotulo: 'Passes certos', formato: 'int' },
  { campo: 'duels_won', rotulo: 'Duelos vencidos', formato: 'int' },
  { campo: 'aerial_duels_won', rotulo: 'Duelos aéreos vencidos', formato: 'int' },
  { campo: 'tackles', rotulo: 'Desarmes', formato: 'int' },
  { campo: 'fouls_committed', rotulo: 'Faltas cometidas', formato: 'int' },
  { campo: 'yellow_cards', rotulo: 'Cartões amarelos', formato: 'int' },
  { campo: 'red_cards', rotulo: 'Cartões vermelhos', formato: 'int' },
];

function formatarMetrica(valor, formato) {
  if (valor == null) return '—';
  if (formato === 'pct') return `${Math.round(valor)}%`;
  if (formato === 'dec') return Number(valor).toFixed(2);
  return Math.round(valor);
}

function LinhaComparativa({ rotulo, home, away, formato }) {
  const total = (Number(home) || 0) + (Number(away) || 0);
  const pctHome = total > 0 ? (Number(home) / total) * 100 : 50;
  const homeMaior = home != null && away != null && Number(home) > Number(away);
  const awayMaior = home != null && away != null && Number(away) > Number(home);

  return (
    <div className="py-2">
      <div className="flex items-center justify-between text-sm mb-1">
        <span className={`font-mono font-bold w-14 text-left ${homeMaior ? 'text-emerald-400' : 'text-slate-300'}`}>
          {formatarMetrica(home, formato)}
        </span>
        <span className="text-[11px] text-slate-500 uppercase tracking-wide text-center flex-1">{rotulo}</span>
        <span className={`font-mono font-bold w-14 text-right ${awayMaior ? 'text-emerald-400' : 'text-slate-300'}`}>
          {formatarMetrica(away, formato)}
        </span>
      </div>
      <div className="flex h-1.5 rounded-full overflow-hidden bg-slate-900">
        <div className="bg-emerald-500/70" style={{ width: `${pctHome}%` }} />
        <div className="bg-slate-600" style={{ width: `${100 - pctHome}%` }} />
      </div>
    </div>
  );
}

function AbaEstatisticasJogo({ carregando, dados, home, away }) {
  if (carregando) {
    return (
      <div className="flex items-center justify-center py-16 text-slate-500 gap-2 text-sm">
        <Loader2 className="animate-spin" size={20} /> Carregando estatísticas...
      </div>
    );
  }
  if (!dados) {
    return (
      <div className="bg-slate-800 border border-slate-700 rounded-2xl p-8 text-center">
        <p className="text-sm text-slate-500">Sem estatísticas detalhadas do FotMob pra esse jogo ainda.</p>
      </div>
    );
  }

  return (
    <div className="bg-slate-800 border border-slate-700 rounded-2xl p-5">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2 min-w-0">
          <Escudo url={home?.crest_url} tamanho={22} />
          <span className="font-bold text-slate-200 truncate">{home?.name}</span>
        </div>
        <span className="text-[10px] text-slate-600 uppercase shrink-0 px-2">vs</span>
        <div className="flex items-center gap-2 min-w-0 flex-row-reverse text-right">
          <Escudo url={away?.crest_url} tamanho={22} />
          <span className="font-bold text-slate-200 truncate">{away?.name}</span>
        </div>
      </div>
      <div className="divide-y divide-slate-700/40">
        {METRICAS_JOGO.map(m => (
          <LinhaComparativa key={m.campo} rotulo={m.rotulo} home={dados.homeStats?.[m.campo]} away={dados.awayStats?.[m.campo]} formato={m.formato} />
        ))}
      </div>
    </div>
  );
}

// --- Aba "Jogadores" (match_player_stats_fotmob) ---

function AbaJogadores({ carregando, jogadores, homeTeamId, home, away }) {
  if (carregando) {
    return (
      <div className="flex items-center justify-center py-16 text-slate-500 gap-2 text-sm">
        <Loader2 className="animate-spin" size={20} /> Carregando jogadores...
      </div>
    );
  }
  if (!jogadores || jogadores.length === 0) {
    return (
      <div className="bg-slate-800 border border-slate-700 rounded-2xl p-8 text-center">
        <p className="text-sm text-slate-500">Sem estatísticas de jogador do FotMob pra esse jogo ainda.</p>
      </div>
    );
  }

  const ordenados = [...jogadores].sort((a, b) => (b.rating ?? 0) - (a.rating ?? 0));

  return (
    <div className="bg-slate-800 border border-slate-700 rounded-2xl overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="bg-slate-900 text-slate-400 text-[10px] uppercase tracking-wider">
              <th className="p-3">Jogador</th>
              <th className="p-3">Time</th>
              <th className="p-3 text-right">Min</th>
              <th className="p-3 text-right">Gols</th>
              <th className="p-3 text-right">Assist.</th>
              <th className="p-3 text-right">Nota</th>
              <th className="p-3 text-right">xG</th>
              <th className="p-3 text-right">xA</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-700/50">
            {ordenados.map(j => {
              const timeMandante = j.team_id === homeTeamId;
              const time = timeMandante ? home : away;
              return (
                <tr key={j.id} className="hover:bg-slate-700/20">
                  <td className="p-3 font-semibold text-slate-200">
                    <div className="flex items-center gap-2">
                      {j.players?.photo_url
                        ? <img src={j.players.photo_url} alt="" className="w-7 h-7 rounded-full object-cover bg-slate-900 border border-slate-700 shrink-0" />
                        : <UserRound size={16} className="text-slate-700 shrink-0" />}
                      <span className="truncate">{j.player_name}</span>
                    </div>
                  </td>
                  <td className="p-3 text-slate-400">
                    <div className="flex items-center gap-1.5">
                      <Escudo url={time?.crest_url} tamanho={14} />
                      <span className="truncate max-w-[8rem]">{time?.name}</span>
                    </div>
                  </td>
                  <td className="p-3 text-right text-slate-400 font-mono">{j.minutes_played ?? '—'}</td>
                  <td className="p-3 text-right text-slate-300 font-mono">{j.goals ?? 0}</td>
                  <td className="p-3 text-right text-slate-300 font-mono">{j.assists ?? 0}</td>
                  <td className="p-3 text-right font-mono font-bold">
                    {j.rating != null ? (
                      <span className={j.rating >= 7 ? 'text-emerald-400' : j.rating < 6 ? 'text-red-400' : 'text-slate-300'}>
                        {Number(j.rating).toFixed(2)}
                      </span>
                    ) : '—'}
                  </td>
                  <td className="p-3 text-right text-slate-400 font-mono">{j.xg != null ? Number(j.xg).toFixed(2) : '—'}</td>
                  <td className="p-3 text-right text-slate-400 font-mono">{j.xa != null ? Number(j.xa).toFixed(2) : '—'}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
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

  const [abaAtiva, setAbaAtiva] = useState('geral');

  // Dados sob demanda — null = ainda não buscado (aba nunca aberta).
  const [statsFotmob, setStatsFotmob] = useState(null);
  const [statsFotmobCarregando, setStatsFotmobCarregando] = useState(false);
  const [jogadoresFotmob, setJogadoresFotmob] = useState(null);
  const [jogadoresFotmobCarregando, setJogadoresFotmobCarregando] = useState(false);

  useEffect(() => {
    if (!supabaseAtivo) return;
    (async () => {
      setCarregando(true);
      setErro('');
      const { data: j, error: erroJogo } = await supabase
        .from('matches')
        .select('id, match_date, league_id, home_team_id, away_team_id, home_goals, away_goals, status, leagues(name, external_id), home:teams!matches_home_team_id_fkey(id,name,crest_url), away:teams!matches_away_team_id_fkey(id,name,crest_url)')
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

  // Troca de jogo (matchId muda) invalida o cache das abas sob demanda.
  useEffect(() => {
    setAbaAtiva('geral');
    setStatsFotmob(null);
    setJogadoresFotmob(null);
  }, [matchId]);

  // Aba "Estatísticas do Jogo": só consulta na primeira vez que a aba é aberta.
  useEffect(() => {
    if (abaAtiva !== 'estatisticas' || !jogo || statsFotmob !== null) return;
    setStatsFotmobCarregando(true);
    supabase
      .from('match_stats_fotmob')
      .select('team_id,' + METRICAS_JOGO.map(m => m.campo).join(','))
      .eq('match_id', jogo.id)
      .then(({ data }) => {
        const homeStats = (data || []).find(s => s.team_id === jogo.home_team_id) || null;
        const awayStats = (data || []).find(s => s.team_id === jogo.away_team_id) || null;
        setStatsFotmob((homeStats || awayStats) ? { homeStats, awayStats } : false);
        setStatsFotmobCarregando(false);
      });
  }, [abaAtiva, jogo, statsFotmob]);

  // Aba "Jogadores": idem, só consulta na primeira vez que a aba é aberta.
  useEffect(() => {
    if (abaAtiva !== 'jogadores' || !jogo || jogadoresFotmob !== null) return;
    setJogadoresFotmobCarregando(true);
    supabase
      .from('match_player_stats_fotmob')
      .select('id, team_id, player_name, minutes_played, goals, assists, rating, xg, xa, players(photo_url)')
      .eq('match_id', jogo.id)
      .then(({ data }) => {
        setJogadoresFotmob(data || []);
        setJogadoresFotmobCarregando(false);
      });
  }, [abaAtiva, jogo, jogadoresFotmob]);

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
      <div className="flex items-center justify-between mb-4">
        <Link to="/eventos" className="flex items-center gap-1.5 text-slate-400 hover:text-slate-200 text-sm w-fit">
          <ArrowLeft size={16} /> Voltar
        </Link>
        <Link
          to={`/estatisticas/${jogo.id}`}
          className="flex items-center gap-1.5 bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 text-xs font-bold px-3 py-1.5 rounded-lg transition-colors"
        >
          <TrendingUp size={14} /> Análise estatística (apostas)
        </Link>
      </div>

      <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 mb-4">
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
      </div>

      <div className="flex gap-1 mb-4 bg-slate-800 border border-slate-700 rounded-2xl p-1.5 w-fit">
        {ABAS.map(aba => {
          const Icone = aba.icone;
          return (
            <button
              key={aba.valor}
              onClick={() => {
                setAbaAtiva(aba.valor);
                // Seta "carregando" já no clique (não só no useEffect) pra não
                // piscar a mensagem de "sem dados" por um frame antes da busca
                // de fato começar.
                if (aba.valor === 'estatisticas' && statsFotmob === null) setStatsFotmobCarregando(true);
                if (aba.valor === 'jogadores' && jogadoresFotmob === null) setJogadoresFotmobCarregando(true);
              }}
              className={`flex items-center gap-1.5 px-3.5 py-2 rounded-xl text-sm font-bold transition-colors ${
                abaAtiva === aba.valor ? 'bg-emerald-500/20 text-emerald-400' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              <Icone size={15} /> {aba.rotulo}
            </button>
          );
        })}
      </div>

      {abaAtiva === 'geral' && (
        <>
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-4">
            <div className="lg:col-span-2 bg-slate-800 border border-slate-700 rounded-2xl p-6">
              <div className="flex items-center justify-center gap-2">
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

            <div className="flex flex-col gap-4">
              <WidgetOdds matchId={jogo.id} />
              {jogo.leagues?.external_id === 'BSA' && <WidgetOddsTheOddsAPI />}
            </div>
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
        </>
      )}

      {abaAtiva === 'estatisticas' && (
        <AbaEstatisticasJogo
          carregando={statsFotmobCarregando}
          dados={statsFotmob || null}
          home={jogo.home}
          away={jogo.away}
        />
      )}

      {abaAtiva === 'jogadores' && (
        <AbaJogadores
          carregando={jogadoresFotmobCarregando}
          jogadores={jogadoresFotmob}
          homeTeamId={jogo.home_team_id}
          home={jogo.home}
          away={jogo.away}
        />
      )}
    </div>
  );
}
