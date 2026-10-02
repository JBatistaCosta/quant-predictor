#!/usr/bin/env python3
"""Modelo de previsão 1X2 a partir do Elo global por xG (logit ordenado, 3 parâmetros).

O Elo por xG (`scripts/elo_global_xg.py`, tabelas `team_elo_xg`/`team_elo_xg_history`)
entrega UMA nota por time; o 1X2 precisa de TRÊS probabilidades. Este módulo é a
conversão, formalizada como modelo:

    x         = (Elo_casa - Elo_fora + h * [jogo não neutro]) / 400 * ln(10)
    P(casa)   = sigma( a * x - t)
    P(fora)   = sigma(-a * x - t)
    P(empate) = 1 - P(casa) - P(fora)            (exige t > 0)

  a = inclinação (≈ 1: a própria escala logística do Elo)
  h = vantagem de casa, em pontos de Elo
  t = largura da faixa de empate (maior t, mais empate; t = 0,6055 dá ~29% de empate
      entre times iguais em campo neutro)

Parâmetros PADRÃO ajustados por máxima verossimilhança em 9.687 jogos de 01/01/2017 a
31/07/2022 (réplica local do Elo por xG, sem sementes do ClubElo):
    a = 1,0149   h = 57,23 pontos   t = 0,6055

Desempenho FORA da amostra (18.705 jogos, 08/2022 a 09/2026; análise de 02/10/2026,
registrada em CONTEXTO_PROJETO.md): acurácia 51,0%, Brier (3 resultados) 0,5982,
RPS 0,2044, perda logarítmica 1,0011, 0,104 bit/jogo de informação (climatologia
0,007); AUC casa/empate/fora 0,684/0,552/0,690; calibração por resultado com
inclinação 1,02/1,00/1,04. Empata com a conversão quadrática anterior
(RPS -0,00007 +- 0,00008) e fica atrás da Pinnacle (RPS +0,0053 +- 0,0006).
O empate NUNCA é o palpite (P(empate) fica sempre abaixo da do favorito).

Este arquivo NÃO grava nada no banco e NÃO está ligado a nenhuma previsão de produção:
é a especificação testada do modelo + um comando para reajustar/avaliar com o banco.

Uso (só leitura):
    python scripts/elo_xg_tres_vias.py            # reajusta no treino padrão e avalia no teste
Variáveis de ambiente: SUPABASE_URL, SUPABASE_KEY.
"""

from __future__ import annotations

import json
import math
import os
import sys

import numpy as np

# Parâmetros ajustados (ver docstring). Reajustar com `ajustar` se o Elo por xG mudar.
PARAMS_PADRAO = {"a": 1.0149, "h": 57.23, "t": 0.6055}
# Janela de ajuste/teste usada na análise de 02/10/2026.
TREINO_INICIO, TREINO_FIM = "2017-01-01", "2022-08-01"
CASA, EMPATE, FORA = 0, 1, 2


def _sigmoide(z):
    return 1.0 / (1.0 + np.exp(-z))


def probabilidades_1x2(rating_casa, rating_fora, neutro=0, params: dict | None = None) -> np.ndarray:
    """Matriz (n, 3) com [P(casa), P(empate), P(fora)] por jogo. `neutro` = 1 em campo
    neutro (sem vantagem de casa). Aceita escalares ou vetores."""
    p = params or PARAMS_PADRAO
    rc = np.atleast_1d(np.asarray(rating_casa, dtype=float))
    rf = np.atleast_1d(np.asarray(rating_fora, dtype=float))
    ne = np.broadcast_to(np.asarray(neutro, dtype=float), rc.shape)
    x = (rc - rf + p["h"] * (1.0 - ne)) / 400.0 * math.log(10.0)
    p_casa = _sigmoide(p["a"] * x - p["t"])
    p_fora = _sigmoide(-p["a"] * x - p["t"])
    p_empate = np.clip(1.0 - p_casa - p_fora, 1e-6, 1.0)
    soma = p_casa + p_empate + p_fora
    return np.column_stack([p_casa / soma, p_empate / soma, p_fora / soma])


def ajustar(rating_casa, rating_fora, neutro, resultado, inicial: dict | None = None) -> tuple[dict, float]:
    """Máxima verossimilhança dos 3 parâmetros. `resultado`: 0 casa / 1 empate / 2 fora.
    Devolve (parâmetros, perda logarítmica média no treino)."""
    from scipy.optimize import minimize

    rc, rf = np.asarray(rating_casa, dtype=float), np.asarray(rating_fora, dtype=float)
    ne, y = np.asarray(neutro, dtype=float), np.asarray(resultado, dtype=int)
    ini = inicial or PARAMS_PADRAO

    def perda(v):
        if v[2] <= 0:
            return 1e6
        P = probabilidades_1x2(rc, rf, ne, {"a": v[0], "h": v[1], "t": v[2]})
        return float(-np.mean(np.log(np.clip(P[np.arange(len(y)), y], 1e-9, 1.0))))

    r = minimize(perda, x0=[ini["a"], ini["h"], ini["t"]], method="Nelder-Mead",
                 options={"xatol": 1e-4, "fatol": 1e-9, "maxiter": 4000})
    return {"a": float(r.x[0]), "h": float(r.x[1]), "t": float(r.x[2])}, float(r.fun)


# --------------------------------------------------------------------------- métricas
def _onehot(y: np.ndarray) -> np.ndarray:
    return np.eye(3)[np.asarray(y, dtype=int)]


def perda_logaritmica(P: np.ndarray, y) -> float:
    y = np.asarray(y, dtype=int)
    return float(-np.mean(np.log(np.clip(P[np.arange(len(y)), y], 1e-9, 1.0))))


def brier_3_resultados(P: np.ndarray, y) -> float:
    return float(np.mean(((P - _onehot(y)) ** 2).sum(axis=1)))


def rps(P: np.ndarray, y) -> float:
    """Ranked probability score (resultados ORDENADOS casa < empate < fora); menor é melhor."""
    O = _onehot(y)
    c = np.cumsum(P, axis=1)[:, :2]
    co = np.cumsum(O, axis=1)[:, :2]
    return float(np.mean(((c - co) ** 2).sum(axis=1) / 2.0))


def informacao_ganha_bits(P: np.ndarray, y, freq_base) -> float:
    """Entropia da frequência-base menos a perda logarítmica do modelo, em bits por jogo."""
    f = np.asarray(freq_base, dtype=float)
    h0 = float(-np.sum(f[f > 0] * np.log2(f[f > 0])))
    return h0 - perda_logaritmica(P, y) / math.log(2.0)


def auc_um_contra_todos(score, alvo) -> float:
    """AUC por postos (empates com posto médio) de `score` para o alvo binário."""
    s = np.asarray(score, dtype=float)
    t = np.asarray(alvo, dtype=int)
    n1, n0 = int(t.sum()), int((1 - t).sum())
    if n1 == 0 or n0 == 0:
        return float("nan")
    ordem = np.argsort(s, kind="mergesort")
    postos = np.empty(len(s), dtype=float)
    i = 0
    s_ord = s[ordem]
    while i < len(s):
        j = i
        while j < len(s) and s_ord[j] == s_ord[i]:
            j += 1
        postos[ordem[i:j]] = (i + 1 + j) / 2.0
        i = j
    return float((postos[t == 1].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0))


def erro_de_calibracao(prob, alvo, faixas: int = 10) -> float:
    """ECE: média ponderada de |probabilidade média - frequência observada| por faixa."""
    p, t = np.asarray(prob, dtype=float), np.asarray(alvo, dtype=float)
    b = np.minimum((p * faixas).astype(int), faixas - 1)
    total = 0.0
    for i in range(faixas):
        m = b == i
        if m.any():
            total += m.mean() * abs(p[m].mean() - t[m].mean())
    return float(total)


def avaliar(P: np.ndarray, y, freq_base) -> dict:
    """Pacote de índices de desempenho 1X2 (palpite = favorito entre casa e fora, via argmax)."""
    y = np.asarray(y, dtype=int)
    palpite = P.argmax(axis=1)
    revocacao = [float((palpite[y == k] == k).mean()) if (y == k).any() else float("nan") for k in range(3)]
    return {
        "n": int(len(y)),
        "acuracia": float((palpite == y).mean()),
        "acuracia_balanceada": float(np.nanmean(revocacao)),
        "revocacao_casa_empate_fora": revocacao,
        "brier_3_resultados": brier_3_resultados(P, y),
        "rps": rps(P, y),
        "perda_logaritmica": perda_logaritmica(P, y),
        "informacao_bits_por_jogo": informacao_ganha_bits(P, y, freq_base),
        "auc_casa": auc_um_contra_todos(P[:, CASA], (y == CASA).astype(int)),
        "auc_empate": auc_um_contra_todos(P[:, EMPATE], (y == EMPATE).astype(int)),
        "auc_fora": auc_um_contra_todos(P[:, FORA], (y == FORA).astype(int)),
        "ece_casa": erro_de_calibracao(P[:, CASA], (y == CASA).astype(float)),
        "ece_empate": erro_de_calibracao(P[:, EMPATE], (y == EMPATE).astype(float)),
        "ece_fora": erro_de_calibracao(P[:, FORA], (y == FORA).astype(float)),
    }


# --------------------------------------------------------------------------- execução
def _carregar(supabase):
    import pandas as pd

    from elo_global import _paginar

    partidas = _paginar(lambda: supabase.table("matches")
                        .select("id, match_date, home_team_id, away_team_id, home_goals, away_goals, is_neutral")
                        .eq("status", "finished"))
    historico = _paginar(lambda: supabase.table("team_elo_xg_history").select("match_id, team_id, rating_antes"))
    m = pd.DataFrame(partidas).dropna(subset=["home_goals", "away_goals"])
    e = pd.DataFrame(historico)
    m = m.merge(e.rename(columns={"match_id": "id", "team_id": "home_team_id", "rating_antes": "rh"}), on=["id", "home_team_id"])
    m = m.merge(e.rename(columns={"match_id": "id", "team_id": "away_team_id", "rating_antes": "ra"}), on=["id", "away_team_id"])
    m["d"] = pd.to_datetime(m["match_date"]).dt.strftime("%Y-%m-%d")
    m["neu"] = m["is_neutral"].fillna(False).astype(float)
    m["y"] = np.where(m.home_goals > m.away_goals, CASA, np.where(m.home_goals == m.away_goals, EMPATE, FORA))
    return m.sort_values(["d", "id"]).reset_index(drop=True)


def main() -> None:
    from supabase import create_client

    url, key = os.environ.get("SUPABASE_URL", "").strip(), os.environ.get("SUPABASE_KEY", "").strip()
    if not url or not key:
        sys.exit("Defina SUPABASE_URL e SUPABASE_KEY como variáveis de ambiente.")
    m = _carregar(create_client(url, key))
    tr = m[(m.d >= TREINO_INICIO) & (m.d < TREINO_FIM)]
    te = m[m.d >= TREINO_FIM]
    if tr.empty or te.empty:
        sys.exit("Janela de treino ou de teste vazia.")
    params, perda_treino = ajustar(tr.rh, tr.ra, tr.neu, tr.y)
    freq = tr.y.value_counts(normalize=True).reindex([CASA, EMPATE, FORA]).fillna(0.0).to_numpy()
    teste = avaliar(probabilidades_1x2(te.rh, te.ra, te.neu, params), te.y.to_numpy(), freq)
    clim = avaliar(np.tile(freq, (len(te), 1)), te.y.to_numpy(), freq)
    print(json.dumps({"parametros": params, "perda_logaritmica_treino": perda_treino, "n_treino": int(len(tr)),
                      "frequencia_base_treino": freq.tolist(), "teste": teste, "climatologia": clim},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
