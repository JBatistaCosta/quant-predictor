// api/modelos-disponiveis.js
// Roda no SERVIDOR do Vercel. Lista todos os model_name "brutos" (não
// calibrados -- variantes `_calibrado_platt`/`_calibrado_isotonic` do
// pipeline "Model Benchmarking" ficam de fora daqui: a correção delas se
// aplica via `usar_calibracao` em api/simulacao-carteira.js, não trocando
// de modelo) com pelo menos 1 partida finalizada resolvível (tem odd de
// fechamento real pra simular), junto da profundidade real de histórico
// de cada um -- usado pra popular os filtros de
// src/pages/SimulacaoCarteira.jsx sem baixar milhares de linhas no
// navegador.

import { createClient } from '@supabase/supabase-js';

function getSupabase() {
  return createClient(process.env.SUPABASE_URL, process.env.SUPABASE_KEY);
}

async function buscarTudoPaginado(criarQuery) {
  const TAMANHO_PAGINA = 1000;
  const resultado = [];
  let pagina = 0;
  while (true) {
    const { data, error } = await criarQuery().range(pagina * TAMANHO_PAGINA, pagina * TAMANHO_PAGINA + TAMANHO_PAGINA - 1);
    if (error) throw error;
    resultado.push(...(data || []));
    if (!data || data.length < TAMANHO_PAGINA) break;
    pagina++;
  }
  return resultado;
}

export default async function handler(req, res) {
  const supabaseUrl = process.env.SUPABASE_URL, supabaseKey = process.env.SUPABASE_KEY;
  if (!supabaseUrl || !supabaseKey) return res.status(500).json({ error: { message: 'SUPABASE_URL / SUPABASE_KEY não configuradas.' } });
  const supabase = getSupabase();

  try {
    const [predAntigas, predBenchmarking, todasMatches, ligas] = await Promise.all([
      buscarTudoPaginado(() => supabase.from('model_predictions').select('model_name, match_id').eq('market', '1X2')),
      buscarTudoPaginado(() => supabase.from('predicoes').select('model_name, match_id')),
      buscarTudoPaginado(() => supabase.from('matches').select('id, league_id, season, status')),
      buscarTudoPaginado(() => supabase.from('leagues').select('id, name')),
    ]);

    const matchInfo = {};
    todasMatches.forEach((m) => { matchInfo[m.id] = m; });

    // "bruto" -- sem sufixo _calibrado_platt/_calibrado_isotonic (essas
    // variantes já são cobertas pelo seletor de correção na simulação).
    const ehBruto = (nome) => !/_calibrado_(platt|isotonic)$/.test(nome);

    const contagem = {}; // model_name -> Set(match_id) finalizado
    const ligasPorModelo = {}; // model_name -> Set(league_id)
    const temporadasPorModelo = {}; // model_name -> Set(season)

    const processar = (linhas) => {
      linhas.forEach((l) => {
        if (!ehBruto(l.model_name)) return;
        const m = matchInfo[l.match_id];
        if (!m || m.status !== 'finished') return;
        (contagem[l.model_name] = contagem[l.model_name] || new Set()).add(l.match_id);
        (ligasPorModelo[l.model_name] = ligasPorModelo[l.model_name] || new Set()).add(m.league_id);
        (temporadasPorModelo[l.model_name] = temporadasPorModelo[l.model_name] || new Set()).add(m.season);
      });
    };
    processar(predAntigas);
    processar(predBenchmarking);

    const modelos = Object.keys(contagem)
      .map((nome) => ({
        model_name: nome,
        n_partidas_resolvidas: contagem[nome].size,
        ligas: [...ligasPorModelo[nome]],
        temporadas: [...temporadasPorModelo[nome]].sort(),
      }))
      .sort((a, b) => b.n_partidas_resolvidas - a.n_partidas_resolvidas);

    res.status(200).json({
      modelos,
      ligas: ligas.sort((a, b) => a.name.localeCompare(b.name)),
    });
  } catch (erro) {
    res.status(500).json({ error: { message: erro.message } });
  }
}
