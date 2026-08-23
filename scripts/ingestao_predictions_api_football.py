"""
Ingestão de PREVISÕES (endpoint /predictions) da API-Football v3 para o Supabase.

Fluxo em duas etapas, conforme a própria API exige (o endpoint /predictions
não aceita consulta em lote, só 1 fixture por chamada):
  1. GET /fixtures?league=X&season=Y  (ou ?date=YYYY-MM-DD)  -> lista as partidas.
  2. Para cada fixture.id retornado, GET /predictions?fixture={id}  -> 1 chamada por jogo.

Autenticação:
  Cabeçalho `x-apisports-key: <chave>`, e SOMENTE esse cabeçalho — a API-Football
  bloqueia a resposta se detectar headers fora do esperado, então a sessão do
  `requests` é criada com `session.headers.clear()` antes de setar a chave (evita
  que o `requests` injete `User-Agent`/`Accept-Encoding` default automaticamente
  na conexão, dependendo da configuração do ambiente).

Rate limiting (2 camadas, a API expõe as duas nos headers de resposta):
  - Por minuto: `X-RateLimit-Limit` (cota/minuto do plano) e `X-RateLimit-Remaining`
    (quanto sobra na janela atual). O script usa o limite pra calcular um intervalo
    mínimo entre chamadas (60 / limite) e, se `Remaining` chegar perto de 0, dorme
    até a virada da janela — throttle dinâmico, não um sleep fixo arbitrário.
  - Por dia: `x-ratelimit-requests-limit` / `x-ratelimit-requests-remaining` (cota
    diária, ex.: 100/dia no plano grátis). Se o saldo restante cair abaixo de
    `--daily-safety-buffer`, o script INTERROMPE o loop (não deixa estourar a cota
    por acidente) e sai listando quantas fixtures ficaram pendentes — a próxima
    execução retoma sozinha, pois fixtures já cacheadas são puladas (idempotência).

Armazenamento / idempotência:
  Cada resposta crua de /predictions é gravada em `api_football_predictions_cache`
  (fixture_id chave primária) pra nunca precisar rechamar uma fixture já processada.
  Se essa tabela ainda não existir no Supabase, crie antes de rodar:

    create table if not exists api_football_predictions_cache (
      fixture_id   text primary key,
      payload      jsonb not null,
      fetched_at   timestamptz not null default now()
    );

  Formato consistente com o padrão de cache já usado no projeto (ver `oddspapi_cache`
  em ingestao_odds_oddspapi_brasileirao.py).

Uso:
  set API_FOOTBALL_KEY=sua_chave
  set SUPABASE_KEY=sua_service_role_key
  python scripts/ingestao_predictions_api_football.py --league 71 --season 2026
  python scripts/ingestao_predictions_api_football.py --date 2026-07-27 --dry-run

Nota (pipeline alternativo em dlt):
  Se este projeto usasse um Postgres/BigQuery/DuckDB acessado diretamente (conexão
  SQL crua), valeria gerar esse pipeline com a lib `dlt` (Data Load Tool), que já
  cuida de paginação e evolução de schema. Aqui a gravação é sempre via
  `supabase-py` (REST/PostgREST, respeitando RLS com a service_role key) como em
  todos os outros scripts do projeto — por isso o script segue no padrão manual
  requests + supabase, pra não introduzir um segundo mecanismo de acesso ao banco.

Requisitos:
  pip install requests supabase
"""

import argparse
import os
import sys
import time
from datetime import datetime, timezone

import requests
from supabase import create_client

BASE_URL = "https://v3.football.api-sports.io"

# Cota diária: aborta o loop se o saldo restante cair até este valor (não zero,
# pra sobrar margem de segurança contra erro de contagem/corrida entre execuções).
DEFAULT_DAILY_SAFETY_BUFFER = 3

# Fallback caso a API não informe X-RateLimit-Limit numa resposta específica.
DEFAULT_PER_MINUTE_LIMIT_FALLBACK = 10


def carregar_env():
    """Carrega variáveis dos arquivos .env do projeto se não estiverem no ambiente."""
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

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://cgurxgfdmpmsnrshqycx.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY")


class CotaDiariaEsgotada(Exception):
    """Levantada quando o saldo diário da API-Football chega perto do limite de segurança."""


class ClienteApiFootball:
    """Encapsula sessão HTTP, autenticação e throttling de rate limit da API-Football v3."""

    def __init__(self, api_key: str, daily_safety_buffer: int = DEFAULT_DAILY_SAFETY_BUFFER):
        self.session = requests.Session()
        # Zera os headers default do requests (ex.: User-Agent) — a API-Football
        # bloqueia respostas se detectar cabeçalhos fora do que ela espera.
        self.session.headers.clear()
        self.session.headers.update({"x-apisports-key": api_key})

        self.daily_safety_buffer = daily_safety_buffer
        self.per_minute_limit = DEFAULT_PER_MINUTE_LIMIT_FALLBACK
        self.per_minute_remaining = None
        self.daily_remaining = None
        self._ultima_chamada_ts = 0.0

    def _atualizar_rate_limits(self, headers: dict) -> None:
        limite_min = headers.get("X-RateLimit-Limit")
        restante_min = headers.get("X-RateLimit-Remaining")
        restante_dia = headers.get("x-ratelimit-requests-remaining")

        if limite_min is not None:
            try:
                self.per_minute_limit = max(1, int(limite_min))
            except ValueError:
                pass
        if restante_min is not None:
            try:
                self.per_minute_remaining = int(restante_min)
            except ValueError:
                pass
        if restante_dia is not None:
            try:
                self.daily_remaining = int(restante_dia)
            except ValueError:
                pass

    def _throttle_por_minuto(self) -> None:
        """Espera o intervalo mínimo (60s / limite por minuto) e, se o saldo da
        janela atual estiver quase zerado, dorme até a próxima virada de minuto."""
        intervalo_minimo = 60.0 / self.per_minute_limit
        decorrido = time.monotonic() - self._ultima_chamada_ts
        if decorrido < intervalo_minimo:
            time.sleep(intervalo_minimo - decorrido)

        if self.per_minute_remaining is not None and self.per_minute_remaining <= 1:
            print(f"    [rate-limit] saldo/minuto quase zerado ({self.per_minute_remaining}), "
                  f"aguardando 60s pra virar a janela...")
            time.sleep(60)

    def _checar_cota_diaria(self) -> None:
        if self.daily_remaining is not None and self.daily_remaining <= self.daily_safety_buffer:
            raise CotaDiariaEsgotada(
                f"Saldo diário restante ({self.daily_remaining}) atingiu o buffer de segurança "
                f"({self.daily_safety_buffer}). Interrompendo para não estourar a cota."
            )

    def chamar(self, endpoint: str, params: dict, max_tentativas: int = 3) -> dict:
        """Faz uma requisição GET com retry/backoff em erro de rede e throttling dinâmico."""
        self._checar_cota_diaria()
        self._throttle_por_minuto()

        url = f"{BASE_URL}/{endpoint}"
        ultimo_erro = None

        for tentativa in range(1, max_tentativas + 1):
            try:
                resp = self.session.get(url, params=params, timeout=30)
                self._ultima_chamada_ts = time.monotonic()
                self._atualizar_rate_limits(resp.headers)

                if resp.status_code == 429:
                    espera = int(resp.headers.get("Retry-After", 60))
                    print(f"    [HTTP 429] rate limit excedido, aguardando {espera}s...")
                    time.sleep(espera)
                    continue

                resp.raise_for_status()
                dados = resp.json()

                if dados.get("errors"):
                    print(f"    [erro da API] {endpoint} {params} -> {dados['errors']}")

                print(f"    [ok] {endpoint} {params} -> "
                      f"restante/min={self.per_minute_remaining} restante/dia={self.daily_remaining}")
                return dados

            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                ultimo_erro = e
                espera = 2 ** tentativa
                print(f"    [rede] falha de conexão (tentativa {tentativa}/{max_tentativas}): {e}. "
                      f"Retentando em {espera}s...")
                time.sleep(espera)
            except requests.exceptions.HTTPError as e:
                ultimo_erro = e
                print(f"    [http] erro {resp.status_code} em {endpoint} {params}: {e}")
                break

        raise RuntimeError(f"Falha ao chamar {endpoint} {params} após {max_tentativas} tentativas: {ultimo_erro}")


def buscar_fixtures(cliente: ClienteApiFootball, league: int | None, season: int | None,
                     date: str | None, status: str | None) -> list[dict]:
    """Etapa 1: busca as partidas de uma liga/temporada ou de uma data específica."""
    params = {}
    if league is not None:
        params["league"] = league
    if season is not None:
        params["season"] = season
    if date is not None:
        params["date"] = date
    if status:
        params["status"] = status

    if not params:
        raise ValueError("Informe --league/--season e/ou --date para buscar fixtures.")

    dados = cliente.chamar("fixtures", params)
    return dados.get("response", [])


def buscar_predicao(cliente: ClienteApiFootball, fixture_id: int) -> dict | None:
    """Etapa 2: busca a previsão de UMA fixture (a API não aceita lote nesse endpoint)."""
    dados = cliente.chamar("predictions", {"fixture": fixture_id})
    respostas = dados.get("response", [])
    return respostas[0] if respostas else None


def ja_cacheada(supabase, fixture_id: int) -> bool:
    res = (
        supabase.table("api_football_predictions_cache")
        .select("fixture_id")
        .eq("fixture_id", str(fixture_id))
        .execute()
        .data
    )
    return bool(res)


def salvar_predicao(supabase, fixture_id: int, payload: dict) -> None:
    supabase.table("api_football_predictions_cache").upsert(
        {
            "fixture_id": str(fixture_id),
            "payload": payload,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        },
        on_conflict="fixture_id",
    ).execute()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingestão de previsões (predictions) da API-Football v3 para o Supabase."
    )
    parser.add_argument("--league", type=int, default=None, help="ID da liga na API-Football")
    parser.add_argument("--season", type=int, default=None, help="Temporada (ex.: 2026)")
    parser.add_argument("--date", default=None, help="Data das fixtures no formato YYYY-MM-DD")
    parser.add_argument("--status", default=None,
                         help="Filtro de status de fixture na API-Football (ex.: NS para 'não iniciados')")
    parser.add_argument("--limite", type=int, default=50,
                         help="Máximo de fixtures a processar nesta execução (padrão: 50)")
    parser.add_argument("--daily-safety-buffer", type=int, default=DEFAULT_DAILY_SAFETY_BUFFER,
                         help=f"Interrompe o loop quando o saldo diário chegar a este valor (padrão: {DEFAULT_DAILY_SAFETY_BUFFER})")
    parser.add_argument("--forcar", action="store_true", help="Rebusca fixtures já cacheadas")
    parser.add_argument("--dry-run", action="store_true", help="Não grava no Supabase, só mostra o que faria")
    parser.add_argument("--api-key", default=API_FOOTBALL_KEY, help="Chave da API-Football")
    parser.add_argument("--supabase-key", default=SUPABASE_KEY, help="Service role key do Supabase")
    args = parser.parse_args()

    api_key = args.api_key
    supabase_key = args.supabase_key

    if not api_key:
        sys.exit("ERRO: API_FOOTBALL_KEY não informada. Defina via env var ou --api-key.")
    if not supabase_key and not args.dry_run:
        sys.exit("ERRO: SUPABASE_KEY não informada. Defina via env var, --supabase-key, ou use --dry-run.")
    if not args.league and not args.date:
        sys.exit("ERRO: informe --league (com --season) e/ou --date para buscar fixtures.")

    supabase = create_client(SUPABASE_URL, supabase_key) if not args.dry_run else None
    cliente = ClienteApiFootball(api_key, daily_safety_buffer=args.daily_safety_buffer)

    print("=== INGESTÃO DE PREDICTIONS (API-FOOTBALL v3) ===")
    print(f"Liga: {args.league} | Temporada: {args.season} | Data: {args.date} | Status: {args.status}")
    print(f"Limite desta execução: {args.limite} fixtures | Buffer de segurança diário: {args.daily_safety_buffer} | Dry-run: {args.dry_run}")

    print("\nEtapa 1/2: buscando fixtures...")
    fixtures = buscar_fixtures(cliente, args.league, args.season, args.date, args.status)
    if not fixtures:
        print("Nenhuma fixture retornada para os filtros informados.")
        return
    print(f"  {len(fixtures)} fixtures encontradas.")

    pendentes = []
    for fx in fixtures:
        fixture_id = fx.get("fixture", {}).get("id")
        if fixture_id is None:
            continue
        if not args.forcar and not args.dry_run and ja_cacheada(supabase, fixture_id):
            continue
        pendentes.append(fx)

    print(f"  {len(pendentes)} fixtures pendentes de previsão (já cacheadas foram puladas).")

    lote = pendentes[: args.limite]
    if len(pendentes) > len(lote):
        print(f"  Processando só {len(lote)} nesta execução (--limite); "
              f"as demais {len(pendentes) - len(lote)} ficam pra próxima chamada do script.")

    print("\nEtapa 2/2: buscando previsão individual por fixture (1 chamada por jogo)...")
    sucesso = 0
    falhas = []
    interrompido_por_cota = False

    for i, fx in enumerate(lote):
        fixture_id = fx["fixture"]["id"]
        home = fx.get("teams", {}).get("home", {}).get("name", "?")
        away = fx.get("teams", {}).get("away", {}).get("name", "?")
        print(f"\n[{i + 1}/{len(lote)}] Fixture {fixture_id}: {home} x {away}")

        try:
            predicao = buscar_predicao(cliente, fixture_id)
            if predicao is None:
                print("    Sem previsão disponível para esta fixture.")
                continue

            if not args.dry_run:
                salvar_predicao(supabase, fixture_id, predicao)
            sucesso += 1

        except CotaDiariaEsgotada as e:
            print(f"\n[PARADA DE SEGURANÇA] {e}")
            interrompido_por_cota = True
            break
        except Exception as e:
            print(f"    [ERRO] falha ao processar fixture {fixture_id}: {e}")
            falhas.append({"fixture_id": fixture_id, "erro": str(e)})

    print("\n=== RESUMO DA EXECUÇÃO ===")
    print(f"Sucesso: {sucesso} previsões")
    if falhas:
        print(f"Falhas: {len(falhas)}")
        for f in falhas:
            print(f"  - Fixture {f['fixture_id']}: {f['erro']}")
    if interrompido_por_cota:
        restantes = len(lote) - sucesso - len(falhas)
        print(f"Interrompido por segurança de cota diária. {restantes} fixtures deste lote ainda não processadas "
              f"— rode o script de novo (fixtures já cacheadas são puladas automaticamente).")


if __name__ == "__main__":
    main()
