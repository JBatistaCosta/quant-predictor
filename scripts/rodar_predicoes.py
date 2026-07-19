#!/usr/bin/env python3
"""Rotina unificada de Model Benchmarking, executada pelo GitHub Actions.

Passo a passo (ver README do PR pra visão geral da arquitetura):
  1. Busca odds de mercado (Pinnacle / Betfair Exchange) e salva em
     `market_odds` via UPSERT.
  2. Roda 4 modelos de predição 1X2 (dixon_coles_v1, catboost_v1,
     xgboost_v1, lightgbm_v1) para as mesmas partidas -- cada um treinado
     com a janela de dado histórico apropriada pra sua família (ver
     `dados_historicos.py`: Dixon-Coles usa 2-3 temporadas + decaimento
     temporal; os 3 modelos de árvore usam um dataset "Feature Stacked" de
     5-8 temporadas empilhadas das 5 ligas de elite europeias, com
     features de gols e xG separadas por mando -- ver `dados_historicos.
     _forma_por_mando`).
  3. Cada modelo ganha DUAS variantes CALIBRADAS (Platt e Isotonic
     Regression, ajustadas num Validation Set que o modelo base não
     treinou -- ver `calibracao.py`), persistidas ao lado da crua com
     `model_name` sufixado `_calibrado_platt`/`_calibrado_isotonic` (nenhum
     método substitui o outro -- a comparação é sempre empírica, feita em
     `backtest_kelly.py`). Calcula o edge de cada uma (crua e as 2
     calibradas) contra a melhor odd capturada e persiste tudo em
     `predicoes`.

Variáveis de ambiente obrigatórias: SUPABASE_URL, SUPABASE_KEY (service_role
-- as tabelas têm RLS habilitado e só aceitam escrita dessa chave).
"""

from __future__ import annotations

import logging
import os
import sys

import pandas as pd
from scipy.stats import poisson
from supabase import Client, create_client

import calibracao
import dados_historicos
import modelos_ml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("rodar_predicoes")

# Janelas de dado histórico (Requisito 5 -- futebol é não-estacionário, cada
# família de modelo usa uma janela diferente, ver dados_historicos.py):
JANELA_DIXON_COLES_TEMPORADAS = 3  # as duas últimas completas + a atual
JANELA_ML_ANOS = 6  # dentro do range pedido de 5-8 temporadas por liga

# Fração do dataset reservada pra Validation Set -- usada só pra ajustar a
# calibração de cada modelo, nunca pra escolher hiperparâmetro em produção
# (isso é papel do grid search em `backtest_kelly.py`). O modelo final que
# prevê as fixtures do dia é sempre refeito no dataset INTEIRO (treino +
# validação), depois de já ter ajustado a calibração.
FRACAO_VALIDACAO_CALIBRACAO = 0.2

# As duas técnicas de calibração ficam disponíveis lado a lado -- nenhuma
# substitui a outra, a comparação empírica é sempre feita em
# `backtest_kelly.py` (ver `calibracao.py`).
METODOS_CALIBRACAO = calibracao.METODOS

# IDs reais em `teams`/`matches` pros 4 times do mock de fixtures
# (confirmado por consulta direta ao Supabase, não adivinhado) -- só serve
# pro cenário de demonstração; numa integração real da The-Odds-API, esse
# mapeamento viria do casamento evento->match_id (`buscar_odds_mercado_real`).
MOCK_TEAM_ID = {
    "Palmeiras": 1,
    "Flamengo": 17,
    "Arsenal": 63,
    "Chelsea": 77,
}

# sport_key da The-Odds-API -> league_id real do pipeline (Brasileirão=1,
# Premier League=4, La Liga=7, Serie A=10, Bundesliga=13, Ligue 1=16 --
# conferir contra /v4/sports antes de ligar a chamada real da API).
SPORT_KEY_PARA_LEAGUE_ID = {
    "soccer_brazil_campeonato": 1,
    "soccer_epl": 4,
    "soccer_spain_la_liga": 7,
    "soccer_italy_serie_a": 10,
    "soccer_germany_bundesliga1": 13,
    "soccer_france_ligue_one": 16,
}

# sport_key -> nome de liga EXATAMENTE como usado em
# `dados_historicos.LIGAS_ELITE_EUROPEIAS` (precisa bater pra alinhar com a
# categoria "liga" vista no treino). Brasileirão não faz parte do dataset
# "Feature Stacked" (só as 5 ligas de elite europeias, por pedido explícito
# do Requisito 5) -- fica None de propósito, tratado como categoria
# desconhecida pelos modelos de ML (o Dixon-Coles não usa esse mapeamento).
SPORT_KEY_PARA_LIGA_NOME = {
    "soccer_epl": "Premier League",
    "soccer_spain_la_liga": "La Liga",
    "soccer_italy_serie_a": "Serie A (Itália)",
    "soccer_germany_bundesliga1": "Bundesliga",
    "soccer_france_ligue_one": "Ligue 1",
}

ELO_PADRAO = 1500.0
FORCA_PADRAO = {"ataque": 1.0, "defesa": 1.0}


# =============================================================================
# Setup
# =============================================================================
def obter_env_obrigatoria(nome: str) -> str:
    # .strip() -- secrets do GitHub Actions colados via copy-paste às vezes
    # carregam um '\n' final, que quebra o parser de URL do httpx com
    # "Invalid non-printable ASCII character in URL" na criação do client
    valor = (os.environ.get(nome) or "").strip()
    if not valor:
        logger.error("Variável de ambiente obrigatória ausente: %s", nome)
        sys.exit(1)
    return valor


def get_supabase_client() -> Client:
    url = obter_env_obrigatoria("SUPABASE_URL")
    key = obter_env_obrigatoria("SUPABASE_KEY")
    return create_client(url, key)


# =============================================================================
# Passo 1 — Odds de mercado (The-Odds-API)
# =============================================================================
def mock_the_odds_api_response() -> list[dict]:
    """Estrutura de mock no formato real da The-Odds-API
    (GET /v4/sports/{sport}/odds?regions=eu&markets=h2h).

    Cada evento carrega `match_id` -- na integração real esse campo não vem
    da API (ela não conhece nosso banco); precisa de um passo de casamento
    por time+data contra `matches`, no mesmo espírito do matching já usado
    em `sync-match-stats.js`/`sync-clubelo.js` no restante do projeto. Não
    implementado aqui de propósito -- ver `buscar_odds_mercado_real` abaixo.
    """
    return [
        {
            "id": "mock_evt_1",
            "sport_key": "soccer_brazil_campeonato",
            "commence_time": "2026-07-19T21:00:00Z",
            "match_id": 1,
            "home_team": "Palmeiras",
            "away_team": "Flamengo",
            "bookmakers": [
                {
                    "key": "pinnacle",
                    "title": "Pinnacle",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Palmeiras", "price": 2.10},
                                {"name": "Draw", "price": 3.40},
                                {"name": "Flamengo", "price": 3.60},
                            ],
                        }
                    ],
                },
                {
                    "key": "betfair_ex_eu",
                    "title": "Betfair Exchange",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Palmeiras", "price": 2.14},
                                {"name": "Draw", "price": 3.45},
                                {"name": "Flamengo", "price": 3.55},
                            ],
                        }
                    ],
                },
            ],
        },
        {
            "id": "mock_evt_2",
            "sport_key": "soccer_epl",
            "commence_time": "2026-07-19T16:30:00Z",
            "match_id": 2,
            "home_team": "Arsenal",
            "away_team": "Chelsea",
            "bookmakers": [
                {
                    "key": "pinnacle",
                    "title": "Pinnacle",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Arsenal", "price": 1.95},
                                {"name": "Draw", "price": 3.60},
                                {"name": "Chelsea", "price": 3.90},
                            ],
                        }
                    ],
                },
                {
                    "key": "betfair_ex_eu",
                    "title": "Betfair Exchange",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Arsenal", "price": 1.98},
                                {"name": "Draw", "price": 3.65},
                                {"name": "Chelsea", "price": 3.85},
                            ],
                        }
                    ],
                },
            ],
        },
    ]


def buscar_odds_mercado_real(sport_key: str, api_key: str, regions: str = "eu", markets: str = "h2h") -> list[dict]:
    """Chamada real à The-Odds-API -- não usada em `main()` ainda.

    Fica scaffolded (imports e assinatura prontos) pra troca direta assim
    que o casamento evento->match_id estiver resolvido. Seguindo a
    disciplina já estabelecida no projeto pra APIs de cota limitada: gastar
    1-2 chamadas de descoberta primeiro e inspecionar o JSON real antes de
    generalizar o parser, nunca adivinhar o formato.
    """
    import requests

    resp = requests.get(
        f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds",
        params={
            "apiKey": api_key,
            "regions": regions,
            "markets": markets,
            "bookmakers": "pinnacle,betfair_ex_eu",
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def buscar_odds_mercado() -> list[dict]:
    the_odds_api_key = os.environ.get("THE_ODDS_API_KEY")
    if the_odds_api_key:
        logger.warning(
            "THE_ODDS_API_KEY presente, mas buscar_odds_mercado_real() ainda não está "
            "ligada em main() -- usando mock até o casamento evento->match_id ser resolvido."
        )
    return mock_the_odds_api_response()


def normalizar_odds(eventos_raw: list[dict]) -> list[dict]:
    """Achata os eventos da API em linhas prontas pra UPSERT em `market_odds`."""
    linhas = []
    for evento in eventos_raw:
        for bookmaker in evento["bookmakers"]:
            outcomes = {o["name"]: o["price"] for o in bookmaker["markets"][0]["outcomes"]}
            linhas.append(
                {
                    "match_id": evento["match_id"],
                    "bookmaker": bookmaker["key"],
                    "odd_home": outcomes.get(evento["home_team"]),
                    "odd_draw": outcomes.get("Draw"),
                    "odd_away": outcomes.get(evento["away_team"]),
                }
            )
    return linhas


def salvar_odds_mercado(supabase: Client, linhas: list[dict]) -> None:
    if not linhas:
        return
    supabase.table("market_odds").upsert(linhas, on_conflict="match_id,bookmaker").execute()


# =============================================================================
# Passo 2 — Os 4 modelos
# =============================================================================

# --- Modelo 1: dixon_coles_v1 (Poisson bivariado com correção Dixon-Coles) ---
MANDO_CASA = 1.15  # multiplicador de vantagem de jogar em casa
RHO_DIXON_COLES = -0.05  # correção de baixo placar (0x0, 1x0, 0x1, 1x1)
MAX_GOLS_SIMULADOS = 8


def tau_dixon_coles(gols_casa: int, gols_fora: int, lambda_casa: float, lambda_fora: float, rho: float) -> float:
    """Fator de correção do artigo original de Dixon & Coles (1997) para os
    placares de baixa pontuação, onde o Poisson independente puro subestima
    a correlação real entre os dois times."""
    if gols_casa == 0 and gols_fora == 0:
        return 1 - (lambda_casa * lambda_fora * rho)
    if gols_casa == 0 and gols_fora == 1:
        return 1 + (lambda_casa * rho)
    if gols_casa == 1 and gols_fora == 0:
        return 1 + (lambda_fora * rho)
    if gols_casa == 1 and gols_fora == 1:
        return 1 - rho
    return 1.0


def _prever_probs_dixon_coles(forca_casa: dict[str, float], forca_fora: dict[str, float]) -> dict[str, float]:
    """Núcleo puro do cálculo (sem I/O) -- reaproveitado tanto pra prever a
    fixture de verdade quanto pra gerar as predições do Validation Set que
    calibram o modelo (`_calibrar_dixon_coles`)."""
    lambda_casa = forca_casa["ataque"] * forca_fora["defesa"] * MANDO_CASA
    lambda_fora = forca_fora["ataque"] * forca_casa["defesa"]

    prob_home = prob_draw = prob_away = 0.0
    for gc in range(MAX_GOLS_SIMULADOS):
        for gf in range(MAX_GOLS_SIMULADOS):
            p = (
                poisson.pmf(gc, lambda_casa)
                * poisson.pmf(gf, lambda_fora)
                * tau_dixon_coles(gc, gf, lambda_casa, lambda_fora, RHO_DIXON_COLES)
            )
            if gc > gf:
                prob_home += p
            elif gc == gf:
                prob_draw += p
            else:
                prob_away += p

    total = prob_home + prob_draw + prob_away  # normaliza o corte da soma infinita
    return {"prob_home": prob_home / total, "prob_draw": prob_draw / total, "prob_away": prob_away / total}


def _resultado_codigo(home_goals: int, away_goals: int) -> int:
    if home_goals > away_goals:
        return dados_historicos.RESULTADO_HOME
    if home_goals == away_goals:
        return dados_historicos.RESULTADO_DRAW
    return dados_historicos.RESULTADO_AWAY


def _calibrar_dixon_coles(supabase: Client, league_ids: set[int]) -> dict[str, tuple[float, float]]:
    """Ajusta a calibração do dixon_coles_v1 num Validation Set: pra cada
    liga presente nas fixtures, separa a janela curta em treino/validação
    (80/20 cronológico), estima força só no treino e gera previsão pro
    trecho de validação -- as previsões de TODAS as ligas entram juntas num
    único ajuste por seleção (mesma granularidade de `model_calibration` em
    produção: por modelo+seleção, não por liga). Ajusta os dois métodos
    (`METODOS_CALIBRACAO`) a partir do MESMO Validation Set -- Platt e
    Isotonic ficam disponíveis lado a lado pra comparação, nunca um
    substitui o outro (ver `calibracao.py`)."""
    predicoes_val: dict[int, dict[str, float]] = {}
    resultados_val: dict[int, int] = {}

    for league_id in league_ids:
        janela = dados_historicos.montar_janela_dixon_coles(supabase, league_id, n_temporadas=JANELA_DIXON_COLES_TEMPORADAS)
        if janela.empty:
            continue
        treino_liga, val_liga, _ = dados_historicos.split_cronologico(
            janela, treino=1 - FRACAO_VALIDACAO_CALIBRACAO, validacao=FRACAO_VALIDACAO_CALIBRACAO, teste=0.0
        )
        if val_liga.empty:
            continue
        forcas_treino = dados_historicos.estimar_forcas_dixon_coles(treino_liga)

        for partida in val_liga.itertuples():
            forca_casa = forcas_treino.get(partida.home_team_id, FORCA_PADRAO)
            forca_fora = forcas_treino.get(partida.away_team_id, FORCA_PADRAO)
            predicoes_val[partida.id] = _prever_probs_dixon_coles(forca_casa, forca_fora)
            resultados_val[partida.id] = _resultado_codigo(partida.home_goals, partida.away_goals)

    return {
        metodo: calibracao.ajustar_calibracao(predicoes_val, resultados_val, metodo=metodo) for metodo in METODOS_CALIBRACAO
    }


def prever_dixon_coles_v1(
    fixtures: pd.DataFrame, supabase: Client
) -> tuple[dict[int, dict[str, float]], dict[str, dict[int, dict[str, float]]]]:
    """Janela CURTA (Requisito 5): `dados_historicos.montar_janela_dixon_coles`
    carrega só as `JANELA_DIXON_COLES_TEMPORADAS` temporadas mais recentes
    da liga de cada partida, com peso de decaimento temporal já aplicado
    (`XI_DECAIMENTO`); `estimar_forcas_dixon_coles` deriva ataque/defesa por
    time em cima dessa janela ponderada (usando a janela INTEIRA -- treino+
    validação -- já que aqui é pra prever de verdade, não pra medir
    calibração). Times sem força calculável (liga não mapeada, ou time sem
    histórico na janela) caem no fallback neutro `FORCA_PADRAO`.

    Devolve `(predicoes_cruas, predicoes_calibradas_por_metodo)` -- a
    calibração é ajustada separadamente em `_calibrar_dixon_coles`, num
    Validation Set dentro da mesma janela, uma vez por método."""
    league_ids_fixtures = {int(lid) for lid in fixtures["league_id"].dropna().unique()}

    forcas_por_liga: dict[int, dict[int, dict[str, float]]] = {}
    for league_id in league_ids_fixtures:
        janela = dados_historicos.montar_janela_dixon_coles(supabase, league_id, n_temporadas=JANELA_DIXON_COLES_TEMPORADAS)
        forcas_por_liga[league_id] = dados_historicos.estimar_forcas_dixon_coles(janela)

    calibradores_por_metodo = (
        _calibrar_dixon_coles(supabase, league_ids_fixtures) if league_ids_fixtures else {m: {} for m in METODOS_CALIBRACAO}
    )

    predicoes_raw: dict[int, dict[str, float]] = {}
    predicoes_calibradas: dict[str, dict[int, dict[str, float]]] = {metodo: {} for metodo in METODOS_CALIBRACAO}
    for _, jogo in fixtures.iterrows():
        league_id = jogo.get("league_id")
        if pd.isna(league_id):
            logger.warning("Fixture %s sem league_id mapeado (sport_key desconhecido) -- usando força neutra.", jogo["match_id"])
            forcas: dict[int, dict[str, float]] = {}
        else:
            forcas = forcas_por_liga.get(int(league_id), {})

        id_casa = MOCK_TEAM_ID.get(jogo["home_team"])
        id_fora = MOCK_TEAM_ID.get(jogo["away_team"])
        forca_casa = forcas.get(id_casa, FORCA_PADRAO)
        forca_fora = forcas.get(id_fora, FORCA_PADRAO)

        probs = _prever_probs_dixon_coles(forca_casa, forca_fora)
        predicoes_raw[jogo["match_id"]] = probs
        for metodo in METODOS_CALIBRACAO:
            coef = calibradores_por_metodo.get(metodo, {})
            predicoes_calibradas[metodo][jogo["match_id"]] = calibracao.aplicar_calibracao(probs, coef) if coef else probs

    return predicoes_raw, predicoes_calibradas


# --- Modelos 2-4: gradient boosting (CatBoost / XGBoost / LightGBM) ---
def montar_features_fixtures(fixtures: pd.DataFrame, supabase: Client) -> pd.DataFrame:
    """Monta as features dos jogos a prever com dado REAL (elo atual + forma
    recente, separada por mando + xG -- `dados_historicos.
    obter_forma_recente_por_mando`) pros 4 times conhecidos do mock
    (`MOCK_TEAM_ID`); times fora desse mapeamento caem no fallback neutro.
    `liga` vem de `SPORT_KEY_PARA_LIGA_NOME` -- fixtures de ligas fora do
    dataset "Feature Stacked" (ex.: Brasileirão) ficam com `liga=None`,
    tratado como categoria desconhecida pelos 3 modelos de ML."""
    ids_conhecidos = sorted(
        {MOCK_TEAM_ID[nome] for nome in pd.concat([fixtures["home_team"], fixtures["away_team"]]).unique() if nome in MOCK_TEAM_ID}
    )
    elo_atual = dados_historicos.obter_elo_atual(supabase, ids_conhecidos)
    forma_recente = dados_historicos.obter_forma_recente_por_mando(supabase, ids_conhecidos)
    forma_padrao = {coluna: float("nan") for coluna in dados_historicos.FEATURES_NUMERICAS if coluna not in ("elo_home", "elo_away")}

    linhas = []
    for _, jogo in fixtures.iterrows():
        id_casa = MOCK_TEAM_ID.get(jogo["home_team"])
        id_fora = MOCK_TEAM_ID.get(jogo["away_team"])
        forma_casa = forma_recente.get(id_casa, forma_padrao)
        forma_fora = forma_recente.get(id_fora, forma_padrao)
        linhas.append(
            {
                "match_id": jogo["match_id"],
                "elo_home": elo_atual.get(id_casa, ELO_PADRAO),
                "elo_away": elo_atual.get(id_fora, ELO_PADRAO),
                "media_gols_marcados_5j_home": forma_casa.get("media_gols_marcados_5j_home"),
                "media_gols_sofridos_5j_home": forma_casa.get("media_gols_sofridos_5j_home"),
                "media_gols_marcados_5j_away": forma_fora.get("media_gols_marcados_5j_away"),
                "media_gols_sofridos_5j_away": forma_fora.get("media_gols_sofridos_5j_away"),
                "media_xg_5j_home": forma_casa.get("media_xg_5j_home"),
                "media_xg_sofrido_5j_home": forma_casa.get("media_xg_sofrido_5j_home"),
                "media_xg_5j_away": forma_fora.get("media_xg_5j_away"),
                "media_xg_sofrido_5j_away": forma_fora.get("media_xg_sofrido_5j_away"),
                "liga": SPORT_KEY_PARA_LIGA_NOME.get(jogo.get("sport_key")),
            }
        )
    return pd.DataFrame(linhas)


def prever_ml_com_calibracao(
    nome_modelo: str, features_fixtures: pd.DataFrame, dataset_treino: pd.DataFrame
) -> tuple[dict[int, dict[str, float]], dict[str, dict[int, dict[str, float]]]]:
    """Treina/prevê um modelo de árvore com suas variantes calibradas
    "conjugadas" (Platt e Isotonic, lado a lado): treina só no Train,
    ajusta os dois métodos no Validation (nunca visto pelo treino), depois
    refita no dataset INTEIRO (treino+validação) pra prever as fixtures de
    verdade, aplicando cada calibração já ajustada. Devolve
    `(predicoes_cruas, predicoes_calibradas_por_metodo)`."""
    treinar, prever = modelos_ml.TREINADORES[nome_modelo]
    params = modelos_ml.PARAMS_DEFAULT[nome_modelo]

    train_df, val_df, _ = dados_historicos.split_cronologico(
        dataset_treino, treino=1 - FRACAO_VALIDACAO_CALIBRACAO, validacao=FRACAO_VALIDACAO_CALIBRACAO, teste=0.0
    )

    calibradores_por_metodo: dict[str, dict] = {metodo: {} for metodo in METODOS_CALIBRACAO}
    if not val_df.empty and not train_df.empty:
        modelo_val, extra_val = treinar(params, train_df)
        probs_val, classes_val = prever(modelo_val, extra_val, val_df)
        preds_val = modelos_ml.empacotar_predicoes(val_df["match_id"].tolist(), probs_val, classes_val)
        resultados_val = dict(zip(val_df["match_id"], val_df["resultado"]))
        for metodo in METODOS_CALIBRACAO:
            calibradores_por_metodo[metodo] = calibracao.ajustar_calibracao(preds_val, resultados_val, metodo=metodo)

    modelo_final, extra_final = treinar(params, dataset_treino)
    probs_fixtures, classes_fixtures = prever(modelo_final, extra_final, features_fixtures)
    predicoes_raw = modelos_ml.empacotar_predicoes(features_fixtures["match_id"].tolist(), probs_fixtures, classes_fixtures)

    predicoes_calibradas: dict[str, dict[int, dict[str, float]]] = {}
    for metodo, coef in calibradores_por_metodo.items():
        predicoes_calibradas[metodo] = (
            {mid: calibracao.aplicar_calibracao(p, coef) for mid, p in predicoes_raw.items()} if coef else predicoes_raw
        )
    return predicoes_raw, predicoes_calibradas


# =============================================================================
# Passo 3 — Edge vs. mercado e persistência
# =============================================================================
def calcular_melhor_odd_por_partida(supabase: Client, match_ids: list[int]) -> dict[int, dict[str, float]]:
    """Melhor (maior) odd disponível por seleção, entre todas as casas
    capturadas -- é contra essa odd que o edge de cada modelo é medido,
    já que é a odd que de fato dá mais valor pro apostador."""
    linhas = (
        supabase.table("market_odds")
        .select("match_id, odd_home, odd_draw, odd_away")
        .in_("match_id", match_ids)
        .execute()
        .data
    )
    melhor_odd: dict[int, dict[str, float]] = {}
    for linha in linhas:
        atual = melhor_odd.setdefault(linha["match_id"], {"odd_home": 0.0, "odd_draw": 0.0, "odd_away": 0.0})
        for campo in ("odd_home", "odd_draw", "odd_away"):
            valor = linha.get(campo)
            if valor and valor > atual[campo]:
                atual[campo] = valor
    return melhor_odd


def calcular_edge(probs_modelo: dict[str, float], melhor_odd: dict[str, float]) -> float | None:
    """edge_detectado = maior diferença entre a probabilidade do modelo e a
    probabilidade implícita (1/odd, sem devigar -- a odd bruta é o que se
    paga de fato) entre as 3 seleções."""
    edges = []
    for selecao, campo_odd in (("home", "odd_home"), ("draw", "odd_draw"), ("away", "odd_away")):
        odd = melhor_odd.get(campo_odd)
        if not odd:
            continue
        prob_implicita = 1 / odd
        edges.append(probs_modelo[f"prob_{selecao}"] - prob_implicita)
    return max(edges) if edges else None


def montar_linhas_predicoes(
    nome_modelo: str,
    predicoes_modelo: dict[int, dict[str, float]],
    melhor_odd_por_partida: dict[int, dict[str, float]],
) -> list[dict]:
    linhas = []
    for match_id, probs in predicoes_modelo.items():
        melhor_odd = melhor_odd_por_partida.get(match_id, {})
        edge = calcular_edge(probs, melhor_odd)
        linhas.append(
            {
                "match_id": int(match_id),
                "model_name": nome_modelo,
                "prob_home": round(probs["prob_home"], 5),
                "prob_draw": round(probs["prob_draw"], 5),
                "prob_away": round(probs["prob_away"], 5),
                "edge_detectado": round(edge, 5) if edge is not None else None,
            }
        )
    return linhas


def salvar_predicoes(supabase: Client, linhas: list[dict]) -> None:
    if not linhas:
        return
    supabase.table("predicoes").upsert(linhas, on_conflict="match_id,model_name").execute()


def _persistir_cru_e_calibrados(
    nome_modelo: str,
    predicoes_raw: dict[int, dict[str, float]],
    predicoes_calibradas_por_metodo: dict[str, dict[int, dict[str, float]]],
    melhor_odd_por_partida: dict[int, dict[str, float]],
    todas_as_linhas: list[dict],
) -> None:
    """Item 3 -- calibração como modelo "conjugado": toda predição crua
    ganha uma linha irmã `{nome_modelo}_calibrado_{metodo}` por método
    (Platt e Isotonic, lado a lado -- nenhum substitui o outro) em
    `predicoes`, lado a lado no painel de benchmarking."""
    linhas_raw = montar_linhas_predicoes(nome_modelo, predicoes_raw, melhor_odd_por_partida)
    todas_as_linhas.extend(linhas_raw)

    total_calibradas = 0
    for metodo, predicoes_calibradas in predicoes_calibradas_por_metodo.items():
        linhas_calibradas = montar_linhas_predicoes(f"{nome_modelo}_calibrado_{metodo}", predicoes_calibradas, melhor_odd_por_partida)
        todas_as_linhas.extend(linhas_calibradas)
        total_calibradas += len(linhas_calibradas)

    logger.info(
        "%s: %d predição(ões) cruas + %d calibradas (%s).",
        nome_modelo, len(linhas_raw), total_calibradas, ", ".join(predicoes_calibradas_por_metodo),
    )


# =============================================================================
# Orquestração
# =============================================================================
def main() -> None:
    logger.info("Iniciando rotina de predições (model benchmarking)...")
    supabase = get_supabase_client()

    # 1. Odds de mercado
    eventos_raw = buscar_odds_mercado()
    linhas_odds = normalizar_odds(eventos_raw)
    salvar_odds_mercado(supabase, linhas_odds)
    logger.info("%d linha(s) de odds salvas em market_odds.", len(linhas_odds))

    fixtures = (
        pd.DataFrame(
            [
                {
                    "match_id": e["match_id"],
                    "home_team": e["home_team"],
                    "away_team": e["away_team"],
                    "sport_key": e.get("sport_key"),
                    "league_id": SPORT_KEY_PARA_LEAGUE_ID.get(e.get("sport_key")),
                }
                for e in eventos_raw
            ]
        )
        .drop_duplicates(subset="match_id")
        .reset_index(drop=True)
    )
    if fixtures.empty:
        logger.warning("Nenhuma partida para prever nessa rodada. Encerrando.")
        return

    melhor_odd_por_partida = calcular_melhor_odd_por_partida(supabase, fixtures["match_id"].astype(int).tolist())

    # 2. Rodar os 4 modelos -- cada um isolado por try/except pra uma falha
    # não derrubar os outros 3 (e não deixar a tabela num estado parcial
    # silencioso). Cada modelo grava uma linha crua + uma calibrada
    # (Item 3 -- "modelo extra conjugado").
    todas_as_linhas: list[dict] = []

    try:
        logger.info("Rodando modelo dixon_coles_v1 (janela de %d temporadas + decaimento temporal)...", JANELA_DIXON_COLES_TEMPORADAS)
        preds_raw, preds_calibradas = prever_dixon_coles_v1(fixtures, supabase)
        _persistir_cru_e_calibrados("dixon_coles_v1", preds_raw, preds_calibradas, melhor_odd_por_partida, todas_as_linhas)
    except Exception:
        logger.exception("Falha ao rodar dixon_coles_v1 -- pulando, os outros modelos continuam.")

    # Os 3 modelos de árvore compartilham o mesmo dataset "Feature Stacked"
    # (Requisito 5: 5-8 temporadas empilhadas das 5 ligas de elite) e o
    # mesmo conjunto de features das fixtures -- montados uma única vez
    # aqui, em vez de 3x (uma por modelo), pra não repetir consulta ao
    # Supabase à toa. Sequencial em vez de paralelo de propósito: CatBoost/
    # LightGBM já usam thread_count=2 internamente, então rodar em paralelo
    # só disputaria os mesmos 2 núcleos do runner do GitHub Actions.
    dataset_treino = dados_historicos.montar_dataset_ml_empilhado(supabase, anos_por_liga=JANELA_ML_ANOS)
    if dataset_treino.empty:
        logger.warning("Dataset de treino (ligas de elite) veio vazio -- pulando catboost_v1/xgboost_v1/lightgbm_v1.")
    else:
        logger.info("Dataset de treino: %d linhas, ligas=%s.", len(dataset_treino), sorted(dataset_treino["liga"].unique()))
        features_fixtures = montar_features_fixtures(fixtures, supabase)

        for nome_modelo in modelos_ml.TREINADORES:
            try:
                logger.info("Rodando modelo %s (+ calibração conjugada)...", nome_modelo)
                preds_raw, preds_calibradas = prever_ml_com_calibracao(nome_modelo, features_fixtures, dataset_treino)
                _persistir_cru_e_calibrados(nome_modelo, preds_raw, preds_calibradas, melhor_odd_por_partida, todas_as_linhas)
            except Exception:
                logger.exception("Falha ao rodar o modelo %s -- pulando, os outros modelos continuam.", nome_modelo)

    # 3. Persistir tudo de uma vez
    salvar_predicoes(supabase, todas_as_linhas)
    logger.info("Concluído: %d predição(ões) salvas em predicoes.", len(todas_as_linhas))


if __name__ == "__main__":
    main()
