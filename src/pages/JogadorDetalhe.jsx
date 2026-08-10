// src/pages/JogadorDetalhe.jsx — rota /jogadores/:id
// "Quantificar desempenho ao longo do tempo" usando o que JÁ está no banco
// (match_player_stats_fotmob, um registro por jogo) — sem nenhuma chamada
// nova ao FotMob. NÃO inclui heatmap/traits de percentil (isso vive num
// endpoint por-JOGADOR separado do FotMob, ainda não importado — decisão
// consciente de escopo, ver CONTEXTO_PROJETO.md).
import React, { useState, useEffect, useMemo, useRef } from 'react';
import { useParams, Link } from 'react-router-dom';
import { ArrowLeft, AlertTriangle, Shield, Loader2, UserRound, Landmark, Calendar, Zap, TrendingUp, Trophy, Briefcase, RefreshCw, Ruler, Footprints, FileClock } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';
import { apiUrl } from '../utils/apiUrl';

const OPCOES_N = [10, 20, 40];

function calcularIdade(dateStr) {
  if (!dateStr) return null;
  const hoje = new Date();
  const nasc = new Date(dateStr);
  let idade = hoje.getFullYear() - nasc.getFullYear();
  if (hoje.getMonth() < nasc.getMonth() || (hoje.getMonth() === nasc.getMonth() && hoje.getDate() < nasc.getDate())) idade--;
  return idade;
}

function formatarDataNasc(dateStr) {
  if (!dateStr) return null;
  const [y, m, d] = dateStr.split('-');
  return `${d}/${m}/${y}`;
}

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

// --- Linha do tempo de valor de mercado ---
// Série única (sem legenda — o título já nomeia), linha fina de 2px com
// ponta arredondada, marcadores nos extremos e no ponto sob o cursor,
// crosshair+tooltip no hover (spec de interação da skill de dataviz).
// Eixo X por DATA real (não por índice), pra respeitar o espaçamento
// real entre os pontos (a fonte não é uniforme: ~2 pontos/ano).
function GraficoValorMercado({ pontos }) {
  const svgRef = useRef(null);
  const [hover, setHover] = useState(null); // { x, y, ponto }
  const W = 640, H = 160, PAD_L = 44, PAD_R = 12, PAD_T = 14, PAD_B = 22;

  const ordenados = useMemo(
    () => [...pontos].filter(p => p.value_eur != null).sort((a, b) => new Date(a.value_date) - new Date(b.value_date)),
    [pontos]
  );

  if (ordenados.length < 2) {
    return <p className="text-xs text-slate-600">Histórico insuficiente pra desenhar a linha do tempo.</p>;
  }

  const datas = ordenados.map(p => new Date(p.value_date).getTime());
  const valores = ordenados.map(p => Number(p.value_eur));
  const minData = Math.min(...datas), maxData = Math.max(...datas);
  const minValor = 0, maxValor = Math.max(...valores) * 1.08;

  const escalaX = t => PAD_L + ((t - minData) / (maxData - minData || 1)) * (W - PAD_L - PAD_R);
  const escalaY = v => H - PAD_B - ((v - minValor) / (maxValor - minValor || 1)) * (H - PAD_T - PAD_B);

  const pathD = ordenados.map((p, i) => `${i === 0 ? 'M' : 'L'} ${escalaX(new Date(p.value_date).getTime()).toFixed(1)} ${escalaY(Number(p.value_eur)).toFixed(1)}`).join(' ');

  const linhasGrade = 3;
  const gradeValores = Array.from({ length: linhasGrade + 1 }, (_, i) => (maxValor / linhasGrade) * i);

  const moverMouse = (e) => {
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const xRelativo = ((e.clientX - rect.left) / rect.width) * W;
    // Ponto mais próximo por posição X (não por índice — respeita o espaçamento real)
    let melhor = ordenados[0], menorDist = Infinity;
    for (const p of ordenados) {
      const dist = Math.abs(escalaX(new Date(p.value_date).getTime()) - xRelativo);
      if (dist < menorDist) { menorDist = dist; melhor = p; }
    }
    setHover({ x: escalaX(new Date(melhor.value_date).getTime()), y: escalaY(Number(melhor.value_eur)), ponto: melhor });
  };

  const primeiro = ordenados[0], ultimo = ordenados[ordenados.length - 1];

  return (
    <div>
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        className="w-full h-auto cursor-crosshair"
        onMouseMove={moverMouse}
        onMouseLeave={() => setHover(null)}
      >
        {gradeValores.map((v, i) => (
          <g key={i}>
            <line x1={PAD_L} x2={W - PAD_R} y1={escalaY(v)} y2={escalaY(v)} stroke="#334155" strokeWidth="1" opacity="0.5" />
            <text x={PAD_L - 6} y={escalaY(v) + 3} textAnchor="end" fontSize="9" fill="#64748b">{formatarValorMercado(v)}</text>
          </g>
        ))}

        <path d={pathD} fill="none" stroke="#34d399" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />

        {/* Marcadores nos extremos (rótulo direto seletivo, não em todo ponto) */}
        <circle cx={escalaX(new Date(primeiro.value_date).getTime())} cy={escalaY(Number(primeiro.value_eur))} r="3" fill="#34d399" />
        <circle cx={escalaX(new Date(ultimo.value_date).getTime())} cy={escalaY(Number(ultimo.value_eur))} r="3" fill="#34d399" />

        {hover && (
          <g>
            <line x1={hover.x} x2={hover.x} y1={PAD_T} y2={H - PAD_B} stroke="#64748b" strokeWidth="1" strokeDasharray="3,3" />
            <circle cx={hover.x} cy={hover.y} r="4" fill="#34d399" stroke="#0f172a" strokeWidth="1.5" />
          </g>
        )}
      </svg>
      {hover ? (
        <div className="text-center text-xs mt-1">
          <span className="text-slate-500">{new Date(hover.ponto.value_date).toLocaleDateString('pt-BR')}</span>
          {' · '}
          <span className="font-bold text-emerald-400">{formatarValorMercado(Number(hover.ponto.value_eur))}</span>
          {hover.ponto.team_name && <span className="text-slate-600"> · {hover.ponto.team_name}</span>}
        </div>
      ) : (
        <div className="flex items-center justify-between text-[10px] text-slate-600 mt-1">
          <span>{new Date(primeiro.value_date).toLocaleDateString('pt-BR')} · {formatarValorMercado(Number(primeiro.value_eur))}</span>
          <span>{new Date(ultimo.value_date).toLocaleDateString('pt-BR')} · {formatarValorMercado(Number(ultimo.value_eur))}</span>
        </div>
      )}
    </div>
  );
}

export default function JogadorDetalhe() {
  const { id } = useParams();
  const [jogador, setJogador] = useState(null);
  const [ratingElo, setRatingElo] = useState(null);
  const [partidas, setPartidas] = useState([]);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState('');
  const [n, setN] = useState(20);

  // Perfil avançado (FotMob playerData: valor de mercado histórico, carreira,
  // títulos, altura/pé/contrato/traits) — sincronizado SOB DEMANDA (botão),
  // não carregado automaticamente: é 1 chamada externa por jogador, cara
  // demais pra disparar sozinha toda vez que a página abre.
  const [valorMercadoHist, setValorMercadoHist] = useState([]);
  const [carreiraHist, setCarreiraHist] = useState([]);
  const [titulos, setTitulos] = useState([]);
  const [detalhesAvancados, setDetalhesAvancados] = useState(null);
  const [sincronizando, setSincronizando] = useState(false);
  const [erroSinc, setErroSinc] = useState('');
  const [msgSinc, setMsgSinc] = useState('');

  useEffect(() => {
    if (!supabaseAtivo) { setCarregando(false); return; }
    (async () => {
      setCarregando(true);
      setErro('');

      const { data: j, error: erroJogador } = await supabase
        .from('players')
        .select('id, name, photo_url, age, birth_date, country_name, country_code, market_value, last_team:teams!players_last_team_id_fkey(id,name,crest_url,equipes!equipes_pipeline_team_id_fkey(id))')
        .eq('id', id)
        .single();

      if (erroJogador || !j) { setErro('Jogador não encontrado.'); setCarregando(false); return; }
      setJogador(j);

      // Rating Elo-like próprio (player_ratings, ver api/model-maintenance.js
      // ?tarefa=player-elo) — ainda não calibrado de verdade (chute inicial
      // razoável, mesmo status do XI do Dixon-Coles), null até a tarefa rodar
      // pra esse jogador (partidas recentes o suficiente pra entrar no lote).
      const { data: elo } = await supabase.from('player_ratings').select('rating, n_partidas').eq('player_id', id).maybeSingle();
      setRatingElo(elo || null);

      // Perfil avançado — busca o que já estiver salvo (de uma sincronização
      // anterior); se nunca foi sincronizado, as listas ficam vazias e o
      // botão "Sincronizar" aparece.
      const [{ data: mv }, { data: carreira }, { data: trof }, { data: det }] = await Promise.all([
        supabase.from('player_market_value_history').select('value_date, value_eur, team_name').eq('player_id', id).order('value_date'),
        supabase.from('player_career_history_fotmob').select('team_name, start_date, end_date, active, transfer_type, appearances, goals, assists').eq('player_id', id).order('start_date', { ascending: false }),
        supabase.from('player_trophies_fotmob').select('team_name, league_name, season, result').eq('player_id', id),
        supabase.from('player_details_fotmob').select('*').eq('player_id', id).maybeSingle(),
      ]);
      setValorMercadoHist(mv || []);
      setCarreiraHist(carreira || []);
      setTitulos(trof || []);
      setDetalhesAvancados(det || null);

      const { data: hist, error: erroHist } = await supabase
        .from('match_player_stats_fotmob')
        .select('id, team_id, rating, minutes_played, goals, assists, xg, xa, total_shots, chances_created, matches(id, match_date, leagues(name), home:teams!matches_home_team_id_fkey(id,name,crest_url), away:teams!matches_away_team_id_fkey(id,name,crest_url))')
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
    return {
      jogos: comMinutos.length,
      notaMedia: media(comNota, 'rating'),
      gols: comMinutos.reduce((s, p) => s + (p.goals || 0), 0),
      assistencias: comMinutos.reduce((s, p) => s + (p.assists || 0), 0),
      xgMedio: media(comMinutos.filter(p => p.xg != null), 'xg'),
      xaMedio: media(comMinutos.filter(p => p.xa != null), 'xa'),
      minutosMedios: media(comMinutos, 'minutesPlayed'),
    };
  }, [partidas]);

  const sincronizarPerfilAvancado = async () => {
    setSincronizando(true); setErroSinc(''); setMsgSinc('');
    try {
      const resp = await fetch(apiUrl(`/api/model-maintenance?tarefa=jogador-perfil&player_id=${id}`));
      const dados = await resp.json();
      if (!resp.ok) throw new Error(dados.error?.message || 'Falha ao sincronizar.');
      const [{ data: mv }, { data: carreira }, { data: trof }, { data: det }, { data: jogadorAtualizado }] = await Promise.all([
        supabase.from('player_market_value_history').select('value_date, value_eur, team_name').eq('player_id', id).order('value_date'),
        supabase.from('player_career_history_fotmob').select('team_name, start_date, end_date, active, transfer_type, appearances, goals, assists').eq('player_id', id).order('start_date', { ascending: false }),
        supabase.from('player_trophies_fotmob').select('team_name, league_name, season, result').eq('player_id', id),
        supabase.from('player_details_fotmob').select('*').eq('player_id', id).maybeSingle(),
        supabase.from('players').select('id, name, photo_url, age, birth_date, country_name, country_code, market_value, last_team:teams!players_last_team_id_fkey(id,name,crest_url,equipes!equipes_pipeline_team_id_fkey(id))').eq('id', id).single(),
      ]);
      setValorMercadoHist(mv || []);
      setCarreiraHist(carreira || []);
      setTitulos(trof || []);
      setDetalhesAvancados(det || null);
      if (jogadorAtualizado) setJogador(jogadorAtualizado);
      setMsgSinc(`Sincronizado: ${dados.pontos_valor_mercado} pontos de valor, ${dados.clubes_carreira} clubes na carreira, ${dados.titulos} títulos${dados.birth_date ? ` · nascimento ${formatarDataNasc(dados.birth_date)}` : ''}.`);
    } catch (e) {
      setErroSinc(e.message);
    } finally {
      setSincronizando(false);
    }
  };

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
            {(jogador.birth_date || jogador.age != null) && (
              <span>
                {jogador.birth_date
                  ? `${formatarDataNasc(jogador.birth_date)} · ${calcularIdade(jogador.birth_date)} anos`
                  : `${jogador.age} anos`}
              </span>
            )}
          </div>
        </div>
        <div className="bg-slate-900 rounded-xl px-4 py-2 text-center shrink-0">
          <div className="text-[10px] text-slate-500 uppercase flex items-center gap-1 justify-center"><Landmark size={11} /> Valor de mercado</div>
          <div className="text-sm font-bold text-emerald-400">{formatarValorMercado(jogador.market_value)}</div>
        </div>
        {ratingElo && (
          <div className="bg-slate-900 rounded-xl px-4 py-2 text-center shrink-0" title="Rating Elo-like próprio, pesos ainda não calibrados (ver /modelos)">
            <div className="text-[10px] text-slate-500 uppercase flex items-center gap-1 justify-center"><Zap size={11} /> Rating</div>
            <div className="text-sm font-bold text-amber-400">{Math.round(ratingElo.rating)}</div>
            <div className="text-[9px] text-slate-600">{ratingElo.n_partidas} jogos</div>
          </div>
        )}
      </div>

      <div className="bg-slate-800 border border-slate-700 rounded-2xl p-5 mb-4">
        <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
          <h2 className="text-sm font-bold text-slate-300 uppercase tracking-wider flex items-center gap-2">
            <TrendingUp className="text-emerald-400" size={16} /> Perfil avançado
          </h2>
          <button
            onClick={sincronizarPerfilAvancado}
            disabled={sincronizando}
            className="flex items-center gap-1.5 bg-slate-900 hover:bg-slate-700 text-slate-400 hover:text-slate-200 text-[11px] font-bold px-3 py-1.5 rounded-lg transition-colors disabled:opacity-50"
            title="Busca valor de mercado histórico, carreira, títulos e atributos direto do FotMob (1 chamada externa, ~1-2s)"
          >
            {sincronizando ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
            {sincronizando ? 'Sincronizando...' : 'Sincronizar'}
          </button>
        </div>

        {erroSinc && <p className="text-xs text-red-400 mb-2 flex items-center gap-1"><AlertTriangle size={12} /> {erroSinc}</p>}
        {msgSinc && <p className="text-xs text-emerald-400 mb-2">{msgSinc}</p>}

        {!detalhesAvancados && valorMercadoHist.length === 0 ? (
          <p className="text-xs text-slate-600">Ainda não sincronizado — clique em "Sincronizar" pra buscar valor de mercado histórico, carreira, títulos e atributos direto do FotMob.</p>
        ) : (
          <div className="space-y-4">
            {detalhesAvancados && (
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                <div className="bg-slate-900 rounded-lg p-2.5 text-center">
                  <div className="text-[9px] text-slate-500 uppercase flex items-center gap-1 justify-center"><Ruler size={10} /> Altura</div>
                  <div className="text-xs font-bold text-slate-200">{detalhesAvancados.height_cm ? `${detalhesAvancados.height_cm}cm` : '—'}</div>
                </div>
                <div className="bg-slate-900 rounded-lg p-2.5 text-center">
                  <div className="text-[9px] text-slate-500 uppercase flex items-center gap-1 justify-center"><Footprints size={10} /> Pé</div>
                  <div className="text-xs font-bold text-slate-200 capitalize">{detalhesAvancados.preferred_foot || '—'}</div>
                </div>
                <div className="bg-slate-900 rounded-lg p-2.5 text-center">
                  <div className="text-[9px] text-slate-500 uppercase">Posição</div>
                  <div className="text-xs font-bold text-slate-200">{detalhesAvancados.primary_position || '—'}</div>
                </div>
                <div className="bg-slate-900 rounded-lg p-2.5 text-center">
                  <div className="text-[9px] text-slate-500 uppercase flex items-center gap-1 justify-center"><FileClock size={10} /> Contrato até</div>
                  <div className="text-xs font-bold text-slate-200">{detalhesAvancados.contract_end || '—'}</div>
                </div>
              </div>
            )}

            {valorMercadoHist.length > 0 && (
              <div>
                <span className="text-[10px] uppercase font-bold text-slate-500 block mb-1.5">Valor de mercado (histórico — estimativa de terceiro via FotMob, não é preço real de transferência)</span>
                <GraficoValorMercado pontos={valorMercadoHist} />
              </div>
            )}

            {titulos.length > 0 && (
              <div>
                <span className="text-[10px] uppercase font-bold text-slate-500 block mb-1.5 flex items-center gap-1"><Trophy size={11} /> Títulos ({titulos.length})</span>
                <div className="flex flex-wrap gap-1.5">
                  {titulos.map((t, i) => (
                    <span key={i} className={`text-[10px] px-2 py-1 rounded-full ${t.result === 'won' ? 'bg-amber-500/15 text-amber-400' : 'bg-slate-700/50 text-slate-500'}`}>
                      {t.league_name} {t.season} · {t.team_name}{t.result !== 'won' ? ' (vice)' : ''}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {carreiraHist.length > 0 && (
              <div>
                <span className="text-[10px] uppercase font-bold text-slate-500 block mb-1.5 flex items-center gap-1"><Briefcase size={11} /> Carreira</span>
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-xs">
                    <thead>
                      <tr className="text-slate-500 text-[9px] uppercase">
                        <th className="py-1 pr-2">Clube</th>
                        <th className="py-1 pr-2">Período</th>
                        <th className="py-1 pr-2 text-right">Jogos</th>
                        <th className="py-1 pr-2 text-right">Gols</th>
                        <th className="py-1 text-right">Assist.</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-700/50">
                      {carreiraHist.map((c, i) => (
                        <tr key={i}>
                          <td className="py-1.5 pr-2 text-slate-300">{c.team_name}{c.active && <span className="text-emerald-400 ml-1">●</span>}</td>
                          <td className="py-1.5 pr-2 text-slate-500">{c.start_date} → {c.end_date || 'atual'}</td>
                          <td className="py-1.5 pr-2 text-right text-slate-400 font-mono">{c.appearances ?? '—'}</td>
                          <td className="py-1.5 pr-2 text-right text-slate-400 font-mono">{c.goals ?? '—'}</td>
                          <td className="py-1.5 text-right text-slate-400 font-mono">{c.assists ?? '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        )}
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

            <div className="grid grid-cols-3 sm:grid-cols-6 gap-2 text-center mt-5">
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
