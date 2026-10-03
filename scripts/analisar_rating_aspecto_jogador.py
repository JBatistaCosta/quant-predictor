#!/usr/bin/env python3
"""Rating de jogador por ASPECTO (criação e defesa), AJUSTADO por papel, mando e força do time.

Por que existe: `analisar_rating_jogador.py` (03/10/2026, CONTEXTO_PROJETO.md) mostrou que o rating geral (Elo da nota
do FotMob) não prevê melhor que uma média simples e que a persistência por aspecto é alta, mas MISTURA habilidade com
papel tático (lateral cria chances por função). Aqui o papel/contexto é retirado antes de medir a habilidade.

MÉTODO (só leitura, nada é gravado):
  - Métricas por 90 min: criação = chances criadas; defesa = desarmes + interceptações (goleiros ficam de fora).
  - Contexto esperado = regressão (ajustada só nos jogos ANTES de `--corte`, padrão 2025-01-01) da métrica sobre o
    PAPEL no jogo (position_id de titular; reserva usa a posição mais frequente do jogador), mando e diferença de Elo
    do time. Resíduo = métrica real - esperada.
  - Habilidade = média do resíduo ponderada por minutos, encolhida para 0 com peso `K` (padrão 10 jogos de 90 min),
    calculada SÓ com o passado de cada jogador. Previsão do próximo jogo = esperado + habilidade.
  - Avaliação fora da amostra (jogos depois do corte, >= 5 jogos anteriores): R² só do contexto, da média bruta do
    jogador e de contexto + habilidade; escala ótima da habilidade (confiabilidade); correlação split-half (resíduo
    médio em jogos pares x ímpares, jogadores com >= 40 jogos). A NOTA GERAL entra como régua de comparação.

Variáveis de ambiente: SUPABASE_URL, SUPABASE_KEY (chave pública basta). Uso:
    python scripts/analisar_rating_aspecto_jogador.py --cache-dir /tmp/cache_rating   # reaproveita o cache do outro script
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analisar_rating_jogador as base
from analisar_desfalques_lista_do_jogo import baixar

PRIOR_K = 10.0
CORTE = "2025-01-01"
MIN_JOGOS_PAPEL = 3000     # papéis (position_id) com menos linhas viram um grupo único (-1)
ASPECTOS = {"criação (chances criadas/90)": "chances90", "defesa (desarmes+interceptações/90)": "def90",
            "nota geral do FotMob (régua de comparação)": "nota90"}


def preparar_aspectos(S: pd.DataFrame, L: pd.DataFrame) -> pd.DataFrame:
    """Acrescenta a `S` (saída de `analisar_rating_jogador.preparar`) o `papel` de cada jogo e as métricas por 90 min.
    Fica sem goleiros, sem linhas sem papel/força do time; `S` já vem em ordem cronológica."""
    L = L.drop_duplicates(["match_id", "team_id", "fotmob_player_id"])[["match_id", "team_id", "fotmob_player_id", "is_starter", "position_id"]]
    S = S.merge(L, on=["match_id", "team_id", "fotmob_player_id"], how="left")
    titular = L[(L.is_starter == True) & (L.position_id > 0)]  # noqa: E712
    modal = titular.groupby("fotmob_player_id").position_id.agg(lambda s: s.mode().iloc[0]).rename("pos_modal")
    S = S.merge(modal, on="fotmob_player_id", how="left")
    S["papel"] = np.where((S.is_starter == True) & (S.position_id > 0), S.position_id, S.pos_modal)  # noqa: E712
    S = S[S.papel.notna() & (S.papel != 11) & (S.papel > 0) & S.elo_diff.notna()].copy()
    S["papel"] = np.where(S.papel.map(S.papel.value_counts()) >= MIN_JOGOS_PAPEL, S.papel, -1)
    S["chances90"] = pd.to_numeric(S.chances_created, errors="coerce") / S.min90
    S["def90"] = (pd.to_numeric(S.tackles, errors="coerce") + pd.to_numeric(S.interceptions, errors="coerce")) / S.min90
    S["nota90"] = S.rating
    return S.sort_values(["d", "match_id", "id"]).reset_index(drop=True)


def contexto_esperado(S: pd.DataFrame, ycol: str, corte: str = CORTE) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(esperado, resíduo, ok) da métrica `ycol`: regressão sobre papel + mando + dif. de Elo/100, ajustada antes de `corte`."""
    y = S[ycol].values.astype(float)
    ok = np.isfinite(y)
    papeis = pd.get_dummies(S.papel).values.astype(float)[:, 1:]
    X = np.column_stack([np.ones(len(S)), S.elo_diff.values / 100.0, S.casa.values, papeis])
    treino = ok & (S.d < corte).values
    b = np.linalg.lstsq(X[treino], y[treino], rcond=None)[0]
    esperado = X @ b
    return esperado, y - esperado, ok


def habilidade_passado(player_ids: np.ndarray, y: np.ndarray, res: np.ndarray, w: np.ndarray, ok: np.ndarray, k: float = PRIOR_K,
                       prior_bruto: float = 0.0) -> dict:
    """Só com o PASSADO de cada jogador: habilidade (resíduo médio ponderado por minutos, encolhido para 0 com peso `k`),
    média bruta encolhida para `prior_bruto`, nº de jogos anteriores; e `final` = habilidade depois do último jogo."""
    n = len(player_ids)
    sk, raw, n_prev = np.full(n, np.nan), np.full(n, np.nan), np.zeros(n)
    sr: dict = {}
    sy: dict = {}
    sw: dict = {}
    cn: dict = {}
    for i in range(n):
        p = player_ids[i]
        c = cn.get(p, 0)
        n_prev[i] = c
        if c > 0:
            sk[i] = sr[p] / (sw[p] + k)
            raw[i] = (sy[p] + k * prior_bruto) / (sw[p] + k)
        if ok[i]:
            sr[p] = sr.get(p, 0.0) + res[i] * w[i]
            sy[p] = sy.get(p, 0.0) + y[i] * w[i]
            sw[p] = sw.get(p, 0.0) + w[i]
            cn[p] = c + 1
    final = {p: sr[p] / (sw[p] + k) for p in sr}
    return {"habilidade": sk, "bruta": raw, "n_prev": n_prev, "final": final, "jogos": cn}


def split_half(player_ids: np.ndarray, res: np.ndarray, w: np.ndarray, ok: np.ndarray, min_jogos: int = 40) -> tuple[float, int]:
    """Correlação entre o resíduo médio (ponderado) dos jogos pares e dos ímpares de cada jogador com >= `min_jogos` jogos."""
    d = pd.DataFrame({"p": player_ids, "r": res, "w": w})[ok]
    d["k"] = d.groupby("p").cumcount()
    d = d[d.groupby("p").p.transform("size") >= min_jogos]
    d["par"] = d.k % 2
    g = d.groupby(["p", "par"]).apply(lambda x: np.average(x.r, weights=x.w)).unstack().dropna()
    return float(g[0].corr(g[1])), len(g)


def avaliar(S: pd.DataFrame, ycol: str, k: float = PRIOR_K, corte: str = CORTE) -> dict:
    esperado, res, ok = contexto_esperado(S, ycol, corte)
    y = S[ycol].values.astype(float)
    treino = ok & (S.d < corte).values
    prior_bruto = float(np.nanmean(y[treino]))
    h = habilidade_passado(S.player_id.values, y, res, S.min90.values, ok, k, prior_bruto)
    teste = ok & (S.d >= corte).values & (h["n_prev"] >= 5)

    def r2(pred):
        return 1 - np.sum((y[teste] - pred[teste]) ** 2) / np.sum((y[teste] - y[teste].mean()) ** 2)

    escala = float(np.sum(res[teste] * h["habilidade"][teste]) / np.sum(h["habilidade"][teste] ** 2))
    sh, n_sh = split_half(S.player_id.values, res, S.min90.values, ok)
    return {"n_teste": int(teste.sum()), "r2_contexto": r2(esperado), "r2_media_bruta": r2(h["bruta"]),
            "r2_contexto_habilidade": r2(esperado + h["habilidade"]), "escala_otima": escala,
            "split_half": sh, "n_split_half": n_sh, "habilidade_final": h["final"], "jogos": h["jogos"], "prior_bruto": prior_bruto}


def ranking(S: pd.DataFrame, res: dict, nomes: dict, ycol: str, topo: int = 10, min_jogos: int = 60) -> pd.DataFrame:
    """Melhores jogadores (>= `min_jogos` jogos) pela habilidade final: quanto acima do esperado para o papel (métrica/90)."""
    papel = S.groupby("player_id").papel.agg(lambda s: s.mode().iloc[0])
    linhas = [{"jogador": nomes.get(p, p), "papel (position_id)": int(papel[p]), "jogos": res["jogos"][p], "acima do esperado /90": h}
              for p, h in res["habilidade_final"].items() if res["jogos"].get(p, 0) >= min_jogos and p in papel.index]
    return pd.DataFrame(linhas).sort_values("acima do esperado /90", ascending=False).head(topo).reset_index(drop=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache-dir", default=None)
    ap.add_argument("--corte", default=CORTE)
    args = ap.parse_args()
    url, chave = os.environ.get("SUPABASE_URL", ""), os.environ.get("SUPABASE_KEY", "")
    if not url or not chave:
        sys.exit("SUPABASE_URL e SUPABASE_KEY precisam estar definidos.")
    d = base.carregar(url, chave, args.cache_dir)
    S0, _ = base.preparar(d)
    S = preparar_aspectos(S0, d["L"])
    caminho = os.path.join(args.cache_dir, "jogadores.pkl") if args.cache_dir else None
    J = pd.read_pickle(caminho) if caminho and os.path.exists(caminho) else baixar(url, chave, "players", "id,name", passo=8000, workers=4)
    if caminho and not os.path.exists(caminho):
        J.to_pickle(caminho)
    nomes = dict(zip(J.id, J.name))
    print(f"linhas (sem goleiros): {len(S)} | corte {args.corte} | prior {PRIOR_K:.0f} jogos\n")
    for nome, ycol in ASPECTOS.items():
        r = avaliar(S, ycol, PRIOR_K, args.corte)
        print(f"== {nome}")
        print(f"   R² só contexto (papel+mando+força) {r['r2_contexto']:.4f} | média bruta do jogador {r['r2_media_bruta']:.4f} | "
              f"contexto + habilidade {r['r2_contexto_habilidade']:.4f} (ganho {100 * (r['r2_contexto_habilidade'] - r['r2_contexto']):+.2f} pp)")
        print(f"   escala ótima da habilidade {r['escala_otima']:.2f} | split-half {r['split_half']:.3f} ({r['n_split_half']} jogadores) | n teste {r['n_teste']}")
        if ycol != "nota90":
            print(ranking(S, r, nomes, ycol).round(2).to_string())
        print()


if __name__ == "__main__":
    main()
