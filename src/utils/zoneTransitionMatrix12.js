// src/utils/zoneTransitionMatrix12.js
// ARQUIVO GERADO por scripts/gerar_matriz_transicao_statsbomb.py -- NÃO editar à mão (rode o script de novo).
//
// Matriz de transição de bola entre as 12 zonas de `public.zona_campo12` (4 faixas de profundidade x 3 corredores),
// refeita do Achado 15 (StatsBomb Open Data, La Liga 2015/16, 380 partidas). CONSTANTE UNIVERSAL EXTERNA, não calibrada
// por liga/confronto, sem IC 95% -- mesmas ressalvas de zoneTransitionMatrix.js (3x3). Módulo à parte: não alimenta
// nenhuma simulação em produção. Índice do array = zona do banco - 1 (zona 1 = defesa/lado_y_baixo ... 12 = grande área/lado_y_alto).
// O corredor 1 do banco é `lado_y_baixo` (no StatsBomb = lado esquerdo de quem ataca); a correspondência com o FotMob NÃO foi verificada.
// Base: 667415 ações (continua=552934, chute=9168, perda=105313).

export const ZONAS_12 = [
  { zona: 1, faixa: 'defesa', corredor: 'lado_y_baixo' },
  { zona: 2, faixa: 'defesa', corredor: 'centro' },
  { zona: 3, faixa: 'defesa', corredor: 'lado_y_alto' },
  { zona: 4, faixa: 'meio', corredor: 'lado_y_baixo' },
  { zona: 5, faixa: 'meio', corredor: 'centro' },
  { zona: 6, faixa: 'meio', corredor: 'lado_y_alto' },
  { zona: 7, faixa: 'ataque_fora_da_area', corredor: 'lado_y_baixo' },
  { zona: 8, faixa: 'ataque_fora_da_area', corredor: 'centro' },
  { zona: 9, faixa: 'ataque_fora_da_area', corredor: 'lado_y_alto' },
  { zona: 10, faixa: 'grande_area_adversaria', corredor: 'lado_y_baixo' },
  { zona: 11, faixa: 'grande_area_adversaria', corredor: 'centro' },
  { zona: 12, faixa: 'grande_area_adversaria', corredor: 'lado_y_alto' },
];

// Fração de chute/perda/continua entre as ações que COMEÇAM em cada zona.
export const TAXA_DESFECHO_12 = [
  { chute: 0.0000, perda: 0.1569, continua: 0.8431 }, // defesa/lado_y_baixo
  { chute: 0.0000, perda: 0.1800, continua: 0.8200 }, // defesa/centro
  { chute: 0.0000, perda: 0.1694, continua: 0.8306 }, // defesa/lado_y_alto
  { chute: 0.0001, perda: 0.1276, continua: 0.8723 }, // meio/lado_y_baixo
  { chute: 0.0004, perda: 0.1090, continua: 0.8906 }, // meio/centro
  { chute: 0.0001, perda: 0.1378, continua: 0.8620 }, // meio/lado_y_alto
  { chute: 0.0073, perda: 0.2026, continua: 0.7901 }, // ataque_fora_da_area/lado_y_baixo
  { chute: 0.0938, perda: 0.1786, continua: 0.7277 }, // ataque_fora_da_area/centro
  { chute: 0.0060, perda: 0.2082, continua: 0.7858 }, // ataque_fora_da_area/lado_y_alto
  { chute: 0.0848, perda: 0.3351, continua: 0.5801 }, // grande_area_adversaria/lado_y_baixo
  { chute: 0.4525, perda: 0.1928, continua: 0.3547 }, // grande_area_adversaria/centro
  { chute: 0.0836, perda: 0.3446, continua: 0.5718 }, // grande_area_adversaria/lado_y_alto
];

// P(próxima zona | a ação continua). Cada linha soma 1 (±0,0005 de arredondamento).
export const MATRIZ_TRANSICAO_12 = [
  [0.5833, 0.1430, 0.0174, 0.2012, 0.0379, 0.0089, 0.0054, 0.0017, 0.0010, 0.0002, 0.0000, 0.0000], // de defesa/lado_y_baixo
  [0.1158, 0.4973, 0.1133, 0.0775, 0.1067, 0.0762, 0.0048, 0.0037, 0.0045, 0.0000, 0.0001, 0.0001], // de defesa/centro
  [0.0164, 0.1372, 0.5818, 0.0088, 0.0393, 0.2060, 0.0012, 0.0022, 0.0067, 0.0000, 0.0001, 0.0002], // de defesa/lado_y_alto
  [0.0387, 0.0178, 0.0017, 0.7020, 0.1030, 0.0141, 0.1003, 0.0115, 0.0063, 0.0025, 0.0015, 0.0006], // de meio/lado_y_baixo
  [0.0078, 0.0319, 0.0079, 0.1430, 0.5682, 0.1380, 0.0300, 0.0388, 0.0300, 0.0013, 0.0018, 0.0014], // de meio/centro
  [0.0018, 0.0178, 0.0362, 0.0137, 0.1092, 0.6998, 0.0052, 0.0121, 0.0997, 0.0005, 0.0015, 0.0025], // de meio/lado_y_alto
  [0.0001, 0.0001, 0.0000, 0.0840, 0.0147, 0.0012, 0.7440, 0.0557, 0.0074, 0.0523, 0.0365, 0.0041], // de ataque_fora_da_area/lado_y_baixo
  [0.0000, 0.0002, 0.0000, 0.0143, 0.0605, 0.0142, 0.1059, 0.5828, 0.1167, 0.0224, 0.0596, 0.0235], // de ataque_fora_da_area/centro
  [0.0000, 0.0001, 0.0001, 0.0016, 0.0135, 0.0772, 0.0086, 0.0492, 0.7567, 0.0039, 0.0394, 0.0497], // de ataque_fora_da_area/lado_y_alto
  [0.0000, 0.0000, 0.0000, 0.0003, 0.0003, 0.0000, 0.1957, 0.0440, 0.0091, 0.5194, 0.2218, 0.0094], // de grande_area_adversaria/lado_y_baixo
  [0.0000, 0.0003, 0.0003, 0.0003, 0.0003, 0.0008, 0.0183, 0.0767, 0.0198, 0.0623, 0.7689, 0.0522], // de grande_area_adversaria/centro
  [0.0000, 0.0000, 0.0000, 0.0000, 0.0000, 0.0003, 0.0085, 0.0343, 0.1999, 0.0106, 0.2090, 0.5375], // de grande_area_adversaria/lado_y_alto
];

// Ações observadas que começam em cada zona: quanto menor, menos confiável a linha correspondente.
export const ACOES_POR_ZONA_12 = [47949, 66345, 45068, 122588, 96942, 118101, 60040, 25903, 62222, 5682, 10809, 5766];
