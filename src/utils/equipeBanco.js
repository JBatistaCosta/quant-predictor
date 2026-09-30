// src/utils/equipeBanco.js
// Busca de equipes do banco (tabela `teams`) e "foto" de uma equipe numa data:
// Elo e médias de xG/xGA/chutes/escanteios/etc. calculados SÓ com jogos
// terminados ANTES da data escolhida (sem vazamento do futuro). Roda direto no
// client com a chave anon (RLS de leitura pública), então não cria função
// serverless nova (limite de 12 do Vercel Hobby).

const JOGOS_JANELA_PADRAO = 10; // janelas oferecidas na UI: 5, 10 ou 20
const JOGOS_HISTORICO = 5;      // últimos jogos p/ a fórmula "Time Decay" (xG/xGA)
const XI_DECAIMENTO = 0.2;      // mesmo ξ padrão de lambdaFormulas.js: peso = exp(-ξ·i), i=0 é o jogo mais recente

// Elasticidade do ajuste por Elo do adversário: variação RELATIVA esperada do valor por +400 de Elo do
// adversário, medida no próprio banco (27.075 pares jogo-time, desde 2023, efeito fixo por time:
// cada valor dividido pela média do time e regredido no Elo do adversário antes do jogo). Contra
// adversário mais forte o time cria menos (xG −45%, chutes −36%, escanteios −42%) e sofre mais (xGA +66%).
// Posse, faltas e cartões não foram medidos, então só recebem o decaimento, sem ajuste por Elo.
const ELASTICIDADE_ELO = { xg: -0.45, xga: 0.66, chutes: -0.36, chutesNoGol: -0.36, escanteios: -0.42 };
const FATOR_MIN = 0.5;
const FATOR_MAX = 1.5;

// Média ponderada com decaimento exponencial; `itens` = [{ valor, i }] com i=0 mais recente.
const mediaDecaida = (itens) => {
  let soma = 0;
  let pesos = 0;
  for (const { valor, i } of itens) {
    if (valor === null || valor === undefined || !Number.isFinite(Number(valor))) continue;
    const w = Math.exp(-XI_DECAIMENTO * i);
    soma += w * Number(valor);
    pesos += w;
  }
  return pesos > 0 ? soma / pesos : null;
};

// Traz o valor de um jogo para a "força de adversário" de referência (Elo do adversário da simulação):
// valor / (1 + β·(EloAdvPassado − EloRef)/400). Sem Elo do jogo ou sem referência, não mexe.
const ajustarPorElo = (valor, campo, eloAdvPassado, eloRef) => {
  const beta = ELASTICIDADE_ELO[campo];
  if (valor === null || valor === undefined || beta === undefined || eloAdvPassado == null || eloRef == null) return valor;
  const fator = Math.min(FATOR_MAX, Math.max(FATOR_MIN, 1 + beta * ((eloAdvPassado - eloRef) / 400)));
  return Number(valor) / fator;
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

// Elo global da equipe ANTES da data (última linha do histórico com match_date < dataRef).
// Data futura/hipotética = último Elo conhecido. Sem histórico, cai no Elo atual (`team_elo`) com aviso.
export async function carregarEloEm(supabase, equipe, dataRef) {
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
    return { rating: Math.round(Number(eloHist[0].rating_depois)), ratingOrigem: `Elo global em ${eloHist[0].match_date}` };
  }
  const { data: eloAtual } = await supabase
    .from('team_elo')
    .select('rating')
    .eq('team_id', equipe.id)
    .eq('escopo', 'global')
    .limit(1);
  if (eloAtual?.[0]?.rating != null) {
    return { rating: Math.round(Number(eloAtual[0].rating)), ratingOrigem: 'Elo ATUAL (sem histórico anterior à data)' };
  }
  return { rating: null, ratingOrigem: null };
}

// Retorna { rating, ratingOrigem, jogos, medias, historico, ligaId, ligaNome, ultimoJogo, markov }.
// `dataRef` = 'YYYY-MM-DD'; entram só jogos com match_date < início desse dia (UTC).
// opts: { janela = 10 (5/10/20 jogos), ajusteElo = true, eloReferencia = Elo do adversário da simulação,
//         elo = resultado já carregado de carregarEloEm (evita repetir a consulta) }.
// Os jogos usados NÃO precisam ser contra o adversário da simulação: cada valor recebe peso de
// decaimento exponencial pela recência e, se `ajusteElo`, é normalizado pelo Elo do adversário daquele jogo.
export async function carregarFotoEquipe(supabase, equipe, dataRef, opts = {}) {
  const { janela = JOGOS_JANELA_PADRAO, ajusteElo = true, eloReferencia = null } = opts;
  const corte = `${dataRef}T00:00:00Z`;

  // Últimos jogos terminados antes da data (mandante ou visitante).
  const { data: partidas, error: erroPartidas } = await supabase
    .from('matches')
    .select('id, match_date, league_id, home_team_id, away_team_id, leagues(name), home:teams!matches_home_team_id_fkey(name), away:teams!matches_away_team_id_fkey(name)')
    .eq('status', 'finished')
    .lt('match_date', corte)
    .or(`home_team_id.eq.${equipe.id},away_team_id.eq.${equipe.id}`)
    .order('match_date', { ascending: false })
    .limit(janela);
  if (erroPartidas) throw erroPartidas;

  const ids = (partidas || []).map((p) => p.id);
  // Duas fontes de estatística por jogo: `match_stats_fotmob` (cobre a maior parte dos jogos,
  // inclusive os mais recentes — ~19 mil jogos com xG) e `match_stats` (legado, ~9 mil).
  // Sem juntar as duas, times cujos jogos recentes só existem no FotMob (ex.: Brasileirão)
  // voltavam com xG/chutes/escanteios vazios. Por campo, FotMob tem prioridade.
  let stats = [];
  if (ids.length > 0) {
    const [{ data: legado, error: erroLegado }, { data: fotmob, error: erroFotmob }, { data: disciplina, error: erroDisciplina }] = await Promise.all([
      supabase.from('match_stats')
        .select('match_id, team_id, shots, shots_on_target, possession, corners, fouls, yellow_cards, red_cards, xg')
        .in('match_id', ids),
      supabase.from('match_stats_fotmob')
        .select('match_id, team_id, total_shots, shots_on_target, possession, corners, fouls_committed, xg')
        .in('match_id', ids),
      // Faltas e cartões: fonte designada pelo projeto (mesma de api/corners-model.js).
      supabase.from('match_disciplina')
        .select('match_id, team_id, faltas_cometidas, cartoes_amarelos, cartoes_vermelhos_equiv, fonte_cartoes')
        .in('match_id', ids),
    ]);
    if (erroLegado) throw erroLegado;
    if (erroFotmob) throw erroFotmob;
    if (erroDisciplina) throw erroDisciplina;
    const chave = (r) => `${r.match_id}:${r.team_id}`;
    const mapa = new Map();
    // Cartões de `match_stats`/`match_stats_fotmob` NÃO são confiáveis (bug documentado: contagem zerada
    // em boa parte dos jogos — medido: 100% dos amarelos de Palmeiras e Fluminense nos últimos 20 jogos
    // vêm 0 no FotMob). Descartados aqui; cartões vêm só de `match_disciplina`, filtrados por `fonte_cartoes`.
    for (const r of legado || []) mapa.set(chave(r), { ...r, yellow_cards: null, red_cards: null });
    for (const f of fotmob || []) {
      const normalizado = {
        match_id: f.match_id, team_id: f.team_id, shots: f.total_shots, shots_on_target: f.shots_on_target,
        possession: f.possession, corners: f.corners, fouls: f.fouls_committed,
        xg: f.xg,
      };
      const atual = mapa.get(chave(f)) || {};
      const fundido = { ...atual };
      for (const [k, v] of Object.entries(normalizado)) if (v !== null && v !== undefined) fundido[k] = v;
      mapa.set(chave(f), fundido);
    }
    // Disciplina: faltas sempre (sem filtro, como a API); cartões só com fonte confiável.
    const FONTES_CARTAO_OK = new Set(['match_events', 'fallback_fotmob']);
    for (const d of disciplina || []) {
      const atual = mapa.get(chave(d)) || { match_id: d.match_id, team_id: d.team_id };
      if (d.faltas_cometidas !== null && d.faltas_cometidas !== undefined) atual.fouls = d.faltas_cometidas;
      if (FONTES_CARTAO_OK.has(d.fonte_cartoes)) {
        atual.yellow_cards = d.cartoes_amarelos;
        atual.red_cards = d.cartoes_vermelhos_equiv;
      }
      mapa.set(chave(d), atual);
    }
    stats = [...mapa.values()];
  }

  // Elo (antes do jogo) do adversário de cada partida, para o ajuste por força do adversário.
  const eloAdvPorJogo = new Map();
  let jogosEloAproximado = 0;
  if (ajusteElo && ids.length > 0) {
    const { data: eloJogos } = await supabase
      .from('team_elo_history')
      .select('match_id, team_id, rating_antes')
      .eq('escopo', 'global')
      .in('match_id', ids);
    for (const r of eloJogos || []) {
      if (Number(r.team_id) !== Number(equipe.id)) eloAdvPorJogo.set(r.match_id, Number(r.rating_antes));
    }
    // Jogos sem linha em team_elo_history (o histórico de Elo pode estar defasado em relação aos jogos
    // mais recentes): usa o último Elo conhecido do adversário (`team_elo`) — aproximação, contada à parte.
    const semElo = (partidas || []).filter((p) => !eloAdvPorJogo.has(p.id));
    const idsAdv = [...new Set(semElo.map((p) => (Number(p.home_team_id) === Number(equipe.id) ? p.away_team_id : p.home_team_id)))];
    if (idsAdv.length > 0) {
      const { data: eloAtual } = await supabase
        .from('team_elo').select('team_id, rating').eq('escopo', 'global').in('team_id', idsAdv);
      const porTime = new Map((eloAtual || []).map((r) => [Number(r.team_id), Number(r.rating)]));
      for (const p of semElo) {
        const adv = Number(p.home_team_id) === Number(equipe.id) ? p.away_team_id : p.home_team_id;
        if (porTime.has(Number(adv))) { eloAdvPorJogo.set(p.id, porTime.get(Number(adv))); jogosEloAproximado += 1; }
      }
    }
  }

  // Por jogo: linha do próprio time + linha do adversário (xGA = xG do adversário).
  const porJogo = (partidas || []).map((p) => {
    const proprio = stats.find((s) => s.match_id === p.id && Number(s.team_id) === Number(equipe.id)) || null;
    const adversario = stats.find((s) => s.match_id === p.id && Number(s.team_id) !== Number(equipe.id)) || null;
    return { partida: p, proprio, adversario, eloAdv: eloAdvPorJogo.get(p.id) ?? null };
  });
  const jogosComEloAdv = porJogo.filter((j) => j.eloAdv != null).length;
  const usaAjuste = ajusteElo && eloReferencia != null;

  // Média com decaimento; `campo` de ajuste por Elo só se aplica às métricas com elasticidade medida.
  const valorDe = (j, extrair, campoElo) => {
    const v = extrair(j);
    return usaAjuste && campoElo ? ajustarPorElo(v, campoElo, j.eloAdv, eloReferencia) : v;
  };
  const decaida = (extrair, campoElo) =>
    mediaDecaida(porJogo.map((j, i) => ({ valor: valorDe(j, extrair, campoElo), i })));
  const col = (campo, campoElo) => decaida((j) => j.proprio?.[campo], campoElo);
  const medias = {
    xg: col('xg', 'xg'),
    xga: decaida((j) => j.adversario?.xg, 'xga'),
    chutes: col('shots', 'chutes'),
    chutesNoGol: col('shots_on_target', 'chutesNoGol'),
    posse: col('possession'),
    escanteios: col('corners', 'escanteios'),
  };

  const historico = porJogo.slice(0, JOGOS_HISTORICO).map((j) => ({
    xg: valorDe(j, (x) => x.proprio?.xg ?? null, 'xg'),
    xga: valorDe(j, (x) => x.adversario?.xg ?? null, 'xga'),
  }));

  // Histórico jogo a jogo de faltas e cartões (mesmas fontes e mesmo peso de decaimento que alimentam o
  // motor de Markov), com média simples e média com time-decay lado a lado. Faltas/cartões não levam
  // ajuste por Elo, então a média com time-decay aqui é exatamente a taxa usada na simulação.
  const pesosBrutos = porJogo.map((_, i) => Math.exp(-XI_DECAIMENTO * i));
  const somaPesos = pesosBrutos.reduce((a, b) => a + b, 0) || 1;
  const jogosDisciplina = porJogo.map((j, i) => {
    const p = j.partida;
    const emCasa = Number(p.home_team_id) === Number(equipe.id);
    return {
      data: p.match_date?.slice(0, 10) ?? null,
      local: emCasa ? 'C' : 'F',
      adversario: (emCasa ? p.away?.name : p.home?.name) ?? '?',
      faltas: j.proprio?.fouls ?? null,
      amarelos: j.proprio?.yellow_cards ?? null,
      vermelhos: j.proprio?.red_cards ?? null,
      pesoPct: (100 * pesosBrutos[i]) / somaPesos,
    };
  });
  const resumir = (campo) => {
    const itens = jogosDisciplina.map((g, i) => ({ valor: g[campo], i }));
    const validos = itens.filter((x) => x.valor !== null && x.valor !== undefined);
    return {
      n: validos.length,
      simples: validos.length ? validos.reduce((a, x) => a + Number(x.valor), 0) / validos.length : null,
      decay: mediaDecaida(itens),
    };
  };
  const disciplina = {
    jogos: jogosDisciplina,
    resumo: { faltas: resumir('faltas'), amarelos: resumir('amarelos'), vermelhos: resumir('vermelhos') },
  };

  const { rating, ratingOrigem } = opts.elo ?? await carregarEloEm(supabase, equipe, dataRef);

  const ultima = partidas?.[0] || null;
  return {
    rating,
    ratingOrigem,
    jogos: porJogo.length,
    janela,
    ajusteEloAplicado: usaAjuste,
    jogosComEloAdv,
    jogosEloAproximado,
    jogosComXg: porJogo.filter((j) => j.proprio?.xg != null).length,
    medias,
    historico,
    disciplina,
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

// Procura o jogo REAL entre as duas equipes no dia escolhido (nas duas ordens de mando).
// Retorna null se não existir — aí a simulação é só um "encontro" hipotético.
export async function buscarJogoReal(supabase, idMandante, idVisitante, dataRef) {
  const inicio = `${dataRef}T00:00:00Z`;
  const fim = new Date(new Date(inicio).getTime() + 24 * 3600 * 1000).toISOString();
  const { data, error } = await supabase
    .from('matches')
    .select('id, match_date, status, home_team_id, away_team_id, home_goals, away_goals, leagues(name)')
    .gte('match_date', inicio)
    .lt('match_date', fim)
    .or(
      `and(home_team_id.eq.${idMandante},away_team_id.eq.${idVisitante}),` +
      `and(home_team_id.eq.${idVisitante},away_team_id.eq.${idMandante})`
    )
    .limit(1);
  if (error) throw error;
  const m = data?.[0];
  if (!m) return null;
  const invertido = Number(m.home_team_id) !== Number(idMandante);
  return {
    status: m.status,
    ligaNome: m.leagues?.name ?? null,
    data: m.match_date?.slice(0, 10),
    invertido, // true = na vida real o "visitante" daqui foi o mandante
    golsMandante: invertido ? m.away_goals : m.home_goals, // sempre do ponto de vista de Equipe 1 x Equipe 2
    golsVisitante: invertido ? m.home_goals : m.away_goals,
  };
}

// r (dispersão) da Binomial Negativa de escanteios, calibrado por liga em `league_model_params`
// (stat='corners', param_name='disp_r') — mesma fonte de `api/corners-model.js`. Tenta as ligas
// candidatas na ordem (mandante primeiro) e devolve a primeira com calibração; senão `null`, e quem
// chama mantém o padrão do app. Hoje só as 5 grandes ligas europeias têm calibração.
export async function carregarDispRCorners(supabase, ligaIds) {
  const ids = [...new Set((ligaIds || []).filter((x) => x != null))];
  if (ids.length === 0) return null;
  const { data, error } = await supabase
    .from('league_model_params')
    .select('league_id, param_value')
    .eq('stat', 'corners')
    .eq('param_name', 'disp_r')
    .in('league_id', ids);
  if (error || !data?.length) return null;
  for (const id of ids) {
    const linha = data.find((r) => Number(r.league_id) === Number(id));
    if (linha && Number(linha.param_value) > 0) return { valor: Number(linha.param_value), ligaId: id };
  }
  return null;
}
