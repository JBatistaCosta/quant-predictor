// api/_lib/dedupMatches.js
// Compartilhado pelos 4 pontos que criam partidas em `matches` a partir de
// fonte externa (api/sync-matches.js + tarefaBackfillCompeticao/
// tarefaBackfillApiFootball/tarefaBackfillFotmobLiga em
// api/model-maintenance.js). Cada um faz upsert só dentro do próprio
// namespace de external_id (fd_/af_/fm_) -- nada impedia 2-3 fontes
// criarem uma linha CADA UMA pro MESMO jogo real quando a mesma liga
// acaba coberta por mais de uma fonte (achado real rodando uma auditoria:
// Premier League 2026 triplicada -- fd_+af_+fm_ --, La Liga 2026
// duplicada -- fd_+af_ --, Brasileirão Série A/B e Copa Sudamericana
// também afetados). Ver scripts/limpar_partidas_duplicadas.py pra
// limpeza do que já ficou duplicado.
//
// Aqui: antes de gravar um lote de partidas candidatas de UMA fonte,
// carrega as partidas já existentes NAQUELA liga e verifica, pra cada
// candidata, se já existe uma partida de QUALQUER fonte pro mesmo
// mandante+visitante+dia (hora pode variar entre fontes -- placeholder
// 00:00 vs horário real de kickoff -- por isso a chave usa só o dia,
// mesmo critério já usado em scripts/limpar_partidas_duplicadas.py). Se
// já existe uma partida de OUTRA fonte, não insere uma linha nova --
// atualiza a existente (sem nunca regredir dado já bom: não apaga
// placar/round/stage preenchido, não derruba status='finished').

function* fatiarInterno(array, tamanho) {
  for (let i = 0; i < array.length; i += tamanho) yield array.slice(i, i + tamanho);
}

function chaveJogo(homeTeamId, awayTeamId, matchDateIso) {
  const dia = (matchDateIso || '').slice(0, 10);
  return `${homeTeamId}|${awayTeamId}|${dia}`;
}

async function carregarIndiceExistentes(supabase, leagueId) {
  const { data } = await supabase
    .from('matches')
    .select('id, external_id, home_team_id, away_team_id, match_date, home_goals, away_goals, status, round, stage')
    .eq('league_id', leagueId);
  const indice = new Map();
  for (const linha of data || []) {
    indice.set(chaveJogo(linha.home_team_id, linha.away_team_id, linha.match_date), linha);
  }
  return indice;
}

// Recebe as linhas candidatas de UMA fonte (já no formato pronto pra
// upsert: external_id/league_id/season/match_date/home_team_id/
// away_team_id/home_goals/away_goals/status/round/stage) e o índice de
// partidas já existentes nessa liga (carregarIndiceExistentes). Devolve:
// - novasOuMesmaFonte: upsert normal por external_id (jogo genuinamente
//   novo, ou resync da MESMA fonte que já tinha gravado essa linha antes).
// - atualizarExistente: linhas já resolvidas pro `id` da partida
//   existente de OUTRA fonte -- upsert por id, mesclando os campos que
//   ainda estão faltando na linha existente, sem sobrescrever nada que já
//   tem valor melhor (placar/round/stage preenchido, status já
//   'finished').
function particionar(linhasCandidatas, indice) {
  const novasOuMesmaFonte = [];
  const atualizarExistente = [];

  for (const linha of linhasCandidatas) {
    const chave = chaveJogo(linha.home_team_id, linha.away_team_id, linha.match_date);
    const existente = indice.get(chave);

    if (!existente || existente.external_id === linha.external_id) {
      novasOuMesmaFonte.push(linha);
      continue;
    }

    atualizarExistente.push({
      id: existente.id,
      home_goals: existente.home_goals ?? linha.home_goals,
      away_goals: existente.away_goals ?? linha.away_goals,
      status: existente.status === 'finished' ? existente.status : (linha.status || existente.status),
      round: existente.round ?? linha.round,
      stage: existente.stage ?? linha.stage,
    });
  }

  return { novasOuMesmaFonte, atualizarExistente };
}

// Ponto de entrada único: dado o league_id e as linhas candidatas de uma
// fonte, carrega o índice, particiona e grava os dois lotes em batches de
// 200. Devolve quantas linhas foram gravadas (inserção ou merge) e quantas
// duplicatas cross-fonte foram evitadas (métrica de diagnóstico, útil pra
// conferir na resposta da tarefa que o dedup está realmente pegando algo).
async function gravarComDedupCruzado(supabase, leagueId, linhasCandidatas) {
  if (linhasCandidatas.length === 0) return { gravados: 0, duplicatas_evitadas: 0 };

  const indice = await carregarIndiceExistentes(supabase, leagueId);
  const { novasOuMesmaFonte, atualizarExistente } = particionar(linhasCandidatas, indice);

  let gravados = 0;
  for (const lote of fatiarInterno(novasOuMesmaFonte, 200)) {
    const { error } = await supabase.from('matches').upsert(lote, { onConflict: 'external_id' });
    if (!error) gravados += lote.length;
  }
  for (const lote of fatiarInterno(atualizarExistente, 200)) {
    const { error } = await supabase.from('matches').upsert(lote, { onConflict: 'id' });
    if (!error) gravados += lote.length;
  }

  return { gravados, duplicatas_evitadas: atualizarExistente.length };
}

export { chaveJogo, carregarIndiceExistentes, particionar, gravarComDedupCruzado };
