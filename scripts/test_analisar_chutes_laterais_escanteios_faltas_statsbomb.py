"""Testes de scripts/analisar_chutes_laterais_escanteios_faltas_statsbomb.py (sem rede)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analisar_chutes_laterais_escanteios_faltas_statsbomb as a  # noqa: E402
import gerar_matriz_transicao_statsbomb as g  # noqa: E402


def ev(tipo, time_id, ts, pos, loc, padrao="Regular Play", posse_time=None, periodo=1, **extra):
    e = {"type": {"name": tipo}, "team": {"id": time_id}, "period": periodo, "timestamp": ts, "possession": pos,
         "possession_team": {"id": posse_time or time_id}, "play_pattern": {"name": padrao}, "location": list(loc)}
    e.update(extra)
    return e


def partida():
    return [
        # chute defendido seguido de escanteio (nova posse "From Corner" do mesmo time) e chute de cabeça depois do escanteio
        ev("Shot", 1, "00:10:00.000", 1, (108, 40), shot={"outcome": {"name": "Saved"}, "statsbomb_xg": 0.2, "body_part": {"name": "Right Foot"}, "type": {"name": "Open Play"}}),
        ev("Pass", 1, "00:10:40.000", 2, (120, 0), padrao="From Corner", pass_={}, **{"pass": {"type": {"name": "Corner"}, "end_location": [112, 40], "technique": {"name": "Inswinging"}}}),
        ev("Shot", 1, "00:10:44.000", 2, (112, 38), padrao="From Corner", shot={"outcome": {"name": "Goal"}, "statsbomb_xg": 0.3, "body_part": {"name": "Head"}, "type": {"name": "Open Play"}}),
        # lateral completo e passe fora
        ev("Pass", 2, "00:20:00.000", 3, (50, 0), padrao="From Throw In", **{"pass": {"type": {"name": "Throw-in"}, "end_location": [60, 20], "length": 12.0}}),
        ev("Pass", 2, "00:21:00.000", 4, (60, 30), **{"pass": {"outcome": {"name": "Out"}, "end_location": [70, 0]}}),
        # falta do time 2 em (30, 40) no referencial dele: espelhada = (90, 40) no referencial do time 1
        ev("Foul Committed", 2, "00:30:00.000", 5, (30, 40), posse_time=1, foul_committed={"card": {"name": "Yellow Card"}}),
    ]


def test_chute_defendido_vira_escanteio_da_mesma_equipe_e_o_escanteio_gera_gol():
    r = a.analisar_partida(partida())
    z, res, xg, cabeca, tipo, prox, mesmo = r["chutes"][0]
    assert z == g.zona_18(*g.para_metros(108, 40)) and res == "Saved" and prox == "From Corner" and mesmo is True and not cabeca
    assert r["chutes"][1][3] is True and r["chutes"][1][1] == "Goal"        # o segundo chute é de cabeça e gol
    esc = r["escanteios"][0]
    assert esc[0] == g.zona_18(*g.para_metros(112, 40)) and esc[1] is True and esc[2] == "Inswinging" and esc[3] == 0
    assert esc[4] == (0.3, True)                                              # escanteio gerou um chute de gol com xG 0,3


def test_lateral_origem_destino_e_completo():
    r = a.analisar_partida(partida())
    assert r["laterais"] == [(g.zona_18(*g.para_metros(50, 0)), g.zona_18(*g.para_metros(60, 20)), True, 12.0)]


def test_passe_fora_e_falta_em_zona_espelhada():
    r = a.analisar_partida(partida())
    assert r["fora"] == [g.zona_18(*g.para_metros(60, 30))]
    assert r["faltas"] == [(g.zona_18(*g.para_metros(90, 40)), "Yellow Card", False)]
    assert a.zona_espelhada((30, 40)) == g.zona_18(*g.para_metros(90, 40))


def test_pct():
    assert a.pct(1, 4) == 25.0 and a.pct(1, 0) == 0.0


def test_falta_de_ataque_nao_e_espelhada():
    eventos = [ev("Foul Committed", 1, "00:05:00.000", 1, (100, 40), posse_time=1, foul_committed={})]      # quem faz a falta é a equipe da posse
    r = a.analisar_partida(eventos)
    assert r["faltas"] == [(g.zona_18(*g.para_metros(100, 40)), None, True)]
