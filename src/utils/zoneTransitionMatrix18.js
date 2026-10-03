// src/utils/zoneTransitionMatrix18.js
// ARQUIVO GERADO por scripts/gerar_matriz_transicao_statsbomb.py -- NÃO editar à mão (rode o script de novo).
//
// Matriz de transição de bola entre as 18 zonas de `public.zona_campo18`, refeita do Achado 15 (StatsBomb Open Data,
// La Liga 2015/16, 380 partidas). CONSTANTE UNIVERSAL EXTERNA, não calibrada por liga/confronto, sem IC 95% -- mesmas
// ressalvas de zoneTransitionMatrix.js (3x3). Módulo à parte: não alimenta nenhuma simulação em produção.
// Índice do array = zona do banco - 1 (zona 1 = defesa/lado_y_baixo ... 18 = grande_area_adversaria/lado_y_alto).
// O corredor 1 do banco é `lado_y_baixo` (no StatsBomb = lado esquerdo de quem ataca); a correspondência com o FotMob NÃO foi verificada.
// Base: 667415 ações (continua=552934, chute=9168, perda=105313).

export const ZONAS_18 = [
  { zona: 1, faixa: 'defesa', corredor: 'lado_y_baixo' },
  { zona: 2, faixa: 'defesa', corredor: 'centro' },
  { zona: 3, faixa: 'defesa', corredor: 'lado_y_alto' },
  { zona: 4, faixa: 'meio_baixo', corredor: 'lado_y_baixo' },
  { zona: 5, faixa: 'meio_baixo', corredor: 'centro' },
  { zona: 6, faixa: 'meio_baixo', corredor: 'lado_y_alto' },
  { zona: 7, faixa: 'meio_alto', corredor: 'lado_y_baixo' },
  { zona: 8, faixa: 'meio_alto', corredor: 'centro' },
  { zona: 9, faixa: 'meio_alto', corredor: 'lado_y_alto' },
  { zona: 10, faixa: 'ataque_fora_da_area_baixo', corredor: 'lado_y_baixo' },
  { zona: 11, faixa: 'ataque_fora_da_area_baixo', corredor: 'centro' },
  { zona: 12, faixa: 'ataque_fora_da_area_baixo', corredor: 'lado_y_alto' },
  { zona: 13, faixa: 'ataque_fora_da_area_alto', corredor: 'lado_y_baixo' },
  { zona: 14, faixa: 'ataque_fora_da_area_alto', corredor: 'centro' },
  { zona: 15, faixa: 'ataque_fora_da_area_alto', corredor: 'lado_y_alto' },
  { zona: 16, faixa: 'grande_area_adversaria', corredor: 'lado_y_baixo' },
  { zona: 17, faixa: 'grande_area_adversaria', corredor: 'centro' },
  { zona: 18, faixa: 'grande_area_adversaria', corredor: 'lado_y_alto' },
];

// Fração de chute/perda/continua entre as ações que COMEÇAM em cada zona.
export const TAXA_DESFECHO_18 = [
  { chute: 0.0000, perda: 0.1569, continua: 0.8431 }, // defesa/lado_y_baixo
  { chute: 0.0000, perda: 0.1800, continua: 0.8200 }, // defesa/centro
  { chute: 0.0000, perda: 0.1694, continua: 0.8306 }, // defesa/lado_y_alto
  { chute: 0.0001, perda: 0.1290, continua: 0.8709 }, // meio_baixo/lado_y_baixo
  { chute: 0.0001, perda: 0.1018, continua: 0.8981 }, // meio_baixo/centro
  { chute: 0.0000, perda: 0.1379, continua: 0.8620 }, // meio_baixo/lado_y_alto
  { chute: 0.0002, perda: 0.1262, continua: 0.8736 }, // meio_alto/lado_y_baixo
  { chute: 0.0008, perda: 0.1179, continua: 0.8813 }, // meio_alto/centro
  { chute: 0.0002, perda: 0.1378, continua: 0.8620 }, // meio_alto/lado_y_alto
  { chute: 0.0023, perda: 0.1467, continua: 0.8510 }, // ataque_fora_da_area_baixo/lado_y_baixo
  { chute: 0.0318, perda: 0.1671, continua: 0.8011 }, // ataque_fora_da_area_baixo/centro
  { chute: 0.0017, perda: 0.1500, continua: 0.8483 }, // ataque_fora_da_area_baixo/lado_y_alto
  { chute: 0.0109, perda: 0.2432, continua: 0.7459 }, // ataque_fora_da_area_alto/lado_y_baixo
  { chute: 0.1744, perda: 0.1935, continua: 0.6321 }, // ataque_fora_da_area_alto/centro
  { chute: 0.0090, perda: 0.2473, continua: 0.7437 }, // ataque_fora_da_area_alto/lado_y_alto
  { chute: 0.0848, perda: 0.3351, continua: 0.5801 }, // grande_area_adversaria/lado_y_baixo
  { chute: 0.4525, perda: 0.1928, continua: 0.3547 }, // grande_area_adversaria/centro
  { chute: 0.0836, perda: 0.3446, continua: 0.5718 }, // grande_area_adversaria/lado_y_alto
];

// P(próxima zona | a ação continua). Cada linha soma 1 (±0,0005 de arredondamento).
export const MATRIZ_TRANSICAO_18 = [
  [0.5833, 0.1430, 0.0174, 0.1772, 0.0332, 0.0066, 0.0240, 0.0047, 0.0023, 0.0036, 0.0012, 0.0006, 0.0018, 0.0004, 0.0004, 0.0002, 0.0000, 0.0000], // de defesa/lado_y_baixo
  [0.1158, 0.4973, 0.1133, 0.0526, 0.0881, 0.0494, 0.0249, 0.0186, 0.0267, 0.0037, 0.0031, 0.0034, 0.0011, 0.0006, 0.0011, 0.0000, 0.0001, 0.0001], // de defesa/centro
  [0.0164, 0.1372, 0.5818, 0.0068, 0.0330, 0.1803, 0.0021, 0.0063, 0.0256, 0.0008, 0.0017, 0.0045, 0.0004, 0.0005, 0.0022, 0.0000, 0.0001, 0.0002], // de defesa/lado_y_alto
  [0.0751, 0.0339, 0.0034, 0.5451, 0.0913, 0.0144, 0.1772, 0.0236, 0.0052, 0.0137, 0.0023, 0.0019, 0.0087, 0.0013, 0.0016, 0.0009, 0.0003, 0.0001], // de meio_baixo/lado_y_baixo
  [0.0133, 0.0511, 0.0133, 0.1094, 0.4648, 0.1034, 0.0572, 0.1046, 0.0550, 0.0062, 0.0043, 0.0052, 0.0044, 0.0016, 0.0047, 0.0004, 0.0004, 0.0004], // de meio_baixo/centro
  [0.0035, 0.0345, 0.0706, 0.0136, 0.0934, 0.5443, 0.0046, 0.0259, 0.1790, 0.0017, 0.0027, 0.0132, 0.0014, 0.0017, 0.0085, 0.0001, 0.0005, 0.0008], // de meio_baixo/lado_y_alto
  [0.0032, 0.0020, 0.0001, 0.0906, 0.0241, 0.0020, 0.5917, 0.0673, 0.0068, 0.1271, 0.0139, 0.0039, 0.0491, 0.0054, 0.0051, 0.0042, 0.0027, 0.0010], // de meio_alto/lado_y_baixo
  [0.0008, 0.0079, 0.0011, 0.0165, 0.0888, 0.0166, 0.0972, 0.4777, 0.0961, 0.0345, 0.0660, 0.0331, 0.0197, 0.0136, 0.0218, 0.0025, 0.0035, 0.0026], // de meio_alto/centro
  [0.0002, 0.0019, 0.0034, 0.0022, 0.0278, 0.0858, 0.0071, 0.0719, 0.5916, 0.0033, 0.0145, 0.1234, 0.0039, 0.0050, 0.0508, 0.0008, 0.0024, 0.0040], // de meio_alto/lado_y_alto
  [0.0001, 0.0001, 0.0000, 0.0052, 0.0016, 0.0000, 0.1541, 0.0273, 0.0024, 0.4454, 0.0442, 0.0032, 0.2516, 0.0229, 0.0062, 0.0206, 0.0121, 0.0029], // de ataque_fora_da_area_baixo/lado_y_baixo
  [0.0000, 0.0003, 0.0000, 0.0003, 0.0019, 0.0009, 0.0210, 0.0913, 0.0207, 0.0524, 0.4339, 0.0544, 0.0685, 0.1326, 0.0766, 0.0101, 0.0222, 0.0130], // de ataque_fora_da_area_baixo/centro
  [0.0000, 0.0001, 0.0002, 0.0002, 0.0017, 0.0058, 0.0032, 0.0263, 0.1467, 0.0038, 0.0397, 0.4580, 0.0068, 0.0200, 0.2548, 0.0027, 0.0118, 0.0182], // de ataque_fora_da_area_baixo/lado_y_alto
  [0.0000, 0.0000, 0.0000, 0.0007, 0.0002, 0.0000, 0.0210, 0.0029, 0.0001, 0.1011, 0.0128, 0.0007, 0.6818, 0.0334, 0.0051, 0.0786, 0.0567, 0.0050], // de ataque_fora_da_area_alto/lado_y_baixo
  [0.0000, 0.0000, 0.0000, 0.0004, 0.0000, 0.0003, 0.0022, 0.0065, 0.0017, 0.0131, 0.0792, 0.0143, 0.0680, 0.5307, 0.0787, 0.0426, 0.1213, 0.0409], // de ataque_fora_da_area_alto/centro
  [0.0000, 0.0000, 0.0000, 0.0000, 0.0003, 0.0003, 0.0001, 0.0022, 0.0190, 0.0006, 0.0114, 0.0937, 0.0065, 0.0299, 0.6967, 0.0048, 0.0606, 0.0739], // de ataque_fora_da_area_alto/lado_y_alto
  [0.0000, 0.0000, 0.0000, 0.0000, 0.0000, 0.0000, 0.0003, 0.0003, 0.0000, 0.0046, 0.0033, 0.0003, 0.1911, 0.0407, 0.0088, 0.5194, 0.2218, 0.0094], // de grande_area_adversaria/lado_y_baixo
  [0.0000, 0.0003, 0.0003, 0.0003, 0.0003, 0.0000, 0.0000, 0.0000, 0.0008, 0.0008, 0.0042, 0.0003, 0.0175, 0.0725, 0.0196, 0.0623, 0.7689, 0.0522], // de grande_area_adversaria/centro
  [0.0000, 0.0000, 0.0000, 0.0000, 0.0000, 0.0000, 0.0000, 0.0000, 0.0003, 0.0000, 0.0021, 0.0049, 0.0085, 0.0322, 0.1950, 0.0106, 0.2090, 0.5375], // de grande_area_adversaria/lado_y_alto
];

// Ações observadas que começam em cada zona: quanto menor, menos confiável a linha correspondente.
export const ACOES_POR_ZONA_18 = [47949, 66345, 45068, 60544, 53305, 57649, 62044, 43637, 60452, 25246, 14650, 25031, 34794, 11253, 37191, 5682, 10809, 5766];
