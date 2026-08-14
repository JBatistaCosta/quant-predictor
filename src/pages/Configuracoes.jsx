// src/pages/Configuracoes.jsx
// Painel de configurações/manutenção do sistema. Três seções:
// 1) Importação de odds históricas exportadas manualmente da Footiqo
//    (footiqo.com) -- fonte gratuita que cobre Champions League/Copa
//    Libertadores, que nem football-data.co.uk nem OddsPapi nem API-Football
//    cobrem com arquivo histórico real (investigado numa sessão anterior, ver
//    CONTEXTO_PROJETO.md). O CSV é parseado aqui no navegador; só o array de
//    linhas já parseado vai pro servidor (tarefa=importar-odds-footiqo em
//    api/model-maintenance.js), que faz o casamento com nossas partidas e
//    grava em odds_market.
// 2) Disparo do enriquecimento de estatísticas via FotMob (tarefa=
//    partidas-fotmob) pra ligas já vinculadas em liga_fonte_externa --
//    processa em lotes (limite do Vercel), então o frontend chama em rounds
//    sucessivos até `restantes` zerar, mesmo padrão de
//    resetarERecalcularRating em Jogadores.jsx.
// 3) Backfill de odds históricas via OddsPapi (tarefa=odds-historico) pras
//    ligas já mapeadas em liga_oddspapi_tournament -- mesmo padrão de rounds
//    sucessivos até `restantes_estimado` zerar. /v4/historical-odds é grátis
//    (confirmado na doc oficial + testado em produção, ver CONTEXTO_PROJETO.md),
//    então processa TODOS os mercados que a resposta trouxer e TODAS as
//    temporadas pendentes, não só a mais recente -- o único limite real por
//    chamada é o timeout de 60s da function (por isso os rounds).
import React, { useEffect, useState } from 'react';
import { Settings, Upload, Loader2, AlertTriangle, CheckCircle2, Info, Database, TrendingUp } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';
import { useAuth } from '../AuthContext';
import { apiUrl } from '../utils/apiUrl';
import { parseCsv } from '../utils/parseCsv';

const COLUNAS_ESPERADAS = ['matchDate', 'homeTeam', 'awayTeam', 'H', 'D', 'A'];
const MAX_RODADAS_FOTMOB = 500; // rede de segurança contra loop infinito, mesmo padrão de Jogadores.jsx
const MAX_RODADAS_ODDSPAPI = 500;

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

function ImportacaoFotmob() {
  const { session } = useAuth();
  const [ligas, setLigas] = useState([]);
  const [ligaId, setLigaId] = useState('');
  const [temporada, setTemporada] = useState('');
  const [rodando, setRodando] = useState(false);
  const [progresso, setProgresso] = useState('');
  const [erro, setErro] = useState('');
  const [resumo, setResumo] = useState(null);

  useEffect(() => {
    if (!supabaseAtivo) return;
    (async () => {
      const { data: fontes } = await supabase.from('liga_fonte_externa').select('league_id').eq('sistema', 'fotmob');
      const ids = [...new Set((fontes || []).map((f) => f.league_id))];
      if (ids.length === 0) { setLigas([]); return; }
      const { data: ligasData } = await supabase.from('leagues').select('id, name').in('id', ids).order('name');
      setLigas(ligasData || []);
    })();
  }, []);

  const importar = async () => {
    if (!session) { setErro('Faça login pra importar.'); return; }
    if (!ligaId) { setErro('Selecione a liga.'); return; }

    setRodando(true); setErro(''); setResumo(null);
    let totalSucesso = 0, totalProcessados = 0, rodada = 0, restantes = null, liga = '';
    try {
      for (rodada = 1; rodada <= MAX_RODADAS_FOTMOB; rodada++) {
        const params = new URLSearchParams({ tarefa: 'partidas-fotmob', liga_id: ligaId });
        if (temporada.trim()) params.set('temporada', temporada.trim());
        const resp = await fetch(apiUrl(`/api/model-maintenance?${params.toString()}`), {
          headers: { Authorization: `Bearer ${session.access_token}` },
        });
        const dados = await resp.json();
        if (!resp.ok) throw new Error(dados.error?.message || 'Falha ao processar lote.');

        liga = dados.liga || liga;
        totalSucesso += dados.sucesso || 0;
        totalProcessados += dados.processados_agora || 0;
        restantes = dados.restantes ?? 0;
        setProgresso(`Rodada ${rodada}: ${totalSucesso}/${totalProcessados} partidas importadas até agora, ${restantes} restante(s).`);
        if (!restantes) break;
      }
      if (restantes) {
        setErro(`Parou depois de ${rodada} rodadas com ${restantes} partida(s) ainda restantes (limite de segurança atingido) — clique de novo pra continuar.`);
      } else {
        setResumo({ liga, rodadas: rodada, sucesso: totalSucesso, processados: totalProcessados });
      }
    } catch (err) {
      setErro(`${err.message} — pode retomar sem perder progresso clicando de novo.`);
    } finally {
      setRodando(false);
      setProgresso('');
    }
  };

  return (
    <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 space-y-4">
      <div>
        <h2 className="text-lg font-bold text-slate-100">FotMob — Importação de Estatísticas</h2>
        <p className="text-slate-400 text-sm mt-1">
          Busca o detalhe completo (stats por time, jogadores, chutes com xG, contexto) das partidas já
          encerradas de uma liga já vinculada ao FotMob — cobre tanto temporadas anteriores quanto os jogos
          já encerrados da temporada atual. Deixe "Temporada" em branco pra processar tudo que ainda falta;
          preencha (ex: 2024 ou 2024/2025) pra restringir a uma só. Processa em lotes automaticamente até não
          sobrar nada pendente.
        </p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div>
          <label className="block text-xs font-bold text-slate-400 uppercase mb-1">Liga</label>
          <select value={ligaId} onChange={(e) => setLigaId(e.target.value)}
            className="w-full bg-slate-900 border border-slate-600 rounded-lg p-2.5 text-sm text-slate-100">
            <option value="">Selecione...</option>
            {ligas.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
          </select>
          {ligas.length === 0 && (
            <p className="text-xs text-slate-500 mt-1">Nenhuma liga vinculada ao FotMob ainda (liga_fonte_externa).</p>
          )}
        </div>
        <div>
          <label className="block text-xs font-bold text-slate-400 uppercase mb-1">Temporada (opcional)</label>
          <input type="text" value={temporada} onChange={(e) => setTemporada(e.target.value)}
            placeholder="ex: 2024 ou 2024/2025 — em branco = todas"
            className="w-full bg-slate-900 border border-slate-600 rounded-lg p-2.5 text-sm text-slate-100 placeholder:text-slate-500" />
        </div>
      </div>

      {progresso && (
        <div className="bg-slate-900 border border-slate-700 rounded-lg px-4 py-2.5 text-sm text-slate-300 flex items-center gap-2">
          <Loader2 size={16} className="animate-spin text-blue-400 shrink-0" /> {progresso}
        </div>
      )}

      {erro && (
        <div className="bg-red-950/30 border border-red-600/40 text-red-300 text-sm px-4 py-2.5 rounded-lg flex items-start gap-2">
          <AlertTriangle size={16} className="shrink-0 mt-0.5" /> {erro}
        </div>
      )}

      <button onClick={importar} disabled={rodando || !ligaId}
        className="bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 text-white font-bold px-5 py-2.5 rounded-lg text-sm flex items-center gap-2">
        {rodando ? <Loader2 size={16} className="animate-spin" /> : <Database size={16} />} Importar do FotMob
      </button>

      {resumo && (
        <div className="bg-emerald-950/30 border border-emerald-600/40 rounded-xl p-4 text-sm">
          <div className="flex items-center gap-2 text-emerald-300 font-bold">
            <CheckCircle2 size={16} /> {resumo.sucesso}/{resumo.processados} partida(s) importadas — {resumo.liga}
          </div>
          <p className="text-xs text-slate-400 mt-1">Concluído em {resumo.rodadas} rodada(s). Nada mais pendente pra essa liga/temporada.</p>
        </div>
      )}
    </div>
  );
}

function ImportacaoOddsPapiHistorico() {
  const { session } = useAuth();
  const [ligas, setLigas] = useState([]);
  const [ligaId, setLigaId] = useState('');
  const [temporada, setTemporada] = useState('');
  const [rodando, setRodando] = useState(false);
  const [progresso, setProgresso] = useState('');
  const [erro, setErro] = useState('');
  const [resumo, setResumo] = useState(null);

  useEffect(() => {
    if (!supabaseAtivo) return;
    (async () => {
      const { data: fontes } = await supabase.from('liga_oddspapi_tournament').select('league_id, tournament_name');
      const nomesTorneio = Object.fromEntries((fontes || []).map((f) => [f.league_id, f.tournament_name]));
      const ids = Object.keys(nomesTorneio).map(Number);
      if (ids.length === 0) { setLigas([]); return; }
      const { data: ligasData } = await supabase.from('leagues').select('id, name').in('id', ids).order('name');
      setLigas((ligasData || []).map((l) => ({ ...l, torneio: nomesTorneio[l.id] })));
    })();
  }, []);

  const importar = async () => {
    if (!session) { setErro('Faça login pra importar.'); return; }
    if (!ligaId) { setErro('Selecione a liga.'); return; }

    setRodando(true); setErro(''); setResumo(null);
    let totalSucesso = 0, totalProcessados = 0, totalSemHistorico = 0, totalFalhas = 0, rodada = 0, restantes = null, torneio = '';
    try {
      for (rodada = 1; rodada <= MAX_RODADAS_ODDSPAPI; rodada++) {
        const params = new URLSearchParams({ tarefa: 'odds-historico', liga_id: ligaId });
        if (temporada.trim()) params.set('temporada', temporada.trim());
        const resp = await fetch(apiUrl(`/api/model-maintenance?${params.toString()}`), {
          headers: { Authorization: `Bearer ${session.access_token}` },
        });
        const dados = await resp.json();
        if (!resp.ok || dados.error) throw new Error(dados.error?.message || dados.error || 'Falha ao processar lote.');

        torneio = dados.torneio || torneio;
        if (dados.mensagem) { restantes = 0; break; } // "Nenhuma partida pendente encontrada"

        totalSucesso += dados.sucesso || 0;
        totalProcessados += dados.processados_agora || 0;
        totalSemHistorico += dados.sem_historico_na_fonte || 0;
        totalFalhas += dados.falhas?.length || 0;
        restantes = Math.max(dados.restantes_estimado ?? 0, 0);
        setProgresso(`Rodada ${rodada}: ${totalSucesso} partida(s) com odds novas, ${totalSemHistorico} sem histórico na fonte, ${restantes} restante(s).`);
        if ((dados.processados_agora || 0) === 0 || restantes <= 0) break;
      }
      if (restantes > 0) {
        setErro(`Parou depois de ${rodada} rodadas com ${restantes} partida(s) ainda restantes (limite de segurança atingido) — clique de novo pra continuar.`);
      } else {
        setResumo({ torneio, rodadas: rodada, sucesso: totalSucesso, processados: totalProcessados, semHistorico: totalSemHistorico, falhas: totalFalhas });
      }
    } catch (err) {
      setErro(`${err.message} — pode retomar sem perder progresso clicando de novo.`);
    } finally {
      setRodando(false);
      setProgresso('');
    }
  };

  return (
    <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 space-y-4">
      <div>
        <h2 className="text-lg font-bold text-slate-100">OddsPapi — Backfill de Odds Históricas</h2>
        <p className="text-slate-400 text-sm mt-1">
          Busca odds de fechamento (Pinnacle/bet365/betano, todos os mercados que a API trouxer) das partidas já
          encerradas de uma liga já mapeada em <span className="text-slate-300">liga_oddspapi_tournament</span> —
          cobre competições sem outra fonte de odds hoje (Libertadores, Sudamericana, Copa do Brasil, Champions
          League, Club World Cup, Copa América, Eurocopa, Copa do Mundo, Brasileirão Série B). Deixe "Temporada"
          em branco pra processar todas as temporadas pendentes; preencha pra restringir a uma só. Processa em
          lotes automaticamente (limite da function) até não sobrar nada pendente.
        </p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div>
          <label className="block text-xs font-bold text-slate-400 uppercase mb-1">Liga</label>
          <select value={ligaId} onChange={(e) => setLigaId(e.target.value)}
            className="w-full bg-slate-900 border border-slate-600 rounded-lg p-2.5 text-sm text-slate-100">
            <option value="">Selecione...</option>
            {ligas.map((l) => <option key={l.id} value={l.id}>{l.name} ({l.torneio})</option>)}
          </select>
          {ligas.length === 0 && (
            <p className="text-xs text-slate-500 mt-1">Nenhuma liga mapeada em liga_oddspapi_tournament ainda.</p>
          )}
        </div>
        <div>
          <label className="block text-xs font-bold text-slate-400 uppercase mb-1">Temporada (opcional)</label>
          <input type="text" value={temporada} onChange={(e) => setTemporada(e.target.value)}
            placeholder="ex: 2025 — em branco = todas"
            className="w-full bg-slate-900 border border-slate-600 rounded-lg p-2.5 text-sm text-slate-100 placeholder:text-slate-500" />
        </div>
      </div>

      {progresso && (
        <div className="bg-slate-900 border border-slate-700 rounded-lg px-4 py-2.5 text-sm text-slate-300 flex items-center gap-2">
          <Loader2 size={16} className="animate-spin text-blue-400 shrink-0" /> {progresso}
        </div>
      )}

      {erro && (
        <div className="bg-red-950/30 border border-red-600/40 text-red-300 text-sm px-4 py-2.5 rounded-lg flex items-start gap-2">
          <AlertTriangle size={16} className="shrink-0 mt-0.5" /> {erro}
        </div>
      )}

      <button onClick={importar} disabled={rodando || !ligaId}
        className="bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 text-white font-bold px-5 py-2.5 rounded-lg text-sm flex items-center gap-2">
        {rodando ? <Loader2 size={16} className="animate-spin" /> : <TrendingUp size={16} />} Importar da OddsPapi
      </button>

      {resumo && (
        <div className="bg-emerald-950/30 border border-emerald-600/40 rounded-xl p-4 text-sm">
          <div className="flex items-center gap-2 text-emerald-300 font-bold">
            <CheckCircle2 size={16} /> {resumo.sucesso}/{resumo.processados} partida(s) com odds novas — {resumo.torneio}
          </div>
          <p className="text-xs text-slate-400 mt-1">
            Concluído em {resumo.rodadas} rodada(s). {resumo.semHistorico} partida(s) sem histórico disponível na OddsPapi
            {resumo.falhas > 0 ? `, ${resumo.falhas} falha(s) transitória(s) (tentar de novo depois)` : ''}. Nada mais pendente pra essa liga/temporada.
          </p>
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
      <ImportacaoFotmob />
      <ImportacaoOddsPapiHistorico />
    </div>
  );
}
