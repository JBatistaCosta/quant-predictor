// src/pages/JogadorDetalhe.jsx — rota /jogadores/:id
// "Quantificar desempenho ao longo do tempo" usando o que JÁ está no banco
// (match_player_stats_fotmob, um registro por jogo) — sem nenhuma chamada
// nova ao FotMob. NÃO inclui heatmap/traits de percentil (isso vive num
// endpoint por-JOGADOR separado do FotMob, ainda não importado — decisão
// consciente de escopo, ver CONTEXTO_PROJETO.md).
import React, { useState, useEffect, useMemo } from 'react';
import { useParams, Link } from 'react-router-dom';
import { ArrowLeft, AlertTriangle, Shield, Loader2, UserRound, Landmark, Calendar } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';

const OPCOES_N = [10, 20, 40];

function formatarValorMercado(v) {
  if (v == null) return '—';
  if (v >= 1_000_000) return `€${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `€${(v / 1_000).toFixed(0)}K`;
  return `€${v}`;
}

function corNota(nota) {
  if (nota == null) return 'bg-slate-700';
  if (nota >= 7) return 'bg-emerald-500';
  if (nota < 6) return 'bg-red-500';
  return 'bg-slate-500';
}

function Escudo({ url, tamanho = 18 }) {
  return url
    ? <img src={url} alt="" className="object-contain shrink-0" style={{ width: tamanho, height: tamanho }} />
    : <Shield size={tamanho * 0.8} className="text-slate-700 shrink-0" />;
}

// Barra vertical por partida — altura proporcional à nota (escala 0-10).
// Cor pela mesma convenção já usada em AnaliseHistorica (>=7 verde, <6
// vermelho). Ordem cronológica esquerda->direita (mais antigo primeiro),
// leitura natural de "evolução no tempo".
function GraficoForma({ partidas }) {
  const ALTURA_MAX = 64;
  return (
    <div>
      <div className="flex items-end gap-1 h-16 overflow-x-auto pb-1">
        {partidas.map(p => {
          const nota = p.rating != null ? Number(p.rating) : null;
          const altura = nota != null ? Math.max(4, (nota / 10) * ALTURA_MAX) : 4;
          const adversario = p.mandante ? p.away?.name : p.home?.name;
          const titulo = `${p.matchDate?.slice(0, 10)} vs ${adversario}${nota != null ? ` — nota ${nota.toFixed(2)}` : ' — não utilizado'}`;
          return (
            <div
              key={p.id}
              title={titulo}
              className={`w-2.5 shrink-0 rounded-t ${corNota(nota)} ${nota == null ? 'opacity-30' : ''}`}
              style={{ height: altura }}
            />
          );
        })}
      </div>
      <div className="flex items-center gap-3 mt-2 text-[10px] text-slate-500">
        <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-sm bg-emerald-500 inline-block" /> Nota ≥ 7</span>
        <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-sm bg-slate-500 inline-block" /> Nota 6-7</span>
        <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-sm bg-red-500 inline-block" /> Nota &lt; 6</span>
        <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-sm bg-slate-700 opacity-30 inline-block" /> Não utilizado</span>
      </div>
    </div>
  );
}

export default function JogadorDetalhe() {
  const { id } = useParams();
  const [jogador, setJogador] = useState(null);
  const [partidas, setPartidas] = useState([]);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState('');
  const [n, setN] = useState(20);

  useEffect(() => {
    if (!supabaseAtivo) { setCarregando(false); return; }
    (async () => {
      setCarregando(true);
      setErro('');

      const { data: j, error: erroJogador } = await supabase
        .from('players')
        .select('id, name, photo_url, age, country_name, country_code, market_value, last_team:teams!players_last_team_id_fkey(id,name,crest_url,equipes!equipes_pipeline_team_id_fkey(id))')
        .eq('id', id)
        .single();

      if (erroJogador || !j) { setErro('Jogador não encontrado.'); setCarregando(false); return; }
      setJogador(j);

      const { data: hist, error: erroHist } = await supabase
        .from('match_player_stats_fotmob')
        .select('id, team_id, rating, minutes_played, goals, assists, xg, xa, xgot, total_shots, chances_created, matches(id, match_date, leagues(name), home:teams!matches_home_team_id_fkey(id,name,crest_url), away:teams!matches_away_team_id_fkey(id,name,crest_url))')
        .eq('player_id', id)
        .order('match_date', { foreignTable: 'matches', ascending: false })
        .limit(n);

      if (erroHist) { setErro(erroHist.message); setCarregando(false); return; }

      const normalizadas = (hist || [])
        .filter(p => p.matches)
        .map(p => ({
          id: p.id,
          matchId: p.matches.id,
          matchDate: p.matches.match_date,
          liga: p.matches.leagues?.name,
          mandante: p.team_id === p.matches.home?.id,
          home: p.matches.home,
          away: p.matches.away,
          rating: p.rating,
          minutesPlayed: p.minutes_played,
          goals: p.goals,
          assists: p.assists,
          xg: p.xg,
          xa: p.xa,
          xgot: p.xgot,
          totalShots: p.total_shots,
          chancesCreated: p.chances_created,
        }));

      setPartidas(normalizadas);
      setCarregando(false);
    })();
  }, [id, n]);

  const resumo = useMemo(() => {
    const comMinutos = partidas.filter(p => p.minutesPlayed != null && p.minutesPlayed > 0);
    const comNota = comMinutos.filter(p => p.rating != null);
    const media = (arr, campo) => arr.length > 0 ? arr.reduce((s, p) => s + (Number(p[campo]) || 0), 0) / arr.length : null;

    // G-xG (gols menos xG acumulado) só faz sentido comparando gols e xG na
    // MESMA amostra de jogos (onde o FotMob tem os dois preenchidos) — sobre
    // várias partidas, não jogo a jogo, senão é ruído de amostra pequena, não
    // sinal real de "finalizador acima/abaixo do esperado" (mesma disciplina
    // estatística já aplicada no resto do projeto).
    const comXg = comMinutos.filter(p => p.xg != null);
    const golsAmostraXg = comXg.reduce((s, p) => s + (p.goals || 0), 0);
    const xgAcumulado = comXg.reduce((s, p) => s + (Number(p.xg) || 0), 0);

    return {
      jogos: comMinutos.length,
      notaMedia: media(comNota, 'rating'),
      gols: comMinutos.reduce((s, p) => s + (p.goals || 0), 0),
      assistencias: comMinutos.reduce((s, p) => s + (p.assists || 0), 0),
      xgMedio: media(comXg, 'xg'),
      xaMedio: media(comMinutos.filter(p => p.xa != null), 'xa'),
      xgotMedio: media(comMinutos.filter(p => p.xgot != null), 'xgot'),
      minutosMedios: media(comMinutos, 'minutesPlayed'),
      gMenosXg: comXg.length > 0 ? golsAmostraXg - xgAcumulado : null,
      gMenosXgAmostra: comXg.length,
    };
  }, [partidas]);

  if (!supabaseAtivo) {
    return (
      <div className="max-w-4xl mx-auto bg-slate-800 border border-red-500/30 rounded-2xl p-6 text-center">
        <AlertTriangle className="text-red-400 mx-auto mb-2" size={28} />
        <p className="text-slate-300">Supabase não configurado.</p>
      </div>
    );
  }

  if (carregando && !jogador) {
    return (
      <div className="max-w-4xl mx-auto flex items-center justify-center py-16 text-slate-500 gap-2 text-sm">
        <Loader2 className="animate-spin" size={20} /> Carregando...
      </div>
    );
  }

  if (erro || !jogador) {
    return (
      <div className="max-w-4xl mx-auto bg-slate-800 border border-red-500/30 rounded-2xl p-6 text-center">
        <AlertTriangle className="text-red-400 mx-auto mb-2" size={28} />
        <p className="text-slate-300">{erro || 'Jogador não encontrado.'}</p>
        <Link to="/jogadores" className="text-emerald-400 text-sm hover:underline mt-3 inline-block">← Voltar pra Jogadores</Link>
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto">
      <Link to="/jogadores" className="flex items-center gap-1.5 text-slate-400 hover:text-slate-200 text-sm w-fit mb-4">
        <ArrowLeft size={16} /> Voltar
      </Link>

      <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 mb-4 flex items-center gap-4 flex-wrap">
        {jogador.photo_url
          ? <img src={jogador.photo_url} alt="" className="w-16 h-16 rounded-full object-cover bg-slate-900 border border-slate-700 shrink-0" />
          : <div className="w-16 h-16 rounded-full bg-slate-900 border border-slate-700 flex items-center justify-center shrink-0"><UserRound size={28} className="text-slate-600" /></div>}
        <div className="flex-1 min-w-[12rem]">
          <h1 className="text-xl font-extrabold text-slate-100">{jogador.name}</h1>
          <div className="flex items-center gap-3 text-sm text-slate-400 mt-1 flex-wrap">
            {jogador.last_team && (
              // teams.id (pipeline) != equipes.id (o que /times/:id espera) — só
              // linka quando o vínculo equipes.pipeline_team_id existir de verdade.
              jogador.last_team.equipes?.[0]?.id ? (
                <Link to={`/times/${jogador.last_team.equipes[0].id}`} className="flex items-center gap-1.5 hover:text-emerald-400 hover:underline">
                  <Escudo url={jogador.last_team.crest_url} /> {jogador.last_team.name}
                </Link>
              ) : (
                <span className="flex items-center gap-1.5">
                  <Escudo url={jogador.last_team.crest_url} /> {jogador.last_team.name}
                </span>
              )
            )}
            {jogador.country_name && <span>{jogador.country_name}</span>}
            {jogador.age != null && <span>{jogador.age} anos</span>}
          </div>
        </div>
        <div className="bg-slate-900 rounded-xl px-4 py-2 text-center shrink-0">
          <div className="text-[10px] text-slate-500 uppercase flex items-center gap-1 justify-center"><Landmark size={11} /> Valor de mercado</div>
          <div className="text-sm font-bold text-emerald-400">{formatarValorMercado(jogador.market_value)}</div>
        </div>
      </div>

      <div className="bg-slate-800 border border-slate-700 rounded-2xl p-5 mb-4">
        <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
          <h2 className="text-sm font-bold text-slate-300 uppercase tracking-wider flex items-center gap-2">
            <Calendar className="text-emerald-400" size={16} /> Forma recente
          </h2>
          <div className="flex items-center gap-1">
            {OPCOES_N.map(opcao => (
              <button
                key={opcao}
                onClick={() => setN(opcao)}
                className={`px-2.5 py-1 rounded-lg text-xs font-bold ${n === opcao ? 'bg-emerald-500/20 text-emerald-400' : 'bg-slate-900 text-slate-500 hover:text-slate-300'}`}
              >
                {opcao}
              </button>
            ))}
          </div>
        </div>

        {carregando ? (
          <div className="flex items-center justify-center py-8 text-slate-500 gap-2 text-xs">
            <Loader2 className="animate-spin" size={16} /> Carregando...
          </div>
        ) : partidas.length === 0 ? (
          <p className="text-sm text-slate-600">Sem partidas registradas via FotMob pra esse jogador ainda.</p>
        ) : (
          <>
            <GraficoForma partidas={[...partidas].reverse()} />

            <div className="grid grid-cols-4 sm:grid-cols-8 gap-2 text-center mt-5">
              <div className="bg-slate-900 rounded-lg py-2">
                <div className="text-[10px] text-slate-500 uppercase">Jogos</div>
                <div className="text-sm font-bold text-slate-200">{resumo.jogos}</div>
              </div>
              <div className="bg-slate-900 rounded-lg py-2">
                <div className="text-[10px] text-slate-500 uppercase">Nota média</div>
                <div className="text-sm font-bold text-slate-200">{resumo.notaMedia?.toFixed(2) ?? '—'}</div>
              </div>
              <div className="bg-slate-900 rounded-lg py-2">
                <div className="text-[10px] text-slate-500 uppercase">Gols</div>
                <div className="text-sm font-bold text-emerald-400">{resumo.gols}</div>
              </div>
              <div className="bg-slate-900 rounded-lg py-2">
                <div className="text-[10px] text-slate-500 uppercase">Assist.</div>
                <div className="text-sm font-bold text-emerald-400">{resumo.assistencias}</div>
              </div>
              <div className="bg-slate-900 rounded-lg py-2">
                <div className="text-[10px] text-slate-500 uppercase">xG médio</div>
                <div className="text-sm font-bold text-slate-300">{resumo.xgMedio?.toFixed(2) ?? '—'}</div>
              </div>
              <div className="bg-slate-900 rounded-lg py-2">
                <div className="text-[10px] text-slate-500 uppercase">xA médio</div>
                <div className="text-sm font-bold text-slate-300">{resumo.xaMedio?.toFixed(2) ?? '—'}</div>
              </div>
              <div className="bg-slate-900 rounded-lg py-2">
                <div className="text-[10px] text-slate-500 uppercase">xGOT médio</div>
                <div className="text-sm font-bold text-slate-300">{resumo.xgotMedio?.toFixed(2) ?? '—'}</div>
              </div>
              <div className="bg-slate-900 rounded-lg py-2" title={resumo.gMenosXg != null ? `Gols - xG acumulado nos ${resumo.gMenosXgAmostra} jogos com xG registrado` : 'Sem jogos com xG registrado nessa janela'}>
                <div className="text-[10px] text-slate-500 uppercase">G-xG</div>
                <div className={`text-sm font-bold ${resumo.gMenosXg == null ? 'text-slate-300' : resumo.gMenosXg > 0.5 ? 'text-emerald-400' : resumo.gMenosXg < -0.5 ? 'text-red-400' : 'text-slate-300'}`}>
                  {resumo.gMenosXg != null ? (resumo.gMenosXg > 0 ? '+' : '') + resumo.gMenosXg.toFixed(2) : '—'}
                </div>
              </div>
            </div>
          </>
        )}
      </div>

      {!carregando && partidas.length > 0 && (
        <div className="bg-slate-800 border border-slate-700 rounded-2xl overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="bg-slate-900 text-slate-400 text-[10px] uppercase tracking-wider">
                  <th className="p-3">Data</th>
                  <th className="p-3">Confronto</th>
                  <th className="p-3">Liga</th>
                  <th className="p-3 text-right">Min</th>
                  <th className="p-3 text-right">Gols</th>
                  <th className="p-3 text-right">Assist.</th>
                  <th className="p-3 text-right">Nota</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-700/50">
                {partidas.map(p => (
                  <tr key={p.id} className="hover:bg-slate-700/20">
                    <td className="p-3 text-slate-500 text-xs whitespace-nowrap">{p.matchDate?.slice(0, 10)}</td>
                    <td className="p-3 text-slate-300">
                      <Link to={`/historico/${p.matchId}`} className="flex items-center gap-1.5 hover:text-emerald-400 hover:underline w-fit">
                        <Escudo url={p.home?.crest_url} tamanho={14} />
                        <span className="truncate max-w-[6rem]">{p.home?.name}</span>
                        <span className="text-slate-600">x</span>
                        <span className="truncate max-w-[6rem]">{p.away?.name}</span>
                        <Escudo url={p.away?.crest_url} tamanho={14} />
                      </Link>
                    </td>
                    <td className="p-3 text-slate-500 text-xs truncate max-w-[8rem]">{p.liga}</td>
                    <td className="p-3 text-right text-slate-400 font-mono">{p.minutesPlayed ?? '—'}</td>
                    <td className="p-3 text-right text-slate-300 font-mono">{p.goals ?? 0}</td>
                    <td className="p-3 text-right text-slate-300 font-mono">{p.assists ?? 0}</td>
                    <td className="p-3 text-right font-mono font-bold">
                      {p.rating != null
                        ? <span className={p.rating >= 7 ? 'text-emerald-400' : p.rating < 6 ? 'text-red-400' : 'text-slate-300'}>{Number(p.rating).toFixed(2)}</span>
                        : <span className="text-slate-700">—</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
