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

Dois modos (--modo):
  - completo (default): delete-e-regrava do zero, mesmo padrão dos outros
    escopos. Necessário sempre que uma liga ou temporada NOVA é adicionada —
    partidas históricas de uma liga recém-importada têm datas no PASSADO,
    então precisam ser inseridas na posição cronológica certa da sequência
    de Elo, não só "no fim". ~30 mil partidas processam em segundos (sem o
    limite de 60s do Vercel que obriga o elo-rotativo a espalhar por
    escopo/dia).
  - incremental: só processa as partidas que ainda não têm linha em
    team_elo_history (escopo=global), aplicando por cima do rating atual em
    vez de recalcular tudo — mais barato (menos leitura/escrita) pro caso
    comum de "só entraram jogos novos das ligas que já estavam na base".
    Proteção: se alguma partida nova tiver data ANTERIOR à mais recente já
    processada (sinal de backfill/liga nova, não just um jogo do dia
    seguinte), aborta com erro em vez de aplicar fora de ordem — nesse caso
    rode --modo completo.

Uso:
    python scripts/elo_global.py [--modo completo|incremental]
"""

import argparse
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
    query pronta pra encadear .order()/.range(). `order` PRECISA ser uma coluna única
    (`id` por padrão) -- sem isso a paginação por `.range()` não garante o mesmo
    conjunto de linhas entre chamadas separadas (linha duplicada ou pulada entre
    páginas -- achado real desta sessão em `carregar_seeds_externas`, que usava
    `valido_ate`, coluna repetida pra vários times/janelas históricas)."""
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


def carregar_partidas_finalizadas(supabase):
    partidas = _paginar(
        lambda: supabase.table("matches")
            .select("id, round, match_date, home_team_id, away_team_id, home_goals, away_goals")
            .eq("status", "finished")
    )
    partidas = [p for p in partidas if p["home_goals"] is not None and p["away_goals"] is not None]
    partidas.sort(key=lambda p: p["match_date"])
    return partidas


def carregar_seeds_externas(supabase):
    # Paginação por `id` (default de _paginar) -- correta e suficiente aqui:
    # o "mais recente por time" é decidido pelo sort em memória logo abaixo
    # (sobre TODAS as linhas já buscadas), não pela ordem em que a paginação
    # as trouxe da base.
    seeds_externas = _paginar(
        lambda: supabase.table("team_elo_external").select("team_id, elo, valido_ate")
    )
    seed_por_time = {}
    for s in sorted(seeds_externas, key=lambda s: s["valido_ate"] or "", reverse=True):
        seed_por_time.setdefault(s["team_id"], float(s["elo"]))
    return seed_por_time


def processar_partidas(partidas, rating, contagem, seed_por_time):
    """Aplica o Elo sequencialmente sobre `partidas` (ordenadas por match_date),
    mutando `rating`/`contagem` in-place e devolvendo as linhas de histórico geradas."""
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
    return historico


TAMANHO_LOTE_HISTORICO_RPC = 5000  # ~30 mil partidas x 2 linhas de histórico
# num --modo completo passariam de 10MB num payload único -- fatiado em
# lotes pra ficar bem abaixo de qualquer limite de tamanho de requisição do
# PostgREST. O delete + as linhas de team_elo (sempre pequenas, no máximo
# uma por time) vão só no PRIMEIRO lote; os demais só acrescentam
# histórico. Cada lote continua sendo UMA transação Postgres (a chamada RPC
# inteira), então fatiar aqui não reintroduz o bug original: no --modo
# completo o rating já foi computado no fim, na memória, ANTES de gravar
# qualquer coisa (não depende do que já está no banco), e o --modo
# incremental nunca gera histórico grande o bastante pra precisar de mais
# de um lote (só processa o que ainda não foi processado desde a última
# vez).
def registrar_elo_lote(supabase, rating, contagem, historico, times_para_gravar=None, apagar_escopo_global=False):
    """Grava team_elo + team_elo_history (escopo=global) via RPC
    (registrar_team_elo_lote, ver migration
    20260902220000_atomicidade_elo_e_chave_team_elo_external.sql) --
    plpgsql roda como transação implícita, então as duas tabelas saem
    consistentes juntas ou nenhuma muda a cada chamada. Antes disso,
    `gravar_elo` escrevia team_elo primeiro e `gravar_historico` escrevia
    team_elo_history depois, em requisições HTTP separadas (a de histórico
    ainda em lotes de 500) -- como o cursor do modo incremental
    (`ids_processados`, abaixo) é derivado da ÚLTIMA linha de
    team_elo_history, um crash/timeout entre as duas escritas deixava
    team_elo já com o rating novo mas o histórico incompleto: a próxima
    chamada via --modo incremental reprocessava as mesmas partidas por cima
    de um rating que já as tinha aplicado -- double-counting silencioso,
    sem erro visível. `apagar_escopo_global` reproduz o delete-e-regrava do
    --modo completo dentro da MESMA transação da escrita (antes era um
    delete solto antes dos dois upserts)."""
    agora = datetime.now(timezone.utc).isoformat()
    alvo = times_para_gravar if times_para_gravar is not None else rating.keys()
    linhas_elo = [
        {"team_id": team_id, "escopo": "global", "league_id": None, "rating": rating[team_id],
         "partidas": contagem[team_id], "atualizado_em": agora}
        for team_id in alvo
    ]
    lotes_historico = [historico[i:i + TAMANHO_LOTE_HISTORICO_RPC] for i in range(0, len(historico), TAMANHO_LOTE_HISTORICO_RPC)] or [[]]
    for i, lote in enumerate(lotes_historico):
        supabase.rpc("registrar_team_elo_lote", {
            "p_elo": linhas_elo if i == 0 else [],
            "p_historico": lote,
            "p_delete_escopo": ("global" if apagar_escopo_global else None) if i == 0 else None,
            "p_delete_league_id": None,
        }).execute()
    return len(linhas_elo)


def processar_completo(supabase):
    print("Carregando partidas finalizadas de TODAS as competições...")
    partidas = carregar_partidas_finalizadas(supabase)
    print(f"  {len(partidas)} partidas.")

    print("Carregando seeds externas (ClubElo)...")
    seed_por_time = carregar_seeds_externas(supabase)
    print(f"  {len(seed_por_time)} times com seed externa.")

    rating, contagem = {}, {}
    historico = processar_partidas(partidas, rating, contagem, seed_por_time)

    print(f"Regravando team_elo/team_elo_history (escopo=global): {len(rating)} times, {len(historico)} linhas de histórico...")
    n_times = registrar_elo_lote(supabase, rating, contagem, historico, apagar_escopo_global=True)

    print(f"OK (completo): {n_times} times atualizados, {len(partidas)} partidas processadas.")


def processar_incremental(supabase):
    print("Carregando rating atual (escopo=global)...")
    linhas_atuais = _paginar(lambda: supabase.table("team_elo").select("team_id, rating, partidas").eq("escopo", "global"))
    if not linhas_atuais:
        print("Nenhum rating global ainda -- primeira execução precisa ser --modo completo.")
        processar_completo(supabase)
        return

    rating = {l["team_id"]: float(l["rating"]) for l in linhas_atuais}
    contagem = {l["team_id"]: l["partidas"] for l in linhas_atuais}
    print(f"  {len(rating)} times carregados.")

    print("Carregando partidas já processadas (match_id em team_elo_history, escopo=global)...")
    ids_processados = {
        l["match_id"] for l in
        _paginar(lambda: supabase.table("team_elo_history").select("match_id").eq("escopo", "global"))
    }
    print(f"  {len(ids_processados)} partidas já processadas.")

    print("Carregando partidas finalizadas de TODAS as competições...")
    todas_partidas = carregar_partidas_finalizadas(supabase)
    partidas_novas = [p for p in todas_partidas if p["id"] not in ids_processados]

    if not partidas_novas:
        print("OK (incremental): nada novo pra processar.")
        return

    partidas_existentes = [p for p in todas_partidas if p["id"] in ids_processados]
    if partidas_existentes:
        data_mais_recente_existente = max(p["match_date"] for p in partidas_existentes)
        data_mais_antiga_nova = min(p["match_date"] for p in partidas_novas)
        if data_mais_antiga_nova < data_mais_recente_existente:
            sys.exit(
                "Partida(s) nova(s) com data ANTERIOR à mais recente já processada "
                f"({data_mais_antiga_nova} < {data_mais_recente_existente}) -- provável liga/temporada nova "
                "adicionada com jogos no passado, não um simples acréscimo do dia. "
                "Rode com --modo completo pra recalcular na ordem cronológica certa."
            )

    print(f"  {len(partidas_novas)} partidas novas.")
    seed_por_time = carregar_seeds_externas(supabase)
    historico = processar_partidas(partidas_novas, rating, contagem, seed_por_time)

    times_tocados = {p["home_team_id"] for p in partidas_novas} | {p["away_team_id"] for p in partidas_novas}
    n_times = registrar_elo_lote(supabase, rating, contagem, historico, times_para_gravar=times_tocados)

    print(f"OK (incremental): {n_times} times atualizados, {len(partidas_novas)} partidas novas processadas.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--modo", choices=["completo", "incremental"], default="completo",
                    help="completo = recalcula tudo do zero (usar ao adicionar liga/temporada nova); "
                         "incremental = só aplica as partidas novas por cima do rating atual")
    args = ap.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        sys.exit("Defina SUPABASE_URL e SUPABASE_KEY (service_role) como variáveis de ambiente.")

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    if args.modo == "completo":
        processar_completo(supabase)
    else:
        processar_incremental(supabase)


if __name__ == "__main__":
    main()
