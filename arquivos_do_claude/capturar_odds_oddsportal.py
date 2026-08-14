"""
Captura odds históricas do OddsPortal via OddsHarvester
(https://github.com/jordantete/OddsHarvester) -> CSV normalizado no repo.

Este script NÃO fala com o Supabase -- só roda o scraper e grava um CSV
"longo" (uma linha por casa/mercado/seleção) em arquivos_do_claude/odds_oddsportal/.
A ingestão pro banco é um passo separado (ver ingestao_odds_oddsportal.py),
mesma separação já usada pra Footiqo (exportação manual -> script de ingestão).

Por que separado do resto dos scripts de odds do projeto: o OddsHarvester
precisa do Python 3.12+ e do Playwright (navegador de verdade), enquanto
os outros scripts de ingestão só precisam de requests/pandas -- rodar isso
localmente ou num ambiente sem esses dois requisitos vai falhar na
instalação. Pensado pra rodar via GitHub Actions
(.github/workflows/importar_odds_oddsportal.yml).

AVISO DE USO (do próprio README do OddsHarvester): "This package is
intended for educational purposes only. The author is not affiliated with
or endorsed by oddsportal.com. Use responsibly and ensure compliance with
their terms of service and applicable laws." Uso aqui é só pra consumo
próprio (sem republicação/redistribuição dos dados), uma corrida manual
por vez (workflow_dispatch, sem cron automático).

Ligas com slug confirmado no próprio pacote OddsHarvester (checado no
código-fonte instalado, não adivinhado -- ver
oddsharvester.utils.sport_league_constants.SPORTS_LEAGUES_URLS_MAPPING):
    copa-libertadores, copa-sudamericana, brazil-serie-b, champions-league,
    world-cup (Copa do Mundo FIFA).
As outras competições do projeto sem odds hoje (Copa do Brasil, Copa
América, Eurocopa, FIFA Club World Cup, FIFA Intercontinental Cup) NÃO
estão no catálogo de ligas embutido no OddsHarvester -- o comando
`historic` rejeita qualquer slug fora dessa lista fixa (valida contra o
dict, não aceita URL solta). Pra essas, confirme o slug certo navegando
manualmente oddsportal.com/football/<país-ou-região>/<competição>/results/
e passe via --liga-slug-custom; se o slug estiver errado, o OddsHarvester
falha alto (ValueError "Invalid league"), não silenciosamente.

Uso:
    pip install "oddsharvester>=0.10" pandas   (requer Python 3.12+)
    playwright install chromium --with-deps    (só na primeira vez)

    python capturar_odds_oddsportal.py --liga copa-libertadores --season 2024
    python capturar_odds_oddsportal.py --liga-slug-custom brazil-copa-do-brasil --season 2024 --liga-nome "Copa do Brasil"

Prioridade do projeto é a Pinnacle (casa "sharp", referência de mercado
eficiente pra comparar com a odd justa do modelo) -- passe
--target-bookmaker Pinnacle pra capturar só ela (mais rápido, menos
requisições) ou deixe sem o filtro pra capturar todas as casas disponíveis
na página (a comparação Pinnacle-vs-modelo continua funcionando de
qualquer forma, só grava odds de mais casas junto).
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd

PASTA_SAIDA = Path(__file__).parent / "odds_oddsportal"

# Nosso apelido -> slug confirmado em oddsharvester.utils.sport_league_constants
# (ver docstring do módulo). Adicione aqui só depois de confirmar o slug real.
LIGAS_CONHECIDAS = {
    "libertadores": "copa-libertadores",
    "sudamericana": "copa-sudamericana",
    "brasileirao-serie-b": "brazil-serie-b",
    "champions-league": "champions-league",
    "copa-do-mundo": "world-cup",
}

MERCADOS_PADRAO = "1x2,btts,over_under_2_5"

# Chaves de um bloco de odds por casa que NÃO são seleções/odds (metadados).
_CHAVES_METADADO = {"bookmaker_name", "period", "blocked_outcomes"}

_ROTULO_PARA_SELECAO = {
    "1": "home", "x": "draw", "2": "away",
    "btts_yes": "yes", "btts_no": "no",
    "odds_over": "over", "odds_under": "under",
    "dnb_team1": "home", "dnb_team2": "away",
    "team1_handicap": "home", "team2_handicap": "away",
    "1x": "home_or_draw", "12": "home_or_away", "x2": "draw_or_away",
    "player_1": "home", "player_2": "away",
    "correct_score": "score",
}


def normalizar_mercado(chave_json: str) -> str:
    """'over_under_2_5_market' -> 'over_under_2.5'; '1x2_market' -> '1X2'; 'btts_market' -> 'btts'."""
    chave = chave_json.removesuffix("_market")
    if chave == "1x2":
        return "1X2"
    for prefixo in ("over_under", "asian_handicap", "european_handicap"):
        if chave.startswith(prefixo + "_"):
            linha = chave[len(prefixo) + 1:].replace("_", ".")
            return f"{prefixo}_{linha}"
    return chave


def normalizar_selecao(rotulo: str) -> str:
    return _ROTULO_PARA_SELECAO.get(rotulo.lower(), rotulo.lower())


def para_float(v):
    try:
        return round(float(v), 3)
    except (TypeError, ValueError):
        return None


def linhas_normalizadas(partidas: list[dict], liga_slug: str) -> list[dict]:
    registros = []
    for partida in partidas:
        base = {
            "league_slug": liga_slug,
            "season": partida.get("season"),
            "match_link": partida.get("match_link"),
            "match_date": partida.get("match_date"),
            "home_team": partida.get("home_team"),
            "away_team": partida.get("away_team"),
            "home_score": partida.get("home_score"),
            "away_score": partida.get("away_score"),
        }
        for chave, valor in partida.items():
            if not chave.endswith("_market") or not isinstance(valor, list):
                continue
            mercado = normalizar_mercado(chave)
            for bloco in valor:
                bookmaker = bloco.get("bookmaker_name")
                period = bloco.get("period")
                bloqueadas = set(bloco.get("blocked_outcomes") or [])
                if not bookmaker:
                    continue
                for rotulo, odd_bruta in bloco.items():
                    if rotulo in _CHAVES_METADADO or rotulo in bloqueadas:
                        continue
                    odd = para_float(odd_bruta)
                    if odd is None:
                        continue
                    registros.append({
                        **base,
                        "bookmaker": bookmaker.strip().lower().replace(" ", "_"),
                        "market": mercado,
                        "selection": normalizar_selecao(rotulo),
                        "odds": odd,
                        "period": period,
                    })
    return registros


def main():
    parser = argparse.ArgumentParser()
    grupo_liga = parser.add_mutually_exclusive_group(required=True)
    grupo_liga.add_argument("--liga", choices=sorted(LIGAS_CONHECIDAS), help="Apelido de uma liga já confirmada.")
    grupo_liga.add_argument("--liga-slug-custom", help="Slug OddsPortal confirmado manualmente (ligas fora de LIGAS_CONHECIDAS).")
    parser.add_argument("--liga-nome", help="Nome pra usar no arquivo de saída quando --liga-slug-custom for usado.")
    parser.add_argument("--season", required=True, help="'2024', '2023-2024' ou 'current'.")
    parser.add_argument("--markets", default=MERCADOS_PADRAO, help=f"Mercados OddsHarvester, separados por vírgula (default: {MERCADOS_PADRAO}).")
    parser.add_argument("--max-pages", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--request-delay", type=float, default=1.5)
    parser.add_argument("--output", help="Caminho do CSV de saída (default: odds_oddsportal/<liga>_<season>.csv)")
    parser.add_argument("--target-bookmaker", help="Só captura odds dessa casa (ex.: 'Pinnacle') -- mais rápido, menos dado. Default: todas as casas.")
    args = parser.parse_args()

    if args.liga:
        slug = LIGAS_CONHECIDAS[args.liga]
        apelido_arquivo = args.liga
    else:
        slug = args.liga_slug_custom
        apelido_arquivo = args.liga_nome or slug

    saida_csv = Path(args.output) if args.output else PASTA_SAIDA / f"{apelido_arquivo}_{args.season}.csv"
    saida_csv.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        json_tmp = Path(tmp) / "scraped.json"
        cmd = [
            sys.executable, "-m", "oddsharvester", "historic",
            "-s", "football",
            "-l", slug,
            "--season", args.season,
            "-m", args.markets,
            "-f", "json",
            "-o", str(json_tmp),
            "--headless",
            "-c", str(args.concurrency),
            "--request-delay", str(args.request_delay),
        ]
        if args.max_pages:
            cmd += ["--max-pages", str(args.max_pages)]
        if args.target_bookmaker:
            cmd += ["--target-bookmaker", args.target_bookmaker]

        print("Rodando:", " ".join(cmd))
        resultado = subprocess.run(cmd)

        if not json_tmp.exists():
            sys.exit(f"OddsHarvester não gerou saída (código de saída {resultado.returncode}). Nada foi gravado.")

        partidas = json.loads(json_tmp.read_text(encoding="utf-8"))
        if resultado.returncode != 0:
            print(f"AVISO: OddsHarvester terminou com código {resultado.returncode} "
                  f"(coleta parcial -- alguma página de listagem pode ter falhado). "
                  f"Gravando os {len(partidas)} jogo(s) que vieram mesmo assim.")

    if not partidas:
        sys.exit("Nenhuma partida retornada -- nada pra gravar.")

    registros = linhas_normalizadas(partidas, slug)
    if not registros:
        sys.exit(f"{len(partidas)} partida(s) encontradas, mas nenhuma tinha odds nos mercados pedidos ({args.markets}).")

    df = pd.DataFrame(registros)
    df.to_csv(saida_csv, index=False)
    print(f"\n{len(partidas)} partida(s), {len(registros)} linha(s) de odds -> {saida_csv}")
    print(f"Mercados encontrados: {sorted(df['market'].unique())}")
    print(f"Casas encontradas: {sorted(df['bookmaker'].unique())}")


if __name__ == "__main__":
    main()
