// src/pages/Jogadores.jsx — rota /jogadores
// Painel de consulta da dimensão `players` (populada via FotMob, ver
// arquivos_do_claude/ingestao_fotmob.py) — nome, foto, time mais recente,
// país, idade, valor de mercado, rating. Usa a view `vw_jogadores_lista`
// (join com player_ratings e com o clube ATUAL na data de hoje, resolvido
// via player_career_history_fotmob quando o jogador já tem perfil
// sincronizado — cai pro snapshot antigo players.last_team_id quando não).
// Paginação real via .range() (nunca carrega tudo de uma vez — hoje já são
// mais de 11 mil linhas).
import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { UserRound, Search, Loader2, AlertTriangle, ChevronLeft, ChevronRight, Shield, ArrowUpDown, RotateCcw, UploadCloud, Star } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';
import { apiUrl } from '../utils/apiUrl';

const TAMANHO_PAGINA = 24;

const ORDENACOES = [
  { valor: 'market_value.desc', rotulo: 'Valor de mercado (maior)', coluna: 'market_value', asc: false },
  { valor: 'rating.desc', rotulo: 'Rating (maior)', coluna: 'rating', asc: false },
  { valor: 'name.asc', rotulo: 'Nome (A-Z)', coluna: 'name', asc: true },
  { valor: 'age.asc', rotulo: 'Idade (menor)', coluna: 'age', asc: true },
  { valor: 'age.desc', rotulo: 'Idade (maior)', coluna: 'age', asc: false },
];

const LOTES_IMPORTACAO = [20, 50, 100, 200];
const PACING_IMPORTACAO_MS = 1300; // mesmo espaçamento usado nos scripts de backfill, evita bloqueio de IP pelo FotMob

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
function TimeCelula({ nome, crestUrl, equipeId }) {
  const conteudo = (
    <>
      {crestUrl
        ? <img src={crestUrl} alt="" className="w-4 h-4 object-contain shrink-0" />
        : <Shield size={14} className="text-slate-700 shrink-0" />}
      <span className="truncate max-w-[10rem]">{nome}</span>
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
  const [ratingMinDigitado, setRatingMinDigitado] = useState('');
  const [ratingMin, setRatingMin] = useState('');
  const [valorMinDigitado, setValorMinDigitado] = useState('');
  const [valorMin, setValorMin] = useState('');
  const [versao, setVersao] = useState(0); // incrementa pra forçar re-fetch após reset/importação

  const [resetando, setResetando] = useState(false);
  const [msgReset, setMsgReset] = useState('');
  const [erroReset, setErroReset] = useState('');

  const [importando, setImportando] = useState(false);
  const [tamanhoLote, setTamanhoLote] = useState(LOTES_IMPORTACAO[0]);
  const [msgImportacao, setMsgImportacao] = useState('');
  const [erroImportacao, setErroImportacao] = useState('');

  useEffect(() => {
    const timer = setTimeout(() => { setBusca(buscaDigitada); setPagina(0); }, 400);
    return () => clearTimeout(timer);
  }, [buscaDigitada]);

  useEffect(() => {
    const timer = setTimeout(() => {
      setRatingMin(ratingMinDigitado);
      setValorMin(valorMinDigitado);
      setPagina(0);
    }, 400);
    return () => clearTimeout(timer);
  }, [ratingMinDigitado, valorMinDigitado]);

  useEffect(() => {
    if (!supabaseAtivo) { setCarregando(false); return; }
    (async () => {
      setCarregando(true);
      setErro('');
      const config = ORDENACOES.find(o => o.valor === ordenacao) || ORDENACOES[0];

      let query = supabase
        .from('vw_jogadores_lista')
        .select('id, name, photo_url, age, country_name, country_code, market_value, rating, n_partidas, team_name, team_crest_url, equipe_id', { count: 'exact' });

      if (busca) query = query.ilike('name', `%${busca}%`);
      if (ratingMin !== '') query = query.gte('rating', Number(ratingMin));
      if (valorMin !== '') query = query.gte('market_value', Number(valorMin) * 1_000_000);
      query = query
        .order(config.coluna, { ascending: config.asc, nullsFirst: false })
        .range(pagina * TAMANHO_PAGINA, pagina * TAMANHO_PAGINA + TAMANHO_PAGINA - 1);

      const { data, error, count } = await query;
      if (error) setErro(error.message);
      else { setJogadores(data || []); setTotalRegistros(count || 0); }
      setCarregando(false);
    })();
  }, [pagina, busca, ordenacao, ratingMin, valorMin, versao]);

  const resetarERecalcularRating = async () => {
    if (!window.confirm('Isso apaga TODOS os ratings de jogador e o histórico, e reprocessa do zero com a configuração atual (pode levar dezenas de lotes, alguns minutos). Continuar?')) return;
    setResetando(true); setMsgReset(''); setErroReset('');
    try {
      const respReset = await fetch(apiUrl('/api/model-maintenance?tarefa=player-elo-reset'));
      if (!respReset.ok) throw new Error('Falha no reset.');
      let restantes = null;
      let rodada = 0;
      // Mesma lógica corrigida do painel de Modelos: sem cap arbitrário de
      // rodadas (um limite fixo já causou reset incompleto em produção
      // quando o volume de partidas cresceu). 500 rodadas é só rede de
      // segurança contra loop infinito, nunca deveria ser atingida.
      for (rodada = 1; rodada <= 500; rodada++) {
        const resp = await fetch(apiUrl('/api/model-maintenance?tarefa=player-elo&limite=200'));
        const dados = await resp.json();
        if (!resp.ok) throw new Error(dados.error?.message || 'Falha ao processar lote.');
        restantes = dados.partidas_restantes;
        setMsgReset(`Reprocessando... rodada ${rodada}, ${restantes ?? '?'} partidas restantes.`);
        if (!restantes) break;
      }
      if (restantes) {
        setErroReset(`Parou depois de ${rodada} rodadas com ${restantes} partidas ainda restantes (limite de segurança atingido) — clique de novo pra continuar.`);
      } else {
        setMsgReset(`Rating recalculado com sucesso (${rodada} rodadas).`);
      }
    } catch (e) {
      setErroReset(`${e.message} — o reprocesso pode ser retomado sem perder progresso clicando de novo.`);
    } finally {
      setResetando(false);
      setVersao(v => v + 1);
    }
  };

  const importarEmMassa = async () => {
    setImportando(true); setMsgImportacao(''); setErroImportacao('');
    try {
      const { data: candidatos, error: erroCandidatos } = await supabase
        .from('vw_jogadores_lista')
        .select('id, name')
        .eq('perfil_sincronizado', false)
        .order('market_value', { ascending: false, nullsFirst: false })
        .order('id', { ascending: true })
        .limit(tamanhoLote);
      if (erroCandidatos) throw erroCandidatos;

      if (!candidatos || candidatos.length === 0) {
        setMsgImportacao('Nenhum jogador pendente de importação — todos já têm perfil sincronizado.');
        return;
      }

      let ok = 0, falhas = 0;
      for (let i = 0; i < candidatos.length; i++) {
        const jogador = candidatos[i];
        setMsgImportacao(`Importando ${i + 1}/${candidatos.length}: ${jogador.name || jogador.id}...`);
        try {
          const resp = await fetch(apiUrl(`/api/model-maintenance?tarefa=jogador-perfil&player_id=${jogador.id}`));
          const dados = await resp.json();
          if (!resp.ok || dados.error) throw new Error(dados.error?.message || dados.error || 'falha desconhecida');
          ok++;
        } catch {
          falhas++;
        }
        if (i < candidatos.length - 1) await new Promise(r => setTimeout(r, PACING_IMPORTACAO_MS));
      }
      setMsgImportacao(`Importação concluída: ${ok} jogadores atualizados, ${falhas} falhas.`);
    } catch (e) {
      setErroImportacao(e.message);
    } finally {
      setImportando(false);
      setVersao(v => v + 1);
    }
  };

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
          Dimensão de jogador via FotMob. Idade e valor de mercado são snapshot (última vez sincronizado); o clube exibido é o vínculo ativo na data de hoje quando o jogador já tem perfil avançado sincronizado, com fallback pro último clube visto em escalação.
        </p>
      </div>

      <div className="bg-slate-800 border border-slate-700 rounded-2xl p-4 mb-4 flex flex-col md:flex-row gap-3 md:items-center md:justify-between">
        <div className="flex flex-col sm:flex-row gap-2">
          <button
            onClick={resetarERecalcularRating}
            disabled={resetando || importando}
            className="flex items-center gap-2 px-3 py-2 rounded-lg bg-slate-900 border border-slate-600 text-slate-200 text-sm hover:bg-slate-700 disabled:opacity-50 disabled:cursor-not-allowed"
            title="Apaga e recalcula o rating de todos os jogadores do zero, com a configuração atual"
          >
            {resetando ? <Loader2 className="animate-spin" size={15} /> : <RotateCcw size={15} />}
            Resetar/recalcular rating
          </button>

          <div className="flex items-center gap-2">
            <select
              value={tamanhoLote}
              onChange={(e) => setTamanhoLote(Number(e.target.value))}
              disabled={importando}
              className="bg-slate-900 border border-slate-600 rounded-lg px-2 py-2 text-sm text-slate-100"
            >
              {LOTES_IMPORTACAO.map(n => <option key={n} value={n}>{n}/{n}</option>)}
            </select>
            <button
              onClick={importarEmMassa}
              disabled={importando || resetando}
              className="flex items-center gap-2 px-3 py-2 rounded-lg bg-slate-900 border border-slate-600 text-slate-200 text-sm hover:bg-slate-700 disabled:opacity-50 disabled:cursor-not-allowed"
              title="Importa em lote o perfil avançado (valor de mercado, carreira, títulos) dos próximos jogadores sem essa informação"
            >
              {importando ? <Loader2 className="animate-spin" size={15} /> : <UploadCloud size={15} />}
              Importar em massa
            </button>
          </div>
        </div>
      </div>

      {(msgReset || erroReset) && (
        <div className={`text-sm px-4 py-3 rounded-xl mb-4 ${erroReset ? 'bg-red-950/30 border border-red-600/40 text-red-300' : 'bg-emerald-950/20 border border-emerald-600/30 text-emerald-300'}`}>
          {erroReset || msgReset}
        </div>
      )}
      {(msgImportacao || erroImportacao) && (
        <div className={`text-sm px-4 py-3 rounded-xl mb-4 ${erroImportacao ? 'bg-red-950/30 border border-red-600/40 text-red-300' : 'bg-emerald-950/20 border border-emerald-600/30 text-emerald-300'}`}>
          {erroImportacao || msgImportacao}
        </div>
      )}

      {erro && (
        <div className="bg-red-950/30 border border-red-600/40 text-red-300 text-sm px-4 py-3 rounded-xl mb-4">{erro}</div>
      )}

      <div className="bg-slate-800 border border-slate-700 rounded-2xl p-4 mb-4 flex flex-col sm:flex-row gap-3 flex-wrap">
        <div className="relative flex-1 min-w-[10rem]">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" size={16} />
          <input
            value={buscaDigitada}
            onChange={(e) => setBuscaDigitada(e.target.value)}
            placeholder="Buscar por nome..."
            className="w-full bg-slate-900 border border-slate-600 rounded-lg pl-9 pr-3 py-2 text-sm text-slate-100"
          />
        </div>
        <div className="relative w-32">
          <Star className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500 pointer-events-none" size={14} />
          <input
            type="number"
            value={ratingMinDigitado}
            onChange={(e) => setRatingMinDigitado(e.target.value)}
            placeholder="Rating mín."
            className="w-full bg-slate-900 border border-slate-600 rounded-lg pl-8 pr-3 py-2 text-sm text-slate-100"
          />
        </div>
        <div className="relative w-36">
          <span className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500 text-xs pointer-events-none">€M</span>
          <input
            type="number"
            value={valorMinDigitado}
            onChange={(e) => setValorMinDigitado(e.target.value)}
            placeholder="Valor mín."
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
                  <th className="p-3">Time (atual)</th>
                  <th className="p-3">País</th>
                  <th className="p-3 text-right">Idade</th>
                  <th className="p-3 text-right">Rating</th>
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
                      {j.team_name ? (
                        <TimeCelula nome={j.team_name} crestUrl={j.team_crest_url} equipeId={j.equipe_id} />
                      ) : '—'}
                    </td>
                    <td className="p-3 text-slate-400">
                      {j.country_name || j.country_code || '—'}
                    </td>
                    <td className="p-3 text-right text-slate-300 font-mono">{j.age ?? '—'}</td>
                    <td className="p-3 text-right text-slate-300 font-mono">
                      {j.rating != null ? Math.round(j.rating) : '—'}
                    </td>
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
