"""Testes de scripts/analisar_reinicios_e_recuperacoes_statsbomb.py (sem rede)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analisar_reinicios_e_recuperacoes_statsbomb as a  # noqa: E402
import gerar_matriz_transicao_statsbomb as g  # noqa: E402


def ev(tipo, time_id, ts, pos, padrao="Regular Play", loc=(60, 40), dur=None, periodo=1, **extra):
    e = {"type": {"name": tipo}, "team": {"id": time_id}, "period": periodo, "minute": int(ts.split(":")[1]), "second": int(float(ts.split(":")[2])),
         "timestamp": ts, "possession": pos, "play_pattern": {"name": padrao}, "location": list(loc)}
    if dur is not None:
        e["duration"] = dur
    e.update(extra)
    return e


def partida():
    return [
        # perda em jogo: passe incompleto do time 1 (acaba em 10:02.0) e recuperação do time 2 às 10:05.0 -> atraso de 3,0 s
        ev("Pass", 1, "00:10:00.000", 1, dur=2.0, **{"pass": {"outcome": {"name": "Incomplete"}, "end_location": [70, 40]}}),
        ev("Pressure", 2, "00:10:01.000", 1),
        ev("Ball Recovery", 2, "00:10:05.000", 2, loc=(50, 40)),
        # bola fora: passe do time 1 para fora (acaba em 20:01.5), lateral do time 2 às 20:30.0 -> tempo morto de 28,5 s
        ev("Pass", 1, "00:20:00.000", 3, dur=1.5, **{"pass": {"outcome": {"name": "Out"}, "end_location": [80, 0]}}),
        ev("Substitution", 2, "00:20:10.000", 3),
        ev("Pass", 2, "00:20:30.000", 4, padrao="From Throw In", loc=(40, 0), dur=1.0, **{"pass": {"type": {"name": "Throw-in"}, "end_location": [45, 20]}}),
    ]


def test_tempo_morto_do_lateral_ignora_ruido_e_desconta_a_duracao_do_ultimo_evento():
    r = a.analisar_partida(partida())
    assert r["mortos"] == [("From Throw In", "Pass/Out", 28.5)]


def test_recuperacao_em_jogo_e_reinicio_depois_de_uma_perda():
    r = a.analisar_partida(partida())
    assert len(r["perdas"]) == 2
    zona_perda0, tipo0, cat0, lag0, zona_adv0 = r["perdas"][0]
    assert (tipo0, cat0) == ("Ball Recovery", "recuperação em jogo") and abs(lag0 - 3.0) < 1e-9
    assert zona_perda0 == g.zona_18(*g.para_metros(60, 40)) and zona_adv0 == g.zona_18(*g.para_metros(50, 40))
    assert r["perdas"][1][1:3] == ("Pass", "reinício: Throw-in") and abs(r["perdas"][1][3] - 28.5) < 1e-9


def test_segundos_e_fim_do_evento():
    e = {"timestamp": "00:03:10.250", "duration": 1.5}
    assert abs(a.segundos(e) - 190.25) < 1e-9 and abs(a.fim_do_evento(e) - 191.75) < 1e-9


def test_quantis():
    q = a.quantis([1.0, 2.0, 3.0, 4.0, 100.0])
    assert q["n"] == 5 and q["mediana"] == 3.0 and q["soma"] == 110.0
