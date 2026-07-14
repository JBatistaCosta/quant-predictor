"""
Ingestão de estatísticas por partida (xG, xGA, chutes, posse, escanteios,
faltas, cartões) do FBref via soccerdata -> tabela match_stats no Supabase.

Cobre as 6 ligas do projeto, INCLUINDO o Brasileirão (configurado como liga
customizada, pois não vem por padrão no soccerdata).

Uso:
    pip install soccerdata supabase pandas
    set SUPABASE_KEY=sua_service_role_key   (cmd, sem aspas)

    python ingestao_stats_fbref.py BSA 2023     (uma liga+temporada por vez)
    python ingestao_stats_fbref.py PL 2024
    ...

IMPORTANTE - seja paciente e gentil com o FBref:
    - Cada liga+temporada leva ~20-40 min (o FBref limita ~1 página a cada
      poucos segundos e o soccerdata respeita isso; páginas ficam em cache
      local, então re-execuções são rápidas).
    - Rode UMA liga+temporada por vez. Rodar tudo de uma vez em paralelo
      pode fazer o FBref bloquear seu IP temporariamente.
"""

import json
import os
import re
import sys
import unicodedata
from difflib import get_close_matches
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------
# 1. Registrar o Brasileirão como liga customizada ANTES de importar
#    o soccerdata (ele lê o arquivo de config na importação).
# ---------------------------------------------------------------
config_dir = Path.home() / "soccerdata" / "config"
config_dir.mkdir(parents=True, exist_ok=True)
league_dict_path = config_dir / "league_dict.json"

custom = {}
if league_dict_path.exists():
    custom = json.loads(league_dict_path.read_text(encoding="utf-8"))
if "BRA-Serie A" not in custom:
    custom["BRA-Serie A"] = {
        "FBref": "Campeonato Brasileiro Série A",
        "season_start": "Jan",
        "season_end": "Dec",
    }
    league_dict_path.write_text(json.dumps(custom, indent=2, ensure_ascii=False),
                                encoding="utf-8")
    print("Brasileirão registrado na config do soccerdata.")

import soccerdata as sd  # noqa: E402 (importar após escrever a config)
from supabase import create_client  # noqa: E402

# ---------------------------------------------------------------
# 2. Configuração
# ---------------------------------------------------------------
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://cgurxgfdmpmsnrshqycx.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNndXJ4Z2ZkbXBtc25yc2hxeWN4Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4MzM0NTU3NiwiZXhwIjoyMDk4OTIxNTc2fQ.FFp-jjSWJYS-2u_0sOdJzPIcJdDfE_wSfw_Kr11H8Us")

# Nosso código de liga -> (liga no soccerdata, temporadas são ano-calendário?)
LIGAS = {
    "BSA": ("BRA-Serie A", True),
    "PL":  ("ENG-Premier League", False),
    "PD":  ("ESP-La Liga", False),
    "SA":  ("ITA-Serie A", False),
    "BL1": ("GER-Bundesliga", False),
    "FL1": ("FRA-Ligue 1", False),
}


def temporada_soccerdata(nossa: str, ano_calendario: bool) -> str:
    """Nossa temporada '2023' -> '2023' (Brasil) ou '2324' (Europa 2023/24)."""
    ano = int(nossa)
    if ano_calendario:
        return str(ano)
    return f"{str(ano)[-2:]}{str(ano + 1)[-2:]}"


# Casos em que nenhuma normalização automática resolve, porque a fonte usa
# uma convenção de nome totalmente diferente para o mesmo clube. Chave =
# nome normalizado como vem do FBref; valor = nome normalizado como está
# no banco. Se aparecerem novos avisos de "sem correspondência", confira
# se é um caso desses e adicione aqui.
ALIASES_MANUAIS = {
    "athletico pr": "ca paranaense",          # Athletico Paranaense / CA Paranaense
    "wolves": "Wolverhampton Wanderers FC",    # apelido, sem token em comum com o nome oficial
    "gladbach": "Borussia Mönchengladbach",    # apelido truncado de "Mönchengladbach"
    "brest": "Stade Brestois 29",              # cidade vs gentílico ("brestois")
    "lyon": "Olympique Lyonnais",              # cidade vs gentílico ("lyonnais")
    "rennes": "Stade Rennais FC 1901",         # cidade vs gentílico ("rennais")
    "psg": "Paris Saint-Germain FC",           # sigla — banco também tem "Paris FC" (outro clube!)
}

# Tokens de tipo de clube a descartar (sufixos/prefixos comuns). Removidos
# por TOKEN INTEIRO, nunca por substring — remover por substring já causou
# um bug real (ver histórico): "as " dentro de "Goiás" virava "Goi".
# Tokens de tipo de clube a descartar (sufixos/prefixos GENÉRICOS de verdade
# — "FC", "SC" etc.). Removidos por TOKEN INTEIRO, nunca por substring — já
# houve um bug real de substring comendo pedaço de nome (ver histórico).
# "real" e "athletic" ficam de FORA de propósito: não são sufixos de tipo,
# são parte do nome próprio de vários clubes (Real Madrid, Real Sociedad,
# Real Betis, Athletic Club) — descartá-los já causou dois bugs de match
# errado (ver histórico: "Athletic Club" viraria "", e "Real Madrid"
# colapsaria para só "madrid", ambos por demais genéricos).
# Tokens de tipo de clube a descartar (sufixos/prefixos GENÉRICOS de verdade
# — "FC", "SC" etc., que NUNCA diferenciam um clube do outro sozinhos).
# Removidos por TOKEN INTEIRO, nunca por substring — já houve um bug real
# de substring comendo pedaço de nome (ver histórico).
#
# De propósito, NÃO descartamos nada que possa carregar informação
# distintiva de verdade: "real"/"athletic" são nome próprio de clube
# (Real Madrid, Athletic Club — já causaram bug quando descartados); "sp"
# poderia ser sigla de estado (ex: distinguir um "Botafogo-SP" de um
# "Botafogo-RJ") e não deve ser tratado como sufixo genérico. Regra geral:
# na dúvida, mantém — informação a mais só faz o casamento por subconjunto
# falhar com segurança (cai pro fuzzy/alias); informação de menos pode
# fazer ele CASAR ERRADO, que é o problema grave.
_TOKENS_IGNORADOS = {
    "fc", "cf", "afc", "fbpa", "fbc", "sc", "ac", "ssc", "as", "rc", "cd",
    "ud", "rcd", "ec", "ca", "cr", "se", "club", "clube",
    "1846", "1904", "04", "05", "96",
}


def normalizar(nome: str) -> str:
    """Normaliza nomes de time para casamento aproximado entre fontes.
    Opera por token (palavra inteira), nunca por substring — ver comentário
    em _TOKENS_IGNORADOS. IMPORTANTE: troca traços/pontuação por espaço
    ANTES de remover acentos — caracteres como "–" não têm equivalente
    ASCII e, se a ordem for invertida, somem em vez de virar espaço,
    grudando palavras (ex: "Athletico–PR" -> "athleticopr")."""
    for ch in ("-", "–", "—", ".", "'", "&"):
        nome = nome.replace(ch, " ")
    nome = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode()
    nome = nome.lower()
    tokens_originais = nome.split()
    tokens = [t for t in tokens_originais if t not in _TOKENS_IGNORADOS]
    if not tokens:
        # Nome inteiro era só tokens "de tipo de clube" (ex: "Athletic Club"
        # -> ambas palavras estão em _TOKENS_IGNORADOS -> ficaria "").
        # Um resultado vazio é PERIGOSO: em Python, set() é subconjunto
        # trivial de QUALQUER conjunto, então esse nome bateria match com
        # qualquer time do banco no casamento por subconjunto. Já causou
        # corrupção real de dados (times espanhóis diferentes todos
        # casando com "Athletic Club" por esse motivo). Nunca retornar "".
        tokens = tokens_originais
    return " ".join(tokens)


def match_times(norm_fb: str, norm_para_id: dict) -> tuple[str | None, str]:
    """Casa um nome normalizado do FBref com um nome normalizado do banco.
    Retorna (nome_banco_normalizado ou None, método usado).

    Ordem de tentativa, da mais para a menos confiável:
        1. match exato
        2. alias manual
        3. subconjunto de tokens (ex: {'corinthians'} contido em
           {'corinthians','paulista'}) — mais seguro que fuzzy puro, que já
           confundiu 'Corinthians' com 'Coritiba' por serem grafias
           parecidas apesar de serem times diferentes
        4. fuzzy como último recurso, com corte alto (0.8) e sinalizado
           para conferência manual
    """
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
        # tokens_fb e tokens_c precisam ser não-vazios: conjunto vazio é
        # subconjunto trivial de QUALQUER conjunto em Python, o que já
        # causou um match errado (ver comentário em normalizar()).
        if tokens_fb and tokens_c and (tokens_fb <= tokens_c or tokens_c <= tokens_fb):
            return candidato, "subconjunto"

    prox = get_close_matches(norm_fb, list(norm_para_id), n=1, cutoff=0.8)
    if prox:
        return prox[0], "fuzzy (confira!)"

    return None, "falhou"


def col(df: pd.DataFrame, *candidatos):
    """Encontra uma coluna por nome aproximado (FBref varia entre versões)."""
    achatadas = {}
    for c in df.columns:
        chave = "_".join(str(p) for p in c if p) if isinstance(c, tuple) else str(c)
        achatadas[chave.lower().strip()] = c
    for cand in candidatos:
        if cand.lower() in achatadas:
            return achatadas[cand.lower()]
    return None


def achatar_colunas(df: pd.DataFrame) -> pd.DataFrame:
    """Achata colunas em dois níveis (ex: ('standard','Sh')) para strings
    simples ('standard_Sh'). Necessário porque df.rename() falha em
    silêncio ao tentar trocar uma chave-tupla por uma string quando o
    eixo de colunas continua sendo um MultiIndex."""
    if df.columns.nlevels > 1:
        df = df.copy()
        df.columns = [
            "_".join(str(p) for p in tup if p and str(p) != "nan")
            for tup in df.columns
        ]
    return df


def carregar_stat(fbref, stat_type: str) -> pd.DataFrame:
    df = fbref.read_team_match_stats(stat_type=stat_type)
    df = df.reset_index()
    return achatar_colunas(df)


def carregar_crosswalk(supabase, source: str) -> dict:
    """IDs já conhecidos dessa fonte -> nosso team_id (de execuções anteriores)."""
    linhas, inicio = [], 0
    while True:
        lote = (supabase.table("team_source_ids").select("source_id, team_id")
                .eq("source", source).range(inicio, inicio + 999).execute().data)
        linhas.extend(lote)
        if len(lote) < 1000:
            break
        inicio += 1000
    return {l["source_id"]: l["team_id"] for l in linhas}


def gravar_crosswalk(supabase, source: str, source_id: str, team_id: int, nome_fonte: str) -> None:
    """Grava a descoberta de um novo (source, source_id) -> team_id, para que
    a próxima raspagem dessa fonte nunca mais precise casar por nome."""
    supabase.table("team_source_ids").upsert(
        {"source": source, "source_id": source_id, "team_id": team_id, "source_name": nome_fonte},
        on_conflict="source,source_id",
    ).execute()


def extrair_ids_fbref(fbref) -> dict:
    """{nome_do_time_no_fbref: id_estavel_do_fbref}, a partir da URL de cada
    time (ex: '/en/squads/72514614/...' -> '72514614'). O ID nunca muda,
    mesmo que o nome de exibição do clube mude."""
    df = fbref.read_team_season_stats(stat_type="standard")
    nomes = df.index.get_level_values("team")
    ids = {}
    for nome, url in zip(nomes, df["url"]):
        m = re.search(r"/squads/([0-9a-fA-F]+)/", str(url))
        if m:
            ids[nome] = m.group(1)
    return ids


def main():
    if "COLE_SUA" in SUPABASE_KEY:
        sys.exit("Configure SUPABASE_KEY antes de rodar.")
    if len(sys.argv) < 3 or sys.argv[1] not in LIGAS:
        sys.exit(f"Uso: python {sys.argv[0]} <LIGA> <TEMPORADA>\n"
                 f"Ligas: {', '.join(LIGAS)} | Temporadas: 2023, 2024, 2025")

    liga_cod, temporada = sys.argv[1], sys.argv[2]
    liga_sd, ano_cal = LIGAS[liga_cod]
    temp_sd = temporada_soccerdata(temporada, ano_cal)

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    # ---- Nossos jogos dessa liga/temporada (com nomes dos times) ----
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
        sys.exit(f"Nenhum jogo de {liga_cod}/{temporada} no banco. Rode a ingestão de resultados antes.")

    ids_times = {j["home_team_id"] for j in jogos} | {j["away_team_id"] for j in jogos}
    times_rows = (supabase.table("teams").select("id, name")
                  .in_("id", list(ids_times)).execute().data)
    nome_por_id = {t["id"]: t["name"] for t in times_rows}
    norm_para_id = {normalizar(t["name"]): t["id"] for t in times_rows}

    nossos = pd.DataFrame(jogos)
    nossos["data"] = pd.to_datetime(nossos["match_date"], utc=True).dt.date

    # ---- Estatísticas do FBref ----
    print(f"Baixando estatísticas do FBref: {liga_sd} / {temp_sd} (pode demorar)...")
    fbref = sd.FBref(leagues=liga_sd, seasons=temp_sd)

    schedule = carregar_stat(fbref, "schedule")   # data, GF/GA, xG, xGA, Poss
    shooting = carregar_stat(fbref, "shooting")   # Sh, SoT
    misc = carregar_stat(fbref, "misc")           # Fls, CrdY, CrdR
    # Nota: escanteios (passing_types/CK) não está disponível por partida
    # nesta versão do soccerdata, só em agregados por temporada. Fica nulo.

    # DIAGNÓSTICO TEMPORÁRIO — remover depois de descobrir o nome real da
    # coluna de xG. Lista todas as colunas de cada tabela para conferência.
    print("\n  [DIAGNÓSTICO] colunas em 'schedule':", sorted(schedule.columns.tolist()))
    print("  [DIAGNÓSTICO] colunas em 'shooting':", sorted(shooting.columns.tolist()))
    print("  [DIAGNÓSTICO] colunas em 'misc':", sorted(misc.columns.tolist()))

    def chaves(df):
        c_team = col(df, "team")
        c_date = col(df, "date")
        c_venue = col(df, "venue")
        c_opp = col(df, "opponent")
        return c_team, c_date, c_venue, c_opp

    c_team, c_date, c_venue, c_opp = chaves(schedule)
    if not all([c_team, c_date, c_venue]):
        sys.exit(f"Colunas inesperadas no FBref: {list(schedule.columns)[:15]}...")

    # ---- Crosswalk: IDs do FBref já conhecidos de raspagens anteriores ----
    crosswalk = carregar_crosswalk(supabase, "fbref")
    try:
        ids_fbref = extrair_ids_fbref(fbref)  # {nome no FBref: id estável}
    except Exception as e:
        print(f"  AVISO: não consegui extrair IDs estáveis do FBref ({e}); "
              f"seguindo só com casamento por nome.")
        ids_fbref = {}

    # Mapeia nome FBref -> nosso team_id.
    # 1) Se já conhecemos o ID estável desse time (de raspagem anterior),
    #    usa direto — sem ambiguidade nenhuma.
    # 2) Senão, casa por nome (exato -> alias -> subconjunto -> fuzzy) e
    #    GRAVA o ID estável no crosswalk, para nunca mais precisar repetir
    #    esse passo para esse time.
    mapa_times = {}
    for nome_fb in schedule[c_team].unique():
        fbref_id = ids_fbref.get(nome_fb)
        if fbref_id and fbref_id in crosswalk:
            mapa_times[nome_fb] = crosswalk[fbref_id]
            continue

        norm = normalizar(str(nome_fb))
        norm_banco, metodo = match_times(norm, norm_para_id)
        if norm_banco is None:
            print(f"  AVISO: time FBref sem correspondência no banco: '{nome_fb}'")
            continue

        team_id = norm_para_id[norm_banco]
        mapa_times[nome_fb] = team_id
        if "confira" in metodo:
            print(f"  ATENÇÃO ({metodo}): '{nome_fb}' -> "
                  f"'{nome_por_id[team_id]}' — confira se é o time certo!")

        if fbref_id:
            gravar_crosswalk(supabase, "fbref", fbref_id, team_id, nome_fb)
            print(f"  Novo time gravado no crosswalk: '{nome_fb}' (id {fbref_id}) "
                  f"-> '{nome_por_id[team_id]}'")

    # Junta as 4 tabelas de stats por (team, date)
    def prepara(df, colunas_interesse):
        ct, cd, _, _ = chaves(df)
        sel = {ct: "team_fb", cd: "data"}
        renome = {}
        for alvo, cands in colunas_interesse.items():
            c = col(df, *cands)
            if c is not None:
                renome[c] = alvo
        out = df[[ct, cd] + list(renome)].rename(columns={**sel, **renome})
        out["data"] = pd.to_datetime(out["data"]).dt.date
        return out

    base = prepara(schedule, {"possession": ["poss"]})
    tiros = prepara(shooting, {"shots": ["standard_sh", "sh"],
                               "shots_on_target": ["standard_sot", "sot"],
                               # xG mora na tabela "shooting", grupo "Expected"
                               # (não na "schedule" — esse foi o bug: a busca
                               # por "xg" solto batia em nada e falhava calado,
                               # sem erro, deixando a coluna inteira nula).
                               "xg": ["expected_xg", "xg"]})
    faltas = prepara(misc, {"fouls": ["performance_fls", "fls"],
                            "yellow_cards": ["performance_crdy", "crdy"],
                            "red_cards": ["performance_crdr", "crdr"]})

    stats = base
    for extra in (tiros, faltas):
        stats = stats.merge(extra, on=["team_fb", "data"], how="left")

    # ---- Casa cada linha (time, data) com nosso match_id ----
    registros, sem_match = [], 0
    for _, linha in stats.iterrows():
        team_id = mapa_times.get(linha["team_fb"])
        if team_id is None:
            sem_match += 1
            continue
        # jogo do nosso banco com esse time nessa data (tolerância de 1 dia
        # para diferenças de fuso horário entre fontes)
        cand = nossos[
            ((nossos["home_team_id"] == team_id) | (nossos["away_team_id"] == team_id))
            & (abs(pd.to_datetime(nossos["data"]) - pd.to_datetime(linha["data"])).dt.days <= 1)
        ]
        if cand.empty:
            sem_match += 1
            continue
        match_id = int(cand.iloc[0]["id"])

        def num(v):
            try:
                return None if pd.isna(v) else float(v)
            except (TypeError, ValueError):
                return None

        registros.append({
            "match_id": match_id,
            "team_id": int(team_id),
            "xg": num(linha.get("xg")),
            "possession": num(linha.get("possession")),
            "shots": int(v) if (v := num(linha.get("shots"))) is not None else None,
            "shots_on_target": int(v) if (v := num(linha.get("shots_on_target"))) is not None else None,
            "corners": int(v) if (v := num(linha.get("corners"))) is not None else None,
            "fouls": int(v) if (v := num(linha.get("fouls"))) is not None else None,
            "yellow_cards": int(v) if (v := num(linha.get("yellow_cards"))) is not None else None,
            "red_cards": int(v) if (v := num(linha.get("red_cards"))) is not None else None,
        })

    for i in range(0, len(registros), 500):
        supabase.table("match_stats").upsert(
            registros[i : i + 500], on_conflict="match_id,team_id"
        ).execute()

    print(f"\n{liga_cod}/{temporada}: {len(registros)} linhas de estatísticas gravadas "
          f"({sem_match} linhas do FBref sem correspondência).")
    print("Nota: xGA fica implícito — o xG do adversário na mesma partida.")

    # Checagem de sanidade: se alguma coluna essencial veio 100% vazia, é
    # quase certo que a busca de coluna no FBref falhou em silêncio (como
    # já aconteceu com o xG) — melhor avisar alto do que descobrir depois.
    if registros:
        for campo in ("xg", "shots", "possession"):
            preenchidos = sum(1 for r in registros if r.get(campo) is not None)
            if preenchidos == 0:
                print(f"  ⚠️  ALERTA: coluna '{campo}' veio 100% vazia nesta leva — "
                      f"provável falha silenciosa na busca de coluna do FBref. "
                      f"Confira antes de seguir para as próximas ligas/temporadas.")


if __name__ == "__main__":
    main()
