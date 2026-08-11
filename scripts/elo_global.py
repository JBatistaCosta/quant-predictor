"""
Elo global unificado (escopo='global' em team_elo/team_elo_history) — UM único
pool de rating por time, calculado a partir de TODAS as partidas finalizadas
de TODAS as competições (domésticas + continentais + internacionais) juntas,
em ordem cronológica.

Diferente dos escopos existentes (ver api/model-maintenance.js, tarefa=elo):
  - 'liga': um Elo ISOLADO por competição individual (Brasileirão e Premier
    League nunca se cruzam) — mantido como está, é o que dados_historicos.py
    consome como feature de modelo (elo_home/elo_away), não pode mudar de
    forma sem invalidar os modelos já treinados/registrados.
  - 'geral': cross-liga mas só ajusta em jogos de Champions League — times
    que nunca jogam Champions ficam travados no valor semeado.
  - 'global' (este script): todo mundo no mesmo pool, atualiza em QUALQUER
    partida (doméstica, continental, o que for). Usado só pelo painel
    /ratings (RatingClubes.jsx) pra permitir comparar times de qualquer
    competição/país/confederação entre si — não alimenta o pipeline de ML.

Sem jogos clube-vs-clube intercontinentais na base (Club World Cup/
Intercontinental Cup ainda sem partidas importadas), o grafo de resultados
é desconectado por confederação — sem uma âncora externa, a comparação
ENTRE confederações seria arbitrária (um artefato de onde cada time começou,
não de força real). Por isso, mesma lógica do escopo 'geral': semeia do
ClubElo (`team_elo_external`) quando disponível, e só cai pro rating inicial
1500 pra quem não tem seed externa.

Mesma fórmula do Elo em JS (api/model-maintenance.js): K=20, vantagem de
casa=65, multiplicador de diferença de gols (1 / 1.5 / (11+dif)/8).

Full recompute a cada execução (delete-e-regrava, mesmo padrão dos outros
escopos) — não incremental. Em Python/GitHub Actions não tem o limite de 60s
do Vercel que obriga o `elo-rotativo` a processar um escopo por dia; ~30 mil
partidas processam em segundos.

Uso:
    python scripts/elo_global.py
"""

import os
import sys
from datetime import datetime, timezone

from supabase import create_client

SUPABASE_URL = (os.environ.get("SUPABASE_URL") or "").strip()
SUPABASE_KEY = (os.environ.get("SUPABASE_KEY") or "").strip()

RATING_INICIAL = 1500
K_ELO = 20
VANTAGEM_CASA = 65


def _paginar(montar_query, order="id"):
    """Busca todas as linhas com paginação explícita (evita corte silencioso de 1000).
    `montar_query` recebe o client-base já com .select()/.eq() aplicados e devolve a
    query pronta pra encadear .order()/.range()."""
    result = []
    page = 0
    while True:
        chunk = montar_query().order(order).range(page * 1000, page * 1000 + 999).execute().data
        result.extend(chunk)
        if len(chunk) < 1000:
            break
        page += 1
    return result


def multiplicador_diferenca(diferenca: int) -> float:
    d = abs(diferenca)
    if d <= 1:
        return 1
    if d == 2:
        return 1.5
    return (11 + d) / 8


def atualizar_elo(rating_mandante, rating_visitante, gols_mandante, gols_visitante):
    diferenca = gols_mandante - gols_visitante
    resultado_mandante = 1 if diferenca > 0 else (0 if diferenca < 0 else 0.5)
    esperado_mandante = 1 / (1 + 10 ** (-((rating_mandante + VANTAGEM_CASA) - rating_visitante) / 400))
    delta = K_ELO * multiplicador_diferenca(diferenca) * (resultado_mandante - esperado_mandante)
    return rating_mandante + delta, rating_visitante - delta


def main():
    if not SUPABASE_URL or not SUPABASE_KEY:
        sys.exit("Defina SUPABASE_URL e SUPABASE_KEY (service_role) como variáveis de ambiente.")

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    print("Carregando partidas finalizadas de TODAS as competições...")
    partidas = _paginar(
        lambda: supabase.table("matches")
            .select("id, round, match_date, home_team_id, away_team_id, home_goals, away_goals")
            .eq("status", "finished")
    )
    partidas = [p for p in partidas if p["home_goals"] is not None and p["away_goals"] is not None]
    partidas.sort(key=lambda p: p["match_date"])
    print(f"  {len(partidas)} partidas.")

    print("Carregando seeds externas (ClubElo)...")
    seeds_externas = _paginar(
        lambda: supabase.table("team_elo_external").select("team_id, elo, valido_ate"),
        order="valido_ate",
    )
    seed_por_time = {}
    for s in sorted(seeds_externas, key=lambda s: s["valido_ate"] or "", reverse=True):
        seed_por_time.setdefault(s["team_id"], float(s["elo"]))
    print(f"  {len(seed_por_time)} times com seed externa.")

    rating: dict[int, float] = {}
    contagem: dict[int, int] = {}
    historico = []

    for p in partidas:
        home_id, away_id = p["home_team_id"], p["away_team_id"]
        if home_id not in rating:
            rating[home_id] = seed_por_time.get(home_id, RATING_INICIAL)
            contagem[home_id] = 0
        if away_id not in rating:
            rating[away_id] = seed_por_time.get(away_id, RATING_INICIAL)
            contagem[away_id] = 0

        antes_mandante, antes_visitante = rating[home_id], rating[away_id]
        novo_mandante, novo_visitante = atualizar_elo(antes_mandante, antes_visitante, p["home_goals"], p["away_goals"])
        rating[home_id], rating[away_id] = novo_mandante, novo_visitante
        contagem[home_id] += 1
        contagem[away_id] += 1

        historico.append({
            "team_id": home_id, "escopo": "global", "league_id": None, "match_id": p["id"],
            "rodada": p["round"], "rating_antes": antes_mandante, "rating_depois": novo_mandante,
            "match_date": p["match_date"],
        })
        historico.append({
            "team_id": away_id, "escopo": "global", "league_id": None, "match_id": p["id"],
            "rodada": p["round"], "rating_antes": antes_visitante, "rating_depois": novo_visitante,
            "match_date": p["match_date"],
        })

    agora = datetime.now(timezone.utc).isoformat()
    linhas_elo = [
        {"team_id": team_id, "escopo": "global", "league_id": None, "rating": r,
         "partidas": contagem[team_id], "atualizado_em": agora}
        for team_id, r in rating.items()
    ]

    print(f"Regravando team_elo/team_elo_history (escopo=global): {len(linhas_elo)} times, {len(historico)} linhas de histórico...")
    supabase.table("team_elo_history").delete().eq("escopo", "global").execute()
    supabase.table("team_elo").delete().eq("escopo", "global").execute()

    for i in range(0, len(linhas_elo), 500):
        supabase.table("team_elo").upsert(
            linhas_elo[i:i + 500], on_conflict="team_id,escopo,league_id"
        ).execute()
    for i in range(0, len(historico), 500):
        supabase.table("team_elo_history").insert(historico[i:i + 500]).execute()

    print(f"OK: {len(linhas_elo)} times atualizados, {len(partidas)} partidas processadas.")


if __name__ == "__main__":
    main()
