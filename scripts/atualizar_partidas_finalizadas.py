"""
Atualiza placares e dados estatísticos de partidas finalizadas que ainda não os
possuem no banco. Complementa ingestao_fotmob.py (que exige liga+temporada
explícitos via dispatch manual) com uma varredura automática de TODAS as ligas
com mapeamento FotMob — útil como cron diário pra corrigir lacunas que o
sync-matches não preenche (ele só registra partidas, não ingere stats).

Detecção de partidas encerradas: usa match_date < NOW()-2h (janela de 120 min)
em vez de status='finished', pois o sync-matches pode não ter atualizado o
status ainda. Status 'cancelled' e 'postponed' são sempre excluídos.

--ao-vivo: processa partidas com match_date nos últimos 120 min (em andamento
ou recém-encerradas sem status atualizado).

Pré-requisito: crosswalk de times (team_source_ids, source='fotmob') já
resolvido pra cada liga processada — mesmo requisito do ingestao_fotmob.py.
Times sem crosswalk têm placar atualizado mas stats puladas (aviso no log).

Pacing: 1.3s entre chamadas de API (mesmo do ingestao_fotmob.py).

Uso:
    python scripts/atualizar_partidas_finalizadas.py [--limite N] [--league-id ID]
        [--modo tudo|placar|stats] [--forcar] [--ao-vivo]
"""

import argparse
import datetime as dt
import os
import sys
import time

import requests
from supabase import create_client

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "arquivos_do_claude"))
from ingestao_fotmob import processar_matchdetails_completo, HEADERS, PACING_SEGUNDOS, BASE

SUPABASE_URL = (os.environ.get("SUPABASE_URL") or "").strip()
SUPABASE_KEY = (os.environ.get("SUPABASE_KEY") or "").strip()


def _exec_retry(query, tentativas=9, espera_inicial=3, espera_max=60):
    """Executa uma query do supabase-py com retry exponencial (teto de 60s
    entre tentativas). Erros de rede (httpx.ReadTimeout) e de instabilidade
    do PostgREST (PGRST002 - schema cache) são transitórios e já derrubaram
    execuções inteiras de ~50min no meio do processamento -- perdendo todo o
    progresso desde o início. Um surto observado em produção (05/09) manteve
    o PostgREST em crash-loop (PGRST002) por mais de 5min seguidos -- por
    isso o orçamento total de espera aqui é de ~8min (3+6+12+24+48+60*4),
    bem acima do budget anterior de 45s que não sobrevivia a esse cenário."""
    for tentativa in range(tentativas):
        try:
            return query.execute()
        except Exception as exc:
            if tentativa == tentativas - 1:
                raise
            espera = min(espera_inicial * (2 ** tentativa), espera_max)
            print(f"    Aviso: erro Supabase ({exc}), tentativa {tentativa + 1}/{tentativas}, aguardando {espera}s...")
            time.sleep(espera)


def _paginar(supabase, table, select, eq_filters=None, order="id"):
    """Busca todas as linhas com paginação explícita (evita corte silencioso de 1000)."""
    result = []
    page = 0
    while True:
        q = supabase.table(table).select(select).order(order).range(page * 1000, page * 1000 + 999)
        for col, val in (eq_filters or {}).items():
            q = q.eq(col, val)
        chunk = _exec_retry(q).data
        result.extend(chunk)
        if len(chunk) < 1000:
            break
        page += 1
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limite", type=int, default=None,
                    help="Processa só os N primeiros jogos pendentes")
    ap.add_argument("--league-id", type=int, default=None,
                    help="Restringe a uma liga interna específica")
    ap.add_argument("--modo", choices=["tudo", "placar", "stats"], default="tudo",
                    help="tudo=placar+stats | placar=só placares faltando | stats=só stats faltando")
    ap.add_argument("--forcar", action="store_true",
                    help="Reprocessa mesmo partidas que já têm dados parciais")
    ap.add_argument("--ao-vivo", action="store_true",
                    help="Processa partidas com match_date nos últimos 120 min (em andamento ou recém-encerradas)")
    args = ap.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        sys.exit("Defina SUPABASE_URL e SUPABASE_KEY (service_role) como variáveis de ambiente.")

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    # ── Crosswalk times: fotmob source_id <-> team_id interno ─────────────────
    print("Carregando crosswalk de times (fotmob)...")
    cw_rows = _paginar(supabase, "team_source_ids", "team_id, source_id",
                       eq_filters={"source": "fotmob"}, order="source_id")
    fotmob_to_internal = {row["source_id"]: row["team_id"] for row in cw_rows}
    internal_to_fotmob = {v: k for k, v in fotmob_to_internal.items()}
    print(f"  {len(fotmob_to_internal)} times mapeados.")

    # ── Liga -> FotMob league id ───────────────────────────────────────────────
    lfe_rows = _exec_retry(
        supabase.table("liga_fonte_externa")
        .select("league_id, identificador")
        .eq("sistema", "fotmob")
    ).data
    fotmob_league_map = {row["league_id"]: row["identificador"] for row in lfe_rows}
    ligas_alvo = [args.league_id] if args.league_id else list(fotmob_league_map.keys())
    if args.league_id and args.league_id not in fotmob_league_map:
        sys.exit(f"Liga {args.league_id} não tem FotMob ID em liga_fonte_externa.")
    print(f"  {len(ligas_alvo)} liga(s) com cobertura FotMob.")

    # ── match_source_ids: match_id -> fotmob match id já conhecidos ───────────
    print("Carregando match_source_ids (fotmob)...")
    msi_rows = _paginar(supabase, "match_source_ids", "match_id, source_id",
                        eq_filters={"source": "fotmob"}, order="match_id")
    match_to_fotmob_id = {row["match_id"]: row["source_id"] for row in msi_rows}
    print(f"  {len(match_to_fotmob_id)} partidas com FotMob match ID conhecido.")

    # ── match_stats_fotmob: set de match_ids que já têm stats ─────────────────
    if args.modo in ("tudo", "stats"):
        print("Carregando IDs com stats já ingeridos...")
        stats_rows = _paginar(supabase, "match_stats_fotmob", "match_id", order="match_id")
        has_stats = {row["match_id"] for row in stats_rows}
        print(f"  {len(has_stats)} partidas com stats.")
    else:
        has_stats = set()

    # ── Janela de tempo para detecção de partidas ─────────────────────────────
    agora_utc = dt.datetime.now(dt.timezone.utc)
    corte_utc = agora_utc - dt.timedelta(minutes=120)
    corte_iso = corte_utc.isoformat()
    agora_iso = agora_utc.isoformat()

    if args.ao_vivo:
        descricao_filtro = f"ao vivo (match_date entre {corte_iso[:16]} e {agora_iso[:16]} UTC)"
    else:
        descricao_filtro = f"encerradas (match_date < {corte_iso[:16]} UTC, excluindo cancelled/postponed)"
    print(f"Modo: {descricao_filtro}")

    # ── Partidas finalizadas que precisam de atualização ──────────────────────
    print("Buscando partidas pendentes...")
    pendentes = []
    for lid in ligas_alvo:
        page = 0
        while True:
            q = (
                supabase.table("matches")
                .select("id, home_team_id, away_team_id, match_date, league_id, home_goals, away_goals, season")
                .eq("league_id", lid)
                .order("id")
                .range(page * 1000, page * 1000 + 999)
            )
            if args.ao_vivo:
                q = q.gte("match_date", corte_iso).lte("match_date", agora_iso)
            else:
                q = q.lt("match_date", corte_iso).not_.in_("status", ["cancelled", "postponed"])
            chunk = _exec_retry(q).data
            for m in chunk:
                if args.forcar:
                    pendentes.append(m)
                    continue
                needs_score = args.modo in ("tudo", "placar") and m["home_goals"] is None
                needs_stats = args.modo in ("tudo", "stats") and m["id"] not in has_stats
                if needs_score or needs_stats:
                    pendentes.append(m)
            if len(chunk) < 1000:
                break
            page += 1

    print(f"Partidas pendentes: {len(pendentes)}")
    if args.limite:
        pendentes = pendentes[: args.limite]
        print(f"  Limitado a {args.limite} partidas.")

    if not pendentes:
        print("Nada a fazer.")
        return

    # ── Cache de fixture lists por (fotmob_league_id, temporada) ───────────────
    fixture_cache: dict = {}

    def get_fixture_index(fotmob_league_id: str, temporada) -> dict:
        """Busca e cacheia fixture list pra UMA temporada (matches.season, ex. '2026' —
        já é o ano de INÍCIO da temporada, mesma convenção usada em toda a base). Tenta
        o formato 'AAAA' primeiro (ligas de temporada = ano civil, ex. Brasileirão) e
        'AAAA/AAAA+1' depois (ligas europeias de calendário ago-mai — FotMob exige
        intervalo, 'AAAA' sozinho devolve 0 fixtures).

        Importante: a chave de cache (e a escolha de temporada) usa `matches.season`,
        NÃO o ano civil de `match_date` — dois jogos com o mesmo ano civil podem ser de
        temporadas europeias DIFERENTES (ex. um jogo de maio/2026, cauda da temporada
        2025/2026, e um de agosto/2026, abertura da 2026/2027, têm `match_date.year`
        igual a 2026 mas pertencem a fixture lists distintas). Cachear por ano civil
        causava um bug real: a primeira temporada resolvida pra um dado ano ficava
        presa no cache e era reusada (errada) pro resto dos jogos daquele ano civil —
        os jogos de abertura de temporada (agosto) ficavam presos em "sem ID FotMob
        identificado" pra sempre porque a fixture list cacheada era da temporada
        ANTERIOR, já encerrada."""
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
                r = requests.get(
                    f"{BASE}/fixtures",
                    params={"id": fotmob_league_id, "season": season_str},
                    headers=HEADERS,
                    timeout=20,
                )
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
                # ao_vivo inclui jogos não finalizados (em andamento); encerradas só finalizados
                if not args.ao_vivo and not fx["status"]["finished"]:
                    continue
                k = (str(fx["home"]["id"]), str(fx["away"]["id"]))
                index.setdefault(k, []).append(fx)
            break  # temporada correta encontrada

        fixture_cache[key] = index
        return index

    def casar_fixture(index: dict, home_team_id: int, away_team_id: int, match_date_str: str):
        """Retorna o fixture FotMob mais próximo por data (janela 36h)."""
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

    # ── Loop principal ─────────────────────────────────────────────────────────
    n_ok, n_sem_id, n_falha = 0, 0, 0

    for i, m in enumerate(pendentes):
        match_id = m["id"]
        league_id = m["league_id"]
        fotmob_league_id = fotmob_league_map[league_id]

        # Descobrir fotmob_match_id
        fotmob_match_id = match_to_fotmob_id.get(match_id)
        if not fotmob_match_id:
            if not m.get("match_date"):
                n_sem_id += 1
                continue
            temporada = m.get("season") or dt.datetime.fromisoformat(str(m["match_date"]).replace("Z", "+00:00")).year
            index = get_fixture_index(fotmob_league_id, temporada)
            fx = casar_fixture(index, m["home_team_id"], m["away_team_id"], m["match_date"])
            if not fx:
                n_sem_id += 1
                continue
            fotmob_match_id = str(fx["id"])

        # Buscar matchDetails
        try:
            r = requests.get(
                f"{BASE}/matchDetails",
                params={"matchId": fotmob_match_id},
                headers=HEADERS,
                timeout=20,
            )
            r.raise_for_status()
            d = r.json()
        except Exception as exc:
            print(f"  [{i+1}/{len(pendentes)}] Falha matchId={fotmob_match_id}: {exc}")
            n_falha += 1
            time.sleep(PACING_SEGUNDOS)
            continue

        # Atualizar placar se faltando.
        # FotMob usa header.teams[].score como placar definitivo; general.homeScore.current
        # é o placar ao vivo e pode ser None em partidas já encerradas.
        if m["home_goals"] is None and args.modo in ("tudo", "placar"):
            try:
                header_teams = d.get("header", {}).get("teams", [])
                general = d.get("general", {}) or {}
                # `or` trocado por checagem explícita de None: placar 0 é um valor
                # válido (ex. vitória 2-0) e era falso-negativo com `or`, caindo pro
                # fallback abaixo e estourando KeyError quando general.homeScore não
                # existe no payload (chave nem sempre presente em partidas encerradas).
                home_g = header_teams[0].get("score") if header_teams else None
                if home_g is None:
                    home_g = (general.get("homeScore") or {}).get("current")
                away_g = header_teams[1].get("score") if len(header_teams) > 1 else None
                if away_g is None:
                    away_g = (general.get("awayScore") or {}).get("current")
                if home_g is not None:
                    update_data = {"home_goals": home_g, "away_goals": away_g}
                    if general.get("finished"):
                        update_data["status"] = "finished"
                    _exec_retry(supabase.table("matches").update(update_data).eq("id", match_id))
            except Exception as exc:
                print(f"  [{i+1}/{len(pendentes)}] Aviso: placar match_id={match_id}: {exc}")

        # Ingerir stats completas
        if args.modo != "placar" and (match_id not in has_stats or args.forcar):
            home_fm_id = str(d["general"]["homeTeam"]["id"])
            away_fm_id = str(d["general"]["awayTeam"]["id"])
            if not fotmob_to_internal.get(home_fm_id) or not fotmob_to_internal.get(away_fm_id):
                print(
                    f"  [{i+1}/{len(pendentes)}] Aviso: sem crosswalk pra times da partida "
                    f"match_id={match_id} (FotMob home={home_fm_id}, away={away_fm_id}) — stats puladas."
                )
            else:
                try:
                    extraido = processar_matchdetails_completo(
                        d, match_id, fotmob_match_id, fotmob_to_internal
                    )
                except Exception as exc:
                    print(f"  [{i+1}/{len(pendentes)}] Falha de parse match_id={match_id}: {exc}")
                    n_falha += 1
                    time.sleep(PACING_SEGUNDOS)
                    continue

                if extraido["team_rows"]:
                    _exec_retry(supabase.table("match_stats_fotmob").upsert(
                        extraido["team_rows"], on_conflict="match_id,team_id"
                    ))

                _exec_retry(supabase.table("match_context_fotmob").upsert(
                    extraido["contexto_row"], on_conflict="match_id"
                ))

                player_dim_rows = extraido["player_dim_rows"]
                lineup_rows = extraido["lineup_rows"]
                fotmob_to_player_id: dict = {}
                if player_dim_rows:
                    player_dim_rows = list(
                        {row["fotmob_player_id"]: row for row in player_dim_rows}.values()
                    )
                    resp = _exec_retry(supabase.table("players").upsert(
                        player_dim_rows, on_conflict="fotmob_player_id"
                    ))
                    for row in resp.data:
                        fotmob_to_player_id[row["fotmob_player_id"]] = row["id"]

                if lineup_rows:
                    for row in lineup_rows:
                        row["player_id"] = fotmob_to_player_id.get(row["fotmob_player_id"])
                    lineup_rows = list(
                        {(r["match_id"], r["team_id"], r["fotmob_player_id"]): r for r in lineup_rows}.values()
                    )
                    _exec_retry(supabase.table("match_lineup_fotmob").upsert(
                        lineup_rows, on_conflict="match_id,team_id,fotmob_player_id"
                    ))

                player_rows = extraido["player_rows"]
                if player_rows:
                    vistos: dict = {}
                    for row in player_rows:
                        row["player_id"] = fotmob_to_player_id.get(row["fotmob_player_id"])
                        vistos[row["fotmob_player_id"]] = row
                    _exec_retry(supabase.table("match_player_stats_fotmob").upsert(
                        list(vistos.values()), on_conflict="match_id,fotmob_player_id"
                    ))

                shot_rows = extraido["shot_rows"]
                if shot_rows:
                    for row in shot_rows:
                        row["player_id"] = (
                            fotmob_to_player_id.get(row["fotmob_player_id"])
                            if row["fotmob_player_id"]
                            else None
                        )
                    _exec_retry(supabase.table("match_shots_fotmob").upsert(
                        shot_rows, on_conflict="fotmob_shot_id"
                    ))

                has_stats.add(match_id)

        # Registrar FotMob match ID se ainda não estava
        if match_id not in match_to_fotmob_id:
            try:
                general = d.get("general") or {}
                home_name = (general.get("homeTeam") or {}).get("name", "")
                away_name = (general.get("awayTeam") or {}).get("name", "")
                _exec_retry(supabase.table("match_source_ids").upsert(
                    {
                        "match_id": match_id,
                        "source": "fotmob",
                        "source_id": str(fotmob_match_id),
                        "source_name": f"{home_name} x {away_name}",
                    },
                    on_conflict="match_id,source",
                ))
                match_to_fotmob_id[match_id] = str(fotmob_match_id)
            except Exception as exc:
                print(f"  Aviso: match_source_ids match_id={match_id}: {exc}")

        n_ok += 1
        if (i + 1) % 10 == 0:
            print(f"  [{i+1}/{len(pendentes)}] processados...")
        time.sleep(PACING_SEGUNDOS)

    print(f"\nOK: {n_ok} atualizados, {n_sem_id} sem ID FotMob identificado, {n_falha} falhas.")


if __name__ == "__main__":
    main()
