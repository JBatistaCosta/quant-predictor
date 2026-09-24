// api/_lib/resultadosReais.js
//
// Resolve o resultado REAL de uma partida finalizada, por mercado --
// compartilhado entre api/model-stats.js e api/backtest-betting.js (código
// idêntico duplicado nos dois arquivos até esta extração, incluindo o
// mesmo comentário sobre o bug histórico que motivou a mudança abaixo).
//
// Mercado sem entrada aqui fica `undefined` DE PROPÓSITO -- comparar contra
// `undefined` nunca "acerta" por acaso. Antes, um switch de 3 braços jogava
// todo mercado desconhecido (btts, dupla_chance, handicap etc.) no bucket
// de escanteios O/U 9.5, com o mesmo rótulo genérico -- um mercado novo sem
// entrada aqui contaminaria log-loss/calibração/backtest silenciosamente
// (toda aposta "perde" por y=0 fixo), sem lançar erro. Qualquer mercado
// novo precisa ganhar uma entrada aqui antes de aparecer em qualquer
// avaliação.

// Linhas de gols por time (mandante/visitante) -- mesma constante/convenção
// de `src/utils/distribuicoesMercados.js`/`scripts/distribuicoes.py`
// (`mercados_de_gols`) e `scripts/rodar_predicoes.py` (`LINHAS_GOLS_TIME`).
export const LINHAS_GOLS_TIME = [0.5, 1.5, 2.5, 3.5, 4.5];

// Gols TOTAIS (mandante+visitante) -- mesmas linhas default de
// `src/utils/distribuicoesMercados.js`/`mercadosDeGols` (`linhasOverUnder`).
// ACHADO REAL (Fase 6 do motor de Markov, ampliando mercados derivados): até
// esta sessão só a linha 2.5 tinha resultado real resolvido aqui (hardcoded
// solto no meio do objeto `resultado`, fora do padrão de loop já usado pras
// outras famílias de linha) -- as demais (0.5/1.5/3.5/4.5) nunca tiveram
// entrada, mesmo bug já corrigido pras outras famílias.
export const LINHAS_GOLS_OU = [0.5, 1.5, 2.5, 3.5, 4.5];

// Handicap ASIÁTICO aplicado ao mandante -- mesmas linhas default de
// `distribuicoesMercados.js`/`mercadosDeGols` (`linhasHandicap`). Linha
// inteira pode empatar (push, `selection='push'`); linha fracionária nunca.
export const LINHAS_HANDICAP_OU = [-2.5, -2, -1.5, -1, -0.5, 0, 0.5, 1, 1.5, 2, 2.5];

// Linhas de chutes/chutes no gol (TOTAL da partida) -- mesma constante de
// `LINHAS_POR_STAT` em `arquivos_do_claude/modelo_stats_esperadas.py` e
// `LINHAS_PADRAO_POR_STAT` em `api/corners-model.js` (duplicada de
// propósito, mesmo padrão do resto do projeto).
export const LINHAS_SHOTS = { shots: [20.5, 22.5, 24.5, 26.5], shots_on_target: [7.5, 8.5, 9.5, 10.5] };

// Mercados "1º tempo" (gols/escanteios/faltas) -- mesmas linhas de
// `scripts/dados_historicos.py` (`LINHAS_GOLS_1T_OU`/`LINHAS_CORNERS_1T_OU`/
// `LINHAS_FALTAS_1T_OU`), duplicadas aqui de propósito (mesmo padrão
// JS<->Python do resto do arquivo). Nomes de mercado: gols/escanteios usam
// o nome REAL da OddsPapi (`over_under_first_half_1h_*`/
// `corners_over_under_first_half_1h_*`); faltas usa a chave interna
// (`faltas_1t_over_under_*`, sem mercado real).
export const LINHAS_GOLS_1T_OU = [0.5, 1.5, 2.5];
export const LINHAS_CORNERS_1T_OU = [3.5, 4.5, 5.5];
export const LINHAS_FALTAS_1T_OU = [9.5, 11.5, 13.5];

// Cartões (bookings), total da partida e por time -- mesmas linhas de
// `scripts/dados_historicos.py` (`LINHAS_CARTOES_OU`/`LINHAS_CARTOES_TIME_OU`)
// e da lista já usada em api/backtest-betting.js/api/model-stats.js pra
// buscar odds reais (`LINHAS_CARTOES_OU`/`LINHAS_CARTOES_TIME_OU` locais
// nesses arquivos, como string -- duplicada aqui de propósito, mesmo padrão
// do resto deste arquivo).
export const LINHAS_CARTOES_OU = [1.5, 2.5, 3.5, 4.5, 5.5, 6.5];
export const LINHAS_CARTOES_TIME_OU = [0.5, 1.5, 2.5, 3.5, 4.5];

// Escanteios TOTAIS (mandante+visitante) -- mesmas 6 linhas já usadas em
// api/model-stats.js/api/backtest-betting.js pra buscar odds reais
// (LINHAS_CORNERS_OU locais nesses arquivos), mas até esta sessão só a
// linha 9.5 tinha resultado real resolvido aqui embaixo -- as outras 5
// (7.5/8.5/10.5/11.5/12.5) caíam no MESMO bug já documentado acima pra
// cartões (comparar contra `undefined`, nunca "acerta" por acaso).
export const LINHAS_CORNERS_OU = [7.5, 8.5, 9.5, 10.5, 11.5, 12.5];

// Faltas TOTAIS (mandante+visitante) -- mesmas linhas de
// `LINHAS_PADRAO_POR_STAT.fouls` em api/corners-model.js. ACHADO REAL desta
// sessão (Fase 6 do motor de Markov): `faltas_over_under_*` nunca teve
// NENHUMA entrada aqui, em NENHUMA linha -- mesmo bug de "compara contra
// undefined, perde sempre" já documentado pra cartões, nunca corrigido pra
// faltas. Fonte: `match_disciplina.faltas_cometidas` (não tem o problema de
// confiabilidade de `fonte_cartoes` que cartões tem -- ver CLAUDE.md).
export const LINHAS_FALTAS_OU = [20.5, 22.5, 24.5, 26.5, 28.5, 30.5];

// ACHADO REAL (13/09): até esta função existir, `cartoes_over_under_X`/
// `cartoes_home_over_under_X`/`cartoes_away_over_under_X` não tinham NENHUMA
// entrada em `calcularResultadosReais` -- toda aposta de cartões comparava
// contra `undefined` e "perdia" sempre, ROI -100% fabricado em qualquer
// linha/liga testada (ver CONTEXTO_PROJETO.md). Porta a mesma lógica de
// fonte primária/fallback de `scripts/dados_historicos.py`
// (`_carregar_total_cartoes_por_partida`/`_carregar_cartoes_por_time_por_
// partida`): PRIMÁRIA `match_events` (evento a evento -- amarelo/2º
// amarelo/vermelho, cada linha é 1 cartão mostrado), usada só quando a
// partida tem QUALQUER linha em `match_events` (cobertura real é ruim --
// 100% ausente desde junho/2026 no treino Python, ver comentário lá);
// FALLBACK pra partida sem NENHUM evento ingerido: `match_stats_fotmob.
// yellow_cards`+`red_cards` (imperfeito, mas usar "sem match_events" como
// "0 cartões" seria pior -- rotularia todo jogo recente como zero). Gate:
// só resolve pra partida com as 2 linhas de `match_stats_fotmob` (mesma
// "processada" que corners/shots já exigem no call-site).
//
// `matchStatsRows`: linhas cruas de `match_stats_fotmob` já filtradas pro
// lote de match_id relevante, com `match_id, team_id, yellow_cards,
// red_cards` (reaproveita a mesma query já feita pra corners/shots/chutes
// nos dois call-sites, só com 2 colunas a mais). `matchEventsRows`: linhas
// cruas de `match_events` (`match_id, team_id, event_type`), TODOS os tipos
// de evento (não só cartão) -- precisa disso pra decidir se a partida "tem
// match_events" antes de filtrar por tipo de cartão.
const TIPOS_EVENTO_CARTAO = new Set(['yellow_card', 'second_yellow_card', 'red_card']);
export function calcularCartoesExtras(matchesValidos, matchStatsRows, matchEventsRows) {
  const cartoesTotal = {}, cartoesHome = {}, cartoesAway = {};

  const statsPorMatch = {};
  matchStatsRows.forEach(r => { (statsPorMatch[r.match_id] ||= []).push(r); });
  const processadas = new Set(
    Object.keys(statsPorMatch).filter(id => statsPorMatch[id].length === 2).map(Number)
  );

  const comEventos = new Set(matchEventsRows.filter(r => processadas.has(r.match_id)).map(r => r.match_id));

  const contagemEventosTotal = {};
  const contagemEventosPorTime = {};
  matchEventsRows
    .filter(r => comEventos.has(r.match_id) && TIPOS_EVENTO_CARTAO.has(r.event_type))
    .forEach(r => {
      contagemEventosTotal[r.match_id] = (contagemEventosTotal[r.match_id] || 0) + 1;
      contagemEventosPorTime[`${r.match_id}_${r.team_id}`] = (contagemEventosPorTime[`${r.match_id}_${r.team_id}`] || 0) + 1;
    });

  const fallbackPorTime = {};
  matchStatsRows.forEach(r => {
    if (r.yellow_cards == null && r.red_cards == null) return;
    fallbackPorTime[`${r.match_id}_${r.team_id}`] = (Number(r.yellow_cards) || 0) + (Number(r.red_cards) || 0);
  });

  matchesValidos.forEach(m => {
    if (!processadas.has(m.id)) return;
    if (comEventos.has(m.id)) {
      cartoesTotal[m.id] = contagemEventosTotal[m.id] || 0;
      cartoesHome[m.id] = contagemEventosPorTime[`${m.id}_${m.home_team_id}`] || 0;
      cartoesAway[m.id] = contagemEventosPorTime[`${m.id}_${m.away_team_id}`] || 0;
    } else {
      const home = fallbackPorTime[`${m.id}_${m.home_team_id}`];
      const away = fallbackPorTime[`${m.id}_${m.away_team_id}`];
      if (home != null) cartoesHome[m.id] = home;
      if (away != null) cartoesAway[m.id] = away;
      if (home != null && away != null) cartoesTotal[m.id] = home + away;
    }
  });

  return { cartoesTotal, cartoesHome, cartoesAway };
}

// Faltas TOTAIS (mandante+visitante) -- bem mais simples que cartões: uma
// fonte só (`match_disciplina.faltas_cometidas`), sem problema de
// confiabilidade documentado (diferente de `fonte_cartoes`, ver CLAUDE.md),
// então não precisa de fallback nem filtro por origem. Gate: só resolve
// quando os DOIS times da partida têm linha em `match_disciplina` (mesmo
// padrão de "count===2" já usado em `calcularCartoesExtras`/escanteios).
//
// `disciplinaRows`: linhas cruas de `match_disciplina` já filtradas pro
// lote de match_id relevante (`match_id, team_id, faltas_cometidas`).
export function calcularFaltasExtras(matchesValidos, disciplinaRows) {
  const faltasTotal = {};
  const porMatch = {};
  disciplinaRows.forEach(r => { (porMatch[r.match_id] ||= []).push(r); });

  matchesValidos.forEach(m => {
    const linhas = porMatch[m.id];
    if (!linhas || linhas.length !== 2) return;
    const [a, b] = linhas;
    if (a.faltas_cometidas == null || b.faltas_cometidas == null) return;
    faltasTotal[m.id] = Number(a.faltas_cometidas) + Number(b.faltas_cometidas);
  });

  return { faltasTotal };
}

// `extras`: `{ corners, shots, shots_on_target, golsPrimeiroTempo, corners1t,
// faltas1t, cartoesTotal, cartoesHome, cartoesAway }`, cada um um mapa
// `{ match_id: total_da_partida }` já somado (mandante+visitante, exceto
// cartoesHome/cartoesAway que já vêm por time) e validado (só entra se os
// dois times tiverem registro -- ver os call-sites em api/model-stats.js/
// api/backtest-betting.js, `cont[id] === 2`/`calcularCartoesExtras` acima).
// Precisam de JOIN novo (não vêm em `matches`, diferente de gols por time)
// -- por isso entram como parâmetro à parte, igual `corners` já fazia.
export function calcularResultadosReais(matches, extras = {}) {
  const {
    corners = {}, shots = {}, shots_on_target: shotsOnTarget = {},
    golsPrimeiroTempo = {}, corners1t = {}, faltas1t = {},
    cartoesTotal = {}, cartoesHome = {}, cartoesAway = {}, faltasTotal = {},
  } = extras;
  const porMatch = {};
  for (const m of matches) {
    if (m.status !== 'finished' || m.home_goals == null || m.away_goals == null) continue;
    const total = m.home_goals + m.away_goals;
    const resultado = {
      league_id: m.league_id,
      '1X2': m.home_goals > m.away_goals ? 'home' : m.home_goals < m.away_goals ? 'away' : 'draw',
      btts: (m.home_goals > 0 && m.away_goals > 0) ? 'yes' : 'no',
      // ACHADO REAL (Fase 6 do motor de Markov): `placar_exato` nunca teve
      // NENHUMA entrada aqui, em nenhum modelo -- mesmo bug de "compara
      // contra undefined, perde sempre" já documentado (e corrigido) acima
      // pra cartões/faltas/escanteios. Não precisa de join novo: `home_goals`/
      // `away_goals` já vêm carregados na query de `matches` que todo o
      // resto desta função já usa. Placar fora da grade que um modelo prevê
      // (ex.: >6 gols de um lado) simplesmente não bate com nenhuma seleção
      // prevista -- mercado esparso normal, não é bug.
      placar_exato: `${m.home_goals}-${m.away_goals}`,
    };
    // `dupla_chance` (seleções '1X'/'X2'/'12') PROPOSITALMENTE não tem
    // entrada aqui -- e não é descuido. É estrutural: 2 das 3 seleções
    // "vencem" em qualquer partida (ex.: mandante ganha -> '1X' E '12'
    // vencem, só 'X2' perde), então não existe um único
    // `resultado.dupla_chance` que sirva pro padrão `selection === resultado`
    // já usado em TODO o resto deste arquivo (e em api/model-stats.js/
    // api/backtest-betting.js pra log-loss/Brier/backtest). Adicionar uma
    // entrada de "único vencedor" aqui seria estruturalmente ERRADA (não só
    // incompleta) -- avaliaria 2 de cada 3 seleções contra o resultado
    // trocado. Corrigir de verdade exige suporte a "múltiplos vencedores por
    // mercado" nos consumidores, não só aqui -- ver CONTEXTO_PROJETO.md,
    // item 10 (achado da Fase 6 do motor de Markov).
    // Gols por time (mandante/visitante separados) -- `home_goals`/
    // `away_goals` já vêm carregados na query de `matches`, então não
    // precisa de join novo (diferente de escanteios/chutes por time, que
    // exigem `match_stats` por team_id). `team_1`/`team_2` (não `home`/
    // `away`) é a convenção real do mercado já em produção em
    // `odds_market` (OddsPapi) -- confirmado empiricamente (odds-sync-
    // diagnostico + teste por-partida contra `team_1_to_score`/
    // `team_2_to_score`: team_1 prevê o mandante marcar em 78,0% das
    // partidas vs. 68,8% pro visitante, N=1248 finalizadas): team_1 =
    // mandante, team_2 = visitante. Mesma constante/convenção de
    // `src/utils/distribuicoesMercados.js`/`scripts/distribuicoes.py`
    // (`mercados_de_gols`) e `scripts/rodar_predicoes.py`
    // (`LINHAS_GOLS_TIME`) -- duplicada aqui de propósito (JS<->Python já
    // não compartilha módulo, mesmo padrão do resto do projeto): se mudar
    // numa, mudar nas outras 3.
    for (const linha of LINHAS_GOLS_TIME) {
      const l = linha.toFixed(1);
      resultado[`over_under_team_1_${l}`] = m.home_goals > linha ? 'over' : 'under';
      resultado[`over_under_team_2_${l}`] = m.away_goals > linha ? 'over' : 'under';
    }
    for (const linha of LINHAS_GOLS_OU) {
      resultado[`over_under_${linha.toFixed(1)}`] = total > linha ? 'over' : 'under';
    }
    // Handicap asiático (mesma convenção de `distribuicoesMercados.js`/
    // `mercadosDeGols`): margem = placar - handicap aplicado ao mandante.
    // `push` só é possível em linha inteira (margem exatamente 0).
    for (const linha of LINHAS_HANDICAP_OU) {
      const margem = (m.home_goals - m.away_goals) + linha;
      resultado[`handicap_${linha.toFixed(1)}`] = margem > 0 ? 'home' : margem < 0 ? 'away' : 'push';
    }
    porMatch[m.id] = resultado;
  }
  // ACHADO REAL (Fase 6 do motor de Markov): até esta sessão, só a linha
  // 9.5 tinha resultado real resolvido aqui -- as outras 5 (7.5/8.5/10.5/
  // 11.5/12.5) caíam no mesmo bug já corrigido uma vez pra cartões
  // (comparar contra `undefined`, nunca "acerta" por acaso). Generalizado
  // pra todas as linhas de `LINHAS_CORNERS_OU`, mesmo padrão de
  // shots/shots_on_target logo abaixo.
  for (const [matchId, totalCorners] of Object.entries(corners)) {
    if (!porMatch[matchId]) continue;
    for (const linha of LINHAS_CORNERS_OU) {
      porMatch[matchId][`corners_over_under_${linha.toFixed(1)}`] = totalCorners > linha ? 'over' : 'under';
    }
  }
  // Chutes/chutes no gol (TOTAL, mandante+visitante) -- mesmo padrão de
  // escanteios acima, mas com várias linhas (não uma só) por stat, mesma
  // convenção de `market` de `arquivos_do_claude/modelo_stats_esperadas.py`
  // (`{stat}_over_under_{linha}`). Sem mercado por time aqui (deliberado:
  // `disp_r` foi calibrado sobre o TOTAL, aplicá-lo a um time isolado
  // reproduziria o mesmo erro de dispersão já corrigido uma vez pra
  // escanteios -- ver comentário em `api/corners-model.js`).
  for (const [stat, mapa] of [['shots', shots], ['shots_on_target', shotsOnTarget]]) {
    for (const [matchId, totalStat] of Object.entries(mapa)) {
      if (!porMatch[matchId]) continue;
      for (const linha of LINHAS_SHOTS[stat]) {
        porMatch[matchId][`${stat}_over_under_${linha.toFixed(1)}`] = totalStat > linha ? 'over' : 'under';
      }
    }
  }
  // Mercados "1º tempo" -- mesmo padrão de escanteios/chutes acima.
  // `golsPrimeiroTempo` já vem gated por cobertura (0 é "processado e sem
  // gol", não "sem dado"), ver comentário no call-site.
  for (const [matchId, totalGols1t] of Object.entries(golsPrimeiroTempo)) {
    if (!porMatch[matchId]) continue;
    for (const linha of LINHAS_GOLS_1T_OU) {
      porMatch[matchId][`over_under_first_half_1h_${linha.toFixed(1)}`] = totalGols1t > linha ? 'over' : 'under';
    }
  }
  for (const [matchId, totalCorners1t] of Object.entries(corners1t)) {
    if (!porMatch[matchId]) continue;
    for (const linha of LINHAS_CORNERS_1T_OU) {
      porMatch[matchId][`corners_over_under_first_half_1h_${linha.toFixed(1)}`] = totalCorners1t > linha ? 'over' : 'under';
    }
  }
  for (const [matchId, totalFaltas1t] of Object.entries(faltas1t)) {
    if (!porMatch[matchId]) continue;
    for (const linha of LINHAS_FALTAS_1T_OU) {
      porMatch[matchId][`faltas_1t_over_under_${linha.toFixed(1)}`] = totalFaltas1t > linha ? 'over' : 'under';
    }
  }
  // Cartões (bookings) -- ver `calcularCartoesExtras` acima pra fonte/
  // fallback. Nome de mercado interno (`cartoes_*`, não `bookings_*` --
  // esse é o nome REAL na odds_market, mapeado em `mercadoOddsReal` nos
  // call-sites) confirmado contra `custom_model_configs`/`model_predictions`.
  for (const [matchId, totalCartoes] of Object.entries(cartoesTotal)) {
    if (!porMatch[matchId]) continue;
    for (const linha of LINHAS_CARTOES_OU) {
      porMatch[matchId][`cartoes_over_under_${linha.toFixed(1)}`] = totalCartoes > linha ? 'over' : 'under';
    }
  }
  for (const [matchId, totalCartoesHome] of Object.entries(cartoesHome)) {
    if (!porMatch[matchId]) continue;
    for (const linha of LINHAS_CARTOES_TIME_OU) {
      porMatch[matchId][`cartoes_home_over_under_${linha.toFixed(1)}`] = totalCartoesHome > linha ? 'over' : 'under';
    }
  }
  for (const [matchId, totalCartoesAway] of Object.entries(cartoesAway)) {
    if (!porMatch[matchId]) continue;
    for (const linha of LINHAS_CARTOES_TIME_OU) {
      porMatch[matchId][`cartoes_away_over_under_${linha.toFixed(1)}`] = totalCartoesAway > linha ? 'over' : 'under';
    }
  }
  // Faltas (bookings) TOTAIS -- ver `calcularFaltasExtras` acima.
  // ACHADO REAL (Fase 6 do motor de Markov): `faltas_over_under_*` nunca
  // teve NENHUMA entrada aqui, em nenhuma linha -- mesmo bug de "compara
  // contra undefined, perde sempre" já documentado pra cartões, nunca
  // corrigido pra faltas totais (só a versão "1º tempo" era resolvida).
  for (const [matchId, totalFaltas] of Object.entries(faltasTotal)) {
    if (!porMatch[matchId]) continue;
    for (const linha of LINHAS_FALTAS_OU) {
      porMatch[matchId][`faltas_over_under_${linha.toFixed(1)}`] = totalFaltas > linha ? 'over' : 'under';
    }
  }
  return porMatch;
}
