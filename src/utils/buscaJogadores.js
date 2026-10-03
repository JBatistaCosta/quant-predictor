// src/utils/buscaJogadores.js
// Funções puras da busca de jogadores (/jogadores): limpeza do texto digitado e ordenação do resultado da busca
// por apelido/semelhança (RPC `buscar_jogadores`, migration 20261003120000).

export const TERMO_MINIMO = 2;
export const LIMITE_BUSCA = 100;

// "  Gabriel   Barbosa " -> "Gabriel Barbosa". Um espaço sobrando no fim (comum quando o teclado do celular completa
// a palavra) fazia a busca antiga procurar "Gabriel Barbosa " e perder o próprio Gabriel Barbosa.
export function normalizarBusca(texto) {
  return String(texto ?? '').replace(/\s+/g, ' ').trim();
}

// Ordena `jogadores` (linhas da view) pelo critério escolhido. `relevancia` é um Map(id -> score) vindo da busca.
// `coluna`/`asc` são os da ORDENACOES da tela; nulos vão sempre para o fim, como no `nullsFirst: false` do banco.
export function ordenarJogadores(jogadores, { coluna, asc, relevancia }) {
  const copia = [...jogadores];
  const comparar = (a, b) => {
    if (relevancia) {
      const d = (relevancia.get(b.id) ?? 0) - (relevancia.get(a.id) ?? 0);
      if (d !== 0) return d;
    }
    if (coluna) {
      const va = a[coluna], vb = b[coluna];
      if (va == null && vb == null) return a.id - b.id;
      if (va == null) return 1;
      if (vb == null) return -1;
      if (va !== vb) {
        const base = typeof va === 'string' ? va.localeCompare(vb, 'pt-BR') : va - vb;
        return asc ? base : -base;
      }
    }
    return a.id - b.id;
  };
  return copia.sort(comparar);
}
