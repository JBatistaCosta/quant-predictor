"""
Ingestão de PERFIL AVANÇADO de jogador via FotMob (/api/data/playerData?id=X)
— endpoint DIFERENTE do matchDetails usado no resto do pipeline (esse é
POR JOGADOR, não por partida). Mesmo espírito de scraping não-oficial já
usado neste projeto (ver avisos em ingestao_fotmob.py).

Popula 4 tabelas a partir de UMA chamada por jogador:
  - player_market_value_history: série temporal de valor de mercado
    (marketValues.values) — fonte "scisports" via FotMob, não é preço real
    de mercado de transferência, é uma ESTIMATIVA de terceiro. Histórico
    completo preservado (upsert por chave natural, nunca sobrescreve).
  - player_career_history_fotmob: passagens por clube com aparições/gols/
    assistências por período (careerHistory.careerItems.senior.teamEntries)
    — bem mais rico que players.last_team_id (só o mais recente visto).
  - player_trophies_fotmob: títulos por clube/competição/temporada
    (trophies.playerTrophies).
  - player_details_fotmob: snapshot de altura/pé/contrato/posições/traits
    (percentil comparado a pares da mesma posição) — sobrescrito a cada
    sync, não é histórico.

DELIBERADAMENTE NÃO importado (mesmo endpoint tem, mas ficou de fora por
decisão consciente — grande volume, dependente de temporada específica,
parcialmente redundante com o que já existe): firstSeasonStats.heatmap
(mapa de toques) e firstSeasonStats.shotmap (chute a chute — já coberto
pra partidas do pipeline via match_shots_fotmob).

ACHADO IMPORTANTE sobre esses dois campos (confirmado inspecionando o dado
real do Mbappé): "firstSeasonStats" NÃO é a carreira inteira, é só a
competição/temporada mais recente mostrada por padrão na página do
jogador no FotMob (no caso testado, só ~41 chutes, todos de um período de
~1 mês — a Copa do Mundo 2026, nem a temporada de clube). A carreira
completa existe só como ÍNDICE em `statSeasons` (lista temporada×
competição com um `entryId` por combinação, ex: "2025/2026"+LaLiga =
"1-0") — puxar heatmap/shotmap de uma temporada ESPECÍFICA exigiria
descobrir um sub-endpoint por entryId que não foi explorado ainda. Se
algum dia for retomado: o custo vira 1 chamada POR TEMPORADA POR JOGADOR
(não 1 por jogador), ordem de grandeza maior que o já caro backfill deste
script — validar com 1-2 chamadas de descoberta antes de generalizar,
mesma disciplina de sempre.

CUSTO: 1 chamada de API por jogador (bem mais caro que os scripts
anteriores, que gastavam 1 chamada por PARTIDA ou por TIME). Com ~7.900
jogadores na base hoje, rodar tudo de uma vez leva bem mais tempo — por
isso o pacing é mais conservador (1.5s) e o script aceita processar em
lotes (--limite) e retomar sozinho (pula quem já tem player_details_fotmob,
a menos que --forcar).

Uso:
    SUPABASE_URL=... SUPABASE_KEY=... python3 ingestao_fotmob_perfil_jogador.py [--limite N] [--player-id X] [--forcar]

    Sem argumentos, processa TODOS os jogadores pendentes (pode levar horas
    pra base inteira) — rode com --limite N pra testar num grupo pequeno
    primeiro (ex: --limite 50) antes de deixar rodando sem limite.
"""

import argparse
import datetime as dt
import os
import sys
import time

import requests
from supabase import create_client

# .strip() -- secrets do GitHub Actions colados via copy-paste às vezes
# carregam um '\n' final, que quebra o parser de URL do httpx com
# "Invalid non-printable ASCII character in URL" na criação do client
# (mesmo bug já documentado/corrigido em rodar_predicoes.py) -- achado
# rodando este script via Actions pela 1ª vez (antes só era rodado local).
SUPABASE_URL = (os.environ.get("SUPABASE_URL") or "").strip()
SUPABASE_KEY = (os.environ.get("SUPABASE_KEY") or "").strip()

BASE = "https://www.fotmob.com/api/data"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
}
PACING_SEGUNDOS = 1.5


def buscar_paginado(supabase, tabela, colunas, filtros=None):
    resultado, pagina = [], 0
    while True:
        q = supabase.table(tabela).select(colunas)
        for chave, valor in (filtros or {}).items():
            q = q.eq(chave, valor)
        chunk = q.range(pagina * 1000, pagina * 1000 + 999).execute().data
        resultado.extend(chunk)
        if len(chunk) < 1000:
            break
        pagina += 1
    return resultado


def parse_data(s):
    if not s:
        return None
    return str(s)[:10]  # YYYY-MM-DD, corta hora se vier junto


def extrair_valor_playerinfo(campo_titulo, player_information):
    for item in player_information or []:
        if item.get("title") == campo_titulo:
            return item.get("value") or {}
    return {}


def montar_linhas(player_id, payload):
    valores_mercado = []
    for v in (payload.get("marketValues") or {}).get("values") or []:
        data_v = parse_data(v.get("date"))
        if not data_v:
            continue
        valores_mercado.append({
            "player_id": player_id,
            "value_date": data_v,
            "value_eur": v.get("value"),
            "lower_bound_eur": v.get("lowerBound"),
            "upper_bound_eur": v.get("upperBound"),
            "source": v.get("source"),
            "team_fotmob_id": str(v.get("teamId")) if v.get("teamId") else None,
            "team_name": v.get("teamName"),
            "is_period_start": bool(v.get("isPeriodStart")),
            "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        })

    carreira = []
    senior = (((payload.get("careerHistory") or {}).get("careerItems") or {}).get("senior") or {})
    for t in senior.get("teamEntries") or []:
        data_inicio = parse_data(t.get("startDate"))
        if not data_inicio:
            continue
        carreira.append({
            "player_id": player_id,
            "team_fotmob_id": str(t.get("teamId")) if t.get("teamId") else None,
            "team_name": t.get("team"),
            "start_date": data_inicio,
            "end_date": parse_data(t.get("endDate")),
            "active": bool(t.get("active")),
            "transfer_type": (t.get("transferType") or {}).get("text"),
            "appearances": int(t["appearances"]) if str(t.get("appearances") or "").isdigit() else None,
            "goals": int(t["goals"]) if str(t.get("goals") or "").isdigit() else None,
            "assists": int(t["assists"]) if str(t.get("assists") or "").isdigit() else None,
            "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        })

    titulos = []
    for time_troféus in (payload.get("trophies") or {}).get("playerTrophies") or []:
        for torneio in time_troféus.get("tournaments") or []:
            for temporada in torneio.get("seasonsWon") or []:
                titulos.append({
                    "player_id": player_id,
                    "team_fotmob_id": str(time_troféus.get("teamId")) if time_troféus.get("teamId") else None,
                    "team_name": time_troféus.get("teamName"),
                    "league_fotmob_id": str(torneio.get("leagueId")) if torneio.get("leagueId") else None,
                    "league_name": torneio.get("leagueName"),
                    "season": temporada,
                    "result": "won",
                    "country_code": time_troféus.get("ccode"),
                    "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                })
            for temporada in torneio.get("seasonsRunnerUp") or []:
                titulos.append({
                    "player_id": player_id,
                    "team_fotmob_id": str(time_troféus.get("teamId")) if time_troféus.get("teamId") else None,
                    "team_name": time_troféus.get("teamName"),
                    "league_fotmob_id": str(torneio.get("leagueId")) if torneio.get("leagueId") else None,
                    "league_name": torneio.get("leagueName"),
                    "season": temporada,
                    "result": "runner_up",
                    "country_code": time_troféus.get("ccode"),
                    "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                })

    player_information = payload.get("playerInformation")
    altura = extrair_valor_playerinfo("Height", player_information).get("numberValue")
    pe = extrair_valor_playerinfo("Preferred foot", player_information).get("key")
    contrato = extrair_valor_playerinfo("Contract expires", player_information).get("dateValue") \
        or extrair_valor_playerinfo("Contract end", player_information).get("dateValue")
    valor_atual = extrair_valor_playerinfo("Market value", player_information).get("numberValue")

    pos_desc = payload.get("positionDescription") or {}
    posicao_principal = (pos_desc.get("primaryPosition") or {}).get("label")

    detalhes = {
        "player_id": player_id,
        "height_cm": altura,
        "preferred_foot": pe,
        "contract_end": parse_data(contrato),
        "current_market_value_eur": valor_atual,
        "primary_position": posicao_principal,
        "all_positions": pos_desc.get("positions"),
        "traits": payload.get("traits"),
        "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }

    return valores_mercado, carreira, titulos, detalhes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limite", type=int, default=None, help="processa só os N primeiros jogadores pendentes")
    ap.add_argument("--player-id", type=int, default=None, help="um jogador só (players.id interno), sempre força")
    ap.add_argument("--forcar", action="store_true", help="reprocessa mesmo quem já tem player_details_fotmob")
    args = ap.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        sys.exit("Defina SUPABASE_URL e SUPABASE_KEY (service_role) como variáveis de ambiente antes de rodar.")

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    if args.player_id:
        alvo = buscar_paginado(supabase, "players", "id, fotmob_player_id, name", {"id": args.player_id})
    else:
        todos = buscar_paginado(supabase, "players", "id, fotmob_player_id, name")
        if args.forcar:
            alvo = todos
        else:
            ja_feitos = {r["player_id"] for r in buscar_paginado(supabase, "player_details_fotmob", "player_id")}
            alvo = [p for p in todos if p["id"] not in ja_feitos]
        if args.limite:
            alvo = alvo[: args.limite]

    print(f"{len(alvo)} jogadores a processar.")
    if not alvo:
        return

    ok, falhas, sem_dado = 0, 0, 0
    for i, p in enumerate(alvo):
        try:
            r = requests.get(f"{BASE}/playerData", params={"id": p["fotmob_player_id"]}, headers=HEADERS, timeout=20)
            r.raise_for_status()
            payload = r.json()
        except Exception as e:
            print(f"  falha player_id={p['id']} ({p['name']}): {e}")
            falhas += 1
            time.sleep(PACING_SEGUNDOS)
            continue

        if not payload:
            sem_dado += 1
            time.sleep(PACING_SEGUNDOS)
            continue

        try:
            valores_mercado, carreira, titulos, detalhes = montar_linhas(p["id"], payload)
        except Exception as e:
            print(f"  falha de parse player_id={p['id']} ({p['name']}): {e}")
            falhas += 1
            time.sleep(PACING_SEGUNDOS)
            continue

        if valores_mercado:
            supabase.table("player_market_value_history").upsert(
                valores_mercado, on_conflict="player_id,value_date,team_fotmob_id"
            ).execute()
        if carreira:
            supabase.table("player_career_history_fotmob").upsert(
                carreira, on_conflict="player_id,team_fotmob_id,start_date"
            ).execute()
        if titulos:
            supabase.table("player_trophies_fotmob").upsert(
                titulos, on_conflict="player_id,team_fotmob_id,league_fotmob_id,season,result"
            ).execute()
        supabase.table("player_details_fotmob").upsert(detalhes, on_conflict="player_id").execute()

        ok += 1
        if (i + 1) % 20 == 0:
            print(f"  {i + 1}/{len(alvo)}...")
        time.sleep(PACING_SEGUNDOS)

    print(f"\nOK: {ok} jogadores processados, {sem_dado} sem dado na fonte, {falhas} falhas.")


if __name__ == "__main__":
    main()
