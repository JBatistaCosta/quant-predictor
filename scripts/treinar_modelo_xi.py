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
from sklearn.metrics import accuracy_score, brier_score_loss, f1_score, log_loss, roc_auc_score
from supabase import Client, create_client
from xgboost import XGBClassifier

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BUCKET_ARTEFATOS = "custom-model-artifacts"
FEATURES = ["dias_desde_ultimo_jogo", "hierarquia_elenco", "media_rating_5j", "posicao_num", "valor_mercado_eur", "is_lesionado"]
TARGET = "is_starter"
FRACAO_TESTE = 0.2


def _paginar(query_factory, tamanho_pagina: int = 1000) -> list[dict]:
    """PostgREST corta em 1000 linhas sem `.range()` -- mesma disciplina de
    paginação usada em todo o resto do repo (ver dados_historicos.py)."""
    linhas: list[dict] = []
    pagina = 0
    while True:
        inicio = pagina * tamanho_pagina
        fim = inicio + tamanho_pagina - 1
        resp = query_factory(inicio, fim).execute()
        lote = resp.data or []
        linhas.extend(lote)
        if len(lote) < tamanho_pagina:
            break
        pagina += 1
    return linhas


def _buscar_por_lotes(supabase: Client, tabela: str, coluna_filtro: str, valores: list, colunas: str) -> list[dict]:
    """Filtra `coluna_filtro IN valores` em lotes de 1000 (limite do
    `.in_()`) E pagina o resultado de cada lote (`_paginar`) -- um lote de
    1000 match_id pode facilmente devolver mais de 1000 linhas (~22
    jogadores/partida), então as duas paginações são necessárias."""
    linhas: list[dict] = []
    for i in range(0, len(valores), 1000):
        lote = valores[i : i + 1000]
        linhas.extend(
            _paginar(
                lambda ini, fim, lote=lote: supabase.table(tabela).select(colunas).in_(coluna_filtro, lote).order(coluna_filtro).range(ini, fim)
            )
        )
    return linhas


def carregar_dados(supabase: Client) -> pd.DataFrame:
    logger.info("Buscando dados de lineup no Supabase...")
    lineup_rows = _paginar(
        lambda i, f: supabase.table("match_lineup_fotmob")
        .select("match_id, team_id, player_id, is_starter, position_id")
        .order("match_id")
        .range(i, f)
    )
    if not lineup_rows:
        return pd.DataFrame()
    df_lineup = pd.DataFrame(lineup_rows)
    df_lineup = df_lineup[df_lineup["player_id"].notna()].copy()

    match_ids = df_lineup["match_id"].unique().tolist()
    matches_resp = supabase.table("matches").select("id, match_date, home_team_id, away_team_id").in_("id", match_ids).execute()
    df_matches = pd.DataFrame(matches_resp.data).rename(columns={"id": "match_id"})

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
    df["posicao_num"] = df["position_id"].fillna(df["usual_position_id"]).fillna(0).astype(int)
    df["valor_mercado_eur"] = df["market_value"].fillna(100000)
    df["is_lesionado"] = 0  # sem histórico de lesão (só snapshot atual, ver player_availability_fotmob) -- treino não tem esse sinal, é 0 constante de propósito; ao vivo (rodar_xi_previsto.py) usa o snapshot real.
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


def treinar(df: pd.DataFrame, supabase: Client) -> dict:
    logger.info("Treinando modelos (LightGBM, XGBoost, CatBoost) para Previsão de XI...")
    treino, teste = _split_cronologico(df, FRACAO_TESTE)
    treino_inner, val = _split_cronologico(treino, 0.2)
    logger.info(f"Treino: {len(treino_inner)} | Validação: {len(val)} | Teste: {len(teste)} linhas")

    X_train, y_train = treino_inner[FEATURES], treino_inner[TARGET]
    X_val, y_val = val[FEATURES], val[TARGET]
    X_test, y_test = teste[FEATURES], teste[TARGET]

    peso_pos = (len(y_train) - sum(y_train)) / max(sum(y_train), 1)
    modelos = {
        "lightgbm": LGBMClassifier(n_estimators=300, max_depth=5, learning_rate=0.05, class_weight="balanced", random_state=42),
        "xgboost": XGBClassifier(n_estimators=300, max_depth=5, learning_rate=0.05, scale_pos_weight=peso_pos, random_state=42, eval_metric="logloss"),
        "catboost": CatBoostClassifier(iterations=300, depth=5, learning_rate=0.05, auto_class_weights="Balanced", random_seed=42, verbose=0),
    }

    resultado: dict[str, dict] = {}
    for nome, modelo in modelos.items():
        logger.info(f"Treinando {nome}...")
        if nome == "lightgbm":
            modelo.fit(X_train, y_train, eval_set=[(X_val, y_val)])
        elif nome == "xgboost":
            modelo.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=False)
        else:
            modelo.fit(X_train, y_train, eval_set=(X_val, y_val), verbose=False)

        y_pred = modelo.predict(X_test)
        y_proba = modelo.predict_proba(X_test)[:, 1]

        metricas = {
            "accuracy": float(accuracy_score(y_test, y_pred)),
            "log_loss": float(log_loss(y_test, y_proba)),
            "brier_score": float(brier_score_loss(y_test, y_proba)),
            "roc_auc": float(roc_auc_score(y_test, y_proba)),
            "f1": float(f1_score(y_test, y_pred)),
            **_metricas_top11(teste, y_proba),
        }
        logger.info(
            f"{nome} - Acc: {metricas['accuracy']:.4f} | AUC: {metricas['roc_auc']:.4f} | "
            f"Precisão top-11: {metricas['precisao_media_top11']:.4f} | XI exato: {metricas['taxa_xi_exato']:.4f}"
        )

        path = _salvar_artefato(supabase, nome, modelo)
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
