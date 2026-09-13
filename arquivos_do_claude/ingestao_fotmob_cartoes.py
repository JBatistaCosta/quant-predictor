"""
Backfill de eventos de cartão POR JOGADOR (FotMob) -- fecha o gap histórico
documentado em CONTEXTO_PROJETO.md: `match_events` existia desde o schema
original mas nunca foi populada antes deste script (0 linhas).

ACHADO REAL (13/09): esta era a ÚNICA forma de popular `match_events`
(rodada manualmente via workflow_dispatch, nunca agendada) -- por isso a
tabela ficou vazia de novo pra qualquer partida sincronizada depois da
última vez que alguém rodou este script. `parse_eventos_cartao` foi
promovida pra `ingestao_fotmob.py` (módulo compartilhado) e agora roda
também na sincronização diária automática (`scripts/atualizar_partidas_
finalizadas.py`), fechando o gap na fonte -- este script continua existindo
só pra reprocessar o histórico anterior a essa mudança (`--forcar`) ou
preencher lacunas pontuais, importando a mesma função em vez de manter
cópia própria (ver docstring de `parse_eventos_cartao` em
`ingestao_fotmob.py` pro contexto completo do achado de `discipline.
yellow_cards` zerando desde julho/2026).

MESMO payload matchDetails já usado por match_stats_fotmob/
match_player_stats_fotmob (`ingestao_fotmob.py`) -- zero chamada de API
extra por partida SE já fosse sincronizada de novo, mas como o campo de
eventos não era extraído nas sincronizações anteriores a esta mudança, este
script RE-BUSCA matchDetails pras partidas já em `match_source_ids` (source=
'fotmob') que ainda não têm nenhuma linha em `match_events` -- mesmo
endpoint, mesmo pacing conservador, mesmo espírito de scraping já aceito
neste projeto (ver ingestao_fotmob.py).

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

sys.path.insert(0, os.path.dirname(__file__))
from ingestao_fotmob import parse_eventos_cartao, BASE, HEADERS, PACING_SEGUNDOS  # noqa: E402

# .strip() -- secrets do GitHub Actions colados via copy-paste às vezes
# carregam um '\n' final, que quebra o parser de URL do httpx com
# "Invalid non-printable ASCII character in URL" na criação do client
# (mesmo bug já documentado/corrigido em rodar_predicoes.py).
SUPABASE_URL = (os.environ.get("SUPABASE_URL") or "").strip()
SUPABASE_KEY = (os.environ.get("SUPABASE_KEY") or "").strip()


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

    # ACHADO REAL (13/09, rodando o backfill em produção pela 1ª vez depois
    # da promoção de parse_eventos_cartao): as 3 paginações abaixo usavam
    # `.range()` SEM `.order()` -- mesmo bug de paginação instável já
    # corrigido várias vezes neste projeto (api/backtest-betting.js,
    # api/model-maintenance.js, api/sync-clubelo.js). Sem ordenação
    # explícita, o Postgres/PostgREST não garante a mesma ordem entre
    # chamadas `.range()` sucessivas -- linhas somem/duplicam entre páginas
    # silenciosamente. Sintoma real confirmado: rodando pra liga_id=1, o
    # script terminou com "0 falhas" e cobriu ~1630 partidas, mas 747
    # partidas finalizadas com cartão real na API (confirmado ao vivo) nunca
    # entraram no lote de `match_ids` processado -- não apareceram nem como
    # falha nem como "sem cartão nenhum", só ficaram invisíveis. Corrigido
    # adicionando `.order()` nas 3 paginações (chave suficiente pra ser
    # única dentro do filtro de cada uma -- `match_id` sozinho não é único
    # em `match_events` (~1 linha por cartão), por isso leva `id` como
    # desempate).

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
            .order("match_id")
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
                .order("id")
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
                .select("match_id, id")
                .eq("source", "fotmob")
                .order("match_id")
                .order("id")
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
