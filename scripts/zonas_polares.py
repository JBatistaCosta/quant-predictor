"""As 14 zonas polares da calculadora (Análise de Evento): 7 anéis de distância ao centro do gol x cone central (ângulo <= 30 graus do eixo) ou aberto.

`FOTMOB_TELA` é a tabela do estudo do frontend (~477 mil chutes do FotMob, sem pênaltis; xG do FotMob e gols reais por chute), copiada da tela em 04/10/2026:
% real de chutes, xG por chute e gols por chute em cada zona. É descritiva, de uma amostra externa, e usada aqui só para dar xG e gol aos chutes simulados.
"""

from __future__ import annotations

ANEIS_M = (6.0, 9.0, 12.0, 16.5, 22.0, 30.0)         # limites superiores dos 6 primeiros anéis; o 7º é "> 30 m"
CONE_GRAUS = 30.0
NOMES = [f"{a} · {c}" for a in ("0–6 m", "6–9 m", "9–12 m", "12–16.5 m", "16.5–22 m", "22–30 m", "> 30 m") for c in ("central", "aberto")]

# (chutes, % real, xG por chute, gols por chute) na ordem de NOMES.
# TABELA ATUAL (usada pelo simulador): chutes de clubes do banco (match_shots_fotmob) de jogos a partir de 01/08/2021, depois da mudança do modelo de xG do FotMob
# (Achado 39), sem pênaltis, gol contra e disputa de pênaltis, SEM as ligas de seleções (Achado 44); 16.930 jogos, 428.248 chutes. Fonte: `dados_referencia/fotmob/tabela_polar_clubes_por_regime_xg.json`.
FOTMOB = [
    (9996, 2.33, 0.4392, 0.3977),
    (10686, 2.5, 0.3980, 0.3852),
    (32685, 7.63, 0.1963, 0.1912),
    (17792, 4.15, 0.1588, 0.1375),
    (33488, 7.82, 0.1287, 0.1257),
    (23344, 5.45, 0.1162, 0.1106),
    (43766, 10.22, 0.1176, 0.1158),
    (53543, 12.5, 0.0923, 0.0924),
    (38826, 9.07, 0.0663, 0.0664),
    (43448, 10.15, 0.0438, 0.0420),
    (72908, 17.02, 0.0315, 0.0320),
    (26043, 6.08, 0.0274, 0.0282),
    (17594, 4.11, 0.0198, 0.0168),
    (4129, 0.96, 0.0218, 0.0417),
]

# TABELA DA TELA (copiada em 04/10/2026): todos os jogos do banco, 479.611 chutes, MISTURA o xG antigo (até jul/2021, ~10% dos chutes) com o novo.
FOTMOB_TELA = [
    (11106, 2.32, 0.443, 0.401), (11933, 2.49, 0.404, 0.388),
    (36627, 7.64, 0.197, 0.193), (19843, 4.14, 0.162, 0.139),
    (37745, 7.87, 0.129, 0.126), (26095, 5.44, 0.118, 0.112),
    (49005, 10.22, 0.116, 0.117), (59889, 12.49, 0.093, 0.093),
    (43273, 9.02, 0.065, 0.067), (48285, 10.07, 0.044, 0.042),
    (82226, 17.14, 0.032, 0.032), (29188, 6.09, 0.028, 0.028),
    (19773, 4.12, 0.020, 0.016), (4623, 0.96, 0.022, 0.041),
]


def indice(dist_m: float, angulo_graus: float) -> int:
    """Índice 0..13 da zona polar de um chute (distância ao centro do gol em metros; ângulo em graus em relação ao eixo do campo, 0 = de frente)."""
    anel = sum(dist_m >= lim for lim in ANEIS_M)
    return anel * 2 + (0 if angulo_graus <= CONE_GRAUS else 1)
