#!/usr/bin/env python3
"""Backtest walk-forward do simulador da cadeia da bola COMO PREVISOR (1X2 e over/under 2,5).

Pergunta: as probabilidades que saem de simular cada jogo N vezes batem o Dixon-Coles e o mercado (odds de fechamento)?

Regras que mantêm o teste honesto:
  * Walk-forward: a força de cada time para o jogo J usa SÓ jogos com data anterior à de J (a temporada de aquecimento entra inteira, a de teste só até J).
  * A força vem de chutes feitos/sofridos (FotMob), encolhida para 1 com `k` pseudo-jogos. Os multiplicadores de perda/quebra ficam em 1: o FotMob não tem as
    coordenadas de passe que o forca_dos_times.py usa (StatsBomb), então só o canal "chute" tem força por time.
  * Mando de campo: o simulador não tem; entra como fator no ataque (mandante x sqrt(h), visitante / sqrt(h), h = chutes casa / chutes fora da história).
  * Nível: o simulador neutro faz ~12,7 chutes por time; o ataque é reescalado para a média de chutes por time da história.
  * Controle obrigatório: a mesma simulação com todos os times iguais (só nível e mando) -- é o piso que a força dos times precisa bater.
Camadas: ver scripts/camadas_simulador.py; cada variante liga uma camada a mais e é comparada com a anterior.
Comparação: log-loss e Brier por jogo, apenas nos jogos que têm Dixon-Coles E odds; diferença pareada com IC 95% por bootstrap sobre jogos.
Parâmetros ajustáveis SEM mexer no código: --exp-mando / --exp-forca / --exp-estado (expoentes de amplificação dos multiplicadores de chute), --alvo-casa / --alvo-fora / --alvo-ctx (placar desejado, global ou por contexto de mando e favoritismo) e --cfg-extra (JSON com
overrides de camadas_simulador.CONFIG para todas as variantes). Os valores usados ficam gravados em resumo["parametros"] do JSON de saída.
Uso: python scripts/backtest_simulador_preditivo.py --csv jogos.csv --temporada 2025 --sims 1000 --saida resultado.json
CSV: id,season,date,home,away,hg,ag,hs,as,hxg,axg,hc,ac,dc_h,dc_d,dc_a,dc_over,o_h,o_d,o_a,o_over,o_under
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import multiprocessing as mp
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import simulador_cadeia_bola as s  # noqa: E402
import camadas_simulador as cam  # noqa: E402

# Cada variante = lista de camadas (scripts/camadas_simulador.py) + semente própria. "neutro" e "forca" mantêm as sementes do Achado 46 (confere que o
# resultado se reproduz). Cada variante é comparada com a anterior da cadeia ("ref") para medir o que a camada nova acrescenta.
BASE = ["nivel_chutes", "mando_chutes"]
SEM_MANDO = ["nivel_chutes"]
KEEP = BASE + ["forca_chutes", "gols_nivel"]                  # o que o Achado 47 deixou ligado
VARIANTES = {
    "neutro": {"camadas": BASE, "semente": 2, "ref": None, "padrao": False},
    "forca": {"camadas": BASE + ["forca_chutes"], "semente": 1, "ref": "neutro", "padrao": False},
    "forca_gols_nivel": {"camadas": KEEP, "semente": 3, "ref": "forca", "padrao": True},
    "forca_gols_nivel_mando": {"camadas": KEEP + ["gols_mando"], "semente": 4, "ref": "forca_gols_nivel", "padrao": False},
    # Achado 48: o mando do chute (e depois o da conversão) olhando só os últimos N jogos, no lugar da história inteira
    # Achado 51: expoente de amplificação (calibrado em scripts/calibrar_elasticidade_chute.py, fixado em 2 antes de olhar o teste); teto mais largo porque o produto amplificado passa de 1,6
    "exp_mando": {"camadas": KEEP, "semente": 8, "ref": "forca_gols_nivel", "cfg": {"exp_mando": 2.0, "teto": (0.5, 2.0)}, "padrao": False},
    "exp_forca": {"camadas": KEEP, "semente": 9, "ref": "forca_gols_nivel", "cfg": {"exp_forca": 2.0, "teto": (0.5, 2.0)}, "padrao": True},
    "exp_ambos": {"camadas": KEEP, "semente": 10, "ref": "forca_gols_nivel", "cfg": {"exp_mando": 2.0, "exp_forca": 2.0, "teto": (0.5, 2.0)}, "padrao": False},
    # Achado 52: reação ao placar (volume de chutes e qualidade), tabela estimada em 2022-2024; referência = a melhor configuração do Achado 51 (força amplificada)
    "estado_v": {"camadas": KEEP, "semente": 11, "ref": "exp_forca", "cfg": {"exp_forca": 2.0, "teto": (0.5, 2.0), "estado": {"volume": True, "qualidade": False}}, "padrao": True},
    "estado_q": {"camadas": KEEP, "semente": 12, "ref": "exp_forca", "cfg": {"exp_forca": 2.0, "teto": (0.5, 2.0), "estado": {"volume": False, "qualidade": True}}, "padrao": True},
    "estado_ctx": {"camadas": KEEP, "semente": 14, "ref": "estado_vq", "cfg": {"exp_forca": 2.0, "teto": (0.5, 2.0), "estado": {"volume": True, "qualidade": True, "ctx": True}}, "padrao": True},
    "estado_vq": {"camadas": KEEP, "semente": 13, "ref": "exp_forca", "cfg": {"exp_forca": 2.0, "teto": (0.5, 2.0), "estado": {"volume": True, "qualidade": True}}, "padrao": True},
    "mando_j200": {"camadas": SEM_MANDO + ["mando_chutes_janela", "forca_chutes", "gols_nivel"], "semente": 5, "ref": "forca_gols_nivel", "cfg": {"janela_mando": 200}, "padrao": False},
    "mando_j100": {"camadas": SEM_MANDO + ["mando_chutes_janela", "forca_chutes", "gols_nivel"], "semente": 6, "ref": "forca_gols_nivel", "cfg": {"janela_mando": 100}, "padrao": False},
    "mando_j200_gols": {"camadas": SEM_MANDO + ["mando_chutes_janela", "forca_chutes", "gols_nivel", "gols_mando_janela"], "semente": 7, "ref": "mando_j200", "cfg": {"janela_mando": 200}, "padrao": False},
}
_P = None


def _iniciar():
    global _P
    os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    _P = s.Parametros()


def _simular_jogo(args):
    jid, mult_casa, mult_fora, n, semente, estado = args
    rng = random.Random(semente)
    casa = s.Multiplicadores(**mult_casa)
    fora = s.Multiplicadores(**mult_fora)
    h = d = a = over = 0
    gc = gf = ch = cf = 0
    for _ in range(n):
        r = s.simular_partida(_P, rng, times=(casa, fora), estado=estado)
        g0, g1 = r.get("gols_0", 0), r.get("gols_1", 0)
        h += g0 > g1
        d += g0 == g1
        a += g0 < g1
        over += (g0 + g1) >= 3
        gc, gf, ch, cf = gc + g0, gf + g1, ch + r.get("chutes_0", 0), cf + r.get("chutes_1", 0)
    return jid, {"h": h, "d": d, "a": a, "over": over, "n": n, "gols_casa": gc / n, "gols_fora": gf / n, "chutes_casa": ch / n, "chutes_fora": cf / n}


def ler(caminho):
    out = []
    for r in csv.DictReader(open(caminho)):
        j = {"id": int(r["id"]), "season": r["season"], "date": r["date"], "home": int(r["home"]), "away": int(r["away"]),
             "hg": int(r["hg"]), "ag": int(r["ag"]), "hs": float(r["hs"]), "as": float(r["as"]),
             "elod": float(r["elod"]) if r.get("elod") not in (None, "") else None, "neutro": r.get("neutro") == "1"}
        for k in ("dc_h", "dc_d", "dc_a", "dc_over", "o_h", "o_d", "o_a", "o_over", "o_under"):
            j[k] = float(r[k]) if r[k] != "" else None
        out.append(j)
    out.sort(key=lambda j: (j["date"], j["id"]))
    return out


def devig(odds):
    inv = [1.0 / o for o in odds]
    t = sum(inv)
    return [x / t for x in inv]


def perda_1x2(p, res):                      # res: 0 casa, 1 empate, 2 fora
    return -math.log(max(p[res], 1e-12)), sum((p[i] - (1.0 if i == res else 0.0)) ** 2 for i in range(3))


def perda_ou(po, over):
    po = min(max(po, 1e-12), 1 - 1e-12)
    return -math.log(po if over else 1 - po), (po - (1.0 if over else 0.0)) ** 2


def bootstrap_ic(dif, B=5000, semente=7):
    rng = random.Random(semente)
    n = len(dif)
    medias = sorted(sum(dif[rng.randrange(n)] for _ in range(n)) / n for _ in range(B))
    return sum(dif) / n, medias[int(0.025 * B)], medias[int(0.975 * B)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--temporada", default="2025")
    ap.add_argument("--sims", type=int, default=1000)
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--limite", type=int, default=0, help="só os N primeiros jogos de teste (depuração)")
    ap.add_argument("--saida", default="resultado_backtest_simulador.json")
    ap.add_argument("--variantes", default="", help="lista separada por vírgula, ou 'todas'; padrão: as marcadas como padrão em VARIANTES")
    ap.add_argument("--exp-mando", type=float, default=2.0, help="expoente de amplificação do mando de chutes nas variantes exp_mando/exp_ambos (1 = sem amplificar; calibrado em 2: ver calibrar_elasticidade_chute.py)")
    ap.add_argument("--exp-forca", type=float, default=2.0, help="idem, para a força dos times (ataque e defesa) nas variantes exp_forca/exp_ambos")
    ap.add_argument("--exp-estado", type=float, default=2.0, help="expoente do multiplicador de VOLUME da reação ao placar nas variantes estado_* (1 = sem amplificar)")
    ap.add_argument("--alvo-casa", type=float, default=1.0, help="placar desejado do mandante (saldo final satisfatório; 1 = ganhar, 0 = empate basta, 2 = vencer por 2, -1 = aceita perder por 1)")
    ap.add_argument("--alvo-fora", type=float, default=1.0, help="placar desejado do visitante (idem)")
    ap.add_argument("--alvo-ctx", default="{}", help='JSON {"local|perfil": alvo} que substitui o placar desejado do time com esse contexto na variante estado_ctx, ex.: \'{"fora|fraco": 0, "casa|fraco": 0.5}\' (local: casa/fora/neutro; perfil: forte/parelho/fraco)')
    ap.add_argument("--cfg-extra", default="{}", help='JSON com overrides de camadas_simulador.CONFIG aplicados a TODAS as variantes escolhidas, ex.: \'{"k_time": 12, "teto": [0.5, 2.0]}\'')
    ap.add_argument("--fatia", default="", help="K/N: simula só as tarefas de índice ≡ K (mod N) e grava o progresso (para dividir entre jobs)")
    ap.add_argument("--so-simular", action="store_true", help="simula a fatia e para, sem agregar")
    ap.add_argument("--agregar", action="store_true", help="não simula nada: junta os arquivos de progresso e calcula; erra se faltar tarefa")
    ap.add_argument("--parciais", default="", help="glob dos .jsonl de progresso a juntar (padrão: <saida>.parcial.jsonl)")
    a = ap.parse_args()

    global VARIANTES
    escolhidas = list(VARIANTES) if a.variantes == "todas" else (a.variantes.split(",") if a.variantes else [k for k, v in VARIANTES.items() if v["padrao"]])
    VARIANTES = {k: {**VARIANTES[k], "cfg": dict(VARIANTES[k].get("cfg", {}))} for k in escolhidas}
    for nome, v in VARIANTES.items():                           # os expoentes das variantes exp_* vêm da linha de comando, não do código
        if "exp_mando" in v["cfg"]:
            v["cfg"]["exp_mando"] = a.exp_mando
        if "exp_forca" in v["cfg"]:
            v["cfg"]["exp_forca"] = a.exp_forca
        v["cfg"].update({k: (tuple(x) if isinstance(x, list) else x) for k, x in json.loads(a.cfg_extra).items()})
        if "estado" in v["cfg"]:
            v["cfg"]["estado"] = {**v["cfg"]["estado"], "exp_volume": a.exp_estado, "alvo": [a.alvo_casa, a.alvo_fora], "alvo_ctx": json.loads(a.alvo_ctx)}
    # "impressão digital" dos parâmetros de cada variante: o arquivo de progresso só é reaproveitado se for idêntica (mudar um expoente não mistura resultados)
    digital = {n: json.dumps({"camadas": v["camadas"], "cfg": {**cam.CONFIG, **v["cfg"]}, "semente": v["semente"], "sims": a.sims}, sort_keys=True, default=list) for n, v in VARIANTES.items()}
    estados = {}
    for nome, v in VARIANTES.items():
        e = v["cfg"].get("estado")
        estados[nome] = cam.montar_estado(volume=e["volume"], qualidade=e["qualidade"], exp_volume=e["exp_volume"], alvo=tuple(e["alvo"])) if e and not e.get("ctx") else None
    jogos = ler(a.csv)
    hist, tarefas, meta = cam.Historia(), [], {}
    teste = 0
    for j in jogos:
        if j["season"] == a.temporada and hist.jogos >= 100 and (not a.limite or teste < a.limite):
            teste += 1
            for nome, v in VARIANTES.items():
                e = v["cfg"].get("estado")
                if e and e.get("ctx"):                              # estado ajustado por mando e favoritismo (Elo) deste jogo
                    estados[nome] = cam.montar_estado_jogo(j["elod"], j["neutro"], e["volume"], e["qualidade"], e["exp_volume"], tuple(e["alvo"]), e.get("alvo_ctx") or None, cam.CONFIG["elo_corte"])
                mc, mf = cam.multiplicadores(v["camadas"], hist, j["home"], j["away"], {**cam.CONFIG, **v.get("cfg", {})})
                tarefas.append(((j["id"], nome), mc, mf, a.sims, j["id"] * 10 + v["semente"], estados[nome]))
            meta[j["id"]] = j
        hist.add(j)

    print(f"{teste} jogos de teste, {len(tarefas)} simulações de {a.sims} partidas, {a.workers} processos", flush=True)
    # progresso gravado a cada simulação concluída: se o processo morrer (contêiner reiniciado), a próxima execução retoma daqui
    parcial = a.saida + ".parcial.jsonl"
    resultados, descartadas = {}, 0
    for arq_in in sorted(glob.glob(a.parciais)) if a.parciais else ([parcial] if os.path.exists(parcial) else []):
        for linha in open(arq_in):
            jid, variante, r, dig = json.loads(linha)
            if variante in digital and dig == digital[variante]:
                resultados[(jid, variante)] = r
            else:
                descartadas += 1
    print(f"{len(resultados)} simulações já prontas" + (f" ({descartadas} descartadas: parâmetros diferentes dos atuais)" if descartadas else ""), flush=True)
    if not a.agregar:
        pendentes = [t for t in tarefas if t[0] not in resultados]
        if a.fatia:
            k, n_f = (int(x) for x in a.fatia.split("/"))
            indice = {t[0]: i for i, t in enumerate(tarefas)}
            pendentes = [t for t in pendentes if indice[t[0]] % n_f == k]
        args = [(t[0], t[1], t[2], t[3], t[4], t[5]) for t in pendentes]
        print(f"simulando {len(args)}", flush=True)
        with mp.Pool(a.workers, initializer=_iniciar) as pool, open(parcial, "a") as arq:
            for i, (jid, r) in enumerate(pool.imap_unordered(_simular_jogo, args, chunksize=2), 1):
                resultados[jid] = r
                arq.write(json.dumps([jid[0], jid[1], r, digital[jid[1]]]) + "\n")
                arq.flush()
                if i % 50 == 0:
                    print(f"  {i}/{len(args)}", flush=True)
        if a.so_simular:
            return
    faltam = [t[0] for t in tarefas if t[0] not in resultados]
    if faltam:
        sys.exit(f"faltam {len(faltam)} simulações (ex.: {faltam[:3]}); rode todas as fatias antes de agregar")

    linhas = []
    for jid, j in meta.items():
        res = 0 if j["hg"] > j["ag"] else (1 if j["hg"] == j["ag"] else 2)
        over = (j["hg"] + j["ag"]) >= 3
        lin = {"id": jid, "data": j["date"], "res": res, "over": over}
        for v in VARIANTES:
            r = resultados[(jid, v)]
            n = r["n"]
            lin[v] = {"p1x2": [(r["h"] + 1) / (n + 3), (r["d"] + 1) / (n + 3), (r["a"] + 1) / (n + 3)], "pover": (r["over"] + 1) / (n + 2),
                      "gols": r["gols_casa"] + r["gols_fora"], "chutes": r["chutes_casa"] + r["chutes_fora"]}
        if j["dc_h"] is not None:
            lin["dc"] = {"p1x2": [j["dc_h"], j["dc_d"], j["dc_a"]], "pover": j["dc_over"]}
        if j["o_h"] is not None and j["o_over"] is not None:
            lin["mercado"] = {"p1x2": devig([j["o_h"], j["o_d"], j["o_a"]]), "pover": devig([j["o_over"], j["o_under"]])[0]}
        linhas.append(lin)

    comuns = [l for l in linhas if "dc" in l and "mercado" in l]
    modelos = list(VARIANTES) + ["dc", "mercado"]
    resumo = {"jogos_teste": len(linhas), "jogos_comuns": len(comuns), "sims_por_jogo": a.sims,
              "parametros": {"exp_mando": a.exp_mando, "exp_forca": a.exp_forca, "exp_estado": a.exp_estado, "alvo_casa": a.alvo_casa, "alvo_fora": a.alvo_fora, "alvo_ctx": json.loads(a.alvo_ctx), "cfg_extra": json.loads(a.cfg_extra),
                             "variantes": {n: {"camadas": v["camadas"], "cfg": {k: x for k, x in v["cfg"].items()}} for n, v in VARIANTES.items()}}}
    perdas = {m: {"ll": [], "br": [], "ll_ou": [], "br_ou": []} for m in modelos}
    for l in comuns:
        for m in modelos:
            ll, br = perda_1x2(l[m]["p1x2"], l["res"])
            lo, bo = perda_ou(l[m]["pover"], l["over"])
            for k, v in (("ll", ll), ("br", br), ("ll_ou", lo), ("br_ou", bo)):
                perdas[m][k].append(v)
    n = len(comuns)
    resumo["medias"] = {m: {k: sum(v) / n for k, v in perdas[m].items()} for m in modelos}
    pares = [(v, nome["ref"]) for v, nome in VARIANTES.items() if nome["ref"] in VARIANTES] + [(v, "dc") for v in VARIANTES] + [(v, "mercado") for v in VARIANTES] + [("dc", "mercado")]
    resumo["diferencas_pareadas"] = {}
    for par in pares:
        for k in ("ll", "br", "ll_ou", "br_ou"):
            dif = [x - y for x, y in zip(perdas[par[0]][k], perdas[par[1]][k])]
            m, lo, hi = bootstrap_ic(dif)
            resumo["diferencas_pareadas"][f"{par[0]}-{par[1]}:{k}"] = {"media": m, "ic95": [lo, hi]}
    # diagnóstico nos jogos comuns: o nível e a forma das probabilidades de cada modelo contra o que aconteceu
    resumo["diagnostico"] = {
        "freq_real_casa_empate_fora": [sum(1 for l in comuns if l["res"] == k) / n for k in range(3)],
        "freq_over_real": sum(1 for l in comuns if l["over"]) / n,
        "gols_reais_por_jogo": sum(meta[l["id"]]["hg"] + meta[l["id"]]["ag"] for l in comuns) / n,
        "chutes_reais_por_jogo": sum(meta[l["id"]]["hs"] + meta[l["id"]]["as"] for l in comuns) / n,
        "modelos": {m: {"p_casa_empate_fora_medio": [sum(l[m]["p1x2"][k] for l in comuns) / n for k in range(3)],
                        "p_over_medio": sum(l[m]["pover"] for l in comuns) / n,
                        **({"gols_por_jogo": sum(l[m]["gols"] for l in comuns) / n, "chutes_por_jogo": sum(l[m]["chutes"] for l in comuns) / n} if m in VARIANTES else {})}
                    for m in modelos},
    }
    json.dump({"resumo": resumo, "jogos": linhas}, open(a.saida, "w"), ensure_ascii=False)
    print(json.dumps(resumo, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
