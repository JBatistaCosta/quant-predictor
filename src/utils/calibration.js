// src/utils/calibration.js
// Aplica em produção a calibração (Platt Scaling / Isotonic Regression) já
// ajustada e persistida em `model_calibration` por api/model-maintenance.js
// (?tarefa=calibracao). Reimplementada aqui (em vez de chamar uma API) porque
// o consumo é direto do Supabase no navegador, em AnaliseEstatisticaJogo.jsx —
// mesma matemática do lado servidor (ver aplicarPlatt/aplicarIsotonicPredicao
// em api/model-maintenance.js).
//
// Achado (CONTEXTO_PROJETO.md): nos mercados binários (over/under gols e
// escanteios) o modelo bruto é sistematicamente overconfident e Platt/Isotonic
// costumam ajudar — mas não sempre: recalibrando catboost/xgboost/lightgbm_v9/
// v11 nesta sessão, 22 das 72 combinações (model_name, market, selection)
// ficaram com log-loss PIOR mesmo na melhor das duas calibrações (achado:
// classificadores já saem razoavelmente bem calibrados por conta própria,
// com bastante dado de treino — Platt/Isotonic aí só adicionam ruído do split
// de teste). Por isso o candidato "sem calibração" (log_loss_bruto, mesma
// métrica medida no mesmo split) entra na disputa ao lado de Platt/Isotonic
// — usamos sempre o de MENOR log-loss medido, que pode ser a prob. crua.
//
// Nota sobre 1X2: a calibração é one-vs-rest (home/draw/away calibrados como
// 3 problemas binários independentes, mesmo método usado pra medir log-loss
// em api/model-stats.js) — as 3 probabilidades calibradas de um mesmo jogo
// não são forçadas a somar 100%. Isso é intencional (não foi validado
// renormalizar), então a UI mostra cada seleção com sua própria calibração.

const clamp = (p) => Math.min(Math.max(p, 1e-4), 1 - 1e-4);
const logit = (p) => Math.log(clamp(p) / (1 - clamp(p)));
const sigmoid = (x) => 1 / (1 + Math.exp(-x));

const aplicarPlatt = (p, coef, intercept) => sigmoid(coef * logit(p) + intercept);

function aplicarIsotonic(p, xs, ys) {
  if (!Array.isArray(xs) || !Array.isArray(ys) || xs.length === 0) return null;
  if (p <= xs[0]) return ys[0];
  if (p >= xs[xs.length - 1]) return ys[ys.length - 1];
  for (let i = 0; i < xs.length - 1; i++) {
    if (p >= xs[i] && p <= xs[i + 1]) {
      const t = (p - xs[i]) / (xs[i + 1] - xs[i] || 1);
      return ys[i] + t * (ys[i + 1] - ys[i]);
    }
  }
  return p;
}

// Linhas cruas de `model_calibration` -> índice "model__market__selection" -> { platt, isotonic, logLossBruto }
export function indexarCalibracao(linhas) {
  const indice = {};
  (linhas || []).forEach(c => {
    const chave = `${c.model_name}__${c.market}__${c.selection}`;
    if (!indice[chave]) indice[chave] = {};
    // log_loss_bruto é o mesmo valor independente do método (medido na mesma
    // prob. crua, no mesmo split de teste) — grava uma vez só por chave.
    if (c.log_loss_bruto != null) indice[chave].logLossBruto = Number(c.log_loss_bruto);
    if (c.method === 'platt' && c.platt_coef != null) {
      indice[chave].platt = { a: Number(c.platt_coef), b: Number(c.platt_intercept), logLoss: Number(c.log_loss_calibrado), nTeste: c.n_teste };
    } else if (c.method === 'isotonic' && c.isotonic_x) {
      indice[chave].isotonic = { x: c.isotonic_x, y: c.isotonic_y, logLoss: Number(c.log_loss_calibrado), nTeste: c.n_teste };
    }
  });
  return indice;
}

// Aplica a calibração de menor log-loss medido no split de teste, entre
// Platt, Isotonic E a própria probabilidade crua (achado: em 22/72 combos
// model+market+selection recalibradas nesta sessão, a melhor calibração
// ainda ficava pior que não calibrar nada — ver comentário no topo do
// arquivo). Retorna null quando não há calibração salva para essa combinação
// OU quando a bruta venceu as duas (equivalente, pro chamador, a "não
// calibrar") — nos dois casos o candidato deve usar a probabilidade crua.
export function calibrarProbabilidade(pBruto, indiceCalibracao, modelName, market, selection) {
  const calib = indiceCalibracao[`${modelName}__${market}__${selection}`];
  if (!calib) return null;

  const candidatos = [];
  if (calib.logLossBruto != null) candidatos.push({ metodo: null, logLoss: calib.logLossBruto, nTeste: null, valor: pBruto });
  if (calib.platt) candidatos.push({ metodo: 'platt', logLoss: calib.platt.logLoss, nTeste: calib.platt.nTeste, valor: aplicarPlatt(pBruto, calib.platt.a, calib.platt.b) });
  if (calib.isotonic) candidatos.push({ metodo: 'isotonic', logLoss: calib.isotonic.logLoss, nTeste: calib.isotonic.nTeste, valor: aplicarIsotonic(pBruto, calib.isotonic.x, calib.isotonic.y) });
  if (candidatos.length === 0) return null;

  candidatos.sort((a, b) => a.logLoss - b.logLoss);
  const melhor = candidatos[0];
  if (melhor.metodo === null) return null;
  return { probabilidade: clamp(melhor.valor), metodo: melhor.metodo, nTeste: melhor.nTeste };
}
