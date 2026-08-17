"""Treino do modelo de XI titular previsto (probabilidade de cada jogador do
elenco começar a próxima partida como titular).

Reescrito a partir da versão original, que nunca rodou com sucesso em
produção: lia `players.is_injured` (coluna que não existe em nenhuma
migration), fazia split aleatório (vaza rodadas futuras de um jogador pro
treino de rodadas passadas dele -- quebra a disciplina de ponto-no-tempo
que o resto do repo segue, ver `dados_historicos._carregar_titular_pre_jogo`)
e salvava o modelo em disco local do runner (`modelos/*.pkl`), que é
descartado ao fim do job do GitHub Actions.

Persistência: mesmo bucket privado (`custom-model-artifacts`) e mecanismo
(joblib) já usado por `model_artifacts.py` pro resto do sistema de modelos
customizados -- prefixo `xi_titular/` pra não colidir com artefatos de
config_id. Cada algoritmo treinado é registrado em `models_registry`
(name=`xi_titular_{algoritmo}`, market=`xi_titular`), a mesma tabela usada
por todos os outros modelos do projeto (v1-v10, dixon_coles etc.) -- não
`custom_model_configs`, que é especificamente pro painel de Treino
Customizado de MERCADO de partida (target='1x2' etc.), não pra um
classificador por jogador.

Força do oponente + risco de suspensão (pedido do usuário): "levar em
consideração o poder do time oponente (geralmente há preservação de
jogadores muito utilizados e/ou pendurados)". 3 features novas, todas
ponto-no-tempo (mesma disciplina de `dados_historicos._carregar_elo_pre_
jogo`/`_carregar_squad_rating_pre_jogo`/`_carregar_cartoes_jogador_pre_
jogo`, reaproveitadas daqui): `elo_diff`/`squad_rating_diff` (força do
PRÓPRIO time menos a do OPONENTE na data da partida -- vantagem/
desvantagem esperada) e `esta_pendurado` (o jogador está a 1 cartão
amarelo da suspensão). A ideia é o modelo aprender o padrão real de
rotação: titular fixo tende a ser poupado quando o oponente é fraco e/ou
ele está pendurado, e mantido quando o jogo é difícil.

Stacking (pedido do usuário: "tem como fazer uma stacking?" / "Forest e
MLP podem ser usados pra incrementar?") -- 2 modelos base novos
(`random_forest`, `mlp`) somados aos 3 já existentes, mais um meta-modelo
`LogisticRegression` (mesmo padrão já usado no stacking do modelo v9 de
resultado de partida, `walkforward_cv_v9.py`/`modelos_ml.treinar_
stacking_v9`) que aprende um peso por modelo base a partir das previsões
OUT-OF-FOLD deles (`_gerar_previsoes_oof`, via `TimeSeriesSplit` -- nunca
KFold aleatório, quebraria a disciplina de ponto-no-tempo). O meta-modelo
recebe [prob_lightgbm, prob_xgboost, prob_catboost, prob_random_forest,
prob_mlp] e devolve uma probabilidade combinada -- na prática funciona como
uma média ponderada "suavizada" (pesos aprendidos + intercepto + sigmoide),
não uma média simples fixa como antes. RandomForest decorrelaciona um
pouco das 3 árvores boosted já existentes (bagging, não boosting); MLP é
uma família genuinamente diferente (rede neural) -- mesmo racional de
diversidade que já vale pro stacking v9.

Uso:
    SUPABASE_URL=... SUPABASE_KEY=... python3 treinar_modelo_xi.py
"""

import io
import logging

import joblib
import numpy as np
import pandas as pd
from catboost import CatBoostClassifier
from lightgbm import LGBMClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss, f1_score, log_loss, roc_auc_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from supabase import Client, create_client
from xgboost import XGBClassifier

import dados_historicos as dh

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BUCKET_ARTEFATOS = "custom-model-artifacts"
FEATURES = [
    "dias_desde_ultimo_jogo", "hierarquia_elenco", "media_rating_5j", "posicao_num", "valor_mercado_eur", "is_lesionado",
    "elo_diff", "squad_rating_diff", "esta_pendurado",
]
TARGET = "is_starter"
FRACAO_TESTE = 0.2

# Ordem CANÔNICA dos 5 modelos base -- rodar_xi_previsto.py usa a MESMA
# lista/ordem pra montar o vetor que alimenta o meta-modelo ao vivo. Trocar
# a ordem aqui sem trocar lá faz o meta-modelo aplicar o peso errado no
# modelo errado (ele não sabe o "nome" de cada coluna, só a posição).
ALGORITMOS = ["lightgbm", "xgboost", "catboost", "random_forest", "mlp"]
NOME_META_MODELO = "stacking_logreg"
N_FOLDS_STACKING = 3


def _paginar_keyset(query_factory, tamanho_pagina: int = 1000) -> list[dict]:
    """Pagina por CURSOR na chave primária `id` (indexada), não por OFFSET.

    Achado rodando em produção: match_lineup_fotmob já tem 190k+ linhas, e
    OFFSET grande dá `statement timeout` no Postgres (cada página reescaneia
    do início) -- mesma classe de bug já documentada em dados_historicos.py
    pra match_player_stats_fotmob (>250k linhas). `query_factory(cursor)`
    deve devolver a query já com `.select(...)` incluindo `id`, ordenada por
    `id` e filtrada por `.gt('id', cursor)` -- só o `.limit()` é aplicado
    aqui."""
    linhas: list[dict] = []
    cursor = 0
    while True:
        lote = query_factory(cursor).gt("id", cursor).order("id").limit(tamanho_pagina).execute().data or []
        if not lote:
            break
        linhas.extend(lote)
        cursor = lote[-1]["id"]
        if len(lote) < tamanho_pagina:
            break
    return linhas


def _buscar_por_lotes(supabase: Client, tabela: str, coluna_filtro: str, valores: list, colunas: str) -> list[dict]:
    """Filtra `coluna_filtro IN valores` em lotes de 1000 (limite prático do
    `.in_()`) E pagina o resultado de cada lote por OFFSET, ordenado pela
    PRÓPRIA coluna filtrada -- um lote de 1000 match_id pode devolver mais
    de 1000 linhas (~22 jogadores/partida), então as duas camadas de
    paginação são necessárias.

    Keyset por `id` (`_paginar_keyset`) foi tentado aqui primeiro e deu
    timeout: `ORDER BY id` numa tabela de 250k+ linhas filtrada por
    `match_id IN (...)` faz o planner ignorar o índice de `match_id` --
    ordenar pela própria coluna do filtro (já indexada, mesmo padrão
    comprovado em `dados_historicos._carregar_titular_pre_jogo`) evita
    isso; o lote já é limitado a 1000 valores de filtro, então OFFSET
    dentro dele fica bem menor que o full-table scan que motivou o keyset
    em `carregar_dados`.

    `coluna_filtro` sozinha não é única quando não é a PK (ex.: match_id em
    match_player_stats_fotmob, várias linhas por partida) -- OFFSET com
    ORDER BY empatado pode pular ou repetir linha entre páginas (mesmo bug
    achado em produção em rodar_xi_previsto.py, upsert duplicado). Desempate
    por `id` (chave primária de toda tabela usada aqui)."""
    linhas: list[dict] = []
    for i in range(0, len(valores), 1000):
        lote_valores = valores[i : i + 1000]
        pagina = 0
        while True:
            inicio = pagina * 1000
            resp = (
                supabase.table(tabela)
                .select(colunas)
                .in_(coluna_filtro, lote_valores)
                .order(coluna_filtro)
                .order("id")
                .range(inicio, inicio + 999)
                .execute()
            )
            pagina_dados = resp.data or []
            linhas.extend(pagina_dados)
            if len(pagina_dados) < 1000:
                break
            pagina += 1
    return linhas


def carregar_dados(supabase: Client) -> pd.DataFrame:
    logger.info("Buscando dados de lineup no Supabase...")
    # NÃO selecionar match_lineup_fotmob.position_id: é a posição no gráfico
    # de FORMAÇÃO TÁTICA, só existe pra quem está no XI -- achado testando
    # métricas em produção: 99,99% dos titulares têm position_id preenchido
    # contra só 30,7% dos reservas, então vira um proxy quase perfeito do
    # PRÓPRIO ALVO (accuracy/AUC saíam ~1.0, vazamento clássico) e nem
    # existiria pra uma partida futura (rodar_xi_previsto.py nunca teve
    # acesso a isso). posicao_num usa só players.usual_position_id (mesma
    # fonte usada ao vivo).
    lineup_rows = _paginar_keyset(
        lambda cursor: supabase.table("match_lineup_fotmob").select("id, match_id, team_id, player_id, is_starter")
    )
    for linha in lineup_rows:
        linha.pop("id", None)
    if not lineup_rows:
        return pd.DataFrame()
    df_lineup = pd.DataFrame(lineup_rows)
    df_lineup = df_lineup[df_lineup["player_id"].notna()].copy()

    match_ids = df_lineup["match_id"].unique().tolist()
    # match_ids pode ter dezenas de milhares de valores (190k+ linhas de
    # lineup) -- um único `.in_("id", match_ids)` sem lotear estoura o
    # limite de tamanho de URL (httpx.InvalidURL: "query too long").
    matches_rows = _buscar_por_lotes(supabase, "matches", "id", match_ids, "id, match_date, home_team_id, away_team_id, season, league_id")
    df_matches = pd.DataFrame(matches_rows).rename(columns={"id": "match_id"})

    player_ids = df_lineup["player_id"].unique().tolist()
    players_rows = _buscar_por_lotes(supabase, "players", "id", player_ids, "id, usual_position_id, market_value")
    df_players = pd.DataFrame(players_rows).rename(columns={"id": "player_id"})

    # rating por jogador/partida não vive em match_lineup_fotmob (só tem
    # team_rating, agregado do time inteiro) -- é match_player_stats_fotmob.rating.
    stats_rows = _buscar_por_lotes(supabase, "match_player_stats_fotmob", "match_id", match_ids, "match_id, player_id, rating")
    df_stats = pd.DataFrame(stats_rows) if stats_rows else pd.DataFrame(columns=["match_id", "player_id", "rating"])

    df = (
        df_lineup.merge(df_matches, on="match_id", how="left")
        .merge(df_players, on="player_id", how="left")
        .merge(df_stats, on=["match_id", "player_id"], how="left")
    )
    df["opponent_team_id"] = np.where(df["team_id"] == df["home_team_id"], df["away_team_id"], df["home_team_id"])

    # Força do oponente (elo + rating do elenco), ponto-no-tempo -- reaproveita
    # as mesmas funções já usadas pro dataset "Feature Stacked" dos modelos de
    # resultado de partida (dados_historicos.py), aqui juntadas TAMBÉM pro
    # lado do oponente (`elo_oponente`/`squad_rating_oponente`) pra computar
    # o diferencial de força na 2ª etapa (`engenharia_features`).
    league_ids = df_matches["league_id"].dropna().astype(int).unique().tolist()
    elo = dh._carregar_elo_pre_jogo(supabase, league_ids)
    if not elo.empty:
        df = df.merge(elo.rename(columns={"rating_antes": "elo_proprio"}), on=["match_id", "team_id"], how="left")
        df = df.merge(
            elo.rename(columns={"team_id": "opponent_team_id", "rating_antes": "elo_oponente"}),
            on=["match_id", "opponent_team_id"],
            how="left",
        )
    else:
        df["elo_proprio"] = np.nan
        df["elo_oponente"] = np.nan

    squad_rating = dh._carregar_squad_rating_pre_jogo(supabase, match_ids)
    if not squad_rating.empty:
        df = df.merge(
            squad_rating.rename(columns={"squad_rating_antes": "squad_rating_proprio"}), on=["match_id", "team_id"], how="left"
        )
        df = df.merge(
            squad_rating.rename(columns={"team_id": "opponent_team_id", "squad_rating_antes": "squad_rating_oponente"}),
            on=["match_id", "opponent_team_id"],
            how="left",
        )
    else:
        df["squad_rating_proprio"] = np.nan
        df["squad_rating_oponente"] = np.nan

    # Risco de suspensão (pendurado) por JOGADOR, ponto-no-tempo -- mesma
    # regra por liga (dados_historicos.CARTAO_LIMIAR_POR_LIGA) já usada pras
    # features de TIME (jogadores_pendurados_home/_away), aqui na
    # granularidade de jogador (ver dh._carregar_cartoes_jogador_pre_jogo).
    ligas_rows = supabase.table("leagues").select("id, name").execute().data or []
    nome_da_liga = {l["id"]: l["name"] for l in ligas_rows}
    partidas_meta = df_matches.rename(columns={"match_id": "id"})[["id", "league_id", "season", "match_date"]].dropna(
        subset=["league_id", "season", "match_date"]
    )
    partidas_meta = partidas_meta.copy()
    partidas_meta["match_date"] = pd.to_datetime(partidas_meta["match_date"], utc=True)
    cartoes_jogador = dh._carregar_cartoes_jogador_pre_jogo(supabase, partidas_meta, nome_da_liga)
    if not cartoes_jogador.empty:
        df = df.merge(cartoes_jogador[["match_id", "team_id", "player_id", "pendurado"]], on=["match_id", "team_id", "player_id"], how="left")
    else:
        df["pendurado"] = False

    return df


def engenharia_features(df: pd.DataFrame) -> pd.DataFrame:
    logger.info("Gerando features para o modelo XI...")
    df["match_date"] = pd.to_datetime(df["match_date"], utc=True)
    df = df.sort_values(by=["player_id", "match_date"])
    df["is_starter"] = df["is_starter"].fillna(False).astype(int)
    df["dias_desde_ultimo_jogo"] = df.groupby("player_id")["match_date"].diff().dt.days
    df["dias_desde_ultimo_jogo"] = df["dias_desde_ultimo_jogo"].fillna(14).clip(upper=30)
    df["jogos_acumulados"] = df.groupby("player_id").cumcount()
    df["titular_acumulado"] = df.groupby("player_id")["is_starter"].cumsum() - df["is_starter"]
    df["hierarquia_elenco"] = np.where(df["jogos_acumulados"] > 0, df["titular_acumulado"] / df["jogos_acumulados"], 0.5)
    df["rating"] = pd.to_numeric(df["rating"], errors="coerce")
    df["media_rating_5j"] = df.groupby("player_id")["rating"].shift(1).rolling(5, min_periods=1).mean().fillna(6.0)
    df["posicao_num"] = df["usual_position_id"].fillna(0).astype(int)
    df["valor_mercado_eur"] = df["market_value"].fillna(100000)
    df["is_lesionado"] = 0  # sem histórico de lesão (só snapshot atual, ver player_availability_fotmob) -- treino não tem esse sinal, é 0 constante de propósito; ao vivo (rodar_xi_previsto.py) usa o snapshot real.
    # Força do oponente -- diferencial (próprio - oponente); NaN quando falta
    # dado de um dos lados (elo/squad_rating de liga/temporada sem cobertura)
    # fica como está -- lightgbm/xgboost/catboost lidam nativamente com NaN
    # numérico (mesma tolerância de todo o resto do repo); random_forest/mlp
    # não toleram NaN nativamente, por isso os dois vêm embrulhados num
    # Pipeline com SimpleImputer em `_construir_modelos_base` abaixo.
    df["elo_diff"] = df["elo_proprio"] - df["elo_oponente"]
    df["squad_rating_diff"] = df["squad_rating_proprio"] - df["squad_rating_oponente"]
    df["esta_pendurado"] = df["pendurado"].fillna(False).astype(int)
    return df.dropna(subset=["match_date", "player_id", "team_id"])


def _split_cronologico(df: pd.DataFrame, fracao_teste: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split por DATA (treino = passado, teste = mais recente), não
    aleatório -- um split aleatório embaralharia rodadas futuras de um
    jogador pro treino de rodadas passadas dele, vazando informação que não
    existiria no momento real da predição (mesma disciplina de
    ponto-no-tempo de `_carregar_titular_pre_jogo` em dados_historicos.py)."""
    datas_unicas = np.sort(df["match_date"].unique())
    corte = datas_unicas[int(len(datas_unicas) * (1 - fracao_teste))]
    treino = df[df["match_date"] < corte].reset_index(drop=True)
    teste = df[df["match_date"] >= corte].reset_index(drop=True)
    return treino, teste


def _metricas_top11(df_teste: pd.DataFrame, y_proba: np.ndarray) -> dict:
    """Métrica que importa de verdade pro caso de uso "escalação provável":
    não é a accuracy por jogador (dominada pelo desbalanceamento
    titular/banco), é o quão bem os 11 jogadores de maior probabilidade
    prevista por (match_id, team_id) batem com o XI real daquele jogo.

    Sem legenda confiável pra `position_id` (mesma ressalva já documentada
    na migration de match_lineup_fotmob), não dá pra forçar "1 goleiro + 10
    de linha" -- o ranking é só por probabilidade, mesma limitação que a
    feature `posicao_num` já aceita."""
    grupo = df_teste[["match_id", "team_id", "is_starter"]].copy()
    grupo["proba"] = y_proba
    precisoes = []
    exatos = []
    for _, g in grupo.groupby(["match_id", "team_id"]):
        if len(g) < 11 or g["is_starter"].sum() == 0:
            continue
        top11 = g.sort_values("proba", ascending=False).head(11)
        reais = set(g[g["is_starter"] == 1].index)
        previstos = set(top11.index)
        acertos = len(reais & previstos)
        precisoes.append(acertos / 11)
        exatos.append(1.0 if reais == previstos else 0.0)
    if not precisoes:
        return {"precisao_media_top11": float("nan"), "taxa_xi_exato": float("nan"), "n_partidas_avaliadas": 0}
    return {
        "precisao_media_top11": float(np.mean(precisoes)),
        "taxa_xi_exato": float(np.mean(exatos)),
        "n_partidas_avaliadas": len(precisoes),
    }


def _salvar_artefato(supabase: Client, nome_algoritmo: str, modelo) -> str:
    path = f"xi_titular/{nome_algoritmo}.joblib"
    buffer = io.BytesIO()
    joblib.dump(modelo, buffer)
    supabase.storage.from_(BUCKET_ARTEFATOS).upload(
        path, buffer.getvalue(), {"upsert": "true", "content-type": "application/octet-stream"}
    )
    return path


def _construir_modelos_base(peso_pos: float) -> dict:
    """Os 5 modelos base do stacking -- mesma interface sklearn (`.fit(X,y)`/
    `.predict_proba(X)`) pros 5, inclusive random_forest/mlp (embrulhados
    num Pipeline com SimpleImputer -- ao contrário de lightgbm/xgboost/
    catboost, nenhum dos dois tolera NaN nativamente, e os diferenciais de
    força do oponente (`elo_diff`/`squad_rating_diff`) podem vir NaN quando
    falta cobertura de liga/temporada). `MLPClassifier` não aceita
    `class_weight` (limitação do sklearn) -- fica sem ponderação de classe,
    aceitável porque o desbalanceamento titular/banco aqui não é severo
    (~40-45% positivo, bem diferente de um evento raro)."""
    return {
        "lightgbm": LGBMClassifier(
            n_estimators=300, max_depth=5, learning_rate=0.05, class_weight="balanced", random_state=42, verbose=-1
        ),
        "xgboost": XGBClassifier(
            n_estimators=300, max_depth=5, learning_rate=0.05, scale_pos_weight=peso_pos, random_state=42, eval_metric="logloss"
        ),
        "catboost": CatBoostClassifier(iterations=300, depth=5, learning_rate=0.05, auto_class_weights="Balanced", random_seed=42, verbose=0),
        "random_forest": Pipeline([
            ("imputer", SimpleImputer(strategy="mean")),
            ("random_forest", RandomForestClassifier(
                n_estimators=300, max_depth=10, min_samples_leaf=5, class_weight="balanced", random_state=42, n_jobs=-1
            )),
        ]),
        "mlp": Pipeline([
            ("imputer", SimpleImputer(strategy="mean")),
            ("scaler", StandardScaler()),
            ("mlp", MLPClassifier(
                hidden_layer_sizes=(64, 32), max_iter=300, early_stopping=True, validation_fraction=0.15,
                n_iter_no_change=20, random_state=42,
            )),
        ]),
    }


def _gerar_previsoes_oof(treino: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Previsões OUT-OF-FOLD dos 5 modelos base, pra treinar o meta-modelo
    de stacking sem vazamento -- usar a previsão do PRÓPRIO modelo treinado
    nos mesmos dados que ele viu (in-sample) deixaria o meta-modelo
    superconfiante nos modelos que mais decoram o treino, não nos que mais
    generalizam. `TimeSeriesSplit` (não KFold aleatório) respeita a
    disciplina de ponto-no-tempo do resto do repo: cada fold treina só com
    dados ANTERIORES ao bloco que está sendo previsto, nunca com o futuro.

    O primeiro bloco cronológico (~1/(N_FOLDS_STACKING+1) do treino) nunca
    vira "validação" de nenhum fold (não tem passado suficiente antes dele)
    -- fica de fora do treino do meta-modelo, mesmo espírito de "sem dado
    suficiente, não força um valor arbitrário" já usado no resto do repo."""
    treino = treino.sort_values("match_date").reset_index(drop=True)
    X, y = treino[FEATURES], treino[TARGET]

    tscv = TimeSeriesSplit(n_splits=N_FOLDS_STACKING)
    meta_X = np.full((len(treino), len(ALGORITMOS)), np.nan)
    linhas_com_oof = np.zeros(len(treino), dtype=bool)

    for fold, (idx_treino, idx_val) in enumerate(tscv.split(X)):
        logger.info(f"  OOF fold {fold + 1}/{N_FOLDS_STACKING} (treino={len(idx_treino)}, val={len(idx_val)})...")
        X_fold_treino, y_fold_treino = X.iloc[idx_treino], y.iloc[idx_treino]
        X_fold_val = X.iloc[idx_val]
        peso_pos_fold = (len(y_fold_treino) - sum(y_fold_treino)) / max(sum(y_fold_treino), 1)
        modelos_fold = _construir_modelos_base(peso_pos_fold)
        for i, nome in enumerate(ALGORITMOS):
            modelos_fold[nome].fit(X_fold_treino, y_fold_treino)
            meta_X[idx_val, i] = modelos_fold[nome].predict_proba(X_fold_val)[:, 1]
        linhas_com_oof[idx_val] = True

    meta_X_validas = meta_X[linhas_com_oof]
    meta_y_validas = y.to_numpy()[linhas_com_oof]
    logger.info(f"  OOF gerado: {len(meta_X_validas)}/{len(treino)} linhas ({linhas_com_oof.mean():.1%} de cobertura).")
    return meta_X_validas, meta_y_validas


def _calcular_metricas(y_test, y_pred, y_proba, teste: pd.DataFrame) -> dict:
    return {
        "accuracy": float(accuracy_score(y_test, y_pred)),
        "log_loss": float(log_loss(y_test, y_proba)),
        "brier_score": float(brier_score_loss(y_test, y_proba)),
        "roc_auc": float(roc_auc_score(y_test, y_proba)),
        "f1": float(f1_score(y_test, y_pred)),
        **_metricas_top11(teste, y_proba),
    }


def treinar(df: pd.DataFrame, supabase: Client) -> dict:
    logger.info(f"Treinando modelos base ({', '.join(ALGORITMOS)}) + stacking para Previsão de XI...")
    treino, teste = _split_cronologico(df, FRACAO_TESTE)
    logger.info(f"Treino: {len(treino)} | Teste: {len(teste)} linhas")

    X_train, y_train = treino[FEATURES], treino[TARGET]
    X_test, y_test = teste[FEATURES], teste[TARGET]

    # 1) Previsões OOF do treino -- alimentam o meta-modelo sem vazamento.
    logger.info("Gerando previsões out-of-fold pro meta-modelo de stacking...")
    meta_X_train, meta_y_train = _gerar_previsoes_oof(treino)
    meta_modelo = LogisticRegression(max_iter=500, random_state=42)
    meta_modelo.fit(meta_X_train, meta_y_train)

    # 2) Modelos base FINAIS -- refit em TODO o treino (não só os folds OOF).
    # São os artefatos que vão pro ar em produção (rodar_xi_previsto.py),
    # tanto pra previsão individual quanto como entrada do meta-modelo.
    peso_pos = (len(y_train) - sum(y_train)) / max(sum(y_train), 1)
    modelos = _construir_modelos_base(peso_pos)

    resultado: dict[str, dict] = {}
    meta_X_test = np.zeros((len(teste), len(ALGORITMOS)))
    for i, nome in enumerate(ALGORITMOS):
        logger.info(f"Treinando {nome} (final, treino completo)...")
        modelos[nome].fit(X_train, y_train)

        y_proba = modelos[nome].predict_proba(X_test)[:, 1]
        y_pred = modelos[nome].predict(X_test)
        meta_X_test[:, i] = y_proba

        metricas = _calcular_metricas(y_test, y_pred, y_proba, teste)
        logger.info(
            f"{nome} - Acc: {metricas['accuracy']:.4f} | AUC: {metricas['roc_auc']:.4f} | "
            f"Precisão top-11: {metricas['precisao_media_top11']:.4f} | XI exato: {metricas['taxa_xi_exato']:.4f}"
        )

        path = _salvar_artefato(supabase, nome, modelos[nome])
        resultado[nome] = {"metricas": metricas, "artifact_path": path}

        supabase.table("models_registry").upsert(
            {
                "name": f"xi_titular_{nome}",
                "market": "xi_titular",
                "algorithm": nome,
                "status": "testing",
                "features_used": FEATURES,
                "metrics_test": metricas,
                "artifact_url": path,
            },
            on_conflict="name,market",
        ).execute()

    # 3) Stacking -- avalia o meta-modelo em cima das previsões dos modelos
    # base FINAIS no teste (mesmo caminho que rodar_xi_previsto.py segue ao
    # vivo: prevê com os 5, empilha, passa pro meta-modelo). Pedido do
    # usuário: registrar tanto o desempenho quanto a PARTICIPAÇÃO de cada
    # modelo base na resposta final -- os coeficientes da LogisticRegression
    # já são diretamente comparáveis entre si (as 5 entradas são todas
    # probabilidades em [0,1], mesma escala), então |peso_i| / soma(|peso|)
    # é uma leitura honesta de "quanto pesa" cada modelo na decisão.
    logger.info("Avaliando stacking (meta-modelo sobre os 5 modelos base)...")
    y_proba_stacking = meta_modelo.predict_proba(meta_X_test)[:, 1]
    y_pred_stacking = meta_modelo.predict(meta_X_test)
    metricas_stacking = _calcular_metricas(y_test, y_pred_stacking, y_proba_stacking, teste)

    pesos_meta_modelo = dict(zip(ALGORITMOS, meta_modelo.coef_[0].tolist()))
    soma_pesos_abs = sum(abs(w) for w in pesos_meta_modelo.values()) or 1.0
    participacao_relativa_pct = {nome: abs(w) / soma_pesos_abs for nome, w in pesos_meta_modelo.items()}
    metricas_stacking["pesos_meta_modelo"] = pesos_meta_modelo
    metricas_stacking["intercepto_meta_modelo"] = float(meta_modelo.intercept_[0])
    metricas_stacking["participacao_relativa_pct"] = participacao_relativa_pct

    logger.info(
        f"stacking - Acc: {metricas_stacking['accuracy']:.4f} | AUC: {metricas_stacking['roc_auc']:.4f} | "
        f"Precisão top-11: {metricas_stacking['precisao_media_top11']:.4f} | XI exato: {metricas_stacking['taxa_xi_exato']:.4f}"
    )
    logger.info("Participação de cada modelo na resposta do stacking:")
    for nome, pct in sorted(participacao_relativa_pct.items(), key=lambda kv: -kv[1]):
        logger.info(f"  {nome}: peso={pesos_meta_modelo[nome]:+.3f} ({pct:.1%} de participação relativa)")

    meta_path = _salvar_artefato(supabase, NOME_META_MODELO, meta_modelo)
    resultado["stacking"] = {"metricas": metricas_stacking, "artifact_path": meta_path}

    supabase.table("models_registry").upsert(
        {
            "name": "xi_titular_stacking",
            "market": "xi_titular",
            "algorithm": f"stacking (LogisticRegression sobre {', '.join(ALGORITMOS)})",
            "status": "testing",
            "features_used": ALGORITMOS,  # input do meta-modelo são as probs dos 5 modelos base, não as features brutas
            "metrics_test": metricas_stacking,
            "artifact_url": meta_path,
        },
        on_conflict="name,market",
    ).execute()

    return resultado


if __name__ == "__main__":
    import os

    url = os.environ["SUPABASE_URL"].strip()
    key = os.environ["SUPABASE_KEY"].strip()
    sb = create_client(url, key)

    df_bruto = carregar_dados(sb)
    if df_bruto.empty:
        logger.warning("Sem dados de lineup em match_lineup_fotmob -- nada para treinar.")
    else:
        treinar(engenharia_features(df_bruto), sb)
