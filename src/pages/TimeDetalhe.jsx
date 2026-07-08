// src/pages/TimeDetalhe.jsx
// Inspirada na página de clube do Sofascore: escudo, dados básicos, e os eventos
// (jogos) que o USUÁRIO cadastrou envolvendo esse time — diferente do Sofascore,
// que mostra todo jogo oficial, aqui só entra o que você mesmo analisou/salvou.
import React, { useState, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import { Shield, Calendar, ArrowLeft, AlertTriangle, MapPin, Flag } from 'lucide-react';
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

      if (eq.tipo === 'selecao') {
        const { data: d } = await supabase.from('detalhes_selecao').select('*').eq('equipe_id', id).maybeSingle();
        setDetalhes(d);
      } else {
        const { data: d } = await supabase.from('detalhes_clube').select('*').eq('equipe_id', id).maybeSingle();
        setDetalhes(d);
      }

      // Eventos onde esse time é mandante OU visitante
      const { data: evData } = await supabase
        .from('eventos')
        .select('*')
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

      setCarregando(false);
    })();
  }, [id]);

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
      <div className="flex items-center gap-2">
        <span className="text-slate-500 text-xs">{ehMandante ? 'vs' : '@'}</span>
        <span className="font-semibold text-slate-200">{adversario || '(desconhecido)'}</span>
      </div>
      <div className="flex items-center gap-3 text-slate-400">
        {ev.data_evento && <span className="text-xs">{new Date(ev.data_evento).toLocaleDateString('pt-BR')}</span>}
        {placar && <span className="font-mono font-bold text-slate-200">{placar}</span>}
      </div>
    </div>
  );
}
