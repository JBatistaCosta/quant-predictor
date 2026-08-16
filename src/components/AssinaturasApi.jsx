// src/components/AssinaturasApi.jsx
// Aba de Configurações: gerenciamento manual de contas/assinaturas de APIs
// externas (OddsPapi, API-Football, FotMob etc.) -- cota, data de reinício,
// status. Não existe um jeito automático uniforme de consultar cota entre
// plataformas diferentes (cada uma expõe isso do seu jeito, se expõe), então
// isso é um registro que o usuário mantém manualmente, não uma leitura ao
// vivo. A chave em si fica salva (campo mascarado por padrão) só pra
// facilitar copiar/colar na hora de trocar a variável de ambiente na
// Vercel -- não é sincronizada com a Vercel automaticamente.
import React, { useEffect, useState } from 'react';
import { KeyRound, Plus, X, Loader2, AlertTriangle, Eye, EyeOff, Copy, Check, Pencil, ExternalLink } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';

const SUGESTOES_PLATAFORMA = ['OddsPapi', 'API-Football', 'Football-Data.org', 'FotMob', 'Footiqo', 'TheSportsDB'];
const SUGESTOES_TIPO_DADOS = ['odds', 'estatísticas', 'escalações', 'jogadores', 'partidas/fixtures'];

const FORM_VAZIO = {
  plataforma: '', tipo_dados: '', nome_conta: '', login: '', chave_api: '',
  variavel_ambiente: '', url_painel: '', cota_diaria: '', cota_semanal: '', cota_mensal: '',
  data_reinicio: '', uso_atual: '', limite_atingido: false, ativa: true, notas: '',
};

function Campo({ label, children }) {
  return (
    <div>
      <label className="block text-xs font-bold text-slate-400 uppercase mb-1">{label}</label>
      {children}
    </div>
  );
}

const classeInput = "w-full bg-slate-900 border border-slate-600 rounded-lg p-2.5 text-sm text-slate-100 placeholder:text-slate-500";

function CampoChave({ valor, onChange }) {
  const [visivel, setVisivel] = useState(false);
  const [copiado, setCopiado] = useState(false);

  const copiar = async () => {
    if (!valor) return;
    await navigator.clipboard.writeText(valor);
    setCopiado(true);
    setTimeout(() => setCopiado(false), 1500);
  };

  return (
    <div className="relative">
      <input
        type={visivel ? 'text' : 'password'}
        value={valor}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Chave / API key"
        className={`${classeInput} pr-16 font-mono`}
        autoComplete="off"
      />
      <div className="absolute right-1.5 top-1/2 -translate-y-1/2 flex gap-1">
        <button type="button" onClick={() => setVisivel((v) => !v)} className="p-1.5 text-slate-500 hover:text-slate-300" title={visivel ? 'Ocultar' : 'Mostrar'}>
          {visivel ? <EyeOff size={14} /> : <Eye size={14} />}
        </button>
        <button type="button" onClick={copiar} className="p-1.5 text-slate-500 hover:text-slate-300" title="Copiar">
          {copiado ? <Check size={14} className="text-emerald-400" /> : <Copy size={14} />}
        </button>
      </div>
    </div>
  );
}

function BadgeStatus({ ativa, limiteAtingido }) {
  if (limiteAtingido) return <span className="text-[10px] font-bold uppercase tracking-wider bg-red-950/40 text-red-300 border border-red-600/40 rounded-full px-2 py-0.5">Cota esgotada</span>;
  if (!ativa) return <span className="text-[10px] font-bold uppercase tracking-wider bg-slate-700/60 text-slate-400 border border-slate-600/40 rounded-full px-2 py-0.5">Inativa</span>;
  return <span className="text-[10px] font-bold uppercase tracking-wider bg-emerald-950/40 text-emerald-300 border border-emerald-600/40 rounded-full px-2 py-0.5">Ativa</span>;
}

function formatarCota(conta) {
  const partes = [];
  if (conta.cota_diaria) partes.push(`${conta.cota_diaria}/dia`);
  if (conta.cota_semanal) partes.push(`${conta.cota_semanal}/sem`);
  if (conta.cota_mensal) partes.push(`${conta.cota_mensal}/mês`);
  if (partes.length === 0) return '—';
  return partes.join(' · ') + (conta.uso_atual != null ? ` (usado: ${conta.uso_atual})` : '');
}

export default function AssinaturasApi() {
  const [contas, setContas] = useState([]);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState('');
  const [mostrarForm, setMostrarForm] = useState(false);
  const [editandoId, setEditandoId] = useState(null);
  const [form, setForm] = useState(FORM_VAZIO);
  const [salvando, setSalvando] = useState(false);

  const buscar = async () => {
    setCarregando(true);
    const { data, error } = await supabase.from('assinaturas_api').select('*').order('plataforma').order('nome_conta');
    if (error) setErro(error.message);
    else setContas(data || []);
    setCarregando(false);
  };

  useEffect(() => { if (supabaseAtivo) buscar(); }, []);

  const abrirNova = () => {
    setForm(FORM_VAZIO);
    setEditandoId(null);
    setMostrarForm(true);
  };

  const abrirEdicao = (conta) => {
    setForm({
      plataforma: conta.plataforma || '', tipo_dados: conta.tipo_dados || '', nome_conta: conta.nome_conta || '',
      login: conta.login || '', chave_api: conta.chave_api || '', variavel_ambiente: conta.variavel_ambiente || '',
      url_painel: conta.url_painel || '',
      cota_diaria: conta.cota_diaria ?? '', cota_semanal: conta.cota_semanal ?? '', cota_mensal: conta.cota_mensal ?? '',
      data_reinicio: conta.data_reinicio || '', uso_atual: conta.uso_atual ?? '',
      limite_atingido: !!conta.limite_atingido, ativa: conta.ativa !== false, notas: conta.notas || '',
    });
    setEditandoId(conta.id);
    setMostrarForm(true);
  };

  const cancelar = () => {
    setMostrarForm(false);
    setEditandoId(null);
    setForm(FORM_VAZIO);
    setErro('');
  };

  const salvar = async (e) => {
    e.preventDefault();
    setSalvando(true);
    setErro('');
    const linha = {
      plataforma: form.plataforma.trim(),
      tipo_dados: form.tipo_dados.trim() || null,
      nome_conta: form.nome_conta.trim() || null,
      login: form.login.trim() || null,
      chave_api: form.chave_api.trim() || null,
      variavel_ambiente: form.variavel_ambiente.trim() || null,
      url_painel: form.url_painel.trim() || null,
      cota_diaria: form.cota_diaria === '' ? null : Number(form.cota_diaria),
      cota_semanal: form.cota_semanal === '' ? null : Number(form.cota_semanal),
      cota_mensal: form.cota_mensal === '' ? null : Number(form.cota_mensal),
      data_reinicio: form.data_reinicio || null,
      uso_atual: form.uso_atual === '' ? null : Number(form.uso_atual),
      limite_atingido: form.limite_atingido,
      ativa: form.ativa,
      notas: form.notas.trim() || null,
      atualizado_em: new Date().toISOString(),
    };

    const { error } = editandoId
      ? await supabase.from('assinaturas_api').update(linha).eq('id', editandoId)
      : await supabase.from('assinaturas_api').insert(linha);

    setSalvando(false);
    if (error) { setErro(error.message); return; }
    cancelar();
    buscar();
  };

  const apagar = async (id) => {
    if (!window.confirm('Apagar essa conta/assinatura? Isso não pode ser desfeito.')) return;
    const { error } = await supabase.from('assinaturas_api').delete().eq('id', id);
    if (error) { setErro(error.message); return; }
    buscar();
  };

  if (!supabaseAtivo) return null;

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <p className="text-slate-400 text-sm max-w-2xl">
          Registro manual das contas/assinaturas de APIs externas (OddsPapi, API-Football, FotMob...) — cota,
          data de reinício e status. A cota não é lida automaticamente (cada plataforma expõe isso do seu jeito,
          quando expõe) — atualize aqui manualmente ao checar o painel de cada uma. A chave fica salva só pra
          facilitar copiar na hora de trocar a variável de ambiente na Vercel; trocar aqui não atualiza a Vercel sozinho.
        </p>
        <button onClick={() => (mostrarForm ? cancelar() : abrirNova())}
          className="shrink-0 flex items-center gap-2 bg-emerald-600 hover:bg-emerald-500 text-white font-bold px-4 py-2.5 rounded-lg text-sm transition-colors">
          {mostrarForm ? <X size={16} /> : <Plus size={16} />} {mostrarForm ? 'Cancelar' : 'Nova conta'}
        </button>
      </div>

      {erro && (
        <div className="bg-red-950/30 border border-red-600/40 text-red-300 text-sm px-4 py-2.5 rounded-lg flex items-start gap-2">
          <AlertTriangle size={16} className="shrink-0 mt-0.5" /> {erro}
        </div>
      )}

      {mostrarForm && (
        <form onSubmit={salvar} className="bg-slate-900 border border-emerald-500/30 rounded-2xl p-5 space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <Campo label="Plataforma">
              <input required list="sugestoes-plataforma" value={form.plataforma}
                onChange={(e) => setForm({ ...form, plataforma: e.target.value })}
                placeholder="Ex: OddsPapi" className={classeInput} />
              <datalist id="sugestoes-plataforma">
                {SUGESTOES_PLATAFORMA.map((p) => <option key={p} value={p} />)}
              </datalist>
            </Campo>
            <Campo label="Tipo de dados">
              <input list="sugestoes-tipo" value={form.tipo_dados}
                onChange={(e) => setForm({ ...form, tipo_dados: e.target.value })}
                placeholder="Ex: odds" className={classeInput} />
              <datalist id="sugestoes-tipo">
                {SUGESTOES_TIPO_DADOS.map((t) => <option key={t} value={t} />)}
              </datalist>
            </Campo>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <Campo label="Nome da conta">
              <input value={form.nome_conta} onChange={(e) => setForm({ ...form, nome_conta: e.target.value })}
                placeholder="Ex: Conta principal, Conta nova ago/2026" className={classeInput} />
            </Campo>
            <Campo label="Login / e-mail">
              <input value={form.login} onChange={(e) => setForm({ ...form, login: e.target.value })}
                placeholder="email@exemplo.com" className={classeInput} />
            </Campo>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <Campo label="Chave / API key">
              <CampoChave valor={form.chave_api} onChange={(v) => setForm({ ...form, chave_api: v })} />
            </Campo>
            <Campo label="Variável de ambiente (Vercel)">
              <input value={form.variavel_ambiente} onChange={(e) => setForm({ ...form, variavel_ambiente: e.target.value })}
                placeholder="Ex: ODDSPAPI_KEY" className={`${classeInput} font-mono`} />
            </Campo>
          </div>

          <Campo label="URL do painel da plataforma (opcional)">
            <input type="url" value={form.url_painel} onChange={(e) => setForm({ ...form, url_painel: e.target.value })}
              placeholder="https://..." className={classeInput} />
          </Campo>

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            <Campo label="Cota diária">
              <input type="number" min="0" value={form.cota_diaria} onChange={(e) => setForm({ ...form, cota_diaria: e.target.value })}
                className={classeInput} />
            </Campo>
            <Campo label="Cota semanal">
              <input type="number" min="0" value={form.cota_semanal} onChange={(e) => setForm({ ...form, cota_semanal: e.target.value })}
                className={classeInput} />
            </Campo>
            <Campo label="Cota mensal">
              <input type="number" min="0" value={form.cota_mensal} onChange={(e) => setForm({ ...form, cota_mensal: e.target.value })}
                className={classeInput} />
            </Campo>
            <Campo label="Uso atual (opcional)">
              <input type="number" min="0" value={form.uso_atual} onChange={(e) => setForm({ ...form, uso_atual: e.target.value })}
                className={classeInput} />
            </Campo>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <Campo label="Data de reinício da cota">
              <input type="date" value={form.data_reinicio} onChange={(e) => setForm({ ...form, data_reinicio: e.target.value })}
                className={classeInput} />
            </Campo>
            <div className="flex items-end gap-6 pb-1">
              <label className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer">
                <input type="checkbox" checked={form.ativa} onChange={(e) => setForm({ ...form, ativa: e.target.checked })}
                  className="w-4 h-4 rounded accent-emerald-500" /> Ativa
              </label>
              <label className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer">
                <input type="checkbox" checked={form.limite_atingido} onChange={(e) => setForm({ ...form, limite_atingido: e.target.checked })}
                  className="w-4 h-4 rounded accent-red-500" /> Cota esgotada
              </label>
            </div>
          </div>

          <Campo label="Notas (opcional)">
            <textarea value={form.notas} onChange={(e) => setForm({ ...form, notas: e.target.value })} rows={2}
              className={classeInput} />
          </Campo>

          <button type="submit" disabled={salvando || !form.plataforma.trim()}
            className="bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white font-bold px-6 py-2.5 rounded-lg text-sm flex items-center gap-2">
            {salvando && <Loader2 size={16} className="animate-spin" />} {editandoId ? 'Salvar alterações' : 'Salvar conta'}
          </button>
        </form>
      )}

      <div className="bg-slate-900 border border-slate-700 rounded-2xl overflow-hidden">
        {carregando ? (
          <p className="text-slate-500 text-center py-10 text-sm">Carregando...</p>
        ) : contas.length === 0 ? (
          <p className="text-slate-500 text-center py-10 text-sm flex items-center justify-center gap-2">
            <KeyRound size={16} /> Nenhuma conta cadastrada ainda.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="bg-slate-800 text-slate-400 text-[10px] uppercase tracking-wider">
                  <th className="p-3">Plataforma</th>
                  <th className="p-3">Conta</th>
                  <th className="p-3">Tipo de dados</th>
                  <th className="p-3">Cota</th>
                  <th className="p-3">Reinício</th>
                  <th className="p-3">Status</th>
                  <th className="p-3"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800">
                {contas.map((c) => (
                  <tr key={c.id} className="hover:bg-slate-800/40">
                    <td className="p-3 font-semibold text-slate-200">
                      {c.plataforma}
                      {c.url_painel && (
                        <a href={c.url_painel} target="_blank" rel="noreferrer" className="inline-block ml-1.5 text-slate-500 hover:text-emerald-400 align-middle">
                          <ExternalLink size={12} />
                        </a>
                      )}
                    </td>
                    <td className="p-3 text-slate-300">
                      {c.nome_conta || '—'}
                      {c.login && <div className="text-slate-500 text-xs">{c.login}</div>}
                    </td>
                    <td className="p-3 text-slate-400">{c.tipo_dados || '—'}</td>
                    <td className="p-3 text-slate-400 font-mono text-xs">{formatarCota(c)}</td>
                    <td className="p-3 text-slate-400">{c.data_reinicio || '—'}</td>
                    <td className="p-3"><BadgeStatus ativa={c.ativa} limiteAtingido={c.limite_atingido} /></td>
                    <td className="p-3 text-right whitespace-nowrap">
                      <button onClick={() => abrirEdicao(c)} className="text-slate-500 hover:text-emerald-400 p-1" title="Editar">
                        <Pencil size={14} />
                      </button>
                      <button onClick={() => apagar(c.id)} className="text-slate-500 hover:text-red-400 p-1" title="Apagar">
                        <X size={14} />
                      </button>
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
