// src/pages/ModelBenchmarking.jsx — rota /model-benchmarking
// Painel dos modelos do Model Benchmarking, com o botão que dispara o
// workflow_dispatch do predict.yml no GitHub Actions (api/model-maintenance
// ?tarefa=disparar-predicoes). Leitura direta de `predicoes`/`market_odds`
// (RLS pública) -- só o disparo exige login (Authorization: Bearer do
// access_token do Supabase Auth, verificado no servidor).
//
// A tabela de predições DIÁRIAS logo abaixo (`partidas`) só mostra
// `MODELOS_BASE` (utils/modelosBenchmarking.js) -- hoje só `dixon_coles_v1`,
// já que v1-v8 de catboost/xgboost/lightgbm foram removidos do pipeline
// (superados pela família v9/v10/v11, ver comentário em
// `modelosBenchmarking.js`). O painel "Backtest completo" abaixo é
// independente disso: lê `model_benchmarking_backtest` sem filtro nenhum
// de modelo, então mostra qualquer linha que `scripts/backtest_kelly.py`
// gravar (dixon_coles_v1 + toda versão registrada em
// `modelos_ml.TREINADORES` -- hoje v1 raw/v9/v10/v11 de catboost/xgboost/
// lightgbm/mlp, contando as variantes calibradas).
import React, { useState, useEffect, useCallback } from 'react';
import { Zap, Loader2, AlertTriangle, TrendingUp, PlayCircle, ChevronDown, ChevronRight, FileDown } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';
import { useAuth } from '../AuthContext';
import PainelInfoModelo, { BotaoInfoModelo } from '../components/PainelInfoModelo';
import { apiUrl } from '../utils/apiUrl';
import { MODELOS_BASE, ROTULO_MODELO } from '../utils/modelosBenchmarking';

async function buscarPaginado(query) {
  const linhas = [];
  let pagina = 0;
  while (true) {
    const { data, error } = await query.range(pagina * 1000, pagina * 1000 + 999);
    if (error) throw error;
    linhas.push(...(data || []));
    if (!data || data.length < 1000) break;
    pagina++;
  }
  return linhas;
}

function fmtPct(v) {
  return v == null ? '—' : `${(v * 100).toFixed(1)}%`;
}

function fmtPctSigned(v) {
  return v == null ? '—' : `${v >= 0 ? '+' : ''}${(v * 100).toFixed(1)}%`;
}

function fmtNum(v, casas = 4) {
  return v == null ? '—' : Number(v).toFixed(casas);
}

function fmtPeriodo(inicio, fim) {
  if (!inicio || !fim) return '—';
  const f = (d) => new Date(d).toLocaleDateString('pt-BR');
  return `${f(inicio)} – ${f(fim)}`;
}

const MERCADOS_BACKTEST = [
  { chave: '1X2', rotulo: '1X2' },
  { chave: 'over_under_2.5', rotulo: 'Over/Under 2.5' },
  { chave: 'btts', rotulo: 'Ambas Marcam (BTTS)' },
  { chave: 'dupla_chance', rotulo: 'Dupla Chance' },
  { chave: 'over_under_1.5', rotulo: 'Over/Under 1.5' },
  { chave: 'over_under_3.5', rotulo: 'Over/Under 3.5' },
  { chave: 'handicap_-2.0', rotulo: 'Handicap -2' },
  { chave: 'handicap_-1.0', rotulo: 'Handicap -1' },
  { chave: 'handicap_1.0', rotulo: 'Handicap +1' },
  { chave: 'handicap_2.0', rotulo: 'Handicap +2' },
  { chave: 'corners_over_under_7.5', rotulo: 'Escanteios O/U 7,5' },
  { chave: 'corners_over_under_8.5', rotulo: 'Escanteios O/U 8,5' },
  { chave: 'corners_over_under_9.5', rotulo: 'Escanteios O/U 9,5 (modelo misto)' },
  { chave: 'corners_over_under_10', rotulo: 'Escanteios O/U 10' },
  { chave: 'corners_over_under_10.5', rotulo: 'Escanteios O/U 10,5' },
  { chave: 'corners_over_under_11', rotulo: 'Escanteios O/U 11' },
  { chave: 'corners_over_under_11.5', rotulo: 'Escanteios O/U 11,5' },
  { chave: 'corners_over_under_12', rotulo: 'Escanteios O/U 12' },
  { chave: 'corners_over_under_12.5', rotulo: 'Escanteios O/U 12,5' },
  { chave: 'corners_ou95', rotulo: 'Escanteios O/U 9,5 (classificador)' },
  { chave: 'faixa_gols', rotulo: 'Faixa de gols' },
];
// Escanteios (classificador) e faixa de gols não têm nenhuma fonte de odds
// de mercado neste projeto -- pra esses 2, o backtest só traz qualidade
// intrínseca da probabilidade (log-loss/Brier/Acurácia), nunca ROI/Kelly/
// EV+ (fica sempre 0 apostas, não é bug -- ver comentário no topo de
// `MERCADOS` em backtest_kelly.py). Todos os outros (incluindo dupla
// chance/handicap/escanteios "modelo misto", que só o modelo misto com ML
// cobre -- ver MERCADOS_HIBRIDO_VALIDOS em backtest_kelly.py) têm odds
// reais da Pinnacle e produzem ROI/Kelly normalmente.
const MERCADOS_SEM_ROI = new Set(['corners_ou95', 'faixa_gols']);
// Par abertura/fechamento da referência de mercado -- mesma ideia das 2
// colunas de ROI (fech./abert.) que cada modelo já tem, pedido do usuário
// pra poder comparar a qualidade da Pinnacle nos dois momentos separados.
const MODEL_NAME_MERCADO_REF = 'mercado_pinnacle_sem_vig';
const MODEL_NAME_MERCADO_REF_FECHAMENTO = 'mercado_pinnacle_sem_vig_fechamento';

function escaparHtml(valor) {
  return String(valor ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// Relatório HTML consolidado (todos os mercados x todos os modelos, cru +
// calibrado_platt + calibrado_isotonic), gerado no CLIENTE a partir do
// mesmo `relatorio`/`relatorioPorLiga` já carregado na tela -- não faz
// nenhuma chamada nova, só formata o que já está em
// `model_benchmarking_backtest`/`_liga` num arquivo autônomo, baixável/
// imprimível.
function montarRelatorioHtml(relatorio, relatorioPorLiga) {
  const geradoEm = new Date().toLocaleString('pt-BR');
  const secoes = MERCADOS_BACKTEST.map(({ chave, rotulo }) => {
    const linhas = relatorio.filter((r) => (r.mercado || '1X2') === chave);
    const referencia = linhas.find((r) => r.model_name === MODEL_NAME_MERCADO_REF);
    const referenciaFechamento = linhas.find((r) => r.model_name === MODEL_NAME_MERCADO_REF_FECHAMENTO);
    const modelos = linhas
      .filter((r) => r.model_name !== MODEL_NAME_MERCADO_REF && r.model_name !== MODEL_NAME_MERCADO_REF_FECHAMENTO)
      .sort((a, b) => (b.roi_ic95_inferior ?? -Infinity) - (a.roi_ic95_inferior ?? -Infinity));
    const periodo = referencia || modelos[0];
    const porLiga = relatorioPorLiga.filter((r) => (r.mercado || '1X2') === chave);

    if (modelos.length === 0) {
      return `<section><h2>${escaparHtml(rotulo)}</h2><p class="vazio">Nenhum backtest rodado ainda pra este mercado.</p></section>`;
    }

    const linhaReferencia = referencia
      ? `<tr class="ref"><td>Pinnacle sem vig (mercado)</td><td class="num">${fmtNum(referencia.log_loss)}</td><td class="num">${fmtNum(referencia.brier)}</td><td class="num">${fmtPct(referencia.accuracy)}</td><td colspan="8" class="dim">— (referência, sem ROI)</td></tr>`
      : '';
    const linhaReferenciaFechamento = referenciaFechamento
      ? `<tr class="ref"><td>Pinnacle sem vig (fechamento)</td><td class="num">${fmtNum(referenciaFechamento.log_loss)}</td><td class="num">${fmtNum(referenciaFechamento.brier)}</td><td class="num">${fmtPct(referenciaFechamento.accuracy)}</td><td colspan="8" class="dim">— (referência, sem ROI)</td></tr>`
      : '';

    const linhasModelo = modelos
      .map((r) => {
        const destaque = r.significativo || r.significativo_abertura ? ' class="ev"' : '';
        return `<tr${destaque}>
          <td>${escaparHtml(r.model_name)}</td>
          <td class="num">${fmtNum(r.log_loss)}</td>
          <td class="num">${fmtNum(r.brier)}</td>
          <td class="num">${fmtPct(r.accuracy)}</td>
          <td class="num">${r.n_apostas ?? 0}</td>
          <td class="num ${r.roi_medio > 0 ? 'pos' : 'neg'}">${fmtPctSigned(r.roi_medio)}</td>
          <td class="num dim">[${fmtPctSigned(r.roi_ic95_inferior)}, ${fmtPctSigned(r.roi_ic95_superior)}]</td>
          <td class="ctr">${r.significativo ? '✓' : '—'}</td>
          <td class="num">${r.n_apostas_abertura ?? 0}</td>
          <td class="num ${r.roi_abertura_medio > 0 ? 'pos' : 'neg'}">${fmtPctSigned(r.roi_abertura_medio)}</td>
          <td class="num dim">[${fmtPctSigned(r.roi_abertura_ic95_inferior)}, ${fmtPctSigned(r.roi_abertura_ic95_superior)}]</td>
          <td class="ctr">${r.significativo_abertura ? '✓' : '—'}</td>
        </tr>`;
      })
      .join('');

    const linhasPorLiga = porLiga
      .map(
        (l) => `<tr class="liga">
          <td>${escaparHtml(l.model_name)} — ${escaparHtml(l.liga)}</td>
          <td class="num">${fmtNum(l.log_loss)}</td>
          <td class="num">${fmtNum(l.brier)}</td>
          <td class="num">${fmtPct(l.accuracy)}</td>
          <td class="num">${l.n_apostas ?? 0}</td>
          <td class="num ${l.roi_medio > 0 ? 'pos' : 'neg'}">${fmtPctSigned(l.roi_medio)}</td>
          <td class="num dim">[${fmtPctSigned(l.roi_ic95_inferior)}, ${fmtPctSigned(l.roi_ic95_superior)}]</td>
          <td class="ctr">${l.significativo ? '✓' : '—'}</td>
          <td class="num">${l.n_apostas_abertura ?? 0}</td>
          <td class="num ${l.roi_abertura_medio > 0 ? 'pos' : 'neg'}">${fmtPctSigned(l.roi_abertura_medio)}</td>
          <td class="num dim">[${fmtPctSigned(l.roi_abertura_ic95_inferior)}, ${fmtPctSigned(l.roi_abertura_ic95_superior)}]</td>
          <td class="ctr">${l.significativo_abertura ? '✓' : '—'}</td>
        </tr>`
      )
      .join('');

    return `<section>
      <h2>${escaparHtml(rotulo)} ${MERCADOS_SEM_ROI.has(chave) ? '<span class="badge">sem odds de mercado — só qualidade</span>' : ''}</h2>
      <p class="periodo">${periodo?.periodo_inicio ? `Período de teste: ${fmtPeriodo(periodo.periodo_inicio, periodo.periodo_fim)}` : ''}</p>
      <table>
        <thead><tr>
          <th>Modelo</th><th>Log-loss</th><th>Brier</th><th>Acurácia</th>
          <th>Apostas (fech.)</th><th>ROI (fech.)</th><th>IC 95% (fech.)</th><th>EV+?</th>
          <th>Apostas (abert.)</th><th>ROI (abert.)</th><th>IC 95% (abert.)</th><th>EV+?</th>
        </tr></thead>
        <tbody>${linhaReferencia}${linhaReferenciaFechamento}${linhasModelo}</tbody>
      </table>
      ${linhasPorLiga ? `<h3>Quebra por liga (variante crua)</h3><table>
        <thead><tr>
          <th>Modelo / Liga</th><th>Log-loss</th><th>Brier</th><th>Acurácia</th>
          <th>Apostas (fech.)</th><th>ROI (fech.)</th><th>IC 95% (fech.)</th><th>EV+?</th>
          <th>Apostas (abert.)</th><th>ROI (abert.)</th><th>IC 95% (abert.)</th><th>EV+?</th>
        </tr></thead>
        <tbody>${linhasPorLiga}</tbody>
      </table>` : ''}
    </section>`;
  }).join('\n');

  return `<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8" />
<title>Relatório Model Benchmarking</title>
<style>
  body { background:#0f172a; color:#e2e8f0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; margin:0; padding:32px; }
  h1 { font-size:22px; margin-bottom:4px; }
  .subtitulo { color:#94a3b8; font-size:13px; margin-bottom:28px; }
  section { margin-bottom:36px; }
  h2 { font-size:16px; color:#f1f5f9; border-bottom:1px solid #334155; padding-bottom:6px; }
  h3 { font-size:12px; color:#94a3b8; text-transform:uppercase; margin-top:18px; }
  .badge { font-size:10px; font-weight:normal; color:#fbbf24; background:rgba(251,191,36,0.1); border:1px solid rgba(251,191,36,0.4); border-radius:6px; padding:2px 8px; margin-left:8px; }
  .periodo { color:#64748b; font-size:11px; margin:2px 0 10px; }
  .vazio { color:#64748b; font-size:13px; }
  table { width:100%; border-collapse:collapse; font-size:11px; font-variant-numeric:tabular-nums; margin-bottom:8px; }
  th { text-align:right; color:#64748b; text-transform:uppercase; font-size:9px; padding:6px 8px; border-bottom:1px solid #334155; }
  th:first-child, td:first-child { text-align:left; }
  td { padding:6px 8px; border-bottom:1px solid #1e293b; color:#cbd5e1; }
  td.num { text-align:right; }
  td.ctr { text-align:center; }
  td.dim { color:#64748b; }
  td.pos { color:#34d399; font-weight:bold; }
  td.neg { color:#f87171; font-weight:bold; }
  tr.ev { background:rgba(16,185,129,0.06); }
  tr.ref { font-style:italic; color:#7dd3fc; }
  tr.liga td { color:#64748b; padding-left:20px; }
  @media print { body { background:#fff; color:#000; } }
</style>
</head>
<body>
  <h1>Relatório — Model Benchmarking</h1>
  <p class="subtitulo">
    Gerado em ${geradoEm}. Test Set out-of-sample (nunca visto pelo treino/tuning). Qualidade (log-loss/Brier/Acurácia)
    comparada com a Pinnacle sem vig quando há odd disponível pro mercado. ROI simulado com Kelly fracionário 25% e edge
    mínimo 2pp, contra a melhor odd de fechamento e contra a odd de abertura da Pinnacle separadamente — IC 95% via
    bootstrap, "EV+" só quando o limite inferior do IC fica acima de zero.
  </p>
  ${secoes}
</body>
</html>`;
}

function baixarRelatorioHtml(html) {
  const blob = new Blob([html], { type: 'text/html' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `relatorio-model-benchmarking-${new Date().toISOString().slice(0, 10)}.html`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// Painel de backtest (log-loss/Brier/Acurácia vs. Pinnacle sem vig + ROI/
// Kelly/IC95%/EV+) equivalente ao "Backtest de apostas simuladas (EV+)" de
// Estatísticas dos Modelos, mas lendo o resultado JÁ PERSISTIDO por
// scripts/backtest_kelly.py (via model_benchmarking_backtest/_liga) em vez
// de calcular na hora -- o backtest de verdade (grid search + tuning, 4
// mercados x toda versão de modelos_ml.TREINADORES) é caro demais pra rodar dentro de uma função
// serverless, então roda no GitHub Actions (backtest_kelly.yml, disparado
// por ?tarefa=disparar-backtest) e só o resultado final é lido aqui.
//
// Dois blocos de ROI por modelo: "fechamento" usa a melhor odd real
// disponível entre TODOS os bookmakers (mesmo teste original), "abertura"
// usa especificamente a odd de abertura da Pinnacle -- são universos de
// apostas diferentes (edge mínimo aplicado em cada odd separadamente), não
// comparáveis diretamente entre si.
function BacktestModelBenchmarking({ session }) {
  const [relatorio, setRelatorio] = useState([]);
  const [relatorioPorLiga, setRelatorioPorLiga] = useState([]);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState(null);
  const [disparando, setDisparando] = useState(false);
  const [mensagemDisparo, setMensagemDisparo] = useState(null);
  const [expandido, setExpandido] = useState(null);
  const [mercado, setMercado] = useState('1X2');
  const [modeloInfo, setModeloInfo] = useState(null);

  const carregar = useCallback(async () => {
    if (!supabaseAtivo) { setErro('Supabase não configurado.'); setCarregando(false); return; }
    setCarregando(true);
    setErro(null);
    try {
      const [{ data: principal, error: e1 }, { data: porLiga, error: e2 }] = await Promise.all([
        supabase.from('model_benchmarking_backtest').select('*'),
        supabase.from('model_benchmarking_backtest_liga').select('*'),
      ]);
      if (e1) throw e1;
      if (e2) throw e2;
      setRelatorio(principal || []);
      setRelatorioPorLiga(porLiga || []);
    } catch (e) {
      setErro(e.message);
    } finally {
      setCarregando(false);
    }
  }, []);

  useEffect(() => { carregar(); }, [carregar]);

  async function dispararBacktest() {
    setDisparando(true);
    setMensagemDisparo(null);
    try {
      const resp = await fetch(apiUrl('/api/model-maintenance?tarefa=disparar-backtest'), {
        method: 'POST',
        headers: { Authorization: `Bearer ${session?.access_token || ''}` },
      });
      const corpo = await resp.json();
      if (!resp.ok) throw new Error(corpo?.error?.message || `HTTP ${resp.status}`);
      setMensagemDisparo({
        tipo: 'ok',
        texto: 'Disparado! Grid search + tuning + simulação Kelly no Dixon-Coles + catboost/xgboost/lightgbm/mlp (v9/v10/v11), em 4 mercados (1X2, Over/Under 2.5, Escanteios O/U 9,5 e Faixa de gols) -- é bem mais pesado que as predições diárias, pode levar mais de 1h. Volte depois e clique em "Recarregar".',
      });
    } catch (e) {
      setMensagemDisparo({ tipo: 'erro', texto: e.message });
    } finally {
      setDisparando(false);
    }
  }

  const relatorioMercado = relatorio.filter((r) => (r.mercado || '1X2') === mercado);
  const relatorioPorLigaMercado = relatorioPorLiga.filter((r) => (r.mercado || '1X2') === mercado);
  const referenciaMercado = relatorioMercado.find((r) => r.model_name === MODEL_NAME_MERCADO_REF);
  const referenciaMercadoFechamento = relatorioMercado.find((r) => r.model_name === MODEL_NAME_MERCADO_REF_FECHAMENTO);
  const relatorioModelos = relatorioMercado
    .filter((r) => r.model_name !== MODEL_NAME_MERCADO_REF && r.model_name !== MODEL_NAME_MERCADO_REF_FECHAMENTO)
    .sort((a, b) => (b.roi_ic95_inferior ?? -Infinity) - (a.roi_ic95_inferior ?? -Infinity));
  const ultimaExecucao = relatorio.reduce((max, r) => (r.executado_em > max ? r.executado_em : max), '');
  const periodo = referenciaMercado || relatorioModelos[0];

  return (
    <div className="bg-slate-900 border border-slate-700/50 rounded-lg p-4 md:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3 mb-1">
        <h2 className="text-lg font-extrabold flex items-center gap-2 text-slate-100">
          <TrendingUp className="text-emerald-400" size={22} /> Backtest completo (qualidade + EV+)
        </h2>
        <div className="flex items-center gap-2">
          <button onClick={carregar} disabled={carregando}
            className="px-3 py-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-sm border border-slate-700 disabled:opacity-50">
            Recarregar
          </button>
          <button
            onClick={() => baixarRelatorioHtml(montarRelatorioHtml(relatorio, relatorioPorLiga))}
            disabled={relatorio.length === 0}
            title="Baixa um relatório HTML com todos os mercados e modelos já carregados nesta tela."
            className="flex items-center gap-2 px-3 py-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-sm border border-slate-700 disabled:opacity-50">
            <FileDown size={16} /> Emitir relatório
          </button>
          <button onClick={dispararBacktest} disabled={disparando || !session}
            title={!session ? 'Faça login pra disparar o backtest.' : ''}
            className="flex items-center gap-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 disabled:cursor-not-allowed text-white font-bold px-4 py-2 rounded-lg text-sm">
            {disparando ? <Loader2 size={16} className="animate-spin" /> : <PlayCircle size={16} />} Rodar backtest
          </button>
        </div>
      </div>
      <p className="text-slate-400 text-sm mb-3">
        Test Set out-of-sample (nunca visto pelo treino/tuning). Qualidade (log-loss/Brier/Acurácia) comparada com a
        Pinnacle sem vig (odds justas, devigadas). ROI simulado com Kelly fracionário 25% e edge mínimo 2pp, em dois
        testes separados: contra a <strong>melhor odd real de fechamento</strong> (qualquer bookmaker) e contra a{' '}
        <strong>odd de abertura da Pinnacle</strong> especificamente — IC 95% via bootstrap (2000 reamostragens), só
        considera "EV+" quando o limite inferior do IC fica acima de zero.
        {ultimaExecucao && <span className="text-slate-500"> Última rodada: {new Date(ultimaExecucao).toLocaleString('pt-BR')}.</span>}
      </p>

      <div className="flex flex-wrap items-center gap-2 mb-4">
        {MERCADOS_BACKTEST.map((m) => (
          <button key={m.chave} onClick={() => setMercado(m.chave)}
            className={`px-3 py-1.5 rounded-lg text-xs font-bold border ${mercado === m.chave ? 'bg-emerald-600 border-emerald-600 text-white' : 'bg-slate-800 border-slate-700 text-slate-400 hover:text-slate-200'}`}>
            {m.rotulo}
          </button>
        ))}
        {periodo?.periodo_inicio && (
          <span className="text-[11px] text-slate-500 ml-2">Período de teste: {fmtPeriodo(periodo.periodo_inicio, periodo.periodo_fim)}</span>
        )}
      </div>

      {MERCADOS_SEM_ROI.has(mercado) && (
        <div className="rounded-lg border border-amber-800/50 bg-amber-950/20 p-3 text-xs text-amber-300 mb-4">
          Sem fonte de odds de mercado pra este mercado (OddsPapi só cobre 1X2 e Over/Under 2.5) — as colunas de
          ROI/Kelly/EV+ ficam sempre com 0 apostas de propósito. Log-loss/Brier/Acurácia continuam válidos (qualidade
          intrínseca da probabilidade, não depende de odd nenhuma).
        </div>
      )}

      {mensagemDisparo && (
        <div className={`rounded-lg border p-3 text-sm mb-4 ${mensagemDisparo.tipo === 'ok' ? 'bg-emerald-950/40 border-emerald-800 text-emerald-300' : 'bg-red-950/40 border-red-800 text-red-300'}`}>
          {mensagemDisparo.texto}
        </div>
      )}

      {erro && (
        <div className="flex items-center gap-2 rounded-lg border border-red-800 bg-red-950/40 p-3 text-sm text-red-300 mb-4">
          <AlertTriangle size={16} /> {erro}
        </div>
      )}

      {carregando ? (
        <div className="flex items-center gap-2 text-slate-500 text-sm py-8 justify-center">
          <Loader2 size={16} className="animate-spin" /> Carregando backtest...
        </div>
      ) : relatorioMercado.length === 0 ? (
        <p className="text-sm text-slate-500 text-center py-6">
          Nenhum backtest rodado ainda pra {MERCADOS_BACKTEST.find((m) => m.chave === mercado)?.rotulo} — clique em
          "Rodar backtest" (leva uns 30-60 minutos, roda em background no GitHub Actions).
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-slate-500 uppercase text-[10px]">
                <th className="text-left p-1.5"></th>
                <th className="text-left p-1.5">Modelo</th>
                <th className="text-right p-1.5">Log-loss</th>
                <th className="text-right p-1.5">Brier</th>
                <th className="text-right p-1.5">Acurácia</th>
                <th className="text-right p-1.5">Apostas (fech.)</th>
                <th className="text-right p-1.5">ROI (fech.)</th>
                <th className="text-right p-1.5">IC 95% (fech.)</th>
                <th className="text-center p-1.5">EV+?</th>
                <th className="text-right p-1.5">Apostas (abert.)</th>
                <th className="text-right p-1.5">ROI (abert.)</th>
                <th className="text-right p-1.5">IC 95% (abert.)</th>
                <th className="text-center p-1.5">EV+?</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-700/50">
              {referenciaMercado && (
                <tr className="bg-sky-500/5 italic">
                  <td className="p-1.5"></td>
                  <td className="p-1.5 text-sky-300">Pinnacle sem vig (mercado)</td>
                  <td className="p-1.5 text-right text-sky-200">{fmtNum(referenciaMercado.log_loss)}</td>
                  <td className="p-1.5 text-right text-sky-200">{fmtNum(referenciaMercado.brier)}</td>
                  <td className="p-1.5 text-right text-sky-200">{fmtPct(referenciaMercado.accuracy)}</td>
                  <td className="p-1.5 text-right text-slate-700" colSpan={8}>— (referência, sem ROI)</td>
                </tr>
              )}
              {referenciaMercadoFechamento && (
                <tr className="bg-sky-500/5 italic">
                  <td className="p-1.5"></td>
                  <td className="p-1.5 text-sky-300">Pinnacle sem vig (fechamento)</td>
                  <td className="p-1.5 text-right text-sky-200">{fmtNum(referenciaMercadoFechamento.log_loss)}</td>
                  <td className="p-1.5 text-right text-sky-200">{fmtNum(referenciaMercadoFechamento.brier)}</td>
                  <td className="p-1.5 text-right text-sky-200">{fmtPct(referenciaMercadoFechamento.accuracy)}</td>
                  <td className="p-1.5 text-right text-slate-700" colSpan={8}>— (referência, sem ROI)</td>
                </tr>
              )}
              {relatorioModelos.map((r) => {
                const ligasDoModelo = relatorioPorLigaMercado.filter((l) => l.model_name === r.model_name);
                const aberto = expandido === `${mercado}:${r.model_name}`;
                return (
                  <React.Fragment key={r.model_name}>
                    <tr className={r.significativo || r.significativo_abertura ? 'bg-emerald-500/5' : ''}>
                      <td className="p-1.5">
                        {ligasDoModelo.length > 0 && (
                          <button onClick={() => setExpandido(aberto ? null : `${mercado}:${r.model_name}`)} className="text-slate-500 hover:text-slate-300">
                            {aberto ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                          </button>
                        )}
                      </td>
                      <td className="p-1.5 text-slate-300">
                        <span className="flex items-center gap-1.5">
                          {r.model_name}
                          <BotaoInfoModelo onClick={() => setModeloInfo(r.model_name)} />
                        </span>
                      </td>
                      <td className="p-1.5 text-right text-slate-400">{fmtNum(r.log_loss)}</td>
                      <td className="p-1.5 text-right text-slate-400">{fmtNum(r.brier)}</td>
                      <td className="p-1.5 text-right text-slate-400">{fmtPct(r.accuracy)}</td>
                      <td className="p-1.5 text-right text-slate-400">{r.n_apostas}</td>
                      <td className={`p-1.5 text-right font-bold ${r.roi_medio > 0 ? 'text-emerald-400' : 'text-red-400'}`}>{fmtPctSigned(r.roi_medio)}</td>
                      <td className="p-1.5 text-right text-slate-500">[{fmtPctSigned(r.roi_ic95_inferior)}, {fmtPctSigned(r.roi_ic95_superior)}]</td>
                      <td className="p-1.5 text-center">{r.significativo ? <span className="text-emerald-400 font-bold">✓</span> : <span className="text-slate-600">—</span>}</td>
                      <td className="p-1.5 text-right text-slate-400">{r.n_apostas_abertura}</td>
                      <td className={`p-1.5 text-right font-bold ${r.roi_abertura_medio > 0 ? 'text-emerald-400' : 'text-red-400'}`}>{fmtPctSigned(r.roi_abertura_medio)}</td>
                      <td className="p-1.5 text-right text-slate-500">[{fmtPctSigned(r.roi_abertura_ic95_inferior)}, {fmtPctSigned(r.roi_abertura_ic95_superior)}]</td>
                      <td className="p-1.5 text-center">{r.significativo_abertura ? <span className="text-emerald-400 font-bold">✓</span> : <span className="text-slate-600">—</span>}</td>
                    </tr>
                    {aberto && ligasDoModelo.map((l) => (
                      <tr key={l.liga} className="bg-slate-950/40">
                        <td className="p-1.5"></td>
                        <td className="p-1.5 pl-6 text-slate-500">
                          {l.liga} {l.periodo_inicio && <span className="text-slate-700">({fmtPeriodo(l.periodo_inicio, l.periodo_fim)})</span>}
                        </td>
                        <td className="p-1.5 text-right text-slate-500">{fmtNum(l.log_loss)}</td>
                        <td className="p-1.5 text-right text-slate-500">{fmtNum(l.brier)}</td>
                        <td className="p-1.5 text-right text-slate-500">{fmtPct(l.accuracy)}</td>
                        <td className="p-1.5 text-right text-slate-500">{l.n_apostas}</td>
                        <td className={`p-1.5 text-right ${l.roi_medio > 0 ? 'text-emerald-500/80' : 'text-red-500/80'}`}>{fmtPctSigned(l.roi_medio)}</td>
                        <td className="p-1.5 text-right text-slate-600">[{fmtPctSigned(l.roi_ic95_inferior)}, {fmtPctSigned(l.roi_ic95_superior)}]</td>
                        <td className="p-1.5 text-center">{l.significativo ? <span className="text-emerald-500/80 font-bold">✓</span> : <span className="text-slate-700">—</span>}</td>
                        <td className="p-1.5 text-right text-slate-500">{l.n_apostas_abertura}</td>
                        <td className={`p-1.5 text-right ${l.roi_abertura_medio > 0 ? 'text-emerald-500/80' : 'text-red-500/80'}`}>{fmtPctSigned(l.roi_abertura_medio)}</td>
                        <td className="p-1.5 text-right text-slate-600">[{fmtPctSigned(l.roi_abertura_ic95_inferior)}, {fmtPctSigned(l.roi_abertura_ic95_superior)}]</td>
                        <td className="p-1.5 text-center">{l.significativo_abertura ? <span className="text-emerald-500/80 font-bold">✓</span> : <span className="text-slate-700">—</span>}</td>
                      </tr>
                    ))}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
          <p className="text-[10px] text-slate-600 mt-2">
            Linhas verdes = IC 95% do ROI inteiramente acima de zero em pelo menos um dos dois testes (EV+ estatisticamente
            sustentado, não só edge médio positivo). "Fech." = melhor odd real de fechamento entre todos os bookmakers;
            "Abert." = odd de abertura da Pinnacle especificamente. Clique na seta pra ver a quebra por liga (só disponível
            pra variante crua de cada modelo).
          </p>
        </div>
      )}
      <PainelInfoModelo
        modelName={modeloInfo}
        rotulo={ROTULO_MODELO[modeloInfo] || modeloInfo}
        aberto={!!modeloInfo}
        onFechar={() => setModeloInfo(null)}
      />
    </div>
  );
}

export default function ModelBenchmarking() {
  const { session } = useAuth();
  const [partidas, setPartidas] = useState([]);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState(null);
  const [disparando, setDisparando] = useState(false);
  const [mensagemDisparo, setMensagemDisparo] = useState(null);

  const carregar = useCallback(async () => {
    if (!supabaseAtivo) { setErro('Supabase não configurado.'); setCarregando(false); return; }
    setCarregando(true);
    setErro(null);
    try {
      // Só partidas recentes/futuras -- essa tela é o monitor das
      // predições DIÁRIAS (predict.yml só prevê as ~10 fixtures mais
      // próximas por rodada), não um navegador histórico (isso é papel de
      // /simulacao-carteira e do backtest abaixo). Sem esse filtro, o
      // backfill histórico (scripts/backfill_predicoes_historicas.py --
      // popula milhares de partidas antigas em `predicoes` pra alimentar a
      // Simulação de Carteira) fazia partidas de meses atrás inundarem
      // esta lista.
      const JANELA_DIAS_PASSADO = 7;
      const dataMinima = new Date(Date.now() - JANELA_DIAS_PASSADO * 86400000).toISOString();

      const [predicoes, odds, matches] = await Promise.all([
        buscarPaginado(supabase.from('predicoes').select('*').in('model_name', MODELOS_BASE).eq('mercado', '1X2')),
        buscarPaginado(supabase.from('market_odds').select('*')),
        buscarPaginado(supabase.from('matches').select('id, match_date, home_team_id, away_team_id').gte('match_date', dataMinima)),
      ]);

      const idsTimes = [...new Set(matches.flatMap(m => [m.home_team_id, m.away_team_id]))];
      const { data: times } = await supabase.from('teams').select('id, name').in('id', idsTimes.length ? idsTimes : [0]);
      const nomeTime = Object.fromEntries((times || []).map(t => [t.id, t.name]));
      const matchPorId = Object.fromEntries(matches.map(m => [m.id, m]));

      const oddsPorPartida = {};
      for (const o of odds) {
        if (!oddsPorPartida[o.match_id]) oddsPorPartida[o.match_id] = [];
        oddsPorPartida[o.match_id].push(o);
      }

      const porPartida = {};
      for (const p of predicoes) {
        if (!matchPorId[p.match_id]) continue; // fora da janela recente -- ver JANELA_DIAS_PASSADO acima
        if (!porPartida[p.match_id]) porPartida[p.match_id] = { match_id: p.match_id, modelos: {} };
        porPartida[p.match_id].modelos[p.model_name] = p;
      }

      const lista = Object.values(porPartida).map(g => {
        const m = matchPorId[g.match_id];
        const melhorOdd = (oddsPorPartida[g.match_id] || []).reduce((melhor, o) => {
          if (!melhor) return o;
          return (o.odd_home ?? 0) > (melhor.odd_home ?? 0) ? o : melhor;
        }, null);
        return {
          match_id: g.match_id,
          time_casa: m ? nomeTime[m.home_team_id] : `#${g.match_id}`,
          time_fora: m ? nomeTime[m.away_team_id] : '',
          match_date: m?.match_date,
          modelos: g.modelos,
          melhor_odd_casa: melhorOdd?.odd_home ?? null,
        };
      }).sort((a, b) => a.match_id - b.match_id);

      setPartidas(lista);
    } catch (e) {
      setErro(e.message);
    } finally {
      setCarregando(false);
    }
  }, []);

  useEffect(() => { carregar(); }, [carregar]);

  async function dispararPredicoes() {
    setDisparando(true);
    setMensagemDisparo(null);
    try {
      const resp = await fetch(apiUrl('/api/model-maintenance?tarefa=disparar-predicoes'), {
        method: 'POST',
        headers: { Authorization: `Bearer ${session?.access_token || ''}` },
      });
      const corpo = await resp.json();
      if (!resp.ok) throw new Error(corpo?.error?.message || `HTTP ${resp.status}`);
      setMensagemDisparo({
        tipo: 'ok',
        texto: 'Disparado! O GitHub Actions roda os modelos em background -- volte aqui em alguns minutos e clique em "Recarregar" pra ver o resultado.',
      });
    } catch (e) {
      setMensagemDisparo({ tipo: 'erro', texto: e.message });
    } finally {
      setDisparando(false);
    }
  }

  return (
    <div className="max-w-6xl mx-auto p-4 md:p-6 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-slate-100">Model Benchmarking</h1>
          <p className="text-sm text-slate-500">
            Predições diárias do Dixon-Coles pras próximas partidas — dados gerados de forma assíncrona pelo GitHub
            Actions (<code>scripts/rodar_predicoes.py</code>). O painel "Backtest completo" mais abaixo cobre também
            catboost/xgboost/lightgbm/mlp (v9/v10/v11), com metodologia de validação separada.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={carregar}
            disabled={carregando}
            className="px-3 py-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-sm border border-slate-700 disabled:opacity-50"
          >
            Recarregar
          </button>
          <button
            onClick={dispararPredicoes}
            disabled={disparando || !session}
            title={!session ? 'Faça login pra disparar as predições.' : ''}
            className="flex items-center gap-2 px-3 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {disparando ? <Loader2 size={16} className="animate-spin" /> : <Zap size={16} />}
            Disparar predições
          </button>
        </div>
      </div>

      {mensagemDisparo && (
        <div className={`rounded-lg border p-3 text-sm ${mensagemDisparo.tipo === 'ok' ? 'bg-emerald-950/40 border-emerald-800 text-emerald-300' : 'bg-red-950/40 border-red-800 text-red-300'}`}>
          {mensagemDisparo.texto}
        </div>
      )}

      {erro && (
        <div className="flex items-center gap-2 rounded-lg border border-red-800 bg-red-950/40 p-3 text-sm text-red-300">
          <AlertTriangle size={16} /> {erro}
        </div>
      )}

      {carregando ? (
        <div className="flex items-center gap-2 text-slate-500 text-sm py-8 justify-center">
          <Loader2 size={16} className="animate-spin" /> Carregando predições...
        </div>
      ) : partidas.length === 0 ? (
        <div className="text-center text-slate-500 text-sm py-12 border border-dashed border-slate-700 rounded-lg">
          Nenhuma predição ainda — clique em "Disparar predições" e volte em alguns minutos.
        </div>
      ) : (
        <div className="space-y-4">
          {partidas.map(p => (
            <div key={p.match_id} className="bg-slate-900 border border-slate-700/50 rounded-lg overflow-hidden">
              <div className="px-4 py-2 bg-slate-800/50 flex items-center justify-between text-sm">
                <span className="font-medium text-slate-200">{p.time_casa} x {p.time_fora}</span>
                <span className="text-slate-500 text-xs">
                  {p.match_date ? new Date(p.match_date).toLocaleDateString('pt-BR') : ''}
                  {p.melhor_odd_casa != null && <span className="ml-2 text-slate-400">melhor odd casa: {Number(p.melhor_odd_casa).toFixed(2)}</span>}
                </span>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-slate-500 text-xs uppercase">
                      <th className="text-left px-4 py-2">Modelo</th>
                      <th className="text-right px-4 py-2">Casa</th>
                      <th className="text-right px-4 py-2">Empate</th>
                      <th className="text-right px-4 py-2">Fora</th>
                      <th className="text-right px-4 py-2">Edge</th>
                    </tr>
                  </thead>
                  <tbody>
                    {MODELOS_BASE.map(nome => {
                      const pred = p.modelos[nome];
                      return (
                        <tr key={nome} className="border-t border-slate-800">
                          <td className="px-4 py-2 text-slate-300">{ROTULO_MODELO[nome]}</td>
                          <td className="px-4 py-2 text-right text-slate-200">{fmtPct(pred?.prob_home)}</td>
                          <td className="px-4 py-2 text-right text-slate-200">{fmtPct(pred?.prob_draw)}</td>
                          <td className="px-4 py-2 text-right text-slate-200">{fmtPct(pred?.prob_away)}</td>
                          <td className={`px-4 py-2 text-right font-medium ${pred?.edge_detectado > 0 ? 'text-emerald-400' : 'text-slate-500'}`}>
                            {pred?.edge_detectado != null ? (
                              <span className="inline-flex items-center gap-1">
                                {pred.edge_detectado > 0 && <TrendingUp size={12} />}
                                {fmtPct(pred.edge_detectado)}
                              </span>
                            ) : '—'}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          ))}
        </div>
      )}

      <p className="text-[11px] text-slate-600">
        Cada modelo também grava variantes calibradas (Platt/Isotonic) em <code>predicoes</code>
        (sufixo <code>_calibrado_platt</code>/<code>_calibrado_isotonic</code>) — não mostradas aqui pra manter o painel legível.
      </p>

      <BacktestModelBenchmarking session={session} />
    </div>
  );
}
