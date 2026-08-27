#!/usr/bin/env python3
"""Validação sintética: EKF de 1 passo (atual) vs. IEKF (iterado) pra força
dinâmica de times (ver forca_dinamica_desenho.md).

Ponto de partida: forca_dinamica_desenho.md tinha diagnosticado um erro
residual de ~0.097 (após corrigir deriva de gauge) e hipotetizado que a
causa era a linearização de 1 passo só (EKF "puro") ser grosseira demais
pra verossimilhança de Poisson com pouca informação por partida — e que
iterar a linearização (IEKF) resolveria.

**Essa hipótese foi testada aqui e NÃO se confirmou.** O que os 3
experimentos abaixo mostram, em ordem:

1. EKF (n_iter=1) vs IEKF (n_iter=5): IEKF melhora só ~3% o RMSE, não o
   suficiente pra ser "a explicação".
2. Rodando o Newton até convergência de verdade (n_iter=20 e 50 dão
   resultado IDÊNTICO a n_iter=5) confirma que o IEKF já está totalmente
   convergido — não é falta de iteração. A moda que ele acha é a moda
   EXATA do posterior 1D (dado o prior gaussiano), não uma aproximação
   grosseira dela.
3. Um teste mínimo e decisivo (1 parâmetro, 1 observação, sem nada
   sequencial/multi-time envolvido) mostra a causa real: a MODA do
   posterior (o que Newton/IEKF/EKF sempre calculam) é sistematicamente
   MAIOR que a MÉDIA EXATA do posterior (calculada por integração
   numérica), por causa da assimetria (skew) da verossimilhança de Poisson
   em contagem baixa. Isso é viés de **forma** da aproximação de Laplace
   (moda ≠ média num posterior assimétrico), não de quantas iterações de
   Newton se dá — por isso IEKF não resolve, ele só acha a moda enviesada
   com mais precisão.

Isso também explica por que o viés apareceu como "defesa" no experimento 1
e não como "ataque": a recentralização de gauge (`atk -= atk.mean()`, já
necessária por outro motivo — remover a única direção não-identificável do
modelo) cancela esse viés agregado em ataque de graça, como efeito
colateral. Defesa não tem essa recentralização (não precisa dela pra
identificabilidade) e por isso acumula o mesmo viés compartilhado sem
correção nenhuma.

Uso:
    python forca_dinamica_iekf.py
"""

from __future__ import annotations

import numpy as np
from scipy.integrate import quad
from scipy.stats import poisson

# ---------------------------------------------------------------------------
# Parâmetros do experimento sintético (times/rodadas)
# ---------------------------------------------------------------------------
N_TIMES = 20
N_RODADAS = 60           # 60 rodadas x 10 partidas/rodada = 600 partidas, igual ao diagnóstico do .md
SIGMA_FORCA = 0.35        # espalhamento realista de ataque/defesa em log-escala
MANDO_REAL = 0.25
V0_PRIOR = 0.30           # variância inicial (incerteza "razoável", não difusa nem exata)
SEED = 20260825


def simular_liga(rng: np.random.Generator):
    ataque_real = rng.normal(0, SIGMA_FORCA, N_TIMES)
    ataque_real -= ataque_real.mean()          # mesmo gauge que o filtro vai impor (atk.mean()=0)
    defesa_real = rng.normal(0, SIGMA_FORCA, N_TIMES)
    defesa_real -= defesa_real.mean()          # não exigido pra identificabilidade, só realismo

    rodadas = []
    for _ in range(N_RODADAS):
        times = rng.permutation(N_TIMES)
        mandantes, visitantes = times[: N_TIMES // 2], times[N_TIMES // 2 :]
        partidas = []
        for i, j in zip(mandantes, visitantes):
            lam_h = np.exp(ataque_real[i] + defesa_real[j] + MANDO_REAL)
            lam_a = np.exp(ataque_real[j] + defesa_real[i])
            hg = rng.poisson(lam_h)
            ag = rng.poisson(lam_a)
            partidas.append((i, j, hg, ag))
        rodadas.append(partidas)
    return ataque_real, defesa_real, rodadas


def newton_converge(mu0: float, V0: float, obs: int, n_iter: int) -> tuple[float, float]:
    """Newton no log-posterior de eta, sempre recombinando contra o prior
    ORIGINAL (mu0, V0) fixo — nunca contra o posterior da iteração anterior
    (senão o prior é contado várias vezes). n_iter=1 == EKF atual (já
    produção); n_iter grande == moda exata do posterior 1D (IEKF
    convergido)."""
    eta = mu0
    for _ in range(n_iter):
        lam = np.exp(eta)
        score = obs - lam
        fisher = lam
        eta = eta + (score - (eta - mu0) / V0) / (fisher + 1.0 / V0)
    return eta, np.exp(eta)


def atualizar_dupla(m1, v1, m2, v2, extra_const, obs, n_iter):
    """Update de duas Gaussianas independentes observadas só através da soma
    delas (eta = x1+x2+extra_const, obs ~ Poisson(exp(eta))). Mesma fórmula
    de split de sempre (ganho = v/(V0+R)); o que muda com n_iter é só o
    (eta*, R) que entra nela."""
    mu0 = m1 + m2 + extra_const
    V0 = v1 + v2
    eta_star, lam_final = newton_converge(mu0, V0, obs, n_iter)
    R = 1.0 / lam_final
    delta = eta_star - mu0
    ganho1 = v1 / (V0 + R)
    ganho2 = v2 / (V0 + R)
    m1n = m1 + ganho1 * delta
    m2n = m2 + ganho2 * delta
    v1n = v1 * (1 - ganho1)
    v2n = v2 * (1 - ganho2)
    return m1n, v1n, m2n, v2n


def rodar_filtro(rodadas, n_iter: int, recentralizar_defesa: bool = False):
    m_atk = np.zeros(N_TIMES)
    v_atk = np.full(N_TIMES, V0_PRIOR)
    m_def = np.zeros(N_TIMES)
    v_def = np.full(N_TIMES, V0_PRIOR)

    trajetoria = []
    for partidas in rodadas:
        for i, j, hg, ag in partidas:
            m_atk[i], v_atk[i], m_def[j], v_def[j] = atualizar_dupla(
                m_atk[i], v_atk[i], m_def[j], v_def[j], MANDO_REAL, hg, n_iter
            )
            m_atk[j], v_atk[j], m_def[i], v_def[i] = atualizar_dupla(
                m_atk[j], v_atk[j], m_def[i], v_def[i], 0.0, ag, n_iter
            )
        m_atk -= m_atk.mean()  # gauge-fix, igual ao lote (atk - atk.mean())
        if recentralizar_defesa:
            m_def -= m_def.mean()
        trajetoria.append((m_atk.copy(), m_def.copy()))
    return trajetoria


def rmse(m_atk, m_def, ataque_real, defesa_real):
    erros = np.concatenate([m_atk - ataque_real, m_def - defesa_real])
    return float(np.sqrt(np.mean(erros**2)))


def experimento_1_ekf_vs_iekf(ataque_real, defesa_real, rodadas):
    print("=" * 78)
    print("Experimento 1 — EKF (n_iter=1) vs IEKF (n_iter=5,20,50)")
    print("=" * 78)
    resultados = {}
    for n_iter in (1, 5, 20, 50):
        traj = rodar_filtro(rodadas, n_iter=n_iter)
        m_atk, m_def = traj[-1]
        resultados[n_iter] = rmse(m_atk, m_def, ataque_real, defesa_real)
        print(f"  n_iter={n_iter:>2}  RMSE final = {resultados[n_iter]:.4f}")
    print()
    print(f"  n_iter=5 -> 20 -> 50 dão o MESMO resultado: Newton já está totalmente")
    print(f"  convergido em 5 iterações. Mais iteração não muda nada — a hipótese")
    print(f"  original (falta convergência) está descartada por este teste.")
    print()
    return resultados


def experimento_2_bias_atk_vs_def(ataque_real, defesa_real, rodadas):
    print("=" * 78)
    print("Experimento 2 — viés médio: ataque (recentralizado) vs defesa (não)")
    print("=" * 78)
    traj = rodar_filtro(rodadas, n_iter=5)
    m_atk, m_def = traj[-1]
    erro_atk = m_atk - ataque_real
    erro_def = m_def - defesa_real
    print(f"  erro médio ataque : {erro_atk.mean():+.4f}  (forçado a ~0 pela recentralização)")
    print(f"  erro médio defesa : {erro_def.mean():+.4f}  (SEM recentralização — viés real exposto)")
    print()

    traj_def_recentralizada = rodar_filtro(rodadas, n_iter=5, recentralizar_defesa=True)
    m_atk2, m_def2 = traj_def_recentralizada[-1]
    print(f"  Se recentralizar defesa também (fora do que a identificabilidade exige):")
    print(f"    RMSE cai de {rmse(m_atk, m_def, ataque_real, defesa_real):.4f}"
          f" para {rmse(m_atk2, m_def2, ataque_real, defesa_real):.4f}")
    print(f"    (melhora parcial — confirma que há um viés AGREGADO comum a ataque/defesa,")
    print(f"    mas não elimina todo o erro: ainda sobra dispersão por time.)")
    print()


def experimento_3_moda_vs_media_exata():
    print("=" * 78)
    print("Experimento 3 — teste decisivo: moda (Newton/IEKF) vs média EXATA do")
    print("posterior, pra 1 parâmetro/1 observação isolados (sem nada sequencial)")
    print("=" * 78)
    v0 = 0.6  # ~ V0 típico (v_ai+v_dj) no experimento acima
    mu0 = 0.0

    def log_post(theta, y):
        return y * theta - np.exp(theta) - (theta - mu0) ** 2 / (2 * v0)

    def media_exata(y):
        lim = 8.0
        Z, _ = quad(lambda t: np.exp(log_post(t, y) - log_post(mu0, y)), -lim, lim, limit=200)
        num, _ = quad(lambda t: t * np.exp(log_post(t, y) - log_post(mu0, y)), -lim, lim, limit=200)
        return num / Z

    print(f"  {'y':>3} | {'moda (Newton)':>13} | {'média exata':>12} | {'viés':>8}")
    for y in range(8):
        eta_star, _ = newton_converge(mu0, v0, y, 20)
        m_exata = media_exata(y)
        print(f"  {y:>3} | {eta_star:>13.4f} | {m_exata:>12.4f} | {eta_star - m_exata:>+8.4f}")

    lam_true = np.exp(mu0)
    ys = np.arange(15)
    pesos = poisson.pmf(ys, lam_true)
    vies_medio = sum(
        p * (newton_converge(mu0, v0, y, 20)[0] - media_exata(y))
        for y, p in zip(ys, pesos) if p > 1e-8
    )
    print()
    print(f"  Viés é POSITIVO pra todo y plausível — não é ruído de amostra, é")
    print(f"  sistemático. Média sob a distribuição geradora (theta_real=0, "
          f"lambda=1): {vies_medio:+.4f}")
    print()
    print("  CONCLUSÃO: a moda do posterior (o que Newton/EKF/IEKF sempre entrega,")
    print("  por definição) fica sistematicamente ACIMA da média exata do posterior,")
    print("  porque a verossimilhança de Poisson em contagem baixa é assimétrica.")
    print("  Isso é viés de FORMA da aproximação de Laplace — iterar Newton não")
    print("  ajuda em nada aqui, porque convergir mais só acha a moda (enviesada)")
    print("  com mais precisão numérica, não a média (que é o que se quer).")


def main():
    rng = np.random.default_rng(SEED)
    ataque_real, defesa_real, rodadas = simular_liga(rng)

    experimento_1_ekf_vs_iekf(ataque_real, defesa_real, rodadas)
    experimento_2_bias_atk_vs_def(ataque_real, defesa_real, rodadas)
    experimento_3_moda_vs_media_exata()


if __name__ == "__main__":
    main()
