"""
Ingestão de dados do FotMob (API não-oficial, endpoints internos do site
www.fotmob.com/api/data/*) para o Supabase (projeto quant-futebol-dados).

Não é uma API pública documentada nem com termos de uso pra consumo
programático — é o mesmo endpoint que o site usa pra se renderizar. Sem
autenticação, sem SLA, sujeito a mudar sem aviso. Usar com moderação (pacing
conservador abaixo) para não arriscar bloqueio de IP. Dado é público
(estatística esportiva sem paywall), mesmo espírito de scraping já usado
neste projeto para FBref/Understat.

A lib `fotmob-api` do PyPI está DESATUALIZADA (usa `/api/*` sem o segmento
`data`, retorna 404 em tudo) — por isso este script chama a API diretamente
via `requests`, sem essa dependência.

Uso:
    1. pip install requests supabase
    2. Defina as variáveis de ambiente:
         SUPABASE_URL -> https://cgurxgfdmpmsnrshqycx.supabase.co
         SUPABASE_KEY -> service_role key (Settings > API no painel do Supabase)
       (sem default hardcoded de propósito — ver pendência de segurança
       documentada em CONTEXTO_PROJETO.md sobre chave exposta nos outros
       scripts desta pasta; não repetir o padrão aqui)
    3. python ingestao_fotmob.py --liga-id 1 --fotmob-league-id 268 --temporada 2026

Pré-requisito: crosswalk de times em team_source_ids (source='fotmob') já
resolvido pra liga em questão — ver INSTRUÇÕES DE CROSSWALK abaixo. Esse
script NÃO cria crosswalk de time automaticamente (mesma disciplina já usada
pra liga_oddspapi_tournament: mapeamento crítico não se resolve por
heurística sem supervisão) — ele só lê o que já existe. Rodado inicialmente
pra Brasileirão (liga_id=1, fotmob-league-id=268) com os 20 times mapeados
manualmente (nomes batem 1:1 por identidade de clube, sem ambiguidade).

INSTRUÇÕES DE CROSSWALK (pra expandir a outras ligas):
    1. GET https://www.fotmob.com/api/data/fixtures?id={fotmob_league_id}&season={ano}
    2. Extrair times únicos (fx['home']['id']/['name'], fx['away']['id']/['name'])
    3. Casar manualmente com teams.name (ou usar external_id/team_source_ids
       de outra fonte já confirmada como ponte) e inserir em team_source_ids
       com source='fotmob' — confirmar cada par antes de gravar, não é lote
       grande o bastante pra justificar heurística automática sem checagem.

Idempotente: já-sincronizados ficam registrados em match_source_ids
(source='fotmob'), pulados em reruns a menos que --forcar seja passado.

Também popula `match_context_fotmob` (estádio com lat/long, árbitro, público e
clima observado — temperatura/vento/umidade/precipitação/cobertura de nuvens)
a partir do MESMO matchDetails já buscado acima, sem chamada de API extra.
Achado por inspeção direta do JSON: Stadium existe pra quase toda partida
(até 2023), mas weather só vem preenchido pra temporada atual/mais recente de
cada competição — partidas antigas ficam com as colunas weather_* NULL, não é
bug.

Também popula/atualiza a tabela `players` (dimensão de jogador — nome, foto,
idade, país, valor de mercado) a partir do bloco `lineup` de cada partida
processada. É um SNAPSHOT (upsert por fotmob_player_id), não histórico —
idade/valor de mercado refletem a última vez que o jogador foi visto em
campo, não uma série temporal. photo_url é construído deterministicamente
(padrão confirmado: images.fotmob.com/image_resources/playerimages/{id}.png,
não precisa buscar). Bandeira de país não tem URL própria confirmada no CDN
do FotMob (testado, 403) — só country_code (ISO) fica disponível.

Também popula `match_lineup_fotmob` -- ao contrário de `players.raw_lineup`
(snapshot, sobrescrito a cada partida), essa tabela guarda um HISTÓRICO por
partida de quem começou titular vs. reserva (content.lineup.{home,away}Team.
{starters,subs}), base pra features de "XI titular" (v3B do Model
Benchmarking). Só aplica a partidas processadas DAQUI PRA FRENTE (ou
reprocessadas com --forcar) -- partidas já sincronizadas antes desta mudança
não têm linha aqui.
"""

import argparse
import json
import os
import sys
import time

import requests
from supabase import create_client

SUPABASE_URL = (os.environ.get("SUPABASE_URL") or "").strip()
SUPABASE_KEY = (os.environ.get("SUPABASE_KEY") or "").strip()

BASE = "https://www.fotmob.com/api/data"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
}
PACING_SEGUNDOS = 1.3

INT_COLS_TEAM_STATS = [
    "total_shots", "shots_on_target", "shots_off_target", "shots_blocked",
    "shots_inside_box", "shots_outside_box", "big_chances", "big_chances_missed",
    "touches_opp_box", "accurate_passes", "accurate_long_balls", "accurate_crosses",
    "corners", "tackles", "interceptions", "blocks", "clearances", "keeper_saves",
    "duels_won", "aerial_duels_won", "successful_dribbles", "fouls_committed",
    "yellow_cards", "red_cards",
]


def pegar(grupo_por_chave: dict, grupo_key: str, stat_key: str, idx_lado: int):
    """Extrai um stat de time do bloco content.stats.Periods.All.stats já
    reindexado por (grupo, chave). Chaves reais confirmadas por inspeção direta
    do JSON — os nomes em inglês do título NÃO batem com a chave interna em
    vários casos (ex: título 'Duels won' -> chave real 'duel_won', 'Fouls
    committed' -> 'fouls', 'Tackles' -> 'matchstats.headers.tackles'). Cartão
    amarelo tem chave 'yellow_cards' em DOIS grupos (top_stats sempre vem
    null, o valor real está em discipline) — usar sempre discipline."""
    g = grupo_por_chave.get(grupo_key, {})
    s = g.get(stat_key)
    if not s:
        return None
    vals = s.get("stats")
    if not vals or len(vals) < 2:
        return None
    v = vals[idx_lado]
    if isinstance(v, str):
        v = v.split(" ")[0].replace("%", "")
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def pegar_com_fallback_top_stats(grupo_por_chave: dict, grupo_key: str, stat_key: str, idx_lado: int, stat_key_top_stats: str):
    """Como `pegar`, mas com fallback pra `top_stats` quando o grupo mais
    detalhado (`grupo_key`) nem existe no payload -- partidas antigas (achado
    confirmado em La Liga 2014/15-2015/16) só trazem a seção `top_stats`,
    sem `shots`/`discipline` separados, mesmo quando `top_stats` já carrega o
    número (`total_shots` e `ShotsOnTarget` confirmados por inspeção direta;
    NÃO usar esse fallback pra cartão -- `top_stats.yellow_cards` vem null
    até em partida moderna com `discipline` presente, ver docstring de
    `pegar`). Só cai no fallback quando o grupo principal está totalmente
    ausente, não quando só o valor individual é null (isso continua
    None -- pode ser uma lacuna real, não confundir com formato antigo)."""
    if grupo_key not in grupo_por_chave:
        return pegar(grupo_por_chave, "top_stats", stat_key_top_stats, idx_lado)
    return pegar(grupo_por_chave, grupo_key, stat_key, idx_lado)


def extrair_stat_jogador(stats_dict: dict, chave_titulo: str):
    item = stats_dict.get(chave_titulo)
    if not item:
        return None
    return item["stat"].get("value")


def parse_contexto_jogo(d: dict, match_id: int, fotmob_match_id):
    """Estádio (nome/cidade/país/lat/long) e clima observado (temperatura/vento/
    umidade/precipitação/etc), extraídos de content.matchFacts.infoBox.Stadium e
    content.weather — MESMO payload de matchDetails já buscado pra stats/jogadores,
    zero chamada de API extra. Achado por inspeção direta do JSON real (mesma
    disciplina de sempre neste script): Stadium existe pra praticamente toda
    partida (até as de 2023), mas weather só vem preenchido pra temporada
    atual/mais recente de cada competição — partidas antigas retornam
    content.weather ausente (None), não é bug, é limitação real da fonte."""
    content = d.get("content") or {}
    info_box = ((content.get("matchFacts") or {}).get("infoBox")) or {}
    stadium = info_box.get("Stadium") or {}
    weather = content.get("weather") or {}
    referee = (info_box.get("Referee") or {}).get("text")
    attendance = info_box.get("Attendance")

    return {
        "match_id": match_id,
        "fotmob_match_id": str(fotmob_match_id),
        "stadium_name": stadium.get("name"),
        "stadium_city": stadium.get("city"),
        "stadium_country": stadium.get("country"),
        "stadium_lat": stadium.get("lat"),
        "stadium_long": stadium.get("long"),
        "attendance": attendance if isinstance(attendance, int) else None,
        "referee": referee,
        "weather_temperature_c": weather.get("temperature"),
        "weather_wind_speed": weather.get("windSpeed"),
        "weather_wind_direction": weather.get("windDirectionCardinal"),
        "weather_humidity": weather.get("relativeHumidity"),
        "weather_precipitation": weather.get("precipitation"),
        "weather_snow": weather.get("snow"),
        "weather_cloud_cover": weather.get("cloudCover"),
        "weather_description": weather.get("description"),
        "weather_api_used": weather.get("apiUsed"),
        "weather_last_updated": weather.get("lastUpdated"),
        "stats_raw": {"stadium": stadium or None, "weather": weather or None, "referee": referee, "attendance": attendance},
    }


def parse_match_details(d: dict, match_id: int, home_team_id: int, away_team_id: int):
    content = d.get("content") or {}
    team_rows = []
    player_rows = []
    shot_rows = []

    stats_periods = (((content.get("stats") or {}).get("Periods") or {}).get("All") or {}).get("stats")
    if stats_periods:
        grupo_por_chave = {}
        for grupo in stats_periods:
            for s in grupo["stats"]:
                grupo_por_chave.setdefault(grupo["key"], {})[s["key"]] = s

        for lado, team_id in ((0, home_team_id), (1, away_team_id)):
            row = {
                "match_id": match_id,
                "team_id": team_id,
                "possession": pegar(grupo_por_chave, "top_stats", "BallPossesion", lado),
                "xg": pegar(grupo_por_chave, "expected_goals", "expected_goals", lado),
                "xg_open_play": pegar(grupo_por_chave, "expected_goals", "expected_goals_open_play", lado),
                "xg_set_play": pegar(grupo_por_chave, "expected_goals", "expected_goals_set_play", lado),
                "xg_non_penalty": pegar(grupo_por_chave, "expected_goals", "expected_goals_non_penalty", lado),
                "xgot": pegar(grupo_por_chave, "expected_goals", "expected_goals_on_target", lado),
                "total_shots": pegar_com_fallback_top_stats(grupo_por_chave, "shots", "total_shots", lado, "total_shots"),
                "shots_on_target": pegar_com_fallback_top_stats(grupo_por_chave, "shots", "ShotsOnTarget", lado, "ShotsOnTarget"),
                "shots_off_target": pegar(grupo_por_chave, "shots", "ShotsOffTarget", lado),
                "shots_blocked": pegar(grupo_por_chave, "shots", "blocked_shots", lado),
                "shots_inside_box": pegar(grupo_por_chave, "shots", "shots_inside_box", lado),
                "shots_outside_box": pegar(grupo_por_chave, "shots", "shots_outside_box", lado),
                "big_chances": pegar(grupo_por_chave, "top_stats", "big_chance", lado),
                "big_chances_missed": pegar(grupo_por_chave, "top_stats", "big_chance_missed_title", lado),
                "touches_opp_box": pegar(grupo_por_chave, "top_stats", "touches_opp_box", lado),
                "accurate_passes": pegar(grupo_por_chave, "top_stats", "accurate_passes", lado),
                "accurate_long_balls": pegar(grupo_por_chave, "passes", "long_balls_accurate", lado),
                "accurate_crosses": pegar(grupo_por_chave, "passes", "accurate_crosses", lado),
                "corners": pegar(grupo_por_chave, "top_stats", "corners", lado),
                "tackles": pegar(grupo_por_chave, "defence", "matchstats.headers.tackles", lado),
                "interceptions": pegar(grupo_por_chave, "defence", "interceptions", lado),
                "blocks": pegar(grupo_por_chave, "defence", "shot_blocks", lado),
                "clearances": pegar(grupo_por_chave, "defence", "clearances", lado),
                "keeper_saves": pegar(grupo_por_chave, "defence", "keeper_saves", lado),
                "duels_won": pegar(grupo_por_chave, "duels", "duel_won", lado),
                "aerial_duels_won": pegar(grupo_por_chave, "duels", "aerials_won", lado),
                "successful_dribbles": pegar(grupo_por_chave, "duels", "dribbles_succeeded", lado),
                "fouls_committed": pegar(grupo_por_chave, "discipline", "fouls", lado),
                "yellow_cards": pegar(grupo_por_chave, "discipline", "yellow_cards", lado),
                "red_cards": pegar(grupo_por_chave, "discipline", "red_cards", lado),
                "stats_raw": stats_periods,
            }
            for c in INT_COLS_TEAM_STATS:
                if row.get(c) is not None:
                    row[c] = int(round(row[c]))
            team_rows.append(row)

    return team_rows, content


def processar_matchdetails_completo(d: dict, match_id: int, fotmob_match_id, fotmob_to_internal: dict) -> dict:
    """Extrai TODAS as tabelas derivadas de um payload `matchDetails` já
    carregado (`d`) -- time (`match_stats_fotmob`), contexto (`match_
    context_fotmob`), dimensão de jogador (`players`), escalação (`match_
    lineup_fotmob`), desempenho por jogador (`match_player_stats_fotmob`)
    e chute a chute (`match_shots_fotmob`). Extraído do corpo de `main()`
    pra ser reaproveitado tanto aqui (payload buscado ao vivo da API)
    quanto por `ingestao_fotmob_dumps_locais.py` (payload já baixado em
    `.json.gz` num repositório separado) -- MESMA lógica de parse pros
    dois casos, sem duplicar e arriscar divergir com o tempo.

    `fotmob_to_internal` é o crosswalk id-do-FotMob -> team_id interno
    relevante PRA ESSA PARTIDA -- o chamador decide como montar (globalmente
    via `team_source_ids`, como faz `main()` abaixo, ou só com os 2 times
    da própria partida via `general.homeTeam`/`general.awayTeam`, como faz
    o script de dumps locais -- mais simples quando o match_id já é
    conhecido de antemão)."""
    import datetime as dt

    content = d.get("content") or {}
    home_team_id = fotmob_to_internal.get(str(d["general"]["homeTeam"]["id"]))
    away_team_id = fotmob_to_internal.get(str(d["general"]["awayTeam"]["id"]))
    team_rows, _ = parse_match_details(d, match_id, home_team_id, away_team_id)

    contexto_row = parse_contexto_jogo(d, match_id, fotmob_match_id)

    player_dim_rows = []
    lineup_rows = []
    lineup = content.get("lineup") or {}
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

    player_rows = []
    for pid, pdata in (content.get("playerStats") or {}).items():
        team_id = fotmob_to_internal.get(str(pdata.get("teamId")))
        if team_id is None:
            continue
        fotmob_player_id = str(pdata.get("id"))
        stats_by_group = {g["key"]: g["stats"] for g in pdata.get("stats", [])}
        top_g = stats_by_group.get("top_stats", {})
        attack_g = stats_by_group.get("attack", {})
        row = {
            "match_id": match_id,
            "team_id": team_id,
            "fotmob_player_id": fotmob_player_id,
            "player_name": pdata.get("name"),
            "is_goalkeeper": pdata.get("isGoalkeeper", False),
            "rating": extrair_stat_jogador(top_g, "FotMob rating"),
            "minutes_played": extrair_stat_jogador(top_g, "Minutes played"),
            "goals": extrair_stat_jogador(top_g, "Goals"),
            "assists": extrair_stat_jogador(top_g, "Assists"),
            "xg": extrair_stat_jogador(top_g, "Expected goals (xG)"),
            "xa": extrair_stat_jogador(top_g, "Expected assists (xA)"),
            "xgot": extrair_stat_jogador(top_g, "Expected goals on target (xGOT)"),
            "total_shots": extrair_stat_jogador(top_g, "Total shots"),
            "chances_created": extrair_stat_jogador(top_g, "Chances created"),
            "accurate_passes": (top_g.get("Accurate passes") or {}).get("stat", {}).get("value"),
            "touches": (attack_g.get("Touches") or {}).get("stat", {}).get("value"),
            "tackles": (stats_by_group.get("defense", {}).get("Tackles won") or {}).get("stat", {}).get("value"),
            "interceptions": (stats_by_group.get("defense", {}).get("Interceptions") or {}).get("stat", {}).get("value"),
            "ground_duels_won": (stats_by_group.get("duels", {}).get("Ground duels won") or {}).get("stat", {}).get("value"),
            "aerials_won": (stats_by_group.get("duels", {}).get("Aerial duels won") or {}).get("stat", {}).get("value"),
            "touches_opp_box": (attack_g.get("Touches in opposition box") or {}).get("stat", {}).get("value"),
            "stats_raw": pdata.get("stats"),
        }
        
        # Clean up formats like "3/4 (75%)" for duels/tackles
        for k in ["tackles", "interceptions", "ground_duels_won", "aerials_won", "touches_opp_box"]:
            v = row[k]
            if isinstance(v, str):
                v = v.split()[0]
                if '/' in v:
                    v = v.split('/')[0]
                try:
                    row[k] = int(v)
                except ValueError:
                    row[k] = None
                    
        player_rows.append(row)

    shot_rows = []
    for s in (content.get("shotmap") or {}).get("shots") or []:
        team_id = fotmob_to_internal.get(str(s.get("teamId")))
        if team_id is None:
            continue
        fotmob_player_id = str(s.get("playerId")) if s.get("playerId") else None
        shot_rows.append({
            "fotmob_shot_id": s["id"],
            "match_id": match_id,
            "team_id": team_id,
            "fotmob_player_id": fotmob_player_id,
            "player_name": s.get("playerName") or s.get("fullName"),
            "minute": s.get("min"),
            "minute_added": s.get("minAdded"),
            "x": s.get("x"),
            "y": s.get("y"),
            "xg": s.get("expectedGoals"),
            "xgot": s.get("expectedGoalsOnTarget"),
            "shot_type": s.get("shotType"),
            "situation": s.get("situation"),
            "event_type": s.get("eventType"),
            "is_on_target": s.get("isOnTarget"),
            "is_blocked": s.get("isBlocked"),
            "is_own_goal": s.get("isOwnGoal"),
            "period": s.get("period"),
        })

    return {
        "team_rows": team_rows,
        "contexto_row": contexto_row,
        "player_dim_rows": player_dim_rows,
        "lineup_rows": lineup_rows,
        "player_rows": player_rows,
        "shot_rows": shot_rows,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--liga-id", type=int, required=True, help="league_id interno (ex: 1 = Brasileirão)")
    ap.add_argument("--fotmob-league-id", type=int, required=True, help="id da liga no FotMob (ex: 268 = Brasileirão)")
    ap.add_argument("--temporada", type=str, required=True, help="temporada no formato do FotMob (ex: 2026 ou 2024/2025)")
    ap.add_argument("--limite", type=int, default=None, help="processa só os N primeiros jogos ainda não sincronizados")
    ap.add_argument("--forcar", action="store_true", help="reprocessa mesmo jogos já em match_source_ids")
    args = ap.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        sys.exit("Defina SUPABASE_URL e SUPABASE_KEY (service_role) como variáveis de ambiente antes de rodar.")

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    crosswalk_res = (
        supabase.table("team_source_ids").select("team_id, source_id").eq("source", "fotmob").execute()
    )
    fotmob_to_internal = {row["source_id"]: row["team_id"] for row in crosswalk_res.data}
    if not fotmob_to_internal:
        sys.exit(
            "Nenhum crosswalk source='fotmob' encontrado em team_source_ids. "
            "Resolva manualmente antes de rodar (ver INSTRUÇÕES DE CROSSWALK no topo deste arquivo)."
        )

    print(f"Crosswalk fotmob carregado: {len(fotmob_to_internal)} times.")

    r = requests.get(f"{BASE}/fixtures", params={"id": args.fotmob_league_id, "season": args.temporada}, headers=HEADERS, timeout=20)
    r.raise_for_status()
    fixtures = r.json()
    finished = [fx for fx in fixtures if fx["status"]["finished"] and not fx["status"].get("cancelled")]
    print(f"Jogos finalizados no FotMob: {len(finished)}")

    if not args.forcar:
        # Mesma paginação explícita do fetch de matches acima — essa tabela já
        # passa de 3.000 linhas; sem .range(), o corte silencioso de 1000 faria
        # jogos já sincronizados parecerem pendentes (re-sync inútil, idempotente
        # mas desperdiçando horas de pacing). `.order("source_id")` é
        # OBRIGATÓRIO junto com `.range()` -- sem ordenação explícita, o
        # PostgREST não garante travessia estável entre páginas numa tabela
        # deste tamanho (bug real encontrado em `ingestao_fotmob_dumps_
        # locais.py`: sem `.order()`, algumas linhas já sincronizadas
        # ficavam de fora da paginação silenciosamente, fazendo partidas já
        # prontas parecerem pendentes pra sempre).
        ja_sync_ids = set()
        _pagina_sync = 0
        while True:
            _chunk_sync = (
                supabase.table("match_source_ids")
                .select("source_id")
                .eq("source", "fotmob")
                .order("source_id")
                .range(_pagina_sync * 1000, _pagina_sync * 1000 + 999)
                .execute()
                .data
            )
            ja_sync_ids.update(row["source_id"] for row in _chunk_sync)
            if len(_chunk_sync) < 1000:
                break
            _pagina_sync += 1
        finished = [fx for fx in finished if str(fx["id"]) not in ja_sync_ids]
        print(f"Ainda não sincronizados: {len(finished)}")

    if args.limite:
        finished = finished[: args.limite]

    # Paginação explícita — .execute() sem .range() corta em 1000 linhas EM
    # SILÊNCIO (bug clássico já documentado várias vezes no projeto): ligas
    # com 8 temporadas têm ~3.000 partidas, e o corte fazia o índice de
    # casamento só enxergar as 1.000 mais antigas — temporadas mais novas
    # caíam 100% em "sem par em matches". `.order("id")` pelo mesmo motivo
    # do bloco acima.
    matches_internos = []
    _pagina = 0
    while True:
        _chunk = (
            supabase.table("matches")
            .select("id, home_team_id, away_team_id, match_date")
            .eq("league_id", args.liga_id)
            .order("id")
            .range(_pagina * 1000, _pagina * 1000 + 999)
            .execute()
            .data
        )
        matches_internos.extend(_chunk)
        if len(_chunk) < 1000:
            break
        _pagina += 1
    idx = {}
    for m in matches_internos:
        idx.setdefault((m["home_team_id"], m["away_team_id"]), []).append(m)

    import datetime as dt

    def casar(home_fm, away_fm, utc_time_str):
        home_id = fotmob_to_internal.get(str(home_fm))
        away_id = fotmob_to_internal.get(str(away_fm))
        if home_id is None or away_id is None:
            return None
        candidatos = idx.get((home_id, away_id), [])
        if not candidatos:
            return None
        alvo = dt.datetime.fromisoformat(utc_time_str.replace("Z", "+00:00"))
        melhor, menor_delta = None, None
        for c in candidatos:
            cd = dt.datetime.fromisoformat(str(c["match_date"]).replace("Z", "+00:00"))
            delta = abs((cd - alvo).total_seconds())
            if delta < 36 * 3600 and (menor_delta is None or delta < menor_delta):
                menor_delta, melhor = delta, c
        return melhor

    n_ok, n_sem_par, n_falha, n_id_reaproveitado = 0, 0, 0, 0

    for i, fx in enumerate(finished):
        casado = casar(fx["home"]["id"], fx["away"]["id"], fx["status"]["utcTime"])
        if not casado:
            n_sem_par += 1
            continue

        match_id = casado["id"]
        home_team_id = fotmob_to_internal[str(fx["home"]["id"])]
        away_team_id = fotmob_to_internal[str(fx["away"]["id"])]

        try:
            r = requests.get(f"{BASE}/matchDetails", params={"matchId": fx["id"]}, headers=HEADERS, timeout=20)
            r.raise_for_status()
            d = r.json()
        except Exception as e:
            print(f"  falha em matchId={fx['id']}: {e}")
            n_falha += 1
            time.sleep(PACING_SEGUNDOS)
            continue

        # Achado real (2026-08-25): o FotMob reaproveita `matchId` antigos pra
        # partidas novas -- `/api/data/fixtures?season=X` continua listando o
        # id associado ao jogo histórico correto, mas `/matchDetails?matchId=`
        # pra esse MESMO id pode devolver um jogo completamente diferente (às
        # vezes anos no futuro) entre os mesmos 2 times, sem erro HTTP nenhum.
        # Sem essa checagem, um re-sync `--forcar` gravaria silenciosamente as
        # stats do jogo ERRADO em cima da partida histórica (achado rodando
        # `--forcar` numa amostra de 10 partidas 2017-2025 que já estavam
        # "sem stats" -- todas tinham essa troca de id, confirmado comparando
        # a data de `header.status.utcTime` contra a data esperada do fixture).
        data_retornada = ((d.get("header") or {}).get("status") or {}).get("utcTime")
        if data_retornada:
            try:
                _esperado = dt.datetime.fromisoformat(fx["status"]["utcTime"].replace("Z", "+00:00"))
                _retornado = dt.datetime.fromisoformat(data_retornada.replace("Z", "+00:00"))
                if abs((_retornado - _esperado).total_seconds()) > 36 * 3600:
                    print(f"  matchId={fx['id']} reaproveitado pelo FotMob (esperado {fx['status']['utcTime']}, veio {data_retornada}) -- pulando sem gravar nada")
                    n_id_reaproveitado += 1
                    time.sleep(PACING_SEGUNDOS)
                    continue
            except (ValueError, TypeError):
                pass

        try:
            extraido = processar_matchdetails_completo(d, match_id, fx["id"], fotmob_to_internal)
        except Exception as e:
            print(f"  falha de parse em matchId={fx['id']}: {e}")
            n_falha += 1
            time.sleep(PACING_SEGUNDOS)
            continue

        if extraido["team_rows"]:
            supabase.table("match_stats_fotmob").upsert(extraido["team_rows"], on_conflict="match_id,team_id").execute()

        supabase.table("match_context_fotmob").upsert(extraido["contexto_row"], on_conflict="match_id").execute()

        # Dimensão de jogador (players) processada ANTES das tabelas de stats
        # pra já ter o mapa fotmob_player_id -> players.id (nosso id interno)
        # disponível na hora de montar player_rows/shot_rows (FK player_id).
        player_dim_rows = extraido["player_dim_rows"]
        lineup_rows = extraido["lineup_rows"]
        fotmob_to_player_id = {}
        if player_dim_rows:
            # Mesma deduplicação (e mesmo motivo) já documentada abaixo pra
            # player_rows: content.lineup pode repetir o mesmo fotmob_player_id
            # (visto em partidas antigas, ex. 2019-2022) -- sem isso, o upsert
            # falha com "ON CONFLICT DO UPDATE command cannot affect row a
            # second time" e derruba o backfill da temporada inteira no meio.
            player_dim_rows = list({row["fotmob_player_id"]: row for row in player_dim_rows}.values())
            resp = supabase.table("players").upsert(player_dim_rows, on_conflict="fotmob_player_id").execute()
            for row in resp.data:
                fotmob_to_player_id[row["fotmob_player_id"]] = row["id"]

        if lineup_rows:
            for row in lineup_rows:
                row["player_id"] = fotmob_to_player_id.get(row["fotmob_player_id"])
            lineup_rows = list({(r["match_id"], r["team_id"], r["fotmob_player_id"]): r for r in lineup_rows}.values())
            supabase.table("match_lineup_fotmob").upsert(lineup_rows, on_conflict="match_id,team_id,fotmob_player_id").execute()

        player_rows = extraido["player_rows"]
        if player_rows:
            # Deduplicação por fotmob_player_id: a chave do dict content.playerStats
            # nem sempre bate com pdata["id"] (mesmo padrão de placeholder "0"/"-1"
            # já documentado em `players` — aqui sem filtro, pode colidir duas
            # chaves diferentes no mesmo id re-derivado) — sem isso, o upsert
            # falhava com "ON CONFLICT DO UPDATE command cannot affect row a
            # second time" e derrubava o backfill da temporada inteira no meio.
            vistos = {}
            for row in player_rows:
                row["player_id"] = fotmob_to_player_id.get(row["fotmob_player_id"])
                vistos[row["fotmob_player_id"]] = row
            supabase.table("match_player_stats_fotmob").upsert(list(vistos.values()), on_conflict="match_id,fotmob_player_id").execute()

        shot_rows = extraido["shot_rows"]
        if shot_rows:
            for row in shot_rows:
                row["player_id"] = fotmob_to_player_id.get(row["fotmob_player_id"]) if row["fotmob_player_id"] else None
            supabase.table("match_shots_fotmob").upsert(shot_rows, on_conflict="fotmob_shot_id").execute()

        supabase.table("match_source_ids").upsert(
            {
                "match_id": match_id,
                "source": "fotmob",
                "source_id": str(fx["id"]),
                "source_name": f"{fx['home']['name']} x {fx['away']['name']}",
            },
            on_conflict="match_id,source",
        ).execute()

        n_ok += 1
        if (i + 1) % 20 == 0:
            print(f"  processados {i + 1}/{len(finished)}...")
        time.sleep(PACING_SEGUNDOS)

    print(f"\nOK: {n_ok} jogos sincronizados, {n_sem_par} sem par em matches, {n_falha} falhas de rede, {n_id_reaproveitado} com matchId reaproveitado pelo FotMob (pulados sem gravar).")


if __name__ == "__main__":
    main()
