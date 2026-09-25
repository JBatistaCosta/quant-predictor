#!/usr/bin/env python3
"""Calibração de potência do λ da produção: λ* = α·λ^γ  <=>  ln λ* = ln α + γ·ln λ.

Estima (α, γ) por GLM de Poisson (ligação log) sobre as observações
time-partida do conjunto de treino (partidas antes de --corte), testa
H0: γ=1 (Wald), compara com a versão restrita que força Σλ*=Σgols, testa
se mandante e visitante precisam de pares diferentes (LRT, 2 df) e avalia
fora da amostra (partidas a partir de --corte) usando a matriz Dixon-Coles
estática de produção (`distribuicoes.matriz_placares`, ρ real).

"λ da produção" = especificação da PR #657: λ_xGOT (soma de
`player_match_walkforward.lambda_xg_jogo` clipado ≥0, `fonte_titular=
'previsto'`) × exp(DEF_BETA_XGA·def_xga_adv + DEF_BETA_XA·def_xa_adv),
com o resíduo defensivo do adversário calculado walk-forward por
`dados_historicos._calcular_forca_defensiva(shift=True)`.

Resultado registrado em CONTEXTO_PROJETO.md (25/09): γ≈0,81 (muito abaixo
de 1, estável entre temporadas) — melhora a previsão de gols POR TIME e o
Over/Under 2.5 fora da amostra, mas NÃO melhora 1X2 nem Handicap -1.0.
Não aplicado em produção.

Uso:
    python arquivos_do_claude/calibrar_potencia_lambda.py            # carrega do banco
    python arquivos_do_claude/calibrar_potencia_lambda.py --entrada painel.pkl

`--entrada` (pickle ou CSV) evita o banco: uma linha por time-partida com
colunas match_id, data, team_id, is_home, lam_prod, gols, rho (rho repetido
nas duas linhas da partida; só é usado na avaliação fora da amostra).

Variáveis de ambiente (só sem --entrada): SUPABASE_URL, SUPABASE_KEY.
Só leitura — não escreve nada no banco.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

import numpy as np
import pandas as pd
from scipy import stats
from scipy.optimize import minimize, minimize_scalar

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import dados_historicos as dh  # noqa: E402
import distribuicoes as dist  # noqa: E402
import pricing_pipeline as pp  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("calibrar_potencia_lambda")

EPS = 1e-6
CORTE_PADRAO = "2025-06-01"
N_BOOT = 2000
SEED = 42
COLUNAS = ["match_id", "data", "team_id", "is_home", "lam_prod", "gols", "rho"]


# =============================================================================
# Núcleo puro
# =============================================================================
def ajustar_potencia(lam: np.ndarray, gols: np.ndarray) -> dict:
    """GLM de Poisson ln λ* = β0 + β1·ln λ. α=exp(β0), γ=β1."""
    x = np.log(np.clip(lam, EPS, None))
    y = np.asarray(gols, dtype=float)
    X = np.column_stack([np.ones_like(x), x])

    def nll(b):
        eta = np.clip(X @ b, -30, 30)
        return float(np.sum(np.exp(eta) - y * eta))

    def grad(b):
        eta = np.clip(X @ b, -30, 30)
        return X.T @ (np.exp(eta) - y)

    def hess(b):
        eta = np.clip(X @ b, -30, 30)
        return (X * np.exp(eta)[:, None]).T @ X

    res = minimize(nll, np.array([0.0, 1.0]), jac=grad, hess=hess, method="Newton-CG",
                   options={"xtol": 1e-12, "maxiter": 500})
    se = np.sqrt(np.diag(np.linalg.inv(hess(res.x))))
    gamma, se_gamma = float(res.x[1]), float(se[1])
    z = (gamma - 1.0) / se_gamma
    return {
        "alpha": float(np.exp(res.x[0])), "gamma": gamma, "se_gamma": se_gamma,
        "z_vs_1": z, "p_valor": float(2 * stats.norm.sf(abs(z))), "nll": nll(res.x), "n": len(y),
    }


def ajustar_restrito(lam: np.ndarray, gols: np.ndarray) -> dict:
    """α(γ) = Σy / Σλ^γ (conserva a média de gols por construção), γ por
    minimize_scalar. Com intercepto livre, o GLM de Poisson já satisfaz essa
    restrição no ótimo — os dois ajustes devem coincidir."""
    lam = np.clip(lam, EPS, None)
    y = np.asarray(gols, dtype=float)

    def nll(g):
        m = (y.sum() / np.sum(lam ** g)) * lam ** g
        return float(np.sum(m - y * np.log(m)))

    res = minimize_scalar(nll, bounds=(0.1, 2.0), method="bounded", options={"xatol": 1e-10})
    return {"alpha": float(y.sum() / np.sum(lam ** res.x)), "gamma": float(res.x), "nll": nll(res.x)}


def aplicar(lam: np.ndarray, alpha: float, gamma: float) -> np.ndarray:
    return alpha * np.clip(lam, EPS, None) ** gamma


def lrt_mando(treino: pd.DataFrame, global_: dict) -> dict:
    """(α_H,γ_H) ≠ (α_A,γ_A)? LRT com 2 df contra o par global."""
    casa = ajustar_potencia(treino.loc[treino["is_home"], "lam_prod"].values, treino.loc[treino["is_home"], "gols"].values)
    fora = ajustar_potencia(treino.loc[~treino["is_home"], "lam_prod"].values, treino.loc[~treino["is_home"], "gols"].values)
    estat = 2 * (global_["nll"] - casa["nll"] - fora["nll"])
    return {"casa": casa, "fora": fora, "lrt": estat, "p_valor": float(stats.chi2.sf(estat, 2))}


def para_partidas(painel: pd.DataFrame) -> pd.DataFrame:
    """Time-partida -> uma linha por partida (mandante/visitante lado a lado)."""
    casa = painel[painel["is_home"]].rename(columns={"lam_prod": "lam_home", "gols": "home_goals"})
    fora = painel[~painel["is_home"]].rename(columns={"lam_prod": "lam_away", "gols": "away_goals"})
    return casa[["match_id", "data", "lam_home", "home_goals", "rho"]].merge(
        fora[["match_id", "lam_away", "away_goals"]], on="match_id", how="inner"
    ).dropna(subset=["rho"])


def _bootstrap_diff(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    rng = np.random.default_rng(SEED)
    n = len(a)
    diffs = [a[i].mean() - b[i].mean() for i in (rng.integers(0, n, n) for _ in range(N_BOOT))]
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    return float(lo), float(hi)


def _probabilidades(lh: np.ndarray, la: np.ndarray, rho: np.ndarray) -> dict[str, np.ndarray]:
    p1x2, phcp, pover = [], [], []
    for a, b, r in zip(lh, la, rho):
        mk = dist.mercados_de_gols(dist.matriz_placares(a, b, r), linhas_handicap=(-1.0,))
        p1x2.append([mk[("1X2", "home")], mk[("1X2", "draw")], mk[("1X2", "away")]])
        phcp.append([mk[("handicap_-1.0", "home")], mk.get(("handicap_-1.0", "push"), 0.0), mk[("handicap_-1.0", "away")]])
        pover.append(mk[("over_under_2.5", "over")])
    return {"1x2": np.array(p1x2), "hcp": np.array(phcp), "over": np.clip(np.array(pover), 1e-4, 1 - 1e-4)}


def avaliar_oos(teste: pd.DataFrame, versoes: dict[str, tuple[np.ndarray, np.ndarray]]) -> None:
    hg, ag = teste["home_goals"].values, teste["away_goals"].values
    lh0, la0 = versoes["producao"]
    delta = lh0 - la0
    decil = pd.qcut(delta, 10, labels=False)
    fav = delta > 0

    logger.info("--- Observado/Esperado por decil de Δλ = λ_M − λ_V (λ da produção define o decil) ---")
    for nome, (lh, la) in versoes.items():
        for d in (0, 4, 9):
            m = decil == d
            logger.info("  %-16s decil %2d (Δλ médio %+.2f): O/E mandante=%.3f  O/E visitante=%.3f",
                        nome, d + 1, delta[m].mean(), hg[m].sum() / lh[m].sum(), ag[m].sum() / la[m].sum())
        ext = (decil == 0) | (decil == 9)
        fav_o, fav_e = np.where(fav, hg, ag)[ext].sum(), np.where(fav, lh, la)[ext].sum()
        aza_o, aza_e = np.where(fav, ag, hg)[ext].sum(), np.where(fav, la, lh)[ext].sum()
        logger.info("  %-16s decis extremos (1 e 10): O/E favorito=%.3f  O/E azarão=%.3f", nome, fav_o / fav_e, aza_o / aza_e)

    res_1x2 = np.where(hg > ag, 0, np.where(hg == ag, 1, 2))
    margem = hg - ag - 1
    res_hcp = np.where(margem > 0, 0, np.where(margem == 0, 1, 2))
    over = (hg + ag > 2.5).astype(float)
    idx = np.arange(len(teste))

    perdas = {}
    for nome, (lh, la) in versoes.items():
        pr = _probabilidades(lh, la, teste["rho"].values)
        perdas[nome] = {
            "1X2": -np.log(np.clip(pr["1x2"][idx, res_1x2], 1e-4, 1)),
            "Handicap -1.0": -np.log(np.clip(pr["hcp"][idx, res_hcp], 1e-4, 1)),
            "Over/Under 2.5": -(over * np.log(pr["over"]) + (1 - over) * np.log(1 - pr["over"])),
            "Gols por time (NLL Poisson)": -(stats.poisson.logpmf(hg, lh) + stats.poisson.logpmf(ag, la)),
        }
        p_push = pr["hcp"][:, 1]
        real = (res_hcp == 1).sum()
        z = (real - p_push.sum()) / np.sqrt((p_push * (1 - p_push)).sum())
        logger.info("  push (mandante vence por exatamente 1) %-16s esperado=%.1f real=%d z=%+.2f", nome, p_push.sum(), real, z)

    logger.info("--- Δ perda fora da amostra vs. produção (IC95%% bootstrap pareado, %d reamostragens) ---", N_BOOT)
    for mercado in perdas["producao"]:
        base = perdas["producao"][mercado]
        logger.info("  %s: produção=%.4f", mercado, base.mean())
        for nome in versoes:
            if nome == "producao":
                continue
            v = perdas[nome][mercado]
            lo, hi = _bootstrap_diff(v, base)
            logger.info("    %-16s %.4f  Δ=%+.4f  IC95%%=[%+.4f, %+.4f]", nome, v.mean(), v.mean() - base.mean(), lo, hi)


# =============================================================================
# Carregamento
# =============================================================================
def obter_env(nome: str) -> str:
    valor = (os.environ.get(nome) or "").strip()
    if not valor:
        sys.exit(f"Configure {nome} antes de rodar (ou use --entrada).")
    return valor


def _paginar_tabela(consulta_factory, tamanho_pagina: int = 1000) -> list[dict]:
    linhas, inicio = [], 0
    while True:
        pagina = consulta_factory(inicio, inicio + tamanho_pagina - 1).execute().data or []
        linhas.extend(pagina)
        if len(pagina) < tamanho_pagina:
            return linhas
        inicio += tamanho_pagina


def _somar_por_time_partida(supabase, tabela: str, coluna: str, match_ids: list[int], filtro=None) -> pd.DataFrame:
    def factory(lote, inicio, fim):
        q = supabase.table(tabela).select(f"match_id, team_id, {coluna}").in_("match_id", lote)
        if filtro:
            q = filtro(q)
        return q.order("match_id").range(inicio, fim)

    linhas = dh._paginar_por_lotes_de_id(factory, match_ids, tamanho_lote=100)
    df = pd.DataFrame(linhas, columns=["match_id", "team_id", coluna])
    df[coluna] = pd.to_numeric(df[coluna], errors="coerce")
    return df


def carregar_do_banco() -> pd.DataFrame:
    from supabase import create_client

    supabase = create_client(obter_env("SUPABASE_URL"), obter_env("SUPABASE_KEY"))
    partidas = pd.DataFrame(_paginar_tabela(
        lambda i, f: supabase.table("matches")
        .select("id, match_date, home_team_id, away_team_id, home_goals, away_goals")
        .eq("status", "finished").not_.is_("home_goals", "null").not_.is_("away_goals", "null")
        .order("id").range(i, f)
    )).rename(columns={"id": "match_id", "match_date": "data"})
    partidas["data"] = pd.to_datetime(partidas["data"], utc=True).dt.tz_localize(None)
    ids = partidas["match_id"].astype(int).tolist()
    logger.info("Partidas finalizadas: %d", len(ids))

    longo = pd.concat([
        partidas.assign(team_id=partidas["home_team_id"], opponent_id=partidas["away_team_id"], is_home=True, gols=partidas["home_goals"]),
        partidas.assign(team_id=partidas["away_team_id"], opponent_id=partidas["home_team_id"], is_home=False, gols=partidas["away_goals"]),
    ])[["match_id", "data", "team_id", "opponent_id", "is_home", "gols"]]

    logger.info("λ_xGOT (player_match_walkforward, previsto)...")
    xg_jog = _somar_por_time_partida(supabase, "player_match_walkforward", "lambda_xg_jogo", ids,
                                     filtro=lambda q: q.eq("fonte_titular", "previsto"))
    xg_jog["lambda_xg_jogo"] = xg_jog["lambda_xg_jogo"].clip(lower=0.0)
    lam_xgot = xg_jog.groupby(["match_id", "team_id"])["lambda_xg_jogo"].sum().rename("lambda_xgot").reset_index()

    logger.info("Painel de defesa (xG de match_shots_fotmob, xA de match_player_stats_fotmob)...")
    xg = _somar_por_time_partida(supabase, "match_shots_fotmob", "xg", ids).groupby(["match_id", "team_id"])["xg"].sum().rename("xg_marcado")
    xa = _somar_por_time_partida(supabase, "match_player_stats_fotmob", "xa", ids).groupby(["match_id", "team_id"])["xa"].sum().rename("xa_marcado")
    marcado = pd.concat([xg, xa], axis=1).reset_index()
    painel = longo.rename(columns={"data": "match_date"}).merge(marcado, on=["match_id", "team_id"], how="left")
    sofrido = marcado.rename(columns={"team_id": "opponent_id", "xg_marcado": "xg_sofrido", "xa_marcado": "xa_sofrido"})
    painel = painel.merge(sofrido, on=["match_id", "opponent_id"], how="left")
    defesa = dh._calcular_forca_defensiva(painel, shift=True)[["match_id", "team_id", "def_residuo_xga", "def_residuo_xa"]]
    defesa_adv = defesa.rename(columns={"team_id": "opponent_id", "def_residuo_xga": "def_xga_adv", "def_residuo_xa": "def_xa_adv"})

    df = longo.merge(lam_xgot, on=["match_id", "team_id"], how="inner").merge(defesa_adv, on=["match_id", "opponent_id"], how="left")
    df[["def_xga_adv", "def_xa_adv"]] = df[["def_xga_adv", "def_xa_adv"]].fillna(0.0)
    df["lam_prod"] = df["lambda_xgot"] * np.exp(pp.DEF_BETA_XGA * df["def_xga_adv"] + pp.DEF_BETA_XA * df["def_xa_adv"])

    logger.info("ρ real (model_match_estimates, hibrido_gols_v1)...")
    est = dh._paginar_por_lotes_de_id(
        lambda lote, i, f: supabase.table("model_match_estimates").select("match_id, params")
        .in_("match_id", lote).eq("model_name", "hibrido_gols_v1").range(i, f),
        ids, tamanho_lote=300,
    )
    rho = pd.DataFrame([{"match_id": e["match_id"], "rho": (e.get("params") or {}).get("rho")} for e in est])
    df = df.merge(rho, on="match_id", how="left")
    return df[COLUNAS]


def carregar_de_arquivo(caminho: str) -> pd.DataFrame:
    df = pd.read_pickle(caminho) if caminho.endswith(".pkl") else pd.read_csv(caminho)
    faltando = set(COLUNAS) - set(df.columns)
    if faltando:
        sys.exit(f"--entrada sem as colunas: {sorted(faltando)}")
    df["data"] = pd.to_datetime(df["data"])
    df["is_home"] = df["is_home"].astype(bool)
    return df[COLUNAS]


# =============================================================================
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corte", default=CORTE_PADRAO, help="treino = antes desta data; teste = a partir dela")
    parser.add_argument("--entrada", help="painel time-partida pronto (.pkl ou .csv) em vez de ler do banco")
    args = parser.parse_args()

    painel = carregar_de_arquivo(args.entrada) if args.entrada else carregar_do_banco()
    painel = painel.dropna(subset=["lam_prod", "gols"])
    corte = pd.Timestamp(args.corte)
    treino, teste = painel[painel["data"] < corte], painel[painel["data"] >= corte]
    logger.info("Treino: %d time-partida (%d partidas) | Teste: %d time-partida",
                len(treino), treino["match_id"].nunique(), len(teste))

    glob = ajustar_potencia(treino["lam_prod"].values, treino["gols"].values)
    restr = ajustar_restrito(treino["lam_prod"].values, treino["gols"].values)
    mando = lrt_mando(treino, glob)
    logger.info("=== Ajuste no treino ===")
    for nome, r in [("global", glob), ("mandantes", mando["casa"]), ("visitantes", mando["fora"])]:
        logger.info("  %-10s n=%6d  α=%.4f  γ=%.4f  SE_γ=%.4f  z(γ=1)=%+.2f  p=%.2e",
                    nome, r["n"], r["alpha"], r["gamma"], r["se_gamma"], r["z_vs_1"], r["p_valor"])
    logger.info("  restrito (Σλ*=Σgols): α=%.4f γ=%.4f  ΔNLL vs irrestrito=%+.2e", restr["alpha"], restr["gamma"], restr["nll"] - glob["nll"])
    logger.info("  LRT mando (2 df): %.2f  p=%.2e -> %s", mando["lrt"], mando["p_valor"],
                "par por mando" if mando["p_valor"] < 0.05 else "par global")

    partidas = para_partidas(teste)
    logger.info("=== Fora da amostra: %d partidas ===", len(partidas))
    lh, la = partidas["lam_home"].values, partidas["lam_away"].values
    versoes = {
        "producao": (lh, la),
        "potencia_global": (aplicar(lh, glob["alpha"], glob["gamma"]), aplicar(la, glob["alpha"], glob["gamma"])),
        "potencia_mando": (aplicar(lh, mando["casa"]["alpha"], mando["casa"]["gamma"]),
                           aplicar(la, mando["fora"]["alpha"], mando["fora"]["gamma"])),
    }
    avaliar_oos(partidas, versoes)


if __name__ == "__main__":
    main()
