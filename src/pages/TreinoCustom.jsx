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
  FlaskConical, BarChart3, RefreshCw, FileText, Copy, StopCircle, RotateCcw,
  Layers, Bookmark, X as XIcon
} from 'lucide-react';
import { useAuth } from '../AuthContext';
import { apiUrl } from '../utils/apiUrl';
import { supabase, supabaseAtivo } from '../supabaseClient';
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
    grupo: 'Contexto da Partida',
    features: [
      {
        key: 'match_stage_ord',
        label: 'Fase da partida — ordinal (0=liga regular … 7=final/playoff)',
        cov: '▅',
        tip: 'Útil principalmente em modelos que incluam copas (UCL, Copa do Brasil). Em modelos só de liga doméstica, quase todas as partidas são 0.',
      },
      {
        key: 'is_neutral',
        label: 'Palco neutro (0/1)',
        cov: '▃',
        tip: 'Coluna nova — dados históricos ainda não populados. Só terá variância em ingestões futuras.',
      },
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

// -----------------------------------------------------------------------
// Templates recomendados — configurações pré-preenchidas baseadas em
// análise estatística: budget de features, causalidade e hiperparâmetros
// validados para o tamanho de dataset (~8-10k linhas de treino).
// -----------------------------------------------------------------------
const TEMPLATES_RECOMENDADOS = [
  {
    id: 'baseline_1x2',
    label: 'Baseline 1X2',
    desc: 'Elo + forma-gols + tabela · 11 features',
    cor: 'violet',
    config: {
      name: 'Baseline 1X2 — Elo + Forma',
      mode: 'walk_forward_cv',
      algorithm: 'catboost',
      algorithms: ['catboost', 'xgboost', 'lightgbm'],
      target: '1x2',
      features: [
        'elo_home', 'elo_away',
        'media_gols_marcados_5j_home', 'media_gols_sofridos_5j_home',
        'media_gols_marcados_5j_away', 'media_gols_sofridos_5j_away',
        'posicao_home', 'posicao_away',
        'pontos_por_jogo_home', 'pontos_por_jogo_away',
        'progresso_temporada',
      ],
      notes: 'Template estatístico recomendado para 1X2. Budget 11 features (~500 amostras/folha). depth=4 reduz overfitting no dataset atual (~8-10k linhas).',
      hyperparameters: JSON.stringify({
        catboost: { depth: 4, learning_rate: 0.04, l2_leaf_reg: 3 },
        xgboost: { max_depth: 4, learning_rate: 0.05 },
        lightgbm: { num_leaves: 15, learning_rate: 0.05 },
      }, null, 2),
    },
  },
  {
    id: 'xg_premium_ou25',
    label: 'xG Premium O/U 2,5',
    desc: 'xG Bayesiano + xGOT + H2H · 9 features',
    cor: 'teal',
    config: {
      name: 'xG Premium — Over/Under 2,5',
      mode: 'walk_forward_cv',
      algorithm: 'lightgbm',
      algorithms: ['lightgbm', 'xgboost'],
      target: 'over_under_2.5',
      features: [
        'xg_bayesiano_home', 'xg_bayesiano_away',
        'xga_bayesiano_home', 'xga_bayesiano_away',
        'xgot_bayesiano_home', 'xgot_bayesiano_away',
        'media_gols_marcados_5j_home', 'media_gols_marcados_5j_away',
        'h2h_media_gols',
      ],
      notes: 'Template xG para O/U 2.5. Cobertura ▅ — funciona melhor nas 5 ligas europeias com dados Understat. Para Brasileirão, substituir xG por forma-gols.',
      hyperparameters: JSON.stringify({
        lightgbm: { num_leaves: 15, learning_rate: 0.05 },
        xgboost: { max_depth: 4, learning_rate: 0.05 },
      }, null, 2),
    },
  },
  {
    id: 'btts_xg',
    label: 'BTTS — Ambas Marcam',
    desc: 'xG ataque × xGA defesa · 8 features',
    cor: 'amber',
    config: {
      name: 'BTTS xG — Ambas Marcam',
      mode: 'walk_forward_cv',
      algorithm: 'lightgbm',
      algorithms: ['lightgbm', 'xgboost'],
      target: 'btts',
      features: [
        'xg_bayesiano_home', 'xg_bayesiano_away',
        'xga_bayesiano_home', 'xga_bayesiano_away',
        'media_gols_marcados_5j_home', 'media_gols_sofridos_5j_home',
        'media_gols_marcados_5j_away', 'media_gols_sofridos_5j_away',
      ],
      notes: 'Template BTTS. Mecanismo: xG de ataque de cada time cruzado com xGA do adversário. Forma-gols cobre o mesmo sinal quando xG não tem cobertura.',
      hyperparameters: JSON.stringify({
        lightgbm: { num_leaves: 15, learning_rate: 0.05 },
        xgboost: { max_depth: 4, learning_rate: 0.05 },
      }, null, 2),
    },
  },
  {
    id: 'escanteios_fotmob',
    label: 'Escanteios O/U 9,5',
    desc: 'FBref escanteios + chutes bloqueados FotMob · 10 features',
    cor: 'orange',
    config: {
      name: 'Escanteios — FBref + FotMob',
      mode: 'walk_forward_cv',
      algorithm: 'lightgbm',
      algorithms: ['lightgbm', 'xgboost'],
      target: 'corners_over_under_9.5',
      features: [
        'media_escanteios_5j_home', 'media_escanteios_sofridos_5j_home',
        'media_escanteios_5j_away', 'media_escanteios_sofridos_5j_away',
        'media_chutes_bloqueados_fm_5j_home', 'media_chutes_bloqueados_fm_sofrido_5j_home',
        'media_chutes_bloqueados_fm_5j_away', 'media_chutes_bloqueados_fm_sofrido_5j_away',
        'media_chutes_area_fm_5j_home', 'media_chutes_area_fm_5j_away',
      ],
      notes: 'Template escanteios. Chutes bloqueados é o sinal mais direto (bola desviada → linha de fundo → escanteio). Cobertura ▅ FotMob.',
      hyperparameters: JSON.stringify({
        lightgbm: { num_leaves: 15, learning_rate: 0.05 },
        xgboost: { max_depth: 4, learning_rate: 0.05 },
      }, null, 2),
    },
  },
];

const TEMPLATE_CORES = {
  violet: 'border-violet-700 bg-violet-900/20 text-violet-300 hover:bg-violet-900/40',
  teal:   'border-teal-700 bg-teal-900/20 text-teal-300 hover:bg-teal-900/40',
  amber:  'border-amber-700 bg-amber-900/20 text-amber-300 hover:bg-amber-900/40',
  orange: 'border-orange-700 bg-orange-900/20 text-orange-300 hover:bg-orange-900/40',
};

const ALGORITMOS = [
  { value: 'catboost', label: 'CatBoost' },
  { value: 'xgboost', label: 'XGBoost' },
  { value: 'lightgbm', label: 'LightGBM' },
  { value: 'logistic_regression', label: 'Regressão Logística' },
  { value: 'random_forest', label: 'Random Forest' },
  { value: 'dixon_coles', label: 'Dixon-Coles' },
];

// Algoritmos disponíveis para o modo Walk-Forward CV
const ALGORITMOS_WF = [
  { value: 'catboost', label: 'CatBoost' },
  { value: 'xgboost', label: 'XGBoost' },
  { value: 'lightgbm', label: 'LightGBM' },
  { value: 'logistic_regression', label: 'Regressão Logística' },
  { value: 'random_forest', label: 'Random Forest' },
];

const TARGETS = [
  { value: '1x2', label: 'Resultado 1X2' },
  { value: 'over_under_2.5', label: 'Over/Under 2,5 gols' },
  { value: 'btts', label: 'Ambas marcam (BTTS)' },
  { value: 'faixa_gols', label: 'Faixa de total de gols (0-1 / 2-3 / 4-6 / 7+)' },
  { value: 'corners_over_under_9.5', label: 'Escanteios O/U 9,5' },
  { value: 'faixa_corners', label: 'Faixa de total de escanteios (≤8 / 9-10 / 11-12 / 13+)' },
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
  mode: 'simples',
  algorithm: 'catboost',
  algorithms: ['catboost', 'xgboost', 'lightgbm'],
  features: [],
  target: '1x2',
  notes: '',
  hyperparameters: '',
  todas_ligas: false,
  league_ids: null,
  seasons: null,
  stacking_groups: [],
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
  const [sidebarAberta, setSidebarAberta] = useState(false);
  const [templatesCustoms, setTemplatesCustoms] = useState(() => {
    try { return JSON.parse(localStorage.getItem('qp_templates_custom') || '[]'); }
    catch { return []; }
  });
  const [ligasDisponiveis, setLigasDisponiveis] = useState([]); // [{id, name}]
  const [temporadaRange, setTemporadaRange] = useState({ min: null, max: null }); // limites globais pro seletor de temporadas
  const [escopoLigasManual, setEscopoLigasManual] = useState(false);
  const [escopoTemporadasManual, setEscopoTemporadasManual] = useState(false);
  const [novoGrupoNome, setNovoGrupoNome] = useState('');
  const [novoGrupoAlgos, setNovoGrupoAlgos] = useState([]);
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

  // Carrega catálogo de ligas + intervalo global de temporadas disponíveis,
  // pra alimentar os seletores manuais de escopo do treino. Consulta direta
  // via cliente Supabase (leitura pública, mesmo padrão de RatingClubes.jsx/
  // ModelosStats.jsx) -- evita criar mais um endpoint em model-maintenance.js
  // (já no limite de 12 funções serverless do plano Hobby). Min/max de season
  // usam 2 queries de 1 linha cada (order + limit(1)) em vez de paginar toda
  // `matches` pra computar distinct no cliente.
  useEffect(() => {
    if (!supabaseAtivo) return;
    (async () => {
      const [ligasResp, minResp, maxResp] = await Promise.all([
        supabase.from('leagues').select('id, name').order('name'),
        supabase.from('matches').select('season').eq('status', 'finished').order('season', { ascending: true }).limit(1),
        supabase.from('matches').select('season').eq('status', 'finished').order('season', { ascending: false }).limit(1),
      ]);
      setLigasDisponiveis(ligasResp.data || []);
      setTemporadaRange({
        min: minResp.data?.[0]?.season || null,
        max: maxResp.data?.[0]?.season || null,
      });
    })();
  }, []);

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

  // Persiste templates do usuário no localStorage
  useEffect(() => {
    localStorage.setItem('qp_templates_custom', JSON.stringify(templatesCustoms));
  }, [templatesCustoms]);

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
      mode: cfg.mode || 'simples',
      algorithm: cfg.algorithm || 'catboost',
      algorithms: cfg.algorithms?.length ? cfg.algorithms : ['catboost', 'xgboost', 'lightgbm'],
      features: cfg.features || [],
      target: cfg.target || '1x2',
      notes: cfg.notes || '',
      hyperparameters: cfg.hyperparameters ? JSON.stringify(cfg.hyperparameters, null, 2) : '',
      todas_ligas: !!cfg.todas_ligas,
      league_ids: cfg.league_ids || null,
      seasons: cfg.seasons || null,
      stacking_groups: cfg.stacking_groups || [],
    });
    setEscopoLigasManual(!!(cfg.league_ids && cfg.league_ids.length));
    setEscopoTemporadasManual(!!(cfg.seasons && cfg.seasons.length));
    setNovoGrupoNome('');
    setNovoGrupoAlgos([]);
    setFormAberto(true);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  function novaConfigForm() {
    setForm(ESTADO_FORM_INICIAL);
    setEscopoLigasManual(false);
    setEscopoTemporadasManual(false);
    setNovoGrupoNome('');
    setNovoGrupoAlgos([]);
    setFormAberto(true);
  }

  function carregarTemplate(tpl) {
    setForm({
      id: null,
      name: tpl.config.name,
      mode: tpl.config.mode,
      algorithm: tpl.config.algorithm,
      algorithms: tpl.config.algorithms,
      features: tpl.config.features,
      target: tpl.config.target,
      notes: tpl.config.notes,
      hyperparameters: tpl.config.hyperparameters,
      todas_ligas: !!tpl.config.todas_ligas,
      league_ids: tpl.config.league_ids || null,
      seasons: tpl.config.seasons || null,
      stacking_groups: tpl.config.stacking_groups || [],
    });
    setEscopoLigasManual(!!(tpl.config.league_ids && tpl.config.league_ids.length));
    setEscopoTemporadasManual(!!(tpl.config.seasons && tpl.config.seasons.length));
    setNovoGrupoNome('');
    setNovoGrupoAlgos([]);
    setSidebarAberta(false);
    setFormAberto(true);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  function salvarComoTemplate() {
    if (!form.name.trim() || form.features.length === 0) {
      mostrarMensagem('erro', 'Preencha pelo menos nome e features antes de salvar como template.');
      return;
    }
    const algoDesc = form.mode === 'walk_forward_cv'
      ? form.algorithms.join('+') + ' WF-CV'
      : form.algorithm + ' Simples';
    const tplTarget = TARGETS.find((t) => t.value === form.target)?.label || form.target;
    const novo = {
      id: `custom_${Date.now()}`,
      label: form.name,
      desc: `${form.features.length} features · ${algoDesc} · ${tplTarget}`,
      cor: 'slate',
      custom: true,
      config: {
        name: form.name,
        mode: form.mode,
        algorithm: form.algorithm,
        algorithms: form.algorithms,
        features: form.features,
        target: form.target,
        notes: form.notes,
        hyperparameters: form.hyperparameters,
      },
    };
    setTemplatesCustoms((prev) => [novo, ...prev]);
    mostrarMensagem('ok', `Template "${form.name}" salvo.`);
  }

  function excluirTemplateCustom(id) {
    setTemplatesCustoms((prev) => prev.filter((t) => t.id !== id));
  }

  // --------- Salvar configuração ---------
  async function salvarConfig(e) {
    e.preventDefault();
    if (!form.name.trim()) { mostrarMensagem('erro', 'Informe um nome para o modelo.'); return; }
    if (form.features.length === 0) { mostrarMensagem('erro', 'Selecione pelo menos 1 feature.'); return; }
    if (form.mode === 'walk_forward_cv' && form.algorithms.length === 0) {
      mostrarMensagem('erro', 'Selecione pelo menos 1 algoritmo para o modo Walk-Forward CV.'); return;
    }
    if (escopoLigasManual && (!form.league_ids || form.league_ids.length === 0)) {
      mostrarMensagem('erro', 'Selecione pelo menos 1 liga no escopo manual, ou desmarque a opção.'); return;
    }
    if (escopoTemporadasManual && (!form.seasons || form.seasons.length === 0)) {
      mostrarMensagem('erro', 'Defina o intervalo de temporadas no escopo manual, ou desmarque a opção.'); return;
    }

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
          mode: form.mode,
          algorithm: form.mode === 'simples' ? form.algorithm : null,
          algorithms: form.mode === 'walk_forward_cv' ? form.algorithms : [],
          features: form.features,
          target: form.target,
          notes: form.notes.trim() || null,
          hyperparameters,
          todas_ligas: form.todas_ligas,
          league_ids: escopoLigasManual ? form.league_ids : null,
          seasons: escopoTemporadasManual ? form.seasons : null,
          stacking_groups: form.mode === 'walk_forward_cv' ? form.stacking_groups : [],
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
            onClick={() => setSidebarAberta(true)}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-slate-700 hover:bg-slate-600 text-slate-300 hover:text-white text-sm font-medium transition-colors"
          >
            <Layers size={16} /> Modelos pré-configurados
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

            {/* Modo de treinamento */}
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-2">Modo de treinamento *</label>
              <div className="flex gap-2">
                {[
                  { value: 'simples', label: 'Simples', desc: 'Split único (treino ≤ 2023 / teste ≥ 2024), 1 algoritmo' },
                  { value: 'walk_forward_cv', label: 'Walk-Forward CV', desc: '3 folds temporais, múltiplos algoritmos' },
                ].map((m) => (
                  <button
                    key={m.value}
                    type="button"
                    onClick={() => setForm((f) => ({ ...f, mode: m.value }))}
                    className={`flex-1 rounded-lg px-4 py-2.5 text-sm font-medium border transition-colors text-left ${
                      form.mode === m.value
                        ? 'bg-violet-600 border-violet-500 text-white'
                        : 'bg-slate-700 border-slate-600 text-slate-300 hover:border-slate-500'
                    }`}
                  >
                    <div>{m.label}</div>
                    <div className={`text-xs mt-0.5 font-normal ${form.mode === m.value ? 'text-violet-200' : 'text-slate-500'}`}>{m.desc}</div>
                  </button>
                ))}
              </div>
            </div>

            {/* Algoritmo (simples) ou Algoritmos (WF) + Target */}
            <div className="grid grid-cols-2 gap-4">
              <div>
                {form.mode === 'simples' ? (
                  <>
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
                  </>
                ) : (
                  <>
                    <label className="block text-sm font-medium text-slate-300 mb-1">
                      Algoritmos *{' '}
                      <span className="text-slate-500 font-normal">({form.algorithms.length} selecionado{form.algorithms.length !== 1 ? 's' : ''})</span>
                    </label>
                    <div className="space-y-1.5">
                      {ALGORITMOS_WF.map((a) => {
                        const ativo = form.algorithms.includes(a.value);
                        return (
                          <label
                            key={a.value}
                            className={`flex items-center gap-2 cursor-pointer rounded px-2.5 py-1.5 text-sm transition-colors ${
                              ativo ? 'bg-violet-900/40 text-violet-300' : 'text-slate-400 hover:text-slate-200 hover:bg-slate-700'
                            }`}
                          >
                            <input
                              type="checkbox"
                              checked={ativo}
                              onChange={() =>
                                setForm((f) => {
                                  const algorithms = ativo
                                    ? f.algorithms.filter((k) => k !== a.value)
                                    : [...f.algorithms, a.value];
                                  // Remove o algoritmo desmarcado de qualquer grupo de
                                  // stacking, e descarta grupos que ficarem com <2.
                                  const stacking_groups = f.stacking_groups
                                    .map((g) => ({ ...g, algorithms: g.algorithms.filter((k) => algorithms.includes(k)) }))
                                    .filter((g) => g.algorithms.length >= 2);
                                  return { ...f, algorithms, stacking_groups };
                                })
                              }
                              className="accent-violet-500 w-3.5 h-3.5"
                            />
                            {a.label}
                          </label>
                        );
                      })}
                    </div>
                  </>
                )}
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

            {/* Stacking (só faz sentido no modo Walk-Forward CV, com >=2 algoritmos escolhidos) */}
            {form.mode === 'walk_forward_cv' && (
              <div className="border border-slate-700 rounded-lg overflow-hidden">
                <div className="px-3 py-2.5 bg-slate-750">
                  <span className="text-sm font-medium text-slate-300">
                    Stacking (opcional){' '}
                    <span className="text-slate-500 font-normal">
                      {form.stacking_groups.length > 0
                        ? `(${form.stacking_groups.length} grupo${form.stacking_groups.length !== 1 ? 's' : ''})`
                        : ''}
                    </span>
                  </span>
                  <p className="text-xs text-slate-500 mt-0.5">
                    Combine as previsões de 2+ algoritmos já selecionados acima num meta-modelo (regressão logística sobre as probabilidades), treinado e avaliado por fold junto com os algoritmos base. Ex.: Stacking 1 = CatBoost + XGBoost, Stacking 2 = XGBoost + LightGBM + Regressão Logística + Random Forest.
                  </p>
                </div>

                {form.stacking_groups.length > 0 && (
                  <div className="px-3 py-2 border-t border-slate-700 bg-slate-800 space-y-1.5">
                    {form.stacking_groups.map((grupo, idx) => (
                      <div key={idx} className="flex items-center justify-between gap-2 rounded bg-slate-700/50 px-2.5 py-1.5">
                        <div className="text-xs">
                          <span className="text-violet-300 font-medium">★ {grupo.name}</span>
                          <span className="text-slate-500"> — {grupo.algorithms.join(' + ')}</span>
                        </div>
                        <button
                          type="button"
                          onClick={() => setForm((f) => ({ ...f, stacking_groups: f.stacking_groups.filter((_, i) => i !== idx) }))}
                          className="text-slate-500 hover:text-red-400 transition-colors shrink-0"
                          title="Remover grupo"
                        >
                          <XIcon size={14} />
                        </button>
                      </div>
                    ))}
                  </div>
                )}

                {form.algorithms.length < 2 ? (
                  <p className="px-3 py-2.5 border-t border-slate-700 text-xs text-slate-500">
                    Selecione ao menos 2 algoritmos acima para poder criar um grupo de stacking.
                  </p>
                ) : (
                  <div className="px-3 py-2.5 border-t border-slate-700 bg-slate-800 space-y-2">
                    <input
                      type="text"
                      value={novoGrupoNome}
                      onChange={(e) => setNovoGrupoNome(e.target.value)}
                      placeholder={`Nome do grupo (ex.: Stacking ${form.stacking_groups.length + 1})`}
                      className="w-full bg-slate-700 border border-slate-600 rounded px-2.5 py-1.5 text-white text-xs focus:outline-none focus:ring-2 focus:ring-violet-500"
                    />
                    <div className="flex flex-wrap gap-1.5">
                      {ALGORITMOS_WF.filter((a) => form.algorithms.includes(a.value)).map((a) => {
                        const ativo = novoGrupoAlgos.includes(a.value);
                        return (
                          <label
                            key={a.value}
                            className={`flex items-center gap-1.5 cursor-pointer rounded px-2 py-1 text-xs transition-colors border ${
                              ativo ? 'bg-violet-900/40 text-violet-300 border-violet-700' : 'text-slate-400 border-slate-600 hover:text-slate-200'
                            }`}
                          >
                            <input
                              type="checkbox"
                              checked={ativo}
                              onChange={() =>
                                setNovoGrupoAlgos((atuais) =>
                                  ativo ? atuais.filter((k) => k !== a.value) : [...atuais, a.value]
                                )
                              }
                              className="accent-violet-500 w-3 h-3"
                            />
                            {a.label}
                          </label>
                        );
                      })}
                    </div>
                    <button
                      type="button"
                      disabled={!novoGrupoNome.trim() || novoGrupoAlgos.length < 2}
                      onClick={() => {
                        setForm((f) => ({
                          ...f,
                          stacking_groups: [...f.stacking_groups, { name: novoGrupoNome.trim(), algorithms: novoGrupoAlgos }],
                        }));
                        setNovoGrupoNome('');
                        setNovoGrupoAlgos([]);
                      }}
                      className="text-xs bg-violet-600 hover:bg-violet-500 disabled:bg-slate-700 disabled:text-slate-500 disabled:cursor-not-allowed text-white rounded px-3 py-1.5 font-medium transition-colors"
                    >
                      Adicionar grupo de stacking
                    </button>
                  </div>
                )}
              </div>
            )}

            <div>
              <label
                className="flex items-start gap-2 cursor-pointer rounded-lg border border-slate-700 px-3 py-2.5 hover:bg-slate-700/50 transition-colors"
                title="Por padrão o treino usa só as 6 ligas do Model Benchmarking (5 europeias + Brasileirão), últimas temporadas. Marcando esta opção, entram TODAS as ligas e copas cadastradas (MLS, Championship, Primeira Liga, Eredivisie, Libertadores, Copa do Brasil, Champions League, Copa do Mundo, Eurocopa, Copa América), com histórico completo (sem corte de temporadas recentes)."
              >
                <input
                  type="checkbox"
                  checked={form.todas_ligas}
                  onChange={() => setForm((f) => ({ ...f, todas_ligas: !f.todas_ligas }))}
                  className="accent-violet-500 w-3.5 h-3.5 mt-0.5"
                />
                <span className="text-sm text-slate-300">
                  Incluir todas as ligas e copas do banco
                  <span className="block text-xs text-slate-500 font-normal mt-0.5">
                    Em vez das 6 ligas do Model Benchmarking, usa todo o histórico de todas as competições cadastradas (domésticas menores, copas nacionais/continentais e torneios internacionais).
                  </span>
                </span>
              </label>
            </div>

            {/* Escopo manual: ligas específicas */}
            <div className="border border-slate-700 rounded-lg overflow-hidden">
              <label className="flex items-start gap-2 cursor-pointer px-3 py-2.5 hover:bg-slate-700/50 transition-colors">
                <input
                  type="checkbox"
                  checked={escopoLigasManual}
                  onChange={() => {
                    const ativar = !escopoLigasManual;
                    setEscopoLigasManual(ativar);
                    if (!ativar) setForm((f) => ({ ...f, league_ids: null }));
                  }}
                  className="accent-violet-500 w-3.5 h-3.5 mt-0.5"
                />
                <span className="text-sm text-slate-300">
                  Selecionar ligas específicas
                  <span className="block text-xs text-slate-500 font-normal mt-0.5">
                    Escolha exatamente quais ligas/copas entram no treino. Tem prioridade sobre "todas as ligas" acima.
                    {form.league_ids?.length ? ` ${form.league_ids.length} liga(s) selecionada(s).` : ''}
                  </span>
                </span>
              </label>
              {escopoLigasManual && (
                <div className="px-3 py-2.5 border-t border-slate-700 bg-slate-800">
                  {ligasDisponiveis.length === 0 ? (
                    <p className="text-xs text-slate-500">Carregando ligas...</p>
                  ) : (
                    <div className="grid grid-cols-2 sm:grid-cols-3 gap-1.5 max-h-48 overflow-y-auto pr-1">
                      {ligasDisponiveis.map((liga) => {
                        const ativo = (form.league_ids || []).includes(liga.id);
                        return (
                          <label
                            key={liga.id}
                            className={`flex items-center gap-1.5 cursor-pointer rounded px-2 py-1 text-xs transition-colors ${
                              ativo ? 'bg-violet-900/40 text-violet-300' : 'text-slate-400 hover:text-slate-200 hover:bg-slate-700'
                            }`}
                          >
                            <input
                              type="checkbox"
                              checked={ativo}
                              onChange={() => {
                                const atuais = form.league_ids || [];
                                const novos = ativo ? atuais.filter((id) => id !== liga.id) : [...atuais, liga.id];
                                setForm((f) => ({ ...f, league_ids: novos }));
                              }}
                              className="accent-violet-500 w-3 h-3 shrink-0"
                            />
                            <span className="truncate">{liga.name}</span>
                          </label>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Escopo manual: intervalo de temporadas */}
            <div className="border border-slate-700 rounded-lg overflow-hidden">
              <label className="flex items-start gap-2 cursor-pointer px-3 py-2.5 hover:bg-slate-700/50 transition-colors">
                <input
                  type="checkbox"
                  checked={escopoTemporadasManual}
                  onChange={() => {
                    const ativar = !escopoTemporadasManual;
                    setEscopoTemporadasManual(ativar);
                    if (!ativar) setForm((f) => ({ ...f, seasons: null }));
                  }}
                  className="accent-violet-500 w-3.5 h-3.5 mt-0.5"
                />
                <span className="text-sm text-slate-300">
                  Selecionar intervalo de temporadas
                  <span className="block text-xs text-slate-500 font-normal mt-0.5">
                    Por padrão, cada liga usa suas temporadas mais recentes (ou histórico completo, se "todas as ligas" estiver marcado). Aqui você fixa o intervalo exato de anos.
                    {form.seasons?.length ? ` ${form.seasons[0]}–${form.seasons[form.seasons.length - 1]}.` : ''}
                  </span>
                </span>
              </label>
              {escopoTemporadasManual && (
                <div className="px-3 py-2.5 border-t border-slate-700 bg-slate-800 flex items-center gap-3">
                  {temporadaRange.min && temporadaRange.max ? (
                    <>
                      <div className="flex items-center gap-1.5">
                        <label className="text-xs text-slate-400">De</label>
                        <select
                          value={form.seasons?.[0] || temporadaRange.min}
                          onChange={(e) => {
                            const de = e.target.value;
                            const ate = form.seasons?.[form.seasons.length - 1] || temporadaRange.max;
                            const ateFinal = ate < de ? de : ate;
                            const anos = [];
                            for (let a = Number(de); a <= Number(ateFinal); a++) anos.push(String(a));
                            setForm((f) => ({ ...f, seasons: anos }));
                          }}
                          className="bg-slate-700 border border-slate-600 rounded px-2 py-1 text-white text-xs focus:outline-none focus:ring-2 focus:ring-violet-500"
                        >
                          {Array.from(
                            { length: Number(temporadaRange.max) - Number(temporadaRange.min) + 1 },
                            (_, i) => String(Number(temporadaRange.min) + i)
                          ).map((ano) => <option key={ano} value={ano}>{ano}</option>)}
                        </select>
                      </div>
                      <div className="flex items-center gap-1.5">
                        <label className="text-xs text-slate-400">Até</label>
                        <select
                          value={form.seasons?.[form.seasons.length - 1] || temporadaRange.max}
                          onChange={(e) => {
                            const ate = e.target.value;
                            const de = form.seasons?.[0] || temporadaRange.min;
                            const deFinal = de > ate ? ate : de;
                            const anos = [];
                            for (let a = Number(deFinal); a <= Number(ate); a++) anos.push(String(a));
                            setForm((f) => ({ ...f, seasons: anos }));
                          }}
                          className="bg-slate-700 border border-slate-600 rounded px-2 py-1 text-white text-xs focus:outline-none focus:ring-2 focus:ring-violet-500"
                        >
                          {Array.from(
                            { length: Number(temporadaRange.max) - Number(temporadaRange.min) + 1 },
                            (_, i) => String(Number(temporadaRange.min) + i)
                          ).map((ano) => <option key={ano} value={ano}>{ano}</option>)}
                        </select>
                      </div>
                    </>
                  ) : (
                    <p className="text-xs text-slate-500">Carregando temporadas disponíveis...</p>
                  )}
                </div>
              )}
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
                                title={f.tip || undefined}
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
                                <span className="flex-1">{f.label}</span>
                                {f.cov && <span className="shrink-0 opacity-50" title={`Cobertura: ${f.cov === '█' ? 'Alta (>80%)' : f.cov === '▅' ? 'Média (40-80%)' : 'Baixa (<40%)'}`}>{f.cov}</span>}
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
                      {cfg.mode === 'walk_forward_cv' ? (
                        <span className="text-xs bg-violet-900/40 text-violet-400 border border-violet-800 rounded px-1.5 py-0.5 font-medium">WF-CV</span>
                      ) : null}
                      {cfg.todas_ligas && !cfg.league_ids?.length ? (
                        <span
                          className="text-xs bg-sky-900/40 text-sky-400 border border-sky-800 rounded px-1.5 py-0.5 font-medium"
                          title="Treinado com todas as ligas e copas do banco, histórico completo"
                        >
                          Todas as ligas
                        </span>
                      ) : null}
                      {cfg.league_ids?.length ? (
                        <span
                          className="text-xs bg-teal-900/40 text-teal-400 border border-teal-800 rounded px-1.5 py-0.5 font-medium"
                          title="Ligas selecionadas manualmente"
                        >
                          {cfg.league_ids.length} liga{cfg.league_ids.length !== 1 ? 's' : ''}
                        </span>
                      ) : null}
                      {cfg.seasons?.length ? (
                        <span
                          className="text-xs bg-amber-900/40 text-amber-400 border border-amber-800 rounded px-1.5 py-0.5 font-medium"
                          title="Intervalo de temporadas selecionado manualmente"
                        >
                          {cfg.seasons[0]}–{cfg.seasons[cfg.seasons.length - 1]}
                        </span>
                      ) : null}
                      {cfg.stacking_groups?.length ? (
                        <span
                          className="text-xs bg-violet-900/40 text-violet-300 border border-violet-700 rounded px-1.5 py-0.5 font-medium"
                          title={cfg.stacking_groups.map((g) => `${g.name}: ${g.algorithms.join(' + ')}`).join(' · ')}
                        >
                          ★ {cfg.stacking_groups.length} grupo{cfg.stacking_groups.length !== 1 ? 's' : ''} stacking
                        </span>
                      ) : null}
                      {cfg.mode === 'walk_forward_cv' ? (
                        <span className="text-xs text-slate-500">
                          {(cfg.algorithms || []).join(', ') || '—'}
                        </span>
                      ) : (
                        <span className="text-xs text-slate-500">
                          {ALGORITMOS.find((a) => a.value === cfg.algorithm)?.label || cfg.algorithm}
                        </span>
                      )}
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

      {/* Drawer lateral — Modelos pré-configurados */}
      {sidebarAberta && (
        <div className="fixed inset-0 z-50 flex justify-end">
          {/* Overlay */}
          <div
            className="flex-1 bg-black/50"
            onClick={() => setSidebarAberta(false)}
          />
          {/* Painel */}
          <div className="w-96 h-full bg-slate-900 border-l border-slate-700 flex flex-col shadow-2xl">
            {/* Header */}
            <div className="flex items-center justify-between px-5 py-4 border-b border-slate-700 shrink-0">
              <h3 className="font-semibold text-white flex items-center gap-2">
                <Layers size={16} className="text-violet-400" />
                Modelos pré-configurados
              </h3>
              <button
                onClick={() => setSidebarAberta(false)}
                className="text-slate-500 hover:text-white transition-colors"
              >
                <XIcon size={18} />
              </button>
            </div>

            {/* Conteúdo */}
            <div className="flex-1 overflow-y-auto p-4 space-y-5">
              {/* Templates fixos */}
              <div>
                <p className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-2">
                  Recomendados pela análise estatística
                </p>
                <div className="space-y-2">
                  {TEMPLATES_RECOMENDADOS.map((tpl) => (
                    <div
                      key={tpl.id}
                      className={`rounded-lg border px-3 py-3 ${TEMPLATE_CORES[tpl.cor]}`}
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <p className="text-sm font-medium leading-snug">{tpl.label}</p>
                          <p className="text-xs mt-0.5 opacity-70">{tpl.desc}</p>
                        </div>
                        <button
                          onClick={() => carregarTemplate(tpl)}
                          className="shrink-0 px-3 py-1 text-xs rounded-md bg-white/10 hover:bg-white/20 transition-colors font-medium"
                        >
                          Usar
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* Templates do usuário */}
              <div>
                <p className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-2 flex items-center gap-1">
                  <Bookmark size={10} /> Meus templates
                  {templatesCustoms.length > 0 && (
                    <span className="ml-1 text-slate-600">({templatesCustoms.length})</span>
                  )}
                </p>
                {templatesCustoms.length === 0 ? (
                  <p className="text-xs text-slate-600 italic">
                    Nenhum template salvo ainda. Configure um modelo e clique em "Salvar como template" abaixo.
                  </p>
                ) : (
                  <div className="space-y-2">
                    {templatesCustoms.map((tpl) => (
                      <div
                        key={tpl.id}
                        className="rounded-lg border border-slate-700 bg-slate-800/50 px-3 py-3"
                      >
                        <div className="flex items-start justify-between gap-2">
                          <div className="min-w-0">
                            <p className="text-sm font-medium text-slate-200 leading-snug truncate">{tpl.label}</p>
                            <p className="text-xs text-slate-500 mt-0.5">{tpl.desc}</p>
                          </div>
                          <div className="flex gap-1 shrink-0">
                            <button
                              onClick={() => carregarTemplate(tpl)}
                              className="px-3 py-1 text-xs rounded-md bg-violet-600 hover:bg-violet-500 text-white transition-colors"
                            >
                              Usar
                            </button>
                            <button
                              onClick={() => excluirTemplateCustom(tpl.id)}
                              className="px-2 py-1 text-xs rounded-md text-red-400 hover:bg-red-900/30 transition-colors"
                              title="Remover template"
                            >
                              <XIcon size={12} />
                            </button>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>

            {/* Rodapé — salvar configuração atual */}
            <div className="border-t border-slate-700 p-4 shrink-0">
              <button
                onClick={salvarComoTemplate}
                disabled={!formAberto || !form.name.trim() || form.features.length === 0}
                className="w-full py-2.5 text-sm rounded-lg border border-slate-600 text-slate-300 hover:bg-slate-800 hover:text-white transition-colors disabled:opacity-30 disabled:cursor-not-allowed flex items-center justify-center gap-2"
              >
                <Bookmark size={14} />
                Salvar configuração atual como template
              </button>
              {(!formAberto || !form.name.trim() || form.features.length === 0) && (
                <p className="text-xs text-slate-600 text-center mt-1.5">
                  Preencha nome e features no formulário para ativar
                </p>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
