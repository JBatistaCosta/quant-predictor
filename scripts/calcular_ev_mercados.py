#!/usr/bin/env python3
"""Registro de apostas hipotéticas de EV (1X2) do modelo Elo por xG contra a Pinnacle.

Para cada partida com odds 1X2 da Pinnacle no snapshot `pre_closing` (entrada), calcula
    EV = p_modelo * odd_entrada - 1
nas três seleções (casa/empate/fora) e registra como aposta hipotética toda seleção com
EV >= 0 (stake flat de 1 unidade + stake de Kelly fracionário, fator 0,25).

p_modelo vem do logit ordenado de `elo_xg_tres_vias.py` sobre o Elo por xG PRÉ-JOGO
(`team_elo_xg_history.rating_antes`) e é WALK-FORWARD: os 3 parâmetros (a, h, t) são
reajustados em cada período usando SÓ partidas terminadas ANTES do início do período
(primeiro período = `--inicio-apostas`, depois um por ano-calendário). Nenhum jogo do
período apostado participa do ajuste.

Odd de fechamento: snapshot `closing` da Pinnacle, desvigada (normalização proporcional
das 3 probabilidades implícitas). CLV = odd_entrada / odd_fechamento_justa - 1, só existe
quando a partida tem os DOIS snapshots.

LOOKAHEAD -- o que é e o que NÃO é verificável: a odd de entrada só pode vir do snapshot
`pre_closing`; o `closing` nunca é usado como entrada. Mas em cargas históricas
(football-data.co.uk etc.) `captured_at` é a data da IMPORTAÇÃO, não a da cotação, então o
caráter pré-jogo repousa no rótulo do snapshot. A coluna `entrada_verificada_por_timestamp`
marca as apostas em que `captured_at < horário do jogo` (verificação real); as demais
dependem do rótulo da fonte.

Sanity gate: `anomalia_dados = TRUE` (e `motivo_anomalia`) quando EV > +35%, overround
da entrada fora de [1,00; 1,12], ou qualquer odd <= 1,01. O auditor
(`arquivos_do_claude/analisar_calibracao_ev.py`) exclui anomalias por padrão.

Não grava no banco por padrão (só CSV); `--gravar` faz upsert em `ev_apostas_hipoteticas`
(migration 20261002100000, exige SUPABASE_SERVICE_ROLE_KEY).

Uso:
    python scripts/calcular_ev_mercados.py --saida ev_apostas_elo_xg.csv
Variáveis de ambiente: SUPABASE_URL, SUPABASE_KEY (leitura); SUPABASE_SERVICE_ROLE_KEY só com --gravar.
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import elo_xg_tres_vias as modelo

MODELO_NOME = "elo_xg_logit_ordenado"
MERCADO = "1X2"
SELECOES = ("home", "draw", "away")  # mesma ordem de modelo.CASA/EMPATE/FORA
BOOKMAKER = "pinnacle"
LIMITE_EV_ANOMALIA = 0.35
OVERROUND_MIN, OVERROUND_MAX = 1.00, 1.12
ODD_MINIMA = 1.01
FATOR_KELLY = 0.25
TREINO_INICIO = "2017-01-01"
INICIO_APOSTAS_PADRAO = "2022-08-01"


# --------------------------------------------------------------------------- funções puras
def desvigar(odds) -> np.ndarray:
    """Probabilidades justas por normalização proporcional das implícitas (1/odd)."""
    q = 1.0 / np.asarray(odds, dtype=float)
    return q / q.sum(axis=-1, keepdims=True)


def calcular_ev(prob_modelo, odd_entrada):
    return np.asarray(prob_modelo, dtype=float) * np.asarray(odd_entrada, dtype=float) - 1.0


def stake_kelly_fracionario(prob_modelo, odd, fator: float = FATOR_KELLY):
    """Fração da banca: fator * (p*odd - 1) / (odd - 1); 0 quando o EV é <= 0."""
    p, o = np.asarray(prob_modelo, dtype=float), np.asarray(odd, dtype=float)
    return fator * np.clip(p * o - 1.0, 0.0, None) / (o - 1.0)


def motivos_anomalia(ev: float, odds_entrada, ev_limite: float = LIMITE_EV_ANOMALIA) -> str:
    """'' se a aposta passa no sanity gate; senão os motivos separados por ';'."""
    motivos = []
    o = np.asarray(odds_entrada, dtype=float)
    if ev > ev_limite:
        motivos.append(f"ev>{ev_limite:.0%}")
    if (o <= ODD_MINIMA).any():
        motivos.append("odd<=1.01")
    else:
        overround = float((1.0 / o).sum())
        if not OVERROUND_MIN <= overround <= OVERROUND_MAX:
            motivos.append(f"overround={overround:.3f}")
    return ";".join(motivos)


def probabilidades_walk_forward(partidas: pd.DataFrame, inicio_apostas: str, treino_inicio: str = TREINO_INICIO):
    """P(casa/empate/fora) de cada partida do período apostado, com parâmetros ajustados
    só em partidas ANTERIORES ao início do período a que a partida pertence.

    `partidas`: colunas d (str AAAA-MM-DD), rh, ra, neu, y, ordenadas por data.
    Períodos: [inicio_apostas, 1º/jan seguinte), depois um por ano-calendário.
    Devolve (índices das linhas apostáveis, matriz (n,3), lista de dicts de parâmetros)."""
    d = partidas["d"].astype(str)
    cortes = [inicio_apostas]
    ano = int(inicio_apostas[:4]) + 1
    while f"{ano}-01-01" <= d.max():
        cortes.append(f"{ano}-01-01")
        ano += 1
    idx, blocos, historico = [], [], []
    for i, ini in enumerate(cortes):
        fim = cortes[i + 1] if i + 1 < len(cortes) else "9999-12-31"
        tr = partidas[(d >= treino_inicio) & (d < ini)]
        te = partidas[(d >= ini) & (d < fim)]
        if te.empty:
            continue
        if len(tr) < 1000:
            raise ValueError(f"treino com {len(tr)} partidas antes de {ini}: insuficiente para ajustar o modelo")
        params, perda = modelo.ajustar(tr.rh, tr.ra, tr.neu, tr.y)
        historico.append({"periodo_inicio": ini, "n_treino": int(len(tr)), "perda_treino": perda, **params})
        blocos.append(modelo.probabilidades_1x2(te.rh, te.ra, te.neu, params))
        idx.append(te.index.to_numpy())
    if not idx:
        return np.array([], dtype=int), np.empty((0, 3)), historico
    return np.concatenate(idx), np.vstack(blocos), historico


def montar_apostas(jogos: pd.DataFrame, probs: np.ndarray, odds_entrada: np.ndarray,
                   odds_fechamento: np.ndarray, entrada_verificada: np.ndarray) -> pd.DataFrame:
    """Uma linha por (partida, seleção) com EV >= 0.

    jogos: partida_id, liga_codigo, temporada, y (0/1/2). probs/odds_*: (n,3) na ordem
    casa/empate/fora; odds_fechamento com NaN quando não há closing."""
    linhas = []
    for i in range(len(jogos)):
        o_in = odds_entrada[i]
        if np.isnan(o_in).any() or (o_in <= 1.0).any():
            continue
        o_fc = odds_fechamento[i]
        tem_fech = not np.isnan(o_fc).any() and (o_fc > 1.0).all()
        justa_fech = 1.0 / desvigar(o_fc) if tem_fech else np.full(3, np.nan)
        ev = calcular_ev(probs[i], o_in)
        for k, sel in enumerate(SELECOES):
            if ev[k] < 0:
                continue
            ganhou = int(jogos.y.iloc[i] == k)
            motivo = motivos_anomalia(float(ev[k]), o_in)
            linhas.append({
                "partida_id": int(jogos.partida_id.iloc[i]),
                "liga_codigo": jogos.liga_codigo.iloc[i],
                "temporada": jogos.temporada.iloc[i],
                "data_jogo": jogos.d.iloc[i],
                "modelo": MODELO_NOME,
                "mercado": MERCADO,
                "selecao": sel,
                "prob_modelo": float(probs[i, k]),
                "odd_entrada": float(o_in[k]),
                "odd_fechamento_pinnacle": float(o_fc[k]) if tem_fech else np.nan,
                "odd_fechamento_justa": float(justa_fech[k]),
                "ev_estimado": float(ev[k]),
                "resultado_real": ganhou,
                "retorno_unidade": float(o_in[k] - 1.0) if ganhou else -1.0,
                "stake_flat": 1.0,
                "stake_kelly": float(stake_kelly_fracionario(probs[i, k], o_in[k])),
                "clv": float(o_in[k] / justa_fech[k] - 1.0) if tem_fech else np.nan,
                "anomalia_dados": bool(motivo),
                "motivo_anomalia": motivo,
                "entrada_verificada_por_timestamp": bool(entrada_verificada[i]),
            })
    return pd.DataFrame(linhas)


# --------------------------------------------------------------------------- carga do banco
def _paginar(montar_query, order="id"):
    resultado, pagina = [], 0
    while True:
        bloco = montar_query().order(order).range(pagina * 1000, pagina * 1000 + 999).execute().data
        resultado.extend(bloco)
        if len(bloco) < 1000:
            return resultado
        pagina += 1


def _carregar_odds(supabase, match_ids: set[int]) -> dict[tuple[int, str], dict[str, tuple[float, str]]]:
    """(partida, snapshot) -> {seleção: (odd, captured_at)}; havendo repetição, vale a de maior id."""
    # Por lote de partidas: o filtro único por bookmaker/mercado em toda a tabela estoura o
    # statement_timeout do PostgREST (8 s); `match_id in (...)` usa o índice e fica rápido.
    ids = sorted(match_ids)
    linhas = []
    for ini in range(0, len(ids), 40):
        lote = ids[ini:ini + 40]
        linhas += _paginar(lambda: supabase.table("odds_market")
                           .select("id, match_id, selection, odds, snapshot, captured_at")
                           .in_("match_id", lote).eq("bookmaker", BOOKMAKER).eq("market", MERCADO)
                           .in_("snapshot", ["pre_closing", "closing"]))
    odds: dict = {}
    for r in linhas:
        if r["match_id"] in match_ids and r["odds"]:
            odds.setdefault((r["match_id"], r["snapshot"]), {})[r["selection"]] = (float(r["odds"]), r["captured_at"])
    return odds


def carregar_dados(supabase):
    import pandas as pd

    partidas = pd.DataFrame(_paginar(lambda: supabase.table("matches")
                                     .select("id, match_date, league_id, season, home_team_id, away_team_id, home_goals, away_goals, is_neutral")
                                     .eq("status", "finished")))
    ligas = {l["id"]: (l.get("external_id") or f"liga_{l['id']}")
             for l in _paginar(lambda: supabase.table("leagues").select("id, external_id"))}
    elo = pd.DataFrame(_paginar(lambda: supabase.table("team_elo_xg_history").select("match_id, team_id, rating_antes")))
    m = partidas.dropna(subset=["home_goals", "away_goals"]).copy()
    m = m.merge(elo.rename(columns={"match_id": "id", "team_id": "home_team_id", "rating_antes": "rh"}), on=["id", "home_team_id"])
    m = m.merge(elo.rename(columns={"match_id": "id", "team_id": "away_team_id", "rating_antes": "ra"}), on=["id", "away_team_id"])
    m["d"] = pd.to_datetime(m["match_date"]).dt.strftime("%Y-%m-%d")
    m["neu"] = m["is_neutral"].fillna(False).astype(float)
    m["y"] = np.where(m.home_goals > m.away_goals, modelo.CASA, np.where(m.home_goals == m.away_goals, modelo.EMPATE, modelo.FORA))
    m["partida_id"] = m["id"]
    m["liga_codigo"] = m["league_id"].map(ligas)
    m["temporada"] = m["season"].astype(str)
    return m.sort_values(["d", "id"]).reset_index(drop=True)


def _matriz_odds(odds, partida_ids, snapshot):
    """(n,3) de odds e (n,) de 'captured_at' mais recente; NaN onde faltar seleção."""
    mat = np.full((len(partida_ids), 3), np.nan)
    captado = [None] * len(partida_ids)
    for i, pid in enumerate(partida_ids):
        sel = odds.get((int(pid), snapshot))
        if not sel or any(s not in sel for s in SELECOES):
            continue
        mat[i] = [sel[s][0] for s in SELECOES]
        captado[i] = max(sel[s][1] for s in SELECOES)
    return mat, captado


def gerar_registro(supabase, inicio_apostas: str) -> tuple[pd.DataFrame, list[dict]]:
    m = carregar_dados(supabase)
    idx, probs, historico = probabilidades_walk_forward(m, inicio_apostas)
    jogos = m.loc[idx].reset_index(drop=True)
    odds = _carregar_odds(supabase, set(int(i) for i in jogos.partida_id))
    o_in, capt_in = _matriz_odds(odds, jogos.partida_id, "pre_closing")
    o_fc, _ = _matriz_odds(odds, jogos.partida_id, "closing")
    kickoff = pd.to_datetime(jogos["match_date"], utc=True)
    verificada = np.array([c is not None and pd.Timestamp(c) < k for c, k in zip(capt_in, kickoff)])
    return montar_apostas(jogos, probs, o_in, o_fc, verificada), historico


def gravar(df: pd.DataFrame) -> None:
    from supabase import create_client

    url, chave = os.environ.get("SUPABASE_URL", "").strip(), os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not chave:
        sys.exit("--gravar exige SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY.")
    cliente = create_client(url, chave)
    registros = df.drop(columns=["data_jogo"]).replace({np.nan: None}).to_dict("records")
    for ini in range(0, len(registros), 500):
        cliente.table("ev_apostas_hipoteticas").upsert(
            registros[ini:ini + 500], on_conflict="partida_id,mercado,selecao,modelo").execute()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--inicio-apostas", default=INICIO_APOSTAS_PADRAO)
    ap.add_argument("--saida", default="ev_apostas_elo_xg.csv")
    ap.add_argument("--gravar", action="store_true", help="também faz upsert em ev_apostas_hipoteticas")
    args = ap.parse_args()

    from supabase import create_client

    url, chave = os.environ.get("SUPABASE_URL", "").strip(), os.environ.get("SUPABASE_KEY", "").strip()
    if not url or not chave:
        sys.exit("Defina SUPABASE_URL e SUPABASE_KEY como variáveis de ambiente.")
    df, historico = gerar_registro(create_client(url, chave), args.inicio_apostas)
    df.to_csv(args.saida, index=False)
    print("Parâmetros walk-forward:")
    for h in historico:
        print(f"  a partir de {h['periodo_inicio']}: a={h['a']:.4f} h={h['h']:.2f} t={h['t']:.4f} (treino n={h['n_treino']})")
    ok = df[~df.anomalia_dados]
    print(f"\n{len(df)} apostas com EV>=0 ({df.partida_id.nunique()} partidas); "
          f"{int(df.anomalia_dados.sum())} marcadas como anomalia (EV>+35% ou odds suspeitas); "
          f"{len(ok)} válidas; {int(ok.clv.notna().sum())} com CLV; "
          f"{int(df.entrada_verificada_por_timestamp.sum())} com entrada verificada por timestamp.")
    print(f"CSV: {args.saida}")
    if args.gravar:
        gravar(df)
        print("Upsert em ev_apostas_hipoteticas concluído.")


if __name__ == "__main__":
    main()
