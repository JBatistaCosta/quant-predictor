// src/lib/predicoes.ts
// Helper de consumo da tabela `predicoes` (Model Benchmarking) pro frontend.
// As predições são geradas de forma assíncrona pelo GitHub Actions
// (scripts/rodar_predicoes.py) e só lidas aqui — a Vercel nunca roda ML,
// só faz essa requisição JSON leve contra o Supabase.

import type { PostgrestError } from '@supabase/supabase-js';
import { supabase } from '../supabaseClient';

const TAMANHO_PAGINA = 1000;

export interface Predicao {
  id: number;
  match_id: number;
  model_name: string;
  prob_home: number;
  prob_draw: number;
  prob_away: number;
  edge_detectado: number | null;
  created_at: string;
}

/**
 * Busca todas as predições de um modelo específico ('dixon_coles_v1',
 * 'catboost_v1', 'xgboost_v1' ou 'lightgbm_v1') pra alimentar os filtros
 * do painel de Model Benchmarking.
 */
export async function buscarPredicoesPorModelo(modelName: string): Promise<Predicao[]> {
  if (!supabase) {
    throw new Error(
      'Cliente Supabase não inicializado — configure VITE_SUPABASE_URL e VITE_SUPABASE_KEY.'
    );
  }

  const todasAsLinhas: Predicao[] = [];
  let pagina = 0;

  // Pagina de verdade em blocos de 1000: sem `.range()` explícito, o
  // PostgREST corta silenciosamente em 1000 linhas (sem erro) — mesma
  // classe de bug já corrigida em outras telas deste projeto.
  for (;;) {
    const inicio = pagina * TAMANHO_PAGINA;
    const fim = inicio + TAMANHO_PAGINA - 1;

    const { data, error }: { data: Predicao[] | null; error: PostgrestError | null } = await supabase
      .from('predicoes')
      .select('*')
      .eq('model_name', modelName)
      .order('match_id', { ascending: true })
      .range(inicio, fim);

    if (error) {
      throw new Error(`Erro ao buscar predições do modelo "${modelName}": ${error.message}`);
    }

    const linhas = data ?? [];
    todasAsLinhas.push(...linhas);

    if (linhas.length < TAMANHO_PAGINA) break;
    pagina += 1;
  }

  return todasAsLinhas;
}
