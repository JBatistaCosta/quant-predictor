// api/_lib/curvaPnlEv.js
// Curva acumulada de Lucro Real (PnL) x Valor Esperado (EV) pra uma
// sequência cronológica de apostas simuladas -- usado por
// api/backtest-betting.js pra alimentar o gráfico "Curva de Retorno x EV"
// em ModelosStats.jsx (pedido do usuário). Função pura, sem dependência de
// Supabase/rede, fácil de testar isolada.
//
// Cada aposta de entrada já vem de api/backtest-betting.js com `odd`/
// `p_aposta` sendo a MESMA odd de fechamento usada tanto pra decidir a
// aposta quanto pra pagar o resultado real (comentário no topo daquele
// arquivo: "aposta na odd REAL de fechamento") -- diferente de um sistema
// com odds_taken/odds_closing distintas (comprar antes, comparar com o
// fechamento depois pra medir CLV), este projeto ainda não separa as duas.
// O EV aqui responde "quanto o modelo esperava ganhar, em média, apostando
// sempre na odd de fechamento" -- útil pra comparar contra o lucro real
// (se divergem muito e de forma consistente, é sinal de miscalibração).
export function calcularCurvaPnlEv(apostas) {
  let pnlAcumulado = 0;
  let evAcumulado = 0;
  let picoPnl = 0;

  return apostas.map((aposta) => {
    const odd = Number(aposta.odd);
    const stake = Number(aposta.stake);
    const pModelo = Number(aposta.p_aposta);
    // odd/stake inválidos (null, 0, negativo) não geram EV nem PnL pra essa
    // aposta -- entrada defensiva, embora api/backtest-betting.js já filtre
    // isso antes de chegar aqui (nunca cria uma candidata sem odd/stake
    // válidos), evita NaN silencioso se essa função for chamada de outro
    // lugar no futuro com dado menos garantido.
    const oddValida = Number.isFinite(odd) && odd > 1;
    const stakeValido = Number.isFinite(stake) && stake > 0;
    const evAposta = oddValida && stakeValido && Number.isFinite(pModelo) ? (pModelo * odd - 1) * stake : 0;

    pnlAcumulado += Number.isFinite(aposta.lucro) ? Number(aposta.lucro) : 0;
    evAcumulado += evAposta;
    picoPnl = Math.max(picoPnl, pnlAcumulado);

    return {
      match_id: aposta.match_id,
      date: aposta.match_date,
      league_id: aposta.league_id,
      selection: aposta.selection,
      lucro: arredondar(aposta.lucro),
      pnl_acumulado: arredondar(pnlAcumulado),
      ev_aposta: arredondar(evAposta),
      ev_acumulado: arredondar(evAcumulado),
      drawdown: arredondar(picoPnl - pnlAcumulado),
    };
  });
}

function arredondar(v, casas = 4) {
  return Number.isFinite(v) ? Number(Number(v).toFixed(casas)) : 0;
}
