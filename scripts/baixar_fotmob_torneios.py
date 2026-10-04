#!/usr/bin/env python3
"""Baixa do FotMob (API interna www.fotmob.com/api/data/*) a Eurocopa e a Copa América de seleções, para cruzar com o StatsBomb (Achado 36).

Para cada temporada: `fixtures` (lista de jogos) e `matchDetails` de cada jogo encerrado (placar, eventos, estatísticas do jogo e de jogadores, escalações, mapa de
chutes com xG/xGOT, momentum). Grava fracionado e comprimido em `dados_referencia/fotmob/<torneio>/<temporada>/parte-NNN.json.xz` (+ INDICE.json), igual ao
StatsBomb, para o dado ficar versionado. NÃO escreve no banco (esse script não usa chave do Supabase); a carga no banco é a de `arquivos_do_claude/ingestao_fotmob.py`.
Pacing de 1,3 s por chamada (API não oficial: moderação para não arriscar bloqueio). Retoma de onde parou (cache por jogo na pasta temporária).
Uso: python scripts/baixar_fotmob_torneios.py [--torneios euro,copa_america] [--cache PASTA]
"""

from __future__ import annotations

import argparse
import json
import lzma
import os
import time

import requests

BASE = "https://www.fotmob.com/api/data"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"}
PACING_S = 1.3
TORNEIOS = {"euro": (50, ["2024", "2020", "2016", "2012"]), "copa_america": (44, ["2024", "2021", "2019", "2016", "2015"])}
CHAVES_DESCARTADAS = ("nav", "seo", "ongoing", "hasPendingVAR")
PARTIDAS_POR_ARQUIVO = 25


def get_json(caminho: str, params: dict, tentativas: int = 4):
    for t in range(tentativas):
        try:
            r = requests.get(f"{BASE}/{caminho}", params=params, headers=HEADERS, timeout=30)
            if r.status_code == 200:
                return r.json()
        except (requests.RequestException, ValueError):
            pass
        time.sleep(2 * (t + 1))
    return None


def enxugar(d: dict) -> dict:
    return {k: v for k, v in d.items() if k not in CHAVES_DESCARTADAS}


def baixar_temporada(liga: int, temporada: str, cache: str) -> list[dict]:
    fixtures = get_json("fixtures", {"id": liga, "season": temporada})
    if not isinstance(fixtures, list):
        raise SystemExit(f"fixtures {liga}/{temporada} sem resposta")
    time.sleep(PACING_S)
    registros = []
    for fx in sorted((f for f in fixtures if (f.get("status") or {}).get("finished")), key=lambda f: (f["status"]["utcTime"], int(f["id"]))):
        arq = os.path.join(cache, f"{liga}_{temporada}_{fx['id']}.json")
        if os.path.exists(arq):
            detalhe = json.load(open(arq))
        else:
            detalhe = get_json("matchDetails", {"matchId": fx["id"]})
            time.sleep(PACING_S)
            if detalhe is None:
                print(f"  FALHOU matchDetails {fx['id']}", flush=True)
                continue
            detalhe = enxugar(detalhe)
            json.dump(detalhe, open(arq, "w"), separators=(",", ":"))
        registros.append({"fotmob_match_id": int(fx["id"]), "fixture": {k: fx.get(k) for k in ("home", "away", "status", "tournament", "pageUrl")}, "matchDetails": detalhe})
    return registros


def salvar(pasta: str, torneio: str, temporada: str, registros: list[dict]) -> None:
    destino = os.path.join(pasta, torneio, temporada)
    os.makedirs(destino, exist_ok=True)
    partes = []
    for k in range(0, len(registros), PARTIDAS_POR_ARQUIVO):
        bloco = registros[k:k + PARTIDAS_POR_ARQUIVO]
        nome = f"parte-{k // PARTIDAS_POR_ARQUIVO + 1:03d}.json.xz"
        with lzma.open(os.path.join(destino, nome), "wt", preset=6) as f:
            json.dump(bloco, f, separators=(",", ":"))
        partes.append({"arquivo": nome, "fotmob_match_ids": [r["fotmob_match_id"] for r in bloco]})
    json.dump({"torneio": torneio, "temporada": temporada, "jogos": len(registros), "partes": partes, "chaves_descartadas": list(CHAVES_DESCARTADAS)},
              open(os.path.join(destino, "INDICE.json"), "w"), ensure_ascii=False, indent=1)


def carregar(pasta: str, torneio: str, temporada: str) -> list[dict]:
    """Registros de uma temporada, em ordem, lidos do disco (sem rede)."""
    destino = os.path.join(pasta, torneio, temporada)
    out = []
    for p in json.load(open(os.path.join(destino, "INDICE.json")))["partes"]:
        with lzma.open(os.path.join(destino, p["arquivo"]), "rt") as f:
            out += json.load(f)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--torneios", default="euro,copa_america")
    ap.add_argument("--pasta", default="dados_referencia/fotmob")
    ap.add_argument("--cache", default="/tmp/fotmob_torneios_cache")
    a = ap.parse_args()
    os.makedirs(a.cache, exist_ok=True)
    for torneio in a.torneios.split(","):
        liga, temporadas = TORNEIOS[torneio]
        for temp in temporadas:
            regs = baixar_temporada(liga, temp, a.cache)
            salvar(a.pasta, torneio, temp, regs)
            print(f"{torneio} {temp}: {len(regs)} jogos", flush=True)
