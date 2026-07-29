"""
Ingestão de perfis de jogador a partir dos dumps locais do FotMob
(arquivos data_players/<fotmob_player_id>.json.gz do repositório
jbatistacosta/repository-fotmob), SEM nenhuma chamada de rede ao FotMob.

O que popula:
  - players.birth_date: data de nascimento (birthDate.utcTime[:10])
    para TODOS os jogadores com arquivo local (upsert em lote por
    fotmob_player_id — coluna com unique constraint).
  - player_details_fotmob, player_market_value_history,
    player_career_history_fotmob, player_trophies_fotmob: perfis completos
    apenas para jogadores que ainda não têm entrada em player_details_fotmob,
    a menos que --forcar seja passado.

O ID do FotMob é extraído do nome do arquivo
(ex.: 123456.json.gz → fotmob_player_id = "123456").

Uso:
    SUPABASE_URL=... SUPABASE_KEY=... python3 ingestao_perfil_jogador_local.py \\
        --pasta-dados _repository-fotmob \\
        [--so-birth-date]   # só atualiza players.birth_date, pula perfis
        [--forcar]          # reprocessa perfis mesmo pra quem já tem detalhes
        [--limite N]        # processa só N arquivos (para testes)
"""

import argparse
import datetime as dt
import gzip
import json
import os
import sys
from pathlib import Path

from supabase import create_client

SUPABASE_URL = (os.environ.get("SUPABASE_URL") or "").strip()
SUPABASE_KEY = (os.environ.get("SUPABASE_KEY") or "").strip()

TAMANHO_FATIA_UPSERT = 500


def _buscar_paginado_coluna(supabase, tabela: str, colunas: str, order_col: str) -> list:
    resultado, pagina = [], 0
    while True:
        chunk = (
            supabase.table(tabela)
            .select(colunas)
            .order(order_col)
            .range(pagina * 1000, pagina * 1000 + 999)
            .execute()
            .data
        )
        resultado.extend(chunk)
        if len(chunk) < 1000:
            break
        pagina += 1
    return resultado


def _deduplicar_por_chave(linhas: list, colunas_chave: list) -> list:
    vistos = {}
    for linha in linhas:
        vistos[tuple(linha.get(c) for c in colunas_chave)] = linha
    return list(vistos.values())


def _upsert_em_fatias(supabase, tabela: str, linhas: list, on_conflict: str) -> None:
    for i in range(0, len(linhas), TAMANHO_FATIA_UPSERT):
        supabase.table(tabela).upsert(
            linhas[i : i + TAMANHO_FATIA_UPSERT], on_conflict=on_conflict
        ).execute()


def _parse_data(s) -> str | None:
    if not s:
        return None
    return str(s)[:10]  # YYYY-MM-DD, corta hora se vier junto


def _extrair_playerinfo(titulo: str, player_information: list) -> dict:
    for item in player_information or []:
        if item.get("title") == titulo:
            return item.get("value") or {}
    return {}


def _montar_perfil_completo(player_id: int, payload: dict, now: str) -> tuple:
    """Extrai dados de perfil completo — mesma lógica de
    ingestao_fotmob_perfil_jogador.py mas sem chamada de API."""
    valores_mercado = []
    for v in (payload.get("marketValues") or {}).get("values") or []:
        data_v = _parse_data(v.get("date"))
        if not data_v:
            continue
        valores_mercado.append({
            "player_id": player_id,
            "value_date": data_v,
            "value_eur": v.get("value"),
            "lower_bound_eur": v.get("lowerBound"),
            "upper_bound_eur": v.get("upperBound"),
            "source": v.get("source"),
            "team_fotmob_id": str(v["teamId"]) if v.get("teamId") else None,
            "team_name": v.get("teamName"),
            "is_period_start": bool(v.get("isPeriodStart")),
            "captured_at": now,
        })

    carreira = []
    senior = (
        ((payload.get("careerHistory") or {}).get("careerItems") or {}).get("senior") or {}
    )
    for t in senior.get("teamEntries") or []:
        data_inicio = _parse_data(t.get("startDate"))
        if not data_inicio:
            continue
        carreira.append({
            "player_id": player_id,
            "team_fotmob_id": str(t["teamId"]) if t.get("teamId") else None,
            "team_name": t.get("team"),
            "start_date": data_inicio,
            "end_date": _parse_data(t.get("endDate")),
            "active": bool(t.get("active")),
            "transfer_type": (t.get("transferType") or {}).get("text"),
            "appearances": int(t["appearances"]) if str(t.get("appearances") or "").isdigit() else None,
            "goals": int(t["goals"]) if str(t.get("goals") or "").isdigit() else None,
            "assists": int(t["assists"]) if str(t.get("assists") or "").isdigit() else None,
            "captured_at": now,
        })

    titulos = []
    for time_trofeus in (payload.get("trophies") or {}).get("playerTrophies") or []:
        for torneio in time_trofeus.get("tournaments") or []:
            for temporada in torneio.get("seasonsWon") or []:
                titulos.append({
                    "player_id": player_id,
                    "team_fotmob_id": str(time_trofeus["teamId"]) if time_trofeus.get("teamId") else None,
                    "team_name": time_trofeus.get("teamName"),
                    "league_fotmob_id": str(torneio["leagueId"]) if torneio.get("leagueId") else None,
                    "league_name": torneio.get("leagueName"),
                    "season": temporada,
                    "result": "won",
                    "country_code": time_trofeus.get("ccode"),
                    "captured_at": now,
                })
            for temporada in torneio.get("seasonsRunnerUp") or []:
                titulos.append({
                    "player_id": player_id,
                    "team_fotmob_id": str(time_trofeus["teamId"]) if time_trofeus.get("teamId") else None,
                    "team_name": time_trofeus.get("teamName"),
                    "league_fotmob_id": str(torneio["leagueId"]) if torneio.get("leagueId") else None,
                    "league_name": torneio.get("leagueName"),
                    "season": temporada,
                    "result": "runner_up",
                    "country_code": time_trofeus.get("ccode"),
                    "captured_at": now,
                })

    player_information = payload.get("playerInformation")
    altura = _extrair_playerinfo("Height", player_information).get("numberValue")
    pe = _extrair_playerinfo("Preferred foot", player_information).get("key")
    contrato = (
        _extrair_playerinfo("Contract expires", player_information).get("dateValue")
        or _extrair_playerinfo("Contract end", player_information).get("dateValue")
    )
    valor_atual = _extrair_playerinfo("Market value", player_information).get("numberValue")
    pos_desc = payload.get("positionDescription") or {}
    posicao_principal = (pos_desc.get("primaryPosition") or {}).get("label")

    detalhes = {
        "player_id": player_id,
        "height_cm": altura,
        "preferred_foot": pe,
        "contract_end": _parse_data(contrato),
        "current_market_value_eur": valor_atual,
        "primary_position": posicao_principal,
        "all_positions": pos_desc.get("positions"),
        "traits": payload.get("traits"),
        "captured_at": now,
    }

    return valores_mercado, carreira, titulos, detalhes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--pasta-dados", required=True,
        help="Caminho raiz do repository-fotmob (contém data_players/)"
    )
    ap.add_argument(
        "--so-birth-date", action="store_true",
        help="Só atualiza players.birth_date; ignora player_details e tabelas relacionadas"
    )
    ap.add_argument(
        "--forcar", action="store_true",
        help="Reprocessa perfis completos mesmo para jogadores já em player_details_fotmob"
    )
    ap.add_argument(
        "--limite", type=int, default=None,
        help="Processa só os N primeiros arquivos (para testes)"
    )
    args = ap.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        sys.exit("Defina SUPABASE_URL e SUPABASE_KEY (service_role) como variáveis de ambiente.")

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    pasta_players = Path(args.pasta_dados) / "data_players"
    if not pasta_players.exists():
        sys.exit(f"Pasta não encontrada: {pasta_players}")

    arquivos = sorted(pasta_players.glob("*.json.gz"))
    if args.limite:
        arquivos = arquivos[: args.limite]
    print(f"{len(arquivos)} arquivos de perfil encontrados em {pasta_players}")

    # Mapeamento fotmob_player_id (texto) → players.id (interno)
    print("Carregando mapeamento fotmob_player_id → id do banco...")
    registros = _buscar_paginado_coluna(supabase, "players", "id,fotmob_player_id", "id")
    fid_para_id = {str(r["fotmob_player_id"]): r["id"] for r in registros}
    print(f"  {len(fid_para_id)} jogadores na base.")

    # Conjunto de player_ids já com player_details_fotmob
    ja_com_detalhes: set[int] = set()
    if not args.forcar and not args.so_birth_date:
        print("Carregando IDs já com player_details_fotmob...")
        det_rows = _buscar_paginado_coluna(supabase, "player_details_fotmob", "player_id", "player_id")
        ja_com_detalhes = {r["player_id"] for r in det_rows}
        print(f"  {len(ja_com_detalhes)} já com detalhes — processando os restantes.")

    now = dt.datetime.now(dt.timezone.utc).isoformat()

    birth_rows: list[dict] = []
    acc_vm: list[dict] = []
    acc_carreira: list[dict] = []
    acc_titulos: list[dict] = []
    acc_detalhes: list[dict] = []
    sem_match = 0
    erros = 0

    for arq in arquivos:
        fotmob_id = arq.name.replace(".json.gz", "")
        player_db_id = fid_para_id.get(fotmob_id)
        if player_db_id is None:
            sem_match += 1
            continue

        try:
            with gzip.open(arq, "rt", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception as e:
            print(f"  erro lendo {arq.name}: {e}")
            erros += 1
            continue

        birth_utc = (payload.get("birthDate") or {}).get("utcTime")
        birth_date = _parse_data(birth_utc)
        if birth_date:
            birth_rows.append({"fotmob_player_id": fotmob_id, "birth_date": birth_date})

        if not args.so_birth_date and (args.forcar or player_db_id not in ja_com_detalhes):
            try:
                vm, car, tit, det = _montar_perfil_completo(player_db_id, payload, now)
                acc_vm.extend(vm)
                acc_carreira.extend(car)
                acc_titulos.extend(tit)
                acc_detalhes.append(det)
            except Exception as e:
                print(f"  erro de parse player_id={player_db_id} ({fotmob_id}): {e}")
                erros += 1

    print(
        f"\nLeitura concluída: {len(birth_rows)} com birth_date | "
        f"{sem_match} sem match no banco | {erros} erros de leitura/parse"
    )

    if birth_rows:
        print(f"Atualizando players.birth_date para {len(birth_rows)} jogadores...")
        _upsert_em_fatias(supabase, "players", birth_rows, "fotmob_player_id")
        print("  OK.")

    if acc_detalhes:
        n = len(acc_detalhes)
        print(f"Inserindo perfis completos para {n} jogadores sem player_details_fotmob...")

        vm = _deduplicar_por_chave(acc_vm, ["player_id", "value_date", "team_fotmob_id"])
        if vm:
            _upsert_em_fatias(supabase, "player_market_value_history", vm,
                              "player_id,value_date,team_fotmob_id")

        car = _deduplicar_por_chave(acc_carreira, ["player_id", "team_fotmob_id", "start_date"])
        if car:
            _upsert_em_fatias(supabase, "player_career_history_fotmob", car,
                              "player_id,team_fotmob_id,start_date")

        tit = _deduplicar_por_chave(
            acc_titulos,
            ["player_id", "team_fotmob_id", "league_fotmob_id", "season", "result"]
        )
        if tit:
            _upsert_em_fatias(supabase, "player_trophies_fotmob", tit,
                              "player_id,team_fotmob_id,league_fotmob_id,season,result")

        det = _deduplicar_por_chave(acc_detalhes, ["player_id"])
        _upsert_em_fatias(supabase, "player_details_fotmob", det, "player_id")

        print(
            f"  Perfis: {len(det)} detalhes | {len(vm)} val. mercado | "
            f"{len(car)} carreira | {len(tit)} títulos."
        )

    print("Concluído.")


if __name__ == "__main__":
    main()
