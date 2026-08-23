// src/utils/confederacoes.js
// Mapa estático país (ISO 3166-1 alpha-3, mesmo código usado em
// leagues.territory_code) -> confederação FIFA. Dado de referência fixo
// (filiação FIFA não muda por heurística/matching, diferente dos crosswalks
// de time/liga do resto do projeto) — cobre os países com liga/seleção mais
// relevantes pra futebol de clubes; não é a lista completa dos ~211
// membros da FIFA, só o suficiente pra qualquer país que possa aparecer
// nesta base. Adicionar códigos novos aqui conforme leagues.territory_code
// crescer.

export const CONFEDERACOES = {
  UEFA: 'UEFA (Europa)',
  CONMEBOL: 'CONMEBOL (América do Sul)',
  CONCACAF: 'CONCACAF (América do Norte/Central)',
  AFC: 'AFC (Ásia)',
  CAF: 'CAF (África)',
  OFC: 'OFC (Oceania)',
};

const PAIS_PARA_CONFEDERACAO = {
  // UEFA
  ENG: 'UEFA', ESP: 'UEFA', FRA: 'UEFA', DEU: 'UEFA', ITA: 'UEFA', NED: 'UEFA', POR: 'UEFA',
  SCO: 'UEFA', WAL: 'UEFA', IRL: 'UEFA', NIR: 'UEFA', BEL: 'UEFA', CHE: 'UEFA', AUT: 'UEFA',
  TUR: 'UEFA', RUS: 'UEFA', UKR: 'UEFA', POL: 'UEFA', CZE: 'UEFA', SVK: 'UEFA', HUN: 'UEFA',
  ROU: 'UEFA', BGR: 'UEFA', GRC: 'UEFA', SRB: 'UEFA', HRV: 'UEFA', BIH: 'UEFA', SVN: 'UEFA',
  DNK: 'UEFA', SWE: 'UEFA', NOR: 'UEFA', FIN: 'UEFA', ISL: 'UEFA', ISR: 'UEFA', CYP: 'UEFA',
  LUX: 'UEFA', MLT: 'UEFA', ALB: 'UEFA', MKD: 'UEFA', MNE: 'UEFA', KOS: 'UEFA', GEO: 'UEFA',
  ARM: 'UEFA', AZE: 'UEFA', BLR: 'UEFA', MDA: 'UEFA', LTU: 'UEFA', LVA: 'UEFA', EST: 'UEFA',
  AND: 'UEFA', SMR: 'UEFA', GIB: 'UEFA', FRO: 'UEFA', KAZ: 'UEFA',

  // CONMEBOL
  BRA: 'CONMEBOL', ARG: 'CONMEBOL', URY: 'CONMEBOL', CHL: 'CONMEBOL', COL: 'CONMEBOL',
  PER: 'CONMEBOL', PRY: 'CONMEBOL', ECU: 'CONMEBOL', BOL: 'CONMEBOL', VEN: 'CONMEBOL',

  // CONCACAF
  USA: 'CONCACAF', MEX: 'CONCACAF', CAN: 'CONCACAF', CRC: 'CONCACAF', HND: 'CONCACAF',
  JAM: 'CONCACAF', PAN: 'CONCACAF', GTM: 'CONCACAF', SLV: 'CONCACAF', TRI: 'CONCACAF',
  CUB: 'CONCACAF', HTI: 'CONCACAF', NIC: 'CONCACAF', DOM: 'CONCACAF',

  // AFC
  JPN: 'AFC', KOR: 'AFC', CHN: 'AFC', AUS: 'AFC', IRN: 'AFC', SAU: 'AFC', QAT: 'AFC',
  UAE: 'AFC', IRQ: 'AFC', IND: 'AFC', THA: 'AFC', VNM: 'AFC', IDN: 'AFC', UZB: 'AFC',
  JOR: 'AFC', KWT: 'AFC', OMN: 'AFC', BHR: 'AFC', SYR: 'AFC', PRK: 'AFC', MYS: 'AFC',

  // CAF
  EGY: 'CAF', MAR: 'CAF', NGA: 'CAF', SEN: 'CAF', TUN: 'CAF', ALG: 'CAF', DZA: 'CAF',
  ZAF: 'CAF', GHA: 'CAF', CIV: 'CAF', CMR: 'CAF', COD: 'CAF', MLI: 'CAF', BFA: 'CAF',
  ZMB: 'CAF', UGA: 'CAF', KEN: 'CAF', GAB: 'CAF', GIN: 'CAF', BEN: 'CAF', AGO: 'CAF',
  MOZ: 'CAF', TAN: 'CAF', ETH: 'CAF', COG: 'CAF', MTN: 'CAF', TOG: 'CAF', LBY: 'CAF',
  CPV: 'CAF', RWA: 'CAF', NAM: 'CAF', ZWE: 'CAF', GNB: 'CAF', SDN: 'CAF', GMB: 'CAF',

  // OFC
  NZL: 'OFC', FJI: 'OFC', NCL: 'OFC', VAN: 'OFC', SOL: 'OFC', PNG: 'OFC', TAH: 'OFC',
};

export function confederacaoDoPais(paisCodigo) {
  return PAIS_PARA_CONFEDERACAO[paisCodigo] || null;
}

export default PAIS_PARA_CONFEDERACAO;
