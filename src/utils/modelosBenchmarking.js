// src/utils/modelosBenchmarking.js
// Lista de modelos do "Model Benchmarking" (tabela `predicoes`) compartilhada
// entre ModelBenchmarking.jsx e RodadaPrevisoes.jsx. v1-v8 de cada árvore
// (catboost/xgboost/lightgbm) foram removidos do pipeline -- versões
// superadas pelo v9 (scripts/walkforward_cv_v9.py, tabela `model_predictions`,
// tela separada). dixon_coles_v1 continua: é o modelo de produção real,
// atualizado todo dia pelo cron (predict.yml).
export const MODELOS_BASE = [
  'dixon_coles_v1',
];

export const ROTULO_MODELO = {
  dixon_coles_v1: 'Dixon-Coles',
};
