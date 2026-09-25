#!/usr/bin/env python3
"""Cansaço e desgaste logístico: assimetria de descanso, distância de viagem e
carga de minutos do XI titular, controlados por força.

Reformulação pedida pelo usuário (25/09) depois do teste simples de descanso
(validar_informacao_nova_lambda.py), cujo sinal no treino tinha direção
estranha (time com ≤3 dias de descanso marcava MAIS) — suspeita de confusão por
força de equipe (quem joga no meio da semana é time de copa europeia).

Features, por time e partida:
  1. ΔDescanso = dias de descanso próprios − do adversário (todas as partidas do
     banco, qualquer competição), discretizado: vantagem ampla (≥+3), neutro
     (−2..+2, referência), desvantagem ampla (≤−3).
  2. Carga logística = ln(1 + distância_km) / dias de descanso (mín. 1, teto 14).
     Distância = da "base" do clube (mediana das coordenadas do estádio nos
     jogos em casa, match_context_fotmob) até o estádio da partida; ~0 para o
     mandante em casa. Entra a carga própria e a do adversário.
  3. Carga do XI titular = média, nos 11 titulares confirmados
     (match_lineup_fotmob), dos minutos jogados nos 14 dias ANTERIORES (em
     unidades de 90 min; match_player_stats_fotmob, todas as competições do banco).

Modelo: GLM de Poisson com offset = ln λ_base, onde λ_base é o λ calibrado com
os titulares confirmados (a melhor base de validar_informacao_nova_lambda.py),
coeficientes ajustados só no treino. "Controle rigoroso de força": além do
offset, a variante controlada inclui ln(λ_base/λ_base_adv) como covariável
livre, pra absorver qualquer resto de força correlacionado com calendário.
Heterogeneidade: carga logística separada por grupo de competição (distâncias
longas: Brasileirão A/B, Libertadores, Sul-Americana, Copa do Brasil, MLS;
compactas: Premier, Championship, Bundesliga, Eredivisie; demais).
Fora da amostra: Δ perda em gols por time, 1X2 e O/U 2.5 (IC95% bootstrap) e
recorte nos jogos extremos (|ΔDescanso| ≥ 3 ou distância > 2.000 km).

Uso:
    python arquivos_do_claude/validar_desgaste_logistico.py --entrada painel.pkl --cache-dir /tmp/info

Variáveis de ambiente: SUPABASE_URL, SUPABASE_KEY (chave pública basta). Só leitura.
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from validar_assistencia_jogador_walkforward import Rest, obter_env  # noqa: E402
from validar_totais_vs_mercado import _cache  # noqa: E402
import validar_informacao_nova_lambda as vin  # noqa: E402

LIGAS_DISTANCIA_LONGA = {1, 34, 23, 46, 28, 29}   # Brasileirão A/B, Libertadores, Sul-Americana, Copa do Brasil, MLS
LIGAS_COMPACTAS = {4, 24, 13, 25}                 # Premier, Championship, Bundesliga, Eredivisie
LIMIAR_DESCANSO = 3
LIMIAR_DISTANCIA_KM = 2000
JANELA_CARGA_DIAS = 14


# =============================================================================
# Carregamento
# =============================================================================
def baixar_coordenadas(rest: Rest) -> pd.DataFrame:
    """match_context_fotmob não tem `id`: pagina por match_id."""
    linhas, cursor = [], -1
    while True:
        pagina = rest.get("match_context_fotmob", {
            "select": "match_id,stadium_lat,stadium_long", "match_id": f"gt.{cursor}",
            "stadium_lat": "not.is.null", "order": "match_id", "limit": 1000})
        linhas += pagina
        if len(pagina) < 1000:
            return pd.DataFrame(linhas)
        cursor = pagina[-1]["match_id"]


def carregar_extras(cache_dir):
    rest = None

    def cliente():
        nonlocal rest
        rest = rest or Rest(obter_env("SUPABASE_URL"), obter_env("SUPABASE_KEY"))
        return rest

    coords = _cache(cache_dir, "coordenadas_estadio", lambda: baixar_coordenadas(cliente()))
    minutos = _cache(cache_dir, "minutos_jogador", lambda: cliente().baixar(
        "match_player_stats_fotmob", "id,match_id,team_id,player_id,minutes_played", {"minutes_played": "gt.0"}, 24))
    return coords, minutos


# =============================================================================
# Features
# =============================================================================
def haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * np.arcsin(np.sqrt(a))


def distancias(jogos: pd.DataFrame, coords: pd.DataFrame) -> pd.DataFrame:
    """Distância (km) da base de cada time até o estádio da partida."""
    c = coords.astype({"stadium_lat": float, "stadium_long": float})
    j = jogos.merge(c, on="match_id")
    base = j.groupby("home_team_id")[["stadium_lat", "stadium_long"]].median().rename_axis("team_id")
    linhas = []
    for col in ("home_team_id", "away_team_id"):
        x = j[["match_id", col, "stadium_lat", "stadium_long"]].rename(columns={col: "team_id"}).merge(
            base.rename(columns={"stadium_lat": "blat", "stadium_long": "blon"}).reset_index(), on="team_id")
        x["dist_km"] = haversine_km(x["blat"], x["blon"], x["stadium_lat"], x["stadium_long"])
        linhas.append(x[["match_id", "team_id", "dist_km"]])
    return pd.concat(linhas)


def carga_xi(minutos: pd.DataFrame, jogos: pd.DataFrame, titulares: pd.DataFrame) -> pd.DataFrame:
    """Média, nos titulares confirmados, dos minutos jogados nos 14 dias
    ANTERIORES à partida (em jogos de 90 min). Soma acumulada por jogador +
    busca binária pelas bordas da janela [dt−14d, dt)."""
    hist = (minutos.merge(jogos[["match_id", "dt"]], on="match_id")
            .drop_duplicates(["player_id", "match_id"]).sort_values(["player_id", "dt"]))
    t = titulares.merge(jogos[["match_id", "dt"]], on="match_id").copy()
    t["min14"] = 0.0
    janela = np.timedelta64(JANELA_CARGA_DIAS, "D")
    grupos_t = t.groupby("player_id").indices
    for pid, g in hist.groupby("player_id"):
        idx_t = grupos_t.get(pid)
        if idx_t is None:
            continue
        d = g["dt"].values
        acum = np.concatenate([[0.0], np.cumsum(g["minutes_played"].values.astype(float))])
        alvo = t["dt"].values[idx_t]
        fim = np.searchsorted(d, alvo, side="left")            # partidas estritamente antes
        ini = np.searchsorted(d, alvo - janela, side="left")    # a partir de dt−14d
        t.iloc[idx_t, t.columns.get_loc("min14")] = acum[fim] - acum[ini]
    return (t.groupby(["match_id", "team_id"])["min14"].mean() / 90).rename("carga_xi").reset_index()


def montar(p: pd.DataFrame, jogos, coords, minutos, titulares) -> pd.DataFrame:
    p = p.merge(distancias(jogos, coords), on=["match_id", "team_id"], how="left")
    p = p.merge(carga_xi(minutos, jogos, titulares), on=["match_id", "team_id"], how="left")
    adv = p[["match_id", "team_id", "dist_km", "carga_xi"]].rename(
        columns={"team_id": "adv_id", "dist_km": "dist_adv", "carga_xi": "carga_xi_adv"})
    p = p.merge(adv, on=["match_id", "adv_id"], how="left")
    dias = p["dias_desc"].clip(1, 14)
    dias_adv = p["dias_adv"].clip(1, 14)
    p["delta_desc"] = p["dias_desc"] - p["dias_adv"]
    p["vant_desc"] = (p["delta_desc"] >= LIMIAR_DESCANSO).astype(float)
    p["desv_desc"] = (p["delta_desc"] <= -LIMIAR_DESCANSO).astype(float)
    p["carga_log"] = np.log1p(p["dist_km"]) / dias
    p["carga_log_adv"] = np.log1p(p["dist_adv"]) / dias_adv
    p["forca_rel"] = np.log(p["lb_real_tit"] / p.merge(
        p[["match_id", "team_id", "lb_real_tit"]].rename(columns={"team_id": "adv_id", "lb_real_tit": "lb_adv"}),
        on=["match_id", "adv_id"], how="left")["lb_adv"].values)
    grupo = np.where(p["league_id"].isin(LIGAS_DISTANCIA_LONGA), "longa",
                     np.where(p["league_id"].isin(LIGAS_COMPACTAS), "compacta", "outras"))
    for g in ("longa", "compacta", "outras"):
        p[f"carga_log_{g}"] = p["carga_log"] * (grupo == g)
        p[f"carga_log_adv_{g}"] = p["carga_log_adv"] * (grupo == g)
    p["grupo"] = grupo
    return p


# =============================================================================
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corte", default=vin.CORTE_PADRAO)
    parser.add_argument("--entrada", help="painel time-partida de calibrar_potencia_lambda.py")
    parser.add_argument("--cache-dir", help="pasta pra guardar/reaproveitar os downloads (.pkl)")
    args = parser.parse_args()
    corte = pd.Timestamp(args.corte)
    rng = np.random.default_rng(vin.SEED)

    p = vin.preparar_painel(args.entrada, args.cache_dir, corte)
    jogos, _, titulares = vin.carregar(args.cache_dir)
    jogos = jogos.rename(columns={"id": "match_id"})
    jogos["dt"] = pd.to_datetime(jogos["match_date"], format="ISO8601", utc=True).dt.tz_localize(None).dt.normalize()
    coords, minutos = carregar_extras(args.cache_dir)
    p = montar(p, jogos, coords, minutos, titulares)

    antes = p["match_id"].nunique()
    p = p.dropna(subset=["dist_km", "dist_adv", "carga_xi", "carga_xi_adv"])
    completos = p.groupby("match_id").size()
    p = p[p["match_id"].isin(completos[completos == 2].index)].copy()
    tr, te = p[p["data"] < corte], p[p["data"] >= corte]
    print(f"\nCom distância e carga do XI: treino {tr['match_id'].nunique()} / teste {te['match_id'].nunique()} partidas "
          f"(de {antes})")
    fora = p[~p["is_home"]]
    print(f"  visitante: distância mediana {fora['dist_km'].median():.0f} km (p90 {fora['dist_km'].quantile(.9):.0f}); "
          f"|ΔDescanso|≥{LIMIAR_DESCANSO}: {(p['delta_desc'].abs() >= LIMIAR_DESCANSO).mean():.1%}; "
          f"carga do XI média {p['carga_xi'].mean():.2f} jogos-90 em 14 dias")
    for g in ("longa", "compacta", "outras"):
        f = fora[fora["grupo"] == g]
        print(f"  grupo {g:8s}: {f['match_id'].nunique():5d} partidas, distância mediana do visitante {f['dist_km'].median():.0f} km")

    # ⚠️ A carga logística do mandante é ~0 (não viaja) e a do visitante é sempre
    # >0: sem controle de mando ela vira um indicador de "joga fora" e absorve a
    # vantagem de casa que o λ calibrado (bivariada SEM mando) não tem — e que
    # mudou de regime entre temporadas (PR #665). Por isso toda variante de
    # desgaste inclui `mando` (is_home) como controle.
    p["mando"] = p["is_home"].astype(float)
    tr, te = p[p["data"] < corte], p[p["data"] >= corte]
    feats_base = ["mando", "vant_desc", "desv_desc", "carga_log", "carga_log_adv"]
    variantes = {
        "base (titulares confirmados)": ("lb_real_tit", []),
        "só mando (controle)": ("lb_real_tit", ["mando"]),
        "desgaste SEM controle de mando (confundido)": ("lb_real_tit", feats_base[1:]),
        "desgaste (ΔDescanso + carga logística)": ("lb_real_tit", feats_base),
        "desgaste + controle de força": ("lb_real_tit", feats_base + ["forca_rel"]),
        "desgaste + carga do XI": ("lb_real_tit", feats_base + ["carga_xi", "carga_xi_adv"]),
        "carga logística por grupo": ("lb_real_tit", ["mando", "vant_desc", "desv_desc"]
                                      + [f"carga_log_{g}" for g in ("longa", "compacta", "outras")]
                                      + [f"carga_log_adv_{g}" for g in ("longa", "compacta", "outras")]),
    }
    partidas, perdas, _ = vin.avaliar_variantes(tr, te, variantes, rng, "base (titulares confirmados)")

    # recorte nos jogos extremos
    casa = te[te["is_home"]].set_index("match_id")
    fora_te = te[~te["is_home"]].set_index("match_id")
    extremo = ((casa["delta_desc"].abs() >= LIMIAR_DESCANSO) | (fora_te["dist_km"] > LIMIAR_DISTANCIA_KM)).reindex(partidas.index).values
    base = "base (titulares confirmados)"
    print(f"\n=== Recorte: jogos extremos (|ΔDescanso|≥{LIMIAR_DESCANSO} ou distância do visitante >{LIMIAR_DISTANCIA_KM} km): "
          f"{extremo.sum()} de {len(extremo)} ===")
    for nome in perdas:
        if nome == base:
            continue
        for mercado in ("gols por time (NLL)", "1X2"):
            for rot, m in (("extremos", extremo), ("demais", ~extremo)):
                d = (perdas[nome][mercado] - perdas[base][mercado])[m]
                bs = [d[rng.integers(0, len(d), len(d))].mean() for _ in range(vin.N_BOOT)]
                print(f"  {nome:40s} {mercado:20s} {rot:8s} n={m.sum():5d} Δ={d.mean():+.5f} "
                      f"[{np.percentile(bs, 2.5):+.5f},{np.percentile(bs, 97.5):+.5f}]")


if __name__ == "__main__":
    main()
