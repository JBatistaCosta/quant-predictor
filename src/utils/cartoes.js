// src/utils/cartoes.js
// Cartões reais por partida — mesma fonte primária/fallback já documentada
// em scripts/dados_historicos.py (`_carregar_cartoes_por_time_por_partida`):
// `match_events` (evento a evento) é confiável quando existe; `match_stats_
// fotmob.yellow_cards`/`red_cards` tem bug real do lado da API do FotMob
// (bloco `discipline` zerado mesmo com cartão de verdade em boa parte de
// 2025-2026, ver CONTEXTO_PROJETO.md) e só deve servir de fallback pras
// partidas sem NENHUM evento capturado. `match_events` só guarda cartão
// (nunca gol/substituição, ver CLAUDE.md), então "a partida tem qualquer
// linha" já garante cobertura completa dela — não precisa checar tipo.
import { supabase } from '../supabaseClient';

const TIPOS_EVENTO_CARTAO = ['yellow_card', 'second_yellow_card', 'red_card'];

// Retorna { porPartida, temEvento }: `porPartida` é um Map
// "matchId:teamId" -> { yellow_cards, red_cards } só das partidas com
// cobertura em match_events; `temEvento` é o Set de matchIds cobertos, pra
// quem chama decidir quando cai no fallback de match_stats_fotmob.
export async function buscarCartoesReaisPorPartida(matchIds) {
  const porPartida = new Map();
  const temEvento = new Set();
  if (!matchIds || matchIds.length === 0) return { porPartida, temEvento };

  const { data: eventos } = await supabase
    .from('match_events')
    .select('match_id, team_id, event_type')
    .in('match_id', matchIds)
    .in('event_type', TIPOS_EVENTO_CARTAO);

  (eventos || []).forEach((e) => {
    temEvento.add(e.match_id);
    const chave = `${e.match_id}:${e.team_id}`;
    if (!porPartida.has(chave)) porPartida.set(chave, { yellow_cards: 0, red_cards: 0 });
    const linha = porPartida.get(chave);
    // second_yellow_card conta como vermelho (é a expulsão em si) -- mesmo
    // critério de TIPOS_EVENTO_CARTAO em dados_historicos.py pro total.
    if (e.event_type === 'yellow_card') linha.yellow_cards += 1;
    else linha.red_cards += 1;
  });

  return { porPartida, temEvento };
}

// Sobrescreve, EM PLACE, as entradas de `statsPorJogo` (map matchId ->
// objeto de stats, já filtrado pro time de interesse) com os valores de
// match_events nas partidas em que ele tem cobertura -- usado pelos
// chamadores que buscam match_stats_fotmob por lote e depois indexam por
// match_id.
export function aplicarCartoesReais(statsPorJogo, teamId, { porPartida, temEvento }) {
  temEvento.forEach((matchId) => {
    const corrigido = porPartida.get(`${matchId}:${teamId}`) || { yellow_cards: 0, red_cards: 0 };
    statsPorJogo[matchId] = { ...(statsPorJogo[matchId] || {}), ...corrigido };
  });
}
