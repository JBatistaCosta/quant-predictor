// src/utils/equipeBanco.js
// Busca de equipes do banco (tabela `teams`) e "foto" de uma equipe numa data:
// Elo e médias de xG/xGA/chutes/escanteios/etc. calculados SÓ com jogos
// terminados ANTES da data escolhida (sem vazamento do futuro). Roda direto no
// client com a chave anon (RLS de leitura pública), então não cria função
// serverless nova (limite de 12 do Vercel Hobby).

const JOGOS_JANELA = 10;   // jogos usados na média
const JOGOS_HISTORICO = 5; // últimos jogos p/ a fórmula "Time Decay" (xG/xGA)

const media = (valores) => {
  const v = valores.filter((x) => x !== null && x !== undefined && Number.isFinite(Number(x))).map(Number);
  return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
};

// Busca equipes pelo nome (ou apelido). `ilike` escapa % e _ digitados.
export async function buscarEquipes(supabase, termo) {
  const limpo = termo.trim().replace(/[%_,()]/g, ' ').trim();
  if (limpo.length < 2) return [];
  const { data, error } = await supabase
    .from('teams')
    .select('id, name, country, is_national_team')
    .ilike('name', `%${limpo}%`)
    .order('name')
    .limit(15);
  if (error) throw error;
  return data || [];
}

// Retorna { rating, ratingOrigem, jogos, medias, historico, ligaId, ligaNome, ultimoJogo, markov }.
// `dataRef` = 'YYYY-MM-DD'; entram só jogos com match_date < início desse dia (UTC).
export async function carregarFotoEquipe(supabase, equipe, dataRef) {
  const corte = `${dataRef}T00:00:00Z`;

  // Últimos jogos terminados antes da data (mandante ou visitante).
  const { data: partidas, error: erroPartidas } = await supabase
    .from('matches')
    .select('id, match_date, league_id, home_team_id, away_team_id, leagues(name)')
    .eq('status', 'finished')
    .lt('match_date', corte)
    .or(`home_team_id.eq.${equipe.id},away_team_id.eq.${equipe.id}`)
    .order('match_date', { ascending: false })
    .limit(JOGOS_JANELA);
  if (erroPartidas) throw erroPartidas;

  const ids = (partidas || []).map((p) => p.id);
  let stats = [];
  if (ids.length > 0) {
    const { data, error } = await supabase
      .from('match_stats')
      .select('match_id, team_id, shots, shots_on_target, possession, corners, fouls, yellow_cards, red_cards, xg')
      .in('match_id', ids);
    if (error) throw error;
    stats = data || [];
  }

  // Por jogo: linha do próprio time + linha do adversário (xGA = xG do adversário).
  const porJogo = (partidas || []).map((p) => {
    const proprio = stats.find((s) => s.match_id === p.id && Number(s.team_id) === Number(equipe.id)) || null;
    const adversario = stats.find((s) => s.match_id === p.id && Number(s.team_id) !== Number(equipe.id)) || null;
    return { partida: p, proprio, adversario };
  });

  const col = (campo) => media(porJogo.map((j) => j.proprio?.[campo]));
  const medias = {
    xg: col('xg'),
    xga: media(porJogo.map((j) => j.adversario?.xg)),
    chutes: col('shots'),
    chutesNoGol: col('shots_on_target'),
    posse: col('possession'),
    escanteios: col('corners'),
  };

  const historico = porJogo.slice(0, JOGOS_HISTORICO).map((j) => ({
    xg: j.proprio?.xg ?? null,
    xga: j.adversario?.xg ?? null,
  }));

  // Elo global ANTES da data: última linha do histórico com match_date < dataRef.
  let rating = null;
  let ratingOrigem = null;
  const { data: eloHist } = await supabase
    .from('team_elo_history')
    .select('rating_depois, match_date')
    .eq('team_id', equipe.id)
    .eq('escopo', 'global')
    .lt('match_date', dataRef)
    .order('match_date', { ascending: false })
    .order('match_id', { ascending: false })
    .limit(1);
  if (eloHist?.[0]?.rating_depois != null) {
    rating = Math.round(Number(eloHist[0].rating_depois));
    ratingOrigem = `Elo global em ${eloHist[0].match_date}`;
  } else {
    // Sem histórico anterior à data: usa o Elo atual, avisando que não é "as-of".
    const { data: eloAtual } = await supabase
      .from('team_elo')
      .select('rating')
      .eq('team_id', equipe.id)
      .eq('escopo', 'global')
      .limit(1);
    if (eloAtual?.[0]?.rating != null) {
      rating = Math.round(Number(eloAtual[0].rating));
      ratingOrigem = 'Elo ATUAL (sem histórico anterior à data)';
    }
  }

  const ultima = partidas?.[0] || null;
  return {
    rating,
    ratingOrigem,
    jogos: porJogo.length,
    jogosComXg: porJogo.filter((j) => j.proprio?.xg != null).length,
    medias,
    historico,
    ligaId: ultima?.league_id ?? null,
    ligaNome: ultima?.leagues?.name ?? null,
    ultimoJogo: ultima?.match_date?.slice(0, 10) ?? null,
    // Taxas por jogo para o motor de Markov multi-evento (mesmos nomes de markovEventRates).
    markov: {
      chutes: medias.chutes,
      chutesNoAlvo: medias.chutesNoGol,
      cartaoAmarelo: col('yellow_cards'),
      cartaoVermelho: col('red_cards'),
      escanteio: medias.escanteios,
      falta: col('fouls'),
    },
  };
}
