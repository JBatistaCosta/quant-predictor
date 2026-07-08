// src/pages/Times.jsx — Cadastro de times/seleções (instituição + equipe por categoria)
import React, { useState, useEffect } from 'react';
import { Users, Plus, X, Loader2, AlertTriangle, Building2 } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';

const CATEGORIAS = [
  { valor: 'masculino_profissional', rotulo: 'Masculino Profissional' },
  { valor: 'feminino_profissional', rotulo: 'Feminino Profissional' },
  { valor: 'sub23', rotulo: 'Sub-23' },
  { valor: 'sub21', rotulo: 'Sub-21' },
  { valor: 'sub20', rotulo: 'Sub-20' },
  { valor: 'sub17', rotulo: 'Sub-17' },
  { valor: 'master', rotulo: 'Master' },
  { valor: 'futsal', rotulo: 'Futsal' },
];

const FORM_VAZIO = {
  modoInstituicao: 'nova',       // 'nova' | 'existente'
  instituicaoExistenteId: '',
  nomeCompleto: '', nomePopular: '', dataFundacao: '',
  tipo: 'clube', categoria: 'masculino_profissional',
  // detalhes de seleção
  paisTerritorio: '', confederacao: '',
  // detalhes de clube
  cidadeSede: '', paisClube: '',
};

export default function Times() {
  const [equipes, setEquipes] = useState([]);
  const [instituicoes, setInstituicoes] = useState([]);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState('');
  const [mostrarForm, setMostrarForm] = useState(false);
  const [form, setForm] = useState(FORM_VAZIO);
  const [salvando, setSalvando] = useState(false);

  const buscarTudo = async () => {
    setCarregando(true);
    // Busca equipes + o nome popular da instituição de cada uma, numa consulta só
    const { data: eqData, error: eqErro } = await supabase
      .from('equipes')
      .select('id, tipo, categoria, instituicao_id')
      .order('id');
    if (eqErro) { setErro(eqErro.message); setCarregando(false); return; }

    const { data: nomesData } = await supabase
      .from('instituicao_nomes')
      .select('instituicao_id, nome')
      .eq('formalidade', 'popular');

    const nomePorInstituicao = {};
    (nomesData || []).forEach(n => { if (!nomePorInstituicao[n.instituicao_id]) nomePorInstituicao[n.instituicao_id] = n.nome; });

    setEquipes((eqData || []).map(e => ({ ...e, nome: nomePorInstituicao[e.instituicao_id] || '(sem nome)' })));

    const { data: instData } = await supabase.from('instituicoes').select('id');
    setInstituicoes((instData || []).map(i => ({ id: i.id, nome: nomePorInstituicao[i.id] || `Instituição #${i.id}` })));

    setCarregando(false);
  };

  useEffect(() => { if (supabaseAtivo) buscarTudo(); }, []);

  const salvar = async (e) => {
    e.preventDefault();
    setSalvando(true);
    setErro('');

    try {
      let instituicaoId = form.instituicaoExistenteId;

      if (form.modoInstituicao === 'nova') {
        const { data: novaInst, error: instErro } = await supabase
          .from('instituicoes')
          .insert({ data_fundacao: form.dataFundacao || null })
          .select('id')
          .single();
        if (instErro) throw instErro;
        instituicaoId = novaInst.id;

        const nomesParaSalvar = [{ instituicao_id: instituicaoId, tipo: 'original', formalidade: 'popular', nome: form.nomePopular || form.nomeCompleto }];
        if (form.nomeCompleto) nomesParaSalvar.push({ instituicao_id: instituicaoId, tipo: 'original', formalidade: 'completo', nome: form.nomeCompleto });
        const { error: nomeErro } = await supabase.from('instituicao_nomes').insert(nomesParaSalvar);
        if (nomeErro) throw nomeErro;
      }

      if (!instituicaoId) throw new Error('Selecione uma instituição existente ou preencha o nome da nova.');

      const { data: novaEquipe, error: eqErro } = await supabase
        .from('equipes')
        .insert({ instituicao_id: instituicaoId, tipo: form.tipo, categoria: form.categoria })
        .select('id')
        .single();
      if (eqErro) throw eqErro;

      if (form.tipo === 'selecao') {
        await supabase.from('detalhes_selecao').insert({
          equipe_id: novaEquipe.id,
          pais_territorio: form.paisTerritorio || null,
          confederacao: form.confederacao || null,
        });
      } else {
        await supabase.from('detalhes_clube').insert({
          equipe_id: novaEquipe.id,
          cidade_sede: form.cidadeSede || null,
          pais: form.paisClube || null,
        });
      }

      setForm(FORM_VAZIO);
      setMostrarForm(false);
      buscarTudo();
    } catch (err) {
      setErro(err.message);
    } finally {
      setSalvando(false);
    }
  };

  const apagar = async (id) => {
    if (!window.confirm('Apagar essa equipe? A instituição continua existindo (isso só remove essa categoria).')) return;
    const { error } = await supabase.from('equipes').delete().eq('id', id);
    if (error) { setErro(error.message); return; }
    buscarTudo();
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
      <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 mb-4 flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-extrabold flex items-center gap-3 text-slate-100">
            <Users className="text-emerald-400" size={28} /> Times
          </h1>
          <p className="text-slate-400 mt-1 text-sm">Cada instituição pode ter várias equipes (masculino, feminino, sub-21...).</p>
        </div>
        <button
          onClick={() => setMostrarForm(!mostrarForm)}
          className="flex items-center gap-2 bg-emerald-600 hover:bg-emerald-500 text-white font-bold px-4 py-2.5 rounded-lg transition-colors"
        >
          {mostrarForm ? <X size={18} /> : <Plus size={18} />} {mostrarForm ? 'Cancelar' : 'Nova Equipe'}
        </button>
      </div>

      {erro && (
        <div className="bg-red-950/30 border border-red-600/40 text-red-300 text-sm px-4 py-3 rounded-xl mb-4">{erro}</div>
      )}

      {mostrarForm && (
        <form onSubmit={salvar} className="bg-slate-800 border border-emerald-500/30 rounded-2xl p-6 mb-4 space-y-5">
          <div>
            <label className="block text-xs font-bold text-slate-400 uppercase mb-2 flex items-center gap-1.5">
              <Building2 size={14} /> Instituição
            </label>
            <div className="flex gap-2 mb-3">
              <button type="button" onClick={() => setForm({ ...form, modoInstituicao: 'nova' })}
                className={`px-3 py-1.5 rounded-lg text-xs font-bold ${form.modoInstituicao === 'nova' ? 'bg-emerald-500/20 text-emerald-400' : 'bg-slate-900 text-slate-500'}`}>
                Nova instituição
              </button>
              <button type="button" onClick={() => setForm({ ...form, modoInstituicao: 'existente' })}
                className={`px-3 py-1.5 rounded-lg text-xs font-bold ${form.modoInstituicao === 'existente' ? 'bg-emerald-500/20 text-emerald-400' : 'bg-slate-900 text-slate-500'}`}>
                Instituição existente (nova categoria pra ela)
              </button>
            </div>

            {form.modoInstituicao === 'existente' ? (
              <select required value={form.instituicaoExistenteId} onChange={(e) => setForm({ ...form, instituicaoExistenteId: e.target.value })}
                className="w-full bg-slate-900 border border-slate-600 rounded-lg p-2.5 text-sm text-slate-100">
                <option value="">Selecione...</option>
                {instituicoes.map(i => <option key={i.id} value={i.id}>{i.nome}</option>)}
              </select>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                <div>
                  <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">Nome popular</label>
                  <input required value={form.nomePopular} onChange={(e) => setForm({ ...form, nomePopular: e.target.value })}
                    className="w-full bg-slate-900 border border-slate-600 rounded-lg p-2.5 text-sm text-slate-100" />
                </div>
                <div>
                  <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">Nome completo (opcional)</label>
                  <input value={form.nomeCompleto} onChange={(e) => setForm({ ...form, nomeCompleto: e.target.value })}
                    className="w-full bg-slate-900 border border-slate-600 rounded-lg p-2.5 text-sm text-slate-100" />
                </div>
                <div>
                  <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">Data de fundação</label>
                  <input type="date" value={form.dataFundacao} onChange={(e) => setForm({ ...form, dataFundacao: e.target.value })}
                    className="w-full bg-slate-900 border border-slate-600 rounded-lg p-2.5 text-sm text-slate-100" />
                </div>
              </div>
            )}
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 pt-3 border-t border-slate-700">
            <div>
              <label className="block text-xs font-bold text-slate-400 uppercase mb-1">Tipo</label>
              <select value={form.tipo} onChange={(e) => setForm({ ...form, tipo: e.target.value })}
                className="w-full bg-slate-900 border border-slate-600 rounded-lg p-2.5 text-sm text-slate-100">
                <option value="clube">Clube</option>
                <option value="selecao">Seleção</option>
              </select>
            </div>
            <div>
              <label className="block text-xs font-bold text-slate-400 uppercase mb-1">Categoria</label>
              <select value={form.categoria} onChange={(e) => setForm({ ...form, categoria: e.target.value })}
                className="w-full bg-slate-900 border border-slate-600 rounded-lg p-2.5 text-sm text-slate-100">
                {CATEGORIAS.map(c => <option key={c.valor} value={c.valor}>{c.rotulo}</option>)}
              </select>
            </div>
          </div>

          {form.tipo === 'selecao' ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">País/Território</label>
                <input value={form.paisTerritorio} onChange={(e) => setForm({ ...form, paisTerritorio: e.target.value })}
                  className="w-full bg-slate-900 border border-slate-600 rounded-lg p-2.5 text-sm text-slate-100" />
              </div>
              <div>
                <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">Confederação</label>
                <input value={form.confederacao} onChange={(e) => setForm({ ...form, confederacao: e.target.value })}
                  className="w-full bg-slate-900 border border-slate-600 rounded-lg p-2.5 text-sm text-slate-100" />
              </div>
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">Cidade-sede</label>
                <input value={form.cidadeSede} onChange={(e) => setForm({ ...form, cidadeSede: e.target.value })}
                  className="w-full bg-slate-900 border border-slate-600 rounded-lg p-2.5 text-sm text-slate-100" />
              </div>
              <div>
                <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">País</label>
                <input value={form.paisClube} onChange={(e) => setForm({ ...form, paisClube: e.target.value })}
                  className="w-full bg-slate-900 border border-slate-600 rounded-lg p-2.5 text-sm text-slate-100" />
              </div>
            </div>
          )}

          <button type="submit" disabled={salvando}
            className="bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white font-bold px-6 py-2.5 rounded-lg flex items-center gap-2">
            {salvando && <Loader2 size={16} className="animate-spin" />} Salvar Equipe
          </button>
        </form>
      )}

      <div className="bg-slate-800 border border-slate-700 rounded-2xl overflow-hidden">
        {carregando ? (
          <p className="text-slate-500 text-center py-10 text-sm">Carregando...</p>
        ) : equipes.length === 0 ? (
          <p className="text-slate-500 text-center py-10 text-sm">Nenhuma equipe cadastrada ainda.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="bg-slate-900 text-slate-400 text-[10px] uppercase tracking-wider">
                  <th className="p-3">Instituição</th>
                  <th className="p-3">Categoria</th>
                  <th className="p-3">Tipo</th>
                  <th className="p-3"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-700/50">
                {equipes.map(eq => (
                  <tr key={eq.id} className="hover:bg-slate-700/20">
                    <td className="p-3 font-semibold text-slate-200">{eq.nome}</td>
                    <td className="p-3 text-slate-400">{CATEGORIAS.find(c => c.valor === eq.categoria)?.rotulo || eq.categoria}</td>
                    <td className="p-3 text-slate-400 capitalize">{eq.tipo}</td>
                    <td className="p-3 text-right">
                      <button onClick={() => apagar(eq.id)} className="text-slate-500 hover:text-red-400 text-xs font-bold">Apagar</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
