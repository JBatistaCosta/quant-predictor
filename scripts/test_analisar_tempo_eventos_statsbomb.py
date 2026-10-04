"""Testes de scripts/analisar_tempo_eventos_statsbomb.py (sem rede)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analisar_tempo_eventos_statsbomb as a  # noqa: E402


def ev(tipo, periodo, minuto, segundo, **extra):
    e = {"type": {"name": tipo}, "team": {"id": 1}, "period": periodo, "minute": minuto, "second": segundo}
    e.update(extra)
    return e


def test_janelas_incluem_acrescimos_e_ignoram_prorrogacao():
    assert a.janela(1, 0) == 0 and a.janela(1, 14) == 0 and a.janela(1, 15) == 1 and a.janela(1, 44) == 2
    assert a.janela(1, 45) == 3 and a.janela(1, 47) == 3
    assert a.janela(2, 45) == 4 and a.janela(2, 59) == 4 and a.janela(2, 75) == 6 and a.janela(2, 89) == 6
    assert a.janela(2, 90) == 7 and a.janela(2, 94) == 7
    assert a.janela(3, 100) is None


def test_analisar_partida_conta_eventos_por_janela_e_duracoes():
    eventos = [
        ev("Pass", 1, 2, 10, duration=1.5, possession=1, **{"pass": {"end_location": [30, 40]}}),
        ev("Pass", 1, 3, 0, duration=2.0, possession=1, **{"pass": {"outcome": {"name": "Incomplete"}, "cross": True, "end_location": [100, 40]}}),
        ev("Shot", 2, 50, 0, duration=0.7, possession=2, shot={"outcome": {"name": "Goal"}}),
        ev("Foul Committed", 2, 95, 0),
        ev("Pass", 2, 91, 0, duration=1.0, possession=3, **{"pass": {"type": {"name": "Corner"}, "end_location": [110, 40]}}),
    ]
    r = a.analisar_partida(eventos)
    assert r["cont"]["0|passe"] == 2 and r["cont"]["0|perda"] == 1 and r["cont"]["0|cruzamento"] == 1
    assert r["cont"]["4|chute"] == 1 and r["cont"]["4|gol"] == 1
    assert r["cont"]["7|falta"] == 1 and r["cont"]["7|escanteio"] == 1
    assert sorted(r["dur"]["Pass"]) == [1.0, 1.5] and r["dur"]["Cruzamento"] == [2.0] and r["dur"]["Shot"] == [0.7]
    assert sorted(x[1] for x in r["posses"]) == [1, 1, 2]


def test_resumo_agrega_partidas():
    p = a.analisar_partida([ev("Shot", 1, 5, 0, duration=0.5, possession=7, shot={"outcome": {"name": "Saved"}})] * 30)
    res = {"jogos": 2, "partidas": [p, p]}
    r = a.resumo(res)
    assert r["totais_por_janela_e_evento"]["0|chute"] == 60 and r["duracao_acoes_s"]["Shot"]["n"] == 60
    assert r["posses_por_jogo"] == 1.0 and r["acoes_por_posse"]["mediana"] == 30


def test_matriz_no_tempo_janelas_e_faixas():
    import analisar_matriz_no_tempo_statsbomb as m
    assert [m.janela(1, x) for x in (0, 14, 15, 44, 45, 47)] == [0, 0, 1, 2, None, None]
    assert [m.janela(2, x) for x in (45, 59, 60, 89, 90, 95)] == [3, 3, 4, 5, None, None]
    assert m.janela(3, 100) is None
    assert [m.faixa_da_zona(z) for z in (0, 2, 3, 8, 9, 14, 15, 17)] == [0, 0, 1, 1, 2, 2, 3, 3]


def test_matriz_no_tempo_acoes_com_janela_so_dentro_dos_90_minutos():
    import analisar_matriz_no_tempo_statsbomb as m
    eventos = [
        {"type": {"name": "Pass"}, "team": {"id": 0}, "period": 1, "minute": 10, "second": 0, "location": [60, 40], "pass": {"end_location": [75, 40]}},
        {"type": {"name": "Shot"}, "team": {"id": 0}, "period": 2, "minute": 80, "second": 0, "location": [110, 40], "shot": {}},
        {"type": {"name": "Pass"}, "team": {"id": 0}, "period": 2, "minute": 93, "second": 0, "location": [60, 40], "pass": {"end_location": [75, 40]}},   # acréscimo: fora
    ]
    r = list(m.acoes_com_janela(eventos))
    assert [x[0] for x in r] == [0, 5] and r[0][3] > 0 and r[1][2] == 18 and r[1][3] is None
