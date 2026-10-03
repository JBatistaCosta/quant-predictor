#!/usr/bin/env python3
"""Testes SQL da migration 20261004100000 (mapa de calor por jogador). Precisam de um Postgres DESCARTÁVEL: sem a variável
TEST_PGHOST os testes são PULADOS. O schema public é APAGADO a cada execução -- nunca aponte para produção.
    TEST_PGHOST=/tmp/pgtest2 TEST_PGPORT=55433 TEST_PGUSER=postgres pytest scripts/test_heatmap_jogador_sql.py -v"""

from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

HOST = os.environ.get("TEST_PGHOST")
pytestmark = pytest.mark.skipif(not HOST, reason="defina TEST_PGHOST (Postgres descartável) para rodar os testes SQL")
MIGRATIONS = pathlib.Path(__file__).resolve().parent.parent / "supabase" / "migrations"
ZONAS = MIGRATIONS / "20261003140000_detalhe_jogador_fotmob_e_zonas_campo.sql"
HEATMAP = MIGRATIONS / "20261004100000_create_match_player_heatmap_fotmob.sql"
ZONA18 = MIGRATIONS / "20261004120000_zona_campo18.sql"


def psql(sql: str, arquivo: pathlib.Path | None = None) -> list[list[str]]:
    cmd = ["psql", "-h", HOST, "-p", os.environ.get("TEST_PGPORT", "5432"), "-U", os.environ.get("TEST_PGUSER", "postgres"),
           "-v", "ON_ERROR_STOP=1", "-q", "-At", "-F", "|"]
    cmd += ["-f", str(arquivo)] if arquivo else ["-c", sql]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return [linha.split("|") for linha in r.stdout.strip().splitlines() if linha]


def um(sql):
    return psql(sql)[0]


def pontos(*xy):
    """(x, y) em metros -> literal smallint[] achatado em décimos de metro."""
    return "array[" + ",".join(str(round(v * 10)) for p in xy for v in p) + "]::smallint[]"


@pytest.fixture(scope="module", autouse=True)
def banco():
    psql("""
      drop schema if exists public cascade; create schema public;
      do $$ begin
        if not exists (select 1 from pg_roles where rolname='anon') then create role anon; end if;
        if not exists (select 1 from pg_roles where rolname='authenticated') then create role authenticated; end if;
        if not exists (select 1 from pg_roles where rolname='service_role') then create role service_role; end if;
      end $$;
      create table matches(id bigint primary key);
      create table players(id bigint primary key, usual_position_id integer);
      create table match_player_stats_fotmob(id bigint primary key, match_id bigint, team_id bigint, fotmob_player_id text, player_id bigint,
        stats_raw jsonb, minutes_played integer, accurate_passes integer);
      create table match_shots_fotmob(id bigint primary key, match_id bigint, x double precision, y double precision, xg numeric);
      create function zona_chute(p_x numeric, p_y numeric) returns smallint language sql immutable as $f$ select 1::smallint $f$;
    """)
    psql("", ZONAS)
    psql("", HEATMAP)
    psql("", HEATMAP)   # idempotente
    psql("", ZONA18)
    psql("", ZONA18)    # idempotente
    # jogador 11 (meio): 4 pontos -- 2 na zona 5 (meio/centro), 1 na 11 (grande área/centro), 1 na 1 (defesa/lado_y_baixo)
    # jogador 12 (goleiro): 2 pontos na zona 2 (defesa/centro); jogador 13 (defesa) joga só 10 min (fora do corte)
    psql(f"""
      insert into matches values (100);
      insert into players values (11, 2), (12, 0), (13, 1);
      insert into match_player_stats_fotmob(id, match_id, team_id, fotmob_player_id, player_id, minutes_played) values
        (1, 100, 7, 'a', 11, 90), (2, 100, 7, 'gk', 12, 90), (3, 100, 7, 'curto', 13, 10);
      insert into match_player_heatmap_fotmob(match_id, team_id, fotmob_player_id, player_id, opta_id, n_pontos, pontos) values
        (100, 7, 'a', 11, '1', 4, {pontos((50, 34), (52, 30), (95, 34), (10, 5))}),
        (100, 7, 'gk', 12, '2', 2, {pontos((5, 34), (8, 40))}),
        (100, 7, 'curto', 13, '3', 1, {pontos((50, 34))});
    """)


def test_toques_por_zona():
    assert psql("select zona, toques from v_toques_zona where fotmob_player_id = 'a' order by zona") == [["1", "1"], ["5", "2"], ["11", "1"]]
    assert psql("select zona, toques from v_toques_zona where fotmob_player_id = 'gk'") == [["2", "2"]]


def test_view_por_linha_usa_corte_de_minutos_e_soma_participacao_1():
    linhas = psql("select linha, zona, toques, round(participacao, 2) from v_toques_zona_linha order by linha, zona")
    assert linhas == [["goleiro", "2", "2", "1.00"], ["meio", "1", "1", "0.25"], ["meio", "5", "2", "0.50"], ["meio", "11", "1", "0.25"]]   # 'curto' (10 min) fora
    assert um("select faixa_nome, corredor_nome from v_toques_zona_linha where linha = 'meio' and zona = 11") == ["grande_area_adversaria", "centro"]


def test_pontos_precisam_ter_duas_coordenadas_por_ponto():
    r = subprocess.run(["psql", "-h", HOST, "-p", os.environ.get("TEST_PGPORT", "5432"), "-U", os.environ.get("TEST_PGUSER", "postgres"), "-q", "-c",
                        "insert into match_player_heatmap_fotmob(match_id, fotmob_player_id, n_pontos, pontos) values (100, 'ruim', 2, array[1,2,3]::smallint[])"],
                       capture_output=True, text=True)
    assert r.returncode != 0 and "check" in r.stderr.lower()


def test_unicidade_por_partida_e_jogador():
    r = subprocess.run(["psql", "-h", HOST, "-p", os.environ.get("TEST_PGPORT", "5432"), "-U", os.environ.get("TEST_PGUSER", "postgres"), "-q", "-c",
                        "insert into match_player_heatmap_fotmob(match_id, fotmob_player_id, n_pontos, pontos) values (100, 'a', 0, array[]::smallint[])"],
                       capture_output=True, text=True)
    assert r.returncode != 0 and "unique" in r.stderr.lower()


@pytest.mark.parametrize("x,y,zona", [
    (10, 5, 1), (10, 34, 2), (10, 60, 3),                       # defesa
    (40, 5, 4), (40, 34, 5), (40, 60, 6),                       # meio_baixo
    (60, 5, 7), (60, 34, 8), (60, 60, 9),                       # meio_alto
    (75, 5, 10), (75, 34, 11), (75, 60, 12),                    # ataque_fora_da_area_baixo
    (85, 5, 13), (85, 34, 14), (85, 60, 15),                    # ataque_fora_da_area_alto
    (95, 20, 16), (95, 34, 17), (95, 50, 18),                   # grande área
    (52.5, 34, 8), (52.49, 34, 5),                              # corte do meio: 52,5 já é "alto"
    (79.25, 34, 14), (79.24, 34, 11),                           # corte do ataque: 79,25 já é "alto"
    (95, 5, 13),                                                # ponta fora da grande área: ataque_fora_da_area_alto
    (88.5, 13.85, 16),                                          # a borda da grande área conta como dentro
])
def test_zona_campo18(x, y, zona):
    assert um(f"select zona_campo18({x}, {y})") == [str(zona)]


def test_zona_campo18_nula_e_tabela():
    assert um("select zona_campo18(null, 10) is null") == ["t"]
    assert um("select count(*), min(zona), max(zona) from zonas_campo18") == ["18", "1", "18"]
    assert um("select faixa_nome, corredor_nome, zona12 from zonas_campo18 where zona = 14") == ["ataque_fora_da_area_alto", "centro", "8"]


def test_cada_zona_de_18_cabe_dentro_de_uma_de_12():
    """Refinamento exato: em qualquer ponto do campo, zona12 de zona_campo18 == zona_campo12."""
    r = um("select count(*), count(*) filter (where c.zona12 = zona_campo12(x, y)) from "
           "generate_series(0, 105, 0.5) x, generate_series(0, 68, 0.5) y, lateral (select zona12 from zonas_campo18 where zona = zona_campo18(x, y)) c")
    assert r[0] == r[1] and int(r[0]) == 211 * 137


def test_sql_e_python_dao_a_mesma_zona_de_18():
    """A regra mora em dois lugares (SQL e o gerador em Python); este teste os mantém iguais num grid de pontos."""
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import gerar_matriz_transicao_statsbomb as g
    esperado = [g.zona_18(x / 2, y / 2) + 1 for x in range(0, 211, 7) for y in range(0, 137, 5)]
    linhas = psql("select zona_campo18(x / 2.0, y / 2.0) from generate_series(0, 210, 7) x, generate_series(0, 136, 5) y order by x, y")
    assert [int(l[0]) for l in linhas] == esperado


def test_view_de_toques_de_18():
    # jogador 'a': (50,34) e (52,30) -> meio_baixo/centro = 5 (x < 52,5), (95,34) -> 17, (10,5) -> 1
    assert psql("select zona, toques from v_toques_zona18 where fotmob_player_id = 'a' order by zona") == [["1", "1"], ["5", "2"], ["17", "1"]]
