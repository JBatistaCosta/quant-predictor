"""
Ingestão de dados de equipe a partir dos dumps locais do FotMob
(arquivos data_teams/<file_id>.json.gz do repositório
jbatistacosta/repository-fotmob), SEM nenhuma chamada de rede ao FotMob.

Popula em `teams`:
  - stadium_capacity INTEGER  (capacidade do estádio)
  - stadium_surface  TEXT     (superfície: Grass, Artificial Grass, etc.)

O FotMob ID real do time está em data['details']['id'] dentro do JSON
(o nome do arquivo NÃO é o FotMob ID — é um identificador interno do
repository-fotmob). O join usa team_source_ids (source='fotmob') para
converter FotMob ID → teams.id interno.

Uso:
    SUPABASE_URL=... SUPABASE_KEY=... python3 ingestao_equipes_local.py \\
        --pasta-dados _repository-fotmob \\
        [--limite N]   # processa só N arquivos (para testes)
"""

import argparse
import gzip
import json
import os
import sys
from pathlib import Path

from supabase import create_client

SUPABASE_URL = (os.environ.get("SUPABASE_URL") or "").strip()
SUPABASE_KEY = (os.environ.get("SUPABASE_KEY") or "").strip()

TAMANHO_FATIA_UPSERT = 200


def _buscar_todos(supabase, tabela: str, colunas: str, order_col: str) -> list:
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


def _extrair_venue(overview: dict) -> tuple[int | None, str | None]:
    """Extrai (capacity, surface) da seção overview.venue."""
    venue = overview.get("venue") or {}
    widget = venue.get("widget") or {}
    stat_pairs = venue.get("statPairs") or []
    capacity = None
    surface = None
    for pair in stat_pairs:
        if isinstance(pair, list) and len(pair) == 2:
            label, value = pair
            if label == "Capacity" and isinstance(value, (int, float)):
                capacity = int(value)
            elif label == "Surface" and isinstance(value, str):
                surface = value
    return capacity, surface


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--pasta-dados", required=True,
        help="Caminho raiz do repository-fotmob (contém data_teams/)"
    )
    ap.add_argument(
        "--limite", type=int, default=None,
        help="Processa só os N primeiros arquivos (para testes)"
    )
    args = ap.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        sys.exit("Defina SUPABASE_URL e SUPABASE_KEY (service_role) como variáveis de ambiente.")

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    pasta = Path(args.pasta_dados) / "data_teams"
    if not pasta.exists():
        sys.exit(f"Pasta não encontrada: {pasta}")

    arquivos = sorted(pasta.glob("*.json.gz"))
    if args.limite:
        arquivos = arquivos[: args.limite]
    print(f"{len(arquivos)} arquivos de equipe encontrados em {pasta}")

    # Mapeamento fotmob_source_id (str) → teams.id interno
    print("Carregando team_source_ids (source=fotmob)...")
    source_rows = (
        supabase.table("team_source_ids")
        .select("team_id,source_id")
        .eq("source", "fotmob")
        .execute()
        .data
    )
    fotmob_para_team_id = {str(r["source_id"]): r["team_id"] for r in source_rows}
    print(f"  {len(fotmob_para_team_id)} times com mapeamento fotmob.")

    rows = []
    sem_match = 0
    sem_venue = 0
    erros = 0

    for arq in arquivos:
        try:
            with gzip.open(arq, "rt", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"  erro lendo {arq.name}: {e}")
            erros += 1
            continue

        fotmob_id = str((data.get("details") or {}).get("id") or "")
        if not fotmob_id:
            erros += 1
            continue

        team_db_id = fotmob_para_team_id.get(fotmob_id)
        if team_db_id is None:
            sem_match += 1
            continue

        overview = data.get("overview") or {}
        capacity, surface = _extrair_venue(overview)

        if capacity is None and surface is None:
            sem_venue += 1
            continue

        rows.append({
            "id": team_db_id,
            "stadium_capacity": capacity,
            "stadium_surface": surface,
        })

    print(
        f"\nLeitura: {len(rows)} com venue | {sem_match} sem match no banco | "
        f"{sem_venue} sem venue | {erros} erros"
    )

    if rows:
        print(f"Upserting {len(rows)} times em teams...")
        for i in range(0, len(rows), TAMANHO_FATIA_UPSERT):
            supabase.table("teams").upsert(rows[i : i + TAMANHO_FATIA_UPSERT], on_conflict="id").execute()
        print("  OK.")

    print("Concluído.")


if __name__ == "__main__":
    main()
