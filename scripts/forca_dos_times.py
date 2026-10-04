"""Força dos times e janela do jogo a partir das contagens de `analisar_forca_e_janela_statsbomb.py` (Achado 32).

Para cada time e papel (ataque / defesa) e cada resultado (chute, perda): razão = observado / esperado de um time médio nas mesmas zonas, ENCOLHIDA em direção a 1
com peso w = confiabilidade (correlação entre duas metades dos jogos, corrigida para o tamanho cheio por Spearman-Brown): 1 + w * (razão - 1).
"""

from __future__ import annotations

import math

NOMES = ("ataque_chute", "ataque_perda", "defesa_chute", "defesa_perda")


def somar(blocos: list[list]) -> list[list]:
    """Soma listas [[n_cont, n_perda, n_chute, n_quebra] x 18 zonas]."""
    out = [[0, 0, 0, 0] for _ in range(18)]
    for b in blocos:
        for z in range(18):
            for k in range(4):
                out[z][k] += b[z][k]
    return out


def taxas_base(dados: dict, condicionar_por_zona: bool = False) -> dict:
    """Taxas do time médio, usando todos os jogos: p_chute, p_perda (por linha) e p_quebra (por linha que continua).
    `condicionar_por_zona=False` (padrão): a mesma taxa para toda zona, então a razão é chute/perda POR LINHA sem descontar onde a linha começou -- é o que o
    simulador reproduz, já que ele não dá ao time uma mistura de zonas própria (ver Achado 32). `True` desconta a mistura de zonas do time."""
    tudo = somar([d for q in dados["papel"].values() for d in q["ataque"].values()])
    if not condicionar_por_zona:
        n = sum(sum(tudo[z][:3]) for z in range(18))
        nc = sum(tudo[z][0] for z in range(18))
        return {"p_chute": [sum(tudo[z][2] for z in range(18)) / n] * 18, "p_perda": [sum(tudo[z][1] for z in range(18)) / n] * 18,
                "p_quebra": [sum(tudo[z][3] for z in range(18)) / nc] * 18}
    linhas = [sum(tudo[z][:3]) for z in range(18)]
    return {"p_chute": [tudo[z][2] / linhas[z] if linhas[z] else 0.0 for z in range(18)],
            "p_perda": [tudo[z][1] / linhas[z] if linhas[z] else 0.0 for z in range(18)],
            "p_quebra": [tudo[z][3] / tudo[z][0] if tudo[z][0] else 0.0 for z in range(18)]}


def esperado_e_observado(bloco: list[list], base: dict) -> dict:
    linhas = [sum(bloco[z][:3]) for z in range(18)]
    return {"chute": (sum(bloco[z][2] for z in range(18)), sum(linhas[z] * base["p_chute"][z] for z in range(18))),
            "perda": (sum(bloco[z][1] for z in range(18)), sum(linhas[z] * base["p_perda"][z] for z in range(18))),
            "quebra": (sum(bloco[z][3] for z in range(18)), sum(bloco[z][0] * base["p_quebra"][z] for z in range(18)))}


def razoes_por_time(dados: dict, quartos: tuple, base: dict) -> dict:
    """{(papel, time): {'chute': razão, 'perda': razão}} somando só os quartos dados."""
    out = {}
    for papel in ("ataque", "defesa"):
        times = set()
        for q in quartos:
            times |= set(dados["papel"][str(q)][papel])
        for t in times:
            bloco = somar([dados["papel"][str(q)][papel][t] for q in quartos if t in dados["papel"][str(q)][papel]])
            eo = esperado_e_observado(bloco, base)
            out[(papel, t)] = {k: (eo[k][0] / eo[k][1] if eo[k][1] > 0 else 1.0) for k in ("chute", "perda")}
    return out


def pearson(x: list[float], y: list[float]) -> float:
    n = len(x)
    if n < 3:
        return 0.0
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    if sxx <= 0 or syy <= 0:
        return 0.0
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / math.sqrt(sxx * syy)


def peso_de_confiabilidade(r_metades: float) -> float:
    """Spearman-Brown: confiabilidade do conjunto inteiro a partir da correlação entre as duas metades; limitada a [0, 1]."""
    return max(0.0, min(1.0, 2 * r_metades / (1 + r_metades))) if r_metades > -0.999 else 0.0


def ajustar(dados: dict, treino: tuple = (0, 1, 2, 3), metades: tuple = ((0, 2), (1, 3)), condicionar_por_zona: bool = False) -> dict:
    """Multiplicadores por time (encolhidos) e por janela. `treino` = quartos usados; `metades` = como dividi-los para medir a confiabilidade."""
    base = taxas_base(dados, condicionar_por_zona)
    cheio = razoes_por_time(dados, treino, base)
    ra, rb = razoes_por_time(dados, metades[0], base), razoes_por_time(dados, metades[1], base)
    conf, peso = {}, {}
    for papel in ("ataque", "defesa"):
        for k in ("chute", "perda"):
            comuns = [t for (p, t) in ra if p == papel and (papel, t) in rb]
            r = pearson([ra[(papel, t)][k] for t in comuns], [rb[(papel, t)][k] for t in comuns])
            conf[f"{papel}_{k}"], peso[f"{papel}_{k}"] = r, peso_de_confiabilidade(r)
    times = {}
    for (papel, t), v in cheio.items():
        d = times.setdefault(t, {"nome": dados["times"].get(t, t), **{n: 1.0 for n in NOMES}})
        for k in ("chute", "perda"):
            d[f"{papel}_{k}"] = 1.0 + peso[f"{papel}_{k}"] * (v[k] - 1.0)
    janela = []
    for w in range(6):
        eo = esperado_e_observado(dados["janela"][w], base)
        janela.append({k: (eo[k][0] / eo[k][1] if eo[k][1] > 0 else 1.0) for k in ("chute", "perda", "quebra")})
    return {"times": times, "confiabilidade": conf, "peso": peso, "janela": janela}
