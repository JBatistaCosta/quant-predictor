#!/usr/bin/env python3
"""Autoverificação do núcleo paramétrico (`distribuicoes.py`).

O projeto não tem suíte de testes, e este módulo é o ponto onde um erro
silencioso sai mais caro: uma identidade quebrada aqui (probabilidade que
não soma 1, dispersão estimada com viés) contamina TODOS os mercados de
uma vez, e o sintoma só apareceria lá na frente como "log-loss ruim", já
misturado com ruído de amostra.

São checagens de identidade matemática e de recuperação de parâmetro em
dado sintético -- não tocam no Supabase e rodam em segundos:

  - identidades: toda família de mercado soma 1; mercados que TÊM que
    coincidir coincidem (handicap 0.0 == 1X2, over 0.5 == 1-P(0x0));
  - casos-limite: ρ=0 devolve Poisson dupla independente exata, NB com
    r grande devolve Poisson, Beta-Binomial(1,1) devolve uniforme;
  - recuperação: gera dado sintético com parâmetro conhecido e confere
    que o estimador o recupera (foi assim que o viés de ruído binomial no
    ajuste Beta-Binomial apareceu).

Uso:
    python verificar_distribuicoes.py     # sai 1 se algo falhar
"""

from __future__ import annotations

import sys

import numpy as np
from scipy.stats import poisson

import distribuicoes as D

_falhas: list[str] = []


def check(nome: str, condicao: bool, extra: str = "") -> None:
    print(("  ok   " if condicao else "  FALHA ") + nome + (f"  [{extra}]" if extra else ""))
    if not condicao:
        _falhas.append(nome)


def testar_gols() -> None:
    print("\n== Gols: Poisson + correção Dixon-Coles ==")
    m = D.matriz_placares(1.6, 1.1, -0.05)
    check("matriz de placares soma 1", abs(m.sum() - 1) < 1e-12)
    check("matriz sem célula negativa", bool((m >= 0).all()))

    # ρ=0 tem que devolver a Poisson dupla independente EXATA -- é o teste
    # que pega erro na aplicação de τ.
    m0 = D.matriz_placares(1.6, 1.1, 0.0)
    ind = np.outer(poisson.pmf(np.arange(11), 1.6), poisson.pmf(np.arange(11), 1.1))
    ind /= ind.sum()
    check("ρ=0 reproduz Poisson independente", np.abs(m0 - ind).max() < 1e-12)

    mk = D.mercados_de_gols(m)
    check("1X2 soma 1", abs(sum(mk[("1X2", s)] for s in ("home", "draw", "away")) - 1) < 1e-12)
    for linha in (0.5, 1.5, 2.5, 3.5, 4.5):
        soma = mk[(f"over_under_{linha}", "over")] + mk[(f"over_under_{linha}", "under")]
        check(f"over/under {linha} soma 1", abs(soma - 1) < 1e-12)
    check("BTTS soma 1", abs(mk[("btts", "yes")] + mk[("btts", "no")] - 1) < 1e-12)
    check("faixas de gols somam 1", abs(sum(v for (mm, _), v in mk.items() if mm == "faixa_gols") - 1) < 1e-12)

    # Coerência cruzada entre mercados -- o ponto do modelo paramétrico.
    check("handicap 0.0 == 1X2", abs(mk[("handicap_0.0", "home")] - mk[("1X2", "home")]) < 1e-12
          and abs(mk[("handicap_0.0", "push")] - mk[("1X2", "draw")]) < 1e-12)
    check("handicap -0.5 home == 1X2 home", abs(mk[("handicap_-0.5", "home")] - mk[("1X2", "home")]) < 1e-12)
    for linha in (-2.5, -1.0, 0.0, 1.5, 2.0):
        soma = (mk[(f"handicap_{linha}", "home")] + mk[(f"handicap_{linha}", "away")]
                + mk.get((f"handicap_{linha}", "push"), 0.0))
        check(f"handicap {linha} soma 1", abs(soma - 1) < 1e-12)
    check("over 0.5 == 1 - P(0x0)", abs(mk[("over_under_0.5", "over")] - (1 - m[0, 0])) < 1e-12)
    check("dupla chance 1X == home+draw",
          abs(mk[("dupla_chance", "1X")] - (mk[("1X2", "home")] + mk[("1X2", "draw")])) < 1e-12)
    check("placares exatos são probabilidades válidas",
          all(0 <= v <= 1 for (mm, _), v in mk.items() if mm == "placar_exato"))


def testar_escanteios() -> None:
    print("\n== Escanteios: NB no total + split Beta-Binomial ==")
    # r grande converge pra Poisson. Testado em r=1e6 (e não 1e12) porque
    # em r absurdo o próprio `nbinom` do scipy perde precisão por
    # cancelamento -- o limite matemático vale, a aritmética de ponto
    # flutuante é que não acompanha.
    nb = D._nb_pmf_vetor(10.0, 1e6)
    check("NB com r grande converge pra Poisson",
          np.abs(nb - poisson.pmf(np.arange(41), 10.0)).max() < 1e-5)
    check("NB soma 1", abs(D._nb_pmf_vetor(10.0, 65.0).sum() - 1) < 1e-9)

    check("Beta-Binomial(1,1) é uniforme", np.abs(D._beta_binomial_pmf(10, 1.0, 1.0) - 1 / 11).max() < 1e-12)
    check("Beta-Binomial soma 1", abs(D._beta_binomial_pmf(12, 3.0, 2.5).sum() - 1) < 1e-12)

    conjunta = D.distribuicao_conjunta_escanteios(10.36, 65.0, 6.0, 5.0)
    check("conjunta soma 1", abs(conjunta.sum() - 1) < 1e-12)

    idx = np.arange(conjunta.shape[0])
    media_casa = float((conjunta.sum(axis=1) * idx).sum())
    media_fora = float((conjunta.sum(axis=0) * idx).sum())
    check("média do total é preservada pelo split", abs(media_casa + media_fora - 10.36) < 1e-6,
          f"casa={media_casa:.2f} fora={media_fora:.2f}")

    # A correlação NEGATIVA é a razão de ser da decomposição (medida no
    # banco: -0,24 a -0,31). Se o split gerasse correlação positiva, a
    # estrutura estaria errada.
    esperanca_produto = float((conjunta * np.outer(idx, idx)).sum())
    cov = esperanca_produto - media_casa * media_fora
    var_casa = float((conjunta.sum(axis=1) * (idx - media_casa) ** 2).sum())
    var_fora = float((conjunta.sum(axis=0) * (idx - media_fora) ** 2).sum())
    corr = cov / np.sqrt(var_casa * var_fora)
    check("split gera correlação negativa casa/fora", corr < 0, f"corr={corr:.3f}")

    mc = D.mercados_de_escanteios(10.36, 65.0, 6.0, 5.0)
    for linha in (7.5, 9.5, 12.5):
        soma = mc[(f"corners_over_under_{linha}", "over")] + mc[(f"corners_over_under_{linha}", "under")]
        check(f"escanteios over/under {linha} soma 1", abs(soma - 1) < 1e-9)
    check("faixas de escanteios somam 1", abs(sum(v for (mm, _), v in mc.items() if mm == "faixa_corners") - 1) < 1e-9)
    check("corners 1X2 soma 1", abs(sum(mc[("corners_1X2", s)] for s in ("home", "draw", "away")) - 1) < 1e-9)


def testar_estimadores() -> None:
    print("\n== Estimadores (recuperação em dado sintético) ==")
    rng = np.random.default_rng(7)
    n = 40_000

    lam, mu = rng.uniform(0.8, 2.4, n), rng.uniform(0.6, 1.9, n)
    rho = D.ajustar_rho_mle(lam, mu, rng.poisson(lam), rng.poisson(mu))
    check("ρ ≈ 0 em dado Poisson puro", abs(rho) < 0.02, f"ρ={rho:.4f}")

    lam_corners = rng.uniform(7, 14, n)
    r_poisson = D.ajustar_dispersao_nb(lam_corners, rng.poisson(lam_corners))
    check("dispersão → ∞ em dado Poisson", r_poisson > 500, f"r={r_poisson:.0f}")
    for r_real in (30.0, 65.0, 150.0):
        amostra = rng.negative_binomial(r_real, r_real / (r_real + lam_corners))
        r_hat = D.ajustar_dispersao_nb(lam_corners, amostra)
        check(f"dispersão recuperada (r={r_real:.0f})", 0.65 * r_real < r_hat < 1.5 * r_real, f"r̂={r_hat:.1f}")

    # Recuperação do split em várias configurações -- foi aqui que o viés
    # do ruído binomial apareceu (a versão sem o desconto E[1/total]
    # devolvia concentração 4,5 quando a real era 11).
    for alpha_real, beta_real in ((6.0, 5.0), (3.0, 3.0), (12.0, 8.0)):
        total = rng.integers(4, 20, n)
        casa = rng.binomial(total, rng.beta(alpha_real, beta_real, n))
        alpha, beta = D.ajustar_beta_binomial(total, casa)
        concentracao_real, concentracao = alpha_real + beta_real, alpha + beta
        check(f"Beta-Binomial recuperado (α={alpha_real:.0f}, β={beta_real:.0f})",
              abs(alpha - alpha_real) < 0.15 * concentracao_real and abs(beta - beta_real) < 0.15 * concentracao_real,
              f"α̂={alpha:.2f} β̂={beta:.2f}")

    # Dado binomial puro (p fixo) não tem superdispersão de Beta: o
    # estimador tem que ir pro teto de concentração, não inventar uma Beta larga.
    total = rng.integers(4, 20, n)
    alpha, beta = D.ajustar_beta_binomial(total, rng.binomial(total, 0.55))
    check("sem superdispersão → concentração no teto", alpha + beta > 100, f"κ̂={alpha + beta:.0f}")


def testar_verossimilhanca() -> None:
    print("\n== Verossimilhança ==")
    m = D.matriz_placares(1.6, 1.1, -0.05)
    check("log P(placar) = log da célula", abs(D.log_verossimilhanca_placar(m, 2, 1) - np.log(m[2, 1])) < 1e-12)
    check("placar acima do teto não vira -inf", np.isfinite(D.log_verossimilhanca_placar(m, 99, 99)))


def main() -> None:
    testar_gols()
    testar_escanteios()
    testar_estimadores()
    testar_verossimilhanca()
    print("\n" + ("Tudo passou." if not _falhas else f"{len(_falhas)} falha(s): {_falhas}"))
    sys.exit(1 if _falhas else 0)


if __name__ == "__main__":
    main()
