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

// Linhas de chutes/chutes no gol (TOTAL da partida) -- mesma constante de
// `LINHAS_POR_STAT` em `arquivos_do_claude/modelo_stats_esperadas.py` e
// `LINHAS_PADRAO_POR_STAT` em `api/corners-model.js` (duplicada de
// propósito, mesmo padrão do resto do projeto).
export const LINHAS_SHOTS = { shots: [20.5, 22.5, 24.5, 26.5], shots_on_target: [7.5, 8.5, 9.5, 10.5] };

// `extras`: `{ corners, shots, shots_on_target }`, cada um um mapa
// `{ match_id: total_da_partida }` já somado (mandante+visitante) e
// validado (só entra se os dois times tiverem registro -- ver os call-
// sites em api/model-stats.js/api/backtest-betting.js, `cont[id] === 2`).
// Precisam de JOIN novo (não vêm em `matches`, diferente de gols por time)
// -- por isso entram como parâmetro à parte, igual `corners` já fazia.
export function calcularResultadosReais(matches, extras = {}) {
  const { corners = {}, shots = {}, shots_on_target: shotsOnTarget = {} } = extras;
  const porMatch = {};
  for (const m of matches) {
    if (m.status !== 'finished' || m.home_goals == null || m.away_goals == null) continue;
    const total = m.home_goals + m.away_goals;
    const resultado = {
      league_id: m.league_id,
      '1X2': m.home_goals > m.away_goals ? 'home' : m.home_goals < m.away_goals ? 'away' : 'draw',
      'over_under_2.5': total > 2.5 ? 'over' : 'under',
      btts: (m.home_goals > 0 && m.away_goals > 0) ? 'yes' : 'no',
    };
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
    porMatch[m.id] = resultado;
  }
  for (const [matchId, totalCorners] of Object.entries(corners)) {
    if (porMatch[matchId]) porMatch[matchId]['corners_over_under_9.5'] = totalCorners > 9.5 ? 'over' : 'under';
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
  return porMatch;
}
