// api/_lib/nomesTimes.js
// Casamento de nome de time (fonte externa -> teams) com regra RÍGIDA e
// vários nomes por time para redundância.
//
// POR QUE EXISTE: a regra antiga (nomesBatemTime em model-maintenance.js)
// aceitava "um nome contido no outro" (conjunto de palavras sub/superconjunto).
// Isso fez "Ireland" cair em "Northern Ireland", "New England" cair em
// "England" (138 jogos da MLS dentro da seleção inglesa) e "Costa Rica"
// (seleção) cair num clube brasileiro de nome igual (ver Achado 42/43 em
// ACHADOS_COMPORTAMENTO.md). Erro de vínculo corrompe todo sync futuro.
//
// REGRA NOVA:
//  1. Igualdade exata (depois de normalizar) com QUALQUER nome do time:
//     name, display_name, name_pt, name_en, name_native, nicknames[], aliases[].
//  2. Só para clubes (não estrito): "contido no outro" é aceito apenas se as
//     palavras que sobram forem afixos genéricos de clube (FC, CF, Club, de...).
//     "Northern", "New", "United" etc. NÃO são afixos -> não casa.
//  3. Em torneio de seleções (estrito) só vale a regra 1, e só entre seleções
//     (is_national_team); em clube, seleções ficam de fora.
//  4. Mais de um candidato depois do filtro de país -> ambíguo -> nenhum
//     (quem chama cria time novo e avisa, em vez de escolher no chute).

// Normaliza preservando espaços (palavras) e alfabetos não latinos
// (árabe, cirílico, japonês...): o normalizador antigo apagava tudo que não
// fosse a-z0-9, então um nome nativo em outro alfabeto virava string vazia.
export function normalizarNome(s) {
  return (s || '')
    .toLowerCase()
    .normalize('NFD').replace(/\p{M}+/gu, '')
    .replace(/[^\p{L}\p{N}]+/gu, ' ')
    .trim();
}

// Palavras que, sozinhas, não distinguem um clube de outro.
export const AFIXOS_GENERICOS_CLUBE = new Set([
  'fc', 'cf', 'sc', 'ac', 'as', 'afc', 'fk', 'sk', 'cd', 'ca', 'cs', 'cr',
  'ec', 'se', 'sd', 'ud', 'club', 'clube', 'de', 'da', 'do', 'del', 'la',
  'el', 'the', 'futebol', 'football', 'esporte', 'esportes',
]);

// Todos os nomes pelos quais um time pode ser reconhecido, já normalizados
// e sem repetição. Campos ausentes (colunas novas ainda nulas) são ignorados.
export function nomesDoTime(t) {
  const brutos = [
    t.name, t.display_name, t.name_pt, t.name_en, t.name_native,
    ...(t.nicknames || []), ...(t.aliases || []),
  ];
  return [...new Set(brutos.map(normalizarNome).filter(Boolean))];
}

export function nomesIguais(a, b) {
  const na = normalizarNome(a), nb = normalizarNome(b);
  return !!na && na === nb;
}

// "Manchester United" ~ "Manchester United FC": todas as palavras de um estão
// no outro E as que sobram são afixos genéricos.
export function nomesBatemComAfixos(a, b) {
  const na = normalizarNome(a), nb = normalizarNome(b);
  if (!na || !nb) return false;
  if (na === nb) return true;
  const ta = na.split(' '), tb = nb.split(' ');
  const [menor, maior] = ta.length <= tb.length ? [ta, tb] : [tb, ta];
  const setMaior = new Set(maior);
  if (!menor.every(p => setMaior.has(p))) return false;
  const setMenor = new Set(menor);
  return maior.filter(p => !setMenor.has(p)).every(p => AFIXOS_GENERICOS_CLUBE.has(p));
}

function normalizarPais(s) {
  return normalizarNome(s);
}

// Devolve { time, motivo }: time = candidato único ou null.
// motivo (quando time=null): 'nenhum' | 'pais_diverge' | 'ambiguo'.
export function escolherTimePorNome(todosOsTimes, nomeFonte, { paisEsperado = null, estrito = false } = {}) {
  const alvo = normalizarNome(nomeFonte);
  if (!alvo) return { time: null, motivo: 'nenhum' };

  // seleção só casa com seleção; clube nunca casa com seleção. Valor ausente
  // (null) não bloqueia, para não duplicar time antigo ainda sem a flag.
  const compativelTipo = (t) => (estrito ? t.is_national_team !== false : t.is_national_team !== true);
  const pool = todosOsTimes.filter(compativelTipo);

  let candidatos = pool.filter(t => nomesDoTime(t).includes(alvo));
  if (candidatos.length === 0 && !estrito) {
    candidatos = pool.filter(t => nomesDoTime(t).some(n => nomesBatemComAfixos(n, nomeFonte)));
  }
  if (candidatos.length === 0) return { time: null, motivo: 'nenhum' };

  const paisNorm = paisEsperado ? normalizarPais(paisEsperado) : null;
  const semConflito = candidatos.filter(t => {
    if (!paisNorm || !t.country) return true;
    return normalizarPais(t.country) === paisNorm;
  });
  if (semConflito.length === 1) return { time: semConflito[0], motivo: null };
  if (semConflito.length === 0) return { time: null, motivo: 'pais_diverge', candidatos };
  return { time: null, motivo: 'ambiguo', candidatos: semConflito };
}
