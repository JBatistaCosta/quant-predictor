// Política de staking por faixa de odd (gestão de risco global do projeto,
// pedida explicitamente pelo usuário) -- substitui a fração de Kelly/teto de
// stake fixos usados antes. Odd mais alta = mais variância de cauda e mais
// incerteza do modelo, então a fração de Kelly cai e o corte mínimo de EV
// sobe conforme a odd sobe. Faixas [min, max) -- a odd exatamente no limite
// superior cai na próxima faixa (só a última faixa é aberta, >= 8.00).
// Duplicado (não importado) em api/_lib/stakingPolicy.js -- mesmo padrão já
// usado em devig.js pra manter api/*.js sem depender de src/ e vice-versa.
export const FAIXAS_STAKING = [
  { oddMin: 1.30, oddMax: 2.50, fracaoKelly: 0.25, evMinimo: 0.025, tetoStakeBanca: 0.02, regime: 'Regime padrão de liquidez' },
  { oddMin: 2.50, oddMax: 4.00, fracaoKelly: 0.20, evMinimo: 0.04, tetoStakeBanca: 0.01, regime: 'Variância moderada' },
  { oddMin: 4.00, oddMax: 8.00, fracaoKelly: 0.125, evMinimo: 0.07, tetoStakeBanca: 0.005, regime: 'Margem de segurança para ruído de cauda' },
  // fracaoKelly null == stake fixa plana (sempre o teto, sem cálculo de Kelly nessa faixa)
  { oddMin: 8.00, oddMax: Infinity, fracaoKelly: null, evMinimo: 0.10, tetoStakeBanca: 0.0025, regime: 'Proteção contra drawdowns de 50+ apostas' },
];

// Odds abaixo de 1.30 ficam fora da política (não cobertas pela tabela) --
// tratadas como "não apostar", nunca como a faixa mais conservadora.
export function encontrarFaixaStaking(odd) {
  if (!odd || odd < FAIXAS_STAKING[0].oddMin) return null;
  return FAIXAS_STAKING.find(faixa => odd >= faixa.oddMin && odd < faixa.oddMax) || null;
}

// Decide se apostar e, se sim, com que fração da banca -- já aplica o corte
// mínimo de EV e o teto de stake da faixa correspondente à odd.
export function calcularStakeKellyPorFaixa(probabilidade, odd) {
  const faixa = encontrarFaixaStaking(odd);
  if (!faixa) return { apostar: false, motivo: 'odd_fora_da_politica', faixa: null, ev: null, stakeFracaoBanca: 0 };

  const ev = probabilidade * odd - 1;
  if (ev < faixa.evMinimo) return { apostar: false, motivo: 'ev_abaixo_do_corte', faixa, ev, stakeFracaoBanca: 0 };

  if (faixa.fracaoKelly == null) {
    return { apostar: true, motivo: null, faixa, ev, stakeFracaoBanca: faixa.tetoStakeBanca };
  }

  const b = odd - 1;
  const kellyCompleto = b > 0 ? (probabilidade * (b + 1) - 1) / b : 0;
  if (kellyCompleto <= 0) return { apostar: false, motivo: 'kelly_completo_negativo', faixa, ev, stakeFracaoBanca: 0 };

  const stakeFracaoBanca = Math.min(kellyCompleto * faixa.fracaoKelly, faixa.tetoStakeBanca);
  return { apostar: true, motivo: null, faixa, ev, kellyCompleto, stakeFracaoBanca };
}
