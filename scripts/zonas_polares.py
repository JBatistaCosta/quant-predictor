"""As 14 zonas polares da calculadora (Análise de Evento): 7 anéis de distância ao centro do gol x cone central (ângulo <= 30 graus do eixo) ou aberto.

`FOTMOB` é a tabela do estudo do frontend (~477 mil chutes do FotMob, sem pênaltis; xG do FotMob e gols reais por chute), copiada da tela em 04/10/2026:
% real de chutes, xG por chute e gols por chute em cada zona. É descritiva, de uma amostra externa, e usada aqui só para dar xG e gol aos chutes simulados.
"""

from __future__ import annotations

ANEIS_M = (6.0, 9.0, 12.0, 16.5, 22.0, 30.0)         # limites superiores dos 6 primeiros anéis; o 7º é "> 30 m"
CONE_GRAUS = 30.0
NOMES = [f"{a} · {c}" for a in ("0–6 m", "6–9 m", "9–12 m", "12–16.5 m", "16.5–22 m", "22–30 m", "> 30 m") for c in ("central", "aberto")]

# (chutes, % real, xG por chute, gols por chute) na ordem de NOMES
FOTMOB = [
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
