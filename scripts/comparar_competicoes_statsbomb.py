#!/usr/bin/env python3
"""Baixa competições do StatsBomb Open Data e compara a matriz de transição (18 zonas) e a origem dos escanteios com a da La Liga 2015/16.

Achado 26 de ACHADOS_COMPORTAMENTO.md. Reaproveita `gerar_matriz_transicao_statsbomb.py` (regra de zonas, definição de ação/perda) e a
atribuição do escanteio ao ÚLTIMO LANCE do time que atacava (`analisar_origem_escanteios_statsbomb.py`).

Grupos (nome -> [(competition_id, season_name)]), todos masculinos e gratuitos; a Copa Africana de Nações (1267) fica de fora de propósito:
    ligas_2015_16 : Premier League (2), Serie A (12), Ligue 1 (7), La Liga (11, só para conferir a baseline)
    copa_mundo    : FIFA World Cup (43), 8 edições (1958 a 2022)
    euro          : UEFA Euro (55), 2020 e 2024
    copa_america  : Copa America (223), 2024

Uso (cada grupo grava `<cache>/<grupo>.json`; refazer não baixa de novo):
    python scripts/comparar_competicoes_statsbomb.py baixar  --cache /tmp/sb_comp [--grupos euro,copa_america]
    python scripts/comparar_competicoes_statsbomb.py comparar --cache /tmp/sb_comp --baseline /tmp/sb/v2/acoes_v2.json
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import os
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gerar_matriz_transicao_statsbomb as g  # noqa: E402

GRUPOS = {
    "ligas_2015_16": [(2, "2015/2016"), (12, "2015/2016"), (7, "2015/2016")],
    "copa_mundo": [(43, s) for s in ("1958", "1962", "1970", "1974", "1986", "1990", "2018", "2022")],
    "euro": [(55, "2020"), (55, "2024")],
    "copa_america": [(223, "2024")],
}
EXCLUIDAS = {1267}   # Copa Africana de Nações (pedido do usuário)
LANCES_ATACANTE = {"Shot", "Pass", "Carry", "Dribble", "Dispossessed", "Miscontrol"}
IGNORAR_ANTES = {"Pressure", "Ball Receipt*", "Starting XI", "Half Start", "Camera On", "Injury Stoppage", "Substitution", "Tactical Shift"}


def origem_escanteios(eventos: list[dict], janela: int = 25) -> list[tuple[str, int | None]]:
    """Para cada escanteio (Pass com type Corner): (tipo do último lance do time que cobrou, zona de 18 onde esse lance começou).
    Procura até `janela` eventos antes. Tipo: 'Chute', 'Cruzamento/<resultado>', 'Passe/<resultado>', 'Conducao' ou o nome do evento."""
    saida = []
    for i, e in enumerate(eventos):
        if e["type"]["name"] != "Pass" or ((e["pass"].get("type") or {}).get("name")) != "Corner":
            continue
        time_id = e["team"]["id"]
        achou = None
        for k in range(i - 1, max(i - 1 - janela, -1), -1):
            p = eventos[k]
            if p["team"]["id"] == time_id and p["type"]["name"] in LANCES_ATACANTE and p.get("location"):
                achou = p
                break
        if achou is None:
            saida.append(("?", None))
            continue
        t = achou["type"]["name"]
        if t == "Shot":
            d = "Chute"
        elif t == "Pass":
            pp = achou["pass"]
            r = (pp.get("outcome") or {}).get("name", "completo")
            d = ("Cruzamento" if pp.get("cross") else "Passe") + "/" + r
        elif t == "Carry":
            d = "Conducao"
        else:
            d = t
        saida.append((d, g.zona_18(*g.para_metros(*achou["location"][:2]))))
    return saida


def jogos_do_grupo(grupo: str) -> list[tuple[int, str]]:
    """[(match_id, rótulo competição/temporada)] do grupo, a partir de competitions.json (competições excluídas nunca entram)."""
    comps = g.baixar_json(f"{g.BASE}/competitions.json")
    alvo = []
    for cid, temporada in GRUPOS[grupo]:
        if cid in EXCLUIDAS:
            continue
        for c in comps:
            if c["competition_id"] == cid and c["season_name"] == temporada and c.get("competition_gender") == "male":
                alvo.append((c["competition_id"], c["season_id"], f'{c["competition_name"]} {c["season_name"]}'))
    jogos = []
    for cid, sid, rotulo in alvo:
        for m in g.baixar_json(f"{g.BASE}/matches/{cid}/{sid}.json"):
            jogos.append((m["match_id"], rotulo))
    return sorted(set(jogos))


def baixar_grupo(grupo: str, cache: str, workers: int = 6) -> dict:
    caminho = os.path.join(cache, f"{grupo}.json")
    if os.path.exists(caminho):
        return json.load(open(caminho))
    jogos = jogos_do_grupo(grupo)
    print(f"{grupo}: {len(jogos)} jogos")

    def um(par):
        mid, rotulo = par
        ev = g.baixar_json(f"{g.BASE}/events/{mid}.json")
        return rotulo, g.acoes_da_partida(ev), origem_escanteios(ev)

    por_comp: dict[str, dict] = collections.defaultdict(lambda: {"jogos": 0, "acoes": [], "escanteios": []})
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, (rotulo, acoes, esc) in enumerate(ex.map(um, jogos), 1):
            c = por_comp[rotulo]
            c["jogos"] += 1
            c["acoes"].extend(acoes)
            c["escanteios"].extend(esc)
            if i % 100 == 0:
                print(f"  {i}/{len(jogos)}")
    res = dict(por_comp)
    os.makedirs(cache, exist_ok=True)
    json.dump(res, open(caminho, "w"))
    return res


# ---------------------------------------------------------------------------------------------- comparação

def simbolos(acoes: list) -> list[tuple[int, int]]:
    """Ação -> (zona de origem 0..17, desfecho 0..17 = zona de destino da continuação, 18 = chute, 19 = perda)."""
    out = []
    for a in acoes:
        tipo, x0, y0, x1, y1 = a[:5]
        o = g.zona_18(*g.para_metros(x0, y0))
        out.append((o, g.zona_18(*g.para_metros(x1, y1)) if tipo == "continua" else (18 if tipo == "chute" else 19)))
    return out


def modelo(dados: list[tuple[int, int]], alfa: float = 0.5) -> list[list[float]]:
    c = [[alfa] * 20 for _ in range(18)]
    for o, s in dados:
        c[o][s] += 1
    return [[v / sum(l) for v in l] for l in c]


def loglik(dados: list[tuple[int, int]], m: list[list[float]]) -> float:
    return sum(math.log(m[o][s]) for o, s in dados) / len(dados)


def comparar_com_baseline(dados: list[tuple[int, int]], baseline: list[tuple[int, int]], blocos: int = 5) -> dict:
    """Em `dados` (outra competição): log-verossimilhança média por ação (nats) do modelo da BASELINE (La Liga 2015/16 inteira) contra a
    do modelo PRÓPRIO treinado por validação cruzada em blocos contíguos. Diferença positiva (próprio - baseline) = a baseline não serve bem."""
    mb = modelo(baseline)
    n = len(dados)
    dif = []
    for b in range(blocos):
        ini, fim = b * n // blocos, (b + 1) * n // blocos
        treino, teste = dados[:ini] + dados[fim:], dados[ini:fim]
        dif.append(loglik(teste, modelo(treino)) - loglik(teste, mb))
    media = sum(dif) / blocos
    dp = (sum((d - media) ** 2 for d in dif) / (blocos - 1)) ** 0.5
    return {"n": n, "ganho_proprio_menos_baseline": media, "erro_padrao": dp / blocos ** 0.5}


def taxas(dados: list[tuple[int, int]]) -> dict:
    """Taxas globais de chute, perda e continuação, e por faixa de zona (grande área = zonas 15..17)."""
    tot = len(dados)
    chute = sum(1 for o, s in dados if s == 18)
    perda = sum(1 for o, s in dados if s == 19)
    area = [(o, s) for o, s in dados if o >= 15]
    ac = max(len(area), 1)
    centro = [(o, s) for o, s in dados if o == 16]
    cc = max(len(centro), 1)
    return {"acoes": tot, "chute": chute / tot, "perda": perda / tot,
            "grande_area_chute": sum(1 for o, s in area if s == 18) / ac, "grande_area_perda": sum(1 for o, s in area if s == 19) / ac,
            "centro_area_chute": sum(1 for o, s in centro if s == 18) / cc, "centro_area_perda": sum(1 for o, s in centro if s == 19) / cc,
            "n_centro_area": len(centro)}


def resumo_escanteios(esc: list, jogos: int, acoes: list) -> dict:
    n = len(esc)
    c = collections.Counter(d for d, z in esc)
    chutes = sum(1 for a in acoes if a[0] == "chute")
    cruz = sum(1 for a in acoes if len(a) > 5 and a[5] == "cruzamento")
    return {"escanteios": n, "por_jogo": n / jogos if jogos else 0.0, "por_100_chutes_total": 100 * n / chutes if chutes else 0.0,
            "pct_chute": 100 * c["Chute"] / n if n else 0.0, "pct_cruzamento_errado": 100 * sum(v for k, v in c.items() if k.startswith("Cruzamento")) / n if n else 0.0,
            "pct_passe_errado": 100 * sum(v for k, v in c.items() if k.startswith("Passe")) / n if n else 0.0,
            "por_100_chutes_lance": 100 * c["Chute"] / chutes if chutes else 0.0,
            "por_100_cruzamentos": 100 * sum(v for k, v in c.items() if k.startswith("Cruzamento")) / cruz if cruz else 0.0}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("acao", choices=["baixar", "comparar"])
    ap.add_argument("--cache", required=True)
    ap.add_argument("--grupos", default=",".join(GRUPOS))
    ap.add_argument("--baseline", default=None, help="acoes_v2.json da La Liga 2015/16 (gerado por gerar_matriz_transicao_statsbomb.py)")
    args = ap.parse_args()
    grupos = [x for x in args.grupos.split(",") if x]
    if args.acao == "baixar":
        for gr in grupos:
            r = baixar_grupo(gr, args.cache)
            for k, v in r.items():
                print(f"  {k}: {v['jogos']} jogos, {len(v['acoes'])} acoes, {len(v['escanteios'])} escanteios")
        return
    base = [tuple(a) for a in json.load(open(args.baseline))]
    sb = simbolos(base)
    tb = taxas(sb)
    print(f"BASELINE La Liga 2015/16: {tb['acoes']} acoes | chute {tb['chute']:.4f} perda {tb['perda']:.4f} | centro da area: chute {tb['centro_area_chute']:.3f} perda {tb['centro_area_perda']:.3f}")
    for gr in grupos:
        for rotulo, c in json.load(open(os.path.join(args.cache, f"{gr}.json"))).items():
            if not c["acoes"]:
                continue
            ac = [tuple(a) for a in c["acoes"]]
            d = simbolos(ac)
            t = taxas(d)
            cmp_ = comparar_com_baseline(d, sb)
            e = resumo_escanteios(c["escanteios"], c["jogos"], ac)
            print(f"{rotulo:28s} jogos={c['jogos']:3d} acoes={t['acoes']:6d} chute={t['chute']:.4f} perda={t['perda']:.4f} "
                  f"centro-area(n={t['n_centro_area']}): chute {t['centro_area_chute']:.3f} perda {t['centro_area_perda']:.3f} | "
                  f"ganho proprio-baseline {cmp_['ganho_proprio_menos_baseline']:+.4f}±{cmp_['erro_padrao']:.4f} | "
                  f"escanteios/jogo {e['por_jogo']:.1f}, {e['por_100_chutes_lance']:.1f}/100 chutes, {e['por_100_cruzamentos']:.1f}/100 cruz., {e['pct_chute']:.0f}% de chute")


if __name__ == "__main__":
    main()
