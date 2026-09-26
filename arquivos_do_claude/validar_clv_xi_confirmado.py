#!/usr/bin/env python3
"""CLV no 1X2 com a escalação confirmada (XI) — e o vazamento que ele revelou.

Continuação de validar_clv_1x2.py (26/09): lá o λ pré-escalação não bate a
linha. Aqui a pergunta é se o λ com os titulares CONFIRMADOS (única variante
com ganho fora da amostra, CONTEXTO_PROJETO.md 25/09) pega preço melhor que o
fechamento da Pinnacle.

Achado principal (bloco 1): o λ "base" do painel (`lam_prod` de
calibrar_potencia_lambda.py = soma de `player_match_walkforward.lambda_xg_jogo`
previsto × força defensiva) VAZA informação pós-jogo — a tabela walk-forward
só tem jogadores que entraram em campo, então a soma inclui os reservas que
ENTRARAM. Time que está perdendo põe atacante; a fração do λ vinda de reservas
prevê o resultado além da Pinnacle de fechamento. O vazamento é ANTI-preditivo
(o base fica pior, não melhor). O λ só-titulares (`lb_real_tit` =
xg_titulares × e^(defesa)) não depende de reserva nenhum: é o único limpo.

Blocos:
  1. Prova do vazamento: acerto − p_Pinnacle_fech ~ (fração_reservas_casa −
     fração_reservas_fora).
  2. Armadilha: apostar onde p_titulares − p_base > δ dá yield alto até na odd
     de FECHAMENTO — só porque p_base carrega o vazamento. Não é implementável.
  3. Janela realista (odd capturada 0-75 min antes do jogo, XI já publicado):
     quantas partidas existem e o CLV descritivo.
  4. Teste limpo contra o fechamento: acerto − p_Pin_fech ~ (p_tit − p_Pin_fech),
     w de log-pooling, Δ log-loss e apostas EV>limiar na odd de fechamento.
  5. Oráculo na abertura (validar_clv_1x2.bloco_clv): CLV das apostas do XI
     confirmado na odd de abertura — teto do que o XI valeria se saísse antes
     do mercado (não saí; é só limite superior).

Capturas "cedo" feitas a ≤75 min do jogo são descartadas (já podem refletir a
escalação). Hora do jogo só onde `match_date` tem hora; captured_at de fontes
importadas é a hora da importação (ver validar_totais_vs_mercado.py).

Uso:
    python arquivos_do_claude/validar_clv_xi_confirmado.py --entrada painel.pkl --cache-dir /tmp/info

Variáveis de ambiente: SUPABASE_URL, SUPABASE_KEY (chave pública basta). Só leitura.
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.dirname(__file__))
import validar_clv_1x2 as clv  # noqa: E402
import validar_informacao_nova_lambda as vin  # noqa: E402
from validar_assistencia_jogador_walkforward import Rest, obter_env  # noqa: E402
from validar_totais_vs_mercado import _cache  # noqa: E402

JANELA_POS_XI_H = 1.25  # escalação sai ~60-75 min antes do jogo
LIMIARES_EV = (0.0, 0.03, 0.06, 0.10)
DELTAS_NOTICIA = (0.02, 0.04, 0.06)
BASE, TIT = "base (λ com reservas que entraram — vaza)", "só titulares confirmados (limpa)"


def horas_antes(odds: pd.DataFrame, jogos: pd.DataFrame) -> np.ndarray:
    """Horas entre a captura e o jogo; NaN quando match_date não tem hora."""
    j = jogos.set_index("id").loc[odds["match_id"], "match_date"].astype(str)
    k = pd.to_datetime(j.values, format="ISO8601", utc=True)
    t = pd.to_datetime(odds["captured_at"], format="ISO8601", utc=True)
    h = (k - pd.DatetimeIndex(t.values).tz_localize("UTC")).total_seconds() / 3600
    return np.where(j.str.contains("T00:00:00").values, np.nan, h)


def ic(v, rng):
    return clv.ic(np.asarray(v), rng)


def inclinacao(x, y, n_partidas, rng):
    """Inclinação de y ~ x (arrays partida × seleção) com IC95% bootstrap por partida."""
    b = np.polyfit(x.ravel(), y.ravel(), 1)[0]
    bs = [np.polyfit(x[k].ravel(), y[k].ravel(), 1)[0] for k in (rng.integers(0, n_partidas, n_partidas) for _ in range(vin.N_BOOT))]
    return b, np.percentile(bs, 2.5), np.percentile(bs, 97.5)


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
    partidas, _, probs = vin.avaliar_variantes(tr, te, {BASE: ("lb_prev", []), TIT: ("lb_real_tit", [])}, rng, BASE)
    jogos = vin.carregar(args.cache_dir)[0]
    ids_all = sorted(int(i) for i in partidas.index)
    odds = _cache(args.cache_dir, "odds_1x2_todas",
                  lambda: clv.odds_1x2_todas(Rest(obter_env("SUPABASE_URL"), obter_env("SUPABASE_KEY")), ids_all))
    odds = odds[odds["match_id"].isin(partidas.index)].copy()
    odds["h"] = horas_antes(odds, jogos)
    pos_xi = (odds["h"] > 0) & (odds["h"] <= JANELA_POS_XI_H)
    cedo = odds[~pos_xi]
    pin_fech = vin.pinnacle_sem_vig(odds[odds["bookmaker"] == "pinnacle"], "fechamento")[list(clv.SELECOES)]

    Pb = pd.DataFrame(probs[BASE], index=partidas.index, columns=list(clv.SELECOES))
    Pt = pd.DataFrame(probs[TIT], index=partidas.index, columns=list(clv.SELECOES))
    res = np.where(partidas["hg"] > partidas["ag"], 0, np.where(partidas["hg"] == partidas["ag"], 1, 2))
    ab, fe = clv.pontas(cedo[cedo["bookmaker"] == "pinnacle"])
    ids = fe.index.intersection(ab.index).intersection(pin_fech.index).intersection(Pt.dropna().index)
    n = len(ids)
    a, f, pf = ab.loc[ids].values, fe.loc[ids].values, pin_fech.loc[ids].values
    pb, pt = Pb.loc[ids].values, Pt.loc[ids].values
    y = pd.Series(res, index=partidas.index).loc[ids].values
    hit = (np.arange(3)[None, :] == y[:, None]).astype(float)
    print(f"\nPartidas com Pinnacle abertura + fechamento: {n}")

    print("\n=== 1. Vazamento: fração do λ base vinda de reservas QUE ENTRARAM (pós-jogo) ===")
    fr = te.assign(fsub=1 - te["xg_prev_tit"] / te["xg_prev"])
    dsub = (fr[fr["is_home"]].set_index("match_id")["fsub"] - fr[~fr["is_home"]].set_index("match_id")["fsub"]).reindex(ids).values
    print(f"  fração média do λ base vinda de reservas: {fr['fsub'].mean():.3f}")
    for rot, col in (("mandante vence", 0), ("visitante vence", 2)):
        b, lo, hi = inclinacao(dsub[:, None], (hit - pf)[:, [col]], n, rng)
        print(f"  acerto − p_Pin_fech ({rot}) ~ (fração_casa − fração_fora): {b:+.3f} [{lo:+.3f},{hi:+.3f}]")
    b, lo, hi = inclinacao(pb - pf, hit - pf, n, rng)
    print(f"  acerto − p_Pin_fech ~ (p_base − p_Pin_fech): {b:+.3f} [{lo:+.3f},{hi:+.3f}]  (negativo = o vazamento piora o base)")

    print("\n=== 2. Armadilha: seleções onde p_titulares − p_base > δ (NÃO implementável — p_base vaza) ===")
    for dl in DELTAS_NOTICIA:
        sel = pt - pb > dl
        r = (hit - pf)[sel]
        g = np.where(hit == 1, f - 1, -1.0)[sel]
        (rlo, rhi), (glo, ghi) = ic(r, rng), ic(g, rng)
        print(f"  δ>{dl:.0%}: {sel.sum():5d} seleções | acerto − p_Pin_fech={r.mean():+.4f} [{rlo:+.4f},{rhi:+.4f}] "
              f"| yield na odd de FECHAMENTO={g.mean():+.1%} [{glo:+.1%},{ghi:+.1%}]")

    print(f"\n=== 3. Janela realista: odd capturada 0-{JANELA_POS_XI_H * 60:.0f} min antes (XI já publicado) ===")
    for casa in ("pinnacle", "bet365", "betano"):
        q = odds[pos_xi & (odds["bookmaker"] == casa) & (odds["odds"] > 1)]
        q = q.sort_values("h").groupby(["match_id", "selection"]).head(1)
        w = q.pivot_table(index="match_id", columns="selection", values="odds").reindex(columns=list(clv.SELECOES)).dropna()
        jj = w.index.intersection(pin_fech.index).intersection(Pt.dropna().index)
        if len(jj) == 0:
            continue
        wa, wf_, wt = w.loc[jj].values, pin_fech.loc[jj].values, Pt.loc[jj].values
        sel = wt * wa - 1 > 0
        cl = (wa * wf_ - 1)[sel].mean() if sel.any() else float("nan")
        print(f"  {casa:8s}: {len(jj)} partidas | {sel.sum()} apostas EV>0 do XI, CLV={cl:+.2%} | aleatório={(wa * wf_ - 1).mean():+.2%}  (amostra anedótica)")

    print("\n=== 4. Teste limpo: XI confirmado contra a Pinnacle de fechamento ===")
    for rot, pm in ((TIT, pt), (BASE, pb)):
        w, se = vin.peso_log_pooling(y, pf, pm)
        ll = -np.log(np.clip(pm[np.arange(n), y], 1e-4, 1)) + np.log(pf[np.arange(n), y])
        lo, hi = ic(ll, rng)
        print(f"  {rot:44s} Δ log-loss vs Pin fech={ll.mean():+.4f} [{lo:+.4f},{hi:+.4f}] | w={w:+.3f} (z {w / se:+.1f})")
    b, lo, hi = inclinacao(pt - pf, hit - pf, n, rng)
    print(f"  acerto − p_Pin_fech ~ (p_tit − p_Pin_fech): {b:+.3f} [{lo:+.3f},{hi:+.3f}]  (0 = o fechamento já tem tudo que o XI sabe)")
    for lim in LIMIARES_EV:
        sel = pt * f - 1 > lim
        r, g = (hit - pf)[sel], np.where(hit == 1, f - 1, -1.0)[sel]
        (rlo, rhi), (glo, ghi) = ic(r, rng), ic(g, rng)
        print(f"  EV>{lim:.0%} na odd de fechamento: {sel.sum():5d} | acerto − p_Pin={r.mean():+.4f} [{rlo:+.4f},{rhi:+.4f}] "
              f"| yield={g.mean():+.1%} [{glo:+.1%},{ghi:+.1%}]")

    print("\n=== 5. Oráculo na abertura (limite superior: o XI não existe na abertura) ===")
    for rot in (TIT, BASE):
        clv.bloco_clv(rot, probs[rot], partidas, cedo, pin_fech, rng)


if __name__ == "__main__":
    main()
