"""
Estatísticas esperadas por partida (xG, chutes, chutes no gol, escanteios)
com a mesma estrutura do Dixon-Coles: ataque/defesa por time + mando +
decaimento temporal.

Para cada estatística s:
    E[stat mandante] = exp(ataque_s[i] + defesa_s[j] + mando_s)
    E[stat visitante] = exp(ataque_s[j] + defesa_s[i])
O xGA esperado de um time é o xG esperado do adversário no mesmo jogo.

Saídas:
    - model_stat_estimates: valores esperados de cada stat por jogo de teste
    - model_predictions: over/under de escanteios (9.5) derivado via Poisson

PRÉ-REQUISITO: match_stats populada (rode ingestao_stats_fbref.py antes).

Uso:
    pip install numpy scipy pandas supabase
    set SUPABASE_KEY=sua_service_role_key
    python modelo_stats_esperadas.py
"""

import os
import sys

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import nbinom, poisson

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

TEMPORADAS_TREINO_POR_LIGA = {
    "BSA": ["2023", "2024"],
    "PL": ["2022", "2023", "2024"],
    "PD": ["2022", "2023", "2024"],
    "SA": ["2022", "2023", "2024"],
    "BL1": ["2022", "2023", "2024"],
    "FL1": ["2022", "2023", "2024"],
}
TEMPORADA_TESTE = "2025"
LIGAS = ["BSA", "PL", "PD", "SA", "BL1", "FL1"]

XI = 0.0018
MODEL_NAME = "stats_glm_v1"

# Estatísticas a modelar. Todas positivas, modeladas com verossimilhança
# Poisson (exata para contagens; quase-verossimilhança válida para xG contínuo).
STATS = ["xg", "shots", "shots_on_target", "corners"]

# Linha de escanteios para o mercado over/under derivado
LINHA_ESCANTEIOS = 9.5

# Linhas de chutes/chutes no gol (TOTAL da partida, mandante+visitante) --
# medianas reais medidas via SQL nesta sessão (~25 chutes, ~9 no gol por
# partida), mesmas linhas de `LINHAS_PADRAO_POR_STAT` em `api/corners-
# model.js` (JS, duplicado aqui de propósito -- Python e JS já não
# compartilham módulo neste projeto, mesmo padrão do resto do código).
LINHAS_POR_STAT = {
    "shots": [20.5, 22.5, 24.5, 26.5],
    "shots_on_target": [7.5, 8.5, 9.5, 10.5],
}

# Tradução do nome da stat (inglês, usado aqui e em `model_stat_estimates`)
# pro nome usado em `league_model_params.stat` (português, calibrado por
# `arquivos_do_claude/calibrar_disp_r_chutes*.py`) -- mesma convenção
# divergente já documentada em `api/corners-model.js`.
STAT_LEAGUE_PARAMS_LABEL = {"shots": "chutes", "shots_on_target": "chutes_no_alvo"}


# ---------------------------------------------------------------
# Núcleo (independente do banco — testável isoladamente)
# ---------------------------------------------------------------
def ajustar_stat(df: pd.DataFrame):
    """Ajusta o modelo multiplicativo para UMA estatística.

    df precisa de: home, away, val_home, val_away, weight.
    Retorna dict {times, ataque, defesa, mando, convergiu}.
    """
    times = sorted(set(df["home"]) | set(df["away"]))
    n = len(times)
    idx = {t: i for i, t in enumerate(times)}

    h = df["home"].map(idx).to_numpy()
    a = df["away"].map(idx).to_numpy()
    vh = df["val_home"].to_numpy(dtype=float)
    va = df["val_away"].to_numpy(dtype=float)
    w = df["weight"].to_numpy(dtype=float)

    def neg_log_lik(params):
        atk = params[:n] - params[:n].mean()
        dfn = params[n : 2 * n]
        mando = params[-1]
        lam = np.exp(atk[h] + dfn[a] + mando)   # esperado do mandante
        mu = np.exp(atk[a] + dfn[h])            # esperado do visitante
        # Deviance de Poisson (aceita valores contínuos como xG)
        ll = vh * np.log(lam) - lam + va * np.log(mu) - mu
        return -np.sum(w * ll)

    x0 = np.concatenate([np.zeros(2 * n), [0.1]])
    bounds = [(-3, 3)] * (2 * n) + [(-1, 1)]
    res = minimize(neg_log_lik, x0, method="L-BFGS-B", bounds=bounds)

    atk = res.x[:n] - res.x[:n].mean()
    return {
        "times": times,
        "ataque": dict(zip(times, atk)),
        "defesa": dict(zip(times, res.x[n : 2 * n])),
        "mando": res.x[-1],
        "convergiu": res.success,
    }


def prever_stat(modelo, mandante, visitante):
    """Valores esperados (mandante, visitante) de uma stat num confronto."""
    lam = np.exp(modelo["ataque"][mandante] + modelo["defesa"][visitante] + modelo["mando"])
    mu = np.exp(modelo["ataque"][visitante] + modelo["defesa"][mandante])
    return lam, mu


def prob_over(total_esperado: float, linha: float) -> float:
    """P(total > linha) assumindo total ~ Poisson(total_esperado)."""
    return float(1 - poisson.cdf(int(np.floor(linha)), total_esperado))


def prob_over_nb(total_esperado: float, disp_r: float, linha: float) -> float:
    """P(total > linha) via Binomial Negativa (NB2: var = média + média²/r) --
    diferente de `prob_over` (Poisson), usada pra chutes/chutes no gol
    (superdispersos, mesmo raciocínio já validado pra escanteios em
    `api/corners-model.js`). `disp_r` None/não-finito degenera pra Poisson
    (r->infinito), mesmo comportamento de `scripts/distribuicoes.py::
    _nb_pmf_vetor`.

    Escanteios (`prob_over`, Poisson) É uma inconsistência conhecida com o
    resto do projeto (o endpoint ao vivo `api/corners-model.js` já usa NB
    pra escanteios há mais tempo) -- deliberadamente NÃO corrigida aqui
    (fora do escopo desta extensão de chutes, documentar sem misturar)."""
    if disp_r is None or not np.isfinite(disp_r) or disp_r <= 0:
        return prob_over(total_esperado, linha)
    p = disp_r / (disp_r + total_esperado)
    return float(1 - nbinom.cdf(int(np.floor(linha)), disp_r, p))


def disp_r_da_liga(supabase, liga_id, stat_label: str) -> float | None:
    """`league_model_params.param_value` (disp_r) pra essa liga+stat, ou
    None se a liga não tem calibração própria (cai pro Poisson via
    `prob_over_nb`, mesmo fallback documentado ali). `.limit(1)` + checar a
    lista (não `.maybe_single()`) -- mesmo estilo já usado no resto deste
    arquivo (`carregar_dados`), sem depender de um método do client não
    usado em nenhum outro lugar do projeto."""
    if liga_id is None:
        return None
    linhas = (
        supabase.table("league_model_params")
        .select("param_value")
        .eq("league_id", liga_id).eq("stat", stat_label).eq("param_name", "disp_r")
        .limit(1).execute().data
    )
    if not linhas or linhas[0].get("param_value") is None:
        return None
    return float(linhas[0]["param_value"])


# ---------------------------------------------------------------
# Pipeline com o banco
# ---------------------------------------------------------------
def carregar_dados(supabase, liga_ext_id):
    """Partidas + stats por time, no formato largo (val_home / val_away).
    Devolve (df, liga_id) -- `liga_id` exposto pra `rodar_liga` poder buscar
    `disp_r` calibrado por liga (chutes/chutes no gol)."""
    liga = supabase.table("leagues").select("id").eq("external_id", liga_ext_id).execute().data
    if not liga:
        return pd.DataFrame(), None
    liga_id = liga[0]["id"]

    jogos, inicio = [], 0
    while True:
        lote = (supabase.table("matches")
                .select("id, season, match_date, home_team_id, away_team_id")
                .eq("league_id", liga_id).eq("status", "finished")
                .range(inicio, inicio + 999).execute().data)
        jogos.extend(lote)
        if len(lote) < 1000:
            break
        inicio += 1000
    if not jogos:
        return pd.DataFrame(), liga_id
    dfj = pd.DataFrame(jogos).rename(columns={"home_team_id": "home", "away_team_id": "away"})

    stats, inicio = [], 0
    ids = [j["id"] for j in jogos]
    for k in range(0, len(ids), 200):  # in_() tem limite de tamanho de URL
        lote = (supabase.table("match_stats")
                .select("match_id, team_id, " + ", ".join(STATS))
                .in_("match_id", ids[k : k + 200]).execute().data)
        stats.extend(lote)
    if not stats:
        return pd.DataFrame(), liga_id
    dfs = pd.DataFrame(stats)
    # match_stats pode ter linha duplicada pro mesmo (match_id, team_id) --
    # sem isso o merge many-to-one abaixo multiplica linhas e desalinha o
    # `.to_numpy()` direto (ValueError de tamanho).
    dfs = dfs.drop_duplicates(subset=["match_id", "team_id"], keep="last")

    # largo: uma linha por jogo com val_home / val_away para cada stat
    df = dfj.copy()
    for lado, col_time in [("home", "home"), ("away", "away")]:
        m = df.merge(dfs, left_on=["id", col_time], right_on=["match_id", "team_id"], how="left")
        for s in STATS:
            df[f"{s}_{lado}"] = m[s].to_numpy()

    df["match_date"] = pd.to_datetime(df["match_date"], utc=True)
    return df, liga_id


def rodar_liga(supabase, liga_ext_id):
    df, liga_id = carregar_dados(supabase, liga_ext_id)
    if df.empty:
        print(f"\n[{liga_ext_id}] sem estatísticas em match_stats — rode a ingestão do FBref antes.")
        return

    treino = df[df["season"].isin(TEMPORADAS_TREINO_POR_LIGA[liga_ext_id])].copy()
    teste = df[df["season"] == TEMPORADA_TESTE].copy()
    if treino.empty or teste.empty:
        print(f"\n[{liga_ext_id}] treino ou teste vazios — pulando.")
        return

    ref = treino["match_date"].max()
    treino["weight"] = np.exp(-XI * (ref - treino["match_date"]).dt.days)

    print(f"\n[{liga_ext_id}] treino: {len(treino)} jogos | teste: {len(teste)} jogos")

    modelos = {}
    for s in STATS:
        sub = treino.dropna(subset=[f"{s}_home", f"{s}_away"]).rename(
            columns={f"{s}_home": "val_home", f"{s}_away": "val_away"})
        if len(sub) < 50:
            print(f"  {s}: só {len(sub)} jogos com dado — pulando esta stat")
            continue
        m = ajustar_stat(sub[["home", "away", "val_home", "val_away", "weight"]])
        modelos[s] = m
        print(f"  {s}: ajustado ({len(sub)} jogos, mando {m['mando']:+.3f}, "
              f"{'ok' if m['convergiu'] else 'NÃO CONVERGIU'})")

    # disp_r por liga pra chutes/chutes no gol -- buscado UMA vez (não por
    # jogo), mesmo padrão de `dispRDaLiga` em `api/corners-model.js`. `None`
    # quando a liga não tem calibração própria (`arquivos_do_claude/
    # calibrar_disp_r_chutes*.py` cobre 12 ligas hoje) -- `prob_over_nb`
    # degenera pra Poisson nesse caso, nunca quebra.
    disp_r_por_stat = {
        s: disp_r_da_liga(supabase, liga_id, STAT_LEAGUE_PARAMS_LABEL[s])
        for s in ("shots", "shots_on_target")
    }

    estimativas, previsoes = [], []
    for _, jogo in teste.iterrows():
        for s, m in modelos.items():
            if jogo["home"] not in m["ataque"] or jogo["away"] not in m["ataque"]:
                continue  # time promovido sem histórico
            lam, mu = prever_stat(m, jogo["home"], jogo["away"])
            estimativas.append({
                "match_id": int(jogo["id"]),
                "model_name": MODEL_NAME,
                "stat": s,
                "home_expected": round(float(lam), 3),
                "away_expected": round(float(mu), 3),
            })
            if s == "corners":
                p_over = prob_over(lam + mu, LINHA_ESCANTEIOS)
                p_over = min(max(p_over, 1e-5), 1 - 1e-5)
                for sel, p in [("over", p_over), ("under", 1 - p_over)]:
                    previsoes.append({
                        "match_id": int(jogo["id"]),
                        "model_name": MODEL_NAME,
                        "market": f"corners_over_under_{LINHA_ESCANTEIOS}",
                        "selection": sel,
                        "probability": round(float(p), 5),
                    })
            elif s in LINHAS_POR_STAT:
                # Over/under de chutes/chutes no gol (TOTAL da partida, mesmo
                # padrão de corners acima) -- via Binomial Negativa
                # (`prob_over_nb`, disp_r calibrado por liga), não Poisson.
                # Sem odds reais pra comparar (mercado não existe agregado
                # por time na OddsPapi, só player prop) -- essas previsões
                # habilitam log-loss/Brier/calibração/lift em `api/model-
                # stats.js` contra o resultado real (`api/_lib/
                # resultadosReais.js`), nunca ROI/edge.
                for linha in LINHAS_POR_STAT[s]:
                    p_over = prob_over_nb(lam + mu, disp_r_por_stat[s], linha)
                    p_over = min(max(p_over, 1e-5), 1 - 1e-5)
                    for sel, p in [("over", p_over), ("under", 1 - p_over)]:
                        previsoes.append({
                            "match_id": int(jogo["id"]),
                            "model_name": MODEL_NAME,
                            "market": f"{s}_over_under_{linha}",
                            "selection": sel,
                            "probability": round(float(p), 5),
                        })

    # Dedup por chave de conflito antes de gravar -- ver mesmo comentário em
    # modelo_dixon_coles.py (fixture duplicada pro mesmo match_id quebra o
    # upsert inteiro).
    estimativas = list({(e["match_id"], e["model_name"], e["stat"]): e for e in estimativas}.values())
    previsoes = list({(p["match_id"], p["model_name"], p["market"], p["selection"]): p for p in previsoes}.values())

    for i in range(0, len(estimativas), 500):
        supabase.table("model_stat_estimates").upsert(
            estimativas[i : i + 500], on_conflict="match_id,model_name,stat").execute()
    for i in range(0, len(previsoes), 500):
        supabase.table("model_predictions").upsert(
            previsoes[i : i + 500], on_conflict="match_id,model_name,market,selection").execute()

    print(f"  {len(estimativas)} estimativas de stats gravadas | "
          f"{len(previsoes)} previsões (escanteios + chutes + chutes no gol) gravadas")


def main():
    if "COLE_SUA" in SUPABASE_KEY:
        sys.exit("Configure SUPABASE_KEY antes de rodar.")
    from supabase import create_client
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    for liga in LIGAS:
        rodar_liga(supabase, liga)
    print("\nEstatísticas esperadas em model_stat_estimates; "
          "over/under de escanteios em model_predictions.")


if __name__ == "__main__":
    main()
