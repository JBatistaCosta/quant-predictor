// Conversão diferença de Elo -> fator multiplicativo do λ (gols esperados) de cada lado.
//
// Antes: λ × E/0,5 e λ × (1-E)/0,5, com E = 1/(1+10^(-peso·dif/400)). Isso satura (fator 2 e 0 nos
// extremos, o λ cai no piso) e, com peso 100%, deixa o 1X2 confiante demais (Achado 62 em
// ACHADOS_COMPORTAMENTO.md: confiança 0,600 contra acerto 0,511).
// Agora: fator log-linear, λ_casa × exp(+k·x/2) e λ_fora × exp(-k·x/2), com x = dif/400·ln(10).
//
// ELO_K = 0,57 vem de uma regressão de Poisson dos gols de cada time-jogo sobre x, ajustada nas
// temporadas 2022 e 2023 das cinco grandes ligas (7.082 jogos no total; 3.578 no ajuste) e avaliada em
// 2024 e 2025 (scripts/calibrar_elo_lambda_calculadora.py). No ponto de partida da tela
// (peso do Elo 50%) o k aplicado é exatamente ELO_K; 0% desliga o Elo e 100% dobra o k.
export const ELO_K = 0.5737;
export const ELO_PESO_CALIBRADO = 50;     // valor do controle "Peso do Histórico (Elo)" em que se aplica ELO_K
export const ELO_FATOR_MAXIMO = 3;        // trava o fator em [1/3, 3] (diferenças de Elo extremas, ex.: seleções muito desiguais)

export function fatoresElo(difElo, pesoPct = ELO_PESO_CALIBRADO, k = ELO_K) {
  const dif = Number.isFinite(difElo) ? difElo : 0;
  const kEfetivo = k * (pesoPct / ELO_PESO_CALIBRADO);
  const x = (dif / 400) * Math.LN10;
  const lim = Math.log(ELO_FATOR_MAXIMO);
  const expoente = Math.max(-lim, Math.min(lim, (kEfetivo * x) / 2));
  return { fator1: Math.exp(expoente), fator2: Math.exp(-expoente) };
}
