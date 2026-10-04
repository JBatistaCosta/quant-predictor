#!/usr/bin/env python3
"""Recalibra o simulador por competição e época e valida fora da amostra (Achado 41).

O simulador foi ajustado nas 4 ligas de 2015/16 e o Achado 35 mostrou que ritmo, estrutura da posse, bolas paradas e gols não se transferem para clubes de elite
recentes nem para torneios de seleções. Aqui cada peça medida é re-estimada dentro do conjunto (matriz de desfecho, núcleo da próxima linha, memória da posse, tempo morto
por reinício, duração das ações, folga entre ações, duração dos tempos, chutes), com ENCOLHIMENTO em direção à base de 2015/16: contagens do conjunto + k pseudo-contagens
da base. O k sai da verossimilhança fora da amostra da matriz de desfecho. Validação em 2 dobras (jogos pares x ímpares): calibra numa metade e compara os totais por jogo da
simulação com os observados na OUTRA metade, contra o simulador de 2015/16 sem calibrar.
Uso: python scripts/calibrar_simulador_por_competicao.py [--sims 2500] [--conjuntos ligas_recentes,torneios_selecoes,la_liga_2015_16]
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import multiprocessing as mp
import os
import pickle
import random
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analisar_chutes_e_progressao_statsbomb as ach  # noqa: E402
import analisar_reinicios_e_recuperacoes_statsbomb as rein  # noqa: E402
import analisar_residuo_entradas_statsbomb as r  # noqa: E402
import calibrar_memoria_posse as cm  # noqa: E402
import comparar_competicoes_statsbomb as c  # noqa: E402
import gerar_matriz_transicao_statsbomb as g  # noqa: E402
import metricas_posse_xt_momentum as M  # noqa: E402
import simulador_cadeia_bola as s  # noqa: E402
import validar_simulador_outras_competicoes as vo  # noqa: E402

PASTA = "dados_referencia/statsbomb"
COMPLETO = os.path.join(PASTA, "completo")
CACHE = "/tmp/calibracao_por_competicao_cache"
CONJUNTOS = {
    "ligas_recentes": [("ligas_recentes", None)],
    "torneios_selecoes": [("euro", None), ("copa_america", None), ("copa_mundo", ("2018", "2022"))],
    "la_liga_2015_16": [("ligas_2015_16", ("La Liga",))],
}
TIPOS_MORTO = ("From Throw In", "From Free Kick", "From Goal Kick", "From Corner", "From Kick Off")
GRADE_K = (0.0, 25.0, 100.0, 400.0, 1600.0, 1e9)
NORMA_JOGOS = 1200                                               # partidas por iteração da normalização da memória
EPOCAS = {          # conjunto -> [(época, nº de jogos)] na ordem de extração (rótulos ordenados); a soma tem de bater com o conjunto
    "torneios_selecoes": [("Euro 2020", 51), ("Euro 2024", 51), ("Copa América 2024", 32), ("Copa do Mundo 2018", 64), ("Copa do Mundo 2022", 64)],
    "ligas_recentes": [("Bundesliga 2023/24", 34), ("La Liga 2018/19", 34), ("La Liga 2019/20", 33), ("La Liga 2020/21", 35), ("Ligue 1 2021/22", 26), ("Ligue 1 2022/23", 32)],
}


# ---------------------------------------------------------------- extração por partida
def resumo_partida(ev: list[dict]) -> dict:
    """Tudo o que o conjunto precisa de UMA partida, em forma aditiva (somável entre partidas)."""
    obs = vo.partida_observada(ev)                                    # totais observados + linhas reais (equipe, zona, tipo, metade, xg, gol, segundos)
    linhas = r.linhas_com_janela(ev)
    nucleo = {k: dict(v) for k, v in r.nucleo_pos_perda_e_chute([x[:4] for x in linhas]).items()}
    cont = c.contagens(g.acoes_da_partida(ev))
    mortos = [(padrao, gap) for padrao, _, gap in rein.analisar_partida(ev)["mortos"]]
    dpa, dsh = [], []
    clock = [0.0, 0.0]
    for e in ev:
        p = e.get("period")
        if p not in (1, 2):
            continue
        ts = int(e["minute"]) * 60 + float(e["second"])
        dur = float(e.get("duration") or 0.0)
        clock[p - 1] = max(clock[p - 1], ts + dur)
        t = (e.get("type") or {}).get("name")
        if t in ("Pass", "Carry") and dur > 0:
            dpa.append(round(dur, 3))
        elif t == "Shot" and dur > 0:
            dsh.append(round(dur, 3))
    return {"m": obs["m"], "linhas": obs["linhas"], "nucleo": nucleo, "cont": cont, "mortos": mortos, "dpa": dpa, "dsh": dsh, "clock": clock,
            "chutes": ach.chutes_da_partida(ev), "n_linhas": len(linhas), "n_chutes_linhas": sum(1 for x in linhas if x[3] == "chute")}


def _resumo_de_registro(reg):
    return resumo_partida(reg["eventos"])


def extrair_conjunto(nome: str, cache: str = CACHE) -> list[dict]:
    """Resumos de todas as partidas do conjunto, em ordem; cacheia em disco (fora do Git)."""
    os.makedirs(cache, exist_ok=True)
    arq = os.path.join(cache, f"{nome}.pkl")
    if os.path.exists(arq):
        return pickle.load(open(arq, "rb"))
    eventos = []
    for grupo, filtro in CONJUNTOS[nome]:
        for rot in sorted(json.load(open(os.path.join(COMPLETO, grupo, d, "INDICE.json")))["rotulo"] for d in os.listdir(os.path.join(COMPLETO, grupo))):
            if filtro and not any(rot.startswith(f) or rot.endswith(f) for f in filtro):
                continue
            eventos += [reg["eventos"] for reg in c.carregar_completo(COMPLETO, grupo, rot)]
    with mp.Pool(4) as pool:
        res = pool.map(resumo_partida, eventos, chunksize=8)
    pickle.dump(res, open(arq, "wb"))
    return res


# ---------------------------------------------------------------- agregação e perfil
def agregar(resumos: list[dict]) -> dict:
    cont = [[0] * 20 for _ in range(18)]
    nucleo: dict = collections.defaultdict(collections.Counter)
    mortos = collections.defaultdict(list)
    dpa, dsh, chutes = [], [], []
    clocks = [[], []]
    linhas_total = chutes_total = 0
    for x in resumos:
        for o in range(18):
            for k in range(20):
                cont[o][k] += x["cont"][o][k]
        for k, v in x["nucleo"].items():
            nucleo[k].update(v)
        for padrao, gap in x["mortos"]:
            mortos[padrao].append(gap)
        dpa += x["dpa"]
        dsh += x["dsh"]
        chutes += x["chutes"]
        clocks[0].append(x["clock"][0])
        clocks[1].append(x["clock"][1] - 45 * 60)                     # o minuto do 2T é cumulativo: tira os 45 min do 1T
        linhas_total += x["n_linhas"]
        chutes_total += x["n_chutes_linhas"]
    return {"cont": cont, "nucleo": {k: dict(v) for k, v in nucleo.items()}, "mortos": dict(mortos), "dpa": dpa, "dsh": dsh, "chutes": chutes, "clocks": clocks,
            "n_linhas": linhas_total, "n_chutes_linhas": chutes_total, "jogos": len(resumos), "linhas_reais": [x["linhas"] for x in resumos]}


def mesclar_matriz(conj: list[list[float]], base: list[list[float]], k: float) -> list[list[float]]:
    """Contagens do conjunto + k pseudo-contagens com as probabilidades de linha da base."""
    out = []
    for lc, lb in zip(conj, base):
        tb = sum(lb) or 1.0
        out.append([a + k * b / tb for a, b in zip(lc, lb)])
    return out


def mesclar_nucleo(conj: dict, base: dict, k: float) -> dict:
    out = {}
    for chave in set(conj) | set(base):
        cc, bb = conj.get(chave, {}), base.get(chave, {})
        tb = sum(bb.values())
        out[chave] = {kk: cc.get(kk, 0) + (k * bb.get(kk, 0) / tb if tb else 0.0) for kk in set(cc) | set(bb)}
    return out


def logveross_matriz(treino: list[list[float]], teste: list[list[int]]) -> float:
    """Log-verossimilhança por ação da matriz de desfecho estimada em `treino` (alfa 0,5) sobre as contagens de `teste`."""
    tot = 0.0
    n = 0
    for lt, le in zip(treino, teste):
        den = sum(lt) + 0.5 * 20
        for a, b in zip(lt, le):
            if b:
                tot += b * math.log((a + 0.5) / den)
                n += b
    return tot / n


def quantis_dur(v: list[float]) -> dict:
    o = sorted(v)
    return {"mediana": o[len(o) // 2], "media": sum(o) / len(o)}


def perfil_de(agg: dict, base_cont: list, base_nucleo: dict, base_morto: dict, k: float, chutes_extra: list | None = None) -> dict:
    """Perfil (para `Parametros.aplicar_perfil`) do conjunto agregado, com encolhimento k em direção à base dada (a de 2015/16 ou o perfil de um conjunto maior).
    `chutes_extra` soma chutes de outra origem ao conjunto de chutes sorteáveis (a qualidade do chute pouco depende da época)."""
    morto = {}
    for tipo in TIPOS_MORTO + ("From Keeper",):
        v = agg["mortos"].get(tipo, [])
        morto[tipo] = quantis_dur(v) if len(v) >= 30 else base_morto[tipo]
    dpa, dsh = quantis_dur(agg["dpa"]), quantis_dur(agg["dsh"])
    clock = [sum(agg["clocks"][0]) / len(agg["clocks"][0]), sum(agg["clocks"][1]) / len(agg["clocks"][1])]
    # folga = tempo de relógio que sobra depois do tempo em ação e do tempo morto modelado, por linha (medida, não ajustada)
    jogos = agg["jogos"]
    em_acao = (agg["n_linhas"] - agg["n_chutes_linhas"]) * dpa["media"] + agg["n_chutes_linhas"] * dsh["media"]
    morto_total = sum(sum(agg["mortos"].get(t, [])) for t in TIPOS_MORTO)
    folga = (sum(clock) * jogos - em_acao - morto_total) / agg["n_linhas"]
    risco = M.calcular(agg["linhas_reais"])["risco_por_posicao_na_corrida"]
    return {"cont": mesclar_matriz(agg["cont"], base_cont, k), "nucleo": mesclar_nucleo(agg["nucleo"], base_nucleo, k),
            "risco_posicao": [[x["perda_obs_sobre_esp"], x["chute_obs_sobre_esp"]] for x in risco], "morto": morto,
            "dur_acao": dpa, "dur_chute": dsh, "folga": max(folga, 0.0), "duracao_tempo": clock, "chutes": agg["chutes"] + (chutes_extra or []), "fonte_xg": "statsbomb", "k": k}


# ---------------------------------------------------------------- simulação e métricas
def simular_perfil(perfil: dict | None, n: int, semente: int, normalizar: bool = True) -> dict:
    par = s.Parametros(PASTA)
    if perfil is not None:
        par.aplicar_perfil({k: v for k, v in perfil.items() if k != "k"})
        if normalizar:
            par.norma_memoria = [(1.0, 1.0)] * 18
            for it in range(2):
                med = s.simular(par, NORMA_JOGOS, semente + 100 + it)
                ef = cm.medias_efetivas(med)
                par.norma_memoria = [(a[0] * e[0], a[1] * e[1]) for a, e in zip(par.norma_memoria, ef)]
    rng = random.Random(semente)
    acc = {k: [] for k in vo.CHAVES}
    faixas = [0] * 6
    partidas = []
    for i in range(n):
        reg: list | None = [] if i < 800 else None
        res = s.simular_partida(par, rng, registro=reg)
        for k, ch in (("acoes", "acoes"), ("chutes", "chutes"), ("gols", "gols"), ("escanteios", "escanteios"), ("laterais", "laterais"), ("tiros_livres", "tiros livres"), ("tiros_de_meta", "tiros de meta")):
            acc[k].append(res.get(ch, 0))
        for b in range(6):
            faixas[b] += sum(v for kk, v in res.items() if kk.startswith("ent|") and kk.endswith(f"|{b}"))
        if reg is not None:
            partidas.append(reg)
    out = {k: sum(v) / n for k, v in acc.items()}
    out["faixa_pct"] = [100 * f / sum(faixas) for f in faixas]
    pos = M.calcular(partidas)
    out["posse"] = {k: pos[k] for k in ("corridas_por_jogo", "linhas_por_corrida_media", "duracao_corrida_media_s")}
    return out, par.norma_memoria


def observado(resumos: list[dict]) -> dict:
    out = {k: st.mean(x["m"][k] for x in resumos) for k in vo.CHAVES}
    faixas = [sum(x["m"]["faixa"][b] for x in resumos) for b in range(6)]
    out["faixa_pct"] = [100 * f / sum(faixas) for f in faixas]
    pos = M.calcular([x["linhas"] for x in resumos])
    out["posse"] = {k: pos[k] for k in ("corridas_por_jogo", "linhas_por_corrida_media", "duracao_corrida_media_s")}
    return out


def erro_relativo(sim: dict, obs: dict) -> dict:
    """Erro absoluto relativo (%) por medida e médio das 7 contagens por jogo, 3 de posse e 6 faixas do campo (as faixas em pontos percentuais)."""
    e = {k: 100 * abs(sim[k] / obs[k] - 1) for k in vo.CHAVES}
    e.update({f"posse_{k}": 100 * abs(sim["posse"][k] / obs["posse"][k] - 1) for k in sim["posse"]})
    e["faixas_pp"] = sum(abs(a - b) for a, b in zip(sim["faixa_pct"], obs["faixa_pct"])) / 6
    e["media_pct"] = sum(v for kk, v in e.items() if kk != "faixas_pp") / (len(e) - 1)
    return e


def validar_conjunto(nome: str, sims: int) -> dict:
    resumos = extrair_conjunto(nome)
    base = s.Parametros(PASTA)
    base_nucleo = json.load(open(os.path.join(PASTA, "residuo_entradas_ligas_2015_16.json")))["nucleo_pos_perda_e_chute"]
    base_morto = json.load(open(os.path.join(PASTA, "reinicios_e_recuperacoes_ligas_2015_16.json")))["tempo_morto_por_reinicio_s"]
    dobras = [([x for i, x in enumerate(resumos) if i % 2 == 0], [x for i, x in enumerate(resumos) if i % 2 == 1]),
              ([x for i, x in enumerate(resumos) if i % 2 == 1], [x for i, x in enumerate(resumos) if i % 2 == 0])]
    resultado = {"jogos": len(resumos), "dobras": []}
    for treino, teste in dobras:
        agg_tr, agg_te = agregar(treino), agregar(teste)
        ll = {k: logveross_matriz(mesclar_matriz(agg_tr["cont"], base.cont, k), agg_te["cont"]) for k in GRADE_K}
        k_bom = max(ll, key=ll.get)
        perfil = perfil_de(agg_tr, base.cont, base_nucleo, base_morto, k_bom)
        obs = observado(teste)
        sim_base, _ = simular_perfil(None, sims, 5)
        sim_cal, _ = simular_perfil(perfil, sims, 5)
        resultado["dobras"].append({"k": k_bom, "logveross_por_k": ll, "folga": perfil["folga"], "duracao_tempo": perfil["duracao_tempo"], "obs": obs,
                                    "sim_base": sim_base, "sim_calibrado": sim_cal, "erro_base": erro_relativo(sim_base, obs), "erro_calibrado": erro_relativo(sim_cal, obs)})
    # perfil final: o conjunto inteiro, com o k médio das dobras
    k_final = st.median(d["k"] for d in resultado["dobras"])
    perfil = perfil_de(agregar(resumos), base.cont, base_nucleo, base_morto, k_final)
    _, norma = simular_perfil(perfil, 50, 5)
    perfil["norma_memoria"] = norma
    resultado["perfil_final"] = {k: v for k, v in perfil.items() if k not in ("chutes",)}
    resultado["perfil_final"]["n_chutes"] = len(perfil["chutes"])
    return resultado, perfil


def validar_epocas(nome: str, sims: int) -> dict:
    """Para cada época do conjunto: (A) simulador de 2015/16, (B) perfil do conjunto SEM a época (leave-one-out), (C) B + calibração com metade dos jogos da época,
    cada um comparado com a OUTRA metade (2 dobras)."""
    resumos = extrair_conjunto(nome)
    epocas = EPOCAS[nome]
    assert sum(n for _, n in epocas) == len(resumos), (nome, sum(n for _, n in epocas), len(resumos))
    base = s.Parametros(PASTA)
    base_nucleo = json.load(open(os.path.join(PASTA, "residuo_entradas_ligas_2015_16.json")))["nucleo_pos_perda_e_chute"]
    base_morto = json.load(open(os.path.join(PASTA, "reinicios_e_recuperacoes_ligas_2015_16.json")))["tempo_morto_por_reinicio_s"]
    fatias, ini = {}, 0
    for rotulo, n in epocas:
        fatias[rotulo] = (ini, ini + n)
        ini += n
    saida = {}
    for rotulo, (a, b) in fatias.items():
        da_epoca = resumos[a:b]
        resto = resumos[:a] + resumos[b:]
        agg_resto = agregar(resto)
        k_resto = 1600.0
        perfil_b = perfil_de(agg_resto, base.cont, base_nucleo, base_morto, k_resto)
        sim_a, _ = simular_perfil(None, sims, 5)
        linhas = {"jogos": len(da_epoca), "dobras": []}
        for treino, teste in (([x for i, x in enumerate(da_epoca) if i % 2 == 0], [x for i, x in enumerate(da_epoca) if i % 2 == 1]),
                              ([x for i, x in enumerate(da_epoca) if i % 2 == 1], [x for i, x in enumerate(da_epoca) if i % 2 == 0])):
            agg_tr, agg_te = agregar(treino), agregar(teste)
            ll = {k: logveross_matriz(mesclar_matriz(agg_tr["cont"], perfil_b["cont"], k), agg_te["cont"]) for k in GRADE_K}
            k_bom = max(ll, key=ll.get)
            perfil_c = perfil_de(agg_tr, perfil_b["cont"], perfil_b["nucleo"], perfil_b["morto"], k_bom, chutes_extra=perfil_b["chutes"])
            obs = observado(teste)
            sim_b, _ = simular_perfil(perfil_b, sims, 5)
            sim_c, _ = simular_perfil(perfil_c, sims, 5)
            linhas["dobras"].append({"k": k_bom, "obs": obs, "erro_a": erro_relativo(sim_a, obs), "erro_b": erro_relativo(sim_b, obs), "erro_c": erro_relativo(sim_c, obs),
                                     "sim_a": sim_a, "sim_b": sim_b, "sim_c": sim_c})
        saida[rotulo] = linhas
        d = linhas["dobras"]
        print(f"{nome} | {rotulo} ({len(da_epoca)} jogos) | erro médio absoluto (média das 2 dobras): A 2015/16 {st.mean(x['erro_a']['media_pct'] for x in d):.1f}%  "
              f"B conjunto sem a época {st.mean(x['erro_b']['media_pct'] for x in d):.1f}%  C B+época {st.mean(x['erro_c']['media_pct'] for x in d):.1f}%  | k {[x['k'] for x in d]}", flush=True)
    return saida


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sims", type=int, default=2500)
    ap.add_argument("--conjuntos", default=",".join(CONJUNTOS))
    ap.add_argument("--saida", default=os.path.join(PASTA, "calibracao_por_competicao.json"))
    ap.add_argument("--epocas", action="store_true", help="valida época por época (leave-one-out dentro do conjunto)")
    a = ap.parse_args()
    if a.epocas:
        NORMA_JOGOS = 700
        tudo = {nome: validar_epocas(nome, a.sims) for nome in a.conjuntos.split(",")}
        json.dump(tudo, open(os.path.join(PASTA, "calibracao_por_epoca.json"), "w"), ensure_ascii=False, default=str)
        raise SystemExit(0)
    tudo = {}
    for nome in a.conjuntos.split(","):
        res, perfil = validar_conjunto(nome, a.sims)
        tudo[nome] = res
        print(f"\n== {nome} ({res['jogos']} jogos) ==", flush=True)
        for i, d in enumerate(res["dobras"]):
            print(f" dobra {i}: k={d['k']:g} folga={d['folga']:.3f}s tempos={d['duracao_tempo'][0] / 60:.1f}/{d['duracao_tempo'][1] / 60:.1f} min")
            print(f"   {'medida':16s} {'observado':>10s} {'sim base':>10s} {'sim calibrado':>14s}")
            for k in vo.CHAVES:
                print(f"   {k:16s} {d['obs'][k]:10.2f} {d['sim_base'][k]:10.2f} {d['sim_calibrado'][k]:14.2f}")
            for k in d["obs"]["posse"]:
                print(f"   {k:16s} {d['obs']['posse'][k]:10.2f} {d['sim_base']['posse'][k]:10.2f} {d['sim_calibrado']['posse'][k]:14.2f}")
            print(f"   erro médio absoluto: base {d['erro_base']['media_pct']:.1f}%  calibrado {d['erro_calibrado']['media_pct']:.1f}%  | faixas do campo (p.p.): base {d['erro_base']['faixas_pp']:.2f}  calibrado {d['erro_calibrado']['faixas_pp']:.2f}", flush=True)
        json.dump(perfil, open(os.path.join(PASTA, f"perfil_{nome}.json"), "w"), separators=(",", ":"))
    json.dump(tudo, open(a.saida, "w"), ensure_ascii=False, default=str)
