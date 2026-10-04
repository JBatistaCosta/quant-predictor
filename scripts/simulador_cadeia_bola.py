#!/usr/bin/env python3
"""Simulador semi-Markov da bola (zonas x eventos x tempo) -- Achado 31, v3.

Junta as peças medidas nos Achados 18 a 31 (StatsBomb, 4 ligas de 2015/16, resumos versionados em `dados_referencia/statsbomb/`) numa cadeia de Markov
com tempo de permanência e a CONFERE contra os totais por jogo que já medimos. É um protótipo ESTÁTICO (sem janela do jogo, sem nível dos times, sem
substituições nem cartões) para achar quais peças falham; não é um modelo de previsão.

ESTADO: uma LINHA de ação = (equipe com a bola, zona de 18 no referencial dela, relógio em segundos). Um SORTEIO por linha:
  1. desfecho da ação a partir da zona: continua para a zona w (18 destinos), chute ou perda (matriz de contagens 18 x 20 das 4 ligas, Achados 18/19);
  2. duração da ação: lognormal (mediana 1,36 s, média 1,66 s; Achado 27); chute 0,67/0,91 s;
  3. chute: gol ou não pela faixa (Achado 30); depois do gol, saída de bola;
  4. PRÓXIMA LINHA pelo núcleo empírico (Achado 31, `residuo_entradas_ligas_2015_16.json`): dado de onde a linha começou e se foi continua/perda/chute,
     quem fica com a próxima linha (mesma equipe ou adversário), de que classe é (jogo corrido, lateral, tiro livre, tiro de meta, escanteio, saída de
     bola, pênalti) e em que zona começa. Substitui recuperação, reinício, escanteio, falta e lateral mantido que o v2 calibrava à mão;
  5. tempo morto lognormal por classe de bola parada (Achado 29).
Cada tempo tem 45,8 min (1T) e 48,3 min (2T) de relógio (até o último evento, Achado 27).

Uso:  python scripts/simulador_cadeia_bola.py [--jogos 2000] [--semente 1] [--pasta dados_referencia/statsbomb]
"""

from __future__ import annotations

import argparse
import bisect
import collections
import json
import math
import os
import random

import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zonas_polares as zp  # noqa: E402

FAIXAS = ["defesa", "meio baixo", "meio alto", "ataque fora baixo", "ataque fora alto", "grande área"]
RESULTADOS = ["Goal", "Saved", "Blocked", "Off T", "Wayward", "Post"]
DURACAO_TEMPO_S = (45.828 * 60, (93.283 - 45.0) * 60)        # 1T e 2T em segundos de relógio
ZONA_SAIDA = 4                                                # meio baixo, centro
CLASSE_PARA_REINICIO = {"lateral": "From Throw In", "tiro livre": "From Free Kick", "tiro de meta": "From Goal Kick", "escanteio": "From Corner",
                        "saída de bola": "From Kick Off", "pênalti": "From Free Kick"}
# Folga entre o fim de uma ação e o início da seguinte: a duração registrada pelo StatsBomb não cobre o intervalo todo (46,1 min em ação + 40,7 de tempo
# morto somam 86,8 dos ~94 min de relógio). CALIBRADA, não medida: 0,2 s por linha fecha os ~7 min que faltam.
FOLGA_ENTRE_ACOES_S = 0.2
POSICOES_CORRIDA = ((1, 1), (2, 2), (3, 3), (4, 5), (6, 8), (9, 14), (15, 10**9))      # posição da linha dentro da corrida (mesmos cortes de metricas_posse_xt_momentum)
CONTAGEM_DA_CLASSE = {"lateral": "laterais", "tiro livre": "tiros livres", "tiro de meta": "tiros de meta", "escanteio": "escanteios",
                      "saída de bola": "saídas de bola"}


def lognormal_de(mediana: float, media: float) -> tuple[float, float]:
    """(mu, sigma) da lognormal com a mediana e a média dadas; sigma = sqrt(2 ln(media/mediana))."""
    if mediana <= 0 or media <= mediana:
        return (math.log(max(media, 1e-6)), 0.0)
    return (math.log(mediana), math.sqrt(2 * math.log(media / mediana)))


class Amostrador:
    """Sorteio de um índice a partir de pesos (bisect na soma acumulada)."""

    def __init__(self, pesos):
        self.cum, s = [], 0.0
        for p in pesos:
            s += float(p)
            self.cum.append(s)
        if s <= 0:
            raise ValueError("pesos somam zero")
        self.total = s

    def sortear(self, rng) -> int:
        return min(bisect.bisect_left(self.cum, rng.random() * self.total), len(self.cum) - 1)


def amostrador_de_chaves(contagens: dict) -> tuple[list, Amostrador]:
    chaves = list(contagens)
    return chaves, Amostrador([contagens[k] for k in chaves])


class Parametros:
    def __init__(self, pasta: str = "dados_referencia/statsbomb", fonte_xg: str = "fotmob"):
        self.fonte_xg = fonte_xg                         # 'fotmob': xG e gol pela zona polar do chute (477 mil chutes); 'statsbomb': os do próprio chute sorteado
        lig = json.load(open(os.path.join(pasta, "ligas_2015_16.json")))
        rein = json.load(open(os.path.join(pasta, "reinicios_e_recuperacoes_ligas_2015_16.json")))
        chu = json.load(open(os.path.join(pasta, "chutes_laterais_escanteios_faltas_ligas_2015_16.json")))
        tmp = json.load(open(os.path.join(pasta, "tempo_la_liga_2015_16.json")))
        nuc = json.load(open(os.path.join(pasta, "residuo_entradas_ligas_2015_16.json")))
        self.jogos_ref = sum(r["jogos"] for r in lig.values())
        # 1. matriz de desfecho por zona (alfa = 0,5 como nos testes fora da amostra)
        cont = [[0] * 20 for _ in range(18)]
        for r in lig.values():
            for o in range(18):
                for s in range(20):
                    cont[o][s] += r["contagens_18x20"][o][s]
        self._definir_matriz(cont)
        self.acoes_por_jogo_ref = sum(r["n_acoes"] for r in lig.values()) / self.jogos_ref
        # 2. durações
        d = tmp["duracao_acoes_s"]
        self.dur_acao = lognormal_de(1.36, (d["Pass"]["media"] * d["Pass"]["n"] + d["Carry"]["media"] * d["Carry"]["n"]) / (d["Pass"]["n"] + d["Carry"]["n"]))
        self.dur_chute = lognormal_de(d["Shot"]["mediana"], d["Shot"]["media"])
        # 3. chute: gol ou não por faixa (defesa e meio baixo têm 2 e 42 chutes: usa a faixa "meio alto")
        cf = chu["chutes_por_faixa"]
        self.p_gol, self.xg_faixa = [], []
        for f in FAIXAS:
            c = cf[f] if cf[f]["n"] >= 200 else cf["meio alto"]
            self.p_gol.append(c["resultados_pct"]["Goal"] / 100.0)
            self.xg_faixa.append(c["xg_medio"])
        # 4. núcleo empírico: próxima linha depois de uma linha que começou na zona z
        self._definir_nucleo(nuc["nucleo_pos_perda_e_chute"])
        # 5. tempo morto lognormal por tipo de reinício (Achado 29); mediana ~0 -> exponencial
        morto = rein["tempo_morto_por_reinicio_s"]
        self.morto = {k: (lognormal_de(v["mediana"], v["media"]) if v["mediana"] > 0.5 else (None, v["media"])) for k, v in morto.items()}
        # 6. memória da posse (Achado 33): risco de perda/chute dado a zona, por posição da linha dentro da corrida do time (observado/esperado)
        arq = os.path.join(pasta, "posse_xt_momentum_observado_ligas_2015_16.json")
        risco = json.load(open(arq))["risco_por_posicao_na_corrida"] if os.path.exists(arq) else []
        self.risco_posicao = [(r["perda_obs_sobre_esp"], r["chute_obs_sobre_esp"]) for r in risco] or [(1.0, 1.0)] * len(POSICOES_CORRIDA)
        # 7. normalização da memória por zona (Achado 34): mantém a taxa de perda/chute de CADA zona igual à da matriz; a memória só redistribui entre posições
        arq = os.path.join(pasta, "memoria_normalizacao_ligas_2015_16.json")
        self.norma_memoria = [tuple(x) for x in json.load(open(arq))["norma"]] if os.path.exists(arq) else [(1.0, 1.0)] * 18
        # 8. chutes observados por zona (xG, gol, distância, ângulo, cabeça, tipo): o chute simulado é UM chute real sorteado da zona (Achado 34)
        arq = os.path.join(pasta, "chutes_e_progressao_ligas_2015_16.json")
        self.pool_chute, self.pool_penalti = None, []
        if os.path.exists(arq):
            self._definir_pool(json.load(open(arq))["chutes"])
        self.folga = FOLGA_ENTRE_ACOES_S
        self.duracao_tempo = DURACAO_TEMPO_S
        # alvos observados por jogo
        j = chu["jogos"]
        n_chutes = sum(c["n"] for c in cf.values())
        self.alvos = {
            "ações": self.acoes_por_jogo_ref, "chutes": n_chutes / j,
            "gols": sum(c["n"] * c["resultados_pct"]["Goal"] / 100 for c in cf.values()) / j,
            "xG": sum(c["n"] * c["xg_medio"] for c in cf.values()) / j,
            "escanteios": chu["escanteios"]["n"] / j, "tiros livres": rein["reinicios_por_jogo"]["From Free Kick"],
            "laterais": rein["reinicios_por_jogo"]["From Throw In"], "tiros de meta": rein["reinicios_por_jogo"]["From Goal Kick"],
            "saídas de bola": rein["reinicios_por_jogo"]["From Kick Off"],
            "tempo morto (min)": sum(rein["tempo_morto_por_jogo_min"][k] for k in ("From Throw In", "From Free Kick", "From Goal Kick", "From Corner", "From Keeper", "From Kick Off")),
        }


    def _definir_matriz(self, cont) -> None:
        """Matriz de desfecho 18 x 20 (zona -> 18 destinos, chute, perda): probabilidades (alfa = 0,5) e sorteio do destino de quem continua."""
        self.cont = cont
        self.acoes_por_zona = [sum(l) for l in cont]
        self.p_chute_zona, self.p_perda_zona, self.destino_continua = [], [], []
        for l in cont:
            pesos = [v + 0.5 for v in l]
            tot = sum(pesos)
            self.p_chute_zona.append(pesos[18] / tot)
            self.p_perda_zona.append(pesos[19] / tot)
            self.destino_continua.append(Amostrador(pesos[:18]))

    def _definir_nucleo(self, nucleo: dict) -> None:
        """Núcleo empírico da próxima linha (chaves 'continua|z', 'perda|z', 'chute|z' -> {'quem|classe|zona': n})."""

        def da_zona(tipo: str, z: int) -> dict:
            """Contagens da zona; zonas com menos de 30 linhas (chute na defesa, por exemplo) usam o agregado de todo o campo."""
            # a saída de bola só vem de gol (tratado à parte) ou do começo de um tempo; nos dados ela aparece também depois da última linha do 1T, que é artefato
            c = {k: v for k, v in nucleo.get(f"{tipo}|{z}", {}).items() if "|saída de bola|" not in k}
            if sum(c.values()) >= 30:
                return c
            agg: collections.Counter = collections.Counter()
            for k, v in nucleo.items():
                if k.startswith(tipo + "|"):
                    agg.update({kk: vv for kk, vv in v.items() if "|saída de bola|" not in kk})
            return dict(agg)

        self.apos_perda, self.apos_chute, self.p_quebra, self.apos_quebra = [], [], [], []
        for z in range(18):
            self.apos_perda.append(amostrador_de_chaves(da_zona("perda", z)))
            self.apos_chute.append(amostrador_de_chaves(da_zona("chute", z)))
            cz = da_zona("continua", z)
            total = sum(cz.values())
            quebra = {k: v for k, v in cz.items() if not k.startswith("mesma|recuperação|")}
            self.p_quebra.append(sum(quebra.values()) / total if total else 0.0)
            self.apos_quebra.append(amostrador_de_chaves(quebra) if quebra else None)

    def _definir_pool(self, chutes: list) -> None:
        """Chutes reais por zona de 18 ([zona, xg, gol, tipo, cabeça, distância, ângulo]); pênalti (tipo 2) em conjunto à parte."""
        originais = [[] for _ in range(18)]
        self.pool_penalti = []
        for z, xg, gol, tipo, cab, dist, ang in chutes:
            if tipo == 2:
                self.pool_penalti.append((xg, gol, dist, ang, cab, tipo, zp.indice(dist, ang)))
            else:
                originais[int(z)].append((xg, gol, dist, ang, cab, tipo, zp.indice(dist, ang)))
        self.pool_chute = []
        for z in range(18):
            pool = originais[z]
            if len(pool) < 30:                           # zonas quase sem chute: usa a faixa; se ainda faltar, os chutes de fora da área
                pool = [r for zz in range(z // 3 * 3, z // 3 * 3 + 3) for r in originais[zz]]
                if len(pool) < 30:
                    pool = [r for zz in range(9, 12) for r in originais[zz]]
            self.pool_chute.append(pool)

    def aplicar_perfil(self, perfil: dict) -> "Parametros":
        """Troca as peças medidas em 2015/16 pelas de uma competição/época (Achado 41). Chaves de `perfil` (todas opcionais):
        cont (18 x 20), nucleo, risco_posicao [[perda, chute] x 7], norma_memoria [[perda, chute] x 18], morto {tipo: {mediana, media}},
        dur_acao / dur_chute {mediana, media}, folga (s), duracao_tempo [s do 1T, s do 2T], chutes (lista como em `_definir_pool`), fonte_xg."""
        if "cont" in perfil:
            self._definir_matriz(perfil["cont"])
        if "nucleo" in perfil:
            self._definir_nucleo(perfil["nucleo"])
        if "risco_posicao" in perfil:
            self.risco_posicao = [tuple(x) for x in perfil["risco_posicao"]]
        if "norma_memoria" in perfil:
            self.norma_memoria = [tuple(x) for x in perfil["norma_memoria"]]
        if "morto" in perfil:
            self.morto = {k: (lognormal_de(v["mediana"], v["media"]) if v["mediana"] > 0.5 else (None, v["media"])) for k, v in perfil["morto"].items()}
        if "dur_acao" in perfil:
            self.dur_acao = lognormal_de(perfil["dur_acao"]["mediana"], perfil["dur_acao"]["media"])
        if "dur_chute" in perfil:
            self.dur_chute = lognormal_de(perfil["dur_chute"]["mediana"], perfil["dur_chute"]["media"])
        if "folga" in perfil:
            self.folga = perfil["folga"]
        if "duracao_tempo" in perfil:
            self.duracao_tempo = tuple(perfil["duracao_tempo"])
        if "chutes" in perfil:
            self._definir_pool(perfil["chutes"])
        if "fonte_xg" in perfil:
            self.fonte_xg = perfil["fonte_xg"]
        return self


class Multiplicadores:
    """Força de um time (razões encolhidas do Achado 32); 1,0 = time médio."""

    def __init__(self, ataque_chute=1.0, ataque_perda=1.0, defesa_chute=1.0, defesa_perda=1.0):
        self.ataque_chute, self.ataque_perda, self.defesa_chute, self.defesa_perda = ataque_chute, ataque_perda, defesa_chute, defesa_perda

    @classmethod
    def de_dict(cls, d: dict, gama_chute: float = 1.0, gama_perda: float = 1.0) -> "Multiplicadores":
        """Os multiplicadores medidos elevados a um expoente (1 = como medido; <1 amortece): ver Achado 32 sobre por que a perda precisa de amortecimento."""
        return cls(d["ataque_chute"] ** gama_chute, d["ataque_perda"] ** gama_perda, d["defesa_chute"] ** gama_chute, d["defesa_perda"] ** gama_perda)


NEUTRO = Multiplicadores()
JANELA_NEUTRA = [{"chute": 1.0, "perda": 1.0, "quebra": 1.0}] * 6


def balde_da_posicao(posicao: int) -> int:
    for i, (lo, hi) in enumerate(POSICOES_CORRIDA):
        if lo <= posicao <= hi:
            return i
    return len(POSICOES_CORRIDA) - 1


def band(z: int) -> int:
    return z // 3


def lognorm(rng, par) -> float:
    mu, sigma = par
    return rng.lognormvariate(mu, sigma) if sigma > 0 else math.exp(mu)


def dead(rng, p: Parametros, padrao: str) -> float:
    par = p.morto[padrao]
    if par[0] is None:                                # mediana ~0: exponencial com a média
        return rng.expovariate(1.0 / max(par[1], 1e-6))
    return lognorm(rng, par)


def proxima_linha(p: Parametros, rng: random.Random, amostra: tuple[list, Amostrador]) -> tuple[str, str, int]:
    chaves, am = amostra
    quem, classe, zona = chaves[am.sortear(rng)].split("|")
    return quem, classe, int(zona)


def simular_partida(p: Parametros, rng: random.Random, times: tuple = (NEUTRO, NEUTRO), janela_mult: list = JANELA_NEUTRA, registro: list | None = None, memoria: bool = True) -> dict:
    """Uma partida entre `times[0]` (começa o 1T) e `times[1]`. Devolve contagens (ações, chutes, gols, bolas paradas, tempo morto, posses, chutes por
    janela de 15 min, e por equipe: chutes_0/1, gols_0/1, escanteios_0/1) e a ocupação por classe/faixa.
    Se `registro` for uma lista, cada linha de ação é anexada como (equipe, zona, tipo, metade 0/1, xG, gol, segundos desde o início da metade)."""
    m: collections.Counter = collections.Counter()
    janela = [0] * 6                                     # chutes por janela de 15 min de relógio (0-15, 15-30, 30-45+, 45-60, 60-75, 75-90+)

    for tempo, duracao in enumerate(p.duracao_tempo):
        t = 0.0
        equipe = tempo % 2
        zona = ZONA_SAIDA
        classe = "jogo"
        posicao = 0                                      # posição da linha dentro da corrida (posse) do time
        m["posses"] += 1
        while t < duracao:
            posicao += 1
            w = tempo * 3 + min(int(t // 900), 2)
            jm = janela_mult[w]
            atk, dfs = times[equipe], times[1 - equipe]
            m[f"ent|{classe}|{band(zona)}"] += 1
            t_inicio, tipo_linha, xg_linha, gol_linha = t, "continua", 0.0, 0
            t += p.folga
            mem_perda, mem_chute = p.risco_posicao[balde_da_posicao(posicao)] if memoria else (1.0, 1.0)
            if memoria:
                mem_perda, mem_chute = mem_perda / p.norma_memoria[zona][0], mem_chute / p.norma_memoria[zona][1]
                m[f"mem|{zona}|n"] += 1
                m[f"mem|{zona}|p"] += mem_perda
                m[f"mem|{zona}|c"] += mem_chute
            ps = min(p.p_chute_zona[zona] * atk.ataque_chute * dfs.defesa_chute * jm["chute"] * mem_chute, 0.5)
            pl = min(p.p_perda_zona[zona] * atk.ataque_perda * dfs.defesa_perda * jm["perda"] * mem_perda, 0.95 - ps)
            penalti = classe == "pênalti" and p.pool_penalti
            if penalti:
                ps, pl = 1.0, 0.0                          # a linha de pênalti é o próprio chute
            u = rng.random()
            m["acoes"] += 1
            m[f"acoes_{equipe}"] += 1
            chutes_antes = m["chutes"]
            if u < ps:                                   # chute
                t += lognorm(rng, p.dur_chute)
                b = band(zona)
                m["chutes"] += 1
                m[f"chutes_{equipe}"] += 1
                tipo_linha = "chute"
                if p.pool_chute is not None:                # um chute real sorteado da zona: xG e gol vêm juntos
                    rec = rng.choice(p.pool_penalti) if penalti else rng.choice(p.pool_chute[zona])
                    if p.fonte_xg == "fotmob" and not penalti:
                        _, _, xg_pz, gol_pz = zp.FOTMOB[rec[6]]
                        xg_linha, gol_linha = xg_pz, 1 if rng.random() < gol_pz else 0
                    else:
                        xg_linha, gol_linha = rec[0], rec[1]
                else:
                    xg_linha, gol_linha = p.xg_faixa[b], 1 if rng.random() < p.p_gol[b] else 0
                m["xG"] += xg_linha
                if gol_linha:
                    m["gols"] += 1
                    m[f"gols_{equipe}"] += 1
                    quem, classe, nova = "adv", "saída de bola", ZONA_SAIDA
                else:
                    quem, classe, nova = proxima_linha(p, rng, p.apos_chute[zona])
            else:
                d = lognorm(rng, p.dur_acao)
                t += d
                m["tempo_em_acao"] += d
                if u < ps + pl:                          # perda
                    tipo_linha = "perda"
                    quem, classe, nova = proxima_linha(p, rng, p.apos_perda[zona])
                elif p.apos_quebra[zona] is not None and rng.random() < min(p.p_quebra[zona] * jm["quebra"], 0.95):   # a bola parou (falta, lateral, ...)
                    quem, classe, nova = proxima_linha(p, rng, p.apos_quebra[zona])
                else:                                    # a bola segue com a mesma equipe
                    quem, classe, nova = "mesma", "continua", p.destino_continua[zona].sortear(rng)
            janela[w] += m["chutes"] - chutes_antes
            if registro is not None:
                registro.append((equipe, zona, tipo_linha, tempo, xg_linha, gol_linha, t_inicio))
            if quem == "adv":
                m["posses"] += 1
            if classe in CLASSE_PARA_REINICIO:
                m[CONTAGEM_DA_CLASSE.get(classe, "tiros livres")] += 1
                if classe == "tiro livre":
                    m["faltas"] += 1
                tm = dead(rng, p, CLASSE_PARA_REINICIO[classe])
                t += tm
                m["tempo morto"] += tm
            if quem == "adv":
                equipe = 1 - equipe
                posicao = 0
            if classe == "escanteio":                    # o escanteio é de quem fica com a próxima linha
                m[f"escanteios_{equipe}"] += 1
            zona = nova
    return dict(m, janela_chutes=janela)


def simular(p: Parametros, n_jogos: int, semente: int, memoria: bool = True) -> dict:
    rng = random.Random(semente)
    tot: collections.Counter = collections.Counter()
    for _ in range(n_jogos):
        r = simular_partida(p, rng, memoria=memoria)
        for k, v in r.items():
            if k != "janela_chutes":
                tot[k] += v
        for w, v in enumerate(r["janela_chutes"]):
            tot[f"janela_{w}"] += v
    return {k: v / n_jogos for k, v in tot.items()}


def relatorio(med: dict, p: Parametros) -> None:
    sim = {"ações": med.get("acoes", 0), "chutes": med.get("chutes", 0), "gols": med.get("gols", 0), "xG": med.get("xG", 0), "escanteios": med.get("escanteios", 0),
           "tiros livres": med.get("tiros livres", 0), "laterais": med.get("laterais", 0), "tiros de meta": med.get("tiros de meta", 0),
           "saídas de bola": med.get("saídas de bola", 0), "tempo morto (min)": med.get("tempo morto", 0) / 60.0}
    print(f"{'por jogo':22s} {'simulado':>10s} {'observado':>10s} {'sim/obs':>8s}")
    for k, obs in p.alvos.items():
        s = sim.get(k, 0.0)
        print(f"{k:22s} {s:10.2f} {obs:10.2f} {s / obs if obs else float('nan'):8.2f}")
    print(f"{'tempo em ação (min)':22s} {med.get('tempo_em_acao', 0) / 60:10.1f} {'46,1':>10s}")
    print(f"{'faltas':22s} {med.get('faltas', 0):10.2f} {30.0:10.2f} {med.get('faltas', 0) / 30.0:8.2f}")
    print(f"{'posses':22s} {med.get('posses', 0):10.1f} {195.2:10.1f}")
    print("chutes por janela de 15 min:", " ".join(f"{med.get(f'janela_{w}', 0):.2f}" for w in range(6)))
    ocup = [sum(v for k, v in med.items() if k.startswith("ent|") and k.endswith(f"|{b}")) for b in range(6)]
    obs = [sum(p.acoes_por_zona[b * 3:b * 3 + 3]) for b in range(6)]
    print("ocupação por faixa (% das ações), simulado/observado:", " ".join(f"{100 * o / sum(ocup):.1f}/{100 * x / sum(obs):.1f}" for o, x in zip(ocup, obs)))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jogos", type=int, default=2000)
    ap.add_argument("--semente", type=int, default=1)
    ap.add_argument("--pasta", default="dados_referencia/statsbomb")
    ap.add_argument("--fonte-xg", choices=["fotmob", "statsbomb"], default="fotmob", help="de onde vem o xG/gol do chute simulado (zona polar do FotMob ou o chute real do StatsBomb)")
    ap.add_argument("--perfil", help="JSON de perfil por competição/época (ex.: dados_referencia/statsbomb/perfil_torneios_selecoes.json, Achado 41); sem ele, usa as 4 ligas de 2015/16")
    ap.add_argument("--sem-memoria", action="store_true", help="desliga a memória da posse (Achado 33): risco de perda/chute só pela zona")
    a = ap.parse_args()
    par = Parametros(a.pasta, a.fonte_xg)
    if a.perfil:
        par.aplicar_perfil({k: v for k, v in json.load(open(a.perfil)).items() if k != "k"})
        med = simular(par, a.jogos, a.semente, memoria=not a.sem_memoria)
        print(f"Perfil {a.perfil} ({a.jogos} jogos simulados); por jogo:")
        for k, v in (("ações", "acoes"), ("chutes", "chutes"), ("gols", "gols"), ("escanteios", "escanteios"), ("laterais", "laterais"), ("tiros livres", "tiros livres"), ("tiros de meta", "tiros de meta")):
            print(f"  {k:16s} {med.get(v, 0):9.2f}")
        print(f"  tempo morto (min) {med.get('tempo morto', 0) / 60:8.1f}")
    else:
        relatorio(simular(par, a.jogos, a.semente, memoria=not a.sem_memoria), par)
