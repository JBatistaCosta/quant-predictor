// src/pages/Configuracoes.jsx
// Painel de configurações/manutenção do sistema. Primeira seção: importação
// de odds históricas exportadas manualmente da Footiqo (footiqo.com) --
// fonte gratuita que cobre Champions League/Copa Libertadores, que nem
// football-data.co.uk nem OddsPapi nem API-Football cobrem com arquivo
// histórico real (investigado numa sessão anterior, ver CONTEXTO_PROJETO.md).
// O CSV é parseado aqui no navegador; só o array de linhas já parseado vai
// pro servidor (tarefa=importar-odds-footiqo em api/model-maintenance.js),
// que faz o casamento com nossas partidas e grava em odds_market.
import React, { useEffect, useState } from 'react';
import { Settings, Upload, Loader2, AlertTriangle, CheckCircle2, Info } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';
import { useAuth } from '../AuthContext';
import { apiUrl } from '../utils/apiUrl';
import { parseCsv } from '../utils/parseCsv';

const COLUNAS_ESPERADAS = ['matchDate', 'homeTeam', 'awayTeam', 'H', 'D', 'A'];

function ImportacaoFootiqo() {
  const { session } = useAuth();
  const [ligas, setLigas] = useState([]);
  const [ligaId, setLigaId] = useState('');
  const [arquivo, setArquivo] = useState(null);
  const [linhas, setLinhas] = useState(null);
  const [previewInfo, setPreviewInfo] = useState(null);
  const [erro, setErro] = useState('');
  const [importando, setImportando] = useState(false);
  const [resultado, setResultado] = useState(null);

  useEffect(() => {
    if (!supabaseAtivo) return;
    supabase.from('leagues').select('id, name').order('name').then(({ data }) => setLigas(data || []));
  }, []);

  const lerArquivo = (e) => {
    const file = e.target.files?.[0];
    setErro(''); setResultado(null); setLinhas(null); setPreviewInfo(null);
    setArquivo(file || null);
    if (!file) return;

    const reader = new FileReader();
    reader.onload = () => {
      try {
        const linhasParsed = parseCsv(String(reader.result));
        if (linhasParsed.length === 0) throw new Error('CSV vazio ou não reconhecido.');
        const faltando = COLUNAS_ESPERADAS.filter((c) => !(c in linhasParsed[0]));
        if (faltando.length > 0) throw new Error(`Colunas esperadas faltando no CSV: ${faltando.join(', ')}. Confira se é mesmo um export da Footiqo.`);

        const temporadas = [...new Set(linhasParsed.map((l) => l.Season).filter(Boolean))];
        const ligaFootiqo = linhasParsed[0].League || '(desconhecida)';
        setLinhas(linhasParsed);
        setPreviewInfo({ total: linhasParsed.length, temporadas, ligaFootiqo });
      } catch (err) {
        setErro(err.message);
      }
    };
    reader.onerror = () => setErro('Falha lendo o arquivo.');
    reader.readAsText(file);
  };

  const importar = async () => {
    if (!session) { setErro('Faça login pra importar.'); return; }
    if (!ligaId) { setErro('Selecione a liga (do nosso cadastro) que corresponde a esse CSV.'); return; }
    if (!linhas) { setErro('Selecione um arquivo CSV primeiro.'); return; }

    setImportando(true); setErro(''); setResultado(null);
    try {
      const resp = await fetch(apiUrl('/api/model-maintenance?tarefa=importar-odds-footiqo'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${session.access_token}` },
        body: JSON.stringify({ liga_id: Number(ligaId), linhas }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error?.message || 'Erro desconhecido.');
      setResultado(data);
      setLinhas(null); setPreviewInfo(null); setArquivo(null);
    } catch (err) {
      setErro(err.message);
    } finally {
      setImportando(false);
    }
  };

  return (
    <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 space-y-4">
      <div>
        <h2 className="text-lg font-bold text-slate-100">Footiqo — Importação de Odds</h2>
        <p className="text-slate-400 text-sm mt-1">
          Cobre competições sem fonte gratuita equivalente (Champions League, Copa Libertadores).
          Baixe o CSV de uma liga+temporada em <span className="text-slate-300">footiqo.com/database/leagues</span> (botão Export, sem login)
          e envie aqui — uma temporada por vez.
        </p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div>
          <label className="block text-xs font-bold text-slate-400 uppercase mb-1">Liga (nosso cadastro)</label>
          <select value={ligaId} onChange={(e) => setLigaId(e.target.value)}
            className="w-full bg-slate-900 border border-slate-600 rounded-lg p-2.5 text-sm text-slate-100">
            <option value="">Selecione...</option>
            {ligas.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
          </select>
        </div>
        <div>
          <label className="block text-xs font-bold text-slate-400 uppercase mb-1">Arquivo CSV da Footiqo</label>
          <input type="file" accept=".csv,text/csv,text/plain,text/comma-separated-values,application/csv,application/vnd.ms-excel,application/octet-stream" onChange={lerArquivo}
            className="w-full bg-slate-900 border border-slate-600 rounded-lg p-2 text-sm text-slate-300 file:mr-3 file:py-1.5 file:px-3 file:rounded-md file:border-0 file:bg-slate-700 file:text-slate-200 file:text-xs file:font-bold" />
        </div>
      </div>

      {previewInfo && (
        <div className="bg-slate-900 border border-slate-700 rounded-lg px-4 py-3 text-sm text-slate-300 flex items-start gap-2">
          <Info size={16} className="text-blue-400 shrink-0 mt-0.5" />
          <span>
            <strong>{arquivo?.name}</strong>: {previewInfo.total} partida(s), liga na Footiqo "<strong>{previewInfo.ligaFootiqo}</strong>",
            temporada(s) {previewInfo.temporadas.join(', ')}. Confira se a liga selecionada acima corresponde antes de importar.
          </span>
        </div>
      )}

      {erro && (
        <div className="bg-red-950/30 border border-red-600/40 text-red-300 text-sm px-4 py-2.5 rounded-lg flex items-start gap-2">
          <AlertTriangle size={16} className="shrink-0 mt-0.5" /> {erro}
        </div>
      )}

      <button onClick={importar} disabled={importando || !linhas || !ligaId}
        className="bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 text-white font-bold px-5 py-2.5 rounded-lg text-sm flex items-center gap-2">
        {importando ? <Loader2 size={16} className="animate-spin" /> : <Upload size={16} />} Importar pra odds_market
      </button>

      {resultado && (
        <div className="bg-emerald-950/30 border border-emerald-600/40 rounded-xl p-4 space-y-2">
          <div className="flex items-center gap-2 text-emerald-300 font-bold text-sm">
            <CheckCircle2 size={16} /> {resultado.odds_gravadas} linha(s) de odds gravadas
          </div>
          <div className="text-xs text-slate-400 space-y-0.5">
            <p>{resultado.linhas_recebidas} partida(s) no arquivo · {resultado.sem_correspondencia} sem correspondência · {resultado.sem_odds_na_fonte} casadas sem odds na fonte · {resultado.ja_tinham_odds} já tinham odds gravadas antes.</p>
          </div>
          {resultado.sem_correspondencia_nomes?.length > 0 && (
            <div className="text-xs text-orange-300 bg-orange-950/20 border border-orange-600/30 rounded-lg px-3 py-2">
              <strong>Times sem correspondência</strong> (nenhuma odd gravada pras partidas deles — avise pra eu adicionar um alias):
              <div className="mt-1 flex flex-wrap gap-1.5">
                {resultado.sem_correspondencia_nomes.map((n) => (
                  <span key={n} className="font-mono bg-slate-900 px-1.5 py-0.5 rounded">{n}</span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function Configuracoes() {
  if (!supabaseAtivo) {
    return (
      <div className="max-w-4xl mx-auto bg-slate-800 border border-red-500/30 rounded-2xl p-6 text-center">
        <AlertTriangle className="text-red-400 mx-auto mb-2" size={28} />
        <p className="text-slate-300">Supabase não configurado.</p>
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto space-y-4">
      <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6">
        <h1 className="text-2xl font-extrabold flex items-center gap-3 text-slate-100">
          <Settings className="text-emerald-400" size={28} /> Configurações
        </h1>
        <p className="text-slate-400 mt-1 text-sm">Manutenção e importações administrativas do sistema.</p>
      </div>

      <ImportacaoFootiqo />
    </div>
  );
}
