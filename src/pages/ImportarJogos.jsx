// src/pages/ImportarJogos.jsx
// Busca jogos (passados e futuros) — de UM time ou de uma LIGA inteira — tenta
// casar os times com o registro já cadastrado, e importa os selecionados pra
// tabela `eventos`, com registro auditado em `importacoes`. Reimportar um jogo
// já existente ATUALIZA o placar/status (upsert de verdade), não só pula.
import React, { useState } from 'react';
import { Download, Loader2, AlertTriangle, Check, Info, RefreshCw } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';
import SeletorEquipe from '../components/SeletorEquipe';

export default function ImportarJogos() {
  const [modo, setModo] = useState('time'); // 'time' | 'liga'
  const [equipe, setEquipe] = useState(null);
  const [nomeLiga, setNomeLiga] = useState('');
  const [temporada, setTemporada] = useState(new Date().getFullYear());
  const [diasPassado, setDiasPassado] = useState(30);
  const [diasFuturo, setDiasFuturo] = useState(30);
  const [buscando, setBuscando] = useState(false);
  const [erro, setErro] = useState('');
  const [jogos, setJogos] = useState(null);
  const [selecionados, setSelecionados] = useState({});
  const [importando, setImportando] = useState(false);
  const [resultadoImportacao, setResultadoImportacao] = useState(null);
  const [fonteInfo, setFonteInfo] = useState(null);

  const buscar = async () => {
    if (modo === 'time' && !equipe) { setErro('Selecione um time primeiro.'); return; }
    if (modo === 'liga' && !nomeLiga.trim()) { setErro('Digite o nome da liga.'); return; }
    setErro('');
    setBuscando(true);
    setJogos(null);
    setResultadoImportacao(null);
    setFonteInfo(null);

    try {
      const url = modo === 'time'
        ? `/api/fixtures?time=${encodeURIComponent(equipe.nome_popular)}&dias_passado=${diasPassado}&dias_futuro=${diasFuturo}`
        : `/api/fixtures?liga=${encodeURIComponent(nomeLiga)}&temporada=${temporada}&dias_passado=${diasPassado}&dias_futuro=${diasFuturo}`;

      const resp = await fetch(url);
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error?.message || 'Erro desconhecido.');
      setFonteInfo({ fonte: data.fonte_usada, aviso: data.aviso });

      // Casamento por ID externo (mais confiável — "Brazil" da API não bate com
      // "Brasil" cadastrado, mas o ID numérico é o mesmo). Só existe ponte salva
      // pro namespace da football-data.org (teams.external_id); ver api/fixtures.js.
      let idPorExternalId = {};
      if (data.fonte_ids === 'football-data.org') {
        const { data: equipesComTeam } = await supabase.from('equipes').select('id, pipeline_team_id').not('pipeline_team_id', 'is', null);
        const teamIds = (equipesComTeam || []).map(e => e.pipeline_team_id);
        if (teamIds.length > 0) {
          const { data: teamsData } = await supabase.from('teams').select('id, external_id').in('id', teamIds).not('external_id', 'is', null);
          const externalIdPorTeamId = {};
          (teamsData || []).forEach(t => { externalIdPorTeamId[t.id] = t.external_id; });
          (equipesComTeam || []).forEach(e => {
            const ext = externalIdPorTeamId[e.pipeline_team_id];
            if (ext) idPorExternalId[ext] = e.id;
          });
        }
      }

      // Casa AMBOS os lados (mandante e visitante) contra o registro — no modo liga
      // não existe "nosso time" fixo, então os dois precisam ser resolvidos.
      // Fallback por nome, pra times sem vínculo com o pipeline (tabela teams).
      const todosNomes = new Set();
      data.jogos.forEach(j => { todosNomes.add(j.mandante); todosNomes.add(j.visitante); });
      let equipesEncontradas = [];
      if (todosNomes.size > 0) {
        const { data: vwData } = await supabase.from('vw_equipes_completo').select('id, nome_popular').in('nome_popular', [...todosNomes]);
        equipesEncontradas = vwData || [];
      }
      const idPorNome = {};
      equipesEncontradas.forEach(e => { idPorNome[e.nome_popular] = e.id; });

      const resolverEquipe = (nome, externalId) => {
        if (externalId && idPorExternalId[externalId]) return { id: idPorExternalId[externalId], por: 'id' };
        if (idPorNome[nome]) return { id: idPorNome[nome], por: 'nome' };
        return { id: null, por: null };
      };

      const jogosComMatch = data.jogos.map(j => {
        const mandante = resolverEquipe(j.mandante, j.mandante_external_id);
        const visitante = resolverEquipe(j.visitante, j.visitante_external_id);
        return {
          ...j,
          equipe_mandante_id: mandante.id,
          equipe_visitante_id: visitante.id,
          mandante_casado: !!mandante.id,
          visitante_casado: !!visitante.id,
          mandante_casado_por: mandante.por,
          visitante_casado_por: visitante.por,
        };
      });

      setJogos(jogosComMatch);
      const preSelecao = {};
      jogosComMatch.forEach(j => { if (j.equipe_mandante_id && j.equipe_visitante_id) preSelecao[j.fixture_id] = true; });
      setSelecionados(preSelecao);
    } catch (e) {
      setErro(e.message);
    } finally {
      setBuscando(false);
    }
  };

  const importar = async () => {
    const idsSelecionados = Object.keys(selecionados).filter(id => selecionados[id]);
    const jogosParaImportar = jogos.filter(j => idsSelecionados.includes(String(j.fixture_id)) && j.equipe_mandante_id && j.equipe_visitante_id);
    if (jogosParaImportar.length === 0) { setErro('Nenhum jogo selecionado (com os dois times identificados) pra importar.'); return; }

    setImportando(true);
    setErro('');

    try {
      const descricaoBusca = modo === 'time' ? equipe.nome_popular : `liga "${nomeLiga}" (${temporada})`;
      const { error: impErro } = await supabase
        .from('importacoes')
        .insert({
          fonte: fonteInfo?.fonte || 'API-Football',
          descricao: `${jogosParaImportar.length} jogos de ${descricaoBusca}`,
          status: 'sucesso',
        });
      if (impErro) throw impErro;

      // Upsert de verdade: jogo novo -> insere; jogo já existente com placar/status
      // diferente -> atualiza; jogo já existente e idêntico -> não faz nada.
      let inseridos = 0, atualizados = 0, inalterados = 0;
      for (const j of jogosParaImportar) {
        const { data: existente } = await supabase
          .from('eventos')
          .select('id, resolvido, placar_mandante, placar_visitante')
          .eq('equipe_mandante_id', j.equipe_mandante_id)
          .eq('equipe_visitante_id', j.equipe_visitante_id)
          .eq('data_evento', j.data)
          .maybeSingle();

        const novoPlacarMandante = j.resolvido ? j.placar_mandante : null;
        const novoPlacarVisitante = j.resolvido ? j.placar_visitante : null;

        if (!existente) {
          const { error } = await supabase.from('eventos').insert({
            equipe_mandante_id: j.equipe_mandante_id,
            equipe_visitante_id: j.equipe_visitante_id,
            data_evento: j.data,
            resolvido: j.resolvido,
            placar_mandante: novoPlacarMandante,
            placar_visitante: novoPlacarVisitante,
          });
          if (!error) inseridos++;
          continue;
        }

        const mudou = existente.resolvido !== j.resolvido
          || existente.placar_mandante !== novoPlacarMandante
          || existente.placar_visitante !== novoPlacarVisitante;

        if (!mudou) { inalterados++; continue; }

        const { error } = await supabase.from('eventos').update({
          resolvido: j.resolvido,
          placar_mandante: novoPlacarMandante,
          placar_visitante: novoPlacarVisitante,
          resolvido_em: j.resolvido ? new Date().toISOString() : null,
        }).eq('id', existente.id);
        if (!error) atualizados++;
      }

      setResultadoImportacao({ inseridos, atualizados, inalterados, total: jogosParaImportar.length });
      setJogos(null);
    } catch (e) {
      setErro(e.message);
    } finally {
      setImportando(false);
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

  return (
    <div className="max-w-4xl mx-auto">
      <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 mb-4">
        <h1 className="text-2xl font-extrabold flex items-center gap-3 text-slate-100">
          <Download className="text-emerald-400" size={28} /> Importar Jogos
        </h1>
        <p className="text-slate-400 mt-1 text-sm">Busca jogos passados e futuros, de um time ou de uma liga inteira. Reimportar atualiza placares em vez de duplicar.</p>
      </div>

      <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 mb-4 space-y-4">
        <div className="flex gap-2">
          <button onClick={() => setModo('time')}
            className={`px-3 py-1.5 rounded-lg text-xs font-bold ${modo === 'time' ? 'bg-emerald-500/20 text-emerald-400' : 'bg-slate-900 text-slate-500'}`}>
            Por Time
          </button>
          <button onClick={() => setModo('liga')}
            className={`px-3 py-1.5 rounded-lg text-xs font-bold ${modo === 'liga' ? 'bg-emerald-500/20 text-emerald-400' : 'bg-slate-900 text-slate-500'}`}>
            Por Liga Inteira
          </button>
        </div>

        {modo === 'time' ? (
          <SeletorEquipe label="Time" selecionado={equipe} onSelecionar={setEquipe} />
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-bold text-slate-400 uppercase mb-1">Nome da liga (em inglês, ex: World Cup)</label>
              <input value={nomeLiga} onChange={(e) => setNomeLiga(e.target.value)} placeholder="World Cup"
                className="w-full bg-slate-900 border border-slate-600 rounded-lg p-2.5 text-sm text-slate-100" />
            </div>
            <div>
              <label className="block text-xs font-bold text-slate-400 uppercase mb-1">Temporada (ano)</label>
              <input type="number" value={temporada} onChange={(e) => setTemporada(e.target.value)}
                className="w-full bg-slate-900 border border-slate-600 rounded-lg p-2.5 text-sm text-slate-100" />
            </div>
          </div>
        )}

        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-xs font-bold text-slate-400 uppercase mb-1">Dias no passado</label>
            <input type="number" min="0" max="365" value={diasPassado} onChange={(e) => setDiasPassado(e.target.value)}
              className="w-full bg-slate-900 border border-slate-600 rounded-lg p-2.5 text-sm text-slate-100" />
          </div>
          <div>
            <label className="block text-xs font-bold text-slate-400 uppercase mb-1">Dias no futuro</label>
            <input type="number" min="0" max="365" value={diasFuturo} onChange={(e) => setDiasFuturo(e.target.value)}
              className="w-full bg-slate-900 border border-slate-600 rounded-lg p-2.5 text-sm text-slate-100" />
          </div>
        </div>

        <button onClick={buscar} disabled={buscando || (modo === 'time' ? !equipe : !nomeLiga.trim())}
          className="bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white font-bold px-6 py-2.5 rounded-lg flex items-center gap-2">
          {buscando && <Loader2 size={16} className="animate-spin" />} Buscar Jogos
        </button>
        {erro && <div className="bg-red-950/30 border border-red-600/40 text-red-300 text-sm px-4 py-2.5 rounded-lg">{erro}</div>}
      </div>

      {resultadoImportacao && (
        <div className="bg-emerald-950/30 border border-emerald-600/40 text-emerald-300 text-sm px-4 py-3 rounded-xl mb-4 flex items-center gap-2">
          <Check size={16} />
          {resultadoImportacao.inseridos} novo(s), {resultadoImportacao.atualizados} atualizado(s), {resultadoImportacao.inalterados} já estava(m) em dia.
        </div>
      )}

      {fonteInfo && (
        <div className={`text-xs px-4 py-2.5 rounded-xl mb-4 flex items-center gap-2 ${fonteInfo.aviso ? 'bg-orange-950/30 border border-orange-600/40 text-orange-300' : 'bg-slate-800 border border-slate-700 text-slate-400'}`}>
          {fonteInfo.aviso ? <AlertTriangle size={14} className="shrink-0" /> : <Info size={14} className="shrink-0" />}
          <span>Fonte usada: <strong>{fonteInfo.fonte}</strong>{fonteInfo.aviso ? ` (fallback — ${fonteInfo.aviso})` : ''}</span>
        </div>
      )}

      {jogos && (
        <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6">
          <div className="flex items-center justify-between mb-3">
            <span className="text-sm font-bold text-slate-300">{jogos.length} jogo(s) encontrado(s)</span>
            <button onClick={importar} disabled={importando}
              className="bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white font-bold px-4 py-2 rounded-lg text-sm flex items-center gap-2">
              {importando ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />} Importar / Atualizar Selecionados
            </button>
          </div>

          {jogos.some(j => !j.mandante_casado || !j.visitante_casado) && (
            <div className="bg-blue-950/30 border border-blue-600/40 text-blue-300 text-xs px-3 py-2 rounded-lg mb-3 flex items-start gap-2">
              <Info size={14} className="shrink-0 mt-0.5" />
              Jogos marcados em laranja têm pelo menos um time que não está no seu registro de Times — não dá pra importar até esses times existirem em "Times".
            </div>
          )}
          {jogos.some(j => j.mandante_casado_por === 'id' || j.visitante_casado_por === 'id') && (
            <div className="bg-emerald-950/30 border border-emerald-600/40 text-emerald-300 text-xs px-3 py-2 rounded-lg mb-3 flex items-start gap-2">
              <Info size={14} className="shrink-0 mt-0.5" />
              Jogos com <span className="font-mono bg-slate-900 px-1 rounded">ID</span> foram casados pelo ID externo do time (mais confiável que nome) — os com <span className="font-mono bg-slate-900 px-1 rounded">nome</span> caíram no casamento por string.
            </div>
          )}

          <div className="space-y-1.5 max-h-96 overflow-y-auto">
            {jogos.map(j => {
              const casado = j.mandante_casado && j.visitante_casado;
              return (
                <label key={j.fixture_id} className={`flex items-center gap-3 px-3 py-2 rounded-lg text-sm ${casado ? 'bg-slate-900' : 'bg-orange-950/20 opacity-60'}`}>
                  <input
                    type="checkbox"
                    checked={!!selecionados[j.fixture_id]}
                    disabled={!casado}
                    onChange={(e) => setSelecionados(prev => ({ ...prev, [j.fixture_id]: e.target.checked }))}
                    className="accent-emerald-500"
                  />
                  <span className="text-slate-500 text-xs w-24 shrink-0">{j.data}</span>
                  <span className="flex-1 text-slate-200">{j.mandante} x {j.visitante}</span>
                  {casado && (
                    <span className={`text-[9px] font-mono px-1.5 py-0.5 rounded shrink-0 ${(j.mandante_casado_por === 'id' && j.visitante_casado_por === 'id') ? 'bg-emerald-500/20 text-emerald-400' : 'bg-slate-700 text-slate-400'}`}>
                      {(j.mandante_casado_por === 'id' && j.visitante_casado_por === 'id') ? 'ID' : 'nome'}
                    </span>
                  )}
                  {j.resolvido && <span className="font-mono text-slate-300">{j.placar_mandante}-{j.placar_visitante}</span>}
                  <span className="text-[10px] text-slate-500 w-28 shrink-0 text-right">{j.rodada || j.liga}</span>
                </label>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
