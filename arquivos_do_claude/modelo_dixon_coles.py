"""
Modelo Dixon-Coles para odds justas (projeto quant-futebol-dados).

O que ele faz:
    1. Busca partidas finalizadas no Supabase (treino: 2023-2024, teste: 2025)
    2. Ajusta o modelo Dixon-Coles por liga: força de ataque e defesa de cada
       time + vantagem de mando + correção rho para placares baixos
    3. Gera a matriz de probabilidades de placar de cada jogo de teste
    4. Deriva os mercados 1X2 e Over/Under 2.5 e grava em model_predictions
    5. Faz backtest: compara o modelo com baselines via log loss

Teoria em 30 segundos:
    Gols do mandante ~ Poisson(lambda), gols do visitante ~ Poisson(mu), onde
        lambda = exp(ataque_mandante + defesa_visitante + mando)
        mu     = exp(ataque_visitante + defesa_mandante)
    A correção de Dixon-Coles (parâmetro rho) ajusta a dependência entre os
    dois Poissons nos placares 0x0, 1x0, 0x1 e 1x1, onde o modelo puro erra.
    Jogos antigos pesam menos via decaimento exponencial no tempo (xi).

Uso:
    pip install supabase numpy scipy pandas
    set SUPABASE_KEY=sua_service_role_key   (cmd, sem aspas)
    python modelo_dixon_coles.py
"""

import os
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import poisson

# ---------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

# Brasileirão não tem 2022 disponível (limite do plano gratuito da fonte);
# as 5 europeias ganharam 2022 depois de testarmos que ajuda a maioria
# (3 de 5 ligas melhoraram o log loss em 2025 incluindo essa temporada).
TEMPORADAS_TREINO_POR_LIGA = {
    "BSA": ["2023", "2024"],
    "PL": ["2022", "2023", "2024"],
    "PD": ["2022", "2023", "2024"],
    "SA": ["2022", "2023", "2024"],
    "BL1": ["2022", "2023", "2024"],
    "FL1": ["2022", "2023", "2024"],
}
TEMPORADA_TESTE = "2025"

# Ligas domésticas (external_id na tabela leagues). CL/Euro ficam fora da v1:
# times de ligas diferentes quase não se enfrentam, inviabilizando calibração.
LIGAS = ["BSA", "PL", "PD", "SA", "BL1", "FL1"]

XI = 0.0018          # decaimento temporal/dia (meia-vida ~13 meses)
MAX_GOLS = 10        # tamanho da matriz de placares (0..10 gols)
MODEL_NAME = "dixon_coles_v1"


# ---------------------------------------------------------------
# Núcleo do modelo (independente do banco — testável isoladamente)
# ---------------------------------------------------------------
def tau(x, y, lam, mu, rho):
    """Correção de Dixon-Coles para placares baixos (0x0, 1x0, 0x1, 1x1)."""
    if x == 0 and y == 0:
        return 1 - lam * mu * rho
    if x == 0 and y == 1:
        return 1 + lam * rho
    if x == 1 and y == 0:
        return 1 + mu * rho
    if x == 1 and y == 1:
        return 1 - rho
    return 1.0


def ajustar_dixon_coles(df: pd.DataFrame):
    """Ajusta o modelo numa liga. df precisa de: home, away, hg, ag, weight.

    Retorna dict com ataque/defesa por time, vantagem de mando e rho.
    """
    times = sorted(set(df["home"]) | set(df["away"]))
    n = len(times)
    idx = {t: i for i, t in enumerate(times)}

    h = df["home"].map(idx).to_numpy()
    a = df["away"].map(idx).to_numpy()
    hg = df["hg"].to_numpy(dtype=float)
    ag = df["ag"].to_numpy(dtype=float)
    w = df["weight"].to_numpy(dtype=float)

    # Parâmetros: n ataques + n defesas + mando + rho
    # Identificabilidade: forçamos média dos ataques = 0 dentro da likelihood.
    def neg_log_lik(params):
        atk = params[:n] - params[:n].mean()
        dfn = params[n : 2 * n]
        mando, rho = params[-2], params[-1]

        lam = np.exp(atk[h] + dfn[a] + mando)
        mu = np.exp(atk[a] + dfn[h])

        # log-verossimilhança Poisson + correção tau nos placares baixos
        ll = hg * np.log(lam) - lam + ag * np.log(mu) - mu

        t = np.ones_like(ll)
        m00 = (hg == 0) & (ag == 0)
        m01 = (hg == 0) & (ag == 1)
        m10 = (hg == 1) & (ag == 0)
        m11 = (hg == 1) & (ag == 1)
        t[m00] = 1 - lam[m00] * mu[m00] * rho
        t[m01] = 1 + lam[m01] * rho
        t[m10] = 1 + mu[m10] * rho
        t[m11] = 1 - rho
        t = np.clip(t, 1e-10, None)  # evita log de valor <= 0

        return -np.sum(w * (ll + np.log(t)))

    x0 = np.concatenate([np.zeros(n), np.zeros(n), [0.25], [-0.05]])
    bounds = [(-3, 3)] * (2 * n) + [(-1, 1), (-0.5, 0.5)]
    res = minimize(neg_log_lik, x0, method="L-BFGS-B", bounds=bounds)

    atk = res.x[:n] - res.x[:n].mean()
    return {
        "times": times,
        "ataque": dict(zip(times, atk)),
        "defesa": dict(zip(times, res.x[n : 2 * n])),
        "mando": res.x[-2],
        "rho": res.x[-1],
        "convergiu": res.success,
    }


def matriz_placares(modelo, mandante, visitante):
    """Matriz (MAX_GOLS+1 x MAX_GOLS+1) com P(placar mandante x visitante)."""
    matriz, _, _ = matriz_placares_com_lambda(modelo, mandante, visitante)
    return matriz


def matriz_placares_com_lambda(modelo, mandante, visitante):
    """Mesma coisa que `matriz_placares`, só que também devolve (lam, mu) —
    os gols esperados (xG do MODELO, não confundir com xG real de fonte
    externa) por equipe, usados como etapa intermediária antes da matriz de
    placares. Função própria (em vez de mudar a assinatura de
    `matriz_placares`) pra não quebrar quem só quer a matriz."""
    lam = np.exp(
        modelo["ataque"][mandante] + modelo["defesa"][visitante] + modelo["mando"]
    )
    mu = np.exp(modelo["ataque"][visitante] + modelo["defesa"][mandante])

    gols = np.arange(MAX_GOLS + 1)
    p_home = poisson.pmf(gols, lam)
    p_away = poisson.pmf(gols, mu)
    matriz = np.outer(p_home, p_away)

    for x in (0, 1):
        for y in (0, 1):
            matriz[x, y] *= tau(x, y, lam, mu, modelo["rho"])

    return matriz / matriz.sum(), float(lam), float(mu)  # renormaliza após a correção


def mercados(matriz):
    """Deriva 1X2 e Over/Under 2.5 da matriz de placares."""
    casa = np.tril(matriz, -1).sum()   # mandante marca mais
    empate = np.trace(matriz)
    fora = np.triu(matriz, 1).sum()

    total = np.add.outer(np.arange(MAX_GOLS + 1), np.arange(MAX_GOLS + 1))
    over25 = matriz[total >= 3].sum()

    return {
        ("1X2", "home"): casa,
        ("1X2", "draw"): empate,
        ("1X2", "away"): fora,
        ("over_under_2.5", "over"): over25,
        ("over_under_2.5", "under"): 1 - over25,
    }


# ---------------------------------------------------------------
# Pipeline com o banco
# ---------------------------------------------------------------
def carregar_partidas(supabase, liga_ext_id):
    """Busca todas as partidas finalizadas da liga (todas as temporadas)."""
    liga = (
        supabase.table("leagues").select("id").eq("external_id", liga_ext_id)
        .execute().data
    )
    if not liga:
        return None, pd.DataFrame()
    liga_id = liga[0]["id"]

    linhas, inicio = [], 0
    while True:  # pagina de 1000 em 1000 (limite do PostgREST)
        lote = (
            supabase.table("matches")
            .select("id, season, match_date, home_team_id, away_team_id, home_goals, away_goals")
            .eq("league_id", liga_id).eq("status", "finished")
            .range(inicio, inicio + 999).execute().data
        )
        linhas.extend(lote)
        if len(lote) < 1000:
            break
        inicio += 1000

    df = pd.DataFrame(linhas)
    if df.empty:
        return liga_id, df
    df = df.rename(columns={"home_team_id": "home", "away_team_id": "away",
                            "home_goals": "hg", "away_goals": "ag"})
    df["match_date"] = pd.to_datetime(df["match_date"], utc=True)
    return liga_id, df


def rodar_liga(supabase, liga_ext_id):
    liga_id, df = carregar_partidas(supabase, liga_ext_id)
    if df.empty:
        print(f"\n[{liga_ext_id}] sem dados — pulando.")
        return None

    treino = df[df["season"].isin(TEMPORADAS_TREINO_POR_LIGA[liga_ext_id])].copy()
    teste = df[df["season"] == TEMPORADA_TESTE].copy()

    # Pesos temporais: jogos mais recentes valem mais
    ref = treino["match_date"].max()
    dias = (ref - treino["match_date"]).dt.days
    treino["weight"] = np.exp(-XI * dias)

    modelo = ajustar_dixon_coles(treino)
    status = "ok" if modelo["convergiu"] else "NÃO CONVERGIU"
    print(f"\n[{liga_ext_id}] treino: {len(treino)} jogos | teste: {len(teste)} jogos "
          f"| mando: {modelo['mando']:.3f} | rho: {modelo['rho']:.3f} | {status}")

    # Times novos (promovidos) que não existem no treino não podem ser previstos
    conhecidos = set(modelo["times"])
    previsiveis = teste[teste["home"].isin(conhecidos) & teste["away"].isin(conhecidos)]
    pulados = len(teste) - len(previsiveis)
    if pulados:
        print(f"  {pulados} jogos de teste pulados (times promovidos, sem histórico no treino)")

    # Previsões + log loss do 1X2
    previsoes, estimativas_xg, log_loss_soma = [], [], 0.0
    for _, jogo in previsiveis.iterrows():
        m, lam, mu = matriz_placares_com_lambda(modelo, jogo["home"], jogo["away"])
        probs = mercados(m)

        resultado = "draw" if jogo["hg"] == jogo["ag"] else ("home" if jogo["hg"] > jogo["ag"] else "away")
        log_loss_soma += -np.log(max(probs[("1X2", resultado)], 1e-10))

        for (mercado, selecao), p in probs.items():
            previsoes.append({
                "match_id": int(jogo["id"]),
                "model_name": MODEL_NAME,
                "market": mercado,
                "selection": selecao,
                "probability": round(float(p), 5),
            })
        estimativas_xg.append({
            "match_id": int(jogo["id"]), "model_name": MODEL_NAME,
            "xg_home_previsto": round(lam, 3), "xg_away_previsto": round(mu, 3),
        })

    n = len(previsiveis)
    log_loss = log_loss_soma / n if n else float("nan")
    # Baseline ingênuo: sempre prever (46% casa, 27% empate, 27% fora) — média histórica
    baseline = 0.0
    for _, jogo in previsiveis.iterrows():
        resultado = "draw" if jogo["hg"] == jogo["ag"] else ("home" if jogo["hg"] > jogo["ag"] else "away")
        baseline += -np.log({"home": 0.46, "draw": 0.27, "away": 0.27}[resultado])
    baseline /= max(n, 1)

    print(f"  log loss 1X2 (teste {TEMPORADA_TESTE}): modelo {log_loss:.4f} vs baseline {baseline:.4f} "
          f"{'-> modelo MELHOR' if log_loss < baseline else '-> modelo pior que baseline'}")

    # Grava previsões (upsert: rodar de novo não duplica). Dedup por chave de
    # conflito antes de gravar -- `matches` pode ter fixture duplicada pro
    # mesmo match_id (visto em produção), o que faz o Postgres rejeitar o
    # upsert inteiro ("cannot affect row a second time" -- ele não aceita
    # duas linhas com a MESMA chave dentro do mesmo INSERT/UPSERT).
    previsoes_dedup = list({(p["match_id"], p["model_name"], p["market"], p["selection"]): p for p in previsoes}.values())
    for i in range(0, len(previsoes_dedup), 500):
        supabase.table("model_predictions").upsert(
            previsoes_dedup[i : i + 500],
            on_conflict="match_id,model_name,market,selection",
        ).execute()
    print(f"  {len(previsoes_dedup)} previsões gravadas em model_predictions")

    estimativas_xg_dedup = list({(e["match_id"], e["model_name"]): e for e in estimativas_xg}.values())
    for i in range(0, len(estimativas_xg_dedup), 500):
        supabase.table("model_match_estimates").upsert(
            estimativas_xg_dedup[i : i + 500],
            on_conflict="match_id,model_name",
        ).execute()
    print(f"  {len(estimativas_xg_dedup)} estimativas de xG gravadas em model_match_estimates")

    return {"liga": liga_ext_id, "log_loss": log_loss, "baseline": baseline, "jogos": n}


def main():
    if "COLE_SUA" in SUPABASE_KEY:
        sys.exit("Configure SUPABASE_KEY antes de rodar.")
    from supabase import create_client
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    resultados = [r for liga in LIGAS if (r := rodar_liga(supabase, liga))]

    print("\n===== RESUMO =====")
    for r in resultados:
        ganho = (r["baseline"] - r["log_loss"]) / r["baseline"] * 100
        print(f"  {r['liga']:>4}: log loss {r['log_loss']:.4f} "
              f"({ganho:+.1f}% vs baseline, {r['jogos']} jogos)")
    print("\nAs odds justas estão em model_predictions (coluna fair_odds).")


if __name__ == "__main__":
    main()
