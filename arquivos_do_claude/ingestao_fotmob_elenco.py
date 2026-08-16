"""
Ingestão de DESFALQUES (lesões/indisponibilidade do elenco) e TRANSFERÊNCIAS
por time, a partir do endpoint de time do FotMob (/api/data/teams?id=X) —
mesma família de endpoints internos não-oficiais já usada por
ingestao_fotmob.py (ver avisos e disciplina de uso lá).

UMA chamada por time alimenta as DUAS tabelas:
  - player_availability_fotmob: snapshot ATUAL por jogador do elenco
    (squad[].injured + injury.expectedReturn). Upsert por
    (team_id, fotmob_player_id) — cada rodada substitui o snapshot do time
    (jogadores que saíram do elenco são REMOVIDOS do snapshot antes do
    upsert, pra não sobrar linha órfã de janela de transferência).
  - team_transfers_fotmob: transfers.data ('Players in'/'Players out'/
    'Contract extension') com valor, empréstimo, origem/destino. Histórico
    preservado (upsert por chave natural, nunca delete).

Cobre só times com crosswalk source='fotmob' em team_source_ids (mesma
disciplina de sempre: sem matching por nome aqui). Custo: ~210 chamadas
(1 por time), ~5 min com pacing de 1.3s. Pensado pra rodar periodicamente
(o snapshot de lesão muda toda semana) — decisão de agendamento fica pro
usuário (cron do Vercel não roda script Python local; rodar manualmente ou
migrar pra tarefa serverless depois).

Uso:
    SUPABASE_URL=... SUPABASE_KEY=... python3 ingestao_fotmob_elenco.py [--limite N] [--team-id X]
"""

import argparse
import datetime as dt
import os
import sys
import time

import requests
from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

BASE = "https://www.fotmob.com/api/data"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
}
PACING_SEGUNDOS = 1.3

DIRECOES = {
    "Players in": "in",
    "Players out": "out",
    "Contract extension": "contract_extension",
}


def parse_elenco(payload, team_id, player_id_por_fotmob):
    grupos = ((payload.get("squad") or {}).get("squad")) or []
    linhas = []
    for grupo in grupos:
        if grupo.get("title") == "coach":
            continue
        for m in grupo.get("members") or []:
            pid = str(m.get("id"))
            if pid in ("0", "-1", "None"):
                continue
            injury = m.get("injury") or {}
            linhas.append({
                "team_id": team_id,
                "fotmob_player_id": pid,
                "player_id": player_id_por_fotmob.get(pid),
                "player_name": m.get("name"),
                "role": (m.get("role") or {}).get("fallback"),
                "injured": bool(m.get("injured")),
                "injury_id": str(injury.get("id")) if injury.get("id") is not None else None,
                "expected_return": injury.get("expectedReturn"),
                "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            })
    return linhas


def parse_transferencias(payload, team_id, player_id_por_fotmob):
    data = ((payload.get("transfers") or {}).get("data")) or {}
    linhas = []
    for titulo, direcao in DIRECOES.items():
        for t in data.get(titulo) or []:
            pid = str(t.get("playerId")) if t.get("playerId") else None
            linhas.append({
                "team_id": team_id,
                "direction": direcao,
                "fotmob_player_id": pid,
                "player_id": player_id_por_fotmob.get(pid) if pid else None,
                "player_name": t.get("name"),
                "position_label": (t.get("position") or {}).get("label"),
                "transfer_date": t.get("transferDate"),
                "from_club": t.get("fromClubFullName") or t.get("fromClub"),
                "from_club_fotmob_id": str(t.get("fromClubId")) if t.get("fromClubId") else None,
                "to_club": t.get("toClubFullName") or t.get("toClub"),
                "to_club_fotmob_id": str(t.get("toClubId")) if t.get("toClubId") else None,
                "fee_value": (t.get("fee") or {}).get("value"),
                "on_loan": t.get("onLoan"),
                "transfer_type": (t.get("transferType") or {}).get("text"),
                "raw": t,
                "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            })
    return linhas


def buscar_paginado(supabase, tabela, colunas, filtros=None):
    resultado = []
    pagina = 0
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limite", type=int, default=None, help="processa só os N primeiros times")
    ap.add_argument("--team-id", type=int, default=None, help="um time só (id interno)")
    args = ap.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        sys.exit("Defina SUPABASE_URL e SUPABASE_KEY como variáveis de ambiente antes de rodar.")

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    crosswalk = buscar_paginado(supabase, "team_source_ids", "team_id, source_id", {"source": "fotmob"})
    if args.team_id:
        crosswalk = [c for c in crosswalk if c["team_id"] == args.team_id]
    if args.limite:
        crosswalk = crosswalk[: args.limite]
    print(f"{len(crosswalk)} times com crosswalk FotMob a processar.")

    jogadores = buscar_paginado(supabase, "players", "id, fotmob_player_id")
    player_id_por_fotmob = {j["fotmob_player_id"]: j["id"] for j in jogadores}

    ok, falhas = 0, 0
    for i, c in enumerate(crosswalk):
        try:
            r = requests.get(f"{BASE}/teams", params={"id": c["source_id"]}, headers=HEADERS, timeout=20)
            r.raise_for_status()
            payload = r.json()
            if not isinstance(payload, dict):
                # Achado rodando em produção (663 times processados de uma
                # vez pela 1a vez, via cron): a API às vezes devolve HTTP 200
                # com corpo JSON `null` pra um fotmob_id específico (inválido/
                # obsoleto), em vez de erro HTTP -- sem essa checagem,
                # payload.get(...) em parse_elenco/parse_transferencias
                # explode com AttributeError e derruba o job inteiro.
                raise ValueError(f"payload inesperado ({type(payload).__name__}, esperava dict)")
        except Exception as e:
            print(f"  falha team_id={c['team_id']} fotmob={c['source_id']}: {e}")
            falhas += 1
            time.sleep(PACING_SEGUNDOS)
            continue

        elenco = parse_elenco(payload, c["team_id"], player_id_por_fotmob)
        if elenco:
            ids_atuais = [l["fotmob_player_id"] for l in elenco]
            # Remove jogadores que saíram do elenco desde o último snapshot
            supabase.table("player_availability_fotmob").delete().eq("team_id", c["team_id"]).not_.in_("fotmob_player_id", ids_atuais).execute()
            supabase.table("player_availability_fotmob").upsert(elenco, on_conflict="team_id,fotmob_player_id").execute()

        transfs = parse_transferencias(payload, c["team_id"], player_id_por_fotmob)
        if transfs:
            # Deduplicação DENTRO do lote: o FotMob pode listar a mesma
            # transferência duas vezes na mesma janela (visto em produção —
            # "ON CONFLICT DO UPDATE command cannot affect row a second time").
            # Mantém a última ocorrência de cada chave natural.
            vistos = {}
            for t in transfs:
                chave = (t["direction"], t["fotmob_player_id"] or t["player_name"], t["transfer_date"])
                vistos[chave] = t
            supabase.table("team_transfers_fotmob").upsert(list(vistos.values()), on_conflict="team_id,direction,fotmob_player_id,transfer_date").execute()

        ok += 1
        if (i + 1) % 20 == 0:
            print(f"  {i + 1}/{len(crosswalk)}...")
        time.sleep(PACING_SEGUNDOS)

    print(f"\nOK: {ok} times processados, {falhas} falhas.")


if __name__ == "__main__":
    main()
