// src/pages/LigaDetalhe.jsx
// Jogos reais (ingeridos pelo pipeline Python) de uma liga cadastrada — só
// funciona pra ligas vinculadas ao pipeline (ligas.external_id preenchido,
// que casa com leagues.external_id). Seletor de temporada + separação por
// fase/rodada + paginação por bloco de rodadas (a lista inteira de uma
// temporada pode ter 380 jogos, não dá pra jogar tudo na tela de uma vez).
import React, { useState, useEffect, useMemo } from 'react';
import { useParams, Link } from 'react-router-dom';
import { Trophy, ArrowLeft, AlertTriangle, ChevronLeft, ChevronRight, Shield, ArrowRight, ListOrdered, CalendarRange } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';
import WidgetOddsTheOddsAPI from '../components/WidgetOddsTheOddsAPI';

const RODADAS_POR_PAGINA = 4;

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

  // 1) Carrega a liga (cadastro manual) e resolve a liga correspondente no pipeline via external_id
  useEffect(() => {
    if (!supabaseAtivo) return;
    (async () => {
      setCarregandoLiga(true);
      setErro('');
      const { data: l, error: lErro } = await supabase.from('ligas').select('*').eq('id', id).single();
      if (lErro) { setErro('Liga não encontrada.'); setCarregandoLiga(false); return; }
      setLiga(l);

      if (!l.external_id) { setCarregandoLiga(false); return; }

      const { data: pipelineLiga } = await supabase.from('leagues').select('id').eq('external_id', l.external_id).maybeSingle();
      if (pipelineLiga) {
        setLeagueIdPipeline(pipelineLiga.id);
        // O Supabase (PostgREST) corta em 1000 linhas por chamada sem paginar —
        // uma liga com 8 temporadas facilmente passa de 1000 jogos, e sem
        // ORDER BY as primeiras 1000 linhas tendem a ser as mais ANTIGAS (ordem
        // de inserção), fazendo as temporadas mais novas nunca aparecerem no
        // seletor (só nas páginas de time, que buscam por outro caminho). Pagina
        // de verdade só a coluna season (leve) até cobrir todos os jogos.
        const temporadasData = [];
        let pagina = 0;
        while (true) {
          const { data } = await supabase.from('matches').select('season').eq('league_id', pipelineLiga.id).range(pagina * 1000, pagina * 1000 + 999);
          temporadasData.push(...(data || []));
          if (!data || data.length < 1000) break;
          pagina++;
        }
        const unicas = [...new Set(temporadasData.map(t => t.season))].sort().reverse();
        setTemporadas(unicas);
        if (unicas.length > 0) setTemporada(unicas[0]);
      }
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

      {!leagueIdPipeline ? (
        <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 text-center">
          <p className="text-slate-500 text-sm">
            Essa liga não está vinculada ao pipeline de dados (sem jogos importados) — só ligas com <code className="text-slate-400">external_id</code> preenchido (as espelhadas de <code className="text-slate-400">leagues</code>) têm jogos aqui.
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
