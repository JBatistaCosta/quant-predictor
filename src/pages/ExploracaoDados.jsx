// src/pages/ExploracaoDados.jsx — rota /explorar-dados
// Explorador interativo de todas as tabelas do banco de dados.
// Permite navegar, paginar, filtrar e pesquisar qualquer tabela pública.
import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Database, Search, ChevronLeft, ChevronRight, RefreshCw,
  Table2, Filter, X, Copy, ChevronDown, Eye, Download, Loader2,
} from 'lucide-react';
import { supabase } from '../supabaseClient';

// Catálogo de tabelas com metadados e estimativas de linhas
const TABELAS = [
  { nome: 'matches',                      desc: 'Partidas históricas',                    grupo: 'Partidas',   linhas: 27944  },
  { nome: 'match_stats',                   desc: 'Estatísticas por partida',               grupo: 'Partidas',   linhas: 24370  },
  { nome: 'match_stats_fotmob',            desc: 'Stats FotMob por partida (xG/xGOT)',     grupo: 'Partidas',   linhas: 31900  },
  { nome: 'match_player_stats_fotmob',     desc: 'Stats individuais por jogador/partida',  grupo: 'Partidas',   linhas: 672166 },
  { nome: 'match_shots_fotmob',            desc: 'Mapa de chutes (xG individual)',         grupo: 'Partidas',   linhas: 477686 },
  { nome: 'match_lineup_fotmob',           desc: 'Escalações históricas FotMob',           grupo: 'Partidas',   linhas: 251203 },
  { nome: 'match_formation_fotmob',        desc: 'Esquema tático por partida/time',        grupo: 'Partidas',   linhas: 37951  },
  { nome: 'match_goal_timeline',           desc: 'Linha do tempo de gols',                 grupo: 'Partidas',   linhas: 51873  },
  { nome: 'match_team_game_state',         desc: 'Tempo e xG por estado do placar',        grupo: 'Partidas',   linhas: 78250  },
  { nome: 'match_events',                  desc: 'Cartões por partida (só cartões)',        grupo: 'Partidas',   linhas: 61181  },
  { nome: 'match_context_fotmob',          desc: 'Estádio e clima por partida',            grupo: 'Partidas',   linhas: 15956  },
  { nome: 'match_features_contexto',       desc: 'Features de contexto (fadiga/H2H)',      grupo: 'Partidas',   linhas: 37782  },
  { nome: 'match_source_ids',             desc: 'Mapeamento de IDs de partidas',           grupo: 'Partidas',   linhas: 16137  },
  { nome: 'players',                       desc: 'Dimensão de jogadores',                  grupo: 'Jogadores',  linhas: 14139  },
  { nome: 'player_ratings',               desc: 'Rating Elo-like atual por jogador',       grupo: 'Jogadores',  linhas: 7889   },
  { nome: 'player_rating_history',        desc: 'Histórico de rating por jogador',        grupo: 'Jogadores',  linhas: 357030 },
  { nome: 'player_market_value_history',  desc: 'Série temporal de valor de mercado',     grupo: 'Jogadores',  linhas: 514641 },
  { nome: 'player_career_history_fotmob', desc: 'Histórico de carreira FotMob',           grupo: 'Jogadores',  linhas: 79433  },
  { nome: 'player_details_fotmob',        desc: 'Perfil detalhado (altura/pé/posições)',  grupo: 'Jogadores',  linhas: 11584  },
  { nome: 'player_trophies_fotmob',       desc: 'Troféus por jogador',                    grupo: 'Jogadores',  linhas: 37119  },
  { nome: 'player_availability_fotmob',   desc: 'Desfalques (lesões/suspensões)',         grupo: 'Jogadores',  linhas: 6917   },
  { nome: 'teams',                        desc: 'Dimensão de times',                       grupo: 'Times',      linhas: 621    },
  { nome: 'teams_fotmob',                 desc: 'Times FotMob',                           grupo: 'Times',      linhas: 295    },
  { nome: 'team_elo',                     desc: 'Elo atual por time',                     grupo: 'Times',      linhas: 376    },
  { nome: 'team_elo_history',             desc: 'Histórico de Elo por time',              grupo: 'Times',      linhas: 20261  },
  { nome: 'team_elo_external',            desc: 'Elo externo (ClubElo)',                  grupo: 'Times',      linhas: 145244 },
  { nome: 'team_transfers_fotmob',        desc: 'Transferências (in/out/renovação)',      grupo: 'Times',      linhas: 6491   },
  { nome: 'team_squad_fotmob',            desc: 'Elenco FotMob por time',                grupo: 'Times',      linhas: 0      },
  { nome: 'team_unavailable_fotmob',      desc: 'Indisponibilidades por time',            grupo: 'Times',      linhas: 0      },
  { nome: 'team_coach_history_fotmob',    desc: 'Histórico de técnicos FotMob',          grupo: 'Times',      linhas: 4159   },
  { nome: 'team_source_ids',             desc: 'Mapeamento de IDs de times',             grupo: 'Times',      linhas: 674    },
  { nome: 'leagues',                      desc: 'Ligas',                                  grupo: 'Ligas',      linhas: 16     },
  { nome: 'model_predictions',           desc: 'Previsões de todos os modelos',           grupo: 'Modelos',    linhas: 107652 },
  { nome: 'predicoes',                    desc: 'Probabilidades 1X2 + edge vs. odds',    grupo: 'Modelos',    linhas: 247362 },
  { nome: 'model_benchmarking_backtest', desc: 'Backtest do Model Benchmarking',          grupo: 'Modelos',    linhas: 371    },
  { nome: 'model_benchmarking_learning_curve', desc: 'Curvas de aprendizado do backtest',grupo: 'Modelos',    linhas: 19151  },
  { nome: 'models_registry',             desc: 'Registro de versões de modelos',         grupo: 'Modelos',    linhas: 230    },
  { nome: 'model_v9_walkforward_results', desc: 'Resultados walk-forward V9',            grupo: 'Modelos',    linhas: 13     },
  { nome: 'model_v9_feature_importance', desc: 'Importância de features V9',             grupo: 'Modelos',    linhas: 184    },
  { nome: 'custom_model_configs',        desc: 'Configs de modelos customizados',        grupo: 'Modelos',    linhas: 3      },
  { nome: 'model_calibration',           desc: 'Calibração Platt Scaling',               grupo: 'Modelos',    linhas: 24     },
  { nome: 'odds_market',                 desc: 'Odds de mercado históricas',             grupo: 'Odds',       linhas: 267312 },
  { nome: 'market_odds',                 desc: 'Odds por partida/bookmaker',             grupo: 'Odds',       linhas: 25     },
  { nome: 'oddspapi_cache',              desc: 'Cache OddsPAPI',                         grupo: 'Odds',       linhas: 5      },
  { nome: 'notas',                       desc: 'Bloco de notas',                         grupo: 'Sistema',    linhas: 9      },
  { nome: 'importacoes',                 desc: 'Histórico de importações',               grupo: 'Sistema',    linhas: 3      },
  { nome: 'eventos',                     desc: 'Eventos manuais (apostas)',              grupo: 'Sistema',    linhas: 99     },
];

const GRUPOS = [...new Set(TABELAS.map((t) => t.grupo))];

function fmtNumero(n) {
  if (n == null) return '—';
  if (n >= 1000000) return `${(n / 1000000).toFixed(1)}M`;
  if (n >= 1000) return `${(n / 1000).toFixed(0)}k`;
  return String(n);
}

function ValorCelula({ valor }) {
  if (valor === null || valor === undefined) return <span className="text-slate-600 italic">null</span>;
  if (typeof valor === 'boolean') return <span className={valor ? 'text-emerald-400' : 'text-red-400'}>{String(valor)}</span>;
  if (typeof valor === 'object') {
    const texto = JSON.stringify(valor);
    return (
      <span className="text-violet-300 font-mono text-xs" title={texto}>
        {texto.length > 60 ? texto.slice(0, 60) + '…' : texto}
      </span>
    );
  }
  const texto = String(valor);
  return <span className={texto.length > 80 ? 'text-slate-300 text-xs' : 'text-slate-200'}>{texto.length > 100 ? texto.slice(0, 100) + '…' : texto}</span>;
}

export default function ExploracaoDados() {
  const [filtroTabela, setFiltroTabela] = useState('');
  const [grupoAtivo, setGrupoAtivo] = useState(null);
  const [tabelaSelecionada, setTabelaSelecionada] = useState(null);
  const [dados, setDados] = useState([]);
  const [colunas, setColunas] = useState([]);
  const [total, setTotal] = useState(null);
  const [pagina, setPagina] = useState(0);
  const [porPagina] = useState(100);
  const [busca, setBusca] = useState('');
  const [colunaBusca, setColunaBusca] = useState('');
  const [carregando, setCarregando] = useState(false);
  const [erro, setErro] = useState(null);
  const [linhaExpandida, setLinhaExpandida] = useState(null);
  const [contagens, setContagens] = useState({});
  const [exportando, setExportando] = useState(false);
  const [menuExportAberto, setMenuExportAberto] = useState(false);
  const buscaRef = useRef(null);
  const menuExportRef = useRef(null);

  useEffect(() => {
    const fechar = (e) => { if (menuExportRef.current && !menuExportRef.current.contains(e.target)) setMenuExportAberto(false); };
    document.addEventListener('mousedown', fechar);
    return () => document.removeEventListener('mousedown', fechar);
  }, []);

  const tabelasFiltradas = TABELAS.filter((t) => {
    const matchGrupo = !grupoAtivo || t.grupo === grupoAtivo;
    const matchNome = !filtroTabela || t.nome.includes(filtroTabela.toLowerCase()) || t.desc.toLowerCase().includes(filtroTabela.toLowerCase());
    return matchGrupo && matchNome;
  });

  const carregarDados = useCallback(async (tabela, pg, col, txt) => {
    if (!tabela) return;
    setCarregando(true);
    setErro(null);
    setLinhaExpandida(null);
    try {
      const inicio = pg * porPagina;
      const fim = inicio + porPagina - 1;
      let q = supabase.from(tabela).select('*', { count: 'exact' }).range(inicio, fim);
      if (txt && col) q = q.ilike(col, `%${txt}%`);
      const { data, count, error } = await q;
      if (error) throw error;
      setDados(data || []);
      setTotal(count);
      if (data && data.length > 0) {
        setColunas(Object.keys(data[0]));
        if (!col || !Object.keys(data[0]).includes(col)) setColunaBusca('');
      }
    } catch (e) {
      setErro(e.message);
      setDados([]);
      setTotal(0);
    } finally {
      setCarregando(false);
    }
  }, [porPagina]);

  useEffect(() => {
    if (tabelaSelecionada) {
      setPagina(0);
      setBusca('');
      setColunaBusca('');
      setColunas([]);
      carregarDados(tabelaSelecionada, 0, '', '');
    }
  }, [tabelaSelecionada, carregarDados]);

  const aplicarBusca = () => {
    setPagina(0);
    carregarDados(tabelaSelecionada, 0, colunaBusca, busca);
  };

  const limparBusca = () => {
    setBusca('');
    setColunaBusca('');
    setPagina(0);
    carregarDados(tabelaSelecionada, 0, '', '');
  };

  const irParaPagina = (novaPagina) => {
    setPagina(novaPagina);
    carregarDados(tabelaSelecionada, novaPagina, colunaBusca, busca);
  };

  const totalPaginas = total != null ? Math.ceil(total / porPagina) : 0;

  const copiarLinha = (linha) => {
    navigator.clipboard.writeText(JSON.stringify(linha, null, 2));
  };

  const baixarArquivo = (blob, nome) => {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = nome; a.click();
    URL.revokeObjectURL(url);
  };

  const toCSV = (linhas) => {
    if (!linhas.length) return '';
    const cols = Object.keys(linhas[0]);
    const esc = (v) => {
      if (v == null) return '';
      const s = typeof v === 'object' ? JSON.stringify(v) : String(v);
      return /[,"\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    };
    return [cols.join(','), ...linhas.map((l) => cols.map((c) => esc(l[c])).join(','))].join('\n');
  };

  const exportarPaginaCSV = () => {
    setMenuExportAberto(false);
    baixarArquivo(new Blob([toCSV(dados)], { type: 'text/csv;charset=utf-8;' }), `${tabelaSelecionada}_p${pagina + 1}.csv`);
  };

  const exportarPaginaJSON = () => {
    setMenuExportAberto(false);
    baixarArquivo(new Blob([JSON.stringify(dados, null, 2)], { type: 'application/json' }), `${tabelaSelecionada}_p${pagina + 1}.json`);
  };

  const exportarTudoCSV = async () => {
    if (!tabelaSelecionada || exportando) return;
    setMenuExportAberto(false);
    setExportando(true);
    try {
      const CHUNK = 1000;
      let todas = [];
      let pg = 0;
      while (true) {
        const inicio = pg * CHUNK;
        let q = supabase.from(tabelaSelecionada).select('*').range(inicio, inicio + CHUNK - 1);
        if (busca && colunaBusca) q = q.ilike(colunaBusca, `%${busca}%`);
        const { data, error } = await q;
        if (error) throw error;
        todas = [...todas, ...(data || [])];
        if (!data || data.length < CHUNK) break;
        pg++;
      }
      const sufixo = busca ? '_filtrado' : '_completo';
      baixarArquivo(new Blob([toCSV(todas)], { type: 'text/csv;charset=utf-8;' }), `${tabelaSelecionada}${sufixo}.csv`);
    } catch (e) {
      alert(`Erro ao exportar: ${e.message}`);
    } finally {
      setExportando(false);
    }
  };

  return (
    <div className="h-[calc(100vh-80px)] flex flex-col gap-3">
      <div className="flex items-center gap-3">
        <Database className="text-cyan-400" size={20} />
        <h1 className="text-lg font-bold text-slate-100">Exploração de Dados</h1>
        <span className="text-xs text-slate-500">{TABELAS.length} tabelas disponíveis</span>
      </div>

      <div className="flex gap-3 flex-1 min-h-0">
        {/* Painel esquerdo — lista de tabelas */}
        <div className="w-64 flex-shrink-0 flex flex-col gap-2 bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
          <div className="p-3 border-b border-slate-700">
            <div className="relative">
              <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
              <input
                type="text"
                value={filtroTabela}
                onChange={(e) => setFiltroTabela(e.target.value)}
                placeholder="Buscar tabela..."
                className="w-full bg-slate-900 border border-slate-700 rounded-lg pl-8 pr-3 py-1.5 text-xs text-slate-200 outline-none focus:border-cyan-500/50"
              />
            </div>
          </div>

          {/* Filtro de grupos */}
          <div className="px-3 flex flex-wrap gap-1">
            <button
              onClick={() => setGrupoAtivo(null)}
              className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${!grupoAtivo ? 'bg-cyan-600 text-white' : 'bg-slate-700 text-slate-400 hover:text-slate-200'}`}
            >
              Todos
            </button>
            {GRUPOS.map((g) => (
              <button
                key={g}
                onClick={() => setGrupoAtivo(g === grupoAtivo ? null : g)}
                className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${grupoAtivo === g ? 'bg-cyan-600 text-white' : 'bg-slate-700 text-slate-400 hover:text-slate-200'}`}
              >
                {g}
              </button>
            ))}
          </div>

          <div className="flex-1 overflow-y-auto px-1 pb-2 space-y-0.5">
            {tabelasFiltradas.map((t) => (
              <button
                key={t.nome}
                onClick={() => setTabelaSelecionada(t.nome)}
                className={`w-full text-left px-3 py-2 rounded-lg transition-colors ${
                  tabelaSelecionada === t.nome
                    ? 'bg-cyan-600/20 border border-cyan-600/40 text-cyan-300'
                    : 'hover:bg-slate-700/60 text-slate-300 border border-transparent'
                }`}
              >
                <div className="flex items-center justify-between gap-1">
                  <span className="font-mono text-[11px] truncate">{t.nome}</span>
                  <span className="text-[10px] text-slate-500 flex-shrink-0">{fmtNumero(t.linhas)}</span>
                </div>
                <p className="text-[10px] text-slate-500 truncate mt-0.5">{t.desc}</p>
              </button>
            ))}
            {tabelasFiltradas.length === 0 && (
              <p className="text-xs text-slate-500 text-center py-6">Nenhuma tabela encontrada.</p>
            )}
          </div>
        </div>

        {/* Painel direito — dados da tabela */}
        <div className="flex-1 flex flex-col gap-2 min-w-0">
          {!tabelaSelecionada ? (
            <div className="flex-1 flex items-center justify-center bg-slate-800 border border-slate-700 rounded-xl">
              <div className="text-center">
                <Table2 size={40} className="text-slate-600 mx-auto mb-3" />
                <p className="text-slate-500 text-sm">Selecione uma tabela para explorar seus dados</p>
              </div>
            </div>
          ) : (
            <>
              {/* Header da tabela */}
              <div className="bg-slate-800 border border-slate-700 rounded-xl px-4 py-3 flex items-center gap-3 flex-wrap">
                <div className="flex items-center gap-2 flex-1 min-w-0">
                  <Table2 size={16} className="text-cyan-400 flex-shrink-0" />
                  <span className="font-mono font-bold text-slate-100 text-sm">{tabelaSelecionada}</span>
                  {total != null && (
                    <span className="text-xs text-slate-500">
                      {total.toLocaleString('pt-BR')} registros totais
                      {busca && ` (filtrado)`}
                    </span>
                  )}
                </div>

                {/* Busca */}
                <div className="flex items-center gap-2 flex-shrink-0">
                  {colunas.length > 0 && (
                    <select
                      value={colunaBusca}
                      onChange={(e) => setColunaBusca(e.target.value)}
                      className="bg-slate-900 border border-slate-600 rounded-lg px-2 py-1.5 text-xs text-slate-300 outline-none focus:border-cyan-500/50 max-w-[140px]"
                    >
                      <option value="">Coluna…</option>
                      {colunas.map((c) => <option key={c} value={c}>{c}</option>)}
                    </select>
                  )}
                  <div className="relative">
                    <input
                      ref={buscaRef}
                      type="text"
                      value={busca}
                      onChange={(e) => setBusca(e.target.value)}
                      onKeyDown={(e) => e.key === 'Enter' && aplicarBusca()}
                      placeholder="Pesquisar…"
                      className="bg-slate-900 border border-slate-600 rounded-lg pl-3 pr-8 py-1.5 text-xs text-slate-200 outline-none focus:border-cyan-500/50 w-44"
                    />
                    {busca && (
                      <button onClick={limparBusca} className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300">
                        <X size={12} />
                      </button>
                    )}
                  </div>
                  <button
                    onClick={aplicarBusca}
                    disabled={!busca || !colunaBusca || carregando}
                    className="flex items-center gap-1 bg-cyan-700 hover:bg-cyan-600 disabled:opacity-40 disabled:cursor-not-allowed text-white text-xs font-bold px-3 py-1.5 rounded-lg transition-colors"
                  >
                    <Filter size={12} /> Filtrar
                  </button>
                  <button
                    onClick={() => carregarDados(tabelaSelecionada, pagina, colunaBusca, busca)}
                    disabled={carregando}
                    className="text-slate-400 hover:text-slate-200 disabled:opacity-40 p-1.5 rounded-lg hover:bg-slate-700 transition-colors"
                    title="Atualizar"
                  >
                    <RefreshCw size={14} className={carregando ? 'animate-spin' : ''} />
                  </button>

                  {/* Exportação */}
                  {dados.length > 0 && (
                    <div className="relative" ref={menuExportRef}>
                      <button
                        onClick={() => setMenuExportAberto((v) => !v)}
                        disabled={exportando}
                        className="flex items-center gap-1 bg-slate-700 hover:bg-slate-600 disabled:opacity-40 disabled:cursor-not-allowed text-slate-300 text-xs font-bold px-3 py-1.5 rounded-lg transition-colors"
                        title="Exportar dados"
                      >
                        {exportando
                          ? <Loader2 size={13} className="animate-spin" />
                          : <Download size={13} />}
                        Exportar
                        <ChevronDown size={11} />
                      </button>
                      {menuExportAberto && (
                        <div className="absolute right-0 top-full mt-1 bg-slate-800 border border-slate-600 rounded-xl shadow-2xl z-50 min-w-[190px] overflow-hidden">
                          <div className="px-3 py-2 text-[10px] font-bold text-slate-500 uppercase tracking-wider border-b border-slate-700">
                            Página atual ({dados.length} linhas)
                          </div>
                          <button onClick={exportarPaginaCSV} className="w-full text-left px-4 py-2 text-xs text-slate-300 hover:bg-slate-700 hover:text-slate-100 transition-colors">
                            CSV (.csv)
                          </button>
                          <button onClick={exportarPaginaJSON} className="w-full text-left px-4 py-2 text-xs text-slate-300 hover:bg-slate-700 hover:text-slate-100 transition-colors">
                            JSON (.json)
                          </button>
                          {total != null && total > porPagina && (
                            <>
                              <div className="px-3 py-2 text-[10px] font-bold text-slate-500 uppercase tracking-wider border-t border-slate-700">
                                Tudo ({total.toLocaleString('pt-BR')} linhas)
                              </div>
                              <button onClick={exportarTudoCSV} className="w-full text-left px-4 py-2 text-xs text-slate-300 hover:bg-slate-700 hover:text-slate-100 transition-colors">
                                CSV completo (.csv)
                              </button>
                            </>
                          )}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>

              {/* Tabela de dados */}
              <div className="flex-1 bg-slate-800 border border-slate-700 rounded-xl overflow-hidden flex flex-col min-h-0">
                {erro ? (
                  <div className="flex-1 flex items-center justify-center p-6">
                    <p className="text-red-400 text-sm">{erro}</p>
                  </div>
                ) : carregando && dados.length === 0 ? (
                  <div className="flex-1 flex items-center justify-center">
                    <RefreshCw size={20} className="text-slate-500 animate-spin" />
                  </div>
                ) : dados.length === 0 ? (
                  <div className="flex-1 flex items-center justify-center">
                    <p className="text-slate-500 text-sm">Nenhum registro encontrado.</p>
                  </div>
                ) : (
                  <div className="flex-1 overflow-auto">
                    <table className="w-full text-xs min-w-max">
                      <thead className="sticky top-0 bg-slate-900 z-10">
                        <tr>
                          <th className="w-8 px-2 py-2 text-left"></th>
                          {colunas.map((col) => (
                            <th
                              key={col}
                              className="px-3 py-2 text-left font-semibold text-slate-400 uppercase tracking-wider whitespace-nowrap border-b border-slate-700 cursor-pointer hover:text-cyan-400"
                              onClick={() => { setColunaBusca(col); buscaRef.current?.focus(); }}
                              title={`Filtrar por ${col}`}
                            >
                              {col}
                            </th>
                          ))}
                          <th className="px-2 py-2 border-b border-slate-700"></th>
                        </tr>
                      </thead>
                      <tbody>
                        {dados.map((linha, i) => {
                          const key = linha.id ?? i;
                          const expandida = linhaExpandida === key;
                          return (
                            <React.Fragment key={key}>
                              <tr
                                className={`group border-b border-slate-700/50 hover:bg-slate-700/30 transition-colors ${expandida ? 'bg-slate-700/40' : ''}`}
                              >
                                <td className="px-2 py-1.5 text-slate-600 text-right">{pagina * porPagina + i + 1}</td>
                                {colunas.map((col) => (
                                  <td key={col} className="px-3 py-1.5 max-w-[200px] truncate">
                                    <ValorCelula valor={linha[col]} />
                                  </td>
                                ))}
                                <td className="px-2 py-1.5">
                                  <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100">
                                    <button
                                      onClick={() => setLinhaExpandida(expandida ? null : key)}
                                      className="text-slate-600 hover:text-cyan-400 transition-colors"
                                      title="Expandir linha"
                                    >
                                      <Eye size={12} />
                                    </button>
                                    <button
                                      onClick={() => copiarLinha(linha)}
                                      className="text-slate-600 hover:text-emerald-400 transition-colors"
                                      title="Copiar JSON"
                                    >
                                      <Copy size={12} />
                                    </button>
                                  </div>
                                </td>
                              </tr>
                              {expandida && (
                                <tr className="bg-slate-900/60">
                                  <td colSpan={colunas.length + 2} className="px-4 py-3">
                                    <pre className="text-[11px] text-slate-300 whitespace-pre-wrap break-words max-h-64 overflow-auto font-mono leading-relaxed">
                                      {JSON.stringify(linha, null, 2)}
                                    </pre>
                                  </td>
                                </tr>
                              )}
                            </React.Fragment>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}

                {/* Paginação */}
                {total != null && totalPaginas > 1 && (
                  <div className="flex items-center justify-between px-4 py-2 border-t border-slate-700 bg-slate-900/50 flex-shrink-0">
                    <span className="text-xs text-slate-500">
                      {(pagina * porPagina + 1).toLocaleString('pt-BR')}–{Math.min((pagina + 1) * porPagina, total).toLocaleString('pt-BR')} de {total.toLocaleString('pt-BR')}
                    </span>
                    <div className="flex items-center gap-1">
                      <button
                        onClick={() => irParaPagina(0)}
                        disabled={pagina === 0 || carregando}
                        className="px-2 py-1 text-xs text-slate-400 hover:text-slate-200 disabled:opacity-30 disabled:cursor-not-allowed"
                      >
                        «
                      </button>
                      <button
                        onClick={() => irParaPagina(pagina - 1)}
                        disabled={pagina === 0 || carregando}
                        className="flex items-center gap-1 px-2 py-1 text-xs text-slate-400 hover:text-slate-200 disabled:opacity-30 disabled:cursor-not-allowed"
                      >
                        <ChevronLeft size={13} /> Anterior
                      </button>
                      <span className="px-3 py-1 text-xs text-slate-300 bg-slate-800 rounded-lg border border-slate-700">
                        {pagina + 1} / {totalPaginas.toLocaleString('pt-BR')}
                      </span>
                      <button
                        onClick={() => irParaPagina(pagina + 1)}
                        disabled={pagina >= totalPaginas - 1 || carregando}
                        className="flex items-center gap-1 px-2 py-1 text-xs text-slate-400 hover:text-slate-200 disabled:opacity-30 disabled:cursor-not-allowed"
                      >
                        Próxima <ChevronRight size={13} />
                      </button>
                      <button
                        onClick={() => irParaPagina(totalPaginas - 1)}
                        disabled={pagina >= totalPaginas - 1 || carregando}
                        className="px-2 py-1 text-xs text-slate-400 hover:text-slate-200 disabled:opacity-30 disabled:cursor-not-allowed"
                      >
                        »
                      </button>
                    </div>
                    <div className="text-xs text-slate-600">
                      {porPagina} por página
                    </div>
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
