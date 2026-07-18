// src/pages/Jogadores.jsx — rota /jogadores
// Painel de consulta da dimensão `players` (populada via FotMob, ver
// arquivos_do_claude/ingestao_fotmob.py) — nome, foto, time mais recente,
// país, idade, valor de mercado. É um SNAPSHOT (idade/valor de mercado da
// última vez que o jogador apareceu num lineup sincronizado), não histórico.
// Paginação real via .range() (nunca carrega tudo de uma vez — hoje já são
// mais de 6 mil linhas).
import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { UserRound, Search, Loader2, AlertTriangle, ChevronLeft, ChevronRight, Shield, ArrowUpDown } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';

const TAMANHO_PAGINA = 24;

const ORDENACOES = [
  { valor: 'market_value.desc', rotulo: 'Valor de mercado (maior)', coluna: 'market_value', asc: false },
  { valor: 'name.asc', rotulo: 'Nome (A-Z)', coluna: 'name', asc: true },
  { valor: 'age.asc', rotulo: 'Idade (menor)', coluna: 'age', asc: true },
  { valor: 'age.desc', rotulo: 'Idade (maior)', coluna: 'age', asc: false },
];

function formatarValorMercado(v) {
  if (v == null) return '—';
  if (v >= 1_000_000) return `€${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `€${(v / 1_000).toFixed(0)}K`;
  return `€${v}`;
}

function FotoJogador({ url, nome }) {
  const [erro, setErro] = useState(false);
  if (!url || erro) {
    return (
      <div className="w-9 h-9 rounded-full bg-slate-900 border border-slate-700 flex items-center justify-center shrink-0">
        <UserRound size={16} className="text-slate-600" />
      </div>
    );
  }
  return (
    <img
      src={url}
      alt={nome || ''}
      onError={() => setErro(true)}
      className="w-9 h-9 rounded-full bg-slate-900 border border-slate-700 object-cover shrink-0"
    />
  );
}

// `teams.id` (pipeline) e `equipes.id` (cadastro manual, o que a rota
// /times/:id espera) são numerações DIFERENTES — só existe link de verdade
// pros times que já têm o vínculo `equipes.pipeline_team_id` preenchido
// (ver Times.jsx). Linkar direto com teams.id levava pra um clube errado
// (a mesma numeração calhava de existir em equipes, mas de outro time).
function TimeCelula({ time }) {
  const equipeId = time.equipes?.[0]?.id;
  const conteudo = (
    <>
      {time.crest_url
        ? <img src={time.crest_url} alt="" className="w-4 h-4 object-contain shrink-0" />
        : <Shield size={14} className="text-slate-700 shrink-0" />}
      <span className="truncate max-w-[10rem]">{time.name}</span>
    </>
  );
  return equipeId ? (
    <Link to={`/times/${equipeId}`} className="flex items-center gap-1.5 hover:text-emerald-400 hover:underline w-fit">
      {conteudo}
    </Link>
  ) : (
    <span className="flex items-center gap-1.5 w-fit" title="Esse time ainda não tem vínculo com o cadastro manual (equipes)">
      {conteudo}
    </span>
  );
}

export default function Jogadores() {
  const [jogadores, setJogadores] = useState([]);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState('');

  const [pagina, setPagina] = useState(0);
  const [totalRegistros, setTotalRegistros] = useState(0);
  const [buscaDigitada, setBuscaDigitada] = useState('');
  const [busca, setBusca] = useState('');
  const [ordenacao, setOrdenacao] = useState(ORDENACOES[0].valor);

  useEffect(() => {
    const timer = setTimeout(() => { setBusca(buscaDigitada); setPagina(0); }, 400);
    return () => clearTimeout(timer);
  }, [buscaDigitada]);

  useEffect(() => {
    if (!supabaseAtivo) { setCarregando(false); return; }
    (async () => {
      setCarregando(true);
      setErro('');
      const config = ORDENACOES.find(o => o.valor === ordenacao) || ORDENACOES[0];

      let query = supabase
        .from('players')
        .select('id, name, photo_url, age, country_name, country_code, market_value, last_team:teams!players_last_team_id_fkey(id,name,crest_url,equipes!equipes_pipeline_team_id_fkey(id))', { count: 'exact' });

      if (busca) query = query.ilike('name', `%${busca}%`);
      query = query
        .order(config.coluna, { ascending: config.asc, nullsFirst: false })
        .range(pagina * TAMANHO_PAGINA, pagina * TAMANHO_PAGINA + TAMANHO_PAGINA - 1);

      const { data, error, count } = await query;
      if (error) setErro(error.message);
      else { setJogadores(data || []); setTotalRegistros(count || 0); }
      setCarregando(false);
    })();
  }, [pagina, busca, ordenacao]);

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
          <UserRound className="text-emerald-400" size={28} /> Jogadores
        </h1>
        <p className="text-slate-400 mt-1 text-sm">
          Dimensão de jogador via FotMob — snapshot da última vez que apareceu numa escalação sincronizada (idade e valor de mercado não são histórico).
        </p>
      </div>

      {erro && (
        <div className="bg-red-950/30 border border-red-600/40 text-red-300 text-sm px-4 py-3 rounded-xl mb-4">{erro}</div>
      )}

      <div className="bg-slate-800 border border-slate-700 rounded-2xl p-4 mb-4 flex flex-col sm:flex-row gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" size={16} />
          <input
            value={buscaDigitada}
            onChange={(e) => setBuscaDigitada(e.target.value)}
            placeholder="Buscar por nome..."
            className="w-full bg-slate-900 border border-slate-600 rounded-lg pl-9 pr-3 py-2 text-sm text-slate-100"
          />
        </div>
        <div className="relative">
          <ArrowUpDown className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500 pointer-events-none" size={14} />
          <select
            value={ordenacao}
            onChange={(e) => { setOrdenacao(e.target.value); setPagina(0); }}
            className="bg-slate-900 border border-slate-600 rounded-lg pl-8 pr-3 py-2 text-sm text-slate-100 appearance-none"
          >
            {ORDENACOES.map(o => <option key={o.valor} value={o.valor}>{o.rotulo}</option>)}
          </select>
        </div>
      </div>

      <div className="bg-slate-800 border border-slate-700 rounded-2xl overflow-hidden">
        {carregando ? (
          <div className="flex items-center justify-center py-10 text-slate-500 gap-2 text-sm">
            <Loader2 className="animate-spin" size={18} /> Carregando...
          </div>
        ) : jogadores.length === 0 ? (
          <p className="text-slate-500 text-center py-10 text-sm">Nenhum jogador encontrado.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="bg-slate-900 text-slate-400 text-[10px] uppercase tracking-wider">
                  <th className="p-3">Jogador</th>
                  <th className="p-3">Time</th>
                  <th className="p-3">País</th>
                  <th className="p-3 text-right">Idade</th>
                  <th className="p-3 text-right">Valor de mercado</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-700/50">
                {jogadores.map(j => (
                  <tr key={j.id} className="hover:bg-slate-700/20">
                    <td className="p-3 font-semibold text-slate-200">
                      <Link to={`/jogadores/${j.id}`} className="flex items-center gap-2.5 hover:text-emerald-400 hover:underline w-fit">
                        <FotoJogador url={j.photo_url} nome={j.name} />
                        <span className="truncate">{j.name || '(sem nome)'}</span>
                      </Link>
                    </td>
                    <td className="p-3 text-slate-400">
                      {j.last_team ? (
                        <TimeCelula time={j.last_team} />
                      ) : '—'}
                    </td>
                    <td className="p-3 text-slate-400">
                      {j.country_name || j.country_code || '—'}
                    </td>
                    <td className="p-3 text-right text-slate-300 font-mono">{j.age ?? '—'}</td>
                    <td className="p-3 text-right text-slate-300 font-mono">{formatarValorMercado(j.market_value)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {totalRegistros > TAMANHO_PAGINA && (
        <div className="flex items-center justify-between mt-4 text-sm">
          <span className="text-slate-500">
            {pagina * TAMANHO_PAGINA + 1}–{Math.min((pagina + 1) * TAMANHO_PAGINA, totalRegistros)} de {totalRegistros}
          </span>
          <div className="flex gap-2">
            <button
              disabled={pagina === 0}
              onClick={() => setPagina(p => p - 1)}
              className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-slate-800 border border-slate-700 text-slate-300 disabled:opacity-30 disabled:cursor-not-allowed hover:bg-slate-700"
            >
              <ChevronLeft size={16} /> Anterior
            </button>
            <button
              disabled={(pagina + 1) * TAMANHO_PAGINA >= totalRegistros}
              onClick={() => setPagina(p => p + 1)}
              className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-slate-800 border border-slate-700 text-slate-300 disabled:opacity-30 disabled:cursor-not-allowed hover:bg-slate-700"
            >
              Próxima <ChevronRight size={16} />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
