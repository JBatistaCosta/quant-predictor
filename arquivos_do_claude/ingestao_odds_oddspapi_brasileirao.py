"""
Script de ingestão de odds HISTÓRICAS de FECHAMENTO do Campeonato Brasileiro 2026 via OddsPapi.

Diferente das odds ao vivo (que usam /v4/odds-by-tournaments), este script busca odds de
partidas JÁ FINALIZADAS através de dois endpoints da OddsPapi:
  1. GET /v4/fixtures?tournamentId=325&statusId=2 (Finished) -> lista fixtures finalizadas
  2. GET /v4/historical-odds?fixtureId=X&bookmakers=pinnacle,bet365,betano -> histórico completo de odds

Filtro Crítico de Fechamento (Pre-Match Closing):
  O endpoint /v4/historical-odds contém todo o histórico de movimento de linha, inclusive
  odds capturadas AO VIVO durante e após a partida. Este script filtra apenas os pontos onde
  `createdAt <= hora_do_kickoff` (startTime) e seleciona a última odd pré-jogo registrada,
  gravando em `odds_market` com `snapshot='closing'`.

Idempotência:
  Registra as partidas processadas na tabela `match_source_ids` (`source='oddspapi_historico'`).

Uso:
  python arquivos_do_claude/ingestao_odds_oddspapi_brasileirao.py [opções]

Requisitos:
  set ODDSPAPI_KEY=sua_chave_oddspapi
  set SUPABASE_KEY=sua_service_role_key
  pip install requests supabase pandas
"""

import argparse
import json
import os
import sys
import time
import unicodedata
from datetime import datetime, timezone

import requests
from supabase import create_client

def carregar_env():
    """Carrega variáveis dos arquivos .env (ex: functions/.env ou .env na raiz) se não estiverem no ambiente."""
    caminhos_env = [
        os.path.join(os.path.dirname(__file__), "..", "functions", ".env"),
        os.path.join(os.path.dirname(__file__), "..", ".env"),
        os.path.join("functions", ".env"),
        ".env",
    ]
    for path in caminhos_env:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            k, v = k.strip(), v.strip().strip("'\"")
                            if k and not os.environ.get(k):
                                os.environ[k] = v
            except Exception:
                pass

carregar_env()

# Configurações padrão do projeto
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://cgurxgfdmpmsnrshqycx.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
ODDSPAPI_KEY = os.environ.get("ODDSPAPI_KEY")

ODDSPAPI_BASE_URL = "https://api.oddspapi.io"
SPORT_ID_FUTEBOL = 10
DEFAULT_TOURNAMENT_ID = 325  # ID da OddsPapi para "Brasileiro Serie A"
DEFAULT_LEAGUE_ID = 1        # ID interno no banco para "Brasileirão Série A"
DEFAULT_SEASON = "2026"
BOOKMAKERS_ALVO = ["pinnacle", "bet365", "betano"]

# De-para de apelidos para times do Brasileirão (OddsPapi <-> Banco Local)
ALIASES_ODDSPAPI = {
    "camineiro": "atleticomineiro",
    "chapecoenseaf": "chapecoense",
    "coritibafbc": "coritibafc",
    "gremiofbpa": "gremiofbportoalegrense",
    "rbbragantino": "redbullbragantino",
    "sccorinthianspaulista": "sccorinthians",
}

TOKENS_IGNORADOS = {
    "fc", "cf", "afc", "fbpa", "fbc", "sc", "ac", "ssc", "as", "rc", "cd",
    "ud", "rcd", "ec", "ca", "cr", "se", "club", "clube", "sa", "sp", "rj", "mg", "rs",
}


def normalizar_texto(s: str) -> str:
    """Normaliza texto removendo acentos, caracteres especiais e espaços extras."""
    if not s:
        return ""
    s = s.lower().replace("-", " ").replace(".", " ").replace("'", " ")
    s = unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode("utf-8")
    tokens = [t for t in s.split() if t and t not in TOKENS_IGNORADOS]
    return "".join(tokens) if tokens else s.replace(" ", "")


def nomes_batem(nome_a: str, nome_b: str) -> bool:
    """Verifica se os nomes de dois times correspondem utilizando normalização e aliases."""
    norm_a = normalizar_texto(nome_a)
    norm_b = normalizar_texto(nome_b)
    
    norm_a = ALIASES_ODDSPAPI.get(norm_a, norm_a)
    norm_b = ALIASES_ODDSPAPI.get(norm_b, norm_b)
    
    if not norm_a or not norm_b:
        return False
        
    return norm_a in norm_b or norm_b in norm_a


def chamar_oddspapi(caminho: str, params: dict, api_key: str) -> dict | list:
    """Chama a API da OddsPapi com tratamento básico de erro."""
    url = f"{ODDSPAPI_BASE_URL}{caminho}"
    params_copia = dict(params)
    params_copia["apiKey"] = api_key
    
    resp = requests.get(url, params=params_copia, timeout=30)
    if not resp.ok:
        texto = resp.text[:300]
        raise RuntimeError(f"OddsPapi HTTP {resp.status_code} [{caminho}]: {texto}")
    return resp.json()


def extrair_preco_abertura(outcome_data: dict) -> float | None:
    """
    Retorna a odd de ABERTURA (primeira odd registrada pela casa de apostas no mercado).
    """
    pontos = outcome_data.get("players", {}).get("0")
    if not isinstance(pontos, list) or not pontos:
        return None

    for p in pontos:
        preco = p.get("price")
        if preco is not None:
            return float(preco)
    return None


def extrair_preco_fechamento(outcome_data: dict, kickoff_iso: str) -> float | None:
    """
    Filtra o histórico de odds de um outcome e retorna o último preço registrado
    ANTES do início da partida (kickoff). Ignora odds in-play / ao vivo.
    """
    pontos = outcome_data.get("players", {}).get("0")
    if not isinstance(pontos, list) or not pontos:
        return None

    try:
        kickoff_dt = datetime.fromisoformat(kickoff_iso.replace("Z", "+00:00"))
    except Exception:
        return None

    mais_recente = None
    mais_recente_ts = None

    for p in pontos:
        preco = p.get("price")
        created_at_str = p.get("createdAt")
        if preco is None or not created_at_str:
            continue

        try:
            p_dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
        except Exception:
            continue

        # Descarta odds capturadas depois do kickoff (ao vivo / pós-jogo)
        if p_dt > kickoff_dt:
            continue

        if mais_recente_ts is None or p_dt > mais_recente_ts:
            mais_recente = preco
            mais_recente_ts = p_dt

    return float(mais_recente) if mais_recente is not None else None


def buscar_mercados_oddspapi(supabase, api_key: str) -> dict:
    """Obtém mapa dos mercados suportados na OddsPapi (1X2, O/U 2.5, BTTS, Placar Exato, Escanteios, Dupla Chance)."""
    chave_cache = "markets"
    res_cache = supabase.table("oddspapi_cache").select("valor").eq("chave", chave_cache).execute().data
    
    if res_cache and res_cache[0].get("valor"):
        mercados = res_cache[0]["valor"]
    else:
        print("Buscando lista de mercados na OddsPapi...")
        mercados = chamar_oddspapi("/v4/markets", {}, api_key)
        supabase.table("oddspapi_cache").upsert(
            {"chave": chave_cache, "valor": mercados, "atualizado_em": datetime.now(timezone.utc).isoformat()},
            on_conflict="chave"
        ).execute()

    mapa_mercados = {}

    # 1. 1X2
    m1x2 = next((m for m in mercados if m.get("marketName") == "Full Time Result" and m.get("handicap") in [0, None]), None)
    if m1x2:
        outcomes = {}
        for o in m1x2.get("outcomes", []):
            name = str(o.get("outcomeName"))
            de_para = {"1": "home", "X": "draw", "2": "away", "home": "home", "draw": "draw", "away": "away"}
            if name in de_para:
                outcomes[str(o.get("outcomeId"))] = de_para[name]
        mapa_mercados["1X2"] = {"marketId": str(m1x2["marketId"]), "outcomes": outcomes}

    # 2. Over/Under 2.5
    mou25 = next((m for m in mercados if m.get("marketName") == "Over Under Full Time" and m.get("handicap") == 2.5), None)
    if mou25:
        outcomes = {str(o.get("outcomeId")): str(o.get("outcomeName")).lower() for o in mou25.get("outcomes", []) if str(o.get("outcomeName")).lower() in ["over", "under"]}
        mapa_mercados["over_under_2.5"] = {"marketId": str(mou25["marketId"]), "outcomes": outcomes}

    # 3. Both Teams To Score (Ambas Marcam)
    mbtts = next((m for m in mercados if str(m.get("marketName")).lower() == "both teams to score"), None)
    if mbtts:
        outcomes = {}
        for o in mbtts.get("outcomes", []):
            name = str(o.get("outcomeName")).lower()
            if name in ["yes", "sim"]:
                outcomes[str(o.get("outcomeId"))] = "yes"
            elif name in ["no", "não", "nao"]:
                outcomes[str(o.get("outcomeId"))] = "no"
        mapa_mercados["btts"] = {"marketId": str(mbtts["marketId"]), "outcomes": outcomes}

    # 4. Correct Score (Placar Exato)
    mcs = next((m for m in mercados if str(m.get("marketName")).lower() == "correct score"), None)
    if mcs:
        outcomes = {}
        for o in mcs.get("outcomes", []):
            name = str(o.get("outcomeName")).replace(":", "-").strip()
            outcomes[str(o.get("outcomeId"))] = name
        mapa_mercados["correct_score"] = {"marketId": str(mcs["marketId"]), "outcomes": outcomes}

    # 5. Corner Total / Corner Over Under (Escanteios - ex: 9.5 ou primeiro disponível)
    mcorners = next((m for m in mercados if "corner" in str(m.get("marketName")).lower() and m.get("handicap") == 9.5), None)
    if not mcorners:
        mcorners = next((m for m in mercados if "corner" in str(m.get("marketName")).lower() and "over under" in str(m.get("marketName")).lower()), None)
    if mcorners:
        outcomes = {str(o.get("outcomeId")): str(o.get("outcomeName")).lower() for o in mcorners.get("outcomes", []) if str(o.get("outcomeName")).lower() in ["over", "under"]}
        handicap = mcorners.get("handicap", 9.5)
        cod_mercado = f"corners_over_under_{handicap}"
        mapa_mercados[cod_mercado] = {"marketId": str(mcorners["marketId"]), "outcomes": outcomes}

    # 6. Double Chance (Dupla Chance)
    mdc = next((m for m in mercados if str(m.get("marketName")).lower() == "double chance"), None)
    if mdc:
        outcomes = {}
        for o in mdc.get("outcomes", []):
            name = str(o.get("outcomeName")).lower()
            outcomes[str(o.get("outcomeId"))] = name
        mapa_mercados["double_chance"] = {"marketId": str(mdc["marketId"]), "outcomes": outcomes}

    return mapa_mercados


import csv


def salvar_em_csv(linhas_odds: list, partida_info: dict, path_csv: str):
    """Salva/anexa as odds extraídas de uma partida em um arquivo CSV para conferência local."""
    if not linhas_odds:
        return

    arquivo_existe = os.path.exists(path_csv)
    cabecalho = ["match_id", "match_date", "home_team", "away_team", "bookmaker", "market", "selection", "odds", "snapshot", "captured_at", "fixture_id"]

    match_obj = partida_info["match"]
    home_team = match_obj.get("home", {}).get("name") if match_obj.get("home") else ""
    away_team = match_obj.get("away", {}).get("name") if match_obj.get("away") else ""
    match_date = match_obj.get("match_date", "")
    fixture_id = partida_info.get("fixtureId", "")

    with open(path_csv, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        if not arquivo_existe:
            writer.writerow(cabecalho)

        for line in linhas_odds:
            writer.writerow([
                line["match_id"],
                match_date,
                home_team,
                away_team,
                line["bookmaker"],
                line["market"],
                line["selection"],
                line["odds"],
                line["snapshot"],
                line["captured_at"],
                fixture_id
            ])


def main():
    parser = argparse.ArgumentParser(description="Ingestão de Odds Históricas (Fechamento/Abertura) via OddsPapi para o Brasileirão 2026")
    parser.add_argument("--temporada", default=DEFAULT_SEASON, help=f"Temporada alvo (padrão: {DEFAULT_SEASON})")
    parser.add_argument("--liga-id", type=int, default=DEFAULT_LEAGUE_ID, help=f"ID da liga no banco (padrão: {DEFAULT_LEAGUE_ID})")
    parser.add_argument("--tournament-id", type=int, default=DEFAULT_TOURNAMENT_ID, help=f"Tournament ID na OddsPapi (padrão: {DEFAULT_TOURNAMENT_ID})")
    parser.add_argument("--limite", type=int, default=10, help="Quantidade máxima de partidas para processar nesta execução (padrão: 10)")
    parser.add_argument("--cooldown", type=float, default=5.0, help="Segundos de espera entre requisições /v4/historical-odds (padrão: 5.0s)")
    parser.add_argument("--snapshot", choices=["closing", "opening", "both"], default="closing", help="Estágio de odds a extrair: closing (fechamento), opening (abertura), ou both (ambos)")
    parser.add_argument("--forcar", action="store_true", help="Re-processar partidas mesmo se já estiverem em match_source_ids")
    parser.add_argument("--dry-run", action="store_true", help="Simular sem gravar no banco de dados")
    parser.add_argument("--no-csv", action="store_true", help="Desativar salvamento do arquivo CSV local")
    parser.add_argument("--csv", default=None, help="Caminho personalizado para o arquivo CSV de saída")
    parser.add_argument("--api-key", default=ODDSPAPI_KEY, help="Chave de API OddsPapi")
    parser.add_argument("--supabase-key", default=SUPABASE_KEY, help="Service Role Key do Supabase")
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("ODDSPAPI_KEY")
    supabase_key = args.supabase_key or os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

    if not api_key:
        sys.exit("ERRO: ODDSPAPI_KEY não informada. Defina via env var ou --api-key.")
    if not supabase_key:
        sys.exit("ERRO: SUPABASE_KEY não informada. Defina via env var ou --supabase-key.")

    supabase = create_client(SUPABASE_URL, supabase_key)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    path_csv_saida = args.csv or os.path.join(script_dir, f"odds_oddspapi_brasileirao_{args.temporada}.csv")

    print("=== INGESTÃO DE ODDS HISTÓRICAS (ODDSPAPI) ===")
    print(f"Liga ID: {args.liga_id} | Temporada: {args.temporada} | Tournament ID OddsPapi: {args.tournament_id}")
    print(f"Snapshot Alvo: {args.snapshot.upper()} | Limite: {args.limite} partidas | Cooldown: {args.cooldown}s | Dry Run: {args.dry_run}")
    if not args.no_csv:
        print(f"Arquivo CSV de conferência: {path_csv_saida}")

    # 1. Resolver mercados mapeados
    mapa_mercados = buscar_mercados_oddspapi(supabase, api_key)
    print(f"Mercados mapeados ({len(mapa_mercados)}): {', '.join(mapa_mercados.keys())}")

    # 2. Buscar fixtures finalizadas da OddsPapi
    print(f"\nConsultando partidas finalizadas no torneio OddsPapi ID {args.tournament_id}...")
    from_date = f"{args.temporada}-01-01T00:00:00Z"
    to_date = datetime.now(timezone.utc).isoformat()
    
    fixtures_oddspapi = chamar_oddspapi("/v4/fixtures", {
        "tournamentId": args.tournament_id,
        "statusId": 2,  # Finished
        "from": from_date,
        "to": to_date
    }, api_key)

    if not isinstance(fixtures_oddspapi, list) or not fixtures_oddspapi:
        print("Nenhuma fixture finalizada encontrada na OddsPapi para o período.")
        return

    print(f"OddsPapi retornou {len(fixtures_oddspapi)} partidas finalizadas.")

    # 3. Buscar jogos do banco local
    query_matches = supabase.table("matches").select(
        "id, match_date, home_team_id, away_team_id, "
        "home:teams!matches_home_team_id_fkey(id, name), "
        "away:teams!matches_away_team_id_fkey(id, name)"
    ).eq("league_id", args.liga_id).eq("season", args.temporada).eq("status", "finished")
    
    res_matches = query_matches.execute().data
    if not res_matches:
        sys.exit(f"Nenhum jogo finalizado encontrado no banco para liga={args.liga_id}, temporada={args.temporada}.")

    print(f"Banco local possui {len(res_matches)} jogos finalizados na temporada {args.temporada}.")

    # 4. Checar partidas já processadas (match_source_ids)
    if not args.forcar:
        res_source = supabase.table("match_source_ids").select("match_id").eq("source", "oddspapi_historico").execute().data
        ja_processados = {r["match_id"] for r in (res_source or [])}
    else:
        ja_processados = set()

    # 5. Casar partidas por data e nome dos times
    pendentes = []
    for fx in fixtures_oddspapi:
        start_time_str = fx.get("startTime")
        fixture_id = fx.get("fixtureId")
        p1_name = fx.get("participant1Name")
        p2_name = fx.get("participant2Name")

        if not start_time_str or not fixture_id or not p1_name or not p2_name:
            continue

        dt_fx = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))

        for m in res_matches:
            match_id = m["id"]
            if match_id in ja_processados or any(p["match"]["id"] == match_id for p in pendentes):
                continue

            home_name = m.get("home", {}).get("name") if m.get("home") else ""
            away_name = m.get("away", {}).get("name") if m.get("away") else ""

            dt_match = datetime.fromisoformat(m["match_date"].replace("Z", "+00:00"))
            diff_horas = abs((dt_match - dt_fx).total_seconds()) / 3600.0

            # Tolerância de até 36 horas entre horário do banco e OddsPapi
            if diff_horas <= 36.0 and nomes_batem(home_name, p1_name) and nomes_batem(away_name, p2_name):
                pendentes.append({
                    "fixtureId": fixture_id,
                    "startTime": start_time_str,
                    "match": m,
                    "oddsPapiHome": p1_name,
                    "oddsPapiAway": p2_name
                })
                break

    print(f"\nPartidas encontradas pendentes para ingestão de odds: {len(pendentes)}")

    if not pendentes:
        print("Nenhuma partida pendente para processar.")
        return

    lote_pendentes = pendentes[:args.limite]
    print(f"Processando lote de {len(lote_pendentes)} partidas com cooldown de {args.cooldown}s...")

    sucesso = 0
    falhas = []

    for index, p in enumerate(lote_pendentes):
        match_id = p["match"]["id"]
        home_name = p["match"]["home"]["name"]
        away_name = p["match"]["away"]["name"]
        fixture_id = p["fixtureId"]
        start_time = p["startTime"]

        if index > 0 and args.cooldown > 0:
            time.sleep(args.cooldown)

        print(f"\n[{index + 1}/{len(lote_pendentes)}] Match ID {match_id}: {home_name} x {away_name} (Fixture {fixture_id})...")

        try:
            dados_hist = chamar_oddspapi("/v4/historical-odds", {
                "fixtureId": fixture_id,
                "bookmakers": ",".join(BOOKMAKERS_ALVO)
            }, api_key)

            bookmakers_data = dados_hist.get("bookmakers", {})
            linhas_odds = []
            agora_iso = datetime.now(timezone.utc).isoformat()

            for bkm in BOOKMAKERS_ALVO:
                b_info = bookmakers_data.get(bkm)
                if not b_info:
                    continue

                markets_data = b_info.get("markets", {})

                # Iterar em todos os mercados mapeados
                for cod_mercado, config_mercado in mapa_mercados.items():
                    m_data = markets_data.get(config_mercado["marketId"])
                    if m_data and "outcomes" in m_data:
                        for outcome_id, out_obj in m_data["outcomes"].items():
                            selecao = config_mercado["outcomes"].get(str(outcome_id))
                            if not selecao:
                                continue

                            # Odd de Abertura (opening)
                            if args.snapshot in ["opening", "both"]:
                                preco_abertura = extrair_preco_abertura(out_obj)
                                if preco_abertura is not None:
                                    linhas_odds.append({
                                        "match_id": match_id,
                                        "bookmaker": bkm,
                                        "market": cod_mercado,
                                        "selection": selecao,
                                        "odds": preco_abertura,
                                        "snapshot": "opening",
                                        "captured_at": agora_iso
                                    })

                            # Odd de Fechamento (closing)
                            if args.snapshot in ["closing", "both"]:
                                preco_fechamento = extrair_preco_fechamento(out_obj, start_time)
                                if preco_fechamento is not None:
                                    linhas_odds.append({
                                        "match_id": match_id,
                                        "bookmaker": bkm,
                                        "market": cod_mercado,
                                        "selection": selecao,
                                        "odds": preco_fechamento,
                                        "snapshot": "closing",
                                        "captured_at": agora_iso
                                    })

            print(f"  -> Extraídas {len(linhas_odds)} odds ({args.snapshot}) das casas {', '.join(BOOKMAKERS_ALVO)}.")

            # Salvar no CSV para conferência
            if not args.no_csv:
                salvar_em_csv(linhas_odds, p, path_csv_saida)
                print(f"  -> Salvas no CSV: {os.path.basename(path_csv_saida)}")

            # Salvar no banco Supabase se não for dry run
            if not args.dry_run:
                if linhas_odds:
                    supabase.table("odds_market").insert(linhas_odds).execute()

                supabase.table("match_source_ids").upsert(
                    {"match_id": match_id, "source": "oddspapi_historico", "source_id": str(fixture_id)},
                    on_conflict="match_id,source"
                ).execute()

            sucesso += 1

        except Exception as e:
            print(f"  [ERRO] Falha ao processar match {match_id}: {e}")
            falhas.append({"match_id": match_id, "erro": str(e)})

    print("\n=== RESUMO DA EXECUÇÃO ===")
    print(f"Sucesso: {sucesso} partidas")
    if not args.no_csv and sucesso > 0:
        print(f"Arquivo CSV atualizado: {path_csv_saida}")
    if falhas:
        print(f"Falhas: {len(falhas)} partidas")
        for f in falhas:
            print(f" - Match {f['match_id']}: {f['erro']}")


if __name__ == "__main__":
    main()
