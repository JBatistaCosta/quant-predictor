"""Testes de scripts/analisar_forca_e_janela_statsbomb.py (sem rede)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analisar_forca_e_janela_statsbomb as f  # noqa: E402
import analisar_residuo_entradas_statsbomb as r  # noqa: E402


def passe(time, loc, fim, minuto=1, periodo=1, tipo=None, resultado=None):
    p = {"end_location": fim}
    if tipo:
        p["type"] = {"name": tipo}
    if resultado:
        p["outcome"] = {"name": resultado}
    return {"type": {"name": "Pass"}, "team": {"id": time}, "location": loc, "pass": p, "minute": minuto, "period": periodo}


def test_janela_de_15_minutos_dos_dois_tempos():
    assert r.janela_de_15min({"minute": 3, "period": 1}) == 0
    assert r.janela_de_15min({"minute": 44, "period": 1}) == 2
    assert r.janela_de_15min({"minute": 47, "period": 1}) == 2          # acréscimo do 1T fica na última janela do 1T
    assert r.janela_de_15min({"minute": 46, "period": 2}) == 3
    assert r.janela_de_15min({"minute": 91, "period": 2}) == 5


def test_times_na_ordem_do_starting_xi_e_contagem_de_gols_e_escanteios():
    ev = [{"type": {"name": "Starting XI"}, "team": {"id": 1, "name": "A"}}, {"type": {"name": "Starting XI"}, "team": {"id": 2, "name": "B"}},
          passe(1, [60, 40], [70, 40]), passe(1, [70, 40], [80, 40], resultado="Incomplete"),
          passe(2, [118, 0], [110, 40], tipo="Corner"),
          {"type": {"name": "Shot"}, "team": {"id": 2}, "location": [108, 40], "shot": {"outcome": {"name": "Goal"}}, "minute": 50, "period": 2}]
    assert f.times_da_partida(ev) == [(1, "A"), (2, "B")]
    res = f.contar_partida(ev)
    assert res["gols"][2] == 1 and res["escanteios"][2] == 1
    assert sum(sum(z[1] for z in res["por_time"][1]) for _ in [0]) == 1            # a perda do time 1
    assert sum(z[2] for z in res["por_time"][2]) == 1                               # o chute do time 2
    assert sum(sum(z[k] for z in res["por_janela"][w]) for w in range(6) for k in range(3)) == 4      # 4 linhas no total
    assert sum(z[1] for z in res["por_janela"][3]) == 0 and sum(z[2] for z in res["por_janela"][4]) == 0 and sum(z[2] for z in res["por_janela"][3]) == 1
