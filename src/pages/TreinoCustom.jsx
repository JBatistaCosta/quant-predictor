// src/pages/TreinoCustom.jsx — rota /treino-custom
// Painel de Treino Customizado: permite criar configurações de modelos
// (algoritmo + features selecionadas + hiperparâmetros) e disparar o
// workflow treinar_modelo_custom.yml no GitHub Actions via
// api/model-maintenance?tarefa=salvar-config-custom /
// listar-configs-custom / disparar-treino-custom.
//
// O status de cada configuração é gerenciado pelo próprio workflow:
//   rascunho → aguardando_treino → treinando → treinado | erro
// O painel faz polling leve (a cada 15s) nos configs que estão em
// aguardando_treino ou treinando, atualizando o status/métricas quando
// o workflow conclui.
import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Plus, Play, Save, Trash2, ChevronDown, ChevronUp, Loader2,
  AlertTriangle, CheckCircle2, Clock, XCircle, Zap, Settings2,
  FlaskConical, BarChart3, RefreshCw, FileText, Copy, StopCircle, RotateCcw
} from 'lucide-react';
import { useAuth } from '../AuthContext';
import { apiUrl } from '../utils/apiUrl';
import RelatorioTreinoModal from '../components/RelatorioTreinoModal';

// -----------------------------------------------------------------------
// Catálogo de features disponíveis, agrupadas por tema
// Legenda de cobertura: ▃ = Baixa (<40%) | ▅ = Média (40-80%) | █ = Alta (>80%)
// ★ = Feature do Modelo de XI Previsto (fallback pré-escalação oficial)
// -----------------------------------------------------------------------
const FEATURE_GROUPS = [
  {
    grupo: 'Força de time',
    features: [
      { key: 'elo_home', label: 'Elo mandante', cov: '█' },
      { key: 'elo_away', label: 'Elo visitante', cov: '█' },
      { key: 'squad_rating_home', label: 'Rating médio do elenco (mandante)', cov: '▅' },
      { key: 'squad_rating_away', label: 'Rating médio do elenco (visitante)', cov: '▅' },
    ],
  },
  {
    grupo: 'Forma recente — Gols',
    features: [
      { key: 'media_gols_marcados_5j_home', label: 'Gols marcados (mandante, 5j)', cov: '█' },
      { key: 'media_gols_sofridos_5j_home', label: 'Gols sofridos (mandante, 5j)', cov: '█' },
      { key: 'media_gols_marcados_5j_away', label: 'Gols marcados (visitante, 5j)', cov: '█' },
      { key: 'media_gols_sofridos_5j_away', label: 'Gols sofridos (visitante, 5j)', cov: '█' },
    ],
  },
  {
    grupo: 'Fadiga e Contexto de Temporada',
    features: [
      { key: 'days_since_last_match_home', label: 'Dias desde último jogo (mandante)', cov: '█' },
      { key: 'days_since_last_match_away', label: 'Dias desde último jogo (visitante)', cov: '█' },
      { key: 'is_midweek_fatigue_home', label: 'Flag de turnaround apertado (mandante)', cov: '█' },
      { key: 'is_midweek_fatigue_away', label: 'Flag de turnaround apertado (visitante)', cov: '█' },
      { key: 'progresso_temporada', label: 'Progresso da temporada (0→1)', cov: '█' },
    ],
  },
  {
    grupo: 'Disciplina — Cartões e Suspensões',
    features: [
      { key: 'cartoes_acumulados_home', label: 'Cartões acumulados no ciclo (mandante)', cov: '█' },
      { key: 'cartoes_acumulados_away', label: 'Cartões acumulados no ciclo (visitante)', cov: '█' },
      { key: 'jogadores_pendurados_home', label: 'Jogadores a 1 cartão da suspensão (mandante)', cov: '█' },
      { key: 'jogadores_pendurados_away', label: 'Jogadores a 1 cartão da suspensão (visitante)', cov: '█' },
    ],
  },
  {
    grupo: 'Classificação — Tabela',
    features: [
      { key: 'posicao_home', label: 'Posição na tabela (mandante)', cov: '█' },
      { key: 'posicao_away', label: 'Posição na tabela (visitante)', cov: '█' },
      { key: 'pontos_por_jogo_home', label: 'Pontos por jogo (mandante)', cov: '█' },
      { key: 'pontos_por_jogo_away', label: 'Pontos por jogo (visitante)', cov: '█' },
      { key: 'saldo_por_jogo_home', label: 'Saldo de gols por jogo (mandante)', cov: '█' },
      { key: 'saldo_por_jogo_away', label: 'Saldo de gols por jogo (visitante)', cov: '█' },
      { key: 'jogos_disputados_home', label: 'Jogos disputados na temp. (mandante)', cov: '█' },
      { key: 'jogos_disputados_away', label: 'Jogos disputados na temp. (visitante)', cov: '█' },
    ],
  },
  {
    grupo: 'Confronto Direto (H2H)',
    features: [
      { key: 'h2h_taxa_vitoria_mandante', label: 'Taxa vitória mandante (histórico H2H)', cov: '█' },
      { key: 'h2h_media_gols', label: 'Média de gols por jogo (H2H)', cov: '█' },
      { key: 'h2h_n_jogos', label: 'Número de confrontos no histórico', cov: '█' },
    ],
  },
  {
    grupo: 'Árbitro',
    features: [
      { key: 'arbitro_cartoes_media', label: 'Média de cartões por jogo (árbitro)', cov: '▅' },
      { key: 'arbitro_faltas_media', label: 'Média de faltas por jogo (árbitro)', cov: '▅' },
      { key: 'arbitro_n_jogos', label: 'Nº de jogos apitados pelo árbitro', cov: '▅' },
    ],
  },
  {
    grupo: 'XI Titular — Real e Previsto ★',
    features: [
      { key: 'titular_rating_home', label: 'Rating médio XI (mandante)', cov: '▅' },
      { key: 'titular_rating_away', label: 'Rating médio XI (visitante)', cov: '▅' },
      { key: 'titular_valor_mercado_home', label: 'Valor de mercado XI real (mandante, €M)', cov: '▅' },
      { key: 'titular_valor_mercado_away', label: 'Valor de mercado XI real (visitante, €M)', cov: '▅' },
      { key: 'titular_avg_age_home', label: 'Idade média XI (mandante)', cov: '▃' },
      { key: 'titular_avg_age_away', label: 'Idade média XI (visitante)', cov: '▃' },
      { key: 'titular_avg_height_home', label: 'Altura média XI (mandante, cm)', cov: '▃' },
      { key: 'titular_avg_height_away', label: 'Altura média XI (visitante, cm)', cov: '▃' },
      { key: 'venue_capacity_home', label: 'Capacidade do estádio (mandante)', cov: '▃' },
    ],
  },
  {
    grupo: 'xG e Estatísticas Avançadas',
    features: [
      { key: 'xg_home_5j', label: 'xG marcado (mandante, 5j)', cov: '▅' },
      { key: 'xg_away_5j', label: 'xG marcado (visitante, 5j)', cov: '▅' },
      { key: 'xg_sofrido_home_5j', label: 'xG sofrido (mandante, 5j)', cov: '▅' },
      { key: 'xg_sofrido_away_5j', label: 'xG sofrido (visitante, 5j)', cov: '▅' },
      { key: 'xg_home_10j', label: 'xG marcado (mandante, 10j)', cov: '▅' },
      { key: 'xg_away_10j', label: 'xG marcado (visitante, 10j)', cov: '▅' },
      { key: 'xg_sofrido_home_10j', label: 'xG sofrido (mandante, 10j)', cov: '▅' },
      { key: 'xg_sofrido_away_10j', label: 'xG sofrido (visitante, 10j)', cov: '▅' },
      { key: 'xg_home_5j_decay', label: 'xG marcado EWMA (mandante, 5j)', cov: '▅' },
      { key: 'xg_away_5j_decay', label: 'xG marcado EWMA (visitante, 5j)', cov: '▅' },
      { key: 'xg_home_10j_decay', label: 'xG marcado EWMA (mandante, 10j)', cov: '▅' },
      { key: 'xg_away_10j_decay', label: 'xG marcado EWMA (visitante, 10j)', cov: '▅' },
      { key: 'xg_home_20j_decay', label: 'xG marcado EWMA (mandante, 20j)', cov: '▅' },
      { key: 'xg_away_20j_decay', label: 'xG marcado EWMA (visitante, 20j)', cov: '▅' },
      { key: 'xg_bayesiano_home', label: 'xG Bayesiano Shrinkage EWMA (mandante)', cov: '▅' },
      { key: 'xg_bayesiano_away', label: 'xG Bayesiano Shrinkage EWMA (visitante)', cov: '▅' },
      { key: 'xga_bayesiano_home', label: 'xGA Bayesiano (Sofrido, mandante)', cov: '▅' },
      { key: 'xga_bayesiano_away', label: 'xGA Bayesiano (Sofrido, visitante)', cov: '▅' },
      { key: 'xgot_bayesiano_home', label: 'xGOT Bayesiano Shrinkage (mandante)', cov: '▅' },
      { key: 'xgot_bayesiano_away', label: 'xGOT Bayesiano Shrinkage (visitante)', cov: '▅' },
      { key: 'is_stat_estimated_home', label: 'Flag: estatística estimada/bayesiana (mandante)', cov: '█' },
      { key: 'is_stat_estimated_away', label: 'Flag: estatística estimada/bayesiana (visitante)', cov: '█' },
    ],
  },
  {
    grupo: 'Features Derivadas — Diferenciais e Momentos (v11)',
    features: [
      { key: 'elo_diff', label: 'Diferença de Elo (mand. − vis.)', cov: '█' },
      { key: 'xg_diff_bayesiano', label: 'Diferença de xG bayesiano (mand. − vis.)', cov: '▅' },
      { key: 'xgot_diff_bayesiano', label: 'Diferença de xGOT bayesiano (mand. − vis.)', cov: '▅' },
      { key: 'squad_rating_diff', label: 'Diferença de rating do elenco (mand. − vis.)', cov: '▅' },
      { key: 'rating_diff_xi', label: 'Diferença de rating do XI (mand. − vis.)', cov: '▅' },
      { key: 'valor_diff_xi', label: 'Diferença de valor de mercado do XI (€M)', cov: '▅' },
      { key: 'age_diff_xi', label: 'Diferença de idade média do XI', cov: '▃' },
      { key: 'height_diff_xi', label: 'Diferença de altura média do XI (cm)', cov: '▃' },
      { key: 'xg_momentum_home', label: 'Momentum xG atacante (mand., 5j−10j)', cov: '▅' },
      { key: 'xg_momentum_away', label: 'Momentum xG atacante (vis., 5j−10j)', cov: '▅' },
      { key: 'posicao_diff', label: 'Diferença de posição na tabela (vis.−mand.)', cov: '█' },
      { key: 'pontos_diff', label: 'Diferença de pts/jogo (mand. − vis.)', cov: '█' },
    ],
  },
  {
    grupo: 'FBref: Forma Básica (v7)',
    features: [
      { key: 'media_posse_5j_home', label: 'Posse de bola (mandante, 5j)', cov: '▅' },
      { key: 'media_posse_sofrida_5j_home', label: 'Posse adversária (mandante, 5j)', cov: '▅' },
      { key: 'media_posse_5j_away', label: 'Posse de bola (visitante, 5j)', cov: '▅' },
      { key: 'media_posse_sofrida_5j_away', label: 'Posse adversária (visitante, 5j)', cov: '▅' },
      { key: 'media_chutes_5j_home', label: 'Chutes (mandante, 5j)', cov: '▅' },
      { key: 'media_chutes_sofridos_5j_home', label: 'Chutes sofridos (mandante, 5j)', cov: '▅' },
      { key: 'media_chutes_5j_away', label: 'Chutes (visitante, 5j)', cov: '▅' },
      { key: 'media_chutes_sofridos_5j_away', label: 'Chutes sofridos (visitante, 5j)', cov: '▅' },
      { key: 'media_chutes_alvo_5j_home', label: 'Chutes no alvo (mandante, 5j)', cov: '▅' },
      { key: 'media_chutes_alvo_sofridos_5j_home', label: 'Chutes no alvo sofridos (mandante, 5j)', cov: '▅' },
      { key: 'media_chutes_alvo_5j_away', label: 'Chutes no alvo (visitante, 5j)', cov: '▅' },
      { key: 'media_chutes_alvo_sofridos_5j_away', label: 'Chutes no alvo sofridos (visitante, 5j)', cov: '▅' },
      { key: 'media_escanteios_5j_home', label: 'Escanteios (mandante, 5j)', cov: '▅' },
      { key: 'media_escanteios_sofridos_5j_home', label: 'Escanteios sofridos (mandante, 5j)', cov: '▅' },
      { key: 'media_escanteios_5j_away', label: 'Escanteios (visitante, 5j)', cov: '▅' },
      { key: 'media_escanteios_sofridos_5j_away', label: 'Escanteios sofridos (visitante, 5j)', cov: '▅' },
      { key: 'media_faltas_5j_home', label: 'Faltas cometidas (mandante, 5j)', cov: '▅' },
      { key: 'media_faltas_sofridas_5j_home', label: 'Faltas sofridas (mandante, 5j)', cov: '▅' },
      { key: 'media_faltas_5j_away', label: 'Faltas cometidas (visitante, 5j)', cov: '▅' },
      { key: 'media_faltas_sofridas_5j_away', label: 'Faltas sofridas (visitante, 5j)', cov: '▅' },
      { key: 'media_cartoes_amarelos_5j_home', label: 'Cartões amarelos (mandante, 5j)', cov: '▅' },
      { key: 'media_cartoes_amarelos_sofridos_5j_home', label: 'Cartões amarelos adv (mandante, 5j)', cov: '▅' },
      { key: 'media_cartoes_amarelos_5j_away', label: 'Cartões amarelos (visitante, 5j)', cov: '▅' },
      { key: 'media_cartoes_amarelos_sofridos_5j_away', label: 'Cartões amarelos adv (visitante, 5j)', cov: '▅' },
      { key: 'media_cartoes_vermelhos_5j_home', label: 'Cartões vermelhos (mandante, 5j)', cov: '▃' },
      { key: 'media_cartoes_vermelhos_5j_away', label: 'Cartões vermelhos (visitante, 5j)', cov: '▃' },
    ],
  },
  {
    grupo: 'FotMob: Situação de Chutes (v9)',
    features: [
      { key: 'media_pct_fast_break_fm_5j_home', label: '% Chutes em contra-ataque (mandante, 5j)', cov: '▅' },
      { key: 'media_pct_fast_break_fm_sofrido_5j_home', label: '% Contra-ataques sofridos (mandante, 5j)', cov: '▅' },
      { key: 'media_pct_fast_break_fm_5j_away', label: '% Chutes em contra-ataque (visitante, 5j)', cov: '▅' },
      { key: 'media_pct_fast_break_fm_sofrido_5j_away', label: '% Contra-ataques sofridos (visitante, 5j)', cov: '▅' },
      { key: 'media_pct_bola_parada_fm_5j_home', label: '% Chutes de bola parada (mandante, 5j)', cov: '▅' },
      { key: 'media_pct_bola_parada_fm_sofrido_5j_home', label: '% Bola parada sofrida (mandante, 5j)', cov: '▅' },
      { key: 'media_pct_bola_parada_fm_5j_away', label: '% Chutes de bola parada (visitante, 5j)', cov: '▅' },
      { key: 'media_pct_bola_parada_fm_sofrido_5j_away', label: '% Bola parada sofrida (visitante, 5j)', cov: '▅' },
      { key: 'media_xg_chute_fm_5j_home', label: 'xG médio por chute (mandante, 5j)', cov: '▅' },
      { key: 'media_xg_chute_fm_sofrido_5j_home', label: 'xG/chute adversário (mandante, 5j)', cov: '▅' },
      { key: 'media_xg_chute_fm_5j_away', label: 'xG médio por chute (visitante, 5j)', cov: '▅' },
      { key: 'media_xg_chute_fm_sofrido_5j_away', label: 'xG/chute adversário (visitante, 5j)', cov: '▅' },
      { key: 'media_pct_gols_2tempo_fm_5j_home', label: '% Gols no 2º tempo (mandante, 5j)', cov: '▅' },
      { key: 'media_pct_gols_2tempo_fm_sofrido_5j_home', label: '% Gols sofridos no 2º tempo (mandante, 5j)', cov: '▅' },
      { key: 'media_pct_gols_2tempo_fm_5j_away', label: '% Gols no 2º tempo (visitante, 5j)', cov: '▅' },
      { key: 'media_pct_gols_2tempo_fm_sofrido_5j_away', label: '% Gols sofridos no 2º tempo (visitante, 5j)', cov: '▅' },
    ],
  },
  // FotMob dynamic groups appended below via FOTMOB_METRICS generator
];

// -----------------------------------------------------------------------
// Geração Dinâmica das Estatísticas do FotMob (Centenas de colunas)
// -----------------------------------------------------------------------
const FOTMOB_METRICS = [
  { short: "xgot", label: "xGOT", category: "Finalização" },
  { short: "chutes_fm", label: "Chutes", category: "Finalização" },
  { short: "chutes_alvo_fm", label: "Chutes no Alvo", category: "Finalização" },
  { short: "chutes_fora_fm", label: "Chutes Fora", category: "Finalização" },
  { short: "chutes_bloqueados_fm", label: "Chutes Bloqueados", category: "Finalização" },
  { short: "chutes_area_fm", label: "Chutes na Área", category: "Finalização" },
  { short: "chutes_fora_area_fm", label: "Chutes de Fora da Área", category: "Finalização" },
  { short: "chances_claras_fm", label: "Grandes Chances", category: "Finalização" },
  { short: "chances_claras_perdidas_fm", label: "Grandes Chances Perdidas", category: "Finalização" },
  { short: "toques_area_adv_fm", label: "Toques na Área Adv.", category: "Ataque Geral" },
  { short: "escanteios_fm", label: "Escanteios", category: "Ataque Geral" },
  { short: "dribles_certos_fm", label: "Dribles Certos", category: "Ataque Geral" },
  { short: "passes_certos_fm", label: "Passes Certos", category: "Posse e Passes" },
  { short: "bolas_longas_certas_fm", label: "Bolas Longas Certas", category: "Posse e Passes" },
  { short: "cruzamentos_certos_fm", label: "Cruzamentos Certos", category: "Posse e Passes" },
  { short: "posse_fm", label: "Posse de Bola", category: "Posse e Passes" },
  { short: "desarmes_fm", label: "Desarmes", category: "Defesa" },
  { short: "interceptacoes_fm", label: "Interceptações", category: "Defesa" },
  { short: "bloqueios_fm", label: "Bloqueios", category: "Defesa" },
  { short: "afastamentos_fm", label: "Afastamentos", category: "Defesa" },
  { short: "defesas_goleiro_fm", label: "Defesas do Goleiro", category: "Defesa" },
  { short: "duelos_vencidos_fm", label: "Duelos (Geral)", category: "Defesa" },
  { short: "duelos_aereos_vencidos_fm", label: "Duelos Aéreos Vencidos", category: "Defesa" },
  { short: "faltas_fm", label: "Faltas Cometidas", category: "Faltas e Cartões" },
  { short: "cartoes_amarelos_fm", label: "Cartões Amarelos", category: "Faltas e Cartões" },
  { short: "cartoes_vermelhos_fm", label: "Cartões Vermelhos", category: "Faltas e Cartões" },
];

const groupedFotMob = {};

groupedFotMob["Finalização"] = [
  { key: 'xgot_bayesiano_home', label: 'xGOT Bayesiano Shrinkage (mandante)' },
  { key: 'xgot_bayesiano_away', label: 'xGOT Bayesiano Shrinkage (visitante)' },
];

FOTMOB_METRICS.forEach(metric => {
  if (!groupedFotMob[metric.category]) groupedFotMob[metric.category] = [];

  if (!metric.short.endsWith('_fm')) {
    // Métricas no formato multi-janela novo (ex: xgot) — chave: {metric}_home_{janela}
    // O dataset gera essas colunas via _forma_por_mando_multi_janelas com o mesmo nome.
    ['5j', '10j', '20j', '5j_decay', '10j_decay', '20j_decay'].forEach(janela => {
      const lbl = janela.replace('_decay', ' decay').replace(/_/g, ' ');
      groupedFotMob[metric.category].push(
        { key: `${metric.short}_home_${janela}`, label: `${metric.label} (mand., ${lbl})` },
        { key: `${metric.short}_sofrido_home_${janela}`, label: `${metric.label} sofrido (mand., ${lbl})` },
        { key: `${metric.short}_away_${janela}`, label: `${metric.label} (vis., ${lbl})` },
        { key: `${metric.short}_sofrido_away_${janela}`, label: `${metric.label} sofrido (vis., ${lbl})` }
      );
    });
  } else {
    // Métricas FotMob v8 — somente 5j disponível no dataset.
    // Chave correta: media_{metric}_5j_{position}  (ex: media_chutes_area_fm_5j_home)
    groupedFotMob[metric.category].push(
      { key: `media_${metric.short}_5j_home`, label: `${metric.label} (mand., 5j)` },
      { key: `media_${metric.short}_sofrido_5j_home`, label: `${metric.label} sofrido (mand., 5j)` },
      { key: `media_${metric.short}_5j_away`, label: `${metric.label} (vis., 5j)` },
      { key: `media_${metric.short}_sofrido_5j_away`, label: `${metric.label} sofrido (vis., 5j)` }
    );
  }
});

// (nenhum pop aqui — o grupo de Situação de Chutes acima é real, não placeholder)

Object.keys(groupedFotMob).forEach(category => {
  FEATURE_GROUPS.push({
    grupo: `FotMob: ${category}`,
    features: groupedFotMob[category]
  });
});

const ALGORITMOS = [
  { value: 'catboost', label: 'CatBoost' },
  { value: 'xgboost', label: 'XGBoost' },
  { value: 'lightgbm', label: 'LightGBM' },
  { value: 'logistic_regression', label: 'Regressão Logística' },
  { value: 'random_forest', label: 'Random Forest' },
  { value: 'dixon_coles', label: 'Dixon-Coles' },
];

const TARGETS = [
  { value: '1x2', label: 'Resultado 1X2' },
  { value: 'over_under_2.5', label: 'Over/Under 2.5' },
  { value: 'btts', label: 'Ambas marcam (BTTS)' },
];

const STATUS_INFO = {
  rascunho: { label: 'Rascunho', icon: Settings2, cor: 'text-slate-400', bg: 'bg-slate-700' },
  aguardando_treino: { label: 'Aguardando treino', icon: Clock, cor: 'text-yellow-400', bg: 'bg-yellow-900/30' },
  treinando: { label: 'Treinando...', icon: Loader2, cor: 'text-blue-400', bg: 'bg-blue-900/30' },
  treinado: { label: 'Treinado ✓', icon: CheckCircle2, cor: 'text-emerald-400', bg: 'bg-emerald-900/30' },
  erro: { label: 'Erro', icon: XCircle, cor: 'text-red-400', bg: 'bg-red-900/30' },
};

const ESTADO_FORM_INICIAL = {
  id: null,
  name: '',
  algorithm: 'catboost',
  features: [],
  target: '1x2',
  notes: '',
  hyperparameters: '',
};

// -----------------------------------------------------------------------
// Componente Principal
// -----------------------------------------------------------------------
export default function TreinoCustom() {
  const { session } = useAuth();
  const [configs, setConfigs] = useState([]);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState(null);
  const [form, setForm] = useState(ESTADO_FORM_INICIAL);
  const [salvando, setSalvando] = useState(false);
  const [disparando, setDisparando] = useState(null); // config_id em disparo
  const [cancelando, setCancelando] = useState(null); // config_id sendo cancelado
  const [copiando, setCopiando] = useState(null); // config_id sendo copiado
  const [resetando, setResetando] = useState(null); // config_id sendo resetado
  const [excluindo, setExcluindo] = useState(null); // config_id sendo excluído
  const [confirmandoExclusao, setConfirmandoExclusao] = useState(null); // config_id aguardando confirm
  const [mensagem, setMensagem] = useState(null); // { tipo: 'ok'|'erro', texto }
  const [expandidos, setExpandidos] = useState({}); // config_id → bool
  const [grupoExpandido, setGrupoExpandido] = useState({}); // grupo → bool
  const [formAberto, setFormAberto] = useState(false);
  const [configModalAberto, setConfigModalAberto] = useState(null);
  const pollingRef = useRef(null);

  const authHeader = session?.access_token
    ? { Authorization: `Bearer ${session.access_token}` }
    : {};

  // --------- Carregamento ---------
  const carregarConfigs = useCallback(async (silencioso = false) => {
    if (!silencioso) setCarregando(true);
    setErro(null);
    try {
      const resp = await fetch(apiUrl('/api/model-maintenance?tarefa=listar-configs-custom'));
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const dados = await resp.json();
      setConfigs(dados.configs || []);
    } catch (e) {
      if (!silencioso) setErro(e.message);
    } finally {
      if (!silencioso) setCarregando(false);
    }
  }, []);

  useEffect(() => {
    carregarConfigs();
  }, [carregarConfigs]);

  // Polling leve: verifica configs em progresso a cada 15s
  useEffect(() => {
    const temEmProgresso = configs.some(
      (c) => c.status === 'aguardando_treino' || c.status === 'treinando',
    );
    if (temEmProgresso) {
      pollingRef.current = setInterval(() => carregarConfigs(true), 15000);
    } else {
      clearInterval(pollingRef.current);
    }
    return () => clearInterval(pollingRef.current);
  }, [configs, carregarConfigs]);

  // --------- Mensagens temporárias ---------
  function mostrarMensagem(tipo, texto) {
    setMensagem({ tipo, texto });
    setTimeout(() => setMensagem(null), 5000);
  }

  // --------- Seleção de features ---------
  function toggleFeature(key) {
    setForm((f) => ({
      ...f,
      features: f.features.includes(key)
        ? f.features.filter((k) => k !== key)
        : [...f.features, key],
    }));
  }

  function toggleGrupoCompleto(grupo) {
    const keysDoGrupo = FEATURE_GROUPS.find((g) => g.grupo === grupo)?.features.map((f) => f.key) || [];
    const todosAtivos = keysDoGrupo.every((k) => form.features.includes(k));
    if (todosAtivos) {
      setForm((f) => ({ ...f, features: f.features.filter((k) => !keysDoGrupo.includes(k)) }));
    } else {
      setForm((f) => ({ ...f, features: [...new Set([...f.features, ...keysDoGrupo])] }));
    }
  }

  // --------- Editar configuração existente ---------
  function editarConfig(cfg) {
    setForm({
      id: cfg.id,
      name: cfg.name,
      algorithm: cfg.algorithm,
      features: cfg.features || [],
      target: cfg.target || '1x2',
      notes: cfg.notes || '',
      hyperparameters: cfg.hyperparameters ? JSON.stringify(cfg.hyperparameters, null, 2) : '',
    });
    setFormAberto(true);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  function novaConfigForm() {
    setForm(ESTADO_FORM_INICIAL);
    setFormAberto(true);
  }

  // --------- Salvar configuração ---------
  async function salvarConfig(e) {
    e.preventDefault();
    if (!form.name.trim()) { mostrarMensagem('erro', 'Informe um nome para o modelo.'); return; }
    if (form.features.length === 0) { mostrarMensagem('erro', 'Selecione pelo menos 1 feature.'); return; }

    let hyperparameters = null;
    if (form.hyperparameters.trim()) {
      try { hyperparameters = JSON.parse(form.hyperparameters); }
      catch { mostrarMensagem('erro', 'Hiperparâmetros com JSON inválido.'); return; }
    }

    setSalvando(true);
    try {
      const resp = await fetch(apiUrl('/api/model-maintenance?tarefa=salvar-config-custom'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeader },
        body: JSON.stringify({
          id: form.id || undefined,
          name: form.name.trim(),
          algorithm: form.algorithm,
          features: form.features,
          target: form.target,
          notes: form.notes.trim() || null,
          hyperparameters,
        }),
      });
      const dados = await resp.json();
      if (!resp.ok) throw new Error(dados.error?.message || `HTTP ${resp.status}`);
      mostrarMensagem('ok', `Configuração "${dados.config.name}" salva com sucesso.`);
      setForm(ESTADO_FORM_INICIAL);
      setFormAberto(false);
      await carregarConfigs();
    } catch (e) {
      mostrarMensagem('erro', e.message);
    } finally {
      setSalvando(false);
    }
  }

  // --------- Disparar treino ---------
  async function dispararTreino(configId, configName) {
    if (!session) { mostrarMensagem('erro', 'Faça login para disparar o treino.'); return; }
    setDisparando(configId);
    try {
      const resp = await fetch(
        apiUrl(`/api/model-maintenance?tarefa=disparar-treino-custom&config_id=${configId}`),
        { method: 'POST', headers: authHeader },
      );
      const dados = await resp.json();
      if (!resp.ok) throw new Error(dados.error?.message || `HTTP ${resp.status}`);
      mostrarMensagem('ok', `Treino do modelo "${configName}" disparado! Acompanhe o status abaixo.`);
      await carregarConfigs();
    } catch (e) {
      mostrarMensagem('erro', e.message);
    } finally {
      setDisparando(null);
    }
  }

  // --------- Parar treino (cancela aguardando/treinando → rascunho) ---------
  async function cancelarTreino(configId, configName) {
    if (!session) { mostrarMensagem('erro', 'Faça login para esta ação.'); return; }
    setCancelando(configId);
    try {
      const resp = await fetch(
        apiUrl(`/api/model-maintenance?tarefa=cancelar-treino-custom&config_id=${configId}`),
        { method: 'POST', headers: authHeader },
      );
      const dados = await resp.json();
      if (!resp.ok) throw new Error(dados.error?.message || `HTTP ${resp.status}`);
      mostrarMensagem('ok', `Treino de "${configName}" cancelado.`);
      await carregarConfigs();
    } catch (e) {
      mostrarMensagem('erro', e.message);
    } finally {
      setCancelando(null);
    }
  }

  // --------- Copiar config ---------
  async function copiarConfig(configId, configName) {
    if (!session) { mostrarMensagem('erro', 'Faça login para esta ação.'); return; }
    setCopiando(configId);
    try {
      const resp = await fetch(
        apiUrl(`/api/model-maintenance?tarefa=copiar-config-custom&config_id=${configId}`),
        { method: 'POST', headers: authHeader },
      );
      const dados = await resp.json();
      if (!resp.ok) throw new Error(dados.error?.message || `HTTP ${resp.status}`);
      mostrarMensagem('ok', `"${configName}" copiado como rascunho.`);
      await carregarConfigs();
    } catch (e) {
      mostrarMensagem('erro', e.message);
    } finally {
      setCopiando(null);
    }
  }

  // --------- Resetar config (limpa métricas → rascunho) ---------
  async function resetarConfig(configId, configName) {
    if (!session) { mostrarMensagem('erro', 'Faça login para esta ação.'); return; }
    setResetando(configId);
    try {
      const resp = await fetch(
        apiUrl(`/api/model-maintenance?tarefa=resetar-config-custom&config_id=${configId}`),
        { method: 'POST', headers: authHeader },
      );
      const dados = await resp.json();
      if (!resp.ok) throw new Error(dados.error?.message || `HTTP ${resp.status}`);
      mostrarMensagem('ok', `"${configName}" resetado para rascunho.`);
      await carregarConfigs();
    } catch (e) {
      mostrarMensagem('erro', e.message);
    } finally {
      setResetando(null);
    }
  }

  // --------- Excluir config ---------
  async function excluirConfig(configId, configName) {
    if (!session) { mostrarMensagem('erro', 'Faça login para esta ação.'); return; }
    setExcluindo(configId);
    setConfirmandoExclusao(null);
    try {
      const resp = await fetch(
        apiUrl(`/api/model-maintenance?tarefa=excluir-config-custom&config_id=${configId}`),
        { method: 'POST', headers: authHeader },
      );
      const dados = await resp.json();
      if (!resp.ok) throw new Error(dados.error?.message || `HTTP ${resp.status}`);
      mostrarMensagem('ok', `"${configName}" excluído.`);
      await carregarConfigs();
    } catch (e) {
      mostrarMensagem('erro', e.message);
    } finally {
      setExcluindo(null);
    }
  }

  // --------- Renderização ---------
  function fmtData(iso) {
    if (!iso) return '—';
    return new Date(iso).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  }

  function renderMetricas(metrics) {
    if (!metrics) return null;
    return (
      <div className="mt-2 flex flex-wrap gap-3">
        {Object.entries(metrics).map(([k, v]) => (
          <span key={k} className="bg-slate-700 rounded px-2 py-0.5 text-xs text-slate-300">
            <span className="text-slate-500">{k}: </span>
            {typeof v === 'number' ? v.toFixed(4) : String(v)}
          </span>
        ))}
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto px-4 py-8 space-y-8">
      {/* Cabeçalho */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-2">
            <FlaskConical className="text-violet-400" size={26} />
            Painel de Treino Customizado
          </h1>
          <p className="text-slate-400 text-sm mt-1">
            Crie suas próprias combinações de algoritmo + features e treine novos modelos.
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => carregarConfigs()}
            className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-sm text-slate-400 hover:text-white hover:bg-slate-700 transition-colors"
            title="Atualizar lista"
          >
            <RefreshCw size={14} />
          </button>
          <button
            onClick={novaConfigForm}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-violet-600 hover:bg-violet-500 text-white text-sm font-medium transition-colors"
          >
            <Plus size={16} /> Nova configuração
          </button>
        </div>
      </div>

      {/* Toast de mensagem */}
      {mensagem && (
        <div className={`flex items-center gap-2 px-4 py-3 rounded-lg text-sm font-medium ${
          mensagem.tipo === 'ok'
            ? 'bg-emerald-900/50 border border-emerald-700 text-emerald-300'
            : 'bg-red-900/50 border border-red-700 text-red-300'
        }`}>
          {mensagem.tipo === 'ok' ? <CheckCircle2 size={16} /> : <AlertTriangle size={16} />}
          {mensagem.texto}
        </div>
      )}

      {/* Formulário de nova / editar configuração */}
      {formAberto && (
        <div className="bg-slate-800 border border-slate-700 rounded-xl p-6 space-y-6">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold text-white flex items-center gap-2">
              <Settings2 size={18} className="text-violet-400" />
              {form.id ? 'Editar configuração' : 'Nova configuração'}
            </h2>
            <button onClick={() => setFormAberto(false)} className="text-slate-500 hover:text-white transition-colors text-sm">
              Cancelar
            </button>
          </div>

          <form onSubmit={salvarConfig} className="space-y-5">
            {/* Nome */}
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-1">Nome do modelo *</label>
              <input
                type="text"
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                placeholder="Ex: CatBoost com xG e descanso"
                className="w-full bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-violet-500"
              />
            </div>

            {/* Algoritmo e Target */}
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-slate-300 mb-1">Algoritmo *</label>
                <select
                  value={form.algorithm}
                  onChange={(e) => setForm((f) => ({ ...f, algorithm: e.target.value }))}
                  className="w-full bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:ring-2 focus:ring-violet-500"
                >
                  {ALGORITMOS.map((a) => (
                    <option key={a.value} value={a.value}>{a.label}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-300 mb-1">Mercado alvo *</label>
                <select
                  value={form.target}
                  onChange={(e) => setForm((f) => ({ ...f, target: e.target.value }))}
                  className="w-full bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:ring-2 focus:ring-violet-500"
                >
                  {TARGETS.map((t) => (
                    <option key={t.value} value={t.value}>{t.label}</option>
                  ))}
                </select>
              </div>
            </div>

            {/* Features */}
            <div>
              <div className="flex items-center justify-between mb-2">
                <label className="text-sm font-medium text-slate-300">
                  Features *{' '}
                  <span className="text-slate-500 font-normal">({form.features.length} selecionada{form.features.length !== 1 ? 's' : ''})</span>
                </label>
                <button
                  type="button"
                  onClick={() => setForm((f) => ({ ...f, features: [] }))}
                  className="text-xs text-slate-500 hover:text-slate-300 transition-colors"
                >
                  Limpar tudo
                </button>
              </div>

              <div className="space-y-2 max-h-96 overflow-y-auto pr-1">
                {FEATURE_GROUPS.map((grupo) => {
                  const aberto = grupoExpandido[grupo.grupo] !== false; // aberto por padrão
                  const todosAtivos = grupo.features.every((f) => form.features.includes(f.key));
                  const algumAtivo = grupo.features.some((f) => form.features.includes(f.key));
                  return (
                    <div key={grupo.grupo} className="border border-slate-700 rounded-lg overflow-hidden">
                      <button
                        type="button"
                        onClick={() => setGrupoExpandido((g) => ({ ...g, [grupo.grupo]: !aberto }))}
                        className="w-full flex items-center justify-between px-3 py-2 bg-slate-750 hover:bg-slate-700 transition-colors"
                      >
                        <span className="flex items-center gap-2 text-sm font-medium text-slate-300">
                          <span className={`w-2 h-2 rounded-full ${todosAtivos ? 'bg-violet-400' : algumAtivo ? 'bg-violet-600' : 'bg-slate-600'}`} />
                          {grupo.grupo}
                          <span className="text-slate-500 font-normal text-xs">
                            ({grupo.features.filter((f) => form.features.includes(f.key)).length}/{grupo.features.length})
                          </span>
                        </span>
                        <div className="flex items-center gap-2">
                          <span
                            role="button"
                            tabIndex={0}
                            onClick={(e) => { e.stopPropagation(); toggleGrupoCompleto(grupo.grupo); }}
                            onKeyDown={(e) => { if (e.key === 'Enter') { e.stopPropagation(); toggleGrupoCompleto(grupo.grupo); } }}
                            className="text-xs text-violet-400 hover:text-violet-300 transition-colors px-1"
                          >
                            {todosAtivos ? 'remover todos' : 'selecionar todos'}
                          </span>
                          {aberto ? <ChevronUp size={14} className="text-slate-500" /> : <ChevronDown size={14} className="text-slate-500" />}
                        </div>
                      </button>
                      {aberto && (
                        <div className="px-3 py-2 grid grid-cols-2 gap-1.5 bg-slate-800">
                          {grupo.features.map((f) => {
                            const ativo = form.features.includes(f.key);
                            return (
                              <label
                                key={f.key}
                                className={`flex items-center gap-2 cursor-pointer rounded px-2 py-1 text-xs transition-colors ${
                                  ativo ? 'bg-violet-900/40 text-violet-300' : 'text-slate-400 hover:text-slate-200 hover:bg-slate-700'
                                }`}
                              >
                                <input
                                  type="checkbox"
                                  checked={ativo}
                                  onChange={() => toggleFeature(f.key)}
                                  className="accent-violet-500 w-3 h-3"
                                />
                                {f.label}
                              </label>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Hiperparâmetros (opcional) */}
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-1">
                Hiperparâmetros <span className="text-slate-500 font-normal">(opcional, JSON)</span>
              </label>
              <textarea
                value={form.hyperparameters}
                onChange={(e) => setForm((f) => ({ ...f, hyperparameters: e.target.value }))}
                placeholder={'{\n  "n_estimators": 300,\n  "max_depth": 6\n}'}
                rows={4}
                className="w-full bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm font-mono placeholder-slate-600 focus:outline-none focus:ring-2 focus:ring-violet-500 resize-none"
              />
            </div>

            {/* Notas */}
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-1">
                Notas <span className="text-slate-500 font-normal">(opcional)</span>
              </label>
              <textarea
                value={form.notes}
                onChange={(e) => setForm((f) => ({ ...f, notes: e.target.value }))}
                placeholder="Objetivo, hipóteses, comparações..."
                rows={2}
                className="w-full bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-violet-500 resize-none"
              />
            </div>

            {/* Botão salvar */}
            <div className="flex justify-end gap-3">
              <button
                type="button"
                onClick={() => setFormAberto(false)}
                className="px-4 py-2 rounded-lg text-sm text-slate-400 hover:text-white hover:bg-slate-700 transition-colors"
              >
                Cancelar
              </button>
              <button
                type="submit"
                disabled={salvando}
                className="flex items-center gap-2 px-5 py-2 rounded-lg bg-violet-600 hover:bg-violet-500 disabled:opacity-50 text-white text-sm font-medium transition-colors"
              >
                {salvando ? <Loader2 size={15} className="animate-spin" /> : <Save size={15} />}
                {salvando ? 'Salvando...' : 'Salvar configuração'}
              </button>
            </div>
          </form>
        </div>
      )}

      {/* Lista de configurações */}
      {carregando ? (
        <div className="flex items-center justify-center py-12 text-slate-500 gap-2">
          <Loader2 className="animate-spin" size={20} />
          Carregando configurações...
        </div>
      ) : erro ? (
        <div className="flex items-center gap-2 text-red-400 text-sm py-6">
          <AlertTriangle size={16} /> Erro ao carregar: {erro}
        </div>
      ) : configs.length === 0 ? (
        <div className="text-center py-16 text-slate-500">
          <FlaskConical size={40} className="mx-auto mb-3 opacity-30" />
          <p className="text-sm">Nenhuma configuração criada ainda.</p>
          <button
            onClick={novaConfigForm}
            className="mt-3 text-violet-400 hover:text-violet-300 text-sm transition-colors"
          >
            Criar primeira configuração →
          </button>
        </div>
      ) : (
        <div className="space-y-3">
          <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wide">
            Configurações ({configs.length})
          </h2>
          {configs.map((cfg) => {
            const statusInfo = STATUS_INFO[cfg.status] || STATUS_INFO.rascunho;
            const StatusIcon = statusInfo.icon;
            const aberto = expandidos[cfg.id];
            const emProgresso = cfg.status === 'aguardando_treino' || cfg.status === 'treinando';

            return (
              <div key={cfg.id} className={`border rounded-xl overflow-hidden transition-colors ${statusInfo.bg} border-slate-700`}>
                {/* Cabeçalho da config */}
                <div className="flex items-center gap-3 px-4 py-3">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-semibold text-white text-sm truncate">{cfg.name}</span>
                      <span className={`flex items-center gap-1 text-xs font-medium ${statusInfo.cor}`}>
                        <StatusIcon size={12} className={emProgresso ? 'animate-spin' : ''} />
                        {statusInfo.label}
                      </span>
                    </div>
                    <div className="flex items-center gap-3 mt-0.5 flex-wrap">
                      <span className="text-xs text-slate-500">
                        {ALGORITMOS.find((a) => a.value === cfg.algorithm)?.label || cfg.algorithm}
                      </span>
                      <span className="text-xs text-slate-600">·</span>
                      <span className="text-xs text-slate-500">
                        {TARGETS.find((t) => t.value === cfg.target)?.label || cfg.target}
                      </span>
                      <span className="text-xs text-slate-600">·</span>
                      <span className="text-xs text-slate-500">{cfg.features?.length || 0} features</span>
                      <span className="text-xs text-slate-600">·</span>
                      <span className="text-xs text-slate-600">{fmtData(cfg.created_at)}</span>
                    </div>
                  </div>

                  <div className="flex items-center gap-1 shrink-0">
                    {/* PARAR — apenas quando em progresso */}
                    {emProgresso && session && (
                      <button
                        onClick={() => cancelarTreino(cfg.id, cfg.name)}
                        disabled={cancelando === cfg.id}
                        title="Parar treino (reverte para rascunho)"
                        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-orange-700 hover:bg-orange-600 disabled:opacity-50 text-white text-xs font-medium transition-colors"
                      >
                        {cancelando === cfg.id
                          ? <Loader2 size={13} className="animate-spin" />
                          : <StopCircle size={13} />}
                        Parar
                      </button>
                    )}

                    {/* INICIAR / REINICIAR */}
                    {!emProgresso && session && (
                      <button
                        onClick={() => dispararTreino(cfg.id, cfg.name)}
                        disabled={disparando === cfg.id}
                        title={cfg.status === 'rascunho' ? 'Iniciar treino' : 'Re-treinar'}
                        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-violet-600 hover:bg-violet-500 disabled:opacity-50 text-white text-xs font-medium transition-colors"
                      >
                        {disparando === cfg.id
                          ? <Loader2 size={13} className="animate-spin" />
                          : cfg.status === 'rascunho'
                            ? <Play size={13} />
                            : <RefreshCw size={13} />}
                        {cfg.status === 'rascunho' ? 'Iniciar' : 'Reiniciar'}
                      </button>
                    )}

                    {/* EDITAR */}
                    {!emProgresso && (
                      <button
                        onClick={() => editarConfig(cfg)}
                        title="Editar configuração"
                        className="px-2 py-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-slate-700 transition-colors text-xs"
                      >
                        Editar
                      </button>
                    )}

                    {/* COPIAR */}
                    {!emProgresso && session && (
                      <button
                        onClick={() => copiarConfig(cfg.id, cfg.name)}
                        disabled={copiando === cfg.id}
                        title="Duplicar como rascunho"
                        className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-slate-700 disabled:opacity-40 transition-colors"
                      >
                        {copiando === cfg.id
                          ? <Loader2 size={13} className="animate-spin" />
                          : <Copy size={13} />}
                      </button>
                    )}

                    {/* RESETAR — apaga métricas, volta a rascunho (só para treinado/erro) */}
                    {(cfg.status === 'treinado' || cfg.status === 'erro') && session && (
                      <button
                        onClick={() => resetarConfig(cfg.id, cfg.name)}
                        disabled={resetando === cfg.id}
                        title="Resetar — apaga métricas e volta a rascunho"
                        className="p-1.5 rounded-lg text-slate-400 hover:text-amber-400 hover:bg-slate-700 disabled:opacity-40 transition-colors"
                      >
                        {resetando === cfg.id
                          ? <Loader2 size={13} className="animate-spin" />
                          : <RotateCcw size={13} />}
                      </button>
                    )}

                    {/* EXCLUIR com confirmação inline */}
                    {!emProgresso && session && (
                      confirmandoExclusao === cfg.id ? (
                        <div className="flex items-center gap-1">
                          <button
                            onClick={() => excluirConfig(cfg.id, cfg.name)}
                            disabled={excluindo === cfg.id}
                            className="px-2 py-1 rounded text-xs font-medium bg-red-700 hover:bg-red-600 text-white disabled:opacity-50 transition-colors"
                          >
                            {excluindo === cfg.id ? <Loader2 size={11} className="animate-spin" /> : 'Confirmar?'}
                          </button>
                          <button
                            onClick={() => setConfirmandoExclusao(null)}
                            className="px-1.5 py-1 rounded text-xs text-slate-500 hover:text-white hover:bg-slate-700 transition-colors"
                          >
                            ✕
                          </button>
                        </div>
                      ) : (
                        <button
                          onClick={() => setConfirmandoExclusao(cfg.id)}
                          title="Excluir configuração"
                          className="p-1.5 rounded-lg text-slate-500 hover:text-red-400 hover:bg-slate-700 transition-colors"
                        >
                          <Trash2 size={13} />
                        </button>
                      )
                    )}

                    {/* EXPANDIR */}
                    <button
                      onClick={() => {
                        setConfirmandoExclusao(null);
                        setExpandidos((e) => ({ ...e, [cfg.id]: !aberto }));
                      }}
                      className="p-1.5 rounded-lg text-slate-500 hover:text-white hover:bg-slate-700 transition-colors"
                    >
                      {aberto ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
                    </button>
                  </div>
                </div>

                {/* Detalhe expandido */}
                {aberto && (
                  <div className="border-t border-slate-700 px-4 py-3 space-y-3">
                    {/* Features */}
                    <div>
                      <p className="text-xs text-slate-500 mb-1 font-medium">Features selecionadas</p>
                      <div className="flex flex-wrap gap-1">
                        {(cfg.features || []).map((fk) => {
                          const meta = FEATURE_GROUPS.flatMap((g) => g.features).find((f) => f.key === fk);
                          return (
                            <span key={fk} className="bg-slate-700 text-slate-300 text-xs px-2 py-0.5 rounded">
                              {meta?.label || fk}
                            </span>
                          );
                        })}
                        {(!cfg.features || cfg.features.length === 0) && (
                          <span className="text-slate-600 text-xs">—</span>
                        )}
                      </div>
                    </div>

                    {/* Métricas (se treinado) */}
                    {cfg.metrics && (
                      <div className="bg-slate-800/50 p-3 rounded-lg border border-slate-700">
                        <div className="flex items-center justify-between mb-2">
                          <p className="text-xs text-slate-400 font-medium flex items-center gap-1">
                            <BarChart3 size={14} className="text-violet-400" /> Resultados do Treinamento
                          </p>
                          <button 
                            onClick={() => setConfigModalAberto(cfg)}
                            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-slate-700 hover:bg-slate-600 text-white rounded-lg transition-colors"
                          >
                            <FileText size={13} />
                            Ver Relatório Completo
                          </button>
                        </div>
                        {cfg.trained_at && (
                          <p className="text-xs text-slate-500">Concluído em: {fmtData(cfg.trained_at)}</p>
                        )}
                      </div>
                    )}

                    {/* Erro (se houver) */}
                    {cfg.error_message && (
                      <div className="bg-red-900/20 border border-red-800 rounded-lg px-3 py-2 text-xs text-red-300">
                        <span className="font-medium">Erro: </span>{cfg.error_message}
                      </div>
                    )}

                    {/* Notas */}
                    {cfg.notes && (
                      <div>
                        <p className="text-xs text-slate-500 mb-0.5 font-medium">Notas</p>
                        <p className="text-xs text-slate-400">{cfg.notes}</p>
                      </div>
                    )}

                    {/* Hiperparâmetros */}
                    {cfg.hyperparameters && (
                      <div>
                        <p className="text-xs text-slate-500 mb-1 font-medium">Hiperparâmetros</p>
                        <pre className="text-xs text-slate-400 bg-slate-900 rounded p-2 overflow-x-auto">
                          {JSON.stringify(cfg.hyperparameters, null, 2)}
                        </pre>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Legenda de status */}
      <div className="border-t border-slate-800 pt-4">
        <p className="text-xs text-slate-600 mb-2 font-medium">Ciclo de vida de um modelo customizado:</p>
        <div className="flex flex-wrap gap-3">
          {Object.entries(STATUS_INFO).map(([key, info]) => {
            const Icon = info.icon;
            return (
              <span key={key} className={`flex items-center gap-1 text-xs ${info.cor}`}>
                <Icon size={11} /> {info.label}
              </span>
            );
          })}
        </div>
      </div>

      {/* Modal de Relatório */}
      {configModalAberto && (
        <RelatorioTreinoModal 
          config={configModalAberto} 
          onClose={() => setConfigModalAberto(null)} 
        />
      )}
    </div>
  );
}
