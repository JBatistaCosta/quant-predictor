#!/usr/bin/env python3
"""Achado 55: a qualidade do chute (xG por chute) cai ao longo do jogo? E o reserva que entra do banco muda isso?

Entrada: dados_referencia/qualidade_chute/qualidade_chute_no_tempo_5ligas_2021_2025.json, gerada por arquivos_do_claude/qualidade_chute_no_tempo.sql (uma linha por
liga x faixa de 15 min do relógio x estado do placar x perfil de força x tipo de lance x papel de quem chutou; n, soma de xG, soma de xG^2, gols). Sem rede.

Controles (o que os achados anteriores não separavam):
  * estado do placar de quem chuta (perde/empata/ganha), perfil de força (Elo global, corte 100), tipo de lance (jogo corrido x bola parada) e liga:
    a média de cada faixa de tempo é PADRONIZADA para a mesma composição (pesos = participação de cada célula no total de todas as faixas);
  * só titulares nas faixas de tempo (reserva entra tarde e tem outra qualidade; misturá-los confundiria a fadiga com a troca);
  * reserva x titular comparados DENTRO da mesma célula (mesma faixa de tempo, estado, perfil, tipo, liga), ponderando pelos chutes do reserva.
Erro-padrão pela variância do xG dentro de cada célula (chutes independentes; jogos e times repetidos tornam-no otimista, ver limites no Achado 55).
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict

CAMINHO = "dados_referencia/qualidade_chute/qualidade_chute_no_tempo_5ligas_2021_2025.json"
FAIXAS = ["0-15", "15-30", "30-45", "45-60", "60-75", "75-90", "90+"]


def media_var(n, sxg, sxg2):
    m = sxg / n
    return m, max(sxg2 / n - m * m, 0.0)


def agrupar(linhas, chave):
    out = defaultdict(lambda: [0, 0.0, 0.0, 0])
    for r in linhas:
        a = out[chave(r)]
        a[0] += r["n"]
        a[1] += r["sxg"]
        a[2] += r["sxg2"]
        a[3] += r["gols"]
    return out


def padronizar(linhas, faixa_de_interesse, celula, pesos):
    """xG por chute da faixa, com a composição de `pesos` (dict célula -> peso). Devolve (média, erro-padrão, chutes)."""
    cel = agrupar([r for r in linhas if r["bucket"] == faixa_de_interesse], celula)
    usados = {c: a for c, a in cel.items() if a[0] >= 5 and pesos.get(c, 0) > 0}
    tw = sum(pesos[c] for c in usados)
    if tw == 0:
        return float("nan"), float("nan"), 0
    m = sum(pesos[c] / tw * a[1] / a[0] for c, a in usados.items())
    v = sum((pesos[c] / tw) ** 2 * media_var(a[0], a[1], a[2])[1] / a[0] for c, a in usados.items())
    return m, math.sqrt(v), sum(a[0] for a in usados.values())


def inclinacao(pontos):
    """Regressão linear ponderada de y em x: [(x, y, erro-padrão de y)] -> (inclinação, erro-padrão), pesos = 1/ep²."""
    w = [1 / (p[2] ** 2) for p in pontos]
    sw = sum(w)
    mx = sum(wi * p[0] for wi, p in zip(w, pontos)) / sw
    my = sum(wi * p[1] for wi, p in zip(w, pontos)) / sw
    sxx = sum(wi * (p[0] - mx) ** 2 for wi, p in zip(w, pontos))
    return sum(wi * (p[0] - mx) * (p[1] - my) for wi, p in zip(w, pontos)) / sxx, math.sqrt(1 / sxx)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--entrada", default=CAMINHO)
    ap.add_argument("--saida", default="")
    a = ap.parse_args()
    linhas = json.load(open(a.entrada))
    res = {}

    def cel(r):
        return (r["liga"], r["estado"], r["perfil"], r["tipo"])

    print("== 1. xG por chute por faixa de tempo (sem controle) ==")
    bruto = agrupar(linhas, lambda r: r["bucket"])
    bruto_tit = agrupar([r for r in linhas if r["papel"] == "titular"], lambda r: r["bucket"])
    res["bruto"] = {}
    for b in range(7):
        n, s, s2, g = bruto[b]
        nt, st, st2, _ = bruto_tit[b]
        m, v = media_var(n, s, s2)
        mt, vt = media_var(nt, st, st2)
        res["bruto"][FAIXAS[b]] = {"n": n, "xg_chute": m, "ep": math.sqrt(v / n), "titulares_xg_chute": mt, "titulares_ep": math.sqrt(vt / nt), "gols_por_xg": g / s}
        print(f"  {FAIXAS[b]:6s} n={n:6d} todos {m:.4f} ±{math.sqrt(v / n):.4f} | só titulares {mt:.4f} ±{math.sqrt(vt / nt):.4f} | gols/xG {g / s:.3f}")

    print("\n== 2. padronizado por liga x estado x perfil x tipo (só titulares) ==")
    tit = [r for r in linhas if r["papel"] == "titular"]
    pesos = {c: a[0] for c, a in agrupar(tit, cel).items()}
    pontos, res["padronizado_titulares"] = [], {}
    for b in range(7):
        m, ep, n = padronizar(tit, b, cel, pesos)
        res["padronizado_titulares"][FAIXAS[b]] = {"xg_chute": m, "ep": ep, "n": n}
        print(f"  {FAIXAS[b]:6s} {m:.4f} ±{ep:.4f}  (n={n})")
        if b <= 5:
            pontos.append((b, m, ep))
    sl, ep = inclinacao(pontos)
    res["inclinacao_por_faixa_titulares_padronizado"] = {"valor": sl, "ep": ep}
    print(f"  inclinação (faixas 0-15 a 75-90): {sl:+.5f} xG/chute por faixa de 15 min (ep {ep:.5f}; z = {sl / ep:+.1f}); de 0-15 a 75-90: {5 * sl:+.4f}")

    print("\n== 3. mesmo, separando jogo corrido e bola parada (só titulares) ==")
    for tipo in ("jogo_corrido", "bola_parada"):
        sub = [r for r in tit if r["tipo"] == tipo]
        pw = {c: a[0] for c, a in agrupar(sub, cel).items()}
        pts, linha = [], []
        for b in range(6):
            m, ep, n = padronizar(sub, b, cel, pw)
            pts.append((b, m, ep))
            linha.append(f"{m:.4f}")
        sl, ep = inclinacao(pts)
        res[f"inclinacao_{tipo}"] = {"valor": sl, "ep": ep, "faixas": [p[1] for p in pts]}
        print(f"  {tipo:13s} {' '.join(linha)} | inclinação {sl:+.5f} (ep {ep:.5f}, z = {sl / ep:+.1f})")

    print("\n== 4. por estado do placar (titulares, padronizado por liga x perfil x tipo) ==")
    cel2 = lambda r: (r["liga"], r["perfil"], r["tipo"])
    res["por_estado"] = {}
    for est in ("perdendo", "empatando", "ganhando"):
        sub = [r for r in tit if r["estado"] == est]
        pw = {c: a[0] for c, a in agrupar(sub, cel2).items()}
        pts, linha = [], []
        for b in range(6):
            m, ep, n = padronizar(sub, b, cel2, pw)
            pts.append((b, m, ep))
            linha.append(f"{m:.4f}")
        sl, ep = inclinacao(pts)
        res["por_estado"][est] = {"faixas": [p[1] for p in pts], "inclinacao": sl, "ep": ep}
        print(f"  {est:10s} {' '.join(linha)} | inclinação {sl:+.5f} (ep {ep:.5f}, z = {sl / ep:+.1f})")

    print("\n== 5. reserva contra titular na MESMA célula (liga, faixa de tempo, estado, perfil, tipo) ==")
    por_cel = defaultdict(dict)
    for r in linhas:
        por_cel[(r["liga"], r["bucket"], r["estado"], r["perfil"], r["tipo"])].setdefault(r["papel"], []).append(r)
    res["reserva_contra_titular"] = {}
    for papel in ("reserva_0_15", "reserva_15_30", "reserva_30mais", "sem_escalacao"):
        num = den = var = 0.0
        n_res = 0
        for c, d in por_cel.items():
            if papel not in d or "titular" not in d:
                continue
            ar = agrupar(d[papel], lambda r: 0)[0]
            at = agrupar(d["titular"], lambda r: 0)[0]
            if ar[0] < 5 or at[0] < 20:
                continue
            mr, vr = media_var(ar[0], ar[1], ar[2])
            mt, vt = media_var(at[0], at[1], at[2])
            w = ar[0]
            num += w * (mr - mt)
            den += w
            var += w * w * (vr / ar[0] + vt / at[0])
            n_res += ar[0]
        dif, ep = num / den, math.sqrt(var) / den
        res["reserva_contra_titular"][papel] = {"diferenca": dif, "ep": ep, "chutes_reserva": n_res}
        print(f"  {papel:15s} reserva - titular = {dif:+.4f} xG/chute (ep {ep:.4f}, z = {dif / ep:+.1f}), {n_res} chutes de reserva")
    # só reservas contra titulares nas faixas finais (60+), onde o reserva realmente joga
    print("\n== 6. reserva (qualquer tempo desde a entrada) x titular, só a partir dos 60 min ==")
    num = den = var = 0.0
    n_res = 0
    for c, d in por_cel.items():
        if c[1] < 4:
            continue
        rs = [r for p in ("reserva_0_15", "reserva_15_30", "reserva_30mais") for r in d.get(p, [])]
        if not rs or "titular" not in d:
            continue
        ar = agrupar(rs, lambda r: 0)[0]
        at = agrupar(d["titular"], lambda r: 0)[0]
        if ar[0] < 5 or at[0] < 20:
            continue
        mr, vr = media_var(ar[0], ar[1], ar[2])
        mt, vt = media_var(at[0], at[1], at[2])
        num += ar[0] * (mr - mt)
        den += ar[0]
        var += ar[0] ** 2 * (vr / ar[0] + vt / at[0])
        n_res += ar[0]
    res["reserva_contra_titular_60mais"] = {"diferenca": num / den, "ep": math.sqrt(var) / den, "chutes_reserva": n_res}
    print(f"  reserva - titular = {num / den:+.4f} xG/chute (ep {math.sqrt(var) / den:.4f}), {n_res} chutes de reserva")

    # quanto do chute de cada faixa vem de reserva (para ver se a mistura poderia mascarar a fadiga do titular)
    todos = agrupar(linhas, lambda r: r["bucket"])
    res_ = agrupar([r for r in linhas if r["papel"].startswith("reserva")], lambda r: r["bucket"])
    res["share_reserva"] = {FAIXAS[b]: res_[b][0] / todos[b][0] for b in range(7)}
    print("\n== 7. participação de reservas nos chutes de cada faixa ==")
    print("  " + "  ".join(f"{FAIXAS[b]} {100 * res['share_reserva'][FAIXAS[b]]:.1f}%" for b in range(7)))
    if a.saida:
        json.dump(res, open(a.saida, "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
