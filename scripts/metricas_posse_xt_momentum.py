"""Métricas de posse, valor de ameaça (xT empírico por zona) e momentum, calculadas sobre uma lista de partidas de linhas de ação (Achado 33).

Uma partida é uma lista de linhas (equipe, zona, tipo, metade, xg, gol, segundos): a MESMA função mede os eventos reais e as partidas simuladas, então a comparação é justa.
 - Corrida (posse): linhas consecutivas da mesma equipe na mesma metade. Duração da corrida = instante da primeira linha da próxima corrida - instante da primeira linha dela
   (o tempo morto de um reinício entra na corrida de quem perdeu a bola, como na posse do StatsBomb).
 - xT empírico: para cada linha em uma zona, o que vem DEPOIS dela na mesma corrida: houve chute? soma de xG? gol?
 - Momentum: (a) correlação entre os chutes de uma equipe em janelas consecutivas de 5 min; (b) fração dos chutes que vêm até 60/120 s depois de outro chute da mesma equipe.
"""

from __future__ import annotations

import math

JANELA_S = 300
POSICOES = ((1, 1), (2, 2), (3, 3), (4, 5), (6, 8), (9, 14), (15, 999))       # posição da linha dentro da corrida (1 = primeira)
LIMITES_GAP_S = (60, 120, 300)


def pearson(x, y) -> float:
    n = len(x)
    if n < 3:
        return 0.0
    mx, my = sum(x) / n, sum(y) / n
    sxx, syy = sum((a - mx) ** 2 for a in x), sum((b - my) ** 2 for b in y)
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else 0.0


def calcular(partidas: list[list[tuple]]) -> dict:
    n_jogos = len(partidas)
    # taxas por zona de todo o conjunto (para o risco de perda/chute ESPERADO dada a zona)
    z_n, z_perda, z_chute = [0] * 18, [0] * 18, [0] * 18
    for linhas in partidas:
        for _, z, tipo, *_ in linhas:
            z_n[z] += 1
            z_perda[z] += tipo == "perda"
            z_chute[z] += tipo == "chute"
    pos_acc = [[0, 0, 0.0, 0, 0.0] for _ in POSICOES]       # n, perdas obs, perdas esp, chutes obs, chutes esp
    zonas = [[0, 0, 0.0, 0] for _ in range(18)]          # por zona: n linhas, n com chute depois, soma de xG depois, n com gol depois
    shares_tempo, shares_linhas = [], []
    runs_por_jogo, dur_runs, linhas_runs = [], [], []
    pares_x, pares_y = [], []
    gaps = [0, 0, 0]
    n_chutes_com_anterior = n_chutes = 0
    for linhas in partidas:
        tempo_eq: dict = {}
        linhas_eq: dict = {}
        n_runs = 0
        chutes_janela: dict = {}                          # (equipe, metade, janela) -> n
        ult_chute: dict = {}                              # (equipe, metade) -> último instante
        for metade in (0, 1):
            ls = [x for x in linhas if x[3] == metade]
            if not ls:
                continue
            # corridas
            inicios = [0] + [i for i in range(1, len(ls)) if ls[i][0] != ls[i - 1][0]]
            fins = inicios[1:] + [len(ls)]
            for ini, fim in zip(inicios, fins):
                eq = ls[ini][0]
                t0 = ls[ini][6]
                t1 = ls[fim][6] if fim < len(ls) else ls[-1][6]
                dur = max(t1 - t0, 0.0)
                tempo_eq[eq] = tempo_eq.get(eq, 0.0) + dur
                linhas_eq[eq] = linhas_eq.get(eq, 0) + (fim - ini)
                n_runs += 1
                dur_runs.append(dur)
                linhas_runs.append(fim - ini)
                for k in range(ini, fim):
                    _, z, tipo, *_ = ls[k]
                    posic = k - ini + 1
                    for bi, (lo, hi) in enumerate(POSICOES):
                        if lo <= posic <= hi:
                            a = pos_acc[bi]
                            a[0] += 1
                            a[1] += tipo == "perda"
                            a[2] += z_perda[z] / z_n[z]
                            a[3] += tipo == "chute"
                            a[4] += z_chute[z] / z_n[z]
                            break
                # xT: o que vem depois (inclusive a própria linha) dentro da corrida
                xg_resto, chute_resto, gol_resto = 0.0, 0, 0
                for k in range(fim - 1, ini - 1, -1):
                    _, z, tipo, _, xg, gol, _ = ls[k]
                    if tipo == "chute":
                        chute_resto, xg_resto, gol_resto = 1, xg_resto + xg, gol_resto + gol
                    zz = zonas[z]
                    zz[0] += 1
                    zz[1] += chute_resto
                    zz[2] += xg_resto
                    zz[3] += 1 if gol_resto else 0
            # momentum
            for eq, z, tipo, _, _, _, t in ls:
                if tipo != "chute":
                    continue
                n_chutes += 1
                chave = (eq, metade, min(int(t // JANELA_S), 9))
                chutes_janela[chave] = chutes_janela.get(chave, 0) + 1
                if (eq, metade) in ult_chute:
                    n_chutes_com_anterior += 1
                    g = t - ult_chute[(eq, metade)]
                    for i, lim in enumerate(LIMITES_GAP_S):
                        if g <= lim:
                            gaps[i] += 1
                ult_chute[(eq, metade)] = t
        total_t = sum(tempo_eq.values()) or 1.0
        total_l = sum(linhas_eq.values()) or 1
        equipes = sorted(tempo_eq)
        if len(equipes) == 2:
            shares_tempo.append(tempo_eq[equipes[0]] / total_t)
            shares_linhas.append(linhas_eq[equipes[0]] / total_l)
        runs_por_jogo.append(n_runs)
        for eq in {c[0] for c in chutes_janela} | set(equipes):
            for metade in (0, 1):
                v = [chutes_janela.get((eq, metade, j), 0) for j in range(10)]
                pares_x += v[:-1]
                pares_y += v[1:]

    def media(v):
        return sum(v) / len(v) if v else 0.0

    def dp(v):
        m = media(v)
        return (sum((a - m) ** 2 for a in v) / len(v)) ** 0.5 if v else 0.0

    def quantil(v, q):
        o = sorted(v)
        return o[int(q * (len(o) - 1))] if o else 0.0

    return {
        "jogos": n_jogos,
        "risco_por_posicao_na_corrida": [{"posicao": f"{lo}" if lo == hi else (f"{lo}-{hi}" if hi < 999 else f"{lo}+"), "n": a[0],
                                          "perda_obs_sobre_esp": a[1] / a[2] if a[2] else 0.0, "chute_obs_sobre_esp": a[3] / a[4] if a[4] else 0.0}
                                         for (lo, hi), a in zip(POSICOES, pos_acc)],
        "corridas_por_jogo": media(runs_por_jogo), "duracao_corrida_media_s": media(dur_runs), "duracao_corrida_mediana_s": quantil(dur_runs, 0.5),
        "duracao_corrida_p90_s": quantil(dur_runs, 0.9), "linhas_por_corrida_media": media(linhas_runs), "linhas_por_corrida_p90": quantil(linhas_runs, 0.9),
        "share_tempo_dp": dp(shares_tempo), "share_linhas_dp": dp(shares_linhas),
        "xt_por_zona": [{"n": zz[0], "p_chute_depois": zz[1] / zz[0] if zz[0] else 0.0, "xg_depois": zz[2] / zz[0] if zz[0] else 0.0,
                         "p_gol_depois": zz[3] / zz[0] if zz[0] else 0.0} for zz in zonas],
        "chutes_por_equipe_jogo": n_chutes / (2 * n_jogos) if n_jogos else 0.0,
        "autocorrelacao_chutes_5min_lag1": pearson(pares_x, pares_y),
        "chutes_ate_60s_do_anterior_pct": 100 * gaps[0] / n_chutes if n_chutes else 0.0,
        "chutes_ate_120s_do_anterior_pct": 100 * gaps[1] / n_chutes if n_chutes else 0.0,
        "chutes_ate_300s_do_anterior_pct": 100 * gaps[2] / n_chutes if n_chutes else 0.0,
    }
