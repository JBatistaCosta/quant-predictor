// src/pages/TimeDetalhe.jsx
// Inspirada na página de clube do Sofascore: escudo, dados básicos, e os eventos
// (jogos) que o USUÁRIO cadastrou envolvendo esse time — diferente do Sofascore,
// que mostra todo jogo oficial, aqui só entra o que você mesmo analisou/salvou.
import React, { useState, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import { Shield, Calendar, ArrowLeft, AlertTriangle, MapPin, Flag, BarChart3, History, ChevronLeft, ChevronRight } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';

const CATEGORIAS_ROTULO = {
  masculino_profissional: 'Masculino Profissional',
  feminino_profissional: 'Feminino Profissional',
  sub23: 'Sub-23', sub21: 'Sub-21', sub20: 'Sub-20', sub17: 'Sub-17',
  master: 'Master', futsal: 'Futsal',
};

export default function TimeDetalhe() {
  const { id } = useParams();
  const [equipe, setEquipe] = useState(null);
  const [instituicao, setInstituicao] = useState(null);
  const [detalhes, setDetalhes] = useState(null);
  const [escudoUrl, setEscudoUrl] = useState(null);
  const [eventos, setEventos] = useState([]);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState('');

  // Dados do time "real" do pipeline Python (tabela teams), quando vinculado
  const [timePipeline, setTimePipeline] = useState(null);
  const [forcas, setForcas] = useState([]);
  const [mediasReais, setMediasReais] = useState(null);
  const [carregandoPipeline, setCarregandoPipeline] = useState(false);

  // Histórico de jogos reais (matches, do pipeline), paginado
  const TAMANHO_PAGINA_JOGOS = 15;
  const [jogosPipeline, setJogosPipeline] = useState([]);
  const [totalJogosPipeline, setTotalJogosPipeline] = useState(0);
  const [paginaJogos, setPaginaJogos] = useState(0);
  const [carregandoJogosPipeline, setCarregandoJogosPipeline] = useState(false);

  useEffect(() => {
    if (!supabaseAtivo) return;
    (async () => {
      setCarregando(true);
      setErro('');

      const { data: eq, error: eqErro } = await supabase.from('equipes').select('*').eq('id', id).single();
      if (eqErro) { setErro('Time não encontrado.'); setCarregando(false); return; }
      setEquipe(eq);

      const { data: inst } = await supabase.from('instituicoes').select('*').eq('id', eq.instituicao_id).single();
      setInstituicao(inst);

      const { data: nomes } = await supabase.from('instituicao_nomes').select('nome').eq('instituicao_id', eq.instituicao_id).eq('formalidade', 'popular').limit(1);
      if (nomes?.[0]) setInstituicao(prev => ({ ...prev, nome: nomes[0].nome }));

      const { data: simbolo } = await supabase.from('instituicao_simbolos').select('url').eq('instituicao_id', eq.instituicao_id).is('data_fim', null).limit(1);
      if (simbolo?.[0]) setEscudoUrl(simbolo[0].url);
      else if (eq.pipeline_team_id) {
        // Sem escudo cadastrado à mão — usa o do time do pipeline, se o vínculo existir
        const { data: teamCrest } = await supabase.from('teams').select('crest_url').eq('id', eq.pipeline_team_id).maybeSingle();
        if (teamCrest?.crest_url) setEscudoUrl(teamCrest.crest_url);
      }

      if (eq.tipo === 'selecao') {
        const { data: d } = await supabase.from('detalhes_selecao').select('*').eq('equipe_id', id).maybeSingle();
        setDetalhes(d);
      } else {
        const { data: d } = await supabase.from('detalhes_clube').select('*').eq('equipe_id', id).maybeSingle();
        setDetalhes(d);
      }

      // Eventos onde esse time é mandante OU visitante (já traz o nome da fase, se houver)
      const { data: evData } = await supabase
        .from('eventos')
        .select('*, fases(nome)')
        .or(`equipe_mandante_id.eq.${id},equipe_visitante_id.eq.${id}`)
        .order('data_evento', { ascending: false });

      // Busca os nomes dos adversários (e do próprio time) numa consulta só
      const idsEnvolvidos = new Set();
      (evData || []).forEach(e => { idsEnvolvidos.add(e.equipe_mandante_id); idsEnvolvidos.add(e.equipe_visitante_id); });
      let nomesPorEquipe = {};
      if (idsEnvolvidos.size > 0) {
        const { data: vwData } = await supabase.from('vw_equipes_completo').select('id, nome_popular').in('id', Array.from(idsEnvolvidos));
        (vwData || []).forEach(v => { nomesPorEquipe[v.id] = v.nome_popular; });
      }
      setEventos((evData || []).map(e => ({ ...e, nome_mandante: nomesPorEquipe[e.equipe_mandante_id], nome_visitante: nomesPorEquipe[e.equipe_visitante_id] })));

      // Se essa equipe estiver vinculada a um time real do pipeline Python,
      // busca as forças treinadas (Dixon-Coles/GLM) e as médias reais recentes.
      if (eq.pipeline_team_id) {
        setCarregandoPipeline(true);
        const { data: teamRow } = await supabase.from('teams').select('*').eq('id', eq.pipeline_team_id).maybeSingle();
        setTimePipeline(teamRow);

        const { data: strengths } = await supabase
          .from('team_strengths')
          .select('*')
          .eq('team_id', eq.pipeline_team_id)
          .order('updated_at', { ascending: false });
        setForcas(strengths || []);

        const { data: statsRows } = await supabase
          .from('match_stats')
          .select('xg, shots, shots_on_target, corners, match_id, matches!inner(match_date)')
          .eq('team_id', eq.pipeline_team_id)
          .order('match_date', { foreignTable: 'matches', ascending: false })
          .limit(10);
        if (statsRows && statsRows.length > 0) {
          const mediaCampo = (campo) => {
            const vals = statsRows.map(s => s[campo]).filter(v => v !== null && v !== undefined);
            return vals.length > 0 ? vals.reduce((a, b) => a + Number(b), 0) / vals.length : null;
          };
          setMediasReais({
            xg: mediaCampo('xg'), shots: mediaCampo('shots'),
            shots_on_target: mediaCampo('shots_on_target'), corners: mediaCampo('corners'),
            n: statsRows.length,
          });
        }
        setCarregandoPipeline(false);
      }

      setCarregando(false);
    })();
  }, [id]);

  // Histórico de jogos reais (public.matches) do time vinculado — separado do
  // efeito principal pra paginar sem recarregar o resto da página.
  useEffect(() => {
    if (!equipe?.pipeline_team_id) return;
    (async () => {
      setCarregandoJogosPipeline(true);
      const teamId = equipe.pipeline_team_id;
      const { data, count } = await supabase
        .from('matches')
        .select('id, match_date, home_goals, away_goals, round, stage, season, home:teams!matches_home_team_id_fkey(id, name, crest_url), away:teams!matches_away_team_id_fkey(id, name, crest_url), leagues(name)', { count: 'exact' })
        .or(`home_team_id.eq.${teamId},away_team_id.eq.${teamId}`)
        .order('match_date', { ascending: false })
        .range(paginaJogos * TAMANHO_PAGINA_JOGOS, paginaJogos * TAMANHO_PAGINA_JOGOS + TAMANHO_PAGINA_JOGOS - 1);
      setJogosPipeline(data || []);
      setTotalJogosPipeline(count || 0);
      setCarregandoJogosPipeline(false);
    })();
  }, [equipe?.pipeline_team_id, paginaJogos]);

  if (!supabaseAtivo) {
    return (
      <div className="max-w-4xl mx-auto bg-slate-800 border border-red-500/30 rounded-2xl p-6 text-center">
        <AlertTriangle className="text-red-400 mx-auto mb-2" size={28} />
        <p className="text-slate-300">Supabase não configurado.</p>
      </div>
    );
  }

  if (carregando) {
    return <div className="max-w-4xl mx-auto text-slate-500 text-center py-16 text-sm">Carregando...</div>;
  }

  if (erro) {
    return (
      <div className="max-w-4xl mx-auto bg-slate-800 border border-red-500/30 rounded-2xl p-6 text-center">
        <AlertTriangle className="text-red-400 mx-auto mb-2" size={28} />
        <p className="text-slate-300">{erro}</p>
        <Link to="/times" className="text-emerald-400 text-sm hover:underline mt-3 inline-block">← Voltar pra Times</Link>
      </div>
    );
  }

  const eventosResolvidos = eventos.filter(e => e.resolvido);
  const eventosPendentes = eventos.filter(e => !e.resolvido);

  return (
    <div className="max-w-4xl mx-auto">
      <Link to="/times" className="flex items-center gap-1.5 text-slate-400 hover:text-slate-200 text-sm mb-4 w-fit">
        <ArrowLeft size={16} /> Voltar
      </Link>

      {/* Cabeçalho: escudo, nome, dados básicos */}
      <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 mb-4 flex items-start gap-5">
        <div className="w-16 h-16 rounded-xl bg-slate-900 border border-slate-700 flex items-center justify-center shrink-0 overflow-hidden">
          {escudoUrl ? <img src={escudoUrl} alt="" className="w-full h-full object-contain" /> : <Shield className="text-slate-600" size={28} />}
        </div>
        <div className="flex-1 min-w-0">
          <h1 className="text-2xl font-extrabold text-slate-100">{instituicao?.nome || `Equipe #${id}`}</h1>
          <div className="flex flex-wrap items-center gap-2 mt-2">
            <span className="text-xs font-bold px-2 py-1 rounded bg-slate-900 text-slate-300 capitalize">{equipe.tipo}</span>
            {equipe.categoria !== 'masculino_profissional' && (
              <span className="text-xs font-bold px-2 py-1 rounded bg-emerald-500/20 text-emerald-400">{CATEGORIAS_ROTULO[equipe.categoria] || equipe.categoria}</span>
            )}
            {!instituicao?.ativo && (
              <span className="text-xs font-bold px-2 py-1 rounded bg-red-950/40 text-red-400">Inativo</span>
            )}
          </div>
          <div className="flex flex-wrap gap-x-5 gap-y-1 mt-3 text-sm text-slate-400">
            {equipe.tipo === 'selecao' && detalhes?.confederacao && (
              <span className="flex items-center gap-1.5"><Flag size={14} /> {detalhes.confederacao}</span>
            )}
            {equipe.tipo === 'selecao' && detalhes?.pais_territorio && (
              <span className="flex items-center gap-1.5"><MapPin size={14} /> {detalhes.pais_territorio}</span>
            )}
            {equipe.tipo === 'clube' && detalhes?.cidade_sede && (
              <span className="flex items-center gap-1.5"><MapPin size={14} /> {detalhes.cidade_sede}{detalhes.pais ? `, ${detalhes.pais}` : ''}</span>
            )}
            {equipe.tipo === 'clube' && detalhes?.estadio && (
              <span>🏟️ {detalhes.estadio}</span>
            )}
            {instituicao?.data_fundacao && (
              <span>Fundado em {new Date(instituicao.data_fundacao).toLocaleDateString('pt-BR')}</span>
            )}
          </div>
        </div>
      </div>

      {/* Dados do modelo (pipeline Python: Dixon-Coles / GLM stats_glm_v1) */}
      {equipe.pipeline_team_id && (
        <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 mb-4">
          <h2 className="text-sm font-bold text-slate-300 uppercase tracking-wider mb-4 flex items-center gap-2">
            <BarChart3 className="text-emerald-400" size={18} /> Dados do modelo
            {timePipeline && <span className="text-slate-500 font-normal normal-case">— vinculado a "{timePipeline.name}"</span>}
          </h2>

          {carregandoPipeline ? (
            <p className="text-slate-500 text-sm">Carregando...</p>
          ) : (
            <div className="space-y-4">
              <div>
                <span className="text-[10px] uppercase font-bold text-slate-500 block mb-2">Força treinada (ataque/defesa)</span>
                {forcas.length === 0 ? (
                  <p className="text-slate-500 text-sm bg-slate-900 border border-slate-700/50 rounded-lg p-3">
                    Ainda não treinado — <code className="text-slate-400">team_strengths</code> não tem linha pra esse time.
                    Rode o pipeline (<code className="text-slate-400">modelo_dixon_coles.py</code> / <code className="text-slate-400">modelo_stats_esperadas.py</code>) e persista o resultado no Supabase.
                  </p>
                ) : (
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    {forcas.map(f => (
                      <div key={f.id} className="bg-slate-900 border border-slate-700/50 rounded-lg p-3 text-sm">
                        <div className="flex items-center justify-between mb-1">
                          <span className="font-semibold text-slate-200">{f.stat || 'gols (Dixon-Coles)'}</span>
                          <span className="text-[10px] text-slate-500">{f.model_name}</span>
                        </div>
                        <div className="flex gap-4 text-slate-400">
                          <span>Ataque: <b className="text-emerald-400">{Number(f.attack).toFixed(3)}</b></span>
                          <span>Defesa: <b className="text-red-400">{Number(f.defense).toFixed(3)}</b></span>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {mediasReais && (
                <div>
                  <span className="text-[10px] uppercase font-bold text-slate-500 block mb-2">Média real — últimas {mediasReais.n} partidas (match_stats)</span>
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-sm">
                    <div className="bg-slate-900 border border-slate-700/50 rounded-lg p-3 text-center">
                      <div className="text-slate-500 text-[10px] uppercase">xG</div>
                      <div className="font-bold text-slate-200">{mediasReais.xg != null ? mediasReais.xg.toFixed(2) : '—'}</div>
                    </div>
                    <div className="bg-slate-900 border border-slate-700/50 rounded-lg p-3 text-center">
                      <div className="text-slate-500 text-[10px] uppercase">Chutes</div>
                      <div className="font-bold text-slate-200">{mediasReais.shots != null ? mediasReais.shots.toFixed(1) : '—'}</div>
                    </div>
                    <div className="bg-slate-900 border border-slate-700/50 rounded-lg p-3 text-center">
                      <div className="text-slate-500 text-[10px] uppercase">No gol</div>
                      <div className="font-bold text-slate-200">{mediasReais.shots_on_target != null ? mediasReais.shots_on_target.toFixed(1) : '—'}</div>
                    </div>
                    <div className="bg-slate-900 border border-slate-700/50 rounded-lg p-3 text-center">
                      <div className="text-slate-500 text-[10px] uppercase">Escanteios</div>
                      <div className="font-bold text-slate-200">{mediasReais.corners != null ? mediasReais.corners.toFixed(1) : '—'}</div>
                    </div>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Histórico de jogos reais (public.matches), quando o time está vinculado ao pipeline */}
      {equipe.pipeline_team_id && (
        <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 mb-4">
          <h2 className="text-sm font-bold text-slate-300 uppercase tracking-wider mb-4 flex items-center gap-2">
            <History className="text-emerald-400" size={18} /> Histórico de Jogos
          </h2>

          {carregandoJogosPipeline ? (
            <p className="text-slate-500 text-sm">Carregando...</p>
          ) : jogosPipeline.length === 0 ? (
            <p className="text-slate-500 text-sm">Nenhum jogo encontrado pra esse time no pipeline.</p>
          ) : (
            <>
              <div className="divide-y divide-slate-700/50">
                {jogosPipeline.map(j => {
                  const ehMandante = j.home?.id === equipe.pipeline_team_id;
                  const adversario = ehMandante ? j.away : j.home;
                  const golsTime = ehMandante ? j.home_goals : j.away_goals;
                  const golsAdversario = ehMandante ? j.away_goals : j.home_goals;
                  const cor = golsTime == null ? 'text-slate-500'
                    : golsTime === golsAdversario ? 'text-slate-400'
                    : golsTime > golsAdversario ? 'text-emerald-400' : 'text-red-400';
                  return (
                    <div key={j.id} className="flex items-center gap-3 py-2.5 text-sm">
                      <span className="text-slate-500 text-xs w-20 shrink-0">{j.match_date?.slice(0, 10)}</span>
                      <span className="text-slate-600 text-xs w-4 shrink-0">{ehMandante ? 'vs' : '@'}</span>
                      {adversario?.crest_url ? <img src={adversario.crest_url} alt="" className="w-5 h-5 object-contain shrink-0" /> : <Shield size={16} className="text-slate-700 shrink-0" />}
                      <span className="flex-1 text-slate-200 truncate">{adversario?.name || '(desconhecido)'}</span>
                      <span className={`font-mono font-bold w-14 text-center shrink-0 ${cor}`}>
                        {golsTime != null ? `${golsTime}-${golsAdversario}` : 'x'}
                      </span>
                      <span className="text-[10px] text-slate-500 w-32 shrink-0 text-right truncate">{j.leagues?.name}{j.round != null ? ` · R${j.round}` : ''}</span>
                    </div>
                  );
                })}
              </div>

              {totalJogosPipeline > TAMANHO_PAGINA_JOGOS && (
                <div className="flex items-center justify-between mt-4 text-xs">
                  <span className="text-slate-500">
                    {paginaJogos * TAMANHO_PAGINA_JOGOS + 1}–{Math.min((paginaJogos + 1) * TAMANHO_PAGINA_JOGOS, totalJogosPipeline)} de {totalJogosPipeline}
                  </span>
                  <div className="flex gap-2">
                    <button disabled={paginaJogos === 0} onClick={() => setPaginaJogos(p => p - 1)}
                      className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-slate-900 border border-slate-700 text-slate-300 disabled:opacity-30 disabled:cursor-not-allowed hover:bg-slate-700">
                      <ChevronLeft size={14} /> Anterior
                    </button>
                    <button disabled={(paginaJogos + 1) * TAMANHO_PAGINA_JOGOS >= totalJogosPipeline} onClick={() => setPaginaJogos(p => p + 1)}
                      className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-slate-900 border border-slate-700 text-slate-300 disabled:opacity-30 disabled:cursor-not-allowed hover:bg-slate-700">
                      Próxima <ChevronRight size={14} />
                    </button>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      )}

      {/* Eventos */}
      <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6">
        <h2 className="text-sm font-bold text-slate-300 uppercase tracking-wider mb-4 flex items-center gap-2">
          <Calendar className="text-emerald-400" size={18} /> Eventos
        </h2>

        {eventos.length === 0 ? (
          <p className="text-slate-500 text-sm text-center py-8">
            Nenhum evento cadastrado envolvendo esse time ainda. Salve uma análise em "Novo Evento" ou "Análise de Evento" pra ver aqui.
          </p>
        ) : (
          <div className="space-y-4">
            {eventosPendentes.length > 0 && (
              <div>
                <span className="text-[10px] uppercase font-bold text-slate-500 block mb-2">Pendentes</span>
                <div className="space-y-2">
                  {eventosPendentes.map(ev => <LinhaEvento key={ev.id} ev={ev} idAtual={Number(id)} />)}
                </div>
              </div>
            )}
            {eventosResolvidos.length > 0 && (
              <div>
                <span className="text-[10px] uppercase font-bold text-slate-500 block mb-2">Resolvidos</span>
                <div className="space-y-2">
                  {eventosResolvidos.map(ev => <LinhaEvento key={ev.id} ev={ev} idAtual={Number(id)} />)}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function LinhaEvento({ ev, idAtual }) {
  const ehMandante = ev.equipe_mandante_id === idAtual;
  const adversario = ehMandante ? ev.nome_visitante : ev.nome_mandante;
  const placar = ev.resolvido && ev.placar_mandante != null
    ? `${ev.placar_mandante} - ${ev.placar_visitante}`
    : null;

  return (
    <div className="flex items-center justify-between bg-slate-900 border border-slate-700/50 rounded-lg px-4 py-3 text-sm">
      <div>
        <div className="flex items-center gap-2">
          <span className="text-slate-500 text-xs">{ehMandante ? 'vs' : '@'}</span>
          <span className="font-semibold text-slate-200">{adversario || '(desconhecido)'}</span>
        </div>
        {ev.fases?.nome && <span className="text-[11px] text-slate-500 ml-5">{ev.fases.nome}</span>}
      </div>
      <div className="flex items-center gap-3 text-slate-400">
        {ev.data_evento && <span className="text-xs">{new Date(ev.data_evento).toLocaleDateString('pt-BR')}</span>}
        {placar && <span className="font-mono font-bold text-slate-200">{placar}</span>}
      </div>
    </div>
  );
}
