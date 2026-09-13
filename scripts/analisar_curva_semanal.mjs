// scripts/analisar_curva_semanal.mjs
//
// "Camada 1" do Expanding Window semanal (ver discussão na sessão de
// 13/09/2026): NÃO retreina nada -- os algoritmos de árvore (CatBoost/
// XGBoost/LightGBM) custariam ~260 runs de treino pra 5 anos de histórico
// fatiado por semana, contra as 9 runs/config de hoje (3 folds x 3
// algoritmos), estourando o orçamento de minutos do GitHub Actions e o
// timeout de 150min por run. Em vez disso, reaproveita as previsões JÁ
// persistidas (sempre treinadas com corte anterior ao período de teste, ver
// FOLDS em treinar_modelo_custom_wf.py) e fatia o backtest cronológico já
// existente (api/backtest-betting.js) por SEMANA ISO em vez de por ano
// inteiro -- responde "o ROI agregado é estável semana a semana, ou
// concentrado numa fatia pequena de sorte inicial?" (mesma técnica que
// achou o bug do shotmap em 12/09: quebrar o agregado até aparecer
// instabilidade).
//
// Uso:
//   node scripts/analisar_curva_semanal.mjs --mercado over_under_first_half_1h_0.5 \
//     [--modelo NOME] [--liga_id N] [--edge_minimo 0.02] [--staking flat|kelly] \
//     [--base_url https://SEU-DOMINIO-DE-PRODUCAO] [--data_inicio AAAA-MM-DD] [--data_fim AAAA-MM-DD]
//
// Saída: tabela semana a semana com ROI da SEMANA e ROI ACUMULADO (expanding
// window de verdade -- cada linha soma tudo desde a primeira semana até o
// fim daquela semana) + IC95% via bootstrap recalculado a cada corte, igual
// ao endpoint faz pro agregado inteiro.

const args = Object.fromEntries(
  process.argv.slice(2).reduce((acc, arg, i, arr) => {
    if (arg.startsWith('--')) acc.push([arg.slice(2), arr[i + 1]]);
    return acc;
  }, [])
);

const BASE_URL = args.base_url || process.env.BACKTEST_BASE_URL;
if (!BASE_URL) {
  console.error('Faltou --base_url (ou env BACKTEST_BASE_URL) -- URL de PRODUÇÃO do projeto (preview tem SSO e bloqueia curl/fetch).');
  process.exit(1);
}
if (!args.mercado) {
  console.error('Faltou --mercado (ex.: over_under_first_half_1h_0.5, corners_over_under_first_half_1h_4.5).');
  process.exit(1);
}

const qs = new URLSearchParams();
qs.set('mercado', args.mercado);
if (args.modelo) qs.set('modelo', args.modelo);
if (args.liga_id) qs.set('liga_id', args.liga_id);
if (args.edge_minimo) qs.set('edge_minimo', args.edge_minimo);
if (args.staking) qs.set('staking', args.staking);
if (args.data_inicio) qs.set('data_inicio', args.data_inicio);
if (args.data_fim) qs.set('data_fim', args.data_fim);

// Mesmo algoritmo de bootstrap de api/backtest-betting.js (reamostragem com
// reposição, 2000 iterações) -- duplicado aqui de propósito (script
// standalone, sem import de api/*.js pra não arrastar dependência de
// @supabase/supabase-js/CORS só pra isso).
function bootstrapROI(apostas, iteracoes = 2000) {
  const n = apostas.length;
  if (n === 0) return { lo: null, hi: null };
  const rois = [];
  for (let iter = 0; iter < iteracoes; iter++) {
    let lucro = 0, staked = 0;
    for (let i = 0; i < n; i++) {
      const a = apostas[Math.floor(Math.random() * n)];
      lucro += a.lucro;
      staked += a.stake;
    }
    rois.push(staked > 0 ? lucro / staked : 0);
  }
  rois.sort((a, b) => a - b);
  return { lo: rois[Math.floor(iteracoes * 0.025)], hi: rois[Math.floor(iteracoes * 0.975)] };
}

// Semana ISO (segunda a domingo) -- rótulo "AAAA-Www", estável ano a ano
// (diferente de "semana do mês", que reinicia e confunde virada de ano).
function semanaIso(dataStr) {
  const d = new Date(dataStr);
  d.setUTCHours(0, 0, 0, 0);
  d.setUTCDate(d.getUTCDate() + 4 - (d.getUTCDay() || 7));
  const anoIso = d.getUTCFullYear();
  const inicioAno = new Date(Date.UTC(anoIso, 0, 1));
  const semana = Math.ceil(((d - inicioAno) / 86400000 + 1) / 7);
  return `${anoIso}-W${String(semana).padStart(2, '0')}`;
}

async function main() {
  const url = `${BASE_URL.replace(/\/$/, '')}/api/backtest-betting?${qs.toString()}`;
  console.log(`Chamando ${url} ...`);
  const resp = await fetch(url);
  if (!resp.ok) {
    console.error(`HTTP ${resp.status} -- ${await resp.text()}`);
    process.exit(1);
  }
  const body = await resp.json();
  if (!body.grupos || body.grupos.length === 0) {
    console.log('Nenhum grupo retornado (0 apostas candidatas com esses filtros).');
    return;
  }

  for (const grupo of body.grupos) {
    console.log('\n' + '='.repeat(78));
    console.log(`${grupo.model_name} | ${grupo.market} | ${grupo.selection} | liga_id=${grupo.league_id}`);
    console.log(`Agregado (endpoint): n=${grupo.n_apostas}  roi=${(grupo.roi * 100).toFixed(2)}%  IC95=[${(grupo.roi_ic95_inferior * 100).toFixed(2)}%, ${(grupo.roi_ic95_superior * 100).toFixed(2)}%]  significativo=${grupo.significativo}`);
    console.log('-'.repeat(78));

    const apostas = grupo.serie_temporal.map(a => ({ ...a, semana: semanaIso(a.date) }));
    const semanasOrdenadas = [...new Set(apostas.map(a => a.semana))]; // já cronológico (serie_temporal vem ordenada)

    console.log('semana      | n_sem | roi_sem  | n_acum | roi_acum | ic95_inf | ic95_sup | signif');
    let acumuladas = [];
    for (const semana of semanasOrdenadas) {
      const daSemana = apostas.filter(a => a.semana === semana);
      acumuladas = acumuladas.concat(daSemana);

      const stakeSem = daSemana.reduce((s, a) => s + a.stake, 0);
      const lucroSem = daSemana.reduce((s, a) => s + a.lucro, 0);
      const roiSem = stakeSem > 0 ? lucroSem / stakeSem : 0;

      const stakeAcum = acumuladas.reduce((s, a) => s + a.stake, 0);
      const lucroAcum = acumuladas.reduce((s, a) => s + a.lucro, 0);
      const roiAcum = stakeAcum > 0 ? lucroAcum / stakeAcum : 0;
      const ic = bootstrapROI(acumuladas);

      const signif = ic.lo != null && ic.lo > 0;
      console.log(
        `${semana} | ${String(daSemana.length).padStart(5)} | ${(roiSem * 100).toFixed(1).padStart(7)}% | ` +
        `${String(acumuladas.length).padStart(6)} | ${(roiAcum * 100).toFixed(1).padStart(7)}% | ` +
        `${(ic.lo * 100).toFixed(1).padStart(7)}% | ${(ic.hi * 100).toFixed(1).padStart(7)}% | ${signif ? 'SIM' : ''}`
      );
    }
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
