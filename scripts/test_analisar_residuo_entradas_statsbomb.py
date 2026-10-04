"""Testes de scripts/analisar_residuo_entradas_statsbomb.py (sem rede)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analisar_residuo_entradas_statsbomb as r  # noqa: E402


def passe(time, loc, fim, tipo=None, resultado=None):
    p = {"end_location": fim}
    if tipo:
        p["type"] = {"name": tipo}
    if resultado:
        p["outcome"] = {"name": resultado}
    return {"type": {"name": "Pass"}, "team": {"id": time}, "location": loc, "pass": p}


def test_classes_de_entrada():
    ev = [
        passe(1, [60, 40], [70, 40]),                              # recuperação (primeira linha sem anterior)
        passe(1, [70, 40], [80, 40]),                              # continua (anterior da mesma equipe terminou em continua)
        passe(1, [80, 40], [90, 40], resultado="Incomplete"),      # continua e termina em perda
        passe(2, [30, 40], [40, 40]),                              # recuperação do adversário
        passe(2, [0, 0], [10, 40], tipo="Throw-in"),               # lateral: classe própria, mesmo depois de lance da mesma equipe
        passe(2, [10, 40], [20, 40], tipo="Goal Kick"),
        {"type": {"name": "Shot"}, "team": {"id": 2}, "location": [108, 40], "shot": {"type": {"name": "Penalty"}}},
    ]
    assert [x[0] for x in r.linhas_da_partida(ev)] == ["recuperação", "continua", "continua", "recuperação", "lateral", "tiro de meta", "pênalti"]


def test_eventos_sem_linha_sao_ignorados():
    ev = [{"type": {"name": "Pressure"}, "team": {"id": 1}, "location": [50, 40]}, passe(1, [60, 40], [70, 40])]
    assert len(r.linhas_da_partida(ev)) == 1


def test_classe_do_lance_so_para_bola_parada():
    assert r.classe_do_lance(passe(1, [1, 1], [2, 2], tipo="Corner")) == "escanteio"
    assert r.classe_do_lance(passe(1, [1, 1], [2, 2], tipo="Recovery")) is None


def test_nucleo_pos_perda_distingue_mesma_e_adversario_e_classe():
    ev = [
        passe(1, [60, 40], [90, 40], resultado="Incomplete"),     # perda
        passe(2, [30, 40], [40, 40]),                             # adversário, jogo corrido -> recuperação
        passe(2, [0, 0], [10, 40], resultado="Out"),              # perda do 2
        passe(2, [0, 0], [10, 40], tipo="Throw-in"),              # lateral da mesma equipe (mantido)
    ]
    n = r.nucleo_pos_perda_e_chute(r.linhas_da_partida(ev))
    assert sum(sum(v.values()) for k, v in n.items() if k.startswith('perda')) == 2
    assert any(k.startswith('continua') for k in n)
    assert any("adv|recuperação|" in k for v in n.values() for k in v)
    assert any("mesma|lateral|" in k for v in n.values() for k in v)
