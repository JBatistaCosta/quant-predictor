#!/usr/bin/env python3
"""Rotina unificada de Model Benchmarking, executada pelo GitHub Actions.

Passo a passo (ver README do PR pra visão geral da arquitetura):
  1. Busca odds de mercado (Pinnacle / Betfair Exchange) e salva em
     `market_odds` via UPSERT.
  2. Roda 4 modelos de predição 1X2 (dixon_coles_v1, catboost_v1,
     xgboost_v1, lightgbm_v1) para as mesmas partidas.
  3. Calcula o edge de cada modelo contra a melhor odd capturada e persiste
     tudo em `predicoes`, diferenciado por `model_name`.

Variáveis de ambiente obrigatórias: SUPABASE_URL, SUPABASE_KEY (service_role
-- as tabelas têm RLS habilitado e só aceitam escrita dessa chave).
"""

from __future__ import annotations

import logging
import os
import sys

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier
from lightgbm import LGBMClassifier
from scipy.stats import poisson
from supabase import Client, create_client
from xgboost import XGBClassifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("rodar_predicoes")

RESULTADO_HOME, RESULTADO_DRAW, RESULTADO_AWAY = 0, 1, 2


# =============================================================================
# Setup
# =============================================================================
def obter_env_obrigatoria(nome: str) -> str:
    valor = os.environ.get(nome)
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
FORCA_TIMES_MOCK = {
    "Palmeiras": {"ataque": 1.35, "defesa": 0.85},
    "Flamengo": {"ataque": 1.30, "defesa": 0.90},
    "Arsenal": {"ataque": 1.45, "defesa": 0.75},
    "Chelsea": {"ataque": 1.20, "defesa": 0.95},
    "_default": {"ataque": 1.00, "defesa": 1.00},
}
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


def prever_dixon_coles_v1(fixtures: pd.DataFrame) -> dict[int, dict[str, float]]:
    """Placeholder estruturado: força ataque/defesa vem de `FORCA_TIMES_MOCK`.
    Em produção, essas forças devem vir de `team_strengths` (já modelada no
    schema do projeto, hoje ainda vazia até o pipeline treinado gravar lá)."""
    predicoes = {}
    for _, jogo in fixtures.iterrows():
        forca_casa = FORCA_TIMES_MOCK.get(jogo["home_team"], FORCA_TIMES_MOCK["_default"])
        forca_fora = FORCA_TIMES_MOCK.get(jogo["away_team"], FORCA_TIMES_MOCK["_default"])
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
        predicoes[jogo["match_id"]] = {
            "prob_home": prob_home / total,
            "prob_draw": prob_draw / total,
            "prob_away": prob_away / total,
        }
    return predicoes


# --- Modelos 2-4: gradient boosting (CatBoost / XGBoost / LightGBM) ---
FEATURES_NUMERICAS = [
    "elo_home",
    "elo_away",
    "gols_marcados_media_home",
    "gols_sofridos_media_home",
    "gols_marcados_media_away",
    "gols_sofridos_media_away",
]
CAT_FEATURES = ["liga"]
FEATURES = FEATURES_NUMERICAS + CAT_FEATURES


def gerar_dataset_treino_mock(n_amostras: int = 500) -> pd.DataFrame:
    """Placeholder estruturado do dataset de treino.

    Gera dados sintéticos com o mesmo formato (colunas + tipos) que o
    dataset real teria -- a fonte real seria uma consulta ao Supabase
    cruzando `matches` + `team_strengths`/`team_elo` + médias de
    `match_stats`. O sinal é gerado a partir de uma diferença de Elo (não é
    ruído puro), só pra dar aos 3 modelos algo consistente pra aprender
    enquanto o dataset real não é conectado.
    """
    rng = np.random.default_rng(42)
    n = n_amostras
    df = pd.DataFrame(
        {
            "elo_home": rng.normal(1550, 120, n),
            "elo_away": rng.normal(1500, 120, n),
            "gols_marcados_media_home": rng.gamma(3.0, 0.5, n),
            "gols_sofridos_media_home": rng.gamma(2.5, 0.5, n),
            "gols_marcados_media_away": rng.gamma(2.7, 0.5, n),
            "gols_sofridos_media_away": rng.gamma(2.8, 0.5, n),
            "liga": rng.choice(["brasileirao", "premier_league", "la_liga"], n),
        }
    )

    diff_elo_com_mando = (df["elo_home"] - df["elo_away"]) + 65
    prob_home_sintetica = 1 / (1 + np.exp(-diff_elo_com_mando / 200))
    sorteio = rng.random(n)
    df["resultado"] = np.where(
        sorteio < prob_home_sintetica * 0.75,
        RESULTADO_HOME,
        np.where(sorteio < prob_home_sintetica * 0.75 + 0.25, RESULTADO_DRAW, RESULTADO_AWAY),
    )
    return df


def montar_features_fixtures(fixtures: pd.DataFrame) -> pd.DataFrame:
    """Mesma fonte de força mock usada pelo Dixon-Coles, reaproveitada aqui
    só pra dar às árvores de decisão algo coerente pra prever em cima."""
    linhas = []
    for _, jogo in fixtures.iterrows():
        forca_casa = FORCA_TIMES_MOCK.get(jogo["home_team"], FORCA_TIMES_MOCK["_default"])
        forca_fora = FORCA_TIMES_MOCK.get(jogo["away_team"], FORCA_TIMES_MOCK["_default"])
        linhas.append(
            {
                "match_id": jogo["match_id"],
                "elo_home": 1500 + forca_casa["ataque"] * 100,
                "elo_away": 1500 + forca_fora["ataque"] * 100,
                "gols_marcados_media_home": forca_casa["ataque"] * 1.4,
                "gols_sofridos_media_home": forca_casa["defesa"] * 1.1,
                "gols_marcados_media_away": forca_fora["ataque"] * 1.3,
                "gols_sofridos_media_away": forca_fora["defesa"] * 1.15,
                "liga": "brasileirao",
            }
        )
    return pd.DataFrame(linhas)


def _empacotar_predicoes(match_ids, probs: np.ndarray, classes) -> dict[int, dict[str, float]]:
    """Mapeia a matriz (N, 3) de predict_proba pra {match_id: {prob_*}},
    respeitando a ordem real de `classes_` do modelo (nem sempre é [0,1,2])."""
    indice_da_classe = {int(rotulo): i for i, rotulo in enumerate(np.ravel(classes))}
    resultado = {}
    for match_id, linha_probs in zip(match_ids, probs):
        resultado[match_id] = {
            "prob_home": float(linha_probs[indice_da_classe[RESULTADO_HOME]]),
            "prob_draw": float(linha_probs[indice_da_classe[RESULTADO_DRAW]]),
            "prob_away": float(linha_probs[indice_da_classe[RESULTADO_AWAY]]),
        }
    return resultado


def treinar_e_prever_catboost_v1(fixtures: pd.DataFrame) -> dict[int, dict[str, float]]:
    dataset = gerar_dataset_treino_mock()
    modelo = CatBoostClassifier(
        loss_function="MultiClass",
        thread_count=2,
        iterations=200,
        depth=6,
        learning_rate=0.05,
        cat_features=CAT_FEATURES,
        random_seed=42,
        verbose=False,
    )
    modelo.fit(dataset[FEATURES], dataset["resultado"])

    features_fixtures = montar_features_fixtures(fixtures)
    probs = modelo.predict_proba(features_fixtures[FEATURES])
    return _empacotar_predicoes(features_fixtures["match_id"], probs, modelo.classes_)


def treinar_e_prever_xgboost_v1(fixtures: pd.DataFrame) -> dict[int, dict[str, float]]:
    dataset = gerar_dataset_treino_mock()
    dataset_encoded = pd.get_dummies(dataset[FEATURES], columns=CAT_FEATURES)

    modelo = XGBClassifier(
        objective="multi:softprob",
        num_class=3,
        n_estimators=200,
        max_depth=4,
        learning_rate=0.08,
        eval_metric="mlogloss",
        random_state=42,
    )
    modelo.fit(dataset_encoded, dataset["resultado"])

    features_fixtures = montar_features_fixtures(fixtures)
    features_fixtures_encoded = pd.get_dummies(features_fixtures[FEATURES], columns=CAT_FEATURES)
    # garante as mesmas colunas (mesma ordem) vistas no treino, mesmo se uma
    # liga não aparecer nas fixtures da rodada atual
    features_fixtures_encoded = features_fixtures_encoded.reindex(columns=dataset_encoded.columns, fill_value=0)

    probs = modelo.predict_proba(features_fixtures_encoded)
    return _empacotar_predicoes(features_fixtures["match_id"], probs, modelo.classes_)


def treinar_e_prever_lightgbm_v1(fixtures: pd.DataFrame) -> dict[int, dict[str, float]]:
    """Configuração deliberadamente leve/rápida (poucas árvores, folhas
    rasas) -- é o modelo mais barato dos 4 em custo de CPU no runner do
    GitHub Actions."""
    dataset = gerar_dataset_treino_mock()
    dataset_lgbm = dataset.copy()
    dataset_lgbm["liga"] = dataset_lgbm["liga"].astype("category")

    modelo = LGBMClassifier(
        objective="multiclass",
        num_class=3,
        n_estimators=80,
        num_leaves=15,
        learning_rate=0.1,
        min_child_samples=10,
        random_state=42,
        verbosity=-1,
    )
    modelo.fit(dataset_lgbm[FEATURES], dataset_lgbm["resultado"], categorical_feature=CAT_FEATURES)

    features_fixtures = montar_features_fixtures(fixtures)
    features_fixtures["liga"] = features_fixtures["liga"].astype("category")
    probs = modelo.predict_proba(features_fixtures[FEATURES])
    return _empacotar_predicoes(features_fixtures["match_id"], probs, modelo.classes_)


MODELOS = {
    "dixon_coles_v1": prever_dixon_coles_v1,
    "catboost_v1": treinar_e_prever_catboost_v1,
    "xgboost_v1": treinar_e_prever_xgboost_v1,
    "lightgbm_v1": treinar_e_prever_lightgbm_v1,
}


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
                {"match_id": e["match_id"], "home_team": e["home_team"], "away_team": e["away_team"]}
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

    # 2. Rodar os 4 modelos em sequência -- cada um isolado por try/except
    # pra uma falha não derrubar os outros 3 (e não deixar a tabela num
    # estado parcial silencioso). Sequencial em vez de paralelo de propósito:
    # CatBoost/LightGBM já usam thread_count=2 internamente, então rodar em
    # paralelo só disputaria os mesmos 2 núcleos do runner do GitHub Actions.
    todas_as_linhas: list[dict] = []
    for nome_modelo, funcao_modelo in MODELOS.items():
        try:
            logger.info("Rodando modelo %s...", nome_modelo)
            predicoes_modelo = funcao_modelo(fixtures)
            linhas = montar_linhas_predicoes(nome_modelo, predicoes_modelo, melhor_odd_por_partida)
            todas_as_linhas.extend(linhas)
            logger.info("%s: %d predição(ões) geradas.", nome_modelo, len(linhas))
        except Exception:
            logger.exception("Falha ao rodar o modelo %s -- pulando, os outros modelos continuam.", nome_modelo)

    # 3. Persistir tudo de uma vez
    salvar_predicoes(supabase, todas_as_linhas)
    logger.info("Concluído: %d predição(ões) salvas em predicoes.", len(todas_as_linhas))


if __name__ == "__main__":
    main()
