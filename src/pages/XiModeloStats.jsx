// src/pages/XiModeloStats.jsx
// Resumo de acurácia do modelo de XI titular previsto (scripts/rodar_xi_previsto.py)
// contra a escalação REAL de partidas já jogadas (match_lineup_fotmob), agrupado
// por liga/temporada/versão de modelo. Dado vem de /api/model-stats?formato=xi
// (dividido por query param em vez de arquivo próprio -- api/*.js já está no
// teto de 12 serverless functions do plano Hobby).
import React, { useState, useEffect, useMemo } from 'react';
import { Users, AlertTriangle, Loader2, Target, History, ChevronDown, ChevronUp, Download } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';
import { apiUrl } from '../utils/apiUrl';

function fmtPct(v) {
  return v == null ? '—' : `${(v * 100).toFixed(1)}%`;
}
function fmtNum(v) {
  return v == null ? '—' : v.toFixed(4);
}

// Média ponderada pelo nº de previsões/partidas de cada grupo -- soma
// simples de percentuais enviesaria pra grupos pequenos.
function mediaPonderada(grupos, campo, peso) {
  const somaPeso = grupos.reduce((s, g) => s + g[peso], 0);
  if (somaPeso === 0) return null;
  return grupos.reduce((s, g) => s + g[campo] * g[peso], 0) / somaPeso;
}

// `colunas`: [{ header, get: (linha) => valor }] -- CSV genérico reaproveitado
// pelas duas seções da página (backtest e ao vivo), cada uma com seu próprio
// conjunto de colunas.
function exportarCSV(linhas, colunas, nomeArquivo) {
  const csvEscape = (v) => `"${String(v ?? '').replace(/"/g, '""')}"`;
  const cabecalho = colunas.map((c) => csvEscape(c.header)).join(',');
  const corpo = linhas.map((l) => colunas.map((c) => csvEscape(c.get(l))).join(','));
  const csv = [cabecalho, ...corpo].join('\n');
  const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = nomeArquivo;
  a.click();
  URL.revokeObjectURL(url);
}

function BotaoExportarCSV({ onClick, disabled }) {
  return (
    <button onClick={onClick} disabled={disabled}
      className="flex items-center gap-1.5 text-[11px] px-2.5 py-1 rounded-lg bg-slate-700 hover:bg-slate-600 disabled:opacity-40 text-slate-300 hover:text-white transition-colors shrink-0">
      <Download size={12} /> Exportar CSV
    </button>
  );
}

function CardResumo({ label, valor, sublabel, cor = 'text-slate-200' }) {
  return (
    <div className="bg-slate-900 border border-slate-700/50 rounded-lg p-3 text-center">
      <div className="text-[10px] text-slate-500 uppercase">{label}</div>
      <div className={`text-xl font-bold mt-1 ${cor}`}>{valor}</div>
      {sublabel && <div className="text-[10px] text-slate-600 mt-0.5">{sublabel}</div>}
    </div>
  );
}

function Calibracao({ quintis }) {
  if (!quintis || quintis.length === 0) return <p className="text-xs text-slate-600">Sem dado suficiente.</p>;
  return (
    <div className="space-y-1">
      {quintis.map((q, i) => {
        const diff = q.real - q.previsto_medio;
        return (
          <div key={i} className="flex items-center gap-2 text-xs">
            <span className="text-slate-500 w-16 shrink-0">faixa {i + 1}/5</span>
            <div className="flex-1 bg-slate-800 rounded h-3 relative overflow-hidden">
              <div className="absolute inset-y-0 bg-slate-600" style={{ width: `${q.previsto_medio * 100}%` }} />
              <div className={`absolute inset-y-0 ${Math.abs(diff) > 0.08 ? 'bg-red-500/70' : 'bg-emerald-500/70'}`} style={{ width: '2px', left: `${q.real * 100}%` }} />
            </div>
            <span className="text-slate-400 w-40 shrink-0 text-right">
              prev {(q.previsto_medio * 100).toFixed(0)}% · real {(q.real * 100).toFixed(0)}% (n={q.n})
            </span>
          </div>
        );
      })}
      <p className="text-[10px] text-slate-600 mt-1">
        Barra cinza = probabilidade prevista média da faixa. Traço = frequência real de titularidade (verde = próximo do previsto, vermelho = desvio maior que 8pp).
      </p>
    </div>
  );
}

// Backtest walk-forward (scripts/backtest_xi_walkforward.py): por temporada,
// o modelo foi RETREINADO do zero só com dado anterior a ela e avaliado nela
// (nunca vista) -- diferente do restante desta página (modelo ÚNICO de
// produção contra partidas já jogadas), aqui não tem risco de vazamento nem
// pra temporadas antigas. Lê direto do Supabase (tabela pública) em vez de
// passar pelo /api/model-stats -- não precisa da mesma junção cara com
// match_lineup_fotmob, o script já grava a métrica pronta.
function LinhaBacktest({ linha, ligasPorId, aberta, onToggle }) {
  return (
    <>
      <tr className="cursor-pointer hover:bg-slate-700/30" onClick={onToggle}>
        <td className="p-2 text-slate-300 font-semibold">{linha.season}</td>
        <td className="p-2 text-slate-300">{ligasPorId[linha.league_id] || `Liga #${linha.league_id}`}</td>
        <td className="p-2 text-right text-slate-400">{linha.n_partidas}</td>
        <td className="p-2 text-right text-sky-300 font-semibold">{fmtPct(linha.precisao_media_top11)}</td>
        <td className="p-2 text-right text-sky-300">{fmtPct(linha.taxa_xi_exato)}</td>
        <td className="p-2 text-right text-slate-300">{fmtNum(linha.brier)}</td>
        <td className="p-2 text-right text-slate-300">{fmtNum(linha.log_loss)}</td>
        <td className="p-2 text-center text-slate-500">{aberta ? <ChevronUp size={14} /> : <ChevronDown size={14} />}</td>
      </tr>
      {aberta && (
        <tr>
          <td colSpan={8} className="p-3 bg-slate-900/50">
            <Calibracao quintis={linha.calibracao} />
          </td>
        </tr>
      )}
    </>
  );
}

function BacktestWalkforward({ ligasPorId }) {
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState('');
  const [linhas, setLinhas] = useState([]);
  const [filtroLiga, setFiltroLiga] = useState('');
  const [filtroTemporada, setFiltroTemporada] = useState('');
  const [abertaIdx, setAbertaIdx] = useState(null);

  useEffect(() => {
    (async () => {
      setCarregando(true);
      setErro('');
      try {
        const { data, error } = await supabase
          .from('xi_backtest_walkforward')
          .select('season, league_id, model_version, n_partidas, n_previsoes, precisao_media_top11, taxa_xi_exato, brier, log_loss, calibracao')
          .order('season', { ascending: false });
        if (error) throw error;
        setLinhas(data || []);
      } catch (e) {
        setErro(e.message);
      } finally {
        setCarregando(false);
      }
    })();
  }, []);

  const temporadas = useMemo(() => [...new Set(linhas.map((l) => l.season))].sort().reverse(), [linhas]);
  const ligas = useMemo(() => [...new Set(linhas.map((l) => l.league_id))].sort((a, b) => a - b), [linhas]);
  const linhasFiltradas = useMemo(() => linhas.filter((l) =>
    (!filtroLiga || String(l.league_id) === filtroLiga) &&
    (!filtroTemporada || l.season === filtroTemporada)
  ), [linhas, filtroLiga, filtroTemporada]);

  // Resumo agregado dos filtros aplicados -- é aqui que o histórico de
  // verdade vive (milhares de partidas via walk-forward), diferente do
  // resumo "ao vivo" mais abaixo (só cresce conforme partidas previstas
  // vão sendo disputadas, ainda com poucas amostras).
  const resumo = useMemo(() => {
    if (linhasFiltradas.length === 0) return null;
    return {
      n_partidas: linhasFiltradas.reduce((s, l) => s + l.n_partidas, 0),
      precisao_media_top11: mediaPonderada(linhasFiltradas, 'precisao_media_top11', 'n_partidas'),
      taxa_xi_exato: mediaPonderada(linhasFiltradas, 'taxa_xi_exato', 'n_partidas'),
      brier: mediaPonderada(linhasFiltradas, 'brier', 'n_previsoes'),
      log_loss: mediaPonderada(linhasFiltradas, 'log_loss', 'n_previsoes'),
    };
  }, [linhasFiltradas]);

  const exportar = () => exportarCSV(
    linhasFiltradas,
    [
      { header: 'Temporada', get: (l) => l.season },
      { header: 'Liga', get: (l) => ligasPorId[l.league_id] || `Liga #${l.league_id}` },
      { header: 'Versão do modelo', get: (l) => l.model_version },
      { header: 'Partidas avaliadas', get: (l) => l.n_partidas },
      { header: 'Previsões avaliadas', get: (l) => l.n_previsoes },
      { header: 'Precisão top-11 (%)', get: (l) => l.precisao_media_top11 != null ? (l.precisao_media_top11 * 100).toFixed(2) : '' },
      { header: 'XI exato (%)', get: (l) => l.taxa_xi_exato != null ? (l.taxa_xi_exato * 100).toFixed(2) : '' },
      { header: 'Brier Score', get: (l) => l.brier != null ? l.brier.toFixed(4) : '' },
      { header: 'Log-loss', get: (l) => l.log_loss != null ? l.log_loss.toFixed(4) : '' },
    ],
    `backtest_xi_walkforward_${new Date().toISOString().slice(0, 10)}.csv`
  );

  return (
    <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 mb-4">
      <div className="flex items-start justify-between gap-2 mb-1">
        <h2 className="text-sm font-bold text-slate-200 flex items-center gap-2">
          <History className="text-emerald-400" size={16} /> Backtest histórico (walk-forward)
        </h2>
        {linhasFiltradas.length > 0 && <BotaoExportarCSV onClick={exportar} />}
      </div>
      <p className="text-[11px] text-slate-500 mb-3">
        "Fingindo não saber" de verdade: pra cada temporada, o modelo foi retreinado do zero só com dado ESTRITAMENTE anterior a ela e avaliado nela -- diferente das métricas abaixo (modelo único de produção), aqui não tem risco de vazamento nem pras temporadas mais antigas. Rodado sob demanda (<code className="text-slate-400">scripts/backtest_xi_walkforward.py</code>), não atualiza sozinho.
      </p>

      {erro && <div className="bg-red-950/30 border border-red-600/40 text-red-300 text-xs px-3 py-2 rounded-lg mb-3">{erro}</div>}

      {carregando ? (
        <div className="flex items-center gap-2 text-slate-500 text-sm py-6"><Loader2 className="animate-spin" size={16} /> Carregando...</div>
      ) : linhas.length === 0 ? (
        <p className="text-xs text-slate-600 py-4">Nenhum backtest rodado ainda -- dispare <code className="text-slate-500">backtest_xi_walkforward.yml</code> manualmente no GitHub Actions.</p>
      ) : (
        <>
          <div className="flex flex-wrap gap-2 mb-3">
            <select value={filtroTemporada} onChange={(e) => setFiltroTemporada(e.target.value)} className="bg-slate-900 border border-slate-600 rounded-lg px-2 py-1.5 text-xs text-slate-100">
              <option value="">Todas as temporadas</option>
              {temporadas.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
            <select value={filtroLiga} onChange={(e) => setFiltroLiga(e.target.value)} className="bg-slate-900 border border-slate-600 rounded-lg px-2 py-1.5 text-xs text-slate-100">
              <option value="">Todas as ligas</option>
              {ligas.map((l) => <option key={l} value={l}>{ligasPorId[l] || `Liga #${l}`}</option>)}
            </select>
          </div>

          {resumo && (
            <div className="bg-slate-900 border border-slate-700/50 rounded-xl p-4 mb-4">
              <span className="text-[10px] uppercase font-bold text-slate-500 flex items-center gap-1.5 mb-2">
                <Target size={12} /> Resumo (filtros aplicados) -- {linhasFiltradas.length} grupo{linhasFiltradas.length === 1 ? '' : 's'} liga/temporada
              </span>
              <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
                <CardResumo label="Partidas avaliadas" valor={resumo.n_partidas} />
                <CardResumo label="Precisão top-11" valor={fmtPct(resumo.precisao_media_top11)} sublabel="acertos/11, média" cor="text-sky-300" />
                <CardResumo label="XI exato" valor={fmtPct(resumo.taxa_xi_exato)} sublabel="11 de 11 corretos" cor="text-sky-300" />
                <CardResumo label="Brier Score" valor={fmtNum(resumo.brier)} sublabel="menor é melhor" />
                <CardResumo label="Log-loss" valor={fmtNum(resumo.log_loss)} sublabel="menor é melhor" />
              </div>
            </div>
          )}

          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-slate-500 uppercase text-[10px]">
                  <th className="text-left p-2">Temporada</th>
                  <th className="text-left p-2">Liga</th>
                  <th className="text-right p-2">Partidas</th>
                  <th className="text-right p-2">Precisão top-11</th>
                  <th className="text-right p-2">XI exato</th>
                  <th className="text-right p-2">Brier</th>
                  <th className="text-right p-2">Log-loss</th>
                  <th className="p-2"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-700/50">
                {linhasFiltradas.map((l, i) => (
                  <LinhaBacktest key={i} linha={l} ligasPorId={ligasPorId} aberta={abertaIdx === i} onToggle={() => setAbertaIdx(abertaIdx === i ? null : i)} />
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

export default function XiModeloStats() {
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState('');
  const [grupos, setGrupos] = useState([]);
  const [ligasPorId, setLigasPorId] = useState({});

  const [filtroLiga, setFiltroLiga] = useState('');
  const [filtroTemporada, setFiltroTemporada] = useState('');
  const [filtroModelo, setFiltroModelo] = useState('');

  useEffect(() => {
    (async () => {
      setCarregando(true);
      setErro('');
      try {
        const [respStats, ligasResp] = await Promise.all([
          fetch(apiUrl('/api/model-stats?formato=xi')),
          supabaseAtivo ? supabase.from('leagues').select('id, name') : Promise.resolve({ data: [] }),
        ]);
        const dados = await respStats.json();
        if (!respStats.ok) throw new Error(dados.error?.message || 'Erro ao carregar estatísticas do XI previsto.');
        setGrupos(dados.grupos_xi || []);
        const mapa = {};
        (ligasResp.data || []).forEach((l) => { mapa[l.id] = l.name; });
        setLigasPorId(mapa);
      } catch (e) {
        setErro(e.message);
      } finally {
        setCarregando(false);
      }
    })();
  }, []);

  const modelos = useMemo(() => [...new Set(grupos.map((g) => g.model_version))].sort(), [grupos]);
  const ligas = useMemo(() => [...new Set(grupos.map((g) => g.league_id))].sort((a, b) => a - b), [grupos]);
  const temporadas = useMemo(() => [...new Set(grupos.map((g) => g.season))].sort().reverse(), [grupos]);

  const gruposFiltrados = useMemo(() => grupos.filter((g) =>
    (!filtroLiga || String(g.league_id) === filtroLiga) &&
    (!filtroTemporada || String(g.season) === filtroTemporada) &&
    (!filtroModelo || g.model_version === filtroModelo)
  ), [grupos, filtroLiga, filtroTemporada, filtroModelo]);

  const resumoGeral = useMemo(() => {
    if (gruposFiltrados.length === 0) return null;
    return {
      n_partidas: gruposFiltrados.reduce((s, g) => s + g.n_partidas, 0),
      precisao_media_top11: mediaPonderada(gruposFiltrados, 'precisao_media_top11', 'n_partidas'),
      taxa_xi_exato: mediaPonderada(gruposFiltrados, 'taxa_xi_exato', 'n_partidas'),
      brier: mediaPonderada(gruposFiltrados, 'brier', 'n_previsoes'),
      log_loss: mediaPonderada(gruposFiltrados, 'log_loss', 'n_previsoes'),
    };
  }, [gruposFiltrados]);

  const exportarAoVivo = () => exportarCSV(
    gruposFiltrados,
    [
      { header: 'Liga', get: (g) => ligasPorId[g.league_id] || `Liga #${g.league_id}` },
      { header: 'Temporada', get: (g) => g.season },
      { header: 'Versão do modelo', get: (g) => g.model_version },
      { header: 'Partidas avaliadas', get: (g) => g.n_partidas },
      { header: 'Previsões avaliadas', get: (g) => g.n_previsoes },
      { header: 'Precisão top-11 (%)', get: (g) => g.precisao_media_top11 != null ? (g.precisao_media_top11 * 100).toFixed(2) : '' },
      { header: 'XI exato (%)', get: (g) => g.taxa_xi_exato != null ? (g.taxa_xi_exato * 100).toFixed(2) : '' },
      { header: 'Brier Score', get: (g) => g.brier != null ? g.brier.toFixed(4) : '' },
      { header: 'Log-loss', get: (g) => g.log_loss != null ? g.log_loss.toFixed(4) : '' },
    ],
    `xi_modelo_stats_ao_vivo_${new Date().toISOString().slice(0, 10)}.csv`
  );

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
          <Users className="text-emerald-400" size={28} /> Estatísticas do XI Previsto
        </h1>
        <p className="text-slate-400 mt-1 text-sm">
          Acurácia do modelo de escalação provável (scripts/rodar_xi_previsto.py) contra a escalação REAL de partidas já jogadas, por liga/temporada. Precisão top-11 e XI exato usam a mesma definição do treino (acertos/11 e "bateu o set inteiro"); Brier/log-loss e a curva de calibração medem o quão bem calibrada é a probabilidade de titularidade prevista pra CADA jogador do elenco avaliado, não só o top-11.
        </p>
      </div>

      {erro && <div className="bg-red-950/30 border border-red-600/40 text-red-300 text-sm px-4 py-3 rounded-xl mb-4">{erro}</div>}

      <BacktestWalkforward ligasPorId={ligasPorId} />

      {carregando ? (
        <div className="flex items-center justify-center py-16 text-slate-500 gap-2">
          <Loader2 className="animate-spin" size={20} /> Calculando estatísticas...
        </div>
      ) : grupos.length === 0 ? (
        <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 text-center text-slate-500 text-sm">
          Nenhuma partida finalizada com previsão de XI e escalação real registrada ainda.
        </div>
      ) : (
        <>
          <div className="bg-slate-800 border border-slate-700 rounded-2xl p-4 mb-4 flex flex-wrap gap-3">
            <select value={filtroModelo} onChange={(e) => setFiltroModelo(e.target.value)} className="bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100">
              <option value="">Todas as versões de modelo</option>
              {modelos.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
            <select value={filtroLiga} onChange={(e) => setFiltroLiga(e.target.value)} className="bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100">
              <option value="">Todas as ligas</option>
              {ligas.map((l) => <option key={l} value={l}>{ligasPorId[l] || `Liga #${l}`}</option>)}
            </select>
            <select value={filtroTemporada} onChange={(e) => setFiltroTemporada(e.target.value)} className="bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100">
              <option value="">Todas as temporadas</option>
              {temporadas.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>

          {resumoGeral && (
            <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 mb-4">
              <div className="flex items-start justify-between gap-2 mb-3">
                <h2 className="text-sm font-bold text-slate-200 flex items-center gap-2">
                  <Target className="text-emerald-400" size={16} /> Resumo (filtros aplicados) -- {gruposFiltrados.length} grupo{gruposFiltrados.length === 1 ? '' : 's'} liga/temporada
                </h2>
                <BotaoExportarCSV onClick={exportarAoVivo} />
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
                <CardResumo label="Partidas avaliadas" valor={resumoGeral.n_partidas} />
                <CardResumo label="Precisão top-11" valor={fmtPct(resumoGeral.precisao_media_top11)} sublabel="acertos/11, média" cor="text-sky-300" />
                <CardResumo label="XI exato" valor={fmtPct(resumoGeral.taxa_xi_exato)} sublabel="11 de 11 corretos" cor="text-sky-300" />
                <CardResumo label="Brier Score" valor={fmtNum(resumoGeral.brier)} sublabel="menor é melhor" />
                <CardResumo label="Log-loss" valor={fmtNum(resumoGeral.log_loss)} sublabel="menor é melhor" />
              </div>
            </div>
          )}

          {gruposFiltrados.length === 0 ? (
            <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 text-center text-slate-500 text-sm">Nenhum grupo com os filtros atuais.</div>
          ) : (
            <div className="space-y-4">
              {gruposFiltrados.map((g, i) => (
                <div key={i} className="bg-slate-800 border border-slate-700 rounded-2xl p-6">
                  <div className="flex items-center justify-between flex-wrap gap-2 mb-4">
                    <h2 className="text-sm font-bold text-slate-200">
                      {ligasPorId[g.league_id] || `Liga #${g.league_id}`} <span className="text-slate-500">·</span> Temporada {g.season} <span className="text-slate-500">·</span> {g.model_version}
                    </h2>
                    <span className="text-xs text-slate-500">{g.n_partidas} partidas · {g.n_previsoes} previsões avaliadas</span>
                  </div>

                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
                    <CardResumo label="Precisão top-11" valor={fmtPct(g.precisao_media_top11)} sublabel="acertos/11, média" cor="text-sky-300" />
                    <CardResumo label="XI exato" valor={fmtPct(g.taxa_xi_exato)} sublabel="11 de 11 corretos" cor="text-sky-300" />
                    <CardResumo label="Brier Score" valor={fmtNum(g.brier)} sublabel="menor é melhor" />
                    <CardResumo label="Log-loss" valor={fmtNum(g.log_loss)} sublabel="menor é melhor" />
                  </div>

                  <div>
                    <span className="text-[10px] uppercase font-bold text-slate-500 block mb-2">Calibração (previsto vs. real, em quintis)</span>
                    <Calibracao quintis={g.calibracao} />
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
