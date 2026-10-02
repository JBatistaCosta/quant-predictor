#!/usr/bin/env python3
"""Desfalques inferidos pela LISTA DO JOGO (titulares + reservas de `match_lineup_fotmob`).

Regular = jogador com >= `min_tit` titularidades nas `janela` partidas ANTERIORES do time. Desfalque =
regular que NÃO está na lista (~20 convocados) do jogo. Motivo desconhecido (lesão, suspensão, rodízio,
empréstimo, saída do clube). Resultado de 02/10/2026 (CONTEXTO_PROJETO.md): cada desvio-padrão a mais de
regulares fora da lista vale ~-16 pontos de Elo sobre o Elo por xG (t = -7,9; 19.576 jogos) e ~-11 sobre a
Pinnacle pré-fechamento (t = -4,0), mas ~-2 sobre a Pinnacle de FECHAMENTO (t = -0,6).

PONTO NO TEMPO: a definição de "regular" usa só partidas ANTERIORES ao jogo; a lista do PRÓPRIO jogo é a
informação nova -- conhecida só ~60-75 min antes do apito. Para jogo futuro sem lista publicada a feature
fica NaN (não confundir com "zero desfalques"). A variante retrospectiva `n_fora_ret` (exige o jogador
reaparecer depois) usa informação futura: serve só de teto de análise, NUNCA de feature.
"""

from __future__ import annotations

import pandas as pd

JANELA_REGULAR = 10          # partidas anteriores do time olhadas para definir "regular"
MIN_TITULARIDADES = 5        # titularidades nessas partidas para ser "regular"
HORIZONTE_RETORNO = 30       # jogos para o jogador reaparecer (variante retrospectiva)
MIN_REGULARES = 8            # abaixo disso o time não tem histórico suficiente: desfalque = NaN


def regulares_fora_por_partida(L: pd.DataFrame, TM: pd.DataFrame, janela: int = JANELA_REGULAR, min_tit: int = MIN_TITULARIDADES,
                               horizonte: int = HORIZONTE_RETORNO, retrospectivo: bool = True) -> pd.DataFrame:
    """Por (jogo, time): `n_reg` regulares (>= min_tit titularidades nas `janela` partidas anteriores do time),
    `n_fora` os que não estão na lista do jogo e `peso_fora` = soma de (titularidades/janela) dos ausentes.
    Com `retrospectivo=True` acrescenta `n_fora_ret` (ausentes que reaparecem em <= `horizonte` jogos --
    informação FUTURA, só para análise). `L`: colunas fotmob_player_id, team_id, tn (ordem do jogo do time),
    st (1 = titular); `TM`: team_id, tn, match_id."""
    keys = ["fotmob_player_id", "team_id"]
    st = L[L.st == 1][keys + ["tn"]]
    X = pd.concat([st.assign(tn_alvo=st.tn + off)[keys + ["tn_alvo"]] for off in range(1, janela + 1)])
    cnt = X.groupby(keys + ["tn_alvo"]).size().rename("n_tit").reset_index()
    reg = cnt[cnt.n_tit >= min_tit]
    lista = L[keys + ["tn"]].assign(na_lista=1).rename(columns={"tn": "tn_alvo"})
    reg = reg.merge(lista, on=keys + ["tn_alvo"], how="left")
    reg["fora"] = reg.na_lista.isna().astype(int)
    reg = reg.merge(TM[["team_id", "tn", "match_id"]].rename(columns={"tn": "tn_alvo"}), on=["team_id", "tn_alvo"])
    if retrospectivo:
        prox = L[keys + ["tn"]].rename(columns={"tn": "tn_vol"})
        reg = reg.merge(prox, on=keys, how="left")
        reg["vol"] = ((reg.tn_vol > reg.tn_alvo) & (reg.tn_vol <= reg.tn_alvo + horizonte)).astype(int)
        reg = reg.groupby(keys + ["tn_alvo", "match_id", "fora", "n_tit"], as_index=False).vol.max()
    agg = reg.groupby(["match_id", "team_id"]).agg(n_reg=("fora", "size"), n_fora=("fora", "sum")).reset_index()
    pes = reg[reg.fora == 1].assign(w=lambda d: d.n_tit / janela).groupby(["match_id", "team_id"]).w.sum().rename("peso_fora").reset_index()
    agg = agg.merge(pes, on=["match_id", "team_id"], how="left").fillna({"peso_fora": 0})
    if retrospectivo:
        ret = reg[(reg.fora == 1) & (reg.vol == 1)].groupby(["match_id", "team_id"]).size().rename("n_fora_ret").reset_index()
        agg = agg.merge(ret, on=["match_id", "team_id"], how="left").fillna({"n_fora_ret": 0})
    return agg


def desfalques_por_partida(lineup: pd.DataFrame, partidas: pd.DataFrame) -> pd.DataFrame:
    """Desfalques (SEM a variante retrospectiva) por (match_id, team_id) a partir da lista de cada jogo.

    `lineup`: match_id, team_id, fotmob_player_id, is_starter. `partidas`: `id` (ou `match_id`) e `match_date`
    -- a ordem dos jogos do time vem da data e só conta jogos presentes em `lineup`. Times com menos de
    `MIN_REGULARES` regulares (início do histórico) ficam com `n_fora`/`peso_fora` = NaN."""
    p = partidas.rename(columns={"id": "match_id"}) if "match_id" not in partidas else partidas
    p = p[["match_id", "match_date"]].drop_duplicates("match_id").assign(d=lambda x: pd.to_datetime(x["match_date"].astype(str).str[:10]))
    L = lineup.dropna(subset=["fotmob_player_id", "is_starter"]).drop_duplicates(["match_id", "team_id", "fotmob_player_id"]).merge(p[["match_id", "d"]], on="match_id")
    L["st"] = L.is_starter.astype(int)
    TM = L[["team_id", "match_id", "d"]].drop_duplicates().sort_values(["team_id", "d", "match_id"]).reset_index(drop=True)
    TM["tn"] = TM.groupby("team_id").cumcount()
    L = L.merge(TM, on=["team_id", "match_id", "d"])
    if L.empty:
        return pd.DataFrame(columns=["match_id", "team_id", "n_reg", "n_fora", "peso_fora"])
    agg = regulares_fora_por_partida(L, TM, retrospectivo=False)
    # partida com lista mas sem nenhum regular identificado (início do histórico): linha com n_reg = 0
    base = TM[["match_id", "team_id"]].merge(agg, on=["match_id", "team_id"], how="left").fillna({"n_reg": 0, "n_fora": 0, "peso_fora": 0})
    sem_hist = base.n_reg < MIN_REGULARES
    base.loc[sem_hist, ["n_fora", "peso_fora"]] = float("nan")
    return base
