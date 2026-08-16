"""
Backfill de xG via Understat — alternativa ao backfill_xg.py (FBref), que
esbarrou num bug conhecido da biblioteca soccerdata (issue #704: URL de
temporada errada em read_schedule()). O Understat expõe os dados como JSON
embutido na página (não tabela HTML raspada), então não sofre do mesmo tipo
de problema, e de quebra traz mais métricas: xG, xG sem pênalti (npxG),
pontos esperados (xpts) e PPDA (intensidade de pressão).

Cobre só as 5 ligas europeias — Understat não tem Brasileirão (limitação
já conhecida, sem solução gratuita encontrada até agora).

IMPORTANTE: como já ingerimos xG do FBref pra algumas partidas com sucesso
(nem todas — foi o que descobrimos que estava zerado), esse script vai
SOBRESCREVER o xg_source para 'understat' onde conseguir. Isso é
intencional: Understat costuma ser considerado mais preciso/consistente
para xG, então prevalece quando os dois estão disponíveis.

Uso:
    pip install soccerdata supabase pandas
    set SUPABASE_KEY=sua_service_role_key   (cmd, sem aspas)

    python backfill_xg_understat.py PL 2023
    python backfill_xg_understat.py PD 2023
    ... (uma liga+temporada por vez)
"""

import os
import sys
import unicodedata
from difflib import get_close_matches

import pandas as pd
import soccerdata as sd
from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

# Understat só cobre as 5 grandes ligas europeias (sem Brasileirão)
LIGAS = {
    "PL":  "ENG-Premier League",
    "PD":  "ESP-La Liga",
    "SA":  "ITA-Serie A",
    "BL1": "GER-Bundesliga",
    "FL1": "FRA-Ligue 1",
}

# O Understat usa nomes de time em estilo próprio, diferente tanto do FBref
# quanto do football-data.org — mas na prática reaproveita vários padrões
# já vistos (mesmo apelido de cidade em vez de gentílico para os clubes
# franceses, por exemplo).
ALIASES_MANUAIS = {
    "rasenballsport leipzig": "RB Leipzig",   # nome completo por extenso do RB
    "cologne": "1. FC Köln",                  # nome em inglês da cidade (Cologne = Köln); "FC" já é removido na normalização
    "brest": "Stade Brestois 29",             # cidade vs gentílico ("brestois")
    "lyon": "Olympique Lyonnais",             # cidade vs gentílico ("lyonnais")
    "rennes": "Stade Rennais FC 1901",        # cidade vs gentílico ("rennais")
    "inter": "FC Internazionale Milano",      # apelido curto do Inter de Milão
}
_TOKENS_IGNORADOS = {
    "fc", "cf", "afc", "fbpa", "fbc", "sc", "ac", "ssc", "as", "rc", "cd",
    "ud", "rcd", "ec", "ca", "cr", "se", "club", "clube",
    "1846", "1904", "04", "05", "96",
}


def normalizar(nome: str) -> str:
    for ch in ("-", "–", "—", ".", "'", "&"):
        nome = nome.replace(ch, " ")
    nome = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode()
    nome = nome.lower()
    tokens_originais = nome.split()
    tokens = [t for t in tokens_originais if t not in _TOKENS_IGNORADOS]
    if not tokens:
        tokens = tokens_originais
    return " ".join(tokens)


def match_times(norm_fb: str, norm_para_id: dict):
    if norm_fb in norm_para_id:
        return norm_fb, "exato"
    alias_bruto = ALIASES_MANUAIS.get(norm_fb)
    if alias_bruto is not None:
        alias = normalizar(alias_bruto)
        if alias in norm_para_id:
            return alias, "alias"
    tokens_fb = set(norm_fb.split())
    for candidato in norm_para_id:
        tokens_c = set(candidato.split())
        if tokens_fb and tokens_c and (tokens_fb <= tokens_c or tokens_c <= tokens_fb):
            return candidato, "subconjunto"
    prox = get_close_matches(norm_fb, list(norm_para_id), n=1, cutoff=0.8)
    if prox:
        return prox[0], "fuzzy (confira!)"
    return None, "falhou"


def main():
    if "COLE_SUA" in SUPABASE_KEY:
        sys.exit("Configure SUPABASE_KEY antes de rodar.")
    if len(sys.argv) < 3 or sys.argv[1] not in LIGAS:
        sys.exit(f"Uso: python {sys.argv[0]} <LIGA> <TEMPORADA>\nLigas: {', '.join(LIGAS)}")

    liga_cod, temporada = sys.argv[1], sys.argv[2]
    liga_understat = LIGAS[liga_cod]

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    liga_row = supabase.table("leagues").select("id").eq("external_id", liga_cod).execute().data
    if not liga_row:
        sys.exit(f"Liga {liga_cod} não encontrada no banco.")
    liga_id = liga_row[0]["id"]

    jogos, inicio = [], 0
    while True:
        lote = (supabase.table("matches")
                .select("id, match_date, home_team_id, away_team_id")
                .eq("league_id", liga_id).eq("season", temporada)
                .range(inicio, inicio + 999).execute().data)
        jogos.extend(lote)
        if len(lote) < 1000:
            break
        inicio += 1000
    if not jogos:
        sys.exit(f"Nenhum jogo de {liga_cod}/{temporada} no banco.")

    nossos = pd.DataFrame(jogos)
    nossos["data"] = pd.to_datetime(nossos["match_date"], utc=True).dt.date

    ids_times = {j["home_team_id"] for j in jogos} | {j["away_team_id"] for j in jogos}
    times_rows = (supabase.table("teams").select("id, name")
                  .in_("id", list(ids_times)).execute().data)
    norm_para_id = {normalizar(t["name"]): t["id"] for t in times_rows}
    nome_por_id = {t["id"]: t["name"] for t in times_rows}

    print(f"Baixando xG do Understat: {liga_understat} / {temporada} (pode demorar)...")
    understat = sd.Understat(leagues=liga_understat, seasons=temporada)
    df = understat.read_team_match_stats().reset_index()

    faltando = [c for c in ("home_team", "away_team", "home_xg", "away_xg", "date")
                if c not in df.columns]
    if faltando:
        print("  [DIAGNÓSTICO] colunas disponíveis:", sorted(df.columns.tolist()))
        sys.exit(f"Faltam colunas esperadas: {faltando} — confira o diagnóstico acima.")

    # Campos extras: já vêm na mesma resposta, sem custo de raspagem adicional.
    # Alguns podem não existir em versões antigas da lib — captura defensiva.
    campos_extra = ["ppda", "expected_points", "np_xg", "deep_completions"]
    extras_disponiveis = [c for c in campos_extra
                          if f"home_{c}" in df.columns and f"away_{c}" in df.columns]
    faltando_extra = set(campos_extra) - set(extras_disponiveis)
    if faltando_extra:
        print(f"  Aviso: campos extras não disponíveis nesta versão: {faltando_extra}")

    df["_data"] = pd.to_datetime(df["date"]).dt.date

    mapa_times = {}
    for nome_us in pd.concat([df["home_team"], df["away_team"]]).unique():
        norm = normalizar(str(nome_us))
        banco_norm, metodo = match_times(norm, norm_para_id)
        if banco_norm is None:
            print(f"  AVISO: time sem correspondência: '{nome_us}'")
            continue
        mapa_times[nome_us] = norm_para_id[banco_norm]
        if "confira" in metodo:
            print(f"  ATENÇÃO ({metodo}): '{nome_us}' -> '{nome_por_id[mapa_times[nome_us]]}'")

    atualizacoes, sem_xg, sem_match = [], 0, 0
    for _, linha in df.iterrows():
        home_id = mapa_times.get(linha["home_team"])
        away_id = mapa_times.get(linha["away_team"])
        if home_id is None or away_id is None:
            continue

        cand = nossos[
            (nossos["home_team_id"] == home_id) & (nossos["away_team_id"] == away_id)
            & (abs(pd.to_datetime(nossos["data"]) - pd.to_datetime(linha["_data"])).dt.days <= 15)
        ]
        if cand.empty:
            sem_match += 1
            continue
        match_id = int(cand.iloc[0]["id"])

        hxg, axg = linha["home_xg"], linha["away_xg"]
        if pd.isna(hxg) and pd.isna(axg):
            sem_xg += 1
            continue

        def extras(lado: str) -> dict:
            """Monta os campos extras (ppda, xpts, npxg, deep_completions)
            disponíveis para o lado 'home' ou 'away' desta linha."""
            out = {}
            for campo in extras_disponiveis:
                v = linha.get(f"{lado}_{campo}")
                if v is None or pd.isna(v):
                    continue
                out[campo] = int(v) if campo == "deep_completions" else round(float(v), 3)
            return out

        if not pd.isna(hxg):
            atualizacoes.append({"match_id": match_id, "team_id": int(home_id),
                                  "xg": round(float(hxg), 3), "xg_source": "understat",
                                  **extras("home")})
        if not pd.isna(axg):
            atualizacoes.append({"match_id": match_id, "team_id": int(away_id),
                                  "xg": round(float(axg), 3), "xg_source": "understat",
                                  **extras("away")})

    for i in range(0, len(atualizacoes), 500):
        supabase.table("match_stats").upsert(
            atualizacoes[i : i + 500], on_conflict="match_id,team_id"
        ).execute()

    print(f"\n{liga_cod}/{temporada}: {len(atualizacoes)} valores de xG atualizados (fonte: understat) "
          f"({sem_xg} partidas sem xG na fonte, {sem_match} sem correspondência de partida).")


if __name__ == "__main__":
    main()
