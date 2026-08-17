// src/utils/matchTeamNames.js
// Casa nomes de times entre fontes diferentes (ex.: payload da API-Football x
// nosso banco, que na Série A vem via football-data.org) por aproximação de
// texto — mesma lógica de scripts/ingestao_odds_oddspapi_brasileirao.py,
// portada pro front porque aqui a comparação é só pra EXIBIÇÃO (nunca grava
// mapeamento em tabela nenhuma, então não precisa da confirmação manual que
// team_source_ids exige pra crosswalks persistidos).

const TOKENS_IGNORADOS = new Set([
  'fc', 'cf', 'afc', 'fbpa', 'fbc', 'sc', 'ac', 'ssc', 'as', 'rc', 'cd',
  'ud', 'rcd', 'ec', 'ca', 'cr', 'se', 'club', 'clube', 'sa', 'sp', 'rj', 'mg', 'rs',
]);

// Grafias divergentes que a normalização por token não resolve sozinha
// (ex.: "Athletico Paranaense", grafia oficial do clube, vira "atleticoparanaense"
// na maioria das fontes, inclusive na API-Football).
const ALIASES = {
  athleticoparanaense: 'atleticoparanaense',
};

export function normalizarNomeTime(nome) {
  if (!nome) return '';
  let s = nome.toLowerCase().replace(/[-.']/g, ' ');
  s = s.normalize('NFD').replace(/[\u0300-\u036f]/g, '');
  const tokens = s.split(/\s+/).filter((t) => t && !TOKENS_IGNORADOS.has(t));
  const chave = tokens.length ? tokens.join('') : s.replace(/\s+/g, '');
  return ALIASES[chave] || chave;
}

export function nomesTimeBatem(a, b) {
  const na = normalizarNomeTime(a);
  const nb = normalizarNomeTime(b);
  if (!na || !nb) return false;
  return na.includes(nb) || nb.includes(na);
}
