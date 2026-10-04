import { describe, it, expect } from 'vitest';
import {
  normalizarNome, nomesDoTime, nomesBatemComAfixos, escolherTimePorNome,
} from './nomesTimes.js';

const sel = (id, name, extra = {}) => ({ id, name, is_national_team: true, country: null, aliases: [], ...extra });
const clube = (id, name, extra = {}) => ({ id, name, is_national_team: false, country: null, aliases: [], ...extra });

describe('normalizarNome', () => {
  it('tira acento e pontuação, mantém palavras', () => {
    expect(normalizarNome('  Méxi-co!  ')).toBe('méxi co'.normalize('NFD').replace(/\p{M}+/gu, ''));
    expect(normalizarNome('Atlético-MG')).toBe('atletico mg');
  });
  it('preserva alfabetos não latinos (o normalizador antigo devolvia vazio)', () => {
    expect(normalizarNome('Россия')).toBe('россия');
    expect(normalizarNome('日本')).toBe('日本');
    expect(normalizarNome('المغرب')).not.toBe('');
  });
});

describe('nomesBatemComAfixos', () => {
  it('aceita só afixos genéricos de clube', () => {
    expect(nomesBatemComAfixos('Manchester United', 'Manchester United FC')).toBe(true);
    expect(nomesBatemComAfixos('Club Atlético Peñarol', 'Atletico Penarol')).toBe(true);
  });
  it('recusa palavra que distingue de verdade', () => {
    expect(nomesBatemComAfixos('Ireland', 'Northern Ireland')).toBe(false);
    expect(nomesBatemComAfixos('England', 'New England')).toBe(false);
    expect(nomesBatemComAfixos('Manchester City', 'Manchester United')).toBe(false);
  });
});

describe('escolherTimePorNome — casos reais que corromperam o banco', () => {
  it('Ireland NÃO casa com Northern Ireland (estrito, seleções)', () => {
    const times = [sel(1038, 'Northern Ireland')];
    const r = escolherTimePorNome(times, 'Ireland', { estrito: true });
    expect(r.time).toBeNull();
  });
  it('New England NÃO casa com a seleção England (clube)', () => {
    const times = [sel(466, 'England')];
    expect(escolherTimePorNome(times, 'New England').time).toBeNull();
    expect(escolherTimePorNome(times, 'New England', { estrito: true }).time).toBeNull();
  });
  it('seleção Costa Rica NÃO casa com clube homônimo (is_national_team=false)', () => {
    const times = [clube(744, 'Costa Rica ', { country: 'Brazil' })];
    expect(escolherTimePorNome(times, 'Costa Rica', { estrito: true }).time).toBeNull();
  });
  it('clube NÃO casa com seleção de nome igual', () => {
    const times = [sel(475, 'Portugal')];
    expect(escolherTimePorNome(times, 'Portugal').time).toBeNull();
  });
  it('Athletic Club (BR) não vira Athletic Club de Bilbao: país diverge', () => {
    const times = [clube(1, 'Athletic Club', { country: 'Spain' })];
    const r = escolherTimePorNome(times, 'Athletic Club', { paisEsperado: 'Brazil' });
    expect(r.time).toBeNull();
    expect(r.motivo).toBe('pais_diverge');
  });
});

describe('escolherTimePorNome — redundância de nomes', () => {
  const brasil = sel(666, 'Brazil', {
    display_name: 'Brasil', name_pt: 'Brasil', name_en: 'Brazil', name_native: 'Brasil',
    nicknames: ['Seleção Canarinho', 'Canarinho'],
  });
  const russia = sel(1036, 'Russia', { name_pt: 'Rússia', name_native: 'Россия' });
  const marrocos = sel(667, 'Morocco', { name_native: 'المغرب' });

  it('acha pelo nome em português, apelido e nome na língua original', () => {
    const times = [brasil, russia, marrocos];
    expect(escolherTimePorNome(times, 'Brasil', { estrito: true }).time.id).toBe(666);
    expect(escolherTimePorNome(times, 'Canarinho', { estrito: true }).time.id).toBe(666);
    expect(escolherTimePorNome(times, 'Россия', { estrito: true }).time.id).toBe(1036);
    expect(escolherTimePorNome(times, 'المغرب', { estrito: true }).time.id).toBe(667);
    expect(escolherTimePorNome(times, 'Rússia', { estrito: true }).time.id).toBe(1036);
  });
  it('apelido repetido em dois times é ambíguo -> nenhum (não escolhe no chute)', () => {
    const times = [sel(1, 'Spain', { nicknames: ['La Roja'] }), sel(2, 'Chile', { nicknames: ['La Roja'] })];
    const r = escolherTimePorNome(times, 'La Roja', { estrito: true });
    expect(r.time).toBeNull();
    expect(r.motivo).toBe('ambiguo');
  });
  it('nomesDoTime reúne e deduplica todos os campos', () => {
    expect(nomesDoTime(brasil).sort()).toEqual(['brasil', 'brazil', 'canarinho', 'seleção canarinho'.normalize('NFD').replace(/\p{M}+/gu, '')].sort());
  });
});

describe('escolherTimePorNome — clubes continuam casando', () => {
  it('Manchester United FC (fonte) acha Manchester United (banco)', () => {
    const times = [clube(10, 'Manchester United'), clube(11, 'Manchester City')];
    expect(escolherTimePorNome(times, 'Manchester United FC').time.id).toBe(10);
  });
  it('time com flag nula (dado antigo) ainda casa nos dois modos', () => {
    const times = [{ id: 5, name: 'Chile', is_national_team: null, aliases: [] }];
    expect(escolherTimePorNome(times, 'Chile', { estrito: true }).time.id).toBe(5);
    expect(escolherTimePorNome(times, 'Chile').time.id).toBe(5);
  });
});
