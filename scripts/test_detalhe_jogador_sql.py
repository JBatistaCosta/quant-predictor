#!/usr/bin/env python3
"""Testes SQL da migration 20261003140000 (detalhe por jogador do stats_raw + zonas do campo).

Precisam de um Postgres DESCARTÁVEL: sem a variável TEST_PGHOST os testes são PULADOS. Exemplo (cluster local):
    TEST_PGHOST=/tmp/pgtest2 TEST_PGPORT=55433 TEST_PGUSER=postgres pytest scripts/test_detalhe_jogador_sql.py -v
O banco é recriado do zero a cada execução (schema public apagado!) -- nunca aponte para produção."""

from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

HOST = os.environ.get("TEST_PGHOST")
pytestmark = pytest.mark.skipif(not HOST, reason="defina TEST_PGHOST (Postgres descartável) para rodar os testes SQL")
MIGRATION = pathlib.Path(__file__).resolve().parent.parent / "supabase" / "migrations" / "20261003140000_detalhe_jogador_fotmob_e_zonas_campo.sql"


def psql(sql: str, arquivo: pathlib.Path | None = None) -> list[list[str]]:
    cmd = ["psql", "-h", HOST, "-p", os.environ.get("TEST_PGPORT", "5432"), "-U", os.environ.get("TEST_PGUSER", "postgres"),
           "-v", "ON_ERROR_STOP=1", "-q", "-At", "-F", "|"]
    cmd += ["-f", str(arquivo)] if arquivo else ["-c", sql]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return [linha.split("|") for linha in r.stdout.strip().splitlines() if linha]


def grupos(*itens):
    """Monta o JSON no formato do FotMob a partir de (grupo, rótulo, chave, tipo, value, total)."""
    por_grupo: dict = {}
    for g, rotulo, chave, tipo, valor, total in itens:
        stat = {"type": tipo}
        if valor is not None:
            stat["value"] = valor
        if total is not None:
            stat["total"] = total
        por_grupo.setdefault(g, {})[rotulo] = {"key": chave, "stat": stat}
    import json
    return json.dumps([{"key": g, "title": g, "stats": s} for g, s in por_grupo.items()])


@pytest.fixture(scope="module", autouse=True)
def banco():
    psql("""
      drop schema if exists public cascade; create schema public;
      do $$ begin
        if not exists (select 1 from pg_roles where rolname='anon') then create role anon; end if;
        if not exists (select 1 from pg_roles where rolname='authenticated') then create role authenticated; end if;
        if not exists (select 1 from pg_roles where rolname='service_role') then create role service_role; end if;
      end $$;
      create table match_player_stats_fotmob(id bigint primary key, match_id bigint, team_id bigint, fotmob_player_id text, player_id bigint,
        stats_raw jsonb, minutes_played integer, accurate_passes integer);
      create table players(id bigint primary key, usual_position_id integer);
      create table match_shots_fotmob(id bigint primary key, match_id bigint, x double precision, y double precision, xg numeric);
      -- Stand-in da zona_chute de produção (20261001120000, 14 zonas polares): aqui só importa que a view a repasse.
      create function zona_chute(p_x numeric, p_y numeric) returns smallint language sql immutable as
        $f$ select case when p_x >= 99.5 then 1 else 14 end::smallint $f$;
    """)
    psql("", MIGRATION)
    psql("", MIGRATION)   # idempotente: reaplicar não pode falhar
    linha = grupos(
        ("top_stats", "Minutes played", "minutes_played", "integer", 90, None),
        ("top_stats", "Accurate passes", "accurate_passes", "fractionWithPercentage", 27, 32),
        ("top_stats", "Shotmap", None, "boolean", 1, None),                              # chave nula: ignorada
        ("top_stats", "Expected goals on target (xGOT)", "expected_goals_on_target_variant", "double", None, None),  # sem value
        ("attack", "Dispossessed", "dispossessed", "integer", 3, None),
        ("attack", "Accurate long balls", "long_balls_accurate", "fractionWithPercentage", 1, 3),
        ("attack", "Successful dribbles", "dribbles_succeeded", "fractionWithPercentage", 2, 5),
        ("attack", "xG Non-penalty", "expected_goals_non_penalty", "double", 0.18, None),
        ("defense", "Recoveries", "recoveries", "integer", 4, None),
        ("defense", "Defensive actions", "defensive_actions", "integer", 7, None),
        ("duels", "Duels won", "duel_won", "integer", 6, None),
        ("duels", "Aerial duels won", "aerials_won", "fractionWithPercentage", 3, 4),
        ("physical_metrics", "Distance covered", "physical_metrics_distance_covered", "distance", 9288, None),
        ("physical_metrics", "Top speed", "physical_metrics_topspeed", "speed", 32.3, None),
    ).replace("'", "''")
    gk = grupos(("top_stats", "Saves", "saves", "integer", 5, None), ("top_stats", "Goals prevented", "goals_prevented", "double", -0.79, None),
                ("top_stats", "xGOT faced", "expected_goals_on_target_faced", "double", 1.55, None),
                ("top_stats", "Touches", "touches", "integer", 43, None)).replace("'", "''")
    antigo = ('{"id": 1, "name": "Antigo", "stats": ' + grupos(("attack", "Dispossessed", "dispossessed", "integer", 2, None)) + "}").replace("'", "''")
    psql(f"""
      insert into match_player_stats_fotmob values
        (1, 100, 7, 'a', 11, '{linha}', 90, 27), (2, 100, 7, 'gk', 12, '{gk}', 90, 10), (3, 100, 8, 'velho', 13, '{antigo}', 10, 5),
        (4, 100, 8, 'reserva', 14, '[]', null, null), (5, 100, 8, 'nulo', 15, null, null, null), (6, 200, 7, 'a', 11, '{linha}', 90, 27);
      insert into players values (11, 2), (12, 0), (13, 1);
      insert into match_shots_fotmob values (1, 100, 100, 34, 0.35), (2, 100, 50, 34, 0.02);
    """)


def um(sql):
    return psql(sql)[0]


def test_fracao_vira_duas_colunas_e_valor_ausente_vira_null():
    psql("select derivar_detalhe_jogador_fotmob(array[100]::bigint[])")
    r = um("select passes_total, bolas_longas_certas, bolas_longas_total, dribles_certos, dribles_total, duelos_aereos_total, xgot_enfrentado is null "
           "from match_player_stats_detalhe_fotmob where stat_id = 1")
    assert r == ["32", "1", "3", "2", "5", "4", "t"]


def test_campos_simples_e_fisico():
    r = um("select perdas_posse, recuperacoes, acoes_defensivas, duelos_ganhos, xg_sem_penalti, dist_total_m, vel_max_kmh "
           "from match_player_stats_detalhe_fotmob where stat_id = 1")
    assert r[:4] == ["3", "4", "7", "6"] and float(r[4]) == pytest.approx(0.18) and r[5] == "9288" and float(r[6]) == pytest.approx(32.3, abs=0.01)


def test_goleiro_e_formato_antigo_embrulhado():
    gk = um("select defesas, gols_evitados, xgot_enfrentado from match_player_stats_detalhe_fotmob where stat_id = 2")
    assert gk[0] == "5" and float(gk[1]) == pytest.approx(-0.79) and float(gk[2]) == pytest.approx(1.55)
    assert um("select perdas_posse from match_player_stats_detalhe_fotmob where stat_id = 3") == ["2"]


def test_reserva_vazio_e_nulo_nao_geram_linha_e_outras_partidas_nao_sao_tocadas():
    assert um("select count(*) from match_player_stats_detalhe_fotmob where stat_id in (4, 5)") == ["0"]
    assert um("select count(*) from match_player_stats_detalhe_fotmob where match_id = 200") == ["0"]   # só a partida 100 foi derivada


def test_derivacao_idempotente_e_atualiza():
    n1 = um("select derivar_detalhe_jogador_fotmob(array[100]::bigint[])")[0]
    n2 = um("select derivar_detalhe_jogador_fotmob(array[100]::bigint[])")[0]
    assert n1 == n2 == "3"
    assert um("select count(*) from match_player_stats_detalhe_fotmob where match_id = 100") == ["3"]


def test_backfill_retomavel_percorre_tudo_e_termina():
    psql("truncate match_player_stats_detalhe_fotmob")
    ultimo, total, passos = 0, 0, 0
    while True:
        u, _, linhas, fim = um(f"select * from backfill_detalhe_jogador_fotmob({ultimo}, 1, 1)")
        ultimo, total, passos = int(u), total + int(linhas), passos + 1
        if fim == "t":
            break
        assert passos < 20
    assert total == 4 and um("select count(distinct match_id) from match_player_stats_detalhe_fotmob") == ["2"]


def test_kv_ignora_entrada_que_nao_e_array():
    assert um("select detalhe_jogador_kv('\"texto\"'::jsonb)") == ["{}"]
    assert um("select detalhe_jogador_kv(null)") == ["{}"]


@pytest.mark.parametrize("x,y,zona", [
    (10, 5, 1), (10, 34, 2), (10, 60, 3), (50, 5, 4), (50, 34, 5), (50, 60, 6), (80, 5, 7), (80, 34, 8), (80, 60, 9),
    (95, 34, 11), (95, 20, 10), (95, 50, 12),
    (95, 5, 7),            # além da largura da grande área: ataque fora da área
    (88.5, 13.85, 10),     # a borda da grande área conta como dentro
    (88.49, 34, 8),        # um passo antes da área
    (105, 68, 9),
])
def test_zona_campo12(x, y, zona):
    assert um(f"select zona_campo12({x}, {y})") == [str(zona)]


def test_zona_campo12_nula_e_tabela_de_zonas():
    assert um("select zona_campo12(null, 10) is null") == ["t"]
    assert um("select count(*), min(zona), max(zona) from zonas_campo12") == ["12", "1", "12"]
    assert um("select faixa_nome, corredor_nome from zonas_campo12 where zona = 11") == ["grande_area_adversaria", "centro"]


def test_view_de_chutes_traz_as_duas_zonas():
    assert um("select zona12, zona_chute from v_chutes_zona where id = 1") == ["11", "1"]
    assert um("select zona12, zona_chute from v_chutes_zona where id = 2") == ["5", "14"]


def test_view_por_linha_soma_acoes_e_minutos_e_ignora_pouco_minuto():
    psql("select derivar_detalhe_jogador_fotmob(array[100, 200]::bigint[])")
    # meio (jogador 11, 2 jogos de 90 min): passes/90 = 90*64/180 = 32; precisão = 54/64; perdas/90 = 90*6/180 = 3
    assert um("select jogos, minutos, round(passes_90, 2), round(precisao_passe, 4), round(perdas_posse_90, 2) "
              "from v_detalhe_jogador_linha where linha = 'meio'") == ["2", "180", "32.00", "0.8438", "3.00"]
    assert um("select count(*) from v_detalhe_jogador_linha where linha = 'defesa'") == ["0"]   # 10 min: abaixo do corte de 20
    assert um("select jogos from v_detalhe_jogador_linha where linha = 'goleiro'") == ["1"]
