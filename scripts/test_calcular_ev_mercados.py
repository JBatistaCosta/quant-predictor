#!/usr/bin/env python3
"""Testes da esteira de EV 1X2 (`calcular_ev_mercados.py`) e da auditoria
(`arquivos_do_claude/analisar_calibracao_ev.py`). Roda com `pytest scripts/test_calcular_ev_mercados.py -v`."""

from __future__ import annotations

import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "arquivos_do_claude"))
import analisar_calibracao_ev as aud
import calcular_ev_mercados as ev


def test_ev_e_a_formula_p_vezes_odd_menos_1():
    assert math.isclose(float(ev.calcular_ev(0.5, 2.2)), 0.10)
    assert float(ev.calcular_ev(0.4, 2.0)) < 0


def test_desvigar_soma_um_e_preserva_proporcao():
    p = ev.desvigar([2.0, 3.5, 4.0])
    assert math.isclose(p.sum(), 1.0)
    assert math.isclose(p[0] / p[1], 3.5 / 2.0)


def test_kelly_fracionario_usa_fator_025_e_zera_sem_valor():
    # p=0.5, odd=2.2: kelly cheio = (0.5*2.2-1)/1.2 = 0.0833; com fator 0.25 = 0.02083
    assert math.isclose(float(ev.stake_kelly_fracionario(0.5, 2.2)), 0.25 * 0.10 / 1.2)
    assert float(ev.stake_kelly_fracionario(0.4, 2.0)) == 0.0


def test_sanity_gate_marca_ev_acima_de_35_pct_overround_estranho_e_odd_invalida():
    ok = [2.0, 3.4, 4.2]  # overround ~1.03
    assert ev.motivos_anomalia(0.34, ok) == ""
    assert "ev>35%" in ev.motivos_anomalia(0.36, ok)
    assert "overround" in ev.motivos_anomalia(0.05, [3.0, 4.0, 5.0])  # < 1,00 (arbitragem)
    assert "overround" in ev.motivos_anomalia(0.05, [1.5, 2.5, 3.0])  # > 1,12
    assert "odd<=1.01" in ev.motivos_anomalia(0.05, [1.01, 9.0, 20.0])


def _jogos(y):
    return pd.DataFrame({"partida_id": range(1, len(y) + 1), "liga_codigo": "XXX", "temporada": "2024",
                         "d": "2024-03-01", "y": y})


def test_registro_campos_retorno_clv_e_aposta_so_com_ev_nao_negativo():
    jogos = _jogos([0])  # casa venceu
    probs = np.array([[0.50, 0.28, 0.22]])
    o_in = np.array([[2.20, 3.50, 3.60]])           # EV casa +10%, empate -2%, fora -20.8%
    o_fc = np.array([[2.00, 3.60, 4.20]])
    df = ev.montar_apostas(jogos, probs, o_in, o_fc, np.array([True]))
    assert list(df.selecao) == ["home"]              # só EV >= 0 vira aposta
    r = df.iloc[0]
    assert math.isclose(r.ev_estimado, 0.10) and r.resultado_real == 1
    assert math.isclose(r.retorno_unidade, 1.20) and r.stake_flat == 1.0
    justa = 1 / ev.desvigar(o_fc[0])[0]
    assert math.isclose(r.odd_fechamento_justa, justa) and math.isclose(r.clv, 2.20 / justa - 1)
    assert r.odd_fechamento_pinnacle == 2.00 and not r.anomalia_dados


def test_perda_rende_menos_um_e_sem_fechamento_nao_ha_clv():
    df = ev.montar_apostas(_jogos([2]), np.array([[0.50, 0.28, 0.22]]), np.array([[2.20, 3.50, 3.60]]),
                           np.full((1, 3), np.nan), np.array([False]))
    assert df.retorno_unidade.iloc[0] == -1.0 and np.isnan(df.clv.iloc[0]) and np.isnan(df.odd_fechamento_justa.iloc[0])
    assert not df.entrada_verificada_por_timestamp.iloc[0]


def test_ev_acima_de_35_pct_vira_anomalia_mas_continua_registrado():
    df = ev.montar_apostas(_jogos([0]), np.array([[0.70, 0.20, 0.10]]), np.array([[2.20, 3.50, 3.60]]),
                           np.full((1, 3), np.nan), np.array([False]))
    assert df.anomalia_dados.iloc[0] and "ev>35%" in df.motivo_anomalia.iloc[0]


def test_partida_sem_odds_de_entrada_nao_gera_aposta():
    df = ev.montar_apostas(_jogos([0]), np.array([[0.5, 0.3, 0.2]]), np.full((1, 3), np.nan), np.full((1, 3), np.nan), np.array([False]))
    assert df.empty


def _partidas_sinteticas(n=3000, seed=3):
    rng = np.random.default_rng(seed)
    rh, ra = rng.normal(1500, 100, n), rng.normal(1500, 100, n)
    P = ev.modelo.probabilidades_1x2(rh, ra, 0)
    u = rng.random(n)
    y = np.where(u < P[:, 0], 0, np.where(u < P[:, 0] + P[:, 1], 1, 2))
    datas = pd.date_range("2017-01-01", periods=n, freq="D").strftime("%Y-%m-%d")
    return pd.DataFrame({"d": datas, "rh": rh, "ra": ra, "neu": 0.0, "y": y})


def test_walk_forward_nao_usa_resultados_do_periodo_apostado():
    """Trocar os resultados de TODAS as partidas do 1º período apostado não pode mudar
    as probabilidades desse período (os parâmetros só enxergam o passado)."""
    m = _partidas_sinteticas()
    inicio, fim = "2023-01-01", "2024-01-01"
    idx, probs, _ = ev.probabilidades_walk_forward(m, inicio)
    m2 = m.copy()
    no_periodo = (m2.d >= inicio) & (m2.d < fim)
    m2.loc[no_periodo, "y"] = 2 - m2.loc[no_periodo, "y"]  # inverte casa/fora no período
    idx2, probs2, _ = ev.probabilidades_walk_forward(m2, inicio)
    primeiro = np.isin(idx, m.index[no_periodo])
    assert np.allclose(probs[primeiro], probs2[np.isin(idx2, m.index[no_periodo])])
    assert not np.allclose(probs[~primeiro], probs2[~np.isin(idx2, m.index[no_periodo])])  # períodos seguintes enxergam o período trocado


def test_walk_forward_recusa_treino_pequeno():
    m = _partidas_sinteticas(n=3000).iloc[:1500]
    try:
        ev.probabilidades_walk_forward(m, "2017-02-01")
    except ValueError:
        return
    raise AssertionError("deveria recusar treino insuficiente")


# --------------------------------------------------------------------------- auditoria
def _apostas(n=400, seed=1, ev_real_fator=1.0):
    rng = np.random.default_rng(seed)
    p = rng.uniform(0.3, 0.6, n)
    odd = (1 + rng.uniform(0.0, 0.2, n)) / p              # EV entre 0 e 20%
    ganhou = (rng.random(n) < p * 1.0).astype(int)
    return pd.DataFrame({"partida_id": np.arange(n), "prob_modelo": p, "odd_entrada": odd, "ev_estimado": p * odd - 1,
                         "resultado_real": ganhou, "retorno_unidade": np.where(ganhou == 1, odd - 1, -1.0),
                         "stake_flat": 1.0, "clv": np.where(rng.random(n) < 0.2, rng.normal(0, 0.02, n), np.nan),
                         "anomalia_dados": False})


def test_baldes_cobrem_todas_as_apostas_e_roi_bate_com_conta_manual():
    df = _apostas()
    b = aud.estratificar(df)
    assert b.n.sum() == len(df)
    f = b[b.n > 0].iloc[0]
    sub = df[(df.ev_estimado >= 0) & (df.ev_estimado < 0.02)]
    assert math.isclose(f.roi, sub.retorno_unidade.sum() / len(sub))
    assert math.isclose(f.o_sobre_e, sub.resultado_real.sum() / sub.prob_modelo.sum())


def test_modelo_calibrado_da_beta_perto_de_1_e_retorno_medio_nao_significativo():
    df = _apostas(n=60000, seed=5)          # p é a probabilidade verdadeira -> EV é honesto
    res = aud.auditar(df)
    assert abs(res["regressao_apostas"]["beta"] - 1) < 0.5
    assert res["regressao_apostas"]["p_beta_igual_1"] > 0.01
    assert abs(res["teste_t"]["retorno_medio"] - df.ev_estimado.mean()) < 0.02


def test_apostas_anomalas_sao_excluidas_por_padrao():
    df = _apostas(n=200)
    df.loc[:19, "anomalia_dados"] = True
    assert aud.auditar(df)["n_analisado"] == 180
    assert aud.auditar(df, incluir_anomalias=True)["n_analisado"] == 200


def test_teste_t_confere_com_formula_e_p_unilateral():
    df = pd.DataFrame({"partida_id": range(5), "retorno_unidade": [1.0, -1.0, 1.0, 1.0, -1.0]})
    t = aud.teste_t_retorno(df)
    esperado = 0.2 / (np.std([1, -1, 1, 1, -1], ddof=1) / math.sqrt(5))
    assert math.isclose(t["t"], esperado) and 0 < t["p_unilateral"] < 0.5
    assert aud.teste_t_retorno(pd.DataFrame({"partida_id": [1], "retorno_unidade": [1.0]}))["n"] == 1


def test_regressao_de_baldes_recupera_reta_exata():
    baldes = pd.DataFrame({"n": [10] * 6, "ev_medio": [0.01, 0.03, 0.05, 0.08, 0.12, 0.2]})
    baldes["roi"] = 0.5 * baldes.ev_medio - 0.01
    r = aud.regressao_baldes(baldes)
    assert math.isclose(r["beta"], 0.5) and math.isclose(r["alpha"], -0.01) and math.isclose(r["r2"], 1.0)
    assert r["gl"] == 4


def test_svg_e_json_serializavel():
    res = aud.auditar(_apostas())
    svg = aud.gerar_svg(res)
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    import json
    json.dumps(res)
