#!/usr/bin/env python3
"""Backtest out-of-sample dos 4 modelos do Model Benchmarking (Requisito 5)
+ comparação calibrado-vs-cru, Platt-vs-Isotonic, por liga e contra o
mercado (rodada de otimização).

Não roda no cron diário (`predict.yml`) -- é uma rotina de VALIDAÇÃO,
disparada manualmente (`python scripts/backtest_kelly.py`), no mesmo
espírito de `api/backtest-betting.js` (painel de modelos em produção), só
que em cima do dataset "Feature Stacked" das 6 ligas do Model
Benchmarking (5 de elite europeias + Brasileirão).

Passo a passo:
  1. Monta o dataset "Feature Stacked" (`dados_historicos.montar_dataset_ml_empilhado`)
     e divide cronologicamente em Train (60%) / Validation (20%) / Test (20%).
  2. Pros 3 modelos de árvore: grid search pequeno (profundidade x
     learning_rate) treinando só no Train e avaliando log-loss só no Val;
     a MESMA configuração vencedora do Train é reaproveitada pra ajustar
     as calibrações Platt E Isotonic (ver `calibracao.py`, as duas ficam
     lado a lado -- nenhuma substitui a outra) comparando a predição do
     modelo-só-Train contra o resultado real do Val -- depois refita a
     configuração vencedora em Train+Val (nunca olha o Test até aqui). O
     dixon_coles_v1 entra como baseline sem tuning (não tem hiperparâmetro
     de árvore), mas com o mesmo esquema de calibração.
  3. No Test Set (out-of-sample de verdade): busca a melhor odd real
     fechada (`odds_market`, snapshot pré-fechamento, exclui a média
     sintética) e simula banca jogo a jogo com Kelly fracionário 25%
     (capado em 25% da banca por aposta, não-composta), pra CRUA e pras 2
     CALIBRADAS de cada modelo -- 12 linhas no relatório principal (4
     modelos x {cru, calibrado_platt, calibrado_isotonic}). Só entra em
     campo quando o edge (prob. modelo - prob. implícita da odd) passa de
     2pp. Uma tabela secundária quebra o resultado CRU por liga (verifica
     se o desempenho é uniforme ou concentrado numa liga só).
  4. Bootstrap (2000 reamostragens) do ROI por aposta pra IC 95% -- só
     conta como "significativo" quando o limite INFERIOR do IC fica acima
     de zero (edge médio isolado não prova vantagem real, é o mesmo
     critério já usado em `api/backtest-betting.js`).
  5. Comparação de QUALIDADE de probabilidade (log-loss + Brier Score, não
     ROI) contra o mercado: odds da Pinnacle (referência padrão de "linha
     eficiente" em análise quantitativa de apostas) devigadas pelo método
     proporcional, nas MESMAS partidas de cada variante de modelo.

Variáveis de ambiente obrigatórias: SUPABASE_URL, SUPABASE_KEY.
"""

from __future__ import annotations

import logging
from itertools import product

import numpy as np
import pandas as pd

import calibracao
import dados_historicos
import modelos_ml
import rodar_predicoes as rp

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backtest_kelly")

FRACAO_KELLY = 0.25  # quarter-Kelly
TETO_FRACAO_BANCA = 0.25  # nunca aposta mais que 25% da banca numa única partida
EDGE_MINIMO = 0.02  # 2pp -- mesmo padrão de api/backtest-betting.js
N_REAMOSTRAGENS_BOOTSTRAP = 2000
SEED = 42


# =============================================================================
# Grid search pequeno (Val) + refit final (Train+Val) por modelo de árvore
# =============================================================================
# v2/v3/v4/v5/v3B/v6/v7/v8... (parâmetros de jogador/fadiga/disciplina/
# contexto de campeonato/XI titular/progresso da temporada/estatísticas de
# jogo, ver dados_historicos.FEATURES_V2..V8) reaproveitam a MESMA grade da
# v1 -- só a lista de features muda (modelos_ml.FEATURES_POR_MODELO), não
# faz sentido duplicar a grade de tuning.
# Profundidade deslocada pra baixo (era [4,6,8]/[3,4,6]/[15,31,63]) --
# achado real na curva de aprendizado (Treino/Validação/Teste): árvores
# rasas generalizam melhor em dados esportivos (ruído inerente alto),
# mesmo espírito de FRACAO_SUBSAMPLE/FRACAO_COLSAMPLE em modelos_ml.py.
# Continua 3x3 combinações -- não expande o grid search (rodaria 9x mais
# caro se subsample/colsample entrassem aqui em vez de fixos).
GRADE_HIPERPARAMETROS = {
    "catboost_v1": [{"depth": d, "learning_rate": lr} for d, lr in product([3, 4, 6], [0.03, 0.05, 0.1])],
    "xgboost_v1": [{"max_depth": d, "learning_rate": lr} for d, lr in product([2, 3, 4], [0.03, 0.08, 0.15])],
    "lightgbm_v1": [{"num_leaves": nl, "learning_rate": lr} for nl, lr in product([7, 15, 31], [0.05, 0.1, 0.2])],
    # MLP (v9+) não ganha grid search aqui -- mesmo critério de
    # `walkforward_cv_v9.py`, que também roda `treinar_mlp` só com os
    # defaults de `params.get(...)` (hidden_layer_sizes/learning_rate_init/
    # etc.), sem varrer hiperparâmetro. Config vazia == usa os defaults.
    "mlp_v9": [{}],
}
# Derivado de `modelos_ml.TREINADORES` em vez de uma lista fixa de sufixos
# -- a lista fixa (_v2.._v5/_v3b) já ficou pra trás uma vez (catboost_v6/v7
# nunca ganharam grade, `tunar_treinar_e_calibrar` ia estourar KeyError pra
# esses 2 -- só não quebrou o backtest inteiro porque o loop principal
# envolve cada modelo num try/except). Assim, qualquer versão nova
# registrada em TREINADORES (v9, v10...) herda a grade da v1 automaticamente,
# sem precisar lembrar de atualizar esta lista de novo. "mlp" entrou na
# lista de prefixos depois de uma rodada real confirmar `KeyError: 'mlp_v9'`
# (só "mlp_v9" tinha grade explícita acima; v10/v11 caíam fora) -- todo
# "mlp_*" agora herda a config vazia de "mlp_v9".
for _nome_modelo in modelos_ml.TREINADORES:
    if _nome_modelo in GRADE_HIPERPARAMETROS:
        continue
    for _prefixo in ("catboost", "xgboost", "lightgbm", "mlp"):
        if _nome_modelo.startswith(_prefixo):
            GRADE_HIPERPARAMETROS[_nome_modelo] = GRADE_HIPERPARAMETROS[f"{_prefixo}_v1" if _prefixo != "mlp" else "mlp_v9"]
            break
del _nome_modelo, _prefixo

# catboost_v1/xgboost_v1/lightgbm_v1 são só o algoritmo puro, sem feature
# set nenhum definido em `modelos_ml.FEATURES_POR_MODELO` -- servem de
# scaffolding pra qualquer config do Treino Customizado (`f"{algoritmo}_v1"`,
# ver comentário em `modelos_ml.TREINADORES`), que sempre sobrescreve as
# features na hora (`treinar_via_parametrico`). Não tem "feature set padrão"
# nenhum que fizesse sentido testar aqui como modelo standalone -- achado
# real (`KeyError: 'catboost_v1'` em `FEATURES_POR_MODELO`) numa rodada de
# produção. Pulados de propósito, não é bug.
MODELOS_SEM_FEATURE_SET_PADRAO = {"catboost_v1", "xgboost_v1", "lightgbm_v1"}

# =============================================================================
# Mercados cobertos por esta análise -- 1X2 (3 seleções), Over/Under 2.5
# gols (binário), Over/Under 9.5 escanteios (binário) e faixa de gols
# totais (4 classes, 0-1/2-3/4-6/7+) usam a MESMA infraestrutura de treino/
# predição por baixo (`modelos_ml.TREINADORES`, parametrizada por
# `coluna_alvo` -- ver `dados_historicos.montar_dataset_ml_empilhado`, que
# já traz as 4 colunas-alvo no mesmo dataset) e a mesma simulação de banca
# Kelly -- só muda o conjunto de seleções e onde ler o resultado real.
#
# Escanteios e faixa de gols NÃO têm nenhuma fonte de odds de mercado neste
# projeto (a OddsPapi só cobre 1X2 e over_under_2.5 em `odds_market`) -- pra
# esses 2 mercados, o relatório principal (`salvar_relatorio`) só traz
# qualidade intrínseca da probabilidade (log-loss/Brier/Acurácia sobre TODO
# o Test Set resolvido, não só as partidas com odd da Pinnacle -- ver
# `main()`), nunca ROI/Kelly/CLV (fica sempre com `n_apostas=0`, não é bug).
MERCADOS = {
    "1X2": {
        "coluna_alvo": "resultado",
        "codigo_por_selecao": {"home": dados_historicos.RESULTADO_HOME, "draw": dados_historicos.RESULTADO_DRAW, "away": dados_historicos.RESULTADO_AWAY},
    },
    "over_under_2.5": {
        "coluna_alvo": "resultado_over25",
        "codigo_por_selecao": {"under": dados_historicos.RESULTADO_UNDER25, "over": dados_historicos.RESULTADO_OVER25},
    },
    "corners_ou95": {
        "coluna_alvo": "resultado_corners_ou95",
        # Nomes de seleção com prefixo "corners_" de propósito -- têm que
        # bater com os campos "prob_corners_under"/"prob_corners_over" que
        # `modelos_ml.ROTULOS_SAIDA["resultado_corners_ou95"]` produz (ao
        # contrário de over_under_2.5, que usa "prob_under"/"prob_over" sem
        # prefixo). Já causou um KeyError real em produção (todo modelo de
        # árvore falhando com "prob_under" pra este mercado) quando as
        # chaves aqui estavam como "under"/"over" sem o prefixo.
        "codigo_por_selecao": {"corners_under": dados_historicos.RESULTADO_CORNERS_UNDER95, "corners_over": dados_historicos.RESULTADO_CORNERS_OVER95},
    },
    "corners_over_under_9.5": {
        "coluna_alvo": "resultado_corners_ou95",
        # Sem prefixo "corners_" de propósito -- diferente de "corners_ou95"
        # acima (mercado do classificador). Esta entrada é pro modelo misto:
        # `model_predictions.selection` grava "under"/"over" puro (mesmo
        # padrão de over_under_2.5), e a odd real em `odds_market` (mercado
        # "corners_over_under_full_time_9.5") também usa "under"/"over" sem
        # prefixo -- ver `_nome_mercado_odds`.
        "codigo_por_selecao": {"under": dados_historicos.RESULTADO_CORNERS_UNDER95, "over": dados_historicos.RESULTADO_CORNERS_OVER95},
    },
    "faixa_gols": {
        "coluna_alvo": "resultado_faixa_gols",
        "codigo_por_selecao": {
            "faixa_0_1": dados_historicos.RESULTADO_FAIXA_0_1,
            "faixa_2_3": dados_historicos.RESULTADO_FAIXA_2_3,
            "faixa_4_6": dados_historicos.RESULTADO_FAIXA_4_6,
            "faixa_7mais": dados_historicos.RESULTADO_FAIXA_7MAIS,
        },
    },
    "btts": {
        "coluna_alvo": "resultado_btts",
        "codigo_por_selecao": {"no": dados_historicos.RESULTADO_BTTS_NO, "yes": dados_historicos.RESULTADO_BTTS_YES},
    },
}

# Entradas SÓ pro modelo misto (ver MERCADOS_SOMENTE_MODELO_MISTO/
# MERCADOS_HIBRIDO_VALIDOS abaixo) -- nenhuma tem `coluna_alvo` real (não
# existe no dataset "Feature Stacked", `main()` nunca treina classificador
# nelas). `codigo_por_selecao` aqui só serve pra `carregar_melhores_odds_
# fechamento`/`_carregar_odds_pinnacle_brutas` saberem quais campos
# `odd_<selecao>` buscar -- os CÓDIGOS batem com `carregar_resultados_
# reais_hibrido` (0/1 pra over/under e escanteios, 0/1/2 pra handicap),
# nunca com os `RESULTADO_*` de mercados diferentes.
MERCADOS["over_under_1.5"] = {"coluna_alvo": None, "codigo_por_selecao": {"under": 0, "over": 1}}
MERCADOS["over_under_3.5"] = {"coluna_alvo": None, "codigo_por_selecao": {"under": 0, "over": 1}}
for _linha_corners in ("7.5", "8.5", "10", "10.5", "11", "11.5", "12", "12.5"):
    MERCADOS[f"corners_over_under_{_linha_corners}"] = {
        "coluna_alvo": None,
        "codigo_por_selecao": {"under": dados_historicos.RESULTADO_CORNERS_UNDER95, "over": dados_historicos.RESULTADO_CORNERS_OVER95},
    }
del _linha_corners
# Só as 4 linhas INTEIRAS com odd real de "european_handicap" no banco
# (ver _nome_mercado_odds) -- handicap_0.0 (sem "european_handicap_0" no
# banco) e as linhas de meio gol (sem equivalente de handicap europeu, só
# "asian_handicap", mercado diferente, fora do pedido) ficam de fora.
for _linha_handicap in ("-2.0", "-1.0", "1.0", "2.0"):
    MERCADOS[f"handicap_{_linha_handicap}"] = {"coluna_alvo": None, "codigo_por_selecao": {"home": 0, "away": 1, "push": 2}}
del _linha_handicap
# Dupla chance: os 3 códigos abaixo NUNCA são comparados por igualdade
# contra um `resultado_real` (ao contrário de todo outro mercado aqui) --
# 2 das 3 seleções sempre "vencem" simultaneamente (ex.: 1X e 12 vencem
# junto se o mandante ganha), então `montar_apostas`/`_metricas_
# probabilisticas` (que assumem exatamente 1 vencedor por partida) não
# servem pra esse mercado. Tem uma simulação PRÓPRIA (ver
# `montar_apostas_dupla_chance`/`_metricas_dupla_chance` no loop do modelo
# misto) -- os códigos aqui só existem pra reaproveitar `codigo_por_
# selecao.keys()` na busca de odds (`carregar_melhores_odds_fechamento`),
# nunca são lidos como valor.
MERCADOS["dupla_chance"] = {"coluna_alvo": None, "codigo_por_selecao": {"1X": 0, "X2": 1, "12": 2}}

# dixon_coles_v1 é um modelo Poisson de GOLS -- prevê faixa de gols pelo
# mesmo grid de placares usado pra 1X2/over_under_2.5 (ver
# `rp._prever_probs_dixon_coles`), mas não tem NENHUMA noção de escanteio.
# `main()` pula o baseline dixon_coles inteiro pros mercados aqui listados.
MERCADOS_SEM_DIXON_COLES = {"corners_ou95"}

# "corners_over_under_9.5" existe em MERCADOS só pra dar a
# `avaliar_modelo_misto_vs_mercado.py` acesso a `codigo_por_selecao` (usada
# via `bk.MERCADOS[...]`) e às funções genéricas de odds
# (`_carregar_odds_pinnacle_brutas`/`_devigar_odds_por_partida`) -- não é um
# mercado do pipeline de CLASSIFICADORES (`main()` abaixo). Diferente de
# "corners_ou95" (seleções com prefixo "corners_", combinando com
# `modelos_ml.ROTULOS_SAIDA`), esta entrada usa seleções sem prefixo
# ("under"/"over", combinando com `model_predictions` do modelo misto e com
# `odds_market`) -- se `main()` tentasse treinar um classificador com essa
# entrada, cairia no mesmo KeyError já documentado acima (prefixo
# incompatível com `ROTULOS_SAIDA`). `main()` pula todo mercado aqui listado.
MERCADOS_SOMENTE_MODELO_MISTO = {
    "corners_over_under_9.5", "corners_over_under_7.5", "corners_over_under_8.5", "corners_over_under_10",
    "corners_over_under_10.5", "corners_over_under_11", "corners_over_under_11.5", "corners_over_under_12",
    "corners_over_under_12.5", "over_under_1.5", "over_under_3.5", "handicap_-2.0", "handicap_-1.0",
    "handicap_1.0", "handicap_2.0", "dupla_chance",
}

# Modelo misto com ML (CatBoost Poisson + camada paramétrica de
# distribuicoes.py, treinado por treinar_modelo_hibrido.py) -- pré-treinado
# num split cronológico PRÓPRIO (60% treino / calibração / teste, ver
# FRACAO_TREINO em treinar_modelo_hibrido.py), diferente do split deste
# script. Nunca é retreinado aqui: suas previsões já persistidas em
# `model_predictions` são só LIDAS e avaliadas com a mesma simulação de
# apostas dos outros modelos (loop dedicado em `main()`, DEPOIS do `for
# mercado in MERCADOS` principal -- roda pros mercados em
# MERCADOS_HIBRIDO_VALIDOS, todos pulados inteiro pelo loop principal via
# MERCADOS_SOMENTE_MODELO_MISTO). `dupla_chance` fica FORA deste set --
# tem loop e simulação própria (ver comentário em MERCADOS["dupla_chance"]).
MODELOS_HIBRIDOS = ("hibrido_gols_v1", "hibrido_gols_xg_v1")
MERCADOS_HIBRIDO_VALIDOS = {
    "1X2", "over_under_2.5", "btts", "corners_over_under_9.5", "corners_over_under_7.5",
    "corners_over_under_8.5", "corners_over_under_10", "corners_over_under_10.5", "corners_over_under_11",
    "corners_over_under_11.5", "corners_over_under_12", "corners_over_under_12.5", "over_under_1.5",
    "over_under_3.5", "handicap_-2.0", "handicap_-1.0", "handicap_1.0", "handicap_2.0",
}


def _resultado_codigo_mercado(home_goals: int, away_goals: int, mercado: str) -> int:
    """Mesma ideia de `rp._resultado_codigo`, mas escolhe o espaço de
    códigos certo pro mercado -- usado no caminho do Dixon-Coles, que
    calcula resultado real a partir de `home_goals`/`away_goals` crus (ao
    contrário dos modelos de árvore, que já leem a coluna pronta do
    dataset "Feature Stacked"), e também no caminho do modelo misto pra
    1X2/over_under_2.5/btts (ver `carregar_resultados_reais_hibrido`) --
    escanteios usa uma fonte de dado diferente (`match_stats_fotmob`, sem
    placar), tratado à parte lá. Nunca chamada pra `corners_ou95` (ver
    `MERCADOS_SEM_DIXON_COLES`)."""
    if mercado == "1X2":
        return rp._resultado_codigo(home_goals, away_goals)
    if mercado == "faixa_gols":
        return dados_historicos.codigo_faixa_gols(home_goals + away_goals)
    if mercado == "btts":
        return dados_historicos.RESULTADO_BTTS_YES if (home_goals > 0 and away_goals > 0) else dados_historicos.RESULTADO_BTTS_NO
    return dados_historicos.RESULTADO_OVER25 if (home_goals + away_goals) > 2.5 else dados_historicos.RESULTADO_UNDER25


def _periodo_teste(df: pd.DataFrame) -> tuple[str | None, str | None]:
    """Data mínima/máxima de `match_date` num recorte do Test Set --
    "temporada ou período de testagem" pro relatório (pode ser o Test Set
    inteiro ou só as partidas de uma liga)."""
    if df.empty:
        return None, None
    return df["match_date"].min().date().isoformat(), df["match_date"].max().date().isoformat()


def _log_loss_multiclasse(y_true: np.ndarray, probs: np.ndarray, classes: np.ndarray) -> float:
    indice_da_classe = {int(rotulo): i for i, rotulo in enumerate(np.ravel(classes))}
    eps = 1e-15
    linhas = np.arange(len(y_true))
    colunas = np.array([indice_da_classe[int(y)] for y in y_true])
    p = np.clip(probs[linhas, colunas], eps, 1 - eps)
    return float(-np.mean(np.log(p)))


def tunar_treinar_e_calibrar(
    nome_modelo: str,
    train_df: pd.DataFrame,
    val_df: pd.DataFrame,
    train_mais_val_df: pd.DataFrame,
    test_df: pd.DataFrame,
    mercado: str = "1X2",
):
    """Treina cada combinação da grade só no Train, mede log-loss só no Val
    -- escolhe a melhor. Ajusta a calibração (Platt E Isotonic, lado a
    lado -- ver `calibracao.py`) comparando a predição do modelo-só-Train
    contra o resultado real do Val (nunca no Train, senão só mediria
    overfitting; nunca no Test, senão deixaria de ser out-of-sample). Por
    fim refita a config vencedora em Train+Val (usa toda informação
    anterior ao Test no modelo final, mas nunca olha o Test). `mercado`
    escolhe a coluna-alvo e o conjunto de seleções (`MERCADOS`) -- 1X2 e
    Over/Under 2.5 usam a mesma infraestrutura de treino, só muda o alvo.

    `test_df` só é usado pra capturar a CURVA DE APRENDIZADO (loss por
    iteração do boosting em Treino/Validação/Teste, pedido do usuário --
    ver `modelos_ml._montar_curva`) da config vencedora -- o modelo em si
    nunca vê o Test durante o tuning (a predição real do Test acontece
    depois, fora desta função, com `modelo_final`)."""
    coluna_alvo = MERCADOS[mercado]["coluna_alvo"]
    codigo_por_selecao = MERCADOS[mercado]["codigo_por_selecao"]
    treinar, prever = modelos_ml.TREINADORES[nome_modelo]
    # Mesmo filtro defensivo de `carregar_dataset` (treinar_modelo_custom.py)
    # e `treinar_modelo_hibrido.py` (PR #314) -- `FEATURES_NUMERICAS` (base
    # de FEATURES_V2..V11) referencia nomes de coluna de xG/xGOT do esquema
    # ANTIGO (`media_xg_5j_home` etc.) que `montar_dataset_ml_empilhado` não
    # gera mais (usa `_forma_por_mando_multi_janelas`, esquema novo) -- sem
    # esse filtro, todo modelo v9/v10/v11 quebra com KeyError puro aqui (achado
    # #15 em CONTEXTO_PROJETO.md). Mutar `FEATURES_POR_MODELO` em memória (não
    # só a variável local) porque o loop em `main()` também lê o dict global
    # direto pra montar a predição final do Test Set, depois desta função
    # retornar. Correção de fundo (portar pro esquema novo, restaurando xG em
    # todos os modelos de produção) é decisão separada, não tomada aqui.
    features_originais = modelos_ml.FEATURES_POR_MODELO[nome_modelo]
    colunas_dataset = set(train_df.columns)
    faltando = [f for f in features_originais if f not in colunas_dataset]
    if faltando:
        logger.warning("[%s] [%s] features ausentes no dataset (ignoradas): %s", mercado, nome_modelo, faltando)
        modelos_ml.FEATURES_POR_MODELO[nome_modelo] = [f for f in features_originais if f in colunas_dataset]
    features = modelos_ml.FEATURES_POR_MODELO[nome_modelo]
    melhor_params, melhor_log_loss = None, np.inf
    melhor_modelo_val, melhor_extra_val, melhor_curva = None, None, None

    for params in GRADE_HIPERPARAMETROS[nome_modelo]:
        modelo, extra, curva = treinar(params, train_df, coluna_alvo=coluna_alvo, features=features, val_df=val_df, test_df=test_df)
        probs_val, classes = prever(modelo, extra, val_df, features=features)
        log_loss = _log_loss_multiclasse(val_df[coluna_alvo].to_numpy(), probs_val, classes)
        logger.info("  %s [%s] params=%s -> log-loss(val)=%.4f", nome_modelo, mercado, params, log_loss)
        if log_loss < melhor_log_loss:
            melhor_log_loss, melhor_params = log_loss, params
            melhor_modelo_val, melhor_extra_val, melhor_curva = modelo, extra, curva

    probs_val_melhor, classes_val_melhor = prever(melhor_modelo_val, melhor_extra_val, val_df, features=features)
    preds_val = modelos_ml.empacotar_predicoes(
        val_df["match_id"].tolist(), probs_val_melhor, classes_val_melhor, coluna_alvo=coluna_alvo
    )
    resultados_val = dict(zip(val_df["match_id"], val_df[coluna_alvo]))
    coeficientes_por_metodo = {
        metodo: calibracao.ajustar_calibracao(preds_val, resultados_val, metodo=metodo, codigo_por_selecao=codigo_por_selecao)
        for metodo in calibracao.METODOS
    }

    logger.info(
        "%s [%s]: melhor config %s (log-loss val=%.4f) -- refitando em Train+Val.",
        nome_modelo,
        mercado,
        melhor_params,
        melhor_log_loss,
    )
    # Refit final em Train+Val não passa val_df/test_df -- é o modelo que
    # realmente prevê o Test Set depois (fora desta função), não precisa de
    # curva (a curva reportada é sempre a da config vencedora, treinada só
    # em Train, que é o cenário onde Val e Teste são de fato out-of-sample).
    modelo_final, extra_final, _ = treinar(melhor_params, train_mais_val_df, coluna_alvo=coluna_alvo, features=features)
    return modelo_final, extra_final, melhor_params, coeficientes_por_metodo, melhor_curva


# =============================================================================
# Baseline dixon_coles_v1 (sem tuning -- não tem profundidade/learning_rate)
# =============================================================================
def _estimar_forcas_com_decaimento(partidas: pd.DataFrame, data_referencia) -> dict[int, dict[str, float]]:
    janela = partidas.copy()
    dias = (data_referencia - janela["match_date"]).dt.total_seconds() / 86400
    janela["peso_decaimento"] = np.exp(-dados_historicos.XI_DECAIMENTO * dias.clip(lower=0))
    return dados_historicos.estimar_forcas_dixon_coles(janela)


def prever_dixon_coles_backtest(
    forcas: dict[int, dict[str, float]], partidas_alvo: pd.DataFrame
) -> dict[int, dict[str, float]]:
    """Aplica `forcas` já estimadas às partidas-alvo (Val ou Test) -- núcleo
    puro reaproveitado de `rodar_predicoes._prever_probs_dixon_coles`."""
    predicoes = {}
    for partida in partidas_alvo.itertuples():
        forca_casa = forcas.get(partida.home_team_id, rp.FORCA_PADRAO)
        forca_fora = forcas.get(partida.away_team_id, rp.FORCA_PADRAO)
        predicoes[partida.id] = rp._prever_probs_dixon_coles(forca_casa, forca_fora)
    return predicoes


def tunar_e_calibrar_dixon_coles(
    partidas_treino: pd.DataFrame,
    partidas_val: pd.DataFrame,
    partidas_treino_val: pd.DataFrame,
    data_referencia_teste,
    mercado: str = "1X2",
):
    """Mesmo esquema Train/Val/Train+Val dos modelos de árvore, adaptado pro
    Dixon-Coles: força estimada só no Train, calibração (Platt e Isotonic,
    lado a lado) ajustada comparando a previsão (com essa força) contra o
    resultado real do Val, versão final com força estimada em Train+Val
    (pra prever o Test). `mercado` escolhe o espaço de resultado/seleções
    (ver `MERCADOS`) -- a força Dixon-Coles em si não muda (é a mesma
    simulação de placares pros dois mercados, ver `rp._prever_probs_dixon_coles`)."""
    codigo_por_selecao = MERCADOS[mercado]["codigo_por_selecao"]
    forcas_treino = _estimar_forcas_com_decaimento(partidas_treino, partidas_val["match_date"].min())
    preds_val = prever_dixon_coles_backtest(forcas_treino, partidas_val)
    resultados_val = {
        int(p.id): _resultado_codigo_mercado(p.home_goals, p.away_goals, mercado) for p in partidas_val.itertuples()
    }
    coeficientes_por_metodo = {
        metodo: calibracao.ajustar_calibracao(preds_val, resultados_val, metodo=metodo, codigo_por_selecao=codigo_por_selecao)
        for metodo in calibracao.METODOS
    }

    forcas_finais = _estimar_forcas_com_decaimento(partidas_treino_val, data_referencia_teste)
    return forcas_finais, coeficientes_por_metodo


# =============================================================================
# Odds reais de fechamento pro Test Set
# =============================================================================
def _nome_mercado_odds(mercado: str) -> str:
    """`market` em `odds_market` usa `"1X2"` ou `"over_under_2.5"` -- mesmos
    literais das chaves de `MERCADOS`, então é uma identidade pra esses
    casos, mas fica isolado aqui pra não espalhar a suposição pelo arquivo
    todo. 3 famílias divergem de verdade: (1) escanteios: `odds_market`
    grava `"corners_over_under_full_time_{linha}"` (nome herdado da
    OddsPapi), string diferente da chave `"corners_over_under_{linha}"`
    que `MERCADOS`/`model_predictions` usam; (2) handicap: `model_
    predictions` grava `"handicap_{linha}"` (convenção do modelo misto,
    ver `distribuicoes.py` -- linha aplicada ao mandante, negativo =
    mandante em desvantagem), `odds_market` grava `"european_handicap_
    {linha_inteira}"` (SEM o `.0`, só existe pra linhas INTEIRAS -- meio
    gol não tem odd de handicap europeu no banco, fica sem tradução de
    propósito); (3) dupla chance: `"dupla_chance"` (modelo) vs.
    `"double_chance_full_time"` (odds) -- nome totalmente diferente.
    Validado empiricamente contra produção (ver PR que introduziu isso):
    filtrando só partidas onde o mandante é franco favorito no 1X2 (odd
    Pinnacle < 1.35), a odd do mandante cobrir `european_handicap_-1`
    fica em ~1.67 (linha quase justa) e piora em -2/-3/-4 -- mesmo sinal
    "negativo = mandante em desvantagem" que o modelo usa, confirma que
    NÃO precisa inverter o sinal na tradução."""
    if mercado.startswith("corners_over_under_"):
        return f"corners_over_under_full_time_{mercado.rsplit('_', 1)[-1]}"
    if mercado.startswith("handicap_"):
        linha = float(mercado.split("_", 1)[1])
        if linha == int(linha):
            return f"european_handicap_{int(linha)}"
        return mercado  # meio gol -- sem odd correspondente, fica sem tradução de propósito
    if mercado == "dupla_chance":
        return "double_chance_full_time"
    return mercado


def _traduzir_selecao_odds(mercado: str, selecao_odds: str) -> str:
    """Traduz a `selection` como gravada em `odds_market` de volta pro
    nome que `MERCADOS`/`model_predictions` usa -- só 2 casos divergem:
    (1) handicap inteiro: a Pinnacle chama o empate ajustado de "draw"
    (like um 1X2 normal -- 3ª seleção genuinamente precificada, NÃO
    devolução de aposta), o modelo misto chama a MESMA coisa de "push"
    (terminologia de handicap asiático, mas o evento por trás é
    idêntico); (2) dupla chance: `odds_market` grava minúsculo e com a
    letra invertida (`"1x"`/`"2x"`), o modelo grava `"1X"`/`"X2"` --
    `"12"` já bate igual nos dois lados."""
    if mercado.startswith("handicap_") and selecao_odds == "draw":
        return "push"
    if mercado == "dupla_chance":
        if selecao_odds == "1x":
            return "1X"
        if selecao_odds == "2x":
            return "X2"
    return selecao_odds


def _melhores_odds_fechamento_snapshot(
    supabase, match_ids: list[int], mercado: str, snapshot: str
) -> dict[int, dict[str, float]]:
    """Melhor odd real (exclui a média sintética `media_mercado`) por
    partida/seleção, num snapshot específico -- base compartilhada de
    `carregar_melhores_odds_fechamento` (com fallback entre snapshots)."""
    selecoes = list(MERCADOS[mercado]["codigo_por_selecao"].keys())
    campo_por_selecao = {selecao: f"odd_{selecao}" for selecao in selecoes}

    def factory(lote, inicio, fim):
        return (
            supabase.table("odds_market")
            .select("match_id, bookmaker, selection, odds")
            .in_("match_id", lote)
            .eq("market", _nome_mercado_odds(mercado))
            .eq("snapshot", snapshot)
            .neq("bookmaker", "media_mercado")
            .order("match_id")
            .range(inicio, fim)
        )

    # tamanho_lote reduzido (default 500 -> 100): a query já filtra por
    # market+snapshot+bookmaker além do .in_(match_id, lote), e com
    # odds_market na casa de milhões de linhas (ver CONTEXTO_PROJETO.md,
    # achado sobre views de cobertura de odds) um lote de ~280 ids já
    # estourava statement_timeout (57014) NA PRIMEIRA página -- não é o
    # caso clássico de OFFSET fundo (achado #21), é o filtro combinado
    # sendo caro num lote grande. Mesmo padrão de mitigação (encolher
    # tamanho_lote em vez de mudar a estratégia de paginação).
    linhas = dados_historicos._paginar_por_lotes_de_id(factory, match_ids, tamanho_lote=100)

    melhor: dict[int, dict[str, float]] = {}
    for linha in linhas:
        campo = campo_por_selecao.get(_traduzir_selecao_odds(mercado, linha["selection"]))
        if not campo:
            continue
        atual = melhor.setdefault(linha["match_id"], {c: 0.0 for c in campo_por_selecao.values()})
        if linha["odds"] and linha["odds"] > atual[campo]:
            atual[campo] = linha["odds"]
    return melhor


def carregar_melhores_odds_fechamento(supabase, match_ids: list[int], mercado: str = "1X2") -> dict[int, dict[str, float]]:
    """Melhor odd real (exclui a média sintética `media_mercado`) por
    partida/seleção -- `snapshot='pre_closing'` porque é a cobertura mais
    ampla de odds históricas reais neste banco pras 5 ligas europeias
    (achado documentado em CONTEXTO_PROJETO.md: `closing` só cobre 1
    temporada). ALTERAÇÃO (inclusão do Brasileirão): pro Brasileirão é o
    INVERSO -- o import de odds (football-data.co.uk, PR #151) só trouxe
    linhas de FECHAMENTO, `pre_closing` tem só 16 partidas em todo o banco
    (contra 2.616 em `closing`). Sem fallback, o Brasileirão nunca teria
    aposta simulada nenhuma no ROI/Kelly. Fallback aplicado por PARTIDA (não
    por liga inteira): só usa `closing` pras partidas que realmente não
    têm `pre_closing` -- não muda nada pras 5 ligas europeias, que já têm
    `pre_closing` de sobra. Investigado Betfair Exchange como alternativa
    de odd de abertura pro Brasileirão -- também só tem `closing` (774
    partidas, 0 em `pre_closing`), mesma limitação da fonte."""
    principal = _melhores_odds_fechamento_snapshot(supabase, match_ids, mercado, "pre_closing")
    faltando = [mid for mid in match_ids if mid not in principal]
    if faltando:
        fallback = _melhores_odds_fechamento_snapshot(supabase, faltando, mercado, "closing")
        principal.update(fallback)
    return principal


# =============================================================================
# Simulação de banca -- Kelly fracionário 25%, não-composta
# =============================================================================
def kelly_fracionario(prob: float, odd: float, fracao: float = FRACAO_KELLY, teto: float = TETO_FRACAO_BANCA) -> float:
    """Fração da banca a apostar: fração de Kelly completo (`f* = (bp - q) / b`,
    b = odd líquida), capada em `teto` -- mesmo padrão de
    `api/backtest-betting.js`."""
    b = odd - 1
    if b <= 0:
        return 0.0
    kelly_completo = (prob * (b + 1) - 1) / b
    if kelly_completo <= 0:
        return 0.0
    return min(kelly_completo * fracao, teto)


def montar_apostas(
    predicoes: dict[int, dict[str, float]],
    odds_por_partida: dict[int, dict[str, float]],
    resultados_reais: dict[int, int],
    liga_por_match_id: dict[int, str] | None = None,
    mercado: str = "1X2",
) -> list[dict]:
    """Só entra em campo quando o edge (prob. modelo - prob. implícita da
    odd) passa de `EDGE_MINIMO` -- mesmo filtro de `api/backtest-betting.js`.
    Cada aposta carrega a `liga` do jogo (quando informada) pra permitir
    quebrar o relatório por liga depois (`resumir_por_liga`). `mercado`
    escolhe o conjunto de seleções (ver `MERCADOS`) -- mesma mecânica pra
    1X2 (3 seleções) e Over/Under 2.5 (2 seleções)."""
    apostas = []
    for match_id, probs in predicoes.items():
        odds = odds_por_partida.get(match_id)
        resultado_real = resultados_reais.get(match_id)
        if not odds or resultado_real is None:
            continue
        for selecao, codigo_resultado in MERCADOS[mercado]["codigo_por_selecao"].items():
            campo_prob, campo_odd = f"prob_{selecao}", f"odd_{selecao}"
            odd = odds.get(campo_odd)
            if not odd:
                continue
            prob_modelo = probs[campo_prob]
            edge = prob_modelo - (1 / odd)
            if edge < EDGE_MINIMO:
                continue
            apostas.append(
                {
                    "match_id": match_id,
                    "selecao": selecao,
                    "prob_modelo": prob_modelo,
                    "odd": odd,
                    "acertou": resultado_real == codigo_resultado,
                    "liga": (liga_por_match_id or {}).get(match_id),
                }
            )
    return apostas


def simular_banca(apostas: list[dict]) -> list[float]:
    """Devolve o ROI de CADA aposta individual (lucro / stake, não o lucro
    bruto) -- não-composto: cada aposta usa fração de uma banca fixa de 1
    unidade, nunca reinveste lucro (mantém apostas comparáveis entre
    modelos/grupos mesmo com stakes de tamanho diferente)."""
    rois = []
    for aposta in apostas:
        stake = kelly_fracionario(aposta["prob_modelo"], aposta["odd"])
        if stake <= 0:
            continue
        lucro = stake * (aposta["odd"] - 1) if aposta["acertou"] else -stake
        rois.append(lucro / stake)
    return rois


def bootstrap_ic95_roi(
    rois: list[float], n_reamostragens: int = N_REAMOSTRAGENS_BOOTSTRAP, seed: int = SEED
) -> tuple[float, float, float]:
    """ROI médio + IC 95% via bootstrap (reamostragem com reposição das
    apostas individuais) -- edge médio isolado não prova vantagem real, o
    que importa é o limite INFERIOR do IC ficar acima de zero (mesmo
    critério de `api/backtest-betting.js`)."""
    if not rois:
        return 0.0, 0.0, 0.0
    rois_arr = np.array(rois)
    roi_medio = float(rois_arr.mean())
    rng = np.random.default_rng(seed)
    rois_bootstrap = [rng.choice(rois_arr, size=len(rois_arr), replace=True).mean() for _ in range(n_reamostragens)]
    limite_inferior, limite_superior = np.percentile(rois_bootstrap, [2.5, 97.5])
    return roi_medio, float(limite_inferior), float(limite_superior)


def resumir_backtest(nome_modelo: str, apostas: list[dict], melhor_params: dict | None) -> dict:
    rois = simular_banca(apostas)
    roi_medio, ic_inferior, ic_superior = bootstrap_ic95_roi(rois)
    return {
        "model_name": nome_modelo,
        "hiperparametros": melhor_params,
        "n_apostas": len(rois),
        "roi_medio": roi_medio,
        "roi_ic95_inferior": ic_inferior,
        "roi_ic95_superior": ic_superior,
        "significativo": ic_inferior > 0,
    }


def imprimir_relatorio(relatorio: list[dict]) -> None:
    relatorio_ordenado = sorted(relatorio, key=lambda r: r["roi_ic95_inferior"], reverse=True)
    logger.info("=" * 86)
    logger.info("BACKTEST OUT-OF-SAMPLE (Test Set) -- Kelly fracionário 25%%, edge mínimo %.0fpp", EDGE_MINIMO * 100)
    logger.info("=" * 86)
    for r in relatorio_ordenado:
        flag = "SIGNIFICATIVO (IC95% > 0)" if r["significativo"] else "sem evidência de edge positivo"
        logger.info(
            "%-32s | %4d apostas | ROI médio %+7.2f%% | IC95%% [%+7.2f%%, %+7.2f%%] | %s",
            r["model_name"],
            r["n_apostas"],
            r["roi_medio"] * 100,
            r["roi_ic95_inferior"] * 100,
            r["roi_ic95_superior"] * 100,
            flag,
        )
        if r["hiperparametros"]:
            logger.info("  hiperparâmetros escolhidos (log-loss no Val): %s", r["hiperparametros"])
    logger.info("=" * 86)


# =============================================================================
# Quebra por liga -- verifica se o desempenho é uniforme ou concentrado
# numa liga só (mesma disciplina de não confiar em edge isolado, aplicada
# por liga em vez de por seleção)
# =============================================================================
def resumir_por_liga(nome_modelo: str, apostas: list[dict]) -> list[dict]:
    apostas_por_liga: dict[str, list[dict]] = {}
    for aposta in apostas:
        apostas_por_liga.setdefault(aposta.get("liga") or "desconhecida", []).append(aposta)
    return [resumir_backtest(f"{nome_modelo} / {liga}", apostas_liga, None) for liga, apostas_liga in apostas_por_liga.items()]


def imprimir_relatorio_por_liga(relatorio_por_liga: list[dict]) -> None:
    if not relatorio_por_liga:
        return
    relatorio_ordenado = sorted(relatorio_por_liga, key=lambda r: r["roi_ic95_inferior"], reverse=True)
    logger.info("=" * 86)
    logger.info("BACKTEST POR LIGA (Test Set, predição CRUA) -- desempenho é uniforme ou concentrado numa liga só?")
    logger.info("=" * 86)
    for r in relatorio_ordenado:
        flag = "SIGNIFICATIVO (IC95% > 0)" if r["significativo"] else "sem evidência de edge positivo"
        logger.info(
            "%-40s | %4d apostas | ROI médio %+7.2f%% | IC95%% [%+7.2f%%, %+7.2f%%] | %s",
            r["model_name"],
            r["n_apostas"],
            r["roi_medio"] * 100,
            r["roi_ic95_inferior"] * 100,
            r["roi_ic95_superior"] * 100,
            flag,
        )
    logger.info("=" * 86)


def _arredondar_ou_none(valor, casas: int):
    return None if valor is None or (isinstance(valor, float) and np.isnan(valor)) else round(valor, casas)


def salvar_relatorio(supabase, relatorio: list[dict]) -> None:
    """Persiste o relatório principal em `model_benchmarking_backtest` --
    painel equivalente ao "Backtest de apostas simuladas (EV+)" já
    existente em Estatísticas dos Modelos, mas pros modelos do Model
    Benchmarking, agora com qualidade (log-loss/Brier/Acurácia vs.
    Pinnacle sem vig) E os 2 blocos de ROI (fechamento -- melhor odd real
    de qualquer bookmaker -- e abertura -- especificamente Pinnacle),
    por mercado (1X2, over_under_2.5). Cada rodada SUBSTITUI o resultado
    anterior por completo (delete-e-regrava, não upsert incremental) -- é
    sempre um resultado fim-a-fim contra o Test Set inteiro."""
    if not relatorio:
        return
    linhas = [
        {
            "model_name": r["model_name"],
            "mercado": r["mercado"],
            "periodo_inicio": r.get("periodo_inicio"),
            "periodo_fim": r.get("periodo_fim"),
            "treino_periodo_inicio": r.get("treino_periodo_inicio"),
            "treino_periodo_fim": r.get("treino_periodo_fim"),
            "validacao_periodo_inicio": r.get("validacao_periodo_inicio"),
            "validacao_periodo_fim": r.get("validacao_periodo_fim"),
            "n_apostas": r.get("n_apostas"),
            "roi_medio": _arredondar_ou_none(r.get("roi_medio"), 5),
            "roi_ic95_inferior": _arredondar_ou_none(r.get("roi_ic95_inferior"), 5),
            "roi_ic95_superior": _arredondar_ou_none(r.get("roi_ic95_superior"), 5),
            "significativo": r.get("significativo"),
            "n_apostas_abertura": r.get("n_apostas_abertura"),
            "roi_abertura_medio": _arredondar_ou_none(r.get("roi_abertura_medio"), 5),
            "roi_abertura_ic95_inferior": _arredondar_ou_none(r.get("roi_abertura_ic95_inferior"), 5),
            "roi_abertura_ic95_superior": _arredondar_ou_none(r.get("roi_abertura_ic95_superior"), 5),
            "significativo_abertura": r.get("significativo_abertura"),
            "log_loss": _arredondar_ou_none(r.get("log_loss"), 6),
            "brier": _arredondar_ou_none(r.get("brier"), 6),
            "accuracy": _arredondar_ou_none(r.get("accuracy"), 5),
            "n_amostras_qualidade": r.get("n_amostras_qualidade"),
            "hiperparametros": r.get("hiperparametros"),
        }
        for r in relatorio
    ]
    supabase.table("model_benchmarking_backtest").delete().neq("model_name", "").execute()
    supabase.table("model_benchmarking_backtest").upsert(linhas, on_conflict="model_name,mercado").execute()
    logger.info("Relatório principal salvo em model_benchmarking_backtest (%d linha(s)).", len(linhas))


def salvar_relatorio_por_liga(supabase, relatorio_por_liga: list[dict]) -> None:
    """Persiste a quebra por liga em `model_benchmarking_backtest_liga` --
    mesmas colunas do relatório principal (`salvar_relatorio`), mais
    `liga`."""
    if not relatorio_por_liga:
        return
    linhas = [
        {
            "model_name": r["model_name"],
            "liga": r["liga"],
            "mercado": r["mercado"],
            "periodo_inicio": r.get("periodo_inicio"),
            "periodo_fim": r.get("periodo_fim"),
            "n_apostas": r.get("n_apostas"),
            "roi_medio": _arredondar_ou_none(r.get("roi_medio"), 5),
            "roi_ic95_inferior": _arredondar_ou_none(r.get("roi_ic95_inferior"), 5),
            "roi_ic95_superior": _arredondar_ou_none(r.get("roi_ic95_superior"), 5),
            "significativo": r.get("significativo"),
            "n_apostas_abertura": r.get("n_apostas_abertura"),
            "roi_abertura_medio": _arredondar_ou_none(r.get("roi_abertura_medio"), 5),
            "roi_abertura_ic95_inferior": _arredondar_ou_none(r.get("roi_abertura_ic95_inferior"), 5),
            "roi_abertura_ic95_superior": _arredondar_ou_none(r.get("roi_abertura_ic95_superior"), 5),
            "significativo_abertura": r.get("significativo_abertura"),
            "log_loss": _arredondar_ou_none(r.get("log_loss"), 6),
            "brier": _arredondar_ou_none(r.get("brier"), 6),
            "accuracy": _arredondar_ou_none(r.get("accuracy"), 5),
            "n_amostras_qualidade": r.get("n_amostras_qualidade"),
        }
        for r in relatorio_por_liga
    ]
    supabase.table("model_benchmarking_backtest_liga").delete().neq("model_name", "").execute()
    supabase.table("model_benchmarking_backtest_liga").upsert(linhas, on_conflict="model_name,liga,mercado").execute()
    logger.info("Relatório por liga salvo em model_benchmarking_backtest_liga (%d linha(s)).", len(linhas))


def salvar_curva_aprendizado(supabase, curvas: list[dict]) -> None:
    """Persiste a curva de aprendizado (log-loss por iteração do boosting,
    Treino/Validação/Teste) da config vencedora de cada modelo/mercado --
    ver `modelos_ml._montar_curva` e `tunar_treinar_e_calibrar`. Todo
    catboost_*/xgboost_*/lightgbm_* em `modelos_ml.TREINADORES` tem curva
    (dixon_coles_v1 é Poisson puro, não boosting -- não entra aqui). `teste`
    fica sempre `None` pra lightgbm_* especificamente (ver comentário em
    `modelos_ml.treinar_lightgbm` sobre por que o Test não entra no
    `eval_set` de early stopping desse framework)."""
    if not curvas:
        return
    linhas = [
        {
            "model_name": c["model_name"],
            "mercado": c["mercado"],
            "iteracao": c["iteracao"],
            "treino": _arredondar_ou_none(c.get("treino"), 6),
            "validacao": _arredondar_ou_none(c.get("validacao"), 6),
            "teste": _arredondar_ou_none(c.get("teste"), 6),
        }
        for c in curvas
    ]
    tamanho_lote = rp.TAMANHO_LOTE_UPSERT_PREDICOES  # mesmo teto de statement_timeout do Postgres, ver rodar_predicoes.py
    supabase.table("model_benchmarking_learning_curve").delete().neq("model_name", "").execute()
    for lote in [linhas[i : i + tamanho_lote] for i in range(0, len(linhas), tamanho_lote)]:
        supabase.table("model_benchmarking_learning_curve").upsert(lote, on_conflict="model_name,mercado,iteracao").execute()
    logger.info("Curva de aprendizado salva em model_benchmarking_learning_curve (%d linha(s)).", len(linhas))


# =============================================================================
# Comparação com o mercado -- Pinnacle sem vig (log-loss / Brier Score)
# =============================================================================
def _carregar_odds_pinnacle_brutas(
    supabase, match_ids: list[int], mercado: str = "1X2", snapshot: str = "pre_closing", com_fallback_fechamento: bool = False
) -> dict[int, dict[str, float]]:
    """Odds cruas (não devigadas) da Pinnacle por partida/seleção --
    `snapshot='pre_closing'` é a odd de ABERTURA (cobertura mais ampla
    neste banco pras 5 ligas europeias, ver CONTEXTO_PROJETO.md);
    `snapshot='closing'` é a odd de FECHAMENTO de verdade (cobertura mais
    estreita nelas, só 1 temporada) -- usada pelo teste de Closing Line
    Value. Base compartilhada por `carregar_odds_pinnacle_devigadas`
    (qualidade) e `carregar_odds_pinnacle_abertura_bruta` (ROI vs.
    abertura).

    `com_fallback_fechamento=True` (só usado por `carregar_odds_pinnacle_
    devigadas`, NUNCA por `carregar_odds_pinnacle_abertura_bruta`/CLV --
    misturar fechamento no meio de um teste que compara abertura vs.
    fechamento anularia o próprio teste) completa com `closing` as
    partidas sem `pre_closing` -- pro Brasileirão é o INVERSO da Europa:
    só 16 partidas em `pre_closing` em todo o banco, contra 2.616 em
    `closing` (import de odds do football-data.co.uk só trouxe linhas de
    FECHAMENTO, ver CONTEXTO_PROJETO.md)."""
    selecoes = list(MERCADOS[mercado]["codigo_por_selecao"].keys())

    def buscar(ids: list[int], snap: str) -> dict[int, dict[str, float]]:
        def factory(lote, inicio, fim):
            return (
                supabase.table("odds_market")
                .select("match_id, selection, odds")
                .in_("match_id", lote)
                .eq("market", _nome_mercado_odds(mercado))
                .eq("snapshot", snap)
                .eq("bookmaker", "pinnacle")
                .order("match_id")
                .range(inicio, fim)
            )

        linhas = dados_historicos._paginar_por_lotes_de_id(factory, ids)
        odds_por_partida: dict[int, dict[str, float]] = {}
        for linha in linhas:
            selecao = _traduzir_selecao_odds(mercado, linha["selection"])
            if selecao not in selecoes:
                continue
            odds_por_partida.setdefault(linha["match_id"], {})[selecao] = linha["odds"]
        return odds_por_partida

    odds_por_partida = buscar(match_ids, snapshot)
    if com_fallback_fechamento and snapshot != "closing":
        faltando = [mid for mid in match_ids if mid not in odds_por_partida]
        if faltando:
            odds_por_partida.update(buscar(faltando, "closing"))
    return odds_por_partida


def _resolver_parametro_devig(g) -> float:
    """Bissecção genérica com expansão dinâmica de bracket: acha t>=0 tal
    que g(t)=0, dobrando o limite superior até trocar de sinal (favoritos
    muito curtos, tipo odd=1.01, precisam de t bem maior que um bracket fixo
    pequeno daria conta). Mesma lógica usada no lado JS
    (api/backtest-betting.js, resolverParametroDevig) e na migration SQL
    (supabase/migrations/20260811190000_devig_odds_ratio_logaritmico.sql,
    _devig_resolver_t) -- os três validados batendo entre si e com a
    planilha de referência (true_odds_calculator.xlsm) até a 10ª casa
    decimal."""
    g0 = g(0.0)
    if abs(g0) < 1e-14:
        return 0.0
    t_lo, g_lo = 0.0, g0
    t_hi, g_hi = 0.0, g0
    while g_hi > 0:
        t_hi = 1.0 if t_hi == 0 else t_hi * 2
        g_hi = g(t_hi)
        if t_hi > 1e15:
            break  # odds degeneradas (<=1) não deveriam chegar aqui
    for _ in range(200):
        t_mid = (t_lo + t_hi) / 2
        g_mid = g(t_mid)
        if abs(g_mid) < 1e-12:
            return t_mid
        if (g_mid > 0) == (g_lo > 0):
            t_lo, g_lo = t_mid, g_mid
        else:
            t_hi = t_mid
    return (t_lo + t_hi) / 2


def _devig_odds_ratio(odds: dict[str, float]) -> dict[str, float]:
    """Devig via Odds Ratio (Cheung): resolve c em
    sum(q_i / (c + q_i - c*q_i)) = 1, onde q_i = 1/odd_i (prob. implícita
    bruta)."""
    selecoes = list(odds.keys())
    q = {s: 1 / odds[s] for s in selecoes}

    def g(t: float) -> float:
        c = 1 + t
        return sum(q[s] / (c + q[s] - c * q[s]) for s in selecoes) - 1

    c = 1 + _resolver_parametro_devig(g)
    return {s: q[s] / (c + q[s] - c * q[s]) for s in selecoes}


def _devig_logaritmico(odds: dict[str, float]) -> dict[str, float]:
    """Devig via Logarithmic function (power): resolve c em
    sum(odd_i^c) = 1 e usa odd_i^c direto como probabilidade."""
    selecoes = list(odds.keys())

    def g(t: float) -> float:
        return sum(odds[s] ** (-t) for s in selecoes) - 1

    c = -_resolver_parametro_devig(g)
    return {s: odds[s] ** c for s in selecoes}


def _devigar_odds_por_partida(
    odds_por_partida: dict[int, dict[str, float]], mercado: str = "1X2", metodo: str = "odds_ratio"
) -> dict[int, dict[str, float]]:
    """Devig via Odds Ratio (Cheung, padrão) ou Logarithmic function
    (`metodo='logaritmico'`) -- ver `_devig_odds_ratio`/`_devig_logaritmico`.
    Substituiu a devigagem proporcional simples (repartia o overround
    igualmente entre as seleções, sem corrigir a distorção de margem entre
    favorito e zebra) -- mesma dupla de métodos usada em
    api/backtest-betting.js, api/model-stats.js e na view `v_market_edge`."""
    selecoes = list(MERCADOS[mercado]["codigo_por_selecao"].keys())
    devig_fn = _devig_logaritmico if metodo == "logaritmico" else _devig_odds_ratio
    devigadas: dict[int, dict[str, float]] = {}
    for match_id, odds in odds_por_partida.items():
        if not all(odds.get(s) for s in selecoes):
            continue
        probs = devig_fn({s: odds[s] for s in selecoes})
        devigadas[match_id] = {f"prob_{s}": probs[s] for s in selecoes}
    return devigadas


def carregar_odds_pinnacle_devigadas(supabase, match_ids: list[int], mercado: str = "1X2") -> dict[int, dict[str, float]]:
    """Probabilidade implícita da Pinnacle -- referência padrão de "linha
    eficiente" em análise quantitativa de apostas (menor margem/vig do
    mercado) -- devigada pelo método proporcional. Usa a odd de ABERTURA
    (`pre_closing`, maior cobertura na Europa) como preferência, com
    fallback pra `closing` partida a partida quando não existe abertura
    (`com_fallback_fechamento=True` -- caso do Brasileirão, ver docstring
    de `_carregar_odds_pinnacle_brutas`). Essa é só a referência de
    QUALIDADE de probabilidade (log-loss/Brier vs. mercado) -- o teste de
    ROI vs. abertura (`carregar_odds_pinnacle_abertura_bruta`) continua
    100% abertura, sem fallback, porque ali misturar fechamento
    invalidaria a comparação."""
    odds_por_partida = _carregar_odds_pinnacle_brutas(supabase, match_ids, mercado, snapshot="pre_closing", com_fallback_fechamento=True)
    return _devigar_odds_por_partida(odds_por_partida, mercado)


def carregar_odds_pinnacle_devigadas_fechamento(supabase, match_ids: list[int], mercado: str = "1X2") -> dict[int, dict[str, float]]:
    """Mesma ideia de `carregar_odds_pinnacle_devigadas`, mas SÓ closing
    de verdade, sem fallback nenhum pra abertura -- forma o par abertura/
    fechamento da referência de mercado (linha "mercado_pinnacle_sem_vig"
    vs. "mercado_pinnacle_sem_vig_fechamento"), espelhando o mesmo par já
    usado nas 2 colunas de ROI de cada modelo (fech./abert.). Cobertura
    bem mais estreita que a versão com fallback (só 1 temporada pras 5
    ligas europeias, ver CONTEXTO_PROJETO.md) -- pode ficar vazia pra
    partidas/mercados sem odd de fechamento capturada, o que é esperado,
    não um bug."""
    odds_por_partida = _carregar_odds_pinnacle_brutas(supabase, match_ids, mercado, snapshot="closing", com_fallback_fechamento=False)
    return _devigar_odds_por_partida(odds_por_partida, mercado)


def carregar_odds_pinnacle_abertura_bruta(supabase, match_ids: list[int], mercado: str = "1X2") -> dict[int, dict[str, float]]:
    """Odds cruas (com vig, NÃO devigadas) de ABERTURA da Pinnacle, no
    formato `odd_{selecao}` esperado por `montar_apostas` -- alimenta o
    teste de EV+/ROI especificamente contra a odd de abertura da Pinnacle
    (distinto do teste de ROI existente, que usa a MELHOR odd real entre
    todos os bookmakers -- ver `carregar_melhores_odds_fechamento`)."""
    odds_por_partida = _carregar_odds_pinnacle_brutas(supabase, match_ids, mercado, snapshot="pre_closing")
    return {match_id: {f"odd_{s}": odd for s, odd in odds.items()} for match_id, odds in odds_por_partida.items()}


# =============================================================================
# Modelo misto com ML (hibrido_gols_v1/hibrido_gols_xg_v1) -- lido de
# `model_predictions`, nunca retreinado aqui (ver MODELOS_HIBRIDOS acima).
# Mesma lógica de resultado real já validada em
# `avaliar_modelo_misto_vs_mercado.py` (ferramenta de análise só-leitura),
# reaproveitada aqui pro loop que PERSISTE em `model_benchmarking_backtest`.
# =============================================================================
def carregar_predicoes_hibrido(supabase, model_name: str, mercado: str) -> dict[int, dict[str, float]]:
    """`model_predictions` (match_id, selection, probability) já persistido
    por `treinar_modelo_hibrido.py`, no mesmo formato largo `prob_<selecao>`
    que `montar_apostas`/`_metricas_probabilisticas` esperam dos outros
    modelos (mesma ideia de `modelos_ml.empacotar_predicoes`, só que lido
    direto do banco em vez de vir de `prever()`)."""
    def factory(inicio, fim):
        return (
            supabase.table("model_predictions")
            .select("match_id, selection, probability")
            .eq("model_name", model_name)
            .eq("market", mercado)
            .order("match_id")
            .range(inicio, fim)
        )

    predicoes: dict[int, dict[str, float]] = {}
    for linha in dados_historicos._paginar(factory):
        predicoes.setdefault(linha["match_id"], {})[f"prob_{linha['selection']}"] = linha["probability"]
    return predicoes


def carregar_partidas_hibrido(supabase, match_ids: list[int]) -> dict[int, dict]:
    """`matches` (league_id, home_goals, away_goals, match_date), só
    finalizadas, pros match_ids que o modelo misto REALMENTE TEM em
    `model_predictions` -- nunca os do split deste script (Test Set dos
    modelos de árvore/Dixon-Coles), que não é garantidamente o mesmo split
    cronológico usado por `treinar_modelo_hibrido.py`. Serve pra
    liga/período em TODOS os 4 mercados (inclusive escanteios, que não usa
    `home_goals`/`away_goals` pro resultado real -- ver
    `carregar_resultados_reais_hibrido` -- mas ainda precisa da liga/data
    daqui pra quebra por liga do relatório)."""
    def factory(lote, inicio, fim):
        return (
            supabase.table("matches")
            .select("id, league_id, home_goals, away_goals, match_date")
            .in_("id", lote)
            .eq("status", "finished")
            .order("id")
            .range(inicio, fim)
        )

    partidas: dict[int, dict] = {}
    for linha in dados_historicos._paginar_por_lotes_de_id(factory, match_ids):
        partidas[linha["id"]] = linha
    return partidas


def carregar_resultados_reais_hibrido(supabase, match_ids: list[int], mercado: str) -> dict[int, int]:
    """Resultado real por mercado pros match_ids que o modelo misto tem em
    `model_predictions`:
    - `corners_over_under_{linha}` (qualquer linha, 7.5-12.5 inclusive
      `_9.5`): sem placar, resultado real é a soma casa+visitante de
      `match_stats_fotmob` (mesma fonte usada como alvo de treino, ver
      `dados_historicos._carregar_total_corners_por_partida`) -- MESMA
      lógica já usada em `avaliar_modelo_misto_vs_mercado.py`.
    - `over_under_{linha}` (1.5/3.5, qualquer linha futura): total de
      gols (`matches.home_goals+away_goals`) comparado com a linha
      extraída do nome do mercado.
    - `handicap_{linha}`: margem ajustada (home_goals-away_goals+linha),
      MESMA fórmula/convenção de `distribuicoes.py` (linha aplicada ao
      mandante) -- 0=home, 1=away, 2=push (empate ajustado).
    - Todo o resto (1X2/over_under_2.5/btts): `_resultado_codigo_mercado`,
      igual antes."""
    if mercado.startswith("corners_over_under_"):
        linha = float(mercado.rsplit("_", 1)[-1])
        df = dados_historicos._carregar_total_corners_por_partida(supabase, match_ids)
        resultados: dict[int, int] = {}
        for _, linha_df in df.iterrows():
            if pd.isna(linha_df["total_corners"]):
                continue
            resultados[int(linha_df["match_id"])] = (
                dados_historicos.RESULTADO_CORNERS_OVER95 if linha_df["total_corners"] > linha else dados_historicos.RESULTADO_CORNERS_UNDER95
            )
        return resultados

    partidas = carregar_partidas_hibrido(supabase, match_ids)
    partidas_validas = {mid: p for mid, p in partidas.items() if p["home_goals"] is not None and p["away_goals"] is not None}

    if mercado.startswith("over_under_"):
        linha = float(mercado.rsplit("_", 1)[-1])
        return {mid: (1 if (p["home_goals"] + p["away_goals"]) > linha else 0) for mid, p in partidas_validas.items()}

    if mercado.startswith("handicap_"):
        linha = float(mercado.split("_", 1)[1])
        resultados = {}
        for mid, p in partidas_validas.items():
            margem = (p["home_goals"] - p["away_goals"]) + linha
            resultados[mid] = 0 if margem > 0 else (2 if margem == 0 else 1)
        return resultados

    return {mid: _resultado_codigo_mercado(p["home_goals"], p["away_goals"], mercado) for mid, p in partidas_validas.items()}


# =============================================================================
# Dupla chance -- simulação PRÓPRIA, fora do padrão genérico usado pelo
# resto do arquivo (ver comentário em MERCADOS["dupla_chance"]): 2 das 3
# seleções ("1X"/"X2"/"12") sempre vencem juntas (ex.: mandante vence ->
# "1X" e "12" vencem, só "X2" perde), então não existe um único
# `resultado_real` comparável por igualdade contra `codigo_por_selecao`
# como em todo outro mercado -- `montar_apostas`/`_metricas_
# probabilisticas` assumem exatamente 1 vencedor e por isso NÃO servem
# aqui. Reaproveita `kelly_fracionario`/`bootstrap_ic95_roi`/
# `resumir_backtest` (esses sim genéricos: só operam sobre a lista de
# apostas já resolvida, não assumem nada sobre o mercado).
# =============================================================================
DUPLA_CHANCE_SELECOES = ("1X", "X2", "12")
_DUPLA_CHANCE_VENCEDORAS_POR_RESULTADO = {
    "home": {"1X", "12"},
    "draw": {"1X", "X2"},
    "away": {"X2", "12"},
}


def carregar_resultado_1x2_bruto_hibrido(supabase, match_ids: list[int]) -> dict[int, str]:
    """Resultado bruto ("home"/"draw"/"away") pros match_ids que o modelo
    misto tem em `model_predictions` -- base pra decidir quais das 3
    seleções de dupla chance venceram (ver `_DUPLA_CHANCE_VENCEDORAS_POR_
    RESULTADO`)."""
    partidas = carregar_partidas_hibrido(supabase, match_ids)
    resultados: dict[int, str] = {}
    for mid, p in partidas.items():
        if p["home_goals"] is None or p["away_goals"] is None:
            continue
        if p["home_goals"] > p["away_goals"]:
            resultados[mid] = "home"
        elif p["home_goals"] < p["away_goals"]:
            resultados[mid] = "away"
        else:
            resultados[mid] = "draw"
    return resultados


def carregar_referencia_dupla_chance_pinnacle(
    supabase, match_ids: list[int], carregar_devigada_1x2=carregar_odds_pinnacle_devigadas
) -> dict[int, dict[str, float]]:
    """Referência de mercado pra dupla chance -- NÃO devigada diretamente
    (as odds de "1X"/"X2"/"12" não formam uma partição do espaço de
    resultados -- 2 das 3 sempre "vencem" juntas -- então o devig
    tradicional, que assume as N odds cobrindo exatamente 100% de
    probabilidade, não se aplica aqui). Em vez disso, deriva da odd de
    1X2 devigada (que É uma partição válida: home/draw/away) por soma de
    pares -- P(1X)=P(home)+P(draw), P(X2)=P(draw)+P(away),
    P(12)=P(home)+P(away) -- mesma técnica de qualquer calculadora de
    dupla chance a partir de 1X2. `carregar_devigada_1x2` escolhe o par
    abertura (padrão, com fallback)/fechamento (`carregar_odds_pinnacle_
    devigadas_fechamento`)."""
    devigada_1x2 = carregar_devigada_1x2(supabase, match_ids, "1X2")
    referencia: dict[int, dict[str, float]] = {}
    for match_id, probs in devigada_1x2.items():
        referencia[match_id] = {
            "prob_1X": probs["prob_home"] + probs["prob_draw"],
            "prob_X2": probs["prob_draw"] + probs["prob_away"],
            "prob_12": probs["prob_home"] + probs["prob_away"],
        }
    return referencia


def montar_apostas_dupla_chance(
    predicoes: dict[int, dict[str, float]],
    odds_por_partida: dict[int, dict[str, float]],
    resultado_1x2_bruto: dict[int, str],
    liga_por_match_id: dict[int, str] | None = None,
) -> list[dict]:
    """Mesmo filtro de edge/Kelly de `montar_apostas`, mas `acertou` checa
    se a seleção está no conjunto de vencedoras do resultado bruto (ver
    módulo acima), não igualdade contra um código único."""
    apostas = []
    for match_id, probs in predicoes.items():
        odds = odds_por_partida.get(match_id)
        bruto = resultado_1x2_bruto.get(match_id)
        if not odds or bruto is None:
            continue
        vencedoras = _DUPLA_CHANCE_VENCEDORAS_POR_RESULTADO[bruto]
        for selecao in DUPLA_CHANCE_SELECOES:
            odd = odds.get(f"odd_{selecao}")
            if not odd:
                continue
            prob_modelo = probs[f"prob_{selecao}"]
            edge = prob_modelo - (1 / odd)
            if edge < EDGE_MINIMO:
                continue
            apostas.append({
                "match_id": match_id, "selecao": selecao, "prob_modelo": prob_modelo, "odd": odd,
                "acertou": selecao in vencedoras,
                "liga": (liga_por_match_id or {}).get(match_id),
            })
    return apostas


def _metricas_dupla_chance(
    predicoes: dict[int, dict[str, float]], resultado_1x2_bruto: dict[int, str], match_ids_validos: set[int]
) -> tuple[float, float, float, int]:
    """Log-loss/Brier/Acurácia AVALIADOS POR SELEÇÃO (cada uma das 3 é um
    evento binário independente -- "essa combinação aconteceu, sim ou
    não" -- log-loss binário padrão por seleção, sem competir entre si
    como em `_metricas_probabilisticas`). Acurácia = fração em que a
    seleção de MAIOR probabilidade do modelo está entre as vencedoras."""
    perdas, briers, acertos = [], [], []
    for match_id in match_ids_validos:
        probs = predicoes.get(match_id)
        bruto = resultado_1x2_bruto.get(match_id)
        if probs is None or bruto is None:
            continue
        vencedoras = _DUPLA_CHANCE_VENCEDORAS_POR_RESULTADO[bruto]
        for selecao in DUPLA_CHANCE_SELECOES:
            p = max(min(probs[f"prob_{selecao}"], 1 - 1e-15), 1e-15)
            y = 1.0 if selecao in vencedoras else 0.0
            perdas.append(-(y * np.log(p) + (1 - y) * np.log(1 - p)))
            briers.append((p - y) ** 2)
        selecao_prevista = max(DUPLA_CHANCE_SELECOES, key=lambda s: probs[f"prob_{s}"])
        acertos.append(1.0 if selecao_prevista in vencedoras else 0.0)
    if not acertos:
        return float("nan"), float("nan"), float("nan"), 0
    return float(np.mean(perdas)), float(np.mean(briers)), float(np.mean(acertos)), len(acertos)


def _metricas_probabilisticas(
    predicoes: dict[int, dict[str, float]], resultados_reais: dict[int, int], match_ids_validos: set[int], mercado: str = "1X2"
) -> tuple[float, float, float, int]:
    """Log-loss, Brier Score e Acurácia (multiclasse ou binário conforme
    `mercado`), restritos aos `match_ids_validos` -- pra comparar modelo e
    mercado exatamente nas MESMAS partidas (só as que têm odd da Pinnacle
    disponível). Acurácia = fração em que a seleção de maior probabilidade
    do modelo é a que realmente aconteceu."""
    codigo_para_campo = {codigo: f"prob_{selecao}" for selecao, codigo in MERCADOS[mercado]["codigo_por_selecao"].items()}
    perdas_log, briers, acertos = [], [], []
    for match_id in match_ids_validos:
        probs = predicoes.get(match_id)
        resultado_real = resultados_reais.get(match_id)
        if probs is None or resultado_real is None:
            continue
        p_real = max(probs[codigo_para_campo[resultado_real]], 1e-15)
        perdas_log.append(-np.log(p_real))
        briers.append(
            sum((probs[campo] - (1.0 if codigo == resultado_real else 0.0)) ** 2 for codigo, campo in codigo_para_campo.items())
        )
        codigo_previsto = max(codigo_para_campo, key=lambda codigo: probs[codigo_para_campo[codigo]])
        acertos.append(1.0 if codigo_previsto == resultado_real else 0.0)
    if not perdas_log:
        return float("nan"), float("nan"), float("nan"), 0
    return float(np.mean(perdas_log)), float(np.mean(briers)), float(np.mean(acertos)), len(perdas_log)


def imprimir_relatorio_qualidade(linhas_qualidade: list[dict]) -> None:
    linhas_ordenadas = sorted(linhas_qualidade, key=lambda r: r["log_loss"])
    logger.info("=" * 86)
    logger.info("QUALIDADE DE PROBABILIDADE NO TEST SET -- modelo vs. mercado (Pinnacle sem vig)")
    logger.info("=" * 86)
    for r in linhas_ordenadas:
        marcador = " <-- mercado" if r["nome"].startswith("mercado") else ""
        logger.info(
            "%-34s | %4d partidas | log-loss %.4f | Brier %.4f | Acurácia %.1f%%%s",
            r["nome"],
            r["n"],
            r["log_loss"],
            r["brier"],
            r["accuracy"] * 100,
            marcador,
        )
    logger.info("=" * 86)


# =============================================================================
# Comparação PAREADA com o mercado -- superioridade / inferioridade /
# não-inferioridade (bootstrap da DIFERENÇA partida a partida, não de cada
# lado isolado -- cancela ruído comum, mesmo espírito do teste de
# Diebold-Mariano pra comparar previsões)
# =============================================================================
MARGEM_NAO_INFERIORIDADE_LOG_LOSS = 0.01  # ajustável -- não há um valor "certo" objetivo sem uma referência de EV
AMOSTRA_MINIMA_CLV = 30  # pares (partida, seleção) mínimos pra confiar na correlação de Closing Line Value


def _perdas_por_partida(
    predicoes: dict[int, dict[str, float]], resultados_reais: dict[int, int], match_ids_ordenados: list[int]
) -> tuple[np.ndarray, np.ndarray]:
    """Log-loss e Brier POR PARTIDA (não a média) -- necessário pro
    bootstrap pareado, que precisa subtrair perda-a-perda na MESMA ordem
    de partida entre modelo e mercado."""
    codigo_para_campo = {
        dados_historicos.RESULTADO_HOME: "prob_home",
        dados_historicos.RESULTADO_DRAW: "prob_draw",
        dados_historicos.RESULTADO_AWAY: "prob_away",
    }
    perdas_log, briers = [], []
    for match_id in match_ids_ordenados:
        probs = predicoes[match_id]
        resultado_real = resultados_reais[match_id]
        p_real = max(probs[codigo_para_campo[resultado_real]], 1e-15)
        perdas_log.append(-np.log(p_real))
        briers.append(
            sum((probs[campo] - (1.0 if codigo == resultado_real else 0.0)) ** 2 for codigo, campo in codigo_para_campo.items())
        )
    return np.array(perdas_log), np.array(briers)


def comparar_pareado_com_mercado(
    perdas_modelo: np.ndarray,
    perdas_mercado: np.ndarray,
    margem: float = MARGEM_NAO_INFERIORIDADE_LOG_LOSS,
    n_reamostragens: int = N_REAMOSTRAGENS_BOOTSTRAP,
    seed: int = SEED,
) -> dict:
    """Bootstrap PAREADO da diferença (perda do modelo - perda do mercado)
    partida a partida -- a MESMA reamostra de partidas é usada nos dois
    lados a cada iteração, então ruído compartilhado (jogos "difíceis" pros
    dois) se cancela, deixando o teste mais sensível que dois IC
    independentes comparados visualmente.

    - `modelo_supera_mercado`: limite SUPERIOR do IC < 0 (modelo
      confiavelmente MELHOR, perda menor).
    - `mercado_supera_modelo`: limite INFERIOR do IC > 0 (mercado
      confiavelmente melhor).
    - `nao_inferior`: limite SUPERIOR do IC < `margem` -- não-inferioridade
      de verdade (ensaio de não-inferioridade: define uma margem de
      tolerância a priori e testa se o pior cenário dentro do IC não
      ultrapassa ela), mais forte que só "não deu diferença significativa".
    """
    diffs = perdas_modelo - perdas_mercado
    rng = np.random.default_rng(seed)
    n = len(diffs)
    diffs_bootstrap = [rng.choice(diffs, size=n, replace=True).mean() for _ in range(n_reamostragens)]
    ic_inferior, ic_superior = np.percentile(diffs_bootstrap, [2.5, 97.5])
    return {
        "n": n,
        "diferenca_media": float(diffs.mean()),
        "ic95_inferior": float(ic_inferior),
        "ic95_superior": float(ic_superior),
        "modelo_supera_mercado": bool(ic_superior < 0),
        "mercado_supera_modelo": bool(ic_inferior > 0),
        "nao_inferior": bool(ic_superior < margem),
    }


def imprimir_relatorio_pareado(linhas_pareadas: list[dict], margem: float = MARGEM_NAO_INFERIORIDADE_LOG_LOSS) -> None:
    if not linhas_pareadas:
        return
    linhas_ordenadas = sorted(linhas_pareadas, key=lambda r: r["comparacao"]["diferenca_media"])
    logger.info("=" * 86)
    logger.info("COMPARAÇÃO PAREADA COM O MERCADO (log-loss, bootstrap) -- margem de não-inferioridade: %.4f", margem)
    logger.info("=" * 86)
    for r in linhas_ordenadas:
        c = r["comparacao"]
        if c["modelo_supera_mercado"]:
            veredito = "MODELO SUPERIOR (IC95% < 0)"
        elif c["mercado_supera_modelo"]:
            veredito = "MERCADO SUPERIOR (IC95% > 0)"
        elif c["nao_inferior"]:
            veredito = "NÃO-INFERIOR (limite sup. do IC < margem)"
        else:
            veredito = "INCONCLUSIVO (IC cruza a margem)"
        logger.info(
            "%-34s | %4d partidas | diferença %+7.4f | IC95%% [%+7.4f, %+7.4f] | %s",
            r["nome"], c["n"], c["diferenca_media"], c["ic95_inferior"], c["ic95_superior"], veredito,
        )
    logger.info("=" * 86)


# =============================================================================
# Closing Line Value -- o edge do modelo (contra a odd de ABERTURA) prevê
# pra que lado a linha se move até o FECHAMENTO? Já testado uma vez neste
# projeto pro Dixon-Coles de produção e deu negativo (ver
# CONTEXTO_PROJETO.md) -- aqui mede de novo pro pipeline novo.
# =============================================================================
def _carregar_pinnacle_devigada_por_snapshot(supabase, match_ids: list[int], snapshot: str) -> dict[int, dict[str, float]]:
    def factory(lote, inicio, fim):
        return (
            supabase.table("odds_market")
            .select("match_id, selection, odds")
            .in_("match_id", lote)
            .eq("market", "1X2")
            .eq("bookmaker", "pinnacle")
            .eq("snapshot", snapshot)
            .order("match_id")
            .range(inicio, fim)
        )

    linhas = dados_historicos._paginar_por_lotes_de_id(factory, match_ids)
    odds_por_partida: dict[int, dict[str, float]] = {}
    for linha in linhas:
        odds_por_partida.setdefault(linha["match_id"], {})[linha["selection"]] = linha["odds"]

    devigadas: dict[int, dict[str, float]] = {}
    for match_id, odds in odds_por_partida.items():
        if not all(odds.get(s) for s in ("home", "draw", "away")):
            continue
        probs = _devig_odds_ratio({s: odds[s] for s in ("home", "draw", "away")})
        devigadas[match_id] = {f"prob_{s}": probs[s] for s in ("home", "draw", "away")}
    return devigadas


def testar_closing_line_value(
    supabase,
    match_ids: list[int],
    predicoes_por_variante: dict[str, dict[int, dict[str, float]]],
    n_reamostragens: int = N_REAMOSTRAGENS_BOOTSTRAP,
    seed: int = SEED,
) -> dict[str, dict]:
    """Pra cada (partida, seleção): `edge = prob_modelo - prob_abertura` e
    `movimento = prob_fechamento - prob_abertura` (as duas devigadas).
    Correlação positiva e significativa entre os dois sugeriria que o
    modelo "acerta" a direção que a linha vai se mover -- ou seja, apostar
    na odd de abertura, antes do mercado incorporar a mesma informação,
    teria valor esperado positivo. Bootstrap (reamostragem dos pares) pra
    IC 95% da correlação."""
    abertura = _carregar_pinnacle_devigada_por_snapshot(supabase, match_ids, "pre_closing")
    fechamento = _carregar_pinnacle_devigada_por_snapshot(supabase, match_ids, "closing")
    match_ids_com_movimento = sorted(set(abertura) & set(fechamento))
    if not match_ids_com_movimento:
        return {}

    resultados: dict[str, dict] = {}
    for nome_variante, predicoes in predicoes_por_variante.items():
        edges, movimentos = [], []
        for match_id in match_ids_com_movimento:
            probs_modelo = predicoes.get(match_id)
            if probs_modelo is None:
                continue
            for selecao in ("home", "draw", "away"):
                campo = f"prob_{selecao}"
                edges.append(probs_modelo[campo] - abertura[match_id][campo])
                movimentos.append(fechamento[match_id][campo] - abertura[match_id][campo])

        if len(edges) < AMOSTRA_MINIMA_CLV:
            continue
        edges_arr, movimentos_arr = np.array(edges), np.array(movimentos)
        if edges_arr.std() == 0 or movimentos_arr.std() == 0:
            continue
        correlacao = float(np.corrcoef(edges_arr, movimentos_arr)[0, 1])

        rng = np.random.default_rng(seed)
        n = len(edges_arr)
        correlacoes_bootstrap = []
        for _ in range(n_reamostragens):
            idx = rng.integers(0, n, n)
            amostra_e, amostra_m = edges_arr[idx], movimentos_arr[idx]
            if amostra_e.std() == 0 or amostra_m.std() == 0:
                continue
            correlacoes_bootstrap.append(np.corrcoef(amostra_e, amostra_m)[0, 1])
        if not correlacoes_bootstrap:
            continue
        ic_inferior, ic_superior = np.percentile(correlacoes_bootstrap, [2.5, 97.5])
        resultados[nome_variante] = {
            "n_pares": n,
            "correlacao": correlacao,
            "ic95_inferior": float(ic_inferior),
            "ic95_superior": float(ic_superior),
            "significativo": bool(ic_inferior > 0),
        }
    return resultados


def imprimir_relatorio_clv(resultados_clv: dict[str, dict]) -> None:
    if not resultados_clv:
        logger.info("Sem dado suficiente pra testar Closing Line Value (poucos pares partida+seleção com abertura E fechamento da Pinnacle).")
        return
    logger.info("=" * 86)
    logger.info("CLOSING LINE VALUE -- o edge do modelo (vs. odd de ABERTURA) antecipa o movimento da linha até o FECHAMENTO?")
    logger.info("=" * 86)
    for nome, r in sorted(resultados_clv.items(), key=lambda kv: kv[1]["ic95_inferior"], reverse=True):
        flag = "SIGNIFICATIVO (antecipa movimento)" if r["significativo"] else "sem evidência de antecipação"
        logger.info(
            "%-34s | %4d pares | correlação %+.3f | IC95%% [%+.3f, %+.3f] | %s",
            nome, r["n_pares"], r["correlacao"], r["ic95_inferior"], r["ic95_superior"], flag,
        )
    logger.info("=" * 86)


# =============================================================================
# Orquestração
# =============================================================================
def main() -> None:
    supabase = rp.get_supabase_client()

    # Nome de liga por league_id -- usado só pelo loop do modelo misto (ver
    # MODELOS_HIBRIDOS/MERCADOS_HIBRIDO_VALIDOS mais abaixo), que busca as
    # próprias partidas direto do banco (nunca do dataset ML, que só cobre
    # o split cronológico deste script) e por isso precisa de um jeito
    # independente de nomear a liga na quebra por liga do relatório.
    nomes_liga = {linha["id"]: linha["name"] for linha in (supabase.table("leagues").select("id, name").execute().data or [])}

    logger.info("Montando dataset 'Feature Stacked' (5-8 temporadas, ligas de elite)...")
    dataset = dados_historicos.montar_dataset_ml_empilhado(supabase, anos_por_liga=rp.JANELA_ML_ANOS)
    if dataset.empty:
        logger.error("Dataset vazio -- não há como rodar o backtest.")
        return

    train_df, val_df, test_df = dados_historicos.split_cronologico(dataset)
    if val_df.empty or test_df.empty:
        logger.error("Validação ou teste ficaram vazios -- dataset pequeno demais pro split 60/20/20.")
        return
    train_mais_val_df = pd.concat([train_df, val_df], ignore_index=True)

    match_ids_teste = test_df["match_id"].astype(int).tolist()
    liga_por_match_id = dict(zip(test_df["match_id"], test_df["liga"]))
    periodo_inicio_geral, periodo_fim_geral = _periodo_teste(test_df)
    periodo_por_liga = {liga: _periodo_teste(sub) for liga, sub in test_df.groupby("liga")}

    match_ids_treino = train_df["match_id"].astype(int).tolist()
    match_ids_val = val_df["match_id"].astype(int).tolist()
    match_ids_treino_val = train_mais_val_df["match_id"].astype(int).tolist()
    partidas_treino = dados_historicos.carregar_partidas_por_id(supabase, match_ids_treino)
    partidas_val = dados_historicos.carregar_partidas_por_id(supabase, match_ids_val)
    partidas_treino_val = dados_historicos.carregar_partidas_por_id(supabase, match_ids_treino_val)
    partidas_teste = dados_historicos.carregar_partidas_por_id(supabase, match_ids_teste)

    relatorio: list[dict] = []
    relatorio_por_liga: list[dict] = []
    curvas_aprendizado: list[dict] = []
    # diagnósticos extra (comparação pareada + Closing Line Value) ainda só
    # cobrem 1X2 -- guardamos as predições dessa rodada pra rodar essas duas
    # seções depois do loop, sem refazer o tuning.
    predicoes_1x2_diagnostico: dict[str, dict[int, dict[str, float]]] = {}
    resultados_reais_1x2: dict[int, int] = {}
    match_ids_validos_1x2: set[int] = set()

    for mercado in MERCADOS:
        if mercado in MERCADOS_SOMENTE_MODELO_MISTO:
            continue
        logger.info("=" * 86)
        logger.info("MERCADO: %s", mercado)
        logger.info("=" * 86)

        coluna_alvo = MERCADOS[mercado]["coluna_alvo"]
        selecoes_mercado = tuple(MERCADOS[mercado]["codigo_por_selecao"].keys())

        # Linhas sem alvo neste mercado específico (ex.: escanteio ainda não
        # ingerido pra aquela partida) ficam de fora SÓ desta iteração -- os
        # outros mercados usam o dataset inteiro normalmente (ver
        # `dados_historicos._carregar_total_corners_por_partida`).
        train_df_m = train_df.dropna(subset=[coluna_alvo]).reset_index(drop=True)
        val_df_m = val_df.dropna(subset=[coluna_alvo]).reset_index(drop=True)
        train_mais_val_df_m = train_mais_val_df.dropna(subset=[coluna_alvo]).reset_index(drop=True)
        test_df_m = test_df.dropna(subset=[coluna_alvo]).reset_index(drop=True)
        match_ids_teste_m = test_df_m["match_id"].astype(int).tolist()
        periodo_inicio_mercado, periodo_fim_mercado = _periodo_teste(test_df_m)
        periodo_por_liga_mercado = {liga: _periodo_teste(sub) for liga, sub in test_df_m.groupby("liga")}
        # Períodos de treino/validação (painel de informação do modelo no
        # frontend) -- mesmo split cronológico, só reporta as datas.
        treino_periodo_inicio_mercado, treino_periodo_fim_mercado = _periodo_teste(train_df_m)
        validacao_periodo_inicio_mercado, validacao_periodo_fim_mercado = _periodo_teste(val_df_m)

        logger.info("[%s] Buscando odds reais pro Test Set (%d partidas out-of-sample)...", mercado, len(match_ids_teste_m))
        odds_fechamento = carregar_melhores_odds_fechamento(supabase, match_ids_teste_m, mercado)
        odds_abertura = carregar_odds_pinnacle_abertura_bruta(supabase, match_ids_teste_m, mercado)
        pinnacle_devigada = carregar_odds_pinnacle_devigadas(supabase, match_ids_teste_m, mercado)
        # Par abertura/fechamento da referência de mercado -- mesma ideia
        # das 2 colunas de ROI (fech./abert.) que cada modelo já tem.
        # `pinnacle_devigada` (acima) é abertura COM fallback pra
        # fechamento (mistura as duas, maximiza cobertura); esta aqui é
        # SÓ fechamento, sem fallback nenhum -- pedido explícito do
        # usuário pra poder comparar a qualidade da Pinnacle nos dois
        # momentos separadamente, não só o ROI.
        pinnacle_devigada_fechamento = carregar_odds_pinnacle_devigadas_fechamento(supabase, match_ids_teste_m, mercado)
        resultados_reais = {int(mid): int(r) for mid, r in zip(test_df_m["match_id"], test_df_m[coluna_alvo])}
        # Qualidade INTRÍNSECA do modelo (log-loss/Brier/Acurácia) usa TODO
        # o Test Set resolvido deste mercado -- não depende de odd nenhuma.
        # A comparação CONTRA o mercado (linha "mercado_pinnacle_sem_vig" +
        # diagnósticos pareados) é que precisa da interseção com a Pinnacle,
        # e fica vazia pra escanteios/faixa de gols (sem fonte de odds) sem
        # derrubar a qualidade intrínseca -- é assim que os 2 mercados sem
        # odds ainda aparecem no relatório principal.
        match_ids_com_resultado = set(resultados_reais.keys())
        match_ids_validos_qualidade = set(pinnacle_devigada.keys()) & match_ids_com_resultado
        match_ids_validos_qualidade_fechamento = set(pinnacle_devigada_fechamento.keys()) & match_ids_com_resultado
        if not match_ids_validos_qualidade:
            logger.warning(
                "[%s] Nenhuma odd da Pinnacle encontrada pro Test Set -- comparação com o mercado ficará vazia "
                "(a qualidade intrínseca de cada modelo continua sendo reportada normalmente).",
                mercado,
            )

        todas_as_predicoes_teste: dict[str, dict[int, dict[str, float]]] = {}

        def _registrar(nome_variante: str, preds: dict[int, dict[str, float]], melhor_params: dict | None, por_liga: bool) -> None:
            todas_as_predicoes_teste[nome_variante] = preds
            apostas_fechamento = montar_apostas(preds, odds_fechamento, resultados_reais, liga_por_match_id, mercado)
            apostas_abertura = montar_apostas(preds, odds_abertura, resultados_reais, liga_por_match_id, mercado)
            resumo_f = resumir_backtest(nome_variante, apostas_fechamento, melhor_params)
            resumo_a = resumir_backtest(nome_variante, apostas_abertura, melhor_params)
            log_loss, brier, accuracy, n_qualidade = _metricas_probabilisticas(
                preds, resultados_reais, match_ids_com_resultado, mercado
            )
            relatorio.append(
                {
                    "model_name": nome_variante,
                    "mercado": mercado,
                    "periodo_inicio": periodo_inicio_mercado,
                    "periodo_fim": periodo_fim_mercado,
                    "treino_periodo_inicio": treino_periodo_inicio_mercado,
                    "treino_periodo_fim": treino_periodo_fim_mercado,
                    "validacao_periodo_inicio": validacao_periodo_inicio_mercado,
                    "validacao_periodo_fim": validacao_periodo_fim_mercado,
                    "hiperparametros": melhor_params,
                    "n_apostas": resumo_f["n_apostas"],
                    "roi_medio": resumo_f["roi_medio"],
                    "roi_ic95_inferior": resumo_f["roi_ic95_inferior"],
                    "roi_ic95_superior": resumo_f["roi_ic95_superior"],
                    "significativo": resumo_f["significativo"],
                    "n_apostas_abertura": resumo_a["n_apostas"],
                    "roi_abertura_medio": resumo_a["roi_medio"],
                    "roi_abertura_ic95_inferior": resumo_a["roi_ic95_inferior"],
                    "roi_abertura_ic95_superior": resumo_a["roi_ic95_superior"],
                    "significativo_abertura": resumo_a["significativo"],
                    "log_loss": log_loss,
                    "brier": brier,
                    "accuracy": accuracy,
                    "n_amostras_qualidade": n_qualidade,
                }
            )
            if not por_liga:
                return
            apostas_f_por_liga: dict[str, list[dict]] = {}
            for a in apostas_fechamento:
                apostas_f_por_liga.setdefault(a.get("liga") or "desconhecida", []).append(a)
            apostas_a_por_liga: dict[str, list[dict]] = {}
            for a in apostas_abertura:
                apostas_a_por_liga.setdefault(a.get("liga") or "desconhecida", []).append(a)
            match_ids_com_resultado_por_liga: dict[str, set[int]] = {}
            for mid in match_ids_com_resultado:
                match_ids_com_resultado_por_liga.setdefault(liga_por_match_id.get(mid) or "desconhecida", set()).add(mid)

            for liga in set(apostas_f_por_liga) | set(apostas_a_por_liga) | set(match_ids_com_resultado_por_liga):
                resumo_f_l = resumir_backtest(nome_variante, apostas_f_por_liga.get(liga, []), None)
                resumo_a_l = resumir_backtest(nome_variante, apostas_a_por_liga.get(liga, []), None)
                log_loss_l, brier_l, accuracy_l, n_qualidade_l = _metricas_probabilisticas(
                    preds, resultados_reais, match_ids_com_resultado_por_liga.get(liga, set()), mercado
                )
                periodo_ini_l, periodo_fim_l = periodo_por_liga_mercado.get(liga, (None, None))
                relatorio_por_liga.append(
                    {
                        "model_name": nome_variante,
                        "liga": liga,
                        "mercado": mercado,
                        "periodo_inicio": periodo_ini_l,
                        "periodo_fim": periodo_fim_l,
                        "n_apostas": resumo_f_l["n_apostas"],
                        "roi_medio": resumo_f_l["roi_medio"],
                        "roi_ic95_inferior": resumo_f_l["roi_ic95_inferior"],
                        "roi_ic95_superior": resumo_f_l["roi_ic95_superior"],
                        "significativo": resumo_f_l["significativo"],
                        "n_apostas_abertura": resumo_a_l["n_apostas"],
                        "roi_abertura_medio": resumo_a_l["roi_medio"],
                        "roi_abertura_ic95_inferior": resumo_a_l["roi_ic95_inferior"],
                        "roi_abertura_ic95_superior": resumo_a_l["roi_ic95_superior"],
                        "significativo_abertura": resumo_a_l["significativo"],
                        "log_loss": log_loss_l,
                        "brier": brier_l,
                        "accuracy": accuracy_l,
                        "n_amostras_qualidade": n_qualidade_l,
                    }
                )

        # --- dixon_coles_v1: baseline sem tuning, mas com o mesmo esquema de calibração ---
        # Modelo Poisson de GOLS -- não tem nenhuma noção de escanteio, pula
        # inteiro pra `corners_ou95` (ver `MERCADOS_SEM_DIXON_COLES`).
        if mercado in MERCADOS_SEM_DIXON_COLES:
            logger.info("[%s] dixon_coles_v1 não modela escanteios -- pulando o baseline pra este mercado.", mercado)
        else:
            try:
                logger.info("[%s] Rodando dixon_coles_v1 (força real + calibração ajustada no Val)...", mercado)
                forcas_finais, coeficientes_por_metodo = tunar_e_calibrar_dixon_coles(
                    partidas_treino, partidas_val, partidas_treino_val, partidas_teste["match_date"].min(), mercado
                )
                preds_raw = prever_dixon_coles_backtest(forcas_finais, partidas_teste)
                _registrar("dixon_coles_v1", preds_raw, None, por_liga=True)
                for metodo, coef in coeficientes_por_metodo.items():
                    preds_calibradas = {
                        mid: calibracao.aplicar_calibracao(p, coef, selecoes=selecoes_mercado) for mid, p in preds_raw.items()
                    }
                    _registrar(f"dixon_coles_v1_calibrado_{metodo}", preds_calibradas, None, por_liga=False)
            except Exception:
                logger.exception("[%s] Falha no baseline dixon_coles_v1 -- pulando, os outros modelos continuam.", mercado)

        # --- catboost/xgboost/lightgbm/mlp v9/v10/v11 (+ v1 raw, quando tem feature set): tuning + calibração ---
        for nome_modelo in modelos_ml.TREINADORES:
            if nome_modelo in MODELOS_SEM_FEATURE_SET_PADRAO:
                logger.info("[%s] %s sem feature set padrão (só usado pelo Treino Customizado) -- pulando.", mercado, nome_modelo)
                continue
            try:
                logger.info("[%s] Tuning + calibração + treino final: %s", mercado, nome_modelo)
                modelo, extra, melhor_params, coeficientes_por_metodo, curva = tunar_treinar_e_calibrar(
                    nome_modelo, train_df_m, val_df_m, train_mais_val_df_m, test_df_m, mercado
                )
                _, prever = modelos_ml.TREINADORES[nome_modelo]

                probs_teste, classes = prever(modelo, extra, test_df_m, features=modelos_ml.FEATURES_POR_MODELO[nome_modelo])
                preds_raw = modelos_ml.empacotar_predicoes(
                    test_df_m["match_id"].tolist(), probs_teste, classes, coluna_alvo=coluna_alvo
                )

                _registrar(nome_modelo, preds_raw, melhor_params, por_liga=True)
                if curva:
                    curvas_aprendizado.extend(
                        {"model_name": nome_modelo, "mercado": mercado, **ponto} for ponto in curva
                    )
                for metodo, coef in coeficientes_por_metodo.items():
                    preds_calibradas = {
                        mid: calibracao.aplicar_calibracao(p, coef, selecoes=selecoes_mercado) for mid, p in preds_raw.items()
                    }
                    _registrar(f"{nome_modelo}_calibrado_{metodo}", preds_calibradas, melhor_params, por_liga=False)
            except Exception:
                logger.exception("[%s] Falha ao rodar %s -- pulando, os outros modelos continuam.", mercado, nome_modelo)

        # --- linha sintética de referência: qualidade da própria Pinnacle (sem ROI) ---
        # Só existe quando há odd da Pinnacle pro mercado (nunca acontece
        # pra corners_ou95/faixa_gols -- ver módulo de comentário no topo).
        if match_ids_validos_qualidade:
            log_loss_mkt, brier_mkt, accuracy_mkt, n_mkt = _metricas_probabilisticas(
                pinnacle_devigada, resultados_reais, match_ids_validos_qualidade, mercado
            )
            relatorio.append(
                {
                    "model_name": "mercado_pinnacle_sem_vig",
                    "mercado": mercado,
                    "periodo_inicio": periodo_inicio_mercado,
                    "periodo_fim": periodo_fim_mercado,
                    "log_loss": log_loss_mkt,
                    "brier": brier_mkt,
                    "accuracy": accuracy_mkt,
                    "n_amostras_qualidade": n_mkt,
                }
            )
            match_ids_qual_por_liga_mkt: dict[str, set[int]] = {}
            for mid in match_ids_validos_qualidade:
                match_ids_qual_por_liga_mkt.setdefault(liga_por_match_id.get(mid) or "desconhecida", set()).add(mid)
            for liga, mids in match_ids_qual_por_liga_mkt.items():
                log_loss_l, brier_l, accuracy_l, n_l = _metricas_probabilisticas(pinnacle_devigada, resultados_reais, mids, mercado)
                periodo_ini_l, periodo_fim_l = periodo_por_liga_mercado.get(liga, (None, None))
                relatorio_por_liga.append(
                    {
                        "model_name": "mercado_pinnacle_sem_vig",
                        "liga": liga,
                        "mercado": mercado,
                        "periodo_inicio": periodo_ini_l,
                        "periodo_fim": periodo_fim_l,
                        "log_loss": log_loss_l,
                        "brier": brier_l,
                        "accuracy": accuracy_l,
                        "n_amostras_qualidade": n_l,
                    }
                )

        # --- mesma linha, mas SÓ fechamento (par abertura/fechamento pedido
        # pelo usuário, espelhando as 2 colunas de ROI de cada modelo) ---
        if match_ids_validos_qualidade_fechamento:
            log_loss_f, brier_f, accuracy_f, n_f = _metricas_probabilisticas(
                pinnacle_devigada_fechamento, resultados_reais, match_ids_validos_qualidade_fechamento, mercado
            )
            relatorio.append(
                {
                    "model_name": "mercado_pinnacle_sem_vig_fechamento",
                    "mercado": mercado,
                    "periodo_inicio": periodo_inicio_mercado,
                    "periodo_fim": periodo_fim_mercado,
                    "log_loss": log_loss_f,
                    "brier": brier_f,
                    "accuracy": accuracy_f,
                    "n_amostras_qualidade": n_f,
                }
            )
            match_ids_qual_por_liga_f: dict[str, set[int]] = {}
            for mid in match_ids_validos_qualidade_fechamento:
                match_ids_qual_por_liga_f.setdefault(liga_por_match_id.get(mid) or "desconhecida", set()).add(mid)
            for liga, mids in match_ids_qual_por_liga_f.items():
                log_loss_l, brier_l, accuracy_l, n_l = _metricas_probabilisticas(pinnacle_devigada_fechamento, resultados_reais, mids, mercado)
                periodo_ini_l, periodo_fim_l = periodo_por_liga_mercado.get(liga, (None, None))
                relatorio_por_liga.append(
                    {
                        "model_name": "mercado_pinnacle_sem_vig_fechamento",
                        "liga": liga,
                        "mercado": mercado,
                        "periodo_inicio": periodo_ini_l,
                        "periodo_fim": periodo_fim_l,
                        "log_loss": log_loss_l,
                        "brier": brier_l,
                        "accuracy": accuracy_l,
                        "n_amostras_qualidade": n_l,
                    }
                )

        imprimir_relatorio([r for r in relatorio if r["mercado"] == mercado and "roi_medio" in r])
        imprimir_relatorio_por_liga([r for r in relatorio_por_liga if r["mercado"] == mercado and "roi_medio" in r])
        imprimir_relatorio_qualidade(
            [
                {"nome": f"{r['model_name']} [{mercado}]", "log_loss": r["log_loss"], "brier": r["brier"], "accuracy": r["accuracy"], "n": r["n_amostras_qualidade"]}
                for r in relatorio
                if r["mercado"] == mercado and r.get("log_loss") is not None
            ]
        )

        if mercado == "1X2":
            predicoes_1x2_diagnostico = todas_as_predicoes_teste
            resultados_reais_1x2 = resultados_reais
            match_ids_validos_1x2 = match_ids_validos_qualidade

    # --- hibrido_gols_v1 / hibrido_gols_xg_v1: modelo misto com ML ---
    # Loop PRÓPRIO, fora do `for mercado in MERCADOS` acima -- cobre os 4
    # mercados em MERCADOS_HIBRIDO_VALIDOS, incluindo `corners_over_under_
    # 9.5` (que o loop principal pula inteiro, ver MERCADOS_SOMENTE_MODELO_
    # MISTO -- nunca treina classificador nesse mercado). Nunca retreina o
    # modelo misto aqui: só lê `model_predictions`/resultado real/odds pros
    # match_ids que ELE REALMENTE TEM (nunca os do Test Set dos modelos de
    # árvore, que usa um split cronológico diferente -- ver MODELOS_
    # HIBRIDOS acima). Reaproveita a MESMA simulação de apostas (montar_
    # apostas/Kelly/bootstrap) dos outros modelos.
    for mercado in MERCADOS_HIBRIDO_VALIDOS:
        for nome_hibrido in MODELOS_HIBRIDOS:
            try:
                preds_hibrido = carregar_predicoes_hibrido(supabase, nome_hibrido, mercado)
                if not preds_hibrido:
                    logger.info("[%s] %s: sem previsões em model_predictions -- pulando.", mercado, nome_hibrido)
                    continue

                match_ids_h = list(preds_hibrido.keys())
                resultados_hibrido = carregar_resultados_reais_hibrido(supabase, match_ids_h, mercado)
                match_ids_com_resultado_h = set(resultados_hibrido.keys())
                if not match_ids_com_resultado_h:
                    logger.info("[%s] %s: nenhum resultado real disponível -- pulando.", mercado, nome_hibrido)
                    continue

                partidas_hibrido = carregar_partidas_hibrido(supabase, match_ids_h)
                liga_por_match_id_h = {mid: nomes_liga.get(p["league_id"], "desconhecida") for mid, p in partidas_hibrido.items()}

                odds_fechamento_h = carregar_melhores_odds_fechamento(supabase, match_ids_h, mercado)
                odds_abertura_h = carregar_odds_pinnacle_abertura_bruta(supabase, match_ids_h, mercado)

                apostas_fechamento_h = montar_apostas(preds_hibrido, odds_fechamento_h, resultados_hibrido, liga_por_match_id_h, mercado)
                apostas_abertura_h = montar_apostas(preds_hibrido, odds_abertura_h, resultados_hibrido, liga_por_match_id_h, mercado)
                resumo_f_h = resumir_backtest(nome_hibrido, apostas_fechamento_h, None)
                resumo_a_h = resumir_backtest(nome_hibrido, apostas_abertura_h, None)
                log_loss_h, brier_h, accuracy_h, n_qualidade_h = _metricas_probabilisticas(
                    preds_hibrido, resultados_hibrido, match_ids_com_resultado_h, mercado
                )

                datas_h = [partidas_hibrido[mid]["match_date"] for mid in match_ids_com_resultado_h if mid in partidas_hibrido]
                periodo_inicio_h = min(datas_h)[:10] if datas_h else None
                periodo_fim_h = max(datas_h)[:10] if datas_h else None

                relatorio.append({
                    "model_name": nome_hibrido,
                    "mercado": mercado,
                    "periodo_inicio": periodo_inicio_h,
                    "periodo_fim": periodo_fim_h,
                    "treino_periodo_inicio": None,
                    "treino_periodo_fim": None,
                    "validacao_periodo_inicio": None,
                    "validacao_periodo_fim": None,
                    "hiperparametros": None,
                    "n_apostas": resumo_f_h["n_apostas"],
                    "roi_medio": resumo_f_h["roi_medio"],
                    "roi_ic95_inferior": resumo_f_h["roi_ic95_inferior"],
                    "roi_ic95_superior": resumo_f_h["roi_ic95_superior"],
                    "significativo": resumo_f_h["significativo"],
                    "n_apostas_abertura": resumo_a_h["n_apostas"],
                    "roi_abertura_medio": resumo_a_h["roi_medio"],
                    "roi_abertura_ic95_inferior": resumo_a_h["roi_ic95_inferior"],
                    "roi_abertura_ic95_superior": resumo_a_h["roi_ic95_superior"],
                    "significativo_abertura": resumo_a_h["significativo"],
                    "log_loss": log_loss_h,
                    "brier": brier_h,
                    "accuracy": accuracy_h,
                    "n_amostras_qualidade": n_qualidade_h,
                })

                apostas_f_por_liga_h: dict[str, list[dict]] = {}
                for a in apostas_fechamento_h:
                    apostas_f_por_liga_h.setdefault(a.get("liga") or "desconhecida", []).append(a)
                apostas_a_por_liga_h: dict[str, list[dict]] = {}
                for a in apostas_abertura_h:
                    apostas_a_por_liga_h.setdefault(a.get("liga") or "desconhecida", []).append(a)
                match_ids_por_liga_h: dict[str, set[int]] = {}
                for mid in match_ids_com_resultado_h:
                    match_ids_por_liga_h.setdefault(liga_por_match_id_h.get(mid) or "desconhecida", set()).add(mid)

                for liga in set(apostas_f_por_liga_h) | set(apostas_a_por_liga_h) | set(match_ids_por_liga_h):
                    mids_liga = match_ids_por_liga_h.get(liga, set())
                    datas_liga = [partidas_hibrido[mid]["match_date"] for mid in mids_liga if mid in partidas_hibrido]
                    resumo_f_l = resumir_backtest(nome_hibrido, apostas_f_por_liga_h.get(liga, []), None)
                    resumo_a_l = resumir_backtest(nome_hibrido, apostas_a_por_liga_h.get(liga, []), None)
                    log_loss_l, brier_l, accuracy_l, n_qualidade_l = _metricas_probabilisticas(
                        preds_hibrido, resultados_hibrido, mids_liga, mercado
                    )
                    relatorio_por_liga.append({
                        "model_name": nome_hibrido,
                        "liga": liga,
                        "mercado": mercado,
                        "periodo_inicio": min(datas_liga)[:10] if datas_liga else None,
                        "periodo_fim": max(datas_liga)[:10] if datas_liga else None,
                        "n_apostas": resumo_f_l["n_apostas"],
                        "roi_medio": resumo_f_l["roi_medio"],
                        "roi_ic95_inferior": resumo_f_l["roi_ic95_inferior"],
                        "roi_ic95_superior": resumo_f_l["roi_ic95_superior"],
                        "significativo": resumo_f_l["significativo"],
                        "n_apostas_abertura": resumo_a_l["n_apostas"],
                        "roi_abertura_medio": resumo_a_l["roi_medio"],
                        "roi_abertura_ic95_inferior": resumo_a_l["roi_ic95_inferior"],
                        "roi_abertura_ic95_superior": resumo_a_l["roi_ic95_superior"],
                        "significativo_abertura": resumo_a_l["significativo"],
                        "log_loss": log_loss_l,
                        "brier": brier_l,
                        "accuracy": accuracy_l,
                        "n_amostras_qualidade": n_qualidade_l,
                    })
                imprimir_relatorio_qualidade([{
                    "nome": f"{nome_hibrido} [{mercado}]", "log_loss": log_loss_h, "brier": brier_h,
                    "accuracy": accuracy_h, "n": n_qualidade_h,
                }])
            except Exception:
                logger.exception("[%s] Falha ao avaliar %s -- pulando, os outros modelos continuam.", mercado, nome_hibrido)

        # --- linha de referência "mercado_pinnacle_sem_vig" (+ fechamento) ---
        # Só pros mercados que NÃO passam pelo loop clássico acima (esse já
        # grava a própria referência pra 1X2/over_under_2.5/btts -- gravar
        # de novo aqui duplicaria a chave única (model_name, mercado) em
        # model_benchmarking_backtest). Usa o universo de match_ids que
        # hibrido_gols_v1 tem pra esse mercado (hibrido_gols_v1/xg_v1 têm
        # cobertura praticamente idêntica -- mesmo split, mesmo pipeline).
        if mercado not in ("1X2", "over_under_2.5", "btts"):
            try:
                match_ids_mkt = list(carregar_predicoes_hibrido(supabase, MODELOS_HIBRIDOS[0], mercado).keys())
                if match_ids_mkt:
                    resultados_mkt = carregar_resultados_reais_hibrido(supabase, match_ids_mkt, mercado)
                    partidas_mkt = carregar_partidas_hibrido(supabase, match_ids_mkt)
                    liga_por_match_id_mkt = {mid: nomes_liga.get(p["league_id"], "desconhecida") for mid, p in partidas_mkt.items()}

                    for nome_ref, odds_fn in (
                        ("mercado_pinnacle_sem_vig", carregar_odds_pinnacle_devigadas),
                        ("mercado_pinnacle_sem_vig_fechamento", carregar_odds_pinnacle_devigadas_fechamento),
                    ):
                        devigada_mkt = odds_fn(supabase, match_ids_mkt, mercado)
                        match_ids_validos_mkt = set(devigada_mkt.keys()) & set(resultados_mkt.keys())
                        if not match_ids_validos_mkt:
                            continue
                        log_loss_mkt, brier_mkt, accuracy_mkt, n_mkt = _metricas_probabilisticas(
                            devigada_mkt, resultados_mkt, match_ids_validos_mkt, mercado
                        )
                        datas_mkt = [partidas_mkt[mid]["match_date"] for mid in match_ids_validos_mkt if mid in partidas_mkt]
                        relatorio.append({
                            "model_name": nome_ref,
                            "mercado": mercado,
                            "periodo_inicio": min(datas_mkt)[:10] if datas_mkt else None,
                            "periodo_fim": max(datas_mkt)[:10] if datas_mkt else None,
                            "log_loss": log_loss_mkt,
                            "brier": brier_mkt,
                            "accuracy": accuracy_mkt,
                            "n_amostras_qualidade": n_mkt,
                        })
                        match_ids_qual_por_liga_mkt: dict[str, set[int]] = {}
                        for mid in match_ids_validos_mkt:
                            match_ids_qual_por_liga_mkt.setdefault(liga_por_match_id_mkt.get(mid) or "desconhecida", set()).add(mid)
                        for liga, mids in match_ids_qual_por_liga_mkt.items():
                            log_loss_l, brier_l, accuracy_l, n_l = _metricas_probabilisticas(devigada_mkt, resultados_mkt, mids, mercado)
                            datas_liga_mkt = [partidas_mkt[mid]["match_date"] for mid in mids if mid in partidas_mkt]
                            relatorio_por_liga.append({
                                "model_name": nome_ref,
                                "liga": liga,
                                "mercado": mercado,
                                "periodo_inicio": min(datas_liga_mkt)[:10] if datas_liga_mkt else None,
                                "periodo_fim": max(datas_liga_mkt)[:10] if datas_liga_mkt else None,
                                "log_loss": log_loss_l,
                                "brier": brier_l,
                                "accuracy": accuracy_l,
                                "n_amostras_qualidade": n_l,
                            })
            except Exception:
                logger.exception("[%s] Falha ao calcular referência 'mercado_pinnacle_sem_vig' -- pulando.", mercado)

    # --- hibrido_gols_v1 / hibrido_gols_xg_v1: dupla chance (simulação própria) ---
    # Mesmo modelo misto, mesmo princípio (nunca retreinado, só lê
    # model_predictions) -- fora do loop acima porque dupla chance não
    # cabe no formato genérico de "1 vencedor por partida" (ver comentário
    # em MERCADOS["dupla_chance"]/montar_apostas_dupla_chance).
    mercado = "dupla_chance"
    for nome_hibrido in MODELOS_HIBRIDOS:
        try:
            preds_hibrido = carregar_predicoes_hibrido(supabase, nome_hibrido, mercado)
            if not preds_hibrido:
                logger.info("[%s] %s: sem previsões em model_predictions -- pulando.", mercado, nome_hibrido)
                continue

            match_ids_h = list(preds_hibrido.keys())
            resultado_bruto_h = carregar_resultado_1x2_bruto_hibrido(supabase, match_ids_h)
            match_ids_com_resultado_h = set(resultado_bruto_h.keys())
            if not match_ids_com_resultado_h:
                logger.info("[%s] %s: nenhum resultado real disponível -- pulando.", mercado, nome_hibrido)
                continue

            partidas_hibrido = carregar_partidas_hibrido(supabase, match_ids_h)
            liga_por_match_id_h = {mid: nomes_liga.get(p["league_id"], "desconhecida") for mid, p in partidas_hibrido.items()}

            odds_fechamento_h = carregar_melhores_odds_fechamento(supabase, match_ids_h, mercado)
            odds_abertura_h = carregar_odds_pinnacle_abertura_bruta(supabase, match_ids_h, mercado)

            apostas_fechamento_h = montar_apostas_dupla_chance(preds_hibrido, odds_fechamento_h, resultado_bruto_h, liga_por_match_id_h)
            apostas_abertura_h = montar_apostas_dupla_chance(preds_hibrido, odds_abertura_h, resultado_bruto_h, liga_por_match_id_h)
            resumo_f_h = resumir_backtest(nome_hibrido, apostas_fechamento_h, None)
            resumo_a_h = resumir_backtest(nome_hibrido, apostas_abertura_h, None)
            log_loss_h, brier_h, accuracy_h, n_qualidade_h = _metricas_dupla_chance(
                preds_hibrido, resultado_bruto_h, match_ids_com_resultado_h
            )

            datas_h = [partidas_hibrido[mid]["match_date"] for mid in match_ids_com_resultado_h if mid in partidas_hibrido]
            periodo_inicio_h = min(datas_h)[:10] if datas_h else None
            periodo_fim_h = max(datas_h)[:10] if datas_h else None

            relatorio.append({
                "model_name": nome_hibrido,
                "mercado": mercado,
                "periodo_inicio": periodo_inicio_h,
                "periodo_fim": periodo_fim_h,
                "treino_periodo_inicio": None,
                "treino_periodo_fim": None,
                "validacao_periodo_inicio": None,
                "validacao_periodo_fim": None,
                "hiperparametros": None,
                "n_apostas": resumo_f_h["n_apostas"],
                "roi_medio": resumo_f_h["roi_medio"],
                "roi_ic95_inferior": resumo_f_h["roi_ic95_inferior"],
                "roi_ic95_superior": resumo_f_h["roi_ic95_superior"],
                "significativo": resumo_f_h["significativo"],
                "n_apostas_abertura": resumo_a_h["n_apostas"],
                "roi_abertura_medio": resumo_a_h["roi_medio"],
                "roi_abertura_ic95_inferior": resumo_a_h["roi_ic95_inferior"],
                "roi_abertura_ic95_superior": resumo_a_h["roi_ic95_superior"],
                "significativo_abertura": resumo_a_h["significativo"],
                "log_loss": log_loss_h,
                "brier": brier_h,
                "accuracy": accuracy_h,
                "n_amostras_qualidade": n_qualidade_h,
            })

            apostas_f_por_liga_h: dict[str, list[dict]] = {}
            for a in apostas_fechamento_h:
                apostas_f_por_liga_h.setdefault(a.get("liga") or "desconhecida", []).append(a)
            apostas_a_por_liga_h: dict[str, list[dict]] = {}
            for a in apostas_abertura_h:
                apostas_a_por_liga_h.setdefault(a.get("liga") or "desconhecida", []).append(a)
            match_ids_por_liga_h: dict[str, set[int]] = {}
            for mid in match_ids_com_resultado_h:
                match_ids_por_liga_h.setdefault(liga_por_match_id_h.get(mid) or "desconhecida", set()).add(mid)

            for liga in set(apostas_f_por_liga_h) | set(apostas_a_por_liga_h) | set(match_ids_por_liga_h):
                mids_liga = match_ids_por_liga_h.get(liga, set())
                datas_liga = [partidas_hibrido[mid]["match_date"] for mid in mids_liga if mid in partidas_hibrido]
                resumo_f_l = resumir_backtest(nome_hibrido, apostas_f_por_liga_h.get(liga, []), None)
                resumo_a_l = resumir_backtest(nome_hibrido, apostas_a_por_liga_h.get(liga, []), None)
                log_loss_l, brier_l, accuracy_l, n_qualidade_l = _metricas_dupla_chance(preds_hibrido, resultado_bruto_h, mids_liga)
                relatorio_por_liga.append({
                    "model_name": nome_hibrido,
                    "liga": liga,
                    "mercado": mercado,
                    "periodo_inicio": min(datas_liga)[:10] if datas_liga else None,
                    "periodo_fim": max(datas_liga)[:10] if datas_liga else None,
                    "n_apostas": resumo_f_l["n_apostas"],
                    "roi_medio": resumo_f_l["roi_medio"],
                    "roi_ic95_inferior": resumo_f_l["roi_ic95_inferior"],
                    "roi_ic95_superior": resumo_f_l["roi_ic95_superior"],
                    "significativo": resumo_f_l["significativo"],
                    "n_apostas_abertura": resumo_a_l["n_apostas"],
                    "roi_abertura_medio": resumo_a_l["roi_medio"],
                    "roi_abertura_ic95_inferior": resumo_a_l["roi_ic95_inferior"],
                    "roi_abertura_ic95_superior": resumo_a_l["roi_ic95_superior"],
                    "significativo_abertura": resumo_a_l["significativo"],
                    "log_loss": log_loss_l,
                    "brier": brier_l,
                    "accuracy": accuracy_l,
                    "n_amostras_qualidade": n_qualidade_l,
                })
        except Exception:
            logger.exception("[%s] Falha ao avaliar %s -- pulando, os outros modelos continuam.", mercado, nome_hibrido)

    # --- dupla chance: referência "mercado_pinnacle_sem_vig" (+ fechamento) ---
    # Derivada da 1X2 devigada (ver carregar_referencia_dupla_chance_pinnacle
    # -- dupla chance em si não devig a, não é uma partição válida), scorada
    # com _metricas_dupla_chance (mesma disciplina de evento binário por
    # seleção usada pros modelos acima, não _metricas_probabilisticas).
    try:
        match_ids_dc = list(carregar_predicoes_hibrido(supabase, MODELOS_HIBRIDOS[0], "dupla_chance").keys())
        if match_ids_dc:
            resultado_bruto_dc = carregar_resultado_1x2_bruto_hibrido(supabase, match_ids_dc)
            partidas_dc = carregar_partidas_hibrido(supabase, match_ids_dc)
            liga_por_match_id_dc = {mid: nomes_liga.get(p["league_id"], "desconhecida") for mid, p in partidas_dc.items()}

            for nome_ref, carregar_devigada_1x2 in (
                ("mercado_pinnacle_sem_vig", carregar_odds_pinnacle_devigadas),
                ("mercado_pinnacle_sem_vig_fechamento", carregar_odds_pinnacle_devigadas_fechamento),
            ):
                referencia_dc = carregar_referencia_dupla_chance_pinnacle(supabase, match_ids_dc, carregar_devigada_1x2)
                match_ids_validos_dc = set(referencia_dc.keys()) & set(resultado_bruto_dc.keys())
                if not match_ids_validos_dc:
                    continue
                log_loss_dc, brier_dc, accuracy_dc, n_dc = _metricas_dupla_chance(referencia_dc, resultado_bruto_dc, match_ids_validos_dc)
                datas_dc = [partidas_dc[mid]["match_date"] for mid in match_ids_validos_dc if mid in partidas_dc]
                relatorio.append({
                    "model_name": nome_ref,
                    "mercado": "dupla_chance",
                    "periodo_inicio": min(datas_dc)[:10] if datas_dc else None,
                    "periodo_fim": max(datas_dc)[:10] if datas_dc else None,
                    "log_loss": log_loss_dc,
                    "brier": brier_dc,
                    "accuracy": accuracy_dc,
                    "n_amostras_qualidade": n_dc,
                })
                match_ids_qual_por_liga_dc: dict[str, set[int]] = {}
                for mid in match_ids_validos_dc:
                    match_ids_qual_por_liga_dc.setdefault(liga_por_match_id_dc.get(mid) or "desconhecida", set()).add(mid)
                for liga, mids in match_ids_qual_por_liga_dc.items():
                    log_loss_l, brier_l, accuracy_l, n_l = _metricas_dupla_chance(referencia_dc, resultado_bruto_dc, mids)
                    datas_liga_dc = [partidas_dc[mid]["match_date"] for mid in mids if mid in partidas_dc]
                    relatorio_por_liga.append({
                        "model_name": nome_ref,
                        "liga": liga,
                        "mercado": "dupla_chance",
                        "periodo_inicio": min(datas_liga_dc)[:10] if datas_liga_dc else None,
                        "periodo_fim": max(datas_liga_dc)[:10] if datas_liga_dc else None,
                        "log_loss": log_loss_l,
                        "brier": brier_l,
                        "accuracy": accuracy_l,
                        "n_amostras_qualidade": n_l,
                    })
    except Exception:
        logger.exception("[dupla_chance] Falha ao calcular referência 'mercado_pinnacle_sem_vig' -- pulando.")

    salvar_relatorio(supabase, relatorio)
    salvar_relatorio_por_liga(supabase, relatorio_por_liga)
    salvar_curva_aprendizado(supabase, curvas_aprendizado)

    # --- diagnósticos extra (comparação pareada + Closing Line Value) -- só 1X2 por ora ---
    if match_ids_validos_1x2:
        try:
            logger.info("Testando superioridade/não-inferioridade PAREADA contra o mercado (1X2)...")
            pinnacle_devigada_1x2 = carregar_odds_pinnacle_devigadas(supabase, match_ids_teste, "1X2")
            match_ids_ordenados = sorted(match_ids_validos_1x2)
            linhas_pareadas = []
            for nome_variante, preds in predicoes_1x2_diagnostico.items():
                match_ids_com_pred = [mid for mid in match_ids_ordenados if mid in preds]
                if len(match_ids_com_pred) < AMOSTRA_MINIMA_CLV:
                    continue
                perdas_modelo_log, _ = _perdas_por_partida(preds, resultados_reais_1x2, match_ids_com_pred)
                perdas_mercado_alinhada, _ = _perdas_por_partida(pinnacle_devigada_1x2, resultados_reais_1x2, match_ids_com_pred)
                comparacao = comparar_pareado_com_mercado(perdas_modelo_log, perdas_mercado_alinhada)
                linhas_pareadas.append({"nome": nome_variante, "comparacao": comparacao})
            imprimir_relatorio_pareado(linhas_pareadas)
        except Exception:
            logger.exception("Falha ao comparar qualidade de probabilidade com o mercado -- pulando essa seção do relatório.")

        try:
            logger.info("Testando Closing Line Value (abertura vs. fechamento da Pinnacle, 1X2)...")
            resultados_clv = testar_closing_line_value(supabase, match_ids_teste, predicoes_1x2_diagnostico)
            imprimir_relatorio_clv(resultados_clv)
        except Exception:
            logger.exception("Falha ao testar Closing Line Value -- pulando essa seção do relatório.")


if __name__ == "__main__":
    main()
