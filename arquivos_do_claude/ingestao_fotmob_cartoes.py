"""
Backfill/sync de eventos de cartão POR JOGADOR (FotMob) -- fecha o gap
documentado em CONTEXTO_PROJETO.md: hoje só existe cartão por TIME por
partida (`match_stats_fotmob.yellow_cards`/`red_cards`), `match_events`
existia desde o schema original mas nunca foi populada (0 linhas).

MESMO payload matchDetails já usado por match_stats_fotmob/
match_player_stats_fotmob (`ingestao_fotmob.py`) -- zero chamada de API
extra por partida SE já fosse sincronizada de novo, mas como o campo de
eventos nunca foi extraído nas sincronizações anteriores, este script
RE-BUSCA matchDetails pras partidas já em `match_source_ids` (source=
'fotmob') que ainda não têm nenhuma linha em `match_events` -- mesmo
endpoint, mesmo pacing conservador, mesmo espírito de scraping já aceito
neste projeto (ver ingestao_fotmob.py).

Achado confirmado por inspeção direta da API antes de desenhar o schema
(disciplina de sempre neste projeto -- nunca adivinhar formato de resposta):
`content.matchFacts.events.events` é a timeline completa da partida
(gols/cartões/substituições/VAR), com `type == "Card"` e `card` in
("Yellow", "Red", "YellowRed" -- segundo amarelo, expulsão). Mapeado pra
vocabulário já usado no resto do pipeline: 'yellow_card' | 'red_card' |
'second_yellow_card'.

BUG DE FONTE REAL, corrigido ANTES de generalizar (mesma disciplina de
"descobrir 1-2 chamadas, inspecionar o JSON real" já usada com OddsPapi/
FotMob no resto do projeto): `eventId` vem **0 pra TODAS as partidas do
Brasileirão testadas** (placeholder, mesmo espírito do `fotmob_player_id`
"0"/"-1" já documentado em `players`) -- só as 5 ligas de elite europeias
têm `eventId` real e não-zero (confirmado testando 1 partida de cada uma
das 5 + 5 partidas do Brasileirão). Corrigido gerando um id sintético
NEGATIVO, estável entre re-syncs (mesma ordem de evento sempre volta da
API), só pras partidas com eventId==0 -- preserva unicidade de
`(match_id, fotmob_event_id)` sem quebrar upsert.

Uso:
    python ingestao_fotmob_cartoes.py [--liga-id N] [--limite N] [--forcar]

Variáveis de ambiente obrigatórias: SUPABASE_URL, SUPABASE_KEY
(service_role -- sem default hardcoded, mesma disciplina de
ingestao_fotmob.py).
"""

import argparse
import os
import sys
import time

import requests

# .strip() -- secrets do GitHub Actions colados via copy-paste às vezes
# carregam um '\n' final, que quebra o parser de URL do httpx com
# "Invalid non-printable ASCII character in URL" na criação do client
# (mesmo bug já documentado/corrigido em rodar_predicoes.py).
SUPABASE_URL = (os.environ.get("SUPABASE_URL") or "").strip()
SUPABASE_KEY = (os.environ.get("SUPABASE_KEY") or "").strip()

BASE = "https://www.fotmob.com/api/data"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
}
PACING_SEGUNDOS = 1.3

# Mapeamento do campo `card` do FotMob pro vocabulário já usado no resto do
# pipeline -- confirmado por inspeção direta (5 partidas europeias + 5 do
# Brasileirão, ver docstring acima). Cartão vermelho DIRETO não entra na
# conta de acúmulo de amarelos (nenhuma das federações pesquisadas conta
# reds diretos pro limiar de suspensão por cartões) -- fica registrado aqui
# só como dado bruto, `dados_historicos.py` filtra na hora de agregar.
MAPA_TIPO_CARTAO = {
    "Yellow": "yellow_card",
    "Red": "red_card",
    "YellowRed": "second_yellow_card",
}


def parse_eventos_cartao(content: dict, match_id: int, home_team_id: int, away_team_id: int) -> list[dict]:
    """Extrai os eventos `type == "Card"` de `content.matchFacts.events.events`
    -- função pura (sem I/O), reaproveitada tanto pelo backfill quanto por
    quem quiser testar contra um payload salvo em disco."""
    eventos = (((content.get("matchFacts") or {}).get("events") or {}).get("events")) or []
    linhas = []
    contador_id_sintetico = 0
    for e in eventos:
        if e.get("type") != "Card":
            continue
        tipo = MAPA_TIPO_CARTAO.get(e.get("card"))
        if tipo is None:
            continue  # valor de `card` não mapeado -- não adivinha, ignora e loga fora daqui
        jogador = e.get("player") or {}
        fotmob_player_id = jogador.get("id")
        event_id = e.get("eventId")
        if not event_id:
            # placeholder (visto 100% das vezes no Brasileirão, ver docstring)
            # -- id sintético negativo, estável entre re-syncs porque a
            # ordem dos eventos na resposta da API é sempre a mesma.
            contador_id_sintetico -= 1
            event_id = contador_id_sintetico
        linhas.append(
            {
                "match_id": match_id,
                "team_id": home_team_id if e.get("isHome") else away_team_id,
                "player_name": jogador.get("name"),
                "event_type": tipo,
                "minute": e.get("time"),
                "source": "fotmob",
                "fotmob_event_id": event_id,
                "fotmob_player_id": str(fotmob_player_id) if fotmob_player_id is not None else None,
                "detail": {
                    "overloadTime": e.get("overloadTime"),
                    "cardDescription": e.get("cardDescription"),
                    "card_raw": e.get("card"),
                },
            }
        )
    return linhas


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--liga-id", type=int, default=None, help="restringe a um league_id interno")
    ap.add_argument("--limite", type=int, default=None, help="processa só as N primeiras partidas pendentes")
    ap.add_argument("--forcar", action="store_true", help="reprocessa mesmo partidas que já têm linhas em match_events")
    args = ap.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        sys.exit("Defina SUPABASE_URL e SUPABASE_KEY (service_role) como variáveis de ambiente antes de rodar.")

    from supabase import create_client

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    # Partidas já sincronizadas via FotMob (match_stats_fotmob já rodou pra
    # elas) -- mesmo matchId do FotMob que casa direto com matchDetails,
    # sem precisar de crosswalk de time (isHome já resolve o lado).
    fotmob_ids: dict[int, str] = {}
    pagina = 0
    while True:
        chunk = (
            supabase.table("match_source_ids")
            .select("match_id, source_id")
            .eq("source", "fotmob")
            .range(pagina * 1000, pagina * 1000 + 999)
            .execute()
            .data
        )
        fotmob_ids.update({row["match_id"]: row["source_id"] for row in chunk})
        if len(chunk) < 1000:
            break
        pagina += 1
    print(f"Partidas com fotmob_match_id conhecido: {len(fotmob_ids)}")

    match_ids = list(fotmob_ids.keys())
    if args.liga_id:
        liga_matches = set()
        pagina = 0
        while True:
            chunk = (
                supabase.table("matches")
                .select("id")
                .eq("league_id", args.liga_id)
                .range(pagina * 1000, pagina * 1000 + 999)
                .execute()
                .data
            )
            liga_matches.update(row["id"] for row in chunk)
            if len(chunk) < 1000:
                break
            pagina += 1
        match_ids = [m for m in match_ids if m in liga_matches]
        print(f"Restrito a liga_id={args.liga_id}: {len(match_ids)} partidas.")

    if not args.forcar:
        ja_processadas = set()
        pagina = 0
        while True:
            chunk = (
                supabase.table("match_events")
                .select("match_id")
                .eq("source", "fotmob")
                .range(pagina * 1000, pagina * 1000 + 999)
                .execute()
                .data
            )
            ja_processadas.update(row["match_id"] for row in chunk)
            if len(chunk) < 1000:
                break
            pagina += 1
        match_ids = [m for m in match_ids if m not in ja_processadas]
        print(f"Ainda pendentes (sem cartão em match_events): {len(match_ids)}")

    if args.limite:
        match_ids = match_ids[: args.limite]

    # home_team_id/away_team_id por partida -- não precisa de crosswalk de
    # time (isHome do evento já resolve o lado).
    times_por_partida: dict[int, tuple[int, int]] = {}
    for i in range(0, len(match_ids), 500):
        lote = match_ids[i : i + 500]
        chunk = supabase.table("matches").select("id, home_team_id, away_team_id").in_("id", lote).execute().data or []
        times_por_partida.update({row["id"]: (row["home_team_id"], row["away_team_id"]) for row in chunk})

    n_ok, n_falha, n_sem_evento = 0, 0, 0
    for i, match_id in enumerate(match_ids):
        fotmob_match_id = fotmob_ids[match_id]
        home_team_id, away_team_id = times_por_partida.get(match_id, (None, None))
        if home_team_id is None:
            n_falha += 1
            continue

        try:
            r = requests.get(f"{BASE}/matchDetails", params={"matchId": fotmob_match_id}, headers=HEADERS, timeout=20)
            r.raise_for_status()
            d = r.json()
        except Exception as e:
            print(f"  falha em matchId={fotmob_match_id}: {e}")
            n_falha += 1
            time.sleep(PACING_SEGUNDOS)
            continue

        content = d.get("content") or {}
        linhas = parse_eventos_cartao(content, match_id, home_team_id, away_team_id)
        if not linhas:
            n_sem_evento += 1
            # ainda assim marca como processada -- upsert de uma linha
            # "vazia" não existe, então gravamos direto em match_source_ids
            # não é o padrão aqui; em vez disso confiamos no fato de que
            # `--forcar` é a única forma de reprocessar (evita rebusca
            # infinita de partidas sem cartão nenhum, que é resultado real).
        else:
            fotmob_player_ids = {l["fotmob_player_id"] for l in linhas if l["fotmob_player_id"]}
            player_id_por_fotmob: dict[str, int] = {}
            if fotmob_player_ids:
                resp = (
                    supabase.table("players")
                    .select("id, fotmob_player_id")
                    .in_("fotmob_player_id", list(fotmob_player_ids))
                    .execute()
                    .data
                    or []
                )
                player_id_por_fotmob = {row["fotmob_player_id"]: row["id"] for row in resp}
            for l in linhas:
                l["player_id"] = player_id_por_fotmob.get(l.pop("fotmob_player_id"))
            supabase.table("match_events").upsert(linhas, on_conflict="match_id,fotmob_event_id").execute()

        n_ok += 1
        if (i + 1) % 50 == 0:
            print(f"  processados {i + 1}/{len(match_ids)}...")
        time.sleep(PACING_SEGUNDOS)

    print(f"\nOK: {n_ok} partidas processadas ({n_sem_evento} sem cartão nenhum), {n_falha} falhas.")


if __name__ == "__main__":
    main()
