#!/usr/bin/env python3
"""Auditoria de calibração do EV das apostas hipotéticas (saída de scripts/calcular_ev_mercados.py).

Pergunta: quando o modelo diz "EV de +X%", o retorno REALIZADO é +X%? Três análises:

  1. Baldes de EV ([0,2), [2,4), [4,7), [7,10), [10,15), >=15%): N, EV médio previsto,
     ROI realizado (soma do lucro / soma do stake), razão Observado/Esperado
     (acertos / soma de p_modelo) e CLV médio.
  2. Regressão de fidelidade ROI_b = beta * EV_b + alpha sobre os baldes (OLS), com teste
     de beta = 1 e R2. Com 6 pontos e 4 graus de liberdade o teste tem pouco poder, então
     também se reporta a mesma regressão no nível da aposta (erro-padrão agrupado por partida).
  3. Teste t unilateral do retorno médio por aposta (H0: média <= 0), simples e com erro
     agrupado por partida (as seleções de uma mesma partida não são independentes).

Por padrão exclui as apostas marcadas `anomalia_dados` (EV > +35% ou odds suspeitas): elas
exigem auditoria manual antes de entrar na amostra (`--incluir-anomalias` para ver o efeito).

O que NÃO provar: ROI positivo numa amostra pequena é o padrão clássico de ruído (ver
api/backtest-betting.js). Sem odds de fechamento o CLV fica vazio; hoje só ~10% das
apostas têm CLV. Resultado esperado de um modelo SEM informação além do mercado: ROI
próximo de -margem da casa e beta < 1 (o EV "previsto" é, em boa parte, erro do modelo).

Entrada: --csv (padrão ev_apostas_elo_xg.csv) ou --supabase (tabela ev_apostas_hipoteticas;
SUPABASE_URL e SUPABASE_KEY por variável de ambiente, sem default).
Saída: tabela no console + --json (dados p/ o frontend) + --svg (gráfico de calibração).

Uso:
    python arquivos_do_claude/analisar_calibracao_ev.py --csv ev.csv --json ev.json --svg ev.svg
"""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import sys

import numpy as np
import pandas as pd
from scipy import stats

FAIXAS = [(0.00, 0.02), (0.02, 0.04), (0.04, 0.07), (0.07, 0.10), (0.10, 0.15), (0.15, math.inf)]


def rotulo_faixa(lo: float, hi: float) -> str:
    return f">={lo:.0%}" if math.isinf(hi) else f"[{lo:.0%}, {hi:.0%})"


# --------------------------------------------------------------------------- análises
def estratificar(df: pd.DataFrame, faixas=FAIXAS) -> pd.DataFrame:
    """Uma linha por balde de EV (apostas com EV fora de todas as faixas são ignoradas)."""
    linhas = []
    for lo, hi in faixas:
        b = df[(df.ev_estimado >= lo) & (df.ev_estimado < hi)]
        n = len(b)
        if n == 0:
            linhas.append({"faixa": rotulo_faixa(lo, hi), "n": 0})
            continue
        lucro = b.retorno_unidade * b.stake_flat
        clv = b.clv.dropna()
        linhas.append({
            "faixa": rotulo_faixa(lo, hi), "n": n,
            "ev_medio": float(b.ev_estimado.mean()),
            "roi": float(lucro.sum() / b.stake_flat.sum()),
            "roi_ep": float(lucro.std(ddof=1) / math.sqrt(n) / b.stake_flat.mean()) if n > 1 else float("nan"),
            "acertos": int(b.resultado_real.sum()),
            "o_sobre_e": float(b.resultado_real.sum() / b.prob_modelo.sum()),
            "clv_medio": float(clv.mean()) if len(clv) else float("nan"),
            "n_clv": int(len(clv)),
        })
    return pd.DataFrame(linhas)


def regressao_baldes(baldes: pd.DataFrame) -> dict:
    """OLS ROI = beta * EV + alpha sobre os baldes não vazios; testa beta = 1 (t, n_baldes - 2 gl)."""
    b = baldes[baldes.n > 0]
    if len(b) < 3:
        return {"n_baldes": int(len(b)), "aviso": "menos de 3 baldes: regressão não estimável"}
    r = stats.linregress(b.ev_medio.to_numpy(), b.roi.to_numpy())
    gl = len(b) - 2
    t1 = (r.slope - 1.0) / r.stderr if r.stderr > 0 else float("nan")
    return {
        "n_baldes": int(len(b)), "beta": float(r.slope), "alpha": float(r.intercept), "r2": float(r.rvalue ** 2),
        "ep_beta": float(r.stderr), "t_beta_igual_1": float(t1), "p_beta_igual_1": float(2 * stats.t.sf(abs(t1), gl)),
        "p_beta_igual_0": float(r.pvalue), "gl": gl,
    }


def _ep_agrupado(x: np.ndarray, residuos: np.ndarray, grupos: np.ndarray) -> np.ndarray:
    """Covariância 'sanduíche' agrupada (CR0): (X'X)^-1 (sum_g X_g' u_g u_g' X_g) (X'X)^-1."""
    bread = np.linalg.inv(x.T @ x)
    meat = np.zeros((x.shape[1], x.shape[1]))
    for g in np.unique(grupos):
        s = x[grupos == g].T @ residuos[grupos == g]
        meat += np.outer(s, s)
    return bread @ meat @ bread


def regressao_apostas(df: pd.DataFrame) -> dict:
    """OLS retorno_i = alpha + beta * EV_i no nível da aposta, erro-padrão agrupado por partida."""
    if len(df) < 30 or df.ev_estimado.std() == 0:
        return {"n": int(len(df)), "aviso": "amostra pequena/sem variação de EV"}
    x = np.column_stack([np.ones(len(df)), df.ev_estimado.to_numpy()])
    y = df.retorno_unidade.to_numpy()
    coef, *_ = np.linalg.lstsq(x, y, rcond=None)
    res = y - x @ coef
    ep = np.sqrt(np.diag(_ep_agrupado(x, res, df.partida_id.to_numpy())))
    t1, t0 = (coef[1] - 1.0) / ep[1], coef[1] / ep[1]
    return {"n": int(len(df)), "alpha": float(coef[0]), "beta": float(coef[1]), "ep_beta": float(ep[1]),
            "t_beta_igual_1": float(t1), "p_beta_igual_1": float(2 * stats.norm.sf(abs(t1))),
            "p_beta_igual_0": float(2 * stats.norm.sf(abs(t0)))}


def teste_t_retorno(df: pd.DataFrame) -> dict:
    """t = media / (sd / sqrt(N)); p unilateral (H1: retorno médio > 0). Também com erro agrupado por partida."""
    r = df.retorno_unidade.to_numpy()
    n = len(r)
    if n < 2:
        return {"n": int(n), "aviso": "menos de 2 apostas"}
    media, dp = float(r.mean()), float(r.std(ddof=1))
    t = media / (dp / math.sqrt(n)) if dp > 0 else float("nan")
    soma = df.groupby("partida_id").retorno_unidade.sum()
    cont = df.groupby("partida_id").retorno_unidade.count()
    ep_cl = math.sqrt(float(((soma - media * cont) ** 2).sum())) / n
    t_cl = media / ep_cl if ep_cl > 0 else float("nan")
    return {"n": int(n), "retorno_medio": media, "desvio_padrao": dp, "t": float(t), "p_unilateral": float(stats.t.sf(t, n - 1)),
            "ep_agrupado": ep_cl, "t_agrupado": float(t_cl), "p_unilateral_agrupado": float(stats.norm.sf(t_cl)),
            "ic95_retorno_medio": [media - 1.96 * ep_cl, media + 1.96 * ep_cl]}


def auditar(df: pd.DataFrame, incluir_anomalias: bool = False) -> dict:
    total = len(df)
    n_anom = int(df.anomalia_dados.sum()) if "anomalia_dados" in df else 0
    base = df if incluir_anomalias else df[~df.anomalia_dados.astype(bool)]
    base = base[base.ev_estimado >= 0]
    baldes = estratificar(base)
    return {
        "n_registrado": int(total), "n_anomalias_excluidas": 0 if incluir_anomalias else n_anom, "n_analisado": int(len(base)),
        "baldes": json.loads(baldes.to_json(orient="records")),
        "regressao_baldes": regressao_baldes(baldes),
        "regressao_apostas": regressao_apostas(base),
        "teste_t": teste_t_retorno(base),
        "clv": {"n": int(base.clv.notna().sum()), "medio": float(base.clv.mean()) if base.clv.notna().any() else None},
    }


# --------------------------------------------------------------------------- saída
def _f(v, fmt="{:+.2%}"):
    return "-" if v is None or (isinstance(v, float) and math.isnan(v)) else fmt.format(v)


def imprimir(res: dict) -> None:
    print(f"Apostas registradas: {res['n_registrado']} | anomalias excluídas: {res['n_anomalias_excluidas']} | analisadas: {res['n_analisado']}\n")
    print(f"{'faixa de EV':<13}{'N':>6}{'EV médio':>10}{'ROI':>9}{'± EP':>8}{'O/E':>7}{'CLV médio':>11}{'(n CLV)':>9}")
    for b in res["baldes"]:
        if b["n"] == 0:
            print(f"{b['faixa']:<13}{0:>6}")
            continue
        print(f"{b['faixa']:<13}{b['n']:>6}{_f(b['ev_medio']):>10}{_f(b['roi']):>9}{_f(b['roi_ep'], '{:.2%}'):>8}"
              f"{_f(b['o_sobre_e'], '{:.3f}'):>7}{_f(b['clv_medio']):>11}{b['n_clv']:>9}")
    rb, ra, tt = res["regressao_baldes"], res["regressao_apostas"], res["teste_t"]
    print("\nRegressão de fidelidade (ROI = beta*EV + alpha; beta=1 é EV perfeitamente calibrado)")
    if "beta" in rb:
        print(f"  baldes ({rb['n_baldes']} pontos, {rb['gl']} gl): beta={rb['beta']:.3f} ± {rb['ep_beta']:.3f}  alpha={rb['alpha']:+.3f}  R²={rb['r2']:.3f}"
              f"  | beta=1: p={rb['p_beta_igual_1']:.3f}  beta=0: p={rb['p_beta_igual_0']:.3f}")
    else:
        print("  baldes:", rb.get("aviso"))
    if "beta" in ra:
        print(f"  apostas (N={ra['n']}, EP agrupado por partida): beta={ra['beta']:.3f} ± {ra['ep_beta']:.3f}  alpha={ra['alpha']:+.3f}"
              f"  | beta=1: p={ra['p_beta_igual_1']:.3f}  beta=0: p={ra['p_beta_igual_0']:.3f}")
    print("\nTeste t do retorno por aposta (stake flat 1; H1: retorno médio > 0)")
    if "t" in tt:
        print(f"  N={tt['n']}  média={tt['retorno_medio']:+.4f}  dp={tt['desvio_padrao']:.3f}  t={tt['t']:.2f}  p unilateral={tt['p_unilateral']:.4f}")
        print(f"  erro agrupado por partida: t={tt['t_agrupado']:.2f}  p unilateral={tt['p_unilateral_agrupado']:.4f}  "
              f"IC95% da média: [{tt['ic95_retorno_medio'][0]:+.4f}, {tt['ic95_retorno_medio'][1]:+.4f}]")
    print(f"\nCLV: {res['clv']['n']} apostas com odd de fechamento" + (f", média {res['clv']['medio']:+.2%}" if res["clv"]["medio"] is not None else ""))


def gerar_svg(res: dict) -> str:
    """Dispersão EV previsto (x) vs ROI realizado (y) por balde, ±1,96 EP, com a diagonal ideal (beta=1)."""
    pts = [b for b in res["baldes"] if b["n"] > 0]
    L, R, T, B, W, H = 64, 24, 28, 52, 640, 400
    xs = [b["ev_medio"] for b in pts] + [0.0]
    ys = [b["roi"] + s * 1.96 * b["roi_ep"] for b in pts for s in (-1, 1) if not math.isnan(b["roi_ep"])] + [b["roi"] for b in pts] + [0.0]
    xmax = max(xs) * 1.1 + 0.005
    lo, hi = min(ys), max(ys + [xmax])
    pad = (hi - lo) * 0.08 or 0.05
    lo, hi = lo - pad, hi + pad
    X = lambda v: L + v / xmax * (W - L - R)
    Y = lambda v: T + (hi - v) / (hi - lo) * (H - T - B)
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" role="img" aria-label="Calibração do EV: ROI realizado contra EV previsto por faixa">',
         "<style>:root{--fg:#1f2328;--mut:#6b7280;--grade:#e5e7eb;--ponto:#2563eb;--ref:#9ca3af;--bg:#fff}"
         "@media (prefers-color-scheme:dark){:root{--fg:#e6edf3;--mut:#9ca3af;--grade:#30363d;--ponto:#60a5fa;--ref:#6b7280;--bg:#0d1117}}"
         "text{font:12px system-ui,sans-serif;fill:var(--fg)}.m{fill:var(--mut)}</style>",
         f'<rect width="{W}" height="{H}" fill="var(--bg)"/>']
    passo = next((p for p in (0.02, 0.05, 0.10, 0.20) if (hi - lo) / p <= 7), 0.50)
    v = math.ceil(lo / passo) * passo
    while v <= hi + 1e-9:  # grade horizontal + rótulos do eixo y em passos redondos
        o.append(f'<line x1="{L}" x2="{W - R}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="var(--grade)"/>'
                 f'<text class="m" x="{L - 8}" y="{Y(v) + 4:.1f}" text-anchor="end">{v:+.0%}</text>')
        v += passo
    for k in range(5):
        v = xmax * k / 4
        o.append(f'<text class="m" x="{X(v):.1f}" y="{H - B + 18}" text-anchor="middle">{v:.0%}</text>')
    o.append(f'<line x1="{L}" x2="{W - R}" y1="{Y(0):.1f}" y2="{Y(0):.1f}" stroke="var(--ref)" stroke-width="1"/>')
    o.append(f'<line x1="{X(0):.1f}" y1="{Y(0):.1f}" x2="{X(xmax):.1f}" y2="{Y(xmax):.1f}" stroke="var(--ref)" stroke-width="2" stroke-dasharray="6 4"/>'
             f'<text class="m" x="{X(xmax) - 4:.1f}" y="{Y(xmax) - 6:.1f}" text-anchor="end">calibração perfeita (β = 1)</text>')
    for b in pts:
        cx, cy = X(b["ev_medio"]), Y(b["roi"])
        if not math.isnan(b["roi_ep"]):
            o.append(f'<line x1="{cx:.1f}" x2="{cx:.1f}" y1="{Y(b["roi"] - 1.96 * b["roi_ep"]):.1f}" y2="{Y(b["roi"] + 1.96 * b["roi_ep"]):.1f}" stroke="var(--ponto)" stroke-width="2"/>')
        tip = html.escape(f"{b['faixa']}: N={b['n']}, EV previsto {b['ev_medio']:+.1%}, ROI {b['roi']:+.1%}, O/E {b['o_sobre_e']:.2f}")
        o.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="6" fill="var(--ponto)" stroke="var(--bg)" stroke-width="2"><title>{tip}</title></circle>'
                 f'<text class="m" x="{cx:.1f}" y="{H - B - 6}" text-anchor="middle">{b["n"]}</text>')
    o.append(f'<text x="{(L + W - R) / 2:.0f}" y="{H - 12}" text-anchor="middle">EV médio previsto da faixa (número no pé = N de apostas)</text>'
             f'<text transform="translate(16 {(T + H - B) / 2:.0f}) rotate(-90)" text-anchor="middle">ROI realizado (barras: IC 95%)</text></svg>')
    return "\n".join(o)


def carregar_supabase() -> pd.DataFrame:
    from supabase import create_client

    url, chave = os.environ.get("SUPABASE_URL", "").strip(), os.environ.get("SUPABASE_KEY", "").strip()
    if not url or not chave:
        sys.exit("--supabase exige SUPABASE_URL e SUPABASE_KEY.")
    cliente, linhas, pagina = create_client(url, chave), [], 0
    while True:
        bloco = (cliente.table("ev_apostas_hipoteticas").select("*").order("id").range(pagina * 1000, pagina * 1000 + 999).execute().data)
        linhas += bloco
        if len(bloco) < 1000:
            return pd.DataFrame(linhas)
        pagina += 1


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--csv", default="ev_apostas_elo_xg.csv")
    ap.add_argument("--supabase", action="store_true", help="lê a tabela ev_apostas_hipoteticas em vez do CSV")
    ap.add_argument("--incluir-anomalias", action="store_true")
    ap.add_argument("--json")
    ap.add_argument("--svg")
    args = ap.parse_args()
    df = carregar_supabase() if args.supabase else pd.read_csv(args.csv)
    res = auditar(df, args.incluir_anomalias)
    imprimir(res)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(res, fh, ensure_ascii=False, indent=2)
    if args.svg:
        with open(args.svg, "w", encoding="utf-8") as fh:
            fh.write(gerar_svg(res))


if __name__ == "__main__":
    main()
