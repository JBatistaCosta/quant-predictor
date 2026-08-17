// api/model-maintenance.js
// Roda no SERVIDOR do Vercel. Variáveis de ambiente necessárias:
//   SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY -> mesmas de sync-matches.js
//
// Dois endpoints administrativos (chamados manualmente, não pelo frontend)
// FUNDIDOS num arquivo só: o plano Hobby do Vercel limita 12 Serverless
// Functions por deployment, e eram dois arquivos separados (compute-elo.js +
// fit-calibration.js) até o deploy do backtest de apostas estourar esse
// limite (13º arquivo) — erro `exceeded_serverless_functions_per_deployment`.
// Nenhum dos dois é referenciado pelo frontend, só chamados via curl direto,
// então fundir é seguro (só muda a URL de chamada).
//
// COMO CHAMAR:
//   ?tarefa=elo&liga_id=X       -> recalcula o Elo interno de UMA liga doméstica (1,4,7,10,13,16)
//   ?tarefa=elo&escopo=geral    -> recalcula o Elo geral (cross-liga, Champions League)
//   ?tarefa=elo-rotativo        -> versão pro cron diário: processa só 1 escopo por dia,
//                                   revezando entre as 6 ligas domésticas + geral (dia do
//                                   ano mod 7) — ciclo completo fecha em ~1 semana. Existe
//                                   porque processar tudo numa chamada só já deu timeout
//                                   antes (testado, ver CONTEXTO_PROJETO.md).
//   ?tarefa=calibracao          -> reajusta Platt Scaling + Isotonic Regression (todos os combos)
//   ?tarefa=calibracao&minimo=N -> idem, exigindo N amostras de treino mínimas (padrão 80)
//   ?tarefa=odds-descobrir      -> FASE 1 do sync de odds (OddsPapi): resolve torneios/mercados/
//                                   casas uma vez só (precisa de ODDSPAPI_KEY) — ver comentário
//                                   detalhado na função abaixo antes de rodar (cota é 250 req/mês)
//   ?tarefa=odds&liga_id=X      -> FASE 2: sincroniza odds de verdade de uma liga (3 chamadas,
//                                   uma por bookmaker) — pula sozinho se não houver jogo
//                                   suficiente no curto prazo, pra não gastar cota à toa
//   ?tarefa=odds-todas          -> roda tarefa=odds nas 6 ligas domésticas em sequência
//                                   (usado pelo cron em vercel.json)
//   ?tarefa=odds-historico-descobrir&liga_id=X
//                               -> FASE 1 do BACKFILL de odds de rodadas JÁ ENCERRADAS: 2
//                                   chamadas só (lista de fixtures finalizadas do torneio +
//                                   1 amostra de /v4/historical-odds), cacheadas em
//                                   oddspapi_cache — rodar e inspecionar a amostra antes de
//                                   confiar no parser da FASE 2 abaixo (endpoint pouco usado
//                                   neste projeto até agora, ver comentário na função).
//   ?tarefa=odds-historico&liga_id=X[&temporada=AAAA][&limite=N]
//                               -> FASE 2: importa odds de fechamento de partidas JÁ
//                                   FINALIZADAS, em lotes de N (padrão/teto
//                                   MAX_FIXTURES_HISTORICO_POR_CHAMADA) — diferente de
//                                   tarefa=odds acima (que só pega jogos AGENDADOS), esse é
//                                   por fixture individual (1 chamada por partida, cooldown
//                                   de 5s da OddsPapi), então uma temporada inteira consome
//                                   cota real — ver aviso de custo na função antes de rodar.
//   ?tarefa=backfill-competicao&codigo=CLI&temporada=2023
//                               -> importa TODAS as partidas de uma temporada específica de
//                                   uma competição já cadastrada em leagues (external_id=codigo),
//                                   via football-data.org (precisa de FOOTBALL_DATA_KEY). Cria
//                                   times novos por upsert (external_id), igual sync-matches.js —
//                                   mas esse sincroniza só a temporada ATUAL; essa tarefa é pra
//                                   backfill histórico manual (ex: popular temporadas passadas de
//                                   uma competição nova). O plano grátis da football-data.org só
//                                   libera as ~4 temporadas mais recentes de cada competição
//                                   (temporada mais antiga retorna 403) — testado com Libertadores:
//                                   2023-2026 acessíveis, 2022 bloqueado.
//   ?tarefa=backfill-api-football&api_football_id=73&temporada=2023
//                               -> mesma ideia, mas pra competições que só existem na API-Football
//                                   (não na football-data.org — ex: Copa do Brasil). Resolve a liga
//                                   via liga_fonte_externa (sistema='api_football'), não via
//                                   leagues.external_id (esse continua reservado pro código da
//                                   football-data.org). Times casados/criados via team_source_ids
//                                   (source='api_football', mesmo padrão do crosswalk usado pro
//                                   fbref/understat) — nomes de time da API-Football não têm
//                                   nenhuma relação com os external_id de football-data.org já
//                                   salvos em teams, por isso o crosswalk separado. Custo de API:
//                                   1 chamada por liga+temporada (/fixtures já traz tudo, sem
//                                   chamada extra por time). Conta free da API-Football só libera
//                                   temporadas 2022-2024 (oposto da restrição da football-data.org,
//                                   que bloqueia temporadas antigas) — testado com Copa do Brasil.
//   ?tarefa=info-clubes&limite=N -> popula teams.city/stadium/country via API-Football (/teams?id=X),
//                                   só pros times que já têm id da API-Football confirmado em
//                                   team_source_ids (zero matching por nome nessa tarefa). Roda em
//                                   lotes (padrão 40) — 1 chamada de API por time.
//   ?tarefa=af-diagnostico-time&api_football_id=X
//                               -> dumpa a resposta crua de /teams?id=X (não escreve nada), pra
//                                   inspecionar o shape antes de confiar num parser novo.
//   ?tarefa=player-elo&limite=N -> rating Elo-like por jogador (padrão N=200 partidas por
//                                   chamada), a partir de match_player_stats_fotmob (nota do
//                                   FotMob + gols/assistências/xG/xA). Global/cross-competição,
//                                   acumulativo — processa só partidas pendentes em ordem
//                                   cronológica, idempotente (retoma sozinho de onde parou).
//                                   Pesos configuráveis via model_config (ver config-get/set);
//                                   default é um chute inicial, não calibrado.
//   ?tarefa=player-elo-reset    -> zera player_ratings/player_rating_history pra reprocessar
//                                   do zero (necessário depois de mudar pesos na config).
//   ?tarefa=config-get&model_name=X -> lê a config de um modelo em model_config.
//   ?tarefa=config-set&model_name=X (POST, corpo JSON) -> faz merge do corpo na config salva.
//   ?tarefa=disparar-predicoes (POST) -> dispara o workflow_dispatch do predict.yml no GitHub
//                                   Actions (Model Benchmarking: dixon_coles_v1 roda de verdade,
//                                   grava em market_odds/predicoes -- v1-v8 dos modelos de árvore
//                                   removidos, superados pela v9/v10 em model_predictions).
//                                   ÚNICA tarefa deste arquivo que exige
//                                   autenticação (header Authorization: Bearer <access_token do
//                                   Supabase Auth>) -- as outras são só chamadas manualmente/por
//                                   cron, nunca pelo frontend; esta é clicável em
//                                   /model-benchmarking, então precisa de verificação de sessão de
//                                   verdade no servidor (ProtectedRoute no frontend é só cosmético,
//                                   não protege a API). Requer a secret GITHUB_ACTIONS_PAT (PAT
//                                   fine-grained, permissão Actions=Read and write, só neste repo).
//   ?tarefa=disparar-backtest (POST) -> mesmo mecanismo de disparar-predicoes, dispara
//                                   backtest_kelly.yml (grid search + tuning + ROI simulado Kelly
//                                   dos 4 modelos) em vez de predict.yml. Também exige autenticação.
//   ?tarefa=jogador-perfil&player_id=X -> sync sob demanda de 1 jogador (valor de mercado
//                                   histórico, carreira, títulos, altura/pé/contrato/traits) via
//                                   /api/data/playerData do FotMob (endpoint POR JOGADOR, 1 chamada
//                                   externa só — rápido o bastante pro frontend chamar direto).
//                                   Backfill em massa é via script Python separado
//                                   (arquivos_do_claude/ingestao_fotmob_perfil_jogador.py).
//   ?tarefa=fotmob-liga-buscar&termo=X -> busca liga por nome no FotMob (apigw.fotmob.com/
//                                   searchapi/suggest), devolve nome/fotmob_league_id/país pra
//                                   preencher o formulário de importação. Não escreve nada.
//   ?tarefa=backfill-fotmob-liga&fotmob_league_id=X&temporada=AAAA[&nome=&pais=&confederacao=&tipo=]
//                               -> onboarding de liga NOVA a partir do FotMob: cria leagues +
//                                   liga_fonte_externa (sistema='fotmob') + ligas (cadastro,
//                                   pipeline_league_id já vinculado) na primeira chamada — nome/
//                                   pais/tipo só são obrigatórios nessa primeira vez, chamadas
//                                   seguintes (outra temporada da mesma liga) reaproveitam pelo
//                                   fotmob_league_id. Times resolvidos por nome ou criados na hora
//                                   (mesmo padrão de backfill-api-football — liga nova não tem
//                                   ambiguidade de crosswalk entre fontes). temporada no formato
//                                   do FotMob (ex: "2024" ou "2024/2025", igual ao site).
//   ?tarefa=partidas-fotmob&liga_id=X&temporada=AAAA[&limite=N]
//                               -> enriquece partidas JÁ EXISTENTES de uma liga/temporada (que
//                                   precisa ter crosswalk em liga_fonte_externa, sistema='fotmob')
//                                   com o detalhe completo do FotMob matchDetails: stats por time,
//                                   por jogador (+ dimensão players), mapa de chutes com xG/xGOT e
//                                   coordenadas, e contexto de estádio/clima. Idempotente via
//                                   match_source_ids (source='fotmob'). Custo alto por partida (1
//                                   chamada pesada + ~5 escritas) — processa no máximo
//                                   MAX_PARTIDAS_POR_CHAMADA_FOTMOB (15) por chamada, mesmo que
//                                   ?limite peça mais; o frontend (/ligas/:id) faz rounds sucessivos
//                                   pra completar o lote escolhido (20/50/100/200).
//   ?tarefa=importar-jogos-api-football&liga_id=X&temporada=AAAA
//   ?tarefa=importar-jogos-fotmob&liga_id=X&temporada=AAAA
//                               -> CRIAM os próprios jogos (data/placar/times) de uma temporada
//                                   que ainda não está em `matches`, pra uma liga já cadastrada no
//                                   pipeline — diferente de partidas-fotmob/sync-match-stats acima,
//                                   que só enriquecem jogo que já existe. Wrappers finos em cima de
//                                   tarefaBackfillApiFootball/tarefaBackfillFotmobLiga (resolvem o
//                                   id externo via liga_fonte_externa a partir do liga_id interno).
//                                   temporada sempre no formato ÚNICO de matches.season (ex:
//                                   "2024") — a versão fotmob converte automaticamente pro formato
//                                   "2024/2025" só na chamada externa, nunca no que é gravado.
//                                   Sem lote: 1 chamada externa já traz a temporada inteira.
//   ?tarefa=paper-carteira-criar (POST, auth) / -listar / -detalhe&carteira_id=X /
//   -apostar&carteira_id=X (POST, auth) / -resolver[&carteira_id=X] (POST, auth) /
//   -alternar-ativa&carteira_id=X (POST, auth) / -excluir&carteira_id=X (POST, auth) /
//   -rodar-todas (cron, sem auth)
//                               -> Carteira (Paper Trading): apostas simuladas com banca
//                                   PERSISTENTE sobre partidas AINDA NÃO disputadas (status=
//                                   scheduled) das ligas domésticas, usando odds ao vivo já
//                                   sincronizadas (odds_market, snapshot=pre_closing) e predições
//                                   já existentes pra essas partidas -- ver comentário detalhado
//                                   na seção "TAREFAS: Carteira (Paper Trading)" abaixo.
//
// Documentação detalhada de cada tarefa nos comentários das funções abaixo
// (mesma lógica que estava nos arquivos originais compute-elo.js/fit-calibration.js).

import { createClient } from '@supabase/supabase-js';
import { applyCors } from './_lib/cors.js';
import { gravarComDedupCruzado } from './_lib/dedupMatches.js';

function getSupabase() {
  return createClient(process.env.SUPABASE_URL, process.env.SUPABASE_SERVICE_ROLE_KEY);
}

const clamp = (p) => Math.min(Math.max(p, 1e-4), 1 - 1e-4);
const logit = (p) => Math.log(clamp(p) / (1 - clamp(p)));
const sigmoid = (x) => 1 / (1 + Math.exp(-x));

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

function* fatiar(array, tamanho) {
  for (let i = 0; i < array.length; i += tamanho) yield array.slice(i, i + tamanho);
}

async function buscarTudoPaginadoIn(ids, criarQuery) {
  const TAMANHO_PAGINA = 1000;
  const resultado = [];
  for (const lote of fatiar(ids, 200)) {
    let pagina = 0;
    while (true) {
      const { data, error } = await criarQuery(lote).range(pagina * TAMANHO_PAGINA, pagina * TAMANHO_PAGINA + TAMANHO_PAGINA - 1);
      if (error) throw error;
      resultado.push(...(data || []));
      if (!data || data.length < TAMANHO_PAGINA) break;
      pagina++;
    }
  }
  return resultado;
}

// ============================================================
// TAREFA: elo — Elo interno (não confundir com team_elo_external, o rating
// do ClubElo). Dois escopos: 'liga' (parte de 1500, sem depender de nada
// externo) e 'geral' (cross-liga, só se ajusta em Champions League, semeado
// da média do ClubElo quando existe). Fórmula: Elo clássico com multiplicador
// de diferença de gols (G=1 se |dif|<=1, 1.5 se dif=2, (11+dif)/8 se dif>=3),
// K=20, vantagem de casa=65. Roda em lotes por escopo (uma liga por vez, ou
// o geral) — processar tudo numa chamada só estourava os 60s do Vercel.
// ============================================================

const RATING_INICIAL = 1500;
const K_ELO = 20;
const VANTAGEM_CASA = 65;
const LIGA_CHAMPIONS_ID = 19;
const LIGAS_DOMESTICAS = [1, 4, 7, 10, 13, 16];

function multiplicadorDiferenca(diferenca) {
  const d = Math.abs(diferenca);
  if (d <= 1) return 1;
  if (d === 2) return 1.5;
  return (11 + d) / 8;
}

function atualizarElo(ratingMandante, ratingVisitante, golsMandante, golsVisitante) {
  const diferenca = golsMandante - golsVisitante;
  const resultadoMandante = diferenca > 0 ? 1 : diferenca < 0 ? 0 : 0.5;
  const esperadoMandante = 1 / (1 + Math.pow(10, -((ratingMandante + VANTAGEM_CASA) - ratingVisitante) / 400));
  const delta = K_ELO * multiplicadorDiferenca(diferenca) * (resultadoMandante - esperadoMandante);
  return { novoMandante: ratingMandante + delta, novoVisitante: ratingVisitante - delta };
}

async function regravarElo(supabase, linhasElo, linhasHistorico, filtroDelete) {
  const filtro = filtroDelete.league_id === null ? { escopo: filtroDelete.escopo } : filtroDelete;
  await supabase.from('team_elo_history').delete().match(filtro);
  await supabase.from('team_elo').delete().match(filtro);

  for (const lote of fatiar(linhasElo, 500)) {
    const { error } = await supabase.from('team_elo').upsert(lote, { onConflict: 'team_id,escopo,league_id' });
    if (error) throw error;
  }
  for (const lote of fatiar(linhasHistorico, 500)) {
    const { error } = await supabase.from('team_elo_history').insert(lote);
    if (error) throw error;
  }
}

async function eloProcessarLiga(supabase, ligaId) {
  const partidas = await buscarTudoPaginado(() =>
    supabase.from('matches').select('id, round, match_date, home_team_id, away_team_id, home_goals, away_goals')
      .eq('league_id', ligaId).eq('status', 'finished').not('home_goals', 'is', null).not('away_goals', 'is', null)
  );
  partidas.sort((a, b) => new Date(a.match_date) - new Date(b.match_date));

  const rating = {}, contagem = {};
  const historico = [];

  for (const p of partidas) {
    const antesMandante = rating[p.home_team_id] ?? RATING_INICIAL;
    const antesVisitante = rating[p.away_team_id] ?? RATING_INICIAL;
    const { novoMandante, novoVisitante } = atualizarElo(antesMandante, antesVisitante, p.home_goals, p.away_goals);
    rating[p.home_team_id] = novoMandante;
    rating[p.away_team_id] = novoVisitante;
    contagem[p.home_team_id] = (contagem[p.home_team_id] || 0) + 1;
    contagem[p.away_team_id] = (contagem[p.away_team_id] || 0) + 1;

    historico.push(
      { team_id: p.home_team_id, escopo: 'liga', league_id: ligaId, match_id: p.id, rodada: p.round, rating_antes: antesMandante, rating_depois: novoMandante, match_date: p.match_date },
      { team_id: p.away_team_id, escopo: 'liga', league_id: ligaId, match_id: p.id, rodada: p.round, rating_antes: antesVisitante, rating_depois: novoVisitante, match_date: p.match_date },
    );
  }

  const linhasElo = Object.entries(rating).map(([team_id, r]) => ({
    team_id: Number(team_id), escopo: 'liga', league_id: ligaId, rating: r, partidas: contagem[team_id], atualizado_em: new Date().toISOString(),
  }));

  await regravarElo(supabase, linhasElo, historico, { escopo: 'liga', league_id: ligaId });
  return { escopo: 'liga', league_id: ligaId, partidas_processadas: partidas.length, times_com_elo: linhasElo.length };
}

async function eloProcessarGeral(supabase) {
  const [partidasChampions, seedsExternas, partidasDomesticas] = await Promise.all([
    buscarTudoPaginado(() =>
      supabase.from('matches').select('id, round, match_date, home_team_id, away_team_id, home_goals, away_goals')
        .eq('league_id', LIGA_CHAMPIONS_ID).eq('status', 'finished').not('home_goals', 'is', null).not('away_goals', 'is', null)
    ),
    buscarTudoPaginado(() => supabase.from('team_elo_external').select('team_id, elo, valido_ate').order('valido_ate', { ascending: false })),
    buscarTudoPaginado(() => supabase.from('matches').select('home_team_id, away_team_id').in('league_id', LIGAS_DOMESTICAS).eq('status', 'finished')),
  ]);
  partidasChampions.sort((a, b) => new Date(a.match_date) - new Date(b.match_date));

  const seedPorTime = {};
  for (const s of seedsExternas) if (!(s.team_id in seedPorTime)) seedPorTime[s.team_id] = Number(s.elo);

  const rating = {}, contagem = {};
  const historico = [];

  for (const p of partidasChampions) {
    if (!(p.home_team_id in rating)) { rating[p.home_team_id] = seedPorTime[p.home_team_id] ?? RATING_INICIAL; contagem[p.home_team_id] = 0; }
    if (!(p.away_team_id in rating)) { rating[p.away_team_id] = seedPorTime[p.away_team_id] ?? RATING_INICIAL; contagem[p.away_team_id] = 0; }
    const antesMandante = rating[p.home_team_id];
    const antesVisitante = rating[p.away_team_id];
    const { novoMandante, novoVisitante } = atualizarElo(antesMandante, antesVisitante, p.home_goals, p.away_goals);
    rating[p.home_team_id] = novoMandante;
    rating[p.away_team_id] = novoVisitante;
    contagem[p.home_team_id]++;
    contagem[p.away_team_id]++;

    historico.push(
      { team_id: p.home_team_id, escopo: 'geral', league_id: null, match_id: p.id, rodada: p.round, rating_antes: antesMandante, rating_depois: novoMandante, match_date: p.match_date },
      { team_id: p.away_team_id, escopo: 'geral', league_id: null, match_id: p.id, rodada: p.round, rating_antes: antesVisitante, rating_depois: novoVisitante, match_date: p.match_date },
    );
  }

  for (const p of partidasDomesticas) {
    for (const timeId of [p.home_team_id, p.away_team_id]) {
      if (!(timeId in rating)) { rating[timeId] = seedPorTime[timeId] ?? RATING_INICIAL; contagem[timeId] = 0; }
    }
  }

  const linhasElo = Object.entries(rating).map(([team_id, r]) => ({
    team_id: Number(team_id), escopo: 'geral', league_id: null, rating: r, partidas: contagem[team_id] || 0, atualizado_em: new Date().toISOString(),
  }));

  await regravarElo(supabase, linhasElo, historico, { escopo: 'geral', league_id: null });
  return { escopo: 'geral', partidas_processadas: partidasChampions.length, times_com_elo: linhasElo.length };
}

// ============================================================
// TAREFA: player-elo — rating Elo-like por JOGADOR (não confundir com o
// team_elo acima), a partir de match_player_stats_fotmob (nota do FotMob +
// gols/assistências/xG/xA/chances criadas). Ancorado na nota do próprio
// FotMob (0-10, holística — já pondera posição/minutos implicitamente
// dentro do próprio modelo deles), com pequenos bônus/penalidades por
// contribuição direta de gol e qualidade de finalização/criação.
//
// IMPORTANTE — pesos são um CHUTE INICIAL, mesmo status "não calibrado"
// já documentado pro decaimento temporal (XI) do Dixon-Coles: dá pra rodar
// hoje e já ter um rating razoável, mas calibrar os pesos de verdade (ex:
// achar a combinação que melhor prevê o resultado real de partidas quando
// usado como ajuste de força de time) é um passo futuro, não feito ainda.
//
// Diferente do Elo de TIME (que reprocessa a liga inteira do zero a cada
// chamada, delete-e-regrava por escopo): rating de jogador é GLOBAL
// (cross-competição — um jogador pode jogar liga doméstica e Libertadores
// na mesma janela) e ACUMULATIVO — cada chamada só processa partidas que
// ainda não têm linha em player_rating_history, em ordem cronológica,
// continuando de onde a chamada anterior parou (rating atual persiste em
// player_ratings). Isso evita o timeout que forçou o Elo de time a ser
// batelado por liga — aqui o lote é por ?limite=N partidas (padrão 300).
// ============================================================

const PLAYER_RATING_INICIAL = 1500;

// Defaults usados quando model_config não tem linha pro player_elo_v1 —
// mesmos valores da seed da migration. Cada parâmetro tem um peso E uma
// flag ativo_* (pedido do usuário: poder ligar/desligar a importância de
// cada parâmetro e comparar configurações), editáveis via ?tarefa=config-set
// e pela seção de configuração em /modelos.
const PLAYER_ELO_CONFIG_PADRAO = {
  k: 20,
  nota_neutra: 6.8, // aproximação da nota "neutra" mais comum do FotMob
  minutos_minimos: 20, // cameo curto demais não entra (ruído de amostra)
  usar_nota_fotmob: true,
  peso_gols: 0.3,
  peso_assistencias: 0.2,
  peso_finalizacao: 0.3,
  peso_criacao: 0.2,
  ativo_gols: true,
  ativo_assistencias: true,
  ativo_finalizacao: true,
  ativo_criacao: true,
};

const clampNum = (v, min, max) => Math.max(min, Math.min(max, v));

async function lerConfigModelo(supabase, modelName, padrao) {
  const { data } = await supabase.from('model_config').select('config').eq('model_name', modelName).maybeSingle();
  return { ...padrao, ...(data?.config || {}) };
}

// Índice de desempenho na partida (escala parecida com a nota do FotMob,
// ~0-10) — ver comentário da tarefa acima sobre os pesos serem um chute
// inicial. `xg` cai pro próprio número de gols quando não existe (ligas sem
// xG do FotMob nessa partida), o que zera o bônus/penalidade de finalização
// nesse caso (não favorece nem penaliza por falta de dado).
function indicePartidaJogador(s, cfg) {
  const nota = cfg.usar_nota_fotmob && s.rating != null ? Number(s.rating) : cfg.nota_neutra;
  const gols = Number(s.goals) || 0;
  const assistencias = Number(s.assists) || 0;
  const xg = s.xg != null ? Number(s.xg) : gols;
  const xa = Number(s.xa) || 0;
  const chancesCriadas = Number(s.chances_created) || 0;

  const bonusGols = cfg.ativo_gols ? Math.min(gols, 3) * cfg.peso_gols : 0;
  const bonusAssistencias = cfg.ativo_assistencias ? Math.min(assistencias, 3) * cfg.peso_assistencias : 0;
  const bonusFinalizacao = cfg.ativo_finalizacao ? clampNum(gols - xg, -1, 1) * cfg.peso_finalizacao : 0;
  const bonusCriacao = cfg.ativo_criacao ? Math.min(xa + chancesCriadas * 0.05, 1) * cfg.peso_criacao : 0;

  return nota + bonusGols + bonusAssistencias + bonusFinalizacao + bonusCriacao;
}

function atualizarRatingJogador(ratingAtual, indice, cfg) {
  return ratingAtual + cfg.k * ((indice - cfg.nota_neutra) / 3);
}

async function tarefaPlayerElo(supabase, limite) {
  const LIMITE_PARTIDAS = limite || 200;

  // A 1ª versão carregava TODAS as ~200k linhas de match_player_stats_fotmob
  // por chamada (200 páginas REST sequenciais) — FUNCTION_INVOCATION_TIMEOUT
  // garantido, mesma classe do timeout já visto no Elo de time. Reestruturado
  // pra cursor: como o processamento é estritamente cronológico (ordem
  // determinística por (match_date, match_id)), a ÚLTIMA linha inserida em
  // player_rating_history identifica o ponto de retomada — só as partidas
  // depois dela entram, e as estatísticas são buscadas APENAS pros ids do
  // lote (.in), nunca a tabela inteira. Implicação documentada: se uma
  // partida ANTIGA for sincronizada no FotMob depois do cursor já ter
  // passado por ela, ela não entra sozinha — reprocesso completo = truncar
  // player_ratings/player_rating_history e re-rodar do zero.
  const [{ data: ultimaLinha }, partidasFotmob, ratingsAtuais, cfg] = await Promise.all([
    supabase.from('player_rating_history').select('match_id').order('id', { ascending: false }).limit(1).maybeSingle(),
    buscarTudoPaginado(() => supabase.from('match_source_ids').select('match_id').eq('source', 'fotmob')),
    buscarTudoPaginado(() => supabase.from('player_ratings').select('player_id, rating, n_partidas')),
    lerConfigModelo(supabase, 'player_elo_v1', PLAYER_ELO_CONFIG_PADRAO),
  ]);

  const idsFotmob = partidasFotmob.map(r => r.match_id);
  const dataPorMatch = {};
  for (const loteIds of fatiar(idsFotmob, 200)) {
    const { data, error } = await supabase.from('matches').select('id, match_date').in('id', loteIds);
    if (error) throw error;
    (data || []).forEach(p => { dataPorMatch[p.id] = p.match_date; });
  }

  const ordenadas = idsFotmob
    .filter(id => dataPorMatch[id])
    .sort((a, b) => (new Date(dataPorMatch[a]) - new Date(dataPorMatch[b])) || (a - b));

  let inicio = 0;
  if (ultimaLinha?.match_id != null) {
    const posCursor = ordenadas.indexOf(ultimaLinha.match_id);
    if (posCursor === -1) throw new Error(`Cursor aponta pra match_id ${ultimaLinha.match_id}, que não está na lista de partidas FotMob — estado inconsistente, investigar antes de continuar.`);
    inicio = posCursor + 1;
  }

  const idsLote = ordenadas.slice(inicio, inicio + LIMITE_PARTIDAS);
  if (idsLote.length === 0) {
    return { mensagem: 'Nenhuma partida pendente.', partidas_processadas: 0, partidas_restantes: 0 };
  }

  const stats = [];
  for (const loteIds of fatiar(idsLote, 100)) {
    const linhas = await buscarTudoPaginado(() =>
      supabase.from('match_player_stats_fotmob')
        .select('match_id, player_id, rating, minutes_played, goals, assists, xg, xa, chances_created')
        .in('match_id', loteIds).not('player_id', 'is', null)
    );
    stats.push(...linhas);
  }

  const posicaoNoLote = new Map(idsLote.map((id, i) => [id, i]));
  const lote = stats
    .filter(s => (s.minutes_played ?? 0) >= cfg.minutos_minimos)
    .sort((a, b) => posicaoNoLote.get(a.match_id) - posicaoNoLote.get(b.match_id));

  const ratingPorJogador = {}, contagemPorJogador = {};
  ratingsAtuais.forEach(r => { ratingPorJogador[r.player_id] = Number(r.rating); contagemPorJogador[r.player_id] = r.n_partidas; });

  const historico = [];
  for (const s of lote) {
    const antes = ratingPorJogador[s.player_id] ?? PLAYER_RATING_INICIAL;
    const indice = indicePartidaJogador(s, cfg);
    const depois = atualizarRatingJogador(antes, indice, cfg);
    ratingPorJogador[s.player_id] = depois;
    contagemPorJogador[s.player_id] = (contagemPorJogador[s.player_id] || 0) + 1;
    historico.push({
      player_id: s.player_id, match_id: s.match_id, rating_antes: antes, rating_depois: depois,
      indice_partida: indice, fotmob_rating: s.rating, minutes_played: s.minutes_played,
    });
  }

  // O cursor efetivo é a última linha gravada em player_rating_history, ou
  // seja, a última partida do lote COM dado válido. Partidas sem nenhum
  // jogador válido no FIM do lote ficam depois do cursor e são re-escaneadas
  // na chamada seguinte junto com partidas novas — redundância barata, sem
  // risco de loop. O único caso degenerado é o lote INTEIRO vir vazio (cursor
  // não anda): reportado pro operador em vez de fingir progresso.
  if (historico.length === 0) {
    return { mensagem: 'Lote inteiro sem estatística de jogador válida — cursor não avançou, rode com ?limite maior pra pular o trecho vazio.', partidas_no_lote: idsLote.length, partidas_restantes: ordenadas.length - inicio - idsLote.length };
  }

  const jogadoresTocados = [...new Set(historico.map(h => h.player_id))];
  const linhasRating = jogadoresTocados.map(pid => ({
    player_id: pid, rating: ratingPorJogador[pid], n_partidas: contagemPorJogador[pid], updated_at: new Date().toISOString(),
  }));

  for (const l of fatiar(linhasRating, 500)) {
    const { error } = await supabase.from('player_ratings').upsert(l, { onConflict: 'player_id' });
    if (error) throw error;
  }
  for (const l of fatiar(historico, 500)) {
    const { error } = await supabase.from('player_rating_history').upsert(l, { onConflict: 'player_id,match_id' });
    if (error) throw error;
  }

  return {
    partidas_processadas: idsLote.length,
    linhas_processadas: historico.length,
    jogadores_atualizados: linhasRating.length,
    partidas_restantes: ordenadas.length - inicio - idsLote.length,
    config_usada: cfg,
  };
}

// Apaga TODO o estado do rating de jogador (player_ratings +
// player_rating_history) pra reprocessar do zero — necessário depois de
// mudar a configuração de pesos (mudança de config só vale pra partidas
// novas; o histórico já processado fica com os pesos antigos até um reset).
// Custo do reprocesso: ~16-20 chamadas de ?tarefa=player-elo em sequência.
async function tarefaPlayerEloReset(supabase) {
  const { error: e1 } = await supabase.from('player_rating_history').delete().gte('id', 0);
  if (e1) throw e1;
  const { error: e2 } = await supabase.from('player_ratings').delete().gte('player_id', 0);
  if (e2) throw e2;
  return { mensagem: 'player_ratings e player_rating_history zerados — rode ?tarefa=player-elo repetidamente pra reprocessar com a config atual.' };
}

// Lê/grava model_config (pesos, flags de ativar/desativar parâmetro,
// metodologia) — a UI de /modelos usa isso pra editar a configuração sem
// precisar de acesso de escrita direto no banco (RLS só permite leitura
// pública; escrita passa por aqui, com service role).
async function tarefaConfigGet(supabase, modelName) {
  if (modelName === 'player_elo_v1' || !modelName) {
    const cfg = await lerConfigModelo(supabase, 'player_elo_v1', PLAYER_ELO_CONFIG_PADRAO);
    return { model_name: 'player_elo_v1', config: cfg, padrao: PLAYER_ELO_CONFIG_PADRAO };
  }
  const { data } = await supabase.from('model_config').select('model_name, config, updated_at').eq('model_name', modelName).maybeSingle();
  return data || { model_name: modelName, config: null };
}

async function tarefaConfigSet(supabase, modelName, configBody) {
  if (!modelName || !configBody || typeof configBody !== 'object' || Array.isArray(configBody)) {
    return { error: 'Envie model_name na query e um objeto JSON de config no corpo (POST).' };
  }
  // Merge por cima da config atual (não substitui o objeto inteiro): permite
  // a UI mandar só o campo alterado sem apagar o resto.
  const atual = await lerConfigModelo(supabase, modelName, modelName === 'player_elo_v1' ? PLAYER_ELO_CONFIG_PADRAO : {});
  const nova = { ...atual, ...configBody };
  const { error } = await supabase.from('model_config').upsert(
    { model_name: modelName, config: nova, updated_at: new Date().toISOString() },
    { onConflict: 'model_name' },
  );
  if (error) throw error;
  return { model_name: modelName, config: nova };
}

// ============================================================
// TAREFA: disparar-predicoes — dispara o workflow_dispatch do predict.yml
// (Model Benchmarking) direto do frontend, autenticado.
//
// Diferente de todas as outras tarefas deste arquivo (chamadas manualmente
// via curl, nunca pelo frontend): esta é clicável em /model-benchmarking,
// então PRECISA verificar a sessão de verdade no servidor -- o
// ProtectedRoute do frontend só esconde o botão de quem não está logado,
// não impede uma chamada direta à API. `verificarUsuarioLogado` usa
// `supabase.auth.getUser(token)`, que valida o JWT contra o Supabase Auth
// (funciona com qualquer client, não precisa ser o client com a anon key).
// ============================================================

const GITHUB_REPO_OWNER = 'JBatistaCosta';
const GITHUB_REPO_NAME = 'quant-predictor';
const GITHUB_WORKFLOW_FILE = 'predict.yml';
const GITHUB_WORKFLOW_FILE_BACKTEST = 'backtest_kelly.yml';
const GITHUB_WORKFLOW_FILE_CUSTOM_TREINO = 'treinar_modelo_custom.yml';
const GITHUB_WORKFLOW_FILE_CUSTOM_TREINO_WF = 'treinar_modelo_custom_wf.yml';
const GITHUB_WORKFLOW_FILE_ESTIMAR_PARTIDA_CUSTOM = 'estimar_partida_custom.yml';
const GITHUB_WORKFLOW_FILE_ATUALIZAR_STATS = 'atualizar_stats.yml';

async function verificarUsuarioLogado(supabase, authHeader) {
  const token = authHeader?.startsWith('Bearer ') ? authHeader.slice('Bearer '.length).trim() : null;
  if (!token) return null;
  const { data, error } = await supabase.auth.getUser(token);
  if (error || !data?.user) return null;
  return data.user;
}

async function dispararWorkflow(supabase, authHeader, arquivoWorkflow, inputs = {}) {
  const usuario = await verificarUsuarioLogado(supabase, authHeader);
  if (!usuario) return { status: 401, error: 'Não autenticado -- faça login antes de disparar.' };

  const pat = process.env.GITHUB_ACTIONS_PAT;
  if (!pat) return { status: 500, error: 'GITHUB_ACTIONS_PAT não configurada -- ver comentário no topo deste arquivo.' };

  // inputs só é incluído no corpo quando há valores (workflow_dispatch
  // padrão sem inputs funciona com body={ ref: 'main' } sem a chave inputs).
  const bodyDispatch = Object.keys(inputs).length > 0
    ? { ref: 'main', inputs }
    : { ref: 'main' };

  const resposta = await fetch(
    `https://api.github.com/repos/${GITHUB_REPO_OWNER}/${GITHUB_REPO_NAME}/actions/workflows/${arquivoWorkflow}/dispatches`,
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${pat}`,
        Accept: 'application/vnd.github+json',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(bodyDispatch),
    },
  );

  // workflow_dispatch bem-sucedido devolve 204 sem corpo -- não tem run_id
  // na resposta (a API do GitHub não devolve isso de forma síncrona), então
  // o frontend só confirma "disparado", sem acompanhar o progresso aqui.
  if (resposta.status !== 204) {
    const corpo = await resposta.text();
    return { status: 502, error: `GitHub Actions recusou o disparo (HTTP ${resposta.status}): ${corpo.slice(0, 300)}` };
  }

  return { status: 200, disparado_por: usuario.email, disparado_em: new Date().toISOString() };
}

function tarefaDispararPredicoes(supabase, authHeader) {
  return dispararWorkflow(supabase, authHeader, GITHUB_WORKFLOW_FILE);
}

// Mesmo mecanismo de disparo/autenticação de disparar-predicoes, workflow
// diferente (backtest_kelly.yml -- grid search + tuning + simulação Kelly,
// bem mais caro em CPU, por isso é sempre manual, nunca no cron).
function tarefaDispararBacktest(supabase, authHeader) {
  return dispararWorkflow(supabase, authHeader, GITHUB_WORKFLOW_FILE_BACKTEST);
}

// Dispara atualizar_stats.yml (varredura de todas as ligas com cobertura FotMob)
// com os mesmos inputs que o workflow já suporta via workflow_dispatch.
async function tarefaDispararAtualizarStats(supabase, authHeader, { liga_id, limite, modo, forcar } = {}) {
  const inputs = {};
  if (liga_id) inputs.league_id = String(liga_id);
  if (limite) inputs.limite = String(limite);
  if (modo && modo !== 'tudo') inputs.modo = modo;
  if (forcar === 'true' || forcar === true) inputs.forcar = 'true';
  return dispararWorkflow(supabase, authHeader, GITHUB_WORKFLOW_FILE_ATUALIZAR_STATS, inputs);
}

// ============================================================
// TAREFAS: Painel de Treino Customizado
// Gerenciam configurações na tabela custom_model_configs e disparam o
// workflow treinar_modelo_custom.yml. Chamadas pelo frontend em
// src/pages/TreinoCustom.jsx. Exigem autenticação (mesmo mecanismo de
// disparar-predicoes / disparar-backtest).
// ============================================================

// Salva (ou atualiza) uma configuração de modelo customizado.
// Body esperado: { name, algorithm, features[], target?, hyperparameters?, notes?, mode?, algorithms? }
// mode: 'simples' (padrão) ou 'walk_forward_cv'. No modo WF, `algorithms` é um array de strings.
// Se `id` está presente no body, faz upsert; senão, cria nova linha.
async function tarefaSalvarConfigCustom(supabase, authHeader, body) {
  const usuario = await verificarUsuarioLogado(supabase, authHeader);
  if (!usuario) return { status: 401, error: 'Não autenticado.' };

  const { id, name, algorithm, features, target, hyperparameters, notes, mode, algorithms, todas_ligas, league_ids, seasons, stacking_groups } = body || {};
  if (!name || !Array.isArray(features) || features.length === 0) {
    return { status: 400, error: 'Campos obrigatórios: name (texto), features (array não-vazio).' };
  }
  if (league_ids !== undefined && league_ids !== null && !Array.isArray(league_ids)) {
    return { status: 400, error: 'league_ids deve ser um array de IDs de leagues (ou null/omitido).' };
  }
  if (seasons !== undefined && seasons !== null && !Array.isArray(seasons)) {
    return { status: 400, error: 'seasons deve ser um array de temporadas (ou null/omitido).' };
  }
  if (stacking_groups !== undefined && stacking_groups !== null && !Array.isArray(stacking_groups)) {
    return { status: 400, error: 'stacking_groups deve ser um array (ou null/omitido).' };
  }

  const modoFinal = mode === 'walk_forward_cv' ? 'walk_forward_cv' : 'simples';

  if (modoFinal === 'simples') {
    if (!algorithm) return { status: 400, error: 'Campo algorithm é obrigatório no modo simples.' };
  } else {
    if (!Array.isArray(algorithms) || algorithms.length === 0) {
      return { status: 400, error: 'No modo walk_forward_cv, selecione ao menos 1 algoritmo em algorithms[].' };
    }
  }

  const algorithmsFinal = modoFinal === 'walk_forward_cv' ? (algorithms || []) : [];

  // Cada grupo: { name: string, algorithms: string[] }. Só faz sentido no
  // modo walk_forward_cv, e cada grupo precisa ter >=2 algoritmos que
  // também estejam em algorithmsFinal (senão o meta-modelo não teria o que
  // combinar). Grupos inválidos são descartados aqui em vez de rejeitar a
  // request inteira -- o frontend já filtra isso na UI, mas não custa nada
  // ser defensivo contra uma config salva por outra via/versão antiga.
  const stackingGroupsFinal = modoFinal === 'walk_forward_cv' && Array.isArray(stacking_groups)
    ? stacking_groups
        .filter((g) => g && typeof g.name === 'string' && g.name.trim() && Array.isArray(g.algorithms))
        .map((g) => ({
          name: g.name.trim(),
          algorithms: [...new Set(g.algorithms.filter((a) => algorithmsFinal.includes(a)))],
        }))
        .filter((g) => g.algorithms.length >= 2)
    : [];

  const camposComuns = {
    name,
    algorithm: algorithm || null,
    features,
    target: target || '1x2',
    hyperparameters: hyperparameters || null,
    notes: notes || null,
    mode: modoFinal,
    algorithms: algorithmsFinal,
    todas_ligas: !!todas_ligas,
    league_ids: (Array.isArray(league_ids) && league_ids.length > 0) ? league_ids : null,
    seasons: (Array.isArray(seasons) && seasons.length > 0) ? seasons : null,
    stacking_groups: stackingGroupsFinal,
  };

  let resultado;
  if (id) {
    // Qualquer edição de uma config existente invalida os artefatos
    // persistidos (model_artifacts) -- eles foram treinados com o estado
    // ANTERIOR de features/algoritmo/hiperparâmetros/escopo, e continuar
    // servindo-os pra estimar-partida-custom depois de uma edição seria uma
    // previsão silenciosamente errada (modelo de outra configuração). O
    // próximo treino bem-sucedido já os repopula automaticamente.
    const { data, error } = await supabase
      .from('custom_model_configs')
      .update({ ...camposComuns, model_artifacts: {} })
      .eq('id', id)
      .select()
      .single();
    if (error) throw error;
    resultado = data;
  } else {
    const { data, error } = await supabase
      .from('custom_model_configs')
      .insert({ ...camposComuns, status: 'rascunho' })
      .select()
      .single();
    if (error) throw error;
    resultado = data;
  }

  return { status: 200, config: resultado };
}

// Lista todas as configurações de modelos customizados (mais recente primeiro).
async function tarefaListarConfigsCustom(supabase) {
  const { data, error } = await supabase
    .from('custom_model_configs')
    .select('id, name, algorithm, algorithms, features, target, status, metrics, model_key, model_artifacts, notes, mode, todas_ligas, league_ids, seasons, stacking_groups, created_at, trained_at, error_message')
    .order('created_at', { ascending: false });
  if (error) throw error;
  return { status: 200, configs: data || [] };
}

// Dispara o workflow de treino (simples ou walk_forward_cv) com o config_id como input.
async function tarefaDispararTreinoCustom(supabase, authHeader, configId) {
  if (!configId) return { status: 400, error: 'Parâmetro config_id é obrigatório.' };

  // Verifica que o config existe antes de disparar.
  const { data: cfg, error: cfgErr } = await supabase
    .from('custom_model_configs')
    .select('id, status, mode')
    .eq('id', configId)
    .maybeSingle();
  if (cfgErr) throw cfgErr;
  if (!cfg) return { status: 404, error: `Config id=${configId} não encontrada em custom_model_configs.` };
  if (cfg.status === 'treinando') return { status: 409, error: 'Esse modelo já está em treinamento. Aguarde.' };

  // Seleciona o workflow correto baseado no mode da config.
  const workflowFile = cfg.mode === 'walk_forward_cv'
    ? GITHUB_WORKFLOW_FILE_CUSTOM_TREINO_WF
    : GITHUB_WORKFLOW_FILE_CUSTOM_TREINO;

  const resultado = await dispararWorkflow(supabase, authHeader, workflowFile, { config_id: configId });
  if (resultado.status === 200) {
    await supabase
      .from('custom_model_configs')
      .update({ status: 'aguardando_treino' })
      .eq('id', configId);
  }
  return resultado;
}

// Dispara a estimativa sob demanda de UMA partida com um modelo customizado
// (retreina do zero em todo o histórico do escopo da config, ver
// scripts/estimar_partida_custom.py). Body: { config_id, match_id, algorithm }.
// `algorithm` é um dos algorithms/algorithm da config, ou "stacking:{nome}"
// pra um grupo de custom_model_configs.stacking_groups. Chamado pelo botão
// "Estimar com modelo personalizado" em AnaliseEstatisticaJogo.jsx -- o
// frontend faz o polling do resultado direto via cliente Supabase (a
// tabela tem policy de leitura pública), então não existe uma tarefa de
// status separada aqui.
async function tarefaEstimarPartidaCustom(supabase, authHeader, body) {
  const usuario = await verificarUsuarioLogado(supabase, authHeader);
  if (!usuario) return { status: 401, error: 'Não autenticado.' };

  const { config_id: configId, match_id: matchId, algorithm } = body || {};
  if (!configId || !matchId || !algorithm) {
    return { status: 400, error: 'Campos obrigatórios: config_id, match_id, algorithm.' };
  }

  const { data: cfg, error: cfgErr } = await supabase
    .from('custom_model_configs')
    .select('id, mode, algorithm, algorithms, stacking_groups, model_artifacts')
    .eq('id', configId)
    .maybeSingle();
  if (cfgErr) throw cfgErr;
  if (!cfg) return { status: 404, error: `Config id=${configId} não encontrada em custom_model_configs.` };

  // Valida que o algorithm pedido de fato existe nessa config, pra não
  // disparar um workflow que sabidamente vai falhar.
  const algoritmosValidos = cfg.mode === 'walk_forward_cv' ? (cfg.algorithms || []) : (cfg.algorithm ? [cfg.algorithm] : []);
  const ehStacking = typeof algorithm === 'string' && algorithm.startsWith('stacking:');
  if (ehStacking) {
    const nomeGrupo = algorithm.slice('stacking:'.length);
    const grupo = (cfg.stacking_groups || []).find((g) => g.name === nomeGrupo);
    if (!grupo) return { status: 400, error: `Grupo de stacking "${nomeGrupo}" não existe nessa configuração.` };
  } else if (!algoritmosValidos.includes(algorithm)) {
    return { status: 400, error: `Algoritmo "${algorithm}" não faz parte dessa configuração.` };
  }

  // A estimativa sob demanda nunca treina -- só aplica um modelo já
  // treinado e persistido (ver scripts/model_artifacts.py). Sem artefato
  // pro algoritmo/grupo pedido, nem dispara o workflow (ele ia falhar do
  // mesmo jeito lá dentro, só que minutos depois).
  if (!(cfg.model_artifacts || {})[algorithm]) {
    return {
      status: 400,
      error: `"${algorithm}" ainda não tem um modelo treinado e persistido nessa configuração -- treine (ou retreine) no painel Treino Customizado primeiro.`,
    };
  }

  const { data: match } = await supabase.from('matches').select('id').eq('id', matchId).maybeSingle();
  if (!match) return { status: 404, error: `Partida id=${matchId} não encontrada.` };

  const { data: request, error: insertErr } = await supabase
    .from('custom_model_ondemand_predictions')
    .insert({ config_id: configId, match_id: matchId, algorithm, status: 'pendente', requested_by: usuario.id })
    .select('id')
    .single();
  if (insertErr) throw insertErr;

  const resultado = await dispararWorkflow(supabase, authHeader, GITHUB_WORKFLOW_FILE_ESTIMAR_PARTIDA_CUSTOM, { request_id: request.id });
  if (resultado.status !== 200) {
    // Disparo falhou (ex.: sem GITHUB_ACTIONS_PAT) -- marca erro em vez de
    // deixar a requisição presa em "pendente" pro frontend ficar pollando
    // pra sempre sem nunca receber uma resposta final.
    await supabase
      .from('custom_model_ondemand_predictions')
      .update({ status: 'erro', error_message: resultado.error || 'Falha ao disparar workflow.' })
      .eq('id', request.id);
    return resultado;
  }
  return { status: 200, request_id: request.id };
}

// ============================================================
// TAREFAS: Gestão de configs customizadas (excluir / copiar / cancelar / resetar)
// ============================================================

async function tarefaExcluirConfigCustom(supabase, authHeader, configId) {
  if (!configId) return { status: 400, error: 'Parâmetro config_id é obrigatório.' };
  const usuario = await verificarUsuarioLogado(supabase, authHeader);
  if (!usuario) return { status: 401, error: 'Não autenticado.' };

  const { data: cfg } = await supabase
    .from('custom_model_configs').select('id, status, name').eq('id', configId).maybeSingle();
  if (!cfg) return { status: 404, error: 'Configuração não encontrada.' };
  if (cfg.status === 'treinando') return { status: 409, error: 'Não é possível excluir um modelo em treinamento. Pare primeiro.' };

  const { error } = await supabase.from('custom_model_configs').delete().eq('id', configId);
  if (error) throw error;
  return { status: 200, excluido: configId, name: cfg.name };
}

async function tarefaCopiarConfigCustom(supabase, authHeader, configId) {
  if (!configId) return { status: 400, error: 'Parâmetro config_id é obrigatório.' };
  const usuario = await verificarUsuarioLogado(supabase, authHeader);
  if (!usuario) return { status: 401, error: 'Não autenticado.' };

  const { data: cfg } = await supabase
    .from('custom_model_configs')
    .select('name, algorithm, algorithms, features, target, hyperparameters, notes, mode, todas_ligas, league_ids, seasons, stacking_groups')
    .eq('id', configId).maybeSingle();
  if (!cfg) return { status: 404, error: 'Configuração não encontrada.' };

  const { data: copia, error } = await supabase
    .from('custom_model_configs')
    .insert({
      name: `[cópia] ${cfg.name}`,
      algorithm: cfg.algorithm,
      algorithms: cfg.algorithms || [],
      features: cfg.features,
      target: cfg.target,
      hyperparameters: cfg.hyperparameters,
      notes: cfg.notes,
      mode: cfg.mode || 'simples',
      todas_ligas: !!cfg.todas_ligas,
      league_ids: cfg.league_ids || null,
      seasons: cfg.seasons || null,
      stacking_groups: cfg.stacking_groups || [],
      status: 'rascunho',
    })
    .select().single();
  if (error) throw error;
  return { status: 200, config: copia };
}

// Reverte aguardando_treino/treinando → rascunho (o workflow pode ainda terminar
// e sobrescrever — mas o usuário sinaliza intenção de cancelar).
async function tarefaCancelarTreinoCustom(supabase, authHeader, configId) {
  if (!configId) return { status: 400, error: 'Parâmetro config_id é obrigatório.' };
  const usuario = await verificarUsuarioLogado(supabase, authHeader);
  if (!usuario) return { status: 401, error: 'Não autenticado.' };

  const { error } = await supabase.from('custom_model_configs')
    .update({ status: 'rascunho', error_message: null })
    .eq('id', configId)
    .in('status', ['aguardando_treino', 'treinando']);
  if (error) throw error;
  return { status: 200, mensagem: 'Treino cancelado. Modelo revertido para rascunho.' };
}

// Limpa métricas, erro e datas — volta a rascunho sem re-treinar.
async function tarefaResetarConfigCustom(supabase, authHeader, configId) {
  if (!configId) return { status: 400, error: 'Parâmetro config_id é obrigatório.' };
  const usuario = await verificarUsuarioLogado(supabase, authHeader);
  if (!usuario) return { status: 401, error: 'Não autenticado.' };

  const { data: cfg } = await supabase
    .from('custom_model_configs').select('id, status').eq('id', configId).maybeSingle();
  if (!cfg) return { status: 404, error: 'Configuração não encontrada.' };
  if (cfg.status === 'treinando' || cfg.status === 'aguardando_treino')
    return { status: 409, error: 'Não é possível resetar um modelo em treinamento. Pare primeiro.' };

  const { error } = await supabase.from('custom_model_configs')
    .update({ status: 'rascunho', metrics: null, error_message: null, trained_at: null, model_key: null, model_artifacts: {} })
    .eq('id', configId);
  if (error) throw error;
  return { status: 200, mensagem: 'Modelo resetado para rascunho.' };
}

// ============================================================
// TAREFA: relatorio-teste — lista paginada de partidas da fase de teste
// com todas as probabilidades estimadas pelos modelos e resultado real.
// Suporta tanto o modo simples (model_name = config.name) quanto o WF
// (model_names = metrics.model_names[]).
// GET ?tarefa=relatorio-teste&config_id=UUID[&pagina=N]
// ============================================================

async function tarefaRelatorioTeste(supabase, configId, pagina) {
  if (!configId) return { status: 400, error: 'config_id é obrigatório.' };

  const pgNum = Math.max(0, parseInt(pagina || '0', 10));
  const POR_PAGINA = 50;

  const { data: cfg, error: cfgErr } = await supabase
    .from('custom_model_configs')
    .select('id, name, metrics, target, mode')
    .eq('id', configId)
    .maybeSingle();
  if (cfgErr) throw cfgErr;
  if (!cfg) return { status: 404, error: 'Config não encontrada.' };

  const metrics = cfg.metrics || {};
  const modelNames = (metrics.model_names?.length) ? metrics.model_names : [cfg.name];
  const refModel = modelNames[0];

  // Busca todos os match_ids do modelo de referência (1 modelo = mesmas partidas).
  // buscarTudoPaginado evita corte silencioso em 1000 linhas do PostgREST.
  const todasLinhas = await buscarTudoPaginado(() =>
    supabase
      .from('model_predictions')
      .select('match_id')
      .eq('model_name', refModel)
      .order('match_id', { ascending: false })
  );

  // Deduplica match_ids mantendo a ordem
  const seenIds = new Set();
  const todosMatchIds = [];
  for (const r of todasLinhas) {
    if (!seenIds.has(r.match_id)) {
      seenIds.add(r.match_id);
      todosMatchIds.push(r.match_id);
    }
  }

  const total = todosMatchIds.length;
  const pageIds = todosMatchIds.slice(pgNum * POR_PAGINA, (pgNum + 1) * POR_PAGINA);

  if (pageIds.length === 0) {
    return { status: 200, jogos: [], pagina: pgNum, por_pagina: POR_PAGINA, total, model_names: modelNames, target: cfg.target };
  }

  // Map target → odds_market.market para buscar odds de abertura/fechamento
  const MARKET_ODDS_MAP = { '1x2': '1X2', 'over_under_2.5': 'over_under_2.5', 'btts': 'btts' };
  const oddsMarketKey = MARKET_ODDS_MAP[cfg.target] || null;

  // Busca detalhes dos jogos + predições + odds de mercado em paralelo
  const [matchResult, predResult, oddsAbRaw, oddsFechRaw] = await Promise.all([
    supabase
      .from('matches')
      .select(`id, match_date, season, home_goals, away_goals,
        home_team:teams!home_team_id(name),
        away_team:teams!away_team_id(name),
        league:leagues!league_id(name)`)
      .in('id', pageIds),
    supabase
      .from('model_predictions')
      .select('match_id, model_name, selection, probability')
      .in('model_name', modelNames)
      .in('match_id', pageIds),
    oddsMarketKey
      ? supabase.from('odds_market')
          .select('match_id, selection, odds, bookmaker')
          .in('match_id', pageIds)
          .eq('market', oddsMarketKey)
          .eq('snapshot', 'pre_closing')
          .in('bookmaker', ['pinnacle', 'media_mercado'])
      : Promise.resolve({ data: [] }),
    oddsMarketKey
      ? supabase.from('odds_market')
          .select('match_id, selection, odds, bookmaker')
          .in('match_id', pageIds)
          .eq('market', oddsMarketKey)
          .eq('snapshot', 'closing')
          .in('bookmaker', ['pinnacle', 'media_mercado'])
      : Promise.resolve({ data: [] }),
  ]);
  if (matchResult.error) throw matchResult.error;
  if (predResult.error) throw predResult.error;
  if (oddsAbRaw.error) throw oddsAbRaw.error;
  if (oddsFechRaw.error) throw oddsFechRaw.error;

  const matchMap = {};
  for (const m of matchResult.data || []) {
    matchMap[m.id] = {
      match_id: m.id,
      match_date: m.match_date,
      season: m.season,
      league: m.league?.name ?? null,
      home_team: m.home_team?.name ?? null,
      away_team: m.away_team?.name ?? null,
      goals_home: m.home_goals,
      goals_away: m.away_goals,
      probabilities: {},
      odds_abertura: {},
      odds_fechamento: {},
    };
  }
  for (const p of predResult.data || []) {
    if (!matchMap[p.match_id]) continue;
    matchMap[p.match_id].probabilities[`${p.model_name}||${p.selection}`] = p.probability;
  }
  // Preenche odds — prefere pinnacle sobre media_mercado quando ambos existem
  const attachOdds = (rows, campo) => {
    const visto = {};
    for (const r of (rows || [])) {
      const key = `${r.match_id}__${r.selection}`;
      if (!visto[key] || r.bookmaker === 'pinnacle') {
        visto[key] = true;
        if (matchMap[r.match_id]) matchMap[r.match_id][campo][r.selection] = Number(r.odds);
      }
    }
  };
  attachOdds(oddsAbRaw.data, 'odds_abertura');
  attachOdds(oddsFechRaw.data, 'odds_fechamento');

  // Ordena por data decrescente
  const jogos = pageIds
    .map((id) => matchMap[id])
    .filter(Boolean)
    .sort((a, b) => (a.match_date > b.match_date ? -1 : a.match_date < b.match_date ? 1 : 0));

  return { status: 200, jogos, pagina: pgNum, por_pagina: POR_PAGINA, total, model_names: modelNames, target: cfg.target };
}

// ============================================================
// TAREFA: backtest-custom — carteira simulada EV+ para modelos customizados.
// Usa predições de model_predictions + odds Pinnacle pre_closing de odds_market.
// Só suporta mercados com odds disponíveis: 1x2 e over_under_2.5.
// Aplica Quarter Kelly com teto de 15% por rodada (mesmo regime da carteira
// principal em tarefaSimulacaoCarteira). Quando há múltiplos modelos (WF mode),
// faz a média das probabilidades antes de calcular o edge.
// GET ?tarefa=backtest-custom&config_id=UUID
// ============================================================

async function tarefaBacktestCustom(supabase, configId) {
  if (!configId) return { status: 400, error: 'config_id é obrigatório.' };

  const { data: cfg, error: cfgErr } = await supabase
    .from('custom_model_configs')
    .select('id, name, metrics, target, mode')
    .eq('id', configId)
    .maybeSingle();
  if (cfgErr) throw cfgErr;
  if (!cfg) return { status: 404, error: 'Config não encontrada.' };

  const metrics = cfg.metrics || {};
  const modelNames = metrics.model_names?.length ? metrics.model_names : [cfg.name];

  // Mapeamento target → market key em model_predictions e em odds_market
  const MARKET_PRED_MAP = { '1x2': '1X2', 'over_under_2.5': 'over_under_2.5' };
  const marketPred = MARKET_PRED_MAP[cfg.target];
  if (!marketPred) {
    return {
      status: 200,
      suportado: false,
      mensagem: `Carteira simulada não disponível para o mercado "${cfg.target}" — odds Pinnacle não disponíveis no banco para este mercado.`,
    };
  }

  // Busca predições + odds em paralelo (match_ids descobertos a partir das predições)
  const [predicoes, oddsRaw] = await Promise.all([
    buscarTudoPaginado(() =>
      supabase
        .from('model_predictions')
        .select('match_id, model_name, selection, probability')
        .in('model_name', modelNames)
        .eq('market', marketPred)
    ),
    buscarTudoPaginado(() =>
      supabase
        .from('odds_market')
        .select('match_id, selection, odds')
        .eq('market', marketPred)
        .eq('snapshot', 'pre_closing')
        .eq('bookmaker', 'pinnacle')
    ),
  ]);

  if (!predicoes.length) {
    return { status: 200, suportado: true, n_apostas: 0, curva_banca: [], roi_pct: 0, mensagem: 'Nenhuma predição encontrada no banco.' };
  }

  const matchIdsSet = new Set(predicoes.map((p) => p.match_id));

  const todasMatches = await buscarTudoPaginado(() =>
    supabase
      .from('matches')
      .select('id, league_id, season, status, goals_home, goals_away, match_date')
      .in('id', [...matchIdsSet])
  );

  const matchPorId = {};
  todasMatches.forEach((m) => { matchPorId[m.id] = m; });

  const oddsPorChave = {};
  oddsRaw
    .filter((o) => matchIdsSet.has(o.match_id))
    .forEach((o) => { oddsPorChave[`${o.match_id}__${o.selection}`] = Number(o.odds); });

  function calcResultado(m) {
    if (m.status !== 'finished' || m.goals_home == null || m.goals_away == null) return null;
    if (marketPred === 'over_under_2.5') return (m.goals_home + m.goals_away) > 2.5 ? 'over' : 'under';
    return m.goals_home > m.goals_away ? 'home' : m.goals_home < m.goals_away ? 'away' : 'draw';
  }

  // Agrega probabilidades por (match_id, selection) — média entre os modelos do ensemble
  const predPorChave = {};
  for (const p of predicoes) {
    const chave = `${p.match_id}__${p.selection}`;
    if (!predPorChave[chave]) predPorChave[chave] = { match_id: p.match_id, selection: p.selection, probs: [] };
    predPorChave[chave].probs.push(Number(p.probability));
  }

  const candidatos = [];
  for (const pred of Object.values(predPorChave)) {
    const match = matchPorId[pred.match_id];
    if (!match) continue;
    const resultado = calcResultado(match);
    if (!resultado) continue;
    const chave = `${pred.match_id}__${pred.selection}`;
    const odd = oddsPorChave[chave];
    if (!odd) continue;
    const pMedia = pred.probs.reduce((a, b) => a + b, 0) / pred.probs.length;
    const ev = pMedia * odd;
    if (ev < 1.02) continue; // filtro EV bruto mínimo (mesmo critério da carteira principal)
    candidatos.push({
      data: String(match.match_date).slice(0, 10),
      selection: pred.selection,
      p_modelo: pMedia,
      odd,
      ev,
      resultado_real: resultado,
      acertou: resultado === pred.selection,
    });
  }

  if (!candidatos.length) {
    return { status: 200, suportado: true, n_apostas: 0, curva_banca: [], roi_pct: 0, mensagem: 'Nenhuma aposta com EV ≥ 1,02 encontrada.' };
  }

  // Simulação Quarter Kelly rodada-a-rodada (dia = unidade de rodada)
  const porDia = {};
  candidatos.forEach((c) => { (porDia[c.data] = porDia[c.data] || []).push(c); });
  const diasOrdenados = Object.keys(porDia).sort();

  const BANCA_INICIAL = 1000;
  let banca = BANCA_INICIAL;
  let totalInvestido = 0;
  let lucroTotal = 0;
  let n_apostas = 0;
  let n_acertos = 0;
  const curva_banca = [];

  for (const dia of diasOrdenados) {
    const apostas = porDia[dia];
    const stakes = apostas.map((a) => {
      const b = a.odd - 1;
      if (b <= 0) return 0;
      const f = (a.p_modelo * b - (1 - a.p_modelo)) / b;
      return Math.max(0, f) * 0.25 * banca; // Quarter Kelly
    });
    const totalExp = stakes.reduce((s, v) => s + v, 0);
    const teto = banca * 0.15; // teto de 15% por rodada
    const escala = totalExp > teto ? teto / totalExp : 1;

    for (let i = 0; i < apostas.length; i++) {
      const stakeReal = stakes[i] * escala;
      if (stakeReal < banca * 0.005) continue; // piso de 0,5%
      const lucro = apostas[i].acertou ? stakeReal * (apostas[i].odd - 1) : -stakeReal;
      banca += lucro;
      lucroTotal += lucro;
      totalInvestido += stakeReal;
      n_apostas++;
      if (apostas[i].acertou) n_acertos++;
    }
    curva_banca.push({ data: dia, banca: Math.round(banca * 100) / 100 });
  }

  const roi_pct = totalInvestido > 0 ? (lucroTotal / totalInvestido) * 100 : 0;

  return {
    status: 200,
    suportado: true,
    n_apostas,
    n_acertos,
    taxa_acerto: n_apostas > 0 ? n_acertos / n_apostas : 0,
    banca_inicial: BANCA_INICIAL,
    banca_final: Math.round(banca * 100) / 100,
    lucro_total: Math.round(lucroTotal * 100) / 100,
    total_investido: Math.round(totalInvestido * 100) / 100,
    roi_pct: Math.round(roi_pct * 100) / 100,
    model_names: modelNames,
    mercado: marketPred,
    curva_banca,
  };
}

// ============================================================
// CATÁLOGO DE FEATURES — retornado por ?tarefa=catalogo-features
// Grupos organizados por tema; cada feature tem col (nome no dataset) e label (UI).
// stats_fotmob e situacao_chutes usam o padrão media_{base}_5j_{home|away}
// (4 variantes por stat: marcado_home, sofrido_home, marcado_away, sofrido_away).

function _variantesFotmob(base, label) {
  return [
    { col: `media_${base}_5j_home`,         label: `${label} marcado (casa)` },
    { col: `media_${base}_sofrido_5j_home`,  label: `${label} sofrido (casa)` },
    { col: `media_${base}_5j_away`,          label: `${label} marcado (fora)` },
    { col: `media_${base}_sofrido_5j_away`,  label: `${label} sofrido (fora)` },
  ];
}

const CATALOGO_FEATURES = [
  {
    grupo: 'ELO',
    descricao: 'Rating Elo pré-jogo',
    features: [
      { col: 'elo_home', label: 'ELO mandante' },
      { col: 'elo_away', label: 'ELO visitante' },
    ],
  },
  {
    grupo: 'Forma — Gols',
    descricao: 'Média de gols marcados/sofridos nos últimos 5 jogos',
    features: [
      { col: 'media_gols_marcados_5j_home', label: 'Gols marcados (casa)' },
      { col: 'media_gols_sofridos_5j_home', label: 'Gols sofridos (casa)' },
      { col: 'media_gols_marcados_5j_away', label: 'Gols marcados (fora)' },
      { col: 'media_gols_sofridos_5j_away', label: 'Gols sofridos (fora)' },
    ],
  },
  {
    grupo: 'Forma — xG',
    descricao: 'Expected Goals médio nos últimos 5 jogos (Understat)',
    features: [
      { col: 'media_xg_5j_home',          label: 'xG gerado (casa)' },
      { col: 'media_xg_sofrido_5j_home',  label: 'xG sofrido (casa)' },
      { col: 'media_xg_5j_away',          label: 'xG gerado (fora)' },
      { col: 'media_xg_sofrido_5j_away',  label: 'xG sofrido (fora)' },
    ],
  },
  {
    grupo: 'Forma — xGOT',
    descricao: 'Expected Goals on Target médio nos últimos 5 jogos (FotMob)',
    features: [
      { col: 'media_xgot_5j_home',         label: 'xGOT gerado (casa)' },
      { col: 'media_xgot_sofrido_5j_home', label: 'xGOT sofrido (casa)' },
      { col: 'media_xgot_5j_away',         label: 'xGOT gerado (fora)' },
      { col: 'media_xgot_sofrido_5j_away', label: 'xGOT sofrido (fora)' },
    ],
  },
  {
    grupo: 'Elenco',
    descricao: 'Força do elenco e do XI titular',
    features: [
      { col: 'squad_rating_home',          label: 'Rating do elenco (casa)' },
      { col: 'squad_rating_away',          label: 'Rating do elenco (fora)' },
      { col: 'titular_rating_home',        label: 'Rating do XI titular (casa)' },
      { col: 'titular_rating_away',        label: 'Rating do XI titular (fora)' },
      { col: 'titular_valor_mercado_home', label: 'Valor de mercado titular (casa)' },
      { col: 'titular_valor_mercado_away', label: 'Valor de mercado titular (fora)' },
    ],
  },
  {
    grupo: 'Fadiga',
    descricao: 'Dias desde o último jogo e acúmulo de jogos em semana cheia',
    features: [
      { col: 'days_since_last_match_home', label: 'Dias desde último jogo (casa)' },
      { col: 'days_since_last_match_away', label: 'Dias desde último jogo (fora)' },
      { col: 'is_midweek_fatigue_home',    label: 'Fadiga de semana cheia (casa)' },
      { col: 'is_midweek_fatigue_away',    label: 'Fadiga de semana cheia (fora)' },
    ],
  },
  {
    grupo: 'Disciplina',
    descricao: 'Cartões acumulados e jogadores pendurados',
    features: [
      { col: 'cartoes_acumulados_home',    label: 'Cartões acumulados (casa)' },
      { col: 'cartoes_acumulados_away',    label: 'Cartões acumulados (fora)' },
      { col: 'jogadores_pendurados_home',  label: 'Jogadores pendurados (casa)' },
      { col: 'jogadores_pendurados_away',  label: 'Jogadores pendurados (fora)' },
    ],
  },
  {
    grupo: 'Classificação',
    descricao: 'Posição e pontuação no campeonato pré-jogo',
    features: [
      { col: 'pontos_por_jogo_home',   label: 'Pontos/jogo (casa)' },
      { col: 'pontos_por_jogo_away',   label: 'Pontos/jogo (fora)' },
      { col: 'saldo_por_jogo_home',    label: 'Saldo de gols/jogo (casa)' },
      { col: 'saldo_por_jogo_away',    label: 'Saldo de gols/jogo (fora)' },
      { col: 'posicao_home',           label: 'Posição na tabela (casa)' },
      { col: 'posicao_away',           label: 'Posição na tabela (fora)' },
      { col: 'jogos_disputados_home',  label: 'Jogos disputados (casa)' },
      { col: 'jogos_disputados_away',  label: 'Jogos disputados (fora)' },
    ],
  },
  {
    grupo: 'H2H',
    descricao: 'Histórico de confrontos diretos',
    features: [
      { col: 'h2h_taxa_vitoria_mandante', label: '% vitórias do mandante (H2H)' },
      { col: 'h2h_media_gols',            label: 'Média de gols (H2H)' },
      { col: 'h2h_n_jogos',              label: 'Número de confrontos' },
    ],
  },
  {
    grupo: 'Árbitro',
    descricao: 'Histórico do árbitro escalado',
    features: [
      { col: 'arbitro_cartoes_media', label: 'Cartões médios por jogo' },
      { col: 'arbitro_faltas_media',  label: 'Faltas médias por jogo' },
      { col: 'arbitro_n_jogos',      label: 'Nº de jogos apitados' },
    ],
  },
  {
    grupo: 'Progresso',
    descricao: 'Progresso da temporada (0=início, 1=final)',
    features: [
      { col: 'progresso_temporada', label: 'Progresso da temporada' },
    ],
  },
  {
    grupo: 'Titulares + Estádio',
    descricao: 'Idade/altura média do XI titular e capacidade do estádio (V10)',
    features: [
      { col: 'titular_avg_age_home',    label: 'Idade média do titular (casa)' },
      { col: 'titular_avg_age_away',    label: 'Idade média do titular (fora)' },
      { col: 'titular_avg_height_home', label: 'Altura média do titular (casa)' },
      { col: 'titular_avg_height_away', label: 'Altura média do titular (fora)' },
      { col: 'venue_capacity_home',     label: 'Capacidade do estádio (casa)' },
    ],
  },
  {
    grupo: 'Stats FBref — Ataque',
    descricao: 'Posse e chutes dos últimos 5 jogos (FBref)',
    features: [
      ..._variantesFotmob('posse', 'Posse de bola'),
      ..._variantesFotmob('chutes', 'Chutes totais'),
      ..._variantesFotmob('chutes_alvo', 'Chutes no alvo'),
      ..._variantesFotmob('escanteios', 'Escanteios'),
    ],
  },
  {
    grupo: 'Stats FBref — Disciplina',
    descricao: 'Faltas e cartões dos últimos 5 jogos (FBref)',
    features: [
      ..._variantesFotmob('faltas', 'Faltas'),
      ..._variantesFotmob('cartoes_amarelos', 'Cartões amarelos'),
      ..._variantesFotmob('cartoes_vermelhos', 'Cartões vermelhos'),
    ],
  },
  {
    grupo: 'Stats FotMob — Chutes',
    descricao: 'Volume e localização dos chutes (últimos 5 jogos, FotMob)',
    features: [
      ..._variantesFotmob('chutes_fm', 'Chutes totais FM'),
      ..._variantesFotmob('chutes_alvo_fm', 'Chutes no alvo FM'),
      ..._variantesFotmob('chutes_fora_fm', 'Chutes fora FM'),
      ..._variantesFotmob('chutes_bloqueados_fm', 'Chutes bloqueados FM'),
      ..._variantesFotmob('chutes_area_fm', 'Chutes dentro da área FM'),
      ..._variantesFotmob('chutes_fora_area_fm', 'Chutes fora da área FM'),
    ],
  },
  {
    grupo: 'Stats FotMob — Chances',
    descricao: 'Grandes chances criadas e perdidas (FotMob)',
    features: [
      ..._variantesFotmob('chances_claras_fm', 'Chances claras'),
      ..._variantesFotmob('chances_claras_perdidas_fm', 'Chances claras perdidas'),
      ..._variantesFotmob('toques_area_adv_fm', 'Toques na área adversária'),
    ],
  },
  {
    grupo: 'Stats FotMob — Passes',
    descricao: 'Passes certos, bolas longas e cruzamentos (FotMob)',
    features: [
      ..._variantesFotmob('passes_certos_fm', 'Passes certos'),
      ..._variantesFotmob('bolas_longas_certas_fm', 'Bolas longas certas'),
      ..._variantesFotmob('cruzamentos_certos_fm', 'Cruzamentos certos'),
      ..._variantesFotmob('posse_fm', 'Posse de bola FM'),
    ],
  },
  {
    grupo: 'Stats FotMob — Defesa',
    descricao: 'Ações defensivas (FotMob)',
    features: [
      ..._variantesFotmob('desarmes_fm', 'Desarmes'),
      ..._variantesFotmob('interceptacoes_fm', 'Interceptações'),
      ..._variantesFotmob('bloqueios_fm', 'Bloqueios'),
      ..._variantesFotmob('afastamentos_fm', 'Afastamentos'),
      ..._variantesFotmob('defesas_goleiro_fm', 'Defesas do goleiro'),
    ],
  },
  {
    grupo: 'Stats FotMob — Duelos',
    descricao: 'Duelos terrestres e aéreos (FotMob)',
    features: [
      ..._variantesFotmob('duelos_vencidos_fm', 'Duelos vencidos'),
      ..._variantesFotmob('duelos_aereos_vencidos_fm', 'Duelos aéreos vencidos'),
      ..._variantesFotmob('dribles_certos_fm', 'Dribles certos'),
    ],
  },
  {
    grupo: 'Stats FotMob — Disciplina',
    descricao: 'Faltas, cartões e escanteios (FotMob)',
    features: [
      ..._variantesFotmob('faltas_fm', 'Faltas FM'),
      ..._variantesFotmob('cartoes_amarelos_fm', 'Cartões amarelos FM'),
      ..._variantesFotmob('cartoes_vermelhos_fm', 'Cartões vermelhos FM'),
      ..._variantesFotmob('escanteios_fm', 'Escanteios FM'),
    ],
  },
  {
    grupo: 'Situação de Chutes',
    descricao: 'Perfil de chute: contra-ataque, bola parada, qualidade, timing (FotMob)',
    features: [
      ..._variantesFotmob('pct_fast_break_fm', '% Chutes em contra-ataque'),
      ..._variantesFotmob('pct_bola_parada_fm', '% Chutes de bola parada'),
      ..._variantesFotmob('xg_chute_fm', 'xG médio por chute'),
      ..._variantesFotmob('pct_gols_2tempo_fm', '% Gols no 2º tempo'),
    ],
  },
  {
    grupo: 'Liga',
    descricao: 'Identificador de liga (feature categórica — sempre incluída)',
    features: [
      { col: 'liga', label: 'Liga (categórica)' },
    ],
  },
];

function tarefaCatalogoFeatures() {
  return { catalogo: CATALOGO_FEATURES };
}

// ============================================================
// TAREFA: jogador-perfil — sync SOB DEMANDA de 1 jogador (endpoint
// /api/data/playerData?id=X do FotMob, diferente do matchDetails usado no
// resto do pipeline — esse é POR JOGADOR, não por partida). Rápido o
// bastante (1 chamada externa) pra ser chamado direto do frontend, ao
// contrário do backfill em massa (ver arquivos_do_claude/
// ingestao_fotmob_perfil_jogador.py, script Python separado pra popular a
// base inteira — ~7.900 jogadores levaria horas, não cabe numa chamada
// serverless de 60s).
//
// Popula: player_market_value_history (série temporal, upsert incremental
// — nunca apaga o que já existe), player_career_history_fotmob,
// player_trophies_fotmob (essas duas via delete-and-regrow do jogador, já
// que são a lista COMPLETA vinda da fonte a cada chamada) e
// player_details_fotmob (snapshot, upsert simples).
//
// NÃO popula heatmap/shotmap (mapa de toques / chute a chute) — mesmo
// payload tem em firstSeasonStats, mas ficou de fora por decisão
// consciente. Achado confirmado inspecionando dado real (Mbappé):
// "firstSeasonStats" NÃO é a carreira inteira, é só a competição/temporada
// mais recente mostrada por padrão na página do jogador no FotMob (no
// teste, só ~41 chutes de ~1 mês — Copa do Mundo 2026, nem a temporada de
// clube). Carreira completa existe só como ÍNDICE em `statSeasons`
// (temporada×competição com um entryId cada) — puxar heatmap/shotmap
// histórico exigiria descobrir um sub-endpoint por entryId, não
// explorado ainda. Custo se retomado: 1 chamada POR TEMPORADA POR
// JOGADOR (não 1 por jogador), ordem de grandeza maior — validar com
// 1-2 chamadas de descoberta antes de generalizar.
// ============================================================

function parseDataFotmob(s) {
  if (!s) return null;
  return String(s).slice(0, 10);
}

function extrairValorPlayerInfo(tituloAlvo, playerInformation) {
  for (const item of playerInformation || []) {
    if (item.title === tituloAlvo) return item.value || {};
  }
  return {};
}

// Deduplica por uma chave equivalente à constraint única de destino. O
// payload do FotMob às vezes repete a mesma linha (torneio listado 2x pro
// mesmo time, período de valor de mercado duplicado etc.) — um insert/upsert
// em lote com chave repetida DENTRO do mesmo lote quebra a chamada INTEIRA
// ("duplicate key value violates unique constraint" no insert simples,
// "ON CONFLICT DO UPDATE command cannot affect row a second time" no
// upsert), o que impedia player_details_fotmob de ser gravado no fim da
// função e deixava o jogador pra sempre como "não importado" — voltando a
// ser escolhido em todo clique de "Importar em massa" (sempre no topo da
// fila por valor de mercado, mesmo erro se repetindo pra sempre).
function dedupPorChave(linhas, chaveFn) {
  const porChave = new Map();
  for (const linha of linhas) porChave.set(chaveFn(linha), linha);
  return [...porChave.values()];
}

function montarLinhasPerfilJogador(playerId, payload) {
  const agora = new Date().toISOString();

  const valoresMercado = dedupPorChave(
    ((payload.marketValues || {}).values || [])
      .map(v => ({
        player_id: playerId,
        value_date: parseDataFotmob(v.date),
        value_eur: v.value ?? null,
        lower_bound_eur: v.lowerBound ?? null,
        upper_bound_eur: v.upperBound ?? null,
        source: v.source ?? null,
        team_fotmob_id: v.teamId != null ? String(v.teamId) : null,
        team_name: v.teamName ?? null,
        is_period_start: !!v.isPeriodStart,
        captured_at: agora,
      }))
      .filter(v => v.value_date),
    v => `${v.value_date}|${v.team_fotmob_id}`
  );

  const senior = (((payload.careerHistory || {}).careerItems || {}).senior || {});
  const carreira = dedupPorChave(
    (senior.teamEntries || [])
      .map(t => ({
        player_id: playerId,
        team_fotmob_id: t.teamId != null ? String(t.teamId) : null,
        team_name: t.team ?? null,
        start_date: parseDataFotmob(t.startDate),
        end_date: parseDataFotmob(t.endDate),
        active: !!t.active,
        transfer_type: (t.transferType || {}).text ?? null,
        appearances: /^\d+$/.test(String(t.appearances ?? '')) ? parseInt(t.appearances, 10) : null,
        goals: /^\d+$/.test(String(t.goals ?? '')) ? parseInt(t.goals, 10) : null,
        assists: /^\d+$/.test(String(t.assists ?? '')) ? parseInt(t.assists, 10) : null,
        captured_at: agora,
      }))
      .filter(t => t.start_date),
    t => `${t.team_fotmob_id}|${t.start_date}`
  );

  const titulosBrutos = [];
  for (const timeTrofeus of (payload.trophies || {}).playerTrophies || []) {
    for (const torneio of timeTrofeus.tournaments || []) {
      const base = {
        player_id: playerId,
        team_fotmob_id: timeTrofeus.teamId != null ? String(timeTrofeus.teamId) : null,
        team_name: timeTrofeus.teamName ?? null,
        league_fotmob_id: torneio.leagueId != null ? String(torneio.leagueId) : null,
        league_name: torneio.leagueName ?? null,
        country_code: timeTrofeus.ccode ?? null,
        captured_at: agora,
      };
      for (const temporada of torneio.seasonsWon || []) titulosBrutos.push({ ...base, season: temporada, result: 'won' });
      for (const temporada of torneio.seasonsRunnerUp || []) titulosBrutos.push({ ...base, season: temporada, result: 'runner_up' });
    }
  }
  const titulos = dedupPorChave(titulosBrutos, t => `${t.team_fotmob_id}|${t.league_fotmob_id}|${t.season}|${t.result}`);

  const playerInformation = payload.playerInformation;
  const altura = extrairValorPlayerInfo('Height', playerInformation).numberValue ?? null;
  const pe = extrairValorPlayerInfo('Preferred foot', playerInformation).key ?? null;
  const contratoInfo = extrairValorPlayerInfo('Contract expires', playerInformation).dateValue
    ?? extrairValorPlayerInfo('Contract end', playerInformation).dateValue ?? null;
  const valorAtual = extrairValorPlayerInfo('Market value', playerInformation).numberValue ?? null;
  const nascimentoRaw = extrairValorPlayerInfo('Date of birth', playerInformation).dateValue ?? null;
  const birthDate = parseDataFotmob(nascimentoRaw);
  const countryCode = payload.ccode ?? payload.countryCode ?? null;

  const posDesc = payload.positionDescription || {};
  const posicaoPrincipal = (posDesc.primaryPosition || {}).label ?? null;

  const detalhes = {
    player_id: playerId,
    height_cm: altura,
    preferred_foot: pe,
    contract_end: parseDataFotmob(contratoInfo),
    current_market_value_eur: valorAtual,
    primary_position: posicaoPrincipal,
    all_positions: posDesc.positions ?? null,
    traits: payload.traits ?? null,
    captured_at: agora,
  };

  return { valoresMercado, carreira, titulos, detalhes, birthDate, countryCode };
}

async function tarefaJogadorPerfil(supabase, playerIdInterno) {
  if (!playerIdInterno) return { error: 'Informe ?player_id=X (players.id interno).' };

  const { data: jogador, error: erroJogador } = await supabase
    .from('players').select('id, fotmob_player_id, name').eq('id', playerIdInterno).maybeSingle();
  if (erroJogador) throw erroJogador;
  if (!jogador) return { error: `players.id=${playerIdInterno} não encontrado.` };
  if (!jogador.fotmob_player_id || jogador.fotmob_player_id === '0' || jogador.fotmob_player_id === '-1') {
    return { error: `Jogador "${jogador.name}" sem fotmob_player_id válido — não é possível sincronizar.` };
  }

  const resp = await fetch(`https://www.fotmob.com/api/data/playerData?id=${jogador.fotmob_player_id}`, {
    headers: { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36' },
  });
  if (!resp.ok) return { error: `FotMob respondeu ${resp.status} pra fotmob_player_id=${jogador.fotmob_player_id}.` };
  const payload = await resp.json();
  if (!payload) return { error: 'FotMob não retornou dado pra esse jogador (payload vazio).' };

  const { valoresMercado, carreira, titulos, detalhes, birthDate, countryCode } = montarLinhasPerfilJogador(jogador.id, payload);

  if (valoresMercado.length) {
    const { error } = await supabase.from('player_market_value_history').upsert(valoresMercado, { onConflict: 'player_id,value_date,team_fotmob_id' });
    if (error) throw error;
  }
  // Carreira/troféus: a fonte já devolve a lista COMPLETA a cada chamada —
  // delete-and-regrow em vez de upsert acumulativo evita duplicar entradas
  // se algum campo de data mudar de formato entre syncs.
  await supabase.from('player_career_history_fotmob').delete().eq('player_id', jogador.id);
  if (carreira.length) {
    const { error } = await supabase.from('player_career_history_fotmob').insert(carreira);
    if (error) throw error;
  }
  await supabase.from('player_trophies_fotmob').delete().eq('player_id', jogador.id);
  if (titulos.length) {
    const { error } = await supabase.from('player_trophies_fotmob').insert(titulos);
    if (error) throw error;
  }
  const { error: erroDetalhes } = await supabase.from('player_details_fotmob').upsert(detalhes, { onConflict: 'player_id' });
  if (erroDetalhes) throw erroDetalhes;

  // Atualiza players com dados biográficos extraídos do perfil FotMob
  const updateJogador = {};
  if (birthDate) {
    updateJogador.birth_date = birthDate;
    const hoje = new Date();
    const bd = new Date(birthDate);
    const age = hoje.getFullYear() - bd.getFullYear()
      - ((hoje.getMonth() < bd.getMonth() || (hoje.getMonth() === bd.getMonth() && hoje.getDate() < bd.getDate())) ? 1 : 0);
    updateJogador.age = age;
  }
  if (countryCode) updateJogador.country_code = countryCode;
  if (Object.keys(updateJogador).length > 0) {
    const { error: erroAtualiza } = await supabase.from('players').update(updateJogador).eq('id', jogador.id);
    if (erroAtualiza) throw erroAtualiza;
  }

  return {
    player_id: jogador.id, nome: jogador.name,
    pontos_valor_mercado: valoresMercado.length, clubes_carreira: carreira.length, titulos: titulos.length,
    detalhes_atualizados: true, birth_date: birthDate ?? null, country_code: countryCode ?? null,
  };
}

// ============================================================
// TAREFA: calibracao — Platt Scaling + Isotonic Regression, salvos em
// model_calibration por (model_name, market, selection). Split temporal
// 70/30. Platt via gradiente descendente, Isotonic via PAVA.
// ============================================================

function chaveMercadoCalib(m) {
  return m === '1X2' ? '1X2' : m === 'over_under_2.5' ? 'over_under_2_5' : 'corners_over_under_9_5';
}

function ajustarPlatt(xs, ys) {
  let a = 1, b = 0;
  const n = xs.length;
  const taxaAprendizado = 0.1;
  for (let iter = 0; iter < 800; iter++) {
    let gradA = 0, gradB = 0;
    for (let i = 0; i < n; i++) {
      const pred = sigmoid(a * xs[i] + b);
      const erro = pred - ys[i];
      gradA += erro * xs[i];
      gradB += erro;
    }
    a -= (taxaAprendizado * gradA) / n;
    b -= (taxaAprendizado * gradB) / n;
  }
  return { a, b };
}

function ajustarIsotonic(xs, ys) {
  const ordem = xs.map((x, i) => i).sort((i, j) => xs[i] - xs[j]);
  const blocos = ordem.map(i => ({ somaX: xs[i], somaY: ys[i], n: 1 }));
  let mudou = true;
  while (mudou) {
    mudou = false;
    for (let i = 0; i < blocos.length - 1; i++) {
      const mediaAtual = blocos[i].somaY / blocos[i].n;
      const mediaProxima = blocos[i + 1].somaY / blocos[i + 1].n;
      if (mediaAtual > mediaProxima) {
        blocos[i] = { somaX: blocos[i].somaX + blocos[i + 1].somaX, somaY: blocos[i].somaY + blocos[i + 1].somaY, n: blocos[i].n + blocos[i + 1].n };
        blocos.splice(i + 1, 1);
        mudou = true;
        break;
      }
    }
  }
  return { x: blocos.map(bl => bl.somaX / bl.n), y: blocos.map(bl => bl.somaY / bl.n) };
}

function aplicarIsotonicPredicao(p, xs, ys) {
  if (p <= xs[0]) return ys[0];
  if (p >= xs[xs.length - 1]) return ys[ys.length - 1];
  for (let i = 0; i < xs.length - 1; i++) {
    if (p >= xs[i] && p <= xs[i + 1]) {
      const t = (p - xs[i]) / (xs[i + 1] - xs[i] || 1);
      return ys[i] + t * (ys[i + 1] - ys[i]);
    }
  }
  return p;
}

const logLossTermo = (p, y) => (y ? -Math.log(clamp(p)) : -Math.log(1 - clamp(p)));

async function tarefaCalibracao(supabase, minimo) {
  const predicoes = await buscarTudoPaginado(() => supabase.from('model_predictions').select('id, model_name, market, selection, probability, match_id'));
  const matches = await buscarTudoPaginado(() => supabase.from('matches').select('id, status, home_goals, away_goals, match_date').eq('status', 'finished').not('home_goals', 'is', null));
  const corneragens = await buscarTudoPaginado(() => supabase.from('match_stats').select('match_id, corners').not('corners', 'is', null));

  const matchPorId = {};
  matches.forEach(m => { matchPorId[m.id] = m; });

  const cornersPorJogo = {};
  { const soma = {}, cont = {};
    corneragens.forEach(r => { soma[r.match_id] = (soma[r.match_id] || 0) + Number(r.corners); cont[r.match_id] = (cont[r.match_id] || 0) + 1; });
    Object.keys(soma).forEach(id => { if (cont[id] === 2) cornersPorJogo[id] = soma[id]; });
  }

  function resultadoReal(matchId, market, selection) {
    const m = matchPorId[matchId];
    if (!m) return null;
    const chave = chaveMercadoCalib(market);
    if (chave === '1X2') {
      const real = m.home_goals > m.away_goals ? 'home' : m.home_goals < m.away_goals ? 'away' : 'draw';
      return real === selection ? 1 : 0;
    }
    if (chave === 'over_under_2_5') {
      const total = m.home_goals + m.away_goals;
      const real = total > 2.5 ? 'over' : 'under';
      return real === selection ? 1 : 0;
    }
    if (cornersPorJogo[matchId] == null) return null;
    const real = cornersPorJogo[matchId] > 9.5 ? 'over' : 'under';
    return real === selection ? 1 : 0;
  }

  const grupos = {};
  for (const p of predicoes) {
    const m = matchPorId[p.match_id];
    if (!m) continue;
    const y = resultadoReal(p.match_id, p.market, p.selection);
    if (y == null) continue;
    const chave = `${p.model_name}__${p.market}__${p.selection}`;
    if (!grupos[chave]) grupos[chave] = { model_name: p.model_name, market: p.market, selection: p.selection, linhas: [] };
    grupos[chave].linhas.push({ p: Number(p.probability), y, data: m.match_date });
  }

  const resultado = { ajustados: [], ignorados_amostra_insuficiente: [] };

  for (const g of Object.values(grupos)) {
    g.linhas.sort((a, b) => new Date(a.data) - new Date(b.data));
    const corte = Math.floor(g.linhas.length * 0.7);
    const treino = g.linhas.slice(0, corte);
    const teste = g.linhas.slice(corte);

    if (treino.length < minimo || teste.length < 20) {
      resultado.ignorados_amostra_insuficiente.push({ model_name: g.model_name, market: g.market, selection: g.selection, n_treino: treino.length, n_teste: teste.length });
      continue;
    }

    const xsTreino = treino.map(l => logit(l.p));
    const ysTreino = treino.map(l => l.y);
    const logLossBruto = teste.reduce((s, l) => s + logLossTermo(l.p, l.y), 0) / teste.length;

    const platt = ajustarPlatt(xsTreino, ysTreino);
    const logLossPlatt = teste.reduce((s, l) => s + logLossTermo(sigmoid(platt.a * logit(l.p) + platt.b), l.y), 0) / teste.length;

    const xsTreinoP = treino.map(l => l.p);
    const isotonic = ajustarIsotonic(xsTreinoP, ysTreino);
    const logLossIsotonic = teste.reduce((s, l) => s + logLossTermo(aplicarIsotonicPredicao(l.p, isotonic.x, isotonic.y), l.y), 0) / teste.length;

    const base = { model_name: g.model_name, market: g.market, selection: g.selection, n_treino: treino.length, n_teste: teste.length, log_loss_bruto: logLossBruto, fitted_at: new Date().toISOString() };

    const linhaPlatt = { ...base, method: 'platt', platt_coef: platt.a, platt_intercept: platt.b, log_loss_calibrado: logLossPlatt, isotonic_x: null, isotonic_y: null };
    const linhaIsotonic = { ...base, method: 'isotonic', platt_coef: null, platt_intercept: null, log_loss_calibrado: logLossIsotonic, isotonic_x: isotonic.x, isotonic_y: isotonic.y };

    const { error: erroUpsert } = await supabase.from('model_calibration').upsert([linhaPlatt, linhaIsotonic], { onConflict: 'model_name,market,selection,method' });
    if (erroUpsert) throw erroUpsert;

    resultado.ajustados.push({
      model_name: g.model_name, market: g.market, selection: g.selection,
      n_treino: treino.length, n_teste: teste.length,
      log_loss_bruto: logLossBruto, log_loss_platt: logLossPlatt, log_loss_isotonic: logLossIsotonic,
    });
  }

  return resultado;
}

// ============================================================
// TAREFA: odds-descobrir — FASE 1 do sync de odds (OddsPapi). Cota grátis é
// só 250 req/mês, então antes de escrever a lógica de parse "às cegas" (e
// arriscar queimar cota com tentativa e erro), essa tarefa faz UMA rodada de
// chamadas de descoberta: lista de torneios, mercados e casas de apostas
// (3 chamadas, cacheadas em oddspapi_cache pra nunca precisar rechamar), e
// devolve uma amostra CRUA de odds de UM torneio já resolvido (4ª chamada) —
// pra inspecionar o formato real da resposta antes de escrever o parser
// definitivo da tarefa "odds".
//
// IMPORTANTE: NÃO grava mais o mapeamento liga->torneio automaticamente — a
// heurística por nome errou duas vezes em produção (achados reais: 1. La
// Liga casou com uma copa feminina espanhola só pela palavra "la"; 2. Serie A
// Itália e Brasileirão colidiram no mesmo torneio "Serie A" genérico; 3. numa
// segunda tentativa já com filtro por país, Serie A Itália foi parar em
// "Coppa Italia Serie C" porque nosso label "(Itália)" batia token com
// "Italia" do nome do torneio errado). O custo de um mapeamento errado é alto
// (corrompe todo sync de odds daquela liga), então o mapeamento real das 6
// ligas foi resolvido manualmente por inspeção direta do cache via SQL e está
// fixo em liga_oddspapi_tournament — essa tarefa só REPORTA o que a
// heurística sugeriria (`sugestoes_heuristica`) pra quem ainda não tem
// mapeamento confirmado, nunca sobrescreve o que já está resolvido.
// ============================================================

const ODDSPAPI_BASE = 'https://api.oddspapi.io';
const SPORT_ID_FUTEBOL = 10;

// BOOKMAKERS_ALVO: usado pelo sync AO VIVO (tarefaOddsSyncLote/tarefaOddsSync/
// tarefaOddsTodas, /v4/odds-by-tournaments) -- esse endpoint É pago, 1
// chamada por casa POR LOTE de até 5 torneios (`tournamentIds` aceita
// vários por chamada, mas tem teto real de 5 -- HTTP 400 acima disso,
// confirmado testando em produção; ver comentário completo onde
// tarefaOddsSyncLote está definida). Cada casa a mais aqui multiplica o
// número de lotes necessários -- mantido enxuto de propósito. Se quiser
// mais casas no sync ao vivo, avaliar cota antes (com 16 ligas resolvidas
// hoje = 4 lotes, cada casa a mais soma +4 chamadas por rodada do cron).
const BOOKMAKERS_ALVO = ['pinnacle', 'bet365', 'betano'];

// BOOKMAKERS_HISTORICO: usado só pelo backfill histórico (tarefaOddsHistorico/
// tarefaOddsHistoricoDescobrir, /v4/historical-odds) -- esse endpoint é
// GRÁTIS (confirmado na doc oficial + testado em produção, ver
// CONTEXTO_PROJETO.md), então pode ser mais generoso sem custo de cota real
// (só timeout de function/rate-limit, não quota). Pedido do usuário: além
// de Pinnacle/bet365/Betano, adicionar William Hill e 1xBet -- William Hill
// já existe no schema via football-data.co.uk (odds_market.bookmaker =
// 'william_hill', com underscore) e 1xBet já aparece em Libertadores via
// outra fonte antiga ('1xbet', sem underscore, bate direto com o slug da
// OddsPapi). Unibet e Bwin avaliados e descartados pelo usuário (sem
// utilidade de aposta/representação de mercado pro caso de uso do projeto).
//
// Betfair Exchange (betfair-ex) foi pedida também, mas NÃO entra aqui --
// achado real testando em produção: ao contrário dos bookmakers de odds
// fixas, a exchange não aceita ser combinada com outras casas nem devolve
// "todos os mercados de uma vez" -- HTTP 400 "Invalid bookmaker/outcome
// combination... When using 'betfair-ex', you must provide only one
// bookmaker and exactly one outcomeId" (faz sentido: exchange tem preço
// back/lay por seleção individual, não um mercado fechado como bookmaker
// tradicional). Integrar isso exigiria um loop dedicado, uma chamada por
// outcome (dezenas por partida), incompatível com o desenho atual de "1
// lote = todos os mercados". Decisão do usuário: deixar de fora por
// enquanto, sem implementação dedicada nesta sessão.
const BOOKMAKERS_HISTORICO = ['pinnacle', 'bet365', 'betano', 'williamhill', '1xbet'];

// LIMITE REAL da OddsPapi (achado testando em produção, não documentado
// antes): /v4/historical-odds aceita no MÁXIMO 3 bookmakers por chamada --
// HTTP 400 "Too many bookmakers specified" acima disso. Com 6 casas em
// BOOKMAKERS_HISTORICO, cada fixture agora precisa de 2 chamadas (uma por
// lote de 3) em vez de uma só -- ainda grátis, só mais latência.
function loteados(array, tamanho) {
  const lotes = [];
  for (let i = 0; i < array.length; i += tamanho) lotes.push(array.slice(i, i + tamanho));
  return lotes;
}
const LOTES_BOOKMAKERS_HISTORICO = loteados(BOOKMAKERS_HISTORICO, 3);

// Slug da OddsPapi -> nome já usado em odds_market.bookmaker nas outras
// fontes do projeto -- sem isso, William Hill viraria uma casa "nova"
// (rótulo diferente) em vez de somar ao dado que já existe vindo do
// football-data.co.uk, fragmentando a mesma casa em dois nomes.
const NOME_CANONICO_BOOKMAKER = {
  williamhill: 'william_hill',
};
function nomeCanonicoBookmaker(slugOddspapi) {
  return NOME_CANONICO_BOOKMAKER[slugOddspapi] || slugOddspapi;
}

async function chamarOddspapi(caminho, params, apiKey) {
  const url = new URL(`${ODDSPAPI_BASE}${caminho}`);
  Object.entries(params).forEach(([k, v]) => { if (v != null) url.searchParams.set(k, v); });
  url.searchParams.set('apiKey', apiKey);
  const resposta = await fetch(url.toString());
  const dados = await resposta.json();
  if (!resposta.ok) throw new Error(`OddsPapi ${caminho}: HTTP ${resposta.status} — ${JSON.stringify(dados).slice(0, 300)}`);
  return dados;
}

function normalizarTexto(s) {
  return (s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/[^a-z0-9]+/g, ' ').trim();
}

// Times de futebol domésticas -> nome do país esperado na OddsPapi (categoryName),
// pra desambiguar nomes de torneio genéricos que colidem entre países (ex.: "Serie A"
// existe pra Itália E aparece como sufixo em vários campeonatos estaduais brasileiros;
// "La Liga"/"a" batia até com "Copa de la Reina" só pela palavra "la"). Achado real
// testado em produção: sem esse filtro, Brasileirão e Serie A (Itália) colidiam no
// mesmo torneio, e La Liga casava com uma copa feminina espanhola.
const PAIS_POR_LIGA = { 1: 'brazil', 4: 'england', 7: 'spain', 10: 'italy', 13: 'germany', 16: 'france' };

const STOPWORDS_LIGA = new Set(['de', 'da', 'do', 'a', 'the', 'liga', 'league']);
function tokensLiga(nome) {
  return normalizarTexto(nome).split(' ').filter(t => t && t.length > 2 && !STOPWORDS_LIGA.has(t));
}

function acharMelhorTorneio(nomeLiga, pais, torneios) {
  const tokensA = tokensLiga(nomeLiga);
  if (tokensA.length === 0) return null;
  // Restringe candidatos ao país esperado quando a categoria bate (evita colisão
  // entre ligas de países diferentes com nome genérico tipo "Serie A"/"Liga").
  const candidatos = torneios.filter(t => normalizarTexto(t.categoryName) === pais);
  const pool = candidatos.length > 0 ? candidatos : torneios;

  let melhor = null, melhorScore = 0;
  for (const t of pool) {
    const tokensB = tokensLiga(t.tournamentName);
    if (tokensB.length === 0) continue;
    const intersecao = tokensA.filter(x => tokensB.includes(x)).length;
    if (intersecao === 0) continue;
    const uniao = new Set([...tokensA, ...tokensB]).size;
    const score = intersecao / uniao; // Jaccard — mais rigoroso que intersecao/min, evita falso-positivo de 1 token só
    if (score > melhorScore) { melhorScore = score; melhor = t; }
  }
  return melhorScore >= 0.34 ? melhor : null;
}

async function buscarOuCache(supabase, chave, buscar) {
  const { data: existente } = await supabase.from('oddspapi_cache').select('valor').eq('chave', chave).maybeSingle();
  if (existente) return existente.valor;
  const valor = await buscar();
  await supabase.from('oddspapi_cache').upsert({ chave, valor, atualizado_em: new Date().toISOString() }, { onConflict: 'chave' });
  return valor;
}

async function tarefaOddsDescobrir(supabase, apiKey, forcar) {
  if (forcar) await supabase.from('oddspapi_cache').delete().in('chave', ['tournaments', 'markets', 'bookmakers']);

  const torneios = await buscarOuCache(supabase, 'tournaments', () => chamarOddspapi('/v4/tournaments', { sportId: SPORT_ID_FUTEBOL }, apiKey));
  const mercados = await buscarOuCache(supabase, 'markets', () => chamarOddspapi('/v4/markets', {}, apiKey));
  const casas = await buscarOuCache(supabase, 'bookmakers', () => chamarOddspapi('/v4/bookmakers', {}, apiKey));

  const agora = new Date().toISOString();
  const { data: ligas } = await supabase.from('leagues').select('id, name').in('id', LIGAS_DOMESTICAS);

  // IMPORTANTE: essa tarefa NÃO grava mais em liga_oddspapi_tournament
  // automaticamente. A heurística por nome já errou duas vezes em produção
  // pra essas 6 ligas (Brasileirão×Serie A colidindo, La Liga×copa feminina,
  // depois Serie A×Coppa Italia Serie C) — o custo de um mapeamento errado é
  // alto (corrompe TODO sync de odds daquela liga), então o mapeamento real
  // já foi resolvido manualmente por inspeção direta do cache (SQL) e está
  // fixo na tabela. Aqui só REPORTA o que a heurística acharia, pra
  // conferência — nunca sobrescreve o que já está confirmado.
  const { data: jaResolvidas } = await supabase.from('liga_oddspapi_tournament').select('league_id, tournament_id, tournament_name');
  const resolvidasPorLiga = Object.fromEntries((jaResolvidas || []).map(r => [r.league_id, r]));

  const resultado = { ja_resolvidas: jaResolvidas || [], sugestoes_heuristica: [], sem_correspondencia: [], casas_encontradas: null, amostra_odds: null };

  for (const liga of ligas || []) {
    if (resolvidasPorLiga[liga.id]) continue; // já confirmado manualmente, não precisa de sugestão
    const torneio = acharMelhorTorneio(liga.name, PAIS_POR_LIGA[liga.id], torneios);
    if (!torneio) { resultado.sem_correspondencia.push(liga.name); continue; }
    resultado.sugestoes_heuristica.push({ liga: liga.name, tournamentId: torneio.tournamentId, tournamentName: torneio.tournamentName, categoryName: torneio.categoryName, aviso: 'NÃO gravado automaticamente — confirme manualmente antes de usar.' });
  }

  // Confere se os slugs esperados (pinnacle/bet365/betano) existem na lista real de casas
  const nomesCasas = (casas || []).map(c => ({ nome: c.bookmakerName, slug: c.slug }));
  resultado.casas_encontradas = BOOKMAKERS_ALVO.map(alvo => {
    const achado = nomesCasas.find(c => c.slug === alvo);
    return { alvo, encontrado: achado || null };
  });

  // Amostra crua de odds de UM torneio já casado, com UM bookmaker só — a API exige
  // exatamente 1 bookmaker por chamada em /v4/odds-by-tournaments (`bookmaker`,
  // singular; descoberto testando em produção — a doc pública sugeria lista separada
  // por vírgula, que não é aceito). Pra pegar as 3 casas, a fase 2 (sync de verdade)
  // vai precisar de 1 chamada POR bookmaker.
  if ((jaResolvidas || []).length > 0) {
    const primeiro = jaResolvidas[0];
    const amostra = await chamarOddspapi('/v4/odds-by-tournaments', {
      tournamentIds: primeiro.tournament_id, bookmaker: 'pinnacle', oddsFormat: 'decimal', verbosity: 3,
    }, apiKey);
    resultado.amostra_odds = { torneio: primeiro.tournament_name, quantidade_fixtures: Array.isArray(amostra) ? amostra.length : null, primeiro_fixture: Array.isArray(amostra) ? amostra[0] : amostra };
  }

  return resultado;
}

// ============================================================
// TAREFA: odds-sync-diagnostico — NÃO escreve nada, só chamada manual de
// inspeção (mesmo espírito de af-diagnostico-time/statsapi-diagnostico).
// Existe pra responder 2 perguntas antes de generalizar tarefaOddsSync:
//   1. `tournamentIds` (plural) em /v4/odds-by-tournaments aceita MAIS DE
//      1 torneio numa chamada só? Só o parâmetro `bookmaker` (singular) já
//      foi confirmado como 1-valor-só (ver comentário em tarefaOddsDescobrir
//      -- doc pública sugeria lista, não é aceito); `tournamentIds` nunca
//      foi testado com mais de 1 valor neste projeto. Se aceitar, dá pra
//      sincronizar VÁRIAS ligas numa chamada só (mesmo custo de 1 liga).
//   2. Qual o formato real de `bookmakerOdds[bookmaker].markets` pra
//      mercados ALÉM dos 3 já usados (1X2/O-U 2.5/BTTS)? tarefaOddsSync usa
//      bookmakerOutcomeId direto (ex.: 'home'/'draw'/'away', '2.5/over') --
//      precisa confirmar se isso é consistente nos outros marketId antes de
//      generalizar o parser (regra do projeto: nunca adivinhar formato de
//      resposta de API paga).
// CUSTO: 1 chamada real de cota (mesmo endpoint pago de tarefaOddsSync) —
// só chamar quando for de fato investigar, não colocar em cron.
// ============================================================
async function tarefaOddsSyncDiagnostico(supabase, apiKey, { tournamentIds, bookmaker }) {
  if (!tournamentIds) return { error: 'tarefa=odds-sync-diagnostico precisa de ?tournament_ids=A,B (ids de torneio da OddsPapi, não league_id nosso -- ver liga_oddspapi_tournament).' };
  const bk = bookmaker || 'pinnacle';

  const resposta = await chamarOddspapi('/v4/odds-by-tournaments', { tournamentIds, bookmaker: bk, oddsFormat: 'decimal', verbosity: 3 }, apiKey);
  if (!Array.isArray(resposta)) return { erro: 'Resposta inesperada (não é array).', resposta_crua: resposta };

  const idsTestados = String(tournamentIds).split(',').map((s) => s.trim());
  // Tenta achar sozinho qual campo do fixture identifica o torneio de origem
  // -- sem isso confirmado, não dá pra saber se os fixtures retornados vêm
  // de fato dos 2+ torneios pedidos ou só do primeiro.
  const candidatosChaveTorneio = ['tournamentId', 'tournament_id', 'competitionId', 'leagueId'];
  const chaveTorneio = candidatosChaveTorneio.find((k) => resposta[0]?.[k] != null);
  const torneiosDistintos = chaveTorneio ? [...new Set(resposta.map((fx) => String(fx[chaveTorneio])))] : null;

  const { data: mercadosCacheRaw } = await supabase.from('oddspapi_cache').select('valor').eq('chave', 'markets').maybeSingle();
  const nomePorMarketId = Object.fromEntries((mercadosCacheRaw?.valor || []).map((m) => [String(m.marketId), m.marketName]));

  const primeiraComMercado = resposta.find((fx) => fx.bookmakerOdds?.[bk]?.markets && Object.keys(fx.bookmakerOdds[bk].markets).length > 0);
  const marketsFixture = primeiraComMercado?.bookmakerOdds?.[bk]?.markets || {};
  const marketIds = Object.keys(marketsFixture);

  return {
    tournament_ids_testados: idsTestados,
    bookmaker: bk,
    total_fixtures_retornados: resposta.length,
    campo_usado_pra_identificar_torneio: chaveTorneio || 'NENHUM campo óbvio -- ver chaves_disponiveis_no_fixture',
    torneios_distintos_encontrados: torneiosDistintos,
    batching_parece_funcionar: idsTestados.length > 1 && torneiosDistintos ? torneiosDistintos.length > 1 : null,
    chaves_disponiveis_no_fixture: resposta[0] ? Object.keys(resposta[0]) : [],
    total_markets_no_fixture_de_amostra: marketIds.length,
    market_ids_com_nome: marketIds.map((id) => ({ marketId: id, marketName: nomePorMarketId[id] || '(fora da cache local)' })),
    // Só os 6 primeiros mercados, em detalhe, pra não devolver um payload
    // gigante -- o suficiente pra ver o formato de outcomes/bookmakerOutcomeId.
    amostra_markets_em_detalhe: Object.fromEntries(marketIds.slice(0, 6).map((id) => [id, marketsFixture[id]])),
  };
}

// ============================================================
// TAREFA: odds-historico-descobrir — FASE 1 do backfill de odds de rodadas
// JÁ ENCERRADAS (pedido do usuário: "importar odds de todas as rodadas
// anteriores do Brasileirão").
//
// Diferente de tarefa=odds (que lê /v4/odds-by-tournaments, o quadro inteiro
// do torneio numa chamada, mas só pega jogo AGENDADO com linha aberta),
// odds de jogo ENCERRADO exigem dois endpoints novos, nunca usados neste
// projeto até agora — por isso essa fase só DESCOBRE e cacheia a resposta
// crua, sem gastar nada além de 2 chamadas, pra inspecionar o formato real
// antes de escrever o parser da fase 2 (regra do projeto: nunca adivinhar
// formato de resposta de API paga e escrever parser às cegas):
//   1. /v4/fixtures?tournamentId=X&statusId=2 (Finished) -- lista TODAS as
//      partidas finalizadas do torneio numa chamada só (com fixtureId de
//      cada uma), diferente de /v4/historical-odds que é por partida.
//   2. /v4/historical-odds?fixtureId=X&bookmakers=pinnacle,bet365,betano --
//      odds da PRIMEIRA fixture da lista acima, só como amostra.
// ============================================================
// BUG REAL corrigido: `from` era fixo em '2026-01-01' (só fazia sentido pro
// caso original, Brasileirão 2026) -- pra qualquer liga com histórico mais
// antigo (ex.: Champions League, temporadas 2023/2024), a chamada a
// /v4/fixtures nem devolvia essas partidas, e o backfill silenciosamente só
// pegava uma fração pequena do total. Calculado a partir da PRIMEIRA partida
// finalizada da liga no nosso banco (com folga de 7 dias) -- mesmo custo de
// cota (é 1 chamada só, cacheada, independente do tamanho da janela).
async function primeiraDataFinalizadaIso(supabase, ligaId) {
  const { data } = await supabase.from('matches').select('match_date')
    .eq('league_id', ligaId).eq('status', 'finished')
    .order('match_date', { ascending: true }).limit(1);
  const primeira = data?.[0]?.match_date ? new Date(data[0].match_date) : new Date('2019-01-01T00:00:00Z');
  primeira.setUTCDate(primeira.getUTCDate() - 7);
  return primeira.toISOString();
}

async function tarefaOddsHistoricoDescobrir(supabase, apiKey, ligaId) {
  const { data: mapa } = await supabase.from('liga_oddspapi_tournament').select('tournament_id, tournament_name').eq('league_id', ligaId).maybeSingle();
  if (!mapa) return { error: `Liga ${ligaId} ainda não tem torneio da OddsPapi resolvido em liga_oddspapi_tournament — rode tarefa=odds-descobrir e confirme manualmente primeiro.` };

  const fixtures = await chamarOddspapi('/v4/fixtures', {
    tournamentId: mapa.tournament_id,
    statusId: 2, // Finished
    from: await primeiraDataFinalizadaIso(supabase, ligaId),
    to: new Date().toISOString(),
  }, apiKey);

  if (!Array.isArray(fixtures) || fixtures.length === 0) {
    return { error: 'Nenhuma fixture finalizada retornada por /v4/fixtures — confira tournament_id/statusId/from/to.', resposta_bruta: fixtures };
  }

  await supabase.from('oddspapi_cache').upsert(
    { chave: `fixtures_finalizadas_liga_${ligaId}`, valor: fixtures, atualizado_em: new Date().toISOString() },
    { onConflict: 'chave' }
  );

  // Tenta a fixture mais RECENTE primeiro (de trás pra frente) -- achado
  // real: fixtures[0] (a mais antiga da janela) pode não ter histórico
  // salvo pela OddsPapi (HTTP 404 "No historical odds found"), mesmo
  // dentro do período coberto por /v4/fixtures. Sem travar a chamada
  // inteira por causa de UMA amostra sem sorte.
  let amostraFixture = null, historico = null, erroAmostra = null;
  const LIMITE_TENTATIVAS_AMOSTRA = 10; // não travar a function inteira numa sequência azarada de 404s
  let tentativas = 0;
  for (let i = fixtures.length - 1; i >= 0 && !historico && tentativas < LIMITE_TENTATIVAS_AMOSTRA; i--) {
    if (!fixtures[i]?.fixtureId) continue;
    tentativas++;
    try {
      // Só o primeiro lote (limite de 3 bookmakers por chamada da OddsPapi)
      // -- essa função é só amostra/inspeção de formato, não precisa das 6
      // casas pra isso.
      historico = await chamarOddspapi('/v4/historical-odds', {
        fixtureId: fixtures[i].fixtureId,
        bookmakers: LOTES_BOOKMAKERS_HISTORICO[0].join(','),
      }, apiKey);
      amostraFixture = fixtures[i];
    } catch (e) {
      erroAmostra = e.message;
    }
  }

  if (!historico) {
    return { liga_id: ligaId, torneio: mapa.tournament_name, total_fixtures_finalizadas: fixtures.length, error: `Nenhuma fixture teve histórico disponível (última tentativa: ${erroAmostra}).` };
  }

  await supabase.from('oddspapi_cache').upsert(
    { chave: `historico_amostra_liga_${ligaId}`, valor: historico, atualizado_em: new Date().toISOString() },
    { onConflict: 'chave' }
  );

  return {
    liga_id: ligaId,
    torneio: mapa.tournament_name,
    total_fixtures_finalizadas: fixtures.length,
    amostra_fixture: amostraFixture,
    amostra_historico: historico,
  };
}

// ============================================================
// TAREFA: odds-historico — FASE 2 do backfill de rodadas encerradas.
//
// Formato real de /v4/historical-odds (confirmado rodando odds-historico-
// descobrir em produção, NÃO estava documentado com esse nível de detalhe):
//   { fixtureId, bookmakers: { <slug>: { markets: { <marketId>: { outcomes:
//     { <outcomeId>: { players: { "0": [ {createdAt, price, active, ...},
//     ... MUITOS pontos, é o histórico de movimento de linha inteiro, não só
//     o fechamento ] } } } } } } }
// -- bem mais pesado que /v4/odds-by-tournaments (um fixture sozinho já
// passou de 3,9MB de JSON na amostra). Por isso só o preço de createdAt
// mais recente de cada outcome é aproveitado (snapshot='closing' em
// odds_market — não vale a pena guardar o histórico de movimento inteiro
// aqui, isso é objetivo futuro separado, já documentado em CONTEXTO).
//
// MERCADOS: extração GENÉRICA (pedido do usuário: "máximo de mercados
// possíveis"), não hardcoded pros 3 originais (1X2/O-U2.5/BTTS) -- pra
// CADA marketId presente na resposta de /v4/historical-odds do bookmaker,
// resolve marketName/handicap/period via a cache `markets` (fase 1 do sync
// ao vivo) e monta um rótulo de mercado genérico (slugMercadoHistorico),
// igual em espírito ao normalizar_mercado() de capturar_odds_oddsportal.py.
// Markets com playerProp=true (mercados de jogador individual, tipo "First
// Goal Scorer") são pulados -- o schema de odds_market é por partida, não
// tem coluna de jogador. Pra não duplicar as linhas dos 3 mercados originais
// já gravados antes desta mudança (nenhuma constraint UNIQUE em
// odds_market), o insert é ADITIVO: consulta as combinações
// (bookmaker,market,selection) já existentes pra aquela partida ANTES de
// montar as linhas novas, e pula qualquer uma que já exista -- nunca
// deleta/sobrescreve nada.
//
// CUSTO DE COTA: **corrigido, achado real testando em produção (14/08)** --
// /v4/historical-odds é um endpoint GRÁTIS da OddsPapi (confirmado na doc
// oficial, oddspapi.io/en/docs/requests-and-quota: "always free, calls
// never increment your request count"), ao contrário do que a versão
// anterior deste comentário assumia. Só /v4/fixtures (pra listar as
// partidas finalizadas, 1 chamada por liga, cacheada) consome cota de
// verdade. O limite real de MAX_FIXTURES_HISTORICO_POR_CHAMADA não é cota
// -- é o timeout de 60s da function na Vercel (vercel.json) combinado com
// o cooldown de 5s documentado da OddsPapi entre chamadas.
//
// fixtureId (por partida, diferente do tournamentId usado no sync ao vivo)
// é descoberto batendo /v4/fixtures (1 chamada só, cacheada) com nossos
// jogos internos por DATA + NOME DE TIME (mesma técnica de tarefaOddsSync).
// Idempotente via match_source_ids -- mesmo padrão de partidas-fotmob, mas
// com DOIS sources por partida (fica registrado o que já foi extraído da
// MESMA resposta da API, já que ela já traz todos os mercados de uma vez):
// 'oddspapi_historico' (1X2 + Over/Under 2.5) e 'oddspapi_historico_btts'
// (BTTS). Uma partida só é considerada "pronta" (fora da lista de
// pendentes) quando os DOIS sources existem -- então uma partida que já
// tinha só o core processado (de antes do BTTS existir) volta a ser elegível
// automaticamente, chama a API de novo, mas só GRAVA/marca o que ainda
// faltava (sem duplicar 1X2/OU25 já gravados).
//
// CUSTO DE COTA: 1 chamada por partida (cooldown de 5s da própria OddsPapi,
// documentado), independente de quantos mercados ainda faltam pra ela --
// backfill de uma temporada inteira (~180 jogos) consome quase toda a cota
// mensal (250 free). Processa no máximo MAX_FIXTURES_HISTORICO_POR_CHAMADA
// por chamada, avisado ao usuário antes de rodar (ver skill do projeto,
// disciplina de cota).
// ============================================================
// Reduzido de 6 pra 4: desde que BOOKMAKERS_HISTORICO passou de 3 pra 6
// casas, cada fixture agora precisa de 2 chamadas a /v4/historical-odds
// (ver LOTES_BOOKMAKERS_HISTORICO logo abaixo) -- mantém margem segura
// contra o timeout de 60s da function. Reduzido de novo pra 3 (de 4) depois
// de trocar o cooldown entre lotes pra 5s uniforme (era 1s, insuficiente) --
// com 3 fixtures x 2 lotes = 6 chamadas, 5 intervalos de 5s = 25s + latência
// real da API, ainda com folga segura.
// Subido de novo pra 4 (pedido do usuário, "mais rápido"): medido em
// produção rodando o backfill completo por horas -- rodada com 3 fixtures
// leva ~36-39s de verdade (25s de cooldown fixo + ~11-14s de
// processamento/rede), bem abaixo do teto de 60s. Com 4 fixtures x 2 lotes =
// 8 chamadas, 7 intervalos de 5s = 35s + processamento ~16s ≈ 51s -- ainda
// dentro do teto, mas com menos folga (timeout ocasional é só uma rodada
// desperdiçada, idempotente, tenta de novo sozinho na próxima chamada).
const MAX_FIXTURES_HISTORICO_POR_CHAMADA = 4;

// BUG REAL corrigido (achado testando em produção antes de rodar o backfill
// inteiro): a maioria dos pontos de /v4/historical-odds é preço AO VIVO,
// capturado durante e depois da partida (na amostra de 1 fixture, 201 de
// 215 pontos eram pós-apito inicial) -- pegar só o createdAt mais recente
// pega o preço já quase decidido pelo placar (ex.: 1.01 pro time que já
// está vencendo de goleada no fim do jogo), não o fechamento pré-jogo de
// verdade. Corrigido filtrando só pontos com createdAt <= horário do apito
// (kickoffIso, vem de fx.startTime) antes de escolher o mais recente.
function extrairPrecoFechamento(outcomeData, kickoffIso) {
  const pontos = outcomeData?.players?.['0'];
  if (!Array.isArray(pontos) || pontos.length === 0) return null;
  const kickoffMs = new Date(kickoffIso).getTime();
  let maisRecente = null;
  for (const p of pontos) {
    // odd decimal válida é sempre > 1 -- achado real rodando o backfill em
    // produção: bet365 às vezes grava price:0 (mercado suspenso/indisponível
    // naquele instante), e sem esse filtro o preço "mais recente" escolhido
    // podia ser esse 0 em vez do último preço de verdade.
    if (p.price == null || p.price <= 1) continue;
    const ts = new Date(p.createdAt).getTime();
    if (ts > kickoffMs) continue; // preço ao vivo/pós-jogo -- descarta
    if (!maisRecente || ts > new Date(maisRecente.createdAt).getTime()) maisRecente = p;
  }
  return maisRecente?.price ?? null;
}

// OddsPapi period (sportId=10 -- futebol) só usa 'fulltime'/'p1'/'p2' na
// prática (achado real inspecionando a cache `markets` inteira) -- p1/p2
// são os tempos do jogo.
const SUFIXO_PERIODO_HISTORICO = { p1: '1h', p2: '2h' };

// Os 3 mercados já usados pelo projeto desde antes desta mudança mantêm o
// rótulo curto de sempre (pra não duplicar linha já gravada por eles);
// qualquer mercado novo (pedido do usuário: "máximo de mercados possíveis")
// vira um slug genérico do nome/period/handicap.
const GERADOR_ROTULO_MERCADO_CONHECIDO = {
  'Full Time Result': () => '1X2',
  'Over Under Full Time': (h) => `over_under_${h}`,
  'Both Teams To Score': () => 'btts',
};

function slugTextoHistorico(s) {
  return (s || '').toString().trim().toLowerCase()
    .normalize('NFD').replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
}

// Mesmo espírito do normalizar_mercado() de capturar_odds_oddsportal.py:
// nunca hardcodar a lista inteira de mercados, só normalizar o que a API
// já descreve nos metadados (marketName/handicap/period).
function slugMercadoHistorico(marketName, handicap, period) {
  const gerador = GERADOR_ROTULO_MERCADO_CONHECIDO[marketName];
  if (gerador && (!period || period === 'fulltime')) return gerador(handicap);
  const base = slugTextoHistorico(marketName);
  const sufixoPeriodo = SUFIXO_PERIODO_HISTORICO[period] ? `_${SUFIXO_PERIODO_HISTORICO[period]}` : '';
  const sufixoHandicap = (handicap != null && handicap !== 0) ? `_${handicap}` : '';
  return `${base}${sufixoPeriodo}${sufixoHandicap}`;
}

const ROTULOS_SELECAO_CONHECIDOS_HISTORICO = { '1': 'home', x: 'draw', '2': 'away', over: 'over', under: 'under', yes: 'yes', no: 'no', odd: 'odd', even: 'even' };
function slugSelecaoHistorico(outcomeName) {
  const norm = (outcomeName || '').toString().trim().toLowerCase();
  return ROTULOS_SELECAO_CONHECIDOS_HISTORICO[norm] || slugTextoHistorico(outcomeName);
}

async function tarefaOddsHistorico(supabase, apiKey, { ligaId, temporada, limite }) {
  const limiteReal = Math.min(parseInt(limite, 10) || MAX_FIXTURES_HISTORICO_POR_CHAMADA, MAX_FIXTURES_HISTORICO_POR_CHAMADA);

  const { data: mapa } = await supabase.from('liga_oddspapi_tournament').select('tournament_id, tournament_name').eq('league_id', ligaId).maybeSingle();
  if (!mapa) return { error: `Liga ${ligaId} ainda não tem torneio da OddsPapi resolvido em liga_oddspapi_tournament — rode tarefa=odds-descobrir e confirme manualmente primeiro.` };

  const { data: mercadosCacheRaw } = await supabase.from('oddspapi_cache').select('valor').eq('chave', 'markets').maybeSingle();
  const mercadosPorId = new Map((mercadosCacheRaw?.valor || []).filter((m) => !m.playerProp).map((m) => [String(m.marketId), m]));
  if (mercadosPorId.size === 0) return { error: 'Cache `markets` vazia/inválida — rode tarefa=odds-descobrir de novo.' };

  const fixtures = await buscarOuCache(supabase, `fixtures_finalizadas_liga_${ligaId}`, async () => chamarOddspapi('/v4/fixtures', {
    tournamentId: mapa.tournament_id,
    statusId: 2, // Finished
    from: await primeiraDataFinalizadaIso(supabase, ligaId),
    to: new Date().toISOString(),
  }, apiKey));
  if (!Array.isArray(fixtures) || fixtures.length === 0) {
    return { error: 'Cache de fixtures finalizadas vazia/inválida — rode tarefa=odds-historico-descobrir de novo (ou apague oddspapi_cache pra essa chave se a temporada mudou).' };
  }

  // SEM filtro de season -- pedido do usuário: processar o máximo de
  // temporadas possível por chamada, não só a mais recente (a fixtures
  // cache já cobre a janela inteira desde a partida mais antiga finalizada
  // no nosso banco). Paginado de verdade (buscarTudoPaginado) já que sem o
  // filtro de season uma liga grande passa fácil de 1000 linhas.
  //
  // round IS NOT NULL foi REMOVIDO daqui -- achado real testando as 9 ligas
  // recém-mapeadas (Libertadores/Sudamericana/Copa do Brasil/Champions/
  // Club World Cup/Copa América/Eurocopa/Copa do Mundo/Série B): esse
  // filtro (pensado só pro caso do Brasileirão, com jogo triplicado por
  // fonte) zerava CINCO delas por completo (Série B, Copa do Brasil, Club
  // World Cup, Copa América, Intercontinental Cup nunca têm round
  // preenchido) -- e nenhuma delas tem duplicata de verdade (confirmado
  // por query: total de linhas bate 1:1 com jogos distintos por
  // home/away/data/temporada), só a Copa Sudamericana tem (963 linhas,
  // 849 jogos distintos). Trocado por deduplicação de verdade em memória
  // (prefere a linha com round quando existe mais de uma pro mesmo jogo,
  // senão pega a primeira) -- resolve o motivo original do filtro sem
  // excluir ligas que nunca tiveram round preenchido nessa fonte.
  // BUG REAL corrigido (achado monitorando o backfill em produção): sem
  // .order() aqui, o Postgres não garante a mesma ordem de linhas entre
  // chamadas -- pra ligas com jogo duplicado por chave (home/away/data/
  // temporada) e round nulo nos dois lados (só a Copa Sudamericana tem
  // isso), o "representante" escolhido em jogosPorChave podia trocar de
  // chamada pra chamada. Resultado: o match_id marcado completo numa
  // rodada não batia com o representante da rodada seguinte, então
  // `idsCompletos` nunca reconhecia progresso anterior (ficava sempre ~0)
  // -- a liga reprocessava as mesmas poucas partidas mais recentes pra
  // sempre, sem nunca avançar (confirmado: match_source_ids tinha só 6
  // linhas completas pra liga 46 depois de dezenas de rodadas). .order()
  // também deixa a paginação de buscarTudoPaginado seguro pra ligas
  // grandes (>1000 finalizadas), que sem ordem explícita não tem garantia
  // de retornar cada linha exatamente uma vez entre páginas.
  const todosOsJogos = await buscarTudoPaginado(() => {
    let q = supabase.from('matches')
      .select('id, season, round, match_date, home:teams!matches_home_team_id_fkey(id,name), away:teams!matches_away_team_id_fkey(id,name)')
      .eq('league_id', ligaId).eq('status', 'finished').order('id', { ascending: true });
    if (temporada) q = q.eq('season', temporada);
    return q;
  });
  const jogosPorChave = new Map();
  for (const m of todosOsJogos) {
    const chave = `${m.home?.id}|${m.away?.id}|${new Date(m.match_date).toISOString().slice(0, 10)}|${m.season}`;
    const existente = jogosPorChave.get(chave);
    if (!existente || (m.round != null && existente.round == null)) jogosPorChave.set(chave, m);
  }
  const nossosJogos = [...jogosPorChave.values()];

  // BUG REAL corrigido (achado monitorando o backfill em produção): essa
  // query buscava o `source='oddspapi_historico_completo' de TODAS as
  // ligas numa chamada só, sem paginação -- clássico truncamento silencioso
  // de 1000 linhas do PostgREST (gotcha já documentado no projeto). Com o
  // backfill rodando, o total global passou de 1000 linhas nesta mesma
  // sessão, e a liga cujas linhas completas caíssem fora da primeira
  // "página" ficava com `idsCompletos` sempre vazio -- reprocessando as
  // mesmas poucas partidas mais recentes pra sempre, sem nunca reconhecer
  // progresso já feito (confirmado: Copa Sudamericana, 6 partidas
  // completas no banco, `ja_importados_antes` sempre retornando 0). Trocado
  // por busca paginada e já filtrada só pelos ids desta liga (mais barato
  // também -- não precisa mais buscar o total global a cada chamada).
  const idsNossosJogos = nossosJogos.map((m) => m.id);
  const completosRows = await buscarTudoPaginadoIn(idsNossosJogos, (lote) =>
    supabase.from('match_source_ids').select('match_id').eq('source', 'oddspapi_historico_completo').in('match_id', lote)
  );
  const idsCompletos = new Set(completosRows.map((r) => r.match_id));

  // Mais RECENTE primeiro -- achado real testando em produção: sem filtro
  // de season, a ordem crua de /v4/fixtures é cronológica ASCENDENTE (mais
  // antiga primeiro), e a OddsPapi não tem retenção de histórico até a
  // partida mais antiga do nosso banco (ex.: Libertadores 2019-2022 dá 404
  // "No historical odds found" em toda tentativa). Processar do mais novo
  // pro mais antigo maximiza sucesso por chamada e naturalmente processa o
  // "máximo de temporadas possível" que a API realmente tiver, em vez de
  // gastar o limite de fixtures da chamada em partidas fadadas a falhar.
  const fixturesRecentesPrimeiro = [...fixtures].sort((a, b) => new Date(b.startTime) - new Date(a.startTime));

  const pendentes = [];
  for (const fx of fixturesRecentesPrimeiro) {
    if (!fx.startTime || pendentes.length >= limiteReal) continue;
    const partida = nossosJogos.find((m) => {
      if (idsCompletos.has(m.id) || pendentes.some((p) => p.match.id === m.id)) return false;
      const diffHoras = Math.abs(new Date(m.match_date) - new Date(fx.startTime)) / 3600000;
      if (diffHoras > 36) return false;
      return nomesBatem(m.home?.name, fx.participant1Name) && nomesBatem(m.away?.name, fx.participant2Name);
    });
    if (partida) pendentes.push({ fixtureId: fx.fixtureId, startTime: fx.startTime, match: partida });
  }

  const totalFinalizadosLocal = nossosJogos.length;
  if (pendentes.length === 0) {
    return { liga_id: ligaId, temporada_filtro: temporada || 'todas', torneio: mapa.tournament_name, mensagem: 'Nenhuma partida pendente encontrada.', total_finalizados_local: totalFinalizadosLocal, ja_importados: idsCompletos.size };
  }

  // Existência prévia (bookmaker,market,selection) por partida -- garante
  // que reprocessar uma partida que já tinha só os 3 mercados originais
  // (época anterior a esta mudança) NUNCA duplica essas linhas, só ADICIONA
  // os mercados novos que ainda não existiam. Nunca deleta/sobrescreve nada.
  const idsPendentesAgora = pendentes.map((p) => p.match.id);
  const { data: existentesRows } = await supabase.from('odds_market')
    .select('match_id,bookmaker,market,selection')
    .in('match_id', idsPendentesAgora).eq('snapshot', 'closing');
  const jaExiste = new Set((existentesRows || []).map((r) => `${r.match_id}|${r.bookmaker}|${r.market}|${r.selection}`));

  const esperar = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  let processados = 0, sucesso = 0, semHistorico = 0, chamadasFeitas = 0;
  const falhas = [];

  for (const { fixtureId, startTime, match } of pendentes) {
    processados++;
    const linhas = [];
    const agora = new Date().toISOString();
    let algumComDado = false;
    let erroTransitorio = null;

    // 1 chamada por LOTE de até 3 casas (limite real da OddsPapi, ver
    // LOTES_BOOKMAKERS_HISTORICO) -- "No historical odds found" num lote
    // específico não é fatal (só significa que aquelas casas não têm essa
    // partida), guarda só erro de verdade (ex.: rate limit) pra decidir
    // depois se marca a partida como completa ou deixa pra retentar.
    //
    // Cooldown UNIFORME de 5s entre QUALQUER chamada a /v4/historical-odds
    // (achado real testando em produção: 1s entre lotes da mesma partida
    // não bastava, a OddsPapi pediu "wait 2.3-2.75 seconds" via 429 --
    // contador único (chamadasFeitas) em vez de dois cooldowns diferentes
    // por fixture/por lote, pra não ter zona cinzenta entre os dois).
    for (let i = 0; i < LOTES_BOOKMAKERS_HISTORICO.length; i++) {
      if (chamadasFeitas > 0) await esperar(5000);
      chamadasFeitas++;
      try {
        const historico = await chamarOddspapi('/v4/historical-odds', { fixtureId, bookmakers: LOTES_BOOKMAKERS_HISTORICO[i].join(',') }, apiKey);
        for (const bookmakerSlug of LOTES_BOOKMAKERS_HISTORICO[i]) {
          const bdata = historico.bookmakers?.[bookmakerSlug];
          if (!bdata) continue;
          algumComDado = true;
          const bookmaker = nomeCanonicoBookmaker(bookmakerSlug); // ex.: 'williamhill' -> 'william_hill', pra somar no mesmo rótulo já usado via football-data.co.uk

          for (const [marketId, marketData] of Object.entries(bdata.markets || {})) {
            const info = mercadosPorId.get(marketId);
            if (!info) continue; // marketId fora da cache -- pula sem travar o resto
            const market = slugMercadoHistorico(info.marketName, info.handicap, info.period);

            for (const [outcomeId, outcomeData] of Object.entries(marketData?.outcomes || {})) {
              const outcomeInfo = (info.outcomes || []).find((o) => String(o.outcomeId) === String(outcomeId));
              if (!outcomeInfo) continue;
              const selection = slugSelecaoHistorico(outcomeInfo.outcomeName);
              const preco = extrairPrecoFechamento(outcomeData, startTime);
              if (preco == null) continue;
              const chave = `${match.id}|${bookmaker}|${market}|${selection}`;
              if (jaExiste.has(chave)) continue;
              jaExiste.add(chave);
              linhas.push({ match_id: match.id, bookmaker, market, selection, odds: preco, snapshot: 'closing', captured_at: agora });
            }
          }
        }
      } catch (erro) {
        // "No historical odds found" é um resultado ESTÁVEL (registro
        // histórico não muda com o tempo) -- só afeta as casas desse lote,
        // não impede os outros lotes/o resto da partida.
        if (!/No historical odds found/i.test(erro.message)) erroTransitorio = erro.message;
      }
    }

    if (linhas.length > 0) {
      const { error: erroInsert } = await supabase.from('odds_market').insert(linhas);
      if (erroInsert) { falhas.push({ match_id: match.id, motivo: erroInsert.message }); continue; }
    }

    if (erroTransitorio) {
      // erro de verdade (ex.: rate limit) em pelo menos um lote -- não
      // marca como completo, deixa pra retentar (as linhas que já vieram
      // dos outros lotes já foram gravadas acima, sem duplicar depois).
      falhas.push({ match_id: match.id, motivo: erroTransitorio });
      continue;
    }

    await supabase.from('match_source_ids').upsert(
      { match_id: match.id, source: 'oddspapi_historico_completo', source_id: fixtureId },
      { onConflict: 'match_id,source' }
    );
    if (algumComDado) sucesso++; else semHistorico++;
  }

  return {
    liga_id: ligaId,
    temporada_filtro: temporada || 'todas',
    torneio: mapa.tournament_name,
    total_finalizados_local: totalFinalizadosLocal,
    ja_importados_antes: idsCompletos.size,
    processados_agora: processados,
    sucesso,
    sem_historico_na_fonte: semHistorico || undefined,
    falhas: falhas.length ? falhas : undefined,
    // BUG REAL corrigido: usava `processados` (tudo que ENTROU no lote),
    // mas quem falhou (erroTransitorio/erroInsert, ver `falhas` acima) não
    // é marcado como completo em match_source_ids -- continua pendente pra
    // próxima chamada. Contar `processados` inteiro aqui subestimava o que
    // faltava sempre que havia falha na rodada (comum com o rate limit da
    // OddsPapi), fazendo o número parecer "travado" por várias rodadas
    // mesmo com progresso real acontecendo (só sucesso+sem_historico ficam
    // de fato completos).
    restantes_estimado: totalFinalizadosLocal - idsCompletos.size - (sucesso + semHistorico),
  };
}

// ============================================================
// TAREFA: odds — FASE 2 do sync de odds (OddsPapi): sincroniza de verdade.
//
// Captura o MÁXIMO possível por chamada: /v4/odds-by-tournaments devolve o
// quadro INTEIRO do torneio (todos os jogos que já têm linha aberta pra
// aquele bookmaker) numa única chamada — já é o mais eficiente possível, não
// tem "paginar por jogo". A resposta já traz nome dos times e horário do
// jogo junto com as odds — não precisa de uma chamada extra em /v4/fixtures.
//
// CUSTO REAL, confirmado testando em produção (tarefa=odds-sync-diagnostico,
// 2026-08-15) — MUDOU o desenho desta tarefa por completo:
//   1. `tournamentIds` (plural) ACEITA vários torneios numa chamada só,
//      separados por vírgula (achado real, batendo 2 torneios numa
//      chamada e confirmando fixtures de AMBOS na resposta, campo
//      `tournamentId` por fixture identifica de qual veio) — diferente de
//      `bookmaker` (singular), que já tinha sido confirmado como 1-valor-só.
//      MAS com um teto real (também só descoberto testando, não documentado
//      antes): HTTP 400 "Too many tournament IDs specified... maximum of 5"
//      acima de 5 IDs por chamada. Por isso esta tarefa foi generalizada de
//      "1 liga por chamada" pra "TODAS as ligas com liga_oddspapi_tournament
//      resolvido, em lotes de até 5 torneios por chamada" (`loteados`, ver
//      tarefaOddsSyncLote) — custo escala em DEGRAUS de 5 ligas, não mais
//      1:1 por liga: 16 ligas resolvidas hoje = 4 lotes × 3 bookmakers = 12
//      chamadas por rodada do cron (contra 48 se fosse 1 chamada por liga, e
//      contra as 18 de antes desta mudança pra cobrir só as 6 domésticas).
//   2. `verbosity=3` já devolve TODOS os mercados do fixture numa chamada só
//      (confirmado: 99 mercados distintos numa amostra de 2 ligas -- Asian/
//      European Handicap, escanteios, cartões, placar exato, 1º tempo,
//      etc, mesma faixa já vista no backfill histórico) — generalizado pra
//      gravar TODOS eles (reaproveitando slugMercadoHistorico/
//      slugSelecaoHistorico, mesmo rótulo do backfill histórico), não só
//      os 3 hardcoded de antes. BUG REAL corrigido de passagem: o parser
//      antigo (`acharMercadoPorNome` + filtro por `bookmakerOutcomeId`)
//      pegava só o PRIMEIRO marketId chamado "Over Under Full Time" —
//      só que CADA linha de gols (0.5/1.5/2.5/3.5...) é um marketId
//      SEPARADO com esse mesmo nome, então na prática o filtro
//      `id.startsWith('2.5/')` batia só quando a linha 2.5 por acaso era a
//      primeira presente naquele fixture (ordem numérica ascendente dos
//      marketId, não garantida) — capturava a 2.5 de forma inconsistente e
//      NUNCA capturava BTTS de verdade: `bookmakerOutcomeId` do mercado
//      "Both Teams To Score" NÃO é 'yes'/'no' (é um ID interno da casa,
//      tipo "1633789709") — só a chave do outcome dentro do mercado
//      (`outcomeId`, olhando a cache `markets`) é estável, exatamente como
//      o backfill histórico já fazia. O parser novo usa outcomeId+cache
//      pra TODOS os mercados, igual ao histórico -- resolve os dois bugs.
//
// JANELA DE CANDIDATOS: mesma pra TODAS as ligas do batch, sem escalonar por
// liga. Existia antes uma "janela adaptativa" (7/14/21 dias, alargando só
// se a liga tivesse poucos jogos no prazo mais curto) pensada pra quando o
// custo era 1 chamada POR LIGA -- fazia sentido evitar gastar uma chamada
// inteira numa liga com pouco jogo agendado. Depois do batching (várias
// ligas por chamada, ver tarefaOddsSyncLote), essa lógica parou de fazer
// sentido: a chamada já é feita pro LOTE inteiro independente da janela, e
// a única coisa que a janela curta ainda fazia era EXCLUIR jogo de verdade
// da lista local de candidatos pra casar -- **bug real confirmado em
// produção** (pedido do usuário, "essa limitação desnecessária já que o
// chamado é por grupos de ligas"): a Premier League achava >=3 jogos já nos
// primeiros 7 dias e parava de alargar, mas a chamada `/v4/odds-by-
// tournaments` não tem filtro de data nenhum -- a OddsPapi devolvia
// fixtures de semanas à frente, que nunca tinham candidato local pra casar
// (ficavam presos em "sem correspondência" à toa, mesmo já tendo odds reais
// abertas). Removida a escalada -- toda liga do batch usa a mesma janela
// fixa (JANELA_CANDIDATOS_DIAS), sem custo extra nenhum de fazer isso.
//
// Guarda com timestamp (captured_at, coluna que já existia) SEM sobrescrever
// o snapshot anterior — cada sync é um INSERT novo, não upsert — pra dar pra
// montar a curva de movimento de linha ao longo do tempo, como pedido.
// ============================================================

const JANELA_CANDIDATOS_DIAS = 21;

function normalizarTexto2(s) {
  return (s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/[^a-z0-9]/g, '');
}

// Apelidos SEM raiz/token em comum entre o nome cadastrado em `teams` e o
// nome usado pela OddsPapi -- mesma classe de bug já resolvida pra outras
// fontes (ver ALIASES_MANUAIS em sync-match-stats.js, ex.: QPR/West Brom).
// Achado comparando os 20 times do Brasileirão 2026 contra os 20 nomes
// distintos da OddsPapi (script local, não em produção): 6 de 20 não batem
// por substring — sigla nossa (FBC/FBPA/CA/AF/RB) vs nome mais por extenso
// ou sigla diferente da OddsPapi. Sem ambiguidade (comparação 1:1 direta
// entre as duas listas fechadas de 20 times), não precisa de confirmação
// time a time como o caso QPR (que tinha colisão entre ligas).
//
// BUG REAL corrigido (achado rodando o backfill de BTTS em produção): as
// duas entradas abaixo ('atleticomineiromg', 'caparanaensepr') são pro
// nome usado por /v4/fixtures (tarefaOddsHistorico) -- convenção DIFERENTE
// de /v4/odds-by-tournaments (tarefaOddsSync, de onde vieram as 6 entradas
// acima, ex. 'camineiro'). /v4/fixtures sufixa a UF no nome ("Atletico
// Mineiro MG", "CA Paranaense PR" -- confirmado nos 20 nomes distintos de
// `oddspapi_cache.fixtures_finalizadas_liga_1`); o sufixo de UF sozinho não
// quebra o casamento por substring pros outros 18 times (é só um sufixo a
// mais no nome já contido), mas quebra pra esses 2 porque nosso nome local
// ("Clube Atlético Mineiro", "Club Athletico Paranaense") tem um prefixo
// ("Clube"/"Club") sem raiz em comum com a sigla curta da OddsPapi ("CA").
// Sem esse fix, TODAS as partidas desses 2 times ficavam presas como "sem
// casamento" em tarefaOddsHistorico -- inclusive 5 sem NENHUM mercado
// extraído (nem 1X2/O-U), não só sem BTTS.
const ALIASES_ODDSPAPI = {
  'camineiro': 'atleticomineiro',
  'chapecoenseaf': 'chapecoense',
  'coritibafbc': 'coritibafc',
  'gremiofbpa': 'gremiofbportoalegrense',
  'rbbragantino': 'redbullbragantino',
  'sccorinthianspaulista': 'sccorinthians',
  'atleticomineiromg': 'clubeatleticomineiro',
  'caparanaensepr': 'clubathleticoparanaense',
};
// Segunda camada de casamento, por TOKEN em vez de substring cru na string
// colapsada -- a checagem acima (ALIASES_ODDSPAPI + substring) continua
// intacta como primeira tentativa (já validada em produção pros apelidos
// brasileiros), essa aqui só entra como fallback OR, nunca substitui nada.
// BUG REAL corrigido (achado auditando Champions League a pedido do
// usuário, 2026-08-17): 375 das 503 partidas finalizadas da liga nunca
// batiam por substring -- ou por causa de uma palavra extra no meio do
// nome nosso ("Club Atlético DE Madrid" vs "Atletico Madrid" -- o "de"
// quebra o substring mesmo com as mesmas palavras dos dois lados) ou por
// nome/cidade traduzida ou abreviação sem raiz em comum ("FC Bayern
// München" vs "Bayern Munich", "AC Sparta Praha" vs "Sparta Prague").
// tarefaOddsHistorico não tinha como distinguir isso de "liga realmente
// sem mais dado disponível" -- reportava "Nenhuma partida pendente
// encontrada" (liga "concluída") com só 25% de fato importado, e o cron
// seguia pra próxima liga sozinho, mascarando o problema indefinidamente.
const ALIASES_TOKEN_ODDSPAPI = {
  munchen: 'munich',        // FC Bayern München / Bayern Munich
  praha: 'prague',          // AC Sparta Praha, SK Slavia Praha / ...Prague
  olympiakos: 'olympiacos', // PAE Olympiakos SFP / Olympiacos Piraeus
  paphos: 'pafos',          // Paphos FC / Pafos FC
  internazionale: 'inter',  // FC Internazionale Milano / Inter Milano
};
// Pares de nome INTEIRO sem nenhum token em comum entre o nosso cadastro e
// a OddsPapi (sigla vs. nome da cidade, sigla diferente) -- não dá pra
// resolver por alias de token isolado, o par inteiro precisa ser
// equivalenciado.
const ALIASES_NOME_ODDSPAPI = {
  'sporting clube de portugal': 'sporting cp',
  'celtic fc': 'celtic glasgow',
  'galatasaray sk': 'galatasaray istanbul',
  'juventus fc': 'juventus turin',
  'athletic club': 'athletic bilbao',
  'fk shakhtar donetsk': 'fc shakhtar donetsk',
  'fk kairat': 'fc kairat almaty',
  'pae olympiakos sfp': 'olympiacos piraeus',
  'real betis balompie': 'real betis seville',
  'real sociedad de futbol': 'real sociedad san sebastian',
  // Os 14 abaixo vieram de uma auditoria cruzada (2026-08-17, a pedido do
  // usuário depois do fix de La Liga): comparei, pra cada uma das 14 ligas
  // mapeadas, os times sem odds importadas contra os nomes já presentes em
  // `oddspapi_cache.fixtures_finalizadas_liga_X` da MESMA liga -- ou seja,
  // são pares onde o dado já está cacheado (não é retenção/ausência da
  // OddsPapi), só nunca casava por nome. Cidade traduzida (Köln/Cologne,
  // København/Copenhagen), sigla sem raiz comum (AFC Ajax/Ajax Amsterdam,
  // BSC Young Boys/Young Boys Bern, LAFC/Los Angeles FC), sufixo do
  // clube preservado só de um lado (SS Lazio/Lazio Rome, AC Pisa
  // 1909/Pisa SC, Stade Brestois 29/Stade Brest 29 -- essa última também
  // destrava Ligue 1) e país com nome diferente na Copa do Mundo FIFA
  // (South Korea/Korea Republic, Turkey/Turkiye, United States/USA).
  'afc ajax': 'ajax amsterdam',
  'bsc young boys': 'young boys bern',
  'fc k benhavn': 'fc copenhagen',
  'stade brestois 29': 'stade brest 29',
  'ss lazio': 'lazio rome',
  'ac pisa 1909': 'pisa sc',
  '1 fc koln': '1 fc cologne',
  'south korea': 'korea republic',
  'turkey': 'turkiye',
  'united states': 'usa',
  'lafc': 'los angeles fc',
  'wydad casablanca': 'wydad ac',
  'sport lisboa e benfica': 'sl benfica',
  'fk bod glimt': 'bodoe glimt',
};
function normalizarNomeOddsPapiTokens(s) {
  return (s || '')
    .toLowerCase()
    .normalize('NFD').replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
}
function nomesBatemPorToken(a, b) {
  let na = normalizarNomeOddsPapiTokens(a), nb = normalizarNomeOddsPapiTokens(b);
  na = ALIASES_NOME_ODDSPAPI[na] || na;
  nb = ALIASES_NOME_ODDSPAPI[nb] || nb;
  if (!na || !nb) return false;
  if (na === nb) return true;
  const tokensA = new Set(na.split(' ').map((t) => ALIASES_TOKEN_ODDSPAPI[t] || t));
  const tokensB = new Set(nb.split(' ').map((t) => ALIASES_TOKEN_ODDSPAPI[t] || t));
  return [...tokensA].every((t) => tokensB.has(t)) || [...tokensB].every((t) => tokensA.has(t));
}
function nomesBatem(a, b) {
  let x = normalizarTexto2(a), y = normalizarTexto2(b);
  x = ALIASES_ODDSPAPI[x] || x;
  y = ALIASES_ODDSPAPI[y] || y;
  if (x.length > 0 && y.length > 0 && (x.includes(y) || y.includes(x))) return true;
  return nomesBatemPorToken(a, b);
}

// Extrai TODAS as seleções de TODOS os mercados presentes num fixture do
// sync ao vivo (/v4/odds-by-tournaments, verbosity=3) -- generaliza o que
// antes só pegava 1X2/O-U 2.5/BTTS hardcoded. A chave de cada outcome
// dentro de `market.outcomes` É o outcomeId da cache `markets` (confirmado
// via tarefa=odds-sync-diagnostico em produção, 2026-08-15 -- mesma
// convenção já usada por tarefaOddsHistorico), NÃO o `bookmakerOutcomeId`
// aninhado (esse é um ID interno da casa, só é semântico por coincidência
// pro 1X2/over-under; pro BTTS é um número arbitrário tipo "1633789709").
// Reaproveita slugMercadoHistorico/slugSelecaoHistorico -- garante o MESMO
// rótulo de mercado/seleção entre sync ao vivo e backfill histórico.
function extrairLinhasOddsGenericas(marketsFixture, mercadosPorId) {
  const linhas = []; // { market, selection, odds }
  for (const [marketId, marketObj] of Object.entries(marketsFixture || {})) {
    if (marketObj?.marketActive === false) continue; // mercado inteiro suspenso agora
    const info = mercadosPorId.get(marketId);
    if (!info) continue; // marketId fora da cache -- pula sem travar o resto

    const market = slugMercadoHistorico(info.marketName, info.handicap, info.period);
    for (const [outcomeId, outcomeData] of Object.entries(marketObj.outcomes || {})) {
      const jogador = outcomeData?.players?.['0'];
      if (!jogador || jogador.price == null || jogador.price <= 1) continue;
      if (jogador.active === false) continue; // linha específica suspensa agora
      const outcomeInfo = (info.outcomes || []).find((o) => String(o.outcomeId) === outcomeId);
      if (!outcomeInfo) continue;
      linhas.push({ market, selection: slugSelecaoHistorico(outcomeInfo.outcomeName), odds: Number(jogador.price) });
    }
  }
  return linhas;
}

// Sincroniza odds ao vivo pra uma LISTA de ligas internas numa chamada
// batched por bookmaker (ver comentário acima -- tournamentIds aceita
// vários valores separados por vírgula, custo NÃO escala com o número de
// ligas). ligaIds sem torneio resolvido em liga_oddspapi_tournament são
// ignoradas silenciosamente (reportadas em `ligas_sem_torneio`).
async function tarefaOddsSyncLote(supabase, apiKey, ligaIds) {
  const { data: mapas } = await supabase.from('liga_oddspapi_tournament').select('league_id, tournament_id, tournament_name').in('league_id', ligaIds);
  const ligasSemTorneio = ligaIds.filter((id) => !(mapas || []).some((m) => m.league_id === id));
  if (!mapas || mapas.length === 0) {
    return { error: `Nenhuma das ligas pedidas (${ligaIds.join(', ')}) tem torneio da OddsPapi resolvido em liga_oddspapi_tournament — rode tarefa=odds-descobrir e confirme manualmente primeiro.` };
  }
  const mapaPorLiga = Object.fromEntries(mapas.map((m) => [m.league_id, m]));
  const mapaPorTorneio = Object.fromEntries(mapas.map((m) => [String(m.tournament_id), m]));
  const ligasResolvidas = mapas.map((m) => m.league_id);

  // Guarda simples (consulta local, sem custo de cota): só pula a chamada
  // batched inteira se NENHUMA liga pedida tiver jogo agendado dentro da
  // janela fixa (ex.: parada pra data FIFA afetando todo o lote de uma vez).
  const janelaAteIso = new Date(Date.now() + JANELA_CANDIDATOS_DIAS * 86400000).toISOString();
  const { count: totalAgendados } = await supabase.from('matches').select('id', { count: 'exact', head: true })
    .in('league_id', ligasResolvidas).eq('status', 'scheduled').lte('match_date', janelaAteIso);
  if (!totalAgendados) {
    return {
      ligas_pedidas: ligasResolvidas,
      ligas_sem_torneio: ligasSemTorneio.length ? ligasSemTorneio : undefined,
      pulado: true,
      motivo: `Nenhuma das ligas pedidas tem jogo agendado nos próximos ${JANELA_CANDIDATOS_DIAS} dias — sync pulado pra não gastar cota à toa.`,
    };
  }

  const { data: mercadosCacheRaw } = await supabase.from('oddspapi_cache').select('valor').eq('chave', 'markets').maybeSingle();
  const mercadosPorId = new Map((mercadosCacheRaw?.valor || []).filter((m) => !m.playerProp).map((m) => [String(m.marketId), m]));
  if (mercadosPorId.size === 0) return { error: 'Cache `markets` vazia/inválida — rode tarefa=odds-descobrir de novo.' };

  // MESMA janela pra todas as ligas do batch (ver comentário no topo da
  // seção) -- nenhum filtro extra por liga depois desta query.
  const { data: candidatosRaw } = await supabase.from('matches')
    .select('id, league_id, match_date, home:teams!matches_home_team_id_fkey(id,name), away:teams!matches_away_team_id_fkey(id,name)')
    .in('league_id', ligasResolvidas).eq('status', 'scheduled').lte('match_date', janelaAteIso);
  const candidatos = candidatosRaw || [];

  // LIMITE REAL da OddsPapi (achado testando em produção, não documentado
  // antes -- diferente do que o comentário anterior desta função presumia):
  // /v4/odds-by-tournaments aceita no MÁXIMO 5 tournamentIds por chamada --
  // HTTP 400 "Too many tournament IDs specified... maximum of 5" acima
  // disso. Com mais de 5 ligas resolvidas, cada bookmaker agora precisa de
  // vários lotes (ainda MUITO mais barato que 1 chamada por liga: 16 ligas
  // = 4 lotes de até 5 = 12 chamadas/rodada pras 3 casas, vs. 48 se fosse 1
  // chamada por liga, vs. as 18 de antes desta mudança pra só 6 ligas).
  const lotesTorneios = loteados(mapas.map((m) => m.tournament_id), 5);

  const resultado = {
    ligas_pedidas: mapas.map((m) => ({ league_id: m.league_id, torneio: m.tournament_name })),
    ligas_sem_torneio: ligasSemTorneio.length ? ligasSemTorneio : undefined,
    janela_dias: JANELA_CANDIDATOS_DIAS,
    jogos_candidatos: candidatos.length,
    lotes_de_torneios: lotesTorneios.length,
    por_bookmaker: [],
    linhas_inseridas: 0,
  };

  let primeiraChamada = true;
  for (const bookmaker of BOOKMAKERS_ALVO) {
    let casados = 0, semCasar = 0, mercadosExtraidos = 0, fixturesRecebidos = 0;
    const linhas = [];
    const errosLotes = [];
    const agora = new Date().toISOString();

    for (const loteIds of lotesTorneios) {
      // Rate limit real da OddsPapi (achado em produção): 429 se as chamadas
      // em /v4/odds-by-tournaments vierem muito rápido uma atrás da outra —
      // a doc pública menciona "500ms de cooldown", mas na prática levou
      // chamadas sequenciais a 429 mesmo com 800ms (visto quando um lote
      // anterior falhou rápido por erro de validação, sem gastar o tempo
      // normal de processamento). Além do cooldown fixo, retenta 1x em cima
      // de um 429 (a própria API devolve `retryMs` -- usa isso, com piso de
      // 1s, em vez de adivinhar um valor).
      if (!primeiraChamada) await new Promise((r) => setTimeout(r, 800));
      primeiraChamada = false;

      let fixtures;
      try {
        fixtures = await chamarOddspapi('/v4/odds-by-tournaments', { tournamentIds: loteIds.join(','), bookmaker, oddsFormat: 'decimal', verbosity: 3 }, apiKey);
      } catch (e) {
        const matchRetry = /HTTP 429/.test(e.message) ? e.message.match(/"retryMs":(\d+)/) : null;
        if (matchRetry) {
          await new Promise((r) => setTimeout(r, Math.max(1000, Number(matchRetry[1]))));
          try {
            fixtures = await chamarOddspapi('/v4/odds-by-tournaments', { tournamentIds: loteIds.join(','), bookmaker, oddsFormat: 'decimal', verbosity: 3 }, apiKey);
          } catch (e2) {
            errosLotes.push({ lote: loteIds, erro: e2.message });
            continue;
          }
        } else {
          errosLotes.push({ lote: loteIds, erro: e.message });
          continue;
        }
      }
      if (!Array.isArray(fixtures)) { errosLotes.push({ lote: loteIds, erro: 'resposta inesperada' }); continue; }
      fixturesRecebidos += fixtures.length;

      for (const fx of fixtures) {
        const dataFixture = fx.startTime ? new Date(fx.startTime) : null;
        const ligaDoFixture = mapaPorTorneio[String(fx.tournamentId)]?.league_id;
        const partida = candidatos.find((m) => {
          if (ligaDoFixture != null && m.league_id !== ligaDoFixture) return false;
          if (!dataFixture || !m.match_date) return false;
          const diffHoras = Math.abs(new Date(m.match_date) - dataFixture) / 3600000;
          if (diffHoras > 36) return false; // tolerância de fuso/horário de exibição
          return (nomesBatem(m.home?.name, fx.participant1Name) && nomesBatem(m.away?.name, fx.participant2Name));
        });
        if (!partida) { semCasar++; continue; }
        casados++;

        const marketsFixture = fx.bookmakerOdds?.[bookmaker]?.markets;
        if (!marketsFixture) continue;

        const extraidas = extrairLinhasOddsGenericas(marketsFixture, mercadosPorId);
        mercadosExtraidos += extraidas.length;
        extraidas.forEach((l) => linhas.push({ match_id: partida.id, bookmaker, market: l.market, selection: l.selection, odds: l.odds, snapshot: 'pre_closing', captured_at: agora }));
      }
    }

    if (linhas.length > 0) {
      const { error: erroInsert } = await supabase.from('odds_market').insert(linhas);
      if (erroInsert) { resultado.por_bookmaker.push({ bookmaker, erro: erroInsert.message, erros_lotes: errosLotes.length ? errosLotes : undefined }); continue; }
    }

    resultado.linhas_inseridas += linhas.length;
    resultado.por_bookmaker.push({
      bookmaker, fixtures_recebidos: fixturesRecebidos, jogos_casados: casados, sem_correspondencia: semCasar,
      linhas_extraidas: mercadosExtraidos, linhas_inseridas: linhas.length, erros_lotes: errosLotes.length ? errosLotes : undefined,
    });
  }

  return resultado;
}

// Wrapper de 1 liga só -- mantém a assinatura antiga (?tarefa=odds&liga_id=X).
async function tarefaOddsSync(supabase, apiKey, ligaId) {
  return tarefaOddsSyncLote(supabase, apiKey, [ligaId]);
}

// Roda o sync batched pra TODAS as ligas com torneio da OddsPapi resolvido
// (não só as 6 domésticas mais -- qualquer linha em liga_oddspapi_tournament,
// fonte de verdade dinâmica, sem precisar mexer em código pra adicionar liga
// nova) — usado pelo cron (vercel.json). Custo FIXO: 3 chamadas (1 por
// bookmaker), qualquer que seja o número de ligas incluídas (ver comentário
// de tarefaOddsSyncLote).
async function tarefaOddsTodas(supabase, apiKey) {
  const { data: mapas } = await supabase.from('liga_oddspapi_tournament').select('league_id');
  const ligaIds = (mapas || []).map((m) => m.league_id);
  if (ligaIds.length === 0) return { error: 'Nenhuma liga com torneio da OddsPapi resolvido em liga_oddspapi_tournament.' };
  return tarefaOddsSyncLote(supabase, apiKey, ligaIds);
}

const FOOTBALL_DATA_BASE_URL = 'https://api.football-data.org/v4';
const MAPA_STATUS_FOOTBALL_DATA = {
  SCHEDULED: 'scheduled', TIMED: 'scheduled',
  IN_PLAY: 'live', PAUSED: 'live',
  FINISHED: 'finished',
  POSTPONED: 'postponed', SUSPENDED: 'postponed',
  CANCELLED: 'cancelled',
};

async function chamarFootballData(caminho, apiKey) {
  const resposta = await fetch(`${FOOTBALL_DATA_BASE_URL}${caminho}`, { headers: { 'X-Auth-Token': apiKey } });
  const dados = await resposta.json();
  if (!resposta.ok) throw new Error(`HTTP ${resposta.status}: ${dados.message || ''}`);
  return dados;
}

// Mesma lógica de resolverOuCriarTime do sync-matches.js — upsert por
// external_id, cria o time na hora se for a primeira vez que aparece.
async function resolverOuCriarTimeFootballData(supabase, mapaExternalIdParaTeamId, timeApi) {
  const externalId = String(timeApi?.id ?? '');
  if (!externalId) return null;
  if (mapaExternalIdParaTeamId[externalId]) return mapaExternalIdParaTeamId[externalId];

  const { data: novoTime, error } = await supabase
    .from('teams')
    .upsert({ external_id: externalId, name: timeApi.name, crest_url: `https://crests.football-data.org/${externalId}.png` }, { onConflict: 'external_id' })
    .select('id')
    .single();
  if (error || !novoTime) return null;
  mapaExternalIdParaTeamId[externalId] = novoTime.id;
  return novoTime.id;
}

// Backfill de UMA temporada específica de uma competição já cadastrada em
// leagues (external_id=codigo) — diferente de sync-matches.js, que só
// mantém a temporada atual. Pensado pra popular histórico de uma competição
// nova (ex: Libertadores) sem depender do cron diário.
//
// Upsert de partidas em LOTE (não uma chamada por jogo) — testado com o
// Championship inglês (24 times, ~552 jogos/temporada, bem mais que os
// ~380 das ligas de 20 times) e um upsert por partida estourava os 60s de
// maxDuration (FUNCTION_INVOCATION_TIMEOUT, com só ~270 de 552 jogos
// processados). Resolução de time continua sequencial (upsert só quando é
// time novo, cacheado no mapa — poucos times por temporada, não é o gargalo).
async function tarefaBackfillCompeticao(supabase, apiKey, codigo, temporada) {
  const { data: ligaRow } = await supabase.from('leagues').select('id').eq('external_id', codigo).maybeSingle();
  if (!ligaRow) return { error: `Liga não encontrada em leagues (external_id=${codigo}).` };

  const dados = await chamarFootballData(`/competitions/${codigo}/matches?season=${temporada}`, apiKey);

  const { data: timesData } = await supabase.from('teams').select('id, external_id').not('external_id', 'is', null);
  const mapaExternalIdParaTeamId = {};
  (timesData || []).forEach(t => { mapaExternalIdParaTeamId[t.external_id] = t.id; });

  const linhas = [];
  const timesCriados = new Set();

  for (const m of dados.matches || []) {
    const homeTeamId = await resolverOuCriarTimeFootballData(supabase, mapaExternalIdParaTeamId, m.homeTeam);
    const awayTeamId = await resolverOuCriarTimeFootballData(supabase, mapaExternalIdParaTeamId, m.awayTeam);
    if (!homeTeamId) timesCriados.add(`falhou: ${m.homeTeam?.name}`);
    if (!awayTeamId) timesCriados.add(`falhou: ${m.awayTeam?.name}`);
    if (!homeTeamId || !awayTeamId) continue;

    linhas.push({
      external_id: `fd_${m.id}`,
      league_id: ligaRow.id,
      season: String(temporada),
      match_date: m.utcDate,
      home_team_id: homeTeamId,
      away_team_id: awayTeamId,
      home_goals: m.score?.fullTime?.home ?? null,
      away_goals: m.score?.fullTime?.away ?? null,
      status: MAPA_STATUS_FOOTBALL_DATA[m.status] || 'scheduled',
      round: m.matchday ?? null,
      stage: m.stage ?? null,
    });
  }

  // dedup cruzado: não cria linha nova se API-Football/FotMob já tiver
  // essa mesma partida gravada nessa liga -- ver api/_lib/dedupMatches.js.
  const { gravados, duplicatas_evitadas } = await gravarComDedupCruzado(supabase, ligaRow.id, linhas);

  return {
    codigo, temporada, total_jogos: dados.matches?.length ?? 0, sincronizados: gravados,
    duplicatas_evitadas: duplicatas_evitadas || undefined,
    times_com_problema: timesCriados.size > 0 ? [...timesCriados] : undefined,
  };
}

// ============================================================
// TAREFA: backfill-api-football — igual backfill-competicao, mas pra
// competições que só existem na API-Football (Copa do Brasil e afins,
// não cobertas pela football-data.org).
// ============================================================
const MAPA_STATUS_API_FOOTBALL = {
  NS: 'scheduled', TBD: 'scheduled',
  '1H': 'live', HT: 'live', '2H': 'live', ET: 'live', BT: 'live', P: 'live',
  FT: 'finished', AET: 'finished', PEN: 'finished', AWD: 'finished', WO: 'finished',
  PST: 'postponed', SUSP: 'postponed', INT: 'postponed',
  CANC: 'cancelled', ABD: 'cancelled',
};

// Normaliza preservando espaços (vira tokens/palavras) em vez de colapsar
// tudo numa string única — colapsar tudo foi o que causou o bug de
// "ABC" casando por substring dentro de "Atalanta BC", "ASA" dentro de
// "Galatasaray SK" etc. (ver CONTEXTO_PROJETO.md). Mesmo padrão usado em
// arquivos_do_claude/ingestao_stats_fbref.py (normalizar/match_times).
function normalizarNomeTime(s) {
  return (s || '')
    .toLowerCase()
    .normalize('NFD').replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
}

function nomesBatemTime(a, b) {
  const na = normalizarNomeTime(a), nb = normalizarNomeTime(b);
  if (!na || !nb) return false;
  if (na === nb) return true;
  const tokensA = new Set(na.split(' '));
  const tokensB = new Set(nb.split(' '));
  const subsetOuSuperset = [...tokensA].every(t => tokensB.has(t)) || [...tokensB].every(t => tokensA.has(t));
  return subsetOuSuperset;
}

// Resolve um time da API-Football pro nosso team_id: (1) crosswalk já
// conhecido (team_source_ids, source='api_football'); (2) casamento por
// nome com time já existente; (3) cria time novo (sem external_id — esse
// campo é reservado pro código da football-data.org, não faz sentido
// misturar namespaces de ID diferentes na mesma coluna). Sempre grava o
// crosswalk no fim, pra próxima raspagem dessa competição nunca mais
// precisar casar por nome pra esse time.
async function resolverOuCriarTimeApiFootball(supabase, crosswalk, todosOsTimes, timeApi) {
  const apiId = String(timeApi.id);
  if (crosswalk[apiId]) return crosswalk[apiId];

  const candidato = todosOsTimes.find(t =>
    nomesBatemTime(t.name, timeApi.name) ||
    (t.aliases || []).some(alias => nomesBatemTime(alias, timeApi.name))
  );
  let teamId;
  if (candidato) {
    teamId = candidato.id;
  } else {
    const { data: novo, error } = await supabase.from('teams').insert({ name: timeApi.name, crest_url: timeApi.logo || null }).select('id').single();
    if (error || !novo) return null;
    teamId = novo.id;
    todosOsTimes.push({ id: teamId, name: timeApi.name, aliases: [] });
  }

  await supabase.from('team_source_ids').upsert(
    { source: 'api_football', source_id: apiId, team_id: teamId, source_name: timeApi.name },
    { onConflict: 'source,source_id' }
  );
  crosswalk[apiId] = teamId;
  return teamId;
}

async function tarefaBackfillApiFootball(supabase, apiKey, apiFootballLeagueId, temporada) {
  const { data: fonteRow } = await supabase
    .from('liga_fonte_externa').select('league_id')
    .eq('sistema', 'api_football').eq('identificador', String(apiFootballLeagueId)).maybeSingle();
  if (!fonteRow) return { error: `Nenhuma liga em liga_fonte_externa com sistema=api_football e identificador=${apiFootballLeagueId}. Cadastre em leagues/ligas/liga_fonte_externa antes.` };

  const resposta = await fetch(`https://v3.football.api-sports.io/fixtures?league=${apiFootballLeagueId}&season=${temporada}`, { headers: { 'x-apisports-key': apiKey } });
  const dados = await resposta.json();
  if (dados.errors && Object.keys(dados.errors).length > 0) return { error: `API-Football: ${JSON.stringify(dados.errors)}` };
  const fixtures = dados.response || [];

  const { data: crosswalkRows } = await supabase.from('team_source_ids').select('source_id, team_id').eq('source', 'api_football');
  const crosswalk = {};
  (crosswalkRows || []).forEach(r => { crosswalk[r.source_id] = r.team_id; });

  const { data: todosOsTimes } = await supabase.from('teams').select('id, name, aliases');

  const linhas = [];
  for (const f of fixtures) {
    const homeTeamId = await resolverOuCriarTimeApiFootball(supabase, crosswalk, todosOsTimes, f.teams.home);
    const awayTeamId = await resolverOuCriarTimeApiFootball(supabase, crosswalk, todosOsTimes, f.teams.away);
    if (!homeTeamId || !awayTeamId) continue;

    linhas.push({
      external_id: `af_${f.fixture.id}`,
      league_id: fonteRow.league_id,
      season: String(temporada),
      match_date: f.fixture.date,
      home_team_id: homeTeamId,
      away_team_id: awayTeamId,
      home_goals: f.goals?.home ?? null,
      away_goals: f.goals?.away ?? null,
      status: MAPA_STATUS_API_FOOTBALL[f.fixture.status?.short] || 'scheduled',
      round: null,
      stage: f.league?.round ?? null, // texto ("1st Round", "Quarterfinals"...) — não é numérico como football-data.org
    });
  }

  // dedup cruzado: não cria linha nova se football-data.org/FotMob já
  // tiver essa mesma partida gravada nessa liga -- ver api/_lib/dedupMatches.js.
  const { gravados, duplicatas_evitadas } = await gravarComDedupCruzado(supabase, fonteRow.league_id, linhas);

  return {
    api_football_league_id: Number(apiFootballLeagueId), temporada, total_jogos: fixtures.length,
    sincronizados: gravados, duplicatas_evitadas: duplicatas_evitadas || undefined,
  };
}

// ============================================================
// TAREFA: fotmob-liga-buscar / backfill-fotmob-liga — onboarding de liga
// NOVA a partir do FotMob (raspagem), disparável direto do botão "+Nova
// Liga" no frontend (Ligas.jsx). Diferente de ingestao_fotmob.py (que só
// ENRIQUECE ligas que já têm partidas de outra fonte, com crosswalk de
// time resolvido manualmente por disciplina do projeto) — aqui a liga é
// nova, então não há ambiguidade de casar times de fontes diferentes: os
// times são criados direto a partir do nome que o próprio FotMob dá,
// mesmo padrão de resolverOuCriarTimeApiFootball (casa por nome com time
// já existente OU cria).
//
// Endpoint de busca (`apigw.fotmob.com/searchapi/suggest`), diferente do
// domínio usado pro resto da raspagem (`www.fotmob.com/api/data/*`) —
// descoberto por inspeção, mesmo espírito não-oficial já documentado.
// ============================================================

const FOTMOB_HEADERS = { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36' };
const MAPA_TIPO_LIGA_PARA_LEAGUES_TYPE = {
  liga_domestica: 'league', copa_nacional: 'cup', copa_continental: 'cup', torneio_internacional: 'international',
};

async function tarefaFotmobLigaBuscar(termo) {
  const resposta = await fetch(`https://apigw.fotmob.com/searchapi/suggest?term=${encodeURIComponent(termo)}&lang=en`, { headers: FOTMOB_HEADERS });
  if (!resposta.ok) return { error: `FotMob respondeu ${resposta.status} na busca.` };
  const dados = await resposta.json();
  const opcoes = (dados.leagueSuggest || []).flatMap(grupo => grupo.options || []);
  const resultados = opcoes.slice(0, 15).map(o => {
    const [nome, id] = (o.text || '').split('|');
    return {
      nome: nome || o.text,
      fotmob_league_id: o.payload?.id || id,
      pais_code: o.payload?.countryCode || null,
      simbolo_url: `https://images.fotmob.com/image_resources/logo/leaguelogo/${o.payload?.id || id}.png`,
    };
  });
  return { resultados };
}

// Mesmo padrão de resolverOuCriarTimeApiFootball: crosswalk conhecido (source
// = 'fotmob') > casamento por nome com time já existente > cria novo. Único
// pra times, não pra jogadores (não confundir com o crosswalk de player).
async function resolverOuCriarTimeFotmob(supabase, crosswalk, todosOsTimes, timeFm) {
  const fmId = String(timeFm.id);
  if (crosswalk[fmId]) return crosswalk[fmId];

  const candidato = todosOsTimes.find(t =>
    nomesBatemTime(t.name, timeFm.name) ||
    (t.aliases || []).some(alias => nomesBatemTime(alias, timeFm.name))
  );
  let teamId;
  if (candidato) {
    teamId = candidato.id;
  } else {
    const { data: novo, error } = await supabase.from('teams').insert({ name: timeFm.name, crest_url: `https://images.fotmob.com/image_resources/logo/teamlogo/${fmId}_xsmall.png` }).select('id').single();
    if (error || !novo) return null;
    teamId = novo.id;
    todosOsTimes.push({ id: teamId, name: timeFm.name, aliases: [] });
  }

  await supabase.from('team_source_ids').upsert(
    { source: 'fotmob', source_id: fmId, team_id: teamId, source_name: timeFm.name },
    { onConflict: 'source,source_id' }
  );
  crosswalk[fmId] = teamId;
  return teamId;
}

function mapaStatusFotmob(fx) {
  if (fx.status?.cancelled) return 'cancelled';
  if (fx.status?.finished) return 'finished';
  if (fx.status?.started) return 'live';
  return 'scheduled';
}

// Cria (se ainda não existir) a liga em `leagues` + `liga_fonte_externa`
// (sistema='fotmob') + a linha correspondente em `ligas` (cadastro manual,
// com pipeline_league_id já vinculado — ver migration add_pipeline_league_id_ligas)
// e importa os confrontos de UMA temporada via /api/data/fixtures. Idempotente
// por fotmob_league_id: rodar de novo (mesma ou outra temporada) reaproveita a
// liga já criada em vez de duplicar — só faz upsert de partidas (external_id
// `fm_<id>`).
async function tarefaBackfillFotmobLiga(supabase, { fotmobLeagueId, temporada, nome, pais, confederacao, tipo, simboloUrl, temporadaArmazenada }) {
  if (!fotmobLeagueId || !temporada) return { error: 'Informe fotmob_league_id e temporada.' };
  // temporadaArmazenada: valor gravado em matches.season, quando difere do
  // formato que a API do FotMob exige (ex: chamador converteu "2024" pro
  // formato "2024/2025" das ligas europeias só pra bater com a API, mas
  // matches.season dessa liga precisa continuar no formato único que o
  // resto do pipeline usa) — ver tarefaImportarJogosFotmob abaixo. Default:
  // grava exatamente o que foi passado em `temporada` (comportamento
  // original, usado pelo formulário "+Nova Liga").
  const temporadaParaGravar = temporadaArmazenada ?? temporada;

  const fmIdStr = String(fotmobLeagueId);
  let ligaCriada = false;

  const { data: fonteExistente } = await supabase
    .from('liga_fonte_externa').select('league_id')
    .eq('sistema', 'fotmob').eq('identificador', fmIdStr).maybeSingle();

  let leagueId = fonteExistente?.league_id;

  if (!leagueId) {
    // tipo só é obrigatório quando a liga ainda não existe (precisa mapear
    // pro leagues.type na hora de criar) — chamadas seguintes pra outra
    // temporada da MESMA liga (já resolvida via liga_fonte_externa acima)
    // não precisam informar de novo.
    if (!nome) return { error: 'Liga ainda não cadastrada pra esse fotmob_league_id — informe nome (e pais/tipo) pra criar.' };
    const tipoLeagues = MAPA_TIPO_LIGA_PARA_LEAGUES_TYPE[tipo];
    if (!tipoLeagues) return { error: `tipo inválido — use um de: ${Object.keys(MAPA_TIPO_LIGA_PARA_LEAGUES_TYPE).join(', ')}.` };
    const { data: novaLeague, error: erroLeague } = await supabase
      .from('leagues').insert({ name: nome, country: pais || null, type: tipoLeagues }).select('id').single();
    if (erroLeague || !novaLeague) return { error: `Falha ao criar leagues: ${erroLeague?.message}` };
    leagueId = novaLeague.id;
    await supabase.from('liga_fonte_externa').insert({ league_id: leagueId, sistema: 'fotmob', identificador: fmIdStr });
    ligaCriada = true;
  }

  const { data: ligaCadastroExistente } = await supabase.from('ligas').select('id').eq('pipeline_league_id', leagueId).maybeSingle();
  let ligaCadastroId = ligaCadastroExistente?.id;
  let avisoCadastroLigas = null;
  if (!ligaCadastroId) {
    const { data: novaLigaCadastro, error: erroLigaCadastro } = await supabase
      .from('ligas')
      .insert({
        nome: nome || `Liga FotMob ${fmIdStr}`, tipo, pais: pais || null, confederacao: confederacao || null,
        simbolo_url: simboloUrl || `https://images.fotmob.com/image_resources/logo/leaguelogo/${fmIdStr}.png`,
        pipeline_league_id: leagueId,
      })
      .select('id').single();
    if (!erroLigaCadastro && novaLigaCadastro) {
      ligaCadastroId = novaLigaCadastro.id;
    } else if (erroLigaCadastro) {
      // Não falha a importação inteira por causa disso (as partidas já foram
      // resolvidas até aqui) — mas sem isso a liga fica invisível no painel
      // /ligas (lê da tabela `ligas`, não de `leagues`), silenciosamente, até
      // alguém notar. Já aconteceu na prática: import de liga_id já cadastrada
      // em `leagues`/`liga_fonte_externa` sem passar tipo/nome (só faz falta
      // pra CRIAR liga nova) engolia o erro de tipo NOT NULL sem avisar.
      avisoCadastroLigas = `Não foi possível criar o cadastro em "ligas" (aparece em /ligas): ${erroLigaCadastro.message}. Informe nome/tipo/pais/confederacao na chamada, ou crie a linha manualmente.`;
    }
  }

  const respFixtures = await fetch(`https://www.fotmob.com/api/data/fixtures?id=${fmIdStr}&season=${encodeURIComponent(temporada)}`, { headers: FOTMOB_HEADERS });
  if (!respFixtures.ok) return { error: `FotMob respondeu ${respFixtures.status} em /fixtures — league_id ou temporada podem estar errados (formato esperado: "2024" ou "2024/2025", igual ao site).` };
  const fixtures = await respFixtures.json();
  if (!Array.isArray(fixtures) || fixtures.length === 0) {
    return { league_id: leagueId, liga_id: ligaCadastroId, liga_criada: ligaCriada, total_jogos: 0, sincronizados: 0, aviso: 'FotMob não retornou nenhum confronto pra essa liga/temporada — confira o formato da temporada.' };
  }

  const { data: crosswalkRows } = await supabase.from('team_source_ids').select('source_id, team_id').eq('source', 'fotmob');
  const crosswalk = {};
  (crosswalkRows || []).forEach(r => { crosswalk[r.source_id] = r.team_id; });
  const { data: todosOsTimes } = await supabase.from('teams').select('id, name, aliases');

  const linhas = [];
  for (const fx of fixtures) {
    const homeTeamId = await resolverOuCriarTimeFotmob(supabase, crosswalk, todosOsTimes, fx.home);
    const awayTeamId = await resolverOuCriarTimeFotmob(supabase, crosswalk, todosOsTimes, fx.away);
    if (!homeTeamId || !awayTeamId) continue;

    const finalizado = fx.status?.finished && !fx.status?.cancelled;
    linhas.push({
      external_id: `fm_${fx.id}`,
      league_id: leagueId,
      season: String(temporadaParaGravar),
      match_date: fx.status?.utcTime,
      home_team_id: homeTeamId,
      away_team_id: awayTeamId,
      home_goals: finalizado ? fx.home?.score ?? null : null,
      away_goals: finalizado ? fx.away?.score ?? null : null,
      status: mapaStatusFotmob(fx),
      round: null,
      stage: fx.tournament?.stage || null,
    });
  }

  // dedup cruzado: não cria linha nova se football-data.org/API-Football
  // já tiver essa mesma partida gravada nessa liga -- importante aqui em
  // especial, já que esse tarefa também é reusado pra sincronizar
  // temporada de liga JÁ existente (não só onboarding de liga nova) -- ver
  // api/_lib/dedupMatches.js.
  const { gravados, duplicatas_evitadas } = await gravarComDedupCruzado(supabase, leagueId, linhas);

  return {
    league_id: leagueId, liga_id: ligaCadastroId, liga_criada: ligaCriada,
    fotmob_league_id: Number(fmIdStr), temporada, total_jogos: fixtures.length, sincronizados: gravados,
    duplicatas_evitadas: duplicatas_evitadas || undefined,
    ...(avisoCadastroLigas ? { aviso: avisoCadastroLigas } : {}),
  };
}

// ============================================================
// TAREFA: partidas-fotmob — enriquece partidas de UMA temporada JÁ
// EXISTENTES em `matches` (criadas por sync-matches.js/backfill-competicao/
// tarefaBackfillFotmobLiga) com o detalhe completo do FotMob matchDetails:
// estatística por time (match_stats_fotmob), por jogador
// (match_player_stats_fotmob, populando também a dimensão `players`), mapa
// de chutes com xG/xGOT e coordenadas (match_shots_fotmob) e contexto de
// estádio/clima (match_context_fotmob). Idempotente via match_source_ids
// (source='fotmob') — jogo com linha lá já é pulado.
//
// Descoberto por inspeção direta do JSON real (matchId de teste na
// Brasileirão 2026) antes de generalizar, disciplina do resto do projeto:
// - content.stats.Periods.All.stats é uma lista de GRUPOS (top_stats, shots,
//   expected_goals, passes, defence, duels, discipline), cada um com uma
//   lista de sub-stats {key, stats:[home,away]} nessa ORDEM fixa (não
//   depende do campo `highlighted`). yellow_cards aparece em top_stats
//   SEMPRE null (campo morto, mesmo bug já visto em jogador-perfil) e de
//   novo em discipline com o valor real — o flatten processa os grupos em
//   ordem e deixa o ÚLTIMO valor de cada key vencer, então discipline (que
//   vem depois) automaticamente sobrescreve o null de top_stats.
// - Chaves reais divergem do título em vários casos: "Tackles" ->
//   matchstats.headers.tackles, "Duels won" -> duel_won, "Fouls committed"
//   -> fouls, "Big chances missed" -> big_chance_missed_title.
// - playerStats tem formato diferente (stats é OBJETO por título, não
//   array) — cada entrada tem {key, stat:{value, total, type}}.
// - Casamento fixture FotMob <-> nosso `matches`: por ID de time via
//   crosswalk (team_source_ids/resolverOuCriarTimeFotmob, reaproveitado de
//   tarefaBackfillFotmobLiga) + mesmo dia — muito mais confiável que o
//   fuzzy-matching por nome usado com a API-Football (sync-match-stats.js),
//   porque o crosswalk já resolve apelido/abreviação.
// - Temporada: Brasileirão/Libertadores/MLS usam ano único no FotMob
//   ("2024", igual ao nosso `matches.season`); testado ao vivo que as 5
//   ligas europeias (calendário ago-mai) EXIGEM intervalo ("2024/2025" —
//   "2024" sozinho devolve 0 fixtures) — convertido automaticamente pra
//   essas ligas via LIGAS_TEMPORADA_PARTIDA_FOTMOB.
// - Custo por partida é alto (payload de matchDetails ~250KB + ~5 escritas
//   no banco) — processamento real por chamada é limitado a
//   MAX_PARTIDAS_POR_CHAMADA (independente do `limite` pedido) pra caber no
//   maxDuration de 60s do Vercel; o frontend faz rounds sucessivos (mesmo
//   padrão de resetarERecalcularRating em Jogadores.jsx) até atingir o lote
//   escolhido ou esgotar os jogos pendentes.
// ============================================================

const LIGAS_TEMPORADA_PARTIDA_FOTMOB = new Set([4, 7, 10, 13, 16]); // Premier League, La Liga, Serie A, Bundesliga, Ligue 1
const MAX_PARTIDAS_POR_CHAMADA_FOTMOB = 15;

function temporadaFotmob(ligaId, temporada) {
  if (!LIGAS_TEMPORADA_PARTIDA_FOTMOB.has(Number(ligaId))) return String(temporada);
  const ano = parseInt(temporada, 10);
  return Number.isFinite(ano) ? `${ano}/${ano + 1}` : String(temporada);
}

function extrairIntValor(v) {
  if (v == null) return null;
  if (typeof v === 'number') return Math.round(v);
  const m = String(v).match(/-?\d+(\.\d+)?/);
  return m ? Math.round(parseFloat(m[0])) : null;
}

function extrairFloatValor(v) {
  if (v == null) return null;
  if (typeof v === 'number') return v;
  const m = String(v).match(/-?\d+(\.\d+)?/);
  return m ? parseFloat(m[0]) : null;
}

// coluna em match_stats_fotmob -> [chave real no payload, parser]
const MAPA_STATS_TIME = {
  possession: ['BallPossesion', extrairFloatValor],
  xg: ['expected_goals', extrairFloatValor],
  xg_open_play: ['expected_goals_open_play', extrairFloatValor],
  xg_set_play: ['expected_goals_set_play', extrairFloatValor],
  xg_non_penalty: ['expected_goals_non_penalty', extrairFloatValor],
  xgot: ['expected_goals_on_target', extrairFloatValor],
  total_shots: ['total_shots', extrairIntValor],
  shots_on_target: ['ShotsOnTarget', extrairIntValor],
  shots_off_target: ['ShotsOffTarget', extrairIntValor],
  shots_blocked: ['blocked_shots', extrairIntValor],
  shots_inside_box: ['shots_inside_box', extrairIntValor],
  shots_outside_box: ['shots_outside_box', extrairIntValor],
  big_chances: ['big_chance', extrairIntValor],
  big_chances_missed: ['big_chance_missed_title', extrairIntValor],
  touches_opp_box: ['touches_opp_box', extrairIntValor],
  accurate_passes: ['accurate_passes', extrairIntValor],
  accurate_passes_total: ['passes', extrairIntValor],
  accurate_long_balls: ['long_balls_accurate', extrairIntValor],
  accurate_crosses: ['accurate_crosses', extrairIntValor],
  corners: ['corners', extrairIntValor],
  tackles: ['matchstats.headers.tackles', extrairIntValor],
  interceptions: ['interceptions', extrairIntValor],
  blocks: ['shot_blocks', extrairIntValor],
  clearances: ['clearances', extrairIntValor],
  keeper_saves: ['keeper_saves', extrairIntValor],
  duels_won: ['duel_won', extrairIntValor],
  aerial_duels_won: ['aerials_won', extrairIntValor],
  successful_dribbles: ['dribbles_succeeded', extrairIntValor],
  fouls_committed: ['fouls', extrairIntValor],
  yellow_cards: ['yellow_cards', extrairIntValor],
  red_cards: ['red_cards', extrairIntValor],
};

function montarStatsTime(statsPayload) {
  const grupos = (((statsPayload || {}).Periods || {}).All || {}).stats || [];
  const porChave = {};
  for (const grupo of grupos) {
    for (const s of grupo.stats || []) {
      if (Array.isArray(s.stats) && s.stats.length === 2) porChave[s.key] = s.stats;
    }
  }
  const linha = (indice) => {
    const out = {};
    for (const [coluna, [chave, parser]] of Object.entries(MAPA_STATS_TIME)) {
      const par = porChave[chave];
      out[coluna] = par ? parser(par[indice]) : null;
    }
    return out;
  };
  return { home: linha(0), away: linha(1) };
}

function montarLinhasStatsTime(matchIdInterno, statsPayload, homeTeamId, awayTeamId) {
  const { home, away } = montarStatsTime(statsPayload);
  return [
    { match_id: matchIdInterno, team_id: homeTeamId, ...home, stats_raw: statsPayload },
    { match_id: matchIdInterno, team_id: awayTeamId, ...away, stats_raw: statsPayload },
  ];
}

// coluna em match_player_stats_fotmob -> [chave real, parser]
const MAPA_STATS_JOGADOR = {
  rating: ['rating_title', extrairFloatValor],
  minutes_played: ['minutes_played', extrairIntValor],
  goals: ['goals', extrairIntValor],
  assists: ['assists', extrairIntValor],
  xg: ['expected_goals', extrairFloatValor],
  xa: ['expected_assists', extrairFloatValor],
  xgot: ['expected_goals_on_target_variant', extrairFloatValor],
  total_shots: ['total_shots', extrairIntValor],
  accurate_passes: ['accurate_passes', extrairIntValor],
  chances_created: ['chances_created', extrairIntValor],
  touches: ['touches', extrairIntValor],
};

function montarStatsJogador(statsGrupos) {
  const porChave = {};
  const porTitulo = {};
  for (const grupo of statsGrupos || []) {
    for (const [titulo, info] of Object.entries(grupo.stats || {})) {
      if (info && info.key) {
        porChave[info.key] = info.stat;
        porTitulo[titulo] = info.stat;
      }
    }
  }
  const out = {};
  for (const [coluna, [chave, parser]] of Object.entries(MAPA_STATS_JOGADOR)) {
    const stat = porChave[chave];
    out[coluna] = stat && stat.value != null ? parser(stat.value) : null;
  }
  // Stats defensivos/duelos vêm como "X/Y (Z%)" — extrai só o numerador
  const fracaoOuInt = (stat) => {
    if (!stat || stat.value == null) return null;
    const m = String(stat.value).match(/^(\d+)/);
    return m ? parseInt(m[1], 10) : null;
  };
  out.tackles          = fracaoOuInt(porTitulo['Tackles won']);
  out.interceptions    = fracaoOuInt(porTitulo['Interceptions']);
  out.ground_duels_won = fracaoOuInt(porTitulo['Ground duels won']);
  out.aerials_won      = fracaoOuInt(porTitulo['Aerial duels won']);
  out.touches_opp_box  = extrairIntValor((porTitulo['Touches in opposition box'] || {}).value);
  return out;
}

// Upsert simples da dimensão `players` a partir de quem jogou essa partida —
// só os campos que o matchDetails realmente traz (nome/time/última partida
// vista/foto determinística); idade/valor de mercado continuam exclusivos
// de jogador-perfil (payload diferente, sob demanda). Ids placeholder "0"/
// "-1" (jogador sem perfil vinculado no FotMob) ficam de fora, mesmo padrão
// já usado em jogador-perfil e no crosswalk de player.
async function upsertJogadoresDoJogo(supabase, matchIdInterno, playerStatsPayload, crosswalkTimes) {
  const linhas = Object.values(playerStatsPayload || {})
    .filter(p => p.id != null && String(p.id) !== '0' && String(p.id) !== '-1')
    .map(p => ({
      fotmob_player_id: String(p.id),
      name: p.name || null,
      last_team_id: crosswalkTimes[String(p.teamId)] || null,
      last_seen_match_id: matchIdInterno,
      photo_url: `https://images.fotmob.com/image_resources/playerimages/${p.id}.png`,
      updated_at: new Date().toISOString(),
    }));
  if (!linhas.length) return {};
  const { data, error } = await supabase.from('players').upsert(linhas, { onConflict: 'fotmob_player_id' }).select('id, fotmob_player_id');
  const mapa = {};
  if (!error) (data || []).forEach(r => { mapa[r.fotmob_player_id] = r.id; });
  return mapa;
}

// Extrai escalação (match_lineup_fotmob) e dados ricos de dimensão de jogador
// a partir de content.lineup — traz firstName/lastName/idade/valor de mercado/
// país/posição habitual/posição em campo, que o bloco playerStats não tem.
function montarLinhasLineup(matchIdInterno, lineupPayload, crosswalkTimes) {
  const lineupRows = [];
  const playerDimRows = [];
  const agora = new Date().toISOString();
  const lineup = lineupPayload || {};
  for (const [side, key] of [['home', 'homeTeam'], ['away', 'awayTeam']]) {
    const team = lineup[key] || {};
    const teamId = crosswalkTimes[String(team.id || '')] || null;
    for (const [grupo, isStarter] of [['starters', true], ['subs', false]]) {
      for (const p of team[grupo] || []) {
        const pid = String(p.id || '');
        if (!pid || pid === '0' || pid === '-1') continue;
        playerDimRows.push({
          fotmob_player_id: pid,
          name: p.name || null,
          first_name: p.firstName || null,
          last_name: p.lastName || null,
          shirt_number: p.shirtNumber ?? null,
          country_name: p.countryName || null,
          country_code: p.countryCode || null,
          age: p.age ?? null,
          market_value: p.marketValue ?? null,
          usual_position_id: p.usualPlayingPositionId ?? null,
          last_team_id: teamId,
          last_seen_match_id: matchIdInterno,
          photo_url: `https://images.fotmob.com/image_resources/playerimages/${pid}.png`,
          raw_lineup: p,
          updated_at: agora,
        });
        if (teamId) {
          const vl = p.verticalLayout || {};
          lineupRows.push({
            match_id: matchIdInterno,
            team_id: teamId,
            fotmob_player_id: pid,
            is_starter: isStarter,
            shirt_number: p.shirtNumber ?? null,
            position_id: p.positionId ?? null,
            field_pos_x: vl.x ?? null,
            field_pos_y: vl.y ?? null,
            is_captain: p.isCaptain || false,
            raw: p,
            captured_at: agora,
          });
        }
      }
    }
  }
  return { lineupRows, playerDimRows };
}

function montarLinhasPlayerStats(matchIdInterno, playerStatsPayload, crosswalkTimes, mapaPlayerIdInterno) {
  return Object.values(playerStatsPayload || {}).map(p => ({
    match_id: matchIdInterno,
    team_id: crosswalkTimes[String(p.teamId)] || null,
    fotmob_player_id: String(p.id),
    player_name: p.name || null,
    is_goalkeeper: !!p.isGoalkeeper,
    ...montarStatsJogador(p.stats),
    player_id: mapaPlayerIdInterno[String(p.id)] || null,
    stats_raw: p,
  }));
}

function montarLinhasShots(matchIdInterno, shotmapPayload, crosswalkTimes, mapaPlayerIdInterno) {
  const shots = (shotmapPayload || {}).shots || [];
  return shots.map(s => ({
    fotmob_shot_id: s.id,
    match_id: matchIdInterno,
    team_id: crosswalkTimes[String(s.teamId)] || null,
    fotmob_player_id: s.playerId != null ? String(s.playerId) : null,
    player_id: s.playerId != null ? (mapaPlayerIdInterno[String(s.playerId)] || null) : null,
    player_name: s.playerName || null,
    minute: s.min ?? null,
    minute_added: s.minAdded ?? null,
    x: s.x ?? null,
    y: s.y ?? null,
    xg: s.expectedGoals ?? null,
    xgot: s.expectedGoalsOnTarget ?? null,
    shot_type: s.shotType || null,
    situation: s.situation || null,
    event_type: s.eventType || null,
    is_on_target: !!s.isOnTarget,
    is_blocked: !!s.isBlocked,
    is_own_goal: !!s.isOwnGoal,
    period: s.period || null,
  }));
}

function montarContexto(matchIdInterno, fotmobMatchId, matchFacts, weather) {
  const infoBox = (matchFacts || {}).infoBox || {};
  const stadium = infoBox.Stadium || {};
  return {
    match_id: matchIdInterno,
    fotmob_match_id: String(fotmobMatchId),
    stadium_name: stadium.name || null,
    stadium_city: stadium.city || null,
    stadium_country: stadium.country || null,
    stadium_lat: stadium.lat ?? null,
    stadium_long: stadium.long ?? null,
    attendance: infoBox.Attendance ?? null,
    referee: (infoBox.Referee || {}).text || null,
    weather_temperature_c: weather?.temperature ?? null,
    weather_wind_speed: weather?.windSpeed ?? null,
    weather_wind_direction: weather?.windDirectionCardinal ?? null,
    weather_humidity: weather?.relativeHumidity ?? null,
    weather_precipitation: weather?.precipitation ?? null,
    weather_snow: weather?.snow ?? null,
    weather_cloud_cover: weather?.cloudCover ?? null,
    weather_description: weather?.description ?? null,
    weather_api_used: weather?.apiUsed ?? null,
    weather_last_updated: weather?.lastUpdated || null,
    stats_raw: matchFacts,
    captured_at: new Date().toISOString(),
  };
}

async function tarefaPartidasFotmob(supabase, authHeader, { ligaId, temporada, limite, modo = 'encerradas' }) {
  const usuario = await verificarUsuarioLogado(supabase, authHeader);
  if (!usuario) return { status: 401, error: 'Não autenticado -- faça login antes de disparar.' };

  const limiteJogos = Math.min(parseInt(limite, 10) || MAX_PARTIDAS_POR_CHAMADA_FOTMOB, MAX_PARTIDAS_POR_CHAMADA_FOTMOB);
  const modoValido = modo === 'ao_vivo' ? 'ao_vivo' : 'encerradas';

  // Corte temporal: 2h atrás. encerradas = match_date < corte OR status=finished;
  // ao_vivo = match_date entre corte e agora (jogo em andamento ou recém-encerrado).
  const agora = new Date();
  const corteIso = new Date(agora.getTime() - 120 * 60 * 1000).toISOString();
  const agoraIso = agora.toISOString();

  // 1. Crosswalk de ligas -> fotmob_league_id (todas ou só a pedida)
  let fontesQuery = supabase.from('liga_fonte_externa').select('league_id, identificador').eq('sistema', 'fotmob');
  if (ligaId) fontesQuery = fontesQuery.eq('league_id', ligaId);
  const { data: fontes } = await fontesQuery;
  if (!fontes || fontes.length === 0) {
    return { error: ligaId
      ? `Liga id=${ligaId} não tem crosswalk FotMob (liga_fonte_externa, sistema=fotmob) — vincule via "Importar do FotMob" em /ligas.`
      : 'Nenhuma liga com crosswalk FotMob encontrada.' };
  }
  const fotmobIdPorLiga = {};
  fontes.forEach(f => { fotmobIdPorLiga[f.league_id] = f.identificador; });
  const ligasAlvo = Object.keys(fotmobIdPorLiga).map(Number);

  // 2. Busca candidatos por data/hora em todas as ligas alvo
  const todosCandidatos = [];
  for (const lid of ligasAlvo) {
    let q = supabase.from('matches')
      .select('id, match_date, home_team_id, away_team_id, league_id, season, home_goals')
      .eq('league_id', lid).neq('status', 'cancelled');
    if (temporada) q = q.eq('season', temporada);
    if (modoValido === 'encerradas') {
      // status já marcado como finished OU match_date passou há mais de 2h (status desatualizado)
      q = q.or(`status.eq.finished,match_date.lt.${corteIso}`);
    } else {
      // ao_vivo: começou nos últimos 120 min e ainda pode estar em andamento
      q = q.gte('match_date', corteIso).lte('match_date', agoraIso);
    }
    q = q.order('match_date', { ascending: true });
    const { data } = await q;
    (data || []).forEach(m => { m._fotmobLeagueId = fotmobIdPorLiga[lid]; });
    todosCandidatos.push(...(data || []));
  }

  if (todosCandidatos.length === 0) {
    return {
      mensagem: `Nenhum jogo ${modoValido === 'ao_vivo' ? 'em andamento' : 'encerrado'} encontrado nas ligas com cobertura FotMob.`,
      restantes: 0,
    };
  }

  // 3. Filtra os que ainda não têm FotMob match_id registrado OU têm stats mas falta placar
  const todosIds = todosCandidatos.map(j => j.id);
  // Map<matchId, fotmobSourceId> — jogos já importados; also used to skip fixture lookup
  const fotmobIdConhecido = new Map();
  for (const lote of fatiar(todosIds, 200)) {
    const { data: ja } = await supabase.from('match_source_ids')
      .select('match_id, source_id').eq('source', 'fotmob').in('match_id', lote);
    (ja || []).forEach(r => fotmobIdConhecido.set(r.match_id, r.source_id));
  }

  // Pendente = sem source_id (nunca importado) OU com source_id mas sem placar ainda
  const pendentesTotal = todosCandidatos.filter(j =>
    !fotmobIdConhecido.has(j.id) || j.home_goals === null
  );
  // Conjunto dos que já têm stats mas faltam apenas o placar
  const apenasPlaycar = new Set(
    todosCandidatos.filter(j => fotmobIdConhecido.has(j.id) && j.home_goals === null).map(j => j.id)
  );

  const loteProcessar = pendentesTotal.slice(0, limiteJogos);
  const restantes = Math.max(0, pendentesTotal.length - loteProcessar.length);

  if (loteProcessar.length === 0) {
    return {
      mensagem: 'Todos os jogos elegíveis já têm detalhe FotMob importado.',
      total_candidatos: todosCandidatos.length,
      ja_importados: fotmobIdConhecido.size,
      restantes: 0,
    };
  }

  // 4. Crosswalk de times + cache de fixture lists por (fotmobLeagueId, temporadaFm)
  const { data: cwRows } = await supabase.from('team_source_ids').select('source_id, team_id').eq('source', 'fotmob');
  const crosswalk = {};
  (cwRows || []).forEach(r => { crosswalk[r.source_id] = r.team_id; });
  const { data: todosOsTimes } = await supabase.from('teams').select('id, name, aliases');

  // Cache: evita buscar a mesma fixture list do FotMob mais de uma vez por (liga, temporada)
  const fixtureCache = new Map();

  async function getFixtureIndex(fotmobLeagueId, ligaIdInterno, season) {
    const temporadaFm = temporadaFotmob(ligaIdInterno, season);
    const cacheKey = `${fotmobLeagueId}:${temporadaFm}`;
    if (fixtureCache.has(cacheKey)) return fixtureCache.get(cacheKey);

    const index = new Map();
    try {
      const resp = await fetch(
        `https://www.fotmob.com/api/data/fixtures?id=${fotmobLeagueId}&season=${encodeURIComponent(temporadaFm)}`,
        { headers: FOTMOB_HEADERS }
      );
      if (resp.ok) {
        const fixtures = await resp.json();
        if (Array.isArray(fixtures)) {
          for (const fx of fixtures) {
            if (fx.status?.cancelled) continue;
            // encerradas: só fixtures já finalizados; ao_vivo: qualquer iniciado
            if (modoValido === 'encerradas' && !fx.status?.finished) continue;
            const homeTeamId = await resolverOuCriarTimeFotmob(supabase, crosswalk, todosOsTimes, fx.home);
            const awayTeamId = await resolverOuCriarTimeFotmob(supabase, crosswalk, todosOsTimes, fx.away);
            if (!homeTeamId || !awayTeamId) continue;
            const dia = fx.status?.utcTime?.slice(0, 10);
            if (dia) index.set(`${dia}|${homeTeamId}|${awayTeamId}`, fx.id);
          }
        }
      }
    } catch (_e) { /* fixture list indisponível: index vazio */ }
    fixtureCache.set(cacheKey, index);
    return index;
  }

  // 5. Processar lote
  const esperar = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  let processados = 0, sucesso = 0;
  const semCasamentoOuFalha = [];

  for (const jogo of loteProcessar) {
    if (processados > 0) await esperar(700);
    processados++;

    // Se já importado, usa o fotmobMatchId conhecido (sem precisar buscar fixture list)
    let fotmobMatchId = fotmobIdConhecido.get(jogo.id) || null;
    if (!fotmobMatchId) {
      const index = await getFixtureIndex(jogo._fotmobLeagueId, jogo.league_id, jogo.season);
      const dia = jogo.match_date?.slice(0, 10);
      fotmobMatchId = index.get(`${dia}|${jogo.home_team_id}|${jogo.away_team_id}`) || null;
    }
    if (!fotmobMatchId) { semCasamentoOuFalha.push({ match_id: jogo.id, motivo: 'sem_casamento' }); continue; }

    try {
      const resp = await fetch(`https://www.fotmob.com/api/data/matchDetails?matchId=${fotmobMatchId}`, { headers: FOTMOB_HEADERS });
      if (!resp.ok) { semCasamentoOuFalha.push({ match_id: jogo.id, motivo: `http_${resp.status}` }); continue; }
      const payload = await resp.json();
      const content = payload.content || {};

      // Atualiza placar se ausente na tabela matches (independente de ter stats ou não).
      // FotMob usa header.teams[].score como placar definitivo; general.homeScore.current
      // é o placar ao vivo e pode ser null em partidas já encerradas.
      if (jogo.home_goals === null) {
        const headerTeams = payload.header?.teams || [];
        const homeG = headerTeams[0]?.score ?? payload.general?.homeScore?.current ?? null;
        const awayG = headerTeams[1]?.score ?? payload.general?.awayScore?.current ?? null;
        if (homeG !== null) {
          const updateData = { home_goals: homeG, away_goals: awayG };
          if (payload.general?.finished) updateData.status = 'finished';
          await supabase.from('matches').update(updateData).eq('id', jogo.id);
        }
      }

      // Jogo que já tem stats mas só precisava do placar — não re-importa tudo
      if (!apenasPlaycar.has(jogo.id)) {
        await supabase.from('match_stats_fotmob').upsert(
          montarLinhasStatsTime(jogo.id, content.stats, jogo.home_team_id, jogo.away_team_id),
          { onConflict: 'match_id,team_id' }
        );

        // Lineup: dimensão de jogador enriquecida (firstName/idade/país/posição)
        // + match_lineup_fotmob (titular/reserva, posição em campo, capitão)
        const { lineupRows, playerDimRows } = montarLinhasLineup(jogo.id, content.lineup, crosswalk);
        let mapaPlayerIdInterno = {};
        if (playerDimRows.length) {
          const dedupDim = [...new Map(playerDimRows.map(r => [r.fotmob_player_id, r])).values()];
          const { data: dimData } = await supabase.from('players').upsert(dedupDim, { onConflict: 'fotmob_player_id' }).select('id, fotmob_player_id');
          (dimData || []).forEach(r => { mapaPlayerIdInterno[r.fotmob_player_id] = r.id; });
        }

        // Também popula players a partir de playerStats (cobre jogadores eventualmente
        // ausentes do lineup — e.g. GK do lado oposto em partidas antigas)
        const statsPlayerMap = await upsertJogadoresDoJogo(supabase, jogo.id, content.playerStats, crosswalk);
        Object.assign(mapaPlayerIdInterno, statsPlayerMap);

        const linhasJogadores = montarLinhasPlayerStats(jogo.id, content.playerStats, crosswalk, mapaPlayerIdInterno);
        if (linhasJogadores.length) {
          await supabase.from('match_player_stats_fotmob').upsert(linhasJogadores, { onConflict: 'match_id,fotmob_player_id' });
        }

        if (lineupRows.length) {
          const lineupComPlayerId = lineupRows.map(r => ({
            ...r, player_id: mapaPlayerIdInterno[r.fotmob_player_id] || null,
          }));
          await supabase.from('match_lineup_fotmob').upsert(lineupComPlayerId, { onConflict: 'match_id,team_id,fotmob_player_id' });
        }

        const linhasShots = montarLinhasShots(jogo.id, content.shotmap, crosswalk, mapaPlayerIdInterno);
        for (const lote of fatiar(linhasShots, 200)) {
          await supabase.from('match_shots_fotmob').upsert(lote, { onConflict: 'fotmob_shot_id' });
        }

        await supabase.from('match_context_fotmob').upsert(
          montarContexto(jogo.id, fotmobMatchId, content.matchFacts, content.weather),
          { onConflict: 'match_id' }
        );

        await supabase.from('match_source_ids').upsert(
          { match_id: jogo.id, source: 'fotmob', source_id: String(fotmobMatchId), source_name: content.general?.matchName || null },
          { onConflict: 'match_id,source' }
        );
      }
      sucesso++;
    } catch (erro) {
      semCasamentoOuFalha.push({ match_id: jogo.id, motivo: erro.message });
    }
  }

  const descLiga = ligaId
    ? (await supabase.from('leagues').select('name').eq('id', ligaId).maybeSingle()).data?.name || `#${ligaId}`
    : `${ligasAlvo.length} liga(s)`;

  return {
    liga: descLiga,
    temporada: temporada || 'todas',
    modo: modoValido,
    total_candidatos: todosCandidatos.length,
    ja_importados: fotmobIdConhecido.size,
    processados_agora: processados,
    sucesso,
    sem_casamento_ou_falha: semCasamentoOuFalha.length ? semCasamentoOuFalha : undefined,
    restantes,
  };
}

// ============================================================
// TAREFAS: importar-jogos-api-football / importar-jogos-fotmob — criam os
// PRÓPRIOS jogos (data, placar, times) de uma temporada que ainda não está
// em `matches`, pra uma liga JÁ cadastrada no pipeline (tem
// ligas.pipeline_league_id). Diferente de partidas-fotmob/sync-match-stats
// acima, que só ENRIQUECEM jogos que já existem — essas duas criam. São
// wrappers finos em cima de tarefaBackfillApiFootball/tarefaBackfillFotmobLiga
// (que já existiam, chamadas manualmente por curl) só pra aceitar o
// `liga_id` interno (que o frontend já tem) em vez do id externo (que o
// frontend não tem sem uma consulta a mais) — resolve o id externo via
// liga_fonte_externa antes de delegar.
//
// Sem paginação/lote: trazer os jogos de uma temporada é uma única chamada
// externa (a fixture list já vem inteira) + upsert em lotes de 200 já feito
// dentro de tarefaBackfillApiFootball/tarefaBackfillFotmobLiga — cabe
// tranquilo no maxDuration de 60s pra uma temporada inteira (~155-560 jogos
// testados em produção), diferente do enriquecimento por partida acima.
// ============================================================

// Diagnóstico do endpoint /odds da API-Football -- gasta 1 chamada só, dumpa
// a resposta crua pra inspecionar o shape real (tem bookmaker de verdade?
// que mercados? cobre a temporada pedida?) antes de decidir se vale a pena
// escrever um parser/backfill de verdade (disciplina do projeto: nunca
// adivinhar formato de API paga às cegas). Não escreve nada no banco.
async function tarefaTesteOddsApiFootball(supabase, apiKey, ligaId, temporada) {
  const { data: fonte } = await supabase
    .from('liga_fonte_externa').select('identificador')
    .eq('league_id', ligaId).eq('sistema', 'api_football').maybeSingle();
  if (!fonte) return { error: `Liga id=${ligaId} ainda não tem crosswalk API-Football cadastrado (liga_fonte_externa, sistema=api_football).` };

  // /leagues?id=X tem um campo "coverage" por temporada que diz se odds é
  // suportado NESSA liga específica -- resolve de vez se o resultado vazio é
  // "sem cobertura pra essa competição" (plano nenhum resolve) ou "cobertura
  // existe, algo mais está errado" (aí sim vale investigar tier/plano).
  const respLeague = await fetch(
    `https://v3.football.api-sports.io/leagues?id=${fonte.identificador}`,
    { headers: { 'x-apisports-key': apiKey } }
  );
  const dadosLeague = await respLeague.json();
  const coberturaPorTemporada = (dadosLeague.response?.[0]?.seasons || []).map((s) => ({ year: s.year, odds: s.coverage?.odds ?? null }));

  const temporadaAlvo = temporada || new Date().getUTCFullYear() - 1; // temporada mais recente com boa chance de estar encerrada
  const resposta = await fetch(
    `https://v3.football.api-sports.io/odds?league=${fonte.identificador}&season=${temporadaAlvo}`,
    { headers: { 'x-apisports-key': apiKey } }
  );
  const dados = await resposta.json();
  if (!resposta.ok) return { error: `API-Football /odds: HTTP ${resposta.status} — ${JSON.stringify(dados).slice(0, 300)}` };

  const primeiro = dados.response?.[0] || null;
  const resultado = {
    liga_id: ligaId,
    api_football_league_id: fonte.identificador,
    cobertura_odds_por_temporada: coberturaPorTemporada,
    temporada: temporadaAlvo,
    results: dados.results,
    paging: dados.paging,
    errors: dados.errors,
    primeira_fixture_bruta: primeiro,
    bookmakers_na_primeira_fixture: primeiro?.bookmakers?.map((b) => b.name) || null,
  };

  // /odds?league=X&season=Y (busca em lote) veio vazio -- tenta por fixture
  // específico (/odds?fixture=X), padrão de consulta diferente que alguns
  // planos/endpoints da API-Football só suportam desse jeito (achado real:
  // a OddsPapi teve o mesmo tipo de diferença entre listar torneio inteiro
  // vs. partida específica).
  if (!dados.results) {
    const respFixtures = await fetch(
      `https://v3.football.api-sports.io/fixtures?league=${fonte.identificador}&season=${temporadaAlvo}&status=FT`,
      { headers: { 'x-apisports-key': apiKey } }
    );
    const dadosFixtures = await respFixtures.json();
    const fixtureId = dadosFixtures.response?.[0]?.fixture?.id || null;
    resultado.fixture_id_testado = fixtureId;
    if (fixtureId) {
      const respOddsFixture = await fetch(
        `https://v3.football.api-sports.io/odds?fixture=${fixtureId}`,
        { headers: { 'x-apisports-key': apiKey } }
      );
      const dadosOddsFixture = await respOddsFixture.json();
      const primeiroPorFixture = dadosOddsFixture.response?.[0] || null;
      resultado.results_por_fixture = dadosOddsFixture.results;
      resultado.errors_por_fixture = dadosOddsFixture.errors;
      resultado.primeira_fixture_bruta_por_fixture = primeiroPorFixture;
      resultado.bookmakers_por_fixture = primeiroPorFixture?.bookmakers?.map((b) => b.name) || null;
    }
  }

  return resultado;
}

async function tarefaImportarJogosApiFootball(supabase, apiKey, ligaId, temporada) {
  if (!ligaId || !temporada) return { error: 'Informe ?liga_id=X (de public.leagues) e ?temporada=AAAA.' };
  const { data: fonte } = await supabase
    .from('liga_fonte_externa').select('identificador')
    .eq('league_id', ligaId).eq('sistema', 'api_football').maybeSingle();
  if (!fonte) return { error: `Liga id=${ligaId} ainda não tem crosswalk API-Football cadastrado (liga_fonte_externa, sistema=api_football).` };
  return tarefaBackfillApiFootball(supabase, apiKey, fonte.identificador, temporada);
}

async function tarefaImportarJogosFotmob(supabase, ligaId, temporada) {
  if (!ligaId || !temporada) return { error: 'Informe ?liga_id=X (de public.leagues) e ?temporada=AAAA.' };
  const { data: fonte } = await supabase
    .from('liga_fonte_externa').select('identificador')
    .eq('league_id', ligaId).eq('sistema', 'fotmob').maybeSingle();
  if (!fonte) return { error: `Liga id=${ligaId} ainda não tem crosswalk FotMob cadastrado (liga_fonte_externa, sistema=fotmob) — use "+Nova Liga > Importar do FotMob" em /ligas se essa liga nunca foi vinculada.` };
  // temporada aqui é sempre no formato ÚNICO já usado em matches.season
  // (ex: "2024") — temporadaFotmob() converte só pra chamada externa nas 5
  // ligas europeias (calendário ago-mai, FotMob exige "2024/2025"), mas o
  // valor GRAVADO continua no formato único (temporadaArmazenada), pra não
  // fraturar o agrupamento por temporada que o resto do app já usa pra essa
  // liga (ver achado em tarefaPartidasFotmob acima).
  const temporadaApi = temporadaFotmob(ligaId, temporada);
  return tarefaBackfillFotmobLiga(supabase, { fotmobLeagueId: fonte.identificador, temporada: temporadaApi, temporadaArmazenada: temporada });
}

// ============================================================
// TAREFA: importar-odds-footiqo -- painel /configuracoes
// Porta pra JS a mesma lógica de arquivos_do_claude/ingestao_odds_footiqo.py
// (já validada em produção: 97/97 partidas da Libertadores 2026 casaram).
// O CSV é parseado no FRONTEND (não dá pra fazer upload de arquivo binário
// nesse endpoint sem parser multipart) -- aqui só chega o corpo já como
// array de objetos (uma linha do CSV = um objeto com as chaves originais
// do cabeçalho: matchDate, homeTeam, awayTeam, H, D, A, O05..U45, BTTSY/N).
// Exige login (mesmo mecanismo de salvar-config-custom) -- é uma escrita
// disparada pelo frontend, ProtectedRoute só esconde o botão, não impede
// chamada direta.
// ============================================================

// Chave = nome normalizado como a Footiqo escreve; valor = nome (bruto) do
// NOSSO banco. Duas ambiguidades resolvidas por inferência (time mais
// frequente na competição, ver docstring de ingestao_odds_footiqo.py):
// "U. Catolica" -> Chile (não Equador); "Nacional" -> Uruguai (não
// Colômbia/Bolívia). Se a inferência errar numa linha específica, ela só
// fica sem correspondência (não grava odd no time errado).
const ALIASES_FOOTIQO = {
  'estudiantes l p': 'Estudiantes de La Plata',
  'ind rivadavia': 'CS Independiente Rivadavia',
  'u catolica': 'CD Universidad Católica',
  'nacional': 'Club Nacional de Football',
  'sporting cristal': 'CS Cristal',
  'ind del valle': 'CAR Independiente del Valle',
  'u de deportes': 'Club Universitario de Deportes',
  'deportes tolima': 'CD Tolima',
  'flamengo rj': 'CR Flamengo',
  'ind medellin': 'CD Independiente Medellín',
  'ldu quito': 'LDU de Quito',
  'la guaira': 'Deportivo La Guaira FC',
  'libertad asuncion': 'Club Libertad Asuncion',
  'cerro porteno': 'Club Cerro Porteño',
  'universidad central': 'Universidad Central de Venezuela FC',
  'penarol': 'CA Peñarol',
  'santa fe': 'Independiente Santa Fe',
  'junior': 'CDP Junior FC',
};

const LINHAS_OU_FOOTIQO = [['O05', 'U05', '0.5'], ['O15', 'U15', '1.5'], ['O25', 'U25', '2.5'], ['O35', 'U35', '3.5'], ['O45', 'U45', '4.5']];

function resolverTimeFootiqo(nomeFootiqo, idPorNomeNorm) {
  const norm = normalizarNomeTime(nomeFootiqo);
  if (idPorNomeNorm[norm]) return idPorNomeNorm[norm];
  const aliasBruto = ALIASES_FOOTIQO[norm];
  if (aliasBruto) {
    const normAlias = normalizarNomeTime(aliasBruto);
    if (idPorNomeNorm[normAlias]) return idPorNomeNorm[normAlias];
  }
  for (const candidatoNorm in idPorNomeNorm) {
    if (nomesBatemTime(norm, candidatoNorm)) return idPorNomeNorm[candidatoNorm];
  }
  return null;
}

// "DD-MM-AA HH:MM", ano de 2 dígitos -- Footiqo só tem dado de 2015 em
// diante, "AA" nunca vai significar 19xx.
function pararDataFootiqo(matchDate) {
  const m = /^(\d{2})-(\d{2})-(\d{2}) (\d{2}):(\d{2})$/.exec((matchDate || '').trim());
  if (!m) return null;
  const [, dd, mm, aa, hh, min] = m;
  return new Date(Date.UTC(2000 + Number(aa), Number(mm) - 1, Number(dd), Number(hh), Number(min)));
}

function paraFloatFootiqo(v) {
  const n = parseFloat(v);
  return Number.isFinite(n) ? n : null;
}

async function tarefaImportarOddsFootiqo(supabase, authHeader, body) {
  const usuario = await verificarUsuarioLogado(supabase, authHeader);
  if (!usuario) return { status: 401, error: 'Não autenticado.' };

  const ligaId = body?.liga_id;
  const linhas = body?.linhas;
  if (!ligaId) return { status: 400, error: 'Informe liga_id.' };
  if (!Array.isArray(linhas) || linhas.length === 0) return { status: 400, error: 'Informe linhas (array não vazio, uma por partida do CSV).' };

  const jogos = [];
  for (let inicio = 0; ; inicio += 1000) {
    const { data: lote } = await supabase.from('matches').select('id, match_date, home_team_id, away_team_id')
      .eq('league_id', ligaId).range(inicio, inicio + 999);
    jogos.push(...(lote || []));
    if (!lote || lote.length < 1000) break;
  }
  if (jogos.length === 0) return { status: 400, error: `Nenhum jogo da liga_id=${ligaId} no banco.` };

  const idsTimes = [...new Set(jogos.flatMap((j) => [j.home_team_id, j.away_team_id]))];
  const { data: timesRows } = await supabase.from('teams').select('id, name').in('id', idsTimes);
  const idPorNomeNorm = {};
  (timesRows || []).forEach((t) => { idPorNomeNorm[normalizarNomeTime(t.name)] = t.id; });

  const idsJogos = jogos.map((j) => j.id);
  const jaTemOdds = new Set();
  for (let inicio = 0; ; inicio += 1000) {
    const { data: lote } = await supabase.from('odds_market').select('match_id').in('match_id', idsJogos).range(inicio, inicio + 999);
    (lote || []).forEach((r) => jaTemOdds.add(r.match_id));
    if (!lote || lote.length < 1000) break;
  }

  // (home_team_id, away_team_id) -> jogos candidatos (normalmente 1, pode
  // ter mais de 1 em mata-mata ida/volta -- desempate pela data mais
  // próxima, não só a primeira da lista).
  const porParTimes = {};
  jogos.forEach((j) => {
    const chave = `${j.home_team_id}:${j.away_team_id}`;
    (porParTimes[chave] ||= []).push(j);
  });

  const nomesSemMatch = new Set();
  const registros = [];
  let semMatch = 0, semOdds = 0, jaGravadas = 0;

  for (const linha of linhas) {
    const homeId = resolverTimeFootiqo(linha.homeTeam, idPorNomeNorm);
    const awayId = resolverTimeFootiqo(linha.awayTeam, idPorNomeNorm);
    if (!homeId) nomesSemMatch.add(linha.homeTeam);
    if (!awayId) nomesSemMatch.add(linha.awayTeam);
    if (!homeId || !awayId) { semMatch++; continue; }

    const dataFootiqo = pararDataFootiqo(linha.matchDate);
    if (!dataFootiqo) { semMatch++; continue; }

    const candidatos = porParTimes[`${homeId}:${awayId}`] || [];
    let melhor = null, melhorDiffMs = Infinity;
    for (const c of candidatos) {
      const diffMs = Math.abs(new Date(c.match_date).getTime() - dataFootiqo.getTime());
      if (diffMs <= 3 * 86400000 && diffMs < melhorDiffMs) { melhor = c; melhorDiffMs = diffMs; }
    }
    if (!melhor) { semMatch++; continue; }
    if (jaTemOdds.has(melhor.id)) { jaGravadas++; continue; }

    let teveOdds = false;
    for (const [selecao, col] of [['home', 'H'], ['draw', 'D'], ['away', 'A']]) {
      const odd = paraFloatFootiqo(linha[col]);
      if (odd !== null) { registros.push({ match_id: melhor.id, bookmaker: '1xbet', market: '1X2', selection: selecao, odds: odd, snapshot: 'closing' }); teveOdds = true; }
    }
    for (const [colOver, colUnder, faixa] of LINHAS_OU_FOOTIQO) {
      const mercado = `over_under_${faixa}`;
      for (const [selecao, col] of [['over', colOver], ['under', colUnder]]) {
        const odd = paraFloatFootiqo(linha[col]);
        if (odd !== null) { registros.push({ match_id: melhor.id, bookmaker: '1xbet', market: mercado, selection: selecao, odds: odd, snapshot: 'closing' }); teveOdds = true; }
      }
    }
    for (const [selecao, col] of [['yes', 'BTTSY'], ['no', 'BTTSN']]) {
      const odd = paraFloatFootiqo(linha[col]);
      if (odd !== null) { registros.push({ match_id: melhor.id, bookmaker: '1xbet', market: 'btts', selection: selecao, odds: odd, snapshot: 'closing' }); teveOdds = true; }
    }
    if (!teveOdds) semOdds++;
  }

  // Dedup por chave de conflito antes de gravar -- mesma cautela do script Python.
  const registrosUnicos = Object.values(Object.fromEntries(registros.map((r) => [`${r.match_id}|${r.bookmaker}|${r.market}|${r.selection}`, r])));

  for (let i = 0; i < registrosUnicos.length; i += 500) {
    const { error } = await supabase.from('odds_market').insert(registrosUnicos.slice(i, i + 500));
    if (error) return { status: 500, error: `Falha gravando em odds_market: ${error.message}` };
  }

  return {
    status: 200,
    liga_id: ligaId,
    linhas_recebidas: linhas.length,
    odds_gravadas: registrosUnicos.length,
    sem_correspondencia: semMatch,
    sem_correspondencia_nomes: [...nomesSemMatch].sort(),
    sem_odds_na_fonte: semOdds,
    ja_tinham_odds: jaGravadas,
  };
}

// Dumpa a resposta crua de /teams?id=X pra inspecionar o shape real antes de
// generalizar o parser (disciplina do projeto: nunca adivinhar formato de API
// paga/limitada). Não escreve nada no banco.
async function tarefaDiagnosticoTime(apiKey, apiFootballId) {
  const resposta = await fetch(`https://v3.football.api-sports.io/teams?id=${apiFootballId}`, { headers: { 'x-apisports-key': apiKey } });
  const dados = await resposta.json();
  return dados;
}

// Mesma ideia, mas pra TheStatsAPI (chave trial de 7 dias, THE_STATSAPI_KEY):
// proxy genérico de QUALQUER caminho (?caminho=/football/competitions?search=brazil),
// dumpando a resposta crua sem escrever nada — descobrir o shape real e os IDs
// de competição/time antes de generalizar qualquer parser.
async function tarefaDiagnosticoStatsApi(apiKey, caminho) {
  const resposta = await fetch(`https://api.thestatsapi.com/api${caminho}`, { headers: { Authorization: `Bearer ${apiKey}` } });
  const dados = await resposta.json().catch(() => ({ erro_parse: true, status: resposta.status, texto: null }));
  return { status_http: resposta.status, dados };
}

// Popula teams.city/stadium/country via API-Football (/teams?id=X), só pros
// times que JÁ têm um id da API-Football confirmado em team_source_ids
// (source='api_football') — ou seja, zero matching por nome nessa tarefa,
// reaproveita só o crosswalk que já foi resolvido com segurança em outra
// importação (Copa do Brasil). Times sem esse crosswalk ficam de fora por
// enquanto (resolver por busca de nome é mais arriscado, ver CONTEXTO_PROJETO.md).
// 1 chamada de API por time — roda em lotes (?limite=N, padrão 40).
async function tarefaInfoClubes(supabase, apiKey, limite) {
  const { data: crosswalkRows } = await supabase.from('team_source_ids').select('team_id, source_id').eq('source', 'api_football');
  if (!crosswalkRows || crosswalkRows.length === 0) {
    return { mensagem: 'Nenhum time com crosswalk api_football em team_source_ids ainda — rode backfill-api-football antes.' };
  }
  const sourceIdPorTeamId = {};
  crosswalkRows.forEach(r => { sourceIdPorTeamId[r.team_id] = r.source_id; });

  const { data: pendentes } = await supabase
    .from('teams')
    .select('id, name')
    .in('id', crosswalkRows.map(r => r.team_id))
    .is('city', null);

  if (!pendentes || pendentes.length === 0) {
    return { mensagem: 'Todos os times com crosswalk api_football já têm cidade/estádio preenchidos.' };
  }

  const lote = pendentes.slice(0, limite);
  let atualizados = 0;
  let processados = 0;
  const semDado = [];
  const esperar = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  for (const time of lote) {
    if (processados > 0) await esperar(200); // plano pago (300 req/min) — pacing modesto, deteccao de rate limit abaixo cobre o resto

    const sourceId = sourceIdPorTeamId[time.id];
    const resposta = await fetch(`https://v3.football.api-sports.io/teams?id=${sourceId}`, { headers: { 'x-apisports-key': apiKey } });
    const dados = await resposta.json();

    // rate limit: para o lote aqui (não marca como "sem dado" — city continua
    // NULL, então o próximo run tenta esses times de novo naturalmente)
    if (dados.errors?.rateLimit) break;

    processados++;
    const item = dados.response?.[0];
    if (!item) { semDado.push(time.name); continue; }

    const patch = {
      country: item.team?.country || null,
      city: item.venue?.city || null,
      stadium: item.venue?.name || null,
    };
    if (!patch.country && !patch.city && !patch.stadium) { semDado.push(time.name); continue; }

    const { error } = await supabase.from('teams').update(patch).eq('id', time.id);
    if (!error) atualizados++;
  }

  return {
    pendentes_no_total: pendentes.length,
    processados_agora: processados,
    atualizados,
    sem_dado: semDado.length > 0 ? semDado : undefined,
    restantes: pendentes.length - processados,
  };
}

// =============================================================================
// tarefa=modelos-disponiveis / tarefa=simulacao-carteira
// -- fundidas aqui (em vez de api/modelos-disponiveis.js e
// api/simulacao-carteira.js próprios) por causa do teto de 12 Serverless
// Functions do plano Hobby do Vercel (cada arquivo em api/*.js conta 1,
// já estava em 12 antes dessas duas -- ver skill workflow-quant-predictor).
// Chamadas pelo FRONTEND (src/pages/SimulacaoCarteira.jsx), não só
// administrativas, mas o dispatch por ?tarefa= já é o padrão estabelecido
// pra qualquer endpoint novo quando o teto é atingido.
// =============================================================================

// "bruto" -- sem sufixo _calibrado_platt/_calibrado_isotonic (essas
// variantes do pipeline "Model Benchmarking" já são cobertas pelo seletor
// de correção da simulação, não precisam aparecer soltas na lista).
//
// Antes a exibição era restrita a partir da v3 pra catboost/xgboost/
// lightgbm (pedido anterior do usuário) -- revertido por pedido novo
// ("incluir todos os modelos"): v1/v2 agora aparecem também, junto com
// v3-v8 e dixon_coles_v1.
function ehModeloBruto(nome) {
  return !/_calibrado_(platt|isotonic)$/.test(nome);
}

// "Teste sempre em 2025" (CONTEXTO_PROJETO.md) -- convenção já documentada
// do projeto: 2019-2024 é o período de treino/validação (usado pra ajustar
// os modelos e o próprio split_cronologico do backtest_kelly.py), 2025 em
// diante é o período de teste out-of-sample de verdade. Pedido do usuário:
// a Simulação de Carteira deve EXCLUIR treino/validação -- piso fixo, não
// desligável pelo filtro de temporada da UI (só permite estreitar mais
// ainda dentro do próprio período de teste).
const TEMPORADA_TESTE_MINIMA = '2025';
function ehTemporadaDeTeste(season) {
  return season != null && String(season) >= TEMPORADA_TESTE_MINIMA;
}

// Mercados oferecidos na Simulação de Carteira -- restrito aos que têm
// odds reais de abertura/fechamento da Pinnacle em `odds_market` (sem
// isso não dá pra rodar Kelly/EV+ nenhum, fica sempre 0 apostas). O
// pipeline antigo também tem um modelo pra "corners_over_under_9.5"
// (stats_glm_v1, model_predictions) mas SEM nenhuma odd capturada pra
// esse mercado (confirmado via SQL) -- por isso NÃO entra aqui, mesmo já
// tendo treino, seria sempre uma simulação vazia. `predicoes` (Model
// Benchmarking) ganhou colunas de over/under (migração
// `add_mercado_predicoes`) depois que se confirmou que os 18 modelos de
// árvore JÁ tinham treino/avaliação real nesse mercado em backtest_
// kelly.py -- só faltava persistir a previsão por partida em algum lugar
// servível (ver scripts/backfill_predicoes_historicas.py).
const MERCADOS_CARTEIRA_SUPORTADOS = new Set(['1X2', 'over_under_2.5', 'btts']);

async function tarefaModelosDisponiveis(supabase, mercado = '1X2') {
  const mercadoValido = MERCADOS_CARTEIRA_SUPORTADOS.has(mercado) ? mercado : '1X2';

  // Stage 1: só partidas finalizadas do período de teste (pequeno conjunto)
  const [matchesFiltradas, ligas] = await Promise.all([
    buscarTudoPaginado(() => supabase.from('matches').select('id, league_id, season')
      .gte('season', TEMPORADA_TESTE_MINIMA).eq('status', 'finished')),
    buscarTudoPaginado(() => supabase.from('leagues').select('id, name')),
  ]);

  const matchInfo = {};
  matchesFiltradas.forEach((m) => { matchInfo[m.id] = m; });
  const matchIds = matchesFiltradas.map((m) => m.id);

  if (matchIds.length === 0) {
    return { modelos: [], ligas: ligas.sort((a, b) => a.name.localeCompare(b.name)) };
  }

  // Stage 2: predições filtradas por esses match_ids (evita varrer tabelas inteiras)
  const [predAntigas, predBenchmarking] = await Promise.all([
    buscarTudoPaginadoIn(matchIds, (ids) =>
      mercadoValido === '1X2'
        ? supabase.from('model_predictions').select('model_name, match_id').in('market', ['1X2', '1x2']).in('match_id', ids)
        : supabase.from('model_predictions').select('model_name, match_id').eq('market', mercadoValido).in('match_id', ids)),
    buscarTudoPaginadoIn(matchIds, (ids) =>
      supabase.from('predicoes').select('model_name, match_id').eq('mercado', mercadoValido).in('match_id', ids)),
  ]);

  const contagem = {};
  const ligasPorModelo = {};
  const temporadasPorModelo = {};

  const processar = (linhas) => {
    linhas.forEach((l) => {
      if (!ehModeloBruto(l.model_name)) return;
      const m = matchInfo[l.match_id];
      if (!m) return;
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

  return { modelos, ligas: ligas.sort((a, b) => a.name.localeCompare(b.name)) };
}

// Normaliza `predicoes`/`market_odds` (pipeline "Model Benchmarking") pro
// mesmo formato de `model_predictions`/`odds_market` (uma linha por
// seleção) -- mesma normalização já usada em api/model-stats.js e
// api/backtest-betting.js.
function normalizarPredicoesBenchmarking(rows) {
  const linhas = [];
  for (const r of rows) {
    linhas.push({ model_name: r.model_name, selection: 'home', probability: Number(r.prob_home), match_id: r.match_id });
    linhas.push({ model_name: r.model_name, selection: 'draw', probability: Number(r.prob_draw), match_id: r.match_id });
    linhas.push({ model_name: r.model_name, selection: 'away', probability: Number(r.prob_away), match_id: r.match_id });
  }
  return linhas;
}

// Mesma normalização, pro mercado Over/Under 2.5 (`prob_under`/`prob_over`,
// ver migração `add_mercado_predicoes`).
function normalizarPredicoesBenchmarkingOverUnder(rows) {
  const linhas = [];
  for (const r of rows) {
    linhas.push({ model_name: r.model_name, selection: 'under', probability: Number(r.prob_under), match_id: r.match_id });
    linhas.push({ model_name: r.model_name, selection: 'over', probability: Number(r.prob_over), match_id: r.match_id });
  }
  return linhas;
}

function aplicarPlattPredicao(p, coef, intercept) { return sigmoid(coef * logit(p) + intercept); }

function fracaoKellySimulacao(p, odd) {
  const b = odd - 1;
  if (b <= 0) return 0;
  const f = (p * b - (1 - p)) / b;
  return Math.max(0, f) * 0.25; // Quarter Kelly
}

function calcularResultadoMercadoSimulacao(m, mercado) {
  if (m.status !== 'finished' || m.home_goals == null || m.away_goals == null) return null;
  if (mercado === 'over_under_2.5') {
    return (m.home_goals + m.away_goals) > 2.5 ? 'over' : 'under';
  }
  if (mercado === 'btts') {
    return (m.home_goals > 0 && m.away_goals > 0) ? 'yes' : 'no';
  }
  return m.home_goals > m.away_goals ? 'home' : m.home_goals < m.away_goals ? 'away' : 'draw';
}

// Simulação de carteira RODADA A RODADA com Quarter Kelly + gerenciamento
// de risco estrito (ver docstring completa que existia em
// api/simulacao-carteira.js, preservada aqui):
//   - Filtro de EV BRUTO: p_modelo * odd >= 1,02 (sem devig -- escolha
//     deliberada da tarefa, não um bug: aceita azarão com qualquer excesso
//     de confiança do modelo).
//   - Piso de ruído: descarta stake < 0,5% da banca da rodada.
//   - Quarter Kelly, teto de exposição de 15% da banca por rodada (escala
//     proporcional se exceder).
//   - Banca da rodada = banca de FECHAMENTO da rodada anterior.
//   - "Rodada" = data de calendário (UTC) do match_date.
//   - `usar_calibracao=platt|isotonic` aplica a correção salva em
//     `model_calibration` sobre a probabilidade crua -- só pro pipeline
//     antigo (Model Benchmarking já calibrado entra como model_name próprio).
//   - Piso de PERÍODO DE TESTE: só entram partidas com season >= 2025
//     ("Teste sempre em 2025", CONTEXTO_PROJETO.md -- 2019-2024 é
//     treino/validação, usado pra ajustar os modelos, não é out-of-sample de
//     verdade). Piso FIXO, não desligável pelo filtro de temporada da UI --
//     o parâmetro `temporada` só estreita mais ainda dentro do próprio
//     período de teste.
//   - DUPLA EXECUÇÃO por chamada: a mesma simulação roda duas vezes, uma
//     precificando cada entrada pela odd de ABERTURA da Pinnacle
//     (`odds_market.snapshot='pre_closing'`) e outra pela odd de FECHAMENTO
//     da Pinnacle (`snapshot='closing'`) -- pedido explícito do usuário pra
//     comparar o efeito da linha de abertura vs. fechamento no resultado
//     final. Como só o pipeline antigo tem esse par abertura/fechamento
//     (o `market_odds` do Model Benchmarking só guarda a odd mais recente
//     por casa, sem distinção), modelos cujas partidas só existem no
//     pipeline novo podem resultar em zero candidatos nas duas execuções.
async function tarefaSimulacaoCarteira(supabase, query) {
  const { modelo, liga_id, temporada } = query;
  if (!modelo) return { status: 400, error: 'Parâmetro "modelo" é obrigatório.' };
  const mercado = MERCADOS_CARTEIRA_SUPORTADOS.has(query.mercado) ? query.mercado : '1X2';

  // Intervalo personalizado de datas (opcional, combina em AND com liga/
  // temporada -- mesmo padrão dos outros filtros). NÃO substitui o piso
  // TEMPORADA_TESTE_MINIMA logo abaixo (pedido explícito do usuário: esse
  // piso não é desligável por nenhum filtro da UI) -- só estreita ainda
  // mais o período de teste já filtrado por season.
  const dataInicioRegex = /^\d{4}-\d{2}-\d{2}$/;
  const dataInicio = dataInicioRegex.test(query.data_inicio || '') ? query.data_inicio : null;
  const dataFimBruta = dataInicioRegex.test(query.data_fim || '') ? query.data_fim : null;
  if (dataInicio && dataFimBruta && dataInicio > dataFimBruta) {
    return { status: 400, error: 'data_inicio não pode ser depois de data_fim.' };
  }
  // match_date é timestamptz -- data_fim precisa virar limite EXCLUSIVO do
  // dia seguinte pra incluir o dia inteiro (senão "2026-05-10" corta às
  // 00:00 e perde todos os jogos daquele dia).
  let dataFimExclusiva = null;
  if (dataFimBruta) {
    const d = new Date(`${dataFimBruta}T00:00:00Z`);
    d.setUTCDate(d.getUTCDate() + 1);
    dataFimExclusiva = d.toISOString().slice(0, 10);
  }

  const evMinimo = query.ev_minimo != null ? Number(query.ev_minimo) : 1.02;
  const evMaximo = query.ev_maximo != null ? Number(query.ev_maximo) : 2.0; // 100% default
  const stakeMinimaPct = query.stake_minima_pct != null ? Number(query.stake_minima_pct) : 0.005;
  const tetoExposicaoPct = query.teto_exposicao_pct != null ? Number(query.teto_exposicao_pct) : 0.15;
  const bancaInicial = query.banca_inicial != null ? Number(query.banca_inicial) : 1000;
  const usarCalibracao = ['platt', 'isotonic'].includes(query.usar_calibracao) ? query.usar_calibracao : 'nenhuma';
  const tipoStake = query.tipo_stake === 'fixa' ? 'fixa' : 'kelly';
  const stakeFixa = query.stake_fixa != null ? Number(query.stake_fixa) : 10;
  const kellyMultiplier = query.kelly_multiplier != null ? Number(query.kelly_multiplier) : 0.25;

  const ligaIdNum = liga_id ? Number(liga_id) : null;

  // Stage 1: predições do modelo + calibrações + partidas finalizadas do
  // período de teste — tudo em paralelo. Matches já filtrados no BD
  // (season>=TEMPORADA_TESTE_MINIMA, status=finished, liga_id e temporada
  // opcionais) evitam varrer a tabela inteira.
  const [predicoesAntigas, predicoesBenchmarkingRaw, calibracoesRaw, todasMatchesFiltradas] = await Promise.all([
    mercado === '1X2'
      ? buscarTudoPaginado(() => supabase.from('model_predictions').select('match_id, selection, probability').eq('model_name', modelo).in('market', ['1X2', '1x2']))
      : buscarTudoPaginado(() => supabase.from('model_predictions').select('match_id, selection, probability').eq('model_name', modelo).eq('market', mercado)),
    mercado === '1X2'
      ? buscarTudoPaginado(() => supabase.from('predicoes').select('match_id, model_name, prob_home, prob_draw, prob_away').eq('model_name', modelo).eq('mercado', '1X2'))
      : buscarTudoPaginado(() => supabase.from('predicoes').select('match_id, model_name, prob_under, prob_over').eq('model_name', modelo).eq('mercado', mercado)),
    usarCalibracao === 'nenhuma'
      ? Promise.resolve([])
      : buscarTudoPaginado(() => supabase.from('model_calibration').select('selection, method, platt_coef, platt_intercept, isotonic_x, isotonic_y').eq('model_name', modelo).eq('market', mercado)),
    buscarTudoPaginado(() => {
      let q = supabase.from('matches')
        .select('id, league_id, season, status, home_goals, away_goals, match_date')
        .gte('season', TEMPORADA_TESTE_MINIMA)
        .eq('status', 'finished');
      if (ligaIdNum) q = q.eq('league_id', ligaIdNum);
      if (temporada) q = q.eq('season', temporada);
      if (dataInicio) q = q.gte('match_date', dataInicio);
      if (dataFimExclusiva) q = q.lt('match_date', dataFimExclusiva);
      return q;
    }),
  ]);
  const predicoesBenchmarking = mercado === '1X2'
    ? normalizarPredicoesBenchmarking(predicoesBenchmarkingRaw)
    : normalizarPredicoesBenchmarkingOverUnder(predicoesBenchmarkingRaw);
  const predicoes = [...predicoesAntigas, ...predicoesBenchmarking];

  const calibPorSelecao = {};
  calibracoesRaw.forEach((c) => {
    if (c.method !== usarCalibracao) return;
    calibPorSelecao[c.selection] = c.method === 'platt'
      ? { tipo: 'platt', a: Number(c.platt_coef), b: Number(c.platt_intercept) }
      : { tipo: 'isotonic', x: c.isotonic_x, y: c.isotonic_y };
  });
  if (predicoes.length === 0) return { status: 200, body: { parametros: {}, execucoes: [] } };

  // Interseção: só partidas com predição DO modelo E dentro do período de teste
  const matchIdsSet = new Set(predicoes.map((p) => p.match_id));
  const matchesValidos = todasMatchesFiltradas.filter((m) => matchIdsSet.has(m.id));
  const matchIdsValidos = new Set(matchesValidos.map((m) => m.id));
  const matchIdsValidosArray = [...matchIdsValidos];
  const matchPorId = {};
  matchesValidos.forEach((m) => { matchPorId[m.id] = m; });

  // Stage 2: odds apenas para partidas válidas (conjunto pequeno, ~centenas de IDs)
  const [pinnacleAberturaRaw, pinnacleFechaRaw] = await Promise.all([
    matchIdsValidosArray.length > 0
      ? buscarTudoPaginadoIn(matchIdsValidosArray, (ids) => supabase.from('odds_market').select('match_id, selection, odds').eq('market', mercado).eq('snapshot', 'pre_closing').eq('bookmaker', 'pinnacle').in('match_id', ids))
      : Promise.resolve([]),
    matchIdsValidosArray.length > 0
      ? buscarTudoPaginadoIn(matchIdsValidosArray, (ids) => supabase.from('odds_market').select('match_id, selection, odds').eq('market', mercado).eq('snapshot', 'closing').eq('bookmaker', 'pinnacle').in('match_id', ids))
      : Promise.resolve([]),
  ]);

  const pinnAberturaPorChave = {};
  pinnacleAberturaRaw.filter((r) => matchIdsValidos.has(r.match_id)).forEach((r) => { pinnAberturaPorChave[`${r.match_id}__${r.selection}`] = Number(r.odds); });
  const pinnFechaPorChave = {};
  pinnacleFechaRaw.filter((r) => matchIdsValidos.has(r.match_id)).forEach((r) => { pinnFechaPorChave[`${r.match_id}__${r.selection}`] = Number(r.odds); });

  const construirCandidatos = (oddExecucaoPorChave) => {
    const candidatos = [];
    for (const p of predicoes) {
      if (!matchIdsValidos.has(p.match_id)) continue;
      const match = matchPorId[p.match_id];
      const resultadoReal = calcularResultadoMercadoSimulacao(match, mercado);
      if (!resultadoReal) continue;
      const chave = `${p.match_id}__${p.selection}`;
      const odd = oddExecucaoPorChave[chave];
      if (odd == null) continue;

      let pModelo = Number(p.probability);
      if (usarCalibracao !== 'nenhuma') {
        const calib = calibPorSelecao[p.selection];
        if (!calib) continue;
        pModelo = calib.tipo === 'platt' ? aplicarPlattPredicao(pModelo, calib.a, calib.b) : aplicarIsotonicPredicao(pModelo, calib.x, calib.y);
        if (pModelo == null) continue;
      }
      const ev = pModelo * odd;
      if (ev < evMinimo || ev > evMaximo) continue;

      const oddPinnAbertura = pinnAberturaPorChave[chave] ?? null;
      const oddPinnFecha = pinnFechaPorChave[chave] ?? null;
      const clv = oddPinnAbertura != null && oddPinnFecha != null ? (oddPinnAbertura / oddPinnFecha - 1) * 100 : null;

      candidatos.push({ match_id: p.match_id, data: match.match_date.slice(0, 10), league_id: match.league_id, selection: p.selection, p_modelo: pModelo, odd, ev, resultado_real: resultadoReal, clv });
    }
    return candidatos;
  };

  const simularCarteira = (candidatos) => {
    const porDia = {};
    candidatos.forEach((c) => { (porDia[c.data] = porDia[c.data] || []).push(c); });
    const diasOrdenados = Object.keys(porDia).sort();

    let banca = bancaInicial, pico = bancaInicial;
    const rodadas = [];
    const todasApostas = [];

    diasOrdenados.forEach((dia) => {
      const bancaInicialRodada = banca;
      const brutas = porDia[dia].map((c) => {
        let stakeBruta = 0;
        if (tipoStake === 'fixa') {
          stakeBruta = stakeFixa;
        } else {
          const b = c.odd - 1;
          const f = b > 0 ? (c.p_modelo * b - (1 - c.p_modelo)) / b : 0;
          stakeBruta = Math.max(0, f) * kellyMultiplier * bancaInicialRodada;
        }
        return { ...c, stake_bruta: stakeBruta };
      });
      const pisoStake = stakeMinimaPct * bancaInicialRodada;
      const validas = brutas.filter((b) => b.stake_bruta >= pisoStake);
      if (validas.length === 0) return;

      const somaStakes = validas.reduce((s, v) => s + v.stake_bruta, 0);
      const teto = tetoExposicaoPct * bancaInicialRodada;
      const fatorEscala = somaStakes > 0 ? Math.min(1, teto / somaStakes) : 1;

      const apostasRodada = validas.map((v) => {
        const stake = v.stake_bruta * fatorEscala;
        const venceu = v.resultado_real === v.selection;
        const lucro = venceu ? stake * (v.odd - 1) : -stake;
        return { ...v, stake, venceu, lucro };
      });

      const resultadoLiquido = apostasRodada.reduce((s, a) => s + a.lucro, 0);
      const exposicaoTotal = apostasRodada.reduce((s, a) => s + a.stake, 0);
      const bancaFinalRodada = bancaInicialRodada + resultadoLiquido;
      pico = Math.max(pico, bancaFinalRodada);
      const drawdownAtual = pico > 0 ? (pico - bancaFinalRodada) / pico : 0;
      const vitorias = apostasRodada.filter((a) => a.venceu).length;

      rodadas.push({
        rodada: rodadas.length + 1, data: dia, banca_inicial: bancaInicialRodada, qtd_apostas: apostasRodada.length,
        exposicao_pct: (exposicaoTotal / bancaInicialRodada) * 100, vitorias, derrotas: apostasRodada.length - vitorias,
        resultado_liquido: resultadoLiquido, retorno_pct: (resultadoLiquido / bancaInicialRodada) * 100,
        banca_final: bancaFinalRodada, drawdown_pct: drawdownAtual * 100, escalado: fatorEscala < 0.999999,
      });
      todasApostas.push(...apostasRodada);
      banca = bancaFinalRodada;
    });

    const bancaFinal = banca;
    const roiTotalPct = ((bancaFinal - bancaInicial) / bancaInicial) * 100;

    let cagrPct = null, diasTotais = 0;
    if (diasOrdenados.length > 0 && rodadas.length > 0) {
      const d0 = new Date(diasOrdenados[0]);
      const d1 = new Date(rodadas[rodadas.length - 1].data);
      diasTotais = Math.max(Math.round((d1 - d0) / 86400000), 1);
      const anos = diasTotais / 365;
      if (bancaFinal > 0 && anos > 0) cagrPct = (Math.pow(bancaFinal / bancaInicial, 1 / anos) - 1) * 100;
    }

    const curva = [bancaInicial, ...rodadas.map((r) => r.banca_final)];
    let picoCorrente = curva[0], picoIdxCorrente = 0, mdd = 0, idxPicoMdd = 0, idxFundoMdd = 0;
    curva.forEach((v, i) => {
      if (v > picoCorrente) { picoCorrente = v; picoIdxCorrente = i; }
      const dd = picoCorrente > 0 ? (picoCorrente - v) / picoCorrente : 0;
      if (dd > mdd) { mdd = dd; idxPicoMdd = picoIdxCorrente; idxFundoMdd = i; }
    });

    const totalApostas = todasApostas.length;
    const vitoriasTotais = todasApostas.filter((a) => a.venceu).length;
    const winRatePct = totalApostas > 0 ? (vitoriasTotais / totalApostas) * 100 : null;

    const comClv = todasApostas.filter((a) => a.clv != null);
    const clvMedioPct = comClv.length > 0 ? comClv.reduce((s, a) => s + a.clv, 0) / comClv.length : null;

    const retornosRodada = rodadas.map((r) => r.retorno_pct / 100);
    let sharpe = null, sortino = null;
    if (retornosRodada.length > 1) {
      const media = retornosRodada.reduce((s, r) => s + r, 0) / retornosRodada.length;
      const variancia = retornosRodada.reduce((s, r) => s + (r - media) ** 2, 0) / (retornosRodada.length - 1);
      const desvio = Math.sqrt(variancia);
      sharpe = desvio > 0 ? media / desvio : null;
      const negativos = retornosRodada.filter((r) => r < 0);
      if (negativos.length > 1) {
        const downsideVar = negativos.reduce((s, r) => s + r ** 2, 0) / negativos.length;
        const downsideStd = Math.sqrt(downsideVar);
        sortino = downsideStd > 0 ? media / downsideStd : null;
      }
    }

    const sumario = {
      modelo, n_candidatos_brutos: predicoes.length, n_passaram_ev: candidatos.length, n_rodadas_com_aposta: rodadas.length,
      n_apostas_totais: totalApostas, banca_inicial: bancaInicial, banca_final: bancaFinal, roi_total_pct: roiTotalPct,
      cagr_pct: cagrPct, dias_totais: diasTotais, mdd_pct: mdd * 100,
      mdd_pico_valor: rodadas.length ? curva[idxPicoMdd] : null, mdd_pico_rodada: idxPicoMdd,
      mdd_fundo_valor: rodadas.length ? curva[idxFundoMdd] : null, mdd_fundo_rodada: idxFundoMdd,
      win_rate_pct: winRatePct, clv_medio_pct: clvMedioPct, n_bets_com_clv: comClv.length,
      sharpe_simplificado: sharpe, sortino_simplificado: sortino,
    };

    return { sumario, rodadas };
  };

  const execucoes = [
    { execucao: 'abertura', ...simularCarteira(construirCandidatos(pinnAberturaPorChave)) },
    { execucao: 'fechamento', ...simularCarteira(construirCandidatos(pinnFechaPorChave)) },
  ];

  return {
    status: 200,
    body: {
      parametros: {
        modelo, mercado, liga_id: ligaIdNum, temporada: temporada || null, temporada_minima_teste: TEMPORADA_TESTE_MINIMA,
        data_inicio: dataInicio, data_fim: dataFimBruta,
        usar_calibracao: usarCalibracao, ev_minimo: evMinimo, stake_minima_pct: stakeMinimaPct,
        teto_exposicao_pct: tetoExposicaoPct, banca_inicial: bancaInicial,
      },
      execucoes,
    },
  };
}

// ============================================================
// TAREFAS: Carteira (Paper Trading)
// Diferente de tarefaSimulacaoCarteira (recalculada do zero sobre histórico
// JÁ RESOLVIDO), aqui a banca é PERSISTENTE e evolui sobre partidas AINDA
// NÃO disputadas (matches.status='scheduled') das LIGAS_DOMESTICAS (únicas
// com odds ao vivo sincronizadas via tarefaOddsSync/tarefaOddsTodas em
// odds_market, snapshot='pre_closing'), nos mesmos 3 mercados já cobertos
// em treino (MERCADOS_CARTEIRA_SUPORTADOS). Duas tabelas novas
// (paper_trading_carteiras/paper_trading_apostas, ver migração
// create_paper_trading): uma carteira guarda modelo+mercado+parâmetros de
// stake/EV+banca_atual; cada aposta é uma linha presa a ela.
//
//   ?tarefa=paper-carteira-criar (POST, auth)         -> cria carteira nova
//   ?tarefa=paper-carteira-listar                     -> lista todas + resumo (banca/ROI/pendentes)
//   ?tarefa=paper-carteira-detalhe&carteira_id=X       -> carteira + histórico completo de apostas
//   ?tarefa=paper-carteira-apostar&carteira_id=X (POST, auth)
//                               -> varre partidas agendadas com odds+predição do modelo/mercado da
//                                   carteira ainda não apostadas, aplica filtro de EV, dimensiona
//                                   stake (Kelly/fixa) contra a banca_atual e debita
//   ?tarefa=paper-carteira-resolver[&carteira_id=X] (POST, auth)
//                               -> resolve apostas pendentes cuja partida já tem resultado
//                                   (finished/cancelled), credita a banca_atual. Sem carteira_id,
//                                   resolve todas.
//   ?tarefa=paper-carteira-alternar-ativa&carteira_id=X (POST, auth) -> pausa/reativa
//   ?tarefa=paper-carteira-excluir&carteira_id=X (POST, auth)        -> apaga carteira + apostas
//   ?tarefa=paper-carteira-rodar-todas -> cron diário (vercel.json): apostar+resolver em
//                                   sequência pra TODAS as carteiras ativas. Sem checagem de auth
//                                   de propósito (mesmo padrão de odds-todas/elo-rotativo -- só o
//                                   cron chama, nunca o frontend).
// ============================================================

async function buscarPredicoesCarteira(supabase, matchIds, modelo, mercado) {
  const [antigasRaw, benchRaw] = await Promise.all([
    mercado === '1X2'
      ? buscarTudoPaginadoIn(matchIds, (ids) => supabase.from('model_predictions').select('match_id, selection, probability').eq('model_name', modelo).in('market', ['1X2', '1x2']).in('match_id', ids))
      : buscarTudoPaginadoIn(matchIds, (ids) => supabase.from('model_predictions').select('match_id, selection, probability').eq('model_name', modelo).eq('market', mercado).in('match_id', ids)),
    mercado === '1X2'
      ? buscarTudoPaginadoIn(matchIds, (ids) => supabase.from('predicoes').select('match_id, model_name, prob_home, prob_draw, prob_away').eq('model_name', modelo).eq('mercado', '1X2').in('match_id', ids))
      : buscarTudoPaginadoIn(matchIds, (ids) => supabase.from('predicoes').select('match_id, model_name, prob_under, prob_over').eq('model_name', modelo).eq('mercado', mercado).in('match_id', ids)),
  ]);
  const bench = mercado === '1X2' ? normalizarPredicoesBenchmarking(benchRaw) : normalizarPredicoesBenchmarkingOverUnder(benchRaw);
  return [...antigasRaw, ...bench];
}

// Melhor (maior) odd por (match_id, selection) entre as casas capturadas --
// mesmo critério já usado em rodar_predicoes.py (calcular_melhor_odd_por_
// partida): é a odd que de fato dá mais valor pro apostador.
function melhorOddPorChave(linhasOdds) {
  const mapa = {};
  linhasOdds.forEach((r) => {
    const chave = `${r.match_id}__${r.selection}`;
    const odd = Number(r.odds);
    const atual = mapa[chave];
    if (!atual || odd > atual.odd) mapa[chave] = { odd, bookmaker: r.bookmaker };
  });
  return mapa;
}

// Núcleo de "apostar" -- sem checagem de auth (chamado tanto pela tarefa
// clicável no frontend quanto pelo cron rodar-todas). Varre partidas
// agendadas, aplica EV, dimensiona stake e credita/debita banca_atual.
async function apostarCarteira(supabase, carteira) {
  const ligasAlvo = carteira.liga_id ? [carteira.liga_id] : LIGAS_DOMESTICAS;
  const matchesAgendadas = await buscarTudoPaginado(() =>
    supabase.from('matches').select('id, league_id, match_date, status').eq('status', 'scheduled').in('league_id', ligasAlvo));
  if (matchesAgendadas.length === 0) {
    return { apostas_novas: 0, motivo: 'Nenhuma partida agendada nas ligas configuradas.', banca_atual: Number(carteira.banca_atual) };
  }
  const matchIds = matchesAgendadas.map((m) => m.id);
  const matchPorId = Object.fromEntries(matchesAgendadas.map((m) => [m.id, m]));

  const [predicoesRaw, oddsRaw, calibracoesRaw, jaApostado] = await Promise.all([
    buscarPredicoesCarteira(supabase, matchIds, carteira.modelo, carteira.mercado),
    buscarTudoPaginadoIn(matchIds, (ids) => supabase.from('odds_market').select('match_id, selection, odds, bookmaker').eq('market', carteira.mercado).eq('snapshot', 'pre_closing').in('match_id', ids)),
    carteira.usar_calibracao === 'nenhuma'
      ? Promise.resolve([])
      : buscarTudoPaginado(() => supabase.from('model_calibration').select('selection, method, platt_coef, platt_intercept, isotonic_x, isotonic_y').eq('model_name', carteira.modelo).eq('market', carteira.mercado)),
    buscarTudoPaginado(() => supabase.from('paper_trading_apostas').select('match_id, selection').eq('carteira_id', carteira.id)),
  ]);

  if (predicoesRaw.length === 0) return { apostas_novas: 0, motivo: `Sem predição de "${carteira.modelo}" pra nenhuma partida agendada.`, banca_atual: Number(carteira.banca_atual) };

  const oddsPorChave = melhorOddPorChave(oddsRaw.filter((r) => matchPorId[r.match_id]));
  const calibPorSelecao = {};
  calibracoesRaw.forEach((c) => {
    if (c.method !== carteira.usar_calibracao) return;
    calibPorSelecao[c.selection] = c.method === 'platt'
      ? { tipo: 'platt', a: Number(c.platt_coef), b: Number(c.platt_intercept) }
      : { tipo: 'isotonic', x: c.isotonic_x, y: c.isotonic_y };
  });
  const jaApostadoSet = new Set(jaApostado.map((a) => `${a.match_id}__${a.selection}`));

  const evMinimo = Number(carteira.ev_minimo);
  const evMaximo = Number(carteira.ev_maximo);
  const candidatos = [];
  for (const p of predicoesRaw) {
    const match = matchPorId[p.match_id];
    if (!match) continue;
    const chave = `${p.match_id}__${p.selection}`;
    if (jaApostadoSet.has(chave)) continue;
    const melhor = oddsPorChave[chave];
    if (!melhor) continue;

    let pModelo = Number(p.probability);
    if (carteira.usar_calibracao !== 'nenhuma') {
      const calib = calibPorSelecao[p.selection];
      if (!calib) continue;
      pModelo = calib.tipo === 'platt' ? aplicarPlattPredicao(pModelo, calib.a, calib.b) : aplicarIsotonicPredicao(pModelo, calib.x, calib.y);
      if (pModelo == null) continue;
    }
    const ev = pModelo * melhor.odd;
    if (ev < evMinimo || ev > evMaximo) continue;

    candidatos.push({ match_id: p.match_id, selection: p.selection, odd: melhor.odd, bookmaker: melhor.bookmaker, p_modelo: pModelo, ev, data_partida: match.match_date });
  }

  if (candidatos.length === 0) return { apostas_novas: 0, motivo: 'Nenhum candidato passou o filtro de EV (ou já foi apostado antes).', banca_atual: Number(carteira.banca_atual) };

  const bancaBase = Number(carteira.banca_atual);
  const brutas = candidatos.map((c) => {
    let stakeBruta;
    if (carteira.tipo_stake === 'fixa') {
      stakeBruta = Number(carteira.stake_fixa);
    } else {
      const b = c.odd - 1;
      const f = b > 0 ? (c.p_modelo * b - (1 - c.p_modelo)) / b : 0;
      stakeBruta = Math.max(0, f) * Number(carteira.kelly_multiplier) * bancaBase;
    }
    return { ...c, stake_bruta: stakeBruta };
  });
  const piso = Number(carteira.stake_minima_pct) * bancaBase;
  const validas = brutas.filter((b) => b.stake_bruta >= piso);
  if (validas.length === 0) return { apostas_novas: 0, motivo: 'Candidatos ficaram abaixo do piso de stake mínima.', banca_atual: bancaBase };

  const somaStakes = validas.reduce((s, v) => s + v.stake_bruta, 0);
  const teto = Number(carteira.teto_exposicao_pct) * bancaBase;
  // Nunca alavanca: além do teto de exposição, o lote nunca pode comprometer
  // mais do que a própria banca_atual disponível.
  const fatorEscala = somaStakes > 0 ? Math.min(1, teto / somaStakes, bancaBase / somaStakes) : 1;

  const apostasParaInserir = [];
  let banca = bancaBase;
  for (const v of validas) {
    const stake = v.stake_bruta * fatorEscala;
    if (stake <= 0 || stake > banca) continue;
    apostasParaInserir.push({
      carteira_id: carteira.id, match_id: v.match_id, selection: v.selection, bookmaker: v.bookmaker,
      odd: v.odd, p_modelo: v.p_modelo, ev: v.ev, stake, status: 'pendente', banca_antes: banca, data_partida: v.data_partida,
    });
    banca -= stake;
  }
  if (apostasParaInserir.length === 0) return { apostas_novas: 0, motivo: 'Nenhuma aposta sobrou depois do dimensionamento de stake.', banca_atual: banca };

  const { error: erroInsert } = await supabase.from('paper_trading_apostas').insert(apostasParaInserir);
  if (erroInsert) throw erroInsert;
  await supabase.from('paper_trading_carteiras').update({ banca_atual: banca, updated_at: new Date().toISOString() }).eq('id', carteira.id);

  return { apostas_novas: apostasParaInserir.length, banca_atual: banca };
}

// Núcleo de "resolver" -- idem, sem checagem de auth própria. anulada =
// stake devolvido sem lucro/prejuízo (partida cancelada).
async function resolverCarteira(supabase, carteira) {
  const pendentes = await buscarTudoPaginado(() => supabase.from('paper_trading_apostas').select('*').eq('carteira_id', carteira.id).eq('status', 'pendente'));
  if (pendentes.length === 0) return { resolvidas: 0, banca_atual: Number(carteira.banca_atual) };

  const matchIds = [...new Set(pendentes.map((p) => p.match_id))];
  const matches = await buscarTudoPaginadoIn(matchIds, (ids) => supabase.from('matches').select('id, status, home_goals, away_goals').in('id', ids));
  const matchPorId = Object.fromEntries(matches.map((m) => [m.id, m]));

  let banca = Number(carteira.banca_atual);
  let resolvidas = 0;
  const agora = new Date().toISOString();
  for (const aposta of pendentes) {
    const match = matchPorId[aposta.match_id];
    if (!match) continue;

    if (match.status === 'cancelled') {
      banca += Number(aposta.stake);
      await supabase.from('paper_trading_apostas').update({ status: 'anulada', resultado_liquido: 0, banca_depois: banca, resolvido_em: agora }).eq('id', aposta.id);
      resolvidas++;
      continue;
    }

    const resultadoReal = calcularResultadoMercadoSimulacao(match, carteira.mercado);
    if (resultadoReal == null) continue; // ainda scheduled/live/postponed sem placar -- fica pendente

    const venceu = resultadoReal === aposta.selection;
    const retorno = venceu ? Number(aposta.stake) * Number(aposta.odd) : 0;
    const lucro = venceu ? Number(aposta.stake) * (Number(aposta.odd) - 1) : -Number(aposta.stake);
    banca += retorno;
    await supabase.from('paper_trading_apostas').update({ status: venceu ? 'ganhou' : 'perdeu', resultado_liquido: lucro, banca_depois: banca, resolvido_em: agora }).eq('id', aposta.id);
    resolvidas++;
  }

  if (resolvidas > 0) {
    await supabase.from('paper_trading_carteiras').update({ banca_atual: banca, updated_at: new Date().toISOString() }).eq('id', carteira.id);
  }
  return { resolvidas, banca_atual: banca };
}

async function tarefaPaperCarteiraCriar(supabase, authHeader, body) {
  const usuario = await verificarUsuarioLogado(supabase, authHeader);
  if (!usuario) return { status: 401, error: 'Não autenticado -- faça login antes de criar uma carteira.' };

  const { nome, modelo, mercado, liga_id, usar_calibracao, tipo_stake, stake_fixa, kelly_multiplier, teto_exposicao_pct, ev_minimo, ev_maximo, stake_minima_pct, banca_inicial } = body || {};
  if (!nome || !modelo) return { status: 400, error: 'nome e modelo são obrigatórios.' };
  if (liga_id != null && !LIGAS_DOMESTICAS.includes(Number(liga_id))) {
    return { status: 400, error: `liga_id precisa ser uma das ligas domésticas com odds ao vivo: ${LIGAS_DOMESTICAS.join(', ')} (ou deixe em branco pra todas).` };
  }
  const banca = Number(banca_inicial) > 0 ? Number(banca_inicial) : 1000;

  const { data, error } = await supabase.from('paper_trading_carteiras').insert({
    nome: String(nome).slice(0, 200),
    modelo,
    mercado: MERCADOS_CARTEIRA_SUPORTADOS.has(mercado) ? mercado : '1X2',
    liga_id: liga_id != null ? Number(liga_id) : null,
    usar_calibracao: ['platt', 'isotonic'].includes(usar_calibracao) ? usar_calibracao : 'nenhuma',
    tipo_stake: tipo_stake === 'fixa' ? 'fixa' : 'kelly',
    stake_fixa: Number(stake_fixa) > 0 ? Number(stake_fixa) : 10,
    kelly_multiplier: Number(kelly_multiplier) > 0 ? Number(kelly_multiplier) : 0.25,
    teto_exposicao_pct: Number(teto_exposicao_pct) > 0 ? Number(teto_exposicao_pct) : 0.15,
    ev_minimo: Number(ev_minimo) >= 1 ? Number(ev_minimo) : 1.02,
    ev_maximo: Number(ev_maximo) > 1 ? Number(ev_maximo) : 2.0,
    stake_minima_pct: Number(stake_minima_pct) >= 0 ? Number(stake_minima_pct) : 0.005,
    banca_inicial: banca,
    banca_atual: banca,
  }).select().single();
  if (error) return { status: 400, error: error.message };
  return { status: 200, carteira: data };
}

async function tarefaPaperCarteiraListar(supabase) {
  const [carteiras, ligasDomesticas] = await Promise.all([
    buscarTudoPaginado(() => supabase.from('paper_trading_carteiras').select('*').order('created_at', { ascending: false })),
    supabase.from('leagues').select('id, name').in('id', LIGAS_DOMESTICAS).then((r) => r.data || []),
  ]);
  if (carteiras.length === 0) return { status: 200, carteiras: [], ligas_domesticas: ligasDomesticas };

  const ids = carteiras.map((c) => c.id);
  const apostas = await buscarTudoPaginadoIn(ids, (lote) => supabase.from('paper_trading_apostas').select('carteira_id, status, stake, resultado_liquido').in('carteira_id', lote));

  const resumoPorCarteira = {};
  apostas.forEach((a) => {
    const r = (resumoPorCarteira[a.carteira_id] = resumoPorCarteira[a.carteira_id] || { pendentes: 0, ganhou: 0, perdeu: 0, anulada: 0, resultado_liquido_total: 0 });
    r[a.status] = (r[a.status] || 0) + 1;
    if (a.status !== 'pendente' && a.resultado_liquido != null) r.resultado_liquido_total += Number(a.resultado_liquido);
  });

  const comResumo = carteiras.map((c) => {
    const r = resumoPorCarteira[c.id] || { pendentes: 0, ganhou: 0, perdeu: 0, anulada: 0, resultado_liquido_total: 0 };
    const bancaInicial = Number(c.banca_inicial);
    return {
      ...c,
      resumo: {
        n_apostas_pendentes: r.pendentes,
        n_apostas_resolvidas: r.ganhou + r.perdeu + r.anulada,
        n_apostas_ganhas: r.ganhou,
        n_apostas_perdidas: r.perdeu,
        resultado_liquido_total: r.resultado_liquido_total,
        roi_total_pct: bancaInicial > 0 ? ((Number(c.banca_atual) - bancaInicial) / bancaInicial) * 100 : null,
        win_rate_pct: (r.ganhou + r.perdeu) > 0 ? (r.ganhou / (r.ganhou + r.perdeu)) * 100 : null,
      },
    };
  });

  return { status: 200, carteiras: comResumo, ligas_domesticas: ligasDomesticas };
}

async function tarefaPaperCarteiraDetalhe(supabase, carteiraId) {
  if (!carteiraId) return { status: 400, error: 'carteira_id é obrigatório.' };
  const { data: carteira, error } = await supabase.from('paper_trading_carteiras').select('*').eq('id', carteiraId).maybeSingle();
  if (error) return { status: 400, error: error.message };
  if (!carteira) return { status: 404, error: 'Carteira não encontrada.' };

  const apostas = await buscarTudoPaginado(() => supabase.from('paper_trading_apostas').select('*').eq('carteira_id', carteiraId).order('data_partida', { ascending: false }));
  const matchIds = [...new Set(apostas.map((a) => a.match_id))];
  const matches = matchIds.length > 0
    ? await buscarTudoPaginadoIn(matchIds, (ids) => supabase.from('matches').select('id, match_date, home:teams!matches_home_team_id_fkey(name), away:teams!matches_away_team_id_fkey(name)').in('id', ids))
    : [];
  const matchPorId = Object.fromEntries(matches.map((m) => [m.id, m]));

  const apostasComPartida = apostas.map((a) => ({
    ...a,
    partida: matchPorId[a.match_id] ? { home: matchPorId[a.match_id].home?.name, away: matchPorId[a.match_id].away?.name, match_date: matchPorId[a.match_id].match_date } : null,
  }));

  return { status: 200, carteira, apostas: apostasComPartida };
}

async function tarefaPaperCarteiraApostar(supabase, authHeader, carteiraId) {
  const usuario = await verificarUsuarioLogado(supabase, authHeader);
  if (!usuario) return { status: 401, error: 'Não autenticado.' };
  if (!carteiraId) return { status: 400, error: 'carteira_id é obrigatório.' };

  const { data: carteira, error } = await supabase.from('paper_trading_carteiras').select('*').eq('id', carteiraId).maybeSingle();
  if (error) return { status: 400, error: error.message };
  if (!carteira) return { status: 404, error: 'Carteira não encontrada.' };
  if (!carteira.ativa) return { status: 400, error: 'Carteira está pausada -- reative antes de apostar.' };

  const resultado = await apostarCarteira(supabase, carteira);
  return { status: 200, carteira_id: carteiraId, ...resultado };
}

async function tarefaPaperCarteiraResolver(supabase, authHeader, carteiraId) {
  const usuario = await verificarUsuarioLogado(supabase, authHeader);
  if (!usuario) return { status: 401, error: 'Não autenticado.' };

  let query = supabase.from('paper_trading_carteiras').select('*');
  if (carteiraId) query = query.eq('id', carteiraId);
  const { data: carteiras, error } = await query;
  if (error) return { status: 400, error: error.message };
  if (carteiraId && (!carteiras || carteiras.length === 0)) return { status: 404, error: 'Carteira não encontrada.' };

  const detalhe = [];
  let totalResolvidas = 0;
  for (const carteira of (carteiras || [])) {
    const r = await resolverCarteira(supabase, carteira);
    totalResolvidas += r.resolvidas;
    detalhe.push({ carteira_id: carteira.id, ...r });
  }
  return { status: 200, total_resolvidas: totalResolvidas, carteiras: detalhe };
}

async function tarefaPaperCarteiraAlternarAtiva(supabase, authHeader, carteiraId) {
  const usuario = await verificarUsuarioLogado(supabase, authHeader);
  if (!usuario) return { status: 401, error: 'Não autenticado.' };
  if (!carteiraId) return { status: 400, error: 'carteira_id é obrigatório.' };

  const { data: carteira, error } = await supabase.from('paper_trading_carteiras').select('ativa').eq('id', carteiraId).maybeSingle();
  if (error) return { status: 400, error: error.message };
  if (!carteira) return { status: 404, error: 'Carteira não encontrada.' };

  const { data, error: erroUpdate } = await supabase.from('paper_trading_carteiras').update({ ativa: !carteira.ativa, updated_at: new Date().toISOString() }).eq('id', carteiraId).select().single();
  if (erroUpdate) return { status: 400, error: erroUpdate.message };
  return { status: 200, carteira: data };
}

async function tarefaPaperCarteiraExcluir(supabase, authHeader, carteiraId) {
  const usuario = await verificarUsuarioLogado(supabase, authHeader);
  if (!usuario) return { status: 401, error: 'Não autenticado.' };
  if (!carteiraId) return { status: 400, error: 'carteira_id é obrigatório.' };

  const { error } = await supabase.from('paper_trading_carteiras').delete().eq('id', carteiraId);
  if (error) return { status: 400, error: error.message };
  return { status: 200, excluido: true };
}

// Cron diário (vercel.json) -- roda apostar+resolver em sequência pra todas
// as carteiras ativas. Sem auth (mesmo padrão de odds-todas/elo-rotativo).
async function tarefaPaperCarteiraRodarTodas(supabase) {
  const carteiras = await buscarTudoPaginado(() => supabase.from('paper_trading_carteiras').select('*').eq('ativa', true));
  const resultado = [];
  for (const carteira of carteiras) {
    try {
      const apostou = await apostarCarteira(supabase, carteira);
      const carteiraAtualizada = { ...carteira, banca_atual: apostou.banca_atual };
      const resolveu = await resolverCarteira(supabase, carteiraAtualizada);
      resultado.push({ carteira_id: carteira.id, nome: carteira.nome, apostas_novas: apostou.apostas_novas, resolvidas: resolveu.resolvidas, banca_atual: resolveu.banca_atual });
    } catch (e) {
      resultado.push({ carteira_id: carteira.id, nome: carteira.nome, erro: e.message });
    }
  }
  return { carteiras_processadas: resultado.length, resultado };
}

export default async function handler(req, res) {
  if (applyCors(req, res)) return;
  const supabaseUrl = process.env.SUPABASE_URL, serviceKey = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!supabaseUrl || !serviceKey) {
    return res.status(500).json({ error: { message: 'SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY não configuradas.' } });
  }
  const supabase = getSupabase();
  const { tarefa, liga_id, escopo, minimo, forcar, codigo, temporada, api_football_id, limite } = req.query;

  try {
    if (tarefa === 'backfill-competicao') {
      const apiKey = process.env.FOOTBALL_DATA_KEY;
      if (!apiKey) return res.status(500).json({ error: { message: 'FOOTBALL_DATA_KEY não configurada.' } });
      if (!codigo || !temporada) return res.status(400).json({ error: { message: 'tarefa=backfill-competicao precisa de ?codigo=XX&temporada=AAAA.' } });
      const resultado = await tarefaBackfillCompeticao(supabase, apiKey, codigo, Number(temporada));
      if (resultado.error) return res.status(400).json({ error: { message: resultado.error } });
      return res.status(200).json(resultado);
    }

    if (tarefa === 'backfill-api-football') {
      const apiKey = process.env.API_FOOTBALL_KEY;
      if (!apiKey) return res.status(500).json({ error: { message: 'API_FOOTBALL_KEY não configurada.' } });
      if (!api_football_id || !temporada) return res.status(400).json({ error: { message: 'tarefa=backfill-api-football precisa de ?api_football_id=X&temporada=AAAA.' } });
      const resultado = await tarefaBackfillApiFootball(supabase, apiKey, api_football_id, Number(temporada));
      if (resultado.error) return res.status(400).json({ error: { message: resultado.error } });
      return res.status(200).json(resultado);
    }

    if (tarefa === 'af-diagnostico-time') {
      const apiKey = process.env.API_FOOTBALL_KEY;
      if (!apiKey) return res.status(500).json({ error: { message: 'API_FOOTBALL_KEY não configurada.' } });
      if (!api_football_id) return res.status(400).json({ error: { message: 'tarefa=af-diagnostico-time precisa de ?api_football_id=X.' } });
      return res.status(200).json(await tarefaDiagnosticoTime(apiKey, api_football_id));
    }

    if (tarefa === 'statsapi-diagnostico') {
      const apiKey = process.env.THE_STATSAPI_KEY;
      if (!apiKey) return res.status(500).json({ error: { message: 'THE_STATSAPI_KEY não configurada.' } });
      if (!req.query.caminho) return res.status(400).json({ error: { message: 'tarefa=statsapi-diagnostico precisa de ?caminho=/football/... (ex: /football/competitions?search=brazil).' } });
      return res.status(200).json(await tarefaDiagnosticoStatsApi(apiKey, req.query.caminho));
    }

    if (tarefa === 'info-clubes') {
      const apiKey = process.env.API_FOOTBALL_KEY;
      if (!apiKey) return res.status(500).json({ error: { message: 'API_FOOTBALL_KEY não configurada.' } });
      return res.status(200).json(await tarefaInfoClubes(supabase, apiKey, Number(limite) || 40));
    }

    if (tarefa === 'odds-descobrir') {
      const apiKey = process.env.ODDSPAPI_KEY;
      if (!apiKey) return res.status(500).json({ error: { message: 'ODDSPAPI_KEY não configurada.' } });
      return res.status(200).json(await tarefaOddsDescobrir(supabase, apiKey, forcar === 'true'));
    }

    if (tarefa === 'odds-sync-diagnostico') {
      const apiKey = process.env.ODDSPAPI_KEY;
      if (!apiKey) return res.status(500).json({ error: { message: 'ODDSPAPI_KEY não configurada.' } });
      const resultado = await tarefaOddsSyncDiagnostico(supabase, apiKey, { tournamentIds: req.query.tournament_ids, bookmaker: req.query.bookmaker });
      if (resultado.error) return res.status(400).json({ error: { message: resultado.error } });
      return res.status(200).json(resultado);
    }

    if (tarefa === 'odds') {
      const apiKey = process.env.ODDSPAPI_KEY;
      if (!apiKey) return res.status(500).json({ error: { message: 'ODDSPAPI_KEY não configurada.' } });
      if (!liga_id) return res.status(400).json({ error: { message: 'tarefa=odds precisa de ?liga_id=X.' } });
      const resultado = await tarefaOddsSync(supabase, apiKey, Number(liga_id));
      if (resultado.error) return res.status(400).json({ error: { message: resultado.error } });
      return res.status(200).json(resultado);
    }

    if (tarefa === 'odds-todas') {
      const apiKey = process.env.ODDSPAPI_KEY;
      if (!apiKey) return res.status(500).json({ error: { message: 'ODDSPAPI_KEY não configurada.' } });
      return res.status(200).json(await tarefaOddsTodas(supabase, apiKey));
    }

    if (tarefa === 'odds-historico-descobrir') {
      const apiKey = process.env.ODDSPAPI_KEY;
      if (!apiKey) return res.status(500).json({ error: { message: 'ODDSPAPI_KEY não configurada.' } });
      if (!liga_id) return res.status(400).json({ error: { message: 'tarefa=odds-historico-descobrir precisa de ?liga_id=X.' } });
      const resultado = await tarefaOddsHistoricoDescobrir(supabase, apiKey, Number(liga_id));
      if (resultado.error) return res.status(400).json({ error: { message: resultado.error } });
      return res.status(200).json(resultado);
    }

    if (tarefa === 'odds-historico') {
      const apiKey = process.env.ODDSPAPI_KEY;
      if (!apiKey) return res.status(500).json({ error: { message: 'ODDSPAPI_KEY não configurada.' } });
      if (!liga_id) return res.status(400).json({ error: { message: 'tarefa=odds-historico precisa de ?liga_id=X.' } });
      const resultado = await tarefaOddsHistorico(supabase, apiKey, { ligaId: Number(liga_id), temporada, limite });
      if (resultado.error) return res.status(400).json({ error: { message: resultado.error } });
      return res.status(200).json(resultado);
    }

    if (tarefa === 'refresh-cobertura-odds') {
      const { error: erroRefresh } = await supabase.rpc('refresh_vw_cobertura_odds');
      if (erroRefresh) return res.status(400).json({ error: { message: erroRefresh.message } });
      return res.status(200).json({ mensagem: 'vw_cobertura_odds / vw_cobertura_odds_bookmaker atualizadas.' });
    }

    if (tarefa === 'elo') {
      if (liga_id) {
        const ligaIdNum = Number(liga_id);
        if (!LIGAS_DOMESTICAS.includes(ligaIdNum)) {
          return res.status(400).json({ error: { message: `liga_id inválido — precisa ser uma das ligas domésticas: ${LIGAS_DOMESTICAS.join(', ')}.` } });
        }
        return res.status(200).json(await eloProcessarLiga(supabase, ligaIdNum));
      }
      if (escopo === 'geral') return res.status(200).json(await eloProcessarGeral(supabase));
      return res.status(400).json({ error: { message: 'tarefa=elo precisa de ?liga_id=X ou ?escopo=geral.' }, ligas_domesticas: LIGAS_DOMESTICAS });
    }

    // Pensada pro cron diário: processa só UM escopo por dia, revezando entre
    // as 6 ligas domésticas + geral (dia do ano mod 7) — já testado antes que
    // rodar tudo numa chamada só estoura os 60s do Vercel mesmo com
    // maxDuration configurado, então cada slot fica um dia sem recompute
    // novo, mas o ciclo completo (todos os 7 escopos) fecha em ~1 semana.
    if (tarefa === 'elo-rotativo') {
      const ESCOPOS_ROTACAO = [...LIGAS_DOMESTICAS, 'geral'];
      const diaDoAno = Math.floor((Date.now() - new Date(new Date().getUTCFullYear(), 0, 0)) / 86400000);
      const slot = ESCOPOS_ROTACAO[diaDoAno % ESCOPOS_ROTACAO.length];
      const resultado = slot === 'geral' ? await eloProcessarGeral(supabase) : await eloProcessarLiga(supabase, slot);
      return res.status(200).json({ slot_de_hoje: slot, ...resultado });
    }

    if (tarefa === 'player-elo') {
      return res.status(200).json(await tarefaPlayerElo(supabase, Number(limite) || 200));
    }

    if (tarefa === 'player-elo-reset') {
      return res.status(200).json(await tarefaPlayerEloReset(supabase));
    }

    if (tarefa === 'config-get') {
      return res.status(200).json(await tarefaConfigGet(supabase, req.query.model_name));
    }

    if (tarefa === 'config-set') {
      const resultado = await tarefaConfigSet(supabase, req.query.model_name, req.body);
      if (resultado.error) return res.status(400).json({ error: { message: resultado.error } });
      return res.status(200).json(resultado);
    }

    if (tarefa === 'jogador-perfil') {
      const resultado = await tarefaJogadorPerfil(supabase, req.query.player_id ? Number(req.query.player_id) : null);
      if (resultado.error) return res.status(400).json({ error: { message: resultado.error } });
      return res.status(200).json(resultado);
    }

    if (tarefa === 'calibracao') {
      return res.status(200).json(await tarefaCalibracao(supabase, Number(minimo) || 80));
    }

    if (tarefa === 'fotmob-liga-buscar') {
      if (!req.query.termo) return res.status(400).json({ error: { message: 'tarefa=fotmob-liga-buscar precisa de ?termo=NomeDaLiga.' } });
      const resultado = await tarefaFotmobLigaBuscar(req.query.termo);
      if (resultado.error) return res.status(400).json({ error: { message: resultado.error } });
      return res.status(200).json(resultado);
    }

    if (tarefa === 'backfill-fotmob-liga') {
      const { fotmob_league_id, nome, pais, confederacao, tipo, simbolo_url } = req.query;
      if (!fotmob_league_id || !temporada) return res.status(400).json({ error: { message: 'tarefa=backfill-fotmob-liga precisa de ?fotmob_league_id=X&temporada=AAAA (formato FotMob, ex: 2024 ou 2024/2025).' } });
      const resultado = await tarefaBackfillFotmobLiga(supabase, { fotmobLeagueId: fotmob_league_id, temporada, nome, pais, confederacao, tipo, simboloUrl: simbolo_url });
      if (resultado.error) return res.status(400).json({ error: { message: resultado.error } });
      return res.status(200).json(resultado);
    }

    if (tarefa === 'partidas-fotmob') {
      const { modo } = req.query;
      const resultado = await tarefaPartidasFotmob(supabase, req.headers.authorization, { ligaId: liga_id, temporada, limite, modo });
      if (resultado.status === 401) return res.status(401).json({ error: { message: resultado.error } });
      if (resultado.error) return res.status(400).json({ error: { message: resultado.error } });
      return res.status(200).json(resultado);
    }

    if (tarefa === 'disparar-atualizar-stats') {
      const { liga_id: _lid, limite: _lim, modo: _modo, forcar } = req.query;
      const resultado = await tarefaDispararAtualizarStats(supabase, req.headers.authorization, { liga_id: _lid, limite: _lim, modo: _modo, forcar });
      const { status, ...corpo } = resultado;
      return res.status(status).json(status === 200 ? corpo : { error: { message: corpo.error } });
    }

    if (tarefa === 'teste-odds-api-football') {
      const apiKey = process.env.API_FOOTBALL_KEY;
      if (!apiKey) return res.status(500).json({ error: { message: 'API_FOOTBALL_KEY não configurada.' } });
      if (!liga_id) return res.status(400).json({ error: { message: 'tarefa=teste-odds-api-football precisa de ?liga_id=X[&temporada=AAAA].' } });
      const resultado = await tarefaTesteOddsApiFootball(supabase, apiKey, liga_id, temporada);
      if (resultado.error) return res.status(400).json({ error: { message: resultado.error } });
      return res.status(200).json(resultado);
    }

    if (tarefa === 'importar-jogos-api-football') {
      const apiKey = process.env.API_FOOTBALL_KEY;
      if (!apiKey) return res.status(500).json({ error: { message: 'API_FOOTBALL_KEY não configurada.' } });
      const resultado = await tarefaImportarJogosApiFootball(supabase, apiKey, liga_id, temporada);
      if (resultado.error) return res.status(400).json({ error: { message: resultado.error } });
      return res.status(200).json(resultado);
    }

    if (tarefa === 'importar-jogos-fotmob') {
      const resultado = await tarefaImportarJogosFotmob(supabase, liga_id, temporada);
      if (resultado.error) return res.status(400).json({ error: { message: resultado.error } });
      return res.status(200).json(resultado);
    }

    if (tarefa === 'disparar-predicoes') {
      const resultado = await tarefaDispararPredicoes(supabase, req.headers.authorization);
      const { status, ...corpo } = resultado;
      return res.status(status).json(status === 200 ? corpo : { error: { message: corpo.error } });
    }

    if (tarefa === 'disparar-backtest') {
      const resultado = await tarefaDispararBacktest(supabase, req.headers.authorization);
      const { status, ...corpo } = resultado;
      return res.status(status).json(status === 200 ? corpo : { error: { message: corpo.error } });
    }

    if (tarefa === 'modelos-disponiveis') {
      return res.status(200).json(await tarefaModelosDisponiveis(supabase, req.query.mercado));
    }

    if (tarefa === 'simulacao-carteira') {
      const resultado = await tarefaSimulacaoCarteira(supabase, req.query);
      if (resultado.error) return res.status(resultado.status).json({ error: { message: resultado.error } });
      return res.status(resultado.status).json(resultado.body);
    }

    // ------------------------------------------------------------------
    // Carteira (Paper Trading)
    // ------------------------------------------------------------------

    if (tarefa === 'paper-carteira-criar') {
      const resultado = await tarefaPaperCarteiraCriar(supabase, req.headers.authorization, req.body);
      const { status, ...corpo } = resultado;
      return res.status(status).json(status === 200 ? corpo : { error: { message: corpo.error } });
    }

    if (tarefa === 'paper-carteira-listar') {
      const resultado = await tarefaPaperCarteiraListar(supabase);
      const { status, ...corpo } = resultado;
      return res.status(status).json(corpo);
    }

    if (tarefa === 'paper-carteira-detalhe') {
      const resultado = await tarefaPaperCarteiraDetalhe(supabase, req.query.carteira_id);
      const { status, ...corpo } = resultado;
      return res.status(status).json(status === 200 ? corpo : { error: { message: corpo.error } });
    }

    if (tarefa === 'paper-carteira-apostar') {
      const carteiraId = (req.body || {}).carteira_id || req.query.carteira_id;
      const resultado = await tarefaPaperCarteiraApostar(supabase, req.headers.authorization, carteiraId);
      const { status, ...corpo } = resultado;
      return res.status(status).json(status === 200 ? corpo : { error: { message: corpo.error } });
    }

    if (tarefa === 'paper-carteira-resolver') {
      const carteiraId = (req.body || {}).carteira_id || req.query.carteira_id;
      const resultado = await tarefaPaperCarteiraResolver(supabase, req.headers.authorization, carteiraId);
      const { status, ...corpo } = resultado;
      return res.status(status).json(status === 200 ? corpo : { error: { message: corpo.error } });
    }

    if (tarefa === 'paper-carteira-alternar-ativa') {
      const carteiraId = (req.body || {}).carteira_id || req.query.carteira_id;
      const resultado = await tarefaPaperCarteiraAlternarAtiva(supabase, req.headers.authorization, carteiraId);
      const { status, ...corpo } = resultado;
      return res.status(status).json(status === 200 ? corpo : { error: { message: corpo.error } });
    }

    if (tarefa === 'paper-carteira-excluir') {
      const carteiraId = (req.body || {}).carteira_id || req.query.carteira_id;
      const resultado = await tarefaPaperCarteiraExcluir(supabase, req.headers.authorization, carteiraId);
      const { status, ...corpo } = resultado;
      return res.status(status).json(status === 200 ? corpo : { error: { message: corpo.error } });
    }

    if (tarefa === 'paper-carteira-rodar-todas') {
      return res.status(200).json(await tarefaPaperCarteiraRodarTodas(supabase));
    }

    // ------------------------------------------------------------------
    // Painel de Treino Customizado
    // ------------------------------------------------------------------

    if (tarefa === 'catalogo-features') {
      return res.status(200).json(tarefaCatalogoFeatures());
    }

    if (tarefa === 'salvar-config-custom') {
      const resultado = await tarefaSalvarConfigCustom(supabase, req.headers.authorization, req.body);
      const { status, ...corpo } = resultado;
      return res.status(status).json(status === 200 ? corpo : { error: { message: corpo.error } });
    }

    if (tarefa === 'importar-odds-footiqo') {
      const resultado = await tarefaImportarOddsFootiqo(supabase, req.headers.authorization, req.body);
      const { status, ...corpo } = resultado;
      return res.status(status).json(status === 200 ? corpo : { error: { message: corpo.error } });
    }

    if (tarefa === 'listar-configs-custom') {
      const resultado = await tarefaListarConfigsCustom(supabase);
      const { status, ...corpo } = resultado;
      return res.status(status).json(corpo);
    }

    if (tarefa === 'excluir-config-custom') {
      const configId = (req.body || {}).config_id || req.query.config_id;
      const resultado = await tarefaExcluirConfigCustom(supabase, req.headers.authorization, configId);
      const { status, ...corpo } = resultado;
      return res.status(status).json(status === 200 ? corpo : { error: { message: corpo.error } });
    }

    if (tarefa === 'copiar-config-custom') {
      const configId = (req.body || {}).config_id || req.query.config_id;
      const resultado = await tarefaCopiarConfigCustom(supabase, req.headers.authorization, configId);
      const { status, ...corpo } = resultado;
      return res.status(status).json(status === 200 ? corpo : { error: { message: corpo.error } });
    }

    if (tarefa === 'cancelar-treino-custom') {
      const configId = (req.body || {}).config_id || req.query.config_id;
      const resultado = await tarefaCancelarTreinoCustom(supabase, req.headers.authorization, configId);
      const { status, ...corpo } = resultado;
      return res.status(status).json(status === 200 ? corpo : { error: { message: corpo.error } });
    }

    if (tarefa === 'resetar-config-custom') {
      const configId = (req.body || {}).config_id || req.query.config_id;
      const resultado = await tarefaResetarConfigCustom(supabase, req.headers.authorization, configId);
      const { status, ...corpo } = resultado;
      return res.status(status).json(status === 200 ? corpo : { error: { message: corpo.error } });
    }

    if (tarefa === 'disparar-treino-custom') {
      const configId = (req.body || {}).config_id || req.query.config_id;
      const resultado = await tarefaDispararTreinoCustom(supabase, req.headers.authorization, configId);
      const { status, ...corpo } = resultado;
      return res.status(status).json(status === 200 ? corpo : { error: { message: corpo.error } });
    }

    if (tarefa === 'estimar-partida-custom') {
      const resultado = await tarefaEstimarPartidaCustom(supabase, req.headers.authorization, req.body);
      const { status, ...corpo } = resultado;
      return res.status(status).json(status === 200 ? corpo : { error: { message: corpo.error } });
    }

    if (tarefa === 'relatorio-teste') {
      const configId = req.query.config_id;
      const pagina = req.query.pagina;
      const resultado = await tarefaRelatorioTeste(supabase, configId, pagina);
      const { status, ...corpo } = resultado;
      return res.status(status).json(status === 200 ? corpo : { error: { message: corpo.error } });
    }

    if (tarefa === 'backtest-custom') {
      const configId = req.query.config_id;
      const resultado = await tarefaBacktestCustom(supabase, configId);
      const { status, ...corpo } = resultado;
      return res.status(status).json(status === 200 ? corpo : { error: { message: corpo.error } });
    }

    // =====================================================================
    // Exploração de Dados — leitura paginada de qualquer tabela pública
    // =====================================================================
    if (tarefa === 'explorar-schema') {
      const tabela = req.query.tabela || '';
      if (!tabela) return res.status(400).json({ error: { message: 'Parâmetro tabela é obrigatório.' } });
      // Busca colunas via information_schema (service role tem acesso)
      const { data, error } = await supabase
        .from('information_schema.columns')
        .select('column_name,data_type,is_nullable,column_default')
        .eq('table_schema', 'public')
        .eq('table_name', tabela)
        .order('ordinal_position');
      if (error) {
        // PostgREST pode não expor information_schema — retorna vazio (frontend deriva de primeira linha)
        return res.status(200).json({ colunas: [] });
      }
      return res.status(200).json({ colunas: data || [] });
    }

    if (tarefa === 'explorar-dados') {
      const { tabela, pagina = '0', por_pagina = '100', coluna_busca = '', busca = '' } = req.query;
      if (!tabela) return res.status(400).json({ error: { message: 'Parâmetro tabela é obrigatório.' } });
      // Valida nome da tabela: só letras, números e underscores
      if (!/^[a-z][a-z0-9_]*$/.test(tabela)) {
        return res.status(400).json({ error: { message: 'Nome de tabela inválido.' } });
      }
      const pg = Math.max(0, parseInt(pagina, 10));
      const pp = Math.min(500, Math.max(10, parseInt(por_pagina, 10)));
      const inicio = pg * pp;
      const fim = inicio + pp - 1;
      let query = supabase.from(tabela).select('*', { count: 'exact' }).range(inicio, fim);
      if (busca && coluna_busca && /^[a-z][a-z0-9_]*$/.test(coluna_busca)) {
        query = query.ilike(coluna_busca, `%${busca}%`);
      }
      const { data, count, error } = await query;
      if (error) return res.status(400).json({ error: { message: error.message } });
      return res.status(200).json({ dados: data, total: count, pagina: pg, por_pagina: pp });
    }

    return res.status(400).json({
      error: { message: 'Especifique ?tarefa=elo (com liga_id ou escopo=geral), ?tarefa=calibracao, ?tarefa=odds-descobrir ou ?tarefa=odds&liga_id=X.' },
    });
  } catch (erro) {
    res.status(500).json({ error: { message: erro.message } });
  }
}
