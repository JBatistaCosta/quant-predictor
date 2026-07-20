// src/pages/LigaDetalhe.jsx
// Jogos reais (ingeridos pelo pipeline) de uma liga cadastrada — só funciona
// pra ligas vinculadas ao pipeline: via ligas.pipeline_league_id (vínculo
// direto, padrão pra ligas novas) ou, em ligas mais antigas que nunca
// ganharam esse vínculo, por ligas.external_id = leagues.external_id
// (fallback, só cobre ligas vindas da football-data.org). Seletor de
// temporada + separação por fase/rodada + paginação por bloco de rodadas (a
// lista inteira de uma temporada pode ter 380 jogos, não dá pra jogar tudo
// na tela de uma vez).
import React, { useState, useEffect, useMemo } from 'react';
import { useParams, Link } from 'react-router-dom';
import { Trophy, ArrowLeft, AlertTriangle, ChevronLeft, ChevronRight, Shield, ArrowRight, ListOrdered, CalendarRange, UploadCloud, Loader2 } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';
import WidgetOddsTheOddsAPI from '../components/WidgetOddsTheOddsAPI';

const RODADAS_POR_PAGINA = 4;
const LOTES_IMPORTACAO_PARTIDAS = [20, 50, 100, 200];
// Limite seguro por CHAMADA (não por clique) — cada partida gasta 1-2
// chamadas externas + várias escritas no banco, e o Vercel corta em 60s.
// O lote escolhido na UI é o alvo total; o loop de rodadas abaixo soma até
// chegar lá (mesmo padrão de "Resetar/recalcular rating" em Jogadores.jsx).
const LIMITE_POR_RODADA = { 'api-football': 30, fotmob: 15 };

const RESULTADO_COR = (mandante, gm, gv) => {
  if (gm == null || gv == null) return 'text-slate-500';
  const empate = gm === gv;
  const mandanteGanhou = gm > gv;
  if (empate) return 'text-slate-400';
  return (mandante && mandanteGanhou) || (!mandante && !mandanteGanhou) ? 'text-emerald-400' : 'text-red-400';
};

// Classificação calculada em cima dos jogos já carregados da temporada (sem
// consulta extra) — pontos corridos padrão (V=3, E=1, D=0), desempate por
// saldo de gols e depois gols pró. Não trata desempate por confronto direto
// (regra oficial de algumas ligas), simplificação aceitável aqui.
function calcularClassificacao(jogos) {
  const tabela = new Map();
  for (const j of jogos) {
    if (j.status !== 'finished' || j.home_goals == null || j.away_goals == null) continue;
    const pares = [
      [j.home, j.home_goals, j.away_goals],
      [j.away, j.away_goals, j.home_goals],
    ];
    for (const [time, golsPro, golsContra] of pares) {
      if (!time) continue;
      if (!tabela.has(time.id)) tabela.set(time.id, { time, j: 0, v: 0, e: 0, d: 0, gp: 0, gc: 0, pts: 0 });
      const linha = tabela.get(time.id);
      linha.j++; linha.gp += golsPro; linha.gc += golsContra;
      if (golsPro > golsContra) { linha.v++; linha.pts += 3; }
      else if (golsPro === golsContra) { linha.e++; linha.pts += 1; }
      else { linha.d++; }
    }
  }
  return [...tabela.values()].sort((a, b) => b.pts - a.pts || (b.gp - b.gc) - (a.gp - a.gc) || b.gp - a.gp);
}

export default function LigaDetalhe() {
  const { id } = useParams();
  const [liga, setLiga] = useState(null);
  const [carregandoLiga, setCarregandoLiga] = useState(true);
  const [erro, setErro] = useState('');

  const [leagueIdPipeline, setLeagueIdPipeline] = useState(null);
  const [temporadas, setTemporadas] = useState([]);
  const [temporada, setTemporada] = useState('');
  const [jogos, setJogos] = useState([]);
  const [carregandoJogos, setCarregandoJogos] = useState(false);
  const [pagina, setPagina] = useState(0);
  const [aba, setAba] = useState('classificacao');

  const [loteImportacao, setLoteImportacao] = useState(LOTES_IMPORTACAO_PARTIDAS[0]);
  const [importando, setImportando] = useState(null); // 'api-football' | 'fotmob' | null
  const [msgImportacao, setMsgImportacao] = useState('');
  const [erroImportacao, setErroImportacao] = useState('');

  const [temporadaNova, setTemporadaNova] = useState('');
  const [criandoJogos, setCriandoJogos] = useState(null); // 'api-football' | 'fotmob' | null
  const [msgCriarJogos, setMsgCriarJogos] = useState('');
  const [erroCriarJogos, setErroCriarJogos] = useState('');

  // O Supabase (PostgREST) corta em 1000 linhas por chamada sem paginar — uma
  // liga com 8 temporadas facilmente passa de 1000 jogos, e sem ORDER BY as
  // primeiras 1000 linhas tendem a ser as mais ANTIGAS (ordem de inserção),
  // fazendo as temporadas mais novas nunca aparecerem no seletor. Pagina de
  // verdade só a coluna season (leve) até cobrir todos os jogos. Extraída da
  // effect original pra também poder ser chamada de novo depois de importar
  // jogos de uma temporada nova (o seletor precisa refletir a temporada
  // recém-criada sem precisar recarregar a página).
  const carregarTemporadas = async (pipelineLigaId) => {
    const temporadasData = [];
    let pagina = 0;
    while (true) {
      const { data } = await supabase.from('matches').select('season').eq('league_id', pipelineLigaId).range(pagina * 1000, pagina * 1000 + 999);
      temporadasData.push(...(data || []));
      if (!data || data.length < 1000) break;
      pagina++;
    }
    const unicas = [...new Set(temporadasData.map(t => t.season))].sort().reverse();
    setTemporadas(unicas);
    return unicas;
  };

  // 1) Carrega a liga (cadastro manual) e resolve a liga correspondente no
  // pipeline — via pipeline_league_id (vínculo direto, preenchido por padrão
  // pra ligas novas desde a importação via FotMob) com fallback pro
  // casamento antigo por external_id (ligas mais antigas, vindas da
  // football-data.org, que nunca ganharam o vínculo direto — ver migration
  // add_pipeline_league_id_ligas).
  useEffect(() => {
    if (!supabaseAtivo) return;
    (async () => {
      setCarregandoLiga(true);
      setErro('');
      const { data: l, error: lErro } = await supabase.from('ligas').select('*').eq('id', id).single();
      if (lErro) { setErro('Liga não encontrada.'); setCarregandoLiga(false); return; }
      setLiga(l);

      let pipelineLigaId = l.pipeline_league_id || null;
      if (!pipelineLigaId && l.external_id) {
        const { data: pipelineLiga } = await supabase.from('leagues').select('id').eq('external_id', l.external_id).maybeSingle();
        pipelineLigaId = pipelineLiga?.id || null;
      }

      if (!pipelineLigaId) { setCarregandoLiga(false); return; }

      setLeagueIdPipeline(pipelineLigaId);
      const unicas = await carregarTemporadas(pipelineLigaId);
      if (unicas.length > 0) setTemporada(unicas[0]);
      setCarregandoLiga(false);
    })();
  }, [id]);

  // 2) Carrega os jogos da temporada selecionada
  useEffect(() => {
    if (!leagueIdPipeline || !temporada) return;
    (async () => {
      setCarregandoJogos(true);
      setPagina(0);
      const { data } = await supabase
        .from('matches')
        .select('id, match_date, home_goals, away_goals, status, round, stage, home:teams!matches_home_team_id_fkey(id, name, crest_url, equipes!equipes_pipeline_team_id_fkey(id)), away:teams!matches_away_team_id_fkey(id, name, crest_url, equipes!equipes_pipeline_team_id_fkey(id))')
        .eq('league_id', leagueIdPipeline)
        .eq('season', temporada)
        .order('match_date', { ascending: true });
      setJogos(data || []);
      setCarregandoJogos(false);
    })();
  }, [leagueIdPipeline, temporada]);

  // Importa/enriquece as partidas da temporada selecionada em lotes — o
  // seletor (20/50/100/200) é o ALVO total do clique, mas cada partida gasta
  // chamada(s) externa(s) pesada(s) + várias escritas no banco, então o
  // Vercel corta bem antes de chegar em 200 numa chamada só. O loop abaixo
  // faz rodadas sucessivas (limite seguro por rodada em LIMITE_POR_RODADA)
  // até acumular o alvo ou a API confirmar que não sobrou mais nada
  // pendente — mesmo padrão de "Resetar/recalcular rating" em Jogadores.jsx.
  const importarPartidas = async (fonte) => {
    if (!leagueIdPipeline || !temporada) return;
    setImportando(fonte); setMsgImportacao(''); setErroImportacao('');
    const rodadaLimite = LIMITE_POR_RODADA[fonte];
    const url = fonte === 'fotmob'
      ? `/api/model-maintenance?tarefa=partidas-fotmob&liga_id=${leagueIdPipeline}&temporada=${encodeURIComponent(temporada)}`
      : `/api/sync-match-stats?liga_id=${leagueIdPipeline}&temporada=${encodeURIComponent(temporada)}`;
    try {
      let totalProcessado = 0;
      let rodada = 0;
      const maxRodadas = Math.ceil(loteImportacao / rodadaLimite) + 3; // rede de segurança contra loop preso
      while (totalProcessado < loteImportacao && rodada < maxRodadas) {
        rodada++;
        const resp = await fetch(`${url}&limite=${rodadaLimite}`);
        const dados = await resp.json();
        if (!resp.ok) throw new Error(dados.error?.message || 'Falha no lote.');
        if (dados.mensagem) { setMsgImportacao(dados.mensagem); break; }
        totalProcessado += dados.processados_agora || 0;
        setMsgImportacao(
          `Rodada ${rodada}: ${totalProcessado}/${loteImportacao} processados` +
          (dados.restantes != null ? ` (restantes na temporada: ${dados.restantes})` : '') +
          (dados.parado_por_rate_limit ? ' — parado por rate limit, retome depois' : '') + '...'
        );
        if (dados.parado_por_rate_limit || !dados.restantes || dados.restantes <= 0 || !dados.processados_agora) break;
      }
      setMsgImportacao(`Importação concluída (${fonte === 'fotmob' ? 'FotMob' : 'API-Football'}): ${totalProcessado} jogo(s) processado(s) na temporada ${temporada}.`);
    } catch (e) {
      setErroImportacao(e.message);
    } finally {
      setImportando(null);
    }
  };

  // Cria os jogos (data/placar/times) de uma temporada que AINDA NÃO está no
  // banco — diferente de importarPartidas acima, que só enriquece jogos já
  // existentes. Ao contrário da enriquecida, criar jogo é barato (1 chamada
  // externa trazendo a temporada inteira + upsert em lotes de 200 já feito
  // no servidor) — cabe inteiro numa única chamada, sem precisar de rounds.
  const criarJogos = async (fonte) => {
    if (!leagueIdPipeline || !temporadaNova.trim()) return;
    setCriandoJogos(fonte); setMsgCriarJogos(''); setErroCriarJogos('');
    const tarefa = fonte === 'fotmob' ? 'importar-jogos-fotmob' : 'importar-jogos-api-football';
    try {
      const resp = await fetch(`/api/model-maintenance?tarefa=${tarefa}&liga_id=${leagueIdPipeline}&temporada=${encodeURIComponent(temporadaNova.trim())}`);
      const dados = await resp.json();
      if (!resp.ok) throw new Error(dados.error?.message || 'Falha ao importar jogos.');
      setMsgCriarJogos(`${dados.sincronizados ?? 0} de ${dados.total_jogos ?? '?'} jogo(s) importado(s) (${fonte === 'fotmob' ? 'FotMob' : 'API-Football'}) pra temporada "${temporadaNova.trim()}".`);
      await carregarTemporadas(leagueIdPipeline);
      setTemporada(String(temporadaNova.trim()));
    } catch (e) {
      setErroCriarJogos(e.message);
    } finally {
      setCriandoJogos(null);
    }
  };

// Nome do mês em pt-BR, usado como agrupamento aproximado quando não há rodada
  // real salva (temporadas 2019-2022 das 5 ligas europeias grandes — a
  // football-data.org só libera rodada de verdade nas 3 temporadas mais
  // recentes no plano grátis; não vale a pena buscar em outra fonte só por isso).
  const MESES = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez'];

  // Agrupa por fase (stage) e rodada (round) quando existir; sem rodada, agrupa
  // por mês da partida como aproximação visual.
  const grupos = useMemo(() => {
    const porChave = new Map();
    for (const j of jogos) {
      if (j.round != null) {
        const chave = `${j.stage || 'Fase única'}__r${j.round}`;
        if (!porChave.has(chave)) porChave.set(chave, { rotulo: `${j.stage || 'Fase única'} — Rodada ${j.round}`, jogos: [] });
        porChave.get(chave).jogos.push(j);
      } else {
        const data = j.match_date ? new Date(j.match_date) : null;
        const chaveMes = data ? `${data.getFullYear()}-${data.getMonth()}` : 'sem-data';
        const rotuloMes = data ? `${j.stage && j.stage !== 'REGULAR_SEASON' ? j.stage + ' — ' : ''}${MESES[data.getMonth()]}/${data.getFullYear()} (sem rodada cadastrada)` : 'Sem data';
        if (!porChave.has(chaveMes)) porChave.set(chaveMes, { rotulo: rotuloMes, jogos: [] });
        porChave.get(chaveMes).jogos.push(j);
      }
    }
    return [...porChave.values()];
  }, [jogos]);

  const totalPaginas = Math.max(1, Math.ceil(grupos.length / RODADAS_POR_PAGINA));
  const gruposPagina = grupos.slice(pagina * RODADAS_POR_PAGINA, (pagina + 1) * RODADAS_POR_PAGINA);
  const classificacao = useMemo(() => calcularClassificacao(jogos), [jogos]);

  if (!supabaseAtivo) {
    return (
      <div className="max-w-4xl mx-auto bg-slate-800 border border-red-500/30 rounded-2xl p-6 text-center">
        <AlertTriangle className="text-red-400 mx-auto mb-2" size={28} />
        <p className="text-slate-300">Supabase não configurado.</p>
      </div>
    );
  }

  if (carregandoLiga) {
    return <div className="max-w-4xl mx-auto text-slate-500 text-center py-16 text-sm">Carregando...</div>;
  }

  if (erro) {
    return (
      <div className="max-w-4xl mx-auto bg-slate-800 border border-red-500/30 rounded-2xl p-6 text-center">
        <AlertTriangle className="text-red-400 mx-auto mb-2" size={28} />
        <p className="text-slate-300">{erro}</p>
        <Link to="/ligas" className="text-emerald-400 text-sm hover:underline mt-3 inline-block">← Voltar pra Ligas</Link>
      </div>
    );
  }

  const ehBrasileirao = liga?.external_id === 'BSA';

  return (
    <div className={ehBrasileirao ? 'max-w-6xl mx-auto flex flex-col lg:flex-row gap-4 items-start' : 'max-w-4xl mx-auto'}>
    <div className="min-w-0 flex-1">
      <Link to="/ligas" className="flex items-center gap-1.5 text-slate-400 hover:text-slate-200 text-sm mb-4 w-fit">
        <ArrowLeft size={16} /> Voltar
      </Link>

      <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 mb-4 flex items-center gap-4">
        <div className="w-14 h-14 rounded-xl bg-slate-900 border border-slate-700 flex items-center justify-center shrink-0 overflow-hidden">
          {liga.simbolo_url ? <img src={liga.simbolo_url} alt="" className="w-full h-full object-contain" /> : <Trophy className="text-slate-600" size={24} />}
        </div>
        <div className="flex-1 min-w-0">
          <h1 className="text-2xl font-extrabold text-slate-100">{liga.nome}</h1>
          <p className="text-slate-500 text-sm mt-0.5">{liga.pais || liga.confederacao || '—'}</p>
        </div>
        {temporadas.length > 0 && (
          <select
            value={temporada}
            onChange={(e) => setTemporada(e.target.value)}
            className="bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 font-semibold"
          >
            {temporadas.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
        )}
      </div>

      {leagueIdPipeline && (
        <div className="bg-slate-800 border border-slate-700 rounded-2xl p-4 mb-4">
          <p className="text-xs font-bold text-slate-400 uppercase tracking-wider mb-2">Importar jogos de uma temporada nova</p>
          <div className="flex flex-wrap items-center gap-2">
            <input
              value={temporadaNova}
              onChange={(e) => setTemporadaNova(e.target.value)}
              disabled={!!criandoJogos}
              placeholder="Temporada (ex: 2024)"
              className="bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 w-40"
            />
            <button
              onClick={() => criarJogos('api-football')}
              disabled={!!criandoJogos || !temporadaNova.trim()}
              className="flex items-center gap-2 px-3 py-2 rounded-lg bg-slate-900 border border-slate-600 text-slate-200 text-sm hover:bg-slate-700 disabled:opacity-50 disabled:cursor-not-allowed"
              title="Traz pro banco os jogos (data/placar/times) dessa temporada via API-Football, se ainda não estiverem importados"
            >
              {criandoJogos === 'api-football' ? <Loader2 className="animate-spin" size={15} /> : <UploadCloud size={15} />}
              Importar jogos (API-Football)
            </button>
            <button
              onClick={() => criarJogos('fotmob')}
              disabled={!!criandoJogos || !temporadaNova.trim()}
              className="flex items-center gap-2 px-3 py-2 rounded-lg bg-slate-900 border border-slate-600 text-slate-200 text-sm hover:bg-slate-700 disabled:opacity-50 disabled:cursor-not-allowed"
              title="Traz pro banco os jogos (data/placar/times) dessa temporada via FotMob, se ainda não estiverem importados"
            >
              {criandoJogos === 'fotmob' ? <Loader2 className="animate-spin" size={15} /> : <UploadCloud size={15} />}
              Importar jogos (FotMob)
            </button>
          </div>
        </div>
      )}
      {(msgCriarJogos || erroCriarJogos) && (
        <div className={`text-sm px-4 py-3 rounded-xl mb-4 ${erroCriarJogos ? 'bg-red-950/30 border border-red-600/40 text-red-300' : 'bg-emerald-950/20 border border-emerald-600/30 text-emerald-300'}`}>
          {erroCriarJogos || msgCriarJogos}
        </div>
      )}

      {leagueIdPipeline && temporadas.length > 0 && (
        <div className="bg-slate-800 border border-slate-700 rounded-2xl p-4 mb-4 flex flex-col sm:flex-row gap-3 sm:items-center sm:justify-between">
          <div className="flex flex-col gap-2 w-full">
            <p className="text-xs font-bold text-slate-400 uppercase tracking-wider">Importar detalhe das partidas da temporada {temporada}</p>
            <div className="flex flex-wrap items-center gap-2">
              <select
                value={loteImportacao}
                onChange={(e) => setLoteImportacao(Number(e.target.value))}
                disabled={!!importando}
                className="bg-slate-900 border border-slate-600 rounded-lg px-2 py-2 text-sm text-slate-100"
              >
                {LOTES_IMPORTACAO_PARTIDAS.map(n => <option key={n} value={n}>{n}/{n}</option>)}
              </select>
              <button
                onClick={() => importarPartidas('api-football')}
                disabled={!!importando}
                className="flex items-center gap-2 px-3 py-2 rounded-lg bg-slate-900 border border-slate-600 text-slate-200 text-sm hover:bg-slate-700 disabled:opacity-50 disabled:cursor-not-allowed"
                title={`Importa/completa chutes, posse, escanteios, faltas, cartões e xG (via API-Football) da temporada ${temporada}`}
              >
                {importando === 'api-football' ? <Loader2 className="animate-spin" size={15} /> : <UploadCloud size={15} />}
                Detalhe (API-Football)
              </button>
              <button
                onClick={() => importarPartidas('fotmob')}
                disabled={!!importando}
                className="flex items-center gap-2 px-3 py-2 rounded-lg bg-slate-900 border border-slate-600 text-slate-200 text-sm hover:bg-slate-700 disabled:opacity-50 disabled:cursor-not-allowed"
                title={`Importa o máximo de detalhe (stats por time/jogador, mapa de chutes, estádio/clima, via FotMob) da temporada ${temporada}`}
              >
                {importando === 'fotmob' ? <Loader2 className="animate-spin" size={15} /> : <UploadCloud size={15} />}
                Detalhe (FotMob)
              </button>
            </div>
          </div>
        </div>
      )}
      {(msgImportacao || erroImportacao) && (
        <div className={`text-sm px-4 py-3 rounded-xl mb-4 ${erroImportacao ? 'bg-red-950/30 border border-red-600/40 text-red-300' : 'bg-emerald-950/20 border border-emerald-600/30 text-emerald-300'}`}>
          {erroImportacao || msgImportacao}
        </div>
      )}

      {!leagueIdPipeline ? (
        <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 text-center">
          <p className="text-slate-500 text-sm">
            Essa liga não está vinculada ao pipeline de dados (sem jogos importados) — use "Importar do FotMob" em <Link to="/ligas" className="text-emerald-400 hover:underline">Ligas</Link> pra trazer os jogos de uma temporada.
          </p>
        </div>
      ) : carregandoJogos ? (
        <div className="text-slate-500 text-center py-16 text-sm">Carregando jogos...</div>
      ) : jogos.length === 0 ? (
        <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 text-center">
          <p className="text-slate-500 text-sm">Nenhum jogo encontrado pra temporada {temporada}.</p>
        </div>
      ) : (
        <>
          <div className="flex gap-2 mb-4">
            <button
              onClick={() => setAba('classificacao')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold ${aba === 'classificacao' ? 'bg-emerald-500/20 text-emerald-400' : 'bg-slate-800 text-slate-500 hover:text-slate-300'}`}
            >
              <ListOrdered size={14} /> Classificação
            </button>
            <button
              onClick={() => setAba('jogos')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold ${aba === 'jogos' ? 'bg-emerald-500/20 text-emerald-400' : 'bg-slate-800 text-slate-500 hover:text-slate-300'}`}
            >
              <CalendarRange size={14} /> Jogos
            </button>
          </div>

          {aba === 'classificacao' ? (
            classificacao.length === 0 ? (
              <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 text-center text-slate-500 text-sm">
                Nenhum jogo finalizado ainda nessa temporada pra montar a classificação.
              </div>
            ) : (
              <div className="bg-slate-800 border border-slate-700 rounded-2xl overflow-hidden">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="bg-slate-900 text-slate-500 text-[10px] uppercase tracking-wider">
                      <th className="text-left p-2.5 pl-4">#</th>
                      <th className="text-left p-2.5">Time</th>
                      <th className="text-center p-2.5">J</th>
                      <th className="text-center p-2.5">V</th>
                      <th className="text-center p-2.5">E</th>
                      <th className="text-center p-2.5">D</th>
                      <th className="text-center p-2.5">GP</th>
                      <th className="text-center p-2.5">GC</th>
                      <th className="text-center p-2.5">SG</th>
                      <th className="text-center p-2.5 pr-4">Pts</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-700/50">
                    {classificacao.map((linha, i) => (
                      <tr key={linha.time.id} className="hover:bg-slate-700/20">
                        <td className="p-2.5 pl-4 text-slate-500">{i + 1}</td>
                        <td className="p-2.5">
                          {linha.time.equipes?.[0]?.id ? (
                            <Link to={`/times/${linha.time.equipes[0].id}`} className="flex items-center gap-2 hover:text-emerald-400">
                              {linha.time.crest_url ? <img src={linha.time.crest_url} alt="" className="w-5 h-5 object-contain shrink-0" /> : <Shield size={16} className="text-slate-700 shrink-0" />}
                              <span className="truncate text-slate-200">{linha.time.name}</span>
                            </Link>
                          ) : (
                            <span className="flex items-center gap-2" title="Esse time ainda não tem vínculo com o cadastro manual (equipes)">
                              {linha.time.crest_url ? <img src={linha.time.crest_url} alt="" className="w-5 h-5 object-contain shrink-0" /> : <Shield size={16} className="text-slate-700 shrink-0" />}
                              <span className="truncate text-slate-200">{linha.time.name}</span>
                            </span>
                          )}
                        </td>
                        <td className="p-2.5 text-center text-slate-400">{linha.j}</td>
                        <td className="p-2.5 text-center text-slate-400">{linha.v}</td>
                        <td className="p-2.5 text-center text-slate-400">{linha.e}</td>
                        <td className="p-2.5 text-center text-slate-400">{linha.d}</td>
                        <td className="p-2.5 text-center text-slate-400">{linha.gp}</td>
                        <td className="p-2.5 text-center text-slate-400">{linha.gc}</td>
                        <td className="p-2.5 text-center text-slate-400">{linha.gp - linha.gc}</td>
                        <td className="p-2.5 pr-4 text-center font-bold text-slate-100">{linha.pts}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          ) : (
            <>
              <div className="space-y-4">
                {gruposPagina.map((g, i) => (
                  <div key={i} className="bg-slate-800 border border-slate-700 rounded-2xl overflow-hidden">
                    <div className="bg-slate-900 px-4 py-2 text-xs font-bold text-slate-400 uppercase tracking-wider">
                      {g.rotulo}
                    </div>
                    <div className="divide-y divide-slate-700/50">
                      {g.jogos.map(j => (
                        <Link key={j.id} to={`/historico/${j.id}`} className="flex items-center gap-3 px-4 py-2.5 text-sm hover:bg-slate-700/20 transition-colors">
                          <span className="text-slate-500 text-xs w-20 shrink-0">{j.match_date?.slice(0, 10)}</span>
                          <div className="flex-1 flex items-center justify-end gap-2 min-w-0">
                            <span className="truncate text-slate-200">{j.home?.name}</span>
                            {j.home?.crest_url ? <img src={j.home.crest_url} alt="" className="w-5 h-5 object-contain shrink-0" /> : <Shield size={16} className="text-slate-700 shrink-0" />}
                          </div>
                          <span className={`font-mono font-bold w-14 text-center shrink-0 ${RESULTADO_COR(true, j.home_goals, j.away_goals)}`}>
                            {j.home_goals != null ? `${j.home_goals}-${j.away_goals}` : 'x'}
                          </span>
                          <div className="flex-1 flex items-center gap-2 min-w-0">
                            {j.away?.crest_url ? <img src={j.away.crest_url} alt="" className="w-5 h-5 object-contain shrink-0" /> : <Shield size={16} className="text-slate-700 shrink-0" />}
                            <span className="truncate text-slate-200">{j.away?.name}</span>
                          </div>
                          <ArrowRight size={14} className="text-slate-600 shrink-0" />
                        </Link>
                      ))}
                    </div>
                  </div>
                ))}
              </div>

              {totalPaginas > 1 && (
                <div className="flex items-center justify-between mt-4 text-sm">
                  <span className="text-slate-500">Bloco {pagina + 1} de {totalPaginas} ({grupos.length} rodadas/fases · {jogos.length} jogos)</span>
                  <div className="flex gap-2">
                    <button disabled={pagina === 0} onClick={() => setPagina(p => p - 1)}
                      className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-slate-800 border border-slate-700 text-slate-300 disabled:opacity-30 disabled:cursor-not-allowed hover:bg-slate-700">
                      <ChevronLeft size={16} /> Anterior
                    </button>
                    <button disabled={pagina + 1 >= totalPaginas} onClick={() => setPagina(p => p + 1)}
                      className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-slate-800 border border-slate-700 text-slate-300 disabled:opacity-30 disabled:cursor-not-allowed hover:bg-slate-700">
                      Próximo <ChevronRight size={16} />
                    </button>
                  </div>
                </div>
              )}
            </>
          )}
        </>
      )}
    </div>
    {ehBrasileirao && <WidgetOddsTheOddsAPI />}
    </div>
  );
}
