#!/usr/bin/env python3
"""Mercados de totais do modelo contra odds reais: Pinnacle sem vig, apostas e
abertura × fechamento por casa.

Pergunta: a calibração bivariada do λ (PR #665), que melhora O/U, BTTS e gols
por time contra a PRÓPRIA produção, bate o MERCADO? Mercados: O/U 1.5/2.5/3.5,
BTTS e gols de cada time O/U 1.5. Fora da amostra (partidas a partir de
--corte), matriz Dixon-Coles estática com ρ real.

Versões de λ comparadas:
  * producao           — λ_xGOT × força defensiva do adversário (PR #657)
  * univariada         — α·λ^γ (PR #664)
  * bivariada_s_mando  — e^μ·λ^γ·λ_adv^δ, sem mando (PR #665), ajustada no treino
  * hibrido_gols_v1    — λ do modelo macro (model_match_estimates.params)

Três blocos de saída:
  1. Fechamento, Pinnacle sem vig (proporcional): log-loss do modelo − log-loss
     da Pinnacle, IC95% bootstrap; teste de "informação a mais" (regressão
     logística y ~ logit(p_pin) + [logit(p_mod) − logit(p_pin)]: β>0
     significativo = o modelo acrescenta algo ao mercado).
  2. Apostas no MELHOR preço de fechamento entre as casas (último registro de
     cada casa), os dois lados, EV>0/3/6%: yield com IC95% e calibração DENTRO
     das apostas escolhidas (acerto real × previsto pelo modelo × Pinnacle).
  3. Mercado × casa × abertura (1ª odd registrada) × fechamento (última odd),
     mesmas partidas nos dois momentos (casa com as duas pontas separadas por
     ≥6h): margem, log-loss da casa, Δ do modelo, yield e CLV das apostas
     feitas na abertura (odd de abertura / odd de fechamento − 1).

Resultado registrado em CONTEXTO_PROJETO.md (25/09): a Pinnacle bate todas as
versões em todos os mercados de totais; o modelo não acrescenta informação;
nenhuma aposta com IC acima de zero; CLV ≈ 0 na abertura.

Uso:
    python arquivos_do_claude/validar_totais_vs_mercado.py --entrada painel.pkl
    python arquivos_do_claude/validar_totais_vs_mercado.py   # painel do banco

`--entrada`: painel time-partida de calibrar_potencia_lambda.py (colunas
match_id, data, team_id, is_home, lam_prod, gols, rho). Sem ele, o painel é
montado do banco por calibrar_potencia_lambda.carregar_do_banco().
`--cache-dir` guarda/reaproveita odds, partidas e λ do híbrido em pickle.

Variáveis de ambiente: SUPABASE_URL, SUPABASE_KEY (chave pública basta —
odds_market, matches e model_match_estimates têm leitura pública). Só leitura.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import os
import sys

import numpy as np
import pandas as pd
from scipy.optimize import minimize

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.dirname(__file__))
import distribuicoes as dist  # noqa: E402
import calibrar_potencia_lambda as cp  # noqa: E402
from validar_assistencia_jogador_walkforward import Rest, obter_env  # noqa: E402

CORTE_PADRAO = "2025-06-01"
N_BOOT = 2000
SEED = 42
HORAS_MIN_ABERTURA = 6
CASAS_ABERTURA = ("pinnacle", "bet365", "betano", "1xbet", "william_hill")
LIMIARES_EV = (0.0, 0.03, 0.06)
# mercado -> seleção "positiva" (over/yes) e a oposta
MERCADOS = {
    "over_under_1.5": ("over", "under"),
    "over_under_2.5": ("over", "under"),
    "over_under_3.5": ("over", "under"),
    "btts": ("yes", "no"),
    "over_under_team_1_1.5": ("over", "under"),
    "over_under_team_2_1.5": ("over", "under"),
}
MODELO_APOSTAS = ("bivariada_s_mando", "hibrido_gols_v1", "producao")
MODELO_ABERTURA = "bivariada_s_mando"


# =============================================================================
# Carregamento
# =============================================================================
def _cache(cache_dir: str | None, nome: str, carregar):
    caminho = os.path.join(cache_dir, f"{nome}.pkl") if cache_dir else None
    if caminho and os.path.exists(caminho):
        return pd.read_pickle(caminho)
    df = carregar()
    if caminho:
        os.makedirs(cache_dir, exist_ok=True)
        df.to_pickle(caminho)
    return df


def baixar_odds(rest: Rest, match_ids: list[int]) -> pd.DataFrame:
    """Todas as capturas (opening/pre_closing/closing) dos mercados de totais
    das partidas pedidas — lotes de 50 ids, paginação por offset ordenada por id."""
    mercados = ",".join(MERCADOS)

    def lote(ids):
        linhas, offset = [], 0
        while True:
            pagina = rest.get("odds_market", {
                "select": "id,match_id,bookmaker,market,selection,odds,captured_at,snapshot",
                "match_id": f"in.({','.join(map(str, ids))})", "market": f"in.({mercados})",
                "order": "id", "limit": 1000, "offset": offset,
            })
            linhas += pagina
            if len(pagina) < 1000:
                return linhas
            offset += 1000

    lotes = [match_ids[i:i + 50] for i in range(0, len(match_ids), 50)]
    with cf.ThreadPoolExecutor(12) as ex:
        partes = list(ex.map(lote, lotes))
    return pd.DataFrame([r for p in partes for r in p])


def baixar_hibrido(rest: Rest) -> pd.DataFrame:
    df = rest.baixar("model_match_estimates", "id,match_id,lh:params->>lambda_home,la:params->>lambda_away",
                     {"model_name": "eq.hibrido_gols_v1"}, 8)
    return pd.DataFrame({"match_id": df["match_id"], "lh_hib": pd.to_numeric(df["lh"], errors="coerce"),
                         "la_hib": pd.to_numeric(df["la"], errors="coerce")})


# =============================================================================
# Núcleo puro
# =============================================================================
def log_loss(y: np.ndarray, p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def ic_bootstrap(valores: np.ndarray, rng: np.random.Generator) -> tuple[float, float]:
    n = len(valores)
    medias = [valores[rng.integers(0, n, n)].mean() for _ in range(N_BOOT)]
    lo, hi = np.percentile(medias, [2.5, 97.5])
    return float(lo), float(hi)


def informacao_extra(y: np.ndarray, p_mercado: np.ndarray, p_modelo: np.ndarray) -> tuple[float, float]:
    """β e SE de logit(y) ~ a + b·logit(p_mercado) + β·[logit(p_modelo) − logit(p_mercado)]."""
    X = np.column_stack([np.ones_like(y), logit(p_mercado), logit(p_modelo) - logit(p_mercado)])

    def nll(b):
        eta = X @ b
        return np.sum(np.logaddexp(0, eta) - y * eta)

    res = minimize(nll, np.array([0.0, 1.0, 0.0]), method="BFGS")
    pr = 1 / (1 + np.exp(-X @ res.x))
    se = np.sqrt(np.diag(np.linalg.inv((X * (pr * (1 - pr))[:, None]).T @ X)))
    return float(res.x[2]), float(se[2])


def sem_vig(odd_pos: np.ndarray, odd_neg: np.ndarray) -> np.ndarray:
    """Probabilidade do lado positivo, margem removida proporcionalmente."""
    return (1 / odd_pos) / (1 / odd_pos + 1 / odd_neg)


def simular_apostas(p_pos, y, odd_pos, odd_neg, limiar, odd_pos_fech=None, odd_neg_fech=None, p_ref_pos=None):
    """Aposta 1 unidade em cada lado com EV = p·odd − 1 > limiar. Devolve dict
    com retornos, probabilidade prevista pelo modelo, acerto, a probabilidade de
    referência (ex.: Pinnacle sem vig) do MESMO lado apostado e, se houver odds
    de fechamento, CLV = odd apostada / odd de fechamento − 1."""
    out = {k: [] for k in ("ret", "prev", "acerto", "ref", "clv")}
    for i in range(len(y)):
        lados = (
            (p_pos[i], odd_pos[i], odd_pos_fech, None if p_ref_pos is None else p_ref_pos[i], y[i] == 1),
            (1 - p_pos[i], odd_neg[i], odd_neg_fech, None if p_ref_pos is None else 1 - p_ref_pos[i], y[i] == 0),
        )
        for p, odd, fech, ref, ganhou in lados:
            if np.isfinite(odd) and p * odd - 1 > limiar:
                out["ret"].append(odd - 1 if ganhou else -1.0)
                out["prev"].append(p)
                out["acerto"].append(float(ganhou))
                if ref is not None:
                    out["ref"].append(ref)
                if fech is not None and np.isfinite(fech[i]):
                    out["clv"].append(odd / fech[i] - 1)
    return {k: np.array(v) for k, v in out.items()}


def probabilidades_modelo(lh, la, rho, match_ids) -> pd.DataFrame:
    linhas = []
    for a, b, r in zip(lh, la, rho):
        if not (np.isfinite(a) and np.isfinite(b)):
            linhas.append([np.nan] * len(MERCADOS))
            continue
        mk = dist.mercados_de_gols(dist.matriz_placares(a, b, r), linhas_over_under=(1.5, 2.5, 3.5),
                                   linhas_over_under_time=(1.5,))
        linhas.append([mk[(m, pos)] for m, (pos, _) in MERCADOS.items()])
    return pd.DataFrame(linhas, columns=list(MERCADOS), index=match_ids)


def resultados(partidas: pd.DataFrame) -> pd.DataFrame:
    hg, ag = partidas["home_goals"].values, partidas["away_goals"].values
    return pd.DataFrame({
        "over_under_1.5": hg + ag > 1.5, "over_under_2.5": hg + ag > 2.5, "over_under_3.5": hg + ag > 3.5,
        "btts": (hg > 0) & (ag > 0), "over_under_team_1_1.5": hg > 1.5, "over_under_team_2_1.5": ag > 1.5,
    }, index=partidas["match_id"].values).astype(float)


def pontas_por_casa(odds: pd.DataFrame, inicio: pd.Series) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Primeira e última odd válida por partida/mercado/casa/seleção.

    NÃO filtra `captured_at <= apito`: o snapshot já garante que a odd é
    pré-jogo, e o filtro quebrava por dois motivos reais (achado 25/09):
    ~29% de `matches.match_date` vêm sem hora (00:00 UTC) e o fechamento
    importado depois do jogo (football-data.co.uk) tem `captured_at` = hora
    da importação. `ko` só é usado pra medir horas antes do jogo, e fica NaT
    quando a hora é desconhecida."""
    o = odds[(odds["odds"] > 1.0) & (odds["bookmaker"] != "media_mercado")].copy()
    o["t"] = pd.to_datetime(o["captured_at"], format="ISO8601", utc=True)
    ko = inicio.where((inicio.dt.hour != 0) | (inicio.dt.minute != 0))
    o = o.merge(ko.rename("ko").reset_index(), on="match_id", how="left")
    chave = ["match_id", "market", "bookmaker", "selection"]
    # Abertura: só capturas feitas ANTES do jogo (opening/pre_closing). Sem isso,
    # partidas que só têm o fechamento importado duas vezes depois do jogo
    # viravam "abertura" com a 1ª importação (achado real 25/09: "1ª odd"
    # 5.416h DEPOIS do jogo).
    pre = o[o["snapshot"].isin(["opening", "pre_closing"])].sort_values("t")
    # Fechamento: prefere o snapshot `closing` (o mais recente, se reimportado);
    # sem ele, a última captura pré-jogo.
    o["rank"] = (o["snapshot"] == "closing").astype(int)
    fech = o.sort_values(["rank", "t"])
    return pre.groupby(chave).head(1).set_index(chave), fech.groupby(chave).tail(1).set_index(chave)


# =============================================================================
# Relatório
# =============================================================================
def bloco_fechamento(probs, Y, ultima, rng):
    print("\n######## 1+2. FECHAMENTO: Pinnacle sem vig e apostas no melhor preço ########")
    for merc, (pos, neg) in MERCADOS.items():
        try:
            pin = ultima.xs((merc, "pinnacle"), level=("market", "bookmaker"))["odds"].unstack()
        except KeyError:
            continue
        if not {pos, neg} <= set(pin.columns):
            continue
        pin = pin.dropna(subset=[pos, neg])
        melhor = ultima.xs(merc, level="market")["odds"].unstack("selection").groupby(level="match_id").max()
        ids = [i for i in pin.index.intersection(Y.index) if all(np.isfinite(p.loc[i, merc]) for p in probs.values())]
        if len(ids) < 100:
            continue
        y = Y.loc[ids, merc].values
        pp = sem_vig(pin.loc[ids, pos].values, pin.loc[ids, neg].values)
        margem = (1 / pin.loc[ids, pos] + 1 / pin.loc[ids, neg] - 1).mean()
        print(f"\n===== {merc}: n={len(ids)} (margem média Pinnacle {margem:.2%}); taxa real={y.mean():.3f}")
        print(f"  Pinnacle sem vig: LL={log_loss(y, pp).mean():.5f}  Brier={((pp - y) ** 2).mean():.5f}  O/E={y.sum() / pp.sum():.3f}")
        for nome, P in probs.items():
            pm = P.loc[ids, merc].values
            d = log_loss(y, pm) - log_loss(y, pp)
            lo, hi = ic_bootstrap(d, rng)
            beta, se = informacao_extra(y, pp, pm)
            print(f"  {nome:18s} LL={log_loss(y, pm).mean():.5f} O/E={y.sum() / pm.sum():.3f} | "
                  f"Δ vs Pinnacle={d.mean():+.5f} [{lo:+.5f},{hi:+.5f}] | info extra β={beta:+.3f} (SE {se:.3f}, z {beta / se:+.2f})")
        mb = melhor.reindex(ids)
        for nome in MODELO_APOSTAS:
            pm = probs[nome].loc[ids, merc].values
            for lim in LIMIARES_EV:
                ap = simular_apostas(pm, y, mb[pos].values, mb[neg].values, lim, p_ref_pos=pp)
                if len(ap["ret"]) < 30:
                    continue
                lo, hi = ic_bootstrap(ap["ret"], rng)
                print(f"    aposta {nome:18s} EV>{lim:.0%}: n={len(ap['ret']):5d} yield={ap['ret'].mean():+.2%} [{lo:+.2%},{hi:+.2%}] "
                      f"| acerto real={ap['acerto'].mean():.3f} prev. modelo={ap['prev'].mean():.3f} prev. Pinnacle={ap['ref'].mean():.3f}")


def bloco_abertura(probs, Y, primeira, ultima, rng):
    print(f"\n######## 3. MERCADO × CASA × ABERTURA (1ª odd) × FECHAMENTO (última) — modelo = {MODELO_ABERTURA} ########")
    P = probs[MODELO_ABERTURA]
    for merc, (pos, neg) in MERCADOS.items():
        for casa in CASAS_ABERTURA:
            try:
                a = primeira.xs((merc, casa), level=("market", "bookmaker"))
                f = ultima.xs((merc, casa), level=("market", "bookmaker"))
            except KeyError:
                continue
            oa, of = a["odds"].unstack(), f["odds"].unstack()
            if not ({pos, neg} <= set(oa.columns) and {pos, neg} <= set(of.columns)):
                continue
            ta, tf = a["t"].unstack().min(axis=1), f["t"].unstack().max(axis=1)
            ko = a["ko"].unstack().iloc[:, 0]
            ids = oa.dropna(subset=[pos, neg]).index.intersection(of.dropna(subset=[pos, neg]).index).intersection(Y.index)
            ids = [i for i in ids if tf[i] - ta[i] >= pd.Timedelta(hours=HORAS_MIN_ABERTURA) and np.isfinite(P.loc[i, merc])]
            if len(ids) < 100:
                continue
            y, pm = Y.loc[ids, merc].values, P.loc[ids, merc].values
            # Horas antes do jogo só onde a hora é conhecida e a captura é anterior
            # a ela: pre_closing importado do football-data.co.uk tem captured_at
            # = hora da importação (a odd em si é de dias antes do jogo).
            h = (ko.reindex(ids) - ta[ids]).dt.total_seconds() / 3600
            h = h[h > 0]
            quando = f"1ª odd ~{h.median():.0f}h antes do jogo em {len(h)}/{len(ids)}" if len(h) else "hora real da 1ª odd desconhecida (importada)"
            print(f"  {merc:22s} {casa:12s} n={len(ids):5d} ({quando})")
            for momento, O in (("abertura", oa), ("fechamento", of)):
                o1, o0 = O.loc[ids, pos].values, O.loc[ids, neg].values
                pb = sem_vig(o1, o0)
                d = log_loss(y, pm) - log_loss(y, pb)
                lo, hi = ic_bootstrap(d, rng)
                fech = (of.loc[ids, pos].values, of.loc[ids, neg].values) if momento == "abertura" else (None, None)
                ap = simular_apostas(pm, y, o1, o0, 0.0, *fech)
                ret, clv = ap["ret"], ap["clv"]
                rlo, rhi = ic_bootstrap(ret, rng) if len(ret) >= 30 else (np.nan, np.nan)
                txt = (f"     {momento:10s}: margem {(1 / o1 + 1 / o0 - 1).mean():5.2%} LL casa={log_loss(y, pb).mean():.4f} "
                       f"Δ modelo={d.mean():+.4f} [{lo:+.4f},{hi:+.4f}] apostas={len(ret)} yield={ret.mean():+.1%} [{rlo:+.1%},{rhi:+.1%}]")
                if len(clv):
                    txt += f" CLV médio={clv.mean():+.2%} (odd caiu depois em {np.mean(clv > 0):.0%})"
                print(txt)


# =============================================================================
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corte", default=CORTE_PADRAO, help="treino = antes desta data; teste = a partir dela")
    parser.add_argument("--entrada", help="painel time-partida (.pkl/.csv) de calibrar_potencia_lambda.py")
    parser.add_argument("--cache-dir", help="pasta pra guardar/reaproveitar os downloads (.pkl)")
    args = parser.parse_args()

    painel = cp.carregar_de_arquivo(args.entrada) if args.entrada else cp.carregar_do_banco()
    painel = painel.dropna(subset=["lam_prod", "gols"])
    corte = pd.Timestamp(args.corte)
    treino, teste = painel[painel["data"] < corte], painel[painel["data"] >= corte]

    biv = cp.ajustar_bivariado(cp.com_lambda_adversario(treino), com_mando=False)
    uni = cp.ajustar_potencia(treino["lam_prod"].values, treino["gols"].values)
    print(f"Treino: bivariada sem mando α={np.exp(biv['coef']['mu']):.4f} γ={biv['coef']['gamma']:.4f} "
          f"δ={biv['coef']['delta']:.4f} | univariada α={uni['alpha']:.4f} γ={uni['gamma']:.4f}")

    rest = None

    def cliente():
        nonlocal rest
        rest = rest or Rest(obter_env("SUPABASE_URL"), obter_env("SUPABASE_KEY"))
        return rest

    partidas = cp.para_partidas(teste)
    ids = sorted(partidas["match_id"].astype(int).unique().tolist())
    hib = _cache(args.cache_dir, "hibrido_lambda", lambda: baixar_hibrido(cliente()))
    odds = _cache(args.cache_dir, "odds_totais", lambda: baixar_odds(cliente(), ids))
    jogos = _cache(args.cache_dir, "matches_inicio", lambda: cliente().baixar("matches", "id,match_date", {}, 4))
    inicio = pd.to_datetime(jogos.set_index("id")["match_date"], format="ISO8601", utc=True).rename_axis("match_id")

    partidas = partidas.merge(hib, on="match_id", how="left")
    lh, la = partidas["lam_home"].values, partidas["lam_away"].values
    um, zero = np.ones(len(lh)), np.zeros(len(lh))
    versoes = {
        "producao": (lh, la),
        "univariada": (cp.aplicar(lh, uni["alpha"], uni["gamma"]), cp.aplicar(la, uni["alpha"], uni["gamma"])),
        "bivariada_s_mando": (cp.aplicar_bivariado(biv, lh, la, um), cp.aplicar_bivariado(biv, la, lh, zero)),
        "hibrido_gols_v1": (partidas["lh_hib"].values, partidas["la_hib"].values),
    }
    probs = {n: probabilidades_modelo(a, b, partidas["rho"].values, partidas["match_id"].values) for n, (a, b) in versoes.items()}
    Y = resultados(partidas)
    print(f"Teste: {len(partidas)} partidas; odds: {len(odds)} linhas em {odds['match_id'].nunique()} partidas")

    primeira, ultima = pontas_por_casa(odds, inicio)
    rng = np.random.default_rng(SEED)
    bloco_fechamento(probs, Y, ultima, rng)
    bloco_abertura(probs, Y, primeira, ultima, rng)


if __name__ == "__main__":
    main()
