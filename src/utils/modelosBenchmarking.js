// src/utils/modelosBenchmarking.js
// Lista de modelos do "Model Benchmarking" (dixon_coles_v1 + catboost/xgboost/
// lightgbm a partir da v3) compartilhada entre ModelBenchmarking.jsx e
// RodadaPrevisoes.jsx. v1/v2 de cada árvore continuam sendo previstos e
// persistidos em `predicoes` pelo pipeline, só não aparecem em nenhuma tela
// (pedido do usuário: "deixe somente a partir da terceira versão").
export const MODELOS_BASE = [
  'dixon_coles_v1',
  'catboost_v3', 'xgboost_v3', 'lightgbm_v3',
  'catboost_v4', 'xgboost_v4', 'lightgbm_v4',
  'catboost_v5', 'xgboost_v5', 'lightgbm_v5',
  'catboost_v3b', 'xgboost_v3b', 'lightgbm_v3b',
];

export const ROTULO_MODELO = {
  dixon_coles_v1: 'Dixon-Coles',
  catboost_v3: 'CatBoost v3 (+ fadiga)',
  xgboost_v3: 'XGBoost v3 (+ fadiga)',
  lightgbm_v3: 'LightGBM v3 (+ fadiga)',
  catboost_v4: 'CatBoost v4 (+ cartões)',
  xgboost_v4: 'XGBoost v4 (+ cartões)',
  lightgbm_v4: 'LightGBM v4 (+ cartões)',
  catboost_v5: 'CatBoost v5 (+ tabela/H2H/árbitro)',
  xgboost_v5: 'XGBoost v5 (+ tabela/H2H/árbitro)',
  lightgbm_v5: 'LightGBM v5 (+ tabela/H2H/árbitro)',
  catboost_v3b: 'CatBoost v3B (+ XI titular/valor de mercado)',
  xgboost_v3b: 'XGBoost v3B (+ XI titular/valor de mercado)',
  lightgbm_v3b: 'LightGBM v3B (+ XI titular/valor de mercado)',
};
