// src/pages/RatingClubes.jsx — rota /ratings
// Painel do Elo interno por clube. Dois modelos de cálculo distintos:
//   - escopo (DB) "global" (scripts/elo_global.py, cron diário): UM único
//     pool de rating, juntando TODAS as partidas de TODAS as competições —
//     a UI organiza esse mesmo rating em 3 níveis (Federação = país,
//     Confederação, Geral = mundial sem filtro), só mudando o FILTRO
//     aplicado sobre os mesmos números, não o cálculo.
//   - escopo (DB) "liga": o modelo antigo, um Elo ISOLADO por competição
//     individual (Brasileirão nunca se cruza com Premier League) — mantido
//     como opção à parte ("Por competição"), é o que dados_historicos.py
//     consome como feature de modelo, não pode mudar de forma.
// Ranking atual + timeline de rating ao longo do tempo pros times
// selecionados na tabela (clique pra incluir/remover, até 6).
import React, { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import { Landmark, Shield, TrendingUp } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';
import { CONFEDERACOES, confederacaoDoPais } from '../utils/confederacoes';

// Nomes de exibição pros códigos de país (ISO 3166 alpha-3) que aparecem em
// leagues.territory_code hoje — cai no próprio código se não tiver entrada
// (nunca quebra pra país novo, só fica menos bonito até alguém adicionar aqui).
const NOME_PAIS = {
  BRA: 'Brasil', ENG: 'Inglaterra', ESP: 'Espanha', FRA: 'França', ITA: 'Itália',
  DEU: 'Alemanha', NED: 'Holanda', POR: 'Portugal', USA: 'Estados Unidos',
};
const nomePais = (codigo) => NOME_PAIS[codigo] || codigo;

// Paleta categórica (passo escuro, validada contra superfície slate-900/800) —
// ordem fixa, nunca ciclada por rank (ver skill de dataviz do projeto).
const PALETA = ['#3987e5', '#008300', '#d55181', '#c98500', '#199e70', '#d95926', '#9085e9', '#e66767'];
const MAX_SELECIONADOS = 6;

async function buscarPaginado(query) {
  const linhas = [];
  let pagina = 0;
  while (true) {
    const { data } = await query.range(pagina * 1000, pagina * 1000 + 999);
    linhas.push(...(data || []));
    if (!data || data.length < 1000) break;
    pagina++;
  }
  return linhas;
}

function escalaLinear(dominio, alcance) {
  const [d0, d1] = dominio;
  const [a0, a1] = alcance;
  const fator = d1 === d0 ? 0 : (a1 - a0) / (d1 - d0);
  return (v) => a0 + (v - d0) * fator;
}

function hojeISO() {
  return new Date().toISOString().slice(0, 10);
}

function deslocarDias(dataISO, dias) {
  const d = new Date(`${dataISO}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + dias);
  return d.toISOString().slice(0, 10);
}

// Reconstrói o ranking "como estava" numa data: pra cada time do roster, pega
// o rating_depois da partida mais recente até essa data (sem partida ainda =
// 1500, valor inicial de todo time no Elo). Filtra direto pelos team_ids do
// roster (em vez de confiar num limite fixo de linhas mais recentes) — no
// escopo "global" (todas as competições juntas), um limite fixo pequeno
// podia ficar todo tomado por partidas de OUTRAS ligas/países acontecendo
// perto da mesma data, sem sequer chegar nos times do roster filtrado.
async function buscarRankingNaData(escopoAtual, ligaIdAtual, dataAlvo, roster) {
  const teamIds = roster.map(r => r.team_id);
  let query = supabase
    .from('team_elo_history')
    .select('team_id, match_date, rating_depois')
    .eq('escopo', escopoAtual)
    .in('team_id', teamIds)
    .lte('match_date', dataAlvo)
    .order('match_date', { ascending: false })
    .limit(teamIds.length * 6 + 50);
  query = escopoAtual === 'liga' ? query.eq('league_id', ligaIdAtual) : query;
  const { data } = await query;

  const maisRecentePorTime = new Map();
  for (const l of data || []) {
    if (!maisRecentePorTime.has(l.team_id)) maisRecentePorTime.set(l.team_id, l);
  }

  return roster
    .map(r => {
      const h = maisRecentePorTime.get(r.team_id);
      return {
        team_id: r.team_id,
        teams: r.teams,
        rating: h ? Number(h.rating_depois) : 1500,
        referencia: h ? h.match_date : null,
      };
    })
    .sort((a, b) => b.rating - a.rating);
}

function EloTimelineChart({ series }) {
  const containerRef = useRef(null);
  const [hover, setHover] = useState(null); // { x, dataAlvo }
  const largura = 760, altura = 300;
  const margem = { topo: 16, baixo: 28, esquerda: 44, direita: 16 };

  const todosPontos = useMemo(() => series.flatMap(s => s.pontos), [series]);

  if (todosPontos.length === 0) {
    return <p className="text-xs text-slate-600 py-10 text-center">Sem histórico suficiente pra montar a timeline.</p>;
  }

  const datasMs = todosPontos.map(p => p.data.getTime());
  const domX = [Math.min(...datasMs), Math.max(...datasMs)];
  const ratings = todosPontos.map(p => p.rating);
  const domYBruto = [Math.min(...ratings), Math.max(...ratings)];
  const folga = Math.max(10, (domYBruto[1] - domYBruto[0]) * 0.08);
  const domY = [Math.floor((domYBruto[0] - folga) / 25) * 25, Math.ceil((domYBruto[1] + folga) / 25) * 25];

  const x = escalaLinear(domX, [margem.esquerda, largura - margem.direita]);
  const y = escalaLinear(domY, [altura - margem.baixo, margem.topo]);

  const tickY = 4;
  const passoY = (domY[1] - domY[0]) / tickY;

  const tickX = 5;
  const passoX = (domX[1] - domX[0]) / tickX;
  const anoFormato = (ms) => new Date(ms).getFullYear();

  const mover = useCallback((e) => {
    const rect = containerRef.current.getBoundingClientRect();
    const mx = ((e.clientX - rect.left) / rect.width) * largura;
    const dataAlvo = domX[0] + ((mx - margem.esquerda) / (largura - margem.esquerda - margem.direita)) * (domX[1] - domX[0]);

    // ponto mais próximo entre todas as séries, pra ancorar o crosshair
    let melhor = null, melhorDist = Infinity;
    for (const p of todosPontos) {
      const dist = Math.abs(p.data.getTime() - dataAlvo);
      if (dist < melhorDist) { melhorDist = dist; melhor = p; }
    }
    if (melhor) setHover({ dataAlvo: melhor.data.getTime() });
  }, [todosPontos, domX]);

  const linhasComValor = useMemo(() => {
    if (!hover) return [];
    return series.map(s => {
      let maisProximo = null, dist = Infinity;
      for (const p of s.pontos) {
        const d = Math.abs(p.data.getTime() - hover.dataAlvo);
        if (d < dist) { dist = d; maisProximo = p; }
      }
      return { ...s, ponto: maisProximo };
    });
  }, [hover, series]);

  return (
    <div className="relative" ref={containerRef} onMouseMove={mover} onMouseLeave={() => setHover(null)}>
      <svg viewBox={`0 0 ${largura} ${altura}`} className="w-full h-auto" style={{ maxHeight: 320 }}>
        {/* gridlines horizontais */}
        {Array.from({ length: tickY + 1 }, (_, i) => domY[0] + i * passoY).map(v => (
          <g key={v}>
            <line x1={margem.esquerda} x2={largura - margem.direita} y1={y(v)} y2={y(v)} stroke="#2c2c2a" strokeWidth={1} />
            <text x={margem.esquerda - 8} y={y(v)} textAnchor="end" dominantBaseline="middle" fontSize={10} fill="#898781">{Math.round(v)}</text>
          </g>
        ))}
        {/* ticks X (anos) */}
        {Array.from({ length: tickX + 1 }, (_, i) => domX[0] + i * passoX).map(v => (
          <text key={v} x={x(v)} y={altura - margem.baixo + 16} textAnchor="middle" fontSize={10} fill="#898781">{anoFormato(v)}</text>
        ))}

        {/* crosshair */}
        {hover && (
          <line x1={x(hover.dataAlvo)} x2={x(hover.dataAlvo)} y1={margem.topo} y2={altura - margem.baixo} stroke="#383835" strokeWidth={1} />
        )}

        {/* linhas por time */}
        {series.map(s => {
          const pontosOrdenados = [...s.pontos].sort((a, b) => a.data - b.data);
          const d = pontosOrdenados.map((p, i) => `${i === 0 ? 'M' : 'L'} ${x(p.data.getTime())} ${y(p.rating)}`).join(' ');
          const ultimo = pontosOrdenados[pontosOrdenados.length - 1];
          return (
            <g key={s.teamId}>
              <path d={d} fill="none" stroke={s.cor} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />
              {ultimo && (
                <circle cx={x(ultimo.data.getTime())} cy={y(ultimo.rating)} r={5} fill={s.cor} stroke="#1e293b" strokeWidth={2} />
              )}
              {series.length <= 4 && ultimo && (
                <text x={x(ultimo.data.getTime()) + 8} y={y(ultimo.rating)} fontSize={10} fill="#c3c2b7" dominantBaseline="middle">{s.nome}</text>
              )}
            </g>
          );
        })}
      </svg>

      {hover && linhasComValor.length > 0 && (
        <div
          className="absolute top-2 bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-xs shadow-xl pointer-events-none"
          style={{ left: `${Math.min(85, Math.max(2, (x(hover.dataAlvo) / largura) * 100))}%` }}
        >
          <div className="text-slate-500 mb-1">{new Date(hover.dataAlvo).toLocaleDateString('pt-BR')}</div>
          {linhasComValor.map(l => (
            <div key={l.teamId} className="flex items-center gap-2">
              <span className="inline-block w-3 h-0.5 rounded" style={{ backgroundColor: l.cor }} />
              <span className="font-bold text-slate-100 tabular-nums">{l.ponto ? Math.round(l.ponto.rating) : '—'}</span>
              <span className="text-slate-400 truncate">{l.nome}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// Nível de organização escolhido na UI. 'federacao'/'confederacao'/'geral'
// são só filtros diferentes sobre o MESMO escopo (DB) "global"; 'liga' é o
// modelo antigo, um cálculo isolado à parte (ver comentário no topo do arquivo).
const NIVEIS = [
  ['federacao', 'Federação'],
  ['confederacao', 'Confederação'],
  ['geral', 'Geral'],
  ['liga', 'Por competição'],
];

export default function RatingClubes() {
  const [nivel, setNivel] = useState('federacao');
  const [ligas, setLigas] = useState([]);
  const [ligaId, setLigaId] = useState('');
  const [paisSelecionado, setPaisSelecionado] = useState('');
  const [confederacaoSelecionada, setConfederacaoSelecionada] = useState('UEFA');
  const [mapaFederacao, setMapaFederacao] = useState(new Map()); // team_id -> pais_codigo
  const [rankingBase, setRankingBase] = useState([]); // roster + rating atual (team_elo), sem filtro de nível
  const [carregandoRanking, setCarregandoRanking] = useState(false);
  const [modoRanking, setModoRanking] = useState('atual'); // 'atual' | 'data'
  const [dataSelecionada, setDataSelecionada] = useState(hojeISO());
  const [rankingNaData, setRankingNaData] = useState([]);
  const [carregandoRankingData, setCarregandoRankingData] = useState(false);
  const [selecionados, setSelecionados] = useState([]);
  const [historico, setHistorico] = useState([]); // { team_id, match_date, rating_depois }
  const [carregandoHistorico, setCarregandoHistorico] = useState(false);

  const coresRef = useRef(new Map());
  const proximoSlotRef = useRef(0);

  // escopo real gravado no banco por trás do nível escolhido na UI.
  const escopo = nivel === 'liga' ? 'liga' : 'global';

  useEffect(() => {
    if (!supabaseAtivo) return;
    supabase.from('leagues').select('id, name, external_id').order('name').then(({ data }) => {
      setLigas(data || []);
      if (data && data.length > 0 && !ligaId) setLigaId(String(data[0].id));
    });
  }, []);

  // Mapa time -> país, usado pra filtrar por federação/confederação. Busca
  // uma vez só (a view não muda por sessão) e reaproveita nos 3 níveis.
  const [carregandoMapa, setCarregandoMapa] = useState(false);
  useEffect(() => {
    if (!supabaseAtivo || nivel === 'liga' || mapaFederacao.size > 0) return;
    (async () => {
      setCarregandoMapa(true);
      const linhas = await buscarPaginado(supabase.from('team_federacao').select('team_id, pais_codigo'));
      setMapaFederacao(new Map(linhas.map(l => [l.team_id, l.pais_codigo])));
      setCarregandoMapa(false);
    })();
  }, [nivel]);

  // Ranking base (escopo global inteiro, ou liga escolhida) — reseta
  // cores/seleção quando o escopo (global<->liga) ou a liga mudam. Os
  // filtros de federação/confederação são aplicados depois, client-side
  // (useMemo abaixo), sem precisar buscar de novo a cada troca de país.
  useEffect(() => {
    if (!supabaseAtivo) return;
    if (escopo === 'liga' && !ligaId) return;
    (async () => {
      setCarregandoRanking(true);
      coresRef.current = new Map();
      proximoSlotRef.current = 0;

      let query = supabase.from('team_elo').select('team_id, rating, partidas, teams(name, crest_url)').eq('escopo', escopo);
      query = escopo === 'liga' ? query.eq('league_id', ligaId) : query;
      const { data } = await query.order('rating', { ascending: false });
      setRankingBase(data || []);
      setCarregandoRanking(false);
    })();
  }, [escopo, ligaId]);

  // Países com pelo menos 1 time no ranking base — popula o seletor de
  // federação e escolhe um default (Brasil se existir, senão o primeiro).
  const paisesDisponiveis = useMemo(() => {
    if (nivel === 'liga' || mapaFederacao.size === 0) return [];
    const codigos = new Set();
    for (const l of rankingBase) {
      const c = mapaFederacao.get(l.team_id);
      if (c) codigos.add(c);
    }
    return [...codigos].sort((a, b) => nomePais(a).localeCompare(nomePais(b)));
  }, [rankingBase, mapaFederacao, nivel]);

  useEffect(() => {
    if (nivel !== 'federacao' || paisSelecionado || paisesDisponiveis.length === 0) return;
    setPaisSelecionado(paisesDisponiveis.includes('BRA') ? 'BRA' : paisesDisponiveis[0]);
  }, [nivel, paisesDisponiveis, paisSelecionado]);

  // Ranking filtrado pelo nível escolhido — é isso que a tabela/timeline usam.
  const ranking = useMemo(() => {
    if (nivel === 'liga' || nivel === 'geral') return rankingBase;
    if (nivel === 'federacao') {
      if (!paisSelecionado) return [];
      return rankingBase.filter(l => mapaFederacao.get(l.team_id) === paisSelecionado);
    }
    // confederacao
    return rankingBase.filter(l => confederacaoDoPais(mapaFederacao.get(l.team_id)) === confederacaoSelecionada);
  }, [rankingBase, nivel, paisSelecionado, confederacaoSelecionada, mapaFederacao]);

  // Reseta a seleção da timeline sempre que o recorte exibido muda.
  useEffect(() => {
    setSelecionados(ranking.slice(0, 5).map(l => l.team_id));
  }, [ranking]);

  // Ranking reconstruído numa data escolhida — só roda quando o modo "nessa
  // data" está ativo, usa o roster (nomes/escudos) já filtrado acima.
  useEffect(() => {
    if (!supabaseAtivo || modoRanking !== 'data' || ranking.length === 0) return;
    if (escopo === 'liga' && !ligaId) return;
    (async () => {
      setCarregandoRankingData(true);
      const linhas = await buscarRankingNaData(escopo, ligaId, dataSelecionada, ranking);
      setRankingNaData(linhas);
      setCarregandoRankingData(false);
    })();
  }, [modoRanking, dataSelecionada, escopo, ligaId, ranking]);

  // Histórico dos times selecionados (paginado — pode passar de 1000 linhas
  // pra uma liga com histórico longo + vários times marcados).
  useEffect(() => {
    if (!supabaseAtivo || selecionados.length === 0) { setHistorico([]); return; }
    (async () => {
      setCarregandoHistorico(true);
      let query = supabase
        .from('team_elo_history')
        .select('team_id, match_date, rating_depois')
        .eq('escopo', escopo)
        .in('team_id', selecionados)
        .order('match_date', { ascending: true });
      query = escopo === 'liga' ? query.eq('league_id', ligaId) : query;
      const linhas = await buscarPaginado(query);
      setHistorico(linhas);
      setCarregandoHistorico(false);
    })();
  }, [escopo, ligaId, selecionados]);

  const nomePorTime = useMemo(() => {
    const m = new Map();
    ranking.forEach(l => m.set(l.team_id, l.teams?.name || `#${l.team_id}`));
    return m;
  }, [ranking]);

  const corDoTime = useCallback((teamId) => {
    if (!coresRef.current.has(teamId)) {
      coresRef.current.set(teamId, proximoSlotRef.current % PALETA.length);
      proximoSlotRef.current++;
    }
    return PALETA[coresRef.current.get(teamId)];
  }, []);

  const series = useMemo(() => {
    return selecionados.map(teamId => ({
      teamId,
      nome: nomePorTime.get(teamId) || `#${teamId}`,
      cor: corDoTime(teamId),
      pontos: historico.filter(h => h.team_id === teamId).map(h => ({ data: new Date(h.match_date), rating: Number(h.rating_depois) })),
    }));
  }, [selecionados, historico, nomePorTime, corDoTime]);

  const alternarTime = (teamId) => {
    setSelecionados(prev => {
      if (prev.includes(teamId)) return prev.filter(id => id !== teamId);
      if (prev.length >= MAX_SELECIONADOS) return prev;
      return [...prev, teamId];
    });
  };

  if (!supabaseAtivo) {
    return <div className="max-w-5xl mx-auto text-center py-16 text-slate-500 text-sm">Supabase não configurado.</div>;
  }

  return (
    <div className="max-w-5xl mx-auto">
      <div className="flex items-center gap-2 mb-4">
        <TrendingUp className="text-emerald-400" size={22} />
        <h1 className="text-2xl font-extrabold text-slate-100">Rating dos Clubes (Elo)</h1>
      </div>

      {/* Filtros — uma linha só, acima do conteúdo, nível de organização tudo abaixo */}
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <div className="flex gap-1 bg-slate-800 border border-slate-700 rounded-lg p-1">
          {NIVEIS.map(([v, rotulo]) => (
            <button
              key={v}
              onClick={() => setNivel(v)}
              className={`px-3 py-1.5 rounded-md text-xs font-bold transition-colors ${nivel === v ? 'bg-emerald-500/20 text-emerald-400' : 'text-slate-500 hover:text-slate-300'}`}
            >
              {rotulo}
            </button>
          ))}
        </div>

        {nivel === 'federacao' && (
          <select
            value={paisSelecionado}
            onChange={(e) => setPaisSelecionado(e.target.value)}
            className="bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 font-semibold"
          >
            {paisesDisponiveis.length === 0 && <option value="">Carregando países...</option>}
            {paisesDisponiveis.map(c => <option key={c} value={c}>{nomePais(c)}</option>)}
          </select>
        )}

        {nivel === 'confederacao' && (
          <select
            value={confederacaoSelecionada}
            onChange={(e) => setConfederacaoSelecionada(e.target.value)}
            className="bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 font-semibold"
          >
            {Object.entries(CONFEDERACOES).map(([codigo, nome]) => <option key={codigo} value={codigo}>{nome}</option>)}
          </select>
        )}

        {nivel === 'liga' && (
          <select
            value={ligaId}
            onChange={(e) => setLigaId(e.target.value)}
            className="bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 font-semibold"
          >
            {ligas.map(l => <option key={l.id} value={l.id}>{l.name}</option>)}
          </select>
        )}

        <span className="text-xs text-slate-600">Clique num time na tabela pra incluir/remover da timeline (até {MAX_SELECIONADOS}).</span>
      </div>

      {/* Ranking por data — segunda linha de filtro, só afeta a tabela (a timeline continua com o histórico completo) */}
      <div className="flex flex-wrap items-center gap-2 mb-4">
        <div className="flex gap-1 bg-slate-800 border border-slate-700 rounded-lg p-1">
          {[['atual', 'Rating atual'], ['data', 'Rating numa data']].map(([v, rotulo]) => (
            <button
              key={v}
              onClick={() => setModoRanking(v)}
              className={`px-3 py-1.5 rounded-md text-xs font-bold transition-colors ${modoRanking === v ? 'bg-emerald-500/20 text-emerald-400' : 'text-slate-500 hover:text-slate-300'}`}
            >
              {rotulo}
            </button>
          ))}
        </div>

        {modoRanking === 'data' && (
          <>
            <button
              onClick={() => setDataSelecionada(d => deslocarDias(d, -30))}
              className="px-2.5 py-1.5 rounded-lg bg-slate-800 border border-slate-700 text-xs font-bold text-slate-300 hover:bg-slate-700"
            >
              ◀ 30 dias
            </button>
            <input
              type="date"
              value={dataSelecionada}
              onChange={(e) => setDataSelecionada(e.target.value)}
              className="bg-slate-900 border border-slate-600 rounded-lg px-3 py-1.5 text-sm text-slate-100"
            />
            <button
              onClick={() => setDataSelecionada(d => deslocarDias(d, 30))}
              className="px-2.5 py-1.5 rounded-lg bg-slate-800 border border-slate-700 text-xs font-bold text-slate-300 hover:bg-slate-700"
            >
              30 dias ▶
            </button>
            <button
              onClick={() => setDataSelecionada(hojeISO())}
              className="px-2.5 py-1.5 rounded-lg text-xs font-bold text-slate-500 hover:text-emerald-400"
            >
              Hoje
            </button>
          </>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        {/* Ranking */}
        <div className="lg:col-span-2 bg-slate-800 border border-slate-700 rounded-2xl overflow-hidden h-fit">
          {(() => {
            const emDataMode = modoRanking === 'data';
            const linhasExibidas = emDataMode ? rankingNaData : ranking;
            const carregando = emDataMode ? carregandoRankingData : (carregandoRanking || (nivel !== 'liga' && carregandoMapa));
            if (carregando) return <div className="text-slate-500 text-center py-10 text-sm">Carregando...</div>;
            if (linhasExibidas.length === 0) return <div className="text-slate-500 text-center py-10 text-sm">Sem rating calculado ainda pra esse recorte.</div>;
            return (
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-slate-900 text-slate-500 text-[10px] uppercase tracking-wider">
                    <th className="text-left p-2.5 pl-4">#</th>
                    <th className="text-left p-2.5">Time</th>
                    <th className="text-center p-2.5">Rating</th>
                    <th className="text-center p-2.5 pr-4">{emDataMode ? 'Ref.' : 'Jogos'}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-700/50">
                  {linhasExibidas.map((linha, i) => {
                    const marcado = selecionados.includes(linha.team_id);
                    return (
                      <tr
                        key={linha.team_id}
                        onClick={() => alternarTime(linha.team_id)}
                        className={`cursor-pointer transition-colors ${marcado ? 'bg-emerald-500/10' : 'hover:bg-slate-700/20'}`}
                      >
                        <td className="p-2.5 pl-4 text-slate-500">{i + 1}</td>
                        <td className="p-2.5">
                          <div className="flex items-center gap-2 min-w-0">
                            {marcado && <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ backgroundColor: corDoTime(linha.team_id) }} />}
                            {linha.teams?.crest_url ? <img src={linha.teams.crest_url} alt="" className="w-5 h-5 object-contain shrink-0" /> : <Shield size={16} className="text-slate-700 shrink-0" />}
                            <span className="truncate text-slate-200">{linha.teams?.name}</span>
                          </div>
                        </td>
                        <td className="p-2.5 text-center font-bold text-slate-100 tabular-nums">{Math.round(linha.rating)}</td>
                        <td className="p-2.5 pr-4 text-center text-slate-400 tabular-nums text-xs">
                          {emDataMode ? (linha.referencia ? linha.referencia.slice(0, 10) : 'sem jogo') : linha.partidas}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            );
          })()}
        </div>

        {/* Timeline */}
        <div className="lg:col-span-3 bg-slate-800 border border-slate-700 rounded-2xl p-5">
          <h2 className="text-sm font-bold text-slate-300 uppercase tracking-wider mb-3 flex items-center gap-2">
            <Landmark className="text-emerald-400" size={16} /> Evolução do rating
          </h2>
          {carregandoHistorico ? (
            <div className="text-slate-500 text-center py-16 text-sm">Carregando histórico...</div>
          ) : (
            <>
              <EloTimelineChart series={series} />
              {series.length > 0 && (
                <div className="flex flex-wrap gap-3 mt-3 pt-3 border-t border-slate-700/50">
                  {series.map(s => (
                    <div key={s.teamId} className="flex items-center gap-1.5 text-xs text-slate-400">
                      <span className="w-3 h-0.5 rounded" style={{ backgroundColor: s.cor }} />
                      {s.nome}
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
