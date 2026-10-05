import { LIGA_MEDIA_GERAL } from './poisson';

// Conversão diferença de Elo -> fator multiplicativo do λ (gols esperados) de cada lado.
//
// Antes: λ × E/0,5 e λ × (1-E)/0,5, com E = 1/(1+10^(-peso·dif/400)). Isso satura (fator 2 e 0 nos
// extremos, o λ cai no piso) e, com peso 100%, deixa o 1X2 confiante demais (Achado 62 em
// ACHADOS_COMPORTAMENTO.md: confiança 0,600 contra acerto 0,511).
// Agora: fator log-linear, λ_casa × exp(+k·x/2) e λ_fora × exp(-k·x/2), com x = dif/400·ln(10).
//
// ELO_K e FORMA_EXPOENTE vêm de um ajuste da calculadora INTEIRA (forma recente em xG e xGA com decaimento,
// mando, Elo e Poisson com Dixon-Coles), por máxima verossimilhança do 1X2 nas temporadas 2022 e 2023 das
// cinco grandes ligas (3.266 jogos) e avaliado em 2024 e 2025 (3.434 jogos), com o mando (gamma) fixo no
// valor do app (scripts/calibrar_calculadora_completa.py, Achado 71). O Achado 69 tinha ajustado o fator
// de Elo sozinho (k 0,57) e isso deixava o favorito da casa inflado: o xG e o xGA da forma já carregam a
// força dos times, e o Elo soma a mesma força de novo.
// No ponto de partida da tela (peso do Elo 50%) o k aplicado é exatamente ELO_K; 0% desliga o Elo e 100% dobra o k.
export const ELO_K = 0.4907;
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

// Força da forma recente: a combinação multiplicativa xG x xGA / média da liga, medida em poucos jogos, é
// ruidosa e extrema demais. O ajuste da calculadora inteira (Achado 71) pede elevá-la a ~0,26 (encolher
// a razão em direção à média da liga). Vale para as fórmulas 'multiplicativo' e 'time decay', que usam as
// médias cruas; as demais (shrinkage já encolhe, ataque × defesa, normalização dinâmica, ML) não mudam.
export const FORMA_EXPOENTE = 0.258;

export function encolherForca(trueXG, gamma, expoente = FORMA_EXPOENTE) {
  const base = LIGA_MEDIA_GERAL * gamma;      // λ de um confronto médio, com o mando do lado
  if (!(trueXG > 0) || !(base > 0)) return trueXG;
  return base * Math.pow(trueXG / base, expoente);
}
