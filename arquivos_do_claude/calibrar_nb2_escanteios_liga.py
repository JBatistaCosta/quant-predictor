#!/usr/bin/env python3
"""Calibra o `r` (dispersão) da Binomial Negativa de escanteios (total do jogo) de UMA liga
que NÃO tem previsão do modelo em `model_stat_estimates` (hoje: Brasileirão Série A), com o
mesmo rigor já usado em defesas/assistências (`analisar_nb2_defesas.py`): MLE de α condicionado
no μ de cada jogo, teste de razão de verossimilhança na fronteira, IC por bootstrap e validação
fora da amostra por log-loss/Brier das linhas over/under.

Como não há μ do modelo para essa liga, o μ é construído aqui **walk-forward, sem vazamento**:
para cada jogo, só entram jogos da mesma liga ANTERIORES a ele.

    ataque(T) = média (encolhida p/ a média da liga) dos escanteios que T fez nos últimos K jogos
    defesa(T) = idem, dos escanteios que T sofreu
    μ_casa   = ataque(casa) × defesa(fora) / média_da_liga
    μ_fora   = ataque(fora) × defesa(casa) / média_da_liga
    μ_total  = c × (μ_casa + μ_fora)      (c = viés médio, ajustado SÓ nas temporadas de treino)

Ressalva de interpretação (importante): o α medido aqui é um TETO do α verdadeiro, e portanto o r
é um PISO. Qualquer erro do próprio μ (aqui, médias móveis ruidosas) entra no resíduo e parece
dispersão. Melhor o μ, mais justo o r. Por isso o script roda a sensibilidade a K e ao
encolhimento, e o número a usar em produção é o de menor dispersão que continua estável.

Uso (leitura pública; chave anon basta para ler):
    set SUPABASE_URL=https://<projeto>.supabase.co
    set SUPABASE_KEY=<anon ou service_role>
    python calibrar_nb2_escanteios_liga.py --liga-id 1
    python calibrar_nb2_escanteios_liga.py --liga-id 1 --gravar   # exige service_role; grava só se passar nos critérios
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict, deque

import numpy as np
import pandas as pd
import requests
from scipy.optimize import minimize_scalar
from scipy.stats import chi2, nbinom, poisson

TAMANHO_PAGINA = 1000
TAMANHO_LOTE_IDS = 120
N_MINIMO = 300
ALPHA_MINIMO = 1e-5
SEASON_CORTE_OOS = "2025"        # temporadas >= isso são validação; < isso calibram α e c
MIN_JOGOS_PREVIOS = 6            # cada time precisa de >= isso jogos anteriores na liga
K_PADRAO, PRIOR_PADRAO = 20, 6.0  # janela móvel e pseudo-jogos de encolhimento
LINHAS = (8.5, 9.5, 10.5, 11.5)
MODEL_NAME = "corners_negbin_walkforward_v1"


def env_obrigatoria(nome: str) -> str:
    v = (os.environ.get(nome) or "").strip()
    if not v:
        sys.exit(f"Defina {nome} como variável de ambiente (sem valor default hardcoded).")
    return v


class Api:
    def __init__(self, url: str, key: str):
        self.base = url.rstrip("/") + "/rest/v1"
        self.h = {"apikey": key, "Authorization": f"Bearer {key}"}

    def get_all(self, tabela: str, params: dict) -> list[dict]:
        linhas, inicio = [], 0
        while True:
            h = {**self.h, "Range-Unit": "items", "Range": f"{inicio}-{inicio + TAMANHO_PAGINA - 1}"}
            r = requests.get(f"{self.base}/{tabela}", params=params, headers=h, timeout=60)
            r.raise_for_status()
            bloco = r.json()
            linhas.extend(bloco)
            if len(bloco) < TAMANHO_PAGINA:
                return linhas
            inicio += TAMANHO_PAGINA


def carregar_jogos(api: Api, liga_id: int) -> pd.DataFrame:
    partidas = api.get_all("matches", {
        "select": "id,match_date,season,home_team_id,away_team_id",
        "league_id": f"eq.{liga_id}", "status": "eq.finished", "order": "match_date.asc,id.asc"})
    ids = [p["id"] for p in partidas]
    escanteios: dict[tuple[int, int], int] = {}
    for i in range(0, len(ids), TAMANHO_LOTE_IDS):
        lote = ids[i:i + TAMANHO_LOTE_IDS]
        for r in api.get_all("match_stats_fotmob", {
                "select": "match_id,team_id,corners", "match_id": f"in.({','.join(map(str, lote))})",
                "order": "id.asc"}):
            if r["corners"] is not None:
                escanteios[(r["match_id"], r["team_id"])] = int(r["corners"])
    linhas = []
    for p in partidas:
        h, a = escanteios.get((p["id"], p["home_team_id"])), escanteios.get((p["id"], p["away_team_id"]))
        if h is not None and a is not None:
            linhas.append({**p, "c_casa": h, "c_fora": a})
    return pd.DataFrame(linhas)


def montar_mu_walkforward(jogos: pd.DataFrame, k: int, prior: float) -> pd.DataFrame:
    """μ_total por jogo usando SÓ jogos anteriores (ordem cronológica já garantida pela query)."""
    fez: dict[int, deque] = defaultdict(lambda: deque(maxlen=k))
    sofreu: dict[int, deque] = defaultdict(lambda: deque(maxlen=k))
    soma_liga, n_liga = 0.0, 0
    saida = []
    for r in jogos.itertuples(index=False):
        casa, fora = r.home_team_id, r.away_team_id
        if n_liga > 0 and len(fez[casa]) >= MIN_JOGOS_PREVIOS and len(fez[fora]) >= MIN_JOGOS_PREVIOS:
            media = soma_liga / n_liga

            def encolhida(seq):
                return (sum(seq) + prior * media) / (len(seq) + prior)

            mu_casa = encolhida(fez[casa]) * encolhida(sofreu[fora]) / media
            mu_fora = encolhida(fez[fora]) * encolhida(sofreu[casa]) / media
            saida.append({"season": str(r.season), "mu_bruto": mu_casa + mu_fora, "y": r.c_casa + r.c_fora})
        fez[casa].append(r.c_casa); sofreu[casa].append(r.c_fora)
        fez[fora].append(r.c_fora); sofreu[fora].append(r.c_casa)
        soma_liga += r.c_casa + r.c_fora; n_liga += 2
    return pd.DataFrame(saida)


def loglik_nb(y: np.ndarray, mu: np.ndarray, alpha: float) -> float:
    if alpha < ALPHA_MINIMO / 10:
        return float(poisson.logpmf(y, mu).sum())
    r = 1.0 / alpha
    return float(nbinom.logpmf(y, r, r / (r + mu)).sum())


def ajustar_alpha(y: np.ndarray, mu: np.ndarray) -> float:
    res = minimize_scalar(lambda la: -loglik_nb(y, mu, float(np.exp(la))), bounds=(-12, 1), method="bounded")
    return float(np.exp(res.x))


def lrt(y: np.ndarray, mu: np.ndarray, alpha: float) -> tuple[float, float]:
    lr = max(0.0, 2 * (loglik_nb(y, mu, alpha) - loglik_nb(y, mu, 0.0)))
    return lr, 0.5 * float(chi2.sf(lr, df=1))  # mistura 50/50 χ²(0)/χ²(1): H0 na fronteira


def bootstrap_alpha(y: np.ndarray, mu: np.ndarray, b: int, semente: int = 7) -> np.ndarray:
    rng = np.random.default_rng(semente)
    n = len(y)
    return np.array([ajustar_alpha(y[i], mu[i]) for i in (rng.integers(0, n, n) for _ in range(b))])


def prob_over(mu: np.ndarray, linha: float, r: float | None) -> np.ndarray:
    x = int(np.floor(linha))
    if r is None or not np.isfinite(r):
        return 1 - poisson.cdf(x, mu)
    return 1 - nbinom.cdf(x, r, r / (r + mu))


def metricas_linhas(y: np.ndarray, mu: np.ndarray, r: float | None) -> tuple[float, float]:
    ll, br = [], []
    for linha in LINHAS:
        p = np.clip(prob_over(mu, linha, r), 1e-9, 1 - 1e-9)
        o = (y > linha).astype(float)
        ll.append(-np.mean(o * np.log(p) + (1 - o) * np.log(1 - p)))
        br.append(np.mean((p - o) ** 2))
    return float(np.mean(ll)), float(np.mean(br))


def formatar_r(alpha: float) -> str:
    return "∞ (Poisson)" if alpha < ALPHA_MINIMO else f"{1 / alpha:.1f}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--liga-id", type=int, default=1)
    ap.add_argument("--bootstrap", type=int, default=1000)
    ap.add_argument("--gravar", action="store_true", help="grava disp_r em league_model_params (exige service_role)")
    args = ap.parse_args()

    api = Api(env_obrigatoria("SUPABASE_URL"), env_obrigatoria("SUPABASE_KEY"))
    jogos = carregar_jogos(api, args.liga_id)
    print(f"Liga {args.liga_id}: {len(jogos)} jogos terminados com escanteios dos dois times.\n")

    # --- sensibilidade do μ (K, encolhimento): o r só é confiável se estável
    print("Sensibilidade ao μ walk-forward (α, r sobre TODAS as temporadas; c = viés médio):")
    especificacoes = {}
    for k, prior in ((10, 3.0), (20, 6.0), (30, 10.0), (38, 10.0)):
        df = montar_mu_walkforward(jogos, k, prior)
        c = df.y.sum() / df.mu_bruto.sum()
        a = ajustar_alpha(df.y.to_numpy(), c * df.mu_bruto.to_numpy())
        especificacoes[(k, prior)] = a
        print(f"  K={k:>2} prior={prior:>4}: n={len(df)}  c={c:.3f}  α={a:.4f}  r={formatar_r(a)}")

    df = montar_mu_walkforward(jogos, K_PADRAO, PRIOR_PADRAO)
    treino, teste = df[df.season < SEASON_CORTE_OOS], df[df.season >= SEASON_CORTE_OOS]
    c = treino.y.sum() / treino.mu_bruto.sum()
    y_tr, mu_tr = treino.y.to_numpy(), c * treino.mu_bruto.to_numpy()
    y_te, mu_te = teste.y.to_numpy(), c * teste.mu_bruto.to_numpy()

    print(f"\nEspecificação escolhida: K={K_PADRAO}, prior={PRIOR_PADRAO}.  c (treino) = {c:.3f}")
    print(f"Treino (<{SEASON_CORTE_OOS}): n={len(treino)}  |  OOS (>={SEASON_CORTE_OOS}): n={len(teste)}")
    print(f"Média real / prevista OOS: {y_te.mean():.2f} / {mu_te.mean():.2f}")

    alpha_tr = ajustar_alpha(y_tr, mu_tr)
    lr, p = lrt(y_tr, mu_tr, alpha_tr)
    print(f"\n[TREINO] α={alpha_tr:.4f}  r={formatar_r(alpha_tr)}  LR={lr:.2f}  p(fronteira)={p:.4g}")

    y_all, mu_all = df.y.to_numpy(), c * df.mu_bruto.to_numpy()
    alpha_all = ajustar_alpha(y_all, mu_all)
    lr_all, p_all = lrt(y_all, mu_all, alpha_all)
    boot = bootstrap_alpha(y_all, mu_all, args.bootstrap)
    lo, hi = np.percentile(boot, [2.5, 97.5])
    print(f"[TODAS ] α={alpha_all:.4f}  r={formatar_r(alpha_all)}  LR={lr_all:.2f}  p={p_all:.4g}  n={len(df)}")
    print(f"         IC95% bootstrap ({args.bootstrap}x): α∈[{lo:.4f}, {hi:.4f}]  →  r∈[{formatar_r(hi)}, {formatar_r(lo)}]")

    print("\nValidação OOS — média das linhas " + "/".join(map(str, LINHAS)) + " (menor = melhor):")
    candidatos = {"Poisson (r=∞)": None, f"NB r calibrado ({formatar_r(alpha_tr)})": (1 / alpha_tr if alpha_tr >= ALPHA_MINIMO else None),
                  "NB r=25": 25.0, "NB r=35": 35.3, "NB r=46.6 (agregado 5 ligas)": 46.6, "NB r=65.85 (padrão do app)": 65.85, "NB r=100": 100.0}
    for nome, r in candidatos.items():
        ll, br = metricas_linhas(y_te, mu_te, r)
        print(f"  {nome:<32} log-loss={ll:.5f}  Brier={br:.5f}")

    print("\nCalibração por tercil de μ (OOS, Over 10.5): frequência real vs prevista")
    tercis = pd.qcut(mu_te, 3, labels=False, duplicates="drop")
    r_cal = 1 / alpha_tr if alpha_tr >= ALPHA_MINIMO else None
    for t in sorted(set(tercis)):
        m = tercis == t
        real = (y_te[m] > 10.5).mean()
        print(f"  tercil {t + 1}: n={m.sum():>3}  μ̄={mu_te[m].mean():.2f}  real={real:.3f}  Poisson={prob_over(mu_te[m], 10.5, None).mean():.3f}  NB={prob_over(mu_te[m], 10.5, r_cal).mean():.3f}")

    passa = len(df) >= N_MINIMO and p_all < 0.05 and alpha_all > ALPHA_MINIMO
    print(f"\nCritérios de persistência (n≥{N_MINIMO}, LRT p<0.05, α>{ALPHA_MINIMO}): {'PASSA' if passa else 'NÃO PASSA'}")
    if not passa:
        return
    valor = 1.0 / alpha_all
    sql = ("insert into league_model_params (league_id, model_name, stat, param_name, param_value) "
           f"values ({args.liga_id}, '{MODEL_NAME}', 'corners', 'disp_r', {valor:.4f});")
    print(f"disp_r proposto = {valor:.2f}\nSQL: {sql}")
    if args.gravar:
        h = {**api.h, "Content-Type": "application/json", "Prefer": "return=minimal"}
        r = requests.post(f"{api.base}/league_model_params", headers=h, timeout=60, json={
            "league_id": args.liga_id, "model_name": MODEL_NAME, "stat": "corners", "param_name": "disp_r", "param_value": round(valor, 4)})
        r.raise_for_status()
        print("Gravado em league_model_params.")


if __name__ == "__main__":
    main()
