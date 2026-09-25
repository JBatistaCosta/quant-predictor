#!/usr/bin/env python3
"""Informação nova no λ: escalação confirmada, descanso/sequência e mando adaptativo.

Item 3 da lista "o que melhorar nos mercados de times" (CONTEXTO_PROJETO.md,
25/09): as transformações globais do λ se esgotaram (PR #665) e o gap contra o
mercado é de informação partida a partida. Aqui cada fonte nova entra por
cima do λ calibrado (bivariada sem mando, PR #665), com coeficientes ajustados
SÓ no treino (GLM de Poisson com offset = ln λ_base), e é avaliada fora da
amostra em gols por time (NLL Poisson), 1X2 e O/U 2.5 (matriz Dixon-Coles
estática, ρ real), com IC95% bootstrap por partida.

Variantes:
  * base                          — λ da produção (XI previsto) + bivariada
  * escalação confirmada completa — soma do λ por jogador da fonte `real` de
    player_match_walkforward. ⚠️ VAZA: a tabela só tem jogadores que ENTRARAM em
    campo (inclui as reservas que entraram, que só se sabe depois do jogo), e os
    minutos esperados da fonte `real` batem exatamente com a média real por
    papel (titular 81,6 × 81,6; reserva 19,4 × 19,9).
  * só titulares confirmados      — soma do λ `real` só dos 11 titulares de
    match_lineup_fotmob (is_starter), sabidos ~1h antes do jogo; o banco vira
    parte do intercepto da bivariada. É a versão LIMPA.
  * previsto × titulares          — λ `previsto` somado só nos titulares
    confirmados (separa "saber quem começa" de "modelo de minutos").
  * descanso/sequência            — descanso ≤3 dias / ≥10 dias do time e do
    adversário (todas as partidas do banco, qualquer competição).
  * mando EWM (meia-vida 100/300) — ln(G/λ) mandante − visitante por liga, EWM
    só de partidas anteriores (≥30), aplicado ±½ (regra da PR #665: mando só
    adaptativo).

⚠️ O λ da produção (base) também vem de player_match_walkforward `previsto`,
que tem o mesmo recorte "só quem entrou em campo" — a base já carrega parte
desse vazamento. O conjunto só-titulares é o único 100% pré-jogo aqui.

Uso:
    python arquivos_do_claude/validar_informacao_nova_lambda.py --entrada painel.pkl --cache-dir /tmp/info

`--entrada`: painel time-partida de calibrar_potencia_lambda.py (sem ele, o
painel é montado do banco). Variáveis de ambiente: SUPABASE_URL, SUPABASE_KEY
(chave pública basta). Só leitura.
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd
from scipy import stats
from scipy.optimize import minimize

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.dirname(__file__))
import distribuicoes as dist  # noqa: E402
import calibrar_potencia_lambda as cp  # noqa: E402
from validar_assistencia_jogador_walkforward import Rest, obter_env  # noqa: E402
from validar_totais_vs_mercado import _cache  # noqa: E402

CORTE_PADRAO = "2025-06-01"
N_BOOT = 2000
SEED = 42
MEIAS_VIDAS_MANDO = (100, 300)
MIN_PARTIDAS_EWM = 30
MIN_TITULARES = 10


# =============================================================================
# Carregamento
# =============================================================================
def carregar(cache_dir):
    rest = None

    def cliente():
        nonlocal rest
        rest = rest or Rest(obter_env("SUPABASE_URL"), obter_env("SUPABASE_KEY"))
        return rest

    jogos = _cache(cache_dir, "matches_completo", lambda: cliente().baixar(
        "matches", "id,match_date,league_id,home_team_id,away_team_id,status,home_goals,away_goals", {}, 4))
    wf = _cache(cache_dir, "wf_xg_jogador", lambda: cliente().baixar(
        "player_match_walkforward", "id,match_id,team_id,player_id,fonte_titular,lambda_xg_jogo",
        {"lambda_xg_jogo": "not.is.null"}, 24))
    titulares = _cache(cache_dir, "lineup_titulares", lambda: cliente().baixar(
        "match_lineup_fotmob", "id,match_id,team_id,player_id", {"is_starter": "eq.true"}, 16))
    return jogos, wf, titulares


# =============================================================================
# Features
# =============================================================================
def somas_de_escalacao(wf: pd.DataFrame, titulares: pd.DataFrame) -> pd.DataFrame:
    """Por time-partida: soma do λ_xg (clip ≥0) previsto e real — todos os
    jogadores da tabela e só os titulares confirmados — e a cobertura de
    titulares com λ."""
    w = wf.assign(x=wf["lambda_xg_jogo"].clip(lower=0))
    tit = titulares[["match_id", "team_id", "player_id"]].assign(titular=True)
    w = w.merge(tit, on=["match_id", "team_id", "player_id"], how="left")
    w["titular"] = w["titular"].fillna(False).astype(bool)
    todos = w.pivot_table(index=["match_id", "team_id"], columns="fonte_titular", values="x", aggfunc="sum")
    so_tit = w[w["titular"]].pivot_table(index=["match_id", "team_id"], columns="fonte_titular", values="x", aggfunc="sum")
    cobertura = w[w["titular"] & (w["fonte_titular"] == "real")].groupby(["match_id", "team_id"]).size()
    out = pd.DataFrame({
        "xg_prev": todos.get("previsto"), "xg_real": todos.get("real"),
        "xg_prev_tit": so_tit.get("previsto"), "xg_real_tit": so_tit.get("real"),
    })
    out["n_titulares_com_lambda"] = cobertura
    return out.reset_index()


def descanso(jogos: pd.DataFrame) -> pd.DataFrame:
    j = pd.concat([
        jogos[["match_id", "dt", "home_team_id"]].rename(columns={"home_team_id": "team_id"}),
        jogos[["match_id", "dt", "away_team_id"]].rename(columns={"away_team_id": "team_id"}),
    ]).dropna().sort_values(["team_id", "dt"])
    j["dias_desc"] = j.groupby("team_id")["dt"].diff().dt.days
    return j[["match_id", "team_id", "dias_desc"]]


def mando_ewm(painel: pd.DataFrame) -> pd.DataFrame:
    """ln(ΣG/Σλ) mandante − visitante, EWM por liga de partidas ANTERIORES."""
    pm = painel.pivot_table(index=["match_id", "league_id", "dt"], columns="is_home", values=["gols", "lam_prod"]).reset_index()
    pm.columns = ["match_id", "league_id", "dt", "ga", "gh", "la", "lh"]
    pm = pm.sort_values("dt")
    for hl in MEIAS_VIDAS_MANDO:
        partes = []
        for _, g in pm.groupby("league_id"):
            e = g[["gh", "lh", "ga", "la"]].ewm(halflife=hl, min_periods=MIN_PARTIDAS_EWM).mean().shift(1)
            partes.append(pd.Series(np.log(e["gh"] / e["lh"]) - np.log(e["ga"] / e["la"]), index=g.index))
        pm[f"mando_ewm{hl}"] = pd.concat(partes).replace([np.inf, -np.inf], np.nan)
    return pm[["match_id"] + [f"mando_ewm{hl}" for hl in MEIAS_VIDAS_MANDO]]


# =============================================================================
# Modelos
# =============================================================================
def ajustar_bivariada(df: pd.DataFrame, col: str) -> dict:
    d = df[["match_id", "team_id", "is_home", col, "gols"]].rename(columns={col: "lam_prod"})
    return cp.ajustar_bivariado(cp.com_lambda_adversario(d), com_mando=False)


def aplicar_bivariada(df: pd.DataFrame, col: str, ajuste: dict) -> pd.Series:
    d = df[["match_id", "team_id", "is_home", col]].rename(columns={col: "lam_prod"})
    a = cp.com_lambda_adversario(d.assign(gols=0))
    lam = cp.aplicar_bivariado(ajuste, a["lam_prod"].values, a["lam_adv"].values, a["is_home"].values)
    return pd.Series(lam, index=pd.MultiIndex.from_frame(a[["match_id", "team_id"]]))


def ajustar_offset(df: pd.DataFrame, cols: list[str], base: str):
    X = np.column_stack([np.ones(len(df))] + [df[c].values.astype(float) for c in cols])
    off, y = np.log(df[base].values), df["gols"].values

    def nll(b):
        return np.sum(np.exp(off + X @ b) - y * (off + X @ b))

    def grad(b):
        return X.T @ (np.exp(off + X @ b) - y)

    def hess(b):
        return (X * np.exp(off + X @ b)[:, None]).T @ X

    r = minimize(nll, np.zeros(X.shape[1]), jac=grad, hess=hess, method="Newton-CG")
    return r.x, np.sqrt(np.diag(np.linalg.inv(hess(r.x)))), nll(r.x)


def aplicar_offset(df, cols, base, b):
    X = np.column_stack([np.ones(len(df))] + [df[c].values.astype(float) for c in cols])
    return df[base].values * np.exp(X @ b)


def perdas_oos(partidas: pd.DataFrame, lh: np.ndarray, la: np.ndarray) -> dict[str, np.ndarray]:
    hg, ag = partidas["hg"].values, partidas["ag"].values
    res = np.where(hg > ag, 0, np.where(hg == ag, 1, 2))
    over = (hg + ag > 2.5).astype(float)
    p1, po = [], []
    for a, b, r in zip(lh, la, partidas["rho"].values):
        mk = dist.mercados_de_gols(dist.matriz_placares(a, b, r))
        p1.append([mk[("1X2", "home")], mk[("1X2", "draw")], mk[("1X2", "away")]])
        po.append(mk[("over_under_2.5", "over")])
    p1, po = np.array(p1), np.clip(np.array(po), 1e-4, 1 - 1e-4)
    return {
        "gols por time (NLL)": -(stats.poisson.logpmf(hg, lh) + stats.poisson.logpmf(ag, la)),
        "1X2": -np.log(np.clip(p1[np.arange(len(hg)), res], 1e-4, 1)),
        "O/U 2.5": -(over * np.log(po) + (1 - over) * np.log(1 - po)),
    }, p1


def odds_1x2_pinnacle(rest_ou_cache, match_ids: list[int]) -> pd.DataFrame:
    """Todas as capturas 1X2 da Pinnacle (opening/pre_closing/closing) das partidas."""
    import concurrent.futures as cf

    def lote(ids):
        linhas, offset = [], 0
        while True:
            pg = rest_ou_cache.get("odds_market", {
                "select": "id,match_id,selection,odds,captured_at,snapshot", "match_id": f"in.({','.join(map(str, ids))})",
                "market": "eq.1X2", "bookmaker": "eq.pinnacle", "order": "id", "limit": 1000, "offset": offset})
            linhas += pg
            if len(pg) < 1000:
                return linhas
            offset += 1000

    with cf.ThreadPoolExecutor(12) as ex:
        partes = list(ex.map(lote, [match_ids[i:i + 50] for i in range(0, len(match_ids), 50)]))
    return pd.DataFrame([r for p in partes for r in p])


def pinnacle_sem_vig(odds: pd.DataFrame, momento: str) -> pd.DataFrame:
    """1X2 sem vig por partida. momento='fechamento': snapshot closing (senão a
    última captura pré-jogo); 'abertura': 1ª captura pré-jogo (opening/pre_closing),
    antes de a escalação sair. Não usa captured_at como hora do jogo (ver
    validar_totais_vs_mercado.py: fontes importadas gravam a hora da importação)."""
    o = odds[odds["odds"] > 1.0].copy()
    o["t"] = pd.to_datetime(o["captured_at"], format="ISO8601", utc=True)
    if momento == "abertura":
        o = o[o["snapshot"].isin(["opening", "pre_closing"])].sort_values("t").groupby(["match_id", "selection"]).head(1)
    else:
        o["rank"] = (o["snapshot"] == "closing").astype(int)
        o = o.sort_values(["rank", "t"]).groupby(["match_id", "selection"]).tail(1)
    w = o.pivot_table(index="match_id", columns="selection", values="odds").dropna(subset=["home", "draw", "away"])
    inv = 1 / w[["home", "draw", "away"]]
    return inv.div(inv.sum(axis=1), axis=0)


def peso_log_pooling(y: np.ndarray, p_mercado: np.ndarray, p_modelo: np.ndarray) -> tuple[float, float]:
    """w de p ∝ p_mercado^(1−w)·p_modelo^w por máxima verossimilhança, com SE.
    w>0 significativo = o modelo acrescenta informação ao mercado."""
    lm, lo = np.log(np.clip(p_mercado, 1e-6, 1)), np.log(np.clip(p_modelo, 1e-6, 1))

    def nll(w):
        z = (1 - w) * lm + w * lo
        z = z - np.log(np.exp(z).sum(axis=1, keepdims=True))
        return -z[np.arange(len(y)), y].sum()

    res = minimize(lambda v: nll(v[0]), np.array([0.0]), method="BFGS")
    w, h = res.x[0], 1e-4
    se = 1 / np.sqrt(max((nll(w + h) - 2 * nll(w) + nll(w - h)) / h ** 2, 1e-12))
    return float(w), float(se)


# =============================================================================
def preparar_painel(entrada: str | None, cache_dir: str | None, corte: pd.Timestamp) -> pd.DataFrame:
    """Painel time-partida com todas as features e os λ calibrados (bivariada
    ajustada só no treino) de cada base: lb_prev, lb_real, lb_real_tit, lb_prev_tit.
    Reaproveitado por validar_desgaste_logistico.py."""
    painel = cp.carregar_de_arquivo(entrada) if entrada else cp.carregar_do_banco()
    painel = painel.dropna(subset=["lam_prod", "gols"])
    jogos, wf, titulares = carregar(cache_dir)
    jogos = jogos.rename(columns={"id": "match_id"})
    jogos["dt"] = pd.to_datetime(jogos["match_date"], format="ISO8601", utc=True).dt.tz_localize(None).dt.normalize()

    p = painel.merge(jogos[["match_id", "league_id", "dt"]], on="match_id")
    p = p.merge(somas_de_escalacao(wf, titulares), on=["match_id", "team_id"], how="left")
    fator_def = p["lam_prod"] / p["xg_prev"]  # λ_prod = λ_xGOT(previsto) × e^(força defensiva do adversário)
    p["lam_real"] = p["xg_real"] * fator_def
    p["lam_real_tit"] = p["xg_real_tit"] * fator_def
    p["lam_prev_tit"] = p["xg_prev_tit"] * fator_def
    p = p.merge(descanso(jogos), on=["match_id", "team_id"], how="left")
    adv = p[["match_id", "team_id", "dias_desc"]].rename(columns={"team_id": "adv_id", "dias_desc": "dias_adv"})
    p = p.merge(p[["match_id", "team_id"]].rename(columns={"team_id": "adv_id"}), on="match_id")
    p = p[p["team_id"] != p["adv_id"]].merge(adv, on=["match_id", "adv_id"], how="left")
    for nome, col, cond in (("curto", "dias_desc", "le"), ("curto_adv", "dias_adv", "le"),
                            ("longo", "dias_desc", "ge"), ("longo_adv", "dias_adv", "ge")):
        p[nome] = (p[col] <= 3).astype(float) if cond == "le" else (p[col] >= 10).astype(float)
    p = p.merge(mando_ewm(p), on="match_id", how="left")
    for hl in MEIAS_VIDAS_MANDO:
        p[f"x_mando{hl}"] = np.where(p["is_home"], 0.5, -0.5) * p[f"mando_ewm{hl}"]

    obrig = ["lam_real", "lam_real_tit", "lam_prev_tit", "dias_desc", "dias_adv"] + [f"mando_ewm{hl}" for hl in MEIAS_VIDAS_MANDO]
    p = p.replace([np.inf, -np.inf], np.nan).dropna(subset=obrig)
    p = p[p["n_titulares_com_lambda"] >= MIN_TITULARES]
    completos = p.groupby("match_id").size()
    p = p[p["match_id"].isin(completos[completos == 2].index)].copy()
    tr = p[p["data"] < corte]
    print(f"Amostra: treino {tr['match_id'].nunique()} / teste {p.loc[p['data'] >= corte, 'match_id'].nunique()} partidas "
          f"(time-partida com ≥{MIN_TITULARES} titulares confirmados com λ)")
    print(f"  |ln(λ_real/λ_prev)| médio={np.log(p['lam_real'] / p['lam_prod']).abs().mean():.3f}; "
          f"|ln(λ_titulares/λ_prev)| médio={np.log(p['lam_real_tit'] / p['lam_prod']).abs().mean():.3f}; descanso ≤3 dias: {p['curto'].mean():.1%}")

    bases = {"lb_prev": "lam_prod", "lb_real": "lam_real", "lb_real_tit": "lam_real_tit", "lb_prev_tit": "lam_prev_tit"}
    idx = pd.MultiIndex.from_frame(p[["match_id", "team_id"]])
    for alvo, col in bases.items():
        p[alvo] = aplicar_bivariada(p, col, ajustar_bivariada(tr, col)).reindex(idx).values
    return p


def avaliar_variantes(tr, te, variantes: dict, rng, base_nome: str):
    """Ajusta cada variante (offset + features) no treino e compara fora da
    amostra contra `base_nome`. Devolve (partidas, perdas, probs_1x2)."""
    print("\n=== Ajuste no treino (offset = ln λ_base) ===")
    lam_te = {}
    for nome, (base, cols) in variantes.items():
        b, se, nll = ajustar_offset(tr, cols, base)
        if cols:
            _, _, nll_b = ajustar_offset(tr, [], base)
            lrt = 2 * (nll_b - nll)
            coefs = " ".join(f"{c}={b[i + 1]:+.3f}(z {b[i + 1] / se[i + 1]:+.1f})" for i, c in enumerate(cols))
            print(f"  {nome:40s} {coefs}  LRT={lrt:.1f} (df {len(cols)}, p={stats.chi2.sf(lrt, len(cols)):.1e})")
        else:
            print(f"  {nome:40s} NLL treino={nll:.1f}")
        lam_te[nome] = pd.Series(aplicar_offset(te, cols, base, b), index=pd.MultiIndex.from_frame(te[["match_id", "team_id"]]))

    casa = te[te["is_home"]].set_index("match_id")
    fora = te[~te["is_home"]].set_index("match_id")
    partidas = pd.DataFrame({"th": casa["team_id"], "hg": casa["gols"], "rho": casa["rho"], "data": casa["data"],
                             "ta": fora["team_id"], "ag": fora["gols"]}).dropna(subset=["rho"])
    perdas, probs_1x2 = {}, {}
    for nome, s in lam_te.items():
        lh = s.loc[list(zip(partidas.index, partidas["th"]))].values
        la = s.loc[list(zip(partidas.index, partidas["ta"]))].values
        perdas[nome], probs_1x2[nome] = perdas_oos(partidas, lh, la)

    print(f"\n=== Fora da amostra: {len(partidas)} partidas — Δ perda vs '{base_nome}' (IC95% bootstrap por partida) ===")
    for mercado in perdas[base_nome]:
        print(f"  {mercado}: base={perdas[base_nome][mercado].mean():.4f}")
        for nome in perdas:
            if nome == base_nome:
                continue
            d = perdas[nome][mercado] - perdas[base_nome][mercado]
            bs = [d[rng.integers(0, len(d), len(d))].mean() for _ in range(N_BOOT)]
            print(f"    {nome:40s} Δ={d.mean():+.5f} [{np.percentile(bs, 2.5):+.5f},{np.percentile(bs, 97.5):+.5f}]")
    return partidas, perdas, probs_1x2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corte", default=CORTE_PADRAO)
    parser.add_argument("--entrada", help="painel time-partida de calibrar_potencia_lambda.py")
    parser.add_argument("--cache-dir", help="pasta pra guardar/reaproveitar os downloads (.pkl)")
    args = parser.parse_args()
    corte = pd.Timestamp(args.corte)
    rng = np.random.default_rng(SEED)

    p = preparar_painel(args.entrada, args.cache_dir, corte)
    tr, te = p[p["data"] < corte], p[p["data"] >= corte]
    variantes = {
        "base (XI previsto)": ("lb_prev", []),
        "escalação confirmada COMPLETA (vaza)": ("lb_real", []),
        "só titulares confirmados (limpa)": ("lb_real_tit", []),
        "previsto × titulares confirmados": ("lb_prev_tit", []),
        "descanso/sequência": ("lb_prev", ["curto", "curto_adv", "longo", "longo_adv"]),
        **{f"mando EWM meia-vida {hl}": ("lb_prev", [f"x_mando{hl}"]) for hl in MEIAS_VIDAS_MANDO},
        "titulares + descanso + mando300": ("lb_real_tit", ["curto", "curto_adv", "longo", "longo_adv", "x_mando300"]),
    }
    partidas, _, probs_1x2 = avaliar_variantes(tr, te, variantes, rng, "base (XI previsto)")

    ids = sorted(int(i) for i in partidas.index)
    odds = _cache(args.cache_dir, "odds_1x2_pinnacle",
                  lambda: odds_1x2_pinnacle(Rest(obter_env("SUPABASE_URL"), obter_env("SUPABASE_KEY")), ids))
    bloco_pinnacle(partidas, probs_1x2, odds, rng)
    bloco_w_cronologico(partidas, probs_1x2, odds, rng)


def bloco_pinnacle(partidas, probs_1x2, odds, rng):
    res = np.where(partidas["hg"] > partidas["ag"], 0, np.where(partidas["hg"] == partidas["ag"], 1, 2))
    print("\n=== 1X2 contra a Pinnacle sem vig (IC95% bootstrap; w = peso do modelo no log-pooling) ===")
    for momento in ("abertura", "fechamento"):
        pin = pinnacle_sem_vig(odds, momento)
        ok = np.isin(partidas.index, pin.index)
        y = res[ok]
        pp = pin.loc[partidas.index[ok], ["home", "draw", "away"]].values
        ll_pin = -np.log(np.clip(pp[np.arange(len(y)), y], 1e-4, 1))
        print(f"  Pinnacle {momento}: n={ok.sum()} partidas, log-loss={ll_pin.mean():.4f}")
        for nome in ("base (XI previsto)", "só titulares confirmados (limpa)", "previsto × titulares confirmados"):
            pm = probs_1x2[nome][ok]
            d = -np.log(np.clip(pm[np.arange(len(y)), y], 1e-4, 1)) - ll_pin
            bs = [d[rng.integers(0, len(d), len(d))].mean() for _ in range(N_BOOT)]
            w, se = peso_log_pooling(y, pp, pm)
            print(f"    {nome:36s} Δ vs Pinnacle={d.mean():+.4f} [{np.percentile(bs, 2.5):+.4f},{np.percentile(bs, 97.5):+.4f}]"
                  f"  w={w:+.3f} (SE {se:.3f}, z {w / se:+.1f})")


def bloco_w_cronologico(partidas, probs_1x2, odds, rng, variante="só titulares confirmados (limpa)"):
    """Validação cronológica do log-pooling: w estimado na 1ª metade (por data)
    das partidas com Pinnacle, aplicado na 2ª. Mede se a combinação
    p ∝ p_pin^(1−w)·p_mod^w bate a Pinnacle sozinha em partidas que o w nunca viu."""
    res = np.where(partidas["hg"] > partidas["ag"], 0, np.where(partidas["hg"] == partidas["ag"], 1, 2))
    print(f"\n=== Validação cronológica do w (variante: {variante}) — estima na 1ª metade, aplica na 2ª ===")
    for momento in ("abertura", "fechamento"):
        pin = pinnacle_sem_vig(odds, momento)
        ok = np.isin(partidas.index, pin.index)
        datas = partidas["data"].values[ok]
        ordem = np.argsort(datas, kind="stable")
        y = res[ok][ordem]
        pp = pin.loc[partidas.index[ok], ["home", "draw", "away"]].values[ordem]
        pm = probs_1x2[variante][ok][ordem]
        meio = len(y) // 2
        w, se = peso_log_pooling(y[:meio], pp[:meio], pm[:meio])
        z = (1 - w) * np.log(np.clip(pp[meio:], 1e-6, 1)) + w * np.log(np.clip(pm[meio:], 1e-6, 1))
        pc = np.exp(z - np.log(np.exp(z).sum(axis=1, keepdims=True)))
        yy = y[meio:]
        idx = np.arange(len(yy))
        d = -np.log(np.clip(pc[idx, yy], 1e-4, 1)) + np.log(np.clip(pp[meio:][idx, yy], 1e-4, 1))
        bs = [d[rng.integers(0, len(d), len(d))].mean() for _ in range(N_BOOT)]
        w2, se2 = peso_log_pooling(yy, pp[meio:], pm[meio:])
        print(f"  {momento:10s}: 1ª metade n={meio} w={w:+.3f} (z {w / se:+.1f}) | 2ª metade n={len(yy)} "
              f"combinação − Pinnacle = {d.mean():+.5f} [{np.percentile(bs, 2.5):+.5f},{np.percentile(bs, 97.5):+.5f}] "
              f"| w reestimado na 2ª metade={w2:+.3f} (z {w2 / se2:+.1f})")


if __name__ == "__main__":
    main()
