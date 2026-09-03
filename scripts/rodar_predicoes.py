#!/usr/bin/env python3
"""Rotina unificada de Model Benchmarking, executada pelo GitHub Actions.

Passo a passo (ver README do PR pra visão geral da arquitetura):
  1. Escolhe as próximas partidas agendadas (6 ligas) que já têm odds
     capturadas, lendo `odds_market` (alimentada em produção via OddsPapi,
     `api/model-maintenance.js ?tarefa=odds-todas`) -- pega o snapshot mais
     recente por casa/seleção e salva em `market_odds` via UPSERT. Não faz
     chamada de API própria: reaproveita odds reais que já existem.
  2. Roda os modelos de predição 1X2 (dixon_coles_v1 + catboost/xgboost/
     lightgbm em v1, v2, v3, v4, v5 e v3B) para as mesmas partidas -- cada
     um treinado com a janela de dado histórico apropriada pra sua família
     (ver `dados_historicos.py`: Dixon-Coles usa 2-3 temporadas +
     decaimento temporal; os modelos de árvore usam um dataset "Feature
     Stacked" de 5-8 temporadas empilhadas das 6 ligas do Model
     Benchmarking (5 europeias de elite + Brasileirão), com features de
     gols e xG separadas por mando -- ver `dados_historicos.
     _forma_por_mando`). A v2 (`modelos_ml.FEATURES_POR_MODELO`) soma força
     do elenco (rating Elo-like por jogador, `dados_historicos.
     obter_squad_rating_atual`/`_carregar_squad_rating_pre_jogo`), a v3 soma
     descanso pré-jogo/fadiga, a v4 soma risco de suspensão por acúmulo de
     cartão (`dados_historicos.obter_cartoes_atuais`/`_carregar_cartoes_pre_
     jogo`), a v5 soma classificação/tabela atual, confronto direto (H2H)
     e tendência de árbitro (`dados_historicos.obter_classificacao_atual`/
     `obter_h2h_atual`/`obter_arbitro_atual`), e a v3B soma força do XI
     titular CONFIRMADO + valor de mercado na data do jogo (`dados_
     historicos.obter_titular_atual`/`_carregar_titular_pre_jogo` -- nome
     "v3B" vem do PR #114, mas o conjunto de features é v5 + XI titular)
     às features da versão anterior -- dixon_coles_v1 não tem v2/v3/v4/v5/
     v3B (modelo Poisson de força de time, não aceita feature de jogador/
     contexto).
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

# As 6 ligas do Model Benchmarking (5 de elite europeias + Brasileirão) --
# mesmo escopo já usado em outras partes do pipeline (ex.: crosswalk de
# jogador) e, desde pedido explícito do usuário, também o mesmo escopo do
# dataset "Feature Stacked" dos modelos de ML (`dados_historicos.
# LIGAS_MODEL_BENCHMARKING`) -- Brasileirão tem cobertura de elo/xG
# pré-jogo mais fraca que a Europa (NaN-tolerante, ver docstring de
# `montar_dataset_ml_empilhado`), mas entra igual.
LIGAS_ESCOPO = [1, 4, 7, 10, 13, 16]

# Quantas partidas futuras (mais próximas) prever por rodada -- lote pequeno
# de propósito, roda diariamente via cron (ver predict.yml).
LIMITE_FIXTURES = 10

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
# Passo 1 — Fixtures + odds de mercado (dado REAL, já capturado pelo
# pipeline de produção via OddsPapi -- `api/model-maintenance.js
# ?tarefa=odds-todas`, tabela `odds_market`)
# =============================================================================
# `market_odds`/`predicoes` continuam tabelas propositalmente separadas de
# `odds_market`/`model_predictions` (não fazem upsert cruzado com elas) --
# mas não faz sentido pagar por uma integração nova (The-Odds-API) quando o
# pipeline de produção já mantém odds reais e atualizadas das mesmas 6
# ligas. `market_odds` passa a ser abastecida por LEITURA de `odds_market`
# (o snapshot mais recente por casa/seleção), não por uma chamada de API
# própria.
def buscar_fixtures_reais(supabase: Client, limite: int = LIMITE_FIXTURES) -> pd.DataFrame:
    """Partidas agendadas mais próximas, nas 6 ligas do benchmarking, que já
    têm pelo menos uma odd capturada em `odds_market` -- sem isso não dá pra
    calcular edge nenhum. `match_date >= hoje` filtra partidas com `status`
    desatualizado (agendada mas já no passado, problema de qualidade de
    dado em outra parte do pipeline, não deste script)."""
    agora = pd.Timestamp.now(tz="UTC").isoformat()
    candidatas = (
        supabase.table("matches")
        .select("id, league_id, home_team_id, away_team_id, match_date, season")
        .eq("status", "scheduled")
        .in_("league_id", LIGAS_ESCOPO)
        .gte("match_date", agora)
        .order("match_date")
        .limit(500)  # sobra suficiente pra filtrar por quem tem odds sem paginar
        .execute()
        .data
        or []
    )
    if not candidatas:
        return pd.DataFrame(columns=["match_id", "league_id", "home_team_id", "away_team_id", "match_date", "season", "liga"])

    ids_candidatos = [c["id"] for c in candidatas]
    com_odds = set()
    for i in range(0, len(ids_candidatos), 500):
        lote = ids_candidatos[i : i + 500]
        linhas = supabase.table("odds_market").select("match_id").in_("match_id", lote).execute().data or []
        com_odds.update(l["match_id"] for l in linhas)

    selecionadas = [c for c in candidatas if c["id"] in com_odds][:limite]
    if not selecionadas:
        return pd.DataFrame(columns=["match_id", "league_id", "home_team_id", "away_team_id", "match_date", "season", "liga"])

    ligas = supabase.table("leagues").select("id, name").in_("id", LIGAS_ESCOPO).execute().data or []
    nome_liga_por_id = {l["id"]: l["name"] for l in ligas}

    return pd.DataFrame(
        [
            {
                "match_id": c["id"],
                "league_id": c["league_id"],
                "home_team_id": c["home_team_id"],
                "away_team_id": c["away_team_id"],
                "match_date": c["match_date"],
                # temporada ATUAL da partida -- usado por v5 pra buscar a
                # classificação/tabela (`dados_historicos.
                # obter_classificacao_atual`, que precisa de (league_id,
                # season) pra saber em qual tabela olhar).
                "season": c["season"],
                # nome EXATO de `leagues.name` -- precisa bater com
                # `dados_historicos.LIGAS_MODEL_BENCHMARKING` pra alinhar com a
                # categoria "liga" vista no treino dos modelos de ML.
                "liga": nome_liga_por_id.get(c["league_id"]),
            }
            for c in selecionadas
        ]
    )


def buscar_odds_mais_recentes(supabase: Client, match_ids: list[int]) -> list[dict]:
    """Pega o snapshot mais recente de odds (mercado 1X2) por
    (match_id, bookmaker) em `odds_market` -- essa tabela é um LOG (cada
    sync é um INSERT novo, não upsert, pra dar pra montar a curva de
    movimento de linha), então precisa filtrar pro ponto mais recente antes
    de achatar em `odd_home`/`odd_draw`/`odd_away` pro formato de
    `market_odds`."""
    if not match_ids:
        return []

    linhas_odds = []
    for i in range(0, len(match_ids), 500):
        lote = match_ids[i : i + 500]
        linhas_odds.extend(
            supabase.table("odds_market")
            .select("match_id, bookmaker, selection, odds, captured_at")
            .eq("market", "1X2")
            .in_("match_id", lote)
            .execute()
            .data
            or []
        )

    # Mantém só a linha mais recente por (match_id, bookmaker, selection).
    mais_recente: dict[tuple, dict] = {}
    for linha in linhas_odds:
        chave = (linha["match_id"], linha["bookmaker"], linha["selection"])
        atual = mais_recente.get(chave)
        if atual is None or linha["captured_at"] > atual["captured_at"]:
            mais_recente[chave] = linha

    achatado: dict[tuple, dict] = {}
    campo_por_selecao = {"home": "odd_home", "draw": "odd_draw", "away": "odd_away"}
    for (match_id, bookmaker, selecao), linha in mais_recente.items():
        campo = campo_por_selecao.get(selecao)
        if not campo:
            continue
        registro = achatado.setdefault((match_id, bookmaker), {"match_id": match_id, "bookmaker": bookmaker})
        registro[campo] = float(linha["odds"])

    return list(achatado.values())


def salvar_odds_mercado(supabase: Client, linhas: list[dict]) -> None:
    if not linhas:
        return
    supabase.table("market_odds").upsert(linhas, on_conflict="match_id,bookmaker").execute()


# =============================================================================
# Passo 2 — Os modelos (dixon_coles_v1 + árvores v1/v2)
# =============================================================================

# --- Modelo 1: dixon_coles_v1 (Poisson bivariado com correção Dixon-Coles) ---
MANDO_CASA = 1.15  # multiplicador de vantagem de jogar em casa
RHO_DIXON_COLES = -0.05  # correção de baixo placar (0x0, 1x0, 0x1, 1x1)
MAX_GOLS_SIMULADOS = 8

# Linhas de over/under de GOLS POR TIME (mandante/visitante separados) --
# marginal da MESMA grade (gc, gf) já simulada pro 1X2/over_under_2.5/faixa
# de gols, sem simulação nem retreino extra (mesmo espírito da nota em
# `_prever_probs_dixon_coles` sobre over/under total). Nomeado
# `team_1`/`team_2` (não `home`/`away`) pra bater com a convenção real do
# mercado de gols por time já em produção em `odds_market` (OddsPapi) --
# confirmado empiricamente nesta sessão (odds-sync-diagnostico, tournament
# 325, + teste por-partida contra `team_1_to_score`/`team_2_to_score`:
# team_1 prevê o mandante marcar em 78,0% das partidas vs. 68,8% pro
# visitante, N=1248 finalizadas): team_1 = mandante, team_2 = visitante.
# Gêmeo do bloco em `src/utils/distribuicoesMercados.js`/`scripts/
# distribuicoes.py::mercados_de_gols` (que fazem a mesma extração de
# marginal a partir de uma matriz de placares completa, pro uso do modelo
# misto -- essa função aqui é a implementação PRÓPRIA do dixon_coles_v1,
# independente e não compartilhada com `distribuicoes.py`, mesmo padrão já
# existente no arquivo pro resto do Dixon-Coles).
LINHAS_GOLS_TIME = (0.5, 1.5, 2.5, 3.5, 4.5)


def _rotulo_linha_time(linha: float) -> str:
    return f"{linha:.1f}"


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
    calibram o modelo (`_calibrar_dixon_coles`).

    Devolve 1X2, Over/Under 2.5 E faixa de gols (0-1/2-3/4-6/7+) do MESMO
    grid de placares (o Dixon-Coles já simula todo o placar cheio pra tirar
    1X2 -- Over/Under e faixa de gols são só somar as mesmas células por
    total de gols em vez de por vencedor, sem custo adicional de simulação
    nem retreino nenhum, ao contrário dos modelos de árvore). Escanteios
    (Over/Under 9.5) fica FORA -- é um modelo Poisson de GOLS, não tem
    nenhuma noção de escanteio (ver `backtest_kelly.MERCADOS_SEM_DIXON_
    COLES`, que pula esse mercado pro dixon_coles_v1)."""
    lambda_casa = forca_casa["ataque"] * forca_fora["defesa"] * MANDO_CASA
    lambda_fora = forca_fora["ataque"] * forca_casa["defesa"]

    prob_home = prob_draw = prob_away = 0.0
    prob_over25 = prob_under25 = 0.0
    prob_por_faixa = {
        dados_historicos.RESULTADO_FAIXA_0_1: 0.0,
        dados_historicos.RESULTADO_FAIXA_2_3: 0.0,
        dados_historicos.RESULTADO_FAIXA_4_6: 0.0,
        dados_historicos.RESULTADO_FAIXA_7MAIS: 0.0,
    }
    prob_over_team1 = {linha: 0.0 for linha in LINHAS_GOLS_TIME}
    prob_over_team2 = {linha: 0.0 for linha in LINHAS_GOLS_TIME}
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

            if gc + gf > 2.5:
                prob_over25 += p
            else:
                prob_under25 += p

            prob_por_faixa[dados_historicos.codigo_faixa_gols(gc + gf)] += p

            for linha in LINHAS_GOLS_TIME:
                if gc > linha:
                    prob_over_team1[linha] += p
                if gf > linha:
                    prob_over_team2[linha] += p

    total = prob_home + prob_draw + prob_away  # normaliza o corte da soma infinita (mesmo total das duas partições)
    resultado = {
        "prob_home": prob_home / total,
        "prob_draw": prob_draw / total,
        "prob_away": prob_away / total,
        "prob_over": prob_over25 / total,
        "prob_under": prob_under25 / total,
        "prob_faixa_0_1": prob_por_faixa[dados_historicos.RESULTADO_FAIXA_0_1] / total,
        "prob_faixa_2_3": prob_por_faixa[dados_historicos.RESULTADO_FAIXA_2_3] / total,
        "prob_faixa_4_6": prob_por_faixa[dados_historicos.RESULTADO_FAIXA_4_6] / total,
        "prob_faixa_7mais": prob_por_faixa[dados_historicos.RESULTADO_FAIXA_7MAIS] / total,
    }
    for linha in LINHAS_GOLS_TIME:
        rot = _rotulo_linha_time(linha)
        over_t1 = prob_over_team1[linha] / total
        over_t2 = prob_over_team2[linha] / total
        resultado[f"prob_team_1_over_{rot}"] = over_t1
        resultado[f"prob_team_1_under_{rot}"] = 1.0 - over_t1
        resultado[f"prob_team_2_over_{rot}"] = over_t2
        resultado[f"prob_team_2_under_{rot}"] = 1.0 - over_t2
    return resultado


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

        forca_casa = forcas.get(jogo["home_team_id"], FORCA_PADRAO)
        forca_fora = forcas.get(jogo["away_team_id"], FORCA_PADRAO)

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
    obter_forma_recente_por_mando` -- e força do elenco atual pros modelos
    v2, `dados_historicos.obter_squad_rating_atual`, que já exclui
    lesionados/suspensos do cálculo) pros times de `fixtures`. `liga` já
    vem de `buscar_fixtures_reais` (nome real de `leagues.name`) e cobre
    as 6 ligas do benchmarking (inclusive Brasileirão, que agora também
    entra no dataset "Feature Stacked" -- ver
    `dados_historicos.LIGAS_MODEL_BENCHMARKING`). v4 (risco de suspensão
    por cartão, `dados_historicos.obter_cartoes_atuais`) já rodava pras 6
    ligas do benchmarking (`CARTAO_LIMIAR_POR_LIGA` já tem a regra da
    CBF). v5 soma classificação/
    tabela atual (`dados_historicos.obter_classificacao_atual`), confronto
    direto (`obter_h2h_atual` + `resumir_h2h`) e tendência de árbitro
    (`obter_arbitro_atual` -- best-effort, NaN honesto quando o árbitro da
    partida ainda não foi divulgado, o que é a norma pra fixtures a mais de
    ~1h do apito inicial). v3B soma força do XI titular confirmado + valor
    de mercado na data do jogo (`obter_titular_atual` -- mesma limitação
    best-effort do árbitro: escalação confirmada só costuma sair perto do
    apito, então fica NaN pra quase toda fixture com dias de antecedência;
    cobertura de treino depende do escopo do backfill parcial de
    `match_lineup_fotmob`, ver arquivos_do_claude/
    ingestao_fotmob_lineup_backfill.py)."""
    ids_conhecidos = sorted(set(fixtures["home_team_id"]) | set(fixtures["away_team_id"]))
    elo_atual = dados_historicos.obter_elo_atual(supabase, ids_conhecidos)
    forma_recente = dados_historicos.obter_forma_recente_por_mando(supabase, ids_conhecidos)
    # v7/v8 ao vivo: forma pré-jogo do FBref (`match_stats`) e do FotMob
    # (`match_stats_fotmob`), mesmo espírito de `forma_recente` (gols/xG)
    # -- sem isso, catboost_v7/v8, xgboost_v7/v8 e lightgbm_v7/v8 quebravam
    # com KeyError na hora de prever (as colunas só existiam no dataset de
    # TREINO, nunca no lado ao vivo).
    forma_extra_atual = dados_historicos.obter_forma_recente_extra_por_mando(supabase, ids_conhecidos)
    forma_fotmob_atual = dados_historicos.obter_forma_recente_fotmob_por_mando(supabase, ids_conhecidos)
    squad_rating_atual = dados_historicos.obter_squad_rating_atual(supabase, ids_conhecidos)
    ultimo_jogo_por_time = dados_historicos.obter_fadiga_atual(supabase, ids_conhecidos)
    forma_padrao = {coluna: float("nan") for coluna in dados_historicos.FEATURES_NUMERICAS if coluna not in ("elo_home", "elo_away")}

    league_id_por_time: dict[int, int] = {}
    nome_por_league_id: dict[int, str] = {}
    for _, jogo in fixtures.iterrows():
        if pd.isna(jogo.get("league_id")):
            continue
        league_id = int(jogo["league_id"])
        league_id_por_time[jogo["home_team_id"]] = league_id
        league_id_por_time[jogo["away_team_id"]] = league_id
        nome_por_league_id[league_id] = jogo["liga"]
    cartoes_atuais = dados_historicos.obter_cartoes_atuais(supabase, ids_conhecidos, nome_por_league_id, league_id_por_time)
    cartoes_padrao = {"cartoes_acumulados": float("nan"), "jogadores_pendurados": float("nan")}

    ligas_temporadas = sorted({(int(jogo["league_id"]), jogo["season"]) for _, jogo in fixtures.iterrows() if pd.notna(jogo.get("league_id")) and pd.notna(jogo.get("season"))})
    classificacao_atual = dados_historicos.obter_classificacao_atual(supabase, ligas_temporadas)
    classificacao_padrao = {"pontos_por_jogo": 0.0, "saldo_por_jogo": 0.0, "posicao": float("nan"), "jogos_disputados": 0}
    # v6 ao vivo: mesmas (league_id, season) já levantadas pra classificação.
    progresso_atual = dados_historicos.obter_progresso_temporada_atual(supabase, ligas_temporadas)
    bayesiano_atual = dados_historicos.obter_bayesiano_atual(supabase, ligas_temporadas)

    pares_de_times = [(jogo["home_team_id"], jogo["away_team_id"]) for _, jogo in fixtures.iterrows()]
    h2h_atual = dados_historicos.obter_h2h_atual(supabase, pares_de_times)

    match_ids = fixtures["match_id"].tolist()
    arbitro_atual = dados_historicos.obter_arbitro_atual(supabase, match_ids)
    arbitro_padrao = {"arbitro_cartoes_media": float("nan"), "arbitro_faltas_media": float("nan"), "arbitro_n_jogos": 0}

    titular_atual = dados_historicos.obter_titular_atual(supabase, match_ids)
    titular_padrao = {"titular_rating": float("nan"), "titular_valor_mercado": float("nan")}

    linhas = []
    for _, jogo in fixtures.iterrows():
        id_casa = jogo["home_team_id"]
        id_fora = jogo["away_team_id"]
        forma_casa = forma_recente.get(id_casa, forma_padrao)
        forma_fora = forma_recente.get(id_fora, forma_padrao)
        forma_extra_casa = forma_extra_atual.get(id_casa, {})
        forma_extra_fora = forma_extra_atual.get(id_fora, {})
        forma_fotmob_casa = forma_fotmob_atual.get(id_casa, {})
        forma_fotmob_fora = forma_fotmob_atual.get(id_fora, {})
        cartoes_casa = cartoes_atuais.get(id_casa, cartoes_padrao)
        cartoes_fora = cartoes_atuais.get(id_fora, cartoes_padrao)
        data_jogo = pd.to_datetime(jogo["match_date"], utc=True)
        dias_casa, midweek_casa = _fadiga_da_fixture(data_jogo, ultimo_jogo_por_time.get(id_casa))
        dias_fora, midweek_fora = _fadiga_da_fixture(data_jogo, ultimo_jogo_por_time.get(id_fora))
        league_id = league_id_por_time.get(id_casa)
        classificacao_casa = classificacao_atual.get((league_id, id_casa), classificacao_padrao)
        classificacao_fora = classificacao_atual.get((league_id, id_fora), classificacao_padrao)
        historico_h2h = h2h_atual.get(tuple(sorted((id_casa, id_fora))), [])
        h2h_taxa_vitoria_mandante, h2h_media_gols, h2h_n_jogos = dados_historicos.resumir_h2h(historico_h2h, id_casa)
        arbitro = arbitro_atual.get(jogo["match_id"], arbitro_padrao)
        titular_por_time = titular_atual.get(jogo["match_id"], {})
        titular_casa = titular_por_time.get(id_casa, titular_padrao)
        titular_fora = titular_por_time.get(id_fora, titular_padrao)
        bayes_casa = bayesiano_atual.get(id_casa, {})
        bayes_fora = bayesiano_atual.get(id_fora, {})

        # v7 (FBref) + v8 (FotMob): "_home" vem do time da CASA jogando em
        # casa, "_away" vem do time de FORA jogando fora -- mesmo padrão de
        # `forma_casa`/`forma_fora` acima (gols/xG), só que iterando sobre
        # os mapas de coluna em vez de listar cada campo na mão (7+24
        # famílias de estatística x 4 campos = 124 colunas).
        colunas_v7v8 = {}
        for mapa in dados_historicos.COLUNAS_FORMA_EXTRA_POR_RAW.values():
            colunas_v7v8[mapa["marcado_home"]] = forma_extra_casa.get(mapa["marcado_home"])
            colunas_v7v8[mapa["sofrido_home"]] = forma_extra_casa.get(mapa["sofrido_home"])
            colunas_v7v8[mapa["marcado_away"]] = forma_extra_fora.get(mapa["marcado_away"])
            colunas_v7v8[mapa["sofrido_away"]] = forma_extra_fora.get(mapa["sofrido_away"])
        for nome_curto in dados_historicos.COLUNAS_STATS_FOTMOB.values():
            mapa = dados_historicos.colunas_forma_fotmob(nome_curto)
            colunas_v7v8[mapa["marcado_home"]] = forma_fotmob_casa.get(mapa["marcado_home"])
            colunas_v7v8[mapa["sofrido_home"]] = forma_fotmob_casa.get(mapa["sofrido_home"])
            colunas_v7v8[mapa["marcado_away"]] = forma_fotmob_fora.get(mapa["marcado_away"])
            colunas_v7v8[mapa["sofrido_away"]] = forma_fotmob_fora.get(mapa["sofrido_away"])

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
                # Nomes NOVOS (mesmos valores de forma_casa/forma_fora acima,
                # só com a chave que FEATURES_V10_XG_CORRIGIDO espera -- ver
                # achado #15/CONTEXTO_PROJETO.md e modelos_ml.py) --
                # aditivo, não substitui as chaves antigas acima (outros
                # modelos ainda podem referenciar o nome antigo).
                "xg_home_5j": forma_casa.get("media_xg_5j_home"),
                "xg_sofrido_home_5j": forma_casa.get("media_xg_sofrido_5j_home"),
                "xg_away_5j": forma_fora.get("media_xg_5j_away"),
                "xg_sofrido_away_5j": forma_fora.get("media_xg_sofrido_5j_away"),
                "xgot_home_5j": forma_casa.get("xgot_home_5j"),
                "xgot_sofrido_home_5j": forma_casa.get("xgot_sofrido_home_5j"),
                "xgot_away_5j": forma_fora.get("xgot_away_5j"),
                "xgot_sofrido_away_5j": forma_fora.get("xgot_sofrido_away_5j"),
                "squad_rating_home": squad_rating_atual.get(id_casa, float("nan")),
                "squad_rating_away": squad_rating_atual.get(id_fora, float("nan")),
                "days_since_last_match_home": dias_casa,
                "days_since_last_match_away": dias_fora,
                "is_midweek_fatigue_home": midweek_casa,
                "is_midweek_fatigue_away": midweek_fora,
                "cartoes_acumulados_home": cartoes_casa.get("cartoes_acumulados"),
                "cartoes_acumulados_away": cartoes_fora.get("cartoes_acumulados"),
                "jogadores_pendurados_home": cartoes_casa.get("jogadores_pendurados"),
                "jogadores_pendurados_away": cartoes_fora.get("jogadores_pendurados"),
                "xg_bayesiano_home": bayes_casa.get("xg_bayesiano"),
                "xg_bayesiano_away": bayes_fora.get("xg_bayesiano"),
                "xgot_bayesiano_home": bayes_casa.get("xgot_bayesiano"),
                "xgot_bayesiano_away": bayes_fora.get("xgot_bayesiano"),
                "xga_bayesiano_home": bayes_casa.get("xga_bayesiano"),
                "xga_bayesiano_away": bayes_fora.get("xga_bayesiano"),
                "is_stat_estimated_home": bayes_casa.get("is_stat_estimated"),
                "is_stat_estimated_away": bayes_fora.get("is_stat_estimated"),
                "pontos_por_jogo_home": classificacao_casa.get("pontos_por_jogo"),
                "pontos_por_jogo_away": classificacao_fora.get("pontos_por_jogo"),
                "saldo_por_jogo_home": classificacao_casa.get("saldo_por_jogo"),
                "saldo_por_jogo_away": classificacao_fora.get("saldo_por_jogo"),
                "posicao_home": classificacao_casa.get("posicao"),
                "posicao_away": classificacao_fora.get("posicao"),
                "jogos_disputados_home": classificacao_casa.get("jogos_disputados"),
                "jogos_disputados_away": classificacao_fora.get("jogos_disputados"),
                "h2h_taxa_vitoria_mandante": h2h_taxa_vitoria_mandante,
                "h2h_media_gols": h2h_media_gols,
                "h2h_n_jogos": h2h_n_jogos,
                "arbitro_cartoes_media": arbitro.get("arbitro_cartoes_media"),
                "arbitro_faltas_media": arbitro.get("arbitro_faltas_media"),
                "arbitro_n_jogos": arbitro.get("arbitro_n_jogos"),
                "titular_rating_home": titular_casa.get("titular_rating"),
                "titular_rating_away": titular_fora.get("titular_rating"),
                "titular_valor_mercado_home": titular_casa.get("titular_valor_mercado"),
                "titular_valor_mercado_away": titular_fora.get("titular_valor_mercado"),
                "progresso_temporada": progresso_atual.get(jogo["match_id"]),
                **colunas_v7v8,
                "liga": jogo["liga"],
            }
        )
    return pd.DataFrame(linhas)


def _fadiga_da_fixture(data_jogo: pd.Timestamp, data_ultimo_jogo: pd.Timestamp | None) -> tuple[float, int]:
    """Descanso (dias) e flag de turnaround apertado (<72h) pra uma fixture
    específica -- mesmos limiares de `arquivos_do_claude/features_contexto.
    py` (`dados_historicos.DIAS_DESCANSO_PADRAO`/`LIMIAR_MIDWEEK_HORAS`).
    Sem jogo anterior no histórico (estreia do time) cai no mesmo fallback
    da versão histórica: `DIAS_DESCANSO_PADRAO` dias, sem fadiga."""
    if data_ultimo_jogo is None:
        return dados_historicos.DIAS_DESCANSO_PADRAO, 0
    horas = (data_jogo - data_ultimo_jogo).total_seconds() / 3600
    return round(horas / 24, 2), (1 if horas < dados_historicos.LIMIAR_MIDWEEK_HORAS else 0)


def prever_ml_com_calibracao(
    nome_modelo: str,
    features_fixtures: pd.DataFrame,
    dataset_treino: pd.DataFrame,
    coluna_alvo: str = "resultado",
    codigo_por_selecao: dict[str, int] | None = None,
    selecoes: tuple[str, ...] | None = None,
) -> tuple[dict[int, dict[str, float]], dict[str, dict[int, dict[str, float]]]]:
    """Treina/prevê um modelo de árvore com suas variantes calibradas
    "conjugadas" (Platt e Isotonic, lado a lado): treina só no Train,
    ajusta os dois métodos no Validation (nunca visto pelo treino), depois
    refita no dataset INTEIRO (treino+validação) pra prever as fixtures de
    verdade, aplicando cada calibração já ajustada. Devolve
    `(predicoes_cruas, predicoes_calibradas_por_metodo)`.

    `coluna_alvo`/`codigo_por_selecao`/`selecoes` default pro mercado 1X2
    (uso diário em produção, `main()` abaixo) -- passados explicitamente só
    por quem também precisa de Over/Under 2.5 (`backfill_predicoes_
    historicas.py`, mesmo padrão de mercado de `backtest_kelly.MERCADOS`)."""
    treinar, prever = modelos_ml.TREINADORES[nome_modelo]
    params = modelos_ml.PARAMS_DEFAULT[nome_modelo]
    features = modelos_ml.FEATURES_POR_MODELO[nome_modelo]

    train_df, val_df, _ = dados_historicos.split_cronologico(
        dataset_treino, treino=1 - FRACAO_VALIDACAO_CALIBRACAO, validacao=FRACAO_VALIDACAO_CALIBRACAO, teste=0.0
    )

    calibradores_por_metodo: dict[str, dict] = {metodo: {} for metodo in METODOS_CALIBRACAO}
    if not val_df.empty and not train_df.empty:
        modelo_val, extra_val, _ = treinar(params, train_df, coluna_alvo=coluna_alvo, features=features)
        probs_val, classes_val = prever(modelo_val, extra_val, val_df, features=features)
        preds_val = modelos_ml.empacotar_predicoes(val_df["match_id"].tolist(), probs_val, classes_val, coluna_alvo=coluna_alvo)
        resultados_val = dict(zip(val_df["match_id"], val_df[coluna_alvo]))
        for metodo in METODOS_CALIBRACAO:
            calibradores_por_metodo[metodo] = calibracao.ajustar_calibracao(
                preds_val, resultados_val, metodo=metodo, codigo_por_selecao=codigo_por_selecao
            )

    modelo_final, extra_final, _ = treinar(params, dataset_treino, coluna_alvo=coluna_alvo, features=features)
    probs_fixtures, classes_fixtures = prever(modelo_final, extra_final, features_fixtures, features=features)
    predicoes_raw = modelos_ml.empacotar_predicoes(
        features_fixtures["match_id"].tolist(), probs_fixtures, classes_fixtures, coluna_alvo=coluna_alvo
    )

    predicoes_calibradas: dict[str, dict[int, dict[str, float]]] = {}
    for metodo, coef in calibradores_por_metodo.items():
        predicoes_calibradas[metodo] = (
            {mid: calibracao.aplicar_calibracao(p, coef, selecoes=selecoes) for mid, p in predicoes_raw.items()} if coef else predicoes_raw
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


def renormalizar_arredondado(valores: dict[str, float], casas: int = 5) -> dict[str, float]:
    """`round()` de cada probabilidade em separado (o que já acontecia antes)
    não garante que a soma continue exatamente 1.0 -- softmax/calibração já
    garantem a soma ANTES do arredondamento, mas arredondar cada uma
    isoladamente pode produzir 0.99999 ou 1.00001 (achado real: usuário
    reportou probabilidades não somando 100% nos modelos de ML). Corrige
    absorvendo o resíduo do arredondamento na MAIOR probabilidade (é a que
    menos distorce percentualmente) -- mesma técnica usada em partilha de
    assentos (maior resto), garante soma exata sem mudar qual seleção é
    favorita."""
    arredondados = {chave: round(valor, casas) for chave, valor in valores.items()}
    residuo = round(1.0 - sum(arredondados.values()), casas)
    if residuo != 0:
        chave_maior = max(arredondados, key=arredondados.get)
        arredondados[chave_maior] = round(arredondados[chave_maior] + residuo, casas)
    return arredondados


def montar_linhas_predicoes(
    nome_modelo: str,
    predicoes_modelo: dict[int, dict[str, float]],
    melhor_odd_por_partida: dict[int, dict[str, float]],
) -> list[dict]:
    linhas = []
    for match_id, probs in predicoes_modelo.items():
        melhor_odd = melhor_odd_por_partida.get(match_id, {})
        edge = calcular_edge(probs, melhor_odd)
        normalizadas = renormalizar_arredondado(
            {"prob_home": probs["prob_home"], "prob_draw": probs["prob_draw"], "prob_away": probs["prob_away"]}
        )
        linhas.append(
            {
                "match_id": int(match_id),
                "model_name": nome_modelo,
                "mercado": "1X2",
                "prob_home": normalizadas["prob_home"],
                "prob_draw": normalizadas["prob_draw"],
                "prob_away": normalizadas["prob_away"],
                "edge_detectado": round(edge, 5) if edge is not None else None,
            }
        )
    return linhas


def montar_linhas_predicoes_over_under(
    nome_modelo: str,
    predicoes_modelo: dict[int, dict[str, float]],
    mercado: str,
    chave_prob_over: str = "prob_over",
    chave_prob_under: str = "prob_under",
) -> list[dict]:
    """Genérico pra qualquer mercado de 2 seleções (over/under), gravado nas
    MESMAS colunas `prob_over`/`prob_under` de `predicoes` (não precisa de
    schema novo -- `mercado` é texto livre, ver migração `add_mercado_
    predicoes`) -- `chave_prob_over`/`chave_prob_under` deixam escolher DE
    QUAL chave do dict de probabilidades (`_prever_probs_dixon_coles` ou o
    output de `modelos_ml.empacotar_predicoes`) puxar o valor, pra
    reaproveitar com over_under_2.5 (chaves default) e com os mercados de
    gols por time (`prob_team_1_over_X.X`/`prob_team_1_under_X.X`, etc.)."""
    linhas = []
    for match_id, probs in predicoes_modelo.items():
        normalizadas = renormalizar_arredondado({"prob_under": probs[chave_prob_under], "prob_over": probs[chave_prob_over]})
        linhas.append(
            {
                "match_id": int(match_id),
                "model_name": nome_modelo,
                "mercado": mercado,
                "prob_under": normalizadas["prob_under"],
                "prob_over": normalizadas["prob_over"],
                "edge_detectado": None,
            }
        )
    return linhas


def montar_linhas_predicoes_over_under25(nome_modelo: str, predicoes_modelo: dict[int, dict[str, float]]) -> list[dict]:
    """Mesma ideia de `montar_linhas_predicoes`, mas pro mercado Over/Under
    2.5 gols (`prob_under`/`prob_over`, ver migração `add_mercado_
    predicoes`) -- usado pelo backfill histórico
    (`backfill_predicoes_historicas.py`, dixon_coles_v1 E os 18 modelos de
    árvore, esses últimos com classificador próprio pro alvo
    `resultado_over25`) e, desde a correção do achado de cron congelado
    (ver `main()`), também pelo cron diário pro dixon_coles_v1. Mantido como
    wrapper fino sobre `montar_linhas_predicoes_over_under` (mercado/chaves
    fixos) pra não mudar a assinatura dos call-sites já existentes."""
    return montar_linhas_predicoes_over_under(nome_modelo, predicoes_modelo, "over_under_2.5")


TAMANHO_LOTE_UPSERT_PREDICOES = 500  # um upsert só com dezenas de milhares de
# linhas (ex.: backfill_predicoes_historicas.py, 19 modelos x milhares de
# partidas x 3 variantes) estoura o statement_timeout do Postgres
# ("57014 canceling statement due to statement timeout") -- confirmado
# rodando o backfill de verdade. O cron diário nunca teria esse volume
# sozinho, mas o lote protege os dois casos igual.


def salvar_predicoes(supabase: Client, linhas: list[dict]) -> None:
    if not linhas:
        return
    for inicio in range(0, len(linhas), TAMANHO_LOTE_UPSERT_PREDICOES):
        lote = linhas[inicio : inicio + TAMANHO_LOTE_UPSERT_PREDICOES]
        supabase.table("predicoes").upsert(lote, on_conflict="match_id,model_name,mercado").execute()


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


def _persistir_cru_e_calibrados_over_under25(
    nome_modelo: str,
    predicoes_raw: dict[int, dict[str, float]],
    predicoes_calibradas_por_metodo: dict[str, dict[int, dict[str, float]]],
    todas_as_linhas: list[dict],
) -> None:
    """Mesma ideia de `_persistir_cru_e_calibrados`, pro mercado Over/Under
    2.5 -- usado pelo backfill histórico (`backfill_predicoes_historicas.
    py`) e, desde a correção do achado de cron congelado, também pelo cron
    diário pro dixon_coles_v1 (ver `main()`). Sem edge (não há odd de
    over/under em `market_odds`, a fonte usada pro edge ao vivo)."""
    linhas_raw = montar_linhas_predicoes_over_under25(nome_modelo, predicoes_raw)
    todas_as_linhas.extend(linhas_raw)

    total_calibradas = 0
    for metodo, predicoes_calibradas in predicoes_calibradas_por_metodo.items():
        linhas_calibradas = montar_linhas_predicoes_over_under25(f"{nome_modelo}_calibrado_{metodo}", predicoes_calibradas)
        todas_as_linhas.extend(linhas_calibradas)
        total_calibradas += len(linhas_calibradas)

    logger.info(
        "%s [over_under_2.5]: %d predição(ões) cruas + %d calibradas (%s).",
        nome_modelo, len(linhas_raw), total_calibradas, ", ".join(predicoes_calibradas_por_metodo),
    )


def _persistir_cru_e_calibrados_gols_time(
    nome_modelo: str,
    predicoes_raw: dict[int, dict[str, float]],
    predicoes_calibradas_por_metodo: dict[str, dict[int, dict[str, float]]],
    todas_as_linhas: list[dict],
) -> None:
    """Mesma ideia de `_persistir_cru_e_calibrados_over_under25`, pros
    mercados de gols por time (`over_under_team_1_X.X`/`over_under_team_2_
    X.X`, `LINHAS_GOLS_TIME`) -- só dixon_coles_v1 tem essas chaves
    (`_prever_probs_dixon_coles`); os modelos de árvore não são chamados
    aqui (previam um alvo binário treinado à parte, e treinar um alvo novo
    por linha/time pra cada um dos 18 modelos é um escopo maior, fora desta
    extensão). Sem edge -- mesma razão do over_under_2.5 (sem odd de gols
    por time em `market_odds`, só em `odds_market`, fonte separada que
    `api/backtest-betting.js`/`api/model-stats.js` consultam direto)."""
    total_linhas_raw = 0
    total_linhas_calibradas = 0
    for linha in LINHAS_GOLS_TIME:
        rot = _rotulo_linha_time(linha)
        for time_slug, chave_over, chave_under in (
            ("team_1", f"prob_team_1_over_{rot}", f"prob_team_1_under_{rot}"),
            ("team_2", f"prob_team_2_over_{rot}", f"prob_team_2_under_{rot}"),
        ):
            mercado = f"over_under_{time_slug}_{rot}"
            linhas_raw = montar_linhas_predicoes_over_under(nome_modelo, predicoes_raw, mercado, chave_over, chave_under)
            todas_as_linhas.extend(linhas_raw)
            total_linhas_raw += len(linhas_raw)

            for metodo, predicoes_calibradas in predicoes_calibradas_por_metodo.items():
                linhas_calibradas = montar_linhas_predicoes_over_under(
                    f"{nome_modelo}_calibrado_{metodo}", predicoes_calibradas, mercado, chave_over, chave_under
                )
                todas_as_linhas.extend(linhas_calibradas)
                total_linhas_calibradas += len(linhas_calibradas)

    logger.info(
        "%s [gols por time, %d linhas x 2 times]: %d predição(ões) cruas + %d calibradas.",
        nome_modelo, len(LINHAS_GOLS_TIME), total_linhas_raw, total_linhas_calibradas,
    )


# =============================================================================
# Orquestração
# =============================================================================
def main() -> None:
    logger.info("Iniciando rotina de predições (model benchmarking)...")
    supabase = get_supabase_client()

    # 1. Fixtures futuras (6 ligas) que já têm odds capturadas + o snapshot
    # mais recente dessas odds -- dado real, lido de `odds_market`
    # (alimentada pelo pipeline de produção via OddsPapi), não mais mock.
    fixtures = buscar_fixtures_reais(supabase)
    if fixtures.empty:
        logger.warning("Nenhuma partida futura com odds capturadas nas 6 ligas. Encerrando.")
        return

    linhas_odds = buscar_odds_mais_recentes(supabase, fixtures["match_id"].astype(int).tolist())
    salvar_odds_mercado(supabase, linhas_odds)
    logger.info("%d partida(s) selecionada(s), %d linha(s) de odds salvas em market_odds.", len(fixtures), len(linhas_odds))

    melhor_odd_por_partida = calcular_melhor_odd_por_partida(supabase, fixtures["match_id"].astype(int).tolist())

    # 2. Rodar os modelos -- cada um isolado por try/except pra uma falha
    # não derrubar os outros 3 (e não deixar a tabela num estado parcial
    # silencioso). Cada modelo grava uma linha crua + uma calibrada
    # (Item 3 -- "modelo extra conjugado").
    todas_as_linhas: list[dict] = []

    try:
        logger.info("Rodando modelo dixon_coles_v1 (janela de %d temporadas + decaimento temporal)...", JANELA_DIXON_COLES_TEMPORADAS)
        preds_raw, preds_calibradas = prever_dixon_coles_v1(fixtures, supabase)
        _persistir_cru_e_calibrados("dixon_coles_v1", preds_raw, preds_calibradas, melhor_odd_por_partida, todas_as_linhas)
        # Achado real corrigido nesta sessão: até aqui, over_under_2.5 só era
        # persistido pelo backfill histórico (`backfill_predicoes_
        # historicas.py`) -- o cron diário nunca escrevia essa previsão pra
        # fixtures FUTURAS, então o mercado ficava congelado na data do
        # último backfill manual. `_prever_probs_dixon_coles` já calculava
        # `prob_over`/`prob_under` de qualquer forma (mesmo grid do 1X2,
        # sem custo adicional) -- só faltava persistir aqui também. Os
        # mercados novos de gols por time (`over_under_team_1/2_X.X`)
        # herdariam o mesmo problema se só entrassem no backfill, por isso
        # os dois entram juntos.
        #
        # Só a variante CRUA (dict de calibração vazio, `{}`) -- `preds_
        # calibradas` é o resultado de `calibracao.aplicar_calibracao` já
        # aplicado pro 1X2 (`selecoes=("home","draw","away")` implícito em
        # `prever_dixon_coles_v1`), que devolve um dict com SÓ essas 3
        # chaves (`aplicar_calibracao` reconstrói o dict do zero a partir de
        # `quais_selecoes`, não preserva as demais). Reusar esse dict aqui
        # daria KeyError em `probs[chave_prob_over]`. Calibrar de verdade
        # esses mercados exigiria ajustar um calibrador PRÓPRIO (Validation
        # Set com o alvo certo por mercado, mesmo padrão de `_calibrar_
        # dixon_coles`) -- fora do escopo desta correção; o backfill
        # histórico já faz isso certo pro over_under_2.5 (`codigo_por_
        # selecao_ou25` dedicado, ver `backfill_predicoes_historicas.py`).
        _persistir_cru_e_calibrados_over_under25("dixon_coles_v1", preds_raw, {}, todas_as_linhas)
        _persistir_cru_e_calibrados_gols_time("dixon_coles_v1", preds_raw, {}, todas_as_linhas)
    except Exception:
        logger.exception("Falha ao rodar dixon_coles_v1 -- pulando, os outros modelos continuam.")

    # Os 3 modelos de árvore compartilham o mesmo dataset "Feature Stacked"
    # (Requisito 5: 5-8 temporadas empilhadas das 6 ligas do Model
    # Benchmarking, 5 de elite europeias + Brasileirão) e o
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
