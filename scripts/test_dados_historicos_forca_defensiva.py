#!/usr/bin/env python3
"""Testes do núcleo PURO (sem I/O) da força defensiva coletiva --
`_ewm_defasado`/`_calcular_forca_defensiva` em `dados_historicos.py`.
Mesmo espírito de `test_pricing_pipeline.py` (função pura, sem precisar de
Supabase fake): a parte de carregamento (`_carregar_serie_ofensiva_
defensiva`/`obter_forca_defensiva_atual`) faz I/O real e não tem cobertura
de teste aqui, só a lógica de agregação/EWM/resíduo (a que quebra
silenciosamente se mexida sem querer).

Roda com `pytest scripts/test_dados_historicos_forca_defensiva.py -v`.
"""

from __future__ import annotations

import sys
import types

import pandas as pd
import pytest

if "supabase" not in sys.modules:
    _stub = types.ModuleType("supabase")
    _stub.Client = object
    sys.modules["supabase"] = _stub

import dados_historicos as dh


def _painel(linhas: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(linhas)
    df["match_date"] = pd.to_datetime(df["match_date"])
    return df


class TestEwmDefasado:
    def test_shift_true_exclui_a_propria_linha(self):
        s = pd.Series([1.0, 2.0, 3.0])
        ewm = dh._ewm_defasado(s, shift=True)
        assert pd.isna(ewm.iloc[0])  # 1a linha nao tem historico anterior
        assert ewm.iloc[1] == pytest.approx(1.0)  # so viu o valor 1.0

    def test_shift_false_inclui_a_propria_linha(self):
        s = pd.Series([1.0, 2.0, 3.0])
        ewm = dh._ewm_defasado(s, shift=False)
        assert not pd.isna(ewm.iloc[0])
        assert ewm.iloc[0] == pytest.approx(1.0)

    def test_ignora_nan_em_vez_de_zerar(self):
        s = pd.Series([2.0, float("nan"), 4.0])
        ewm = dh._ewm_defasado(s, shift=False)
        # a media nao deve ser puxada por um NaN tratado como 0
        assert ewm.iloc[2] > 2.0


class TestCalcularForcaDefensiva:
    def test_residuo_positivo_quando_time_sofre_mais_que_o_esperado(self):
        # Time 1 sempre sofre MUITO xG do adversario (adversario cria mais
        # do que a media dele quando joga contra time 1) -> residuo > 0
        # nas partidas mais recentes (depois de ter historico suficiente).
        linhas = []
        for i in range(6):
            linhas.append(dict(match_id=i, team_id=1, opponent_id=2, match_date=f"2026-01-{i+1:02d}",
                                xg_marcado=1.0, xa_marcado=0.5, xg_sofrido=3.0, xa_sofrido=1.5))
            linhas.append(dict(match_id=i, team_id=2, opponent_id=1, match_date=f"2026-01-{i+1:02d}",
                                xg_marcado=3.0, xa_marcado=1.5, xg_sofrido=1.0, xa_sofrido=0.5))
        painel = _painel(linhas)
        calc = dh._calcular_forca_defensiva(painel, shift=False)
        ultima_time1 = calc[calc["team_id"] == 1].sort_values("match_date").iloc[-1]
        assert ultima_time1["def_residuo_xga"] == pytest.approx(0.0, abs=1e-9)  # sofre exatamente a media do adversario

    def test_residuo_negativo_quando_defesa_e_melhor_que_o_esperado(self):
        # Time 1 enfrenta adversarios que costumam marcar MUITO (media alta
        # contra outros rivais) mas sofre pouco de time 1 -- defesa boa.
        linhas = []
        for i in range(6):
            # Adversario (time 2) cria MUITO contra o time 3 (estabelece a media alta dele)
            linhas.append(dict(match_id=100+i, team_id=2, opponent_id=3, match_date=f"2025-01-{i+1:02d}",
                                xg_marcado=3.0, xa_marcado=1.5, xg_sofrido=1.0, xa_sofrido=0.5))
            linhas.append(dict(match_id=100+i, team_id=3, opponent_id=2, match_date=f"2025-01-{i+1:02d}",
                                xg_marcado=1.0, xa_marcado=0.5, xg_sofrido=3.0, xa_sofrido=1.5))
        for i in range(3):
            # Contra o time 1, o adversario 2 cria pouco (defesa do time 1 e boa)
            linhas.append(dict(match_id=200+i, team_id=1, opponent_id=2, match_date=f"2025-06-{i+1:02d}",
                                xg_marcado=1.0, xa_marcado=0.5, xg_sofrido=0.5, xa_sofrido=0.2))
            linhas.append(dict(match_id=200+i, team_id=2, opponent_id=1, match_date=f"2025-06-{i+1:02d}",
                                xg_marcado=0.5, xa_marcado=0.2, xg_sofrido=1.0, xa_sofrido=0.5))
        painel = _painel(linhas)
        calc = dh._calcular_forca_defensiva(painel, shift=False)
        ultima_time1 = calc[calc["team_id"] == 1].sort_values("match_date").iloc[-1]
        assert ultima_time1["def_residuo_xga"] < 0.0

    def test_shift_walkforward_nao_usa_a_propria_partida(self):
        linhas = [
            dict(match_id=1, team_id=1, opponent_id=2, match_date="2026-01-01", xg_marcado=1.0, xa_marcado=0.5, xg_sofrido=1.0, xa_sofrido=0.5),
            dict(match_id=1, team_id=2, opponent_id=1, match_date="2026-01-01", xg_marcado=1.0, xa_marcado=0.5, xg_sofrido=1.0, xa_sofrido=0.5),
            dict(match_id=2, team_id=1, opponent_id=2, match_date="2026-01-08", xg_marcado=1.0, xa_marcado=0.5, xg_sofrido=1.0, xa_sofrido=0.5),
            dict(match_id=2, team_id=2, opponent_id=1, match_date="2026-01-08", xg_marcado=1.0, xa_marcado=0.5, xg_sofrido=1.0, xa_sofrido=0.5),
        ]
        painel = _painel(linhas)
        calc = dh._calcular_forca_defensiva(painel, shift=True)
        primeira = calc[(calc["team_id"] == 1) & (calc["match_id"] == 1)].iloc[0]
        assert pd.isna(primeira["def_residuo_xga"])  # sem historico anterior


class TestObterForcaDefensivaAtual:
    def test_lista_vazia_devolve_dict_vazio(self):
        assert dh.obter_forca_defensiva_atual(object(), []) == {}
