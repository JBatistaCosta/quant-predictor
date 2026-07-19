#!/usr/bin/env python3
"""Calibração Platt (one-vs-rest) pros modelos do Model Benchmarking.

Mesma técnica já validada em produção neste projeto (`model_calibration`,
achado documentado em CONTEXTO_PROJETO.md: coeficiente `a` sempre < 1,
confirmando que os modelos crus tendem a ser confiantes demais) -- aqui
aplicada como uma variante "conjugada" de cada modelo (`{nome}_calibrado`),
persistida lado a lado com a versão crua em `predicoes`.

Regra que não pode ser quebrada: a calibração é sempre ajustada num
conjunto de dado que o modelo BASE não usou pra treinar (aqui, o
Validation Set) -- calibrar em cima do próprio treino inflaria a confiança
artificialmente e não mediria nada de real.
"""

from __future__ import annotations

import numpy as np
from sklearn.linear_model import LogisticRegression

SELECOES = ("home", "draw", "away")
CODIGO_POR_SELECAO = {"home": 0, "draw": 1, "away": 2}
AMOSTRA_MINIMA = 30  # abaixo disso, o ajuste de Platt é ruído -- usa identidade (sem calibrar)


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def ajustar_platt_selecao(probs: np.ndarray, alvo_binario: np.ndarray) -> tuple[float, float]:
    """Ajusta a=coeficiente/b=intercepto de `sigmoid(a*logit(p)+b)` via
    regressão logística de 1 variável (`sklearn.LogisticRegression`, já é
    dependência transitiva do XGBoost -- não precisa de lib nova)."""
    x = _logit(probs).reshape(-1, 1)
    modelo = LogisticRegression(solver="lbfgs")
    modelo.fit(x, alvo_binario)
    return float(modelo.coef_[0][0]), float(modelo.intercept_[0])


def ajustar_calibracao(
    predicoes_val: dict[int, dict[str, float]], resultados_val: dict[int, int]
) -> dict[str, tuple[float, float]]:
    """Ajusta um Platt por seleção (one-vs-rest: home/draw/away cada um
    como problema binário independente), usando as predições CRUAS do
    modelo no Validation Set -- nunca no Train (senão a calibração só
    mediria overfitting do próprio modelo) nem no Test (senão deixaria de
    ser out-of-sample de verdade)."""
    coeficientes: dict[str, tuple[float, float]] = {}
    for selecao, codigo in CODIGO_POR_SELECAO.items():
        probs, alvo = [], []
        for match_id, p in predicoes_val.items():
            resultado_real = resultados_val.get(match_id)
            if resultado_real is None:
                continue
            probs.append(p[f"prob_{selecao}"])
            alvo.append(1.0 if resultado_real == codigo else 0.0)

        alvo_arr = np.array(alvo)
        if len(probs) < AMOSTRA_MINIMA or len(np.unique(alvo_arr)) < 2:
            coeficientes[selecao] = (1.0, 0.0)  # identidade -- amostra pequena/degenerada demais pra confiar
            continue
        coeficientes[selecao] = ajustar_platt_selecao(np.array(probs), alvo_arr)
    return coeficientes


def aplicar_calibracao(probs: dict[str, float], coeficientes: dict[str, tuple[float, float]]) -> dict[str, float]:
    """Aplica o Platt em cada seleção e RENORMALIZA pra somar 1 -- o ajuste
    one-vs-rest calibra cada seleção de forma independente, então a soma
    das 3 não vem garantida em 1 depois da transformação."""
    calibradas = {}
    for selecao in SELECOES:
        a, b = coeficientes.get(selecao, (1.0, 0.0))
        p = probs[f"prob_{selecao}"]
        z = a * _logit(np.array([p]))[0] + b
        calibradas[selecao] = float(1 / (1 + np.exp(-z)))
    total = sum(calibradas.values())
    return {f"prob_{selecao}": calibradas[selecao] / total for selecao in SELECOES}
