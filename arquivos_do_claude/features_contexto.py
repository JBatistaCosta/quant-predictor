"""
Engenharia de features de contexto (fadiga, logística, ciclo de técnico,
torneios de seleção) por PARTIDA × TIME, pra modelos de árvore (CatBoost/
XGBoost/LightGBM). Persiste em match_features_contexto (upsert idempotente).

FEATURES E LIMITAÇÕES HONESTAS (documentadas, não escondidas):

1. days_since_last_match / hours_since_last_match / is_midweek_fatigue (<72h):
   calculadas sobre TODAS as partidas do time presentes no NOSSO banco —
   liga nacional + Champions/Libertadores/copas que importamos. Europa
   League e copas domésticas europeias (FA Cup, Copa del Rey etc.) NÃO
   estão no banco, então o descanso real pode ser menor que o calculado
   pra times europeus em semanas de copa. Nulo (primeiro jogo do time no
   histórico) -> preenchido com 7 dias + days_filled=true.

2. travel_distance_km: haversine entre o estádio do JOGO ANTERIOR do time e
   o estádio do jogo atual (match_context_fotmob.stadium_lat/long). Se o
   jogo anterior foi em casa (mesmo estádio/cidade), a distância tende a 0
   naturalmente. Sem coordenadas de um dos lados -> 0 + travel_filled=true.

3. manager_match_count / is_honeymoon_phase (primeiros 4 jogos do técnico):
   reconstruídos de team_coach_history_fotmob, que tem granularidade
   TEMPORADA × técnico × liga (V+E+D = jogos de liga daquele técnico na
   temporada, em ordem cronológica de passagem). A vigência por partida é
   APROXIMADA: os primeiros N jogos de liga da temporada vão pro 1º técnico
   da lista (N = jogos dele), os próximos pro 2º, etc. manager_match_count
   acumula temporadas anteriores CONTÍNUAS do mesmo técnico no mesmo time.
   Só cobre jogos de LIGA (copas ficam com o count da liga vigente).
   Time/temporada sem histórico de técnico -> manager_match_count=30
   (valor estável "técnico estabelecido", média segura pra gradient
   boosting) + manager_filled=true + is_honeymoon_phase=0.

4. key_players_missing_torneo / squad_natl_matches_window (PROXIES):
   minutos reais de seleção por jogador NÃO existem no banco (Copa/Euro só
   em nível de time; Copa Africana nem existe na base). O proxy conta:
   - titulares = jogadores com >=450 min pelo clube nos 10 jogos anteriores
     (match_player_stats_fotmob);
   - cujo país (players.country_code -> teams de seleção via nome ISO não
     é confiável; usa-se o crosswalk country_name -> teams.is_national_team
     por nome) disputou partida de torneio de seleções (ligas 3=Copa,
     22=Eurocopa no nosso banco) numa janela de 30 dias antes do jogo.
   squad_natl_matches_window = total de partidas de seleção na janela
   somado sobre esses jogadores. Cobertura limitada aos torneios que temos.

Uso:
    SUPABASE_URL=... SUPABASE_KEY=... python3 features_contexto.py [--liga-id N] [--temporada AAAA]
    (sem filtros processa todas as partidas das ligas do pipeline)
"""

import argparse
import datetime as dt
import math
import os
import sys
from collections import defaultdict

from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

DIAS_DEFAULT = 7.0
MANAGER_COUNT_DEFAULT = 30
HONEYMOON_JOGOS = 4
JANELA_TORNEIO_DIAS = 30
MIN_MINUTOS_TITULAR = 450
LIGAS_SELECOES = [3, 22]  # Copa do Mundo, Eurocopa (ids internos)

# league_id interno -> fotmob_league_id (pra casar temporada do coachHistory)
FOTMOB_LIGA = {1: 268, 4: 47, 7: 87, 10: 55, 13: 54, 16: 53}


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def parse_dt(s):
    return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def buscar_paginado(supabase, tabela, colunas, filtros=None, in_filtros=None):
    resultado, pagina = [], 0
    while True:
        q = supabase.table(tabela).select(colunas)
        for chave, valor in (filtros or {}).items():
            q = q.eq(chave, valor)
        for chave, valores in (in_filtros or {}).items():
            q = q.in_(chave, valores)
        chunk = q.range(pagina * 1000, pagina * 1000 + 999).execute().data
        resultado.extend(chunk)
        if len(chunk) < 1000:
            break
        pagina += 1
    return resultado


def temporada_fotmob(season, liga_id):
    """Nosso season '2022' vira '2022/2023' nas ligas europeias (ago-mai) e
    '2022' no Brasileirão (ano civil)."""
    if liga_id == 1:
        return str(season)
    try:
        a = int(season)
        return f"{a}/{a + 1}"
    except (TypeError, ValueError):
        return str(season)


def montar_vigencias_tecnico(coach_rows):
    """(team_id, season_fotmob, fotmob_league_id) -> lista ordenada de
    (coach_id, coach_name, jogos_na_temporada). Também devolve, por
    (team_id, coach_id), o total de jogos em temporadas ANTERIORES contínuas
    — aproximação do acumulado de carreira no clube."""
    por_chave = defaultdict(list)
    for r in sorted(coach_rows, key=lambda x: (x["team_id"], x["season"] or "", x["order_idx"])):
        jogos = (r["wins"] or 0) + (r["draws"] or 0) + (r["losses"] or 0)
        por_chave[(r["team_id"], r["season"], r["fotmob_league_id"])].append(
            {"coach_id": r["coach_fotmob_id"], "nome": r["coach_name"], "jogos": jogos}
        )
    return por_chave


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--liga-id", type=int, default=None)
    ap.add_argument("--temporada", type=str, default=None)
    args = ap.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        sys.exit("Defina SUPABASE_URL e SUPABASE_KEY.")
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    print("Carregando dados base...")
    filtros_matches = {"status": "finished"}
    if args.liga_id:
        filtros_matches["league_id"] = args.liga_id
    if args.temporada:
        filtros_matches["season"] = args.temporada
    alvo = buscar_paginado(supabase, "matches", "id, league_id, season, match_date, home_team_id, away_team_id", filtros_matches)
    print(f"  {len(alvo)} partidas alvo.")

    todas = buscar_paginado(supabase, "matches", "id, league_id, season, match_date, home_team_id, away_team_id, status")
    contextos = buscar_paginado(supabase, "match_context_fotmob", "match_id, stadium_lat, stadium_long")
    coach_rows = buscar_paginado(supabase, "team_coach_history_fotmob", "team_id, coach_fotmob_id, coach_name, season, fotmob_league_id, wins, draws, losses, order_idx")
    print(f"  {len(todas)} partidas no banco, {len(contextos)} contextos, {len(coach_rows)} linhas de técnico.")

    coords = {c["match_id"]: (c["stadium_lat"], c["stadium_long"]) for c in contextos if c["stadium_lat"] is not None}

    # Agenda completa por time (qualquer competição no banco), ordenada por data
    agenda = defaultdict(list)
    for m in todas:
        if m["status"] != "finished":
            continue
        d = parse_dt(m["match_date"])
        agenda[m["home_team_id"]].append((d, m["id"], True))
        agenda[m["away_team_id"]].append((d, m["id"], False))
    for t in agenda:
        agenda[t].sort(key=lambda x: x[0])
    posicao = {t: {mid: i for i, (_, mid, _) in enumerate(lst)} for t, lst in agenda.items()}

    # Jogos de LIGA por (time, liga, temporada) em ordem — pra vigência de técnico
    liga_jogos = defaultdict(list)
    for m in todas:
        if m["status"] != "finished":
            continue
        d = parse_dt(m["match_date"])
        liga_jogos[(m["home_team_id"], m["league_id"], m["season"])].append((d, m["id"]))
        liga_jogos[(m["away_team_id"], m["league_id"], m["season"])].append((d, m["id"]))
    for k in liga_jogos:
        liga_jogos[k].sort(key=lambda x: x[0])

    vigencias = montar_vigencias_tecnico(coach_rows)

    # Acumulado de temporadas anteriores por (team, coach): season em ordem
    acumulado_prev = defaultdict(int)
    temporadas_ordenadas = sorted({r["season"] for r in coach_rows if r["season"]})
    acum_por_temporada = {}
    for (team_id, season, lid), lista in sorted(vigencias.items(), key=lambda kv: (kv[0][0], kv[0][1] or "")):
        for item in lista:
            chave = (team_id, item["coach_id"])
            acum_por_temporada[(team_id, season, lid, item["coach_id"])] = acumulado_prev[chave]
            acumulado_prev[chave] += item["jogos"]

    # Partidas de seleções na janela (torneios que temos) + país por seleção
    natl = buscar_paginado(supabase, "matches", "id, match_date, home_team_id, away_team_id", in_filtros={"league_id": LIGAS_SELECOES})
    natl_team_ids = sorted({m["home_team_id"] for m in natl} | {m["away_team_id"] for m in natl})
    selecoes = buscar_paginado(supabase, "teams", "id, name", in_filtros={"id": natl_team_ids}) if natl_team_ids else []
    nome_selecao = {s["id"]: (s["name"] or "").lower() for s in selecoes}
    jogos_selecao_por_nome = defaultdict(list)
    for m in natl:
        d = parse_dt(m["match_date"])
        for tid in (m["home_team_id"], m["away_team_id"]):
            jogos_selecao_por_nome[nome_selecao.get(tid, "")].append(d)

    jogadores = buscar_paginado(supabase, "players", "id, country_name")
    pais_jogador = {j["id"]: (j["country_name"] or "").lower() for j in jogadores}

    # Minutos por (team, match) -> {player: min}, carregados só pros match_ids
    # que ENTRAM na janela de 10 jogos anteriores de algum time-alvo — a
    # tabela inteira (match_player_stats_fotmob) já passa de 250k linhas
    # depois da expansão europeia e um .range() sem filtro dava timeout no
    # Postgres (statement timeout), mesmo paginado. Filtrar por match_id
    # relevante reduz o volume em ordens de grandeza sem perder nada (só os
    # jogos de 10-em-10 anteriores a cada partida-alvo importam pro proxy).
    match_ids_relevantes = set()
    for m in alvo:
        for team_id in (m["home_team_id"], m["away_team_id"]):
            pos = posicao.get(team_id, {}).get(m["id"])
            if pos:
                for _, mid, _ in agenda[team_id][max(0, pos - 10):pos]:
                    match_ids_relevantes.add(mid)
    match_ids_relevantes = list(match_ids_relevantes)
    print(f"Carregando minutos de jogador pra {len(match_ids_relevantes)} partidas relevantes...")
    stats_all = []
    for lote_ids in [match_ids_relevantes[i:i + 500] for i in range(0, len(match_ids_relevantes), 500)]:
        stats_all.extend(buscar_paginado(
            supabase, "match_player_stats_fotmob", "team_id, match_id, player_id, minutes_played",
            in_filtros={"match_id": lote_ids},
        ))
    minutos_por_time_jogo = defaultdict(dict)
    for s in stats_all:
        if s["player_id"] is not None:
            minutos_por_time_jogo[(s["team_id"], s["match_id"])][s["player_id"]] = s["minutes_played"] or 0
    print(f"  {len(stats_all)} linhas de minutos carregadas.")

    def features_torneio(team_id, match_id, data_jogo):
        pos = posicao.get(team_id, {}).get(match_id)
        if pos is None or pos == 0:
            return 0, 0
        minutos = defaultdict(int)
        for _, mid, _ in agenda[team_id][max(0, pos - 10):pos]:
            for pid, mins in minutos_por_time_jogo.get((team_id, mid), {}).items():
                minutos[pid] += mins
        titulares = [pid for pid, m in minutos.items() if m >= MIN_MINUTOS_TITULAR]
        ini = data_jogo - dt.timedelta(days=JANELA_TORNEIO_DIAS)
        desfalcados, total_jogos = 0, 0
        for pid in titulares:
            pais = pais_jogador.get(pid, "")
            if not pais:
                continue
            jogos_na_janela = [d for d in jogos_selecao_por_nome.get(pais, []) if ini <= d < data_jogo]
            if jogos_na_janela:
                desfalcados += 1
                total_jogos += len(jogos_na_janela)
        return desfalcados, total_jogos

    linhas = []
    for m in alvo:
        data_jogo = parse_dt(m["match_date"])
        for team_id, em_casa in ((m["home_team_id"], True), (m["away_team_id"], False)):
            pos = posicao.get(team_id, {}).get(m["id"])
            row = {"match_id": m["id"], "team_id": team_id, "computed_at": dt.datetime.now(dt.timezone.utc).isoformat()}

            # 1) descanso
            if pos is None or pos == 0:
                row.update({"days_since_last_match": DIAS_DEFAULT, "hours_since_last_match": DIAS_DEFAULT * 24, "days_filled": True, "is_midweek_fatigue": 0})
                anterior_id = None
            else:
                data_ant, anterior_id, _ = agenda[team_id][pos - 1]
                horas = (data_jogo - data_ant).total_seconds() / 3600
                row.update({
                    "days_since_last_match": round(horas / 24, 2),
                    "hours_since_last_match": round(horas, 1),
                    "days_filled": False,
                    "is_midweek_fatigue": 1 if horas < 72 else 0,
                })

            # 2) deslocamento
            c_atual = coords.get(m["id"])
            c_ant = coords.get(anterior_id) if anterior_id else None
            if c_atual and c_ant:
                row["travel_distance_km"] = round(haversine_km(c_ant[0], c_ant[1], c_atual[0], c_atual[1]), 1)
                row["travel_filled"] = False
            else:
                row["travel_distance_km"] = 0
                row["travel_filled"] = True

            # 3) técnico (aproximação por ordem na temporada, só liga)
            chave_v = (team_id, temporada_fotmob(m["season"], m["league_id"]), FOTMOB_LIGA.get(m["league_id"]))
            lista_tecnicos = vigencias.get(chave_v)
            jogos_liga = liga_jogos.get((team_id, m["league_id"], m["season"]), [])
            idx_liga = next((i for i, (_, mid) in enumerate(jogos_liga) if mid == m["id"]), None)
            if lista_tecnicos and idx_liga is not None:
                acumulado, tecnico = 0, None
                for item in lista_tecnicos:
                    if idx_liga < acumulado + item["jogos"]:
                        tecnico = item
                        idx_no_tecnico = idx_liga - acumulado
                        break
                    acumulado += item["jogos"]
                if tecnico is None:  # jogo além da soma (dado incompleto) — usa o último
                    tecnico = lista_tecnicos[-1]
                    idx_no_tecnico = idx_liga - (acumulado - lista_tecnicos[-1]["jogos"])
                count = acum_por_temporada.get((team_id, chave_v[1], chave_v[2], tecnico["coach_id"]), 0) + idx_no_tecnico + 1
                row.update({
                    "manager_match_count": count,
                    "manager_filled": False,
                    "is_honeymoon_phase": 1 if count <= HONEYMOON_JOGOS else 0,
                    "coach_name": tecnico["nome"],
                })
            else:
                row.update({"manager_match_count": MANAGER_COUNT_DEFAULT, "manager_filled": True, "is_honeymoon_phase": 0, "coach_name": None})

            # 4) torneios de seleção (proxy)
            desf, njogos = features_torneio(team_id, m["id"], data_jogo)
            row["key_players_missing_torneo"] = desf
            row["squad_natl_matches_window"] = njogos

            linhas.append(row)

    print(f"Gravando {len(linhas)} linhas de features...")
    for i in range(0, len(linhas), 500):
        supabase.table("match_features_contexto").upsert(linhas[i:i + 500], on_conflict="match_id,team_id").execute()
    print("OK.")


if __name__ == "__main__":
    main()
