#!/usr/bin/env python3
"""Mede os LIMITES do rating Elo-like de jogador (`player_ratings`, tarefa `player-elo` de
`api/model-maintenance.js`) e se rating por ASPECTO (finalização, criação, defesa) faz sentido.

Resultados de 03/10/2026 em CONTEXTO_PROJETO.md (667 mil jogos com >= 20 min, 17 mil jogadores).

DESENHO (só leitura, nada é gravado):
  1. Reproduz o cálculo do rating em Python com a config de `model_config` (k=20, nota neutra 6,8, escala 500,
     pesos gols 0,15 / assistências 0,10 / finalização 0,20 / criação 0,25) e confere com o histórico gravado
     (`player_rating_history.rating_antes`): correlação 0,9992, erro médio ~1 ponto.
  2. A) Prevê a PRÓXIMA nota do FotMob com o rating (com e sem bônus) e com baselines simples (média encolhida
     da carreira, EWMA), só com o passado; R² sozinho e acima dos controles (posição, mando, força do time).
  3. B-F) Quanto do rating é posição, força do time, liga, número de jogos e bônus.
  4. G) Persistência por ASPECTO: R² de prever a métrica por 90 min do PRÓXIMO jogo pela média encolhida do
     próprio jogador, por posição, contra a persistência da nota geral.

CUIDADOS: o alvo (nota do FotMob) é a mesma que alimenta o rating, então A mede persistência da nota e não
impacto no resultado; a persistência por aspecto mistura HABILIDADE com PAPEL tático (lateral cria mais chances que
zagueiro por função, não só por qualidade); xG por jogador existe só em ~40% dos jogos (ligas com cobertura).

Variáveis de ambiente: SUPABASE_URL, SUPABASE_KEY (chave pública basta). Uso:
    python scripts/analisar_rating_jogador.py --cache-dir /tmp/cache_rating   # ~10 min na 1ª vez
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analisar_desfalques_lista_do_jogo import baixar

CFG = dict(k=20.0, neutra=6.8, pg=0.15, pa=0.10, pf=0.20, pc=0.25, escala=500.0, minutos_min=20)
NOMES_POS = {0: "goleiro", 1: "defensor", 2: "meio", 3: "atacante"}
PRIOR_JOGOS = 10.0     # peso do prior (média da posição) na média encolhida
ALPHA_EWMA = 0.1       # memória de ~10 jogos
METRICAS_ASPECTO = {   # nome -> (coluna, por 90 min?)
    "chances criadas/90": ("chances_created", True), "xA/90": ("xa", True), "xG/90": ("xg", True),
    "chutes/90": ("total_shots", True), "desarmes+interc./90": ("def_acoes", True), "nota FotMob": ("rating", False),
}


# --------------------------------------------------------------------------- dados
def carregar(url: str, chave: str, cache_dir: str | None) -> dict:
    def com_cache(nome, fn):
        caminho = None
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)
            caminho = os.path.join(cache_dir, nome + ".pkl")
            if os.path.exists(caminho):
                return pd.read_pickle(caminho)
        df = fn()
        if caminho:
            df.to_pickle(caminho)
        return df

    return {
        "S": com_cache("stats_jog", lambda: baixar(url, chave, "match_player_stats_fotmob",
                       "id,match_id,team_id,fotmob_player_id,player_id,minutes_played,rating,goals,assists,xg,xa,total_shots,"
                       "tackles,interceptions,chances_created")),
        "H": com_cache("hist_rating", lambda: baixar(url, chave, "player_rating_history", "id,player_id,match_id,rating_antes")),
        "L": com_cache("lineup_pos", lambda: baixar(url, chave, "match_lineup_fotmob", "id,match_id,team_id,fotmob_player_id,is_starter,position_id")),
        "E": com_cache("elo_hist", lambda: baixar(url, chave, "team_elo_history", "id,match_id,team_id,rating_antes")),
        "P": com_cache("partidas", lambda: baixar(url, chave, "matches",
                       "id,match_date,home_team_id,away_team_id,league_id,status").query("status == 'finished'")),
        "G": com_cache("ligas", lambda: baixar(url, chave, "leagues", "id,name")),
    }


def grupo_posicao(p) -> float:
    """position_id do FotMob -> 0 goleiro, 1 defensor, 2 meio, 3 atacante (NaN para banco/desconhecido)."""
    if p is None or pd.isna(p):
        return np.nan
    p = float(p)
    return 0 if p == 11 else 1 if 31 <= p <= 39 else 2 if 51 <= p <= 79 else 3 if p >= 80 else np.nan


# --------------------------------------------------------------------------- cálculo do rating
def indice_partida(S: pd.DataFrame, cfg: dict = CFG) -> tuple[np.ndarray, np.ndarray]:
    """(nota, bônus) por linha, como `indicePartidaJogador` de api/model-maintenance.js: nota do FotMob (ou a
    neutra) + bônus de gols (até 3), assistências (até 3), finalização (gols - xG, entre -1 e +1) e criação."""
    nota = np.where(S.rating.notna(), S.rating, cfg["neutra"]).astype(float)
    gols, ass = S.goals.fillna(0).values, S.assists.fillna(0).values
    xg = np.where(S.xg.notna(), S.xg, gols)
    xa, ch = S.xa.fillna(0).values, S.chances_created.fillna(0).values
    bonus = (np.minimum(gols, 3) * cfg["pg"] + np.minimum(ass, 3) * cfg["pa"]
             + np.clip(gols - xg, -1, 1) * cfg["pf"] + np.minimum(xa + ch * 0.05, 1) * cfg["pc"])
    return nota, bonus


def simular(player_ids: np.ndarray, indice: np.ndarray, cfg: dict = CFG) -> tuple[np.ndarray, dict]:
    """Rating ANTES de cada linha (linhas já em ordem cronológica) e rating final por jogador. Mesma regra de
    `atualizarRatingJogador`: r += k * (indice - (neutra + (r - 1500) / escala)) / 3."""
    r: dict = {}
    antes = np.empty(len(player_ids))
    for i in range(len(player_ids)):
        a = r.get(player_ids[i], 1500.0)
        antes[i] = a
        r[player_ids[i]] = a + cfg["k"] * ((indice[i] - (cfg["neutra"] + (a - 1500.0) / cfg["escala"])) / 3.0)
    return antes, r


def baselines_passado(player_ids: np.ndarray, notas: np.ndarray, prior: float = 6.8) -> pd.DataFrame:
    """Só com o PASSADO de cada jogador: nº de notas anteriores, média encolhida (prior com peso PRIOR_JOGOS) e EWMA."""
    n_prev = np.zeros(len(player_ids))
    media_enc = np.full(len(player_ids), np.nan)
    ewma = np.full(len(player_ids), np.nan)
    cnt: dict = {}
    soma: dict = {}
    ew: dict = {}
    for i, p in enumerate(player_ids):
        n = cnt.get(p, 0)
        n_prev[i] = n
        if n > 0:
            media_enc[i] = (soma[p] + PRIOR_JOGOS * prior) / (n + PRIOR_JOGOS)
            ewma[i] = ew[p]
        if not np.isnan(notas[i]):
            cnt[p] = n + 1
            soma[p] = soma.get(p, 0.0) + notas[i]
            ew[p] = notas[i] if n == 0 else (1 - ALPHA_EWMA) * ew[p] + ALPHA_EWMA * notas[i]
    return pd.DataFrame({"n_prev": n_prev, "media_encolhida": media_enc, "ewma": ewma})


def preparar(d: dict, cfg: dict = CFG) -> tuple[pd.DataFrame, dict]:
    """Devolve (tabela, rating final por jogador). Uma linha por (jogador, partida) com >= `minutos_min` min, em ordem cronológica, com índice, bônus, rating
    simulado (com e sem bônus), baselines do passado, posição, mando, força do time e liga."""
    S = d["S"].drop_duplicates(["match_id", "team_id", "fotmob_player_id"])
    S = S[S.player_id.notna()].copy()
    for c in ["rating", "minutes_played", "goals", "assists", "xg", "xa", "chances_created", "total_shots", "tackles", "interceptions"]:
        S[c] = pd.to_numeric(S[c], errors="coerce")
    S = S[S.minutes_played.fillna(0) >= cfg["minutos_min"]].copy()
    P = d["P"][["id", "match_date", "home_team_id", "away_team_id", "league_id"]].rename(columns={"id": "match_id"}).copy()
    P["d"] = pd.to_datetime(P.match_date.astype(str).str[:10])
    S = S.merge(P, on="match_id")
    S["casa"] = (S.team_id == S.home_team_id).astype(int)
    E = d["E"].drop_duplicates(["match_id", "team_id"])[["match_id", "team_id", "rating_antes"]]
    S = S.merge(E.rename(columns={"rating_antes": "elo_t"}), on=["match_id", "team_id"], how="left")
    S["opp"] = np.where(S.casa == 1, S.away_team_id, S.home_team_id)
    S = S.merge(E.rename(columns={"team_id": "opp", "rating_antes": "elo_o"}), on=["match_id", "opp"], how="left")
    S["elo_diff"] = S.elo_t - S.elo_o
    L = d["L"]
    L = L[L.is_starter == True].copy()  # noqa: E712
    L["g"] = L.position_id.map(grupo_posicao)
    pos = L.dropna(subset=["g"]).groupby("fotmob_player_id").g.agg(lambda s: s.mode().iloc[0]).rename("pos")
    S = S.merge(pos, on="fotmob_player_id", how="left")
    S = S.sort_values(["d", "match_id", "id"]).reset_index(drop=True)
    nota, bonus = indice_partida(S, cfg)
    S["bonus"], S["indice"] = bonus, nota + bonus
    pid = S.player_id.values
    S["elo_cfg"], final = simular(pid, nota + bonus, cfg)
    S["elo_notas"], _ = simular(pid, nota, cfg)
    S = pd.concat([S, baselines_passado(pid, S.rating.values)], axis=1)
    S["def_acoes"] = S.tackles + S.interceptions
    S["min90"] = S.minutes_played / 90.0
    return S, final  # `final` fora de S.attrs: o pandas copia attrs a cada operação (lento com 17 mil jogadores)


# --------------------------------------------------------------------------- estatística
def r2(y, X) -> tuple[float, np.ndarray]:
    """R² de OLS de `y` sobre as colunas de `X` (+ constante), ignorando linhas com NaN."""
    y = np.asarray(y, float)
    M = np.column_stack([np.ones(len(y))] + [np.asarray(x, float).reshape(len(y), -1) for x in X])
    ok = np.isfinite(y) & np.isfinite(M).all(1)
    y, M = y[ok], M[ok]
    b = np.linalg.lstsq(M, y, rcond=None)[0]
    e = y - M @ b
    return float(1 - e.var() / y.var()), b


def dummies(s) -> np.ndarray:
    return pd.get_dummies(s, drop_first=True).values.astype(float)


def persistencia_aspecto(S: pd.DataFrame, coluna: str, por90: bool) -> pd.DataFrame:
    """R² (por posição) de prever a métrica do PRÓXIMO jogo pela média encolhida do próprio jogador (só o passado;
    prior = média da posição, peso PRIOR_JOGOS; só com >= 5 jogos anteriores)."""
    y = (S[coluna] / S.min90 if por90 else S[coluna]).values.astype(float)
    w = S.min90.values if por90 else np.ones(len(S))
    pid, pos = S.player_id.values, S.pos.values
    glob = pd.Series(y).groupby(pos).mean().to_dict()
    gm = float(np.nanmean(y))
    pred = np.full(len(S), np.nan)
    soma: dict = {}
    peso: dict = {}
    n: dict = {}
    for i in range(len(S)):
        p = pid[i]
        if n.get(p, 0) >= 5:
            pred[i] = (soma[p] + PRIOR_JOGOS * glob.get(pos[i], gm)) / (peso[p] + PRIOR_JOGOS)
        if not np.isnan(y[i]):
            soma[p] = soma.get(p, 0.0) + y[i] * w[i]
            peso[p] = peso.get(p, 0.0) + w[i]
            n[p] = n.get(p, 0) + 1
    linhas = []
    for g in NOMES_POS:
        m = (pos == g) & np.isfinite(y) & np.isfinite(pred)
        if m.sum() > 2000:
            linhas.append({"posicao": NOMES_POS[g], "n": int(m.sum()), "R2": float(np.corrcoef(y[m], pred[m])[0, 1] ** 2)})
    return pd.DataFrame(linhas)


# --------------------------------------------------------------------------- relatório
def relatorio(S: pd.DataFrame, final: dict, ligas: dict | None = None) -> None:
    ligas = ligas or {}
    print("=== A) O rating prevê a PRÓXIMA nota? (>= 5 notas anteriores)")
    A = S[(S.n_prev >= 5) & S.pos.notna() & S.elo_diff.notna()].copy()
    y = A.rating.values
    base = [dummies(A.pos), A.casa.values, A.elo_diff.values]
    rb0, _ = r2(y, base)
    print(f"linhas {len(A)} | controles (posição + mando + dif. Elo do time) R²={rb0:.4f}")
    for nome, col in [("Elo jogador (config do banco)", "elo_cfg"), ("Elo só com a nota (sem bônus)", "elo_notas"),
                      ("média encolhida da carreira", "media_encolhida"), ("EWMA das notas", "ewma")]:
        ra, _ = r2(y, [A[col].values])
        rb, _ = r2(y, [A[col].values] + base)
        print(f"  {nome:<32} sozinho R²={ra:.4f} | +controles R²={rb:.4f} (ganho {rb - rb0:+.4f})")
    A["faixa"] = pd.cut(A.n_prev, [4, 9, 29, 99, 1e9], labels=["5-9", "10-29", "30-99", "100+"])
    for f, g in A.groupby("faixa", observed=True):
        print(f"  {f:>6} n={len(g):>7} | " + " | ".join(
            f"{n} {r2(g.rating.values, [g[c].values])[0]:.3f}" for n, c in
            [("Elo", "elo_cfg"), ("Elo s/bônus", "elo_notas"), ("média enc.", "media_encolhida"), ("EWMA", "ewma")]))

    J = S.groupby("player_id").agg(n=("rating", "size"), media=("rating", "mean"), pos=("pos", "first"), elo_t=("elo_t", "mean"),
                                   liga=("league_id", lambda s: s.mode().iloc[0])).reset_index()
    J["rating_final"] = J.player_id.map(final)
    J30 = J[(J.n >= 30) & J.pos.notna()].copy()

    print("\n=== B) POSIÇÃO")
    for g, x in S[S.pos.notna()].groupby("pos"):
        print(f"  {NOMES_POS[int(g)]:<8} n={len(x):>7} nota média {x.rating.mean():.3f} (dp {x.rating.std():.2f}) | índice médio {x.indice.mean():.3f}")
    for g, x in J30.groupby("pos"):
        print(f"  rating final {NOMES_POS[int(g)]:<8} n={len(x):>5} média {x.rating_final.mean():.0f} | p90 {x.rating_final.quantile(.9):.0f} | máx {x.rating_final.max():.0f}")
    print(f"  % da variação do rating final explicada pela posição: {100 * r2(J30.rating_final.values, [dummies(J30.pos)])[0]:.1f}%")

    print("\n=== C) FORÇA DO TIME / ADVERSÁRIO")
    C = S[S.elo_diff.notna() & S.pos.notna()]
    _, b = r2(C.rating.values, [C.elo_diff.values / 100, C.casa.values, dummies(C.pos)])
    print(f"  nota por +100 pontos de Elo do time sobre o adversário: {b[1]:+.3f} | mando {b[2]:+.3f}")
    print(f"  corr(rating final, Elo médio do time)={J30.rating_final.corr(J30.elo_t):.3f} | "
          f"% da variação do rating final explicada pelo Elo do time: {100 * r2(J30.rating_final.values, [J30.elo_t.values])[0]:.1f}%")

    print("\n=== D) LIGA")
    print(f"  % explicada pela liga principal: {100 * r2(J30.rating_final.values, [dummies(J30.liga)])[0]:.1f}% ({J30.liga.nunique()} ligas)")
    g = J30.groupby("liga").agg(n=("rating_final", "size"), media=("rating_final", "mean"))
    g = g[g.n >= 40].sort_values("media")
    print("  mais ALTO:", [(ligas.get(i, i), round(r.media)) for i, r in g.tail(4).iloc[::-1].iterrows()])
    print("  mais BAIXO:", [(ligas.get(i, i), round(r.media)) for i, r in g.head(4).iterrows()])

    print("\n=== E) NÚMERO DE JOGOS")
    print(f"  corr(rating final, nº jogos)={J.rating_final.corr(J.n):.3f} | corr com nota média={J.rating_final.corr(J.media):.3f} "
          f"(>= 30 jogos: {J[J.n >= 30].rating_final.corr(J[J.n >= 30].media):.3f})")
    rn, rm = r2(J.rating_final.values, [J.n.values])[0], r2(J.rating_final.values, [J.media.values])[0]
    print(f"  R² do rating final: nº jogos {rn:.3f} | nota média {rm:.3f} | os dois {r2(J.rating_final.values, [J.n.values, J.media.values])[0]:.3f}")
    J["fx"] = pd.cut(J.n, [0, 10, 30, 100, 300, 2000], labels=["1-10", "11-30", "31-100", "101-300", "300+"])
    for f, x in J.groupby("fx", observed=True):
        print(f"    {f:>8} n={len(x):>6} média {x.rating_final.mean():.0f} | dp {x.rating_final.std():.0f} | nota média {x.media.mean():.2f}")

    print("\n=== F) PESOS DOS BÔNUS")
    print(f"  var(nota) {S.rating.var():.3f} | var(bônus) {S.bonus.var():.4f} ({100 * S.bonus.var() / S.indice.var():.1f}% da variância do índice) "
          f"| corr(nota, bônus) {S.rating.corr(S.bonus):.3f}")

    print("\n=== G) PERSISTÊNCIA POR ASPECTO (R² de prever o PRÓXIMO jogo pela média encolhida do próprio jogador)")
    tab = {}
    for nome, (col, por90) in METRICAS_ASPECTO.items():
        t = persistencia_aspecto(S, col, por90)
        tab[nome] = t.set_index("posicao").R2
    print(pd.DataFrame(tab).T.round(3).to_string())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache-dir", default=None)
    args = ap.parse_args()
    url, chave = os.environ.get("SUPABASE_URL", ""), os.environ.get("SUPABASE_KEY", "")
    if not url or not chave:
        sys.exit("SUPABASE_URL e SUPABASE_KEY precisam estar definidos.")
    d = carregar(url, chave, args.cache_dir)
    S, final = preparar(d)
    H = d["H"].drop_duplicates(["player_id", "match_id"])[["player_id", "match_id", "rating_antes"]]
    c = S[["player_id", "match_id", "elo_cfg"]].merge(H, on=["player_id", "match_id"])
    print(f"réplica vs gravado: n={len(c)} | corr {c.elo_cfg.corr(c.rating_antes):.4f} | erro abs médio {(c.elo_cfg - c.rating_antes).abs().mean():.1f}\n")
    relatorio(S, final, dict(zip(d["G"].id, d["G"].name)))


if __name__ == "__main__":
    main()
