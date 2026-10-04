"""Testes das funções puras de scripts/backtest_simulador_preditivo.py (sem rede, sem simular)."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_simulador_preditivo as bt  # noqa: E402

CFG = {"janela": 3, "minimo_jogos": 2, "expoente": 1.0, "piso": 0.85, "teto": 1.25}


def base(*linhas):                                      # (data, reais, simulados)
    return [(i, d, r, s) for i, (d, r, s) in enumerate(linhas)]


def test_fator_movel_usa_so_jogos_estritamente_anteriores():
    b = base(("2025-01-01", 3, 2.5), ("2025-01-02", 3, 2.5), ("2025-01-03", 100, 1))
    assert abs(bt.fator_gols_movel(b, "2025-01-03", CFG) - 6 / 5) < 1e-9          # o jogo de 01-03 (e o do mesmo dia) não entra
    assert abs(bt.fator_gols_movel(b, "2025-01-04", CFG) - 1.25) < 1e-9           # com o de 01-03 a razão explode e é limitada ao teto


def test_fator_movel_sem_informacao_suficiente_e_janela_curta():
    b = base(("2025-01-01", 3, 2.0), ("2025-01-02", 3, 3.0), ("2025-01-03", 2, 4.0), ("2025-01-04", 2, 4.0))
    assert bt.fator_gols_movel(b, "2025-01-01", CFG) == 1.0                       # nenhum jogo anterior
    assert bt.fator_gols_movel(b[:1], "2025-01-05", CFG) == 1.0                   # só 1 anterior (< minimo_jogos)
    assert abs(bt.fator_gols_movel(b, "2025-01-05", CFG) - max(7 / 11, 0.85)) < 1e-9   # janela de 3: ignora o primeiro jogo; piso 0,85


def test_ler_base_gols_junta_arquivos_e_gols_reais():
    jogos = [{"id": 1, "hg": 2, "ag": 1}, {"id": 2, "hg": 0, "ag": 0}]
    with tempfile.TemporaryDirectory() as d:
        a, b = os.path.join(d, "a.json"), os.path.join(d, "b.json")
        json.dump({"exp_forca": [[1, "2025-01-01", 2.7]]}, open(a, "w"))
        json.dump({"exp_forca": [[2, "2025-01-02", 2.9], [99, "2025-01-03", 3.0]]}, open(b, "w"))      # id 99 não está no CSV: ignorado
        assert bt.ler_base_gols([a, b], "exp_forca", jogos) == [(1, "2025-01-01", 3, 2.7), (2, "2025-01-02", 0, 2.9)]
