#!/usr/bin/env python3
"""IC95% por bootstrap E teste de McNemar pareado, por liga -- responde duas
perguntas que o ranking puro do painel `/modelos` (log-loss/Brier/acurácia,
sem teste nenhum) não responde: "esse modelo é bom o bastante sozinho?" (IC95%
marginal) e "esse modelo é REALMENTE diferente do líder da liga, ou só ficou
por trás por ruído de amostra pequena?" (McNemar pareado, qui-quadrado).

Motivação do IC95% (CONTEXTO_PROJETO.md, achado #27): investigando por que o
modelo misto (`hibrido_gols_v1`/`hibrido_gols_xg_v1`) aparecia como "melhor"
em Bundesliga/Champions League/Copa Libertadores no mercado Over/Under 2.5,
um bootstrap ad-hoc (2000 reamostragens, mesmo espírito de
`backtest_kelly.comparar_pareado_com_mercado` -- só que comparando MODELOS
entre si, não modelo-vs-mercado) mostrou que a "vitória" na Bundesliga não
resiste ao IC95% (sobreposição quase total com os classificadores). Esse
script generalizou aquele bootstrap ad-hoc (achado #28).

Motivação do McNemar (achado #29, pedido explícito do usuário): duas IC95%
marginais que se sobrepõem não PROVAM que os modelos empatam -- é uma leitura
conservadora, informal. McNemar é o teste certo pra "modelo A bate modelo B
na MESMA amostra pareada de partidas": usa só os jogos onde os dois
DISCORDAM (um acerta, o outro erra) -- b = A acerta/líder erra, c = A
erra/líder acerta -- estatística qui-quadrado com correção de continuidade
de Yates, `qui2 = (|b-c|-1)² / (b+c)`, p-valor pela cauda superior da
qui-quadrado com 1 grau de liberdade (`scipy.stats.chi2.sf`). Comparar contra
TODOS os pares (C(n,2) por grupo) explodiria combinatoriamente e a maioria
das comparações não interessa -- em vez disso, cada modelo não-líder de um
grupo (model_name, market, league_id) é comparado só contra o LÍDER do grupo
(menor log-loss médio, mesmo critério de ranking já usado no painel) --
responde diretamente "o líder bate esse modelo de verdade?", que é a pergunta
prática de quem está decidindo qual modelo usar.

Descoberta dos combos a avaliar: reaproveita `model_stats_resumo` (já lista
todo model_name presente em cada mercado, tabela pequena) em vez de fazer
`SELECT DISTINCT` em `model_predictions` (5,2M+ linhas) -- mesma combinação
que o painel `/modelos` já trata como "existe dado suficiente pra mostrar".

Diferente da primeira versão deste script (achado #28), agora carrega TODOS
os modelos de um mercado ANTES de agrupar por liga -- precisa ter todo mundo
em memória ao mesmo tempo pra identificar o líder de cada liga e casar
partida a partida com ele (McNemar é pareado por match_id, não dá pra
processar um modelo por vez e descartar como a versão anterior fazia).

Resultado real: gols reais (`matches.home_goals/away_goals`) pra 1X2/
Over-Under 2.5, total de escanteios (`match_stats_fotmob`, mesma fonte de
`avaliar_modelo_misto_vs_mercado._carregar_resultados_reais`) pro mercado de
escanteios.

Amostra mínima de 30 partidas por grupo pro IC95% (mesmo corte de
`avaliar_modelo_misto_vs_mercado.py`); McNemar exige a INTERSECÇÃO de
partidas entre o modelo e o líder, com o mesmo mínimo de 30 -- calculado e
persistido mesmo quando poucos pares são discordantes (chi-quadrado fica
menos confiável com poucos pares discordantes, sinalizado via `confiavel`
quando b+c < 10, não omitido).

Não grava nada em `model_predictions`/`matches` -- só leitura desses,
escrita em `model_stats_ic`/`model_stats_mcnemar`.

Uso:
    set SUPABASE_URL=...
    set SUPABASE_KEY=sua_service_role_key
    python scripts/avaliar_ic_modelos_por_liga.py
    python scripts/avaliar_ic_modelos_por_liga.py --mercados "1X2,over_under_2.5"  # subconjunto, teste rápido
    python scripts/avaliar_ic_modelos_por_liga.py --sem-gravar                     # só imprime, não escreve
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

import numpy as np
import pandas as pd
from postgrest.exceptions import APIError
from scipy.stats import chi2
from supabase import create_client

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dados_historicos as dh

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
logger = logging.getLogger("avaliar_ic_modelos_por_liga")

# Seleções esperadas por mercado -- usado só pra validar que uma previsão tem
# TODAS as seleções antes de entrar no cálculo (favorito/log-loss exigem o
# conjunto completo; uma seleção faltando indicaria dado incompleto, não um
# jogo sem previsão nenhuma).
MERCADOS = {
    "1X2": ["home", "draw", "away"],
    "over_under_2.5": ["over", "under"],
    "corners_over_under_9.5": ["over", "under"],
}

AMOSTRA_MINIMA = 30
# Abaixo desse total de pares DISCORDANTES (b+c), a aproximação qui-quadrado
# de McNemar (mesmo com correção de continuidade) é pouco confiável -- regra
# prática clássica (equivalente ao "célula esperada < 5" do qui-quadrado
# comum). Não descarta a linha, só marca `confiavel=false` -- mesmo espírito
# de `AMOSTRA_MINIMA_CONFIAVEL` em `avaliar_modelo_misto_vs_mercado.py`
# (avisar em vez de omitir).
MINIMO_DISCORDANTES_CONFIAVEL = 10
N_REAMOSTRAGENS = 2000
SEED = 42
LOTE_UPSERT = 500


def obter_env(nome: str) -> str:
    valor = os.environ.get(nome)
    if not valor:
        sys.exit(f"Configure {nome} antes de rodar.")
    return valor


def _modelos_por_mercado(supabase, mercado: str) -> list[str]:
    linhas = dh._paginar(
        lambda inicio, fim: supabase.table("model_stats_resumo").select("model_name").eq("market", mercado).order("model_name").range(inicio, fim)
    )
    return sorted({l["model_name"] for l in linhas})


def _paginar_predicoes(supabase, model_name: str, mercado: str) -> dict[int, dict[str, float]]:
    """`model_predictions` (match_id, selection, probability) filtrado por
    model_name/market, paginado por KEYSET composto (match_id, selection) --
    mesmo padrão de `avaliar_modelo_misto_vs_mercado._carregar_predicoes`
    (índice `(model_name, market, match_id)`, achado #19, casa com
    `ORDER BY match_id`; OFFSET nessa mesma tabela já estourou
    `statement_timeout` em produção, ver mesmo achado)."""
    predicoes: dict[int, dict[str, float]] = {}
    cursor: tuple[int, str] | None = None
    while True:
        query = (
            supabase.table("model_predictions")
            .select("match_id, selection, probability")
            .eq("model_name", model_name)
            .eq("market", mercado)
            .order("match_id")
            .order("selection")
            .limit(1000)
        )
        if cursor is not None:
            cursor_match_id, cursor_selecao = cursor
            query = query.or_(f"match_id.gt.{cursor_match_id},and(match_id.eq.{cursor_match_id},selection.gt.{cursor_selecao})")
        try:
            pagina = query.execute().data or []
        except APIError as e:
            # Mesmo padrão de `avaliar_modelo_misto_vs_mercado._carregar_
            # predicoes` -- `statement_timeout` (role `anon`=3s, acha real
            # documentado no achado #19) é tratado como "para com o que já
            # tem" em vez de derrubar o script inteiro; rodando com
            # `SUPABASE_SERVICE_ROLE_KEY` (uso normal via GitHub Actions)
            # isso não costuma disparar.
            if e.code == "57014":
                logger.warning(
                    "%s [%s]: timeout paginando model_predictions (cursor=%s) -- seguindo com %d partida(s) já lida(s).",
                    model_name, mercado, cursor, len(predicoes),
                )
                break
            raise
        for linha in pagina:
            predicoes.setdefault(linha["match_id"], {})[linha["selection"]] = float(linha["probability"])
        if len(pagina) < 1000:
            break
        ultimo = pagina[-1]
        cursor = (ultimo["match_id"], ultimo["selection"])
    return predicoes


def _resultado_real_gols(home_goals: int, away_goals: int, mercado: str) -> str:
    if mercado == "1X2":
        if home_goals > away_goals:
            return "home"
        if home_goals == away_goals:
            return "draw"
        return "away"
    return "over" if (home_goals + away_goals) > 2.5 else "under"


def _carregar_resultados_reais(supabase, match_ids: list[int], mercado: str) -> dict[int, tuple[int, str]]:
    """`match_id -> (league_id, seleção_real)`."""
    if mercado == "corners_over_under_9.5":
        total_df = dh._carregar_total_corners_por_partida(supabase, match_ids)

        def factory_liga(lote, inicio, fim):
            return supabase.table("matches").select("id, league_id").in_("id", lote).range(inicio, fim)

        league_por_match = {l["id"]: l["league_id"] for l in dh._paginar_por_lotes_de_id(factory_liga, match_ids)}

        resultados: dict[int, tuple[int, str]] = {}
        for _, linha in total_df.iterrows():
            if pd.isna(linha["total_corners"]):
                continue
            match_id = int(linha["match_id"])
            league_id = league_por_match.get(match_id)
            if league_id is None:
                continue
            resultados[match_id] = (league_id, "over" if linha["total_corners"] > 9.5 else "under")
        return resultados

    def factory(lote, inicio, fim):
        return (
            supabase.table("matches")
            .select("id, league_id, home_goals, away_goals")
            .in_("id", lote)
            .eq("status", "finished")
            .order("id")
            .range(inicio, fim)
        )

    resultados = {}
    for linha in dh._paginar_por_lotes_de_id(factory, match_ids):
        if linha["home_goals"] is None or linha["away_goals"] is None:
            continue
        resultados[linha["id"]] = (linha["league_id"], _resultado_real_gols(linha["home_goals"], linha["away_goals"], mercado))
    return resultados


def _clamp(p: float, eps: float = 1e-4) -> float:
    return min(max(p, eps), 1 - eps)


def carregar_dados_mercado(supabase, mercado: str, modelos: list[str]) -> dict[int, dict[str, dict[int, tuple[dict, str]]]]:
    """`league_id -> model_name -> match_id -> (probs, seleção_real)`.

    Um `model_name` por vez na BUSCA (mesma paginação de antes), mas o
    resultado fica todo residente em memória por mercado -- diferente da
    primeira versão (achado #28), que processava e descartava um modelo por
    vez. Necessário aqui: McNemar casa partida a partida entre dois modelos,
    então precisa dos dois num lugar só ao mesmo tempo. Custo de memória é o
    mesmo total de linhas já buscadas antes, só residente por mais tempo."""
    selecoes_esperadas = MERCADOS[mercado]
    dados: dict[int, dict[str, dict[int, tuple[dict, str]]]] = {}
    for model_name in modelos:
        predicoes = _paginar_predicoes(supabase, model_name, mercado)
        if not predicoes:
            continue
        resultados = _carregar_resultados_reais(supabase, list(predicoes.keys()), mercado)
        for match_id, (league_id, real) in resultados.items():
            probs = predicoes.get(match_id)
            if not probs or not all(s in probs for s in selecoes_esperadas):
                continue
            dados.setdefault(league_id, {}).setdefault(model_name, {})[match_id] = (probs, real)
    return dados


def _log_loss_e_acertos(por_match: dict[int, tuple[dict, str]]) -> tuple[list[int], np.ndarray, np.ndarray]:
    match_ids = list(por_match.keys())
    log_losses = np.empty(len(match_ids))
    acertos = np.empty(len(match_ids))
    for i, match_id in enumerate(match_ids):
        probs, real = por_match[match_id]
        log_losses[i] = -np.log(_clamp(probs[real]))
        favorito = max(probs, key=probs.get)
        acertos[i] = 1.0 if favorito == real else 0.0
    return match_ids, log_losses, acertos


def _bootstrap_ic95(log_losses: np.ndarray, acertos: np.ndarray, rng: np.random.Generator, n_reamostragens: int = N_REAMOSTRAGENS) -> tuple[tuple[float, float], tuple[float, float]]:
    """Bootstrap não pareado (2000 reamostragens com reposição, mesma
    disciplina de `backtest_kelly.comparar_pareado_com_mercado`/achado #3 --
    aqui MARGINAL por modelo, não pareado contra um segundo modelo, porque o
    objetivo é o IC de cada modelo isoladamente, não uma diferença). O MESMO
    índice reamostrado é usado pra log-loss e acurácia em cada iteração
    (matriz `(n_reamostragens, n)` vetorizada, não um laço Python por
    reamostragem -- laço chegou a levar dezenas de minutos pro total de
    grupos deste script num teste inicial)."""
    n = len(log_losses)
    idx = rng.integers(0, n, size=(n_reamostragens, n))
    ll_medias = log_losses[idx].mean(axis=1)
    acc_medias = acertos[idx].mean(axis=1)
    ll_lo, ll_hi = np.percentile(ll_medias, [2.5, 97.5])
    acc_lo, acc_hi = np.percentile(acc_medias, [2.5, 97.5])
    return (float(ll_lo), float(ll_hi)), (float(acc_lo), float(acc_hi))


def mcnemar(b: int, c: int) -> tuple[float, float]:
    """Qui-quadrado de McNemar com correção de continuidade de Yates --
    fórmula clássica `(|b-c|-1)² / (b+c)`, p-valor pela cauda superior da
    qui-quadrado com 1 grau de liberdade. `b`/`c` são os pares DISCORDANTES
    (um modelo acerta, o outro erra) -- concordâncias (os dois acertam ou os
    dois erram) não entram na estatística, não carregam informação sobre
    QUAL dos dois é melhor."""
    total_discordantes = b + c
    if total_discordantes == 0:
        return 0.0, 1.0
    estatistica = (abs(b - c) - 1) ** 2 / total_discordantes
    p_valor = float(chi2.sf(estatistica, df=1))
    return float(estatistica), p_valor


def avaliar_mercado(dados: dict, mercado: str, rng: np.random.Generator) -> tuple[list[dict], list[dict]]:
    linhas_ic: list[dict] = []
    linhas_mcnemar: list[dict] = []

    for league_id, por_modelo in dados.items():
        stats_modelo = {}
        for model_name, por_match in por_modelo.items():
            if len(por_match) < AMOSTRA_MINIMA:
                continue
            match_ids, log_losses, acertos = _log_loss_e_acertos(por_match)
            stats_modelo[model_name] = {
                "match_ids": match_ids, "log_losses": log_losses, "acertos": acertos,
                "log_loss_medio": float(log_losses.mean()),
            }
        if not stats_modelo:
            continue

        for model_name, st in stats_modelo.items():
            (ll_lo, ll_hi), (acc_lo, acc_hi) = _bootstrap_ic95(st["log_losses"], st["acertos"], rng)
            linhas_ic.append({
                "model_name": model_name, "market": mercado, "league_id": league_id,
                "n_jogos": len(st["match_ids"]),
                "log_loss": round(st["log_loss_medio"], 4),
                "log_loss_ic_inf": round(ll_lo, 4), "log_loss_ic_sup": round(ll_hi, 4),
                "accuracy": round(float(st["acertos"].mean()), 4),
                "accuracy_ic_inf": round(acc_lo, 4), "accuracy_ic_sup": round(acc_hi, 4),
            })

        # Líder = menor log-loss médio do grupo (mesmo critério de ranking já
        # usado pelo painel `/modelos`) -- todo outro modelo é comparado só
        # contra ele via McNemar, não contra todos os pares possíveis.
        lider = min(stats_modelo, key=lambda m: stats_modelo[m]["log_loss_medio"])
        acertos_lider = dict(zip(stats_modelo[lider]["match_ids"], stats_modelo[lider]["acertos"]))
        match_ids_lider = set(stats_modelo[lider]["match_ids"])

        for model_name, st in stats_modelo.items():
            if model_name == lider:
                continue
            match_ids_comuns = match_ids_lider & set(st["match_ids"])
            if len(match_ids_comuns) < AMOSTRA_MINIMA:
                continue
            acertos_modelo = dict(zip(st["match_ids"], st["acertos"]))
            b = c = concordantes = 0
            for match_id in match_ids_comuns:
                a_ok = acertos_modelo[match_id] == 1.0
                l_ok = acertos_lider[match_id] == 1.0
                if a_ok and not l_ok:
                    b += 1
                elif l_ok and not a_ok:
                    c += 1
                else:
                    concordantes += 1
            estatistica, p_valor = mcnemar(b, c)
            linhas_mcnemar.append({
                "market": mercado, "league_id": league_id,
                "model_name": model_name, "model_lider": lider,
                "n_pareado": len(match_ids_comuns), "n_concordantes": concordantes,
                "n_favorece_model": b, "n_favorece_lider": c,
                "qui2": round(estatistica, 4), "p_valor": round(p_valor, 6),
                "significativo": p_valor < 0.05,
                "confiavel": (b + c) >= MINIMO_DISCORDANTES_CONFIAVEL,
            })

    return linhas_ic, linhas_mcnemar


def upsert_em_lotes(supabase, tabela: str, linhas: list[dict], on_conflict: str) -> int:
    for i in range(0, len(linhas), LOTE_UPSERT):
        supabase.table(tabela).upsert(linhas[i : i + LOTE_UPSERT], on_conflict=on_conflict).execute()
    return len(linhas)


def main() -> None:
    parser = argparse.ArgumentParser(description="IC95% por bootstrap e McNemar pareado (log-loss/acurácia), por liga.")
    parser.add_argument("--mercados", default="", help="Subconjunto separado por vírgula (vazio = todos os 3 de model_stats_resumo).")
    parser.add_argument("--modelos", default="", help="Subconjunto de model_name separado por vírgula (vazio = todos os de model_stats_resumo pro mercado). Útil pra teste rápido.")
    parser.add_argument("--sem-gravar", action="store_true", help="Só imprime, não escreve em model_stats_ic/model_stats_mcnemar.")
    args = parser.parse_args()
    filtro_modelos = {m.strip() for m in args.modelos.split(",") if m.strip()} or None

    supabase = create_client(obter_env("SUPABASE_URL"), obter_env("SUPABASE_KEY"))
    rng = np.random.default_rng(SEED)

    mercados = [m.strip() for m in args.mercados.split(",") if m.strip()] or list(MERCADOS.keys())
    for m in mercados:
        if m not in MERCADOS:
            sys.exit(f"Mercado desconhecido: {m!r} (válidos: {list(MERCADOS.keys())})")

    todas_ic: list[dict] = []
    todas_mcnemar: list[dict] = []
    for mercado in mercados:
        modelos = _modelos_por_mercado(supabase, mercado)
        if filtro_modelos is not None:
            modelos = [m for m in modelos if m in filtro_modelos]
        logger.info("[%s] carregando %d modelo(s)...", mercado, len(modelos))
        dados = carregar_dados_mercado(supabase, mercado, modelos)
        linhas_ic, linhas_mcnemar = avaliar_mercado(dados, mercado, rng)
        logger.info("[%s] IC95%% em %d grupo(s), McNemar em %d comparação(ões) contra o líder.", mercado, len(linhas_ic), len(linhas_mcnemar))
        todas_ic.extend(linhas_ic)
        todas_mcnemar.extend(linhas_mcnemar)

    todas_ic.sort(key=lambda r: (r["market"], r["league_id"], r["log_loss"]))
    for r in todas_ic:
        logger.info(
            "[IC]      %-30s %-22s liga=%-3d n=%-5d log-loss=%.4f IC95%%=[%.4f,%.4f]  acc=%.1f%% IC95%%=[%.1f%%,%.1f%%]",
            r["market"], r["model_name"], r["league_id"], r["n_jogos"],
            r["log_loss"], r["log_loss_ic_inf"], r["log_loss_ic_sup"],
            r["accuracy"] * 100, r["accuracy_ic_inf"] * 100, r["accuracy_ic_sup"] * 100,
        )

    todas_mcnemar.sort(key=lambda r: (r["market"], r["league_id"], r["p_valor"]))
    for r in todas_mcnemar:
        veredito = "SIGNIFICATIVO" if r["significativo"] else "não significativo"
        aviso = "" if r["confiavel"] else " (poucos pares discordantes, pouco confiável)"
        logger.info(
            "[McNemar] %-30s vs líder %-30s liga=%-3d n_pareado=%-5d b=%-4d c=%-4d qui2=%.3f p=%.4f -- %s%s",
            r["model_name"], r["model_lider"], r["league_id"], r["n_pareado"],
            r["n_favorece_model"], r["n_favorece_lider"], r["qui2"], r["p_valor"], veredito, aviso,
        )

    if args.sem_gravar:
        print(f"\n(--sem-gravar: {len(todas_ic)} linha(s) de IC e {len(todas_mcnemar)} de McNemar NÃO escritas.)")
        return

    n_ic = upsert_em_lotes(supabase, "model_stats_ic", todas_ic, "model_name,market,league_id") if todas_ic else 0
    n_mc = upsert_em_lotes(supabase, "model_stats_mcnemar", todas_mcnemar, "market,league_id,model_name") if todas_mcnemar else 0
    logger.info("Gravadas %d linha(s) em model_stats_ic e %d em model_stats_mcnemar.", n_ic, n_mc)


if __name__ == "__main__":
    main()
