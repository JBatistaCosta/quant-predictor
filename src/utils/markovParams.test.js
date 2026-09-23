import { describe, it, expect } from 'vitest';
import { carregarMarkovParams } from './markovParams';

// Mock mínimo do client Supabase -- só o suficiente pra exercitar a cadeia
// `.from().select().eq()/.is().range()` que `carregarMarkovParams` usa.
// Não importa o client real (é código de browser, sem rede em teste).
function fakeSupabase(rows) {
  return {
    from() {
      return {
        select() {
          const builder = {
            _filtros: [],
            eq(coluna, valor) { builder._filtros.push((r) => r[coluna] === valor); return builder; },
            is(coluna, valor) { builder._filtros.push((r) => r[coluna] === valor); return builder; },
            async range(inicio, fim) {
              const filtradas = rows.filter((r) => builder._filtros.every((f) => f(r)));
              return { data: filtradas.slice(inicio, fim + 1), error: null };
            },
          };
          return builder;
        },
      };
    },
  };
}

const linhas = [
  { league_id: 5, evento: 'gol', tipo: 'mult_estado_placar', chave: 'ganhando', valor: 1.4 },
  { league_id: null, evento: 'gol', tipo: 'mult_estado_placar', chave: 'ganhando', valor: 1.2 },
  { league_id: null, evento: 'gol', tipo: 'mult_estado_placar', chave: 'perdendo', valor: 0.9 },
];

describe('carregarMarkovParams', () => {
  it('supabase null devolve objeto vazio (motor cai pra multiplicadores neutros)', async () => {
    const resultado = await carregarMarkovParams(null, 5);
    expect(resultado).toEqual({});
  });

  it('prioriza o valor da liga sobre o fallback global quando os dois existem', async () => {
    const supabase = fakeSupabase(linhas);
    const resultado = await carregarMarkovParams(supabase, 5);
    expect(resultado.gol.mult_estado_placar.ganhando).toBe(1.4); // da liga, não do fallback (1.2)
  });

  it('usa o fallback global pra chave que a liga não tem calibrada', async () => {
    const supabase = fakeSupabase(linhas);
    const resultado = await carregarMarkovParams(supabase, 5);
    expect(resultado.gol.mult_estado_placar.perdendo).toBe(0.9); // só existe no fallback
  });

  it('liga sem nenhuma calibração própria cai inteiramente pro fallback global', async () => {
    const supabase = fakeSupabase(linhas);
    const resultado = await carregarMarkovParams(supabase, 999); // liga sem linhas próprias
    expect(resultado.gol.mult_estado_placar.ganhando).toBe(1.2);
    expect(resultado.gol.mult_estado_placar.perdendo).toBe(0.9);
  });

  it('leagueId null usa só o fallback global', async () => {
    const supabase = fakeSupabase(linhas);
    const resultado = await carregarMarkovParams(supabase, null);
    expect(resultado.gol.mult_estado_placar.ganhando).toBe(1.2);
  });
});
