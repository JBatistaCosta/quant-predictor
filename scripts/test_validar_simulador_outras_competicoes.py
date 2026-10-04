"""Testes de scripts/validar_simulador_outras_competicoes.py (sem rede, sem os eventos completos)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import validar_simulador_outras_competicoes as v  # noqa: E402


def passe(time, loc, fim, tipo=None, resultado=None, seg=0):
    p = {"end_location": fim}
    if tipo:
        p["type"] = {"name": tipo}
    if resultado:
        p["outcome"] = {"name": resultado}
    return {"type": {"name": "Pass"}, "team": {"id": time}, "location": loc, "pass": p, "minute": 1, "second": seg, "period": 1, "timestamp": f"00:01:{seg:02d}.000"}


def test_media_e_erro_padrao():
    mu, ep = v.media_e_ep([2, 4, 6])
    assert mu == 4 and abs(ep - (4 / 3) ** 0.5 / 1) < 0.5 and v.media_e_ep([5]) == (5.0, 0.0)


def test_partida_observada_conta_classes_chutes_e_faixas():
    ev = [passe(1, [60, 40], [70, 40], seg=1), passe(1, [0, 0], [10, 40], tipo="Throw-in", seg=5),
          {"type": {"name": "Shot"}, "team": {"id": 1}, "location": [108, 40], "shot": {"statsbomb_xg": 0.3, "outcome": {"name": "Goal"}}, "minute": 1, "second": 9, "period": 1, "timestamp": "00:01:09.000"}]
    out = v.partida_observada(ev)
    assert out["m"]["acoes"] == 3 and out["m"]["laterais"] == 1 and out["m"]["chutes"] == 1 and out["m"]["gols"] == 1 and sum(out["m"]["faixa"]) == 3
