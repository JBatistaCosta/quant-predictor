#!/usr/bin/env python3
"""Coleta o mapa de calor (posição dos toques) de cada jogador, por partida encerrada, em `match_player_heatmap_fotmob`.

Por que existe: base de posição para uma futura simulação de jogo por cadeia de Markov (fase 2 da granularidade de
jogador, CONTEXTO_PROJETO.md 03/10/2026). O FotMob serve, por partida, um SVG com um <circle cx cy> por ponto, em
coordenadas 105 x 68 (time sempre atacando rumo a x = 105); o nº de pontos acompanha os toques do jogador. Cada ponto traz só cx/cy/r (sem minuto), mas a lista de cada jogador vem em ORDEM CRONOLÓGICA (verificado em 03/10/2026); `pontos` guarda essa ordem. Só existe
para jogos de ~março/2026 em diante (antes: 404).

Fluxo por partida (2 chamadas, FotMob é grátis; pacing de 1,3 s como os demais ingestores):
  1. heatmap  -> {`p<optaId>`: "<circle cx=.. cy=../>..."}   (404 = indisponível: registrado e não repetido)
  2. matchDetails -> `content.playerStats`: ponte optaId -> id FotMob do jogador (a escalação só tem optaId em ~40%)
Linhas gravadas com a chave de serviço; `match_heatmap_coleta_fotmob` guarda o que já foi tentado, então rodar de novo
só pega partidas novas (indisponível recente, até 3 dias após o jogo, é retentado até 3 vezes).

Variáveis de ambiente: SUPABASE_URL, SUPABASE_KEY (service_role). Uso:
    python scripts/ingerir_heatmap_jogador.py --limite 200            # as 200 partidas mais recentes ainda não coletadas
    python scripts/ingerir_heatmap_jogador.py --desde 2026-03-01      # janela de início (padrão)
    python scripts/ingerir_heatmap_jogador.py --match-ids 14408,14409 # ids internos, mesmo já coletados
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"}
BASE = "https://www.fotmob.com/api/data"
PACING_SEGUNDOS = 1.3
DESDE_PADRAO = "2026-03-01"
MAX_TENTATIVAS_INDISPONIVEL = 3
DIAS_RETENTAR_INDISPONIVEL = 3
X_MAX, Y_MAX = 1050, 680          # décimos de metro (campo 105 x 68)

_CIRCLE = re.compile(r'<circle\s+cx="(-?[\d.]+)"\s+cy="(-?[\d.]+)"')


def url_heatmap(fotmob_match_id: str) -> str:
    return (f"{BASE}/heatmap/match/{fotmob_match_id}/heatmaps?heatmapUrl="
            f"https%3A%2F%2Fpub.fotmob.com%2Fprod%2Fdb%2Fapi%2Fheatmap%2Fmatch%2F{fotmob_match_id}")


def parse_pontos(svg: str) -> list[int]:
    """SVG de um jogador -> [x1, y1, x2, y2, ...] em décimos de metro (inteiros), limitados ao campo."""
    saida: list[int] = []
    for x, y in _CIRCLE.findall(svg or ""):
        saida.append(min(max(round(float(x) * 10), 0), X_MAX))
        saida.append(min(max(round(float(y) * 10), 0), Y_MAX))
    return saida


def mapa_opta_para_fotmob(match_details: dict) -> dict[str, str]:
    """`content.playerStats` do matchDetails -> {optaId: id FotMob do jogador} (strings)."""
    stats = ((match_details or {}).get("content") or {}).get("playerStats") or {}
    saida: dict[str, str] = {}
    for chave, p in stats.items():
        opta = p.get("optaId")
        if opta is not None:
            saida[str(opta)] = str(p.get("id") or chave)
    return saida


def montar_linhas(match_id: int, heatmap: dict, opta_para_fotmob: dict[str, str], lineup: dict[str, dict],
                  jogadores_por_fotmob: dict[str, int]) -> tuple[list[dict], int]:
    """Linhas de `match_player_heatmap_fotmob` e quantos jogadores ficaram sem ponte optaId -> FotMob.
    `lineup`: {fotmob_player_id: {"team_id", "player_id"}} da escalação da partida;
    `jogadores_por_fotmob`: {fotmob_player_id: players.id} (plano B para o player_id)."""
    modificado = heatmap.get("lastModified")
    try:
        modificado_iso = datetime.strptime(modificado, "%a, %d %b %Y %H:%M:%S GMT").replace(tzinfo=timezone.utc).isoformat() if modificado else None
    except ValueError:
        modificado_iso = None
    linhas, sem_ponte = [], 0
    for chave, svg in (heatmap.get("players") or {}).items():
        opta = chave[1:] if chave.startswith("p") else chave
        fotmob_id = opta_para_fotmob.get(opta)
        if not fotmob_id:
            sem_ponte += 1
            continue
        pontos = parse_pontos(svg)
        if not pontos:
            continue
        esc = lineup.get(fotmob_id) or {}
        linhas.append({
            "match_id": match_id, "team_id": esc.get("team_id"), "fotmob_player_id": fotmob_id,
            "player_id": esc.get("player_id") or jogadores_por_fotmob.get(fotmob_id), "opta_id": opta,
            "n_pontos": len(pontos) // 2, "pontos": pontos, "fonte_atualizada_em": modificado_iso,
        })
    return linhas, sem_ponte


def combinar_time_jogador(lineup_rows: list[dict], stats_rows: list[dict]) -> dict[str, dict]:
    """{fotmob_player_id: {"team_id", "player_id"}} da partida. A escalação (`match_lineup_fotmob`) manda; onde ela não tem o
    jogador, ou tem sem time/`player_id`, completa com as estatísticas do jogo (`match_player_stats_fotmob`). Achado
    03/10/2026: 5,9% dos jogadores do mapa de calor (133 partidas) não estavam na escalação guardada, e quase todos
    existiam nas estatísticas."""
    saida: dict[str, dict] = {}
    for fonte in (lineup_rows, stats_rows):
        for r in fonte:
            fid = r.get("fotmob_player_id")
            if not fid:
                continue
            atual = saida.setdefault(fid, {"team_id": None, "player_id": None})
            for campo in ("team_id", "player_id"):
                if atual[campo] is None and r.get(campo) is not None:
                    atual[campo] = r[campo]
    return saida


def deve_retentar(coleta: dict | None, match_date: datetime, agora: datetime) -> bool:
    """Partida sem registro entra; 'ok' nunca volta; 'indisponivel'/'erro' voltam até MAX_TENTATIVAS, só se o jogo é recente."""
    if coleta is None:
        return True
    if coleta["status"] == "ok":
        return False
    return coleta.get("tentativas", 1) < MAX_TENTATIVAS_INDISPONIVEL and agora - match_date <= timedelta(days=DIAS_RETENTAR_INDISPONIVEL)


# --------------------------------------------------------------------------- banco / rede (não coberto pelos testes unitários)
def _exec_retry(query, tentativas=6, espera_inicial=3, espera_max=60):
    for t in range(tentativas):
        try:
            return query.execute()
        except Exception as exc:  # erro transitório de rede/PostgREST
            if t == tentativas - 1:
                raise
            espera = min(espera_inicial * (2 ** t), espera_max)
            print(f"    Aviso: erro Supabase ({exc}), tentativa {t + 1}/{tentativas}, aguardando {espera}s...")
            time.sleep(espera)


def _paginar(supabase, tabela, select, filtros=None, order="id"):
    """Paginação explícita (PostgREST corta em 1000 linhas sem aviso)."""
    saida, pagina = [], 0
    while True:
        q = supabase.table(tabela).select(select).order(order).range(pagina * 1000, pagina * 1000 + 999)
        for metodo, col, val in (filtros or []):
            q = getattr(q, metodo)(col, val)
        bloco = _exec_retry(q).data
        saida.extend(bloco)
        if len(bloco) < 1000:
            return saida
        pagina += 1


def candidatas(supabase, desde: str, match_ids: list[int] | None, agora: datetime) -> list[dict]:
    """Partidas encerradas com id FotMob, da mais recente para a mais antiga, que ainda precisam de coleta."""
    if match_ids:
        partidas = [p for i in range(0, len(match_ids), 100)
                    for p in _exec_retry(supabase.table("matches").select("id,match_date").in_("id", match_ids[i:i + 100])).data]
        coletadas = {}
    else:
        partidas = _paginar(supabase, "matches", "id,match_date", [("eq", "status", "finished"), ("gte", "match_date", desde)])
        coletadas = {c["match_id"]: c for c in _paginar(supabase, "match_heatmap_coleta_fotmob", "match_id,status,tentativas", order="match_id")}
    fontes = {s["match_id"]: s["source_id"] for s in _paginar(supabase, "match_source_ids", "match_id,source_id", [("eq", "source", "fotmob")])}
    saida = []
    for p in partidas:
        if p["id"] not in fontes:
            continue
        data = datetime.fromisoformat(p["match_date"].replace("Z", "+00:00"))
        if match_ids or deve_retentar(coletadas.get(p["id"]), data, agora):
            saida.append({"match_id": p["id"], "fotmob_match_id": fontes[p["id"]], "match_date": data, "tentativas": (coletadas.get(p["id"]) or {}).get("tentativas", 0)})
    return sorted(saida, key=lambda c: c["match_date"], reverse=True)


def registrar(supabase, c: dict, status: str, n_jogadores: int | None = None) -> None:
    _exec_retry(supabase.table("match_heatmap_coleta_fotmob").upsert(
        {"match_id": c["match_id"], "status": status, "n_jogadores": n_jogadores, "tentativas": c["tentativas"] + 1,
         "coletado_em": datetime.now(timezone.utc).isoformat()}, on_conflict="match_id"))


def processar(supabase, requests, c: dict) -> str:
    resp = requests.get(url_heatmap(c["fotmob_match_id"]), headers=HEADERS, timeout=30)
    if resp.status_code == 404:
        registrar(supabase, c, "indisponivel")
        return "indisponivel"
    resp.raise_for_status()
    heatmap = resp.json()
    time.sleep(PACING_SEGUNDOS)
    det = requests.get(f"{BASE}/matchDetails?matchId={c['fotmob_match_id']}", headers=HEADERS, timeout=30)
    det.raise_for_status()
    ponte = mapa_opta_para_fotmob(det.json())
    lineup = combinar_time_jogador(
        _exec_retry(supabase.table("match_lineup_fotmob").select("fotmob_player_id,team_id,player_id").eq("match_id", c["match_id"])).data,
        _exec_retry(supabase.table("match_player_stats_fotmob").select("fotmob_player_id,team_id,player_id").eq("match_id", c["match_id"])).data)
    ids = sorted({f for f in ponte.values() if not lineup.get(f, {}).get("player_id")})
    jogadores = {}
    for i in range(0, len(ids), 100):
        jogadores.update({j["fotmob_player_id"]: j["id"] for j in _exec_retry(supabase.table("players").select("id,fotmob_player_id").in_("fotmob_player_id", ids[i:i + 100])).data})
    linhas, sem_ponte = montar_linhas(c["match_id"], heatmap, ponte, lineup, jogadores)
    if sem_ponte:
        print(f"    Aviso: {sem_ponte} jogador(es) do mapa sem optaId no matchDetails (ignorados)")
    if linhas:
        _exec_retry(supabase.table("match_player_heatmap_fotmob").upsert(linhas, on_conflict="match_id,fotmob_player_id"))
    registrar(supabase, c, "ok" if linhas else "indisponivel", len(linhas))
    return "ok" if linhas else "indisponivel"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limite", type=int, default=None, help="processa só as N partidas mais recentes pendentes")
    ap.add_argument("--desde", default=DESDE_PADRAO, help="data inicial (YYYY-MM-DD) das partidas candidatas")
    ap.add_argument("--match-ids", default=None, help="ids internos separados por vírgula (ignora o registro de coleta)")
    args = ap.parse_args()
    url, chave = (os.environ.get("SUPABASE_URL") or "").strip(), (os.environ.get("SUPABASE_KEY") or "").strip()
    if not url or not chave:
        sys.exit("Defina SUPABASE_URL e SUPABASE_KEY (service_role) como variáveis de ambiente.")
    import requests
    from supabase import create_client
    supabase = create_client(url, chave)
    ids = [int(x) for x in args.match_ids.split(",") if x.strip()] if args.match_ids else None
    pend = candidatas(supabase, args.desde, ids, datetime.now(timezone.utc))
    if args.limite:
        pend = pend[:args.limite]
    print(f"{len(pend)} partida(s) a coletar")
    contagem = {"ok": 0, "indisponivel": 0, "erro": 0}
    for n, c in enumerate(pend, 1):
        try:
            contagem[processar(supabase, requests, c)] += 1
        except Exception as exc:  # uma partida ruim não derruba a fila; fica 'erro' e volta na próxima execução
            print(f"  [{n}/{len(pend)}] match {c['match_id']}: erro {exc}")
            contagem["erro"] += 1
            try:
                registrar(supabase, c, "erro")
            except Exception:
                pass
        if n % 25 == 0:
            print(f"  {n}/{len(pend)} -- {contagem}")
        time.sleep(PACING_SEGUNDOS)
    print(f"Concluído: {contagem}")


if __name__ == "__main__":
    main()
