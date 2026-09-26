#!/usr/bin/env python3
"""CLV (closing line value) no 1X2: o modelo antecipa o movimento da linha?

Item 2 da lista "o que melhorar nos mercados de times" (CONTEXTO_PROJETO.md,
25/09). O modelo perde da Pinnacle de FECHAMENTO, mas poderia bater a linha de
ABERTURA (menos eficiente) — se as apostas que ele escolhe cedo pegam preços
melhores que o fechamento justo, isso já é uma estratégia (apostar cedo).

Modelos (todos pré-escalação, válidos na abertura):
  * base (XI previsto)  — λ da produção calibrado (bivariada, PR #665)
  * hibrido_gols_v1     — λ do modelo macro (model_match_estimates)
  * só titulares        — ⚠️ REFERÊNCIA, não vale na abertura: usa a escalação
    confirmada, que só sai ~1h antes do jogo.

Medidas, por casa com capturas pré-jogo (abertura = 1ª captura
opening/pre_closing; fechamento = snapshot closing, senão a última captura):
  1. CLV das apostas do modelo na odd de ABERTURA da casa, EV_modelo > limiar:
     CLV = odd_abertura × p_Pinnacle_fechamento_sem_vig(seleção) − 1.
     Referência: CLV médio de TODAS as seleções na abertura daquela casa (o que
     uma aposta aleatória pega). Também: % de apostas em que a odd da casa caiu
     até o fechamento e yield real, com IC95% bootstrap.
  2. Regressão do movimento: Δp = p_fechamento − p_abertura (sem vig, mesma casa)
     contra (p_modelo − p_abertura), por seleção. Inclinação > 0 = o modelo prevê
     para onde a linha vai. IC95% bootstrap por partida.

Não usa captured_at como hora do jogo (fontes importadas gravam a hora da
importação — ver validar_totais_vs_mercado.py).

Uso:
    python arquivos_do_claude/validar_clv_1x2.py --entrada painel.pkl --cache-dir /tmp/info

Variáveis de ambiente: SUPABASE_URL, SUPABASE_KEY (chave pública basta). Só leitura.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.dirname(__file__))
import distribuicoes as dist  # noqa: E402
import validar_informacao_nova_lambda as vin  # noqa: E402
from validar_assistencia_jogador_walkforward import Rest, obter_env  # noqa: E402
from validar_totais_vs_mercado import _cache, baixar_hibrido  # noqa: E402

LIMIARES_EV = (0.0, 0.03, 0.06)
SELECOES = ("home", "draw", "away")
MIN_APOSTAS = 30


def odds_1x2_todas(rest: Rest, match_ids: list[int]) -> pd.DataFrame:
    """Todas as capturas 1X2 de todas as casas das partidas pedidas."""
    def lote(ids):
        linhas, offset = [], 0
        while True:
            pg = rest.get("odds_market", {
                "select": "id,match_id,bookmaker,selection,odds,captured_at,snapshot",
                "match_id": f"in.({','.join(map(str, ids))})", "market": "eq.1X2",
                "order": "id", "limit": 1000, "offset": offset})
            linhas += pg
            if len(pg) < 1000:
                return linhas
            offset += 1000

    with cf.ThreadPoolExecutor(12) as ex:
        partes = list(ex.map(lote, [match_ids[i:i + 50] for i in range(0, len(match_ids), 50)]))
    return pd.DataFrame([r for p in partes for r in p])


def pontas(odds_casa: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(abertura, fechamento) em formato largo match_id × seleção, para uma casa."""
    o = odds_casa[odds_casa["odds"] > 1.0].copy()
    o["t"] = pd.to_datetime(o["captured_at"], format="ISO8601", utc=True)
    pre = o[o["snapshot"].isin(["opening", "pre_closing"])].sort_values("t").groupby(["match_id", "selection"]).head(1)
    o["rank"] = (o["snapshot"] == "closing").astype(int)
    fech = o.sort_values(["rank", "t"]).groupby(["match_id", "selection"]).tail(1)
    larg = lambda d: d.pivot_table(index="match_id", columns="selection", values="odds").reindex(columns=list(SELECOES))  # noqa: E731
    return larg(pre).dropna(), larg(fech).dropna()


def sem_vig(larga: pd.DataFrame) -> pd.DataFrame:
    inv = 1 / larga
    return inv.div(inv.sum(axis=1), axis=0)


def ic(valores: np.ndarray, rng) -> tuple[float, float]:
    bs = [valores[rng.integers(0, len(valores), len(valores))].mean() for _ in range(vin.N_BOOT)]
    return float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))


def probs_hibrido(partidas: pd.DataFrame, hib: pd.DataFrame) -> np.ndarray:
    h = partidas.join(hib.set_index("match_id"), how="left")
    saida = []
    for lh, la, r in zip(h["lh_hib"].values, h["la_hib"].values, h["rho"].values):
        if not (np.isfinite(lh) and np.isfinite(la)):
            saida.append([np.nan] * 3)
            continue
        mk = dist.mercados_de_gols(dist.matriz_placares(lh, la, r))
        saida.append([mk[("1X2", "home")], mk[("1X2", "draw")], mk[("1X2", "away")]])
    return np.array(saida)


def bloco_clv(nome_modelo, P, partidas, odds, pin_fech, rng):
    res = np.where(partidas["hg"] > partidas["ag"], 0, np.where(partidas["hg"] == partidas["ag"], 1, 2))
    Pm = pd.DataFrame(P, index=partidas.index, columns=list(SELECOES))
    print(f"\n===== {nome_modelo} =====")
    for casa in ("pinnacle", "bet365", "betano"):
        ab, fe = pontas(odds[odds["bookmaker"] == casa])
        ids = ab.index.intersection(fe.index).intersection(pin_fech.index).intersection(Pm.dropna().index)
        if len(ids) < 100:
            continue
        a, f, pf, pm = ab.loc[ids].values, fe.loc[ids].values, pin_fech.loc[ids].values, Pm.loc[ids].values
        y = pd.Series(res, index=partidas.index).loc[ids].values
        clv_todos = (a * pf - 1).ravel()
        print(f"  {casa:8s} n={len(ids)} partidas | CLV médio de TODAS as seleções na abertura: {clv_todos.mean():+.2%} "
              f"(margem média abertura {(1 / a).sum(axis=1).mean() - 1:.2%})")
        for lim in LIMIARES_EV:
            sel = pm * a - 1 > lim
            if sel.sum() < MIN_APOSTAS:
                continue
            clv = (a * pf - 1)[sel]
            caiu = (f < a)[sel]
            ganho = np.where(np.arange(3)[None, :] == y[:, None], a - 1, -1.0)[sel]
            lo, hi = ic(clv, rng)
            ylo, yhi = ic(ganho, rng)
            print(f"    EV>{lim:.0%}: {sel.sum():5d} apostas | CLV={clv.mean():+.2%} [{lo:+.2%},{hi:+.2%}] "
                  f"| odd caiu depois em {caiu.mean():.0%} | yield={ganho.mean():+.1%} [{ylo:+.1%},{yhi:+.1%}]")
        # regressão do movimento (mesma casa, sem vig)
        pa, pc = sem_vig(ab.loc[ids]).values, sem_vig(fe.loc[ids]).values
        x, d = (pm - pa).ravel(), (pc - pa).ravel()
        b = np.polyfit(x, d, 1)[0]
        n = len(ids)
        bs = []
        for _ in range(vin.N_BOOT):
            k = rng.integers(0, n, n)
            bs.append(np.polyfit((pm[k] - pa[k]).ravel(), (pc[k] - pa[k]).ravel(), 1)[0])
        print(f"    movimento: Δp(fech−abert) ~ (p_modelo − p_abertura): inclinação={b:+.4f} "
              f"[{np.percentile(bs, 2.5):+.4f},{np.percentile(bs, 97.5):+.4f}]  (desvio médio |p_mod−p_ab|={np.abs(x).mean():.3f}; |Δp| médio={np.abs(d).mean():.4f})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corte", default=vin.CORTE_PADRAO)
    parser.add_argument("--entrada", help="painel time-partida de calibrar_potencia_lambda.py")
    parser.add_argument("--cache-dir", help="pasta pra guardar/reaproveitar os downloads (.pkl)")
    args = parser.parse_args()
    corte = pd.Timestamp(args.corte)
    rng = np.random.default_rng(vin.SEED)

    p = vin.preparar_painel(args.entrada, args.cache_dir, corte)
    tr, te = p[p["data"] < corte], p[p["data"] >= corte]
    variantes = {"base (XI previsto)": ("lb_prev", []),
                 "só titulares (⚠️ usa escalação — NÃO vale na abertura)": ("lb_real_tit", [])}
    partidas, _, probs = vin.avaliar_variantes(tr, te, variantes, rng, "base (XI previsto)")

    rest = None

    def cliente():
        nonlocal rest
        rest = rest or Rest(obter_env("SUPABASE_URL"), obter_env("SUPABASE_KEY"))
        return rest

    ids = sorted(int(i) for i in partidas.index)
    odds = _cache(args.cache_dir, "odds_1x2_todas", lambda: odds_1x2_todas(cliente(), ids))
    hib = _cache(args.cache_dir, "hibrido_lambda", lambda: baixar_hibrido(cliente()))
    probs["hibrido_gols_v1"] = probs_hibrido(partidas, hib)

    pin_fech = vin.pinnacle_sem_vig(odds[odds["bookmaker"] == "pinnacle"], "fechamento")[list(SELECOES)]
    print(f"\nOdds 1X2: {len(odds)} capturas em {odds['match_id'].nunique()} partidas; Pinnacle fechamento em {len(pin_fech)}")
    print(odds.groupby(["bookmaker", "snapshot"])["match_id"].nunique().unstack(fill_value=0).to_string())
    for nome in ("base (XI previsto)", "hibrido_gols_v1", "só titulares (⚠️ usa escalação — NÃO vale na abertura)"):
        bloco_clv(nome, probs[nome], partidas, odds, pin_fech, rng)


if __name__ == "__main__":
    main()
