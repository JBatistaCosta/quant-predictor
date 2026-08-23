"""Captura a escalação titular oficial (FotMob) na janela pré-jogo, antes do
apito -- viabiliza o cutover "abertura -> fechamento" da força do XI titular
em produção (ver FEATURES_V11/dados_historicos.py::obter_titular_atual).

`obter_titular_atual` já cai pra escalação real automaticamente quando ela
existe em match_lineup_fotmob (fallback pra xi_previsto só quando não
existe) -- o único gap era NINGUÉM ir buscar essa escalação ANTES do apito.
O único job existente que grava match_lineup_fotmob
(atualizar_partidas_finalizadas.py) só olha match_date no PASSADO --
`--ao-vivo` cobre partida em andamento (match_date nos últimos 120min),
nunca uma que ainda não começou.

Escopo deliberadamente estreito: grava só `players` (dimensão, upsert
idempotente) e `match_lineup_fotmob` -- NÃO toca em match_stats_fotmob/
match_context_fotmob/match_player_stats_fotmob/match_shots_fotmob/placar.
Esses continuam sendo responsabilidade exclusiva de
atualizar_partidas_finalizadas.py depois que a partida termina de verdade;
escrever qualquer coisa neles aqui a partir de um payload pré-jogo
marcaria a partida como "já tem stats" (has_stats) e faria aquele job
pular o preenchimento real pós-jogo.

Janela: match_date entre AGORA e AGORA+90min, status='scheduled', só
partidas que AINDA não têm nenhuma linha em match_lineup_fotmob (evita
rechecar partida já capturada a cada 15min). Se a lista vier vazia
(nenhuma partida na janela), sai sem nenhuma chamada de API -- é isso que
torna viável rodar esse script a cada 15min o dia inteiro sem estourar
cota (custo real só acontece quando tem jogo de verdade por perto).

Lineups oficiais costumam sair ~60min antes do apito -- com janela de
90min e cadência de 15min, uma partida é checada ~6x antes do apito, boa
margem pra capturar o anúncio pouco depois de sair.

Pacing: 1.3s entre chamadas de API (mesmo padrão de ingestao_fotmob.py).

Uso:
    python scripts/ingerir_escalacao_pre_jogo.py [--janela-min 90] [--league-id ID]
"""

import argparse
import datetime as dt
import os
import sys
import time

import requests
from supabase import create_client

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "arquivos_do_claude"))
from ingestao_fotmob import HEADERS, PACING_SEGUNDOS, BASE

SUPABASE_URL = (os.environ.get("SUPABASE_URL") or "").strip()
SUPABASE_KEY = (os.environ.get("SUPABASE_KEY") or "").strip()

JANELA_MIN_DEFAULT = 90


def _paginar(supabase, table, select, eq_filters=None, in_filters=None, order="id"):
    """Busca todas as linhas com paginação explícita (evita corte silencioso de 1000)."""
    result = []
    page = 0
    while True:
        q = supabase.table(table).select(select).order(order).range(page * 1000, page * 1000 + 999)
        for col, val in (eq_filters or {}).items():
            q = q.eq(col, val)
        for col, vals in (in_filters or {}).items():
            q = q.in_(col, vals)
        chunk = q.execute().data
        result.extend(chunk)
        if len(chunk) < 1000:
            break
        page += 1
    return result


def _extrair_lineup(d: dict, match_id: int, fotmob_to_internal: dict) -> tuple[list, list]:
    """Extrai só a escalação (titulares+reservas) de um payload matchDetails --
    mesmo mapeamento de campo de `processar_matchdetails_completo`
    (arquivos_do_claude/ingestao_fotmob.py), mas sem tocar em score/stats/
    shots -- pré-jogo não tem esse dado ainda, e chamar o parser completo
    aqui arriscaria gravar linha vazia/incompleta nas tabelas que o job
    pós-jogo usa pra decidir o que já foi processado."""
    content = d.get("content") or {}
    lineup = content.get("lineup") or {}
    player_dim_rows: list = []
    lineup_rows: list = []
    for side in ("homeTeam", "awayTeam"):
        team = lineup.get(side) or {}
        team_id = fotmob_to_internal.get(str(team.get("id")))
        for grupo, is_starter in (("starters", True), ("subs", False)):
            for p in team.get(grupo) or []:
                pid = str(p.get("id"))
                if pid in ("0", "-1"):
                    continue
                player_dim_rows.append({
                    "fotmob_player_id": pid,
                    "name": p.get("name"),
                    "first_name": p.get("firstName") or None,
                    "last_name": p.get("lastName") or None,
                    "shirt_number": p.get("shirtNumber"),
                    "country_name": p.get("countryName"),
                    "country_code": p.get("countryCode"),
                    "age": p.get("age"),
                    "market_value": p.get("marketValue"),
                    "usual_position_id": p.get("usualPlayingPositionId"),
                    "photo_url": f"https://images.fotmob.com/image_resources/playerimages/{pid}.png",
                    "last_team_id": team_id,
                    "last_seen_match_id": match_id,
                    "raw_lineup": p,
                    "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                })
                if team_id is not None:
                    vl = p.get("verticalLayout") or {}
                    lineup_rows.append({
                        "match_id": match_id,
                        "team_id": team_id,
                        "fotmob_player_id": pid,
                        "is_starter": is_starter,
                        "shirt_number": p.get("shirtNumber"),
                        "position_id": p.get("positionId"),
                        "field_pos_x": vl.get("x"),
                        "field_pos_y": vl.get("y"),
                        "is_captain": p.get("isCaptain") or False,
                        "raw": p,
                        "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                    })
    return player_dim_rows, lineup_rows


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--janela-min", type=int, default=JANELA_MIN_DEFAULT,
                     help=f"Janela de minutos à frente pra considerar 'pré-jogo' (default {JANELA_MIN_DEFAULT})")
    ap.add_argument("--league-id", type=int, default=None,
                     help="Restringe a uma liga interna específica")
    args = ap.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        sys.exit("Defina SUPABASE_URL e SUPABASE_KEY (service_role) como variáveis de ambiente.")

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    agora = dt.datetime.now(dt.timezone.utc)
    limite = agora + dt.timedelta(minutes=args.janela_min)

    # ── Liga -> FotMob league id ────────────────────────────────────────────
    lfe_rows = (
        supabase.table("liga_fonte_externa")
        .select("league_id, identificador")
        .eq("sistema", "fotmob")
        .execute()
        .data
    )
    fotmob_league_map = {row["league_id"]: row["identificador"] for row in lfe_rows}
    ligas_alvo = [args.league_id] if args.league_id else list(fotmob_league_map.keys())
    if args.league_id and args.league_id not in fotmob_league_map:
        sys.exit(f"Liga {args.league_id} não tem FotMob ID em liga_fonte_externa.")

    # ── Partidas na janela pré-jogo, status scheduled ───────────────────────
    candidatas = []
    for lid in ligas_alvo:
        chunk = (
            supabase.table("matches")
            .select("id, home_team_id, away_team_id, match_date, league_id, season")
            .eq("league_id", lid)
            .eq("status", "scheduled")
            .gte("match_date", agora.isoformat())
            .lte("match_date", limite.isoformat())
            .execute()
            .data
        )
        candidatas.extend(chunk)

    if not candidatas:
        print(f"Nenhuma partida na janela pré-jogo ({args.janela_min}min) -- nada a fazer.")
        return

    # Pula partida que já tem escalação real capturada (evita recheck a cada 15min).
    match_ids = [m["id"] for m in candidatas]
    ja_capturadas = _paginar(supabase, "match_lineup_fotmob", "match_id", in_filters={"match_id": match_ids}, order="match_id")
    ids_capturados = {row["match_id"] for row in ja_capturadas}
    candidatas = [m for m in candidatas if m["id"] not in ids_capturados]

    if not candidatas:
        print("Todas as partidas da janela já têm escalação capturada -- nada a fazer.")
        return

    print(f"{len(candidatas)} partida(s) na janela pré-jogo sem escalação ainda.")

    # ── Crosswalk times e match_source_ids já conhecidos ────────────────────
    cw_rows = _paginar(supabase, "team_source_ids", "team_id, source_id", eq_filters={"source": "fotmob"}, order="source_id")
    fotmob_to_internal = {row["source_id"]: row["team_id"] for row in cw_rows}
    internal_to_fotmob = {v: k for k, v in fotmob_to_internal.items()}

    msi_rows = _paginar(supabase, "match_source_ids", "match_id, source_id", eq_filters={"source": "fotmob"}, order="match_id")
    match_to_fotmob_id = {row["match_id"]: row["source_id"] for row in msi_rows}

    fixture_cache: dict = {}

    def get_fixture_index(fotmob_league_id: str, temporada) -> dict:
        """Mesma lógica de atualizar_partidas_finalizadas.py::get_fixture_index
        (cache por liga+temporada, tenta 'AAAA' depois 'AAAA/AAAA+1') --
        aqui NUNCA filtra por fx["status"]["finished"] (as partidas da janela
        pré-jogo, por definição, ainda não terminaram)."""
        key = (fotmob_league_id, str(temporada))
        if key in fixture_cache:
            return fixture_cache[key]
        index: dict = {}
        candidatos_temporada = [str(temporada)]
        try:
            candidatos_temporada.append(f"{int(temporada)}/{int(temporada) + 1}")
        except (TypeError, ValueError):
            pass
        for season_str in candidatos_temporada:
            try:
                r = requests.get(f"{BASE}/fixtures", params={"id": fotmob_league_id, "season": season_str}, headers=HEADERS, timeout=20)
                r.raise_for_status()
                fixtures = r.json()
            except Exception as exc:
                print(f"    Aviso: falha ao buscar fixtures (liga={fotmob_league_id}, temporada={season_str}): {exc}")
                time.sleep(PACING_SEGUNDOS)
                continue
            time.sleep(PACING_SEGUNDOS)
            if not fixtures:
                continue
            for fx in fixtures:
                if fx["status"].get("cancelled"):
                    continue
                k = (str(fx["home"]["id"]), str(fx["away"]["id"]))
                index.setdefault(k, []).append(fx)
            break
        fixture_cache[key] = index
        return index

    def casar_fixture(index, home_team_id, away_team_id, match_date_str):
        """Retorna o fixture FotMob mais próximo por data (janela 36h) -- mesmo
        critério de atualizar_partidas_finalizadas.py::casar_fixture."""
        home_fm = internal_to_fotmob.get(home_team_id)
        away_fm = internal_to_fotmob.get(away_team_id)
        if not home_fm or not away_fm:
            return None
        candidatos = index.get((home_fm, away_fm), [])
        if not candidatos:
            return None
        alvo = dt.datetime.fromisoformat(str(match_date_str).replace("Z", "+00:00"))
        melhor, menor_delta = None, None
        for fx in candidatos:
            utc = fx["status"].get("utcTime", "")
            if not utc:
                continue
            fd = dt.datetime.fromisoformat(utc.replace("Z", "+00:00"))
            delta = abs((fd - alvo).total_seconds())
            if delta < 36 * 3600 and (menor_delta is None or delta < menor_delta):
                menor_delta, melhor = delta, fx
        return melhor

    n_capturadas, n_ainda_sem_escalacao, n_sem_id, n_falha = 0, 0, 0, 0

    for m in candidatas:
        match_id = m["id"]
        league_id = m["league_id"]
        fotmob_league_id = fotmob_league_map[league_id]

        fotmob_match_id = match_to_fotmob_id.get(match_id)
        if not fotmob_match_id:
            temporada = m.get("season") or dt.datetime.fromisoformat(str(m["match_date"]).replace("Z", "+00:00")).year
            index = get_fixture_index(fotmob_league_id, temporada)
            fx = casar_fixture(index, m["home_team_id"], m["away_team_id"], m["match_date"])
            if not fx:
                n_sem_id += 1
                continue
            fotmob_match_id = str(fx["id"])

        try:
            r = requests.get(f"{BASE}/matchDetails", params={"matchId": fotmob_match_id}, headers=HEADERS, timeout=20)
            r.raise_for_status()
            d = r.json()
        except Exception as exc:
            print(f"  Falha matchId={fotmob_match_id} (match_id={match_id}): {exc}")
            n_falha += 1
            time.sleep(PACING_SEGUNDOS)
            continue

        player_dim_rows, lineup_rows = _extrair_lineup(d, match_id, fotmob_to_internal)

        if not lineup_rows:
            n_ainda_sem_escalacao += 1
            time.sleep(PACING_SEGUNDOS)
            continue

        if player_dim_rows:
            player_dim_rows = list({row["fotmob_player_id"]: row for row in player_dim_rows}.values())
            resp = supabase.table("players").upsert(player_dim_rows, on_conflict="fotmob_player_id").execute()
            fotmob_to_player_id = {row["fotmob_player_id"]: row["id"] for row in resp.data}
            for row in lineup_rows:
                row["player_id"] = fotmob_to_player_id.get(row["fotmob_player_id"])

        lineup_rows = list({(r["match_id"], r["team_id"], r["fotmob_player_id"]): r for r in lineup_rows}.values())
        supabase.table("match_lineup_fotmob").upsert(lineup_rows, on_conflict="match_id,team_id,fotmob_player_id").execute()
        n_capturadas += 1
        print(f"  match_id={match_id}: escalação capturada ({len(lineup_rows)} jogadores).")

        if match_id not in match_to_fotmob_id:
            try:
                general = d.get("general") or {}
                home_name = (general.get("homeTeam") or {}).get("name", "")
                away_name = (general.get("awayTeam") or {}).get("name", "")
                supabase.table("match_source_ids").upsert(
                    {"match_id": match_id, "source": "fotmob", "source_id": str(fotmob_match_id), "source_name": f"{home_name} x {away_name}"},
                    on_conflict="match_id,source",
                ).execute()
            except Exception as exc:
                print(f"  Aviso: match_source_ids match_id={match_id}: {exc}")

        time.sleep(PACING_SEGUNDOS)

    print(f"\nOK: {n_capturadas} escalação(ões) capturada(s), {n_ainda_sem_escalacao} ainda sem escalação oficial publicada, {n_sem_id} sem ID FotMob identificado, {n_falha} falhas.")


if __name__ == "__main__":
    main()
