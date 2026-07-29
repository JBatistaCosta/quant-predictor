"""
Ingestão dos dumps locais do FotMob (payload `matchDetails` já baixado e
gzipado) do repositório separado `jbatistacosta/repository-fotmob`
(https://github.com/jbatistacosta/repository-fotmob) para o Supabase do
quant-predictor.

Reaproveita EXATAMENTE a mesma lógica de parse de `ingestao_fotmob.py`
(`processar_matchdetails_completo`) -- a diferença é só a origem do
payload: aqui vem de um arquivo `data/{league_id}/{season}/{match_id}.
json.gz` já em disco (IDs internos do quant-predictor, não do FotMob --
ver README daquele repositório), em vez de uma chamada HTTP ao vivo.
Zero requisição de rede nesta ingestão -- todo o trabalho de scraping já
foi feito por `dump_matchdetails.py` naquele outro repositório.

Como o `match_id` já é conhecido a partir do NOME do arquivo, o crosswalk
FotMob -> interno não precisa de `team_source_ids` global: cada partida
resolve os 2 lados sozinha, comparando `general.homeTeam.id`/`general.
awayTeam.id` (IDs do FotMob dentro do próprio JSON) com `matches.
home_team_id`/`away_team_id` (que já sabemos, pelo `match_id`).

Uso:
    1. pip install -r scripts/requirements.txt
    2. Defina SUPABASE_URL/SUPABASE_KEY (service_role) como variáveis de
       ambiente (sem default hardcoded, mesma disciplina de
       ingestao_fotmob.py).
    3. Clone/tenha localmente `jbatistacosta/repository-fotmob` e aponte
       `--pasta-dados` pra pasta `data/` dele.
    4. python ingestao_fotmob_dumps_locais.py --pasta-dados ../repository-fotmob/data --limite 3000

Idempotente -- pula qualquer `match_id` já em `match_source_ids`
(source='fotmob') a menos que `--forcar` seja passado (mesma convenção de
`ingestao_fotmob.py`) -- ou seja, partidas já sincronizadas pelo scraper
AO VIVO não são reprocessadas à toa; esta ingestão só preenche o que
ainda falta (ou tudo, com --forcar, pra reprocessar com uma versão nova
do parser).

Processa em lotes (`--lote`, default 200 partidas) -- decodifica/parseia
o lote inteiro em memória, depois grava cada tabela num upsert só (em
fatias de 500 linhas, teto de statement_timeout do Postgres, mesma
convenção de `rodar_predicoes.py`/`TAMANHO_LOTE_UPSERT_PREDICOES`) -- bem
mais rápido que o padrão "1 partida = 1 upsert por tabela" do scraper ao
vivo, que precisa desse ritmo por causa do pacing entre chamadas de API
(não existe aqui, é tudo leitura de disco).
"""

import argparse
import gzip
import json
import os
import sys
from pathlib import Path

from supabase import create_client

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ingestao_fotmob import processar_matchdetails_completo  # noqa: E402

SUPABASE_URL = (os.environ.get("SUPABASE_URL") or "").strip()
SUPABASE_KEY = (os.environ.get("SUPABASE_KEY") or "").strip()

TAMANHO_FATIA_UPSERT = 500


def _paginar_ids_existentes(supabase, tabela: str, coluna: str, filtros: dict) -> set:
    """Busca todos os valores de `coluna` numa tabela filtrada por
    `filtros` (igualdade), paginando de 1000 em 1000 -- sem isso,
    `.execute()` sozinho corta em 1000 linhas EM SILÊNCIO (bug clássico já
    documentado várias vezes neste projeto, ver ingestao_fotmob.py).

    `.order(coluna)` é OBRIGATÓRIO aqui -- sem uma ordenação explícita, o
    PostgREST/Postgres não garante uma travessia estável entre chamadas
    `.range()` sucessivas numa tabela deste tamanho (>15k linhas): o plano
    de execução pode variar entre as duas requisições e algumas linhas
    ficam de fora silenciosamente (nem duplicadas nem repetidas, só
    ausentes). Bug real encontrado nesta sessão: sem `.order()`, alguns
    `match_id` já sincronizados (ex.: 6951-6953, de 18/07) apareciam como
    "pendentes" -- o script então reprocessava (upsert) partidas já
    prontas em vez de nunca alcançar as genuinamente novas, e como o
    upsert só ATUALIZA uma linha já existente (não gera `created_at`
    novo), o total da tabela nunca mudava e o problema ficava invisível
    nos logs (sempre reportava sucesso)."""
    valores = set()
    pagina = 0
    while True:
        q = supabase.table(tabela).select(coluna)
        for col, val in filtros.items():
            q = q.eq(col, val)
        chunk = q.order(coluna).range(pagina * 1000, pagina * 1000 + 999).execute().data
        valores.update(row[coluna] for row in chunk)
        if len(chunk) < 1000:
            break
        pagina += 1
    return valores


def _upsert_em_fatias(supabase, tabela: str, linhas: list, on_conflict: str):
    if not linhas:
        return
    for i in range(0, len(linhas), TAMANHO_FATIA_UPSERT):
        supabase.table(tabela).upsert(linhas[i : i + TAMANHO_FATIA_UPSERT], on_conflict=on_conflict).execute()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pasta-dados", required=True, help="pasta data/ do repository-fotmob (contém {league_id}/{season}/{match_id}.json.gz)")
    ap.add_argument("--limite", type=int, default=None, help="processa só os N primeiros arquivos ainda não sincronizados")
    ap.add_argument("--lote", type=int, default=200, help="tamanho do lote em memória antes de gravar no banco (default 200)")
    ap.add_argument("--forcar", action="store_true", help="reprocessa mesmo partidas já em match_source_ids")
    args = ap.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        sys.exit("Defina SUPABASE_URL e SUPABASE_KEY (service_role) como variáveis de ambiente antes de rodar.")

    pasta = Path(args.pasta_dados)
    if not pasta.is_dir():
        sys.exit(f"Pasta não encontrada: {pasta}")

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    arquivos = sorted(pasta.glob("*/*/*.json.gz"))
    print(f"Arquivos encontrados em disco: {len(arquivos)}")
    if not arquivos:
        return

    # match_id vem do NOME do arquivo -- é o id interno de `matches`,
    # não precisa casar por data/time como o scraper ao vivo (ver README
    # do repository-fotmob).
    por_match_id = {}
    for f in arquivos:
        try:
            match_id = int(f.stem.split(".")[0])
        except ValueError:
            print(f"  nome de arquivo inesperado, pulando: {f}")
            continue
        por_match_id[match_id] = f

    if not args.forcar:
        ja_sync = _paginar_ids_existentes(supabase, "match_source_ids", "match_id", {"source": "fotmob"})
        antes = len(por_match_id)
        por_match_id = {mid: f for mid, f in por_match_id.items() if mid not in ja_sync}
        print(f"Já sincronizados (pulados): {antes - len(por_match_id)}. Pendentes: {len(por_match_id)}")

    match_ids_pendentes = sorted(por_match_id)
    if args.limite:
        match_ids_pendentes = match_ids_pendentes[: args.limite]
    print(f"Processando {len(match_ids_pendentes)} partidas nesta execução (lotes de {args.lote}).")

    n_ok, n_sem_match, n_falha = 0, 0, 0

    for inicio in range(0, len(match_ids_pendentes), args.lote):
        lote_ids = match_ids_pendentes[inicio : inicio + args.lote]

        # Busca em lote as partidas correspondentes em `matches` -- só
        # precisamos de home_team_id/away_team_id, o resto (data, times)
        # já está resolvido pelo próprio nome do arquivo.
        matches_info = {}
        for i in range(0, len(lote_ids), 500):
            sub = lote_ids[i : i + 500]
            chunk = supabase.table("matches").select("id, home_team_id, away_team_id").in_("id", sub).execute().data or []
            for m in chunk:
                matches_info[m["id"]] = m

        team_rows_all, contexto_rows_all = [], []
        player_dim_rows_all, lineup_rows_all = [], []
        player_rows_all, shot_rows_all = [], []
        source_rows_all = []

        for match_id in lote_ids:
            info = matches_info.get(match_id)
            if info is None:
                # Dump referencia uma partida que não existe (ou ainda
                # não existe) em `matches` neste banco -- pula, não é
                # erro de parse, só não tem onde gravar.
                n_sem_match += 1
                continue

            try:
                with gzip.open(por_match_id[match_id]) as fh:
                    d = json.load(fh)
            except Exception as e:
                print(f"  falha ao ler {por_match_id[match_id]}: {e}")
                n_falha += 1
                continue

            try:
                fotmob_match_id = d["general"]["matchId"]
                fotmob_to_internal = {
                    str(d["general"]["homeTeam"]["id"]): info["home_team_id"],
                    str(d["general"]["awayTeam"]["id"]): info["away_team_id"],
                }
                extraido = processar_matchdetails_completo(d, match_id, fotmob_match_id, fotmob_to_internal)
            except Exception as e:
                print(f"  falha de parse em match_id={match_id}: {e}")
                n_falha += 1
                continue

            team_rows_all.extend(extraido["team_rows"])
            contexto_rows_all.append(extraido["contexto_row"])
            player_dim_rows_all.extend(extraido["player_dim_rows"])
            lineup_rows_all.extend(extraido["lineup_rows"])
            player_rows_all.extend(extraido["player_rows"])
            shot_rows_all.extend(extraido["shot_rows"])
            source_rows_all.append({
                "match_id": match_id,
                "source": "fotmob",
                "source_id": str(fotmob_match_id),
                "source_name": f"{d['general']['homeTeam']['name']} x {d['general']['awayTeam']['name']}",
            })
            n_ok += 1

        # Dimensão de jogador PRIMEIRO -- precisa do id interno (resposta
        # do upsert) pra preencher player_id nas demais tabelas, mesma
        # ordem/motivo de ingestao_fotmob.py.
        fotmob_to_player_id = {}
        if player_dim_rows_all:
            dedup = list({row["fotmob_player_id"]: row for row in player_dim_rows_all}.values())
            for i in range(0, len(dedup), TAMANHO_FATIA_UPSERT):
                resp = supabase.table("players").upsert(dedup[i : i + TAMANHO_FATIA_UPSERT], on_conflict="fotmob_player_id").execute()
                for row in resp.data:
                    fotmob_to_player_id[row["fotmob_player_id"]] = row["id"]

        for row in lineup_rows_all:
            row["player_id"] = fotmob_to_player_id.get(row["fotmob_player_id"])
        lineup_rows_all = list({(r["match_id"], r["team_id"], r["fotmob_player_id"]): r for r in lineup_rows_all}.values())

        for row in player_rows_all:
            row["player_id"] = fotmob_to_player_id.get(row["fotmob_player_id"])
        player_rows_all = list({(r["match_id"], r["fotmob_player_id"]): r for r in player_rows_all}.values())

        for row in shot_rows_all:
            row["player_id"] = fotmob_to_player_id.get(row["fotmob_player_id"]) if row["fotmob_player_id"] else None

        _upsert_em_fatias(supabase, "match_stats_fotmob", team_rows_all, "match_id,team_id")
        _upsert_em_fatias(supabase, "match_context_fotmob", contexto_rows_all, "match_id")
        _upsert_em_fatias(supabase, "match_lineup_fotmob", lineup_rows_all, "match_id,team_id,fotmob_player_id")
        _upsert_em_fatias(supabase, "match_player_stats_fotmob", player_rows_all, "match_id,fotmob_player_id")
        _upsert_em_fatias(supabase, "match_shots_fotmob", shot_rows_all, "fotmob_shot_id")
        _upsert_em_fatias(supabase, "match_source_ids", source_rows_all, "match_id,source")

        print(f"  lote {inicio + len(lote_ids)}/{len(match_ids_pendentes)} -- {n_ok} ok, {n_sem_match} sem match, {n_falha} falhas até agora.")

    print(f"\nOK: {n_ok} partidas processadas, {n_sem_match} sem partida correspondente em `matches`, {n_falha} falhas de leitura/parse.")


if __name__ == "__main__":
    main()
