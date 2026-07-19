#!/usr/bin/env python3
"""Calibração (Platt e Isotonic Regression, one-vs-rest) pros modelos do
Model Benchmarking.

Mesmas duas técnicas já validadas em produção neste projeto
(`model_calibration`, achado documentado em CONTEXTO_PROJETO.md:
coeficiente `a` do Platt sempre < 1, confirmando que os modelos crus
tendem a ser confiantes demais; Isotonic com resultado misto -- ajuda em
alguns casos, mas com amostra pequena é mais propensa a overfit que o
Platt, que só tem 2 parâmetros). As duas ficam disponíveis lado a lado
(`ajustar_calibracao(..., metodo="platt"|"isotonic")`) -- a decisão de
qual usar em produção é sempre comparada empiricamente (`backtest_kelly.py`),
nunca assumida.

Cada modelo ganha DUAS variantes "conjugadas" (`{nome}_calibrado_platt`,
`{nome}_calibrado_isotonic`), persistidas lado a lado com a versão crua em
`predicoes`.

Regra que não pode ser quebrada: a calibração é sempre ajustada num
conjunto de dado que o modelo BASE não usou pra treinar (aqui, o
Validation Set) -- calibrar em cima do próprio treino inflaria a confiança
artificialmente e não mediria nada de real.
"""

from __future__ import annotations

from typing import Protocol

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

SELECOES = ("home", "draw", "away")
CODIGO_POR_SELECAO = {"home": 0, "draw": 1, "away": 2}
METODOS = ("platt", "isotonic")
AMOSTRA_MINIMA = 30  # abaixo disso, o ajuste é ruído -- usa identidade (sem calibrar)


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


class Calibrador(Protocol):
    """Interface comum -- `aplicar_calibracao` não precisa saber qual dos
    dois métodos está por trás."""

    def aplicar(self, p: float) -> float: ...


class CalibradorIdentidade:
    """Fallback quando a amostra de validação é pequena/degenerada demais
    (`AMOSTRA_MINIMA`) pra confiar num ajuste -- devolve a probabilidade
    crua sem alterar."""

    def aplicar(self, p: float) -> float:
        return p


class CalibradorPlatt:
    """`sigmoid(a*logit(p)+b)` -- só 2 parâmetros, menos propenso a overfit
    com amostra pequena que o Isotonic."""

    def __init__(self, a: float, b: float):
        self.a = a
        self.b = b

    def aplicar(self, p: float) -> float:
        z = self.a * _logit(np.array([p]))[0] + self.b
        return float(1 / (1 + np.exp(-z)))


class CalibradorIsotonic:
    """Regressão isotônica (Pool Adjacent Violators) -- mapeamento monótono
    não-paramétrico, mais flexível que o Platt (não assume a forma
    sigmoide), mas com mais parâmetros efetivos -- mais sensível a
    overfitting quando a amostra de validação é pequena."""

    def __init__(self, modelo: IsotonicRegression):
        self.modelo = modelo

    def aplicar(self, p: float) -> float:
        return float(self.modelo.predict([p])[0])


def _ajustar_platt(probs: np.ndarray, alvo_binario: np.ndarray) -> CalibradorPlatt:
    x = _logit(probs).reshape(-1, 1)
    modelo = LogisticRegression(solver="lbfgs")
    modelo.fit(x, alvo_binario)
    return CalibradorPlatt(float(modelo.coef_[0][0]), float(modelo.intercept_[0]))


def _ajustar_isotonic(probs: np.ndarray, alvo_binario: np.ndarray) -> CalibradorIsotonic:
    modelo = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    modelo.fit(probs, alvo_binario)
    return CalibradorIsotonic(modelo)


_AJUSTADORES = {"platt": _ajustar_platt, "isotonic": _ajustar_isotonic}


def ajustar_calibracao(
    predicoes_val: dict[int, dict[str, float]],
    resultados_val: dict[int, int],
    metodo: str = "platt",
    codigo_por_selecao: dict[str, int] | None = None,
) -> dict[str, Calibrador]:
    """Ajusta um calibrador por seleção (one-vs-rest: cada seleção como
    problema binário independente), usando as predições CRUAS do modelo no
    Validation Set -- nunca no Train (senão a calibração só mediria
    overfitting do próprio modelo) nem no Test (senão deixaria de ser
    out-of-sample de verdade). `codigo_por_selecao` default é 1X2
    (`CODIGO_POR_SELECAO`) -- outros mercados (ex.: Over/Under 2.5,
    `{"under": 0, "over": 1}`) passam o próprio mapeamento, mesma mecânica
    one-vs-rest funciona igual pra 2 ou 3 seleções."""
    if metodo not in _AJUSTADORES:
        raise ValueError(f"método de calibração desconhecido: {metodo!r} (esperado um de {METODOS})")
    ajustar = _AJUSTADORES[metodo]
    mapeamento = codigo_por_selecao or CODIGO_POR_SELECAO

    calibradores: dict[str, Calibrador] = {}
    for selecao, codigo in mapeamento.items():
        probs, alvo = [], []
        for match_id, p in predicoes_val.items():
            resultado_real = resultados_val.get(match_id)
            if resultado_real is None:
                continue
            probs.append(p[f"prob_{selecao}"])
            alvo.append(1.0 if resultado_real == codigo else 0.0)

        alvo_arr = np.array(alvo)
        if len(probs) < AMOSTRA_MINIMA or len(np.unique(alvo_arr)) < 2:
            calibradores[selecao] = CalibradorIdentidade()
            continue
        calibradores[selecao] = ajustar(np.array(probs), alvo_arr)
    return calibradores


def aplicar_calibracao(
    probs: dict[str, float], calibradores: dict[str, Calibrador], selecoes: tuple[str, ...] | None = None
) -> dict[str, float]:
    """Aplica o calibrador de cada seleção e RENORMALIZA pra somar 1 -- o
    ajuste one-vs-rest calibra cada seleção de forma independente, então a
    soma não vem garantida em 1 depois da transformação. `selecoes` default
    é 1X2 (`SELECOES`); outros mercados passam a própria tupla (ex.:
    `("under", "over")`)."""
    quais_selecoes = selecoes or SELECOES
    calibradas = {}
    for selecao in quais_selecoes:
        calibrador = calibradores.get(selecao, CalibradorIdentidade())
        calibradas[selecao] = calibrador.aplicar(probs[f"prob_{selecao}"])
    total = sum(calibradas.values())
    return {f"prob_{selecao}": calibradas[selecao] / total for selecao in quais_selecoes}
