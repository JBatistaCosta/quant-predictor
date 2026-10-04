#!/usr/bin/env python3
"""Baixa competições do StatsBomb Open Data e compara a matriz de transição (18 zonas) e a origem dos escanteios com a da La Liga 2015/16.

Achado 26 de ACHADOS_COMPORTAMENTO.md. Reaproveita `gerar_matriz_transicao_statsbomb.py` (regra de zonas, definição de ação/perda) e a
atribuição do escanteio ao ÚLTIMO LANCE do time que atacava (`analisar_origem_escanteios_statsbomb.py`).

Grupos (nome -> [(competition_id, season_name)]), todos masculinos e gratuitos; a Copa Africana de Nações (1267) fica de fora de propósito:
    ligas_2015_16 : Premier League (2), Serie A (12), Ligue 1 (7), La Liga (11, só para conferir a baseline)
    copa_mundo    : FIFA World Cup (43), 8 edições (1958 a 2022)
    euro          : UEFA Euro (55), 2020 e 2024
    copa_america  : Copa America (223), 2024
    ligas_recentes: Bundesliga 2023/24 (so 1 time), Ligue 1 2021/22 e 2022/23 e La Liga 2018/19 a 2020/21 (parciais), Indian Super League 2021/22
                    -- servem para separar "efeito da epoca de marcacao" de "efeito de torneio de selecoes"

Uso (cada grupo grava `<cache>/<grupo>.json`; refazer não baixa de novo):
    python scripts/comparar_competicoes_statsbomb.py baixar    --cache /tmp/sb_comp [--grupos euro,copa_america]
    python scripts/comparar_competicoes_statsbomb.py comparar  --cache /tmp/sb_comp --baseline /tmp/sb/v2/acoes_v2.json
    python scripts/comparar_competicoes_statsbomb.py escanteios --cache /tmp/sb_comp     # mediana e variância dos escanteios por competição
    python scripts/comparar_competicoes_statsbomb.py resumir   --cache /tmp/sb_comp --saida dados_referencia/statsbomb
    python scripts/comparar_competicoes_statsbomb.py conferir  --saida dados_referencia/statsbomb   # refaz as contas SEM baixar nada

O StatsBomb Open Data pode mudar o que oferece de graça (jogos já foram e podem ser retirados), então o repositório guarda os DADOS REDUZIDOS de cada
partida, FRACIONADOS em arquivos `.json.xz` de até 50 partidas (`dados_referencia/statsbomb/brutos/<grupo>/<competição>/parte-NNN.json.xz`, ~16 KB por
partida): só os tipos de evento que as análises usam (passe, condução, chute, drible, desarme sofrido, erro de domínio, falta, escalação), em colunas, mais
a origem de cada escanteio já calculada. `reconstruir` refaz o cache e `resumir`/`comparar`/`escanteios` rodam a partir dele, SEM rede.
    python scripts/comparar_competicoes_statsbomb.py baixar      --cache /tmp/sb_comp --fracionado dados_referencia/statsbomb/brutos
    python scripts/comparar_competicoes_statsbomb.py reconstruir --cache /tmp/sb_comp --fracionado dados_referencia/statsbomb/brutos
Os resumos (matriz de contagens 18 x 20 etc.) continuam em `dados_referencia/statsbomb/<grupo>.json`.
O RESTANTE (todos os tipos de evento e todos os campos, mais escalações e metadados da partida) fica em `dados_referencia/statsbomb/completo/`:
    python scripts/comparar_competicoes_statsbomb.py completo --fracionado dados_referencia/statsbomb/completo [--grupos euro]
    # cada parte = até 25 partidas {match_id, partida, escalacoes, eventos}; só `id` e `related_events` (UUIDs que não comprimem) são removidos. O StatsBomb Open Data é público (citar a fonte ao usar).
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
    "ligas_2015_16": [(2, "2015/2016"), (12, "2015/2016"), (7, "2015/2016"), (11, "2015/2016")],
    "copa_mundo": [(43, s) for s in ("1958", "1962", "1970", "1974", "1986", "1990", "2018", "2022")],
    "euro": [(55, "2020"), (55, "2024")],
    "copa_america": [(223, "2024")],
    "ligas_recentes": [(9, "2023/2024"), (7, "2021/2022"), (7, "2022/2023"), (11, "2018/2019"), (11, "2019/2020"), (11, "2020/2021"), (1238, "2021/2022")],
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


def escanteios_por_time(eventos: list[dict]) -> list[int]:
    """Escanteios de cada um dos dois times da partida (inclui time com zero), na ordem do evento `Starting XI`."""
    ids = [e["team"]["id"] for e in eventos if e["type"]["name"] == "Starting XI"]
    cont = {t: 0 for t in ids}
    for e in eventos:
        if e["type"]["name"] == "Pass" and ((e["pass"].get("type") or {}).get("name")) == "Corner":
            cont[e["team"]["id"]] = cont.get(e["team"]["id"], 0) + 1
    return list(cont.values())


# ------------------------------------------------------------------------------------ dados reduzidos e fracionados
TIPOS_GUARDADOS = {"Pass", "Carry", "Shot", "Dribble", "Dispossessed", "Miscontrol", "Foul Committed", "Starting XI"}
CAMPOS = ("T", "team", "p", "m", "s", "d", "pos", "x", "y", "x2", "y2", "o", "f")
PARTIDAS_POR_ARQUIVO = 50


def reduzir_partida(ev: list[dict]) -> dict:
    """Evento do StatsBomb -> colunas só com o que as análises usam. `team` é o índice do time (0, 1, ...) na ordem em que aparece;
    `f` = bits (1 = cruzamento, 2 = escanteio cobrado); `o` = resultado do passe ou do chute. A origem de cada escanteio já vem calculada
    (janela de 25 eventos BRUTOS, que não existem mais depois da redução)."""
    cols = {k: [] for k in CAMPOS}
    times: dict[int, int] = {}
    for e in ev:
        t = e["type"]["name"]
        if t not in TIPOS_GUARDADOS:
            continue
        loc = e.get("location") or [None, None]
        fim = None
        out = None
        flags = 0
        if t == "Pass":
            pp = e["pass"]
            fim = pp.get("end_location")
            out = (pp.get("outcome") or {}).get("name")
            flags = (1 if pp.get("cross") else 0) | (2 if ((pp.get("type") or {}).get("name")) == "Corner" else 0)
        elif t == "Carry":
            fim = (e.get("carry") or {}).get("end_location")
        elif t == "Shot":
            out = (e["shot"].get("outcome") or {}).get("name")
        d = e.get("duration")
        linha = (t, times.setdefault(e["team"]["id"], len(times)), e.get("period"), e.get("minute"), e.get("second"),
                 None if d is None else round(d, 2), e.get("possession"), loc[0], loc[1],
                 fim[0] if fim else None, fim[1] if fim else None, out, flags)
        for k, v in zip(CAMPOS, linha):
            cols[k].append(v)
    return {"cols": cols, "escanteios": origem_escanteios(ev)}


def eventos_reconstruidos(red: dict) -> list[dict]:
    """Colunas reduzidas -> eventos com a MESMA forma que as funções de análise leem (`acoes_da_partida`, `escanteios_por_time`, tempo)."""
    c = red["cols"]
    saida = []
    for i in range(len(c["T"])):
        t = c["T"][i]
        e: dict = {"type": {"name": t}, "team": {"id": c["team"][i]}, "period": c["p"][i], "minute": c["m"][i], "second": c["s"][i]}
        if c["d"][i] is not None:
            e["duration"] = c["d"][i]
        if c["pos"][i] is not None:
            e["possession"] = c["pos"][i]
        if c["x"][i] is not None:
            e["location"] = [c["x"][i], c["y"][i]]
        fim = [c["x2"][i], c["y2"][i]] if c["x2"][i] is not None else None
        if t == "Pass":
            pp: dict = {}
            if fim:
                pp["end_location"] = fim
            if c["o"][i]:
                pp["outcome"] = {"name": c["o"][i]}
            if c["f"][i] & 1:
                pp["cross"] = True
            if c["f"][i] & 2:
                pp["type"] = {"name": "Corner"}
            e["pass"] = pp
        elif t == "Carry":
            e["carry"] = {"end_location": fim} if fim else {}
        elif t == "Shot":
            e["shot"] = {"outcome": {"name": c["o"][i]}} if c["o"][i] else {}
        saida.append(e)
    return saida


def slug(rotulo: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "_", rotulo.lower()).strip("_")


def salvar_fracionado(pasta: str, grupo: str, rotulo: str, registros: list[dict], por_arquivo: int = PARTIDAS_POR_ARQUIVO) -> None:
    """Grava as partidas de UMA competição-temporada em `<pasta>/<grupo>/<slug>/parte-NNN.json.xz` (até `por_arquivo` partidas por arquivo)
    mais um `INDICE.json` com os ids de cada parte. `registros` = [{'match_id', 'cols', 'escanteios'}] em ordem de match_id."""
    import lzma
    destino = os.path.join(pasta, grupo, slug(rotulo))
    os.makedirs(destino, exist_ok=True)
    partes = []
    for k in range(0, len(registros), por_arquivo):
        bloco = registros[k:k + por_arquivo]
        nome = f"parte-{k // por_arquivo + 1:03d}.json.xz"
        with lzma.open(os.path.join(destino, nome), "wt", preset=9 | lzma.PRESET_EXTREME) as f:
            json.dump(bloco, f, separators=(",", ":"))
        partes.append({"arquivo": nome, "match_ids": [r["match_id"] for r in bloco]})
    with open(os.path.join(destino, "INDICE.json"), "w") as f:
        json.dump({"rotulo": rotulo, "grupo": grupo, "jogos": len(registros), "partes": partes}, f, ensure_ascii=False, indent=1)


def carregar_fracionado(pasta: str, grupo: str) -> dict[str, list[dict]]:
    """{rótulo: [registros em ordem]} lidos de `<pasta>/<grupo>/*/parte-*.json.xz`. Sem rede."""
    import lzma
    base = os.path.join(pasta, grupo)
    saida = {}
    for sub in sorted(os.listdir(base)):
        d = os.path.join(base, sub)
        if not os.path.isdir(d):
            continue
        ind = json.load(open(os.path.join(d, "INDICE.json")))
        regs = []
        for parte in ind["partes"]:
            with lzma.open(os.path.join(d, parte["arquivo"]), "rt") as f:
                regs.extend(json.load(f))
        saida[ind["rotulo"]] = regs
    return saida


def registro_para_cache(regs: list[dict]) -> dict:
    """Registros reduzidos de uma competição -> o mesmo formato do cache de `baixar_grupo` (acoes, escanteios, corners_times, jogos)."""
    c = {"jogos": 0, "acoes": [], "escanteios": [], "corners_times": []}
    for r in regs:
        ev = eventos_reconstruidos(r)
        c["jogos"] += 1
        c["acoes"].extend(g.acoes_da_partida(ev))
        c["escanteios"].extend([tuple(x) for x in r["escanteios"]])
        c["corners_times"].append(escanteios_por_time(ev))
    return c


def reconstruir(pasta: str, cache: str, grupos: list[str]) -> None:
    os.makedirs(cache, exist_ok=True)
    for gr in grupos:
        res = {rotulo: registro_para_cache(regs) for rotulo, regs in carregar_fracionado(pasta, gr).items()}
        json.dump(res, open(os.path.join(cache, f"{gr}.json"), "w"))
        for k, v in res.items():
            print(f"  {k}: {v['jogos']} jogos, {len(v['acoes'])} acoes, {len(v['escanteios'])} escanteios")


# --------------------------------------------------------------------------------- dados COMPLETOS (o restante)
PARTIDAS_POR_ARQUIVO_COMPLETO = 25
CAMPOS_REMOVIDOS = ("id", "related_events")      # UUIDs: não comprimem e não são usados; `index` (ordem) fica


def limpar_evento(e: dict) -> dict:
    return {k: v for k, v in e.items() if k not in CAMPOS_REMOVIDOS}


def partidas_do_grupo(grupo: str) -> list[tuple[str, dict]]:
    """[(rótulo, entrada de matches/<competição>/<temporada>.json)] do grupo, em ordem de match_id. Competições excluídas nunca entram."""
    comps = g.baixar_json(f"{g.BASE}/competitions.json")
    saida = []
    for cid, temporada in GRUPOS[grupo]:
        if cid in EXCLUIDAS:
            continue
        for c in comps:
            if c["competition_id"] == cid and c["season_name"] == temporada and c.get("competition_gender") == "male":
                rotulo = f'{c["competition_name"]} {c["season_name"]}'
                for m in sorted(g.baixar_json(f"{g.BASE}/matches/{cid}/{c['season_id']}.json"), key=lambda x: x["match_id"]):
                    saida.append((rotulo, m))
    return saida


def baixar_completo(grupo: str, pasta: str, workers: int = 6) -> None:
    """Baixa eventos completos (sem `id`/`related_events`), escalações e metadados das partidas do grupo e grava em blocos de 25 partidas
    (`<pasta>/<grupo>/<slug>/parte-NNN.json.xz` + `INDICE.json`). Escreve bloco a bloco (não segura o grupo inteiro na memória) e pula a competição-
    temporada cujo `INDICE.json` já existe (retomada)."""
    import lzma
    por_rotulo: dict[str, list[dict]] = collections.defaultdict(list)
    for rotulo, m in partidas_do_grupo(grupo):
        por_rotulo[rotulo].append(m)

    def uma(m: dict) -> dict:
        mid = m["match_id"]
        return {"match_id": mid, "partida": m, "escalacoes": g.baixar_json(f"{g.BASE}/lineups/{mid}.json"),
                "eventos": [limpar_evento(e) for e in g.baixar_json(f"{g.BASE}/events/{mid}.json")]}

    for rotulo, partidas in por_rotulo.items():
        destino = os.path.join(pasta, grupo, slug(rotulo))
        if os.path.exists(os.path.join(destino, "INDICE.json")):
            print(f"{rotulo}: já existe, pulando")
            continue
        os.makedirs(destino, exist_ok=True)
        indice, bloco, n_parte = [], [], 0

        def grava():
            nonlocal bloco, n_parte
            if not bloco:
                return
            n_parte += 1
            nome = f"parte-{n_parte:03d}.json.xz"
            with lzma.open(os.path.join(destino, nome), "wt", preset=9 | lzma.PRESET_EXTREME) as f:
                json.dump(bloco, f, separators=(",", ":"), ensure_ascii=False)
            indice.append({"arquivo": nome, "match_ids": [r["match_id"] for r in bloco]})
            bloco = []
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for k, reg in enumerate(ex.map(uma, partidas), 1):
                bloco.append(reg)
                if len(bloco) >= PARTIDAS_POR_ARQUIVO_COMPLETO:
                    grava()
                if k % 100 == 0:
                    print(f"  {rotulo}: {k}/{len(partidas)}")
        grava()
        with open(os.path.join(destino, "INDICE.json"), "w") as f:
            json.dump({"rotulo": rotulo, "grupo": grupo, "jogos": len(partidas), "partes": indice,
                       "campos_removidos": list(CAMPOS_REMOVIDOS)}, f, ensure_ascii=False, indent=1)
        print(f"{rotulo}: {len(partidas)} jogos em {n_parte} arquivos")


def carregar_completo(pasta: str, grupo: str, rotulo: str):
    """Gera, em ordem, os registros {match_id, partida, escalacoes, eventos} de uma competição-temporada guardada por `baixar_completo` (sem rede)."""
    import lzma
    d = os.path.join(pasta, grupo, slug(rotulo))
    for parte in json.load(open(os.path.join(d, "INDICE.json")))["partes"]:
        with lzma.open(os.path.join(d, parte["arquivo"]), "rt") as f:
            yield from json.load(f)


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


def baixar_grupo(grupo: str, cache: str, workers: int = 6, fracionado: str | None = None) -> dict:
    caminho = os.path.join(cache, f"{grupo}.json")
    if os.path.exists(caminho):
        antigo = json.load(open(caminho))
        if all("corners_times" in c for c in antigo.values()) and fracionado is None:    # caches antigos sem a contagem por jogo são refeitos
            return antigo
    jogos = jogos_do_grupo(grupo)
    print(f"{grupo}: {len(jogos)} jogos")

    def um(par):
        mid, rotulo = par
        ev = g.baixar_json(f"{g.BASE}/events/{mid}.json")
        red = reduzir_partida(ev)
        red["match_id"] = mid
        return rotulo, g.acoes_da_partida(ev), origem_escanteios(ev), escanteios_por_time(ev), red

    por_comp: dict[str, dict] = collections.defaultdict(lambda: {"jogos": 0, "acoes": [], "escanteios": [], "corners_times": []})
    registros: dict[str, list[dict]] = collections.defaultdict(list)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, (rotulo, acoes, esc, por_time, red) in enumerate(ex.map(um, jogos), 1):
            registros[rotulo].append(red)
            c = por_comp[rotulo]
            c["jogos"] += 1
            c["acoes"].extend(acoes)
            c["escanteios"].extend(esc)
            c["corners_times"].append(por_time)
            if i % 100 == 0:
                print(f"  {i}/{len(jogos)}")
    res = dict(por_comp)
    os.makedirs(cache, exist_ok=True)
    json.dump(res, open(caminho, "w"))
    if fracionado:
        for rotulo, regs in registros.items():
            salvar_fracionado(fracionado, grupo, rotulo, sorted(regs, key=lambda r: r["match_id"]))
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


def contagens(acoes: list) -> list[list[int]]:
    """Matriz de contagens 18 (zona de origem) x 20 (0..17 zona de destino da continuação, 18 chute, 19 perda)."""
    m = [[0] * 20 for _ in range(18)]
    for o, sd in simbolos(acoes):
        m[o][sd] += 1
    return m


def resumir_competicao(c: dict, match_ids: list[int]) -> dict:
    """Resumo compacto de uma competição-temporada a partir do cache bruto (ver docstring do módulo)."""
    acoes = [tuple(a) for a in c["acoes"]]
    por_origem: dict[str, dict[str, int]] = {}
    for a in acoes:
        origem = a[5] if len(a) > 5 else "?"
        d = por_origem.setdefault(origem, {"continua": 0, "chute": 0, "perda": 0})
        d[a[0]] += 1
    esc_tipo: dict[str, list[int]] = {}
    for tipo, zona in c["escanteios"]:
        v = esc_tipo.setdefault(tipo, [0] * 19)         # 0..17 zona do último lance, 18 = sem zona (tipo '?')
        v[18 if zona is None else zona] += 1
    return {"jogos": c["jogos"], "match_ids": sorted(match_ids), "n_acoes": len(acoes), "contagens_18x20": contagens(acoes),
            "acoes_por_origem": por_origem, "escanteios_origem_por_zona": esc_tipo, "corners_times": c["corners_times"]}


def resumir(cache: str, saida: str, grupos: list[str]) -> None:
    os.makedirs(saida, exist_ok=True)
    for gr in grupos:
        bruto = json.load(open(os.path.join(cache, f"{gr}.json")))
        ids: dict[str, list[int]] = collections.defaultdict(list)
        for mid, rotulo in jogos_do_grupo(gr):
            ids[rotulo].append(mid)
        res = {rotulo: resumir_competicao(c, ids.get(rotulo, [])) for rotulo, c in bruto.items()}
        caminho = os.path.join(saida, f"{gr}.json")
        with open(caminho, "w") as f:
            json.dump(res, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        print(f"{caminho}: {os.path.getsize(caminho) / 1024:.0f} KB, {len(res)} competições-temporadas")


def conferir(pasta: str, baseline_rotulo: str = "La Liga 2015/2016") -> None:
    """Refaz, SÓ com os resumos versionados, as taxas, o ganho dentro da amostra contra a baseline e a distribuição dos escanteios.
    O ganho aqui é DENTRO da amostra (o modelo próprio vê os próprios dados): é um pouco maior que o da validação cruzada de `comparar`."""
    todos: dict[str, dict] = {}
    for nome in sorted(os.listdir(pasta)):
        if nome.endswith(".json"):
            todos.update(json.load(open(os.path.join(pasta, nome))))
    if baseline_rotulo not in todos:
        raise SystemExit(f"baseline {baseline_rotulo} ausente em {pasta}")

    def modelo_de(cont, alfa=0.5):
        return [[(v + alfa) / (sum(l) + 20 * alfa) for v in l] for l in cont]

    def ll(cont, m):
        n = sum(sum(l) for l in cont)
        return sum(cont[o][s] * math.log(m[o][s]) for o in range(18) for s in range(20) if cont[o][s]) / n
    mb = modelo_de(todos[baseline_rotulo]["contagens_18x20"])
    for rotulo, r in sorted(todos.items()):
        cont = r["contagens_18x20"]
        n = sum(sum(l) for l in cont)
        chute = sum(l[18] for l in cont) / n
        perda = sum(l[19] for l in cont) / n
        ganho = ll(cont, modelo_de(cont)) - ll(cont, mb)
        cj, ct = [sum(x) for x in r["corners_times"]], [y for x in r["corners_times"] for y in x]
        dj, dt = distribuicao(cj), distribuicao(ct)
        print(f"{rotulo:28s} jogos={r['jogos']:4d} acoes={n:7d} chute={chute:.4f} perda={perda:.4f} ganho(em amostra)={ganho:+.4f} | "
              + (f"escanteios/jogo media {dj['media']:.2f} mediana {dj['mediana']:.1f} var {dj['variancia']:.2f} var/media {dj['var_sobre_media']:.2f}" if dj.get("n", 0) > 1 else "sem contagem por jogo"))


def distribuicao(valores: list[float]) -> dict:
    """n, média, mediana, quartis, variância amostral e razão variância/média (1 = Poisson) de uma lista de contagens."""
    n = len(valores)
    if n < 2:
        return {"n": n}
    v = sorted(valores)
    media = sum(v) / n
    var = sum((x - media) ** 2 for x in v) / (n - 1)

    def q(p):
        k = (n - 1) * p
        lo = int(k)
        hi = min(lo + 1, n - 1)
        return v[lo] + (v[hi] - v[lo]) * (k - lo)
    return {"n": n, "media": media, "mediana": q(0.5), "p25": q(0.25), "p75": q(0.75), "variancia": var, "var_sobre_media": var / media if media else float("nan")}


def corners_por_competicao(cache: str, grupos: list[str]) -> dict[str, dict]:
    """{rótulo: {'jogo': distribuição do total do jogo, 'time': distribuição por time}} a partir do campo `corners_times`."""
    saida = {}
    for gr in grupos:
        for rotulo, c in json.load(open(os.path.join(cache, f"{gr}.json"))).items():
            ct = c.get("corners_times") or []
            saida[rotulo] = {"grupo": gr, "jogo": distribuicao([sum(x) for x in ct]), "time": distribuicao([y for x in ct for y in x])}
    return saida


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("acao", choices=["baixar", "comparar", "escanteios", "resumir", "conferir", "reconstruir", "completo"])
    ap.add_argument("--fracionado", default=None, help="pasta dos dados reduzidos fracionados (baixar grava; reconstruir lê)")
    ap.add_argument("--cache", default=None)
    ap.add_argument("--saida", default="dados_referencia/statsbomb", help="pasta dos resumos versionados (resumir/conferir)")
    ap.add_argument("--grupos", default=",".join(GRUPOS))
    ap.add_argument("--baseline", default=None, help="acoes_v2.json da La Liga 2015/16 (gerado por gerar_matriz_transicao_statsbomb.py)")
    args = ap.parse_args()
    grupos = [x for x in args.grupos.split(",") if x]
    if args.acao == "conferir":
        conferir(args.saida)
        return
    if args.acao == "completo":
        if not args.fracionado:
            raise SystemExit("--fracionado é obrigatório para completo")
        for gr in grupos:
            baixar_completo(gr, args.fracionado)
        return
    if args.cache is None:
        raise SystemExit("--cache é obrigatório para esta ação")
    if args.acao == "resumir":
        resumir(args.cache, args.saida, grupos)
        return
    if args.acao == "reconstruir":
        if not args.fracionado:
            raise SystemExit("--fracionado é obrigatório para reconstruir")
        reconstruir(args.fracionado, args.cache, grupos)
        return
    if args.acao == "baixar":
        for gr in grupos:
            r = baixar_grupo(gr, args.cache, fracionado=args.fracionado)
            for k, v in r.items():
                print(f"  {k}: {v['jogos']} jogos, {len(v['acoes'])} acoes, {len(v['escanteios'])} escanteios")
        return
    if args.acao == "escanteios":
        for rotulo, d in corners_por_competicao(args.cache, grupos).items():
            j, t = d["jogo"], d["time"]
            if j.get("n", 0) < 2:
                continue
            print(f"{rotulo:28s} jogos={j['n']:4d} | total do jogo: media {j['media']:.2f} mediana {j['mediana']:.1f} [p25 {j['p25']:.1f}, p75 {j['p75']:.1f}] "
                  f"variancia {j['variancia']:.2f} var/media {j['var_sobre_media']:.2f} | por time: media {t['media']:.2f} mediana {t['mediana']:.1f} variancia {t['variancia']:.2f} var/media {t['var_sobre_media']:.2f}")
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
