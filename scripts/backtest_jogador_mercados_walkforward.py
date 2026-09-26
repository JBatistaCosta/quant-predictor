"""Backtest walk-forward por temporada dos modelos de chutes/gols por
jogador -- mesmo espírito de `backtest_xi_walkforward.py`: pra cada
temporada com histórico suficiente ANTES dela, treina do zero só com dado
estritamente anterior e avalia nela, sem vazamento.

Responde 2 perguntas que o plano da sessão deixou em aberto pra decisão
empírica, não assumida:

1. **Chutes**: o modelo (CatBoost Poisson) bate o baseline ingênuo (EWMA
   crua do próprio jogador x minutos esperados) de forma sustentada,
   temporada a temporada -- não só no split único de
   `treinar_modelo_jogador_mercados.py`?
2. **Gols**: entre afinamento de Poisson (`lambda_gols = lambda_chutes_
   previsto x taxa_conversao_bayesiana`, sem treinar nada novo) e um
   regressor Poisson DIRETO de gols (mesmas features, alvo=gols_partida),
   qual bate o baseline com mais folga? Reportado lado a lado, sem
   declarar vencedor fixo no código -- cabe a quem for usar em produção
   olhar `player_market_backtest` e decidir.
3. **xG/xA** (mesmo tratamento dos dois -- regressor CatBoost RMSE puro,
   alvo contínuo): bate o baseline EWMA crua de forma sustentada (IC95%),
   temporada a temporada? xA nunca tinha passado por essa validação walk-
   forward antes (só existia o split único de `treinar_modelo_jogador_
   mercados.treinar()`) -- xG já era mercado nativo aqui.

Também roda uma segunda passada `fonte_titular='real'` (além da `'previsto'`
de sempre) nas partidas onde `match_lineup_fotmob` (escalação oficial
confirmada) já existe -- decisão revista nesta sessão: um comentário anterior
achava que essa comparação "não é replicável retroativamente sem re-simular
qual escalação teria saído", mas isso estava desatualizado -- a escalação
oficial de ~18 mil partidas históricas já está na base (backfill dedicado,
ver `arquivos_do_claude/ingestao_fotmob_lineup_backfill.py`, não só o cron ao
vivo), então não precisa simular nada: só reusa o mesmo modelo já treinado
pra temporada (nunca retreina) e troca `minutos_esperados` pela versão
determinística por papel confirmado (`minutos_esperados_real`, calculada em
`treinar_modelo_jogador_mercados.engenharia_features`), mesmo padrão de
produção (`rodar_jogador_mercados_previsto.py`). Só roda a passada 'real'
numa temporada quando há pelo menos 30 linhas elegíveis (mesmo piso mínimo já
usado nas agregações por liga) -- temporada/liga sem escalação histórica
capturada ainda simplesmente não gera essa passada, sem erro.

⚠️ 'previsto' e 'real' só têm linha pra quem ENTROU em campo (o dataset
nasce de match_player_stats_fotmob, minutos > 0). Servem pra avaliar o
jogador condicionado a ter jogado, mas NUNCA devem ser somadas por time: a
soma inclui os reservas que entraram, informação pós-jogo (CONTEXTO_PROJETO.md,
26/09). Pra agregado por time existe a terceira passada, 'relacionados' --
elenco relacionado inteiro (`xi_titular_walkforward`) com minutos esperados
= mistura por prob_titular, igual à fonte 'previsto' da produção (ver
`montar_candidatos_relacionados`).

Uso:
    SUPABASE_URL=... SUPABASE_KEY=... python3 backtest_jogador_mercados_walkforward.py
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, log_loss
from supabase import Client, create_client

import dados_historicos as dh
import modelos_ml
import treinar_modelo_jogador_mercados as tmj

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

MODEL_VERSION = "jogador_mercados_catboost_walkforward_v1"
# Mesmo racional de MIN_LINHAS_TREINO em backtest_xi_walkforward.py -- só
# tenta treinar uma temporada se já existe histórico minimamente informativo
# antes dela. Maior que o de XI (300) porque o alvo aqui é mais esparso
# (evento raro por jogador-partida, precisa de mais linhas pra estabilizar).
MIN_LINHAS_TREINO = 2000
N_REAMOSTRAGENS_BOOTSTRAP = 1000


# Passada 'relacionados' (26/09) -- a única que pode ser SOMADA por time.
# As passadas 'previsto'/'real' só têm linha pra quem ENTROU em campo (o
# dataset nasce de match_player_stats_fotmob com minutos > 0): somar o λ
# delas por (partida, time) inclui os reservas que entraram, informação
# pós-jogo -- time perdendo põe atacante, e a fração do λ vinda de reservas
# prevê o resultado além da Pinnacle de fechamento (CONTEXTO_PROJETO.md,
# 26/09). Aqui os candidatos são o elenco RELACIONADO (titulares + banco,
# `xi_titular_walkforward`, ~21 por time) com a prob_titular walk-forward, e
# os minutos esperados seguem a mistura da produção
# (`rodar_jogador_mercados_previsto.py`, fonte 'previsto'):
# p × minutos_como_titular + (1 − p) × minutos_como_reserva.
COLUNAS_HISTORICO_JOGADOR = [
    "chutes_90_bayesiano", "gols_90_bayesiano", "xg_90_bayesiano", "xa_90_bayesiano", "chutes_no_alvo_90_bayesiano",
    "ewma_chutes_90", "ewma_gols_90", "ewma_xg_90", "ewma_xa_90", "ewma_chutes_no_alvo_90",
    "taxa_conversao_bayesiana", "taxa_no_alvo_bayesiana", "posicao_num",
    "minutos_esperados_titular", "minutos_esperados_reserva",
]
COLUNAS_CONTEXTO_PARTIDA = ["league_id", "season", "liga", "match_date", "elo_diff", "squad_rating_diff", "mando"]
ALVOS_CONTAGEM = [tmj.TARGET_CHUTES, tmj.TARGET_GOLS, tmj.TARGET_CHUTES_NO_ALVO]


def montar_candidatos_relacionados(df: pd.DataFrame, xi: pd.DataFrame, match_ids: set) -> pd.DataFrame:
    """Uma linha por jogador RELACIONADO (`xi`: match_id, team_id, player_id,
    prob_titular) das partidas em `match_ids`, com as mesmas colunas que
    `_avaliar_e_persistir_passada` consome. `df` é o dataset de aparições já
    featurizado (`tmj.engenharia_features`), com TODAS as temporadas.

    Histórico do jogador (colunas de `COLUNAS_HISTORICO_JOGADOR`):
      * entrou nessa partida -> a própria linha (features já excluem a partida, shift(1));
      * não entrou -> a PRÓXIMA aparição dele depois da partida: as features
        dela usam só as aparições anteriores a ela, que são exatamente as
        anteriores a esta partida (ele não jogou nesta). Sem próxima
        aparição, a anterior (defasada em 1 jogo, nunca vaza);
      * nunca apareceu no dataset -> prior da liga (média das 1as aparições,
        n_hist = 0), posição desconhecida.
    Contexto (Elo, rating do elenco, mando, liga) vem do time na partida.
    Alvos de quem não entrou = 0 (xA só onde o time tem xA capturado)."""
    cand = xi[xi["match_id"].isin(match_ids)][["match_id", "team_id", "player_id", "prob_titular"]].copy()
    cand = cand.drop_duplicates(subset=["match_id", "team_id", "player_id"])
    contexto = df[df["match_id"].isin(match_ids)].groupby(["match_id", "team_id"], as_index=False)[COLUNAS_CONTEXTO_PARTIDA].first()
    cand = cand.merge(contexto, on=["match_id", "team_id"], how="inner")
    if cand.empty:
        return cand

    aparicoes = df[["match_id", "team_id", "player_id", "match_date", "dias_desde_ultimo_jogo", "xg_partida", "xa_partida",
                    *ALVOS_CONTAGEM, *COLUNAS_HISTORICO_JOGADOR]]
    cand = cand.merge(aparicoes.drop(columns=["match_date"]), on=["match_id", "team_id", "player_id"], how="left")
    entrou = cand[tmj.TARGET_CHUTES].notna()

    fora = cand.loc[~entrou, ["match_id", "team_id", "player_id", "match_date"]].reset_index()
    if not fora.empty:
        hist = df[["player_id", "match_date", *COLUNAS_HISTORICO_JOGADOR]].sort_values("match_date")
        fora = fora.sort_values("match_date")
        proxima = pd.merge_asof(fora, hist, on="match_date", by="player_id", direction="forward", allow_exact_matches=False)
        anterior = pd.merge_asof(fora, hist, on="match_date", by="player_id", direction="backward", allow_exact_matches=False)
        ultima_data = pd.merge_asof(
            fora, df[["player_id", "match_date"]].assign(data_ultima=df["match_date"]).sort_values("match_date"),
            on="match_date", by="player_id", direction="backward", allow_exact_matches=False,
        )
        proxima, anterior, ultima_data = (x.set_index("index").loc[fora["index"]] for x in (proxima, anterior, ultima_data))
        tem_proxima = proxima["chutes_90_bayesiano"].notna().to_numpy()[:, None]
        valores = pd.DataFrame(
            np.where(tem_proxima, proxima[COLUNAS_HISTORICO_JOGADOR].to_numpy(dtype=float), anterior[COLUNAS_HISTORICO_JOGADOR].to_numpy(dtype=float)),
            columns=COLUNAS_HISTORICO_JOGADOR,
        )

        estreia = df[df["n_hist"] == 0].groupby("liga")[COLUNAS_HISTORICO_JOGADOR].mean()
        sem_hist = valores["chutes_90_bayesiano"].isna().to_numpy()
        if sem_hist.any():
            ligas = cand.loc[fora["index"].to_numpy()[sem_hist], "liga"].to_numpy()
            valores.iloc[np.flatnonzero(sem_hist)] = estreia.reindex(ligas)[COLUNAS_HISTORICO_JOGADOR].to_numpy()
            valores.iloc[np.flatnonzero(sem_hist), valores.columns.get_loc("posicao_num")] = 0
        idx = fora["index"].to_numpy()
        cand.loc[idx, COLUNAS_HISTORICO_JOGADOR] = valores.to_numpy()
        dias = (fora["match_date"].reset_index(drop=True) - ultima_data["data_ultima"].reset_index(drop=True)).dt.days
        cand.loc[idx, "dias_desde_ultimo_jogo"] = dias.fillna(14).clip(upper=30).to_numpy()
        for alvo in ALVOS_CONTAGEM:
            cand.loc[idx, alvo] = 0
        cand.loc[idx, "xg_partida"] = 0.0
        tem_xa = cand.loc[entrou].groupby(["match_id", "team_id"])["xa_partida"].apply(lambda s: s.notna().any())
        chave = pd.MultiIndex.from_frame(cand.loc[idx, ["match_id", "team_id"]])
        cand.loc[idx, "xa_partida"] = np.where(tem_xa.reindex(chave).fillna(False).to_numpy(), 0.0, np.nan)

    p = cand["prob_titular"].clip(0, 1)
    cand["minutos_esperados"] = p * cand["minutos_esperados_titular"] + (1 - p) * cand["minutos_esperados_reserva"]
    cand["posicao_num"] = cand["posicao_num"].astype(int)
    for alvo in ALVOS_CONTAGEM:
        cand[alvo] = cand[alvo].astype(int)
    cand["entrou_em_campo"] = entrou.to_numpy()
    return cand.reset_index(drop=True)


def _rmse(previsto: np.ndarray, real: np.ndarray) -> float:
    return float(np.sqrt(np.mean((previsto - real) ** 2)))


def _ic95_bootstrap_diferenca(erro_baseline: np.ndarray, erro_modelo: np.ndarray) -> tuple[float, float, float]:
    """IC95% via bootstrap da diferença (baseline - modelo) de erro
    ABSOLUTO por linha -- positivo = modelo erra menos que o baseline em
    média. Mesmo espírito (reamostragem com reposição, 2000-ish
    repetições) já usado no projeto pra decidir "edge real vs. ruído de
    amostra pequena" em `api/backtest-betting.js`/`avaliar_ic_modelos_por_
    liga.py` -- aqui aplicado a erro de regressão em vez de log-loss de
    aposta, mesmo princípio estatístico."""
    diffs = erro_baseline - erro_modelo
    n = len(diffs)
    if n < 30:
        return float(diffs.mean()), float("nan"), float("nan")
    rng = np.random.default_rng(42)
    medias_boot = np.array([
        diffs[rng.integers(0, n, size=n)].mean() for _ in range(N_REAMOSTRAGENS_BOOTSTRAP)
    ])
    return float(diffs.mean()), float(np.percentile(medias_boot, 2.5)), float(np.percentile(medias_boot, 97.5))


def _metricas_regressao_com_ic(previsto: np.ndarray, baseline: np.ndarray, real: np.ndarray) -> dict:
    erro_modelo = np.abs(previsto - real)
    erro_baseline = np.abs(baseline - real)
    diff_media, ic_inf, ic_sup = _ic95_bootstrap_diferenca(erro_baseline, erro_modelo)
    return {
        "rmse_modelo": _rmse(previsto, real),
        "rmse_baseline": _rmse(baseline, real),
        "diff_erro_absoluto_medio": diff_media,
        "ic95_inf": ic_inf,
        "ic95_sup": ic_sup,
        # Só "sustentado" quando o IC95% inteiro fica ACIMA de zero -- mesma
        # leitura conservadora já documentada no projeto (IC que cruza zero
        # não é edge comprovado, mesmo com média pontual positiva).
        "modelo_melhor_sustentado": bool(ic_inf > 0) if not np.isnan(ic_inf) else False,
        "n": int(len(real)),
    }


def _metricas_probabilidade_marcar(lambda_gols: np.ndarray, real_marcou: np.ndarray, baseline_lambda: np.ndarray) -> dict:
    """Log-loss/Brier/calibração em quintis de P(marcou>=1) = 1 - exp(-lambda),
    contra o baseline "chute inicial" (mesma fórmula, com o lambda do
    baseline). Evento raro (~9% marca em qualquer partida, medido antes de
    escrever este script) -- log-loss é a métrica que importa de verdade,
    acurácia seria dominada pelo desbalanceamento."""
    p_modelo = 1 - np.exp(-np.clip(lambda_gols, 1e-6, None))
    p_baseline = 1 - np.exp(-np.clip(baseline_lambda, 1e-6, None))
    ordenado = np.argsort(p_modelo)
    calibracao = []
    tamanho = len(ordenado) // 5
    if tamanho > 0:
        for i in range(5):
            idx = ordenado[i * tamanho: len(ordenado) if i == 4 else (i + 1) * tamanho]
            if len(idx) == 0:
                continue
            calibracao.append({
                "previsto_medio": float(p_modelo[idx].mean()),
                "real": float(real_marcou[idx].mean()),
                "n": int(len(idx)),
            })
    return {
        "log_loss_modelo": float(log_loss(real_marcou, p_modelo, labels=[0, 1])),
        "log_loss_baseline": float(log_loss(real_marcou, p_baseline, labels=[0, 1])),
        "brier_modelo": float(brier_score_loss(real_marcou, p_modelo)),
        "brier_baseline": float(brier_score_loss(real_marcou, p_baseline)),
        "calibracao": calibracao,
        "n": int(len(real_marcou)),
    }


def _persistir_previsao_bruta(
    supabase: Client, teste: pd.DataFrame, previsto_chutes: np.ndarray, lambda_gols_thinning: np.ndarray,
    lambda_gols_direto: np.ndarray, temporada: str, lambda_xg: dict[int, float] | None = None,
    lambda_chutes_no_alvo: np.ndarray | None = None, lambda_xa: dict[int, float] | None = None,
    fonte_titular: str = "previsto",
) -> int:
    """Grava a previsão bruta (1 linha por jogador x partida x fonte_titular)
    em `player_match_walkforward`. `fonte_titular` é 'previsto' (minutos_
    esperados = média histórica incondicional), 'real' (minutos_esperados
    determinístico por papel confirmado em `match_lineup_fotmob`) ou
    'relacionados' (elenco relacionado, mistura por prob_titular -- ver
    docstring do módulo); em todos os casos `teste["minutos_esperados"]` já
    vem com o valor certo pra essa passada (o CALLER decide qual coluna usar,
    esta função só grava).

    `lambda_xg`/`lambda_xa` são dicts indexados por posição em `teste` (não
    um array alinhado 1:1 como os demais) porque os subconjuntos de xG/xA são
    filtrados por `dropna(subset=[TARGET_XG/TARGET_XA])` separadamente de
    `teste` -- linhas diferentes podem ter xg_partida/xa_partida nulo (ver
    docstring do módulo). Ausente do dict = xG/xA não avaliado pra essa linha
    (fica None, não 0.0 -- não inventar previsão pra linha que não passou
    pelo modelo). `lambda_chutes_no_alvo` já vem alinhado 1:1 com `teste`
    (chutes_no_alvo_partida nunca é nulo, mesmo tratamento de
    chutes_partida -- não precisa do dict)."""
    lambda_xg = lambda_xg or {}
    lambda_xa = lambda_xa or {}
    if lambda_chutes_no_alvo is None:
        lambda_chutes_no_alvo = [None] * len(teste)
    probs_titular = teste["prob_titular"] if "prob_titular" in teste.columns else [None] * len(teste)
    linhas = []
    for idx, match_id, team_id, player_id, league_id, minutos_esp, taxa_conv, prev_chutes, lam_gols_thin, lam_gols_dir, lam_no_alvo, prob_tit in zip(
        teste.index, teste["match_id"], teste["team_id"], teste["player_id"], teste["league_id"], teste["minutos_esperados"],
        teste["taxa_conversao_bayesiana"], previsto_chutes, lambda_gols_thinning, lambda_gols_direto, lambda_chutes_no_alvo,
        probs_titular, strict=True,
    ):
        lam_xg = lambda_xg.get(idx)
        lam_xa = lambda_xa.get(idx)
        linhas.append({
            "match_id": int(match_id), "team_id": int(team_id), "player_id": int(player_id),
            "fonte_titular": fonte_titular, "prob_titular_usada": float(prob_tit) if prob_tit is not None else None,
            "minutos_esperados": float(minutos_esp),
            "taxa_conversao_bayesiana": float(taxa_conv), "lambda_chutes_jogo": float(prev_chutes),
            "lambda_gols_jogo_thinning": float(lam_gols_thin), "lambda_gols_jogo_direto": float(lam_gols_dir),
            "lambda_xg_jogo": float(lam_xg) if lam_xg is not None else None,
            "lambda_chutes_no_alvo_jogo": float(lam_no_alvo) if lam_no_alvo is not None else None,
            "lambda_xa_jogo": float(lam_xa) if lam_xa is not None else None,
            "season": str(temporada), "league_id": int(league_id), "model_version": MODEL_VERSION,
        })
    total = 0
    for lote in dh._dividir_em_lotes(linhas, 500):
        supabase.table("player_match_walkforward").upsert(
            lote, on_conflict="match_id,team_id,player_id,model_version,fonte_titular"
        ).execute()
        total += len(lote)
    return total


def _avaliar_e_persistir_passada(
    supabase: Client, teste_pass: pd.DataFrame, temporada: str,
    modelo_chutes, modelo_gols_direto, modelo_xg, modelo_xa, fonte_titular: str,
) -> list[dict]:
    """Pontua `teste_pass` com os modelos JÁ TREINADOS pra essa temporada
    (nunca retreina -- treino/teste dependem só de `match_date`, não de
    `fonte_titular`) e persiste em `player_match_walkforward`/agrega pra
    `player_market_backtest`. `teste_pass["minutos_esperados"]` já deve vir
    com o valor certo pra essa passada (o CALLER decide: incondicional pra
    'previsto', `minutos_esperados_real` pra 'real' -- ver `rodar`).

    Mesma lógica/ordem de cálculo já usada antes desta função existir (ver
    histórico do módulo) -- só extraída pra rodar 2x (previsto e real) sem
    duplicar ~80 linhas."""
    previsto_chutes = modelos_ml.prever_catboost_poisson(modelo_chutes, None, teste_pass, features=tmj.FEATURES_CHUTES)
    baseline_chutes = (teste_pass["ewma_chutes_90"] * teste_pass["minutos_esperados"] / 90.0).clip(lower=0.01).to_numpy()
    real_chutes = teste_pass[tmj.TARGET_CHUTES].to_numpy()
    metricas_chutes = _metricas_regressao_com_ic(previsto_chutes, baseline_chutes, real_chutes)
    logger.info(
        f"  [{fonte_titular}] chutes: RMSE modelo={metricas_chutes['rmse_modelo']:.4f} baseline={metricas_chutes['rmse_baseline']:.4f} "
        f"IC95%(dif)=[{metricas_chutes['ic95_inf']:.4f},{metricas_chutes['ic95_sup']:.4f}] "
        f"sustentado={metricas_chutes['modelo_melhor_sustentado']} n={metricas_chutes['n']}"
    )

    previsto_gols_direto = modelos_ml.prever_catboost_poisson(modelo_gols_direto, None, teste_pass, features=tmj.FEATURES_CHUTES)

    taxa_conversao = teste_pass["taxa_conversao_bayesiana"].to_numpy()
    lambda_gols_thinning = previsto_chutes * taxa_conversao
    real_marcou = (teste_pass[tmj.TARGET_GOLS].to_numpy() > 0).astype(int)
    baseline_lambda_gols = (teste_pass["ewma_gols_90"] * teste_pass["minutos_esperados"] / 90.0).clip(lower=0.001).to_numpy()

    metricas_gols_thinning = _metricas_probabilidade_marcar(lambda_gols_thinning, real_marcou, baseline_lambda_gols)
    metricas_gols_direto = _metricas_probabilidade_marcar(previsto_gols_direto, real_marcou, baseline_lambda_gols)
    logger.info(
        f"  [{fonte_titular}] gols (marcar>=1): thinning log-loss={metricas_gols_thinning['log_loss_modelo']:.4f} vs. "
        f"direto log-loss={metricas_gols_direto['log_loss_modelo']:.4f} vs. baseline={metricas_gols_thinning['log_loss_baseline']:.4f} "
        f"-- {'thinning melhor' if metricas_gols_thinning['log_loss_modelo'] < metricas_gols_direto['log_loss_modelo'] else 'direto melhor'}"
    )

    # Chutes ao gol (on target -- ver docstring de treinar_modelo_jogador_
    # mercados.carregar_dados) -- mesmo afinamento de Poisson de gols_
    # thinning (lambda_chutes x taxa_no_alvo_bayesiana), mas avaliado como
    # CONTAGEM (RMSE, mesmo tratamento de "chutes") já que não é evento raro
    # binário como "marcou".
    taxa_no_alvo = teste_pass["taxa_no_alvo_bayesiana"].to_numpy()
    lambda_chutes_no_alvo_thinning = previsto_chutes * taxa_no_alvo
    real_chutes_no_alvo = teste_pass[tmj.TARGET_CHUTES_NO_ALVO].to_numpy()
    baseline_chutes_no_alvo = (
        teste_pass["ewma_chutes_no_alvo_90"] * teste_pass["minutos_esperados"] / 90.0
    ).clip(lower=0.01).to_numpy()
    metricas_chutes_no_alvo = _metricas_regressao_com_ic(lambda_chutes_no_alvo_thinning, baseline_chutes_no_alvo, real_chutes_no_alvo)
    logger.info(
        f"  [{fonte_titular}] chutes_no_alvo (thinning): RMSE modelo={metricas_chutes_no_alvo['rmse_modelo']:.4f} "
        f"baseline={metricas_chutes_no_alvo['rmse_baseline']:.4f} "
        f"IC95%(dif)=[{metricas_chutes_no_alvo['ic95_inf']:.4f},{metricas_chutes_no_alvo['ic95_sup']:.4f}] "
        f"sustentado={metricas_chutes_no_alvo['modelo_melhor_sustentado']}"
    )

    # xG por jogador -- alvo CONTÍNUO. `teste_xg` pode ficar vazio (sobretudo
    # na passada 'real', que já é um subconjunto menor de teste_pass) --
    # nesse caso pula xG pra essa passada/temporada sem erro, em vez de
    # chamar o modelo com um dataframe vazio.
    teste_xg = teste_pass.dropna(subset=[tmj.TARGET_XG])
    lambda_xg_por_indice: dict[int, float] = {}
    metricas_xg = None
    previsto_xg = baseline_xg = real_xg = None
    if not teste_xg.empty:
        previsto_xg = np.maximum(modelos_ml.prever_catboost_regressor(modelo_xg, None, teste_xg, features=tmj.FEATURES_XG), 0.0)
        baseline_xg = (teste_xg["ewma_xg_90"] * teste_xg["minutos_esperados"] / 90.0).clip(lower=0.0).to_numpy()
        real_xg = teste_xg[tmj.TARGET_XG].to_numpy()
        metricas_xg = _metricas_regressao_com_ic(previsto_xg, baseline_xg, real_xg)
        logger.info(
            f"  [{fonte_titular}] xg: RMSE modelo={metricas_xg['rmse_modelo']:.4f} baseline={metricas_xg['rmse_baseline']:.4f} "
            f"IC95%(dif)=[{metricas_xg['ic95_inf']:.4f},{metricas_xg['ic95_sup']:.4f}] "
            f"sustentado={metricas_xg['modelo_melhor_sustentado']}"
        )
        lambda_xg_por_indice = dict(zip(teste_xg.index, previsto_xg, strict=True))

    # xA por jogador -- mesmo tratamento de xG (alvo contínuo, RMSE puro,
    # dropna próprio já que xa_partida pode ser nulo independente de xg).
    teste_xa = teste_pass.dropna(subset=[tmj.TARGET_XA])
    lambda_xa_por_indice: dict[int, float] = {}
    metricas_xa = None
    previsto_xa = baseline_xa = real_xa = None
    if not teste_xa.empty:
        previsto_xa = tmj._prever_catboost_regressor_nao_negativo(modelo_xa, None, teste_xa, features=tmj.FEATURES_XA)
        baseline_xa = (teste_xa["ewma_xa_90"] * teste_xa["minutos_esperados"] / 90.0).clip(lower=0.0).to_numpy()
        real_xa = teste_xa[tmj.TARGET_XA].to_numpy()
        metricas_xa = _metricas_regressao_com_ic(previsto_xa, baseline_xa, real_xa)
        logger.info(
            f"  [{fonte_titular}] xa: RMSE modelo={metricas_xa['rmse_modelo']:.4f} baseline={metricas_xa['rmse_baseline']:.4f} "
            f"IC95%(dif)=[{metricas_xa['ic95_inf']:.4f},{metricas_xa['ic95_sup']:.4f}] "
            f"sustentado={metricas_xa['modelo_melhor_sustentado']}"
        )
        lambda_xa_por_indice = dict(zip(teste_xa.index, previsto_xa, strict=True))

    n_gravado = _persistir_previsao_bruta(
        supabase, teste_pass, previsto_chutes, lambda_gols_thinning, previsto_gols_direto, temporada,
        lambda_xg=lambda_xg_por_indice, lambda_chutes_no_alvo=lambda_chutes_no_alvo_thinning,
        lambda_xa=lambda_xa_por_indice, fonte_titular=fonte_titular,
    )
    logger.info(f"  [{fonte_titular}] {n_gravado} previsões por jogador gravadas em player_match_walkforward.")

    linhas_agregado = []
    for league_id, g in teste_pass.groupby("league_id"):
        mask = (teste_pass["league_id"] == league_id).to_numpy()
        if mask.sum() < 30:
            continue
        m_chutes = _metricas_regressao_com_ic(previsto_chutes[mask], baseline_chutes[mask], real_chutes[mask])
        m_gols_thin = _metricas_probabilidade_marcar(lambda_gols_thinning[mask], real_marcou[mask], baseline_lambda_gols[mask])
        m_gols_dir = _metricas_probabilidade_marcar(previsto_gols_direto[mask], real_marcou[mask], baseline_lambda_gols[mask])
        m_chutes_no_alvo = _metricas_regressao_com_ic(
            lambda_chutes_no_alvo_thinning[mask], baseline_chutes_no_alvo[mask], real_chutes_no_alvo[mask]
        )

        mercados_da_liga = [
            ("chutes", m_chutes),
            ("gols_thinning", m_gols_thin),
            ("gols_direto", m_gols_dir),
            ("chutes_no_alvo_thinning", m_chutes_no_alvo),
        ]
        if metricas_xg is not None:
            mask_xg = (teste_xg["league_id"] == league_id).to_numpy()
            if mask_xg.sum() >= 30:
                m_xg = _metricas_regressao_com_ic(previsto_xg[mask_xg], baseline_xg[mask_xg], real_xg[mask_xg])
                mercados_da_liga.append(("xg", m_xg))
        if metricas_xa is not None:
            mask_xa = (teste_xa["league_id"] == league_id).to_numpy()
            if mask_xa.sum() >= 30:
                m_xa = _metricas_regressao_com_ic(previsto_xa[mask_xa], baseline_xa[mask_xa], real_xa[mask_xa])
                mercados_da_liga.append(("xa", m_xa))
        for mercado, metricas in mercados_da_liga:
            linhas_agregado.append({
                "season": str(temporada), "league_id": int(league_id), "model_version": MODEL_VERSION,
                "mercado": mercado, "fonte_titular": fonte_titular,
                "n_partidas": int(g["match_id"].nunique()), "n_previsoes": metricas["n"],
                "rmse_modelo": metricas.get("rmse_modelo"), "rmse_baseline": metricas.get("rmse_baseline"),
                "log_loss": metricas.get("log_loss_modelo"), "brier": metricas.get("brier_modelo"),
                "calibracao": metricas.get("calibracao"),
            })

    return linhas_agregado


def rodar(supabase: Client) -> int:
    logger.info("Carregando dataset (12 ligas, corte temporal por liga, shotmap confirmado)...")
    df_bruto = tmj.carregar_dados(supabase)
    if df_bruto.empty:
        logger.warning("Dataset vazio -- nada pra fazer backtest.")
        return 0
    df = tmj.engenharia_features(df_bruto)
    if df.empty:
        logger.warning("Nenhuma linha após engenharia de features -- nada pra fazer backtest.")
        return 0

    n_com_lineup_real = int(df["is_starter_real"].notna().sum())
    logger.info(
        f"{n_com_lineup_real} de {len(df)} linhas têm escalação oficial confirmada "
        f"(match_lineup_fotmob) -- essas também rodam a passada 'real'."
    )

    logger.info("Carregando elenco relacionado com prob_titular walk-forward (xi_titular_walkforward)...")
    xi = pd.DataFrame(tmj._buscar_por_lotes(
        supabase, "xi_titular_walkforward", "match_id", sorted(df["match_id"].unique().tolist()),
        "match_id, team_id, player_id, prob_titular",
    ))
    if xi.empty:
        xi = pd.DataFrame(columns=["match_id", "team_id", "player_id", "prob_titular"])
    xi[["match_id", "team_id", "player_id"]] = xi[["match_id", "team_id", "player_id"]].astype(int)
    xi["prob_titular"] = xi["prob_titular"].astype(float)
    logger.info(f"{len(xi)} relacionados em {xi['match_id'].nunique()} partidas -- alimentam a passada 'relacionados'.")

    temporadas = sorted(df["season"].unique())
    logger.info(f"Temporadas encontradas: {temporadas}")

    total_gravado = 0
    for temporada in temporadas:
        teste_temporada = df[df["season"] == temporada]
        data_inicio = teste_temporada["match_date"].min()
        treino = df[df["match_date"] < data_inicio]

        if len(treino) < MIN_LINHAS_TREINO:
            logger.info(f"Temporada {temporada}: só {len(treino)} linhas de treino antes dela (< {MIN_LINHAS_TREINO}) -- pulando.")
            continue

        logger.info(f"Temporada {temporada}: treinando com {len(treino)} linhas, avaliando {len(teste_temporada)} linhas...")

        params = {"depth": 6, "learning_rate": 0.05}
        modelo_chutes, _, _ = modelos_ml.treinar_catboost_poisson(params, treino, tmj.TARGET_CHUTES, features=tmj.FEATURES_CHUTES)
        modelo_gols_direto, _, _ = modelos_ml.treinar_catboost_poisson(params, treino, tmj.TARGET_GOLS, features=tmj.FEATURES_CHUTES)
        treino_xg = treino.dropna(subset=[tmj.TARGET_XG])
        modelo_xg, _, _ = modelos_ml.treinar_catboost_regressor(params, treino_xg, tmj.TARGET_XG, features=tmj.FEATURES_XG)
        treino_xa = treino.dropna(subset=[tmj.TARGET_XA])
        modelo_xa, _, _ = modelos_ml.treinar_catboost_regressor(params, treino_xa, tmj.TARGET_XA, features=tmj.FEATURES_XA)

        linhas_agregado = _avaliar_e_persistir_passada(
            supabase, teste_temporada, temporada, modelo_chutes, modelo_gols_direto, modelo_xg, modelo_xa, "previsto",
        )

        # Passada 'real' -- só nas linhas onde a escalação oficial daquela
        # partida específica já é conhecida (ver docstring do módulo), com
        # minutos_esperados trocado pela versão determinística por papel
        # confirmado. Reusa os MESMOS modelos já treinados acima (não
        # retreina -- treino não depende de fonte_titular). Piso de 30 linhas
        # (mesmo mínimo já usado nas agregações por liga) evita gravar uma
        # passada 'real' com amostra irrelevante numa temporada onde a
        # escalação histórica ainda não foi capturada.
        teste_real = teste_temporada[teste_temporada["is_starter_real"].notna()].copy()
        if len(teste_real) >= 30:
            teste_real["minutos_esperados"] = teste_real["minutos_esperados_real"]
            linhas_agregado += _avaliar_e_persistir_passada(
                supabase, teste_real, temporada, modelo_chutes, modelo_gols_direto, modelo_xg, modelo_xa, "real",
            )
        else:
            logger.info(
                f"Temporada {temporada}: só {len(teste_real)} linhas com escalação real confirmada (<30) -- pulando passada 'real'."
            )

        # Passada 'relacionados' -- a única somável por time (ver
        # montar_candidatos_relacionados). Mesmos modelos, sem retreino.
        teste_rel = montar_candidatos_relacionados(df, xi, set(teste_temporada["match_id"].unique()))
        if len(teste_rel) >= 30:
            logger.info(
                f"Temporada {temporada}: {len(teste_rel)} relacionados em {teste_rel['match_id'].nunique()} partidas "
                f"({(~teste_rel['entrou_em_campo']).mean():.0%} não entraram em campo)."
            )
            linhas_agregado += _avaliar_e_persistir_passada(
                supabase, teste_rel, temporada, modelo_chutes, modelo_gols_direto, modelo_xg, modelo_xa, "relacionados",
            )
        else:
            logger.info(f"Temporada {temporada}: só {len(teste_rel)} relacionados com prob_titular (<30) -- pulando passada 'relacionados'.")

        if linhas_agregado:
            supabase.table("player_market_backtest").upsert(
                linhas_agregado, on_conflict="season,league_id,model_version,mercado,fonte_titular"
            ).execute()
            total_gravado += len(linhas_agregado)
            logger.info(f"  {len(linhas_agregado)} grupos (liga x mercado x fonte) gravados em player_market_backtest.")

    logger.info(f"Backtest concluído: {total_gravado} grupos gravados em player_market_backtest.")
    return total_gravado


if __name__ == "__main__":
    import os

    url = os.environ["SUPABASE_URL"].strip()
    key = os.environ["SUPABASE_KEY"].strip()
    sb = create_client(url, key)
    rodar(sb)
